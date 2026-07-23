#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DMG_PATH="${1:-${ROOT}/dist/TotalSegmentator Wrapper for Mac-0.2.0-20260722-gdcm-toothseg-arm64.dmg}"
EXPECTED_APP_VERSION="${TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_APP_VERSION:-0.2.0}"

if [[ ! -f "${DMG_PATH}" ]]; then
  echo "DMG not found: ${DMG_PATH}" >&2
  exit 2
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

echo "Zero-env DMG install verification passed."
