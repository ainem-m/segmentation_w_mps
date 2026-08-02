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
[[ "$#" -gt 0 && "$1" = /* && -x "$1" ]] \
  || fail "an executable absolute component build command is required after --"
[[ -x "${PREPARED_PYTHON}" ]] \
  || fail "prepared release build-toolchain Python is not executable: ${PREPARED_PYTHON}"

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

VERIFY_JSON="$(run_sealed_preflight "${PREPARED_PYTHON}" -I "${ROOT}/scripts/release_build_toolchain.py" \
  --lock "${LOCK}" \
  --metadata "${METADATA}" \
  --verify-receipt "${RECEIPT}" \
  --verify-prepared-python "${PREPARED_PYTHON}" \
  --component "${COMPONENT}" \
  --json)"
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
EXPECTED_FPSAMPLE_PRE_SIGN_SHA256="${TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_FPSAMPLE_PRE_SIGN_SHA256:-}"
EXPECTED_ACVL_UTILS_WHEEL_SHA256="${TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_ACVL_UTILS_WHEEL_SHA256:-}"

exec /usr/bin/env -i \
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
  TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_FPSAMPLE_PRE_SIGN_SHA256="${EXPECTED_FPSAMPLE_PRE_SIGN_SHA256}" \
  TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_ACVL_UTILS_WHEEL_SHA256="${EXPECTED_ACVL_UTILS_WHEEL_SHA256}" \
  "$@"
