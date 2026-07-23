#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR:-${ROOT}/dist}"
APP_NAME="TotalSegmentator Wrapper for Mac"
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
PYTHON_RUNTIME_SOURCE="${TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_PYTHON_RUNTIME_DIR:-${PYTHON_RUNTIME_DIR:-}}"
PYTHON_RUNTIME_STRATEGY="external_python312_required"
PYTHON_RUNTIME_EXECUTABLE_JSON="null"
PYTHON_RUNTIME_BUNDLED_JSON="false"
PYTHON_RUNTIME_BUNDLE_JSON="null"
APP_VERSION="${TOTALSEGMENTATOR_WRAPPER_MAC_APP_VERSION:-0.2.0}"
BUILD_ID="${TOTALSEGMENTATOR_WRAPPER_MAC_BUILD_ID:-}"
DEPENDENCY_SET_ID="${TOTALSEGMENTATOR_WRAPPER_MAC_DEPENDENCY_SET_ID:-macos-arm64-py312-torch2.12-totalseg2.14.0-nnunetv2.8.1-pydicom3-gdcm3.2-toothseg-acvl0.2-scipy1}"
UPDATE_MANIFEST_URL="${TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_MANIFEST_URL:-}"
UPDATE_ALLOWED_HOSTS="${TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_ALLOWED_HOSTS:-}"
XCODE_DEVELOPER_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_XCODE_DEVELOPER_DIR:-}"
DCM2NIIX_PATH="${TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX:-}"
SIGNING_MODE="${TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE:-ad-hoc}"
CODESIGN_IDENTITY="${TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY:-}"
BUNDLE_IDENTIFIER="${TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER:-jp.chino.totalsegmentator.wrapper.mac}"
NOTARY_PROFILE="${TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_PROFILE:-}"
APP_ENTITLEMENTS="${ROOT}/resources/entitlements/app.entitlements"
PYTHON_ENTITLEMENTS="${ROOT}/resources/entitlements/python-runtime.entitlements"
TOTALSEGMENTATOR_LICENSE_PATH="${ROOT}/resources/third_party/licenses/TotalSegmentator-Apache-2.0.txt"
TOOTHSEG_NOTICE_PATH="${ROOT}/resources/third_party/licenses/ToothSeg-NOTICE.txt"
DCM2NIIX_LICENSE_PATH="${ROOT}/resources/third_party/licenses/dcm2niix-license.txt"
DICOM_RUNTIME_LICENSE_PATHS=(
  "${ROOT}/resources/third_party/licenses/GDCM-BSD-3-Clause.txt"
  "${ROOT}/resources/third_party/licenses/GDCM-IJG-JPEG-README.txt"
  "${ROOT}/resources/third_party/licenses/OpenJPEG-BSD-2-Clause.txt"
  "${ROOT}/resources/third_party/licenses/CharLS-BSD-3-Clause.txt"
  "${ROOT}/resources/third_party/licenses/json-c-MIT.txt"
  "${ROOT}/resources/third_party/licenses/OpenSSL-Apache-2.0.txt"
)
LICENSE_MANUAL_OVERRIDES_PATH="${ROOT}/resources/third_party/licenses/manual-overrides.json"
LICENSE_INVENTORY_SCRIPT="${ROOT}/scripts/generate_third_party_license_inventory.py"
LICENSE_INVENTORY_ENV_DIR="${DIST_DIR}/license_inventory_env"
LICENSE_SITE_PACKAGES="${TOTALSEGMENTATOR_WRAPPER_MAC_LICENSE_SITE_PATH:-}"

json_string() {
  "${PYTHON_BIN}" -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"
}

json_string_list() {
  "${PYTHON_BIN}" -c 'import json, sys; print(json.dumps([part.strip() for part in sys.argv[1].split(",") if part.strip()]))' "$1"
}

sha256_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

first_json_line() {
  "${PYTHON_BIN}" -c 'import json, sys; print(json.dumps(next((line.strip() for line in sys.stdin if line.strip()), "")))'
}

require_developer_id_signing() {
  if [[ "${SIGNING_MODE}" != "ad-hoc" && "${SIGNING_MODE}" != "developer-id" ]]; then
    echo "TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE must be ad-hoc or developer-id; got ${SIGNING_MODE}" >&2
    exit 2
  fi
  if [[ "${SIGNING_MODE}" != "developer-id" ]]; then
    return
  fi
  if [[ -z "${CODESIGN_IDENTITY}" ]]; then
    echo "TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY is required when TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE=developer-id." >&2
    exit 2
  fi
  if [[ -z "${TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER:-}" ]]; then
    echo "TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER is required when TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE=developer-id." >&2
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
  if [[ -z "${XCODE_DEVELOPER_DIR}" && -d "/Applications/Xcode.app/Contents/Developer" ]]; then
    XCODE_DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
  fi
  if [[ -n "${XCODE_DEVELOPER_DIR}" ]]; then
    export DEVELOPER_DIR="${XCODE_DEVELOPER_DIR}"
  fi
  if ! command -v xcodebuild >/dev/null 2>&1; then
    echo "xcodebuild is required to build the SwiftUI app frontend." >&2
    exit 2
  fi
  if ! xcodebuild -version >/dev/null 2>&1; then
    echo "Full Xcode is required to build the SwiftUI app frontend. Command Line Tools alone are not enough." >&2
    echo "Install Xcode and select it before running this build script." >&2
    exit 2
  fi
  local developer_dir
  developer_dir="$(xcode-select -p 2>/dev/null || true)"
  if [[ "${developer_dir}" == *CommandLineTools* ]]; then
    echo "Full Xcode must be selected to build the SwiftUI app frontend; current developer dir is ${developer_dir}." >&2
    echo "Set TOTALSEGMENTATOR_WRAPPER_MAC_XCODE_DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer or select full Xcode for this shell." >&2
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
  sdk_path="$(xcrun --sdk macosx --show-sdk-path)"
  mkdir -p "${SWIFT_MODULE_CACHE_PATH}"
  xcrun --sdk macosx swiftc \
    -O \
    -parse-as-library \
    -target arm64-apple-macos13.0 \
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
  while IFS= read -r path; do
    sign_targets+=("${path}")
  done < <(find "${RESOURCES_DIR}/bin/lib" -type f -name "*.dylib" -print | sort)
  sign_targets+=("${RESOURCES_DIR}/bin/totalsegmentator-wrapper-dicom-normalizer")
  if [[ -d "${RESOURCES_DIR}/python/cpython-3.12" ]]; then
    local python_framework_binary="${RESOURCES_DIR}/python/cpython-3.12/Frameworks/Python.framework/Versions/3.12/Python"
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
}

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
if [[ -z "${PYTHON_RUNTIME_SOURCE}" ]] && [[ "${TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_EXTERNAL_PYTHON_RUNTIME:-0}" != "1" ]]; then
  PYTHON_RUNTIME_SOURCE="$("${PYTHON_BIN}" -c 'import sys; print(sys.base_prefix)' 2>/dev/null || true)"
fi
if [[ -z "${PYTHON_RUNTIME_SOURCE}" ]] && [[ "${TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_EXTERNAL_PYTHON_RUNTIME:-0}" != "1" ]]; then
  echo "Could not discover a Python 3.12 runtime to bundle. Set TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_PYTHON_RUNTIME_DIR or TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_EXTERNAL_PYTHON_RUNTIME=1." >&2
  exit 1
fi

if [[ -z "${DCM2NIIX_PATH}" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX must point to a dcm2niix executable for packaged DICOM intake." >&2
  exit 2
fi
if [[ ! -x "${DCM2NIIX_PATH}" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX is not executable: ${DCM2NIIX_PATH}" >&2
  exit 2
fi

require_full_xcode
require_developer_id_signing
"${ROOT}/scripts/build_mac_wheel.sh" >/dev/null

WHEEL_PATH="$(ls -1t "${DIST_DIR}"/totalsegmentator_wrapper_mac-*.whl | head -n 1)"
NORMALIZER_PATH="${ROOT}/build/dicom_normalizer/totalsegmentator-wrapper-dicom-normalizer"
CONSTRAINTS_PATH="${ROOT}/constraints/macos-arm64-py312.txt"
SAMPLE1_MANIFEST_PATH="${ROOT}/resources/sample1/sample_manifest.json"
if [[ ! -f "${TOTALSEGMENTATOR_LICENSE_PATH}" ]]; then
  echo "TotalSegmentator Apache-2.0 license text is missing: ${TOTALSEGMENTATOR_LICENSE_PATH}" >&2
  exit 1
fi
if [[ ! -f "${DCM2NIIX_LICENSE_PATH}" ]]; then
  echo "dcm2niix license text is missing: ${DCM2NIIX_LICENSE_PATH}" >&2
  exit 1
fi
for license_path in "${DICOM_RUNTIME_LICENSE_PATHS[@]}"; do
  if [[ ! -f "${license_path}" ]]; then
    echo "DICOM runtime license text is missing: ${license_path}" >&2
    exit 1
  fi
done
if [[ ! -f "${LICENSE_MANUAL_OVERRIDES_PATH}" ]]; then
  echo "Manual license override manifest is missing: ${LICENSE_MANUAL_OVERRIDES_PATH}" >&2
  exit 1
fi
if [[ ! -x "${LICENSE_INVENTORY_SCRIPT}" && ! -f "${LICENSE_INVENTORY_SCRIPT}" ]]; then
  echo "Third-party license inventory script is missing: ${LICENSE_INVENTORY_SCRIPT}" >&2
  exit 1
fi
WHEEL_SHA256="$(sha256_file "${WHEEL_PATH}")"
CONSTRAINTS_SHA256="$(sha256_file "${CONSTRAINTS_PATH}")"
NORMALIZER_SHA256="$(sha256_file "${NORMALIZER_PATH}")"
SAMPLE1_MANIFEST_SHA256="$(sha256_file "${SAMPLE1_MANIFEST_PATH}")"
DCM2NIIX_SHA256="$(sha256_file "${DCM2NIIX_PATH}")"
SWIFT_SOURCE_SHA256="$(cat "${SWIFT_SOURCE_FILES[@]}" | shasum -a 256 | awk '{print $1}')"
DCM2NIIX_VERSION_JSON="$("${DCM2NIIX_PATH}" -h 2>&1 | awk 'BEGIN{fallback=""} /version|dcm2niix/{print; found=1; exit} NF && fallback==""{fallback=$0} END{if (!found) print fallback}' | first_json_line)"
DCM2NIIX_SOURCE_JSON="$(json_string "$(basename "${DCM2NIIX_PATH}")")"
if [[ -z "${BUILD_ID}" ]]; then
  BUILD_ID="app-${APP_VERSION}-${WHEEL_SHA256:0:12}-${CONSTRAINTS_SHA256:0:12}-${NORMALIZER_SHA256:0:12}-${DCM2NIIX_SHA256:0:12}-${SAMPLE1_MANIFEST_SHA256:0:12}-${SWIFT_SOURCE_SHA256:0:12}"
fi
UPDATE_MANIFEST_URL_JSON="$(json_string "${UPDATE_MANIFEST_URL}")"
UPDATE_ALLOWED_HOSTS_JSON="$(json_string_list "${UPDATE_ALLOWED_HOSTS}")"
BUNDLE_IDENTIFIER_JSON="$(json_string "${BUNDLE_IDENTIFIER}")"
NOTARY_PROFILE_JSON="$(json_string "${NOTARY_PROFILE}")"
NOTARIZED_JSON="false"
if [[ "${TOTALSEGMENTATOR_WRAPPER_MAC_NOTARIZED:-0}" == "1" ]]; then
  NOTARIZED_JSON="true"
fi

if [[ -d "${APP_DIR}" ]]; then
  chmod -R u+rwX "${APP_DIR}" || true
fi
rm -rf "${APP_DIR}"
mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}/wheels" "${RESOURCES_DIR}/bin" "${RESOURCES_DIR}/constraints" "${RESOURCES_DIR}/sample1" "${RESOURCES_DIR}/model_comparison" "${RESOURCES_DIR}/licenses"

build_swiftui_frontend
cp "${WHEEL_PATH}" "${RESOURCES_DIR}/wheels/"
cp "${CONSTRAINTS_PATH}" "${RESOURCES_DIR}/constraints/"
cp "${NORMALIZER_PATH}" "${RESOURCES_DIR}/bin/totalsegmentator-wrapper-dicom-normalizer"
cp -R "${ROOT}/build/dicom_normalizer/lib" "${RESOURCES_DIR}/bin/lib"
cp "${DCM2NIIX_PATH}" "${RESOURCES_DIR}/bin/dcm2niix"
cp "${TOTALSEGMENTATOR_LICENSE_PATH}" "${RESOURCES_DIR}/licenses/TotalSegmentator-Apache-2.0.txt"
cp "${TOOTHSEG_NOTICE_PATH}" "${RESOURCES_DIR}/licenses/ToothSeg-NOTICE.txt"
cp "${DCM2NIIX_LICENSE_PATH}" "${RESOURCES_DIR}/licenses/dcm2niix-license.txt"
for license_path in "${DICOM_RUNTIME_LICENSE_PATHS[@]}"; do
  cp "${license_path}" "${RESOURCES_DIR}/licenses/$(basename "${license_path}")"
done
rsync -a "${ROOT}/resources/sample1/" "${RESOURCES_DIR}/sample1/"
rsync -a "${ROOT}/resources/model_comparison/" "${RESOURCES_DIR}/model_comparison/"
chmod 755 "${RESOURCES_DIR}/bin/totalsegmentator-wrapper-dicom-normalizer"
chmod 755 "${RESOURCES_DIR}/bin/lib"/*.dylib
chmod 755 "${RESOURCES_DIR}/bin/dcm2niix"

if [[ -n "${PYTHON_RUNTIME_SOURCE}" ]]; then
  PYTHON_RUNTIME_SOURCE="${PYTHON_RUNTIME_SOURCE%/}"
  if [[ ! -x "${PYTHON_RUNTIME_SOURCE}/bin/python3.12" ]]; then
    echo "TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_PYTHON_RUNTIME_DIR must point to a Python 3.12 runtime root containing bin/python3.12" >&2
    exit 1
  fi
  mkdir -p "${RESOURCES_DIR}/python"
  rsync -a "${PYTHON_RUNTIME_SOURCE}/" "${RESOURCES_DIR}/python/cpython-3.12/"
  bundled_site_packages="${RESOURCES_DIR}/python/cpython-3.12/lib/python3.12/site-packages"
  if [[ -L "${bundled_site_packages}" && ! -e "${bundled_site_packages}" ]]; then
    rm "${bundled_site_packages}"
  fi
  chmod 755 "${RESOURCES_DIR}/python/cpython-3.12/bin/python3.12"
  PYTHON_RUNTIME_STRATEGY="bundled_python312"
  PYTHON_RUNTIME_EXECUTABLE_JSON='"python/cpython-3.12/bin/python3.12"'
  PYTHON_RUNTIME_BUNDLED_JSON="true"
  PYTHON_RUNTIME_BUNDLE_JSON='"python/cpython-3.12"'
fi

LICENSE_INVENTORY_ARGS=(
  "${LICENSE_INVENTORY_SCRIPT}"
  --output-dir "${RESOURCES_DIR}/licenses"
  --dependency-set-id "${DEPENDENCY_SET_ID}"
  --manual-overrides "${LICENSE_MANUAL_OVERRIDES_PATH}"
  --fail-on-unresolved
)
if [[ "${PYTHON_RUNTIME_STRATEGY}" == "bundled_python312" ]]; then
  LICENSE_INVENTORY_ARGS+=(--python-runtime-root "${PYTHON_RUNTIME_SOURCE}")
fi
LICENSE_INVENTORY_BASE_PYTHON="${PYTHON_BIN}"
if [[ -n "${PYTHON_RUNTIME_SOURCE}" ]]; then
  LICENSE_INVENTORY_BASE_PYTHON="${PYTHON_RUNTIME_SOURCE}/bin/python3.12"
fi
if [[ -z "${LICENSE_SITE_PACKAGES}" ]]; then
  rm -rf "${LICENSE_INVENTORY_ENV_DIR}"
  "${LICENSE_INVENTORY_BASE_PYTHON}" -m venv "${LICENSE_INVENTORY_ENV_DIR}"
  LICENSE_INVENTORY_ENV_PYTHON="${LICENSE_INVENTORY_ENV_DIR}/bin/python"
  "${LICENSE_INVENTORY_ENV_PYTHON}" -m pip install -c "${CONSTRAINTS_PATH}" "${WHEEL_PATH}[dicom,mps,dentalseg,toothseg]" >/dev/null
  LICENSE_SITE_PACKAGES="$("${LICENSE_INVENTORY_ENV_PYTHON}" -c 'import site; print(next(path for path in site.getsitepackages() if path.endswith("site-packages")))')"
fi
if [[ ! -d "${LICENSE_SITE_PACKAGES}" ]]; then
  echo "License inventory site-packages directory is missing: ${LICENSE_SITE_PACKAGES}" >&2
  exit 1
fi
LICENSE_INVENTORY_ARGS+=(--site-path "${LICENSE_SITE_PACKAGES}")
"${PYTHON_BIN}" "${LICENSE_INVENTORY_ARGS[@]}" >/dev/null
LICENSE_INVENTORY_JSON="${RESOURCES_DIR}/licenses/third_party_license_inventory.json"
LICENSE_UNRESOLVED_COUNT="$("${PYTHON_BIN}" -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["unresolved_count"])' "${LICENSE_INVENTORY_JSON}")"
LICENSE_GENERATED_AT_JSON="$("${PYTHON_BIN}" -c 'import json, sys; print(json.dumps(json.load(open(sys.argv[1], encoding="utf-8"))["generated_at"]))' "${LICENSE_INVENTORY_JSON}")"

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
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSArchitecturePriority</key>
  <array>
    <string>arm64</string>
  </array>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

cat > "${RESOURCES_DIR}/setup_manifest.json" <<JSON
{
  "schema": "totalsegmentator_wrapper_mac.mac_app_manifest.v1",
  "app_name": "TotalSegmentator Wrapper for Mac",
  "version": "${APP_VERSION}",
  "app_version": "${APP_VERSION}",
  "ui_frontend": "swiftui",
  "build_id": "${BUILD_ID}",
  "architecture": "arm64",
  "dependency_set_id": "${DEPENDENCY_SET_ID}",
  "bundle_identifier": ${BUNDLE_IDENTIFIER_JSON},
  "signing_mode": "${SIGNING_MODE}",
  "notarization_profile_name": ${NOTARY_PROFILE_JSON},
  "update_manifest_url": ${UPDATE_MANIFEST_URL_JSON},
  "update_allowed_hosts": ${UPDATE_ALLOWED_HOSTS_JSON},
  "wheel_sha256": "${WHEEL_SHA256}",
  "constraints_sha256": "${CONSTRAINTS_SHA256}",
  "normalizer_sha256": "${NORMALIZER_SHA256}",
  "dcm2niix_sha256": "${DCM2NIIX_SHA256}",
  "dcm2niix_version": ${DCM2NIIX_VERSION_JSON},
  "dcm2niix_source": ${DCM2NIIX_SOURCE_JSON},
  "sample1_manifest_sha256": "${SAMPLE1_MANIFEST_SHA256}",
  "swift_source_sha256": "${SWIFT_SOURCE_SHA256}",
  "python_runtime": {
    "strategy": "${PYTHON_RUNTIME_STRATEGY}",
    "env": "TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312",
    "python_executable": ${PYTHON_RUNTIME_EXECUTABLE_JSON},
    "bundled": ${PYTHON_RUNTIME_BUNDLED_JSON},
    "bundle_path": ${PYTHON_RUNTIME_BUNDLE_JSON},
    "required_major": 3,
    "required_minor": 12
  },
  "third_party_licenses": {
    "inventory": "licenses/third_party_license_inventory.json",
    "summary": "licenses/THIRD_PARTY_LICENSES.txt",
    "dependency_set_id": "${DEPENDENCY_SET_ID}",
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
    "wheel": "$(basename "${WHEEL_PATH}")",
    "constraints": "constraints/macos-arm64-py312.txt",
    "dicom_normalizer": "bin/totalsegmentator-wrapper-dicom-normalizer",
    "dicom_normalizer_libraries": "bin/lib",
    "dcm2niix": "bin/dcm2niix",
    "totalsegmentator_license": "licenses/TotalSegmentator-Apache-2.0.txt",
    "toothseg_notice": "licenses/ToothSeg-NOTICE.txt",
    "dcm2niix_license": "licenses/dcm2niix-license.txt",
    "third_party_license_inventory": "licenses/third_party_license_inventory.json",
    "third_party_license_summary": "licenses/THIRD_PARTY_LICENSES.txt",
    "sample1": {
      "root": "sample1",
      "input": "sample1/input/DZ-CBCT_jawcrop_0p5mm.nii.gz",
      "surface_preview": "sample1/surface_preview/index.html",
      "precomputed_teeth_labelmap": "sample1/teeth_result/teeth_multilabel_fullspace.nii.gz",
      "manifest": "sample1/sample_manifest.json",
      "notices": "sample1/THIRD_PARTY_NOTICES.txt"
    },
    "model_comparison": {
      "root": "model_comparison",
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

License inventory
- Inventory JSON: Contents/Resources/licenses/third_party_license_inventory.json
- License summary: Contents/Resources/licenses/THIRD_PARTY_LICENSES.txt
- Unresolved license items at build time: ${LICENSE_UNRESOLVED_COUNT}

TotalSegmentator
- Upstream: https://github.com/wasserth/TotalSegmentator
- License: Apache-2.0
- Bundled license text: Contents/Resources/licenses/TotalSegmentator-Apache-2.0.txt

ToothSeg
- Upstream: https://github.com/MIC-DKFZ/ToothSeg
- Code license: Apache-2.0
- Separately downloaded model license: CC BY 4.0
- Attribution and model DOI: Contents/Resources/licenses/ToothSeg-NOTICE.txt

dcm2niix
- Bundled executable: Contents/Resources/bin/dcm2niix
- Build input executable name: $(basename "${DCM2NIIX_PATH}")
- Version line: $(printf '%s' "${DCM2NIIX_VERSION_JSON}" | "${PYTHON_BIN}" -c 'import json, sys; print(json.load(sys.stdin))')
- SHA256: ${DCM2NIIX_SHA256}
- Upstream: https://github.com/rordenlab/dcm2niix
- Bundled license text: Contents/Resources/licenses/dcm2niix-license.txt

GDCM DICOM runtime
- Bundled runtime: Contents/Resources/bin/totalsegmentator-wrapper-dicom-normalizer
- Bundled libraries: Contents/Resources/bin/lib
- GDCM license: Contents/Resources/licenses/GDCM-BSD-3-Clause.txt
- GDCM IJG JPEG notice: Contents/Resources/licenses/GDCM-IJG-JPEG-README.txt
- OpenJPEG license: Contents/Resources/licenses/OpenJPEG-BSD-2-Clause.txt
- CharLS license: Contents/Resources/licenses/CharLS-BSD-3-Clause.txt
- json-c license: Contents/Resources/licenses/json-c-MIT.txt
- OpenSSL license: Contents/Resources/licenses/OpenSSL-Apache-2.0.txt

Sample 1 notices remain in Contents/Resources/sample1/THIRD_PARTY_NOTICES.txt.
Comparison images in Contents/Resources/model_comparison are non-clinical preview
renders derived from bundled Sample 1. The same Sample 1 source notices apply.
TotalSegmentator Wrapper for Mac is a non-clinical preview and is not for diagnosis or treatment planning.
TXT

if command -v xattr >/dev/null 2>&1; then
  find "${APP_DIR}" -type d -exec chmod u+rwx,go+rx {} +
  find "${APP_DIR}" -type f -exec chmod u+rw {} +
  xattr -cr "${APP_DIR}" || true
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

echo "${APP_DIR}"
