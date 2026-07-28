#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_JSON="${1:-}"

if [[ -z "${EVIDENCE_JSON}" ]]; then
  echo "Usage: $0 /path/to/test_account_install_evidence.json" >&2
  exit 2
fi
if [[ ! -f "${EVIDENCE_JSON}" ]]; then
  echo "Evidence JSON not found: ${EVIDENCE_JSON}" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-${ROOT}/.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

"${PYTHON_BIN}" - "${ROOT}" "${EVIDENCE_JSON}" "${TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE:-0}" <<'PY'
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


root = Path(sys.argv[1]).resolve()
source = Path(sys.argv[2]).expanduser().resolve()
allow_zero_env = sys.argv[3] == "1"
current_home = Path.home().resolve()

payload = json.loads(source.read_text(encoding="utf-8"))
expected_app_version_from_env = os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_APP_VERSION", "").strip()
expected_app_version = expected_app_version_from_env or str(payload.get("expected_app_version") or "").strip()
required_checks = [
    "app_codesign_valid",
    "spctl_app_accepted",
    "stapler_dmg_valid",
    "setup_state_success",
    "mps_actual_device",
    "mps_gate_pass",
    "normalizer_from_app_bundle",
    "python_version_312",
    "python_executable_inside_app",
    "app_support_inside_current_home",
    "no_user_global_pip_cache",
    "pip_cache_under_app_support",
    "pycache_under_app_support",
    "manifest_ui_frontend_swiftui",
    "manifest_notarized",
    "manifest_bundled_python312",
    "manifest_python_bundled",
    "bundled_python_exists",
    "bundled_python_has_no_absolute_symlinks",
    "manifest_has_app_version",
    "manifest_has_build_id",
    "manifest_has_dependency_set_id",
    "manifest_has_wheel_sha256",
    "manifest_has_constraints_sha256",
    "manifest_has_normalizer_sha256",
    "manifest_has_dcm2niix_sha256",
    "manifest_has_dcm2niix_version",
    "manifest_has_dcm2niix_source",
    "manifest_has_sample1_manifest_sha256",
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
]
if expected_app_version:
    required_checks.append("manifest_app_version_matches_expected")
checks = {item.get("name"): bool(item.get("passed")) for item in payload.get("checks", []) if isinstance(item, dict)}
missing = [name for name in required_checks if name not in checks]
failed = [name for name in required_checks if checks.get(name) is False]

evidence_home = Path(str(payload.get("home", ""))).expanduser()
try:
    evidence_home = evidence_home.resolve()
except Exception:
    pass

def is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False

home_failures: list[str] = []
if not evidence_home.is_absolute():
    home_failures.append("evidence_home_not_absolute")
if is_under(evidence_home, Path("/tmp")) or is_under(evidence_home, Path("/private/tmp")):
    home_failures.append("evidence_home_is_temporary")
if evidence_home == current_home:
    home_failures.append("evidence_home_is_current_development_home")
if not is_under(evidence_home, Path("/Users")) and not allow_zero_env:
    home_failures.append("evidence_home_not_under_users")

if allow_zero_env:
    home_failures = []

passed = payload.get("passed") is True and not missing and not failed and not home_failures

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
out_dir = root / "artifacts" / "test_account_install" / stamp
out_dir.mkdir(parents=True, exist_ok=True)
copied = out_dir / "test_account_install_evidence.json"
shutil.copy2(source, copied)

verdict = {
    "schema": "totalsegmentator_wrapper_mac.test_account_install_verdict.v1",
    "passed": passed,
    "source_evidence": str(source),
    "copied_evidence": str(copied),
    "required_checks": required_checks,
    "missing_checks": missing,
    "failed_checks": failed,
    "home_failures": home_failures,
    "allow_zero_env_evidence": allow_zero_env,
    "expected_app_version": expected_app_version or None,
    "development_home": str(current_home),
    "evidence_home": payload.get("home"),
    "evidence_app_path": payload.get("app_path"),
    "evidence_support_dir": payload.get("support_dir"),
}
verdict_path = out_dir / "test_account_install_verdict.json"
verdict_path.write_text(json.dumps(verdict, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(verdict, indent=2, ensure_ascii=False))
raise SystemExit(0 if passed else 1)
PY
