#!/usr/bin/env bash
# Build the exact dcm2niix source release that produced the previously tested
# command-line behaviour, for the project's macOS 14 / arm64 release floor.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT}/.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi

# Do not replace this tag with the latest upstream release without a fresh
# compatibility review.  The previous Homebrew-built input came from this
# exact official archive.  That source tree intentionally prints the embedded
# CLI version v1.0.20250505; the release tag itself is v1.0.20250506.
DCM2NIIX_RELEASE_TAG="v1.0.20250506"
DCM2NIIX_EXPECTED_CLI_VERSION="v1.0.20250505"
DCM2NIIX_SOURCE_URL="https://github.com/rordenlab/dcm2niix/archive/refs/tags/${DCM2NIIX_RELEASE_TAG}.tar.gz"
DCM2NIIX_SOURCE_SHA256="1b24658678b6c24141e58760dbea9fe2786ffdd736bcc37a36d9cdabc731bafa"
DCM2NIIX_LICENSE_SHA256="a423e1c074ff39d9c22843489dd81bbaf42d4fa243fd785f8e96ce084db2e503"
DCM2NIIX_SOURCE_ROOT="dcm2niix-1.0.20250506"
MINIMUM_MACOS_VERSION="14.0"
REQUESTED_SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH-}"
SOURCE_DATE_EPOCH="1746489600"
SOURCE_CACHE_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_SOURCE_CACHE_DIR:-${ROOT}/build/source-cache}"
OUTPUT_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX_BUILD_DIR:-${ROOT}/build/dcm2niix-macos14-arm64}"
SOURCE_ARCHIVE="${SOURCE_CACHE_DIR}/dcm2niix-${DCM2NIIX_RELEASE_TAG}.tar.gz"
SOURCE_PARENT="${SOURCE_CACHE_DIR}/src"
ARTIFACTS_DIR="${OUTPUT_DIR}/artifacts"
CURRENT_ARTIFACT_POINTER="${OUTPUT_DIR}/current-artifact.json"
FETCH_SCRIPT="${ROOT}/scripts/fetch_pinned_source_archive.py"
MACHO_VERIFY_SCRIPT="${ROOT}/scripts/verify_macos_deployment_target.py"
LINKAGE_VERIFY_SCRIPT="${ROOT}/scripts/verify_macos_binary_linkage.py"
BUNDLED_NOTICE="${ROOT}/resources/third_party/licenses/dcm2niix-license.txt"
PUBLISH_LOCK_ACQUIRED=0

die() {
  echo "dcm2niix macOS 14 build: $*" >&2
  exit 2
}

if [[ -n "${REQUESTED_SOURCE_DATE_EPOCH}" \
  && "${REQUESTED_SOURCE_DATE_EPOCH}" != "${SOURCE_DATE_EPOCH}" ]]; then
  die "SOURCE_DATE_EPOCH is fixed for this pinned release at ${SOURCE_DATE_EPOCH}; got ${REQUESTED_SOURCE_DATE_EPOCH}"
fi

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

require_bsd_notice() {
  local path="$1"
  local label="$2"
  [[ -f "${path}" ]] || die "${label} BSD license text is missing: ${path}"
  for required in \
    "Chris Rorden" \
    "Redistribution and use in source and binary forms" \
    "THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT OWNER"; do
    grep -Fq -- "${required}" "${path}" \
      || die "${label} BSD license text is missing required attribution: ${required}"
  done
}

sha256_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

require_expected_sha256() {
  local path="$1"
  local expected="$2"
  local label="$3"
  local actual
  actual="$(sha256_file "${path}")"
  [[ "${actual}" == "${expected}" ]] \
    || die "${label} SHA-256 mismatch: expected ${expected}, found ${actual}"
}

require_owner_controlled_directory() {
  local path="$1"
  local label="$2"
  local owner
  [[ -d "${path}" && ! -L "${path}" ]] \
    || die "${label} must be a non-symlink directory: ${path}"
  owner="$(stat -f '%u' "${path}")"
  [[ "${owner}" == "$(id -u)" ]] \
    || die "${label} must be owned by the invoking user: ${path}"
}

remove_owned_staging_directory() {
  local path="$1"
  local parent="$2"
  local prefix="$3"
  [[ -n "${path}" && -d "${path}" && ! -L "${path}" ]] || return 0
  [[ "$(dirname "${path}")" == "${parent}" && "$(basename "${path}")" == "${prefix}"* ]] || return 0
  [[ "$(stat -f '%u' "${path}")" == "$(id -u)" ]] || return 0
  rm -rf "${path}"
}

cleanup_staging() {
  if [[ -n "${SOURCE_BUILD_PARENT:-}" && -n "${SOURCE_PARENT:-}" ]]; then
    remove_owned_staging_directory \
      "${SOURCE_BUILD_PARENT}" "${SOURCE_PARENT}" ".dcm2niix-source."
  fi
  if [[ -n "${CMAKE_BUILD_DIR:-}" && -n "${BUILD_STAGING_PARENT:-}" ]]; then
    remove_owned_staging_directory \
      "${CMAKE_BUILD_DIR}" "${BUILD_STAGING_PARENT}" ".dcm2niix-cmake."
  fi
  if [[ -n "${INSTALL_STAGING:-}" ]]; then
    remove_owned_staging_directory \
      "${INSTALL_STAGING}" "${OUTPUT_DIR}" ".dcm2niix-install."
  fi
  if [[ -n "${ARTIFACT_STAGING:-}" && -n "${ARTIFACTS_DIR:-}" ]]; then
    remove_owned_staging_directory \
      "${ARTIFACT_STAGING}" "${ARTIFACTS_DIR}" ".dcm2niix-artifact."
  fi
  if [[ "${PUBLISH_LOCK_ACQUIRED:-0}" == "1" ]] \
    && [[ -n "${PUBLISH_LOCK:-}" && -d "${PUBLISH_LOCK}" && ! -L "${PUBLISH_LOCK}" ]] \
    && [[ "$(dirname "${PUBLISH_LOCK}")" == "${ARTIFACTS_DIR}" ]] \
    && [[ "$(basename "${PUBLISH_LOCK}")" == ".dcm2niix-publish-lock" ]] \
    && [[ "$(stat -f '%u' "${PUBLISH_LOCK}")" == "$(id -u)" ]]; then
    rmdir "${PUBLISH_LOCK}" 2>/dev/null || true
  fi
}

require_owner_controlled_regular_file() {
  local path="$1"
  local label="$2"
  local owner
  [[ -f "${path}" && ! -L "${path}" ]] \
    || die "${label} must be a regular non-symlink file: ${path}"
  owner="$(stat -f '%u' "${path}")"
  [[ "${owner}" == "$(id -u)" ]] \
    || die "${label} must be owned by the invoking user: ${path}"
}

verify_artifact_metadata() {
  local artifact_dir="$1"
  local artifact_relative="$2"
  local expected_binary_sha256="$3"
  "${PYTHON_BIN}" - \
    "${artifact_dir}" \
    "${artifact_relative}" \
    "${expected_binary_sha256}" \
    "${DCM2NIIX_RELEASE_TAG}" \
    "${DCM2NIIX_EXPECTED_CLI_VERSION}" \
    "${DCM2NIIX_SOURCE_URL}" \
    "${DCM2NIIX_SOURCE_SHA256}" \
    "${DCM2NIIX_LICENSE_SHA256}" \
    "${MINIMUM_MACOS_VERSION}" \
    "${SOURCE_DATE_EPOCH}" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

(
    artifact_dir,
    artifact_relative,
    expected_binary_sha256,
    release_tag,
    expected_cli_version,
    source_url,
    source_archive_sha256,
    license_sha256,
    minimum_macos,
    source_date_epoch,
) = sys.argv[1:]
root = Path(artifact_dir)
expected_nodes = {
    "dcm2niix": "file",
    "dcm2niix-build-provenance.json": "file",
    "licenses": "directory",
    "licenses/dcm2niix-license.txt": "file",
}

def regular_owned(path: Path, kind: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise SystemExit(f"artifact is missing required {kind}: {path}") from exc
    if metadata.st_uid != os.getuid():
        raise SystemExit(f"artifact entry is not owned by the invoking user: {path}")
    if kind == "file" and not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(f"artifact entry must be a regular file: {path}")
    if kind == "directory" and not stat.S_ISDIR(metadata.st_mode):
        raise SystemExit(f"artifact entry must be a directory: {path}")

regular_owned(root, "directory")
seen = set()
for path in root.rglob("*"):
    relative = path.relative_to(root).as_posix()
    seen.add(relative)
    if path.is_symlink():
        raise SystemExit(f"artifact must not contain symlinks: {path}")
    if relative not in expected_nodes:
        raise SystemExit(f"artifact contains an unexpected entry: {path}")
    regular_owned(path, expected_nodes[relative])
if seen != set(expected_nodes):
    raise SystemExit("artifact tree does not exactly match the required files")

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()

binary = root / "dcm2niix"
license_file = root / "licenses" / "dcm2niix-license.txt"
receipt_file = root / "dcm2niix-build-provenance.json"
if sha256(binary) != expected_binary_sha256:
    raise SystemExit("artifact binary SHA-256 does not match its content-addressed directory")
if sha256(license_file) != license_sha256:
    raise SystemExit("artifact license SHA-256 does not match the pinned upstream license")
try:
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"artifact receipt is invalid JSON: {exc}") from exc
expected = {
    "schema": "totalsegmentator_wrapper_mac.dcm2niix_source_build.v2",
    "architecture": "arm64",
    "artifact_directory": artifact_relative,
    "binary": "dcm2niix",
    "binary_sha256": expected_binary_sha256,
    "bundled_license": "licenses/dcm2niix-license.txt",
    "bundled_license_sha256": license_sha256,
    "expected_cli_version": expected_cli_version,
    "license_sha256": license_sha256,
    "minimum_macos": minimum_macos,
    "release_tag": release_tag,
    "source_archive_sha256": source_archive_sha256,
    "source_license_sha256": license_sha256,
    "source_url": source_url,
}
if set(receipt) != {*expected, "linkage", "source_date_epoch"}:
    raise SystemExit("artifact receipt has an unexpected schema")
for key, value in expected.items():
    if receipt.get(key) != value:
        raise SystemExit(f"artifact receipt {key!r} does not match the pinned build contract")
linkage = receipt.get("linkage")
if linkage != {
    "allowed_dependency_prefixes": ["/System/Library/", "/usr/lib/"],
    "result": "system-only-no-rpath",
    "rpaths": [],
}:
    raise SystemExit("artifact receipt linkage result is missing or invalid")
if receipt.get("source_date_epoch") != int(source_date_epoch):
    raise SystemExit("artifact receipt source_date_epoch does not match the pinned build contract")
PY
}

verify_artifact_directory() {
  local artifact_dir="$1"
  local artifact_relative="$2"
  local expected_binary_sha256="$3"
  require_owner_controlled_directory "${artifact_dir}" "dcm2niix artifact"
  verify_artifact_metadata \
    "${artifact_dir}" "${artifact_relative}" "${expected_binary_sha256}" \
    || die "dcm2niix artifact receipt or content verification failed: ${artifact_dir}"
  "${PYTHON_BIN}" "${MACHO_VERIFY_SCRIPT}" \
    --path "${artifact_dir}/dcm2niix" \
    --max-macos "${MINIMUM_MACOS_VERSION}" \
    --require-arm64 >/dev/null \
    || die "dcm2niix artifact does not meet the macOS ${MINIMUM_MACOS_VERSION} arm64 contract: ${artifact_dir}"
  "${PYTHON_BIN}" "${LINKAGE_VERIFY_SCRIPT}" \
    --path "${artifact_dir}/dcm2niix" >/dev/null \
    || die "dcm2niix artifact has unexpected macOS linkage: ${artifact_dir}"
  local version_output
  if ! version_output="$("${artifact_dir}/dcm2niix" -h 2>&1)"; then
    die "artifact dcm2niix did not run its help command: ${artifact_dir}"
  fi
  [[ "${version_output}" == *"${DCM2NIIX_EXPECTED_CLI_VERSION}"* ]] \
    || die "artifact has an unexpected dcm2niix CLI version: ${artifact_dir}"
}

verify_current_artifact() {
  [[ -e "${CURRENT_ARTIFACT_POINTER}" || -L "${CURRENT_ARTIFACT_POINTER}" ]] || return 1
  require_owner_controlled_regular_file \
    "${CURRENT_ARTIFACT_POINTER}" "current dcm2niix artifact pointer"
  local artifact_relative
  artifact_relative="$("${PYTHON_BIN}" - \
    "${CURRENT_ARTIFACT_POINTER}" \
    "${DCM2NIIX_RELEASE_TAG}" \
    "${DCM2NIIX_SOURCE_URL}" \
    "${DCM2NIIX_SOURCE_SHA256}" \
    "${DCM2NIIX_LICENSE_SHA256}" <<'PY'
import json
import re
import sys
from pathlib import Path

pointer, release_tag, source_url, source_archive_sha256, license_sha256 = sys.argv[1:]
try:
    data = json.loads(Path(pointer).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"current artifact pointer is invalid JSON: {exc}") from exc
binary_sha256 = data.get("binary_sha256")
artifact_directory = data.get("artifact_directory")
if not isinstance(binary_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", binary_sha256) is None:
    raise SystemExit("current artifact pointer binary_sha256 is invalid")
if artifact_directory != f"artifacts/{binary_sha256}":
    raise SystemExit("current artifact pointer does not name its content-addressed artifact")
expected = {
    "schema": "totalsegmentator_wrapper_mac.dcm2niix_current_artifact.v1",
    "release_tag": release_tag,
    "source_archive_sha256": source_archive_sha256,
    "license_sha256": license_sha256,
    "source_url": source_url,
}
if set(data) != {*expected, "artifact_directory", "binary_sha256"}:
    raise SystemExit("current artifact pointer has an unexpected schema")
for key, value in expected.items():
    if data.get(key) != value:
        raise SystemExit(f"current artifact pointer {key!r} does not match the pinned build contract")
print(artifact_directory)
PY
)" || die "current dcm2niix artifact pointer failed strict validation"
  local expected_binary_sha256="${artifact_relative#artifacts/}"
  local artifact_dir="${OUTPUT_DIR}/${artifact_relative}"
  verify_artifact_directory \
    "${artifact_dir}" "${artifact_relative}" "${expected_binary_sha256}"
  printf '%s\n' "${artifact_dir}/dcm2niix"
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  die "this builder is for macOS only"
fi
[[ -n "${PYTHON_BIN}" && -x "${PYTHON_BIN}" ]] \
  || die "Python 3 is required; set PYTHON_BIN to an executable interpreter"
for required_command in cmake xcrun otool shasum; do
  require_command "${required_command}"
done
for required_file in \
  "${FETCH_SCRIPT}" \
  "${MACHO_VERIFY_SCRIPT}" \
  "${LINKAGE_VERIFY_SCRIPT}" \
  "${BUNDLED_NOTICE}"; do
  [[ -f "${required_file}" ]] || die "required release input is missing: ${required_file}"
done

mkdir -p "${SOURCE_CACHE_DIR}" "${SOURCE_PARENT}" "${OUTPUT_DIR}" "${ARTIFACTS_DIR}"
require_owner_controlled_directory "${SOURCE_CACHE_DIR}" "source cache directory"
require_owner_controlled_directory "${SOURCE_PARENT}" "source extraction directory"
require_owner_controlled_directory "${OUTPUT_DIR}" "dcm2niix output directory"
require_owner_controlled_directory "${ARTIFACTS_DIR}" "dcm2niix artifact directory"
for legacy_output in \
  "${OUTPUT_DIR}/dcm2niix" \
  "${OUTPUT_DIR}/dcm2niix-build-provenance.json" \
  "${OUTPUT_DIR}/licenses"; do
  if [[ -e "${legacy_output}" || -L "${legacy_output}" ]]; then
    die "legacy loose dcm2niix output is not a valid atomic artifact; remove or preserve it outside ${OUTPUT_DIR} deliberately: ${legacy_output}"
  fi
done
if [[ -e "${CURRENT_ARTIFACT_POINTER}" || -L "${CURRENT_ARTIFACT_POINTER}" ]]; then
  CURRENT_ARTIFACT_BINARY="$(verify_current_artifact)" \
    || die "existing current dcm2niix artifact failed strict validation"
  echo "${CURRENT_ARTIFACT_BINARY}"
  exit 0
fi

SOURCE_BUILD_PARENT="$(mktemp -d "${SOURCE_PARENT}/.dcm2niix-source.XXXXXX")"
trap cleanup_staging EXIT
"${PYTHON_BIN}" "${FETCH_SCRIPT}" \
  --url "${DCM2NIIX_SOURCE_URL}" \
  --sha256 "${DCM2NIIX_SOURCE_SHA256}" \
  --archive "${SOURCE_ARCHIVE}" \
  --output-parent "${SOURCE_BUILD_PARENT}" \
  --expected-root "${DCM2NIIX_SOURCE_ROOT}" >/dev/null

SOURCE_DIR="${SOURCE_BUILD_PARENT}/${DCM2NIIX_SOURCE_ROOT}"
SOURCE_LICENSE="${SOURCE_DIR}/license.txt"
SOURCE_PROVENANCE="${SOURCE_DIR}/.source-archive-provenance.json"
[[ -d "${SOURCE_DIR}" && ! -L "${SOURCE_DIR}" ]] \
  || die "pinned source tree is missing or is a symlink: ${SOURCE_DIR}"
require_owner_controlled_regular_file \
  "${SOURCE_PROVENANCE}" "pinned source provenance receipt"
require_owner_controlled_regular_file "${SOURCE_LICENSE}" "upstream source license"
require_bsd_notice "${SOURCE_LICENSE}" "upstream source"
require_bsd_notice "${BUNDLED_NOTICE}" "bundled attribution"
require_expected_sha256 \
  "${SOURCE_LICENSE}" "${DCM2NIIX_LICENSE_SHA256}" "upstream source license"
require_expected_sha256 \
  "${BUNDLED_NOTICE}" "${DCM2NIIX_LICENSE_SHA256}" "bundled attribution"
if ! cmp -s "${SOURCE_LICENSE}" "${BUNDLED_NOTICE}"; then
  die "upstream license.txt differs from resources/third_party/licenses/dcm2niix-license.txt; update attribution deliberately before building"
fi

SDKROOT="$(xcrun --sdk macosx --show-sdk-path)"
[[ -d "${SDKROOT}" ]] || die "xcrun did not return a usable macOS SDK path"
BUILD_STAGING_PARENT="${OUTPUT_DIR}/.build-staging"
mkdir -p "${BUILD_STAGING_PARENT}"
require_owner_controlled_directory "${BUILD_STAGING_PARENT}" "fresh CMake build staging parent"
CMAKE_BUILD_DIR="$(mktemp -d "${BUILD_STAGING_PARENT}/.dcm2niix-cmake.XXXXXX")"
INSTALL_STAGING="$(mktemp -d "${OUTPUT_DIR}/.dcm2niix-install.XXXXXX")"

# Scrub package-discovery and linker variables that could silently make this
# build depend on Homebrew.  The linkage verifier below remains the release
# authority: a non-system dylib or LC_RPATH is a hard error.
REPRO_FLAGS="-O2 -ffile-prefix-map=${SOURCE_DIR}=. -fdebug-prefix-map=${SOURCE_DIR}=."
env \
  -u DESTDIR \
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
  MACOSX_DEPLOYMENT_TARGET="${MINIMUM_MACOS_VERSION}" \
  CMAKE_OSX_DEPLOYMENT_TARGET="${MINIMUM_MACOS_VERSION}" \
  CFLAGS="${REPRO_FLAGS}" \
  CXXFLAGS="${REPRO_FLAGS}" \
  LDFLAGS="" \
  CPPFLAGS="" \
  cmake -S "${SOURCE_DIR}" -B "${CMAKE_BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${INSTALL_STAGING}" \
    -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_OSX_DEPLOYMENT_TARGET="${MINIMUM_MACOS_VERSION}" \
    -DCMAKE_OSX_SYSROOT="${SDKROOT}" \
    -DCMAKE_SKIP_RPATH=ON \
    "-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local" \
    "-DCMAKE_SYSTEM_IGNORE_PATH=/opt/homebrew;/usr/local" \
    -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE \
    -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DBUILD_SHARED_LIBS=OFF

env \
  -u DESTDIR \
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
  -u CFLAGS \
  -u CXXFLAGS \
  -u LDFLAGS \
  -u CPPFLAGS \
  LC_ALL=C \
  LANG=C \
  TZ=UTC \
  SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH}" \
  SDKROOT="${SDKROOT}" \
  MACOSX_DEPLOYMENT_TARGET="${MINIMUM_MACOS_VERSION}" \
  CMAKE_OSX_DEPLOYMENT_TARGET="${MINIMUM_MACOS_VERSION}" \
  cmake --build "${CMAKE_BUILD_DIR}" --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-4}"

env \
  -u DESTDIR \
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
  -u CFLAGS \
  -u CXXFLAGS \
  -u LDFLAGS \
  -u CPPFLAGS \
  LC_ALL=C \
  LANG=C \
  TZ=UTC \
  SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH}" \
  SDKROOT="${SDKROOT}" \
  MACOSX_DEPLOYMENT_TARGET="${MINIMUM_MACOS_VERSION}" \
  CMAKE_OSX_DEPLOYMENT_TARGET="${MINIMUM_MACOS_VERSION}" \
  cmake --install "${CMAKE_BUILD_DIR}"

CANDIDATE="${INSTALL_STAGING}/bin/dcm2niix"
[[ -x "${CANDIDATE}" && ! -L "${CANDIDATE}" ]] \
  || die "CMake install did not create a regular executable at ${CANDIDATE}"
"${PYTHON_BIN}" "${MACHO_VERIFY_SCRIPT}" \
  --path "${CANDIDATE}" \
  --max-macos "${MINIMUM_MACOS_VERSION}" \
  --require-arm64
"${PYTHON_BIN}" "${LINKAGE_VERIFY_SCRIPT}" --path "${CANDIDATE}"

if ! VERSION_OUTPUT="$("${CANDIDATE}" -h 2>&1)"; then
  die "newly built dcm2niix did not run its help command"
fi
if [[ "${VERSION_OUTPUT}" != *"${DCM2NIIX_EXPECTED_CLI_VERSION}"* ]]; then
  die "unexpected dcm2niix CLI version; expected ${DCM2NIIX_EXPECTED_CLI_VERSION} from ${DCM2NIIX_RELEASE_TAG}"
fi

NEW_BINARY_SHA256="$(sha256_file "${CANDIDATE}")"
ARTIFACT_RELATIVE="artifacts/${NEW_BINARY_SHA256}"
ARTIFACT_DIR="${OUTPUT_DIR}/${ARTIFACT_RELATIVE}"
PUBLISH_LOCK="${ARTIFACTS_DIR}/.dcm2niix-publish-lock"
if ! mkdir "${PUBLISH_LOCK}"; then
  die "another dcm2niix artifact publication is active or a prior crash left a lock: ${PUBLISH_LOCK}"
fi
PUBLISH_LOCK_ACQUIRED=1
require_owner_controlled_directory "${PUBLISH_LOCK}" "dcm2niix publication lock"
if [[ -e "${CURRENT_ARTIFACT_POINTER}" || -L "${CURRENT_ARTIFACT_POINTER}" ]]; then
  die "a current dcm2niix artifact pointer appeared during this build; refuse to replace it: ${CURRENT_ARTIFACT_POINTER}"
fi

if [[ -e "${ARTIFACT_DIR}" || -L "${ARTIFACT_DIR}" ]]; then
  [[ -d "${ARTIFACT_DIR}" && ! -L "${ARTIFACT_DIR}" ]] \
    || die "content-addressed dcm2niix artifact path is not a directory: ${ARTIFACT_DIR}"
  verify_artifact_directory \
    "${ARTIFACT_DIR}" "${ARTIFACT_RELATIVE}" "${NEW_BINARY_SHA256}"
else
  ARTIFACT_STAGING="$(mktemp -d "${ARTIFACTS_DIR}/.dcm2niix-artifact.${NEW_BINARY_SHA256:0:12}.XXXXXX")"
  mkdir -p "${ARTIFACT_STAGING}/licenses"
  cp "${CANDIDATE}" "${ARTIFACT_STAGING}/dcm2niix"
  chmod 755 "${ARTIFACT_STAGING}/dcm2niix"
  cp "${BUNDLED_NOTICE}" "${ARTIFACT_STAGING}/licenses/dcm2niix-license.txt"
  chmod 644 "${ARTIFACT_STAGING}/licenses/dcm2niix-license.txt"

  "${PYTHON_BIN}" - \
    "${ARTIFACT_STAGING}/dcm2niix-build-provenance.json" \
    "${ARTIFACT_RELATIVE}" \
    "${DCM2NIIX_RELEASE_TAG}" \
    "${DCM2NIIX_EXPECTED_CLI_VERSION}" \
    "${DCM2NIIX_SOURCE_URL}" \
    "${DCM2NIIX_SOURCE_SHA256}" \
    "${DCM2NIIX_LICENSE_SHA256}" \
    "${MINIMUM_MACOS_VERSION}" \
    "${SOURCE_DATE_EPOCH}" \
    "${NEW_BINARY_SHA256}" <<'PY'
import json
import sys
from pathlib import Path

(
    output,
    artifact_directory,
    release_tag,
    expected_cli_version,
    source_url,
    source_archive_sha256,
    license_sha256,
    minimum_macos,
    source_date_epoch,
    binary_sha256,
) = sys.argv[1:]
Path(output).write_text(
    json.dumps(
        {
            "schema": "totalsegmentator_wrapper_mac.dcm2niix_source_build.v2",
            "architecture": "arm64",
            "artifact_directory": artifact_directory,
            "binary": "dcm2niix",
            "binary_sha256": binary_sha256,
            "bundled_license": "licenses/dcm2niix-license.txt",
            "bundled_license_sha256": license_sha256,
            "expected_cli_version": expected_cli_version,
            "license_sha256": license_sha256,
            "linkage": {
                "allowed_dependency_prefixes": ["/System/Library/", "/usr/lib/"],
                "result": "system-only-no-rpath",
                "rpaths": [],
            },
            "minimum_macos": minimum_macos,
            "release_tag": release_tag,
            "source_archive_sha256": source_archive_sha256,
            "source_date_epoch": int(source_date_epoch),
            "source_license_sha256": license_sha256,
            "source_url": source_url,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
  verify_artifact_directory \
    "${ARTIFACT_STAGING}" "${ARTIFACT_RELATIVE}" "${NEW_BINARY_SHA256}"
  mv "${ARTIFACT_STAGING}" "${ARTIFACT_DIR}"
  ARTIFACT_STAGING=""
  verify_artifact_directory \
    "${ARTIFACT_DIR}" "${ARTIFACT_RELATIVE}" "${NEW_BINARY_SHA256}"
fi

TEMP_POINTER="$(mktemp "${OUTPUT_DIR}/.current-artifact.XXXXXX")"
"${PYTHON_BIN}" - \
  "${TEMP_POINTER}" \
  "${ARTIFACT_RELATIVE}" \
  "${NEW_BINARY_SHA256}" \
  "${DCM2NIIX_RELEASE_TAG}" \
  "${DCM2NIIX_SOURCE_URL}" \
  "${DCM2NIIX_SOURCE_SHA256}" \
  "${DCM2NIIX_LICENSE_SHA256}" <<'PY'
import json
import sys
from pathlib import Path

(
    output,
    artifact_directory,
    binary_sha256,
    release_tag,
    source_url,
    source_archive_sha256,
    license_sha256,
) = sys.argv[1:]
Path(output).write_text(
    json.dumps(
        {
            "schema": "totalsegmentator_wrapper_mac.dcm2niix_current_artifact.v1",
            "artifact_directory": artifact_directory,
            "binary_sha256": binary_sha256,
            "license_sha256": license_sha256,
            "release_tag": release_tag,
            "source_archive_sha256": source_archive_sha256,
            "source_url": source_url,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
mv "${TEMP_POINTER}" "${CURRENT_ARTIFACT_POINTER}"
CURRENT_ARTIFACT_BINARY="$(verify_current_artifact)"
echo "${CURRENT_ARTIFACT_BINARY}"
