#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${ROOT}/dist"
APP_NAME="TotalSegmentator Wrapper for Mac"
APP_PATH="${DIST_DIR}/${APP_NAME}.app"
APP_VERSION="${TOTALSEGMENTATOR_WRAPPER_MAC_APP_VERSION:-0.3.0}"
DMG_VERSION_TAG="${TOTALSEGMENTATOR_WRAPPER_MAC_DMG_VERSION_TAG:-${APP_VERSION}-20260728-oss1}"
DMG_PATH="${TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH:-${DIST_DIR}/${APP_NAME}-${DMG_VERSION_TAG}-arm64.dmg}"
NOTARY_DIR="${DIST_DIR}/notary"
NOTARY_PROFILE="${TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_PROFILE:-totalsegmentator-wrapper-mac-notary}"
NOTARY_TIMEOUT="${TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_TIMEOUT:-60m}"
CODESIGN_IDENTITY="${TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY:-}"
BUNDLE_IDENTIFIER="${TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER:-}"
PYTHON_BIN="${PYTHON_BIN:-${ROOT}/.venv/bin/python}"
XCODE_DEVELOPER_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_XCODE_DEVELOPER_DIR:-}"

if [[ -z "${CODESIGN_IDENTITY}" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY is required." >&2
  exit 2
fi
if [[ -z "${BUNDLE_IDENTIFIER}" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER is required." >&2
  exit 2
fi
if [[ -z "${XCODE_DEVELOPER_DIR}" && -d "/Applications/Xcode.app/Contents/Developer" ]]; then
  XCODE_DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
fi
if [[ -n "${XCODE_DEVELOPER_DIR}" ]]; then
  export DEVELOPER_DIR="${XCODE_DEVELOPER_DIR}"
fi
if ! xcodebuild -version >/dev/null 2>&1; then
  echo "Full Xcode must be selected before notarized builds can run." >&2
  echo "Set TOTALSEGMENTATOR_WRAPPER_MAC_XCODE_DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer or select full Xcode for this shell." >&2
  exit 2
fi
if ! security find-identity -v -p codesigning | grep -F "${CODESIGN_IDENTITY}" >/dev/null 2>&1; then
  echo "Developer ID codesigning identity not found in keychain: ${CODESIGN_IDENTITY}" >&2
  exit 2
fi
if ! xcrun notarytool history \
  --keychain-profile "${NOTARY_PROFILE}" \
  --output-format json >/dev/null 2>&1; then
  echo "Notary keychain profile is missing or unusable: ${NOTARY_PROFILE}" >&2
  echo "Store valid notarytool credentials before building the notarized release." >&2
  exit 2
fi

mkdir -p "${NOTARY_DIR}"
rm -f "${NOTARY_DIR}/notary_submission.json" "${NOTARY_DIR}/notary_log.json"

env \
  TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE=developer-id \
  TOTALSEGMENTATOR_WRAPPER_MAC_NOTARIZED=1 \
  TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_PROFILE="${NOTARY_PROFILE}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY="${CODESIGN_IDENTITY}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER="${BUNDLE_IDENTIFIER}" \
  "${ROOT}/scripts/build_mac_app.sh" >/dev/null

codesign --verify --deep --strict --verbose=2 "${APP_PATH}"

env TOTALSEGMENTATOR_WRAPPER_MAC_SKIP_APP_BUILD=1 "${ROOT}/scripts/build_mac_dmg.sh" >/dev/null

codesign --force --timestamp --sign "${CODESIGN_IDENTITY}" "${DMG_PATH}" >/dev/null
codesign --verify --verbose=2 "${DMG_PATH}"

xcrun notarytool submit \
  "${DMG_PATH}" \
  --keychain-profile "${NOTARY_PROFILE}" \
  --wait \
  --timeout "${NOTARY_TIMEOUT}" \
  --output-format json > "${NOTARY_DIR}/notary_submission.json"

SUBMISSION_ID="$("${PYTHON_BIN}" -c 'import json, sys; print(json.load(open(sys.argv[1])).get("id", ""))' "${NOTARY_DIR}/notary_submission.json")"
SUBMISSION_STATUS="$("${PYTHON_BIN}" -c 'import json, sys; print(json.load(open(sys.argv[1])).get("status", ""))' "${NOTARY_DIR}/notary_submission.json")"

if [[ "${SUBMISSION_STATUS}" != "Accepted" ]]; then
  if [[ -n "${SUBMISSION_ID}" ]]; then
    xcrun notarytool log \
      "${SUBMISSION_ID}" \
      "${NOTARY_DIR}/notary_log.json" \
      --keychain-profile "${NOTARY_PROFILE}" || true
  fi
  echo "Notarization failed with status: ${SUBMISSION_STATUS}" >&2
  exit 1
fi

xcrun stapler staple "${DMG_PATH}" >/dev/null
xcrun stapler validate "${DMG_PATH}" >/dev/null

spctl --assess --type open --context context:primary-signature --verbose=4 "${DMG_PATH}"

MOUNT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/totalsegmentator-wrapper-mac-notary-mount.XXXXXX")"
cleanup() {
  hdiutil detach "${MOUNT_ROOT}" >/dev/null 2>&1 || true
  rmdir "${MOUNT_ROOT}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

hdiutil attach "${DMG_PATH}" -nobrowse -readonly -mountpoint "${MOUNT_ROOT}" >/dev/null
spctl --assess --type execute --verbose=4 "${MOUNT_ROOT}/${APP_NAME}.app"

echo "${DMG_PATH}"
