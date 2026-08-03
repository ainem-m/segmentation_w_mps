#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT}/build"
DIST_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR:-${ROOT}/dist}"
PYTHON_BIN="${PYTHON_BIN:-${ROOT}/.venv/bin/python}"
CANONICAL_PLAT_NAME="macosx_14_0_arm64"
PLAT_NAME="${PLAT_NAME:-${CANONICAL_PLAT_NAME}}"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
RELEASE_BUILD_TOOLCHAIN_REQUIRED="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_REQUIRED:-0}"
RELEASE_BUILD_TOOLCHAIN_PYTHON="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_PYTHON:-}"
RELEASE_COMPONENT_RUNNER="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_COMPONENT_RUNNER:-0}"
SIGNING_MODE="${TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE:-ad-hoc}"
CODESIGN_IDENTITY="${TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY:-}"
APP_ENTITLEMENTS="${ROOT}/resources/entitlements/app.entitlements"
NORMALIZER_ARTIFACT_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_ARTIFACT_DIR:-${ROOT}/build/dicom_normalizer-macos14-arm64}"
NORMALIZER_PATH="${NORMALIZER_ARTIFACT_DIR}/totalsegmentator-wrapper-dicom-normalizer"
export UV_CACHE_DIR="${UV_CACHE_DIR:-${ROOT}/.uv-cache}"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1704067200}"

path_has_safe_write_mode() {
  local mode
  mode="$(stat -f %Lp "$1" 2>/dev/null || true)"
  [[ "${mode}" =~ ^[0-7]{3,4}$ ]] && (( (8#${mode} & 8#22) == 0 ))
}

validate_distribution_directory() {
  local directory="$1"
  local parent
  if [[ "${directory}" != /* || "${directory}" == "/" ]]; then
    echo "Refusing to use an unsafe wheel distribution directory: ${directory}" >&2
    return 2
  fi
  if [[ -e "${directory}" || -L "${directory}" ]]; then
    if [[ ! -d "${directory}" || -L "${directory}" || ! -O "${directory}" ]] \
      || ! path_has_safe_write_mode "${directory}"; then
      echo "Refusing to use an unsafe wheel distribution directory: ${directory}" >&2
      return 2
    fi
  else
    parent="$(dirname "${directory}")"
    if [[ ! -d "${parent}" || -L "${parent}" || ! -O "${parent}" ]] \
      || ! path_has_safe_write_mode "${parent}"; then
      echo "Refusing to create a wheel distribution directory under an unsafe parent: ${parent}" >&2
      return 2
    fi
    mkdir "${directory}"
  fi
}

validate_distribution_directory "${DIST_DIR}"

if [[ "${PLAT_NAME}" != "${CANONICAL_PLAT_NAME}" ]]; then
  echo "Wrapper product wheel tag must be exactly ${CANONICAL_PLAT_NAME}; got ${PLAT_NAME}. Component wheels may use an older compatible tag, but the 0.4.1 app product floor is macOS 14." >&2
  exit 2
fi

if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" != "0" && "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" != "1" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_REQUIRED must be 0 or 1." >&2
  exit 2
fi
BUILD_PYTHON="${PYTHON_BIN}"
if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" == "1" ]]; then
  if [[ "${RELEASE_COMPONENT_RUNNER}" != "1" ]]; then
    echo "Release wrapper-wheel builds must run through run_release_component_build.sh." >&2
    exit 2
  fi
  if [[ ! -x "${RELEASE_BUILD_TOOLCHAIN_PYTHON}" ]]; then
    echo "Release wrapper-wheel builds require a prepared offline build-toolchain Python; run through build_mac_app.sh after preparing the hash-bound toolchain." >&2
    exit 2
  fi
  BUILD_PYTHON="${RELEASE_BUILD_TOOLCHAIN_PYTHON}"
fi
if [[ ! -x "${BUILD_PYTHON}" ]]; then
  PYTHON_BIN="$(command -v python3)"
  BUILD_PYTHON="${PYTHON_BIN}"
fi
PYTHON_VERSION="$("${BUILD_PYTHON}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
if [[ "${PYTHON_VERSION}" != "3.12" ]]; then
  echo "Wrapper macOS product wheel must be built with CPython 3.12; got ${PYTHON_VERSION:-unknown} from ${PYTHON_BIN}." >&2
  exit 2
fi
if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" != "1" && -z "${UV_BIN}" ]]; then
  echo "uv is required to build the Mac wheel without direct setup.py invocation." >&2
  exit 1
fi
PROJECT_VERSION="$("${PYTHON_BIN}" -c 'import pathlib, sys, tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["project"]["version"])' "${ROOT}/pyproject.toml")"
EXPECTED_WHEEL_BASENAME="totalsegmentator_wrapper_mac-${PROJECT_VERSION}-cp312-cp312-${CANONICAL_PLAT_NAME}.whl"

if [[ ! -f "${NORMALIZER_ARTIFACT_DIR}/dicom-normalizer-build-provenance.json" ]]; then
  echo "Pinned macOS 14 DICOM normalizer artifact is not prepared. Run scripts/build_dicom_normalizer_mac.sh explicitly before wheel/app packaging; packaging will not download or build GDCM implicitly." >&2
  exit 2
fi
"${PYTHON_BIN}" "${ROOT}/scripts/verify_dicom_normalizer_artifact.py" \
  --verify \
  --artifact-dir "${NORMALIZER_ARTIFACT_DIR}" \
  --source-dir "${ROOT}/native/dicom_normalizer" >/dev/null
NATIVE_BUILD_DIR="$(dirname "${NORMALIZER_PATH}")"
"${PYTHON_BIN}" "${ROOT}/scripts/verify_macos_deployment_target.py" \
  --path "${NORMALIZER_PATH}" \
  --max-macos 14.0 \
  --require-arm64 >/dev/null
"${PYTHON_BIN}" "${ROOT}/scripts/verify_macos_binary_linkage.py" \
  --path "${NORMALIZER_PATH}" >/dev/null

if [[ ! -e "${BUILD_DIR}" ]]; then
  BUILD_PARENT="$(dirname "${BUILD_DIR}")"
  if [[ ! -d "${BUILD_PARENT}" || -L "${BUILD_PARENT}" || ! -O "${BUILD_PARENT}" ]] \
    || ! path_has_safe_write_mode "${BUILD_PARENT}"; then
    echo "Wheel build parent must be owner-controlled and non-symlink: ${BUILD_PARENT}" >&2
    exit 2
  fi
  mkdir "${BUILD_DIR}"
fi
if [[ ! -d "${BUILD_DIR}" || -L "${BUILD_DIR}" || ! -O "${BUILD_DIR}" ]] \
  || ! path_has_safe_write_mode "${BUILD_DIR}"; then
  echo "Wheel build directory must be owner-controlled and non-symlink: ${BUILD_DIR}" >&2
  exit 2
fi
WHEEL_RUN_ID="$("${PYTHON_BIN}" -c 'import secrets; print(secrets.token_hex(8))')"
WHEEL_RUN_DIR="${BUILD_DIR}/mac-wheel-${WHEEL_RUN_ID}"
STAGE_DIR="${WHEEL_RUN_DIR}/source"
WHEEL_BUILD_OUT_DIR="${WHEEL_RUN_DIR}/out"
mkdir "${WHEEL_RUN_DIR}"
if [[ ! -d "${WHEEL_RUN_DIR}" || -L "${WHEEL_RUN_DIR}" || ! -O "${WHEEL_RUN_DIR}" ]] \
  || ! path_has_safe_write_mode "${WHEEL_RUN_DIR}"; then
  echo "Wheel run directory must be owner-controlled and non-symlink: ${WHEEL_RUN_DIR}" >&2
  exit 2
fi
mkdir "${STAGE_DIR}" "${WHEEL_BUILD_OUT_DIR}"
for project_file in pyproject.toml README.md LICENSE NOTICE; do
  cp "${ROOT}/${project_file}" "${STAGE_DIR}/${project_file}"
done
mkdir "${STAGE_DIR}/src"
rsync -a \
  --exclude "__pycache__" \
  --exclude "*.pyc" \
  --exclude "*.egg-info" \
  --exclude "totalsegmentator_wrapper_mac/bin" \
  --exclude "totalsegmentator_wrapper_mac/licenses" \
  "${ROOT}/src/" "${STAGE_DIR}/src/"

mkdir -p "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/bin"
cp "${NORMALIZER_PATH}" \
  "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/bin/totalsegmentator-wrapper-dicom-normalizer"
mkdir -p "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/licenses"
for license_path in "${NATIVE_BUILD_DIR}/licenses"/*; do
  cp "${license_path}" \
    "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/licenses/$(basename "${license_path}")"
done
cp "${NATIVE_BUILD_DIR}/dicom-normalizer-build-provenance.json" \
  "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/licenses/dicom-normalizer-build-provenance.json"
cp "${NATIVE_BUILD_DIR}/gdcm-build-provenance.json" \
  "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/licenses/gdcm-build-provenance.json"
for license_name in \
  TotalSegmentator-Apache-2.0.txt \
  DentalSegmentator-NOTICE.txt \
  ToothSeg-NOTICE.txt \
  MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt \
  TGNet-User-Provided-Checkpoint-NOTICE.txt; do
  cp "${ROOT}/resources/third_party/licenses/${license_name}" \
    "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/licenses/${license_name}"
done
cp "${ROOT}/resources/third_party/totalsegmentator_task_inventory.json" \
  "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/licenses/TotalSegmentator-task-inventory.json"

if [[ "${SIGNING_MODE}" == "developer-id" ]]; then
  if [[ -z "${CODESIGN_IDENTITY}" ]]; then
    echo "TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY is required when signing wheel binaries." >&2
    exit 2
  fi
  codesign \
    --force \
    --timestamp \
    --options runtime \
    --entitlements "${APP_ENTITLEMENTS}" \
    --sign "${CODESIGN_IDENTITY}" \
    "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/bin/totalsegmentator-wrapper-dicom-normalizer" >/dev/null
  codesign --verify --strict --verbose=2 \
    "${STAGE_DIR}/src/totalsegmentator_wrapper_mac/bin/totalsegmentator-wrapper-dicom-normalizer" >/dev/null
fi

EXPECTED_WHEEL_PATH="${DIST_DIR}/${EXPECTED_WHEEL_BASENAME}"
WHEEL_RECEIPT_PATH="${EXPECTED_WHEEL_PATH}.receipt.json"
if [[ -e "${EXPECTED_WHEEL_PATH}" || -L "${EXPECTED_WHEEL_PATH}" ]]; then
  if [[ ! -f "${EXPECTED_WHEEL_PATH}" || -L "${EXPECTED_WHEEL_PATH}" || ! -O "${EXPECTED_WHEEL_PATH}" ]] \
    || ! path_has_safe_write_mode "${EXPECTED_WHEEL_PATH}"; then
    echo "Refusing to replace an unsafe wrapper wheel path: ${EXPECTED_WHEEL_PATH}" >&2
    exit 2
  fi
fi
if [[ -e "${WHEEL_RECEIPT_PATH}" || -L "${WHEEL_RECEIPT_PATH}" ]]; then
  if [[ ! -f "${WHEEL_RECEIPT_PATH}" || -L "${WHEEL_RECEIPT_PATH}" || ! -O "${WHEEL_RECEIPT_PATH}" ]] \
    || ! path_has_safe_write_mode "${WHEEL_RECEIPT_PATH}"; then
    echo "Refusing to replace an unsafe wrapper wheel receipt path: ${WHEEL_RECEIPT_PATH}" >&2
    exit 2
  fi
fi
cat > "${STAGE_DIR}/setup.py" <<'PY'
from setuptools import Distribution, setup


class BinaryDistribution(Distribution):
    def has_ext_modules(self):
        return True


setup(distclass=BinaryDistribution)
PY
if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" == "1" ]]; then
  "${BUILD_PYTHON}" -m build --wheel --no-isolation --outdir "${WHEEL_BUILD_OUT_DIR}" \
    --config-setting="--build-option=--plat-name" \
    --config-setting="--build-option=${PLAT_NAME}" \
    "${STAGE_DIR}"
else
  "${UV_BIN}" build --wheel --no-build-isolation "${STAGE_DIR}" \
    --python "${BUILD_PYTHON}" \
    --out-dir "${WHEEL_BUILD_OUT_DIR}" \
    --config-setting="--build-option=--plat-name" \
    --config-setting="--build-option=${PLAT_NAME}"
fi

BUILT_WHEEL_PATH="${WHEEL_BUILD_OUT_DIR}/${EXPECTED_WHEEL_BASENAME}"
if [[ ! -f "${BUILT_WHEEL_PATH}" || -L "${BUILT_WHEEL_PATH}" ]]; then
  echo "Wheel build did not produce the exact CPython 3.12/macOS 14 artifact: ${EXPECTED_WHEEL_BASENAME}" >&2
  exit 2
fi
if [[ ! -O "${BUILT_WHEEL_PATH}" ]] || ! path_has_safe_write_mode "${BUILT_WHEEL_PATH}"; then
  echo "Built wheel is not owner-controlled with a safe write mode: ${BUILT_WHEEL_PATH}" >&2
  exit 2
fi
WHEEL_PATH="${BUILT_WHEEL_PATH}"
"${PYTHON_BIN}" "${ROOT}/scripts/verify_macos_deployment_target.py" \
  --wheel "${WHEEL_PATH}" \
  --max-macos 14.0 \
  --require-arm64 >/dev/null
"${PYTHON_BIN}" "${ROOT}/scripts/verify_macos_binary_linkage.py" \
  --wheel "${WHEEL_PATH}" >/dev/null
"${PYTHON_BIN}" "${ROOT}/scripts/verify_license_distribution.py" \
  --source "${ROOT}" \
  --wheel "${WHEEL_PATH}" \
  --expected-version "${PROJECT_VERSION}" >/dev/null

WHEEL_RECEIPT_STAGED="${WHEEL_RUN_DIR}/wheel-build-receipt.json"
# Keep receipt generation explicit and independent of shell interpolation.
"${PYTHON_BIN}" - "${ROOT}" "${BUILT_WHEEL_PATH}" "${PROJECT_VERSION}" "${CANONICAL_PLAT_NAME}" "${WHEEL_RECEIPT_STAGED}" <<'PY'
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1]).resolve()
wheel = Path(sys.argv[2]).resolve(strict=True)
version = sys.argv[3]
tag = sys.argv[4]
receipt = Path(sys.argv[5])
source_commit = subprocess.run(
    ["git", "-C", str(root), "rev-parse", "HEAD"],
    check=True,
    stdout=subprocess.PIPE,
    text=True,
).stdout.strip()
payload = {
    "schema": "totalsegmentator_wrapper_mac.wheel_build_receipt.v1",
    "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "version": version,
    "platform_tag": tag,
    "source_commit": source_commit,
    "wheel_filename": wheel.name,
    "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    "wheel_size_bytes": wheel.stat().st_size,
}
receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

if [[ ! -f "${WHEEL_RECEIPT_STAGED}" || -L "${WHEEL_RECEIPT_STAGED}" \
  || ! -O "${WHEEL_RECEIPT_STAGED}" ]] \
  || ! path_has_safe_write_mode "${WHEEL_RECEIPT_STAGED}"; then
  echo "Wheel receipt staging file is unsafe: ${WHEEL_RECEIPT_STAGED}" >&2
  exit 2
fi

mv -f "${BUILT_WHEEL_PATH}" "${EXPECTED_WHEEL_PATH}"
WHEEL_RECEIPT_TEMP="${DIST_DIR}/.$(basename "${WHEEL_RECEIPT_PATH}").${WHEEL_RUN_ID}.tmp"
if [[ -e "${WHEEL_RECEIPT_TEMP}" || -L "${WHEEL_RECEIPT_TEMP}" ]]; then
  echo "Refusing to replace an existing wheel receipt temporary file: ${WHEEL_RECEIPT_TEMP}" >&2
  exit 2
fi
cp "${WHEEL_RECEIPT_STAGED}" "${WHEEL_RECEIPT_TEMP}"
if [[ ! -O "${WHEEL_RECEIPT_TEMP}" ]] || ! path_has_safe_write_mode "${WHEEL_RECEIPT_TEMP}"; then
  echo "Wheel receipt temporary file is unsafe: ${WHEEL_RECEIPT_TEMP}" >&2
  exit 2
fi
mv -f "${WHEEL_RECEIPT_TEMP}" "${WHEEL_RECEIPT_PATH}"

echo "${EXPECTED_WHEEL_PATH}"
