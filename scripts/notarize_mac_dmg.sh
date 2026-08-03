#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR:-${ROOT}/dist}"
if [[ "${DIST_DIR}" != "/" ]]; then
  DIST_DIR="${DIST_DIR%/}"
fi
APP_NAME="TotalSegmentator Wrapper for Mac"
CANONICAL_BUNDLE_IDENTIFIER="jp.chino.totalsegmentator.wrapper.mac"
APP_PATH="${DIST_DIR}/${APP_NAME}.app"
APP_VERSION_OVERRIDE="${TOTALSEGMENTATOR_WRAPPER_MAC_APP_VERSION:-}"
APP_VERSION=""
NOTARY_DIR="${DIST_DIR}/notary"
NOTARY_PROFILE="${TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_PROFILE:-totalsegmentator-wrapper-mac-notary}"
NOTARY_TIMEOUT="${TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_TIMEOUT:-60m}"
CODESIGN_IDENTITY="${TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY:-}"
BUNDLE_IDENTIFIER="${TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER:-${CANONICAL_BUNDLE_IDENTIFIER}}"
TEAM_IDENTIFIER="${TOTALSEGMENTATOR_WRAPPER_MAC_TEAM_IDENTIFIER:-}"
UPDATE_MANIFEST_URL="${TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_MANIFEST_URL:-https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable-v2/update.json}"
UPDATE_ALLOWED_HOSTS="${TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_ALLOWED_HOSTS:-downloads.lacramy.com}"
PYTHON_BIN="${PYTHON_BIN:-${ROOT}/.venv/bin/python}"
XCODE_DEVELOPER_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_XCODE_DEVELOPER_DIR:-}"
EXPECTED_SOURCE_COMMIT="${TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_SOURCE_COMMIT:-}"

path_has_safe_write_mode() {
  local mode
  mode="$(stat -f %Lp "$1" 2>/dev/null || true)"
  [[ "${mode}" =~ ^[0-7]{3,4}$ ]] && (( (8#${mode} & 8#22) == 0 ))
}

validate_owned_notary_paths() {
  if [[ "${DIST_DIR}" != /* || "${DIST_DIR}" == "/" ]]; then
    echo "Notarization distribution directory must be a specific absolute path: ${DIST_DIR:-empty}." >&2
    exit 2
  fi
  if [[ -e "${DIST_DIR}" || -L "${DIST_DIR}" ]]; then
    if [[ ! -d "${DIST_DIR}" || -L "${DIST_DIR}" || ! -O "${DIST_DIR}" ]] \
      || ! path_has_safe_write_mode "${DIST_DIR}"; then
      echo "Notarization distribution directory must be owner-controlled and non-symlink: ${DIST_DIR}" >&2
      exit 2
    fi
  else
    local parent
    parent="$(dirname "${DIST_DIR}")"
    if [[ ! -d "${parent}" || -L "${parent}" || ! -O "${parent}" ]] \
      || ! path_has_safe_write_mode "${parent}"; then
      echo "Notarization distribution parent must be owner-controlled and non-symlink: ${parent}" >&2
      exit 2
    fi
    mkdir "${DIST_DIR}"
  fi
  if [[ "$(dirname "${NOTARY_DIR}")" != "${DIST_DIR}" \
    || "$(basename "${NOTARY_DIR}")" != "notary" ]]; then
    echo "Notary directory escaped the fixed distribution boundary: ${NOTARY_DIR}" >&2
    exit 2
  fi
  if [[ -e "${NOTARY_DIR}" || -L "${NOTARY_DIR}" ]]; then
    if [[ ! -d "${NOTARY_DIR}" || -L "${NOTARY_DIR}" || ! -O "${NOTARY_DIR}" ]] \
      || ! path_has_safe_write_mode "${NOTARY_DIR}"; then
      echo "Notary directory must be owner-controlled and non-symlink: ${NOTARY_DIR}" >&2
      exit 2
    fi
  else
    mkdir "${NOTARY_DIR}"
  fi
  if ! path_has_safe_write_mode "${NOTARY_DIR}"; then
    echo "Notary directory must not be group- or world-writable: ${NOTARY_DIR}" >&2
    exit 2
  fi
}

require_clean_source_identity() {
  local current_commit
  local status
  current_commit="$(git -C "${ROOT}" rev-parse HEAD 2>/dev/null || true)"
  status="$(git -C "${ROOT}" status --porcelain=v1 --untracked-files=all 2>/dev/null || true)"
  if [[ ! "${current_commit}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Notarization requires a Git source commit." >&2
    exit 2
  fi
  if [[ -z "${EXPECTED_SOURCE_COMMIT}" ]]; then
    EXPECTED_SOURCE_COMMIT="${current_commit}"
  fi
  if [[ ! "${EXPECTED_SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ \
    || "${current_commit}" != "${EXPECTED_SOURCE_COMMIT}" ]]; then
    echo "Notarization source commit changed: expected ${EXPECTED_SOURCE_COMMIT:-missing}, found ${current_commit}." >&2
    exit 2
  fi
  if [[ -n "${status}" ]]; then
    echo "Notarization requires a clean tracked and untracked source worktree." >&2
    exit 2
  fi
}

sha256_file() {
  "${PYTHON_BIN}" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$1"
}

validate_existing_canonical_pair() {
  local dmg_present=0
  local receipt_present=0
  [[ -e "${DMG_PATH}" || -L "${DMG_PATH}" ]] && dmg_present=1
  [[ -e "${RECEIPT_PATH}" || -L "${RECEIPT_PATH}" ]] && receipt_present=1
  if [[ "${dmg_present}" != "${receipt_present}" ]]; then
    echo "Canonical DMG and notary receipt must both exist or both be absent." >&2
    exit 2
  fi
  if [[ "${dmg_present}" != "1" ]]; then
    return 0
  fi

  local actual_sha256
  local actual_size
  local validation_error
  actual_sha256="$(sha256_file "${DMG_PATH}")"
  actual_size="$(stat -f %z "${DMG_PATH}")"
  if ! validation_error="$("${PYTHON_BIN}" - \
    "${RECEIPT_PATH}" \
    "${APP_VERSION}" \
    "${EXPECTED_SOURCE_COMMIT}" \
    "$(basename "${DMG_PATH}")" \
    "${actual_sha256}" \
    "${actual_size}" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

receipt_path, version, source_commit, dmg_filename, actual_sha256, actual_size = sys.argv[1:]
try:
    receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    print(f"canonical notary receipt is unreadable: {exc}")
    raise SystemExit(1)
if not isinstance(receipt, dict):
    print("canonical notary receipt must be a JSON object")
    raise SystemExit(1)
expected = {
    "schema": "totalsegmentator_wrapper_mac.notary_release_receipt.v1",
    "version": version,
    "source_commit": source_commit,
    "dmg_filename": dmg_filename,
}
for key, value in expected.items():
    if receipt.get(key) != value:
        print(f"canonical notary receipt {key} mismatch")
        raise SystemExit(1)
recorded_sha256 = receipt.get("final_dmg_sha256")
if not isinstance(recorded_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", recorded_sha256):
    print("canonical notary receipt final_dmg_sha256 is invalid")
    raise SystemExit(1)
if recorded_sha256 != actual_sha256:
    print("canonical DMG SHA-256 mismatch")
    raise SystemExit(1)
if receipt.get("final_dmg_size_bytes") != int(actual_size):
    print("canonical DMG size mismatch")
    raise SystemExit(1)
PY
)"; then
    echo "Existing ${validation_error:-canonical DMG/notary receipt binding is invalid}." >&2
    exit 2
  fi
}

atomic_write_json() {
  local target="$1"
  shift
  "${PYTHON_BIN}" - "${target}" "$@" <<'PY'
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

target = Path(sys.argv[1])
payload = json.loads(sys.argv[2])
temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    offset = 0
    while offset < len(data):
        offset += os.write(descriptor, data[offset:])
    os.fsync(descriptor)
finally:
    os.close(descriptor)
os.replace(temporary, target)
directory = os.open(target.parent, os.O_RDONLY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
}

rollback_publication() {
  if [[ "${PUBLICATION_STARTED:-0}" != "1" || "${NOTARY_COMPLETED:-0}" == "1" ]]; then
    return 0
  fi
  if [[ -f "${DMG_PATH:-}" && ! -L "${DMG_PATH:-}" ]]; then
    if [[ -n "${PENDING_DMG_PATH:-}" && ! -e "${PENDING_DMG_PATH}" ]]; then
      mv -f "${DMG_PATH}" "${PENDING_DMG_PATH}" || true
    fi
  fi
  if [[ "${PREVIOUS_DMG_PRESENT:-0}" == "1" && -f "${PREVIOUS_DMG_BACKUP:-}" ]]; then
    local restore_dmg="${DIST_DIR}/.restore-dmg-${NOTARY_RUN_ID}.tmp"
    cp -p "${PREVIOUS_DMG_BACKUP}" "${restore_dmg}" \
      && mv -f "${restore_dmg}" "${DMG_PATH}" || true
  fi
  if [[ "${PREVIOUS_RECEIPT_PRESENT:-0}" == "1" && -f "${PREVIOUS_RECEIPT_BACKUP:-}" ]]; then
    local restore_receipt="${NOTARY_DIR}/.restore-receipt-${NOTARY_RUN_ID}.tmp"
    cp -p "${PREVIOUS_RECEIPT_BACKUP}" "${restore_receipt}" \
      && mv -f "${restore_receipt}" "${RECEIPT_PATH}" || true
  elif [[ -f "${RECEIPT_PATH:-}" && ! -L "${RECEIPT_PATH:-}" ]]; then
    mv -f "${RECEIPT_PATH}" "${RECEIPT_STAGED}.failed-publish" || true
  fi
}

write_failure_state() {
  [[ -d "${NOTARY_RUN_DIR:-}" && ! -L "${NOTARY_RUN_DIR:-}" ]] || return 0
  local pending_exists="false"
  [[ -f "${PENDING_DMG_PATH:-}" && ! -L "${PENDING_DMG_PATH:-}" ]] && pending_exists="true"
  local FAILURE_STATE_JSON
  FAILURE_STATE_JSON="$("${PYTHON_BIN}" - \
    "${CURRENT_STAGE:-unknown}" \
    "$(basename "${PENDING_DMG_PATH:-pending.dmg}")" \
    "${pending_exists}" <<'PY'
import json
import sys
from datetime import datetime, timezone

payload = {
    "schema": "totalsegmentator_wrapper_mac.notary_failure_state.v1",
    "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "failed_stage": sys.argv[1],
    "pending_dmg_basename": sys.argv[2],
    "pending_dmg_exists": sys.argv[3] == "true",
}
print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
PY
)" || return 0
  atomic_write_json "${NOTARY_RUN_DIR}/notary-failure-state.json" "${FAILURE_STATE_JSON}" || true
}

notarization_exit() {
  local status=$?
  set +e
  if [[ -n "${MOUNT_ROOT:-}" ]]; then
    hdiutil detach "${MOUNT_ROOT}" >/dev/null 2>&1 || true
    rmdir "${MOUNT_ROOT}" >/dev/null 2>&1 || true
  fi
  if [[ "${status}" -ne 0 ]]; then
    rollback_publication
    write_failure_state
  fi
  exit "${status}"
}

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
validate_owned_notary_paths
require_clean_source_identity
PROJECT_VERSION="$("${PYTHON_BIN}" -c 'import pathlib, sys, tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["project"]["version"])' "${ROOT}/pyproject.toml")"
if [[ -n "${APP_VERSION_OVERRIDE}" && "${APP_VERSION_OVERRIDE}" != "${PROJECT_VERSION}" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_APP_VERSION=${APP_VERSION_OVERRIDE} does not match pyproject version ${PROJECT_VERSION}." >&2
  exit 2
fi
APP_VERSION="${PROJECT_VERSION}"
DMG_VERSION_TAG="${TOTALSEGMENTATOR_WRAPPER_MAC_DMG_VERSION_TAG:-${APP_VERSION}-release}"
DMG_PATH="${TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH:-${DIST_DIR}/${APP_NAME}-${DMG_VERSION_TAG}-arm64.dmg}"
if [[ "${DMG_PATH}" != /* \
  || "$(dirname "${DMG_PATH}")" != "${DIST_DIR}" \
  || "$(basename "${DMG_PATH}")" != *.dmg ]]; then
  echo "Canonical notarized DMG must be directly inside the validated distribution directory: ${DMG_PATH}" >&2
  exit 2
fi
if [[ -e "${DMG_PATH}" || -L "${DMG_PATH}" ]]; then
  if [[ ! -f "${DMG_PATH}" || -L "${DMG_PATH}" || ! -O "${DMG_PATH}" ]] \
    || ! path_has_safe_write_mode "${DMG_PATH}"; then
    echo "Refusing to replace an unsafe canonical notarized DMG: ${DMG_PATH}" >&2
    exit 2
  fi
fi
NOTARY_RUN_ID="$("${PYTHON_BIN}" -c 'import secrets; print(secrets.token_hex(8))')"
NOTARY_RUN_DIR="${NOTARY_DIR}/run-${NOTARY_RUN_ID}"
PENDING_DMG_PATH="${DIST_DIR}/.$(basename "${DMG_PATH}" .dmg).pending-notarization-${NOTARY_RUN_ID}.dmg"
SUBMISSION_JSON="${NOTARY_RUN_DIR}/notary_submission.json"
NOTARY_LOG_JSON="${NOTARY_RUN_DIR}/notary_log.json"
RECEIPT_STAGED="${NOTARY_RUN_DIR}/notary-release-receipt.json"
RECEIPT_PATH="${NOTARY_DIR}/notary-release-receipt.json"
PREVIOUS_DMG_BACKUP="${NOTARY_RUN_DIR}/previous-canonical.dmg"
PREVIOUS_RECEIPT_BACKUP="${NOTARY_RUN_DIR}/previous-notary-release-receipt.json"
PREVIOUS_DMG_PRESENT=0
PREVIOUS_RECEIPT_PRESENT=0
PUBLICATION_STARTED=0
NOTARY_COMPLETED=0
CURRENT_STAGE="preflight"
MOUNT_ROOT=""
if [[ -e "${PENDING_DMG_PATH}" || -L "${PENDING_DMG_PATH}" ]]; then
  echo "Refusing to replace an existing pending notarization DMG: ${PENDING_DMG_PATH}" >&2
  exit 2
fi
if [[ -e "${RECEIPT_PATH}" || -L "${RECEIPT_PATH}" ]]; then
  if [[ ! -f "${RECEIPT_PATH}" || -L "${RECEIPT_PATH}" || ! -O "${RECEIPT_PATH}" ]] \
    || ! path_has_safe_write_mode "${RECEIPT_PATH}"; then
    echo "Refusing to replace an unsafe notarization receipt: ${RECEIPT_PATH}" >&2
    exit 2
  fi
fi
validate_existing_canonical_pair
mkdir "${NOTARY_RUN_DIR}"
if [[ ! -d "${NOTARY_RUN_DIR}" || -L "${NOTARY_RUN_DIR}" || ! -O "${NOTARY_RUN_DIR}" ]] \
  || ! path_has_safe_write_mode "${NOTARY_RUN_DIR}"; then
  echo "Notary run directory must be owner-controlled with a safe write mode: ${NOTARY_RUN_DIR}" >&2
  exit 2
fi
trap notarization_exit EXIT

if [[ -z "${CODESIGN_IDENTITY}" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY is required." >&2
  exit 2
fi
if [[ "${BUNDLE_IDENTIFIER}" != "${CANONICAL_BUNDLE_IDENTIFIER}" ]]; then
  echo "Notarized releases require the canonical bundle identifier ${CANONICAL_BUNDLE_IDENTIFIER}; got ${BUNDLE_IDENTIFIER:-empty}." >&2
  exit 2
fi
if [[ ! "${TEAM_IDENTIFIER}" =~ ^[A-Z0-9]{10}$ ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_TEAM_IDENTIFIER must be the 10-character Developer ID Team ID." >&2
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

CURRENT_STAGE="build-app"
env \
  TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE=developer-id \
  TOTALSEGMENTATOR_WRAPPER_MAC_NOTARIZED=1 \
  TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_PROFILE="${NOTARY_PROFILE}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY="${CODESIGN_IDENTITY}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER="${BUNDLE_IDENTIFIER}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_TEAM_IDENTIFIER="${TEAM_IDENTIFIER}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_MANIFEST_URL="${UPDATE_MANIFEST_URL}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_ALLOWED_HOSTS="${UPDATE_ALLOWED_HOSTS}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR="${DIST_DIR}" \
  "${ROOT}/scripts/build_mac_app.sh" >/dev/null

require_clean_source_identity
codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
"${PYTHON_BIN}" "${ROOT}/scripts/verify_license_distribution.py" \
  --source "${ROOT}" \
  --app "${APP_PATH}" \
  --expected-version "${APP_VERSION}" \
  --expected-source-commit "${EXPECTED_SOURCE_COMMIT}" >/dev/null
APP_MANIFEST_PATH="${APP_PATH}/Contents/Resources/setup_manifest.json"
APP_MANIFEST_SHA256="$(sha256_file "${APP_MANIFEST_PATH}")"

CURRENT_STAGE="build-pending-dmg"
env \
  TOTALSEGMENTATOR_WRAPPER_MAC_SKIP_APP_BUILD=1 \
  TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR="${DIST_DIR}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH="${PENDING_DMG_PATH}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_SOURCE_COMMIT="${EXPECTED_SOURCE_COMMIT}" \
  "${ROOT}/scripts/build_mac_dmg.sh" >/dev/null

require_clean_source_identity
"${PYTHON_BIN}" "${ROOT}/scripts/verify_license_distribution.py" \
  --source "${ROOT}" \
  --dmg "${PENDING_DMG_PATH}" \
  --expected-version "${APP_VERSION}" \
  --expected-source-commit "${EXPECTED_SOURCE_COMMIT}" >/dev/null
codesign --force --timestamp --sign "${CODESIGN_IDENTITY}" "${PENDING_DMG_PATH}" >/dev/null
codesign --verify --verbose=2 "${PENDING_DMG_PATH}"
SUBMITTED_DMG_SHA256="$(sha256_file "${PENDING_DMG_PATH}")"
SUBMITTED_DMG_SIZE="$(stat -f %z "${PENDING_DMG_PATH}")"

CURRENT_STAGE="notary-submit"
xcrun notarytool submit \
  "${PENDING_DMG_PATH}" \
  --keychain-profile "${NOTARY_PROFILE}" \
  --wait \
  --timeout "${NOTARY_TIMEOUT}" \
  --output-format json > "${SUBMISSION_JSON}"

SUBMISSION_ID="$("${PYTHON_BIN}" -c 'import json, sys; print(json.load(open(sys.argv[1])).get("id", ""))' "${SUBMISSION_JSON}")"
SUBMISSION_STATUS="$("${PYTHON_BIN}" -c 'import json, sys; print(json.load(open(sys.argv[1])).get("status", ""))' "${SUBMISSION_JSON}")"

if [[ "${SUBMISSION_STATUS}" != "Accepted" \
  || ! "${SUBMISSION_ID}" =~ ^[0-9A-Fa-f-]{36}$ ]]; then
  if [[ -n "${SUBMISSION_ID}" ]]; then
    xcrun notarytool log \
      "${SUBMISSION_ID}" \
      "${NOTARY_LOG_JSON}" \
      --keychain-profile "${NOTARY_PROFILE}" || true
  fi
  echo "Notarization failed with status/id: ${SUBMISSION_STATUS:-missing}/${SUBMISSION_ID:-missing}" >&2
  exit 1
fi

CURRENT_STAGE="staple-and-assess-dmg"
xcrun stapler staple "${PENDING_DMG_PATH}" >/dev/null
xcrun stapler validate "${PENDING_DMG_PATH}" >/dev/null

spctl --assess --type open --context context:primary-signature --verbose=4 "${PENDING_DMG_PATH}"

CURRENT_STAGE="mounted-app-assessment"
MOUNT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/totalsegmentator-wrapper-mac-notary-mount.XXXXXX")"
hdiutil attach "${PENDING_DMG_PATH}" -nobrowse -readonly -mountpoint "${MOUNT_ROOT}" >/dev/null
codesign --verify --deep --strict --verbose=2 "${MOUNT_ROOT}/${APP_NAME}.app"
spctl --assess --type execute --verbose=4 "${MOUNT_ROOT}/${APP_NAME}.app"
hdiutil detach "${MOUNT_ROOT}" >/dev/null
"${PYTHON_BIN}" "${ROOT}/scripts/verify_license_distribution.py" \
  --source "${ROOT}" \
  --dmg "${PENDING_DMG_PATH}" \
  --expected-version "${APP_VERSION}" \
  --expected-source-commit "${EXPECTED_SOURCE_COMMIT}" >/dev/null
require_clean_source_identity

CURRENT_STAGE="create-release-receipt"
FINAL_DMG_SHA256="$(sha256_file "${PENDING_DMG_PATH}")"
FINAL_DMG_SIZE="$(stat -f %z "${PENDING_DMG_PATH}")"
RECEIPT_JSON="$("${PYTHON_BIN}" - \
  "${APP_VERSION}" \
  "${EXPECTED_SOURCE_COMMIT}" \
  "${BUNDLE_IDENTIFIER}" \
  "${TEAM_IDENTIFIER}" \
  "${SUBMISSION_ID}" \
  "${SUBMISSION_STATUS}" \
  "${SUBMITTED_DMG_SHA256}" \
  "${SUBMITTED_DMG_SIZE}" \
  "${FINAL_DMG_SHA256}" \
  "${FINAL_DMG_SIZE}" \
  "${APP_MANIFEST_SHA256}" \
  "$(basename "${DMG_PATH}")" <<'PY'
import json
import sys
from datetime import datetime, timezone

keys = (
    "version",
    "source_commit",
    "bundle_identifier",
    "team_identifier",
    "submission_id",
    "submission_status",
    "submitted_dmg_sha256",
    "submitted_dmg_size_bytes",
    "final_dmg_sha256",
    "final_dmg_size_bytes",
    "app_manifest_sha256",
    "dmg_filename",
)
values = sys.argv[1:]
payload = dict(zip(keys, values, strict=True))
payload["schema"] = "totalsegmentator_wrapper_mac.notary_release_receipt.v1"
payload["created_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
payload["submitted_dmg_size_bytes"] = int(payload["submitted_dmg_size_bytes"])
payload["final_dmg_size_bytes"] = int(payload["final_dmg_size_bytes"])
print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
PY
)"
atomic_write_json "${RECEIPT_STAGED}" "${RECEIPT_JSON}"
if [[ ! -f "${RECEIPT_STAGED}" || -L "${RECEIPT_STAGED}" \
  || ! -O "${RECEIPT_STAGED}" ]] \
  || ! path_has_safe_write_mode "${RECEIPT_STAGED}"; then
  echo "Notary receipt staging file is unsafe: ${RECEIPT_STAGED}" >&2
  exit 2
fi
CURRENT_STAGE="prepare-publication-rollback"
if [[ -f "${DMG_PATH}" ]]; then
  cp -p "${DMG_PATH}" "${PREVIOUS_DMG_BACKUP}"
  PREVIOUS_DMG_PRESENT=1
fi
if [[ -f "${RECEIPT_PATH}" ]]; then
  cp -p "${RECEIPT_PATH}" "${PREVIOUS_RECEIPT_BACKUP}"
  PREVIOUS_RECEIPT_PRESENT=1
fi
RECEIPT_TEMP="${NOTARY_DIR}/.notary-release-receipt.${NOTARY_RUN_ID}.tmp"
if [[ -e "${RECEIPT_TEMP}" || -L "${RECEIPT_TEMP}" ]]; then
  echo "Refusing to replace an existing notarization receipt temporary file: ${RECEIPT_TEMP}" >&2
  exit 2
fi
cp "${RECEIPT_STAGED}" "${RECEIPT_TEMP}"
if [[ ! -f "${RECEIPT_TEMP}" || -L "${RECEIPT_TEMP}" \
  || ! -O "${RECEIPT_TEMP}" ]] \
  || ! path_has_safe_write_mode "${RECEIPT_TEMP}"; then
  echo "Notarization receipt temporary file is unsafe: ${RECEIPT_TEMP}" >&2
  exit 2
fi
PUBLICATION_STARTED=1
CURRENT_STAGE="publish-dmg"
mv -f "${PENDING_DMG_PATH}" "${DMG_PATH}"
if [[ "${TOTALSEGMENTATOR_WRAPPER_MAC_TEST_FAIL_AFTER_DMG_PUBLISH:-0}" == "1" ]]; then
  CURRENT_STAGE="test-injected-after-dmg-publish"
  echo "Injected notarization publication failure after DMG move." >&2
  false
fi
CURRENT_STAGE="publish-receipt"
mv -f "${RECEIPT_TEMP}" "${RECEIPT_PATH}"
CURRENT_STAGE="publication-complete"
NOTARY_COMPLETED=1

echo "${DMG_PATH}"
