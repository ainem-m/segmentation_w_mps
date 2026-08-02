from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import platform
import re
import subprocess
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator
from uuid import UUID, uuid4

from totalsegmentator_wrapper_mac.dicom_normalizer_bridge import inspect_dicom_normalizer
from totalsegmentator_wrapper_mac.totalseg_weights_setup import setup_weight_manifest_sha256


APP_SUPPORT_NAME = "TotalSegmentatorWrapperMac"
SETUP_STATE_FILENAME = "setup_state.json"
FORBIDDEN_COMMAND_PARTS = {
    "sudo",
    "brew",
    "port",
}
FORBIDDEN_WRITE_PREFIXES = (
    Path("/usr/local"),
    Path("/opt/homebrew"),
    Path("/Library"),
    Path("/System"),
)
DEFAULT_TOTALSEG_WEIGHT_TASK_IDS = (115, 297, 113)
DENTALSEGMENTATOR_DATASET_ID = "112"
DENTALSEGMENTATOR_DATASET_NAME = "Dataset112_DentalSegmentator_v100"
DENTALSEGMENTATOR_MODEL_FILENAME = "Dataset112_DentalSegmentator_v100.zip"
DENTALSEGMENTATOR_MODEL_MD5 = "b71cd5230168d28a4f71b078265b76be"
DENTALSEGMENTATOR_MODEL_URL = (
    "https://zenodo.org/api/records/10829675/files/"
    "Dataset112_DentalSegmentator_v100.zip/content"
)
DENTALSEGMENTATOR_ZENODO_DOI = "10.5281/zenodo.10829675"
TOOTHSEG_MODEL_FILENAME = "ToothSeg.zip"
TOOTHSEG_MODEL_MD5 = "5d8dd061cce9529943567aeba3271143"
TOOTHSEG_MODEL_URL = "https://zenodo.org/records/14893540/files/ToothSeg.zip?download=1"
TOOTHSEG_ZENODO_DOI = "10.5281/zenodo.14893540"
SETUP_ATTEMPT_ID_ENV = "TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_ATTEMPT_ID"
SETUP_LOCK_FILENAME = ".totalsegmentator-wrapper-setup.lock"
SETUP_PARENT_LOCK_TOKEN_ENV = "TOTALSEGMENTATOR_WRAPPER_MAC_PARENT_SETUP_LOCK_TOKEN"
SETUP_PARENT_LOCK_PID_ENV = "TOTALSEGMENTATOR_WRAPPER_MAC_PARENT_SETUP_LOCK_PID"
BUNDLED_WHEEL_SPECS = (
    (
        "fpsample_wheel",
        "fpsample_wheel_sha256",
        "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl",
    ),
    (
        "acvl_utils_wheel",
        "acvl_utils_wheel_sha256",
        "acvl_utils-0.2.6-py3-none-any.whl",
    ),
)
WRAPPER_WHEEL_PREFIX = "totalsegmentator_wrapper_mac-"
WRAPPER_WHEEL_SUFFIX = ".whl"
DEPENDENCY_LOCK_SCHEMA = "totalsegmentator_wrapper_mac.dependency_lock.v3"
DEPENDENCY_LOCK_ROOT_REQUIREMENT = (
    "totalsegmentator-wrapper-mac[dicom,mps,dentalseg,toothseg,ios-meshsegnet]"
)
CANONICAL_DEPENDENCY_LOCK_RESOLVER = {
    "name": "pip-compile",
    "version": "7.5.0",
    "platform": "macos-14-arm64",
    "python": "3.12",
}
BUNDLED_OVERRIDE_DISTRIBUTION_PINS = {
    "acvl-utils": "0.2.6",
    "fpsample": "1.0.2",
}
BUNDLED_OVERRIDE_ROLE = "separately_bundled_no_deps_override"
BUNDLED_OVERRIDE_RELEASE_HASH_BINDING = "setup_manifest_after_signing"
BUNDLED_OVERRIDE_METADATA_FIELDS = {
    "version",
    "role",
    "excluded_from_requirements_lock",
    "resolution_input_filename",
    "resolution_input_sha256",
    "resolution_input_metadata_sha256",
    "resolution_input_wheel_metadata_sha256",
    "release_wheel_hash_binding",
}
BUNDLED_OVERRIDE_SPECS = {
    "acvl-utils": {
        "filename": "acvl_utils-0.2.6-py3-none-any.whl",
        "version": "0.2.6",
    },
    "fpsample": {
        "filename": "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl",
        "version": "1.0.2",
    },
}
DEPENDENCY_LOCK_METADATA_FIELDS = {
    "schema",
    "generation_id",
    "constraints_sha256",
    "project_file",
    "project_file_sha256",
    "requirements_lock",
    "requirements_lock_sha256",
    "root_install_requirement",
    "resolved_distribution_names",
    "install_distribution_names",
    "excluded_bundled_overrides",
    "resolution_complete",
    "resolver",
    "pip_require_hashes",
    "setup_consumes_requirements_lock",
}
DEPENDENCY_LOCK_GENERATION_COMMENT_PREFIX = (
    "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
)
DEPENDENCY_LOCK_RESOLVER_OBSERVED_FIELDS = {
    "pip_version",
    "python_full_version",
    "macos_version",
    "sysconfig_platform",
}
EXACT_LOCK_REQUIREMENT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?==[^\s;]+"
    r"(?:\s*;\s*[^\s].*)?$"
)
LOCK_HASH_TOKEN = re.compile(r"--hash=sha256:[0-9a-f]{64}(?:\s|$)")
EXACT_LOCK_PINNED_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9_,.-]+\])?==([^\s;]+)$"
)
PIP_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)+(?:[A-Za-z0-9.+-]+)?$")
PYTHON_312_FULL_VERSION = re.compile(r"^3\.12\.(?:0|[1-9][0-9]*)$")
MACOS_14_FULL_VERSION = re.compile(r"^14\.(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
MACOS_ARM64_SYSCONFIG_PLATFORM = re.compile(r"^macosx-14(?:\.[0-9]+)*-arm64$")


class BundleResourceValidationError(ValueError):
    """A packaged resource cannot be used safely for setup.

    The exception message is intentionally a stable code rather than a local
    path. Callers write only a generic diagnostic into user-visible setup
    state; detailed filesystem paths must stay out of that JSON contract.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SetupBusyError(RuntimeError):
    pass


class SetupLockError(RuntimeError):
    pass


CommandRunner = Callable[[list[str], Path | None, dict[str, str] | None], subprocess.CompletedProcess[str]]
PythonInspector = Callable[[Path], dict[str, Any]]


@dataclass(frozen=True)
class SetupPaths:
    app_support: Path
    env_dir: Path
    wheels_dir: Path
    models_dir: Path
    cases_dir: Path
    logs_dir: Path
    cache_dir: Path
    state_json: Path

    def to_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


@dataclass
class SetupStep:
    name: str
    status: str
    command: list[str] = field(default_factory=list)
    elapsed_seconds: float | None = None
    returncode: int | None = None
    error: str | None = None
    diagnostic_log: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SetupResult:
    status: str
    reason: str | None
    paths: SetupPaths
    steps: list[SetupStep]
    python: str
    platform: str
    machine: str
    allow_network: bool
    dry_run: bool
    wheel: str | None
    doctor: dict[str, Any] | None = None
    dicom_normalizer: dict[str, Any] | None = None
    python_executable: str | None = None
    python_version: str | None = None
    venv_reused: bool = False
    wheel_install_mode: str | None = None
    constraints: str | None = None
    requirements_lock: str | None = None
    installed_bundle: dict[str, Any] | None = None
    setup_attempt_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "totalsegmentator_wrapper_mac.setup_state.v1",
            "status": self.status,
            "reason": self.reason,
            "paths": self.paths.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "python": self.python,
            "platform": self.platform,
            "machine": self.machine,
            "allow_network": self.allow_network,
            "dry_run": self.dry_run,
            "wheel": self.wheel,
            "doctor": self.doctor,
            "dicom_normalizer": self.dicom_normalizer,
            "python_executable": self.python_executable,
            "python_version": self.python_version,
            "venv_reused": self.venv_reused,
            "wheel_install_mode": self.wheel_install_mode,
            "constraints": self.constraints,
            "requirements_lock": self.requirements_lock,
            "installed_bundle": self.installed_bundle,
            "setup_attempt_id": self.setup_attempt_id,
        }


def default_app_support_dir(home: Path | None = None) -> Path:
    root = home or Path.home()
    return root / "Library" / "Application Support" / APP_SUPPORT_NAME


def setup_paths(app_support_dir: Path | None = None, *, home: Path | None = None) -> SetupPaths:
    app_support = (app_support_dir or default_app_support_dir(home)).expanduser()
    return SetupPaths(
        app_support=app_support,
        env_dir=app_support / "env",
        wheels_dir=app_support / "wheels",
        models_dir=app_support / "models",
        cases_dir=app_support / "cases",
        logs_dir=app_support / "logs",
        cache_dir=app_support / "cache",
        state_json=app_support / SETUP_STATE_FILENAME,
    )


@contextmanager
def exclusive_app_setup_lock(app_support: Path) -> Iterator[None]:
    """Reject concurrent setup writers without waiting or overwriting their state."""

    app_support.mkdir(parents=True, exist_ok=True)
    lock_path = app_support / SETUP_LOCK_FILENAME
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        file_status = os.fstat(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    if (
        not stat.S_ISREG(file_status.st_mode)
        or file_status.st_uid != os.geteuid()
        or file_status.st_nlink != 1
        or file_status.st_mode & 0o022
    ):
        os.close(descriptor)
        raise SetupLockError("application setup lock is not a private regular file")
    handle: BinaryIO
    with os.fdopen(descriptor, "a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            if _parent_setup_lock_matches(lock_path):
                # The native launcher holds the same lock across venv/bootstrap and
                # delegates only to its direct Python child using a per-run token.
                yield
                return
            raise SetupBusyError("application setup is already running") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _parent_setup_lock_matches(lock_path: Path) -> bool:
    token = os.environ.get(SETUP_PARENT_LOCK_TOKEN_ENV, "")
    parent_pid = os.environ.get(SETUP_PARENT_LOCK_PID_ENV, "")
    if (
        not token
        or len(token) > 128
        or not all(character.isalnum() or character in {"-", "_", "."} for character in token)
        or parent_pid != str(os.getppid())
    ):
        return False
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("schema") == "totalsegmentator_wrapper_mac.parent_setup_lock.v1"
        and payload.get("token") == token
        and str(payload.get("pid")) == parent_pid
    )


def validate_app_support_path(paths: SetupPaths, *, home: Path | None = None) -> None:
    expected = default_app_support_dir(home).resolve()
    actual = paths.app_support.resolve()
    if actual != expected:
        raise ValueError(f"app support directory must be {expected}; got {actual}")
    for path in paths.to_dict().values():
        resolved = Path(path).resolve()
        if not _is_relative_to(resolved, expected):
            raise ValueError(f"setup path escapes app support directory: {resolved}")
        for prefix in FORBIDDEN_WRITE_PREFIXES:
            if _is_relative_to(resolved, prefix):
                raise ValueError(f"setup path uses forbidden system prefix: {resolved}")


def create_setup_directories(paths: SetupPaths, *, dry_run: bool) -> SetupStep:
    step = SetupStep(name="create_app_support_dirs", status="skipped" if dry_run else "success")
    if dry_run:
        return step
    for path in (
        paths.app_support,
        paths.env_dir,
        paths.wheels_dir,
        paths.models_dir,
        dentalsegmentator_model_root(paths),
        dentalsegmentator_model_root(paths) / "nnUNet_raw",
        dentalsegmentator_model_root(paths) / "nnUNet_preprocessed",
        dentalsegmentator_model_root(paths) / "nnUNet_results",
        toothseg_model_root(paths),
        toothseg_model_root(paths) / "nnUNet_results",
        paths.cases_dir,
        paths.logs_dir,
        paths.cache_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return step


def build_venv_command(python_executable: Path, env_dir: Path) -> list[str]:
    command = [str(python_executable), "-I", "-m", "venv", str(env_dir)]
    validate_safe_command(command)
    return command


def _isolated_pip_command(venv_python: Path) -> list[str]:
    """Use the managed interpreter and ignore all user pip configuration."""

    return [str(venv_python), "-I", "-m", "pip", "--isolated"]


def build_wheel_install_command(
    venv_python: Path,
    wheel: Path,
    *,
    allow_network: bool,
    constraints: Path | None = None,
) -> list[str]:
    target = str(wheel)
    if allow_network:
        target = f"{target}[dicom,mps,dentalseg,toothseg,ios-meshsegnet]"
        command = [
            *_isolated_pip_command(venv_python),
            "install",
            "--find-links",
            str(wheel.parent),
            "--only-binary",
            ":all:",
        ]
        if constraints is not None:
            command.extend(["-c", str(constraints)])
        command.append(target)
    else:
        command = [
            *_isolated_pip_command(venv_python),
            "install",
            "--no-deps",
            str(wheel),
        ]
    validate_safe_command(command)
    return command


def build_bundled_wheels_install_command(
    venv_python: Path,
    bundled_wheels: tuple[Path, ...],
) -> list[str]:
    if not bundled_wheels:
        raise ValueError("at least one bundled wheel is required")
    command = [
        *_isolated_pip_command(venv_python),
        "install",
        "--force-reinstall",
        "--no-deps",
        *(str(path) for path in bundled_wheels),
    ]
    validate_safe_command(command)
    return command


def build_locked_dependencies_install_command(
    venv_python: Path,
    *,
    requirements_lock: Path,
    wheel_directory: Path,
) -> list[str]:
    command = [
        *_isolated_pip_command(venv_python),
        "install",
        "--require-hashes",
        "--no-deps",
        "--only-binary",
        ":all:",
        "--find-links",
        str(wheel_directory),
        "-r",
        str(requirements_lock),
    ]
    validate_safe_command(command)
    return command


def build_pip_check_command(venv_python: Path) -> list[str]:
    command = [*_isolated_pip_command(venv_python), "check"]
    validate_safe_command(command)
    return command


def resolve_bundled_wheels(
    manifest_path: Path,
    manifest: dict[str, Any],
) -> tuple[Path, ...]:
    resources, resolved_resources = _bundle_resources_root(manifest_path)
    bundled = manifest.get("bundled")
    if not isinstance(bundled, dict):
        raise ValueError("bundle manifest bundled wheel map is missing")

    resolved_wheels: list[Path] = []
    for path_key, sha_key, expected_name in BUNDLED_WHEEL_SPECS:
        relative_value = bundled.get(path_key)
        expected_sha256 = manifest.get(sha_key)
        if not isinstance(relative_value, str) or not relative_value:
            raise ValueError(f"bundle manifest bundled.{path_key} is missing")
        if not _is_lowercase_sha256(expected_sha256):
            raise ValueError(f"bundle manifest {sha_key} is invalid")

        candidate = _resolve_bundle_regular_file(
            resources,
            resolved_resources,
            relative_value,
            field=f"bundled.{path_key}",
        )
        if candidate.name != expected_name:
            raise ValueError(
                f"bundled wheel filename mismatch for {path_key}: {candidate.name}"
            )
        if _sha256_file(candidate) != expected_sha256:
            raise ValueError(f"bundled wheel SHA-256 mismatch: {relative_value}")
        resolved_wheels.append(candidate.resolve(strict=True))
    return tuple(resolved_wheels)


def resolve_bundled_setup_resources(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    wheel: Path | None,
    constraints: Path | None,
) -> tuple[Path, Path, Path | None]:
    """Resolve only the manifest-bound wrapper wheel and constraints file.

    This is deliberately stricter than a normal pip invocation: the wheel
    selected by the app must be the sole wrapper wheel beneath
    ``Resources/wheels`` and the supplied command-line paths must be the exact
    manifest-owned files.  It runs before venv creation or pip/network work.
    """

    manifest_path = _absolute_path_without_resolving(manifest_path)
    if not _is_regular_file_without_symlink(manifest_path):
        raise BundleResourceValidationError("bundle_manifest_file_invalid")
    resources, resolved_resources = _bundle_resources_root(manifest_path)
    bundled = manifest.get("bundled")
    if not isinstance(bundled, dict):
        raise BundleResourceValidationError("bundle_manifest_bundled_missing")
    release_manifest = (
        manifest.get("signing_mode") == "developer-id"
        or manifest.get("notarized") is True
    )

    wrapper_name = bundled.get("wheel")
    if not _is_safe_wrapper_wheel_basename(wrapper_name):
        raise BundleResourceValidationError("bundle_wrapper_wheel_path_invalid")
    if release_manifest:
        expected_release_wheel = (
            "totalsegmentator_wrapper_mac-"
            f"{manifest.get('app_version')}-cp312-cp312-macosx_14_0_arm64.whl"
        )
        if wrapper_name != expected_release_wheel:
            raise BundleResourceValidationError(
                "bundle_release_wrapper_wheel_identity_invalid"
            )
    expected_wheel_sha256 = manifest.get("wheel_sha256")
    if not _is_lowercase_sha256(expected_wheel_sha256):
        raise BundleResourceValidationError("bundle_wrapper_wheel_sha256_invalid")

    wheels_dir = resources / "wheels"
    if not _is_directory_without_symlink(wheels_dir):
        raise BundleResourceValidationError("bundle_wrapper_wheel_directory_invalid")
    try:
        wrapper_entries = [
            item
            for item in wheels_dir.iterdir()
            if _looks_like_wrapper_wheel(item.name)
        ]
    except OSError as exc:
        raise BundleResourceValidationError("bundle_wrapper_wheel_directory_unreadable") from exc
    if len(wrapper_entries) != 1 or wrapper_entries[0].name != wrapper_name:
        raise BundleResourceValidationError("bundle_wrapper_wheel_ambiguous")

    expected_wheel = _resolve_bundle_regular_file(
        resources,
        resolved_resources,
        f"wheels/{wrapper_name}",
        field="bundled.wheel",
        error_type=BundleResourceValidationError,
    )
    if _sha256_file(expected_wheel) != expected_wheel_sha256:
        raise BundleResourceValidationError("bundle_wrapper_wheel_sha256_mismatch")
    _require_supplied_bundle_file(
        wheel,
        expected_wheel,
        code="bundle_wrapper_wheel_argument_mismatch",
    )

    constraints_relative = bundled.get("constraints")
    if not isinstance(constraints_relative, str):
        raise BundleResourceValidationError("bundle_constraints_path_invalid")
    expected_constraints_sha256 = manifest.get("constraints_sha256")
    if not _is_lowercase_sha256(expected_constraints_sha256):
        raise BundleResourceValidationError("bundle_constraints_sha256_invalid")
    expected_constraints = _resolve_bundle_regular_file(
        resources,
        resolved_resources,
        constraints_relative,
        field="bundled.constraints",
        error_type=BundleResourceValidationError,
    )
    if _sha256_file(expected_constraints) != expected_constraints_sha256:
        raise BundleResourceValidationError("bundle_constraints_sha256_mismatch")
    _require_supplied_bundle_file(
        constraints,
        expected_constraints,
        code="bundle_constraints_argument_mismatch",
    )
    lock_relative = bundled.get("requirements_lock")
    lock_sha256 = manifest.get("requirements_lock_sha256")
    metadata_relative = bundled.get("dependency_lock_metadata")
    metadata_sha256 = manifest.get("dependency_lock_metadata_sha256")
    project_relative = bundled.get("project_file")
    project_sha256 = manifest.get("project_file_sha256")
    lock_fields_absent = all(
        value is None
        for value in (
            lock_relative,
            lock_sha256,
            metadata_relative,
            metadata_sha256,
            project_relative,
            project_sha256,
        )
    )
    if lock_fields_absent:
        if release_manifest:
            raise BundleResourceValidationError("bundle_requirements_lock_missing")
        return expected_wheel, expected_constraints, None
    if (
        lock_relative != "constraints/macos-arm64-py312.requirements.lock"
        or metadata_relative != "constraints/macos-arm64-py312.lock.json"
        or project_relative != "constraints/pyproject.toml"
        or not _is_lowercase_sha256(lock_sha256)
        or not _is_lowercase_sha256(metadata_sha256)
        or not _is_lowercase_sha256(project_sha256)
    ):
        raise BundleResourceValidationError("bundle_requirements_lock_identity_invalid")
    expected_lock = _resolve_bundle_regular_file(
        resources,
        resolved_resources,
        lock_relative,
        field="bundled.requirements_lock",
        error_type=BundleResourceValidationError,
    )
    expected_metadata = _resolve_bundle_regular_file(
        resources,
        resolved_resources,
        metadata_relative,
        field="bundled.dependency_lock_metadata",
        error_type=BundleResourceValidationError,
    )
    expected_project_file = _resolve_bundle_regular_file(
        resources,
        resolved_resources,
        project_relative,
        field="bundled.project_file",
        error_type=BundleResourceValidationError,
    )
    if _sha256_file(expected_lock) != lock_sha256:
        raise BundleResourceValidationError("bundle_requirements_lock_sha256_mismatch")
    if _sha256_file(expected_metadata) != metadata_sha256:
        raise BundleResourceValidationError("bundle_dependency_lock_metadata_sha256_mismatch")
    if _sha256_file(expected_project_file) != project_sha256:
        raise BundleResourceValidationError("bundle_project_file_sha256_mismatch")
    try:
        lock_metadata = _read_json(expected_metadata)
    except Exception as exc:  # noqa: BLE001
        raise BundleResourceValidationError(
            "bundle_dependency_lock_metadata_invalid"
        ) from exc
    try:
        lock_names = _dependency_lock_distribution_names(expected_lock, exact=True)
        lock_generation_id = _dependency_lock_generation_id(expected_lock)
        constraint_names = _dependency_lock_distribution_names(
            expected_constraints,
            exact=False,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise BundleResourceValidationError(
            "bundle_dependency_lock_contents_invalid"
        ) from exc
    if not isinstance(lock_metadata, dict):
        raise BundleResourceValidationError("bundle_dependency_lock_metadata_invalid")
    recorded_names = lock_metadata.get("resolved_distribution_names")
    install_names = lock_metadata.get("install_distribution_names")
    override_names = set(BUNDLED_OVERRIDE_DISTRIBUTION_PINS)
    expected_full_names = sorted(lock_names | override_names)
    if (
        set(lock_metadata) != DEPENDENCY_LOCK_METADATA_FIELDS
        or lock_metadata.get("schema") != DEPENDENCY_LOCK_SCHEMA
        or _canonical_generation_id(lock_metadata.get("generation_id"))
        != lock_generation_id
        or lock_metadata.get("constraints_sha256")
        != expected_constraints_sha256
        or lock_metadata.get("project_file") != expected_project_file.name
        or lock_metadata.get("project_file_sha256") != project_sha256
        or lock_metadata.get("requirements_lock") != expected_lock.name
        or lock_metadata.get("requirements_lock_sha256") != lock_sha256
        or lock_metadata.get("root_install_requirement")
        != DEPENDENCY_LOCK_ROOT_REQUIREMENT
        or not isinstance(recorded_names, list)
        or any(not isinstance(name, str) for name in recorded_names)
        or recorded_names != expected_full_names
        or not isinstance(install_names, list)
        or any(not isinstance(name, str) for name in install_names)
        or install_names != sorted(lock_names)
        or bool(lock_names & override_names)
        or not constraint_names.issubset(set(recorded_names))
        or not _excluded_bundled_override_metadata_is_valid(
            lock_metadata.get("excluded_bundled_overrides")
        )
        or not _dependency_lock_resolver_is_valid(lock_metadata.get("resolver"))
        or lock_metadata.get("setup_consumes_requirements_lock") is not True
        or lock_metadata.get("pip_require_hashes") is not True
        or lock_metadata.get("resolution_complete") is not True
    ):
        raise BundleResourceValidationError("bundle_dependency_lock_metadata_invalid")
    return expected_wheel, expected_constraints, expected_lock


def _dependency_lock_logical_lines(path: Path) -> list[str]:
    logical: list[str] = []
    pending = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        logical.append(pending)
        pending = ""
    if pending or not logical:
        raise ValueError("dependency input has no complete requirement lines")
    return logical


def _dependency_requirement_name(requirement: str) -> str:
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    if match is None:
        raise ValueError("dependency requirement name is invalid")
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def _dependency_requirement_pin(requirement: str) -> tuple[str, str]:
    match = EXACT_LOCK_PINNED_REQUIREMENT.fullmatch(requirement)
    if match is None:
        raise ValueError("dependency lock requirement pin is invalid")
    return re.sub(r"[-_.]+", "-", match.group(1)).lower(), match.group(2)


def _dependency_lock_distribution_names(path: Path, *, exact: bool) -> set[str]:
    names: set[str] = set()
    for line in _dependency_lock_logical_lines(path):
        requirement = line.split(" --hash=", 1)[0].split(";", 1)[0].strip()
        if exact:
            exact_requirement = line.split(" --hash=", 1)[0].strip()
            hashes = LOCK_HASH_TOKEN.findall(line + " ")
            residual = LOCK_HASH_TOKEN.sub("", line + " ").strip()
            if (
                EXACT_LOCK_REQUIREMENT.fullmatch(exact_requirement) is None
                or not hashes
                or residual != exact_requirement
            ):
                raise ValueError("dependency lock entry is not exact and hashed")
        name = _dependency_requirement_name(requirement)
        if name in names:
            raise ValueError("dependency lock has duplicate distribution entries")
        names.add(name)
    return names


def _dependency_lock_distribution_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in _dependency_lock_logical_lines(path):
        requirement = line.split(" --hash=", 1)[0].split(";", 1)[0].strip()
        name, version = _dependency_requirement_pin(requirement)
        if name in pins:
            raise ValueError("dependency lock has duplicate distribution entries")
        pins[name] = version
    return pins


def _excluded_bundled_override_metadata_is_valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != set(BUNDLED_OVERRIDE_SPECS):
        return False
    for name, expected in BUNDLED_OVERRIDE_SPECS.items():
        entry = value.get(name)
        if not isinstance(entry, dict) or set(entry) != BUNDLED_OVERRIDE_METADATA_FIELDS:
            return False
        if (
            entry.get("version") != expected["version"]
            or entry.get("role") != BUNDLED_OVERRIDE_ROLE
            or entry.get("excluded_from_requirements_lock") is not True
            or entry.get("resolution_input_filename") != expected["filename"]
            or not _is_lowercase_sha256(entry.get("resolution_input_sha256"))
            or not _is_lowercase_sha256(
                entry.get("resolution_input_metadata_sha256")
            )
            or not _is_lowercase_sha256(
                entry.get("resolution_input_wheel_metadata_sha256")
            )
            or entry.get("release_wheel_hash_binding")
            != BUNDLED_OVERRIDE_RELEASE_HASH_BINDING
        ):
            return False
    return True


def _canonical_generation_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return None
    return value if str(parsed) == value else None


def _dependency_lock_generation_id(path: Path) -> str:
    markers = [
        raw[len(DEPENDENCY_LOCK_GENERATION_COMMENT_PREFIX) :].strip()
        for raw in path.read_text(encoding="utf-8").splitlines()
        if raw.startswith(DEPENDENCY_LOCK_GENERATION_COMMENT_PREFIX)
    ]
    if len(markers) != 1:
        raise ValueError("dependency lock generation ID marker is missing or ambiguous")
    generation_id = _canonical_generation_id(markers[0])
    if generation_id is None:
        raise ValueError("dependency lock generation ID is invalid")
    return generation_id


def _dependency_lock_resolver_is_valid(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    expected_fields = (
        set(CANONICAL_DEPENDENCY_LOCK_RESOLVER)
        | DEPENDENCY_LOCK_RESOLVER_OBSERVED_FIELDS
    )
    if set(value) != expected_fields:
        return False
    if {
        key: value.get(key) for key in CANONICAL_DEPENDENCY_LOCK_RESOLVER
    } != CANONICAL_DEPENDENCY_LOCK_RESOLVER:
        return False
    return (
        isinstance(value.get("pip_version"), str)
        and PIP_VERSION.fullmatch(str(value["pip_version"])) is not None
        and isinstance(value.get("python_full_version"), str)
        and PYTHON_312_FULL_VERSION.fullmatch(str(value["python_full_version"]))
        is not None
        and isinstance(value.get("macos_version"), str)
        and MACOS_14_FULL_VERSION.fullmatch(str(value["macos_version"])) is not None
        and isinstance(value.get("sysconfig_platform"), str)
        and MACOS_ARM64_SYSCONFIG_PLATFORM.fullmatch(
            str(value["sysconfig_platform"]).lower()
        )
        is not None
    )


def _bundle_resources_root(manifest_path: Path) -> tuple[Path, Path]:
    resources = _absolute_path_without_resolving(manifest_path).parent
    if not _is_directory_without_symlink(resources):
        raise ValueError("bundle Resources directory is invalid")
    try:
        resolved_resources = resources.resolve(strict=True)
    except OSError as exc:
        raise ValueError("bundle Resources directory is missing") from exc
    if not _is_directory_without_symlink(resolved_resources):
        raise ValueError("bundle Resources directory is unsafe")
    return resources, resolved_resources


def _resolve_bundle_regular_file(
    resources: Path,
    resolved_resources: Path,
    relative_value: str,
    *,
    field: str,
    error_type: type[ValueError] = ValueError,
) -> Path:
    parts = _safe_bundle_relative_parts(relative_value)
    if parts is None:
        raise error_type(f"bundle manifest {field} is unsafe")

    candidate = resources.joinpath(*parts)
    current = resources
    for part in parts[:-1]:
        current = current / part
        if not _is_directory_without_symlink(current):
            raise error_type(f"bundle manifest {field} has an unsafe parent")
    if not _is_regular_file_without_symlink(candidate):
        raise error_type(f"bundle manifest {field} is not a regular file")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise error_type(f"bundle manifest {field} is missing") from exc
    if not _is_relative_to(resolved, resolved_resources):
        raise error_type(f"bundle manifest {field} escapes app Resources")
    return candidate


def _require_supplied_bundle_file(
    supplied: Path | None,
    expected: Path,
    *,
    code: str,
) -> None:
    if supplied is None:
        raise BundleResourceValidationError(code)
    candidate = _absolute_path_without_resolving(supplied)
    if candidate != expected or not _is_regular_file_without_symlink(candidate):
        raise BundleResourceValidationError(code)


def _safe_bundle_relative_parts(value: str) -> tuple[str, ...] | None:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return None
    parts = tuple(value.split("/"))
    if not parts or any(not part or part in {".", ".."} for part in parts):
        return None
    return parts


def _is_safe_wrapper_wheel_basename(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = _safe_bundle_relative_parts(value)
    return (
        parts is not None
        and len(parts) == 1
        and _looks_like_wrapper_wheel(parts[0])
    )


def _looks_like_wrapper_wheel(name: str) -> bool:
    return (
        name.lower().startswith(WRAPPER_WHEEL_PREFIX)
        and name.lower().endswith(WRAPPER_WHEEL_SUFFIX)
    )


def _is_lowercase_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_regular_file_without_symlink(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _is_directory_without_symlink(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _absolute_path_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def build_installed_doctor_command(venv_python: Path, output_json: Path) -> list[str]:
    command = [
        str(venv_python),
        "-m",
        "totalsegmentator_wrapper_mac",
        "doctor",
        "--json",
        str(output_json),
    ]
    validate_safe_command(command)
    return command


def build_totalseg_privacy_command(venv_python: Path) -> list[str]:
    command = [
        str(venv_python),
        "-c",
        (
            "from totalsegmentator.config import setup_totalseg, set_config_key; "
            "setup_totalseg(); "
            "set_config_key('send_usage_stats', False); "
            "set_config_key('statistics_disclaimer_shown', True)"
        ),
    ]
    validate_safe_command(command)
    return command


def build_totalseg_weights_command(
    venv_python: Path,
    task_ids: tuple[int, ...] = DEFAULT_TOTALSEG_WEIGHT_TASK_IDS,
    *,
    progress_log: Path | None = None,
) -> list[str]:
    command = [
        str(venv_python),
        "-m",
        "totalsegmentator_wrapper_mac.totalseg_weights_setup",
    ]
    if progress_log is not None:
        command.extend(["--progress-log", str(progress_log)])
    command.append("--task-ids")
    command.extend(str(task_id) for task_id in task_ids)
    validate_safe_command(command)
    return command


def dentalsegmentator_model_root(paths: SetupPaths) -> Path:
    return paths.models_dir / "dentalsegmentator"


def toothseg_model_root(paths: SetupPaths) -> Path:
    return paths.models_dir / "toothseg"


def build_dentalseg_weights_command(
    venv_python: Path,
    model_root: Path,
    *,
    model_url: str = DENTALSEGMENTATOR_MODEL_URL,
    expected_md5: str = DENTALSEGMENTATOR_MODEL_MD5,
    dataset_id: str = DENTALSEGMENTATOR_DATASET_ID,
    dataset_name: str = DENTALSEGMENTATOR_DATASET_NAME,
    progress_log: Path | None = None,
) -> list[str]:
    command = [
        str(venv_python),
        "-m",
        "totalsegmentator_wrapper_mac.dentalsegmentator_setup",
        "--model-url",
        model_url,
        "--model-zip",
        str(model_root / DENTALSEGMENTATOR_MODEL_FILENAME),
        "--expected-md5",
        expected_md5,
        "--nnunet-results",
        str(model_root / "nnUNet_results"),
        "--nnunet-raw",
        str(model_root / "nnUNet_raw"),
        "--nnunet-preprocessed",
        str(model_root / "nnUNet_preprocessed"),
        "--dataset-id",
        dataset_id,
        "--dataset-name",
        dataset_name,
    ]
    if progress_log is not None:
        command.extend(["--progress-log", str(progress_log)])
    validate_safe_command(command)
    return command


def build_setup_environment(
    paths: SetupPaths,
    *,
    dicom_normalizer: Path | None = None,
    setup_attempt_id: str | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    for key in tuple(env):
        if key.upper().startswith("PIP_"):
            env.pop(key, None)
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "PYTHONSTARTUP",
        "PYTHONSAFEPATH",
        "VIRTUAL_ENV",
    ):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["PIP_CONFIG_FILE"] = os.devnull
    env["PIP_NO_INPUT"] = "1"
    env["PIP_CACHE_DIR"] = str(paths.cache_dir / "pip")
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONPYCACHEPREFIX"] = str(paths.cache_dir / "pycache")
    env["TOTALSEGMENTATOR_WRAPPER_MAC_APP_SUPPORT"] = str(paths.app_support)
    env["XDG_CACHE_HOME"] = str(paths.cache_dir)
    env["MPLCONFIGDIR"] = str(paths.cache_dir / "matplotlib")
    env["TOTALSEG_HOME_DIR"] = str(paths.models_dir / "totalsegmentator")
    env["TOTALSEG_WEIGHTS_PATH"] = str(paths.models_dir / "totalsegmentator" / "weights")
    if setup_attempt_id:
        env[SETUP_ATTEMPT_ID_ENV] = setup_attempt_id
    dentalseg_root = dentalsegmentator_model_root(paths)
    env["nnUNet_raw"] = str(dentalseg_root / "nnUNet_raw")
    env["nnUNet_preprocessed"] = str(dentalseg_root / "nnUNet_preprocessed")
    env["nnUNet_results"] = str(dentalseg_root / "nnUNet_results")
    if dicom_normalizer is not None:
        env["TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER"] = str(dicom_normalizer)
    return env


def validate_safe_command(command: list[str]) -> None:
    if not command:
        raise ValueError("empty command")
    executable_name = Path(command[0]).name
    if executable_name in FORBIDDEN_COMMAND_PARTS:
        raise ValueError(f"forbidden setup command: {executable_name}")
    if any(part in FORBIDDEN_COMMAND_PARTS for part in command):
        raise ValueError(f"forbidden setup command part in: {command}")


def run_setup(
    *,
    app_support_dir: Path | None = None,
    python_executable: Path | None = None,
    wheel: Path | None = None,
    constraints: Path | None = None,
    bundle_manifest: Path | None = None,
    allow_network: bool = False,
    dry_run: bool = False,
    skip_install: bool = False,
    skip_mps_check: bool = False,
    use_existing_env: bool = False,
    skip_dentalseg_model: bool = False,
    progress_log: Path | None = None,
    home: Path | None = None,
    runner: CommandRunner | None = None,
    normalizer_inspector: Callable[[], dict[str, Any]] | None = None,
    python_inspector: PythonInspector | None = None,
) -> SetupResult:
    paths = setup_paths(app_support_dir, home=home)
    validate_app_support_path(paths, home=home)
    setup_attempt_id = _setup_attempt_id()
    runner = runner or _run_command
    normalizer_inspector = normalizer_inspector or inspect_dicom_normalizer
    python_executable = python_executable.expanduser().resolve() if python_executable is not None else None
    wheel = _absolute_path_without_resolving(wheel) if wheel is not None else _find_latest_wheel()
    constraints = _absolute_path_without_resolving(constraints) if constraints is not None else None
    bundle_manifest = (
        _absolute_path_without_resolving(bundle_manifest)
        if bundle_manifest is not None
        else None
    )
    python_inspector = python_inspector or inspect_python_runtime

    steps: list[SetupStep] = []
    result = SetupResult(
        status="success",
        reason=None,
        paths=paths,
        steps=steps,
        python=sys.version,
        platform=platform.platform(),
        machine=platform.machine(),
        allow_network=allow_network,
        dry_run=dry_run,
        # A bundle-bound invocation may have been supplied hostile paths.  Do
        # not serialize those paths until strict manifest validation succeeds.
        wheel=None if bundle_manifest is not None else (str(wheel) if wheel is not None else None),
        python_executable=str(python_executable) if python_executable is not None else None,
        constraints=(
            None
            if bundle_manifest is not None
            else (str(constraints) if constraints is not None else None)
        ),
        installed_bundle=None,
        setup_attempt_id=setup_attempt_id,
    )
    bundle_payload: dict[str, Any] | None = None
    bundled_wheels: tuple[Path, ...] = ()
    requirements_lock: Path | None = None

    setup_lock = exclusive_app_setup_lock(paths.app_support) if not dry_run else None
    if setup_lock is not None:
        try:
            setup_lock.__enter__()
        except SetupBusyError:
            result.status = "failed"
            result.reason = "setup_busy"
            steps.append(
                SetupStep(
                    name="acquire_setup_lock",
                    status="failed",
                    error="Another setup attempt is already running.",
                )
            )
            _write_progress(
                progress_log,
                "acquire_setup_lock",
                "failed",
                "別のセットアップが実行中です。",
            )
            return result
        except (OSError, SetupLockError) as exc:
            result.status = "failed"
            result.reason = (
                "insufficient_disk_space"
                if isinstance(exc, OSError) and exc.errno == errno.ENOSPC
                else "setup_lock_failed"
            )
            steps.append(
                SetupStep(
                    name="acquire_setup_lock",
                    status="failed",
                    error="The setup lock could not be acquired safely.",
                )
            )
            _write_progress(
                progress_log,
                "acquire_setup_lock",
                "failed",
                "セットアップの排他制御を開始できませんでした。",
            )
            return result

    def setup_environment() -> dict[str, str]:
        return build_setup_environment(paths, setup_attempt_id=setup_attempt_id)

    try:
        if bundle_manifest is not None:
            try:
                bundle_payload = _read_json(bundle_manifest)
                result.installed_bundle = bundle_install_record(bundle_payload)
            except Exception:  # noqa: BLE001
                _write_progress(progress_log, "setup_exception", "failed", "アプリ同梱manifestを読めません。")
                steps.append(
                    SetupStep(
                        name="read_bundle_manifest",
                        status="failed",
                        error="Bundled application manifest could not be validated.",
                    )
                )
                result.status = "failed"
                result.reason = "bundle_manifest_invalid"
                return _finalize_result(result, write_state=not dry_run)
            if (
                result.installed_bundle.get("setup_weights_manifest_sha256")
                != setup_weight_manifest_sha256()
            ):
                _write_progress(
                    progress_log,
                    "read_bundle_manifest",
                    "failed",
                    "同梱モデル定義の整合性を確認できません。",
                )
                steps.append(
                    SetupStep(
                        name="read_bundle_manifest",
                        status="failed",
                        error="Packaged setup weights manifest identity does not match the bundle record.",
                    )
                )
                result.status = "failed"
                result.reason = "weights_manifest_incompatible"
                return _finalize_result(result, write_state=not dry_run)
            try:
                wheel, constraints, requirements_lock = resolve_bundled_setup_resources(
                    bundle_manifest,
                    bundle_payload,
                    wheel=wheel,
                    constraints=constraints,
                )
            except Exception:  # noqa: BLE001
                _write_progress(
                    progress_log,
                    "validate_bundled_wheels",
                    "failed",
                    "同梱アプリ部品の完全性を確認できません。",
                )
                steps.append(
                    SetupStep(
                        name="validate_bundled_wheels",
                        status="failed",
                        error="Bundled application resources did not pass integrity validation.",
                    )
                )
                result.status = "failed"
                result.reason = "bundle_manifest_invalid"
                return _finalize_result(result, write_state=not dry_run)
            result.wheel = str(wheel)
            result.constraints = str(constraints)
            result.requirements_lock = (
                str(requirements_lock) if requirements_lock is not None else None
            )
        _write_progress(progress_log, "create_app_support_dirs", "running", "App Supportディレクトリを準備しています。")
        steps.append(create_setup_directories(paths, dry_run=dry_run))
        _write_progress(progress_log, "create_app_support_dirs", steps[-1].status, "App Supportディレクトリの準備が完了しました。")
        if python_executable is None:
            _write_progress(progress_log, "validate_python_312", "failed", "同梱Python 3.12が見つかりません。")
            steps.append(
                SetupStep(
                    name="validate_python_312",
                    status="failed",
                    error="Python 3.12 executable was not supplied.",
                )
            )
            result.status = "failed"
            result.reason = "python312_missing"
            return _finalize_result(result, write_state=not dry_run)

        _write_progress(progress_log, "validate_python_312", "running", "Python 3.12を確認しています。")
        python_info = python_inspector(python_executable)
        result.python_version = str(python_info.get("version")) if python_info.get("version") else None
        steps.append(
            SetupStep(
                name="validate_python_312",
                status="success" if python_info.get("status") == "success" else "failed",
                command=list(python_info.get("command", [])),
                error=str(python_info.get("error") or python_info.get("reason") or "") or None,
            )
        )
        if python_info.get("status") != "success":
            _write_progress(progress_log, "validate_python_312", "failed", "Python 3.12の確認に失敗しました。")
            result.status = "failed"
            result.reason = str(python_info.get("reason") or "python_version_unsupported")
            return _finalize_result(result, write_state=not dry_run)
        _write_progress(progress_log, "validate_python_312", "success", "Python 3.12を確認しました。")

        if wheel is None or not wheel.exists():
            _write_progress(progress_log, "install_wheel", "failed", "同梱wheelが見つかりません。")
            result.status = "failed"
            result.reason = "wheel_missing"
            return _finalize_result(result, write_state=not dry_run)

        if allow_network and constraints is None:
            _write_progress(progress_log, "install_wheel", "failed", "依存固定ファイルが見つかりません。")
            result.status = "failed"
            result.reason = "constraints_missing"
            return _finalize_result(result, write_state=not dry_run)
        if constraints is not None and not constraints.exists():
            _write_progress(progress_log, "install_wheel", "failed", "依存固定ファイルが見つかりません。")
            result.status = "failed"
            result.reason = "constraints_missing"
            return _finalize_result(result, write_state=not dry_run)

        if allow_network and bundle_manifest is not None:
            _write_progress(
                progress_log,
                "validate_bundled_wheels",
                "running",
                "同梱依存wheelの整合性を確認しています。",
            )
            try:
                assert bundle_payload is not None
                bundled_wheels = resolve_bundled_wheels(bundle_manifest, bundle_payload)
            except Exception as exc:  # noqa: BLE001
                diagnostic_log = _write_local_diagnostic_log(
                    paths.logs_dir / "validate_bundled_wheels.log",
                    "Bundled dependency wheel validation failed.\n"
                    f"exception_repr={exc!r}\n"
                    f"exception_text={exc}\n",
                )
                _write_progress(
                    progress_log,
                    "validate_bundled_wheels",
                    "failed",
                    "同梱依存wheelの整合性を確認できません。",
                )
                steps.append(
                    SetupStep(
                        name="validate_bundled_wheels",
                        status="failed",
                        error="Bundled dependency wheel validation failed.",
                        diagnostic_log=diagnostic_log,
                    )
                )
                result.status = "failed"
                result.reason = "bundled_wheel_invalid"
                return _finalize_result(result, write_state=not dry_run)
            steps.append(SetupStep(name="validate_bundled_wheels", status="success"))
            _write_progress(
                progress_log,
                "validate_bundled_wheels",
                "success",
                "同梱依存wheelの整合性を確認しました。",
            )

        venv_python = paths.env_dir / "bin" / "python"
        result.venv_reused = venv_python.exists()
        _write_progress(progress_log, "create_venv", "running", "専用Python環境を準備しています。")
        if result.venv_reused:
            venv_step = SetupStep(name="create_venv", status="skipped")
        elif use_existing_env:
            venv_step = SetupStep(
                name="create_venv",
                status="failed",
                error=f"--use-existing-env was requested but {venv_python} does not exist.",
            )
        else:
            venv_step = _execute_step(
                "create_venv",
                build_venv_command(python_executable, paths.env_dir),
                paths.logs_dir,
                runner,
                env=setup_environment(),
                dry_run=dry_run or skip_install,
            )
        steps.append(venv_step)
        if venv_step.status == "failed":
            _write_progress(progress_log, "create_venv", "failed", "専用Python環境の準備に失敗しました。")
            result.status = "failed"
            result.reason = "runtime_install_failed"
            return _finalize_result(result, write_state=not dry_run)
        _write_progress(progress_log, "create_venv", venv_step.status, "専用Python環境の準備が完了しました。")

        if bundled_wheels:
            _write_progress(
                progress_log,
                "install_bundled_wheels",
                "running",
                "同梱依存パッケージを確認しています。",
            )
            bundled_install_step = _execute_step(
                "install_bundled_wheels",
                build_bundled_wheels_install_command(venv_python, bundled_wheels),
                paths.logs_dir,
                runner,
                env=setup_environment(),
                dry_run=dry_run or skip_install,
            )
            steps.append(bundled_install_step)
            if bundled_install_step.status == "failed":
                _write_progress(
                    progress_log,
                    "install_bundled_wheels",
                    "failed",
                    "同梱依存パッケージの導入に失敗しました。",
                )
                result.status = "failed"
                result.reason = "bundled_wheel_install_failed"
                return _finalize_result(result, write_state=not dry_run)
            _write_progress(
                progress_log,
                "install_bundled_wheels",
                bundled_install_step.status,
                "同梱依存パッケージの導入が完了しました。",
            )

        if allow_network and requirements_lock is not None:
            _write_progress(
                progress_log,
                "install_locked_dependencies",
                "running",
                "SHA-256固定済みの依存パッケージを取得しています。",
            )
            locked_dependencies_step = _execute_step(
                "install_locked_dependencies",
                build_locked_dependencies_install_command(
                    venv_python,
                    requirements_lock=requirements_lock,
                    wheel_directory=wheel.parent,
                ),
                paths.logs_dir,
                runner,
                env=setup_environment(),
                dry_run=dry_run or skip_install,
            )
            steps.append(locked_dependencies_step)
            if locked_dependencies_step.status == "failed":
                _write_progress(
                    progress_log,
                    "install_locked_dependencies",
                    "failed",
                    "固定済み依存パッケージの導入に失敗しました。",
                )
                result.status = "failed"
                result.reason = _classify_dependency_install_failure(
                    locked_dependencies_step.error
                )
                return _finalize_result(result, write_state=not dry_run)
            _write_progress(
                progress_log,
                "install_locked_dependencies",
                locked_dependencies_step.status,
                "固定済み依存パッケージの導入が完了しました。",
            )
        result.wheel_install_mode = (
            "network_require_hashes_lock"
            if allow_network and requirements_lock is not None
            else ("network_constraints_binary_only" if allow_network else "no_deps")
        )
        _write_progress(progress_log, "install_wheel", "running", "依存パッケージを取得中です。数分かかることがあります。")
        install_step = _execute_step(
            "install_wheel",
            build_wheel_install_command(
                venv_python,
                wheel,
                allow_network=allow_network and requirements_lock is None,
                constraints=constraints,
            ),
            paths.logs_dir,
            runner,
            env=setup_environment(),
            dry_run=dry_run or skip_install,
        )
        steps.append(install_step)
        if install_step.status == "failed":
            _write_progress(progress_log, "install_wheel", "failed", "依存パッケージの導入に失敗しました。")
            result.status = "failed"
            result.reason = _classify_dependency_install_failure(install_step.error)
            return _finalize_result(result, write_state=not dry_run)
        _write_progress(progress_log, "install_wheel", "success", "依存パッケージの導入が完了しました。")

        if allow_network:
            _write_progress(
                progress_log,
                "verify_dependencies",
                "running",
                "依存パッケージの整合性を確認しています。",
            )
            dependency_check_step = _execute_step(
                "verify_dependencies",
                build_pip_check_command(venv_python),
                paths.logs_dir,
                runner,
                env=setup_environment(),
                dry_run=dry_run or skip_install,
            )
            steps.append(dependency_check_step)
            if dependency_check_step.status == "failed":
                # _execute_step preserves pip stdout/stderr in its local diagnostic
                # log.  The user-facing setup JSON intentionally gets only a
                # stable, path-free description.
                dependency_check_step.error = (
                    "Installed dependency consistency validation failed."
                )
                _write_progress(
                    progress_log,
                    "verify_dependencies",
                    "failed",
                    "依存パッケージの整合性を確認できません。",
                )
                result.status = "failed"
                result.reason = "dependency_consistency_failed"
                return _finalize_result(result, write_state=not dry_run)
            _write_progress(
                progress_log,
                "verify_dependencies",
                dependency_check_step.status,
                "依存パッケージの整合性を確認しました。",
            )

        if not allow_network and not skip_mps_check:
            result.dicom_normalizer = _annotate_normalizer_source(normalizer_inspector())
            if result.dicom_normalizer.get("status") != "success":
                _write_progress(progress_log, "doctor", "failed", "CT確認用部品の確認に失敗しました。")
                result.status = "failed"
                result.reason = "normalizer_missing"
                return _finalize_result(result, write_state=not dry_run)
            _write_progress(progress_log, "install_wheel", "failed", "ネットワーク接続が必要です。")
            result.status = "failed"
            result.reason = "needs_network"
            return _finalize_result(result, write_state=not dry_run)

        _write_progress(progress_log, "configure_totalseg_privacy", "running", "プライバシー設定を適用しています。")
        privacy_step = _execute_step(
            "configure_totalseg_privacy",
            build_totalseg_privacy_command(venv_python),
            paths.logs_dir,
            runner,
            env=setup_environment(),
            dry_run=dry_run or skip_install,
        )
        steps.append(privacy_step)
        if privacy_step.status == "failed":
            _write_progress(progress_log, "configure_totalseg_privacy", "failed", "プライバシー設定に失敗しました。")
            result.status = "failed"
            result.reason = "totalseg_privacy_config_failed"
            return _finalize_result(result, write_state=not dry_run)
        _write_progress(progress_log, "configure_totalseg_privacy", privacy_step.status, "プライバシー設定を適用しました。")

        _write_progress(progress_log, "download_totalseg_weights", "running", "初回実行に必要なモデルを取得しています。数分かかることがあります。")
        weights_step = _execute_step(
            "download_totalseg_weights",
            build_totalseg_weights_command(venv_python, progress_log=progress_log),
            paths.logs_dir,
            runner,
            env=setup_environment(),
            dry_run=dry_run or skip_install,
        )
        steps.append(weights_step)
        if weights_step.status == "failed":
            result.reason = _classify_totalseg_weights_failure(weights_step.error)
            failure_message = {
                "weights_integrity_failed": "取得したモデルの完全性確認に失敗しました。",
                "weights_manifest_incompatible": "モデル定義とTotalSegmentatorのバージョンが一致しません。",
                "weights_setup_busy": "別のモデル準備処理が実行中です。",
                "insufficient_disk_space": "モデル取得に必要な空き容量が不足しています。",
            }.get(result.reason, "モデルの取得に失敗しました。")
            _write_progress(progress_log, "download_totalseg_weights", "failed", failure_message)
            result.status = "failed"
            return _finalize_result(result, write_state=not dry_run)
        _write_progress(progress_log, "download_totalseg_weights", weights_step.status, "モデルの取得が完了しました。")

        if skip_dentalseg_model:
            dentalseg_weights_step = SetupStep(
                name="download_dentalseg_weights",
                status="skipped",
                error="DentalSegmentator model preparation was deferred by --skip-dentalseg-model.",
            )
            steps.append(dentalseg_weights_step)
            _write_progress(
                progress_log,
                "download_dentalseg_weights",
                "skipped",
                "DentalSegmentatorモデルの準備は後で行います。",
            )
        else:
            _write_progress(
                progress_log,
                "download_dentalseg_weights",
                "running",
                "DentalSegmentatorモデルを取得しています。数分かかることがあります。",
            )
            dentalseg_weights_step = _execute_step(
                "download_dentalseg_weights",
                build_dentalseg_weights_command(
                    venv_python,
                    dentalsegmentator_model_root(paths),
                    progress_log=progress_log,
                ),
                paths.logs_dir,
                runner,
                env=setup_environment(),
                dry_run=dry_run or skip_install,
            )
            steps.append(dentalseg_weights_step)
            if dentalseg_weights_step.status == "failed":
                _write_progress(
                    progress_log,
                    "download_dentalseg_weights",
                    "failed",
                    "DentalSegmentatorモデルの取得に失敗しました。",
                )
                result.status = "failed"
                result.reason = "dentalseg_weights_download_failed"
                return _finalize_result(result, write_state=not dry_run)
            _write_progress(
                progress_log,
                "download_dentalseg_weights",
                dentalseg_weights_step.status,
                "DentalSegmentatorモデルの取得が完了しました。",
            )

        doctor_json = paths.logs_dir / "doctor.json"
        _write_progress(progress_log, "doctor", "running", "MPSとCT確認用部品を確認しています。")
        doctor_step = _execute_step(
            "doctor",
            build_installed_doctor_command(venv_python, doctor_json),
            paths.logs_dir,
            runner,
            env=setup_environment(),
            dry_run=dry_run or skip_mps_check,
        )
        steps.append(doctor_step)
        if doctor_json.exists():
            result.doctor = _read_json(doctor_json)
            result.dicom_normalizer = _annotate_normalizer_source(result.doctor.get("dicom_normalizer"))
        else:
            result.dicom_normalizer = _annotate_normalizer_source(normalizer_inspector())

        if not result.dicom_normalizer or result.dicom_normalizer.get("status") != "success":
            _write_progress(progress_log, "doctor", "failed", "CT確認用部品の確認に失敗しました。")
            result.status = "failed"
            result.reason = "normalizer_missing"
            return _finalize_result(result, write_state=not dry_run)

        if doctor_step.status == "failed":
            _write_progress(progress_log, "doctor", "failed", "MPS確認に失敗しました。")
            result.status = "failed"
            result.reason = "mps_unavailable"
            return _finalize_result(result, write_state=not dry_run)

        _write_progress(progress_log, "doctor", "success", "MPS確認が完了しました。")
        _write_progress(progress_log, "complete", "success", "起動準備が完了しました。")
        return _finalize_result(result, write_state=not dry_run)
    except Exception as exc:  # noqa: BLE001
        _write_progress(progress_log, "setup_exception", "failed", f"セットアップ中に例外が発生しました: {exc!r}")
        steps.append(SetupStep(name="setup_exception", status="failed", error=repr(exc)))
        result.status = "failed"
        result.reason = "setup_exception"
        return _finalize_result(result, write_state=not dry_run)
    finally:
        if setup_lock is not None:
            setup_lock.__exit__(None, None, None)


def write_setup_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_setup_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json(path)


def read_bundle_install_record(manifest_path: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    return bundle_install_record(manifest)


def _declared_python_runtime_fingerprint(manifest: dict[str, Any]) -> str:
    """Normalize either supported manifest spelling without deriving runtime identity.

    The build manifest owns the complete runtime fingerprint.  In particular, this
    setup path must not substitute a hash of the Python executable: that value is
    not a fingerprint of the complete bundled runtime and can change when a
    Developer ID signature is timestamped again.
    """

    fingerprint = manifest.get("python_runtime_fingerprint")
    if isinstance(fingerprint, str) and fingerprint:
        return fingerprint
    runtime = manifest.get("python_runtime")
    if isinstance(runtime, dict):
        fingerprint = runtime.get("fingerprint")
        if isinstance(fingerprint, str) and fingerprint:
            return fingerprint
    return ""


def bundle_install_record(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "totalsegmentator_wrapper_mac.installed_bundle.v1",
        "app_version": manifest.get("app_version") or manifest.get("version"),
        "build_id": manifest.get("build_id"),
        "dependency_set_id": manifest.get("dependency_set_id"),
        "wheel_sha256": manifest.get("wheel_sha256"),
        "fpsample_wheel_sha256": manifest.get("fpsample_wheel_sha256"),
        "acvl_utils_wheel_sha256": manifest.get("acvl_utils_wheel_sha256"),
        "constraints_sha256": manifest.get("constraints_sha256"),
        "project_file_sha256": manifest.get("project_file_sha256"),
        "requirements_lock_sha256": manifest.get("requirements_lock_sha256"),
        "dependency_lock_metadata_sha256": manifest.get(
            "dependency_lock_metadata_sha256"
        ),
        "normalizer_sha256": manifest.get("normalizer_sha256"),
        "dcm2niix_sha256": manifest.get("dcm2niix_sha256"),
        "sample1_manifest_sha256": manifest.get("sample1_manifest_sha256"),
        "setup_weights_manifest_sha256": manifest.get("setup_weights_manifest_sha256"),
        "python_runtime_fingerprint": _declared_python_runtime_fingerprint(manifest),
        "update_manifest_url": manifest.get("update_manifest_url"),
    }


def _execute_step(
    name: str,
    command: list[str],
    cwd: Path | None,
    runner: CommandRunner,
    *,
    env: dict[str, str],
    dry_run: bool,
) -> SetupStep:
    if dry_run:
        return SetupStep(name=name, status="skipped", command=command)
    started = time.perf_counter()
    try:
        proc = runner(command, cwd, env)
    except Exception as exc:  # noqa: BLE001
        return SetupStep(name=name, status="failed", command=command, error=repr(exc))
    elapsed = time.perf_counter() - started
    diagnostic_log: str | None = None
    if cwd is not None and (proc.stdout or proc.stderr):
        diagnostic_path = cwd / f"{name}.log"
        diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        diagnostic_path.write_text(
            "STDOUT\n"
            + (proc.stdout or "")
            + "\nSTDERR\n"
            + (proc.stderr or ""),
            encoding="utf-8",
        )
        diagnostic_log = str(diagnostic_path)
    error_text = (proc.stderr or "").strip()
    if proc.returncode != 0 and not error_text:
        error_text = (proc.stdout or "").strip()
    return SetupStep(
        name=name,
        status="success" if proc.returncode == 0 else "failed",
        command=command,
        elapsed_seconds=elapsed,
        returncode=proc.returncode,
        error=error_text if proc.returncode != 0 else None,
        diagnostic_log=diagnostic_log,
    )


def _write_local_diagnostic_log(path: Path, text: str) -> str | None:
    """Keep low-level setup details locally, out of structured user-facing state."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return str(path)


def _classify_dependency_install_failure(error: str | None) -> str:
    text = (error or "").lower()
    if any(marker in text for marker in ("failed building wheel", "could not build wheels", "c++17 compiler", "cmake is required")):
        return "dependency_build_failed"
    if any(marker in text for marker in ("resolutionimpossible", "conflicting dependencies", "dependency conflict")):
        return "dependency_resolution_failed"
    if any(marker in text for marker in ("no matching distribution found", "could not find a version that satisfies")):
        return "dependency_distribution_unavailable"
    if any(marker in text for marker in ("certificate_verify_failed", "readtimeout", "connectionerror", "failed to establish a new connection")):
        return "dependency_network_failed"
    if "no space left on device" in text:
        return "insufficient_disk_space"
    return "runtime_install_failed"


def _classify_totalseg_weights_failure(error: str | None) -> str:
    text = (error or "").lower()
    if "reason=weights_setup_busy" in text:
        return "weights_setup_busy"
    if "reason=insufficient_disk_space" in text or "no space left on device" in text:
        return "insufficient_disk_space"
    if "reason=weights_manifest_incompatible" in text:
        return "weights_manifest_incompatible"
    if "reason=weights_integrity_failed" in text:
        return "weights_integrity_failed"
    if any(
        marker in text
        for marker in (
            "require version",
            "unsupported setup totalsegmentator task",
            "setup weights manifest",
            "manifest version",
            "untrusted totalsegmentator asset url",
        )
    ):
        return "weights_manifest_incompatible"
    if any(
        marker in text
        for marker in (
            "sha-256 mismatch",
            "zip crc",
            "badzipfile",
            "not a zip file",
            "unsafe zip member",
            "expected model structure",
            "asset size header mismatch",
        )
    ):
        return "weights_integrity_failed"
    return "weights_download_failed"


def _write_progress(progress_log: Path | None, step: str, status: str, message: str) -> None:
    if progress_log is None:
        return
    progress_log.parent.mkdir(parents=True, exist_ok=True)
    with progress_log.open("a", encoding="utf-8") as log:
        log.write(f"SETUP_PROGRESS step={step} status={status} message={message}\n")
        log.flush()


def _run_command(
    command: list[str],
    cwd: Path | None,
    env: dict[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _setup_env() -> dict[str, str]:
    paths = setup_paths()
    return build_setup_environment(paths)


def inspect_python_runtime(python_executable: Path) -> dict[str, Any]:
    command = [
        str(python_executable),
        "-c",
        (
            "import json, sys; "
            "print(json.dumps({'version': sys.version.split()[0], "
            "'major': sys.version_info.major, 'minor': sys.version_info.minor}))"
        ),
    ]
    if not python_executable.exists():
        return {
            "status": "failed",
            "reason": "python312_missing",
            "command": command,
            "error": f"Python executable does not exist: {python_executable}",
        }
    proc = _run_command(command, None, build_setup_environment(setup_paths()))
    if proc.returncode != 0:
        return {
            "status": "failed",
            "reason": "python312_missing",
            "command": command,
            "error": proc.stderr.strip() or proc.stdout.strip(),
        }
    try:
        payload = json.loads(proc.stdout)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "reason": "python_version_unsupported",
            "command": command,
            "error": repr(exc),
        }
    if payload.get("major") != 3 or payload.get("minor") != 12:
        return {
            "status": "failed",
            "reason": "python_version_unsupported",
            "command": command,
            "version": payload.get("version"),
            "error": f"Expected Python 3.12, got {payload.get('version')}",
        }
    return {
        "status": "success",
        "reason": None,
        "command": command,
        "version": payload.get("version"),
    }


def _finalize_result(result: SetupResult, *, write_state: bool) -> SetupResult:
    if write_state:
        write_setup_state(result.paths.state_json, result.to_dict())
    return result


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _find_latest_wheel() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    wheels = [
        wheel
        for wheel in (root / "dist").glob(f"{WRAPPER_WHEEL_PREFIX}*{WRAPPER_WHEEL_SUFFIX}")
        if _is_regular_file_without_symlink(wheel)
    ]
    return wheels[0] if len(wheels) == 1 else None


def _annotate_normalizer_source(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    annotated = dict(payload)
    annotated["normalizer_source"] = _normalizer_source(payload.get("binary"))
    return annotated


def _normalizer_source(binary: Any) -> str:
    if not binary:
        return "missing"
    path = Path(str(binary))
    text = str(path)
    if ".app/Contents/Resources/bin/" in text:
        return "app_bundle"
    if path.parent.name == "bin" and path.parent.parent.name == "totalsegmentator_wrapper_mac":
        return "package"
    return "path"


def _setup_attempt_id() -> str:
    configured = os.environ.get(SETUP_ATTEMPT_ID_ENV, "")
    if configured and len(configured) <= 128 and all(
        character.isalnum() or character in {"-", "_", "."}
        for character in configured
    ):
        return configured
    return str(uuid4())


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
