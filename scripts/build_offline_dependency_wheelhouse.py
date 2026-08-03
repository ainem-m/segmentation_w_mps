#!/usr/bin/env python3
"""Build one complete, hash-bound offline dependency wheelhouse.

This is deliberately a *release-input* preparation command, not an end-user
setup path.  It consumes the already resolved canonical requirements lock and
downloads only binary wheels for its macOS arm64 / CPython 3.12 target.  The
two project-built overrides (``acvl-utils`` and ``fpsample``) remain outside
this artifact because they are intentionally absent from the install lock and
``fpsample`` changes bytes when Developer-ID signed later in packaging.

No source archive is accepted.  If a locked dependency has no suitable public
wheel, build it separately for the approved target and pass that wheel through
``--approved-local-wheel-directory``; its bytes must already be represented by
one of the lock hashes.  This keeps source builds and dependency resolution
out of an end user's setup process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONSTRAINTS = ROOT / "constraints" / "macos-arm64-py312.txt"
DEFAULT_REQUIREMENTS_LOCK = ROOT / "constraints" / "macos-arm64-py312.requirements.lock"
DEFAULT_LOCK_METADATA = ROOT / "constraints" / "macos-arm64-py312.lock.json"
DEFAULT_OUTPUT_DIRECTORY = ROOT / "build" / "offline-dependency-wheelhouse"

WHEELHOUSE_SCHEMA = "totalsegmentator_wrapper_mac.offline_dependency_wheelhouse.v1"
TARGET_PLATFORM = "macosx_14_0_arm64"
TARGET_PYTHON_VERSION = "3.12"
TARGET_IMPLEMENTATION = "cp"
TARGET_ABI = "cp312"
TARGET_MACHINE = "arm64"
TARGET_MACOS_MAJOR = 14

# These wheels are deliberately excluded from the canonical pip install lock.
# This command only validates that exclusion in the lock metadata; it does not
# stage either wheel because their separately packaged release bytes are bound
# later (including the Developer-ID-signed fpsample wheel).
BUNDLED_OVERRIDE_SPECS: Mapping[str, Mapping[str, str]] = {
    "acvl-utils": {
        "filename": "acvl_utils-0.2.6-py3-none-any.whl",
        "version": "0.2.6",
        "wheel_tag": "py3-none-any",
    },
    "fpsample": {
        "filename": "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl",
        "version": "1.0.2",
        "wheel_tag": "cp312-cp312-macosx_13_0_arm64",
    },
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PINNED_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9_,.-]+\])?==([^\s;]+)$"
)
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)")
_WHEEL_BUILD_TAG = re.compile(r"^[0-9][0-9A-Za-z_.]*$")
_MACOS_PLATFORM_TAG = re.compile(
    r"^macosx_(?P<major>[0-9]+)_(?P<minor>[0-9]+)_(?P<architecture>arm64|universal2)$"
)
_CPYTHON_ABI3_TAG = re.compile(r"^cp3(?P<minor>[0-9]+)$")
_GENERATION_COMMENT_PREFIX = (
    "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
)


class OfflineWheelhouseError(RuntimeError):
    """The complete offline wheelhouse cannot be safely published."""


@dataclass(frozen=True)
class TargetHost:
    """Facts about the Python used to obtain target wheels."""

    system: str
    machine: str
    implementation: str
    python_version: tuple[int, int]
    macos_version: str
    sysconfig_platform: str


@dataclass(frozen=True)
class LockEntry:
    distribution: str
    version: str
    hashes: frozenset[str]


@dataclass(frozen=True)
class WheelIdentity:
    distribution: str
    version: str
    filename: str
    sha256: str
    size_bytes: int
    tags: tuple[str, ...]
    metadata_sha256: str
    wheel_metadata_sha256: str


DownloadRunner = Callable[..., subprocess.CompletedProcess[str]]
CanonicalLockValidator = Callable[..., dict[str, Any]]


def _normalize_distribution(value: str) -> str:
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
        raise OfflineWheelhouseError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
        raise OfflineWheelhouseError(
            f"{label} must be a regular non-symlink file: {path}"
        )


def _require_directory_non_symlink(path: Path, label: str) -> None:
    try:
        entry = path.lstat()
    except FileNotFoundError as exc:
        raise OfflineWheelhouseError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
        raise OfflineWheelhouseError(
            f"{label} must be a directory and not a symlink: {path}"
        )


def _logical_requirement_lines(path: Path) -> list[str]:
    _require_regular_non_symlink(path, "canonical requirements lock")
    logical: list[str] = []
    pending = ""
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise OfflineWheelhouseError(
            f"could not read canonical requirements lock: {path}"
        ) from exc
    for raw in raw_lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        logical.append(pending)
        pending = ""
    if pending:
        raise OfflineWheelhouseError(
            "canonical requirements lock ends with an incomplete line continuation"
        )
    if not logical:
        raise OfflineWheelhouseError("canonical requirements lock contains no requirements")
    return logical


def parse_hashed_requirements_lock(path: Path) -> dict[str, LockEntry]:
    """Parse the exact/hash subset this command is allowed to consume."""

    entries: dict[str, LockEntry] = {}
    for line in _logical_requirement_lines(path):
        requirement = line.split(" --hash=", 1)[0].strip()
        matched = _PINNED_REQUIREMENT.fullmatch(requirement)
        if matched is None:
            raise OfflineWheelhouseError(
                f"canonical requirements lock is not exact-pinned: {requirement}"
            )
        distribution = _normalize_distribution(matched.group(1))
        version = matched.group(2)
        hashes = frozenset(_HASH.findall(line + " "))
        if not hashes:
            raise OfflineWheelhouseError(
                f"canonical requirements lock has no SHA-256 hash: {distribution}"
            )
        residual = _HASH.sub("", line + " ").strip()
        if residual != requirement:
            raise OfflineWheelhouseError(
                f"canonical requirements lock has unsupported tokens: {line}"
            )
        if distribution in entries:
            raise OfflineWheelhouseError(
                f"canonical requirements lock has duplicate distribution: {distribution}"
            )
        entries[distribution] = LockEntry(distribution, version, hashes)
    return entries


def _lock_generation_id(path: Path) -> str:
    _require_regular_non_symlink(path, "canonical requirements lock")
    try:
        markers = [
            raw[len(_GENERATION_COMMENT_PREFIX) :].strip()
            for raw in path.read_text(encoding="utf-8").splitlines()
            if raw.startswith(_GENERATION_COMMENT_PREFIX)
        ]
    except (OSError, UnicodeError) as exc:
        raise OfflineWheelhouseError(
            f"could not read canonical requirements lock generation ID: {path}"
        ) from exc
    if len(markers) != 1:
        raise OfflineWheelhouseError(
            "canonical requirements lock must contain exactly one generation ID"
        )
    try:
        parsed = UUID(markers[0])
    except (ValueError, AttributeError) as exc:
        raise OfflineWheelhouseError(
            "canonical requirements lock generation ID is invalid"
        ) from exc
    if str(parsed) != markers[0]:
        raise OfflineWheelhouseError("canonical requirements lock generation ID is invalid")
    return markers[0]


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    _require_regular_non_symlink(path, label)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OfflineWheelhouseError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise OfflineWheelhouseError(f"{label} must be a JSON object: {path}")
    return payload


def validate_canonical_lock_inputs(
    *,
    constraints: Path,
    requirements_lock: Path,
    lock_metadata: Path,
    lock_entries: Mapping[str, LockEntry],
) -> dict[str, Any]:
    """Check the lock/metadata boundary needed by wheelhouse preparation.

    The broader release readiness verifier can impose additional release
    conditions.  This independent check intentionally covers only the facts
    required to avoid downloading wheels for a substituted lock or target.
    """

    _require_regular_non_symlink(constraints, "source constraints")
    metadata = _load_json_object(lock_metadata, "dependency lock metadata")
    if metadata.get("requirements_lock") != requirements_lock.name:
        raise OfflineWheelhouseError(
            "dependency lock metadata names a different requirements lock"
        )
    if metadata.get("requirements_lock_sha256") != _sha256_file(requirements_lock):
        raise OfflineWheelhouseError("dependency lock metadata SHA-256 mismatch")
    if metadata.get("constraints_sha256") != _sha256_file(constraints):
        raise OfflineWheelhouseError("dependency lock metadata constraints SHA-256 mismatch")
    if metadata.get("generation_id") != _lock_generation_id(requirements_lock):
        raise OfflineWheelhouseError(
            "dependency lock metadata generation ID does not match the requirements lock"
        )
    resolver = metadata.get("resolver")
    if not isinstance(resolver, dict) or (
        resolver.get("platform") != "macos-14-arm64"
        or resolver.get("python") != TARGET_PYTHON_VERSION
    ):
        raise OfflineWheelhouseError(
            "dependency lock metadata target must be macos-14-arm64 / Python 3.12"
        )
    install_names = metadata.get("install_distribution_names")
    if (
        not isinstance(install_names, list)
        or not all(isinstance(name, str) for name in install_names)
        or [_normalize_distribution(name) for name in install_names]
        != sorted(lock_entries)
    ):
        raise OfflineWheelhouseError(
            "dependency lock metadata install distribution inventory mismatch"
        )
    overrides = metadata.get("excluded_bundled_overrides")
    if not isinstance(overrides, dict) or set(overrides) != set(BUNDLED_OVERRIDE_SPECS):
        raise OfflineWheelhouseError(
            "dependency lock metadata bundled override inventory mismatch"
        )
    for distribution, expected in BUNDLED_OVERRIDE_SPECS.items():
        entry = overrides.get(distribution)
        if not isinstance(entry, dict):
            raise OfflineWheelhouseError(
                f"dependency lock metadata bundled override is invalid: {distribution}"
            )
        required = {
            "version": expected["version"],
            "excluded_from_requirements_lock": True,
            "resolution_input_filename": expected["filename"],
        }
        if any(entry.get(key) != value for key, value in required.items()):
            raise OfflineWheelhouseError(
                f"dependency lock metadata bundled override identity mismatch: {distribution}"
            )
        for key in (
            "resolution_input_sha256",
            "resolution_input_metadata_sha256",
            "resolution_input_wheel_metadata_sha256",
        ):
            if not isinstance(entry.get(key), str) or _SHA256.fullmatch(entry[key]) is None:
                raise OfflineWheelhouseError(
                    f"dependency lock metadata bundled override hash is invalid: {distribution}"
                )
    present_overrides = sorted(set(lock_entries) & set(BUNDLED_OVERRIDE_SPECS))
    if present_overrides:
        raise OfflineWheelhouseError(
            "canonical requirements lock must exclude bundled overrides: "
            + ", ".join(present_overrides)
        )
    return metadata


def _wheel_filename_parts(filename: str) -> tuple[str, str, tuple[str, ...]]:
    if not filename.endswith(".whl"):
        raise OfflineWheelhouseError(f"wheel filename is invalid: {filename}")
    # PEP 427 escapes a project's hyphens in the filename distribution field,
    # so the identity prefix has exactly ``distribution-version`` plus an
    # optional numeric build tag.  Splitting from the right avoids treating a
    # valid numeric build tag as part of the distribution or version.
    parts = filename[:-4].rsplit("-", 3)
    if len(parts) != 4:
        raise OfflineWheelhouseError(f"wheel filename is invalid: {filename}")
    identity_prefix, python_field, abi_field, platform_field = parts
    identity = identity_prefix.split("-")
    if len(identity) == 3 and _WHEEL_BUILD_TAG.fullmatch(identity[2]) is not None:
        distribution_field, version, _build = identity
    elif len(identity) == 2:
        distribution_field, version = identity
    else:
        raise OfflineWheelhouseError(f"wheel filename is invalid: {filename}")
    if not distribution_field or not version:
        raise OfflineWheelhouseError(f"wheel filename is invalid: {filename}")
    python_tags = python_field.split(".")
    abi_tags = abi_field.split(".")
    platform_tags = platform_field.split(".")
    if not all(python_tags) or not all(abi_tags) or not all(platform_tags):
        raise OfflineWheelhouseError(f"wheel filename has an empty compatibility tag: {filename}")
    tags = tuple(
        sorted(
            f"{python_tag}-{abi_tag}-{platform_tag}"
            for python_tag in python_tags
            for abi_tag in abi_tags
            for platform_tag in platform_tags
        )
    )
    return (
        _normalize_distribution(distribution_field),
        version,
        tags,
    )


def _target_compatible_tag(tag: str) -> bool:
    try:
        python_tag, abi_tag, platform_tag = tag.split("-", 2)
    except ValueError:
        return False
    if python_tag == "cp312":
        if abi_tag not in {"cp312", "abi3", "none"}:
            return False
    elif python_tag in {"py312", "py3"}:
        if abi_tag != "none":
            return False
    else:
        # PEP 384's stable ABI is forward-compatible: a cp311-abi3 wheel is
        # usable by CPython 3.12.  Do not incorrectly require a cp312 tag for
        # extensions such as fast-simplification.  A non-abi3 cp311 wheel is
        # still tied to CPython 3.11 and must remain rejected.
        stable_abi = _CPYTHON_ABI3_TAG.fullmatch(python_tag)
        if stable_abi is None or abi_tag != "abi3":
            return False
        minimum_minor = int(stable_abi.group("minor"))
        if minimum_minor < 2 or minimum_minor > 12:
            return False
    if platform_tag == "any":
        return True
    match = _MACOS_PLATFORM_TAG.fullmatch(platform_tag)
    if match is None:
        return False
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    architecture = match.group("architecture")
    if architecture not in {"arm64", "universal2"}:
        return False
    # macOS 11+ uses its major version in the first field.  We only accept a
    # wheel whose declared minimum is no newer than the distributed macOS 14
    # target; an older 10.x compatibility wheel is also suitable.
    if major > TARGET_MACOS_MAJOR:
        return False
    if major == TARGET_MACOS_MAJOR and minor > 0:
        return False
    return True


def _wheel_header_values(payload: str, field: str) -> list[str]:
    message = Parser().parsestr(payload)
    return [value.strip() for value in (message.get_all(field) or [])]


def inspect_wheel(path: Path) -> WheelIdentity:
    """Read one wheel without trusting its filename or archive structure."""

    _require_regular_non_symlink(path, "wheel candidate")
    if path.suffix != ".whl":
        raise OfflineWheelhouseError(f"source distribution or non-wheel input is not allowed: {path.name}")
    filename_distribution, filename_version, filename_tags = _wheel_filename_parts(path.name)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos:
                raise OfflineWheelhouseError(f"wheel archive is empty: {path.name}")
            for info in infos:
                pure = PurePosixPath(info.filename)
                if pure.is_absolute() or ".." in pure.parts or not pure.parts:
                    raise OfflineWheelhouseError(
                        f"wheel archive contains an unsafe path: {path.name}"
                    )
            if archive.testzip() is not None:
                raise OfflineWheelhouseError(f"wheel archive CRC check failed: {path.name}")
            metadata_members = [
                info
                for info in infos
                if not info.is_dir() and info.filename.endswith(".dist-info/METADATA")
            ]
            if len(metadata_members) != 1:
                raise OfflineWheelhouseError(
                    f"wheel metadata is missing or ambiguous: {path.name}"
                )
            metadata_member = metadata_members[0]
            dist_info = metadata_member.filename[: -len("/METADATA")]
            wheel_member_name = f"{dist_info}/WHEEL"
            record_member_name = f"{dist_info}/RECORD"
            wheel_members = [
                info for info in infos if not info.is_dir() and info.filename == wheel_member_name
            ]
            record_members = [
                info for info in infos if not info.is_dir() and info.filename == record_member_name
            ]
            if len(wheel_members) != 1 or len(record_members) != 1:
                raise OfflineWheelhouseError(
                    f"wheel WHEEL or RECORD metadata is missing or ambiguous: {path.name}"
                )
            metadata_bytes = archive.read(metadata_member)
            wheel_metadata_bytes = archive.read(wheel_members[0])
    except OfflineWheelhouseError:
        raise
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise OfflineWheelhouseError(f"wheel archive is invalid: {path.name}") from exc
    try:
        metadata = metadata_bytes.decode("utf-8")
        wheel_metadata = wheel_metadata_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OfflineWheelhouseError(f"wheel metadata is not UTF-8: {path.name}") from exc
    names = _wheel_header_values(metadata, "Name")
    versions = _wheel_header_values(metadata, "Version")
    declared_tags = _wheel_header_values(wheel_metadata, "Tag")
    if len(names) != 1 or len(versions) != 1 or not declared_tags:
        raise OfflineWheelhouseError(f"wheel identity metadata is invalid: {path.name}")
    if (
        filename_distribution != _normalize_distribution(names[0])
        or filename_version != versions[0]
    ):
        raise OfflineWheelhouseError(
            f"wheel filename and identity metadata differ: {path.name}"
        )
    if tuple(sorted(declared_tags)) != filename_tags:
        raise OfflineWheelhouseError(
            f"wheel filename and WHEEL compatibility tags differ: {path.name}"
        )
    if not any(_target_compatible_tag(tag) for tag in filename_tags):
        raise OfflineWheelhouseError(
            f"wheel is not compatible with macOS arm64 CPython 3.12: {path.name}"
        )
    return WheelIdentity(
        distribution=_normalize_distribution(names[0]),
        version=versions[0],
        filename=path.name,
        sha256=_sha256_file(path),
        size_bytes=path.stat().st_size,
        tags=filename_tags,
        metadata_sha256=hashlib.sha256(metadata_bytes).hexdigest(),
        wheel_metadata_sha256=hashlib.sha256(wheel_metadata_bytes).hexdigest(),
    )


def verify_target_host(host: TargetHost) -> None:
    if (
        host.system != "Darwin"
        or host.machine != TARGET_MACHINE
        or host.implementation != "CPython"
        or host.python_version != (3, 12)
    ):
        raise OfflineWheelhouseError(
            "offline wheelhouse generation requires macOS arm64 / CPython 3.12"
        )


def inspect_target_host(python_executable: Path) -> TargetHost:
    """Inspect the selected Python in isolated mode before any download."""

    code = (
        "import json,platform,sys,sysconfig;"
        "print(json.dumps({'system':platform.system(),'machine':platform.machine(),"
        "'implementation':platform.python_implementation(),'python_version':"
        "[sys.version_info.major,sys.version_info.minor],"
        "'macos_version':platform.mac_ver()[0],'sysconfig_platform':"
        "sysconfig.get_platform()}))"
    )
    try:
        completed = subprocess.run(
            [str(python_executable), "-I", "-c", code],
            check=False,
            capture_output=True,
            text=True,
            env={"PATH": os.defpath},
        )
    except OSError as exc:
        raise OfflineWheelhouseError(
            f"could not inspect wheelhouse Python: {python_executable}"
        ) from exc
    if completed.returncode != 0:
        raise OfflineWheelhouseError(
            "wheelhouse Python identity probe failed: "
            f"returncode={completed.returncode}; stderr={completed.stderr.strip()}"
        )
    try:
        payload = json.loads(completed.stdout)
        python_version = payload["python_version"]
        if not isinstance(python_version, list) or len(python_version) != 2:
            raise ValueError("python_version")
        return TargetHost(
            system=str(payload["system"]),
            machine=str(payload["machine"]),
            implementation=str(payload["implementation"]),
            python_version=(int(python_version[0]), int(python_version[1])),
            macos_version=str(payload["macos_version"]),
            sysconfig_platform=str(payload["sysconfig_platform"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OfflineWheelhouseError("wheelhouse Python identity probe returned invalid JSON") from exc


def build_download_command(
    *,
    python_executable: Path,
    requirements_lock: Path,
    destination: Path,
    approved_local_wheel_directory: Path | None = None,
) -> list[str]:
    """Return the sole permitted network-facing command for this artifact."""

    command = [
        str(python_executable),
        "-I",
        "-m",
        "pip",
        "--isolated",
        "download",
        "--dest",
        str(destination),
        "--require-hashes",
        "--only-binary=:all:",
        "--no-deps",
        "--platform",
        TARGET_PLATFORM,
        "--implementation",
        TARGET_IMPLEMENTATION,
        "--python-version",
        TARGET_PYTHON_VERSION,
        "--abi",
        TARGET_ABI,
    ]
    if approved_local_wheel_directory is not None:
        command.extend(["--find-links", str(approved_local_wheel_directory)])
    command.extend(["-r", str(requirements_lock)])
    return command


def _clean_pip_environment() -> dict[str, str]:
    """Use pip's isolated mode and avoid inheriting resolver configuration."""

    return {
        "PATH": os.defpath,
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONNOUSERSITE": "1",
    }


def _default_download_runner(
    command: Sequence[str], *, cwd: Path, env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(env),
        check=False,
        capture_output=True,
        text=True,
    )


def _collect_wheels(directory: Path, label: str) -> list[Path]:
    _require_directory_non_symlink(directory, label)
    result: list[Path] = []
    for candidate in sorted(directory.iterdir(), key=lambda item: item.name):
        try:
            entry = candidate.lstat()
        except OSError as exc:
            raise OfflineWheelhouseError(f"could not inspect {label}: {candidate}") from exc
        if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
            raise OfflineWheelhouseError(
                f"{label} contains a non-regular or symlink member: {candidate.name}"
            )
        if candidate.suffix != ".whl":
            raise OfflineWheelhouseError(
                f"{label} contains a source distribution or non-wheel member: {candidate.name}"
            )
        result.append(candidate)
    return result


def _copy_wheel(source: Path, destination: Path) -> None:
    _require_regular_non_symlink(source, "wheel source")
    source_digest = _sha256_file(source)
    shutil.copyfile(source, destination, follow_symlinks=False)
    if _sha256_file(destination) != source_digest or _sha256_file(source) != source_digest:
        raise OfflineWheelhouseError(
            f"wheel changed while staging: {source.name}"
        )


def _stage_candidate_wheels(
    *,
    downloaded_directory: Path,
    staged_wheelhouse: Path,
) -> None:
    for source in _collect_wheels(downloaded_directory, "downloaded wheel directory"):
        _copy_wheel(source, staged_wheelhouse / source.name)


def _validate_approved_local_wheels(
    directory: Path | None,
    lock_entries: Mapping[str, LockEntry],
) -> dict[str, str]:
    """Validate the explicit fallback for a dependency without a public wheel.

    pip consumes this directory through ``--find-links``.  It is not copied
    separately, which avoids a duplicate wheel if pip selected the same local
    input.  The returned hashes label that selected byte in the manifest.
    """

    if directory is None:
        return {}
    approved: dict[str, str] = {}
    for candidate in _collect_wheels(directory, "approved local wheel directory"):
        identity = inspect_wheel(candidate)
        entry = lock_entries.get(identity.distribution)
        if entry is None:
            raise OfflineWheelhouseError(
                "approved local wheel directory contains a distribution not in "
                f"the canonical lock: {identity.distribution}"
            )
        if identity.version != entry.version or identity.sha256 not in entry.hashes:
            raise OfflineWheelhouseError(
                "approved local wheel does not match the canonical lock: "
                f"{identity.distribution}"
            )
        if identity.distribution in approved:
            raise OfflineWheelhouseError(
                "approved local wheel directory contains duplicate distribution: "
                f"{identity.distribution}"
            )
        approved[identity.distribution] = identity.sha256
    return approved


def _verify_complete_wheelhouse(
    *,
    staged_wheelhouse: Path,
    lock_entries: Mapping[str, LockEntry],
    approved_local_hashes: Mapping[str, str] = {},
) -> list[dict[str, Any]]:
    expected = set(lock_entries)
    inspected: dict[str, WheelIdentity] = {}
    for path in _collect_wheels(staged_wheelhouse, "staged wheelhouse"):
        identity = inspect_wheel(path)
        if identity.distribution not in expected:
            raise OfflineWheelhouseError(
                f"staged wheelhouse contains an extra distribution: {identity.distribution}"
            )
        if identity.distribution in inspected:
            raise OfflineWheelhouseError(
                f"staged wheelhouse contains duplicate distribution: {identity.distribution}"
            )
        inspected[identity.distribution] = identity

    missing = sorted(expected - set(inspected))
    if missing:
        raise OfflineWheelhouseError(
            "staged wheelhouse is missing required distributions: " + ", ".join(missing)
        )
    if set(inspected) != expected:
        raise OfflineWheelhouseError("staged wheelhouse distribution inventory is incomplete")

    output: list[dict[str, Any]] = []
    for distribution in sorted(expected):
        identity = inspected[distribution]
        entry = lock_entries[distribution]
        if identity.version != entry.version:
            raise OfflineWheelhouseError(
                f"wheel version does not match canonical lock: {distribution}"
            )
        if identity.sha256 not in entry.hashes:
            raise OfflineWheelhouseError(
                f"wheel SHA-256 does not match canonical lock: {distribution}"
            )
        source = (
            "approved-locally-built-wheel"
            if approved_local_hashes.get(distribution) == identity.sha256
            else "downloaded-hashed-wheel"
        )
        output.append(
            {
                "distribution": distribution,
                "filename": identity.filename,
                "sha256": identity.sha256,
                "size_bytes": identity.size_bytes,
                "source": source,
                "tags": list(identity.tags),
                "version": identity.version,
            }
        )
    return output


def _manifest_payload(
    *,
    requirements_lock: Path,
    lock_metadata: Path,
    metadata: Mapping[str, Any],
    wheels: list[dict[str, Any]],
) -> dict[str, Any]:
    generation_id = metadata.get("generation_id")
    if not isinstance(generation_id, str):
        raise OfflineWheelhouseError("dependency lock metadata generation ID is missing")
    return {
        "schema": WHEELHOUSE_SCHEMA,
        "canonical_lock": {
            "generation_id": generation_id,
            "metadata_filename": lock_metadata.name,
            "metadata_sha256": _sha256_file(lock_metadata),
            "requirements_lock_filename": requirements_lock.name,
            "requirements_lock_sha256": _sha256_file(requirements_lock),
        },
        "target": {
            "abi": TARGET_ABI,
            "implementation": "CPython",
            "machine": TARGET_MACHINE,
            "platform": TARGET_PLATFORM,
            "python_version": TARGET_PYTHON_VERSION,
        },
        "wheels": wheels,
    }


def verify_existing_offline_dependency_wheelhouse(
    *,
    constraints: Path,
    requirements_lock: Path,
    lock_metadata: Path,
    output_directory: Path,
    canonical_lock_validator: CanonicalLockValidator = validate_canonical_lock_inputs,
) -> dict[str, Any]:
    """Read-only verification for a previously published wheelhouse artifact."""

    constraints = constraints.expanduser().absolute()
    requirements_lock = requirements_lock.expanduser().absolute()
    lock_metadata = lock_metadata.expanduser().absolute()
    output_directory = output_directory.expanduser().absolute()
    _require_directory_non_symlink(output_directory, "offline wheelhouse output")
    lock_entries = parse_hashed_requirements_lock(requirements_lock)
    metadata = canonical_lock_validator(
        constraints=constraints,
        requirements_lock=requirements_lock,
        lock_metadata=lock_metadata,
        lock_entries=lock_entries,
    )
    if not isinstance(metadata, dict):
        raise OfflineWheelhouseError("canonical lock validator did not return metadata")
    manifest_path = output_directory / "manifest.json"
    manifest = _load_json_object(manifest_path, "offline wheelhouse manifest")
    expected_wheels = _verify_complete_wheelhouse(
        staged_wheelhouse=output_directory / "wheels",
        lock_entries=lock_entries,
    )
    expected = _manifest_payload(
        requirements_lock=requirements_lock,
        lock_metadata=lock_metadata,
        metadata=metadata,
        wheels=expected_wheels,
    )
    if set(output_directory.iterdir()) != {manifest_path, output_directory / "wheels"}:
        raise OfflineWheelhouseError(
            "offline wheelhouse output contains unexpected members"
        )
    if manifest.get("schema") != WHEELHOUSE_SCHEMA:
        raise OfflineWheelhouseError("offline wheelhouse manifest schema mismatch")
    recorded_wheels = manifest.get("wheels")
    if not isinstance(recorded_wheels, list):
        raise OfflineWheelhouseError("offline wheelhouse manifest wheel inventory is invalid")
    # Physical source provenance cannot be reconstructed after publication;
    # validate the declared source value while comparing all immutable wheel
    # facts against the recomputed canonical expected manifest.
    allowed_sources = {
        "downloaded-hashed-wheel",
        "approved-locally-built-wheel",
    }
    if any(
        not isinstance(entry, dict)
        or entry.get("source") not in allowed_sources
        for entry in recorded_wheels
    ):
        raise OfflineWheelhouseError("offline wheelhouse manifest source provenance is invalid")
    comparable_manifest = dict(manifest)
    comparable_manifest["wheels"] = [
        {key: value for key, value in entry.items() if key != "source"}
        for entry in recorded_wheels
    ]
    comparable_expected = dict(expected)
    comparable_expected["wheels"] = [
        {key: value for key, value in entry.items() if key != "source"}
        for entry in expected_wheels
    ]
    if comparable_manifest != comparable_expected:
        raise OfflineWheelhouseError(
            "offline wheelhouse manifest does not match its sealed wheels and lock"
        )
    for entry in recorded_wheels:
        source = entry["source"]
        if source not in {
            "downloaded-hashed-wheel",
            "approved-locally-built-wheel",
        }:
            raise OfflineWheelhouseError(
                "offline wheelhouse manifest locked-wheel source is invalid"
            )
    return manifest


def build_offline_dependency_wheelhouse(
    *,
    python_executable: Path,
    constraints: Path,
    requirements_lock: Path,
    lock_metadata: Path,
    output_directory: Path,
    approved_local_wheel_directory: Path | None = None,
    host: TargetHost | None = None,
    download_runner: DownloadRunner = _default_download_runner,
    canonical_lock_validator: CanonicalLockValidator = validate_canonical_lock_inputs,
) -> dict[str, Any]:
    """Download, verify, and atomically publish the complete wheelhouse.

    ``download_runner`` and ``host`` are injectable so tests can exercise all
    failure cases without opening a network connection or requiring macOS.
    The output must be absent: a complete directory is published in one
    ``os.replace`` only after every wheel and the deterministic manifest have
    passed validation.
    """

    python_executable = python_executable.expanduser().absolute()
    constraints = constraints.expanduser().absolute()
    requirements_lock = requirements_lock.expanduser().absolute()
    lock_metadata = lock_metadata.expanduser().absolute()
    output_directory = output_directory.expanduser().absolute()
    if approved_local_wheel_directory is not None:
        approved_local_wheel_directory = approved_local_wheel_directory.expanduser().absolute()

    if output_directory.exists() or output_directory.is_symlink():
        raise OfflineWheelhouseError(
            f"offline wheelhouse output must be absent: {output_directory}"
        )
    _require_directory_non_symlink(output_directory.parent, "offline wheelhouse output parent")
    if host is None:
        host = inspect_target_host(python_executable)
    verify_target_host(host)
    lock_entries = parse_hashed_requirements_lock(requirements_lock)
    metadata = canonical_lock_validator(
        constraints=constraints,
        requirements_lock=requirements_lock,
        lock_metadata=lock_metadata,
        lock_entries=lock_entries,
    )
    if not isinstance(metadata, dict):
        raise OfflineWheelhouseError("canonical lock validator did not return metadata")
    approved_local_hashes = _validate_approved_local_wheels(
        approved_local_wheel_directory, lock_entries
    )

    with tempfile.TemporaryDirectory(
        prefix=f".{output_directory.name}.staging-", dir=output_directory.parent
    ) as temporary:
        temporary_root = Path(temporary)
        staged_output = temporary_root / output_directory.name
        staged_output.mkdir(mode=0o700)
        # Keep transient package-index downloads outside the published root.
        downloaded_directory = temporary_root / ".downloaded"
        downloaded_directory.mkdir(mode=0o700)
        staged_wheelhouse = staged_output / "wheels"
        staged_wheelhouse.mkdir(mode=0o700)
        command = build_download_command(
            python_executable=python_executable,
            requirements_lock=requirements_lock,
            destination=downloaded_directory,
            approved_local_wheel_directory=approved_local_wheel_directory,
        )
        completed = download_runner(
            command,
            cwd=staged_output,
            env=_clean_pip_environment(),
        )
        if completed.returncode != 0:
            raise OfflineWheelhouseError(
                "offline wheel download failed: "
                f"returncode={completed.returncode}; stdout={completed.stdout.strip()}; "
                f"stderr={completed.stderr.strip()}"
            )
        _stage_candidate_wheels(
            downloaded_directory=downloaded_directory,
            staged_wheelhouse=staged_wheelhouse,
        )
        wheels = _verify_complete_wheelhouse(
            staged_wheelhouse=staged_wheelhouse,
            lock_entries=lock_entries,
            approved_local_hashes=approved_local_hashes,
        )
        manifest = _manifest_payload(
            requirements_lock=requirements_lock,
            lock_metadata=lock_metadata,
            metadata=metadata,
            wheels=wheels,
        )
        (staged_output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        # The temporary directory lives beside output_directory, so this is a
        # same-filesystem atomic publication.  No incomplete output is visible.
        os.replace(staged_output, output_directory)

    return manifest


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a complete macOS arm64 / CPython 3.12 offline dependency "
            "wheelhouse from the canonical hashed lock."
        )
    )
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--constraints", type=Path, default=DEFAULT_CONSTRAINTS)
    parser.add_argument("--requirements-lock", type=Path, default=DEFAULT_REQUIREMENTS_LOCK)
    parser.add_argument("--lock-metadata", type=Path, default=DEFAULT_LOCK_METADATA)
    parser.add_argument(
        "--approved-local-wheel-directory",
        type=Path,
        help=(
            "Optional directory of prebuilt target wheels for locked dependencies "
            "that have no public binary wheel. Every wheel must match the lock hash."
        ),
    )
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="Verify an existing output read-only; do not inspect a host or download wheels.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.verify_existing:
            manifest = verify_existing_offline_dependency_wheelhouse(
                constraints=args.constraints,
                requirements_lock=args.requirements_lock,
                lock_metadata=args.lock_metadata,
                output_directory=args.output_directory,
            )
        else:
            manifest = build_offline_dependency_wheelhouse(
                python_executable=args.python,
                constraints=args.constraints,
                requirements_lock=args.requirements_lock,
                lock_metadata=args.lock_metadata,
                approved_local_wheel_directory=args.approved_local_wheel_directory,
                output_directory=args.output_directory,
            )
    except OfflineWheelhouseError as exc:
        print(f"offline dependency wheelhouse failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
