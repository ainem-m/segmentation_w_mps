#!/usr/bin/env python3
"""Generate the release-only macOS 14 / arm64 dependency-lock pair.

This command deliberately has a narrow execution envelope.  ``pip-compile``
does not provide a reliable cross-platform wheel-tag resolver, so a lock for
the distributed macOS 14 arm64 / CPython 3.12 app is generated only on that
exact platform.  It writes both related files by swapping their containing
directory with Darwin's ``renameatx_np(RENAME_SWAP)``.  Consumers additionally
verify a generation ID stored in both files, so a complete generation cannot be
mistaken for a mixed lock/metadata pair.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import importlib.metadata as importlib_metadata
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

try:
    from scripts.release_build_toolchain import (
        ReleaseBuildToolchainError,
        load_release_pre_sign_wheel_receipt,
        verify_release_pre_sign_wheel_receipt,
    )
    from scripts.verify_release_input_readiness import (
        CANONICAL_DEPENDENCY_LOCK_RESOLVER,
        CANONICAL_TARGET_COMPATIBILITY,
        BUNDLED_OVERRIDE_DISTRIBUTION_PINS,
        BUNDLED_OVERRIDE_RELEASE_HASH_BINDING,
        BUNDLED_OVERRIDE_ROLE,
        BUNDLED_OVERRIDE_SPECS,
        DEPENDENCY_LOCK_GENERATION_COMMENT_PREFIX,
        DEPENDENCY_LOCK_SCHEMA,
        MACOS_ARM64_SYSCONFIG_PLATFORM,
        MACOS_14_OR_LATER_FULL_VERSION,
        PIP_VERSION,
        ReleaseInputReadinessError,
        _logical_requirement_lines,
        _requirement_name,
        _requirement_name_and_version,
        verify_bundled_override_distribution_pins,
        verify_canonical_dependency_lock,
        verify_hashed_requirement_entries,
    )
except ModuleNotFoundError:  # Direct execution from scripts/.
    from release_build_toolchain import (  # type: ignore[no-redef]
        ReleaseBuildToolchainError,
        load_release_pre_sign_wheel_receipt,
        verify_release_pre_sign_wheel_receipt,
    )
    from verify_release_input_readiness import (  # type: ignore[no-redef]
        CANONICAL_DEPENDENCY_LOCK_RESOLVER,
        CANONICAL_TARGET_COMPATIBILITY,
        BUNDLED_OVERRIDE_DISTRIBUTION_PINS,
        BUNDLED_OVERRIDE_RELEASE_HASH_BINDING,
        BUNDLED_OVERRIDE_ROLE,
        BUNDLED_OVERRIDE_SPECS,
        DEPENDENCY_LOCK_GENERATION_COMMENT_PREFIX,
        DEPENDENCY_LOCK_SCHEMA,
        MACOS_ARM64_SYSCONFIG_PLATFORM,
        MACOS_14_OR_LATER_FULL_VERSION,
        PIP_VERSION,
        ReleaseInputReadinessError,
        _logical_requirement_lines,
        _requirement_name,
        _requirement_name_and_version,
        verify_bundled_override_distribution_pins,
        verify_canonical_dependency_lock,
        verify_hashed_requirement_entries,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONSTRAINTS = ROOT / "constraints" / "macos-arm64-py312.txt"
DEFAULT_REQUIREMENTS_LOCK = (
    ROOT / "constraints" / "macos-arm64-py312.requirements.lock"
)
DEFAULT_LOCK_METADATA = ROOT / "constraints" / "macos-arm64-py312.lock.json"
DEFAULT_PROJECT_FILE = ROOT / "pyproject.toml"
DEFAULT_SETUP_MANAGER_SOURCE = (
    ROOT / "src" / "totalsegmentator_wrapper_mac" / "setup_manager.py"
)
ROOT_INSTALL_REQUIREMENT = (
    "totalsegmentator-wrapper-mac[dicom,mps,dentalseg,toothseg,ios-meshsegnet]"
)
ROOT_EXTRAS = ("dicom", "mps", "dentalseg", "toothseg", "ios-meshsegnet")
LOCK_SCHEMA = DEPENDENCY_LOCK_SCHEMA
PIP_TOOLS_DISTRIBUTION = "pip-tools"
PIP_TOOLS_VERSION = "7.5.0"
PIP_DISTRIBUTION = "pip"
RENAME_SWAP = 0x00000002
RENAME_NOFOLLOW_ANY = 0x00000010


class LockGenerationError(RuntimeError):
    """The canonical lock could not be generated safely."""


@dataclass(frozen=True)
class ResolverHost:
    system: str
    machine: str
    python_implementation: str
    python_version: tuple[int, int, int]
    macos_version: str
    sysconfig_platform: str


@dataclass(frozen=True)
class BundledOverrideResolutionInput:
    distribution: str
    path: Path
    sha256: str
    metadata_sha256: str
    wheel_metadata_sha256: str


Runner = Callable[..., subprocess.CompletedProcess[str]]
DirectorySwap = Callable[[Path, Path], None]


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


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
        raise LockGenerationError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
        raise LockGenerationError(
            f"{label} must be a regular non-symlink file: {path}"
        )


def _require_clean_output(path: Path, label: str) -> None:
    """Accept an absent output or an existing regular non-symlink file."""

    try:
        entry = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
        raise LockGenerationError(
            f"{label} must be absent or a regular non-symlink file: {path}"
        )


def _require_clean_directory(path: Path, label: str) -> None:
    try:
        entry = path.lstat()
    except FileNotFoundError as exc:
        raise LockGenerationError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
        raise LockGenerationError(
            f"{label} must be a directory and not a symlink: {path}"
        )


def _normalize_distribution_name(value: str) -> str:
    return value.lower().replace("_", "-").replace(".", "-")


def _wheel_header_values(payload: str, field: str) -> list[str]:
    prefix = field + ":"
    return [
        line[len(prefix) :].strip()
        for line in payload.splitlines()
        if line.startswith(prefix)
    ]


def _validate_bundled_override_resolution_wheel(
    distribution: str,
    path: Path,
) -> BundledOverrideResolutionInput:
    """Validate one prebuilt wheel used solely to resolve the full graph.

    The source wheel is deliberately not the release artifact: Developer ID
    signing changes the fpsample wheel bytes later.  Its filename, metadata,
    and wheel tag still must match the exact separately bundled override.
    """

    expected = BUNDLED_OVERRIDE_SPECS[distribution]
    _require_regular_non_symlink(path, f"bundled override resolution wheel for {distribution}")
    if path.name != expected["filename"]:
        raise LockGenerationError(
            "bundled override resolution wheel filename mismatch for "
            f"{distribution}: expected {expected['filename']}, found {path.name}"
        )
    metadata_name = f"{expected['dist_info']}/METADATA"
    wheel_name = f"{expected['dist_info']}/WHEEL"
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            for info in infos:
                parts = Path(info.filename.replace("\\", "/")).parts
                if info.filename.startswith("/") or ".." in parts:
                    raise LockGenerationError(
                        f"bundled override resolution wheel has unsafe archive path: {distribution}"
                    )
            if archive.testzip() is not None:
                raise LockGenerationError(
                    f"bundled override resolution wheel CRC validation failed: {distribution}"
                )
            metadata_members = [
                info for info in infos if not info.is_dir() and info.filename == metadata_name
            ]
            wheel_members = [
                info for info in infos if not info.is_dir() and info.filename == wheel_name
            ]
            if len(metadata_members) != 1 or len(wheel_members) != 1:
                raise LockGenerationError(
                    f"bundled override resolution wheel metadata is missing or ambiguous: {distribution}"
                )
            metadata_bytes = archive.read(metadata_members[0])
            wheel_metadata_bytes = archive.read(wheel_members[0])
            metadata = metadata_bytes.decode("utf-8")
            wheel_metadata = wheel_metadata_bytes.decode("utf-8")
    except LockGenerationError:
        raise
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise LockGenerationError(
            f"bundled override resolution wheel is invalid: {distribution}"
        ) from exc
    names = _wheel_header_values(metadata, "Name")
    versions = _wheel_header_values(metadata, "Version")
    tags = _wheel_header_values(wheel_metadata, "Tag")
    if (
        len(names) != 1
        or _normalize_distribution_name(names[0]) != distribution
        or versions != [expected["version"]]
        or tags != [expected["wheel_tag"]]
    ):
        raise LockGenerationError(
            f"bundled override resolution wheel name/version/tag mismatch: {distribution}"
        )
    return BundledOverrideResolutionInput(
        distribution=distribution,
        path=path,
        sha256=_sha256_file(path),
        metadata_sha256=hashlib.sha256(metadata_bytes).hexdigest(),
        wheel_metadata_sha256=hashlib.sha256(wheel_metadata_bytes).hexdigest(),
    )


def resolve_bundled_override_resolution_wheels(
    wheel_directory: Path | None,
    pre_sign_wheel_receipt: Path | None,
) -> tuple[dict[str, BundledOverrideResolutionInput], dict[str, object], str]:
    """Require receipt-bound local wheels for the canonical resolver phase.

    The wheel hashes originate after the explicit bootstrap component builds;
    canonical lock metadata must never be asked to predict them first.
    """

    if wheel_directory is None:
        raise LockGenerationError(
            "explicit local bundled override resolution wheel directory is required"
        )
    wheel_directory = _absolute(wheel_directory)
    _require_clean_directory(
        wheel_directory, "bundled override resolution wheel directory"
    )
    # Validate the two concrete local artifacts before looking at their
    # receipt.  This keeps a missing/unsafe/wrong wheel a direct, actionable
    # error rather than masking it behind a later handoff artifact, while the
    # receipt remains mandatory before any resolver can be invoked.
    resolution_inputs = {
        name: _validate_bundled_override_resolution_wheel(
            name,
            wheel_directory / str(spec["filename"]),
        )
        for name, spec in sorted(BUNDLED_OVERRIDE_SPECS.items())
    }
    if pre_sign_wheel_receipt is None:
        raise LockGenerationError(
            "a sealed pre-sign wheel receipt is required before canonical dependency resolution"
        )
    try:
        receipt, receipt_sha256 = load_release_pre_sign_wheel_receipt(
            pre_sign_wheel_receipt
        )
        verified_receipt = verify_release_pre_sign_wheel_receipt(
            pre_sign_wheel_receipt_path=pre_sign_wheel_receipt,
            wheel_directory=wheel_directory,
        )
    except ReleaseBuildToolchainError as exc:
        raise LockGenerationError(f"pre-sign wheel receipt is invalid: {exc}") from exc
    if receipt != {key: value for key, value in verified_receipt.items() if key != "pre_sign_wheel_receipt_sha256"}:
        raise LockGenerationError("pre-sign wheel receipt verification is inconsistent")
    wheels = receipt.get("wheels")
    assert isinstance(wheels, dict)
    for name, item in resolution_inputs.items():
        expected = wheels.get(name)
        if not isinstance(expected, dict) or (
            item.sha256 != expected.get("sha256")
            or item.metadata_sha256 != expected.get("metadata_sha256")
            or item.wheel_metadata_sha256 != expected.get("wheel_metadata_sha256")
        ):
            raise LockGenerationError(
                f"pre-sign wheel receipt does not bind the local resolver wheel: {name}"
            )
    return resolution_inputs, receipt, receipt_sha256


def _stage_bundled_override_resolution_wheels(
    *,
    resolution_inputs: Mapping[str, BundledOverrideResolutionInput],
    destination: Path,
) -> Path:
    """Copy validated wheels to an owned immutable-by-convention resolver view.

    The caller's directory can be edited between its initial inspection and a
    `pip-compile` invocation.  Resolve from a freshly copied, hash-checked view
    instead, so the full-graph result is bound to the input digests recorded in
    metadata.  The staging directory is inside the generator's temporary
    directory and is removed before publication.
    """

    destination.mkdir(mode=0o700)
    for name, item in sorted(resolution_inputs.items()):
        source = item.path
        target = destination / source.name
        _require_regular_non_symlink(
            source, f"bundled override resolution wheel for {name}"
        )
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            source_fd = os.open(source, flags)
        except OSError as exc:
            raise LockGenerationError(
                f"could not securely read bundled override resolution wheel for {name}"
            ) from exc
        digest = hashlib.sha256()
        try:
            source_stat = os.fstat(source_fd)
            if not stat.S_ISREG(source_stat.st_mode):
                raise LockGenerationError(
                    f"bundled override resolution wheel for {name} became non-regular"
                )
            with os.fdopen(source_fd, "rb", closefd=False) as source_handle, target.open(
                "xb"
            ) as target_handle:
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    target_handle.write(chunk)
        finally:
            os.close(source_fd)
        if digest.hexdigest() != item.sha256:
            raise LockGenerationError(
                f"bundled override resolution wheel changed while staging: {name}"
            )
        _require_regular_non_symlink(
            target, f"staged bundled override resolution wheel for {name}"
        )
    return destination


def current_resolver_host() -> ResolverHost:
    version = sys.version_info
    return ResolverHost(
        system=platform.system(),
        machine=platform.machine(),
        python_implementation=platform.python_implementation(),
        python_version=(version.major, version.minor, version.micro),
        macos_version=platform.mac_ver()[0],
        sysconfig_platform=sysconfig.get_platform(),
    )


def validate_resolver_host(host: ResolverHost) -> None:
    """Require a native arm64 CPython host capable of target-14 resolution."""

    failures: list[str] = []
    if host.system != "Darwin":
        failures.append(f"host system is {host.system!r}, not Darwin")
    if host.machine != "arm64":
        failures.append(f"host architecture is {host.machine!r}, not arm64")
    if host.python_implementation != "CPython":
        failures.append(
            "Python implementation is "
            f"{host.python_implementation!r}, not CPython"
        )
    if host.python_version[:2] != (3, 12):
        failures.append(
            "Python version is "
            f"{host.python_version[0]}.{host.python_version[1]}, not 3.12"
        )
    if MACOS_14_OR_LATER_FULL_VERSION.fullmatch(host.macos_version) is None:
        failures.append(
            "host macOS version is "
            f"{host.macos_version or 'unknown'}, not macOS 14 or later"
        )
    if MACOS_ARM64_SYSCONFIG_PLATFORM.fullmatch(
        host.sysconfig_platform.lower()
    ) is None:
        failures.append(
            "Python sysconfig platform is "
            f"{host.sysconfig_platform!r}, not a macOS arm64 build"
        )
    if failures:
        raise LockGenerationError(
            "Refusing to generate the macOS 14 arm64 target lock because the "
            "current host cannot execute the target-compatible resolver:\n- "
            + "\n- ".join(failures)
            + "\nRun this command in CPython 3.12 on an Apple Silicon Mac running "
            "macOS 14 or later."
        )


def validate_pip_tools_version(version: str) -> None:
    if version != PIP_TOOLS_VERSION:
        raise LockGenerationError(
            "The canonical resolver must be pip-tools "
            f"{PIP_TOOLS_VERSION}; found {version!r}."
        )


def validate_pip_version(version: str) -> None:
    if PIP_VERSION.fullmatch(version) is None:
        raise LockGenerationError(
            f"The canonical resolver must report a concrete pip version; found {version!r}."
        )


def _validate_paths(
    *,
    constraints: Path,
    requirements_lock: Path,
    lock_metadata: Path,
    project_file: Path,
    setup_manager_source: Path,
) -> tuple[Path, Path, Path, Path, Path, Path]:
    constraints = _absolute(constraints)
    requirements_lock = _absolute(requirements_lock)
    lock_metadata = _absolute(lock_metadata)
    project_file = _absolute(project_file)
    setup_manager_source = _absolute(setup_manager_source)
    constraints_dir = constraints.parent

    _require_clean_directory(constraints_dir, "constraints directory")
    _require_regular_non_symlink(constraints, "source constraints")
    _require_regular_non_symlink(project_file, "project metadata")
    _require_regular_non_symlink(setup_manager_source, "setup manager source")
    if requirements_lock.parent != constraints_dir or lock_metadata.parent != constraints_dir:
        raise LockGenerationError(
            "source constraints, requirements lock, and lock metadata must be "
            "direct children of the same directory for atomic publication"
        )
    if requirements_lock == constraints or lock_metadata == constraints:
        raise LockGenerationError("lock output must not replace the source constraints")
    if requirements_lock == lock_metadata:
        raise LockGenerationError("requirements lock and metadata paths must differ")
    _require_clean_output(requirements_lock, "requirements lock output")
    _require_clean_output(lock_metadata, "lock metadata output")
    return (
        constraints,
        requirements_lock,
        lock_metadata,
        project_file,
        setup_manager_source,
        constraints_dir,
    )


def build_pip_compile_command(
    *,
    constraints: Path,
    project_file: Path,
    output_lock: Path,
    bundled_override_wheel_directory: Path,
    bundled_override_direct_reference_constraints: Path,
) -> list[str]:
    """Return the resolver command for the complete release dependency graph.

    ``pip-compile`` resolves the two separately bundled overrides through
    exact direct references to the owned staged wheels.  ``--find-links`` is
    retained only as an availability source; by itself it would not establish
    preference over an equally versioned PyPI candidate.  Every other binary
    dependency remains eligible for the pinned PyPI index.
    """

    command = [
        sys.executable,
        "-I",
        "-m",
        "piptools",
        "compile",
        "--generate-hashes",
        "--rebuild",
        "--resolver=backtracking",
        "--no-config",
        "--strip-extras",
        "--no-annotate",
        "--no-header",
        "--no-emit-options",
        "--no-emit-index-url",
        "--no-emit-trusted-host",
        "--pip-args="
        "--isolated --only-binary=:all: "
        f"--platform {CANONICAL_TARGET_COMPATIBILITY['platform']} "
        f"--implementation {CANONICAL_TARGET_COMPATIBILITY['implementation']} "
        f"--python-version {CANONICAL_TARGET_COMPATIBILITY['python_version']} "
        f"--abi {CANONICAL_TARGET_COMPATIBILITY['abi']}",
        "--index-url",
        "https://pypi.org/simple",
        "--find-links",
        str(bundled_override_wheel_directory),
        "--constraint",
        str(constraints),
        "--constraint",
        str(bundled_override_direct_reference_constraints),
    ]
    for extra in ROOT_EXTRAS:
        command.extend(("--extra", extra))
    command.extend(("--output-file", str(output_lock), str(project_file)))
    return command


def _clean_pip_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    result = dict(os.environ if environment is None else environment)
    for key in tuple(result):
        if key.upper().startswith("PIP_"):
            result.pop(key, None)
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "PYTHONSTARTUP",
        "PYTHONSAFEPATH",
        "VIRTUAL_ENV",
    ):
        result.pop(key, None)
    result.update(
        {
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PIP_ONLY_BINARY": ":all:",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return result


def _distribution_names(path: Path) -> list[str]:
    names = [_requirement_name(line) for line in _logical_requirement_lines(path)]
    if not names:
        raise LockGenerationError("generated requirements lock contains no distributions")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise LockGenerationError(
            "generated requirements lock has duplicate distribution entries: "
            + ", ".join(duplicates)
        )
    return names


_DIRECT_LOCAL_REFERENCE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s+@\s+(?P<url>file://\S+)$"
)
_LOCK_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)")


def _write_bundled_override_direct_reference_constraints(
    *,
    resolution_inputs: Mapping[str, BundledOverrideResolutionInput],
    staged_resolution_wheels: Path,
    destination: Path,
) -> tuple[Path, dict[str, str]]:
    """Write the resolver-only direct references for the two local overrides.

    The file stays inside the generator-owned temporary directory.  It is not
    published and the corresponding resolver-output blocks are removed before
    the install lock is generated.
    """

    _require_clean_output(
        destination, "bundled override direct-reference constraints output"
    )
    direct_urls: dict[str, str] = {}
    lines: list[str] = []
    for name, spec in sorted(BUNDLED_OVERRIDE_SPECS.items()):
        wheel = staged_resolution_wheels / str(spec["filename"])
        _require_regular_non_symlink(
            wheel, f"staged bundled override resolution wheel for {name}"
        )
        # The staged name is declared by the explicit validated resolution
        # input, rather than enumerating arbitrary caller-supplied wheelhouse
        # entries.
        if wheel.name != resolution_inputs[name].path.name:
            raise LockGenerationError(
                f"staged bundled override resolution wheel filename changed: {name}"
            )
        direct_url = wheel.as_uri()
        direct_urls[name] = direct_url
        lines.append(f"{name} @ {direct_url}")
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _require_regular_non_symlink(
        destination, "bundled override direct-reference constraints output"
    )
    return destination, direct_urls


def _parse_complete_resolver_requirement(
    *,
    line: str,
    allowed_override_direct_urls: Mapping[str, str],
    resolution_inputs: Mapping[str, BundledOverrideResolutionInput],
) -> tuple[str, str, bool]:
    """Parse one complete resolver line, allowing only two staged file URLs."""

    requirement = line.split(" --hash=", 1)[0].strip()
    direct = _DIRECT_LOCAL_REFERENCE.fullmatch(requirement)
    hashes = _LOCK_HASH.findall(line + " ")
    residual = _LOCK_HASH.sub("", line + " ").strip()
    if direct is None:
        if "file://" in requirement or " @ " in requirement:
            raise LockGenerationError(
                "pip-compile emitted an unapproved local direct reference: "
                + requirement
            )
        try:
            name, version = _requirement_name_and_version(line)
        except ReleaseInputReadinessError as exc:
            raise LockGenerationError(
                "pip-compile output is not a complete hashed requirements lock: "
                + str(exc)
            ) from exc
        if not hashes or residual != requirement:
            raise LockGenerationError(
                "pip-compile output is not a complete hashed requirements lock: "
                + requirement
            )
        return name, version, False

    name = _normalize_distribution_name(direct.group("name"))
    direct_url = direct.group("url")
    expected_url = allowed_override_direct_urls.get(name)
    if expected_url is None or direct_url != expected_url:
        raise LockGenerationError(
            "pip-compile emitted an unapproved local direct reference for "
            f"{name}: {direct_url}"
        )
    if residual != requirement:
        raise LockGenerationError(
            f"pip-compile direct reference contains unsupported lock tokens: {requirement}"
        )
    expected_hash = resolution_inputs[name].sha256
    if set(hashes) != {expected_hash}:
        raise LockGenerationError(
            "pip-compile direct reference hash does not bind the staged "
            f"bundled override wheel: {name}"
        )
    return name, BUNDLED_OVERRIDE_DISTRIBUTION_PINS[name], True


def _validate_complete_generated_lock(
    *,
    constraints: Path,
    requirements_lock: Path,
    allowed_override_direct_urls: Mapping[str, str],
    resolution_inputs: Mapping[str, BundledOverrideResolutionInput],
) -> tuple[list[str], dict[str, str]]:
    """Validate the full graph before removing resolver-only local overrides."""

    lines = _logical_requirement_lines(requirements_lock)
    if not lines:
        raise LockGenerationError("pip-compile output contains no requirements")
    parsed = [
        _parse_complete_resolver_requirement(
            line=line,
            allowed_override_direct_urls=allowed_override_direct_urls,
            resolution_inputs=resolution_inputs,
        )
        for line in lines
    ]
    lock_names = [name for name, _, _ in parsed]
    duplicates = sorted({name for name in lock_names if lock_names.count(name) > 1})
    if duplicates:
        raise LockGenerationError(
            "generated requirements lock has duplicate distribution entries: "
            + ", ".join(duplicates)
        )
    lock_pins = {name: version for name, version, _ in parsed}
    direct_names = {name for name, _, is_direct in parsed if is_direct}
    if direct_names != set(BUNDLED_OVERRIDE_DISTRIBUTION_PINS):
        missing = sorted(set(BUNDLED_OVERRIDE_DISTRIBUTION_PINS) - direct_names)
        unexpected = sorted(direct_names - set(BUNDLED_OVERRIDE_DISTRIBUTION_PINS))
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if unexpected:
            detail.append("unexpected " + ", ".join(unexpected))
        raise LockGenerationError(
            "pip-compile did not retain exactly the staged bundled override "
            "direct references: " + "; ".join(detail)
        )
    try:
        verify_bundled_override_distribution_pins(lock_pins)
    except ReleaseInputReadinessError as exc:
        raise LockGenerationError(str(exc)) from exc
    source_names = set(_distribution_names_from_constraints(constraints))
    if not source_names.issubset(set(lock_names)):
        missing = sorted(source_names - set(lock_names))
        raise LockGenerationError(
            "generated requirements lock omits source-constraint distributions: "
            + ", ".join(missing)
        )
    return sorted(lock_names), lock_pins


def _strip_bundled_override_requirement_blocks(
    *,
    complete_lock: Path,
    install_lock: Path,
) -> tuple[list[str], list[str]]:
    """Write a hashed install lock with only the exact two override blocks removed."""
    full_lines = _logical_requirement_lines(complete_lock)
    override_names = set(BUNDLED_OVERRIDE_DISTRIBUTION_PINS)
    removed_names = {
        _requirement_name(line)
        for line in full_lines
        if _requirement_name(line) in override_names
    }
    if removed_names != override_names:
        raise LockGenerationError(
            "complete generated lock does not contain exactly the bundled overrides"
        )
    install_lines = [
        line for line in full_lines if _requirement_name(line) not in override_names
    ]
    if not install_lines:
        raise LockGenerationError(
            "generated install lock would be empty after excluding bundled overrides"
        )
    install_lock.write_text("\n".join(install_lines) + "\n", encoding="utf-8")
    try:
        verify_hashed_requirement_entries(install_lock)
    except ReleaseInputReadinessError as exc:
        raise LockGenerationError(
            f"generated install lock is not complete after excluding bundled overrides: {exc}"
        ) from exc
    install_names = _distribution_names(install_lock)
    unexpected_overrides = sorted(override_names & set(install_names))
    if unexpected_overrides:
        raise LockGenerationError(
            "generated install lock still contains bundled overrides: "
            + ", ".join(unexpected_overrides)
        )
    full_names = [_requirement_name(line) for line in full_lines]
    if set(full_names) != set(install_names) | override_names:
        raise LockGenerationError(
            "generated install lock removed distributions other than bundled overrides"
        )
    return sorted(full_names), sorted(install_names)


def _distribution_names_from_constraints(path: Path) -> list[str]:
    try:
        names = [_requirement_name(line) for line in _logical_requirement_lines(path)]
    except ReleaseInputReadinessError as exc:
        raise LockGenerationError(f"could not parse source constraints: {exc}") from exc
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise LockGenerationError(
            "source constraints contain duplicate distribution entries: "
            + ", ".join(duplicates)
        )
    return names


def _excluded_bundled_override_metadata(
    resolution_inputs: Mapping[str, BundledOverrideResolutionInput],
) -> dict[str, dict[str, object]]:
    """Record the pre-sign resolver inputs without claiming release wheel hashes."""

    return {
        name: {
            "version": spec["version"],
            "role": BUNDLED_OVERRIDE_ROLE,
            "excluded_from_requirements_lock": True,
            "resolution_input_filename": spec["filename"],
            "resolution_input_sha256": resolution_inputs[name].sha256,
            "resolution_input_metadata_sha256": resolution_inputs[name].metadata_sha256,
            "resolution_input_wheel_metadata_sha256": resolution_inputs[
                name
            ].wheel_metadata_sha256,
            "release_wheel_hash_binding": BUNDLED_OVERRIDE_RELEASE_HASH_BINDING,
        }
        for name, spec in sorted(BUNDLED_OVERRIDE_SPECS.items())
    }


def _metadata_for(
    *,
    constraints: Path,
    project_file: Path,
    requirements_lock: Path,
    resolved_distribution_names: list[str],
    install_distribution_names: list[str],
    resolution_inputs: Mapping[str, BundledOverrideResolutionInput],
    pre_sign_wheel_receipt: Mapping[str, object],
    pre_sign_wheel_receipt_sha256: str,
    generation_id: str,
    host: ResolverHost,
    pip_version: str,
) -> dict[str, Any]:
    return {
        "schema": LOCK_SCHEMA,
        "bootstrap": {
            "schema": "totalsegmentator_wrapper_mac.dependency_lock_bootstrap_binding.v1",
            "source_identity_sha256": pre_sign_wheel_receipt[
                "source_identity_sha256"
            ],
            "sealed_toolchain": pre_sign_wheel_receipt["sealed_toolchain"],
            "pre_sign_wheel_receipt_sha256": pre_sign_wheel_receipt_sha256,
        },
        "generation_id": generation_id,
        "constraints_sha256": _sha256_file(constraints),
        "project_file": project_file.name,
        "project_file_sha256": _sha256_file(project_file),
        "requirements_lock": requirements_lock.name,
        "requirements_lock_sha256": _sha256_file(requirements_lock),
        "root_install_requirement": ROOT_INSTALL_REQUIREMENT,
        "resolved_distribution_names": resolved_distribution_names,
        "install_distribution_names": install_distribution_names,
        "excluded_bundled_overrides": _excluded_bundled_override_metadata(
            resolution_inputs
        ),
        "resolution_complete": True,
        "resolver": {
            **CANONICAL_DEPENDENCY_LOCK_RESOLVER,
            "pip_version": pip_version,
            "python_full_version": ".".join(map(str, host.python_version)),
            "macos_version": host.macos_version,
            "sysconfig_platform": host.sysconfig_platform,
            "target_compatibility": CANONICAL_TARGET_COMPATIBILITY,
        },
        "pip_require_hashes": True,
        "setup_consumes_requirements_lock": True,
    }


def _prepend_generation_marker(path: Path, generation_id: str) -> None:
    """Bind a generated lock to its metadata without changing pip semantics."""

    _require_regular_non_symlink(path, "generated requirements lock")
    content = path.read_text(encoding="utf-8")
    if DEPENDENCY_LOCK_GENERATION_COMMENT_PREFIX in content:
        raise LockGenerationError(
            "generated requirements lock already contains a generation ID marker"
        )
    path.write_text(
        DEPENDENCY_LOCK_GENERATION_COMMENT_PREFIX + generation_id + "\n" + content,
        encoding="utf-8",
    )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _copy_clean_tree(source: Path, destination: Path) -> None:
    """Copy only a regular, non-symlink tree into a newly created directory."""

    _require_clean_directory(source, "constraints directory")
    destination.mkdir(mode=0o700)
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        entry_mode = entry.lstat().st_mode
        target = destination / entry.name
        if stat.S_ISLNK(entry_mode):
            raise LockGenerationError(
                f"constraints directory contains a symlink and cannot be atomically copied: {entry}"
            )
        if stat.S_ISREG(entry_mode):
            shutil.copy2(entry, target, follow_symlinks=False)
        elif stat.S_ISDIR(entry_mode):
            _copy_clean_tree(entry, target)
        else:
            raise LockGenerationError(
                f"constraints directory contains an unsupported filesystem entry: {entry}"
            )
    shutil.copystat(source, destination, follow_symlinks=False)


def _remove_clean_tree(path: Path) -> None:
    """Remove only a regular, non-symlink directory tree controlled by this run."""

    _require_clean_directory(path, "staged constraints directory")
    for entry in sorted(path.iterdir(), key=lambda item: item.name):
        entry_mode = entry.lstat().st_mode
        if stat.S_ISLNK(entry_mode):
            raise LockGenerationError(
                f"refusing to remove staged symlink entry: {entry}"
            )
        if stat.S_ISREG(entry_mode):
            entry.unlink()
        elif stat.S_ISDIR(entry_mode):
            _remove_clean_tree(entry)
        else:
            raise LockGenerationError(
                f"refusing to remove unsupported staged entry: {entry}"
            )
    path.rmdir()


def _macos_atomic_directory_swap(live: Path, staged: Path) -> None:
    """Atomically exchange same-parent directories using Darwin renameatx_np."""

    if platform.system() != "Darwin":
        raise LockGenerationError(
            "Darwin renameatx_np(RENAME_SWAP) is required for atomic lock publication"
        )
    if live.parent != staged.parent:
        raise LockGenerationError("atomic directory swap requires sibling directories")
    try:
        renameatx_np = ctypes.CDLL(None, use_errno=True).renameatx_np
    except AttributeError as exc:
        raise LockGenerationError(
            "Darwin renameatx_np(RENAME_SWAP) is unavailable; refusing non-atomic publication"
        ) from exc
    renameatx_np.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameatx_np.restype = ctypes.c_int
    # Use an owned parent-directory descriptor instead of AT_FDCWD so both
    # names stay within the already validated sibling directory.
    directory_fd = os.open(
        live.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        result = renameatx_np(
            directory_fd,
            os.fsencode(live.name),
            directory_fd,
            os.fsencode(staged.name),
            RENAME_SWAP | RENAME_NOFOLLOW_ANY,
        )
    finally:
        os.close(directory_fd)
    if result != 0:
        error_number = ctypes.get_errno()
        raise LockGenerationError(
            "Darwin atomic directory swap failed: "
            f"{os.strerror(error_number or errno.EIO)}"
        )


def _publish_pair_atomically(
    *,
    constraints_dir: Path,
    constraints: Path,
    project_file: Path,
    requirements_lock: Path,
    lock_metadata: Path,
    generated_lock: Path,
    generated_metadata: Path,
    setup_manager_source: Path,
    expected_constraints_sha256: str,
    expected_project_file_sha256: str,
    directory_swap: DirectorySwap,
) -> None:
    """Publish a prevalidated pair by swapping the complete constraints directory."""

    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{constraints_dir.name}.lock-publish-",
            dir=constraints_dir.parent,
        )
    )
    swapped = False
    try:
        # tempfile created the directory; replace it with a regular copy of the
        # complete directory so unrelated future constraint files are retained.
        stage.rmdir()
        _copy_clean_tree(constraints_dir, stage)
        staged_lock = stage / requirements_lock.name
        staged_metadata = stage / lock_metadata.name
        shutil.copy2(generated_lock, staged_lock, follow_symlinks=False)
        shutil.copy2(generated_metadata, staged_metadata, follow_symlinks=False)
        try:
            verify_canonical_dependency_lock(
                constraints=stage / constraints.name,
                requirements_lock=staged_lock,
                lock_metadata=staged_metadata,
                project_file=project_file,
                setup_manager_source=setup_manager_source,
            )
        except ReleaseInputReadinessError as exc:
            raise LockGenerationError(
                f"staged lock pair does not satisfy the release contract: {exc}"
            ) from exc
        if _sha256_file(constraints) != expected_constraints_sha256:
            raise LockGenerationError(
                "source constraints changed while resolving; refusing to publish a stale lock"
            )
        if _sha256_file(project_file) != expected_project_file_sha256:
            raise LockGenerationError(
                "project dependency declarations changed while resolving; refusing to publish a stale lock"
            )
        directory_swap(constraints_dir, stage)
        swapped = True
        try:
            verify_canonical_dependency_lock(
                constraints=constraints,
                requirements_lock=requirements_lock,
                lock_metadata=lock_metadata,
                project_file=project_file,
                setup_manager_source=setup_manager_source,
            )
        except ReleaseInputReadinessError as exc:
            # This should be unreachable after validation of exactly the staged
            # bytes.  Swap back atomically rather than leaving a questionable
            # new pair live.
            directory_swap(constraints_dir, stage)
            swapped = False
            raise LockGenerationError(
                f"published lock pair failed post-publication validation: {exc}"
            ) from exc
    finally:
        if stage.exists() or stage.is_symlink():
            if swapped:
                # The stage now contains the old live directory. It has already
                # been copied and validated, and is removed only after the new
                # pair has passed the post-publication contract check.
                _remove_clean_tree(stage)
            else:
                _remove_clean_tree(stage)


def generate_canonical_dependency_lock(
    *,
    constraints: Path = DEFAULT_CONSTRAINTS,
    requirements_lock: Path = DEFAULT_REQUIREMENTS_LOCK,
    lock_metadata: Path = DEFAULT_LOCK_METADATA,
    project_file: Path = DEFAULT_PROJECT_FILE,
    setup_manager_source: Path = DEFAULT_SETUP_MANAGER_SOURCE,
    bundled_override_wheel_directory: Path | None = None,
    pre_sign_wheel_receipt: Path | None = None,
    host: ResolverHost | None = None,
    pip_tools_version: str | None = None,
    pip_version: str | None = None,
    runner: Runner = subprocess.run,
    directory_swap: DirectorySwap = _macos_atomic_directory_swap,
) -> dict[str, Any]:
    """Resolve, validate, and atomically publish the canonical lock pair.

    ``runner`` and ``directory_swap`` are dependency-injected solely so the
    offline regression suite can exercise staging/publication without fetching
    packages or calling Darwin-only syscalls.
    """

    resolver_host = host or current_resolver_host()
    validate_resolver_host(resolver_host)
    try:
        actual_pip_tools_version = (
            pip_tools_version
            if pip_tools_version is not None
            else importlib_metadata.version(PIP_TOOLS_DISTRIBUTION)
        )
    except importlib_metadata.PackageNotFoundError as exc:
        raise LockGenerationError(
            f"{PIP_TOOLS_DISTRIBUTION} {PIP_TOOLS_VERSION} is required to generate the canonical lock"
        ) from exc
    validate_pip_tools_version(actual_pip_tools_version)
    try:
        actual_pip_version = (
            pip_version
            if pip_version is not None
            else importlib_metadata.version(PIP_DISTRIBUTION)
        )
    except importlib_metadata.PackageNotFoundError as exc:
        raise LockGenerationError(
            f"{PIP_DISTRIBUTION} is required to generate the canonical lock"
        ) from exc
    validate_pip_version(actual_pip_version)
    (
        constraints,
        requirements_lock,
        lock_metadata,
        project_file,
        setup_manager_source,
        constraints_dir,
    ) = _validate_paths(
        constraints=constraints,
        requirements_lock=requirements_lock,
        lock_metadata=lock_metadata,
        project_file=project_file,
        setup_manager_source=setup_manager_source,
    )
    (
        resolution_inputs,
        verified_pre_sign_wheel_receipt,
        pre_sign_wheel_receipt_sha256,
    ) = resolve_bundled_override_resolution_wheels(
        bundled_override_wheel_directory, pre_sign_wheel_receipt
    )
    source_constraints_sha256 = _sha256_file(constraints)
    source_project_file_sha256 = _sha256_file(project_file)

    with tempfile.TemporaryDirectory(
        prefix=".dependency-lock-compile-", dir=constraints_dir.parent
    ) as temporary:
        temporary_dir = Path(temporary)
        generated_lock = temporary_dir / requirements_lock.name
        complete_resolved_lock = temporary_dir / "complete-resolved.requirements.lock"
        staged_resolution_wheels = _stage_bundled_override_resolution_wheels(
            resolution_inputs=resolution_inputs,
            destination=temporary_dir / "bundled-override-resolver-wheels",
        )
        direct_reference_constraints, allowed_override_direct_urls = (
            _write_bundled_override_direct_reference_constraints(
                resolution_inputs=resolution_inputs,
                staged_resolution_wheels=staged_resolution_wheels,
                destination=(
                    temporary_dir
                    / "bundled-override-direct-reference-constraints.txt"
                ),
            )
        )
        command = build_pip_compile_command(
            constraints=constraints,
            project_file=project_file,
            output_lock=complete_resolved_lock,
            bundled_override_wheel_directory=staged_resolution_wheels,
            bundled_override_direct_reference_constraints=direct_reference_constraints,
        )
        completed = runner(
            command,
            cwd=project_file.parent,
            env=_clean_pip_environment(),
            capture_output=True,
            text=True,
            check=False,
        )
        if not isinstance(completed, subprocess.CompletedProcess):
            raise LockGenerationError("pip-compile runner returned an invalid result")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "no diagnostic output").strip()
            raise LockGenerationError(
                f"pip-compile failed with exit status {completed.returncode}: {detail}"
            )
        _require_regular_non_symlink(
            complete_resolved_lock, "complete generated requirements lock"
        )
        _validate_complete_generated_lock(
            constraints=constraints,
            requirements_lock=complete_resolved_lock,
            allowed_override_direct_urls=allowed_override_direct_urls,
            resolution_inputs=resolution_inputs,
        )
        resolved_distribution_names, install_distribution_names = (
            _strip_bundled_override_requirement_blocks(
                complete_lock=complete_resolved_lock,
                install_lock=generated_lock,
            )
        )
        generation_id = str(uuid4())
        _prepend_generation_marker(generated_lock, generation_id)
        generated_metadata = temporary_dir / lock_metadata.name
        metadata = _metadata_for(
            constraints=constraints,
            project_file=project_file,
            requirements_lock=generated_lock,
            resolved_distribution_names=resolved_distribution_names,
            install_distribution_names=install_distribution_names,
            resolution_inputs=resolution_inputs,
            pre_sign_wheel_receipt=verified_pre_sign_wheel_receipt,
            pre_sign_wheel_receipt_sha256=pre_sign_wheel_receipt_sha256,
            generation_id=generation_id,
            host=resolver_host,
            pip_version=actual_pip_version,
        )
        _write_json(generated_metadata, metadata)
        try:
            verify_canonical_dependency_lock(
                constraints=constraints,
                requirements_lock=generated_lock,
                lock_metadata=generated_metadata,
                project_file=project_file,
                setup_manager_source=setup_manager_source,
                pre_sign_wheel_receipt=pre_sign_wheel_receipt,
            )
        except ReleaseInputReadinessError as exc:
            raise LockGenerationError(
                f"generated lock pair does not satisfy the release contract: {exc}"
            ) from exc
        _publish_pair_atomically(
            constraints_dir=constraints_dir,
            constraints=constraints,
            project_file=project_file,
            requirements_lock=requirements_lock,
            lock_metadata=lock_metadata,
            generated_lock=generated_lock,
            generated_metadata=generated_metadata,
            setup_manager_source=setup_manager_source,
            expected_constraints_sha256=source_constraints_sha256,
            expected_project_file_sha256=source_project_file_sha256,
            directory_swap=directory_swap,
        )
    return metadata


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the canonical macOS 14 arm64 CPython 3.12 dependency lock."
    )
    parser.add_argument("--constraints", type=Path, default=DEFAULT_CONSTRAINTS)
    parser.add_argument("--requirements-lock", type=Path, default=DEFAULT_REQUIREMENTS_LOCK)
    parser.add_argument("--lock-metadata", type=Path, default=DEFAULT_LOCK_METADATA)
    parser.add_argument("--project-file", type=Path, default=DEFAULT_PROJECT_FILE)
    parser.add_argument(
        "--setup-manager-source", type=Path, default=DEFAULT_SETUP_MANAGER_SOURCE
    )
    parser.add_argument(
        "--bundled-override-wheel-directory",
        type=Path,
        required=True,
        help=(
            "directory containing the exact prebuilt fpsample and acvl-utils "
            "resolver wheels; these are not copied into the lock"
        ),
    )
    parser.add_argument(
        "--pre-sign-wheel-receipt",
        type=Path,
        required=True,
        help=(
            "sealed receipt emitted after the fpsample and acvl-utils bootstrap "
            "builds; binds their pre-sign wheel bytes before lock generation"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        metadata = generate_canonical_dependency_lock(
            constraints=args.constraints,
            requirements_lock=args.requirements_lock,
            lock_metadata=args.lock_metadata,
            project_file=args.project_file,
            setup_manager_source=args.setup_manager_source,
            bundled_override_wheel_directory=args.bundled_override_wheel_directory,
            pre_sign_wheel_receipt=args.pre_sign_wheel_receipt,
        )
    except LockGenerationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
