#!/usr/bin/env python3
"""Fail closed when release dependency/model inputs are not fully attested."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse
from uuid import UUID

try:
    from scripts.python_runtime_fingerprint import (
        RuntimeFingerprintError,
        fingerprint_runtime_tree,
    )
except ModuleNotFoundError:  # Direct execution from scripts/.
    from python_runtime_fingerprint import (  # type: ignore[no-redef]
        RuntimeFingerprintError,
        fingerprint_runtime_tree,
    )

try:
    from scripts.release_build_toolchain import (
        ReleaseBuildToolchainError,
        verify_release_build_toolchain_inputs,
    )
except ModuleNotFoundError:  # Direct execution from scripts/.
    from release_build_toolchain import (  # type: ignore[no-redef]
        ReleaseBuildToolchainError,
        verify_release_build_toolchain_inputs,
    )


EXACT_REQUIREMENT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?==[^\s;]+"
    r"(?:\s*;\s*[^\s].*)?$"
)
HASH_TOKEN = re.compile(r"--hash=sha256:[0-9a-f]{64}(?:\s|$)")
REVALIDATION_EVIDENCE_SCHEMA = (
    "totalsegmentator_wrapper_mac.official_asset_revalidation.v1"
)
REVALIDATION_CHECKS = [
    "complete-size",
    "sha256",
    "zip-crc",
    "expected-model-structure",
]
CANONICAL_DEPENDENCY_LOCK_RESOLVER = {
    "name": "pip-compile",
    "version": "7.5.0",
    "platform": "macos-14-arm64",
    "python": "3.12",
}
DEPENDENCY_LOCK_SCHEMA = "totalsegmentator_wrapper_mac.dependency_lock.v3"
DEFAULT_PROJECT_FILE = Path(__file__).resolve().parents[1] / "pyproject.toml"
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
        "dist_info": "acvl_utils-0.2.6.dist-info",
        "filename": "acvl_utils-0.2.6-py3-none-any.whl",
        "version": "0.2.6",
        "wheel_tag": "py3-none-any",
    },
    "fpsample": {
        "dist_info": "fpsample-1.0.2.dist-info",
        "filename": "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl",
        "version": "1.0.2",
        "wheel_tag": "cp312-cp312-macosx_13_0_arm64",
    },
}
PINNED_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9_,.-]+\])?==([^\s;]+)$"
)
PIP_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)+(?:[A-Za-z0-9.+-]+)?$")
PYTHON_312_FULL_VERSION = re.compile(r"^3\.12\.(?:0|[1-9][0-9]*)$")
MACOS_14_FULL_VERSION = re.compile(r"^14\.(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
# The release resolver is intentionally run on macOS 14, rather than merely
# on a newer machine that can emit a higher minimum platform tag.  Keep the
# recorded sysconfig tag tied to that resolver host; the resulting app itself
# has a separate ``minimum_macos_version: 14.0`` user-target contract.
MACOS_ARM64_SYSCONFIG_PLATFORM = re.compile(r"^macosx-14(?:\.[0-9]+)*-arm64$")


class ReleaseInputReadinessError(RuntimeError):
    pass


def _logical_requirement_lines(path: Path) -> list[str]:
    if not path.is_file() or path.is_symlink():
        raise ReleaseInputReadinessError(f"constraints must be a regular file: {path}")
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
    if pending:
        raise ReleaseInputReadinessError("constraints end with an incomplete line continuation")
    return logical


def verify_hashed_requirement_entries(path: Path) -> None:
    failures: list[str] = []
    lines = _logical_requirement_lines(path)
    if not lines:
        failures.append("constraints contain no requirements")
    for line in lines:
        requirement = line.split(" --hash=", 1)[0].strip()
        if not EXACT_REQUIREMENT.fullmatch(requirement):
            failures.append(f"requirement is not an exact == pin: {requirement}")
        hashes = HASH_TOKEN.findall(line + " ")
        if not hashes:
            failures.append(f"requirement has no SHA-256 wheel/archive hash: {requirement}")
        residual = HASH_TOKEN.sub("", line + " ").strip()
        if residual != requirement:
            failures.append(f"requirement contains unsupported lock tokens: {line}")
    if failures:
        raise ReleaseInputReadinessError(
            "requirement entries are not exact and SHA-256 hashed:\n- "
            + "\n- ".join(failures)
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ReleaseInputReadinessError(f"{label} must be a regular non-symlink file: {path}")


def _require_external_regular(path: Path, runtime_root: Path, label: str) -> None:
    _require_regular(path, label)
    try:
        path.resolve(strict=True).relative_to(runtime_root.resolve(strict=True))
    except ValueError:
        return
    except OSError as exc:
        raise ReleaseInputReadinessError(
            f"could not resolve {label}: {path}: {exc}"
        ) from exc
    raise ReleaseInputReadinessError(
        f"{label} must be stored outside the Python runtime payload: {path}"
    )


def _requirement_name(line: str) -> str:
    requirement = line.split(" --hash=", 1)[0].split(";", 1)[0].strip()
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    if match is None:
        raise ReleaseInputReadinessError(
            f"could not parse requirement distribution name: {requirement}"
        )
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def _requirement_name_and_version(line: str) -> tuple[str, str]:
    requirement = line.split(" --hash=", 1)[0].split(";", 1)[0].strip()
    match = PINNED_REQUIREMENT.fullmatch(requirement)
    if match is None:
        raise ReleaseInputReadinessError(
            f"could not parse exact requirement distribution/version: {requirement}"
        )
    return re.sub(r"[-_.]+", "-", match.group(1)).lower(), match.group(2)


def verify_bundled_override_distribution_pins(
    distribution_pins: dict[str, str],
) -> None:
    """Validate the two overrides in the complete resolution graph.

    The published install lock intentionally excludes these local wheels: their
    installed bytes are bound later by the app setup manifest, after an optional
    Developer ID signature has changed the fpsample wheel hash.
    """

    mismatched = {
        name: distribution_pins.get(name)
        for name, expected in BUNDLED_OVERRIDE_DISTRIBUTION_PINS.items()
        if distribution_pins.get(name) != expected
    }
    if mismatched:
        detail = ", ".join(
            f"{name}={actual!r} (expected {BUNDLED_OVERRIDE_DISTRIBUTION_PINS[name]!r})"
            for name, actual in sorted(mismatched.items())
        )
        raise ReleaseInputReadinessError(
            "bundled override distribution pin mismatch: " + detail
        )


def verify_lock_excludes_bundled_overrides(lock_names: set[str]) -> None:
    """Require the install lock to exclude exactly the separately bundled wheels."""

    present = sorted(set(BUNDLED_OVERRIDE_DISTRIBUTION_PINS) & lock_names)
    if present:
        raise ReleaseInputReadinessError(
            "canonical install lock must exclude bundled overrides: "
            + ", ".join(present)
        )


def verify_excluded_bundled_override_metadata(value: object) -> None:
    """Validate the explicit resolution-input boundary for local override wheels."""

    if not isinstance(value, dict) or set(value) != set(BUNDLED_OVERRIDE_SPECS):
        raise ReleaseInputReadinessError(
            "excluded bundled override metadata names are invalid"
        )
    for name, expected in sorted(BUNDLED_OVERRIDE_SPECS.items()):
        entry = value.get(name)
        if not isinstance(entry, dict) or set(entry) != BUNDLED_OVERRIDE_METADATA_FIELDS:
            raise ReleaseInputReadinessError(
                f"excluded bundled override metadata is invalid for {name}"
            )
        if (
            entry.get("version") != expected["version"]
            or entry.get("role") != BUNDLED_OVERRIDE_ROLE
            or entry.get("excluded_from_requirements_lock") is not True
            or entry.get("resolution_input_filename") != expected["filename"]
            or not isinstance(entry.get("resolution_input_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", entry["resolution_input_sha256"])
            is None
            or not isinstance(entry.get("resolution_input_metadata_sha256"), str)
            or re.fullmatch(
                r"[0-9a-f]{64}", entry["resolution_input_metadata_sha256"]
            )
            is None
            or not isinstance(entry.get("resolution_input_wheel_metadata_sha256"), str)
            or re.fullmatch(
                r"[0-9a-f]{64}", entry["resolution_input_wheel_metadata_sha256"]
            )
            is None
            or entry.get("release_wheel_hash_binding")
            != BUNDLED_OVERRIDE_RELEASE_HASH_BINDING
        ):
            raise ReleaseInputReadinessError(
                f"excluded bundled override metadata is invalid for {name}"
            )


def _canonical_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return None
    return value if str(parsed) == value else None


def dependency_lock_generation_id(path: Path) -> str:
    """Read the generation marker that binds the lock bytes to its metadata."""

    _require_regular(path, "canonical requirements lock")
    markers = [
        raw[len(DEPENDENCY_LOCK_GENERATION_COMMENT_PREFIX) :].strip()
        for raw in path.read_text(encoding="utf-8").splitlines()
        if raw.startswith(DEPENDENCY_LOCK_GENERATION_COMMENT_PREFIX)
    ]
    if len(markers) != 1:
        raise ReleaseInputReadinessError(
            "canonical requirements lock must contain exactly one generation ID marker"
        )
    generation_id = _canonical_uuid(markers[0])
    if generation_id is None:
        raise ReleaseInputReadinessError(
            "canonical requirements lock generation ID is invalid"
        )
    return generation_id


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _contains_name(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(child, ast.Name) and child.id == name for child in ast.walk(node)
    )


def _contains_string_literal(node: ast.AST, value: str) -> bool:
    return any(
        isinstance(child, ast.Constant) and child.value == value
        for child in ast.walk(node)
    )


def _setup_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    matches = [
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    ]
    return matches[0] if len(matches) == 1 and isinstance(matches[0], ast.FunctionDef) else None


def _has_hashed_lock_call_in_setup(run_setup: ast.FunctionDef) -> bool:
    """Require the hashed-lock builder to feed the install execution call.

    A mere call (or a comment) to the builder is not enough: this is a
    release-gating check on the shipped wheel, so the current compatibility
    contract deliberately requires the builder's result to be passed directly
    to the ``install_locked_dependencies`` execution step inside the network
    and lock-available branch.
    """

    def is_lock_builder_call(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and _call_name(node) == "build_locked_dependencies_install_command"
            and any(
                keyword.arg == "requirements_lock"
                and _contains_name(keyword.value, "requirements_lock")
                for keyword in node.keywords
            )
        )

    for candidate in ast.walk(run_setup):
        if not isinstance(candidate, ast.If):
            continue
        condition_uses_network = _contains_name(candidate.test, "allow_network")
        condition_uses_lock = _contains_name(candidate.test, "requirements_lock")
        if not (condition_uses_network and condition_uses_lock):
            continue
        for nested in ast.walk(candidate):
            if not isinstance(nested, ast.Call) or _call_name(nested) != "_execute_step":
                continue
            if not nested.args or not _contains_string_literal(
                nested.args[0], "install_locked_dependencies"
            ):
                continue
            if any(is_lock_builder_call(argument) for argument in nested.args[1:]):
                return True
            if any(
                keyword.arg == "command" and is_lock_builder_call(keyword.value)
                for keyword in nested.keywords
            ):
                return True
    return False


def verify_setup_manager_hashed_lock_contract(setup_source: str) -> None:
    """Verify executable setup structure instead of comments or loose tokens.

    The distribution verifier reads this source from the embedded wrapper wheel.
    This intentionally checks the function/argument/command relationship that
    makes the release path install the canonical hashed lock, rather than a
    string which can be present only in a comment or unrelated code path.
    """

    try:
        tree = ast.parse(setup_source)
    except SyntaxError as exc:
        raise ReleaseInputReadinessError(
            f"setup lock-consumer contract source is not valid Python: {exc.msg}"
        ) from exc
    locked_builder = _setup_function(tree, "build_locked_dependencies_install_command")
    run_setup = _setup_function(tree, "run_setup")
    isolated_pip_helper = _setup_function(tree, "_isolated_pip_command")
    if locked_builder is None or run_setup is None:
        raise ReleaseInputReadinessError(
            "setup lock-consumer contract is missing its hashed-lock builder or setup entrypoint"
        )
    parameters = {
        argument.arg
        for argument in (*locked_builder.args.args, *locked_builder.args.kwonlyargs)
    }
    required_builder_literals = {"--require-hashes", "--no-deps", "-r"}
    has_required_builder_literals = all(
        _contains_string_literal(locked_builder, literal)
        for literal in required_builder_literals
    )
    uses_isolated_helper = any(
        isinstance(item, ast.Call) and _call_name(item) == "_isolated_pip_command"
        for item in ast.walk(locked_builder)
    )
    has_direct_isolated_pip = all(
        _contains_string_literal(locked_builder, literal)
        for literal in {"pip", "--isolated"}
    )
    has_helper_isolated_pip = (
        isolated_pip_helper is not None
        and all(
            _contains_string_literal(isolated_pip_helper, literal)
            for literal in {"pip", "--isolated"}
        )
    )
    returns_command = any(
        isinstance(item, ast.Return)
        and isinstance(item.value, ast.Name)
        and item.value.id == "command"
        for item in ast.walk(locked_builder)
    )
    validates_command = any(
        isinstance(item, ast.Call)
        and _call_name(item) == "validate_safe_command"
        and any(_contains_name(argument, "command") for argument in item.args)
        for item in ast.walk(locked_builder)
    )
    if (
        "requirements_lock" not in parameters
        or not has_required_builder_literals
        or not (has_direct_isolated_pip or (uses_isolated_helper and has_helper_isolated_pip))
        or not _contains_name(locked_builder, "requirements_lock")
        or not returns_command
        or not validates_command
        or not _has_hashed_lock_call_in_setup(run_setup)
    ):
        raise ReleaseInputReadinessError(
            "setup lock-consumer contract does not structurally install the canonical hashed lock"
        )


def _validate_resolver_provenance(resolver: object) -> None:
    if not isinstance(resolver, dict):
        raise ReleaseInputReadinessError("dependency lock resolver provenance is invalid")
    expected_fields = (
        set(CANONICAL_DEPENDENCY_LOCK_RESOLVER)
        | DEPENDENCY_LOCK_RESOLVER_OBSERVED_FIELDS
    )
    if set(resolver) != expected_fields:
        raise ReleaseInputReadinessError("dependency lock resolver provenance field set mismatch")
    static = {
        key: resolver.get(key) for key in CANONICAL_DEPENDENCY_LOCK_RESOLVER
    }
    if static != CANONICAL_DEPENDENCY_LOCK_RESOLVER:
        raise ReleaseInputReadinessError(
            "dependency lock resolver identity must be exactly "
            + json.dumps(CANONICAL_DEPENDENCY_LOCK_RESOLVER, sort_keys=True)
        )
    if (
        not isinstance(resolver.get("pip_version"), str)
        or PIP_VERSION.fullmatch(str(resolver["pip_version"])) is None
        or not isinstance(resolver.get("python_full_version"), str)
        or PYTHON_312_FULL_VERSION.fullmatch(str(resolver["python_full_version"])) is None
        or not isinstance(resolver.get("macos_version"), str)
        or MACOS_14_FULL_VERSION.fullmatch(str(resolver["macos_version"])) is None
        or not isinstance(resolver.get("sysconfig_platform"), str)
        or MACOS_ARM64_SYSCONFIG_PLATFORM.fullmatch(
            str(resolver["sysconfig_platform"]).lower()
        )
        is None
    ):
        raise ReleaseInputReadinessError("dependency lock resolver provenance is invalid")


def verify_canonical_dependency_lock(
    *,
    constraints: Path,
    requirements_lock: Path,
    lock_metadata: Path,
    project_file: Path = DEFAULT_PROJECT_FILE,
    setup_manager_source: Path | None = None,
    setup_manager_source_text: str | None = None,
) -> None:
    """Require a resolved lock that setup actually installs with --require-hashes."""

    if (setup_manager_source is None) == (setup_manager_source_text is None):
        raise ReleaseInputReadinessError(
            "provide exactly one setup manager source path or source text"
        )
    for path, label in (
        (constraints, "source constraints"),
        (requirements_lock, "canonical requirements lock"),
        (lock_metadata, "dependency lock metadata"),
        (project_file, "dependency lock project file"),
    ):
        _require_regular(path, label)
    if setup_manager_source is not None:
        _require_regular(setup_manager_source, "setup manager source")
        try:
            setup_manager_source_text = setup_manager_source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ReleaseInputReadinessError(
                f"could not read setup manager source: {exc}"
            ) from exc
    assert setup_manager_source_text is not None
    verify_hashed_requirement_entries(requirements_lock)
    try:
        metadata = json.loads(lock_metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseInputReadinessError(f"invalid dependency lock metadata: {exc}") from exc
    if not isinstance(metadata, dict) or set(metadata) != DEPENDENCY_LOCK_METADATA_FIELDS:
        raise ReleaseInputReadinessError("dependency lock metadata field set mismatch")
    if metadata.get("schema") != DEPENDENCY_LOCK_SCHEMA:
        raise ReleaseInputReadinessError("dependency lock metadata schema mismatch")
    metadata_generation_id = _canonical_uuid(metadata.get("generation_id"))
    if metadata_generation_id is None:
        raise ReleaseInputReadinessError("dependency lock metadata generation ID is invalid")
    if dependency_lock_generation_id(requirements_lock) != metadata_generation_id:
        raise ReleaseInputReadinessError(
            "dependency lock generation ID does not match its metadata"
        )
    if metadata.get("constraints_sha256") != _sha256_file(constraints):
        raise ReleaseInputReadinessError("dependency lock does not bind the source constraints")
    if metadata.get("project_file") != project_file.name:
        raise ReleaseInputReadinessError(
            "dependency lock metadata names a different project file"
        )
    if metadata.get("project_file_sha256") != _sha256_file(project_file):
        raise ReleaseInputReadinessError(
            "dependency lock does not bind the project dependency declarations"
        )
    if metadata.get("requirements_lock") != requirements_lock.name:
        raise ReleaseInputReadinessError("dependency lock metadata names a different requirements lock")
    if metadata.get("requirements_lock_sha256") != _sha256_file(requirements_lock):
        raise ReleaseInputReadinessError("dependency lock SHA-256 mismatch")
    if metadata.get("root_install_requirement") != "totalsegmentator-wrapper-mac[dicom,mps,dentalseg,toothseg,ios-meshsegnet]":
        raise ReleaseInputReadinessError("dependency lock root install requirement mismatch")
    lock_entries = [
        _requirement_name_and_version(line)
        for line in _logical_requirement_lines(requirements_lock)
    ]
    lock_names_in_order = [name for name, _version in lock_entries]
    lock_names = set(lock_names_in_order)
    if len(lock_names) != len(lock_names_in_order):
        raise ReleaseInputReadinessError(
            "dependency lock package inventory contains duplicate distributions"
        )
    verify_lock_excludes_bundled_overrides(lock_names)
    verify_excluded_bundled_override_metadata(
        metadata.get("excluded_bundled_overrides")
    )
    constraint_names = {_requirement_name(line) for line in _logical_requirement_lines(constraints)}
    recorded_names = metadata.get("resolved_distribution_names")
    install_names = metadata.get("install_distribution_names")
    expected_full_names = sorted(
        lock_names | set(BUNDLED_OVERRIDE_DISTRIBUTION_PINS)
    )
    if (
        not isinstance(recorded_names, list)
        or any(not isinstance(name, str) for name in recorded_names)
        or recorded_names != expected_full_names
        or not isinstance(install_names, list)
        or any(not isinstance(name, str) for name in install_names)
        or install_names != sorted(lock_names)
        or not constraint_names.issubset(set(recorded_names))
    ):
        raise ReleaseInputReadinessError("dependency lock package inventory is incomplete or inconsistent")
    if metadata.get("resolution_complete") is not True:
        raise ReleaseInputReadinessError("dependency resolution completion evidence is missing")
    _validate_resolver_provenance(metadata.get("resolver"))
    if metadata.get("pip_require_hashes") is not True or metadata.get("setup_consumes_requirements_lock") is not True:
        raise ReleaseInputReadinessError("dependency lock does not require hashed setup consumption")
    verify_setup_manager_hashed_lock_contract(setup_manager_source_text)


def _python_runtime_source_descriptor(
    *,
    policy: object,
    receipt: object,
    runtime_fingerprint: str,
    policy_sha256: str,
    receipt_sha256: str,
) -> dict[str, object]:
    policy_keys = {
        "schema", "implementation", "python_version", "source_url",
        "source_archive_sha256", "license", "receipt_schema", "build_options",
        "minimum_macos", "architecture",
    }
    if not isinstance(policy, dict) or set(policy) != policy_keys:
        raise ReleaseInputReadinessError("Python runtime source policy field set mismatch")
    source_url = policy.get("source_url")
    parsed_source_url = urlparse(source_url) if isinstance(source_url, str) else None
    try:
        source_port = parsed_source_url.port if parsed_source_url is not None else None
    except ValueError:
        source_port = -1
    archive_sha = policy.get("source_archive_sha256")
    receipt_schema = policy.get("receipt_schema")
    build_options = policy.get("build_options")
    if (
        policy.get("schema") != "totalsegmentator_wrapper_mac.python_runtime_source_policy.v1"
        or policy.get("implementation") != "CPython"
        or not isinstance(policy.get("python_version"), str)
        or re.fullmatch(r"3\.12\.(?:0|[1-9][0-9]*)", str(policy["python_version"]))
        is None
        or not isinstance(source_url, str)
        or parsed_source_url is None
        or parsed_source_url.scheme != "https"
        or not parsed_source_url.hostname
        or parsed_source_url.username is not None
        or parsed_source_url.password is not None
        or source_port not in (None, 443)
        or parsed_source_url.query
        or parsed_source_url.fragment
        or not isinstance(archive_sha, str)
        or re.fullmatch(r"[0-9a-f]{64}", archive_sha) is None
        or not isinstance(policy.get("license"), str)
        or not policy.get("license")
        or not isinstance(receipt_schema, str)
        or not receipt_schema
        or policy.get("minimum_macos") != "14.0"
        or policy.get("architecture") != "arm64"
        or not isinstance(build_options, list)
        or not build_options
        or any(not isinstance(option, str) or not option for option in build_options)
        or re.fullmatch(r"[0-9a-f]{64}", runtime_fingerprint) is None
        or re.fullmatch(r"[0-9a-f]{64}", policy_sha256) is None
        or re.fullmatch(r"[0-9a-f]{64}", receipt_sha256) is None
    ):
        raise ReleaseInputReadinessError("Python runtime source policy is incomplete")
    receipt_keys = {
        "schema", "implementation", "python_version", "source_url",
        "source_archive_sha256", "build_options", "minimum_macos",
        "architecture", "runtime_fingerprint",
    }
    if not isinstance(receipt, dict) or set(receipt) != receipt_keys:
        raise ReleaseInputReadinessError("Python runtime source-build receipt field set mismatch")
    expected_receipt = {
        "schema": policy.get("receipt_schema"),
        "implementation": policy.get("implementation"),
        "python_version": policy.get("python_version"),
        "source_url": source_url,
        "source_archive_sha256": archive_sha,
        "build_options": policy.get("build_options"),
        "minimum_macos": "14.0",
        "architecture": "arm64",
        "runtime_fingerprint": runtime_fingerprint,
    }
    if receipt != expected_receipt:
        raise ReleaseInputReadinessError(
            "Python runtime source-build receipt does not match the pinned policy/runtime payload"
        )
    return {
        "kind": "pinned-cpython-source-build",
        "implementation": policy["implementation"],
        "python_version": policy["python_version"],
        "source_url": source_url,
        "source_archive_sha256": archive_sha,
        "license": policy["license"],
        "build_options": build_options,
        "minimum_macos": "14.0",
        "architecture": "arm64",
        "runtime_fingerprint": expected_receipt["runtime_fingerprint"],
        "policy_sha256": policy_sha256,
        "receipt_sha256": receipt_sha256,
    }


def verify_python_runtime_source_provenance(
    *,
    policy_path: Path,
    receipt_path: Path,
    runtime_root: Path,
) -> dict[str, object]:
    """Bind the bundled Python payload to external reviewed provenance files."""

    if not runtime_root.is_dir() or runtime_root.is_symlink():
        raise ReleaseInputReadinessError("Python runtime root must be a non-symlink directory")
    _require_external_regular(
        policy_path, runtime_root, "Python runtime source policy"
    )
    _require_external_regular(
        receipt_path, runtime_root, "Python runtime source-build receipt"
    )
    try:
        policy_bytes = policy_path.read_bytes()
        receipt_bytes = receipt_path.read_bytes()
        policy = json.loads(policy_bytes)
        receipt = json.loads(receipt_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseInputReadinessError(f"invalid Python runtime provenance JSON: {exc}") from exc
    try:
        runtime_fingerprint = fingerprint_runtime_tree(runtime_root)
    except RuntimeFingerprintError as exc:
        raise ReleaseInputReadinessError(
            f"Python runtime payload cannot be fingerprinted safely: {exc}"
        ) from exc
    return _python_runtime_source_descriptor(
        policy=policy,
        receipt=receipt,
        runtime_fingerprint=runtime_fingerprint,
        policy_sha256=hashlib.sha256(policy_bytes).hexdigest(),
        receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
    )


def validate_packaged_python_runtime_provenance(
    source_manifest: object,
    *,
    policy_bytes: bytes,
    receipt_bytes: bytes,
    runtime_fingerprint: str,
) -> None:
    """Validate copied policy/receipt bytes against a pre-sign manifest digest."""

    try:
        policy = json.loads(policy_bytes)
        receipt = json.loads(receipt_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseInputReadinessError(
            f"invalid packaged Python runtime provenance JSON: {exc}"
        ) from exc
    expected = _python_runtime_source_descriptor(
        policy=policy,
        receipt=receipt,
        runtime_fingerprint=runtime_fingerprint,
        policy_sha256=hashlib.sha256(policy_bytes).hexdigest(),
        receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
    )
    expected.update(
        {
            "policy_bundled_path": "licenses/python-runtime-source-policy.json",
            "receipt_bundled_path": "licenses/python-runtime-build-provenance.json",
        }
    )
    if source_manifest != expected:
        raise ReleaseInputReadinessError(
            "packaged Python runtime provenance does not match the app manifest"
        )


def verify_setup_weight_revalidation_complete(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ReleaseInputReadinessError(
            f"setup weights manifest must be a regular file: {path}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseInputReadinessError(f"invalid setup weights manifest: {exc}") from exc
    assets = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(assets, list):
        raise ReleaseInputReadinessError("setup weights manifest assets are missing")
    pending: list[str] = []
    malformed: list[str] = []
    for item in assets:
        if not isinstance(item, dict):
            raise ReleaseInputReadinessError("setup weights manifest contains a malformed asset")
        task_label = f"task {item.get('task_id', 'unknown')} ({item.get('filename', 'unknown')})"
        if item.get("publisher_digest_available") is True:
            if (
                item.get("sha256_source") != "github-release-digest"
                or "revalidation_required_before_release" in item
                or "local_observation_evidence" in item
                or "revalidation_evidence" in item
            ):
                malformed.append(task_label)
            continue
        if item.get("publisher_digest_available") is not False:
            malformed.append(task_label)
            continue
        state = item.get("revalidation_required_before_release")
        if state is True:
            if (
                item.get("sha256_source") != "locally-observed-official-asset"
                or item.get("local_observation_evidence")
                != "not-preserved-unverified"
                or "revalidation_evidence" in item
            ):
                malformed.append(task_label)
            else:
                pending.append(task_label)
        elif state is False:
            if (
                item.get("sha256_source")
                != "approved-official-asset-revalidation"
                or "local_observation_evidence" in item
                or not _valid_setup_weight_revalidation_evidence(item)
            ):
                malformed.append(task_label)
        else:
            malformed.append(task_label)
    if malformed:
        raise ReleaseInputReadinessError(
            "setup weights contain malformed or unsubstantiated checksum provenance: "
            + ", ".join(malformed)
        )
    if pending:
        raise ReleaseInputReadinessError(
            "setup weights still require official-asset revalidation before release: "
            + ", ".join(pending)
        )


def _valid_setup_weight_revalidation_evidence(item: dict[str, object]) -> bool:
    evidence = item.get("revalidation_evidence")
    if not isinstance(evidence, dict) or set(evidence) != {
        "schema",
        "official_url",
        "release_tag",
        "filename",
        "size_bytes",
        "sha256",
        "verified_at_utc",
        "transport",
        "checks",
        "approval",
    }:
        return False
    verified_at = evidence.get("verified_at_utc")
    return (
        evidence.get("schema") == REVALIDATION_EVIDENCE_SCHEMA
        and evidence.get("official_url") == item.get("url")
        and evidence.get("release_tag") == item.get("release_tag")
        and evidence.get("filename") == item.get("filename")
        and evidence.get("size_bytes") == item.get("size_bytes")
        and evidence.get("sha256") == item.get("sha256")
        and valid_revalidation_timestamp(verified_at)
        and evidence.get("transport")
        == "https-pinned-official-release-asset"
        and evidence.get("checks") == REVALIDATION_CHECKS
        and evidence.get("approval") == "approved-for-release"
    )


def valid_revalidation_timestamp(value: object) -> bool:
    if (
        not isinstance(value, str)
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
            value,
        )
        is None
    ):
        return False
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify immutable dependency and setup-weight release inputs.")
    parser.add_argument("--constraints", type=Path, required=True)
    parser.add_argument("--requirements-lock", type=Path, required=True)
    parser.add_argument("--lock-metadata", type=Path, required=True)
    parser.add_argument("--project-file", type=Path, required=True)
    parser.add_argument("--setup-manager-source", type=Path, required=True)
    parser.add_argument("--setup-weights-manifest", type=Path, required=True)
    parser.add_argument("--python-runtime-policy", type=Path, required=True)
    parser.add_argument("--python-runtime-receipt", type=Path, required=True)
    parser.add_argument("--python-runtime-root", type=Path, required=True)
    parser.add_argument("--release-build-toolchain-lock", type=Path)
    parser.add_argument("--release-build-toolchain-metadata", type=Path)
    parser.add_argument("--release-build-toolchain-wheelhouse", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    failures: list[str] = []
    python_runtime_source: dict[str, object] | None = None
    release_build_toolchain: dict[str, object] | None = None
    toolchain_args = (
        args.release_build_toolchain_lock,
        args.release_build_toolchain_metadata,
        args.release_build_toolchain_wheelhouse,
    )
    if any(value is not None for value in toolchain_args) and not all(
        value is not None for value in toolchain_args
    ):
        raise ReleaseInputReadinessError(
            "release build toolchain lock, metadata, and wheelhouse must be provided together"
        )
    checks = (
        ("dependencies", lambda: verify_canonical_dependency_lock(
            constraints=args.constraints.expanduser(),
            requirements_lock=args.requirements_lock.expanduser(),
            lock_metadata=args.lock_metadata.expanduser(),
            project_file=args.project_file.expanduser(),
            setup_manager_source=args.setup_manager_source.expanduser(),
        )),
        ("weights", lambda: verify_setup_weight_revalidation_complete(
            args.setup_weights_manifest.expanduser()
        )),
        ("python_runtime", lambda: verify_python_runtime_source_provenance(
            policy_path=args.python_runtime_policy.expanduser(),
            receipt_path=args.python_runtime_receipt.expanduser(),
            runtime_root=args.python_runtime_root.expanduser(),
        )),
    )
    for name, verifier in checks:
        try:
            result = verifier()
            if name == "python_runtime":
                python_runtime_source = result
        except ReleaseInputReadinessError as exc:
            failures.append(str(exc))
    if all(value is not None for value in toolchain_args):
        assert args.release_build_toolchain_lock is not None
        assert args.release_build_toolchain_metadata is not None
        assert args.release_build_toolchain_wheelhouse is not None
        try:
            release_build_toolchain = verify_release_build_toolchain_inputs(
                lock_path=args.release_build_toolchain_lock.expanduser(),
                metadata_path=args.release_build_toolchain_metadata.expanduser(),
                wheelhouse=args.release_build_toolchain_wheelhouse.expanduser(),
            )
        except ReleaseBuildToolchainError as exc:
            failures.append(str(exc))
    if failures:
        raise ReleaseInputReadinessError(
            "release input readiness failed:\n" + "\n".join(failures)
        )
    if args.json:
        try:
            lock_metadata = json.loads(
                args.lock_metadata.expanduser().read_text(encoding="utf-8")
            )
            excluded_bundled_overrides = lock_metadata["excluded_bundled_overrides"]
        except (UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ReleaseInputReadinessError(
                "validated dependency lock metadata could not be read for build provenance"
            ) from exc
        print(
            json.dumps(
                {
                    "python_runtime_source": python_runtime_source,
                    "dependency_lock": {
                        "excluded_bundled_overrides": excluded_bundled_overrides
                    },
                    "release_build_toolchain": release_build_toolchain,
                },
                sort_keys=True,
            )
        )
    else:
        print("PASS: dependency lock, setup weights, and Python runtime source are release-attested")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseInputReadinessError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
