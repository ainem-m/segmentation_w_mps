#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NATIVE_BUILD_DIR="${ROOT}/build/dicom_normalizer"
STAGE_DIR="${ROOT}/build/mac_wheel_staging"
DIST_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR:-${ROOT}/dist}"
PYTHON_BIN="${PYTHON_BIN:-${ROOT}/.venv/bin/python}"
PLAT_NAME="${PLAT_NAME:-macosx_11_0_arm64}"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
SIGNING_MODE="${TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE:-ad-hoc}"
CODESIGN_IDENTITY="${TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY:-}"
APP_ENTITLEMENTS="${ROOT}/resources/entitlements/app.entitlements"
export UV_CACHE_DIR="${UV_CACHE_DIR:-${ROOT}/.uv-cache}"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1704067200}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
if [[ -z "${UV_BIN}" ]]; then
  echo "uv is required to build the Mac wheel without direct setup.py invocation." >&2
  exit 1
fi

"${ROOT}/scripts/build_dicom_normalizer_mac.sh" >/dev/null

rm -rf "${STAGE_DIR}"
mkdir -p "${STAGE_DIR}"
rsync -a \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude ".uv-cache" \
  --exclude "__pycache__" \
  --exclude "*.pyc" \
  --exclude "artifacts" \
  --exclude "build" \
  --exclude "dist" \
  --exclude "runs" \
  "${ROOT}/" "${STAGE_DIR}/"

mkdir -p "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/bin"
cp "${NATIVE_BUILD_DIR}/totalsegmentator-wrapper-dicom-normalizer" \
  "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/bin/totalsegmentator-wrapper-dicom-normalizer"
cp -R "${NATIVE_BUILD_DIR}/lib" \
  "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/bin/lib"
mkdir -p "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/licenses"
for license_name in \
  GDCM-BSD-3-Clause.txt \
  GDCM-IJG-JPEG-README.txt \
  OpenJPEG-BSD-2-Clause.txt \
  CharLS-BSD-3-Clause.txt \
  json-c-MIT.txt \
  OpenSSL-Apache-2.0.txt; do
  cp "${ROOT}/resources/third_party/licenses/${license_name}" \
    "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/licenses/${license_name}"
done

if [[ "${SIGNING_MODE}" == "developer-id" ]]; then
  if [[ -z "${CODESIGN_IDENTITY}" ]]; then
    echo "TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY is required when signing wheel binaries." >&2
    exit 2
  fi
  for library in "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/bin/lib"/*.dylib; do
    codesign \
      --force \
      --timestamp \
      --options runtime \
      --sign "${CODESIGN_IDENTITY}" \
      "${library}" >/dev/null
  done
  codesign \
    --force \
    --timestamp \
    --options runtime \
    --entitlements "${APP_ENTITLEMENTS}" \
    --sign "${CODESIGN_IDENTITY}" \
    "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/bin/totalsegmentator-wrapper-dicom-normalizer" >/dev/null
  codesign --verify --strict --verbose=2 \
    "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/bin/totalsegmentator-wrapper-dicom-normalizer" >/dev/null
  for library in "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/bin/lib"/*.dylib; do
    codesign --verify --strict --verbose=2 "${library}" >/dev/null
  done
fi

mkdir -p "${DIST_DIR}"
rm -f "${DIST_DIR}"/totalsegmentator_wrapper_mac-*.whl
cat > "${STAGE_DIR}/setup.py" <<'PY'
from setuptools import Distribution, setup


class BinaryDistribution(Distribution):
    def has_ext_modules(self):
        return True


setup(distclass=BinaryDistribution)
PY
"${UV_BIN}" build --wheel --no-build-isolation "${STAGE_DIR}" \
  --python "${PYTHON_BIN}" \
  --out-dir "${DIST_DIR}" \
  --config-setting="--build-option=--plat-name" \
  --config-setting="--build-option=${PLAT_NAME}"

echo "${DIST_DIR}"
