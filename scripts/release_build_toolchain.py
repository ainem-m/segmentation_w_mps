#!/usr/bin/env python3
"""Verify and prepare the offline, release-only Python build toolchain.

The application dependency lock describes what is installed on an end-user
Mac.  It is not a safe substitute for the tooling that builds the three wheel
artifacts included in a release.  This module gives that builder toolchain its
own strict input boundary: every backend and transitive is an exact hashed
wheel in an operator-prepared wheelhouse, and the CPython/uv executables used
to consume it are recorded by version and SHA-256.

No downloader is implemented here.  A missing canonical toolchain lock or its
wheelhouse is a release blocker rather than a reason to consult a package
index.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from uuid import uuid4


RELEASE_BUILD_TOOLCHAIN_SCHEMA = (
    "totalsegmentator_wrapper_mac.release_build_toolchain.v1"
)
RELEASE_BUILD_TOOLCHAIN_RECEIPT_SCHEMA = (
    "totalsegmentator_wrapper_mac.release_build_toolchain_receipt.v1"
)
RELEASE_BUILD_TOOLCHAIN_INSTALLER = (
    "uv-pip-offline-no-index-require-hashes-no-deps-v1"
)
TRUSTED_NATIVE_TOOLCHAIN_BOUNDARY = (
    "apple-xcode-clang-external-recorded-not-hash-bound-v1"
)
TRUSTED_NATIVE_TOOLCHAIN_DEVELOPER_SELECTION = "selected-full-xcode"
TRUSTED_NATIVE_TOOLCHAIN_CLANG_SELECTION = "xcrun--find-clang"
TRUSTED_NATIVE_TOOLCHAIN_PATH_POLICY = (
    "prepared-toolchain-bin-plus-apple-system-tools-v1"
)
SEALED_SYSTEM_PATH_ENTRIES = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
REQUIRED_COMPONENT_TOOL_NAMES: dict[str, set[str]] = {
    # ``build`` is the PEP 517 frontend.  Every backend and its transitive
    # wheel must still be present in resolved_distribution_names/wheel_inputs.
    "wrapper": {"build", "setuptools", "wheel"},
    "acvl-utils": {"build", "setuptools", "wheel"},
    # scikit-build-core otherwise falls back to ambient CMake/Ninja discovery.
    "fpsample": {"build", "scikit-build-core", "pybind11", "cmake", "ninja"},
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXACT_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)$"
)
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)")
_UV_VERSION = re.compile(r"^uv\s+([^\s]+)(?:\s|$)")


class ReleaseBuildToolchainError(RuntimeError):
    """The release builder toolchain is incomplete or not reproducible."""


def _normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_non_symlink(path: Path, label: str) -> None:
    try:
        entry = path.lstat()
    except FileNotFoundError as exc:
        raise ReleaseBuildToolchainError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
        raise ReleaseBuildToolchainError(
            f"{label} must be a regular non-symlink file: {path}"
        )


def _require_directory_non_symlink(path: Path, label: str) -> None:
    try:
        entry = path.lstat()
    except FileNotFoundError as exc:
        raise ReleaseBuildToolchainError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
        raise ReleaseBuildToolchainError(
            f"{label} must be a directory and not a symlink: {path}"
        )


def _require_current_user_owned_private_directory(path: Path, label: str) -> None:
    """Require a stable directory boundary before creating release artifacts.

    The preparation venv and receipt are published with paths selected under
    these directories.  A group- or world-writable directory could be swapped
    after validation; a directory owned by another account is not a build
    boundary this process can safely control.
    """

    _require_directory_non_symlink(path, label)
    entry = path.lstat()
    if entry.st_uid != os.getuid():
        raise ReleaseBuildToolchainError(
            f"{label} must be owned by the current user: {path}"
        )
    if stat.S_IMODE(entry.st_mode) & (stat.S_IWGRP | stat.S_IWOTH):
        raise ReleaseBuildToolchainError(
            f"{label} must not be group- or other-writable: {path}"
        )


def _logical_requirement_lines(path: Path) -> list[str]:
    _require_regular_non_symlink(path, "release build toolchain lock")
    result: list[str] = []
    pending = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        result.append(pending)
        pending = ""
    if pending:
        raise ReleaseBuildToolchainError(
            "release build toolchain lock ends with an incomplete line continuation"
        )
    if not result:
        raise ReleaseBuildToolchainError("release build toolchain lock contains no requirements")
    return result


def _parse_hashed_lock(path: Path) -> dict[str, dict[str, object]]:
    parsed: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    for line in _logical_requirement_lines(path):
        requirement = line.split(" --hash=", 1)[0].strip()
        match = _EXACT_REQUIREMENT.fullmatch(requirement)
        hashes = set(_HASH.findall(line + " "))
        residual = _HASH.sub("", line + " ").strip()
        if match is None or not hashes or residual != requirement:
            failures.append(line)
            continue
        name = _normalize_name(match.group(1))
        if name in parsed:
            failures.append(f"duplicate distribution {name}")
            continue
        parsed[name] = {"version": match.group(2), "hashes": hashes}
    if failures:
        raise ReleaseBuildToolchainError(
            "release build toolchain lock requires exact SHA-256-hashed wheel pins: "
            + "; ".join(failures)
        )
    return parsed


def _require_exact_keys(value: object, keys: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ReleaseBuildToolchainError(f"{label} field set is invalid")
    return value


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _portable_wheel_filename(value: object) -> bool:
    """Allow only a basename that cannot carry an operator-local path."""

    return (
        isinstance(value, str)
        and Path(value).name == value
        and value.endswith(".whl")
        and bool(value)
        and not any(token in value for token in ("/", "\\", "~", "\x00", "\n", "\r"))
    )


def _sealed_probe_environment() -> dict[str, str]:
    """Return the minimal environment allowed for identity inspection."""

    return {
        "PATH": ":".join(SEALED_SYSTEM_PATH_ENTRIES),
        "HOME": os.devnull,
        "TMPDIR": "/tmp",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "PYTHONNOUSERSITE": "1",
        "LC_ALL": "C",
    }


def _native_toolchain_environment(*, developer_dir: str | None) -> dict[str, str]:
    """Run Xcode probes without inheriting build-tool override variables."""

    environment = _sealed_probe_environment()
    if developer_dir is not None:
        environment["DEVELOPER_DIR"] = developer_dir
    return environment


def _run_native_toolchain_probe(
    command: list[str], *, developer_dir: str | None, label: str
) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=_native_toolchain_environment(developer_dir=developer_dir),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleaseBuildToolchainError(
            f"could not inspect trusted external native toolchain {label}"
        ) from exc
    output = completed.stdout.strip()
    if not output:
        raise ReleaseBuildToolchainError(
            f"trusted external native toolchain {label} produced no output"
        )
    return output


def _validated_native_toolchain_record(value: object) -> dict[str, object]:
    record = _require_exact_keys(
        value,
        {
            "boundary",
            "developer_selection",
            "xcode_version",
            "xcode_build_version",
            "clang_version",
            "clang_binary_sha256",
            "clang_selection",
            "sealed_path_policy",
        },
        "trusted external native toolchain record",
    )
    xcode_version = record.get("xcode_version")
    xcode_build_version = record.get("xcode_build_version")
    clang_version = record.get("clang_version")
    clang_binary_sha256 = record.get("clang_binary_sha256")
    if (
        record.get("boundary") != TRUSTED_NATIVE_TOOLCHAIN_BOUNDARY
        or record.get("developer_selection")
        != TRUSTED_NATIVE_TOOLCHAIN_DEVELOPER_SELECTION
        or record.get("clang_selection") != TRUSTED_NATIVE_TOOLCHAIN_CLANG_SELECTION
        or record.get("sealed_path_policy") != TRUSTED_NATIVE_TOOLCHAIN_PATH_POLICY
        or not isinstance(xcode_version, str)
        or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*(?:[A-Za-z0-9._-]+)?", xcode_version)
        or not isinstance(xcode_build_version, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]+", xcode_build_version)
        or not isinstance(clang_version, str)
        or not clang_version
        or any(token in clang_version for token in ("/", "\\", "\n", "\r", "~"))
        or len(clang_version) > 256
        or not _valid_digest(clang_binary_sha256)
    ):
        raise ReleaseBuildToolchainError(
            "trusted external native toolchain record is invalid"
        )
    return dict(record)


def capture_trusted_native_toolchain() -> dict[str, object]:
    """Record the full-Xcode compiler boundary used for a release build.

    Xcode is deliberately an external trusted platform dependency rather than
    a wheelhouse input.  We do not describe this as fully hermetic: a later
    component invocation must re-capture and match this exact recorded
    identity before compiling native code.
    """

    requested_developer_dir = os.environ.get("DEVELOPER_DIR") or None
    selected_developer_dir = requested_developer_dir
    if selected_developer_dir is None:
        selected_developer_dir = _run_native_toolchain_probe(
            ["/usr/bin/xcode-select", "-p"],
            developer_dir=None,
            label="developer directory",
        )
    developer_dir = Path(selected_developer_dir)
    if (
        not developer_dir.is_absolute()
        or "CommandLineTools" in developer_dir.parts
        or not developer_dir.is_dir()
        or developer_dir.is_symlink()
    ):
        raise ReleaseBuildToolchainError(
            "trusted external native toolchain must use a non-symlink full Xcode developer directory"
        )
    xcode_output = _run_native_toolchain_probe(
        ["/usr/bin/xcodebuild", "-version"],
        developer_dir=str(developer_dir),
        label="Xcode version",
    )
    xcode_lines = xcode_output.splitlines()
    if len(xcode_lines) != 2 or not xcode_lines[0].startswith("Xcode ") or not xcode_lines[
        1
    ].startswith("Build version "):
        raise ReleaseBuildToolchainError(
            "trusted external native toolchain Xcode version output is invalid"
        )
    clang_path_text = _run_native_toolchain_probe(
        ["/usr/bin/xcrun", "--find", "clang"],
        developer_dir=str(developer_dir),
        label="clang path",
    )
    clang_path = Path(clang_path_text)
    try:
        clang_path.resolve(strict=True).relative_to(developer_dir.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ReleaseBuildToolchainError(
            "trusted external native toolchain clang is outside the selected Xcode developer directory"
        ) from exc
    _require_regular_non_symlink(clang_path.resolve(strict=True), "trusted external clang")
    clang_output = _run_native_toolchain_probe(
        [str(clang_path), "--version"],
        developer_dir=str(developer_dir),
        label="clang version",
    )
    clang_version = clang_output.splitlines()[0].strip()
    if not clang_version:
        raise ReleaseBuildToolchainError(
            "trusted external native toolchain clang version is invalid"
        )
    # Do not persist absolute Xcode paths, usernames, or shell environment
    # values into the app.  The runner retains the selected developer directory
    # only in memory and recaptures this normalized identity before each build.
    return _validated_native_toolchain_record(
        {
            "boundary": TRUSTED_NATIVE_TOOLCHAIN_BOUNDARY,
            "developer_selection": TRUSTED_NATIVE_TOOLCHAIN_DEVELOPER_SELECTION,
            "xcode_version": xcode_lines[0][len("Xcode ") :].strip(),
            "xcode_build_version": xcode_lines[1][len("Build version ") :].strip(),
            "clang_version": clang_version,
            "clang_binary_sha256": _sha256_file(clang_path.resolve(strict=True)),
            "clang_selection": TRUSTED_NATIVE_TOOLCHAIN_CLANG_SELECTION,
            "sealed_path_policy": TRUSTED_NATIVE_TOOLCHAIN_PATH_POLICY,
        }
    )


def _wheel_distribution_identity(wheel: Path, *, label: str) -> tuple[str, str]:
    """Read a wheel Name/Version only after archive safety checks."""

    _require_regular_non_symlink(wheel, label)
    try:
        with zipfile.ZipFile(wheel) as archive:
            infos = archive.infolist()
            if not infos or archive.testzip() is not None:
                raise ReleaseBuildToolchainError(
                    f"release build toolchain wheel ZIP validation failed: {wheel.name}"
                )
            for info in infos:
                parts = PurePosixPath(info.filename).parts
                if (
                    info.filename.startswith("/")
                    or "\\" in info.filename
                    or ".." in parts
                ):
                    raise ReleaseBuildToolchainError(
                        f"release build toolchain wheel has an unsafe archive path: {wheel.name}"
                    )
                # Wheels are installed by an archive extractor.  A hashed
                # filename is not enough if the ZIP itself carries a POSIX
                # symlink/device/FIFO entry: such an entry could alter where a
                # later member is written.  Normal wheel ZIPs either omit the
                # POSIX file type or use regular-file/directory entries.
                mode = info.external_attr >> 16
                if info.create_system == 3 and mode and (
                    stat.S_ISLNK(mode)
                    or stat.S_ISCHR(mode)
                    or stat.S_ISBLK(mode)
                    or stat.S_ISFIFO(mode)
                    or stat.S_ISSOCK(mode)
                ):
                    raise ReleaseBuildToolchainError(
                        "release build toolchain wheel has an unsafe archive member "
                        f"type: {wheel.name}"
                    )
            metadata_members = [
                info
                for info in infos
                if not info.is_dir() and info.filename.endswith(".dist-info/METADATA")
            ]
            if len(metadata_members) != 1:
                raise ReleaseBuildToolchainError(
                    f"release build toolchain wheel has ambiguous METADATA: {wheel.name}"
                )
            metadata = Parser().parsestr(
                archive.read(metadata_members[0]).decode("utf-8")
            )
    except ReleaseBuildToolchainError:
        raise
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise ReleaseBuildToolchainError(
            f"release build toolchain wheel is invalid: {wheel.name}"
        ) from exc
    actual_name = _normalize_name(str(metadata.get("Name") or ""))
    actual_version = str(metadata.get("Version") or "")
    if not actual_name or not actual_version:
        raise ReleaseBuildToolchainError(
            f"release build toolchain wheel has incomplete Name/Version metadata: {wheel.name}"
        )
    return actual_name, actual_version


def _read_wheel_identity(
    *,
    wheel: Path,
    expected_name: str,
    expected_version: str,
) -> None:
    """Reject malformed wheels before they become an offline finder input."""

    actual_name, actual_version = _wheel_distribution_identity(
        wheel,
        label=f"release build toolchain wheel {expected_name}",
    )
    if actual_name != expected_name or actual_version != expected_version:
        raise ReleaseBuildToolchainError(
            "release build toolchain wheel metadata mismatch for "
            f"{expected_name}: found {actual_name}=={actual_version}"
        )


def _load_toolchain_metadata(path: Path) -> dict[str, object]:
    _require_regular_non_symlink(path, "release build toolchain metadata")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildToolchainError(
            f"release build toolchain metadata is invalid JSON: {path}"
        ) from exc
    return _require_exact_keys(
        payload,
        {
            "schema",
            "lock_filename",
            "lock_sha256",
            "resolved_distribution_names",
            "wheel_inputs",
            "components",
            "toolchain",
        },
        "release build toolchain metadata",
    )


def verify_release_build_toolchain_inputs(
    *,
    lock_path: Path,
    metadata_path: Path,
    wheelhouse: Path,
) -> dict[str, object]:
    """Validate a prepared offline toolchain without running a build.

    The returned data is safe to use for staging exactly the listed wheels.  A
    caller must never pass the original arbitrary wheelhouse to uv/pip.
    """

    lock_path = lock_path.expanduser().absolute()
    metadata_path = metadata_path.expanduser().absolute()
    wheelhouse = wheelhouse.expanduser().absolute()
    _require_directory_non_symlink(wheelhouse, "release build toolchain wheelhouse")
    metadata = _load_toolchain_metadata(metadata_path)
    if metadata.get("schema") != RELEASE_BUILD_TOOLCHAIN_SCHEMA:
        raise ReleaseBuildToolchainError("release build toolchain metadata schema mismatch")
    if metadata.get("lock_filename") != lock_path.name:
        raise ReleaseBuildToolchainError(
            "release build toolchain metadata names a different lock file"
        )
    if metadata.get("lock_sha256") != _sha256_file(lock_path):
        raise ReleaseBuildToolchainError("release build toolchain lock SHA-256 mismatch")

    lock_entries = _parse_hashed_lock(lock_path)
    resolved = metadata.get("resolved_distribution_names")
    if (
        not isinstance(resolved, list)
        or not all(isinstance(name, str) for name in resolved)
        or [_normalize_name(name) for name in resolved] != sorted(lock_entries)
        or len(resolved) != len(set(_normalize_name(name) for name in resolved))
    ):
        raise ReleaseBuildToolchainError(
            "release build toolchain resolved distribution inventory is invalid"
        )

    wheel_inputs = metadata.get("wheel_inputs")
    if not isinstance(wheel_inputs, dict) or set(wheel_inputs) != set(lock_entries):
        raise ReleaseBuildToolchainError(
            "release build toolchain wheel inputs do not cover the complete lock"
        )
    normalized_wheels: dict[str, dict[str, str]] = {}
    seen_filenames: set[str] = set()
    for raw_name, raw_entry in sorted(wheel_inputs.items()):
        name = _normalize_name(raw_name)
        if name != raw_name:
            raise ReleaseBuildToolchainError(
                f"release build toolchain wheel input name is not normalized: {raw_name}"
            )
        entry = _require_exact_keys(
            raw_entry,
            {"filename", "sha256", "version"},
            f"release build toolchain wheel input {name}",
        )
        filename = entry.get("filename")
        digest = entry.get("sha256")
        version = entry.get("version")
        if (
            not _portable_wheel_filename(filename)
            or filename in seen_filenames
            or not _valid_digest(digest)
            or not isinstance(version, str)
            or not version
        ):
            raise ReleaseBuildToolchainError(
                f"release build toolchain wheel input is invalid for {name}"
            )
        seen_filenames.add(filename)
        lock_entry = lock_entries[name]
        if version != lock_entry["version"] or digest not in lock_entry["hashes"]:
            raise ReleaseBuildToolchainError(
                f"release build toolchain wheel SHA-256 does not match its hashed lock pin: {name}"
            )
        wheel = wheelhouse / filename
        actual_digest = _sha256_file(wheel) if wheel.is_file() and not wheel.is_symlink() else None
        if actual_digest != digest:
            raise ReleaseBuildToolchainError(
                f"release build toolchain wheel SHA-256 mismatch: {name}"
            )
        _read_wheel_identity(
            wheel=wheel,
            expected_name=name,
            expected_version=version,
        )
        normalized_wheels[name] = {
            "filename": filename,
            "sha256": digest,
            "version": version,
        }

    components = metadata.get("components")
    if not isinstance(components, dict) or set(components) != set(
        REQUIRED_COMPONENT_TOOL_NAMES
    ):
        raise ReleaseBuildToolchainError(
            "release build toolchain component inventory is invalid"
        )
    normalized_components: dict[str, list[str]] = {}
    for component, required in REQUIRED_COMPONENT_TOOL_NAMES.items():
        tools = components.get(component)
        if (
            not isinstance(tools, list)
            or not all(isinstance(value, str) for value in tools)
            or len(tools) != len(set(_normalize_name(value) for value in tools))
        ):
            raise ReleaseBuildToolchainError(
                f"release build toolchain component list is invalid: {component}"
            )
        normalized = {_normalize_name(value) for value in tools}
        if not required.issubset(normalized) or not normalized.issubset(
            normalized_wheels
        ):
            raise ReleaseBuildToolchainError(
                f"release build toolchain is missing required {component} backend tools"
            )
        normalized_components[component] = sorted(normalized)

    toolchain = _require_exact_keys(
        metadata.get("toolchain"),
        {"installer", "uv", "python"},
        "release build toolchain identity",
    )
    if toolchain.get("installer") != RELEASE_BUILD_TOOLCHAIN_INSTALLER:
        raise ReleaseBuildToolchainError(
            "release build toolchain installer contract is invalid"
        )
    uv = _require_exact_keys(
        toolchain.get("uv"),
        {"version", "binary_sha256"},
        "release build toolchain uv identity",
    )
    python = _require_exact_keys(
        toolchain.get("python"),
        {
            "implementation",
            "full_version",
            "machine",
            "sysconfig_platform",
            "executable_sha256",
        },
        "release build toolchain Python identity",
    )
    if (
        not isinstance(uv.get("version"), str)
        or not uv["version"]
        or not _valid_digest(uv.get("binary_sha256"))
        or python.get("implementation") != "CPython"
        or not isinstance(python.get("full_version"), str)
        or not re.fullmatch(r"3\.12\.[0-9]+", python["full_version"])
        or python.get("machine") != "arm64"
        or not isinstance(python.get("sysconfig_platform"), str)
        or not re.fullmatch(r"macosx-14(?:\.[0-9]+)*-arm64", python["sysconfig_platform"])
        or not _valid_digest(python.get("executable_sha256"))
    ):
        raise ReleaseBuildToolchainError(
            "release build toolchain uv/Python identity metadata is invalid"
        )
    return {
        "lock_sha256": metadata["lock_sha256"],
        "metadata_sha256": _sha256_file(metadata_path),
        "wheel_inputs": normalized_wheels,
        "components": normalized_components,
        "toolchain": {
            "installer": toolchain["installer"],
            "uv": dict(uv),
            "python": dict(python),
        },
    }


def generate_release_build_toolchain_metadata(
    *,
    lock_path: Path,
    metadata_path: Path,
    wheelhouse: Path,
    python_executable: Path,
    uv_executable: Path,
) -> dict[str, object]:
    """Create metadata from already prepared, local, hashed toolchain inputs.

    This command is deliberately not a resolver or downloader.  An operator
    first prepares a complete exact/hash lock and matching wheelhouse, then
    this function inventories those bytes and captures the CPython/uv identity
    that is allowed to consume them.  The output is refused when a candidate
    metadata path already exists so a release input cannot be overwritten by
    accident.
    """

    lock_path = lock_path.expanduser().absolute()
    metadata_path = metadata_path.expanduser().absolute()
    wheelhouse = wheelhouse.expanduser().absolute()
    _require_regular_non_symlink(lock_path, "release build toolchain lock")
    _require_directory_non_symlink(wheelhouse, "release build toolchain wheelhouse")
    if metadata_path.exists() or metadata_path.is_symlink():
        raise ReleaseBuildToolchainError(
            "release build toolchain metadata output must be absent: "
            f"{metadata_path}"
        )
    _require_directory_non_symlink(
        metadata_path.parent, "release build toolchain metadata output parent"
    )
    lock_entries = _parse_hashed_lock(lock_path)

    candidates: dict[tuple[str, str], list[tuple[Path, str]]] = {}
    for wheel in sorted(wheelhouse.glob("*.whl")):
        name, version = _wheel_distribution_identity(
            wheel, label="release build toolchain wheelhouse wheel"
        )
        candidates.setdefault((name, version), []).append((wheel, _sha256_file(wheel)))
    wheel_inputs: dict[str, dict[str, str]] = {}
    for name, lock_entry in sorted(lock_entries.items()):
        version = lock_entry["version"]
        hashes = lock_entry["hashes"]
        assert isinstance(version, str) and isinstance(hashes, set)
        matches = [
            (wheel, digest)
            for wheel, digest in candidates.get((name, version), [])
            if digest in hashes
        ]
        if len(matches) != 1:
            raise ReleaseBuildToolchainError(
                "release build toolchain wheelhouse must contain exactly one "
                f"hash-matching wheel for {name}=={version}; found {len(matches)}"
            )
        wheel, digest = matches[0]
        wheel_inputs[name] = {
            "filename": wheel.name,
            "sha256": digest,
            "version": version,
        }
    missing_required = sorted(
        set().union(*REQUIRED_COMPONENT_TOOL_NAMES.values()) - set(wheel_inputs)
    )
    if missing_required:
        raise ReleaseBuildToolchainError(
            "release build toolchain lock omits required backend tools: "
            + ", ".join(missing_required)
        )
    toolchain_identity = capture_release_build_toolchain_identity(
        python_executable=python_executable,
        uv_executable=uv_executable,
    )
    payload = {
        "schema": RELEASE_BUILD_TOOLCHAIN_SCHEMA,
        "lock_filename": lock_path.name,
        "lock_sha256": _sha256_file(lock_path),
        "resolved_distribution_names": sorted(lock_entries),
        "wheel_inputs": wheel_inputs,
        "components": {
            component: sorted(tools)
            for component, tools in REQUIRED_COMPONENT_TOOL_NAMES.items()
        },
        "toolchain": toolchain_identity,
    }
    temporary_metadata = metadata_path.with_name(
        f".{metadata_path.name}.{uuid4().hex}.tmp"
    )
    try:
        temporary_metadata.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_metadata, metadata_path)
    finally:
        if temporary_metadata.exists() or temporary_metadata.is_symlink():
            temporary_metadata.unlink()
    return verify_release_build_toolchain_inputs(
        lock_path=lock_path,
        metadata_path=metadata_path,
        wheelhouse=wheelhouse,
    )


def _resolved_executable(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ReleaseBuildToolchainError(f"could not resolve {label}: {path}") from exc
    _require_regular_non_symlink(resolved, label)
    return resolved


def capture_release_build_toolchain_identity(
    *, python_executable: Path, uv_executable: Path
) -> dict[str, object]:
    """Capture the two hash-bound executables for a new metadata file.

    This is intentionally separate from the external Xcode record: CPython
    and uv are exact metadata inputs, while Xcode/clang is recorded in each
    receipt as a checked but non-hermetic platform boundary.
    """

    python_binary = _resolved_executable(python_executable, "release build Python")
    uv_binary = _resolved_executable(uv_executable, "release build uv")
    try:
        python_probe = subprocess.run(
            [
                str(python_binary),
                "-I",
                "-c",
                (
                    "import json, platform, sys, sysconfig; "
                    "print(json.dumps({'implementation': platform.python_implementation(), "
                    "'full_version': '.'.join(map(str, sys.version_info[:3])), "
                    "'machine': platform.machine(), "
                    "'sysconfig_platform': sysconfig.get_platform()}, sort_keys=True))"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
            env=_sealed_probe_environment(),
        )
        observed_python = json.loads(python_probe.stdout)
        uv_probe = subprocess.run(
            [str(uv_binary), "--version"],
            check=True,
            capture_output=True,
            text=True,
            env=_sealed_probe_environment(),
        )
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise ReleaseBuildToolchainError(
            "could not inspect the release build uv/Python identity"
        ) from exc
    uv_match = _UV_VERSION.match(uv_probe.stdout.strip())
    if uv_match is None:
        raise ReleaseBuildToolchainError("release build uv did not report a parseable version")
    candidate = {
        "installer": RELEASE_BUILD_TOOLCHAIN_INSTALLER,
        "uv": {
            "version": uv_match.group(1),
            "binary_sha256": _sha256_file(uv_binary),
        },
        "python": {
            **observed_python,
            "executable_sha256": _sha256_file(python_binary),
        },
    }
    # Reuse the normal strict schema check, without requiring a wheelhouse.
    candidate = _require_exact_keys(
        candidate, {"installer", "uv", "python"}, "release build toolchain identity"
    )
    uv = _require_exact_keys(
        candidate["uv"], {"version", "binary_sha256"}, "release build toolchain uv identity"
    )
    python = _require_exact_keys(
        candidate["python"],
        {
            "implementation",
            "full_version",
            "machine",
            "sysconfig_platform",
            "executable_sha256",
        },
        "release build toolchain Python identity",
    )
    if (
        candidate["installer"] != RELEASE_BUILD_TOOLCHAIN_INSTALLER
        or not isinstance(uv.get("version"), str)
        or not uv["version"]
        or not _valid_digest(uv.get("binary_sha256"))
        or python.get("implementation") != "CPython"
        or not isinstance(python.get("full_version"), str)
        or not re.fullmatch(r"3\.12\.[0-9]+", python["full_version"])
        or python.get("machine") != "arm64"
        or not isinstance(python.get("sysconfig_platform"), str)
        or not re.fullmatch(r"macosx-14(?:\.[0-9]+)*-arm64", python["sysconfig_platform"])
        or not _valid_digest(python.get("executable_sha256"))
    ):
        raise ReleaseBuildToolchainError(
            "release build uv/Python identity is not CPython 3.12 macOS 14 arm64"
        )
    return {
        "installer": candidate["installer"],
        "uv": dict(uv),
        "python": dict(python),
    }


def verify_release_build_toolchain_runtime(
    *,
    verified_inputs: Mapping[str, object],
    python_executable: Path,
    uv_executable: Path,
) -> dict[str, object]:
    """Verify that the actual uv and CPython match the prepared lock metadata."""

    toolchain = verified_inputs["toolchain"]
    assert isinstance(toolchain, dict)
    expected_uv = toolchain["uv"]
    expected_python = toolchain["python"]
    assert isinstance(expected_uv, dict) and isinstance(expected_python, dict)
    observed = capture_release_build_toolchain_identity(
        python_executable=python_executable,
        uv_executable=uv_executable,
    )
    if (
        observed["python"] != expected_python
        or observed["uv"] != expected_uv
        or observed["installer"] != RELEASE_BUILD_TOOLCHAIN_INSTALLER
    ):
        raise ReleaseBuildToolchainError(
            "release build uv/Python identity differs from the hash-bound toolchain metadata"
        )
    native_toolchain = capture_trusted_native_toolchain()
    return {
        "python": dict(observed["python"]),
        "uv": dict(observed["uv"]),
        "native_toolchain": native_toolchain,
    }


def _stage_selected_wheels(
    *,
    wheelhouse: Path,
    wheel_inputs: Mapping[str, Mapping[str, str]],
    destination: Path,
) -> None:
    destination.mkdir(mode=0o700)
    for name, entry in sorted(wheel_inputs.items()):
        source = wheelhouse / entry["filename"]
        target = destination / entry["filename"]
        shutil.copyfile(source, target, follow_symlinks=False)
        if _sha256_file(target) != entry["sha256"]:
            raise ReleaseBuildToolchainError(
                f"staged release build toolchain wheel changed while copying: {name}"
            )


def _run(command: list[str], *, label: str) -> None:
    # Do not start from os.environ and try to blacklist dangerous keys.  The
    # release toolchain is created before any component build, so it needs no
    # user shell configuration at all; an allowlist avoids UV/PIP/Python,
    # Homebrew, compiler, CMake/Ninja, dynamic-loader, proxy, or keychain
    # configuration quietly changing the prepared venv.
    environment: dict[str, str] = {}
    with tempfile.TemporaryDirectory(
        prefix="release-build-toolchain-command-"
    ) as isolated_root:
        isolated = Path(isolated_root)
        home = isolated / "home"
        command_tmp = isolated / "tmp"
        cache = isolated / "uv-cache"
        home.mkdir(mode=0o700)
        command_tmp.mkdir(mode=0o700)
        cache.mkdir(mode=0o700)
        environment.update(
            {
                "PATH": ":".join(SEALED_SYSTEM_PATH_ENTRIES),
                "HOME": str(home),
                "TMPDIR": str(command_tmp),
                "UV_CACHE_DIR": str(cache),
                "PIP_CONFIG_FILE": os.devnull,
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INPUT": "1",
                "PYTHONNOUSERSITE": "1",
                "LC_ALL": "C",
            }
        )
        completed = subprocess.run(
            command, capture_output=True, text=True, env=environment
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "no diagnostic output").strip()
        raise ReleaseBuildToolchainError(f"{label} failed: {detail}")


def _installed_versions(python_executable: Path, names: Sequence[str]) -> dict[str, str]:
    code = (
        "import importlib.metadata as m, json, sys; "
        "print(json.dumps({name: m.version(name) for name in json.loads(sys.argv[1])}, sort_keys=True))"
    )
    try:
        completed = subprocess.run(
            [str(python_executable), "-I", "-c", code, json.dumps(sorted(names))],
            check=True,
            capture_output=True,
            text=True,
            env=_sealed_probe_environment(),
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise ReleaseBuildToolchainError(
            "could not verify installed release build toolchain distributions"
        ) from exc
    if not isinstance(payload, dict) or not all(
        isinstance(name, str) and isinstance(version, str)
        for name, version in payload.items()
    ):
        raise ReleaseBuildToolchainError(
            "installed release build toolchain distribution report is invalid"
        )
    return {str(name): str(version) for name, version in payload.items()}


def _verify_prepared_venv_isolated(
    *,
    prepared_python: Path,
    receipt: Mapping[str, object],
    component: str,
) -> dict[str, object]:
    """Recheck a prepared venv immediately before a component build.

    The build scripts intentionally use ``python -m build --no-isolation``.
    This check makes that safe only when all backend packages remain the exact
    receipt-bound versions and the venv cannot see system site packages.
    """

    if component not in REQUIRED_COMPONENT_TOOL_NAMES:
        raise ReleaseBuildToolchainError(
            f"unknown release build toolchain component: {component}"
        )
    prepared_python = prepared_python.expanduser().absolute()
    if prepared_python.name != "python" or prepared_python.parent.name != "bin":
        raise ReleaseBuildToolchainError(
            "prepared release build toolchain Python must be a venv bin/python path"
        )
    if not prepared_python.exists() or not prepared_python.is_file():
        raise ReleaseBuildToolchainError(
            f"prepared release build toolchain Python is missing: {prepared_python}"
        )
    venv_root = prepared_python.parent.parent
    pyvenv_configuration = venv_root / "pyvenv.cfg"
    _require_regular_non_symlink(
        pyvenv_configuration, "prepared release build toolchain pyvenv.cfg"
    )
    configuration: dict[str, str] = {}
    for raw in pyvenv_configuration.read_text(encoding="utf-8").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        configuration[key.strip().lower()] = value.strip().lower()
    if configuration.get("include-system-site-packages") != "false":
        raise ReleaseBuildToolchainError(
            "prepared release build toolchain venv may not expose system site packages"
        )

    toolchain = receipt.get("toolchain")
    wheel_inputs = receipt.get("wheel_inputs")
    installed_expected = receipt.get("installed_distribution_versions")
    if (
        not isinstance(toolchain, dict)
        or not isinstance(wheel_inputs, dict)
        or not isinstance(installed_expected, dict)
        or not isinstance(toolchain.get("python"), dict)
    ):
        raise ReleaseBuildToolchainError(
            "prepared release build toolchain receipt is invalid"
        )
    base_python = _resolved_executable(prepared_python, "prepared release build Python")
    expected_python = toolchain["python"]
    if (
        _sha256_file(base_python) != expected_python.get("executable_sha256")
        or _installed_versions(prepared_python, list(wheel_inputs)) != installed_expected
    ):
        raise ReleaseBuildToolchainError(
            "prepared release build toolchain no longer matches its receipt"
        )
    required_tools = REQUIRED_COMPONENT_TOOL_NAMES[component]
    missing_tools: list[str] = []
    for name in required_tools:
        expected = wheel_inputs.get(name)
        if not isinstance(expected, dict) or installed_expected.get(name) != expected.get(
            "version"
        ):
            missing_tools.append(name)
    if missing_tools:
        raise ReleaseBuildToolchainError(
            "prepared release build toolchain is missing exact backend tools for "
            f"{component}: {', '.join(sorted(missing_tools))}"
        )
    toolchain_bin = prepared_python.parent
    for executable in ("cmake", "ninja"):
        candidate = toolchain_bin / executable
        if not candidate.is_file() or candidate.is_symlink() or not os.access(candidate, os.X_OK):
            raise ReleaseBuildToolchainError(
                f"prepared release build toolchain {executable} executable is missing"
            )
    observed_native = capture_trusted_native_toolchain()
    if observed_native != toolchain.get("native_toolchain"):
        raise ReleaseBuildToolchainError(
            "trusted external Xcode/clang identity changed after the release build toolchain was prepared"
        )
    return {
        "prepared_python": str(prepared_python),
        "toolchain_bin": str(toolchain_bin),
        "native_toolchain": observed_native,
        "component": component,
    }


def _new_prepared_venv_path(work_directory: Path) -> Path:
    """Choose a fresh, direct child path for one prepared release venv.

    Console scripts installed by Python wheels use an absolute shebang pointing
    at ``venv/bin/python``.  The venv therefore must be created at its final
    published location, rather than created in a temporary directory and moved
    after installation.
    """

    for _ in range(32):
        candidate = work_directory / f"prepared-venv-{uuid4().hex}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise ReleaseBuildToolchainError(
        "could not reserve a unique prepared release build toolchain venv path"
    )


def _remove_failed_prepared_venv(*, work_directory: Path, candidate: Path) -> None:
    """Remove only the unique venv path owned by this failed preparation."""

    if (
        candidate.parent != work_directory
        or re.fullmatch(r"prepared-venv-[0-9a-f]{32}", candidate.name) is None
    ):
        raise ReleaseBuildToolchainError(
            "refusing to remove an unexpected prepared release build toolchain venv"
        )
    try:
        entry = candidate.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
        raise ReleaseBuildToolchainError(
            "refusing to remove an unsafe prepared release build toolchain venv"
        )
    if entry.st_uid != os.getuid():
        raise ReleaseBuildToolchainError(
            "refusing to remove a prepared release build toolchain venv not owned by this user"
        )
    shutil.rmtree(candidate)


def prepare_release_build_toolchain(
    *,
    lock_path: Path,
    metadata_path: Path,
    wheelhouse: Path,
    python_executable: Path,
    uv_executable: Path,
    work_directory: Path,
    receipt_path: Path,
) -> dict[str, object]:
    """Create a fresh uv-managed venv using only the staged locked wheels."""

    verified = verify_release_build_toolchain_inputs(
        lock_path=lock_path,
        metadata_path=metadata_path,
        wheelhouse=wheelhouse,
    )
    runtime = verify_release_build_toolchain_runtime(
        verified_inputs=verified,
        python_executable=python_executable,
        uv_executable=uv_executable,
    )
    work_directory = work_directory.expanduser().absolute()
    if work_directory.exists() or work_directory.is_symlink():
        _require_current_user_owned_private_directory(
            work_directory, "release build toolchain work directory"
        )
        _require_current_user_owned_private_directory(
            work_directory.parent, "release build toolchain work parent"
        )
    else:
        parent = work_directory.parent
        _require_current_user_owned_private_directory(
            parent, "release build toolchain work parent"
        )
        work_directory.mkdir(mode=0o700)
        _require_current_user_owned_private_directory(
            work_directory, "release build toolchain work directory"
        )
    published_venv = _new_prepared_venv_path(work_directory)
    prepared_successfully = False
    try:
        # The selected wheels are transient staging input.  The venv itself is
        # deliberately outside this temporary directory so installed console
        # script shebangs already name their final path.
        with tempfile.TemporaryDirectory(
            prefix="release-build-toolchain-wheels-", dir=work_directory
        ) as temporary:
            staged_wheels = Path(temporary) / "wheels"
            wheel_inputs = verified["wheel_inputs"]
            assert isinstance(wheel_inputs, dict)
            _stage_selected_wheels(
                wheelhouse=wheelhouse.expanduser().absolute(),
                wheel_inputs=wheel_inputs,  # type: ignore[arg-type]
                destination=staged_wheels,
            )
            python_binary = _resolved_executable(python_executable, "release build Python")
            uv_binary = _resolved_executable(uv_executable, "release build uv")
            _run(
                [
                    str(uv_binary),
                    "venv",
                    "--offline",
                    "--python",
                    str(python_binary),
                    str(published_venv),
                ],
                label="offline release build toolchain venv creation",
            )
            venv_python = published_venv / "bin" / "python"
            _require_regular_non_symlink(
                venv_python.resolve(strict=True), "release build toolchain venv Python"
            )
            _run(
                [
                    str(uv_binary),
                    "pip",
                    "install",
                    "--offline",
                    "--no-index",
                    "--find-links",
                    str(staged_wheels),
                    "--require-hashes",
                    "--no-deps",
                    "--only-binary",
                    ":all:",
                    "--python",
                    str(venv_python),
                    "-r",
                    str(lock_path.expanduser().absolute()),
                ],
                label="offline release build toolchain installation",
            )
            # ``--no-deps`` is intentional: every backend transitive must have
            # a separate exact/hash-bound entry in this lock.  Check the
            # installed wheel metadata before any component build so a manually
            # incomplete lock cannot rely on ambient site packages or fail only
            # mid-build.
            _run(
                [str(venv_python), "-I", "-m", "pip", "--isolated", "check"],
                label="release build toolchain dependency closure check",
            )
            expected_versions = {
                name: entry["version"]
                for name, entry in wheel_inputs.items()
                if isinstance(entry, dict)
            }
            installed = _installed_versions(venv_python, list(expected_versions))
            if installed != expected_versions:
                raise ReleaseBuildToolchainError(
                    "installed release build toolchain versions differ from the locked wheel inputs"
                )

        receipt = {
            "schema": RELEASE_BUILD_TOOLCHAIN_RECEIPT_SCHEMA,
            "lock_sha256": verified["lock_sha256"],
            "metadata_sha256": verified["metadata_sha256"],
            "toolchain": runtime,
            "components": verified["components"],
            "wheel_inputs": verified["wheel_inputs"],
            "installed_distribution_versions": installed,
        }
        receipt_path = receipt_path.expanduser().absolute()
        _require_current_user_owned_private_directory(
            receipt_path.parent, "release build toolchain receipt parent"
        )
        if receipt_path.exists() or receipt_path.is_symlink():
            _require_regular_non_symlink(receipt_path, "release build toolchain receipt")
        temporary_receipt = receipt_path.with_name(receipt_path.name + ".tmp")
        if temporary_receipt.exists() or temporary_receipt.is_symlink():
            raise ReleaseBuildToolchainError(
                "release build toolchain temporary receipt path already exists: "
                f"{temporary_receipt}"
            )
        temporary_receipt.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_receipt, receipt_path)
        prepared_successfully = True
        return {**receipt, "prepared_python": str(published_venv / "bin" / "python")}
    finally:
        if not prepared_successfully:
            _remove_failed_prepared_venv(
                work_directory=work_directory, candidate=published_venv
            )


def verify_release_build_toolchain_receipt(
    *,
    receipt_path: Path,
    lock_path: Path,
    metadata_path: Path,
) -> dict[str, object]:
    """Validate the immutable receipt copied into a completed app artifact."""

    _require_regular_non_symlink(receipt_path, "release build toolchain receipt")
    verified = verify_release_build_toolchain_inputs_metadata_only(
        lock_path=lock_path, metadata_path=metadata_path
    )
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildToolchainError("release build toolchain receipt is invalid JSON") from exc
    receipt = _require_exact_keys(
        receipt,
        {
            "schema",
            "lock_sha256",
            "metadata_sha256",
            "toolchain",
            "components",
            "wheel_inputs",
            "installed_distribution_versions",
        },
        "release build toolchain receipt",
    )
    receipt_toolchain = _require_exact_keys(
        receipt.get("toolchain"),
        {"python", "uv", "native_toolchain"},
        "release build toolchain receipt runtime identity",
    )
    expected_toolchain = verified["toolchain"]
    assert isinstance(expected_toolchain, dict)
    if (
        receipt.get("schema") != RELEASE_BUILD_TOOLCHAIN_RECEIPT_SCHEMA
        or receipt.get("lock_sha256") != verified["lock_sha256"]
        or receipt.get("metadata_sha256") != verified["metadata_sha256"]
        or receipt_toolchain.get("python") != expected_toolchain["python"]
        or receipt_toolchain.get("uv") != expected_toolchain["uv"]
        or receipt.get("components") != verified["components"]
        or receipt.get("wheel_inputs") != verified["wheel_inputs"]
    ):
        raise ReleaseBuildToolchainError(
            "release build toolchain receipt does not match its locked input metadata"
        )
    _validated_native_toolchain_record(receipt_toolchain.get("native_toolchain"))
    installed = receipt.get("installed_distribution_versions")
    expected_versions = {
        name: entry["version"]
        for name, entry in verified["wheel_inputs"].items()
    }
    if installed != expected_versions:
        raise ReleaseBuildToolchainError(
            "release build toolchain receipt installed versions are invalid"
        )
    return receipt


def verify_prepared_release_build_toolchain(
    *,
    receipt_path: Path,
    lock_path: Path,
    metadata_path: Path,
    prepared_python: Path,
    component: str,
) -> dict[str, object]:
    """Verify a sealed prepared venv and its external compiler record."""

    receipt = verify_release_build_toolchain_receipt(
        receipt_path=receipt_path,
        lock_path=lock_path,
        metadata_path=metadata_path,
    )
    return _verify_prepared_venv_isolated(
        prepared_python=prepared_python,
        receipt=receipt,
        component=component,
    )


def verify_release_build_toolchain_inputs_metadata_only(
    *, lock_path: Path, metadata_path: Path
) -> dict[str, object]:
    """Validate lock/metadata shape for a bundled receipt without wheel files.

    The release artifact intentionally does not embed its build wheelhouse.
    Hash/filename/role continuity is nevertheless checked against the copied
    lock and metadata; the full wheelhouse check happened before the build.
    """

    lock_path = lock_path.expanduser().absolute()
    metadata_path = metadata_path.expanduser().absolute()
    metadata = _load_toolchain_metadata(metadata_path)
    if metadata.get("schema") != RELEASE_BUILD_TOOLCHAIN_SCHEMA:
        raise ReleaseBuildToolchainError("release build toolchain metadata schema mismatch")
    if metadata.get("lock_filename") != lock_path.name or metadata.get(
        "lock_sha256"
    ) != _sha256_file(lock_path):
        raise ReleaseBuildToolchainError("release build toolchain lock metadata mismatch")
    lock_entries = _parse_hashed_lock(lock_path)
    # A temporary empty directory is not needed: repeat only the structural
    # part of input validation because the artifact deliberately omits wheels.
    resolved = metadata.get("resolved_distribution_names")
    wheel_inputs = metadata.get("wheel_inputs")
    if (
        not isinstance(resolved, list)
        or not all(isinstance(name, str) for name in resolved)
        or [_normalize_name(name) for name in resolved] != sorted(lock_entries)
        or len(resolved) != len(set(_normalize_name(name) for name in resolved))
        or not isinstance(wheel_inputs, dict)
        or set(wheel_inputs) != set(lock_entries)
    ):
        raise ReleaseBuildToolchainError(
            "release build toolchain bundled metadata inventory is invalid"
        )
    normalized_wheels: dict[str, dict[str, str]] = {}
    seen_filenames: set[str] = set()
    for name, lock_entry in lock_entries.items():
        raw_entry = wheel_inputs.get(name)
        entry = _require_exact_keys(
            raw_entry,
            {"filename", "sha256", "version"},
            f"release build toolchain wheel input {name}",
        )
        filename, digest, version = entry.get("filename"), entry.get("sha256"), entry.get("version")
        if (
            _normalize_name(name) != name
            or not _portable_wheel_filename(filename)
            or filename in seen_filenames
            or not _valid_digest(digest)
            or version != lock_entry["version"]
            or digest not in lock_entry["hashes"]
        ):
            raise ReleaseBuildToolchainError(
                f"release build toolchain bundled wheel metadata is invalid: {name}"
            )
        seen_filenames.add(filename)
        normalized_wheels[name] = {"filename": filename, "sha256": digest, "version": str(version)}
    components = metadata.get("components")
    if not isinstance(components, dict) or set(components) != set(REQUIRED_COMPONENT_TOOL_NAMES):
        raise ReleaseBuildToolchainError("release build toolchain component inventory is invalid")
    normalized_components: dict[str, list[str]] = {}
    for component, required in REQUIRED_COMPONENT_TOOL_NAMES.items():
        tools = components[component]
        if (
            not isinstance(tools, list)
            or not all(isinstance(value, str) for value in tools)
            or len(tools) != len(set(_normalize_name(value) for value in tools))
        ):
            raise ReleaseBuildToolchainError(
                f"release build toolchain component list is invalid: {component}"
            )
        normalized = {_normalize_name(value) for value in tools}
        if not required.issubset(normalized) or not normalized.issubset(normalized_wheels):
            raise ReleaseBuildToolchainError(
                f"release build toolchain is missing required {component} backend tools"
            )
        normalized_components[component] = sorted(normalized)
    toolchain = _require_exact_keys(
        metadata.get("toolchain"), {"installer", "uv", "python"}, "release build toolchain identity"
    )
    uv = _require_exact_keys(toolchain.get("uv"), {"version", "binary_sha256"}, "release build toolchain uv identity")
    python = _require_exact_keys(
        toolchain.get("python"),
        {"implementation", "full_version", "machine", "sysconfig_platform", "executable_sha256"},
        "release build toolchain Python identity",
    )
    if (
        toolchain.get("installer") != RELEASE_BUILD_TOOLCHAIN_INSTALLER
        or not isinstance(uv.get("version"), str)
        or not uv["version"]
        or not _valid_digest(uv.get("binary_sha256"))
        or python.get("implementation") != "CPython"
        or not isinstance(python.get("full_version"), str)
        or not re.fullmatch(r"3\.12\.[0-9]+", python["full_version"])
        or python.get("machine") != "arm64"
        or not isinstance(python.get("sysconfig_platform"), str)
        or not re.fullmatch(r"macosx-14(?:\.[0-9]+)*-arm64", python["sysconfig_platform"])
        or not _valid_digest(python.get("executable_sha256"))
    ):
        raise ReleaseBuildToolchainError("release build toolchain identity metadata is invalid")
    return {
        "lock_sha256": metadata["lock_sha256"],
        "metadata_sha256": _sha256_file(metadata_path),
        "wheel_inputs": normalized_wheels,
        "components": normalized_components,
        "toolchain": {"installer": toolchain["installer"], "uv": dict(uv), "python": dict(python)},
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify or prepare the offline release build toolchain."
    )
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--python", dest="python_executable", type=Path)
    parser.add_argument("--uv", dest="uv_executable", type=Path)
    parser.add_argument("--prepare-work-directory", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--verify-receipt", type=Path)
    parser.add_argument("--verify-prepared-python", type=Path)
    parser.add_argument(
        "--generate-metadata",
        action="store_true",
        help=(
            "create a new metadata inventory from an already prepared exact "
            "hash lock and local wheelhouse; never resolves or downloads"
        ),
    )
    parser.add_argument(
        "--component", choices=sorted(REQUIRED_COMPONENT_TOOL_NAMES)
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.generate_metadata:
        if (
            args.wheelhouse is None
            or args.python_executable is None
            or args.uv_executable is None
        ):
            raise ReleaseBuildToolchainError(
                "--generate-metadata requires --wheelhouse, --python, and --uv"
            )
        if any(
            value is not None
            for value in (
                args.prepare_work_directory,
                args.receipt,
                args.verify_receipt,
                args.verify_prepared_python,
                args.component,
            )
        ):
            raise ReleaseBuildToolchainError(
                "--generate-metadata cannot be combined with prepare or receipt verification options"
            )
        result = generate_release_build_toolchain_metadata(
            lock_path=args.lock,
            metadata_path=args.metadata,
            wheelhouse=args.wheelhouse,
            python_executable=args.python_executable,
            uv_executable=args.uv_executable,
        )
    elif args.verify_prepared_python is not None:
        if args.verify_receipt is None or args.component is None:
            raise ReleaseBuildToolchainError(
                "--verify-prepared-python requires --verify-receipt and --component"
            )
        result = verify_prepared_release_build_toolchain(
            receipt_path=args.verify_receipt,
            lock_path=args.lock,
            metadata_path=args.metadata,
            prepared_python=args.verify_prepared_python,
            component=args.component,
        )
    elif args.verify_receipt is not None:
        if args.component is not None:
            raise ReleaseBuildToolchainError(
                "--component requires --verify-prepared-python"
            )
        result = verify_release_build_toolchain_receipt(
            receipt_path=args.verify_receipt,
            lock_path=args.lock,
            metadata_path=args.metadata,
        )
    else:
        if args.wheelhouse is None:
            raise ReleaseBuildToolchainError("--wheelhouse is required")
        if args.prepare_work_directory is None:
            result = verify_release_build_toolchain_inputs(
                lock_path=args.lock,
                metadata_path=args.metadata,
                wheelhouse=args.wheelhouse,
            )
        else:
            if args.python_executable is None or args.uv_executable is None or args.receipt is None:
                raise ReleaseBuildToolchainError(
                    "--prepare-work-directory requires --python, --uv, and --receipt"
                )
            result = prepare_release_build_toolchain(
                lock_path=args.lock,
                metadata_path=args.metadata,
                wheelhouse=args.wheelhouse,
                python_executable=args.python_executable,
                uv_executable=args.uv_executable,
                work_directory=args.prepare_work_directory,
                receipt_path=args.receipt,
            )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("PASS: release build toolchain is hash-bound and offline-ready")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseBuildToolchainError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
