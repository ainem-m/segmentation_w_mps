#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR:-${ROOT}/dist}"
if [[ "${DIST_DIR}" != "/" ]]; then
  DIST_DIR="${DIST_DIR%/}"
fi
APP_NAME="TotalSegmentator Wrapper for Mac"
CANONICAL_BUNDLE_IDENTIFIER="jp.chino.totalsegmentator.wrapper.mac"
APP_DIR="${DIST_DIR}/${APP_NAME}.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
PYTHON_BIN="${PYTHON_BIN:-${ROOT}/.venv/bin/python}"
SWIFT_APP_SOURCE_DIR="${ROOT}/native/macos/TotalSegmentatorWrapperForMac"
SWIFT_SOURCE_FILES=(
  "${SWIFT_APP_SOURCE_DIR}/CommandBuilder.swift"
  "${SWIFT_APP_SOURCE_DIR}/ProcessSupport.swift"
  "${SWIFT_APP_SOURCE_DIR}/AppState.swift"
  "${SWIFT_APP_SOURCE_DIR}/Views.swift"
  "${SWIFT_APP_SOURCE_DIR}/TotalSegmentatorWrapperForMacApp.swift"
)
SWIFT_MODULE_CACHE_PATH="${TOTALSEGMENTATOR_WRAPPER_MAC_SWIFT_MODULE_CACHE_PATH:-${DIST_DIR}/swift_module_cache}"
PYTHON_RUNTIME_EXPLICIT_SOURCE="${TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_PYTHON_RUNTIME_DIR:-${PYTHON_RUNTIME_DIR:-}}"
PYTHON_RUNTIME_SOURCE=""
PYTHON_RUNTIME_INPUT_KIND="none"
PYTHON_RUNTIME_STRATEGY="bundled_python312"
PYTHON_RUNTIME_EXECUTABLE_JSON='"python/cpython-3.12/bin/python3.12"'
PYTHON_RUNTIME_BUNDLED_JSON="true"
PYTHON_RUNTIME_BUNDLE_JSON='"python/cpython-3.12"'
PYTHON_RUNTIME_FINGERPRINT_SCRIPT="${ROOT}/scripts/python_runtime_fingerprint.py"
MACHO_DEPLOYMENT_VERIFY_SCRIPT="${ROOT}/scripts/verify_macos_deployment_target.py"
MACHO_LINKAGE_VERIFY_SCRIPT="${ROOT}/scripts/verify_macos_binary_linkage.py"
DCM2NIIX_SOURCE_ARTIFACT_VERIFY_SCRIPT="${ROOT}/scripts/verify_dcm2niix_source_artifact.py"
DICOM_NORMALIZER_ARTIFACT_VERIFY_SCRIPT="${ROOT}/scripts/verify_dicom_normalizer_artifact.py"
RELEASE_INPUT_READINESS_SCRIPT="${ROOT}/scripts/verify_release_input_readiness.py"
RELEASE_BUILD_TOOLCHAIN_SCRIPT="${ROOT}/scripts/release_build_toolchain.py"
RELEASE_COMPONENT_BUILD_RUNNER="${ROOT}/scripts/run_release_component_build.sh"
OFFLINE_DEPENDENCY_WHEELHOUSE_SCRIPT="${ROOT}/scripts/build_offline_dependency_wheelhouse.py"
OFFLINE_DEPENDENCY_WHEELHOUSE_ROOT="${TOTALSEGMENTATOR_WRAPPER_MAC_OFFLINE_DEPENDENCY_WHEELHOUSE:-${ROOT}/build/offline-dependency-wheelhouse}"
OFFLINE_DEPENDENCY_WHEEL_DIRECTORY="${OFFLINE_DEPENDENCY_WHEELHOUSE_ROOT}/wheels"
OFFLINE_DEPENDENCY_WHEELHOUSE_MANIFEST_PATH="${OFFLINE_DEPENDENCY_WHEELHOUSE_ROOT}/manifest.json"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
PYTHON_RUNTIME_FINGERPRINT=""
PYTHON_RUNTIME_FINGERPRINT_SCOPE=""
PYTHON_RUNTIME_BUILD_ID_PROVENANCE="external-python"
REQUIREMENTS_LOCK_SHA256_JSON="null"
DEPENDENCY_LOCK_METADATA_SHA256_JSON="null"
REQUIREMENTS_LOCK_BUNDLED_JSON="null"
DEPENDENCY_LOCK_METADATA_BUNDLED_JSON="null"
DEPENDENCY_WHEELHOUSE_MANIFEST_SHA256=""
DEPENDENCY_WHEELHOUSE_MANIFEST_SHA256_JSON="null"
DEPENDENCY_WHEELHOUSE_MANIFEST_BUNDLED_JSON="null"
PROJECT_FILE_PATH="${ROOT}/pyproject.toml"
PROJECT_FILE_SHA256=""
PROJECT_FILE_SHA256_JSON="null"
PROJECT_FILE_BUNDLED_JSON="null"
RELEASE_DEPENDENCY_LOCK_ATTESTED="0"
RELEASE_BUILD_TOOLCHAIN_ATTESTED="0"
RELEASE_BUILD_TOOLCHAIN_LOCK_PATH="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_LOCK:-${ROOT}/constraints/macos-arm64-py312.release-build-toolchain.lock}"
RELEASE_BUILD_TOOLCHAIN_METADATA_PATH="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_METADATA:-${ROOT}/constraints/macos-arm64-py312.release-build-toolchain.lock.json}"
RELEASE_BUILD_TOOLCHAIN_WHEELHOUSE="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_WHEELHOUSE:-${ROOT}/build/release-build-toolchain/wheels}"
RELEASE_BUILD_TOOLCHAIN_WORK_DIRECTORY="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_WORK_DIRECTORY:-${ROOT}/build/release-build-toolchain/work}"
RELEASE_BUILD_TOOLCHAIN_RECEIPT_PATH="${TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_RECEIPT:-${ROOT}/build/release-build-toolchain/release-build-toolchain-receipt.json}"
RELEASE_BUILD_TOOLCHAIN_PREPARED_PYTHON=""
RELEASE_BUILD_TOOLCHAIN_LOCK_SHA256=""
RELEASE_BUILD_TOOLCHAIN_METADATA_SHA256=""
RELEASE_BUILD_TOOLCHAIN_RECEIPT_SHA256=""
RELEASE_BUILD_TOOLCHAIN_LOCK_SHA256_JSON="null"
RELEASE_BUILD_TOOLCHAIN_METADATA_SHA256_JSON="null"
RELEASE_BUILD_TOOLCHAIN_RECEIPT_SHA256_JSON="null"
RELEASE_BUILD_TOOLCHAIN_PROVENANCE_JSON="null"
RELEASE_BUILD_TOOLCHAIN_LOCK_BUNDLED_JSON="null"
RELEASE_BUILD_TOOLCHAIN_METADATA_BUNDLED_JSON="null"
RELEASE_BUILD_TOOLCHAIN_RECEIPT_BUNDLED_JSON="null"
FPSAMPLE_PRE_SIGN_WHEEL_SHA256=""
FPSAMPLE_PRE_SIGN_WHEEL_SHA256_JSON="null"
ACVL_UTILS_RESOLUTION_INPUT_SHA256=""
BUNDLED_PYTHON_RUNTIME_ROOT=""
APP_VERSION_OVERRIDE="${TOTALSEGMENTATOR_WRAPPER_MAC_APP_VERSION:-}"
APP_VERSION=""
MINIMUM_MACOS_VERSION="14.0"
SOURCE_COMMIT=""
SOURCE_TREE_DIRTY_JSON="false"
BUILD_ID="${TOTALSEGMENTATOR_WRAPPER_MAC_BUILD_ID:-}"
DEPENDENCY_SET_ID="${TOTALSEGMENTATOR_WRAPPER_MAC_DEPENDENCY_SET_ID:-macos-arm64-py312-torch2.12-totalseg2.14.0-nnunetv2.8.1-pydicom3-gdcm3.2-toothseg-acvl0.2.6-bundled-scipy1-iosmesh-open3d0.19-fastsimp0.1.13-fpsample1.0.2-bundled}"
UPDATE_MANIFEST_URL="${TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_MANIFEST_URL:-}"
UPDATE_ALLOWED_HOSTS="${TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_ALLOWED_HOSTS:-}"
CANONICAL_UPDATE_MANIFEST_URL="https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable-v2/update.json"
CANONICAL_UPDATE_HOST="downloads.lacramy.com"
XCODE_DEVELOPER_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_XCODE_DEVELOPER_DIR:-}"
DCM2NIIX_EXPLICIT_PATH="${TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX:-}"
DCM2NIIX_BUILD_ROOT="${TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX_BUILD_DIR:-${ROOT}/build/dcm2niix-macos14-arm64}"
DCM2NIIX_PATH=""
DCM2NIIX_LICENSE_SOURCE_PATH=""
DCM2NIIX_BUILD_RECEIPT_PATH=""
DCM2NIIX_ARTIFACT_POINTER_PATH=""
DCM2NIIX_BUNDLED_RECEIPT_JSON="null"
DCM2NIIX_BUNDLED_POINTER_JSON="null"
DCM2NIIX_PROVENANCE_NOTICE=""
NORMALIZER_SOURCE_JSON="null"
NORMALIZER_ARTIFACT_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_ARTIFACT_DIR:-${ROOT}/build/dicom_normalizer-macos14-arm64}"
NORMALIZER_PATH="${NORMALIZER_ARTIFACT_DIR}/totalsegmentator-wrapper-dicom-normalizer"
NORMALIZER_BUILD_RECEIPT_PATH="${NORMALIZER_ARTIFACT_DIR}/dicom-normalizer-build-provenance.json"
GDCM_BUILD_RECEIPT_PATH="${NORMALIZER_ARTIFACT_DIR}/gdcm-build-provenance.json"
SIGNING_MODE="${TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE:-ad-hoc}"
CODESIGN_IDENTITY="${TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY:-}"
BUNDLE_IDENTIFIER="${TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER:-${CANONICAL_BUNDLE_IDENTIFIER}}"
NOTARY_PROFILE="${TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_PROFILE:-}"
TEAM_IDENTIFIER="${TOTALSEGMENTATOR_WRAPPER_MAC_TEAM_IDENTIFIER:-}"
APP_ENTITLEMENTS="${ROOT}/resources/entitlements/app.entitlements"
PYTHON_ENTITLEMENTS="${ROOT}/resources/entitlements/python-runtime.entitlements"
WRAPPER_LICENSE_PATH="${ROOT}/LICENSE"
WRAPPER_NOTICE_PATH="${ROOT}/NOTICE"
TOTALSEGMENTATOR_LICENSE_PATH="${ROOT}/resources/third_party/licenses/TotalSegmentator-Apache-2.0.txt"
TOTALSEGMENTATOR_TASK_INVENTORY_PATH="${ROOT}/resources/third_party/totalsegmentator_task_inventory.json"
DENTALSEG_NOTICE_PATH="${ROOT}/resources/third_party/licenses/DentalSegmentator-NOTICE.txt"
TOOTHSEG_NOTICE_PATH="${ROOT}/resources/third_party/licenses/ToothSeg-NOTICE.txt"
MESHSEGNET_NOTICE_PATH="${ROOT}/resources/third_party/licenses/MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt"
TGNET_NOTICE_PATH="${ROOT}/resources/third_party/licenses/TGNet-User-Provided-Checkpoint-NOTICE.txt"
FPSAMPLE_NOTICE_PATH="${ROOT}/resources/third_party/licenses/fpsample-1.0.2-MIT-and-nanoflann-BSD.txt"
SETUP_WEIGHTS_MANIFEST_PATH="${ROOT}/src/totalsegmentator_wrapper_mac/totalseg_setup_weights_manifest.json"
CONSTRAINTS_PATH="${ROOT}/constraints/macos-arm64-py312.txt"
REQUIREMENTS_LOCK_PATH="${ROOT}/constraints/macos-arm64-py312.requirements.lock"
DEPENDENCY_LOCK_METADATA_PATH="${ROOT}/constraints/macos-arm64-py312.lock.json"
DCM2NIIX_LICENSE_PATH="${ROOT}/resources/third_party/licenses/dcm2niix-license.txt"
DICOM_RUNTIME_LICENSE_NAMES=(
  "GDCM-BSD-3-Clause.txt"
  "GDCM-IJG-JPEG-README.txt"
  "OpenJPEG-BSD-2-Clause.txt"
  "CharLS-BSD-3-Clause.txt"
  "Expat-MIT.txt"
  "zlib-Zlib.txt"
  "GDCM-UUID-BSD-3-Clause.txt"
  "GDCM-static-license-inventory.json"
)
LICENSE_MANUAL_OVERRIDES_PATH="${ROOT}/resources/third_party/licenses/manual-overrides.json"
LICENSE_INVENTORY_SCRIPT="${ROOT}/scripts/generate_third_party_license_inventory.py"
LICENSE_INVENTORY_ENV_DIR="${DIST_DIR}/license_inventory_env"
LICENSE_SITE_PACKAGES="${TOTALSEGMENTATOR_WRAPPER_MAC_LICENSE_SITE_PATH:-}"
ALLOW_DEVELOPMENT_LICENSE_INVENTORY="${TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_DEVELOPMENT_LICENSE_INVENTORY:-0}"
LICENSE_INVENTORY_MODE=""
LICENSE_INVENTORY_RELEASE_ELIGIBLE_JSON="false"

json_string() {
  "${PYTHON_BIN}" -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"
}

json_string_list() {
  "${PYTHON_BIN}" -c 'import json, sys; print(json.dumps([part.strip() for part in sys.argv[1].split(",") if part.strip()]))' "$1"
}

sha256_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

require_sha256_digest() {
  local value="$1"
  local label="$2"
  if [[ ! "${value}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "${label} must be a lowercase SHA-256 digest; got ${value:-empty}" >&2
    exit 2
  fi
}

prepare_owned_dist_directory() {
  if [[ "${DIST_DIR}" != /* || "${DIST_DIR}" == "/" ]]; then
    echo "Distribution directory must be a specific absolute path, not ${DIST_DIR:-empty}." >&2
    exit 2
  fi
  local resolved
  resolved="$("${PYTHON_BIN}" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=False))' "${DIST_DIR}")"
  if [[ "${resolved}" == "/" || -L "${DIST_DIR}" ]]; then
    echo "Distribution directory must resolve to a specific non-symlink location: ${DIST_DIR}" >&2
    exit 2
  fi
  if [[ -e "${DIST_DIR}" && ! -d "${DIST_DIR}" ]]; then
    echo "Distribution path is not a directory: ${DIST_DIR}" >&2
    exit 2
  fi
  if [[ ! -e "${DIST_DIR}" ]]; then
    local parent
    parent="$(dirname "${DIST_DIR}")"
    if [[ ! -d "${parent}" || -L "${parent}" || ! -O "${parent}" ]]; then
      echo "Distribution parent must be an owner-controlled non-symlink directory: ${parent}" >&2
      exit 2
    fi
    mkdir "${DIST_DIR}"
  fi
  if [[ ! -d "${DIST_DIR}" || -L "${DIST_DIR}" || ! -O "${DIST_DIR}" ]]; then
    echo "Distribution directory must be owner-controlled and non-symlink: ${DIST_DIR}" >&2
    exit 2
  fi
}

validate_owned_dist_child_directory_if_present() {
  local candidate="$1"
  local expected_name="$2"
  if [[ ! -e "${candidate}" && ! -L "${candidate}" ]]; then
    return 0
  fi
  if [[ "$(dirname "${candidate}")" != "${DIST_DIR}" \
    || "$(basename "${candidate}")" != "${expected_name}" \
    || ! -d "${candidate}" \
    || -L "${candidate}" \
    || ! -O "${candidate}" ]]; then
    echo "Refusing to modify an unsafe distribution directory: ${candidate}" >&2
    exit 2
  fi
}

remove_owned_dist_child_directory() {
  local candidate="$1"
  local expected_name="$2"
  validate_owned_dist_child_directory_if_present "${candidate}" "${expected_name}"
  if [[ -d "${candidate}" ]]; then
    find "${candidate}" -type d -exec chmod u+rwx {} +
    find "${candidate}" -type f -exec chmod u+rw {} +
    rm -rf "${candidate}"
  fi
}

remove_verified_runtime_smoke_dir() {
  local candidate="$1"
  local parent="${2%/}"
  local expected_prefix="${parent}/totalsegmentator-wrapper-python-runtime-smoke."
  if [[ -z "${candidate}" || "${candidate}" != "${expected_prefix}"* ]]; then
    echo "Refusing to remove an unexpected Python runtime smoke directory: ${candidate:-empty}" >&2
    return 1
  fi
  if [[ "$(dirname "${candidate}")" != "${parent}" || ! -d "${candidate}" || -L "${candidate}" || ! -O "${candidate}" ]]; then
    echo "Refusing to remove an unverified Python runtime smoke directory: ${candidate}" >&2
    return 1
  fi
  rm -rf "${candidate}"
}

run_isolated_runtime_command() {
  local home_dir="$1"
  local temp_dir="$2"
  shift 2
  env -i \
    PATH="/usr/bin:/bin" \
    HOME="${home_dir}" \
    TMPDIR="${temp_dir}" \
    PIP_CACHE_DIR="${temp_dir}/pip-cache" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INDEX=1 \
    PIP_NO_INPUT=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    "$@"
}

run_isolated_inventory_python() {
  local python_executable="$1"
  shift
  env -i \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    HOME="${LICENSE_INVENTORY_ENV_DIR}/home" \
    TMPDIR="${LICENSE_INVENTORY_ENV_DIR}/tmp" \
    PIP_CONFIG_FILE="/dev/null" \
    PIP_CACHE_DIR="${LICENSE_INVENTORY_ENV_DIR}/pip-cache" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    "${python_executable}" -I "$@"
}

run_isolated_inventory_pip() {
  local python_executable="$1"
  shift
  run_isolated_inventory_python "${python_executable}" -m pip --isolated "$@"
}

verify_and_copy_offline_dependency_wheels() {
  local destination_directory="$1"
  "${PYTHON_BIN}" - \
    "${OFFLINE_DEPENDENCY_WHEELHOUSE_ROOT}" \
    "${destination_directory}" \
    "${DEPENDENCY_WHEELHOUSE_MANIFEST_SHA256}" <<'PY'
from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


artifact_root = Path(sys.argv[1])
destination = Path(sys.argv[2])
expected_manifest_sha256 = sys.argv[3]
manifest_path = artifact_root / "manifest.json"
wheel_directory = artifact_root / "wheels"

if artifact_root.is_symlink() or not artifact_root.is_dir():
    raise SystemExit("offline dependency wheelhouse root must be a regular directory")
manifest_stat = manifest_path.lstat()
wheel_directory_stat = wheel_directory.lstat()
if stat.S_ISLNK(manifest_stat.st_mode) or not stat.S_ISREG(manifest_stat.st_mode):
    raise SystemExit("offline dependency wheelhouse manifest must be a regular file")
if stat.S_ISLNK(wheel_directory_stat.st_mode) or not stat.S_ISDIR(wheel_directory_stat.st_mode):
    raise SystemExit("offline dependency wheelhouse wheels must be a regular directory")
if sha256_file(manifest_path) != expected_manifest_sha256:
    raise SystemExit("offline dependency wheelhouse manifest changed after verification")
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
entries = manifest.get("wheels")
if not isinstance(entries, list) or not entries:
    raise SystemExit("offline dependency wheelhouse manifest has no wheel inventory")
if destination.is_symlink() or not destination.is_dir():
    raise SystemExit("offline dependency wheel destination must be a regular directory")
if any(destination.iterdir()):
    raise SystemExit("offline dependency wheel destination must start empty")

copied_names: set[str] = set()
for entry in entries:
    if not isinstance(entry, dict):
        raise SystemExit("offline dependency wheelhouse manifest contains an invalid entry")
    filename = entry.get("filename")
    distribution = entry.get("distribution")
    expected_sha256 = entry.get("sha256")
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not filename.endswith(".whl")
        or filename in copied_names
        or not isinstance(distribution, str)
        or not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
    ):
        raise SystemExit("offline dependency wheelhouse manifest entry is unsafe")
    source = wheel_directory / filename
    source_stat = source.lstat()
    if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISREG(source_stat.st_mode):
        raise SystemExit(f"offline dependency wheel is not a regular file: {filename}")
    source_sha256 = sha256_file(source)
    if source_sha256 != expected_sha256:
        raise SystemExit(f"offline dependency wheel SHA-256 changed: {filename}")
    target = destination / filename
    shutil.copyfile(source, target, follow_symlinks=False)
    if sha256_file(source) != source_sha256 or sha256_file(target) != source_sha256:
        raise SystemExit(f"offline dependency wheel changed while copying: {filename}")
    copied_names.add(filename)

source_names = {path.name for path in wheel_directory.iterdir()}
destination_names = {path.name for path in destination.iterdir()}
if source_names != copied_names or destination_names != copied_names:
    raise SystemExit("offline dependency wheelhouse contains an unsealed or uncopied member")
if sha256_file(manifest_path) != expected_manifest_sha256:
    raise SystemExit("offline dependency wheelhouse manifest changed while copying")
PY
}

verify_copied_python_runtime_smoke() {
  local runtime_root="$1"
  local runtime_python="${runtime_root}/bin/python3.12"
  local smoke_parent="${TMPDIR:-/tmp}"
  local smoke_dir=""
  if [[ ! -x "${runtime_python}" ]]; then
    echo "Copied Python runtime is missing an executable bin/python3.12: ${runtime_python}" >&2
    return 1
  fi
  if [[ "${smoke_parent}" != /* || ! -d "${smoke_parent}" ]]; then
    echo "TMPDIR must be an existing absolute directory for the copied Python smoke test: ${smoke_parent}" >&2
    return 1
  fi
  smoke_parent="${smoke_parent%/}"
  smoke_dir="$(mktemp -d "${smoke_parent}/totalsegmentator-wrapper-python-runtime-smoke.XXXXXX")" || {
    echo "Could not create a temporary directory for the copied Python runtime smoke test." >&2
    return 1
  }
  if [[ ! -d "${smoke_dir}" || -L "${smoke_dir}" || ! -O "${smoke_dir}" ]]; then
    echo "Copied Python runtime smoke directory failed ownership/type validation: ${smoke_dir}" >&2
    return 1
  fi
  mkdir -p "${smoke_dir}/home" "${smoke_dir}/tmp"

  if ! run_isolated_runtime_command "${smoke_dir}/home" "${smoke_dir}/tmp" \
    "${runtime_python}" -I -B "${PYTHON_RUNTIME_FINGERPRINT_SCRIPT}" \
    --runtime-root "${runtime_root}" --check-self-contained; then
    echo "Copied Python runtime isolation check failed; retained ${smoke_dir} for diagnosis." >&2
    return 1
  fi
  if ! run_isolated_runtime_command "${smoke_dir}/home" "${smoke_dir}/tmp" \
    "${runtime_python}" -I -B -m ensurepip --version; then
    echo "Copied Python runtime ensurepip check failed; retained ${smoke_dir} for diagnosis." >&2
    return 1
  fi
  if ! run_isolated_runtime_command "${smoke_dir}/home" "${smoke_dir}/tmp" \
    "${runtime_python}" -I -B -m venv "${smoke_dir}/venv"; then
    echo "Copied Python runtime venv creation failed; retained ${smoke_dir} for diagnosis." >&2
    return 1
  fi
  if ! run_isolated_runtime_command "${smoke_dir}/home" "${smoke_dir}/tmp" \
    "${smoke_dir}/venv/bin/python" -I -B "${PYTHON_RUNTIME_FINGERPRINT_SCRIPT}" \
    --runtime-root "${runtime_root}" --venv-root "${smoke_dir}/venv" --check-venv-base; then
    echo "Copied Python runtime venv base check failed; retained ${smoke_dir} for diagnosis." >&2
    return 1
  fi
  if ! run_isolated_runtime_command "${smoke_dir}/home" "${smoke_dir}/tmp" \
    "${smoke_dir}/venv/bin/python" -I -B -m pip --version; then
    echo "Copied Python runtime pip check failed; retained ${smoke_dir} for diagnosis." >&2
    return 1
  fi
  remove_verified_runtime_smoke_dir "${smoke_dir}" "${smoke_parent}"
}

first_json_line() {
  "${PYTHON_BIN}" -c 'import json, sys; print(json.dumps(next((line.strip() for line in sys.stdin if line.strip()), "")))'
}

require_release_python_runtime() {
  if [[ -n "${PYTHON_RUNTIME_EXPLICIT_SOURCE}" ]]; then
    PYTHON_RUNTIME_SOURCE="${PYTHON_RUNTIME_EXPLICIT_SOURCE%/}"
    PYTHON_RUNTIME_INPUT_KIND="explicit-runtime"
  else
    PYTHON_RUNTIME_SOURCE="$("${PYTHON_BIN}" -c 'import sys; print(sys.base_prefix)')"
    PYTHON_RUNTIME_INPUT_KIND="python-base-prefix"
  fi

  PYTHON_RUNTIME_SOURCE="${PYTHON_RUNTIME_SOURCE%/}"
  if [[ ! -d "${PYTHON_RUNTIME_SOURCE}" || -L "${PYTHON_RUNTIME_SOURCE}" \
    || ! -f "${PYTHON_RUNTIME_SOURCE}/bin/python3.12" \
    || -L "${PYTHON_RUNTIME_SOURCE}/bin/python3.12" \
    || ! -x "${PYTHON_RUNTIME_SOURCE}/bin/python3.12" ]]; then
    echo "Selected Python runtime must be a flat, non-symlink runtime root containing regular executable bin/python3.12." >&2
    exit 2
  fi
  if ! "${PYTHON_RUNTIME_SOURCE}/bin/python3.12" -I -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'; then
    echo "Selected Python runtime executable must report Python 3.12." >&2
    exit 2
  fi
}

require_developer_id_signing() {
  if [[ "${SIGNING_MODE}" != "ad-hoc" && "${SIGNING_MODE}" != "developer-id" ]]; then
    echo "TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE must be ad-hoc or developer-id; got ${SIGNING_MODE}" >&2
    exit 2
  fi
  if [[ "${SIGNING_MODE}" != "developer-id" ]]; then
    if [[ "${TOTALSEGMENTATOR_WRAPPER_MAC_NOTARIZED:-0}" == "1" ]]; then
      echo "A notarized manifest cannot be produced from an ad-hoc build." >&2
      exit 2
    fi
    return
  fi
  if [[ -z "${CODESIGN_IDENTITY}" ]]; then
    echo "TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY is required when TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE=developer-id." >&2
    exit 2
  fi
  if [[ "${BUNDLE_IDENTIFIER}" != "${CANONICAL_BUNDLE_IDENTIFIER}" ]]; then
    echo "Developer ID builds require the canonical bundle identifier ${CANONICAL_BUNDLE_IDENTIFIER}; got ${BUNDLE_IDENTIFIER:-empty}." >&2
    exit 2
  fi
  if [[ "${UPDATE_MANIFEST_URL}" != "${CANONICAL_UPDATE_MANIFEST_URL}" ]]; then
    echo "Developer ID builds require the canonical stable-v2 update manifest: ${CANONICAL_UPDATE_MANIFEST_URL}" >&2
    exit 2
  fi
  if [[ "${UPDATE_ALLOWED_HOSTS}" != "${CANONICAL_UPDATE_HOST}" ]]; then
    echo "Developer ID builds require UPDATE_ALLOWED_HOSTS=${CANONICAL_UPDATE_HOST}; got ${UPDATE_ALLOWED_HOSTS:-empty}" >&2
    exit 2
  fi
  if [[ ! "${TEAM_IDENTIFIER}" =~ ^[A-Z0-9]{10}$ ]]; then
    echo "TOTALSEGMENTATOR_WRAPPER_MAC_TEAM_IDENTIFIER must be the 10-character Developer ID Team ID." >&2
    exit 2
  fi
  if [[ ! -f "${APP_ENTITLEMENTS}" || ! -f "${PYTHON_ENTITLEMENTS}" ]]; then
    echo "Developer ID signing entitlements are missing under resources/entitlements." >&2
    exit 2
  fi
  if ! security find-identity -v -p codesigning | grep -F "${CODESIGN_IDENTITY}" >/dev/null 2>&1; then
    echo "Developer ID codesigning identity not found in keychain: ${CODESIGN_IDENTITY}" >&2
    exit 2
  fi
}

require_full_xcode() {
  local developer_dir
  if [[ -z "${XCODE_DEVELOPER_DIR}" && -d "/Applications/Xcode.app/Contents/Developer" ]]; then
    XCODE_DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
  fi
  if [[ -n "${XCODE_DEVELOPER_DIR}" ]]; then
    developer_dir="${XCODE_DEVELOPER_DIR}"
  else
    developer_dir="$(DEVELOPER_DIR='' /usr/bin/xcode-select -p 2>/dev/null || true)"
  fi
  if [[ -z "${developer_dir}" || "${developer_dir}" != /* || ! -d "${developer_dir}" || -L "${developer_dir}" || "${developer_dir}" == *CommandLineTools* ]]; then
    echo "Full Xcode must be selected to build the SwiftUI app frontend. Command Line Tools alone are not enough." >&2
    echo "Set TOTALSEGMENTATOR_WRAPPER_MAC_XCODE_DEVELOPER_DIR to a full Xcode Contents/Developer directory or select full Xcode for this shell." >&2
    exit 2
  fi
  export DEVELOPER_DIR="${developer_dir}"
  if ! /usr/bin/xcodebuild -version >/dev/null 2>&1; then
    echo "Full Xcode is required to build the SwiftUI app frontend." >&2
    echo "Install Xcode and select it before running this build script." >&2
    exit 2
  fi
}

require_release_source_unchanged() {
  local current_commit current_status
  current_commit="$(git -C "${ROOT}" rev-parse HEAD)"
  current_status="$(git -C "${ROOT}" status --porcelain=v1 --untracked-files=all)"
  if [[ "${current_commit}" != "${SOURCE_COMMIT}" ]]; then
    echo "Source HEAD changed during the build; refusing to package mixed provenance." >&2
    exit 2
  fi
  if [[ -n "${current_status}" ]]; then
    SOURCE_TREE_DIRTY_JSON="true"
  fi
  if [[ "${SIGNING_MODE}" == "developer-id" && "${SOURCE_TREE_DIRTY_JSON}" != "false" ]]; then
    echo "Developer ID source changed or became dirty during the build; refusing to sign." >&2
    exit 2
  fi
}

require_release_project_file_unchanged() {
  if [[ "${RELEASE_DEPENDENCY_LOCK_ATTESTED}" != "1" ]]; then
    return
  fi
  if [[ ! -f "${PROJECT_FILE_PATH}" || -L "${PROJECT_FILE_PATH}" ]]; then
    echo "Release project dependency declarations must remain a regular non-symlink file: ${PROJECT_FILE_PATH}" >&2
    exit 2
  fi
  if [[ "$(sha256_file "${PROJECT_FILE_PATH}")" != "${PROJECT_FILE_SHA256}" ]]; then
    echo "pyproject.toml dependency declarations changed after release lock attestation; refusing to build mixed provenance." >&2
    exit 2
  fi
}

build_swiftui_frontend() {
  require_full_xcode
  if [[ ! -d "${SWIFT_APP_SOURCE_DIR}" ]]; then
    echo "SwiftUI app source directory not found: ${SWIFT_APP_SOURCE_DIR}" >&2
    exit 2
  fi
  for source in "${SWIFT_SOURCE_FILES[@]}"; do
    if [[ ! -f "${source}" ]]; then
      echo "SwiftUI app source missing: ${source}" >&2
      exit 2
    fi
  done
  local sdk_path
  sdk_path="$(/usr/bin/xcrun --sdk macosx --show-sdk-path)"
  mkdir -p "${SWIFT_MODULE_CACHE_PATH}"
  /usr/bin/xcrun --sdk macosx swiftc \
    -O \
    -parse-as-library \
    -target arm64-apple-macos${MINIMUM_MACOS_VERSION} \
    -sdk "${sdk_path}" \
    -module-cache-path "${SWIFT_MODULE_CACHE_PATH}" \
    -Xcc "-fmodules-cache-path=${SWIFT_MODULE_CACHE_PATH}" \
    -framework SwiftUI \
    -framework AppKit \
    -framework Combine \
    -framework CryptoKit \
    -o "${MACOS_DIR}/TotalSegmentatorWrapperForMac" \
    "${SWIFT_SOURCE_FILES[@]}"
  chmod 755 "${MACOS_DIR}/TotalSegmentatorWrapperForMac"
}

codesign_one() {
  local entitlements="$1"
  local target="$2"
  if codesign \
    --force \
    --timestamp \
    --options runtime \
    --entitlements "${entitlements}" \
    --sign "${CODESIGN_IDENTITY}" \
    "${target}" >/dev/null; then
    return 0
  fi
  echo "Retrying codesign once after normalizing permissions: ${target}" >&2
  chmod u+rw "${target}"
  codesign \
    --force \
    --timestamp \
    --options runtime \
    --entitlements "${entitlements}" \
    --sign "${CODESIGN_IDENTITY}" \
    "${target}" >/dev/null
}

codesign_developer_id() {
  find "${APP_DIR}" -type d -exec chmod u+rwx,go+rx {} +
  find "${APP_DIR}" -type f -exec chmod u+rw {} +

  local sign_targets=("${MACOS_DIR}/TotalSegmentatorWrapperForMac" "${RESOURCES_DIR}/bin/dcm2niix")
  sign_targets+=("${RESOURCES_DIR}/bin/totalsegmentator-wrapper-dicom-normalizer")
  if [[ -d "${RESOURCES_DIR}/python/cpython-3.12" ]]; then
    # python-build-standalone places the shared-library Python bundle at the
    # runtime root. Sign it only after every nested extension because its
    # resource seal covers the adjacent runtime tree.
    local python_framework_binary="${RESOURCES_DIR}/python/cpython-3.12/Python"
    local deferred_python_framework_binary=""
    while IFS= read -r path; do
      if [[ "${path}" == "${python_framework_binary}" ]]; then
        deferred_python_framework_binary="${path}"
      else
        sign_targets+=("${path}")
      fi
    done < <(
      find "${RESOURCES_DIR}/python/cpython-3.12" -type f \
        \( -perm -111 -o -name "*.dylib" -o -name "*.so" \) \
        -print | sort
    )
    if [[ -n "${deferred_python_framework_binary}" ]]; then
      sign_targets+=("${deferred_python_framework_binary}")
    fi
  fi

  local target
  for target in "${sign_targets[@]}"; do
    if [[ "${target}" == "${RESOURCES_DIR}/python/cpython-3.12"* ]]; then
      codesign_one "${PYTHON_ENTITLEMENTS}" "${target}"
    else
      codesign_one "${APP_ENTITLEMENTS}" "${target}"
    fi
  done

  if [[ -d "${RESOURCES_DIR}/python/cpython-3.12" ]]; then
    find "${RESOURCES_DIR}/python/cpython-3.12" -type f -exec chmod a-w {} +
  fi
  codesign_one "${APP_ENTITLEMENTS}" "${APP_DIR}"
  codesign --verify --deep --strict --verbose=2 "${APP_DIR}" >/dev/null
  local signed_team_identifier
  signed_team_identifier="$(codesign -dv --verbose=4 "${APP_DIR}" 2>&1 | awk -F= '/^TeamIdentifier=/{print $2; exit}')"
  if [[ "${signed_team_identifier}" != "${TEAM_IDENTIFIER}" ]]; then
    echo "Signed app TeamIdentifier mismatch: expected ${TEAM_IDENTIFIER}, found ${signed_team_identifier:-missing}" >&2
    exit 2
  fi
}

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
prepare_owned_dist_directory
validate_owned_dist_child_directory_if_present "${APP_DIR}" "${APP_NAME}.app"
validate_owned_dist_child_directory_if_present "${LICENSE_INVENTORY_ENV_DIR}" "license_inventory_env"
PROJECT_VERSION="$("${PYTHON_BIN}" -c 'import pathlib, sys, tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["project"]["version"])' "${ROOT}/pyproject.toml")"
if [[ -n "${APP_VERSION_OVERRIDE}" && "${APP_VERSION_OVERRIDE}" != "${PROJECT_VERSION}" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_APP_VERSION=${APP_VERSION_OVERRIDE} does not match pyproject version ${PROJECT_VERSION}." >&2
  exit 2
fi
APP_VERSION="${PROJECT_VERSION}"
SOURCE_COMMIT="$(git -C "${ROOT}" rev-parse HEAD)"
SOURCE_STATUS="$(git -C "${ROOT}" status --porcelain=v1 --untracked-files=all)"
if [[ -n "${SOURCE_STATUS}" ]]; then
  SOURCE_TREE_DIRTY_JSON="true"
fi
if [[ "${SIGNING_MODE}" == "developer-id" && "${SOURCE_TREE_DIRTY_JSON}" != "false" ]]; then
  echo "Developer ID builds require a clean tracked and untracked source worktree." >&2
  exit 2
fi
require_release_python_runtime
if [[ "${SIGNING_MODE}" == "developer-id" || "${TOTALSEGMENTATOR_WRAPPER_MAC_NOTARIZED:-0}" == "1" ]]; then
  # The build-toolchain receipt records this full Xcode/clang boundary.  Do
  # this before it is prepared so component compilation cannot silently select
  # Command Line Tools or an ambient Homebrew compiler later.
  require_full_xcode
  if [[ ! -f "${RELEASE_INPUT_READINESS_SCRIPT}" ]]; then
    echo "Release input readiness verifier is missing: ${RELEASE_INPUT_READINESS_SCRIPT}" >&2
    exit 2
  fi
  RELEASE_INPUT_READINESS_JSON="$("${PYTHON_BIN}" "${RELEASE_INPUT_READINESS_SCRIPT}" \
    --constraints "${CONSTRAINTS_PATH}" \
    --requirements-lock "${REQUIREMENTS_LOCK_PATH}" \
    --lock-metadata "${DEPENDENCY_LOCK_METADATA_PATH}" \
    --project-file "${PROJECT_FILE_PATH}" \
    --setup-manager-source "${ROOT}/src/totalsegmentator_wrapper_mac/setup_manager.py" \
    --setup-weights-manifest "${SETUP_WEIGHTS_MANIFEST_PATH}" \
    --release-build-toolchain-lock "${RELEASE_BUILD_TOOLCHAIN_LOCK_PATH}" \
    --release-build-toolchain-metadata "${RELEASE_BUILD_TOOLCHAIN_METADATA_PATH}" \
    --release-build-toolchain-wheelhouse "${RELEASE_BUILD_TOOLCHAIN_WHEELHOUSE}" \
    --json)"
  if [[ ! -f "${RELEASE_BUILD_TOOLCHAIN_SCRIPT}" ]]; then
    echo "Release build toolchain verifier is missing: ${RELEASE_BUILD_TOOLCHAIN_SCRIPT}" >&2
    exit 2
  fi
  if [[ ! -x "${RELEASE_COMPONENT_BUILD_RUNNER}" ]]; then
    echo "Release component build runner is missing or not executable: ${RELEASE_COMPONENT_BUILD_RUNNER}" >&2
    exit 2
  fi
  if [[ -z "${UV_BIN}" ]]; then
    echo "Developer ID and notarized builds require the hash-bound uv executable recorded by the release build toolchain metadata." >&2
    exit 2
  fi
  RELEASE_BUILD_TOOLCHAIN_PREPARE_JSON="$("${PYTHON_BIN}" "${RELEASE_BUILD_TOOLCHAIN_SCRIPT}" \
    --lock "${RELEASE_BUILD_TOOLCHAIN_LOCK_PATH}" \
    --metadata "${RELEASE_BUILD_TOOLCHAIN_METADATA_PATH}" \
    --wheelhouse "${RELEASE_BUILD_TOOLCHAIN_WHEELHOUSE}" \
    --python "${PYTHON_BIN}" \
    --uv "${UV_BIN}" \
    --prepare-work-directory "${RELEASE_BUILD_TOOLCHAIN_WORK_DIRECTORY}" \
    --receipt "${RELEASE_BUILD_TOOLCHAIN_RECEIPT_PATH}" \
    --json)"
  RELEASE_BUILD_TOOLCHAIN_PREPARED_PYTHON="$("${PYTHON_BIN}" -c 'import json, sys; print(json.loads(sys.argv[1])["prepared_python"])' "${RELEASE_BUILD_TOOLCHAIN_PREPARE_JSON}")"
  RELEASE_BUILD_TOOLCHAIN_LOCK_SHA256="$("${PYTHON_BIN}" -c 'import json, sys; print(json.loads(sys.argv[1])["lock_sha256"])' "${RELEASE_BUILD_TOOLCHAIN_PREPARE_JSON}")"
  RELEASE_BUILD_TOOLCHAIN_METADATA_SHA256="$("${PYTHON_BIN}" -c 'import json, sys; print(json.loads(sys.argv[1])["metadata_sha256"])' "${RELEASE_BUILD_TOOLCHAIN_PREPARE_JSON}")"
  RELEASE_BUILD_TOOLCHAIN_RECEIPT_SHA256="$(sha256_file "${RELEASE_BUILD_TOOLCHAIN_RECEIPT_PATH}")"
  require_sha256_digest "${RELEASE_BUILD_TOOLCHAIN_LOCK_SHA256}" "Release build toolchain lock SHA-256"
  require_sha256_digest "${RELEASE_BUILD_TOOLCHAIN_METADATA_SHA256}" "Release build toolchain metadata SHA-256"
  require_sha256_digest "${RELEASE_BUILD_TOOLCHAIN_RECEIPT_SHA256}" "Release build toolchain receipt SHA-256"
  if [[ ! -x "${RELEASE_BUILD_TOOLCHAIN_PREPARED_PYTHON}" ]]; then
    echo "Prepared release build toolchain did not produce an executable Python: ${RELEASE_BUILD_TOOLCHAIN_PREPARED_PYTHON}" >&2
    exit 2
  fi
  RELEASE_BUILD_TOOLCHAIN_PROVENANCE_JSON="$("${PYTHON_BIN}" -c 'import json, sys; payload=json.loads(sys.argv[1]); print(json.dumps({"lock_sha256": payload["lock_sha256"], "metadata_sha256": payload["metadata_sha256"], "uv": payload["toolchain"]["uv"], "python": payload["toolchain"]["python"], "native_toolchain": payload["toolchain"]["native_toolchain"]}, sort_keys=True))' "${RELEASE_BUILD_TOOLCHAIN_PREPARE_JSON}")"
  RELEASE_BUILD_TOOLCHAIN_LOCK_SHA256_JSON="$(json_string "${RELEASE_BUILD_TOOLCHAIN_LOCK_SHA256}")"
  RELEASE_BUILD_TOOLCHAIN_METADATA_SHA256_JSON="$(json_string "${RELEASE_BUILD_TOOLCHAIN_METADATA_SHA256}")"
  RELEASE_BUILD_TOOLCHAIN_RECEIPT_SHA256_JSON="$(json_string "${RELEASE_BUILD_TOOLCHAIN_RECEIPT_SHA256}")"
  RELEASE_BUILD_TOOLCHAIN_LOCK_BUNDLED_JSON="$(json_string "build-toolchain/macos-arm64-py312.release-build-toolchain.lock")"
  RELEASE_BUILD_TOOLCHAIN_METADATA_BUNDLED_JSON="$(json_string "build-toolchain/macos-arm64-py312.release-build-toolchain.lock.json")"
  RELEASE_BUILD_TOOLCHAIN_RECEIPT_BUNDLED_JSON="$(json_string "build-toolchain/release-build-toolchain-receipt.json")"
  FPSAMPLE_PRE_SIGN_WHEEL_SHA256="$("${PYTHON_BIN}" -c 'import json, sys; print(json.loads(sys.argv[1])["dependency_lock"]["excluded_bundled_overrides"]["fpsample"]["resolution_input_sha256"])' "${RELEASE_INPUT_READINESS_JSON}")"
  ACVL_UTILS_RESOLUTION_INPUT_SHA256="$("${PYTHON_BIN}" -c 'import json, sys; print(json.loads(sys.argv[1])["dependency_lock"]["excluded_bundled_overrides"]["acvl-utils"]["resolution_input_sha256"])' "${RELEASE_INPUT_READINESS_JSON}")"
  require_sha256_digest "${FPSAMPLE_PRE_SIGN_WHEEL_SHA256}" "fpsample resolver input SHA-256"
  require_sha256_digest "${ACVL_UTILS_RESOLUTION_INPUT_SHA256}" "acvl-utils resolver input SHA-256"
  FPSAMPLE_PRE_SIGN_WHEEL_SHA256_JSON="$(json_string "${FPSAMPLE_PRE_SIGN_WHEEL_SHA256}")"
  REQUIREMENTS_LOCK_SHA256="$(sha256_file "${REQUIREMENTS_LOCK_PATH}")"
  DEPENDENCY_LOCK_METADATA_SHA256="$(sha256_file "${DEPENDENCY_LOCK_METADATA_PATH}")"
  PROJECT_FILE_SHA256="$(sha256_file "${PROJECT_FILE_PATH}")"
  LOCK_METADATA_PROJECT_FILE_SHA256="$("${PYTHON_BIN}" -c 'import json, sys; value=json.load(open(sys.argv[1], encoding="utf-8")).get("project_file_sha256"); print(value if isinstance(value, str) else "")' "${DEPENDENCY_LOCK_METADATA_PATH}")"
  require_sha256_digest "${REQUIREMENTS_LOCK_SHA256}" "Canonical requirements lock SHA-256"
  require_sha256_digest "${DEPENDENCY_LOCK_METADATA_SHA256}" "Dependency lock metadata SHA-256"
  require_sha256_digest "${PROJECT_FILE_SHA256}" "Canonical project dependency declarations SHA-256"
  if [[ "${LOCK_METADATA_PROJECT_FILE_SHA256}" != "${PROJECT_FILE_SHA256}" ]]; then
    echo "Canonical dependency lock metadata does not bind the current pyproject.toml dependency declarations." >&2
    exit 2
  fi
  REQUIREMENTS_LOCK_SHA256_JSON='"'"${REQUIREMENTS_LOCK_SHA256}"'"'
  DEPENDENCY_LOCK_METADATA_SHA256_JSON='"'"${DEPENDENCY_LOCK_METADATA_SHA256}"'"'
  PROJECT_FILE_SHA256_JSON='"'"${PROJECT_FILE_SHA256}"'"'
  REQUIREMENTS_LOCK_BUNDLED_JSON='"constraints/macos-arm64-py312.requirements.lock"'
  DEPENDENCY_LOCK_METADATA_BUNDLED_JSON='"constraints/macos-arm64-py312.lock.json"'
  PROJECT_FILE_BUNDLED_JSON='"constraints/pyproject.toml"'
  if [[ ! -f "${OFFLINE_DEPENDENCY_WHEELHOUSE_SCRIPT}" || -L "${OFFLINE_DEPENDENCY_WHEELHOUSE_SCRIPT}" ]]; then
    echo "Offline dependency wheelhouse verifier is missing or unsafe: ${OFFLINE_DEPENDENCY_WHEELHOUSE_SCRIPT}" >&2
    exit 2
  fi
  "${PYTHON_BIN}" "${OFFLINE_DEPENDENCY_WHEELHOUSE_SCRIPT}" \
    --verify-existing \
    --constraints "${CONSTRAINTS_PATH}" \
    --requirements-lock "${REQUIREMENTS_LOCK_PATH}" \
    --lock-metadata "${DEPENDENCY_LOCK_METADATA_PATH}" \
    --output-directory "${OFFLINE_DEPENDENCY_WHEELHOUSE_ROOT}" >/dev/null
  DEPENDENCY_WHEELHOUSE_MANIFEST_SHA256="$(sha256_file "${OFFLINE_DEPENDENCY_WHEELHOUSE_MANIFEST_PATH}")"
  require_sha256_digest "${DEPENDENCY_WHEELHOUSE_MANIFEST_SHA256}" "Offline dependency wheelhouse manifest SHA-256"
  DEPENDENCY_WHEELHOUSE_MANIFEST_SHA256_JSON="$(json_string "${DEPENDENCY_WHEELHOUSE_MANIFEST_SHA256}")"
  DEPENDENCY_WHEELHOUSE_MANIFEST_BUNDLED_JSON='"constraints/macos-arm64-py312.wheelhouse.json"'
  RELEASE_DEPENDENCY_LOCK_ATTESTED="1"
  RELEASE_BUILD_TOOLCHAIN_ATTESTED="1"
  DEPENDENCY_SET_ID="${DEPENDENCY_SET_ID}-lock-${REQUIREMENTS_LOCK_SHA256:0:12}-metadata-${DEPENDENCY_LOCK_METADATA_SHA256:0:12}"
fi

if [[ ! -f "${MACHO_DEPLOYMENT_VERIFY_SCRIPT}" || ! -f "${MACHO_LINKAGE_VERIFY_SCRIPT}" ]]; then
  echo "macOS deployment-target or linkage verifier is missing under scripts/." >&2
  exit 2
fi
if [[ -n "${DCM2NIIX_EXPLICIT_PATH}" ]]; then
  if [[ "${SIGNING_MODE}" == "developer-id" || "${TOTALSEGMENTATOR_WRAPPER_MAC_NOTARIZED:-0}" == "1" ]]; then
    echo "Developer ID and notarized builds require the pinned source-built dcm2niix artifact selected by ${DCM2NIIX_BUILD_ROOT}/current-artifact.json; TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX is development-only." >&2
    exit 2
  fi
  DCM2NIIX_PATH="${DCM2NIIX_EXPLICIT_PATH}"
  DCM2NIIX_LICENSE_SOURCE_PATH="${DCM2NIIX_LICENSE_PATH}"
  DCM2NIIX_SOURCE_JSON="$("${PYTHON_BIN}" -c 'import json; print(json.dumps({"kind": "explicit-development-input-unpinned", "release_eligible": False, "expected_cli_version": "v1.0.20250505", "source_provenance": "not-verified", "license_status": "not-verified-for-custom-build-input", "minimum_macos": "14.0", "architecture": "arm64", "linkage": "verified-system-only-no-rpath-at-packaging"}, sort_keys=True))')"
  DCM2NIIX_PROVENANCE_NOTICE="- Development-only explicit input; source and license provenance were not verified. This app is not release-eligible."
else
  if [[ ! -f "${DCM2NIIX_SOURCE_ARTIFACT_VERIFY_SCRIPT}" ]]; then
    echo "Pinned dcm2niix source artifact verifier is missing: ${DCM2NIIX_SOURCE_ARTIFACT_VERIFY_SCRIPT}" >&2
    exit 2
  fi
  if [[ ! -f "${DCM2NIIX_BUILD_ROOT}/current-artifact.json" ]]; then
    echo "Pinned dcm2niix macOS 14 artifact is not prepared. Run scripts/build_dcm2niix_macos14_arm64.sh before app packaging; build_mac_app.sh will not download or build it implicitly. Expected ${DCM2NIIX_BUILD_ROOT}/current-artifact.json." >&2
    exit 2
  fi
  DCM2NIIX_ARTIFACT_JSON="$("${PYTHON_BIN}" "${DCM2NIIX_SOURCE_ARTIFACT_VERIFY_SCRIPT}" \
    --build-root "${DCM2NIIX_BUILD_ROOT}" \
    --expected-license "${DCM2NIIX_LICENSE_PATH}" \
    --json)"
  DCM2NIIX_PATH="$("${PYTHON_BIN}" -c 'import json, sys; print(json.loads(sys.argv[1])["binary_path"])' "${DCM2NIIX_ARTIFACT_JSON}")"
  DCM2NIIX_LICENSE_SOURCE_PATH="$("${PYTHON_BIN}" -c 'import json, sys; print(json.loads(sys.argv[1])["license_path"])' "${DCM2NIIX_ARTIFACT_JSON}")"
  DCM2NIIX_BUILD_RECEIPT_PATH="$("${PYTHON_BIN}" -c 'import json, sys; print(json.loads(sys.argv[1])["receipt_path"])' "${DCM2NIIX_ARTIFACT_JSON}")"
  DCM2NIIX_ARTIFACT_POINTER_PATH="$("${PYTHON_BIN}" -c 'import json, sys; print(json.loads(sys.argv[1])["pointer_path"])' "${DCM2NIIX_ARTIFACT_JSON}")"
  DCM2NIIX_SOURCE_JSON="$("${PYTHON_BIN}" -c 'import json, sys; print(json.dumps(json.loads(sys.argv[1])["source"], sort_keys=True))' "${DCM2NIIX_ARTIFACT_JSON}")"
  DCM2NIIX_BUNDLED_RECEIPT_JSON='"licenses/dcm2niix-build-provenance.json"'
  DCM2NIIX_BUNDLED_POINTER_JSON='"licenses/dcm2niix-current-artifact.json"'
  DCM2NIIX_PROVENANCE_NOTICE="- Pinned official source tag: v1.0.20250506
- Source archive SHA256: 1b24658678b6c24141e58760dbea9fe2786ffdd736bcc37a36d9cdabc731bafa
- Build receipt: Contents/Resources/licenses/dcm2niix-build-provenance.json
- Content-addressed artifact pointer: Contents/Resources/licenses/dcm2niix-current-artifact.json"
fi
if [[ ! -x "${DCM2NIIX_PATH}" || -L "${DCM2NIIX_PATH}" ]]; then
  echo "dcm2niix build input must be an executable regular non-symlink file: ${DCM2NIIX_PATH}" >&2
  exit 2
fi
"${PYTHON_BIN}" "${MACHO_DEPLOYMENT_VERIFY_SCRIPT}" \
  --path "${DCM2NIIX_PATH}" \
  --max-macos "${MINIMUM_MACOS_VERSION}" \
  --require-arm64 >/dev/null
"${PYTHON_BIN}" "${MACHO_LINKAGE_VERIFY_SCRIPT}" --path "${DCM2NIIX_PATH}" >/dev/null

if [[ ! -f "${NORMALIZER_BUILD_RECEIPT_PATH}" ]]; then
  echo "Pinned macOS 14 DICOM normalizer artifact is not prepared. Run scripts/build_dicom_normalizer_mac.sh explicitly before app packaging; build_mac_app.sh will not download or build GDCM implicitly. Expected ${NORMALIZER_BUILD_RECEIPT_PATH}." >&2
  exit 2
fi
if [[ ! -f "${DICOM_NORMALIZER_ARTIFACT_VERIFY_SCRIPT}" ]]; then
  echo "DICOM normalizer source artifact verifier is missing: ${DICOM_NORMALIZER_ARTIFACT_VERIFY_SCRIPT}" >&2
  exit 1
fi
NORMALIZER_ARTIFACT_JSON="$("${PYTHON_BIN}" "${DICOM_NORMALIZER_ARTIFACT_VERIFY_SCRIPT}" \
  --verify \
  --artifact-dir "${NORMALIZER_ARTIFACT_DIR}" \
  --source-dir "${ROOT}/native/dicom_normalizer" \
  --json)"
VERIFIED_NORMALIZER_PATH="$("${PYTHON_BIN}" -c 'import json, sys; print(json.loads(sys.argv[1])["binary_path"])' "${NORMALIZER_ARTIFACT_JSON}")"
NORMALIZER_SOURCE_JSON="$("${PYTHON_BIN}" -c 'import json, sys; print(json.dumps(json.loads(sys.argv[1])["source"], sort_keys=True))' "${NORMALIZER_ARTIFACT_JSON}")"
if [[ "$("${PYTHON_BIN}" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "${NORMALIZER_PATH}")" != "${VERIFIED_NORMALIZER_PATH}" ]]; then
  echo "DICOM normalizer artifact verifier resolved an unexpected binary path." >&2
  exit 1
fi
"${PYTHON_BIN}" "${MACHO_DEPLOYMENT_VERIFY_SCRIPT}" \
  --path "${NORMALIZER_PATH}" \
  --max-macos "${MINIMUM_MACOS_VERSION}" \
  --require-arm64 >/dev/null
"${PYTHON_BIN}" "${MACHO_LINKAGE_VERIFY_SCRIPT}" --path "${NORMALIZER_PATH}" >/dev/null

require_full_xcode
require_developer_id_signing
require_release_project_file_unchanged
if [[ "${RELEASE_BUILD_TOOLCHAIN_ATTESTED}" == "1" ]]; then
  # Pass only these already-validated app inputs into the sealed component
  # runner.  It starts each child with env -i and ignores every other shell
  # build/compiler override.
  export TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR="${DIST_DIR}"
  export TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_ARTIFACT_DIR="${NORMALIZER_ARTIFACT_DIR}"
  export TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE="${SIGNING_MODE}"
  export TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY="${CODESIGN_IDENTITY}"
  "${RELEASE_COMPONENT_BUILD_RUNNER}" \
    --lock "${RELEASE_BUILD_TOOLCHAIN_LOCK_PATH}" \
    --metadata "${RELEASE_BUILD_TOOLCHAIN_METADATA_PATH}" \
    --receipt "${RELEASE_BUILD_TOOLCHAIN_RECEIPT_PATH}" \
    --prepared-python "${RELEASE_BUILD_TOOLCHAIN_PREPARED_PYTHON}" \
    --component wrapper \
    -- "${ROOT}/scripts/build_mac_wheel.sh" >/dev/null
  TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_FPSAMPLE_PRE_SIGN_SHA256="${FPSAMPLE_PRE_SIGN_WHEEL_SHA256}" \
    "${RELEASE_COMPONENT_BUILD_RUNNER}" \
      --lock "${RELEASE_BUILD_TOOLCHAIN_LOCK_PATH}" \
      --metadata "${RELEASE_BUILD_TOOLCHAIN_METADATA_PATH}" \
      --receipt "${RELEASE_BUILD_TOOLCHAIN_RECEIPT_PATH}" \
      --prepared-python "${RELEASE_BUILD_TOOLCHAIN_PREPARED_PYTHON}" \
      --component fpsample \
      -- "${ROOT}/scripts/build_fpsample_wheel_macos.sh" >/dev/null
  TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_ACVL_UTILS_WHEEL_SHA256="${ACVL_UTILS_RESOLUTION_INPUT_SHA256}" \
    "${RELEASE_COMPONENT_BUILD_RUNNER}" \
      --lock "${RELEASE_BUILD_TOOLCHAIN_LOCK_PATH}" \
      --metadata "${RELEASE_BUILD_TOOLCHAIN_METADATA_PATH}" \
      --receipt "${RELEASE_BUILD_TOOLCHAIN_RECEIPT_PATH}" \
      --prepared-python "${RELEASE_BUILD_TOOLCHAIN_PREPARED_PYTHON}" \
      --component acvl-utils \
      -- "${ROOT}/scripts/build_acvl_utils_wheel.sh" >/dev/null
else
  "${ROOT}/scripts/build_mac_wheel.sh" >/dev/null
  "${ROOT}/scripts/build_fpsample_wheel_macos.sh" >/dev/null
  "${ROOT}/scripts/build_acvl_utils_wheel.sh" >/dev/null
fi
require_release_project_file_unchanged

WHEEL_PATH="${DIST_DIR}/totalsegmentator_wrapper_mac-${APP_VERSION}-cp312-cp312-macosx_14_0_arm64.whl"
FPSAMPLE_WHEEL_PATH="$(ls -1t "${DIST_DIR}"/fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl | head -n 1)"
ACVL_UTILS_WHEEL_PATH="${DIST_DIR}/acvl_utils-0.2.6-py3-none-any.whl"
SAMPLE1_MANIFEST_PATH="${ROOT}/resources/sample1/sample_manifest.json"
if [[ ! -f "${WHEEL_PATH}" || -L "${WHEEL_PATH}" ]]; then
  echo "Wrapper wheel build did not produce the exact CPython 3.12/macOS 14 artifact: ${WHEEL_PATH}" >&2
  exit 2
fi
if [[ ! -f "${WRAPPER_LICENSE_PATH}" || ! -f "${WRAPPER_NOTICE_PATH}" ]]; then
  echo "Wrapper Apache-2.0 LICENSE or NOTICE is missing." >&2
  exit 1
fi
if [[ ! -f "${TOTALSEGMENTATOR_LICENSE_PATH}" ]]; then
  echo "TotalSegmentator Apache-2.0 license text is missing: ${TOTALSEGMENTATOR_LICENSE_PATH}" >&2
  exit 1
fi
if [[ ! -f "${TOTALSEGMENTATOR_TASK_INVENTORY_PATH}" ]]; then
  echo "TotalSegmentator task inventory is missing: ${TOTALSEGMENTATOR_TASK_INVENTORY_PATH}" >&2
  exit 1
fi
if [[ ! -f "${DENTALSEG_NOTICE_PATH}" || ! -f "${TOOTHSEG_NOTICE_PATH}" || ! -f "${MESHSEGNET_NOTICE_PATH}" || ! -f "${TGNET_NOTICE_PATH}" ]]; then
  echo "DentalSegmentator, ToothSeg, MeshSegNet, or TGNet attribution/policy notice is missing." >&2
  exit 1
fi
if [[ ! -f "${SETUP_WEIGHTS_MANIFEST_PATH}" ]]; then
  echo "Canonical TotalSegmentator setup weights manifest is missing: ${SETUP_WEIGHTS_MANIFEST_PATH}" >&2
  exit 1
fi
if [[ ! -f "${FPSAMPLE_WHEEL_PATH}" || ! -f "${FPSAMPLE_NOTICE_PATH}" ]]; then
  echo "Bundled fpsample wheel or redistribution notice is missing." >&2
  exit 1
fi
if [[ ! -f "${ACVL_UTILS_WHEEL_PATH}" ]]; then
  echo "Bundled acvl-utils pure-Python wheel is missing: ${ACVL_UTILS_WHEEL_PATH}" >&2
  exit 1
fi
if [[ ! -f "${DCM2NIIX_LICENSE_PATH}" ]]; then
  echo "dcm2niix license text is missing: ${DCM2NIIX_LICENSE_PATH}" >&2
  exit 1
fi
if [[ ! -f "${DCM2NIIX_LICENSE_SOURCE_PATH}" ]]; then
  echo "Verified dcm2niix artifact license is missing: ${DCM2NIIX_LICENSE_SOURCE_PATH}" >&2
  exit 1
fi
for license_name in "${DICOM_RUNTIME_LICENSE_NAMES[@]}"; do
  license_path="${NORMALIZER_ARTIFACT_DIR}/licenses/${license_name}"
  if [[ ! -f "${license_path}" ]]; then
    echo "DICOM runtime license text is missing: ${license_path}" >&2
    exit 1
  fi
done
"${PYTHON_BIN}" "${MACHO_DEPLOYMENT_VERIFY_SCRIPT}" \
  --path "${NORMALIZER_PATH}" \
  --wheel "${WHEEL_PATH}" \
  --wheel "${FPSAMPLE_WHEEL_PATH}" \
  --max-macos "${MINIMUM_MACOS_VERSION}" \
  --require-arm64 >/dev/null
"${PYTHON_BIN}" "${MACHO_LINKAGE_VERIFY_SCRIPT}" --path "${NORMALIZER_PATH}" >/dev/null
"${PYTHON_BIN}" "${MACHO_LINKAGE_VERIFY_SCRIPT}" \
  --wheel "${WHEEL_PATH}" \
  --wheel "${FPSAMPLE_WHEEL_PATH}" >/dev/null
if [[ ! -f "${LICENSE_MANUAL_OVERRIDES_PATH}" ]]; then
  echo "Manual license override manifest is missing: ${LICENSE_MANUAL_OVERRIDES_PATH}" >&2
  exit 1
fi
if [[ ! -x "${LICENSE_INVENTORY_SCRIPT}" && ! -f "${LICENSE_INVENTORY_SCRIPT}" ]]; then
  echo "Third-party license inventory script is missing: ${LICENSE_INVENTORY_SCRIPT}" >&2
  exit 1
fi
if [[ ! -f "${PYTHON_RUNTIME_FINGERPRINT_SCRIPT}" ]]; then
  echo "Python runtime fingerprint script is missing: ${PYTHON_RUNTIME_FINGERPRINT_SCRIPT}" >&2
  exit 1
fi
WHEEL_SHA256="$(sha256_file "${WHEEL_PATH}")"
FPSAMPLE_WHEEL_SHA256="$(sha256_file "${FPSAMPLE_WHEEL_PATH}")"
ACVL_UTILS_WHEEL_SHA256="$(sha256_file "${ACVL_UTILS_WHEEL_PATH}")"
SETUP_WEIGHTS_MANIFEST_SHA256="$(sha256_file "${SETUP_WEIGHTS_MANIFEST_PATH}")"
CONSTRAINTS_SHA256="$(sha256_file "${CONSTRAINTS_PATH}")"
NORMALIZER_INPUT_SHA256="$(sha256_file "${NORMALIZER_PATH}")"
SAMPLE1_MANIFEST_SHA256="$(sha256_file "${SAMPLE1_MANIFEST_PATH}")"
DCM2NIIX_INPUT_SHA256="$(sha256_file "${DCM2NIIX_PATH}")"
DCM2NIIX_LICENSE_INPUT_SHA256="$(sha256_file "${DCM2NIIX_LICENSE_SOURCE_PATH}")"
SWIFT_SOURCE_SHA256="$(cat "${SWIFT_SOURCE_FILES[@]}" | shasum -a 256 | awk '{print $1}')"
DCM2NIIX_VERSION_LINE="$("${DCM2NIIX_PATH}" -h 2>&1 | awk 'BEGIN{fallback=""} /version|dcm2niix/{print; found=1; exit} NF && fallback==""{fallback=$0} END{if (!found) print fallback}')"
if [[ "${DCM2NIIX_VERSION_LINE}" != *"v1.0.20250505"* ]]; then
  echo "dcm2niix compatibility version mismatch: expected embedded CLI v1.0.20250505." >&2
  exit 2
fi
DCM2NIIX_VERSION_JSON="$(printf '%s\n' "${DCM2NIIX_VERSION_LINE}" | first_json_line)"
UPDATE_MANIFEST_URL_JSON="$(json_string "${UPDATE_MANIFEST_URL}")"
UPDATE_ALLOWED_HOSTS_JSON="$(json_string_list "${UPDATE_ALLOWED_HOSTS}")"
BUNDLE_IDENTIFIER_JSON="$(json_string "${BUNDLE_IDENTIFIER}")"
NOTARIZATION_CREDENTIALS_CONFIGURED_JSON="false"
if [[ -n "${NOTARY_PROFILE}" ]]; then
  NOTARIZATION_CREDENTIALS_CONFIGURED_JSON="true"
fi
NOTARIZED_JSON="false"
TEAM_IDENTIFIER_JSON="null"
BUNDLE_IDENTITY_STATUS="degraded-ad-hoc"
if [[ "${TOTALSEGMENTATOR_WRAPPER_MAC_NOTARIZED:-0}" == "1" ]]; then
  NOTARIZED_JSON="true"
fi
if [[ "${SIGNING_MODE}" == "developer-id" ]]; then
  TEAM_IDENTIFIER_JSON="$(json_string "${TEAM_IDENTIFIER}")"
  BUNDLE_IDENTITY_STATUS="verified-developer-id"
fi

remove_owned_dist_child_directory "${APP_DIR}" "${APP_NAME}.app"
mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}/wheels" "${RESOURCES_DIR}/bin" "${RESOURCES_DIR}/constraints" "${RESOURCES_DIR}/sample1" "${RESOURCES_DIR}/model_comparison" "${RESOURCES_DIR}/licenses"

build_swiftui_frontend
require_release_project_file_unchanged
if [[ "${RELEASE_DEPENDENCY_LOCK_ATTESTED}" == "1" ]]; then
  verify_and_copy_offline_dependency_wheels "${RESOURCES_DIR}/wheels"
fi
for component_wheel in "${FPSAMPLE_WHEEL_PATH}" "${ACVL_UTILS_WHEEL_PATH}" "${WHEEL_PATH}"; do
  if [[ -e "${RESOURCES_DIR}/wheels/$(basename "${component_wheel}")" ]]; then
    echo "Component wheel filename collides with an offline dependency wheel: $(basename "${component_wheel}")" >&2
    exit 2
  fi
  cp "${component_wheel}" "${RESOURCES_DIR}/wheels/"
done
cp "${SETUP_WEIGHTS_MANIFEST_PATH}" "${RESOURCES_DIR}/totalseg_setup_weights_manifest.json"
cp "${CONSTRAINTS_PATH}" "${RESOURCES_DIR}/constraints/"
if [[ "${RELEASE_DEPENDENCY_LOCK_ATTESTED}" == "1" ]]; then
  cp "${REQUIREMENTS_LOCK_PATH}" "${RESOURCES_DIR}/constraints/macos-arm64-py312.requirements.lock"
  cp "${DEPENDENCY_LOCK_METADATA_PATH}" "${RESOURCES_DIR}/constraints/macos-arm64-py312.lock.json"
  cp "${PROJECT_FILE_PATH}" "${RESOURCES_DIR}/constraints/pyproject.toml"
  cp "${OFFLINE_DEPENDENCY_WHEELHOUSE_MANIFEST_PATH}" \
    "${RESOURCES_DIR}/constraints/macos-arm64-py312.wheelhouse.json"
  if [[ "$(sha256_file "${RESOURCES_DIR}/constraints/macos-arm64-py312.requirements.lock")" != "${REQUIREMENTS_LOCK_SHA256}" \
    || "$(sha256_file "${RESOURCES_DIR}/constraints/macos-arm64-py312.lock.json")" != "${DEPENDENCY_LOCK_METADATA_SHA256}" \
    || "$(sha256_file "${RESOURCES_DIR}/constraints/pyproject.toml")" != "${PROJECT_FILE_SHA256}" \
    || "$(sha256_file "${OFFLINE_DEPENDENCY_WHEELHOUSE_MANIFEST_PATH}")" != "${DEPENDENCY_WHEELHOUSE_MANIFEST_SHA256}" \
    || "$(sha256_file "${RESOURCES_DIR}/constraints/macos-arm64-py312.wheelhouse.json")" != "${DEPENDENCY_WHEELHOUSE_MANIFEST_SHA256}" ]]; then
    echo "Copied dependency lock or wheelhouse inputs differ from their release-attested SHA-256 values." >&2
    exit 1
  fi
fi
if [[ "${RELEASE_BUILD_TOOLCHAIN_ATTESTED}" == "1" ]]; then
  mkdir -p "${RESOURCES_DIR}/build-toolchain"
  cp "${RELEASE_BUILD_TOOLCHAIN_LOCK_PATH}" \
    "${RESOURCES_DIR}/build-toolchain/macos-arm64-py312.release-build-toolchain.lock"
  cp "${RELEASE_BUILD_TOOLCHAIN_METADATA_PATH}" \
    "${RESOURCES_DIR}/build-toolchain/macos-arm64-py312.release-build-toolchain.lock.json"
  cp "${RELEASE_BUILD_TOOLCHAIN_RECEIPT_PATH}" \
    "${RESOURCES_DIR}/build-toolchain/release-build-toolchain-receipt.json"
  if [[ "$(sha256_file "${RESOURCES_DIR}/build-toolchain/macos-arm64-py312.release-build-toolchain.lock")" != "${RELEASE_BUILD_TOOLCHAIN_LOCK_SHA256}" \
    || "$(sha256_file "${RESOURCES_DIR}/build-toolchain/macos-arm64-py312.release-build-toolchain.lock.json")" != "${RELEASE_BUILD_TOOLCHAIN_METADATA_SHA256}" \
    || "$(sha256_file "${RESOURCES_DIR}/build-toolchain/release-build-toolchain-receipt.json")" != "${RELEASE_BUILD_TOOLCHAIN_RECEIPT_SHA256}" ]]; then
    echo "Copied release build-toolchain inputs differ from their release-attested SHA-256 values." >&2
    exit 1
  fi
fi
cp "${NORMALIZER_PATH}" "${RESOURCES_DIR}/bin/totalsegmentator-wrapper-dicom-normalizer"
cp "${DCM2NIIX_PATH}" "${RESOURCES_DIR}/bin/dcm2niix"
cp "${WRAPPER_LICENSE_PATH}" "${RESOURCES_DIR}/LICENSE"
cp "${WRAPPER_NOTICE_PATH}" "${RESOURCES_DIR}/NOTICE"
cp "${TOTALSEGMENTATOR_LICENSE_PATH}" "${RESOURCES_DIR}/licenses/TotalSegmentator-Apache-2.0.txt"
cp "${TOTALSEGMENTATOR_TASK_INVENTORY_PATH}" "${RESOURCES_DIR}/licenses/TotalSegmentator-task-inventory.json"
cp "${DENTALSEG_NOTICE_PATH}" "${RESOURCES_DIR}/licenses/DentalSegmentator-NOTICE.txt"
cp "${TOOTHSEG_NOTICE_PATH}" "${RESOURCES_DIR}/licenses/ToothSeg-NOTICE.txt"
cp "${MESHSEGNET_NOTICE_PATH}" "${RESOURCES_DIR}/licenses/MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt"
cp "${TGNET_NOTICE_PATH}" "${RESOURCES_DIR}/licenses/TGNet-User-Provided-Checkpoint-NOTICE.txt"
cp "${FPSAMPLE_NOTICE_PATH}" "${RESOURCES_DIR}/licenses/fpsample-1.0.2-MIT-and-nanoflann-BSD.txt"
cp "${DCM2NIIX_LICENSE_SOURCE_PATH}" "${RESOURCES_DIR}/licenses/dcm2niix-license.txt"
if [[ -n "${DCM2NIIX_BUILD_RECEIPT_PATH}" ]]; then
  cp "${DCM2NIIX_BUILD_RECEIPT_PATH}" "${RESOURCES_DIR}/licenses/dcm2niix-build-provenance.json"
  cp "${DCM2NIIX_ARTIFACT_POINTER_PATH}" "${RESOURCES_DIR}/licenses/dcm2niix-current-artifact.json"
fi
for license_path in "${NORMALIZER_ARTIFACT_DIR}/licenses"/*; do
  cp "${license_path}" "${RESOURCES_DIR}/licenses/$(basename "${license_path}")"
done
cp "${NORMALIZER_BUILD_RECEIPT_PATH}" "${RESOURCES_DIR}/licenses/dicom-normalizer-build-provenance.json"
cp "${GDCM_BUILD_RECEIPT_PATH}" "${RESOURCES_DIR}/licenses/gdcm-build-provenance.json"
rsync -a "${ROOT}/resources/sample1/" "${RESOURCES_DIR}/sample1/"
rsync -a "${ROOT}/resources/model_comparison/" "${RESOURCES_DIR}/model_comparison/"
chmod 755 "${RESOURCES_DIR}/bin/totalsegmentator-wrapper-dicom-normalizer"
chmod 755 "${RESOURCES_DIR}/bin/dcm2niix"
if [[ "$(sha256_file "${RESOURCES_DIR}/bin/totalsegmentator-wrapper-dicom-normalizer")" != "${NORMALIZER_INPUT_SHA256}" ]]; then
  echo "Copied DICOM normalizer differs from its declared build input." >&2
  exit 1
fi
if [[ "$(sha256_file "${RESOURCES_DIR}/bin/dcm2niix")" != "${DCM2NIIX_INPUT_SHA256}" ]]; then
  echo "Copied dcm2niix differs from its declared build input." >&2
  exit 1
fi
if [[ "$(sha256_file "${RESOURCES_DIR}/licenses/dcm2niix-license.txt")" != "${DCM2NIIX_LICENSE_INPUT_SHA256}" ]]; then
  echo "Copied dcm2niix license differs from its verified artifact input." >&2
  exit 1
fi

if [[ -n "${PYTHON_RUNTIME_SOURCE}" ]]; then
  mkdir -p "${RESOURCES_DIR}/python"
  rsync -a "${PYTHON_RUNTIME_SOURCE}/" "${RESOURCES_DIR}/python/cpython-3.12/"
  BUNDLED_PYTHON_RUNTIME_ROOT="${RESOURCES_DIR}/python/cpython-3.12"
  bundled_site_packages="${RESOURCES_DIR}/python/cpython-3.12/lib/python3.12/site-packages"
  if [[ -L "${bundled_site_packages}" && ! -e "${bundled_site_packages}" ]]; then
    rm "${bundled_site_packages}"
  fi
  chmod 755 "${RESOURCES_DIR}/python/cpython-3.12/bin/python3.12"
  # This is intentionally a copied, pre-sign runtime payload fingerprint.
  # Code signing may add signature bytes to Mach-O files afterwards; final app
  # bytes are instead attested by codesign/notarization verification.
  verify_copied_python_runtime_smoke "${BUNDLED_PYTHON_RUNTIME_ROOT}"
  PYTHON_RUNTIME_FINGERPRINT="$("${PYTHON_BIN}" "${PYTHON_RUNTIME_FINGERPRINT_SCRIPT}" \
    --runtime-root "${BUNDLED_PYTHON_RUNTIME_ROOT}" --fingerprint)"
  require_sha256_digest "${PYTHON_RUNTIME_FINGERPRINT}" "Copied Python runtime fingerprint"
  PYTHON_RUNTIME_FINGERPRINT_SCOPE="copied-runtime-payload-pre-sign-v1"
  PYTHON_RUNTIME_BUILD_ID_PROVENANCE="python-runtime-${PYTHON_RUNTIME_FINGERPRINT:0:12}"
fi

if [[ -z "${BUILD_ID}" ]]; then
  BUILD_ID="app-${APP_VERSION}-${WHEEL_SHA256:0:12}-${FPSAMPLE_WHEEL_SHA256:0:12}-${ACVL_UTILS_WHEEL_SHA256:0:12}-${SETUP_WEIGHTS_MANIFEST_SHA256:0:12}-${CONSTRAINTS_SHA256:0:12}-${NORMALIZER_INPUT_SHA256:0:12}-${DCM2NIIX_INPUT_SHA256:0:12}-${SAMPLE1_MANIFEST_SHA256:0:12}-${SWIFT_SOURCE_SHA256:0:12}-${PYTHON_RUNTIME_BUILD_ID_PROVENANCE}"
fi
if [[ "${RELEASE_DEPENDENCY_LOCK_ATTESTED}" == "1" ]]; then
  BUILD_ID="${BUILD_ID}-lock-${REQUIREMENTS_LOCK_SHA256:0:12}-metadata-${DEPENDENCY_LOCK_METADATA_SHA256:0:12}-project-${PROJECT_FILE_SHA256:0:12}-wheelhouse-${DEPENDENCY_WHEELHOUSE_MANIFEST_SHA256:0:12}"
fi
if [[ "${RELEASE_BUILD_TOOLCHAIN_ATTESTED}" == "1" ]]; then
  BUILD_ID="${BUILD_ID}-build-toolchain-${RELEASE_BUILD_TOOLCHAIN_LOCK_SHA256:0:12}-metadata-${RELEASE_BUILD_TOOLCHAIN_METADATA_SHA256:0:12}-receipt-${RELEASE_BUILD_TOOLCHAIN_RECEIPT_SHA256:0:12}"
fi

LICENSE_INVENTORY_ARGS=(
  "${LICENSE_INVENTORY_SCRIPT}"
  --output-dir "${RESOURCES_DIR}/licenses"
  --dependency-set-id "${DEPENDENCY_SET_ID}"
  --manual-overrides "${LICENSE_MANUAL_OVERRIDES_PATH}"
  --first-party-package "totalsegmentator-wrapper-mac"
  --fail-on-unresolved
)
if [[ "${PYTHON_RUNTIME_BUNDLED_JSON}" == "true" ]]; then
  LICENSE_INVENTORY_ARGS+=(--python-runtime-root "${PYTHON_RUNTIME_SOURCE}")
fi
LICENSE_INVENTORY_BASE_PYTHON="${PYTHON_BIN}"
if [[ -n "${PYTHON_RUNTIME_SOURCE}" ]]; then
  LICENSE_INVENTORY_BASE_PYTHON="${PYTHON_RUNTIME_SOURCE}/bin/python3.12"
fi
if [[ "${RELEASE_DEPENDENCY_LOCK_ATTESTED}" == "1" && -n "${LICENSE_SITE_PACKAGES}" ]]; then
  echo "Developer ID/notarized builds cannot use TOTALSEGMENTATOR_WRAPPER_MAC_LICENSE_SITE_PATH; generate the inventory from the canonical hashed dependency lock." >&2
  exit 2
fi
if [[ "${RELEASE_DEPENDENCY_LOCK_ATTESTED}" == "1" && "${ALLOW_DEVELOPMENT_LICENSE_INVENTORY}" == "1" ]]; then
  echo "Developer ID/notarized builds cannot use the development-only license inventory path." >&2
  exit 2
fi
if [[ "${RELEASE_DEPENDENCY_LOCK_ATTESTED}" == "1" ]]; then
  if [[ ! -f "${REQUIREMENTS_LOCK_PATH}" || -L "${REQUIREMENTS_LOCK_PATH}" ]]; then
    echo "Canonical requirements lock is required to generate the release third-party license inventory: ${REQUIREMENTS_LOCK_PATH}" >&2
    exit 2
  fi
  remove_owned_dist_child_directory "${LICENSE_INVENTORY_ENV_DIR}" "license_inventory_env"
  mkdir -p "${LICENSE_INVENTORY_ENV_DIR}/home" "${LICENSE_INVENTORY_ENV_DIR}/tmp" "${LICENSE_INVENTORY_ENV_DIR}/pip-cache"
  run_isolated_inventory_python "${LICENSE_INVENTORY_BASE_PYTHON}" -m venv "${LICENSE_INVENTORY_ENV_DIR}"
  LICENSE_INVENTORY_ENV_PYTHON="${LICENSE_INVENTORY_ENV_DIR}/bin/python"
  run_isolated_inventory_pip "${LICENSE_INVENTORY_ENV_PYTHON}" install \
    --no-index \
    --no-deps \
    "${FPSAMPLE_WHEEL_PATH}" "${ACVL_UTILS_WHEEL_PATH}" >/dev/null
  run_isolated_inventory_pip "${LICENSE_INVENTORY_ENV_PYTHON}" install \
    --no-index \
    --find-links "${OFFLINE_DEPENDENCY_WHEEL_DIRECTORY}" \
    --require-hashes \
    --no-deps \
    --only-binary :all: \
    -r "${REQUIREMENTS_LOCK_PATH}" >/dev/null
  run_isolated_inventory_pip "${LICENSE_INVENTORY_ENV_PYTHON}" install \
    --no-index --no-deps "${WHEEL_PATH}" >/dev/null
  run_isolated_inventory_pip "${LICENSE_INVENTORY_ENV_PYTHON}" check >/dev/null
  LICENSE_SITE_PACKAGES="$(run_isolated_inventory_python "${LICENSE_INVENTORY_ENV_PYTHON}" -c 'import site; print(next(path for path in site.getsitepackages() if path.endswith("site-packages")))')"
  LICENSE_INVENTORY_MODE="release_hashed_lock"
  LICENSE_INVENTORY_RELEASE_ELIGIBLE_JSON="true"
elif [[ -n "${LICENSE_SITE_PACKAGES}" ]]; then
  LICENSE_INVENTORY_MODE="development_explicit_site_path"
else
  if [[ "${ALLOW_DEVELOPMENT_LICENSE_INVENTORY}" != "1" ]]; then
    echo "Development builds without TOTALSEGMENTATOR_WRAPPER_MAC_LICENSE_SITE_PATH require TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_DEVELOPMENT_LICENSE_INVENTORY=1 to create a development-only constraints inventory." >&2
    exit 2
  fi
  remove_owned_dist_child_directory "${LICENSE_INVENTORY_ENV_DIR}" "license_inventory_env"
  mkdir -p "${LICENSE_INVENTORY_ENV_DIR}/home" "${LICENSE_INVENTORY_ENV_DIR}/tmp" "${LICENSE_INVENTORY_ENV_DIR}/pip-cache"
  run_isolated_inventory_python "${LICENSE_INVENTORY_BASE_PYTHON}" -m venv "${LICENSE_INVENTORY_ENV_DIR}"
  LICENSE_INVENTORY_ENV_PYTHON="${LICENSE_INVENTORY_ENV_DIR}/bin/python"
  run_isolated_inventory_pip "${LICENSE_INVENTORY_ENV_PYTHON}" install \
    --no-deps "${FPSAMPLE_WHEEL_PATH}" "${ACVL_UTILS_WHEEL_PATH}" >/dev/null
  run_isolated_inventory_pip "${LICENSE_INVENTORY_ENV_PYTHON}" install \
    --find-links "${DIST_DIR}" \
    --only-binary :all: \
    -c "${CONSTRAINTS_PATH}" \
    "${WHEEL_PATH}[dicom,mps,dentalseg,toothseg,ios-meshsegnet]" >/dev/null
  run_isolated_inventory_pip "${LICENSE_INVENTORY_ENV_PYTHON}" check >/dev/null
  LICENSE_SITE_PACKAGES="$(run_isolated_inventory_python "${LICENSE_INVENTORY_ENV_PYTHON}" -c 'import site; print(next(path for path in site.getsitepackages() if path.endswith("site-packages")))')"
  LICENSE_INVENTORY_MODE="development_constraints"
fi
if [[ ! -d "${LICENSE_SITE_PACKAGES}" ]]; then
  echo "License inventory site-packages directory is missing: ${LICENSE_SITE_PACKAGES}" >&2
  exit 1
fi
LICENSE_INVENTORY_ARGS+=(--site-path "${LICENSE_SITE_PACKAGES}")
run_isolated_inventory_python "${PYTHON_BIN}" "${LICENSE_INVENTORY_ARGS[@]}" >/dev/null
LICENSE_INVENTORY_JSON="${RESOURCES_DIR}/licenses/third_party_license_inventory.json"
LICENSE_UNRESOLVED_COUNT="$(run_isolated_inventory_python "${PYTHON_BIN}" -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["unresolved_count"])' "${LICENSE_INVENTORY_JSON}")"
LICENSE_GENERATED_AT_JSON="$(run_isolated_inventory_python "${PYTHON_BIN}" -c 'import json, sys; print(json.dumps(json.load(open(sys.argv[1], encoding="utf-8"))["generated_at"]))' "${LICENSE_INVENTORY_JSON}")"

cat > "${CONTENTS_DIR}/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>TotalSegmentator Wrapper for Mac</string>
  <key>CFBundleExecutable</key>
  <string>TotalSegmentatorWrapperForMac</string>
  <key>CFBundleIdentifier</key>
  <string>${BUNDLE_IDENTIFIER}</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>TotalSegmentator Wrapper for Mac</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>${APP_VERSION}</string>
  <key>CFBundleVersion</key>
  <string>${APP_VERSION}</string>
  <key>NSHumanReadableCopyright</key>
  <string>Copyright 2026 TotalSegmentator Wrapper for Mac contributors. Apache-2.0.</string>
  <key>LSMinimumSystemVersion</key>
  <string>${MINIMUM_MACOS_VERSION}</string>
  <key>LSArchitecturePriority</key>
  <array>
    <string>arm64</string>
  </array>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

require_release_source_unchanged

cat > "${RESOURCES_DIR}/setup_manifest.json" <<JSON
{
  "schema": "totalsegmentator_wrapper_mac.mac_app_manifest.v1",
  "app_name": "TotalSegmentator Wrapper for Mac",
  "version": "${APP_VERSION}",
  "app_version": "${APP_VERSION}",
  "ui_frontend": "swiftui",
  "build_id": "${BUILD_ID}",
  "source_commit": "${SOURCE_COMMIT}",
  "source_tree_dirty": ${SOURCE_TREE_DIRTY_JSON},
  "architecture": "arm64",
  "minimum_macos_version": "${MINIMUM_MACOS_VERSION}",
  "dependency_set_id": "${DEPENDENCY_SET_ID}",
  "bundle_identifier": ${BUNDLE_IDENTIFIER_JSON},
  "team_identifier": ${TEAM_IDENTIFIER_JSON},
  "bundle_identity_status": "${BUNDLE_IDENTITY_STATUS}",
  "license": {
    "expression": "Apache-2.0",
    "text": "LICENSE",
    "notice": "NOTICE",
    "scope": "First-party wrapper code, documentation, and resources except where separately noted"
  },
  "signing_mode": "${SIGNING_MODE}",
  "notarization_credentials_configured": ${NOTARIZATION_CREDENTIALS_CONFIGURED_JSON},
  "update_manifest_url": ${UPDATE_MANIFEST_URL_JSON},
  "update_allowed_hosts": ${UPDATE_ALLOWED_HOSTS_JSON},
  "wheel_sha256": "${WHEEL_SHA256}",
  "fpsample_wheel_sha256": "${FPSAMPLE_WHEEL_SHA256}",
  "fpsample_pre_sign_wheel_sha256": ${FPSAMPLE_PRE_SIGN_WHEEL_SHA256_JSON},
  "acvl_utils_wheel_sha256": "${ACVL_UTILS_WHEEL_SHA256}",
  "setup_weights_manifest_sha256": "${SETUP_WEIGHTS_MANIFEST_SHA256}",
  "constraints_sha256": "${CONSTRAINTS_SHA256}",
  "requirements_lock_sha256": ${REQUIREMENTS_LOCK_SHA256_JSON},
  "dependency_lock_metadata_sha256": ${DEPENDENCY_LOCK_METADATA_SHA256_JSON},
  "dependency_wheelhouse_manifest_sha256": ${DEPENDENCY_WHEELHOUSE_MANIFEST_SHA256_JSON},
  "project_file_sha256": ${PROJECT_FILE_SHA256_JSON},
  "release_build_toolchain_lock_sha256": ${RELEASE_BUILD_TOOLCHAIN_LOCK_SHA256_JSON},
  "release_build_toolchain_metadata_sha256": ${RELEASE_BUILD_TOOLCHAIN_METADATA_SHA256_JSON},
  "release_build_toolchain_receipt_sha256": ${RELEASE_BUILD_TOOLCHAIN_RECEIPT_SHA256_JSON},
  "release_build_toolchain": ${RELEASE_BUILD_TOOLCHAIN_PROVENANCE_JSON},
  "normalizer_input_sha256": "${NORMALIZER_INPUT_SHA256}",
  "normalizer_sha256": "${NORMALIZER_INPUT_SHA256}",
  "normalizer_sha256_scope": "build-input-before-copy-and-code-sign-v1",
  "normalizer_source": ${NORMALIZER_SOURCE_JSON},
  "dcm2niix_input_sha256": "${DCM2NIIX_INPUT_SHA256}",
  "dcm2niix_sha256": "${DCM2NIIX_INPUT_SHA256}",
  "dcm2niix_sha256_scope": "build-input-before-copy-and-code-sign-v1",
  "dcm2niix_version": ${DCM2NIIX_VERSION_JSON},
  "dcm2niix_source": ${DCM2NIIX_SOURCE_JSON},
  "sample1_manifest_sha256": "${SAMPLE1_MANIFEST_SHA256}",
  "swift_source_sha256": "${SWIFT_SOURCE_SHA256}",
  "python_runtime_fingerprint": "${PYTHON_RUNTIME_FINGERPRINT}",
  "python_runtime": {
    "strategy": "${PYTHON_RUNTIME_STRATEGY}",
    "env": "TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312",
    "python_executable": ${PYTHON_RUNTIME_EXECUTABLE_JSON},
    "bundled": ${PYTHON_RUNTIME_BUNDLED_JSON},
    "bundle_path": ${PYTHON_RUNTIME_BUNDLE_JSON},
    "fingerprint": "${PYTHON_RUNTIME_FINGERPRINT}",
    "fingerprint_scope": "${PYTHON_RUNTIME_FINGERPRINT_SCOPE}",
    "required_major": 3,
    "required_minor": 12
  },
  "third_party_licenses": {
    "inventory": "licenses/third_party_license_inventory.json",
    "summary": "licenses/THIRD_PARTY_LICENSES.txt",
    "dependency_set_id": "${DEPENDENCY_SET_ID}",
    "inventory_mode": "${LICENSE_INVENTORY_MODE}",
    "release_eligible": ${LICENSE_INVENTORY_RELEASE_ELIGIBLE_JSON},
    "generated_at": ${LICENSE_GENERATED_AT_JSON},
    "unresolved_count": ${LICENSE_UNRESOLVED_COUNT}
  },
  "permission_policy": {
    "requires_admin": false,
    "writes_system_locations": false,
    "uses_homebrew": false,
    "user_selected_input_only": true,
    "app_support_directory": "~/Library/Application Support/TotalSegmentatorWrapperMac"
  },
  "bundled": {
    "wrapper_license": "LICENSE",
    "wrapper_notice": "NOTICE",
    "wheel": "$(basename "${WHEEL_PATH}")",
    "fpsample_wheel": "wheels/$(basename "${FPSAMPLE_WHEEL_PATH}")",
    "acvl_utils_wheel": "wheels/$(basename "${ACVL_UTILS_WHEEL_PATH}")",
    "totalseg_setup_weights_manifest": "totalseg_setup_weights_manifest.json",
    "constraints": "constraints/macos-arm64-py312.txt",
    "requirements_lock": ${REQUIREMENTS_LOCK_BUNDLED_JSON},
    "dependency_lock_metadata": ${DEPENDENCY_LOCK_METADATA_BUNDLED_JSON},
    "dependency_wheelhouse_manifest": ${DEPENDENCY_WHEELHOUSE_MANIFEST_BUNDLED_JSON},
    "project_file": ${PROJECT_FILE_BUNDLED_JSON},
    "release_build_toolchain_lock": ${RELEASE_BUILD_TOOLCHAIN_LOCK_BUNDLED_JSON},
    "release_build_toolchain_metadata": ${RELEASE_BUILD_TOOLCHAIN_METADATA_BUNDLED_JSON},
    "release_build_toolchain_receipt": ${RELEASE_BUILD_TOOLCHAIN_RECEIPT_BUNDLED_JSON},
    "dicom_normalizer": "bin/totalsegmentator-wrapper-dicom-normalizer",
    "dicom_normalizer_linkage": "static-gdcm-3.2.7",
    "dicom_normalizer_build_provenance": "licenses/dicom-normalizer-build-provenance.json",
    "gdcm_build_provenance": "licenses/gdcm-build-provenance.json",
    "gdcm_static_license_inventory": "licenses/GDCM-static-license-inventory.json",
    "dcm2niix": "bin/dcm2niix",
    "dcm2niix_build_provenance": ${DCM2NIIX_BUNDLED_RECEIPT_JSON},
    "dcm2niix_artifact_pointer": ${DCM2NIIX_BUNDLED_POINTER_JSON},
    "totalsegmentator_license": "licenses/TotalSegmentator-Apache-2.0.txt",
    "totalsegmentator_task_inventory": "licenses/TotalSegmentator-task-inventory.json",
    "dentalsegmentator_notice": "licenses/DentalSegmentator-NOTICE.txt",
    "toothseg_notice": "licenses/ToothSeg-NOTICE.txt",
    "meshsegnet_checkpoint_notice": "licenses/MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt",
    "tgnet_checkpoint_policy_notice": "licenses/TGNet-User-Provided-Checkpoint-NOTICE.txt",
    "fpsample_license": "licenses/fpsample-1.0.2-MIT-and-nanoflann-BSD.txt",
    "dcm2niix_license": "licenses/dcm2niix-license.txt",
    "third_party_license_inventory": "licenses/third_party_license_inventory.json",
    "third_party_license_summary": "licenses/THIRD_PARTY_LICENSES.txt",
    "sample1": {
      "root": "sample1",
      "input": "sample1/input/owner_cbct_jawcrop_0p5mm.nii.gz",
      "surface_preview": "sample1/surface_preview/index.html",
      "precomputed_teeth_labelmap": "sample1/teeth_result/toothseg_fdi_multilabel_0p5mm.nii.gz",
      "manifest": "sample1/sample_manifest.json",
      "notices": "sample1/THIRD_PARTY_NOTICES.txt"
    },
    "model_comparison": {
      "root": "model_comparison",
      "provenance": "model_comparison/ASSET_PROVENANCE.json",
      "totalsegmentator": "model_comparison/totalseg.png",
      "dentalsegmentator": "model_comparison/dentalseg.png",
      "individual_teeth_beta": "model_comparison/individual.png",
      "toothseg": "model_comparison/toothseg.png"
    }
  },
  "notarized": ${NOTARIZED_JSON}
}
JSON

cat > "${RESOURCES_DIR}/THIRD_PARTY_NOTICES.txt" <<TXT
TotalSegmentator Wrapper for Mac third-party notices

TotalSegmentator Wrapper for Mac is an unofficial Mac wrapper powered by TotalSegmentator.
It is not the official TotalSegmentator application or project.

Wrapper license
- The wrapper's original code, documentation, and first-party resources are Apache-2.0.
- License: Contents/Resources/LICENSE
- Notice and scope boundary: Contents/Resources/NOTICE
- Third-party software, models, sample data, derived sample/model images, and marks
  retain their respective terms and are not relicensed by the wrapper.

License inventory
- Inventory JSON: Contents/Resources/licenses/third_party_license_inventory.json
- License summary: Contents/Resources/licenses/THIRD_PARTY_LICENSES.txt
- Unresolved license items at build time: ${LICENSE_UNRESOLVED_COUNT}

TotalSegmentator
- Upstream: https://github.com/wasserth/TotalSegmentator
- License: Apache-2.0
- Audited application tasks: craniofacial_structures (115), teeth (113)
- Robust-crop helper model: open total 3 mm task ID 297
- Bundled license text: Contents/Resources/licenses/TotalSegmentator-Apache-2.0.txt

DentalSegmentator
- Model source: https://doi.org/10.5281/zenodo.10829675
- Creator: Gauthier Dot
- Separately downloaded model license: CC BY 4.0
- Attribution, license URL, checksum, and change status:
  Contents/Resources/licenses/DentalSegmentator-NOTICE.txt

ToothSeg
- Upstream: https://github.com/MIC-DKFZ/ToothSeg
- Code license: Apache-2.0
- Separately downloaded model license: CC BY 4.0
- Attribution and model DOI: Contents/Resources/licenses/ToothSeg-NOTICE.txt

MeshSegNet Teeth3DS checkpoint
- The checkpoint is stored separately and is not bundled in the app.
- Canonical model source, pinned revision, SHA-256, and declared Apache-2.0 license:
  Contents/Resources/licenses/MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt

TGNet user-provided checkpoints
- source: user-provided
- license: not-verified
- TGNet checkpoints are not bundled, automatically downloaded, or redistributed by this app.
- Users obtain the specified compatible checkpoint set themselves from the distribution page linked by the app and must review the distributor's terms.
- Policy boundary and pinned compatibility identifiers:
  Contents/Resources/licenses/TGNet-User-Provided-Checkpoint-NOTICE.txt

fpsample
- A prebuilt CPython 3.12 / Apple Silicon wheel is bundled so user Macs do not
  need Xcode Command Line Tools, CMake, or a C++ compiler during setup.
- fpsample license: MIT. Its bundled nanoflann header uses the BSD license.
- License texts and source archive SHA-256:
  Contents/Resources/licenses/fpsample-1.0.2-MIT-and-nanoflann-BSD.txt

acvl-utils
- A pinned pure-Python wheel is built from the official PyPI 0.2.6 source
  archive and bundled so setup can reject all source-distribution installs.
- License: Apache-2.0. The wheel-carried license is copied into the generated
  dependency inventory under Contents/Resources/licenses/python-packages/.

dcm2niix
- Bundled executable: Contents/Resources/bin/dcm2niix
- Version line: $(printf '%s' "${DCM2NIIX_VERSION_JSON}" | "${PYTHON_BIN}" -c 'import json, sys; print(json.load(sys.stdin))')
- Build-input SHA256 (before copy and code signing): ${DCM2NIIX_INPUT_SHA256}
- Upstream: https://github.com/rordenlab/dcm2niix
- Bundled license text: Contents/Resources/licenses/dcm2niix-license.txt
${DCM2NIIX_PROVENANCE_NOTICE}

GDCM DICOM runtime
- Bundled runtime: Contents/Resources/bin/totalsegmentator-wrapper-dicom-normalizer
- GDCM 3.2.7 and its enabled internal codecs are statically linked; gdcmconv is not bundled.
- Normalizer source-build receipt: Contents/Resources/licenses/dicom-normalizer-build-provenance.json
- Pinned GDCM source-build receipt: Contents/Resources/licenses/gdcm-build-provenance.json
- GDCM license: Contents/Resources/licenses/GDCM-BSD-3-Clause.txt
- GDCM IJG JPEG notice: Contents/Resources/licenses/GDCM-IJG-JPEG-README.txt
- OpenJPEG license: Contents/Resources/licenses/OpenJPEG-BSD-2-Clause.txt
- CharLS license: Contents/Resources/licenses/CharLS-BSD-3-Clause.txt
- Expat license: Contents/Resources/licenses/Expat-MIT.txt
- zlib license: Contents/Resources/licenses/zlib-Zlib.txt
- UUID license: Contents/Resources/licenses/GDCM-UUID-BSD-3-Clause.txt
- Source-path and digest inventory: Contents/Resources/licenses/GDCM-static-license-inventory.json

Sample 1 provenance and notices remain in Contents/Resources/sample1/THIRD_PARTY_NOTICES.txt.
Comparison images in Contents/Resources/model_comparison are non-clinical preview
renders derived from rights-holder-authorized CT data and model outputs. The
Sample 1 provenance and the applicable TotalSegmentator, DentalSegmentator, or
ToothSeg model notice apply. Exact hashes and generators are recorded in
Contents/Resources/model_comparison/ASSET_PROVENANCE.json.
TotalSegmentator Wrapper for Mac is a non-clinical preview and is not for diagnosis or treatment planning.
TXT

"${PYTHON_BIN}" "${MACHO_LINKAGE_VERIFY_SCRIPT}" --app "${APP_DIR}" >/dev/null

if command -v xattr >/dev/null 2>&1; then
  find "${APP_DIR}" -type d -exec chmod u+rwx,go+rx {} +
  find "${APP_DIR}" -type f -exec chmod u+rw {} +
  xattr -cr "${APP_DIR}" || true
fi
if [[ "${SIGNING_MODE}" == "developer-id" && "${SKIP_CODESIGN:-0}" == "1" ]]; then
  echo "Developer ID builds cannot set SKIP_CODESIGN=1." >&2
  exit 2
fi
if [[ "${SKIP_CODESIGN:-0}" != "1" ]] && command -v codesign >/dev/null 2>&1; then
  if [[ "${SIGNING_MODE}" == "developer-id" ]]; then
    codesign_developer_id
  else
    if [[ -d "${RESOURCES_DIR}/python/cpython-3.12" ]]; then
      find "${RESOURCES_DIR}/python/cpython-3.12" -type d -exec chmod u+rwx,go+rx {} +
      find "${RESOURCES_DIR}/python/cpython-3.12" -type f -exec chmod a-w {} +
    fi
    codesign --force --deep --sign - "${APP_DIR}" >/dev/null
  fi
fi

VERIFY_DISTRIBUTION_ARGS=(
  --source "${ROOT}"
  --app "${APP_DIR}"
  --expected-version "${APP_VERSION}"
)
if [[ "${SIGNING_MODE}" == "developer-id" ]]; then
  VERIFY_DISTRIBUTION_ARGS+=(--expected-source-commit "${SOURCE_COMMIT}")
fi
"${PYTHON_BIN}" "${ROOT}/scripts/verify_license_distribution.py" \
  "${VERIFY_DISTRIBUTION_ARGS[@]}"

echo "${APP_DIR}"
