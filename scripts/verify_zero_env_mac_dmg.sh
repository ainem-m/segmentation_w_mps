#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
PROJECT_VERSION="$("${PYTHON_BIN}" -c 'import pathlib, sys, tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["project"]["version"])' "${ROOT}/pyproject.toml")"
EXPECTED_APP_VERSION="${TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_APP_VERSION:-${PROJECT_VERSION}}"
if [[ "${EXPECTED_APP_VERSION}" != "${PROJECT_VERSION}" ]]; then
  echo "Expected app version ${EXPECTED_APP_VERSION} does not match pyproject version ${PROJECT_VERSION}." >&2
  exit 2
fi
DMG_PATH="${1:-${ROOT}/dist/TotalSegmentator Wrapper for Mac-${PROJECT_VERSION}-release-arm64.dmg}"
NOTARY_RECEIPT="${TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_RECEIPT:-${ROOT}/dist/notary/notary-release-receipt.json}"
DEVELOPMENT_PREFLIGHT="${TOTALSEGMENTATOR_WRAPPER_MAC_ZERO_ENV_DEVELOPMENT_PREFLIGHT:-0}"

if [[ "${DEVELOPMENT_PREFLIGHT}" != "0" && "${DEVELOPMENT_PREFLIGHT}" != "1" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_ZERO_ENV_DEVELOPMENT_PREFLIGHT must be 0 or 1." >&2
  exit 2
fi
if [[ ! -f "${DMG_PATH}" || -L "${DMG_PATH}" ]]; then
  echo "DMG not found: ${DMG_PATH}" >&2
  exit 2
fi

EXPECTED_DMG_SHA256=""
EXPECTED_SOURCE_COMMIT=""
if [[ "${DEVELOPMENT_PREFLIGHT}" == "1" ]]; then
  echo "Running development-only zero-env preflight without final notary receipt binding."
else
  if [[ ! -f "${NOTARY_RECEIPT}" || -L "${NOTARY_RECEIPT}" ]]; then
    echo "Final zero-env gate requires a regular non-symlink notary receipt: ${NOTARY_RECEIPT}" >&2
    exit 2
  fi
  RECEIPT_FIELDS="$("${PYTHON_BIN}" - "${NOTARY_RECEIPT}" "${PROJECT_VERSION}" <<'PY'
import json
import re
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
if payload.get("schema") != "totalsegmentator_wrapper_mac.notary_release_receipt.v1":
    raise SystemExit("notary receipt schema mismatch")
if payload.get("version") != sys.argv[2]:
    raise SystemExit("notary receipt version mismatch")
digest = payload.get("final_dmg_sha256")
commit = payload.get("source_commit")
if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
    raise SystemExit("notary receipt final_dmg_sha256 is invalid")
if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
    raise SystemExit("notary receipt source_commit is invalid")
print(digest + " " + commit)
PY
)"
  EXPECTED_DMG_SHA256="${RECEIPT_FIELDS%% *}"
  EXPECTED_SOURCE_COMMIT="${RECEIPT_FIELDS#* }"
  ACTUAL_DMG_SHA256="$("${PYTHON_BIN}" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "${DMG_PATH}")"
  if [[ "${ACTUAL_DMG_SHA256}" != "${EXPECTED_DMG_SHA256}" ]]; then
    echo "DMG SHA-256 does not match the notary receipt: expected ${EXPECTED_DMG_SHA256}, found ${ACTUAL_DMG_SHA256}." >&2
    exit 1
  fi
  "${PYTHON_BIN}" "${ROOT}/scripts/verify_license_distribution.py" \
    --source "${ROOT}" \
    --dmg "${DMG_PATH}" \
    --expected-version "${PROJECT_VERSION}" \
    --expected-source-commit "${EXPECTED_SOURCE_COMMIT}" >/dev/null
fi

hdiutil verify "${DMG_PATH}" >/dev/null

MOUNT_ROOT="$(mktemp -d /tmp/totalsegmentator-wrapper-mac-dmg-mount.XXXXXX)"
ATTACH_OUTPUT="$(hdiutil attach "${DMG_PATH}" -nobrowse -readonly -mountpoint "${MOUNT_ROOT}")"
cleanup() {
  hdiutil detach "${MOUNT_ROOT}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ ! -d "${MOUNT_ROOT}/TotalSegmentator Wrapper for Mac.app" ]]; then
  echo "Mounted DMG does not contain TotalSegmentator Wrapper for Mac.app" >&2
  echo "${ATTACH_OUTPUT}" >&2
  exit 1
fi
if [[ ! -f "${MOUNT_ROOT}/README.txt" ]]; then
  echo "Mounted DMG does not contain README.txt" >&2
  exit 1
fi
if [[ ! -f "${MOUNT_ROOT}/TEST_ACCOUNT_INSTALL.txt" ]]; then
  echo "Mounted DMG does not contain TEST_ACCOUNT_INSTALL.txt" >&2
  exit 1
fi
if [[ ! -x "${MOUNT_ROOT}/Verify Test Account Install.command" ]]; then
  echo "Mounted DMG does not contain executable Verify Test Account Install.command" >&2
  exit 1
fi
if ! grep -q "TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE" "${MOUNT_ROOT}/TEST_ACCOUNT_INSTALL.txt"; then
  echo "TEST_ACCOUNT_INSTALL.txt does not mention the final evidence import gate" >&2
  exit 1
fi

TEST_HOME="$(mktemp -d /tmp/totalsegmentator-wrapper-mac-zero-env-dmg.XXXXXX)"
mkdir -p "${TEST_HOME}/Applications"
ditto "${MOUNT_ROOT}/TotalSegmentator Wrapper for Mac.app" "${TEST_HOME}/Applications/TotalSegmentator Wrapper for Mac.app"

APP_PATH="${TEST_HOME}/Applications/TotalSegmentator Wrapper for Mac.app"
APP_EXE="${APP_PATH}/Contents/MacOS/TotalSegmentatorWrapperForMac"
APP_INFO_PLIST="${APP_PATH}/Contents/Info.plist"
APP_SETUP_MANIFEST="${APP_PATH}/Contents/Resources/setup_manifest.json"

if [[ ! -f "${APP_INFO_PLIST}" || ! -f "${APP_SETUP_MANIFEST}" ]]; then
  echo "Copied app is missing Info.plist or setup_manifest.json" >&2
  exit 1
fi
APP_MINIMUM_MACOS_VERSION="$("${PYTHON_BIN}" -c 'import plistlib, sys; print(plistlib.load(open(sys.argv[1], "rb")).get("LSMinimumSystemVersion") or "")' "${APP_INFO_PLIST}")"
MANIFEST_MINIMUM_MACOS_VERSION="$("${PYTHON_BIN}" -c 'import json, sys; print(json.load(open(sys.argv[1])).get("minimum_macos_version") or "")' "${APP_SETUP_MANIFEST}")"
if [[ "${APP_MINIMUM_MACOS_VERSION}" != "14.0" || "${MANIFEST_MINIMUM_MACOS_VERSION}" != "14.0" ]]; then
  echo "Copied app does not require macOS 14.0: Info.plist=${APP_MINIMUM_MACOS_VERSION:-missing}, setup_manifest=${MANIFEST_MINIMUM_MACOS_VERSION:-missing}" >&2
  exit 1
fi

if command -v codesign >/dev/null 2>&1; then
  codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
fi

echo "Using clean HOME: ${TEST_HOME}"
env -i \
  HOME="${TEST_HOME}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_HEADLESS=1 \
  "${APP_EXE}"

STATE_JSON="${TEST_HOME}/Library/Application Support/TotalSegmentatorWrapperMac/setup_state.json"
if [[ ! -f "${STATE_JSON}" ]]; then
  echo "setup_state.json was not created" >&2
  exit 1
fi

cat "${STATE_JSON}"

if ! grep -q '"status": "success"' "${STATE_JSON}"; then
  echo "Setup did not finish with status=success" >&2
  exit 1
fi
if ! grep -q '"actual_device": "mps"' "${STATE_JSON}"; then
  echo "MPS doctor did not record actual_device=mps" >&2
  exit 1
fi
if ! grep -q '"normalizer_source": "app_bundle"' "${STATE_JSON}"; then
  echo "Bundled DICOM normalizer was not used" >&2
  exit 1
fi
if [[ -e "${TEST_HOME}/Library/Caches/pip" ]]; then
  echo "pip cache escaped App Support: ${TEST_HOME}/Library/Caches/pip" >&2
  exit 1
fi
if [[ ! -d "${TEST_HOME}/Library/Application Support/TotalSegmentatorWrapperMac/cache/pip" ]]; then
  echo "pip cache was not kept under App Support" >&2
  exit 1
fi
if [[ ! -d "${TEST_HOME}/Library/Application Support/TotalSegmentatorWrapperMac/cache/pycache" ]]; then
  echo "Python bytecode cache was not kept under App Support" >&2
  exit 1
fi

HOME="${TEST_HOME}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_SHARED_EVIDENCE_DIR="${TEST_HOME}/SharedEvidence" \
  TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH="${DMG_PATH}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_APP_VERSION="${EXPECTED_APP_VERSION}" \
  "${ROOT}/scripts/collect_test_account_install_evidence.sh" "${APP_PATH}" >/dev/null

if [[ ! -f "${TEST_HOME}/SharedEvidence/test_account_install_evidence.json" ]]; then
  echo "Shared evidence copy was not created" >&2
  exit 1
fi

if command -v codesign >/dev/null 2>&1; then
  codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
fi

if [[ "${DEVELOPMENT_PREFLIGHT}" == "1" ]]; then
  echo "Development-only zero-env DMG preflight passed; this is not final release evidence."
else
  echo "Final notary-receipt-bound zero-env DMG install verification passed."
  echo "Import the evidence with TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_RECEIPT=${NOTARY_RECEIPT} and TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_DMG_SHA256=${EXPECTED_DMG_SHA256}."
fi
