#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR:-${ROOT}/dist}"
BUILD_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_ACVL_UTILS_BUILD_DIR:-${ROOT}/build/acvl-utils-wheel}"
PYTHON_BIN="${PYTHON_BIN:-${ROOT}/.venv/bin/python}"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
RELEASE_BUILD_TOOLCHAIN_REQUIRED="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_REQUIRED:-0}"
RELEASE_BUILD_TOOLCHAIN_PYTHON="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_PYTHON:-}"
RELEASE_COMPONENT_RUNNER="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_COMPONENT_RUNNER:-0}"
EXPECTED_WHEEL_SHA256="${TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_ACVL_UTILS_WHEEL_SHA256:-}"
BOOTSTRAP_PRE_SIGN="${TOTALSEGMENTATOR_WRAPPER_MAC_BOOTSTRAP_PRE_SIGN:-0}"
BOOTSTRAP_AUTHORIZATION="${TOTALSEGMENTATOR_WRAPPER_MAC_BOOTSTRAP_AUTHORIZATION:-}"
RELEASE_TOOLCHAIN_LOCK="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_LOCK:-}"
RELEASE_TOOLCHAIN_METADATA="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_METADATA:-}"
RELEASE_TOOLCHAIN_WHEELHOUSE="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_WHEELHOUSE:-}"
RELEASE_TOOLCHAIN_RECEIPT="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_RECEIPT:-}"
RELEASE_TOOLCHAIN_BOOTSTRAP_DECLARATION="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_BOOTSTRAP_DECLARATION:-}"
RELEASE_TOOLCHAIN_SOURCE_IDENTITY="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_SOURCE_IDENTITY:-}"
ACVL_UTILS_VERSION="0.2.6"
ACVL_UTILS_SDIST="acvl_utils-${ACVL_UTILS_VERSION}.tar.gz"
ACVL_UTILS_URL="https://files.pythonhosted.org/packages/f3/7b/cac76bd8285369399be3ac2e29f3e4ef2b36fe3b75fe357825e481eee825/${ACVL_UTILS_SDIST}"
ACVL_UTILS_SDIST_SHA256="d6bd68a916fb2451ab3dd640b2494e545edc204c839ae1d4dd49f88f89999b74"
EXPECTED_WHEEL="acvl_utils-0.2.6-py3-none-any.whl"

if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" != "0" && "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" != "1" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_REQUIRED must be 0 or 1." >&2
  exit 2
fi
if [[ "${BOOTSTRAP_PRE_SIGN}" != "0" && "${BOOTSTRAP_PRE_SIGN}" != "1" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_BOOTSTRAP_PRE_SIGN must be 0 or 1." >&2
  exit 2
fi
if [[ "${BOOTSTRAP_PRE_SIGN}" == "1" && "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" != "1" ]]; then
  echo "Bootstrap acvl-utils builds require the sealed release build-toolchain mode." >&2
  exit 2
fi
BUILD_PYTHON="${PYTHON_BIN}"
if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" == "1" ]]; then
  if [[ "${RELEASE_COMPONENT_RUNNER}" != "1" ]]; then
    echo "Release acvl-utils builds must run through run_release_component_build.sh." >&2
    exit 2
  fi
  if [[ ! -x "${RELEASE_BUILD_TOOLCHAIN_PYTHON}" ]]; then
    echo "Release acvl-utils builds require a prepared offline build-toolchain Python; run through build_mac_app.sh after preparing the hash-bound toolchain." >&2
    exit 2
  fi
  if [[ "${BOOTSTRAP_PRE_SIGN}" == "1" ]]; then
    if [[ -n "${EXPECTED_WHEEL_SHA256}" ]]; then
      echo "Bootstrap acvl-utils builds must not receive a final expected wheel SHA-256." >&2
      exit 2
    fi
    if [[ -z "${BOOTSTRAP_AUTHORIZATION}" ]]; then
      echo "Bootstrap acvl-utils builds require receipt-backed bootstrap authorization from run_release_component_build.sh." >&2
      exit 2
    fi
    if [[ -z "${RELEASE_TOOLCHAIN_LOCK}" || -z "${RELEASE_TOOLCHAIN_METADATA}" || -z "${RELEASE_TOOLCHAIN_WHEELHOUSE}" || -z "${RELEASE_TOOLCHAIN_RECEIPT}" || -z "${RELEASE_TOOLCHAIN_BOOTSTRAP_DECLARATION}" || -z "${RELEASE_TOOLCHAIN_SOURCE_IDENTITY}" ]]; then
      echo "Bootstrap acvl-utils builds require the complete sealed toolchain and source identity context." >&2
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
      --component acvl-utils \
      --bootstrap-authorization "${BOOTSTRAP_AUTHORIZATION}" \
      --verify-bootstrap-authorization >/dev/null
  elif [[ ! "${EXPECTED_WHEEL_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Final acvl-utils builds require the receipt-bound pre-sign SHA-256 supplied by run_release_component_build.sh." >&2
    exit 2
  fi
  BUILD_PYTHON="${RELEASE_BUILD_TOOLCHAIN_PYTHON}"
fi
if [[ ! -x "${BUILD_PYTHON}" ]]; then
  echo "Python 3.12 is required to build the acvl-utils wheel: ${PYTHON_BIN}" >&2
  exit 2
fi
if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" != "1" && -z "${UV_BIN}" ]]; then
  echo "uv is required to build the acvl-utils wheel." >&2
  exit 2
fi
if [[ "$("${BUILD_PYTHON}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" != "3.12" ]]; then
  echo "acvl-utils wheel build requires Python 3.12." >&2
  exit 2
fi

mkdir -p "${BUILD_DIR}" "${DIST_DIR}"
ARCHIVE_PATH="${BUILD_DIR}/${ACVL_UTILS_SDIST}"
DOWNLOAD_PATH="${ARCHIVE_PATH}.download"
SOURCE_DIR="${BUILD_DIR}/acvl_utils-${ACVL_UTILS_VERSION}"

if [[ ! -f "${ARCHIVE_PATH}" ]] || [[ "$(shasum -a 256 "${ARCHIVE_PATH}" | awk '{print $1}')" != "${ACVL_UTILS_SDIST_SHA256}" ]]; then
  if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" == "1" ]]; then
    echo "Release acvl-utils builds require the pinned source archive to be prepared locally; refusing a network download: ${ARCHIVE_PATH}" >&2
    exit 2
  fi
  rm -f "${DOWNLOAD_PATH}"
  curl --fail --location --retry 3 --output "${DOWNLOAD_PATH}" "${ACVL_UTILS_URL}"
  ACTUAL_DOWNLOAD_SHA256="$(shasum -a 256 "${DOWNLOAD_PATH}" | awk '{print $1}')"
  if [[ "${ACTUAL_DOWNLOAD_SHA256}" != "${ACVL_UTILS_SDIST_SHA256}" ]]; then
    echo "acvl-utils sdist SHA-256 mismatch: ${ACTUAL_DOWNLOAD_SHA256}" >&2
    exit 1
  fi
  mv "${DOWNLOAD_PATH}" "${ARCHIVE_PATH}"
fi
ACTUAL_SHA256="$(shasum -a 256 "${ARCHIVE_PATH}" | awk '{print $1}')"
if [[ "${ACTUAL_SHA256}" != "${ACVL_UTILS_SDIST_SHA256}" ]]; then
  echo "acvl-utils sdist SHA-256 mismatch: ${ACTUAL_SHA256}" >&2
  exit 1
fi

rm -rf "${SOURCE_DIR}"
"${BUILD_PYTHON}" - "${ARCHIVE_PATH}" "${BUILD_DIR}" "acvl_utils-${ACVL_UTILS_VERSION}" <<'PY'
from __future__ import annotations

import sys
import tarfile
from pathlib import Path, PurePosixPath


archive_path = Path(sys.argv[1])
build_dir = Path(sys.argv[2]).resolve()
expected_root = sys.argv[3]
with tarfile.open(archive_path, "r:gz") as archive:
    members = archive.getmembers()
    if not members:
        raise SystemExit("acvl-utils sdist is empty")
    for member in members:
        relative = PurePosixPath(member.name)
        if relative.is_absolute() or ".." in relative.parts:
            raise SystemExit(f"unsafe acvl-utils sdist member: {member.name}")
        if not relative.parts or relative.parts[0] != expected_root:
            raise SystemExit(f"unexpected acvl-utils sdist root: {member.name}")
        if member.issym() or member.islnk() or member.isdev():
            raise SystemExit(f"unsupported acvl-utils sdist member type: {member.name}")
        target = (build_dir / Path(*relative.parts)).resolve()
        if not target.is_relative_to(build_dir):
            raise SystemExit(f"unsafe acvl-utils sdist destination: {member.name}")
    archive.extractall(build_dir, filter="data")
PY

rm -f "${DIST_DIR}"/acvl_utils-${ACVL_UTILS_VERSION}-*.whl
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1704067200}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-${ROOT}/.uv-cache}"
if [[ "${RELEASE_BUILD_TOOLCHAIN_REQUIRED}" == "1" ]]; then
  "${BUILD_PYTHON}" -m build --wheel --no-isolation --outdir "${DIST_DIR}" "${SOURCE_DIR}"
else
  "${UV_BIN}" build --wheel "${SOURCE_DIR}" \
    --python "${BUILD_PYTHON}" \
    --out-dir "${DIST_DIR}"
fi

WHEEL_PATH="${DIST_DIR}/${EXPECTED_WHEEL}"
if [[ ! -f "${WHEEL_PATH}" ]]; then
  echo "Expected pure-Python acvl-utils wheel was not produced: ${EXPECTED_WHEEL}" >&2
  exit 1
fi
"${BUILD_PYTHON}" - "${WHEEL_PATH}" <<'PY'
from __future__ import annotations

import sys
import zipfile
from pathlib import Path, PurePosixPath


wheel = Path(sys.argv[1])
dist_info = "acvl_utils-0.2.6.dist-info"
with zipfile.ZipFile(wheel) as archive:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    for info in infos:
        relative = PurePosixPath(info.filename)
        if relative.is_absolute() or ".." in relative.parts:
            raise SystemExit(f"unsafe acvl-utils wheel member: {info.filename}")
        if not info.is_dir() and relative.suffix.lower() in {".so", ".dylib", ".a", ".o"}:
            raise SystemExit(f"acvl-utils wheel unexpectedly contains native code: {info.filename}")
    required = {
        "metadata": f"{dist_info}/METADATA",
        "wheel": f"{dist_info}/WHEEL",
        "license": f"{dist_info}/licenses/LICENCE",
        "record": f"{dist_info}/RECORD",
    }
    for label, name in required.items():
        if names.count(name) != 1:
            raise SystemExit(f"acvl-utils wheel {label} file is missing or duplicated: {name}")
    metadata = archive.read(required["metadata"]).decode("utf-8")
    wheel_metadata = archive.read(required["wheel"]).decode("utf-8")
    license_text = archive.read(required["license"]).decode("utf-8")
    if "Name: acvl_utils\n" not in metadata or "Version: 0.2.6\n" not in metadata:
        raise SystemExit("acvl-utils wheel name or version metadata is invalid")
    if "License-Expression: Apache-2.0\n" not in metadata:
        raise SystemExit("acvl-utils wheel License-Expression: Apache-2.0 is missing")
    if "Root-Is-Purelib: true\n" not in wheel_metadata or "Tag: py3-none-any\n" not in wheel_metadata:
        raise SystemExit("acvl-utils wheel is not the expected pure py3-none-any wheel")
    if "Apache License" not in license_text or "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" not in license_text:
        raise SystemExit("acvl-utils wheel Apache-2.0 license text is missing")
PY

"${BUILD_PYTHON}" - "${ROOT}" "${WHEEL_PATH}" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, sys.argv[1])
from scripts.verify_license_distribution import verify_bundled_acvl_utils_wheel


verify_bundled_acvl_utils_wheel(Path(sys.argv[2]))
PY

ACTUAL_WHEEL_SHA256="$(shasum -a 256 "${WHEEL_PATH}" | awk '{print $1}')"
if [[ -n "${EXPECTED_WHEEL_SHA256}" && "${ACTUAL_WHEEL_SHA256}" != "${EXPECTED_WHEEL_SHA256}" ]]; then
  echo "acvl-utils wheel SHA-256 differs from the canonical dependency-lock resolver input: expected ${EXPECTED_WHEEL_SHA256}, found ${ACTUAL_WHEEL_SHA256}" >&2
  exit 1
fi

echo "${WHEEL_PATH}"
