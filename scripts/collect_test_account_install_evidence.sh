#!/bin/bash
set -euo pipefail

APP_NAME="TotalSegmentator Wrapper for Mac.app"
APP_PATH="${1:-}"

if [[ -z "${APP_PATH}" ]]; then
  if [[ -d "${HOME}/Applications/${APP_NAME}" ]]; then
    APP_PATH="${HOME}/Applications/${APP_NAME}"
  elif [[ -d "/Applications/${APP_NAME}" ]]; then
    APP_PATH="/Applications/${APP_NAME}"
else
    echo "TotalSegmentator Wrapper for Mac.app が ~/Applications または /Applications に見つかりません。" >&2
    echo "必要ならアプリのパスを明示してください: $0 /path/to/TotalSegmentator\\ Wrapper\\ for\\ Mac.app" >&2
    exit 2
  fi
fi

SUPPORT_DIR="${HOME}/Library/Application Support/TotalSegmentatorWrapperMac"
STATE_JSON="${SUPPORT_DIR}/setup_state.json"
EVIDENCE_JSON="${SUPPORT_DIR}/logs/test_account_install_evidence.json"
VENV_PYTHON="${SUPPORT_DIR}/env/bin/python"
SHARED_EVIDENCE_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_SHARED_EVIDENCE_DIR:-/Users/Shared/TotalSegmentatorWrapperMac}"
SHARED_EVIDENCE_JSON="${SHARED_EVIDENCE_DIR}/test_account_install_evidence.json"
DMG_PATH="${TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH:-}"
EXPECTED_APP_VERSION="${TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_APP_VERSION:-}"

if [[ ! -d "${APP_PATH}" ]]; then
  echo "App bundle が見つかりません: ${APP_PATH}" >&2
  exit 2
fi
if [[ ! -f "${STATE_JSON}" ]]; then
  echo "Setup状態ファイルが見つかりません: ${STATE_JSON}" >&2
  echo "このアカウントで TotalSegmentator Wrapper for Mac.app を開き、先にSetupを実行してください。" >&2
  exit 1
fi
if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "Setup済みruntime Pythonが見つかりません: ${VENV_PYTHON}" >&2
  exit 1
fi

mkdir -p "${SUPPORT_DIR}/logs"

set +e
"${VENV_PYTHON}" - "${APP_PATH}" "${SUPPORT_DIR}" "${STATE_JSON}" "${EVIDENCE_JSON}" "${SHARED_EVIDENCE_JSON}" "${DMG_PATH}" "${EXPECTED_APP_VERSION}" <<'PY'
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


app_path = Path(sys.argv[1]).expanduser().resolve()
support_dir = Path(sys.argv[2]).expanduser().resolve()
state_json = Path(sys.argv[3]).expanduser().resolve()
evidence_json = Path(sys.argv[4]).expanduser().resolve()
shared_evidence_json = Path(sys.argv[5]).expanduser().resolve()
dmg_path_arg = sys.argv[6]
expected_app_version = sys.argv[7].strip()
home = Path.home().resolve()
resources = app_path / "Contents" / "Resources"
manifest_path = resources / "setup_manifest.json"
runtime_dir = resources / "python" / "cpython-3.12"
runtime_python = runtime_dir / "bin" / "python3.12"
bundled_dcm2niix = resources / "bin" / "dcm2niix"
license_inventory = resources / "licenses" / "third_party_license_inventory.json"
wrapper_license = resources / "LICENSE"
wrapper_notice = resources / "NOTICE"
totalsegmentator_license = resources / "licenses" / "TotalSegmentator-Apache-2.0.txt"
dentalsegmentator_notice = resources / "licenses" / "DentalSegmentator-NOTICE.txt"
toothseg_notice = resources / "licenses" / "ToothSeg-NOTICE.txt"
dcm2niix_license = resources / "licenses" / "dcm2niix-license.txt"
sample1_input = resources / "sample1" / "input" / "owner_cbct_jawcrop_0p5mm.nii.gz"
sample1_viewer = resources / "sample1" / "surface_preview" / "index.html"
sample1_manifest = resources / "sample1" / "sample_manifest.json"
sample1_notices = resources / "sample1" / "THIRD_PARTY_NOTICES.txt"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive for field script
        return {"_load_error": repr(exc)}


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


state = load_json(state_json)
manifest = load_json(manifest_path)
checks: list[dict] = []


def check(name: str, passed: bool, detail: object = None) -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


codesign = shutil.which("codesign")
if codesign:
    proc = subprocess.run(
        [codesign, "--verify", "--deep", "--strict", "--verbose=2", str(app_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    check("app_codesign_valid", proc.returncode == 0, (proc.stderr or proc.stdout).strip())
else:
    check("app_codesign_valid", False, "codesign not found")

spctl = shutil.which("spctl")
if spctl:
    proc = subprocess.run(
        [spctl, "--assess", "--type", "execute", "--verbose=4", str(app_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    check("spctl_app_accepted", proc.returncode == 0, (proc.stderr or proc.stdout).strip())
else:
    check("spctl_app_accepted", False, "spctl not found")

is_notarized_manifest = manifest.get("notarized") is True
check("manifest_notarized", is_notarized_manifest, manifest.get("notarized"))
stapler = shutil.which("xcrun")
dmg_path = Path(dmg_path_arg).expanduser() if dmg_path_arg else None
if stapler and dmg_path and dmg_path.exists():
    proc = subprocess.run(
        [stapler, "stapler", "validate", str(dmg_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    check("stapler_dmg_valid", proc.returncode == 0, (proc.stderr or proc.stdout).strip())
elif is_notarized_manifest:
    check("stapler_dmg_valid", False, "set TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH to the notarized DMG for this check")
else:
    check("stapler_dmg_valid", False, "notarized manifest is false")

check("setup_state_success", state.get("status") == "success", state.get("status"))
check("mps_actual_device", state.get("doctor", {}).get("actual_device") == "mps", state.get("doctor", {}).get("actual_device"))
check("mps_gate_pass", state.get("doctor", {}).get("convtranspose3d_fp32") == "pass", state.get("doctor", {}).get("convtranspose3d_fp32"))
check(
    "normalizer_from_app_bundle",
    state.get("dicom_normalizer", {}).get("normalizer_source") == "app_bundle",
    state.get("dicom_normalizer", {}).get("normalizer_source"),
)
check("python_version_312", str(state.get("python_version", "")).startswith("3.12."), state.get("python_version"))

state_python = state.get("python_executable")
check(
    "python_executable_inside_app",
    bool(state_python) and is_relative_to(Path(state_python), app_path),
    state_python,
)

state_support = state.get("paths", {}).get("app_support")
check(
    "app_support_inside_current_home",
    bool(state_support) and is_relative_to(Path(state_support), home),
    state_support,
)
check("no_user_global_pip_cache", not (home / "Library" / "Caches" / "pip").exists(), str(home / "Library" / "Caches" / "pip"))
check("pip_cache_under_app_support", (support_dir / "cache" / "pip").is_dir(), str(support_dir / "cache" / "pip"))
check("pycache_under_app_support", (support_dir / "cache" / "pycache").is_dir(), str(support_dir / "cache" / "pycache"))

runtime = manifest.get("python_runtime", {})
check("manifest_ui_frontend_swiftui", manifest.get("ui_frontend") == "swiftui", manifest.get("ui_frontend"))
check("manifest_bundled_python312", runtime.get("strategy") == "bundled_python312", runtime.get("strategy"))
check("manifest_python_bundled", runtime.get("bundled") is True, runtime.get("bundled"))
check("bundled_python_exists", runtime_python.exists() and os.access(runtime_python, os.X_OK), str(runtime_python))

absolute_symlinks = []
if runtime_dir.exists():
    for candidate in runtime_dir.rglob("*"):
        if candidate.is_symlink():
            target = os.readlink(candidate)
            if os.path.isabs(target):
                absolute_symlinks.append(f"{candidate}: {target}")
check("bundled_python_has_no_absolute_symlinks", not absolute_symlinks, absolute_symlinks[:20])

bundled = manifest.get("bundled", {})
license_inventory_payload = load_json(license_inventory)
sample1_manifest_payload = load_json(sample1_manifest)
check("manifest_includes_sample1", "sample1" in bundled, bundled.get("sample1"))
for manifest_field in (
    "app_version",
    "build_id",
    "dependency_set_id",
    "wheel_sha256",
    "constraints_sha256",
    "normalizer_sha256",
    "dcm2niix_sha256",
    "dcm2niix_version",
    "dcm2niix_source",
    "sample1_manifest_sha256",
    "update_manifest_url",
    "update_allowed_hosts",
    "third_party_licenses",
  ):
    check(f"manifest_has_{manifest_field}", manifest_field in manifest, manifest.get(manifest_field))
actual_app_version = manifest.get("app_version") or manifest.get("version")
if expected_app_version:
    check(
        "manifest_app_version_matches_expected",
        actual_app_version == expected_app_version,
        {"expected": expected_app_version, "actual": actual_app_version},
    )
check("bundled_dcm2niix_exists", bundled_dcm2niix.exists() and os.access(bundled_dcm2niix, os.X_OK), str(bundled_dcm2niix))
check("manifest_license_apache_2_0", manifest.get("license", {}).get("expression") == "Apache-2.0", manifest.get("license"))
check("wrapper_license_exists", wrapper_license.exists(), str(wrapper_license))
check("wrapper_notice_exists", wrapper_notice.exists(), str(wrapper_notice))
check("totalsegmentator_license_exists", totalsegmentator_license.exists(), str(totalsegmentator_license))
check("dentalsegmentator_notice_exists", dentalsegmentator_notice.exists(), str(dentalsegmentator_notice))
check("toothseg_notice_exists", toothseg_notice.exists(), str(toothseg_notice))
check("dcm2niix_license_exists", dcm2niix_license.exists(), str(dcm2niix_license))
check("license_inventory_exists", license_inventory.exists(), str(license_inventory))
check(
    "license_inventory_unresolved_zero",
    license_inventory_payload.get("unresolved_count") == 0,
    license_inventory_payload.get("unresolved"),
)
license_surface_text = "\n".join(
    path.read_text(encoding="utf-8")
    for path in (
        wrapper_license,
        wrapper_notice,
        dentalsegmentator_notice,
        toothseg_notice,
        manifest_path,
        license_inventory,
    )
    if path.is_file()
)
check(
    "license_surfaces_no_old_first_party_markers",
    "LicenseRef-Proprietary" not in license_surface_text
    and "WrapperMac-Proprietary-License" not in license_surface_text,
)
check("sample1_input_exists", sample1_input.exists(), str(sample1_input))
check("sample1_surface_preview_exists", sample1_viewer.exists(), str(sample1_viewer))
check("sample1_manifest_exists", sample1_manifest.exists(), str(sample1_manifest))
check("sample1_notices_exists", sample1_notices.exists(), str(sample1_notices))
check(
    "sample1_manifest_non_clinical",
    sample1_manifest_payload.get("clinical_use") is False,
    sample1_manifest_payload.get("clinical_use"),
)

installed_bundle = state.get("installed_bundle", {})
current_bundle = {
    "schema": "totalsegmentator_wrapper_mac.installed_bundle.v1",
    "app_version": manifest.get("app_version") or manifest.get("version"),
    "build_id": manifest.get("build_id"),
    "dependency_set_id": manifest.get("dependency_set_id"),
    "wheel_sha256": manifest.get("wheel_sha256"),
    "constraints_sha256": manifest.get("constraints_sha256"),
    "normalizer_sha256": manifest.get("normalizer_sha256"),
    "dcm2niix_sha256": manifest.get("dcm2niix_sha256"),
    "sample1_manifest_sha256": manifest.get("sample1_manifest_sha256"),
    "update_manifest_url": manifest.get("update_manifest_url"),
}
check("setup_state_installed_bundle_current", installed_bundle == current_bundle, installed_bundle)

evidence = {
    "schema": "totalsegmentator_wrapper_mac.test_account_install_evidence.v1",
    "passed": all(item["passed"] for item in checks),
    "home": str(home),
    "app_path": str(app_path),
    "support_dir": str(support_dir),
    "state_json": str(state_json),
    "manifest_path": str(manifest_path),
    "shared_copy_path": str(shared_evidence_json),
    "expected_app_version": expected_app_version or None,
    "checks": checks,
}
evidence_json.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(evidence, indent=2, ensure_ascii=False))
raise SystemExit(0 if evidence["passed"] else 1)
PY
PYTHON_STATUS=$?
set -e

if [[ -f "${EVIDENCE_JSON}" ]]; then
  mkdir -p "${SHARED_EVIDENCE_DIR}" || true
  if [[ -d "${SHARED_EVIDENCE_DIR}" ]]; then
    cp "${EVIDENCE_JSON}" "${SHARED_EVIDENCE_JSON}" || true
  fi
fi

echo "テスト用アカウントのinstall evidenceを書き出しました:"
echo "${EVIDENCE_JSON}"
if [[ -f "${SHARED_EVIDENCE_JSON}" ]]; then
  echo "共有受け渡し用コピーを書き出しました:"
  echo "${SHARED_EVIDENCE_JSON}"
else
  echo "共有受け渡し用コピーは書き出されませんでした:"
  echo "${SHARED_EVIDENCE_JSON}"
fi
exit "${PYTHON_STATUS}"
