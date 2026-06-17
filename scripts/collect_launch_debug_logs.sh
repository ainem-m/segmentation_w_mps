#!/usr/bin/env bash
set -euo pipefail

APP_NAME="TotalSegmentator Wrapper for Mac.app"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DEBUG_LOG_DIR:-/Users/Shared/TotalSegmentatorWrapperMac/launch_debug_${USER}_${STAMP}}"
SUPPORT_DIR="${HOME}/Library/Application Support/TotalSegmentatorWrapperMac"

mkdir -p "${OUT_DIR}"

{
  echo "TotalSegmentator Wrapper for Mac launch debug"
  echo "date: $(date)"
  echo "user: ${USER:-unknown}"
  echo "home: ${HOME}"
  echo "output: ${OUT_DIR}"
  echo "privacy_note: ログにはローカルパスや実行状況が含まれる場合があります。共有前に内容を確認してください。"
  echo
  echo "support_dir: ${SUPPORT_DIR}"
  if [[ -d "${SUPPORT_DIR}" ]]; then
    echo "support_dir_exists: yes"
  else
    echo "support_dir_exists: no"
  fi
} > "${OUT_DIR}/summary.txt"

if [[ -f "${SUPPORT_DIR}/setup_state.json" ]]; then
  cp "${SUPPORT_DIR}/setup_state.json" "${OUT_DIR}/setup_state.json"
fi
if [[ -d "${SUPPORT_DIR}/logs" ]]; then
  mkdir -p "${OUT_DIR}/logs"
  cp -R "${SUPPORT_DIR}/logs/." "${OUT_DIR}/logs/" || true
fi

{
  echo "Candidate app locations"
  for location in \
    "${HOME}/Applications/${APP_NAME}" \
    "/Applications/${APP_NAME}" \
    "/Volumes/TotalSegmentator Wrapper for Mac/${APP_NAME}" \
    "/Users/Shared/TotalSegmentatorWrapperForMac/${APP_NAME}"
  do
    if [[ -d "${location}" ]]; then
      echo "FOUND: ${location}"
    else
      echo "missing: ${location}"
    fi
  done
  echo
  echo "Directory listings"
  for directory in "${HOME}/Applications" "${HOME}/Desktop" "${HOME}/Downloads" "/Users/Shared/TotalSegmentatorWrapperForMac"; do
    echo "--- ${directory}"
    ls -la "${directory}" 2>&1 || true
  done
} > "${OUT_DIR}/paths.txt"

{
  for app in \
    "${HOME}/Applications/${APP_NAME}" \
    "/Applications/${APP_NAME}" \
    "/Volumes/TotalSegmentator Wrapper for Mac/${APP_NAME}" \
    "/Users/Shared/TotalSegmentatorWrapperForMac/${APP_NAME}"
  do
    [[ -d "${app}" ]] || continue
    echo "=== ${app}"
    echo "--- xattr"
    xattr -l "${app}" 2>&1 || true
    echo "--- codesign"
    codesign --verify --deep --strict --verbose=2 "${app}" 2>&1 || true
    echo "--- spctl"
    spctl --assess --type execute --verbose=4 "${app}" 2>&1 || true
    echo
  done
} > "${OUT_DIR}/app_assessment.txt"

if [[ -x /usr/bin/log ]]; then
  /usr/bin/log show --last 2h --style compact \
    --predicate 'process == "TotalSegmentatorWrapperForMac" OR eventMessage CONTAINS[c] "TotalSegmentatorWrapperForMac" OR eventMessage CONTAINS[c] "jp.chino.totalsegmentator.wrapper.mac" OR eventMessage CONTAINS[c] "TotalSegmentator Wrapper for Mac"' \
    > "${OUT_DIR}/system_log_totalsegmentator_wrapper.txt" 2>&1 || true
fi

echo "TotalSegmentator Wrapper for Macの起動ログを回収しました:"
echo "${OUT_DIR}"
echo "ログにはローカルパスや実行状況が含まれる場合があります。共有前に内容を確認してください。"
