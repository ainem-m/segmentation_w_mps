#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOYMENT_TARGET="14.0"
ARCHITECTURE="arm64"
SOURCE_DATE_EPOCH="1735689600"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
SOURCE_DIR="${ROOT_DIR}/native/dicom_normalizer"
WORK_PARENT="${TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_WORK_PARENT:-${ROOT_DIR}/build}"
ARTIFACT_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_ARTIFACT_DIR:-${ROOT_DIR}/build/dicom_normalizer-macos14-arm64}"
ARTIFACT_PARENT="$(dirname "${ARTIFACT_DIR}")"
GDCM_ARTIFACT_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_GDCM_ARTIFACT_DIR:-${ROOT_DIR}/build/gdcm-3.2.7-macos14-arm64}"
GDCM_ARTIFACT_VERIFY_SCRIPT="${ROOT_DIR}/scripts/verify_gdcm_source_artifact.py"
ARTIFACT_VERIFY_SCRIPT="${ROOT_DIR}/scripts/verify_dicom_normalizer_artifact.py"
MACHO_VERIFY_SCRIPT="${ROOT_DIR}/scripts/verify_macos_deployment_target.py"
LINKAGE_VERIFY_SCRIPT="${ROOT_DIR}/scripts/verify_macos_binary_linkage.py"
DISCOVERY_CACHE_VERIFY_SCRIPT="${ROOT_DIR}/scripts/validate_cmake_package_discovery.py"
BUILD_WORK=""
ARTIFACT_STAGING=""
TOOLCHAIN_JSON=""
CMAKE_PATH=""
XCRUN_PATH=""

die() {
  echo "DICOM normalizer macOS 14 build: $*" >&2
  exit 2
}

cleanup_owned_staging() {
  local candidate="${BUILD_WORK}"
  if [[ -n "${candidate}" \
    && "$(dirname "${candidate}")" == "${WORK_PARENT}" \
    && "$(basename "${candidate}")" == .dicom-normalizer-build.* \
    && -d "${candidate}" \
    && ! -L "${candidate}" \
    && -O "${candidate}" ]]; then
    rm -rf "${candidate}"
  fi
  candidate="${ARTIFACT_STAGING}"
  if [[ -n "${candidate}" \
    && "$(dirname "${candidate}")" == "${ARTIFACT_PARENT}" \
    && "$(basename "${candidate}")" == .dicom-normalizer-artifact.* \
    && -d "${candidate}" \
    && ! -L "${candidate}" \
    && -O "${candidate}" ]]; then
    rm -rf "${candidate}"
  fi
}

verify_static_artifact() {
  local artifact_dir="$1"
  local binary="${artifact_dir}/totalsegmentator-wrapper-dicom-normalizer"
  "${PYTHON_BIN}" "${ARTIFACT_VERIFY_SCRIPT}" \
    --verify \
    --artifact-dir "${artifact_dir}" \
    --source-dir "${SOURCE_DIR}" \
    --expected-toolchain-json "${TOOLCHAIN_JSON}" >/dev/null
  "${PYTHON_BIN}" "${MACHO_VERIFY_SCRIPT}" \
    --path "${binary}" \
    --max-macos "${DEPLOYMENT_TARGET}" \
    --require-arm64 >/dev/null
  "${PYTHON_BIN}" "${LINKAGE_VERIFY_SCRIPT}" --path "${binary}" >/dev/null
}

capture_toolchain() {
  CMAKE_PATH="$(command -v cmake)"
  XCRUN_PATH="$(command -v xcrun)"
  SDKROOT="$("${XCRUN_PATH}" --sdk macosx --show-sdk-path)"
  CC_PATH="$("${XCRUN_PATH}" --find clang)"
  CXX_PATH="$("${XCRUN_PATH}" --find clang++)"
  [[ -d "${SDKROOT}" && -x "${CMAKE_PATH}" && -x "${XCRUN_PATH}" && -x "${CC_PATH}" && -x "${CXX_PATH}" ]] \
    || die "Xcode did not provide a usable SDK and Apple Clang toolchain"
  TOOLCHAIN_JSON="$("${PYTHON_BIN}" "${ARTIFACT_VERIFY_SCRIPT}" \
    --capture-toolchain \
    --cmake-path "${CMAKE_PATH}" \
    --xcrun-path "${XCRUN_PATH}" \
    --compiler-path "${CC_PATH}" \
    --cxx-compiler-path "${CXX_PATH}" \
    --sdk-root "${SDKROOT}")"
}

assert_toolchain_unchanged() {
  local observed_toolchain
  observed_toolchain="$("${PYTHON_BIN}" "${ARTIFACT_VERIFY_SCRIPT}" \
    --capture-toolchain \
    --cmake-path "${CMAKE_PATH}" \
    --xcrun-path "${XCRUN_PATH}" \
    --compiler-path "${CC_PATH}" \
    --cxx-compiler-path "${CXX_PATH}" \
    --sdk-root "${SDKROOT}")"
  [[ "${observed_toolchain}" == "${TOOLCHAIN_JSON}" ]] \
    || die "CMake, xcrun, compiler, or SDK identity changed during the normalizer build"
}

[[ "$(uname -s)" == "Darwin" ]] || die "this builder is for macOS only"
[[ -n "${PYTHON_BIN}" && -x "${PYTHON_BIN}" ]] || die "Python 3 is required"
for command_name in cmake xcrun; do
  command -v "${command_name}" >/dev/null 2>&1 || die "required command is unavailable: ${command_name}"
done
for required_file in "${ARTIFACT_VERIFY_SCRIPT}" "${GDCM_ARTIFACT_VERIFY_SCRIPT}" "${MACHO_VERIFY_SCRIPT}" "${LINKAGE_VERIFY_SCRIPT}" "${DISCOVERY_CACHE_VERIFY_SCRIPT}"; do
  [[ -f "${required_file}" ]] || die "required release verifier is missing: ${required_file}"
done

mkdir -p "${WORK_PARENT}" "${ARTIFACT_PARENT}"
for directory in "${WORK_PARENT}" "${ARTIFACT_PARENT}"; do
  [[ -d "${directory}" && ! -L "${directory}" && -O "${directory}" ]] \
    || die "work/artifact parent must be owner-controlled and non-symlink: ${directory}"
done
if [[ -e "${ARTIFACT_DIR}" || -L "${ARTIFACT_DIR}" ]]; then
  capture_toolchain
  if verify_static_artifact "${ARTIFACT_DIR}"; then
    echo "${ARTIFACT_DIR}/totalsegmentator-wrapper-dicom-normalizer"
    exit 0
  fi
  die "refusing to overwrite an invalid or stale existing artifact; choose a new TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_ARTIFACT_DIR"
fi

if [[ ! -f "${GDCM_ARTIFACT_DIR}/gdcm-build-provenance.json" ]]; then
  die "pinned GDCM 3.2.7 macOS 14 artifact is not prepared. Run scripts/build_gdcm_macos14_arm64.sh explicitly first; the DICOM normalizer builder will not download or build GDCM implicitly"
fi
capture_toolchain
GDCM_PREFIX="$("${PYTHON_BIN}" "${GDCM_ARTIFACT_VERIFY_SCRIPT}" \
  --verify \
  --artifact-dir "${GDCM_ARTIFACT_DIR}" \
  --expected-toolchain-json "${TOOLCHAIN_JSON}")"
EXPECTED_GDCM_DIR="${GDCM_PREFIX}/lib/gdcm-3.2"
[[ -d "${EXPECTED_GDCM_DIR}" && ! -L "${EXPECTED_GDCM_DIR}" ]] \
  || die "pinned GDCM CMake package directory is missing: ${EXPECTED_GDCM_DIR}"

BUILD_WORK="$(mktemp -d "${WORK_PARENT}/.dicom-normalizer-build.XXXXXX")"
ARTIFACT_STAGING="$(mktemp -d "${ARTIFACT_PARENT}/.dicom-normalizer-artifact.XXXXXX")"
trap cleanup_owned_staging EXIT
REPRO_FLAGS="-O2 -ffile-prefix-map=${SOURCE_DIR}=. -fdebug-prefix-map=${SOURCE_DIR}=. -ffile-prefix-map=${BUILD_WORK}=. -fdebug-prefix-map=${BUILD_WORK}=."

# GDCM_DIR is the sole non-system dependency input.  Package registries,
# Homebrew prefixes, pkg-config, and any previous CMake cache are excluded.
env \
  -u CMAKE_PREFIX_PATH \
  -u CMAKE_LIBRARY_PATH \
  -u CMAKE_INCLUDE_PATH \
  -u PKG_CONFIG_PATH \
  -u PKG_CONFIG_LIBDIR \
  -u CPATH \
  -u C_INCLUDE_PATH \
  -u CPLUS_INCLUDE_PATH \
  -u LIBRARY_PATH \
  -u DYLD_LIBRARY_PATH \
  -u DYLD_FALLBACK_LIBRARY_PATH \
  LC_ALL=C \
  LANG=C \
  TZ=UTC \
  SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH}" \
  SDKROOT="${SDKROOT}" \
  MACOSX_DEPLOYMENT_TARGET="${DEPLOYMENT_TARGET}" \
  CC="${CC_PATH}" \
  CXX="${CXX_PATH}" \
  CFLAGS="${REPRO_FLAGS}" \
  CXXFLAGS="${REPRO_FLAGS}" \
  LDFLAGS="" \
  CPPFLAGS="" \
  "${CMAKE_PATH}" -S "${SOURCE_DIR}" -B "${BUILD_WORK}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_OSX_ARCHITECTURES="${ARCHITECTURE}" \
    -DCMAKE_OSX_DEPLOYMENT_TARGET="${DEPLOYMENT_TARGET}" \
    -DCMAKE_OSX_SYSROOT="${SDKROOT}" \
    -DCMAKE_SKIP_RPATH=ON \
    "-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local" \
    "-DCMAKE_SYSTEM_IGNORE_PATH=/opt/homebrew;/usr/local" \
    -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE \
    -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE \
    -DGDCM_DIR="${EXPECTED_GDCM_DIR}" \
    -DBUILD_TESTING=OFF
env \
  LC_ALL=C \
  LANG=C \
  TZ=UTC \
  SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH}" \
  MACOSX_DEPLOYMENT_TARGET="${DEPLOYMENT_TARGET}" \
  "${CMAKE_PATH}" --build "${BUILD_WORK}" --config Release --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-4}"

BINARY="${BUILD_WORK}/totalsegmentator-wrapper-dicom-normalizer"
[[ -x "${BINARY}" && ! -L "${BINARY}" ]] \
  || die "build did not produce an executable regular normalizer: ${BINARY}"
RECORDED_GDCM_DIR="$(awk -F= '$1 == "GDCM_DIR:PATH" || $1 == "GDCM_DIR:UNINITIALIZED" {print $2; exit}' "${BUILD_WORK}/CMakeCache.txt")"
[[ "${RECORDED_GDCM_DIR}" == "${EXPECTED_GDCM_DIR}" ]] \
  || die "CMake did not use the pinned GDCM_DIR; found ${RECORDED_GDCM_DIR:-missing}"
"${PYTHON_BIN}" "${DISCOVERY_CACHE_VERIFY_SCRIPT}" "${BUILD_WORK}/CMakeCache.txt" \
  || die "CMake cache contains a forbidden Homebrew or /usr/local path"
"${PYTHON_BIN}" "${MACHO_VERIFY_SCRIPT}" \
  --path "${BINARY}" \
  --max-macos "${DEPLOYMENT_TARGET}" \
  --require-arm64 >/dev/null
"${PYTHON_BIN}" "${LINKAGE_VERIFY_SCRIPT}" --path "${BINARY}" >/dev/null

cp "${BINARY}" "${ARTIFACT_STAGING}/totalsegmentator-wrapper-dicom-normalizer"
chmod 755 "${ARTIFACT_STAGING}/totalsegmentator-wrapper-dicom-normalizer"
mkdir "${ARTIFACT_STAGING}/licenses"
cp "${GDCM_ARTIFACT_DIR}/licenses/"* "${ARTIFACT_STAGING}/licenses/"
assert_toolchain_unchanged
"${PYTHON_BIN}" "${ARTIFACT_VERIFY_SCRIPT}" \
  --create \
  --artifact-dir "${ARTIFACT_STAGING}" \
  --source-dir "${SOURCE_DIR}" \
  --gdcm-artifact-dir "${GDCM_ARTIFACT_DIR}" \
  --toolchain-json "${TOOLCHAIN_JSON}" >/dev/null

if ! "${PYTHON_BIN}" -c 'import os, sys; os.rename(sys.argv[1], sys.argv[2])' "${ARTIFACT_STAGING}" "${ARTIFACT_DIR}"; then
  die "could not atomically publish the immutable normalizer artifact; another build may have won the race"
fi
ARTIFACT_STAGING=""
verify_static_artifact "${ARTIFACT_DIR}"
echo "${ARTIFACT_DIR}/totalsegmentator-wrapper-dicom-normalizer"
