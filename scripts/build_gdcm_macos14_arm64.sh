#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GDCM_VERSION="3.2.7"
GDCM_SOURCE_URL="https://github.com/malaterre/GDCM/archive/refs/tags/v3.2.7.tar.gz"
GDCM_SOURCE_SHA256="b7b17b70c009677cf244cc7837b88386441e097f8861fdeee83aa27d1bc1b090"
GDCM_SOURCE_ROOT_NAME="GDCM-3.2.7"
DEPLOYMENT_TARGET="14.0"
ARCHITECTURE="arm64"
SOURCE_DATE_EPOCH="1735689600"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
CACHE_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_GDCM_CACHE_DIR:-${ROOT}/build/source-cache/gdcm-3.2.7}"
WORK_PARENT="${TOTALSEGMENTATOR_WRAPPER_MAC_GDCM_WORK_PARENT:-${ROOT}/build}"
ARTIFACT_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_GDCM_ARTIFACT_DIR:-${ROOT}/build/gdcm-3.2.7-macos14-arm64}"
ARTIFACT_PARENT="$(dirname "${ARTIFACT_DIR}")"
ARCHIVE_PATH="${CACHE_DIR}/GDCM-v3.2.7.tar.gz"
FETCH_SCRIPT="${ROOT}/scripts/fetch_pinned_source_archive.py"
LICENSE_SCRIPT="${ROOT}/scripts/collect_gdcm_source_licenses.py"
ARTIFACT_VERIFY_SCRIPT="${ROOT}/scripts/verify_gdcm_source_artifact.py"
BUILD_WORK=""
INSTALL_WORK=""
ARTIFACT_STAGING=""
SOURCE_WORK=""
TOOLCHAIN_JSON=""

die() {
  echo "Pinned GDCM macOS 14 build: $*" >&2
  exit 2
}

cleanup_owned_staging() {
  local candidate
  for candidate in "${BUILD_WORK}" "${INSTALL_WORK}" "${SOURCE_WORK}"; do
    if [[ -n "${candidate}" \
      && "$(dirname "${candidate}")" == "${WORK_PARENT}" \
      && "$(basename "${candidate}")" == .gdcm-* \
      && -d "${candidate}" \
      && ! -L "${candidate}" \
      && -O "${candidate}" ]]; then
      rm -rf "${candidate}"
    fi
  done
  candidate="${ARTIFACT_STAGING}"
  if [[ -n "${candidate}" \
    && "$(dirname "${candidate}")" == "${ARTIFACT_PARENT}" \
    && "$(basename "${candidate}")" == .gdcm-artifact.* \
    && -d "${candidate}" \
    && ! -L "${candidate}" \
    && -O "${candidate}" ]]; then
    rm -rf "${candidate}"
  fi
}

verify_existing_artifact() {
  "${PYTHON_BIN}" "${ARTIFACT_VERIFY_SCRIPT}" \
    --verify \
    --artifact-dir "${ARTIFACT_DIR}" \
    --expected-toolchain-json "${TOOLCHAIN_JSON}"
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
    || die "CMake, xcrun, compiler, or SDK identity changed during the GDCM build"
}

[[ "$(uname -s)" == "Darwin" ]] || die "this builder is for macOS only"
[[ -n "${PYTHON_BIN}" && -x "${PYTHON_BIN}" ]] || die "Python 3 is required"
for command_name in cmake xcrun; do
  command -v "${command_name}" >/dev/null 2>&1 || die "required command is unavailable: ${command_name}"
done
for required_file in "${FETCH_SCRIPT}" "${LICENSE_SCRIPT}" "${ARTIFACT_VERIFY_SCRIPT}"; do
  [[ -f "${required_file}" ]] || die "required release input is missing: ${required_file}"
done

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

mkdir -p "${CACHE_DIR}" "${WORK_PARENT}" "${ARTIFACT_PARENT}"
for directory in "${CACHE_DIR}" "${WORK_PARENT}" "${ARTIFACT_PARENT}"; do
  [[ -d "${directory}" && ! -L "${directory}" && -O "${directory}" ]] \
    || die "build/cache directory must be an owner-controlled non-symlink: ${directory}"
done
if [[ -e "${ARTIFACT_DIR}" || -L "${ARTIFACT_DIR}" ]]; then
  verify_existing_artifact
  exit 0
fi

SOURCE_WORK="$(mktemp -d "${WORK_PARENT}/.gdcm-source.XXXXXX")"
trap cleanup_owned_staging EXIT
SOURCE_ROOT="$(
  "${PYTHON_BIN}" "${FETCH_SCRIPT}" \
    --url "${GDCM_SOURCE_URL}" \
    --sha256 "${GDCM_SOURCE_SHA256}" \
    --archive "${ARCHIVE_PATH}" \
    --output-parent "${SOURCE_WORK}" \
    --expected-root "${GDCM_SOURCE_ROOT_NAME}"
)"
[[ -d "${SOURCE_ROOT}" && ! -L "${SOURCE_ROOT}" && -O "${SOURCE_ROOT}" ]] \
  || die "verified GDCM source root is not owner-controlled: ${SOURCE_ROOT}"

BUILD_WORK="$(mktemp -d "${WORK_PARENT}/.gdcm-build.XXXXXX")"
INSTALL_WORK="$(mktemp -d "${WORK_PARENT}/.gdcm-install.XXXXXX")"
ARTIFACT_STAGING="$(mktemp -d "${ARTIFACT_PARENT}/.gdcm-artifact.XXXXXX")"
PREFIX="${INSTALL_WORK}/prefix"
LICENSES="${ARTIFACT_STAGING}/licenses"
REPRO_FLAGS="-O2 -ffile-prefix-map=${SOURCE_ROOT}=. -fdebug-prefix-map=${SOURCE_ROOT}=. -ffile-prefix-map=${BUILD_WORK}=. -fdebug-prefix-map=${BUILD_WORK}=."

# A fresh build directory plus a scrubbed discovery environment prevents an
# old CMake cache or Homebrew/pkg-config setting from entering static archives.
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
  "${CMAKE_PATH}" -S "${SOURCE_ROOT}" -B "${BUILD_WORK}" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${PREFIX}" \
    -DCMAKE_OSX_ARCHITECTURES="${ARCHITECTURE}" \
    -DCMAKE_OSX_DEPLOYMENT_TARGET="${DEPLOYMENT_TARGET}" \
    -DCMAKE_OSX_SYSROOT="${SDKROOT}" \
    -DCMAKE_SKIP_RPATH=ON \
    "-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local" \
    "-DCMAKE_SYSTEM_IGNORE_PATH=/opt/homebrew;/usr/local" \
    -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE \
    -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE \
    -DBUILD_SHARED_LIBS=OFF \
    -DGDCM_BUILD_SHARED_LIBS=OFF \
    -DGDCM_BUILD_APPLICATIONS=OFF \
    -DGDCM_BUILD_TESTING=OFF \
    -DGDCM_BUILD_EXAMPLES=OFF \
    -DGDCM_BUILD_DOCBOOK_MANPAGES=OFF \
    -DGDCM_USE_VTK=OFF \
    -DGDCM_WRAP_PYTHON=OFF \
    -DGDCM_WRAP_JAVA=OFF \
    -DGDCM_WRAP_CSHARP=OFF \
    -DGDCM_WRAP_PERL=OFF \
    -DGDCM_WRAP_PHP=OFF \
    -DGDCM_USE_SYSTEM_ZLIB=OFF \
    -DGDCM_USE_SYSTEM_OPENSSL=OFF \
    -DGDCM_USE_SYSTEM_EXPAT=OFF \
    -DGDCM_USE_SYSTEM_JSON=OFF \
    -DGDCM_USE_SYSTEM_OPENJPEG=OFF \
    -DGDCM_USE_SYSTEM_CHARLS=OFF \
    -DGDCM_USE_SYSTEM_UUID=OFF \
    -DGDCM_USE_SYSTEM_SOCKETXX=OFF \
    -DGDCM_USE_SYSTEM_LJPEG=OFF \
    -DGDCM_USE_SYSTEM_LIBXML2=OFF \
    -DGDCM_USE_SYSTEM_POPPLER=OFF \
    -DGDCM_USE_JPEGTURBO=OFF \
    -DGDCM_USE_PVRG=OFF \
    -DGDCM_USE_KAKADU=OFF

env \
  LC_ALL=C \
  LANG=C \
  TZ=UTC \
  SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH}" \
  MACOSX_DEPLOYMENT_TARGET="${DEPLOYMENT_TARGET}" \
  "${CMAKE_PATH}" --build "${BUILD_WORK}" --config Release --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-4}"
"${CMAKE_PATH}" --install "${BUILD_WORK}" --config Release

mv "${PREFIX}" "${ARTIFACT_STAGING}/prefix"
rmdir "${INSTALL_WORK}"
INSTALL_WORK=""
"${PYTHON_BIN}" "${LICENSE_SCRIPT}" \
  --source-root "${SOURCE_ROOT}" \
  --output-dir "${LICENSES}" >/dev/null
assert_toolchain_unchanged
"${PYTHON_BIN}" "${ARTIFACT_VERIFY_SCRIPT}" \
  --create \
  --artifact-dir "${ARTIFACT_STAGING}" \
  --toolchain-json "${TOOLCHAIN_JSON}" >/dev/null

if ! "${PYTHON_BIN}" -c 'import os, sys; os.rename(sys.argv[1], sys.argv[2])' "${ARTIFACT_STAGING}" "${ARTIFACT_DIR}"; then
  die "could not atomically publish the immutable GDCM artifact; another build may have won the race"
fi
ARTIFACT_STAGING=""
verify_existing_artifact
