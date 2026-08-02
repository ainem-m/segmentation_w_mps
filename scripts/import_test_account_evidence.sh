#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_JSON="${1:-}"

if [[ -z "${EVIDENCE_JSON}" ]]; then
  echo "Usage: $0 /path/to/test_account_install_evidence.json" >&2
  exit 2
fi
PYTHON_BIN="${PYTHON_BIN:-${ROOT}/.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

"${PYTHON_BIN}" - "${ROOT}" "${EVIDENCE_JSON}" "${TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE:-0}" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path


EVIDENCE_SCHEMA = "totalsegmentator_wrapper_mac.test_account_install_evidence.v2"
PREFLIGHT_FAILURE_EVIDENCE_SCHEMA = (
    "totalsegmentator_wrapper_mac.test_account_install_preflight_failure.v1"
)
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_EVIDENCE_AGE_SECONDS = 7 * 24 * 60 * 60
EVIDENCE_FIELDS = {
    "schema",
    "passed",
    "run_id",
    "collected_at_utc",
    "home",
    "app_path",
    "support_dir",
    "state_json",
    "manifest_path",
    "shared_copy_path",
    "expected_app_version",
    "app_identity",
    "checks",
}
PREFLIGHT_FAILURE_EVIDENCE_FIELDS = {
    "schema",
    "passed",
    "run_id",
    "collected_at_utc",
    "preflight_failure",
}
PREFLIGHT_FAILURE_REASONS = {
    "app_not_found_in_expected_location",
    "app_path_is_not_a_directory",
    "setup_state_missing",
    "setup_runtime_python_missing",
    "collector_failed_to_publish_current_evidence",
}


root = Path(sys.argv[1]).resolve()
source = Path(sys.argv[2]).expanduser().absolute()
allow_zero_env = sys.argv[3] == "1"
current_home = Path.home().resolve()


def read_regular_non_symlink(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"evidence_source_missing:{exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("evidence_source_must_be_regular_non_symlink")
    if metadata.st_size < 0 or metadata.st_size > MAX_EVIDENCE_BYTES:
        raise ValueError("evidence_source_size_out_of_bounds")
    try:
        nofollow = os.O_NOFOLLOW
    except AttributeError as exc:  # pragma: no cover - supported macOS always has it
        raise ValueError("evidence_source_safe_open_is_unavailable") from exc
    flags = os.O_RDONLY | nofollow
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"evidence_source_open_failed:{exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("evidence_source_must_be_regular_non_symlink")
        if (
            opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or opened.st_size != metadata.st_size
            or opened.st_mtime_ns != metadata.st_mtime_ns
            or opened.st_ctime_ns != metadata.st_ctime_ns
        ):
            raise ValueError("evidence_source_changed_while_opening")
        chunks: list[bytes] = []
        received = 0
        while chunk := os.read(descriptor, 64 * 1024):
            received += len(chunk)
            if received > MAX_EVIDENCE_BYTES:
                raise ValueError("evidence_source_size_out_of_bounds")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            received != opened.st_size
            or not stat.S_ISREG(after.st_mode)
            or after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise ValueError("evidence_source_changed_while_reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    try:
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def parse_project_version(path: Path) -> str | None:
    try:
        source_text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    matches = re.findall(r'^version\s*=\s*"([^"\r\n]+)"\s*$', source_text, flags=re.MULTILINE)
    if len(matches) != 1:
        return None
    value = matches[0].strip()
    return value or None


def normalized_absolute_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    path = Path(value)
    if not path.is_absolute() or any(part in ("", ".", "..") for part in path.parts[1:]):
        return None
    return path


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def reject_duplicate_json_keys(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


source_bytes: bytes | None = None
payload: dict[str, object] = {}
malformed_evidence: list[str] = []
try:
    source_bytes = read_regular_non_symlink(source)
    decoded = json.loads(
        source_bytes.decode("utf-8"), object_pairs_hook=reject_duplicate_json_keys
    )
    if not isinstance(decoded, dict):
        raise ValueError("evidence_payload_must_be_object")
    payload = decoded
except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
    malformed_evidence.append(str(exc))

project_version = parse_project_version(root / "pyproject.toml")
identity_failures: list[str] = []
if project_version is None:
    identity_failures.append("project_version_unavailable")
expected_app_version_from_env = os.environ.get(
    "TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_APP_VERSION", ""
).strip()
if (
    expected_app_version_from_env
    and project_version is not None
    and expected_app_version_from_env != project_version
):
    identity_failures.append("expected_app_version_env_does_not_match_project_version")
expected_app_version = project_version or expected_app_version_from_env
if not expected_app_version:
    identity_failures.append("expected_app_version_unavailable")

try:
    max_evidence_age_seconds = int(
        os.environ.get(
            "TOTALSEGMENTATOR_WRAPPER_MAC_MAX_EVIDENCE_AGE_SECONDS",
            str(DEFAULT_MAX_EVIDENCE_AGE_SECONDS),
        )
    )
except ValueError:
    max_evidence_age_seconds = 0
if not 60 <= max_evidence_age_seconds <= 30 * 24 * 60 * 60:
    identity_failures.append("invalid_evidence_max_age")

payload_schema = payload.get("schema")
is_preflight_failure_evidence = payload_schema == PREFLIGHT_FAILURE_EVIDENCE_SCHEMA
if is_preflight_failure_evidence:
    # This is a deliberately non-importable diagnostic record, emitted before
    # the private runtime can produce the complete release-evidence schema.
    # Validate its small contract so a malformed record does not masquerade as
    # a genuine collector result, then reject it with a specific operator cue.
    malformed_evidence.append("preflight_failure_evidence_cannot_be_imported")
    if set(payload) != PREFLIGHT_FAILURE_EVIDENCE_FIELDS:
        malformed_evidence.append("invalid_preflight_failure_evidence_fields")
    if payload.get("passed") is not False:
        malformed_evidence.append("invalid_preflight_failure_evidence_passed")
    if payload.get("preflight_failure") not in PREFLIGHT_FAILURE_REASONS:
        malformed_evidence.append("invalid_preflight_failure_reason")
else:
    if payload_schema != EVIDENCE_SCHEMA:
        malformed_evidence.append("unexpected_evidence_schema")
    if set(payload) != EVIDENCE_FIELDS:
        malformed_evidence.append("evidence_field_set_mismatch")
    if type(payload.get("passed")) is not bool:
        malformed_evidence.append("top_level_passed_not_boolean")
run_id = payload.get("run_id")
if not isinstance(run_id, str) or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
    malformed_evidence.append("invalid_run_id")
collected_at_utc = payload.get("collected_at_utc")
if not isinstance(collected_at_utc, str) or not collected_at_utc.endswith("Z"):
    identity_failures.append("invalid_evidence_timestamp")
else:
    try:
        collected_at = datetime.fromisoformat(collected_at_utc[:-1] + "+00:00")
    except ValueError:
        identity_failures.append("invalid_evidence_timestamp")
    else:
        age_seconds = (datetime.now(timezone.utc) - collected_at).total_seconds()
        if age_seconds > max_evidence_age_seconds:
            identity_failures.append("evidence_is_stale")
        elif age_seconds < -300:
            identity_failures.append("evidence_collected_in_future")

required_checks = [
    "app_codesign_valid",
    "spctl_app_accepted",
    "stapler_dmg_valid",
    "setup_state_success",
    "mps_actual_device",
    "mps_gate_pass",
    "mps_no_fallback",
    "install_wheel_step_success",
    "wheel_install_hashed_lock",
    "install_bundled_wheels_step_success",
    "install_locked_dependencies_step_success",
    "pip_check_step_success",
    "normalizer_from_app_bundle",
    "installed_fpsample_version",
    "installed_fpsample_import_sample",
    "installed_acvl_utils_version",
    "installed_acvl_utils_import",
    "python_version_312",
    "python_executable_inside_app",
    "app_support_inside_current_home",
    "no_user_global_pip_cache",
    "pip_cache_under_app_support",
    "pycache_under_app_support",
    "manifest_ui_frontend_swiftui",
    "app_minimum_macos_version_14",
    "app_and_wheel_macho_macos14_arm64",
    "dicom_helpers_system_linkage_no_rpath",
    "normalizer_source_matches_bundled_receipts",
    "dcm2niix_source_matches_bundled_receipt_and_pointer",
    "manifest_notarized",
    "manifest_bundled_python312",
    "manifest_python_bundled",
    "bundled_python_exists",
    "bundled_python_has_no_absolute_symlinks",
    "manifest_has_app_version",
    "manifest_has_build_id",
    "manifest_has_dependency_set_id",
    "manifest_has_wheel_sha256",
    "manifest_has_fpsample_wheel_sha256",
    "bundled_fpsample_wheel_sha256_matches_manifest",
    "manifest_has_acvl_utils_wheel_sha256",
    "bundled_acvl_utils_wheel_sha256_matches_manifest",
    "manifest_has_constraints_sha256",
    "manifest_has_requirements_lock_sha256",
    "manifest_has_dependency_lock_metadata_sha256",
    "bundled_requirements_lock_sha256_matches_manifest",
    "bundled_dependency_lock_metadata_sha256_matches_manifest",
    "manifest_has_normalizer_input_sha256",
    "manifest_has_normalizer_sha256",
    "manifest_has_normalizer_sha256_scope",
    "normalizer_input_digest_scope_explicit",
    "manifest_has_dcm2niix_input_sha256",
    "manifest_has_dcm2niix_sha256",
    "manifest_has_dcm2niix_sha256_scope",
    "dcm2niix_input_digest_scope_explicit",
    "manifest_has_dcm2niix_version",
    "manifest_has_dcm2niix_source",
    "manifest_has_sample1_manifest_sha256",
    "manifest_has_setup_weights_manifest_sha256",
    "manifest_has_update_manifest_url",
    "manifest_has_update_allowed_hosts",
    "manifest_has_third_party_licenses",
    "manifest_includes_sample1",
    "bundled_dcm2niix_exists",
    "manifest_license_apache_2_0",
    "wrapper_license_exists",
    "wrapper_notice_exists",
    "totalsegmentator_license_exists",
    "dentalsegmentator_notice_exists",
    "toothseg_notice_exists",
    "dcm2niix_license_exists",
    "license_inventory_exists",
    "license_inventory_unresolved_zero",
    "license_surfaces_no_old_first_party_markers",
    "sample1_input_exists",
    "sample1_surface_preview_exists",
    "sample1_manifest_exists",
    "sample1_notices_exists",
    "sample1_manifest_non_clinical",
    "setup_state_installed_bundle_current",
    "installed_requirements_lock_sha256_matches_manifest",
    "installed_dependency_lock_metadata_sha256_matches_manifest",
    "app_bundle_in_expected_install_location",
    "app_bundle_not_symlink",
    "manifest_release_identity_complete",
]
if expected_app_version:
    required_checks.append("manifest_app_version_matches_expected")
checks: dict[str, bool] = {}
raw_checks = payload.get("checks")
if not isinstance(raw_checks, list):
    malformed_evidence.append("checks_must_be_list")
else:
    for item in raw_checks:
        if not isinstance(item, dict):
            malformed_evidence.append("check_must_be_object")
            continue
        name = item.get("name")
        if not isinstance(name, str) or re.fullmatch(r"[a-z0-9_]+", name) is None:
            malformed_evidence.append("invalid_check_name")
            continue
        if name in checks:
            malformed_evidence.append("duplicate_check_name")
            continue
        value = item.get("passed")
        if type(value) is not bool:
            malformed_evidence.append(f"check_passed_not_boolean:{name}")
            continue
        checks[name] = value
missing = [name for name in required_checks if name not in checks]
failed = [name for name in required_checks if checks.get(name) is False]

evidence_home = normalized_absolute_path(payload.get("home"))
evidence_app_path = normalized_absolute_path(payload.get("app_path"))
evidence_support_dir = normalized_absolute_path(payload.get("support_dir"))
evidence_state_json = normalized_absolute_path(payload.get("state_json"))
evidence_manifest_path = normalized_absolute_path(payload.get("manifest_path"))
evidence_shared_copy_path = normalized_absolute_path(payload.get("shared_copy_path"))

def is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False

home_failures: list[str] = []
if evidence_home is None:
    home_failures.append("evidence_home_not_absolute")
elif is_under(evidence_home, Path("/tmp")) or is_under(evidence_home, Path("/private/tmp")):
    home_failures.append("evidence_home_is_temporary")
if evidence_home == current_home:
    home_failures.append("evidence_home_is_current_development_home")
if evidence_home is not None and not is_under(evidence_home, Path("/Users")) and not allow_zero_env:
    home_failures.append("evidence_home_not_under_users")

if allow_zero_env:
    home_failures = []

identity = payload.get("app_identity")
if not isinstance(identity, dict):
    identity_failures.append("app_identity_missing")
else:
    expected_identity_fields = {
        "app_version",
        "build_id",
        "dependency_set_id",
        "setup_manifest_sha256",
        "info_plist_sha256",
        "dmg_path",
        "dmg_sha256",
    }
    if set(identity) != expected_identity_fields:
        identity_failures.append("app_identity_field_set_mismatch")
    if identity.get("app_version") != expected_app_version:
        identity_failures.append("app_version_does_not_match_project_version")
    if payload.get("expected_app_version") != expected_app_version:
        identity_failures.append("evidence_expected_app_version_mismatch")
    for field in ("build_id", "dependency_set_id"):
        if not isinstance(identity.get(field), str) or not identity[field].strip():
            identity_failures.append(f"invalid_app_identity_{field}")
    for field in ("setup_manifest_sha256", "info_plist_sha256"):
        if not valid_sha256(identity.get(field)):
            identity_failures.append(f"invalid_app_identity_{field}")
    dmg_path = identity.get("dmg_path")
    dmg_sha256 = identity.get("dmg_sha256")
    if (dmg_path is None) != (dmg_sha256 is None):
        identity_failures.append("dmg_identity_incomplete")
    elif dmg_path is not None:
        if normalized_absolute_path(dmg_path) is None or not valid_sha256(dmg_sha256):
            identity_failures.append("invalid_dmg_identity")
    expected_dmg_sha256 = os.environ.get(
        "TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_DMG_SHA256", ""
    ).strip()
    if expected_dmg_sha256 and dmg_sha256 != expected_dmg_sha256:
        identity_failures.append("dmg_sha256_does_not_match_expected")

if evidence_app_path is None or evidence_home is None:
    identity_failures.append("invalid_evidence_app_or_home_path")
else:
    valid_app_parents = {Path("/Applications"), evidence_home / "Applications"}
    if evidence_app_path.name != "TotalSegmentator Wrapper for Mac.app" or evidence_app_path.parent not in valid_app_parents:
        identity_failures.append("app_path_is_not_expected_install_location")
if (
    evidence_home is None
    or evidence_support_dir is None
    or evidence_support_dir
    != evidence_home / "Library" / "Application Support" / "TotalSegmentatorWrapperMac"
):
    identity_failures.append("support_dir_does_not_match_evidence_home")
if evidence_support_dir is None or evidence_state_json != evidence_support_dir / "setup_state.json":
    identity_failures.append("state_json_does_not_match_support_dir")
if (
    evidence_app_path is None
    or evidence_manifest_path
    != evidence_app_path / "Contents" / "Resources" / "setup_manifest.json"
):
    identity_failures.append("manifest_path_does_not_match_app_path")
if evidence_shared_copy_path is None:
    identity_failures.append("invalid_shared_copy_path")

passed = (
    payload.get("passed") is True
    and not missing
    and not failed
    and not home_failures
    and not malformed_evidence
    and not identity_failures
)

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
run_prefix = run_id[:8] if isinstance(run_id, str) else "invalid-run"
artifact_suffix = f"{run_prefix}-{secrets.token_hex(4)}"
out_dir = root / "artifacts" / "test_account_install" / f"{stamp}-{artifact_suffix}"
out_dir.mkdir(parents=True, exist_ok=False)
copied = out_dir / "test_account_install_evidence.json"
if source_bytes is not None:
    atomic_write(copied, source_bytes)

verdict = {
    "schema": "totalsegmentator_wrapper_mac.test_account_install_verdict.v2",
    "passed": passed,
    "source_evidence": str(source),
    "source_evidence_sha256": sha256(source_bytes) if source_bytes is not None else None,
    "copied_evidence": str(copied) if source_bytes is not None else None,
    "required_checks": required_checks,
    "missing_checks": missing,
    "failed_checks": failed,
    "malformed_evidence": sorted(set(malformed_evidence)),
    "identity_failures": sorted(set(identity_failures)),
    "home_failures": home_failures,
    "allow_zero_env_evidence": allow_zero_env,
    "expected_app_version": expected_app_version or None,
    "project_version": project_version,
    "max_evidence_age_seconds": max_evidence_age_seconds,
    "evidence_run_id": run_id if isinstance(run_id, str) else None,
    "evidence_collected_at_utc": collected_at_utc if isinstance(collected_at_utc, str) else None,
    "development_home": str(current_home),
    "evidence_home": payload.get("home"),
    "evidence_app_path": payload.get("app_path"),
    "evidence_support_dir": payload.get("support_dir"),
}
verdict_path = out_dir / "test_account_install_verdict.json"
atomic_write(
    verdict_path,
    (json.dumps(verdict, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
)
print(json.dumps(verdict, indent=2, ensure_ascii=False))
raise SystemExit(0 if passed else 1)
PY
