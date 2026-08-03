#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_PATH="${1:-${ROOT}/dist/TotalSegmentator Wrapper for Mac.app}"
APP_EXE="${APP_PATH}/Contents/MacOS/TotalSegmentatorWrapperForMac"

if [[ ! -x "${APP_EXE}" ]]; then
  echo "App executable not found: ${APP_EXE}" >&2
  exit 2
fi

if command -v codesign >/dev/null 2>&1; then
  codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
fi

TEST_HOME="$(mktemp -d /tmp/totalsegmentator-wrapper-mac-zero-env-verify.XXXXXX)"
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

if ! /usr/bin/python3 -c 'import json, sys; raise SystemExit(0 if json.load(open(sys.argv[1], encoding="utf-8")).get("status") == "success" else 1)' "${STATE_JSON}"; then
  echo "Setup did not finish with status=success" >&2
  exit 1
fi
if ! /usr/bin/python3 -c 'import json, sys; raise SystemExit(0 if json.load(open(sys.argv[1], encoding="utf-8")).get("doctor", {}).get("actual_device") == "mps" else 1)' "${STATE_JSON}"; then
  echo "MPS doctor did not record actual_device=mps" >&2
  exit 1
fi
if ! /usr/bin/python3 -c 'import json, sys; raise SystemExit(0 if json.load(open(sys.argv[1], encoding="utf-8")).get("dicom_normalizer", {}).get("normalizer_source") == "app_bundle" else 1)' "${STATE_JSON}"; then
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
  "${ROOT}/scripts/collect_test_account_install_evidence.sh" "${APP_PATH}" >/dev/null

if [[ ! -f "${TEST_HOME}/SharedEvidence/test_account_install_evidence.json" ]]; then
  echo "Shared evidence copy was not created" >&2
  exit 1
fi

if command -v codesign >/dev/null 2>&1; then
  codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
fi

echo "Zero-env app setup verification passed."
