#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR:-${ROOT}/dist}"
BUILD_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_FPSAMPLE_BUILD_DIR:-${ROOT}/build/fpsample-wheel}"
PYTHON_BIN="${PYTHON_BIN:-${ROOT}/.venv/bin/python}"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
RELEASE_BUILD_TOOLCHAIN_REQUIRED="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_REQUIRED:-0}"
RELEASE_BUILD_TOOLCHAIN_PYTHON="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_PYTHON:-}"
RELEASE_COMPONENT_RUNNER="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_COMPONENT_RUNNER:-0}"
EXPECTED_PRE_SIGN_SHA256="${TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_FPSAMPLE_PRE_SIGN_SHA256:-}"
BOOTSTRAP_PRE_SIGN="${TOTALSEGMENTATOR_WRAPPER_MAC_BOOTSTRAP_PRE_SIGN:-0}"
BOOTSTRAP_AUTHORIZATION="${TOTALSEGMENTATOR_WRAPPER_MAC_BOOTSTRAP_AUTHORIZATION:-}"
RELEASE_TOOLCHAIN_LOCK="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_LOCK:-}"
RELEASE_TOOLCHAIN_METADATA="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_METADATA:-}"
RELEASE_TOOLCHAIN_WHEELHOUSE="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_WHEELHOUSE:-}"
RELEASE_TOOLCHAIN_RECEIPT="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_RECEIPT:-}"
RELEASE_TOOLCHAIN_BOOTSTRAP_DECLARATION="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_BOOTSTRAP_DECLARATION:-}"
RELEASE_TOOLCHAIN_SOURCE_IDENTITY="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_SOURCE_IDENTITY:-}"
FPSAMPLE_VERSION="1.0.2"
FPSAMPLE_SDIST="fpsample-${FPSAMPLE_VERSION}.tar.gz"
FPSAMPLE_URL="https://files.pythonhosted.org/packages/51/0d/c58b12e5dd6c0880b7f420d32af317e5817c54369019c0a04eb350b47ea2/${FPSAMPLE_SDIST}"
FPSAMPLE_SDIST_SHA256="5e25f97c03412d243767fb9e47f7b6d6c736c7ce1e9d51918894e3fd327749f2"

if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" != "0" && "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" != "1" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_REQUIRED must be 0 or 1." >&2
  exit 2
fi
if [[ "${BOOTSTRAP_PRE_SIGN}" != "0" && "${BOOTSTRAP_PRE_SIGN}" != "1" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_BOOTSTRAP_PRE_SIGN must be 0 or 1." >&2
  exit 2
fi
if [[ "${BOOTSTRAP_PRE_SIGN}" == "1" && "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" != "1" ]]; then
  echo "Bootstrap fpsample builds require the sealed release build-toolchain mode." >&2
  exit 2
fi
BUILD_PYTHON="${PYTHON_BIN}"
if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" == "1" ]]; then
  if [[ "${RELEASE_COMPONENT_RUNNER}" != "1" ]]; then
    echo "Release fpsample builds must run through run_release_component_build.sh." >&2
    exit 2
  fi
  if [[ ! -x "${RELEASE_BUILD_TOOLCHAIN_PYTHON}" ]]; then
    echo "Release fpsample builds require a prepared offline build-toolchain Python; run through build_mac_app.sh after preparing the hash-bound toolchain." >&2
    exit 2
  fi
  if [[ "${BOOTSTRAP_PRE_SIGN}" == "1" ]]; then
    if [[ -n "${EXPECTED_PRE_SIGN_SHA256}" ]]; then
      echo "Bootstrap fpsample builds must not receive a final expected wheel SHA-256." >&2
      exit 2
    fi
    if [[ -z "${BOOTSTRAP_AUTHORIZATION}" ]]; then
      echo "Bootstrap fpsample builds require receipt-backed bootstrap authorization from run_release_component_build.sh." >&2
      exit 2
    fi
    if [[ -z "${RELEASE_TOOLCHAIN_LOCK}" || -z "${RELEASE_TOOLCHAIN_METADATA}" || -z "${RELEASE_TOOLCHAIN_WHEELHOUSE}" || -z "${RELEASE_TOOLCHAIN_RECEIPT}" || -z "${RELEASE_TOOLCHAIN_BOOTSTRAP_DECLARATION}" || -z "${RELEASE_TOOLCHAIN_SOURCE_IDENTITY}" ]]; then
      echo "Bootstrap fpsample builds require the complete sealed toolchain and source identity context." >&2
      exit 2
    fi
    "${RELEASE_BUILD_TOOLCHAIN_PYTHON}" -I "${ROOT}/scripts/release_build_toolchain.py" \
      --lock "${RELEASE_TOOLCHAIN_LOCK}" \
      --metadata "${RELEASE_TOOLCHAIN_METADATA}" \
      --wheelhouse "${RELEASE_TOOLCHAIN_WHEELHOUSE}" \
      --bootstrap-declaration "${RELEASE_TOOLCHAIN_BOOTSTRAP_DECLARATION}" \
      --source-identity "${RELEASE_TOOLCHAIN_SOURCE_IDENTITY}" \
      --receipt "${RELEASE_TOOLCHAIN_RECEIPT}" \
      --verify-prepared-python "${RELEASE_BUILD_TOOLCHAIN_PYTHON}" \
      --component fpsample \
      --bootstrap-authorization "${BOOTSTRAP_AUTHORIZATION}" \
      --verify-bootstrap-authorization >/dev/null
  elif [[ ! "${EXPECTED_PRE_SIGN_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Final fpsample builds require the receipt-bound pre-sign SHA-256 supplied by run_release_component_build.sh." >&2
    exit 2
  fi
  BUILD_PYTHON="${RELEASE_BUILD_TOOLCHAIN_PYTHON}"
fi
if [[ ! -x "${BUILD_PYTHON}" ]]; then
  echo "Python 3.12 is required to build the fpsample wheel: ${PYTHON_BIN}" >&2
  exit 2
fi
if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" != "1" && -z "${UV_BIN}" ]]; then
  echo "uv is required to build the fpsample wheel." >&2
  exit 2
fi
if [[ "$("${BUILD_PYTHON}" -c 'import platform, sys; print(f"{sys.version_info.major}.{sys.version_info.minor}:{platform.machine()}")')" != "3.12:arm64" ]]; then
  echo "fpsample wheel build requires CPython 3.12 on arm64." >&2
  exit 2
fi

mkdir -p "${BUILD_DIR}" "${DIST_DIR}"
ARCHIVE_PATH="${BUILD_DIR}/${FPSAMPLE_SDIST}"
SOURCE_DIR="${BUILD_DIR}/fpsample-${FPSAMPLE_VERSION}"

if [[ ! -f "${ARCHIVE_PATH}" ]] || [[ "$(shasum -a 256 "${ARCHIVE_PATH}" | awk '{print $1}')" != "${FPSAMPLE_SDIST_SHA256}" ]]; then
  if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" == "1" ]]; then
    echo "Release fpsample builds require the pinned source archive to be prepared locally; refusing a network download: ${ARCHIVE_PATH}" >&2
    exit 2
  fi
  curl -fL "${FPSAMPLE_URL}" -o "${ARCHIVE_PATH}"
fi
ACTUAL_SHA256="$(shasum -a 256 "${ARCHIVE_PATH}" | awk '{print $1}')"
if [[ "${ACTUAL_SHA256}" != "${FPSAMPLE_SDIST_SHA256}" ]]; then
  echo "fpsample sdist SHA-256 mismatch: ${ACTUAL_SHA256}" >&2
  exit 1
fi

rm -rf "${SOURCE_DIR}"
tar -xzf "${ARCHIVE_PATH}" -C "${BUILD_DIR}"
rm -f "${DIST_DIR}"/fpsample-${FPSAMPLE_VERSION}-*.whl

export MACOSX_DEPLOYMENT_TARGET=13.0
export CMAKE_OSX_DEPLOYMENT_TARGET=13.0
export CMAKE_OSX_ARCHITECTURES=arm64
export ARCHFLAGS="-arch arm64"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1704067200}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-${ROOT}/.uv-cache}"

if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" == "1" ]]; then
  "${BUILD_PYTHON}" -m build --wheel --no-isolation --outdir "${DIST_DIR}" "${SOURCE_DIR}"
else
  "${UV_BIN}" build --wheel "${SOURCE_DIR}" \
    --python "${BUILD_PYTHON}" \
    --out-dir "${DIST_DIR}"
fi

WHEEL_PATH="$(ls -1 "${DIST_DIR}"/fpsample-${FPSAMPLE_VERSION}-cp312-cp312-macosx_13_0_arm64.whl 2>/dev/null | head -n 1)"
if [[ -z "${WHEEL_PATH}" || ! -f "${WHEEL_PATH}" ]]; then
  echo "Expected macOS 13 arm64 fpsample wheel was not produced." >&2
  exit 1
fi
PRE_SIGN_WHEEL_SHA256="$(shasum -a 256 "${WHEEL_PATH}" | awk '{print $1}')"
if [[ -n "${EXPECTED_PRE_SIGN_SHA256}" && "${PRE_SIGN_WHEEL_SHA256}" != "${EXPECTED_PRE_SIGN_SHA256}" ]]; then
  echo "fpsample pre-sign wheel SHA-256 differs from the canonical dependency-lock resolver input: expected ${EXPECTED_PRE_SIGN_SHA256}, found ${PRE_SIGN_WHEEL_SHA256}" >&2
  exit 1
fi
if [[ "${TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE:-ad-hoc}" == "developer-id" ]]; then
  CODESIGN_IDENTITY="${TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY:-}"
  if [[ -z "${CODESIGN_IDENTITY}" ]]; then
    echo "Developer ID identity is required to sign the bundled fpsample wheel." >&2
    exit 2
  fi
  "${BUILD_PYTHON}" "${ROOT}/scripts/sign_fpsample_wheel_macos.py" \
    --wheel "${WHEEL_PATH}" \
    --identity "${CODESIGN_IDENTITY}"
fi
if ! unzip -l "${WHEEL_PATH}" | grep -E 'fpsample-1[.]0[.]2[.]dist-info/.*/?LICENSE|fpsample-1[.]0[.]2[.]dist-info/LICENSE' >/dev/null; then
  echo "fpsample wheel does not contain its MIT license." >&2
  exit 1
fi

echo "${WHEEL_PATH}"
