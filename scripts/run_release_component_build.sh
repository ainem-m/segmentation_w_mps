#!/bin/bash
# Run one release wheel build in a sealed, receipt-verified environment.
#
# The offline Python build-toolchain lock protects Python backends.  This
# wrapper additionally keeps CMake/Ninja/PATH and compiler-selection variables
# from being inherited from Homebrew, a developer shell, or CI.  Apple Xcode
# remains an explicitly recorded external compiler boundary; it is rechecked
# against the receipt immediately before each component build.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK=""
METADATA=""
RECEIPT=""
PREPARED_PYTHON=""
COMPONENT=""
WHEELHOUSE=""
BOOTSTRAP_DECLARATION=""
SOURCE_IDENTITY=""
PRE_SIGN_WHEEL_RECEIPT=""
PRE_SIGN_WHEEL_DIRECTORY=""
PRE_SIGN_COMPONENT_RECEIPT=""
BOOTSTRAP_PRE_SIGN=0

fail() {
  echo "$*" >&2
  exit 2
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --lock)
      [[ "$#" -ge 2 ]] || fail "--lock requires a value"
      LOCK="$2"
      shift 2
      ;;
    --metadata)
      [[ "$#" -ge 2 ]] || fail "--metadata requires a value"
      METADATA="$2"
      shift 2
      ;;
    --receipt)
      [[ "$#" -ge 2 ]] || fail "--receipt requires a value"
      RECEIPT="$2"
      shift 2
      ;;
    --prepared-python)
      [[ "$#" -ge 2 ]] || fail "--prepared-python requires a value"
      PREPARED_PYTHON="$2"
      shift 2
      ;;
    --wheelhouse)
      [[ "$#" -ge 2 ]] || fail "--wheelhouse requires a value"
      WHEELHOUSE="$2"
      shift 2
      ;;
    --bootstrap-declaration)
      [[ "$#" -ge 2 ]] || fail "--bootstrap-declaration requires a value"
      BOOTSTRAP_DECLARATION="$2"
      shift 2
      ;;
    --source-identity)
      [[ "$#" -ge 2 ]] || fail "--source-identity requires a value"
      SOURCE_IDENTITY="$2"
      shift 2
      ;;
    --pre-sign-wheel-receipt)
      [[ "$#" -ge 2 ]] || fail "--pre-sign-wheel-receipt requires a value"
      PRE_SIGN_WHEEL_RECEIPT="$2"
      shift 2
      ;;
    --pre-sign-wheel-directory)
      [[ "$#" -ge 2 ]] || fail "--pre-sign-wheel-directory requires a value"
      PRE_SIGN_WHEEL_DIRECTORY="$2"
      shift 2
      ;;
    --pre-sign-component-receipt)
      [[ "$#" -ge 2 ]] || fail "--pre-sign-component-receipt requires a value"
      PRE_SIGN_COMPONENT_RECEIPT="$2"
      shift 2
      ;;
    --bootstrap-pre-sign)
      BOOTSTRAP_PRE_SIGN=1
      shift
      ;;
    --component)
      [[ "$#" -ge 2 ]] || fail "--component requires a value"
      COMPONENT="$2"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ -n "${LOCK}" && -n "${METADATA}" && -n "${RECEIPT}" ]] \
  || fail "--lock, --metadata, and --receipt are required"
[[ -n "${PREPARED_PYTHON}" && -n "${COMPONENT}" ]] \
  || fail "--prepared-python and --component are required"
[[ -n "${WHEELHOUSE}" && -n "${BOOTSTRAP_DECLARATION}" && -n "${SOURCE_IDENTITY}" ]] \
  || fail "--wheelhouse, --bootstrap-declaration, and --source-identity are required"
[[ "$#" -gt 0 && "$1" = /* && -x "$1" ]] \
  || fail "an executable absolute component build command is required after --"
[[ -x "${PREPARED_PYTHON}" ]] \
  || fail "prepared release build-toolchain Python is not executable: ${PREPARED_PYTHON}"
if [[ "${BOOTSTRAP_PRE_SIGN}" == "1" ]]; then
  [[ "${COMPONENT}" == "fpsample" || "${COMPONENT}" == "acvl-utils" ]] \
    || fail "--bootstrap-pre-sign is valid only for fpsample or acvl-utils"
  [[ -n "${PRE_SIGN_COMPONENT_RECEIPT}" && -n "${PRE_SIGN_WHEEL_DIRECTORY}" ]] \
    || fail "--bootstrap-pre-sign requires --pre-sign-component-receipt and --pre-sign-wheel-directory"
  [[ -z "${PRE_SIGN_WHEEL_RECEIPT}" ]] \
    || fail "--bootstrap-pre-sign cannot consume a final pre-sign wheel receipt"
else
  [[ -n "${PRE_SIGN_WHEEL_RECEIPT}" && -n "${PRE_SIGN_WHEEL_DIRECTORY}" ]] \
    || fail "final release component builds require --pre-sign-wheel-receipt and --pre-sign-wheel-directory"
  [[ -z "${PRE_SIGN_COMPONENT_RECEIPT}" ]] \
    || fail "--pre-sign-component-receipt is only valid with --bootstrap-pre-sign"
fi

# Absolute developer paths intentionally never enter the receipt or app.  The
# outer release builder selected Full Xcode before preparation; use that
# in-memory selection only after the verifier has re-captured matching versions.
DEVELOPER_DIR="${DEVELOPER_DIR:-}"
[[ -n "${DEVELOPER_DIR}" && -d "${DEVELOPER_DIR}" && ! -L "${DEVELOPER_DIR}" ]] \
  || fail "release component runner requires the already-selected full Xcode DEVELOPER_DIR"

SEALED_ROOT="${ROOT}/build/release-build-toolchain"
SEALED_HOME="${SEALED_ROOT}/sealed-home"
SEALED_TMP="${SEALED_ROOT}/sealed-tmp"
umask 077
mkdir -p "${SEALED_ROOT}" "${SEALED_HOME}" "${SEALED_TMP}"
[[ -d "${SEALED_ROOT}" && ! -L "${SEALED_ROOT}" && -O "${SEALED_ROOT}" \
  && -d "${SEALED_HOME}" && ! -L "${SEALED_HOME}" && -O "${SEALED_HOME}" \
  && -d "${SEALED_TMP}" && ! -L "${SEALED_TMP}" && -O "${SEALED_TMP}" ]] \
  || fail "sealed release build root/home/tmp paths must be owner-controlled directories, not symlinks"

run_sealed_preflight() {
  /usr/bin/env -i \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    HOME="${SEALED_HOME}" \
    TMPDIR="${SEALED_TMP}" \
    DEVELOPER_DIR="${DEVELOPER_DIR}" \
    PIP_CONFIG_FILE=/dev/null \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INDEX=1 \
    PIP_NO_INPUT=1 \
    PYTHONNOUSERSITE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_OFFLINE=1 \
    UV_NO_CONFIG=1 \
    LC_ALL=C \
    "$@"
}

BOOTSTRAP_AUTHORIZATION=""
BOOTSTRAP_AUTHORIZATION_DIRECTORY=""
cleanup_bootstrap_authorization() {
  if [[ -n "${BOOTSTRAP_AUTHORIZATION}" && -f "${BOOTSTRAP_AUTHORIZATION}" && ! -L "${BOOTSTRAP_AUTHORIZATION}" ]]; then
    rm -f -- "${BOOTSTRAP_AUTHORIZATION}"
  fi
  if [[ -n "${BOOTSTRAP_AUTHORIZATION_DIRECTORY}" && -d "${BOOTSTRAP_AUTHORIZATION_DIRECTORY}" && ! -L "${BOOTSTRAP_AUTHORIZATION_DIRECTORY}" ]]; then
    rmdir "${BOOTSTRAP_AUTHORIZATION_DIRECTORY}" 2>/dev/null || true
  fi
}
trap cleanup_bootstrap_authorization EXIT

if [[ "${BOOTSTRAP_PRE_SIGN}" == "1" ]]; then
  BOOTSTRAP_AUTHORIZATION_DIRECTORY="$(mktemp -d "${SEALED_ROOT}/bootstrap-authorization.${COMPONENT}.XXXXXXXX")"
  BOOTSTRAP_AUTHORIZATION="${BOOTSTRAP_AUTHORIZATION_DIRECTORY}/authorization.json"
  run_sealed_preflight "${PREPARED_PYTHON}" -I "${ROOT}/scripts/release_build_toolchain.py" \
    --lock "${LOCK}" \
    --metadata "${METADATA}" \
    --wheelhouse "${WHEELHOUSE}" \
    --bootstrap-declaration "${BOOTSTRAP_DECLARATION}" \
    --source-identity "${SOURCE_IDENTITY}" \
    --receipt "${RECEIPT}" \
    --verify-prepared-python "${PREPARED_PYTHON}" \
    --component "${COMPONENT}" \
    --bootstrap-authorization "${BOOTSTRAP_AUTHORIZATION}" \
    --create-bootstrap-authorization \
    --json >/dev/null
  VERIFY_JSON="$(run_sealed_preflight "${PREPARED_PYTHON}" -I "${ROOT}/scripts/release_build_toolchain.py" \
    --lock "${LOCK}" \
    --metadata "${METADATA}" \
    --verify-receipt "${RECEIPT}" \
    --verify-prepared-python "${PREPARED_PYTHON}" \
    --component "${COMPONENT}" \
    --json)"
  EXPECTED_FPSAMPLE_PRE_SIGN_SHA256=""
  EXPECTED_ACVL_UTILS_WHEEL_SHA256=""
else
  VERIFY_JSON="$(run_sealed_preflight "${PREPARED_PYTHON}" -I "${ROOT}/scripts/release_build_toolchain.py" \
    --lock "${LOCK}" \
    --metadata "${METADATA}" \
    --wheelhouse "${WHEELHOUSE}" \
    --bootstrap-declaration "${BOOTSTRAP_DECLARATION}" \
    --source-identity "${SOURCE_IDENTITY}" \
    --receipt "${RECEIPT}" \
    --verify-prepared-python "${PREPARED_PYTHON}" \
    --component "${COMPONENT}" \
    --pre-sign-wheel-receipt "${PRE_SIGN_WHEEL_RECEIPT}" \
    --pre-sign-wheel-directory "${PRE_SIGN_WHEEL_DIRECTORY}" \
    --verify-bootstrap-artifact \
    --json)"
  EXPECTED_FPSAMPLE_PRE_SIGN_SHA256="$(run_sealed_preflight "${PREPARED_PYTHON}" -I -c 'import json, sys; print(json.loads(sys.argv[1])["wheels"]["fpsample"]["sha256"])' "${VERIFY_JSON}")"
  EXPECTED_ACVL_UTILS_WHEEL_SHA256="$(run_sealed_preflight "${PREPARED_PYTHON}" -I -c 'import json, sys; print(json.loads(sys.argv[1])["wheels"]["acvl-utils"]["sha256"])' "${VERIFY_JSON}")"
  [[ "${EXPECTED_FPSAMPLE_PRE_SIGN_SHA256}" =~ ^[0-9a-f]{64}$ ]] \
    || fail "final pre-sign receipt did not provide a valid fpsample SHA-256"
  [[ "${EXPECTED_ACVL_UTILS_WHEEL_SHA256}" =~ ^[0-9a-f]{64}$ ]] \
    || fail "final pre-sign receipt did not provide a valid acvl-utils SHA-256"
fi
TOOLCHAIN_BIN="$(run_sealed_preflight "${PREPARED_PYTHON}" -I -c 'import json, sys; print(json.loads(sys.argv[1])["toolchain_bin"])' "${VERIFY_JSON}")"
[[ "${TOOLCHAIN_BIN}" = "$(dirname "${PREPARED_PYTHON}")" ]] \
  || fail "prepared release toolchain bin path is inconsistent"

SEALED_PATH="${TOOLCHAIN_BIN}:/usr/bin:/bin:/usr/sbin:/sbin"
PATH="${SEALED_PATH}"
export PATH
[[ "$(command -v cmake)" = "${TOOLCHAIN_BIN}/cmake" ]] \
  || fail "sealed release PATH did not resolve cmake from the prepared toolchain"
[[ "$(command -v ninja)" = "${TOOLCHAIN_BIN}/ninja" ]] \
  || fail "sealed release PATH did not resolve ninja from the prepared toolchain"

# Preserve only the application inputs that the component scripts actually
# consume.  In particular, do not carry CC/CXX/CMAKE_*/NINJA_*/PIP_*/UV_*/
# PYTHON* or dynamic-loader variables into the native build.
DIST_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR:-${ROOT}/dist}"
DICOM_ARTIFACT_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_ARTIFACT_DIR:-${ROOT}/build/dicom_normalizer-macos14-arm64}"
SIGNING_MODE="${TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE:-ad-hoc}"
CODESIGN_IDENTITY="${TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY:-}"

run_component_command() {
  /usr/bin/env -i \
  PATH="${SEALED_PATH}" \
  HOME="${SEALED_HOME}" \
  TMPDIR="${SEALED_TMP}" \
  DEVELOPER_DIR="${DEVELOPER_DIR}" \
  PIP_CONFIG_FILE=/dev/null \
  PIP_DISABLE_PIP_VERSION_CHECK=1 \
  PIP_NO_INDEX=1 \
  PIP_NO_INPUT=1 \
  PYTHONNOUSERSITE=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONHASHSEED=0 \
  SOURCE_DATE_EPOCH=1704067200 \
  UV_OFFLINE=1 \
  UV_NO_CONFIG=1 \
  LC_ALL=C \
  PYTHON_BIN="${PREPARED_PYTHON}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR="${DIST_DIR}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_ARTIFACT_DIR="${DICOM_ARTIFACT_DIR}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE="${SIGNING_MODE}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY="${CODESIGN_IDENTITY}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_REQUIRED=1 \
  TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_COMPONENT_RUNNER=1 \
  TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_PYTHON="${PREPARED_PYTHON}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_LOCK="${LOCK}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_METADATA="${METADATA}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_WHEELHOUSE="${WHEELHOUSE}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_RECEIPT="${RECEIPT}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_BOOTSTRAP_DECLARATION="${BOOTSTRAP_DECLARATION}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_SOURCE_IDENTITY="${SOURCE_IDENTITY}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_BOOTSTRAP_PRE_SIGN="${BOOTSTRAP_PRE_SIGN}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_BOOTSTRAP_AUTHORIZATION="${BOOTSTRAP_AUTHORIZATION}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_FPSAMPLE_PRE_SIGN_SHA256="${EXPECTED_FPSAMPLE_PRE_SIGN_SHA256}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_ACVL_UTILS_WHEEL_SHA256="${EXPECTED_ACVL_UTILS_WHEEL_SHA256}" \
  "$@"
}

run_component_command "$@"

if [[ "${BOOTSTRAP_PRE_SIGN}" == "1" ]]; then
  run_sealed_preflight "${PREPARED_PYTHON}" -I "${ROOT}/scripts/release_build_toolchain.py" \
    --lock "${LOCK}" \
    --metadata "${METADATA}" \
    --wheelhouse "${WHEELHOUSE}" \
    --bootstrap-declaration "${BOOTSTRAP_DECLARATION}" \
    --source-identity "${SOURCE_IDENTITY}" \
    --receipt "${RECEIPT}" \
    --verify-prepared-python "${PREPARED_PYTHON}" \
    --component "${COMPONENT}" \
    --bootstrap-authorization "${BOOTSTRAP_AUTHORIZATION}" \
    --pre-sign-wheel-directory "${PRE_SIGN_WHEEL_DIRECTORY}" \
    --record-component-pre-sign-receipt "${PRE_SIGN_COMPONENT_RECEIPT}" \
    --json >/dev/null
fi
