#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR:-${ROOT}/dist}"
if [[ "${DIST_DIR}" != "/" ]]; then
  DIST_DIR="${DIST_DIR%/}"
fi
APP_NAME="TotalSegmentator Wrapper for Mac"
APP_PATH="${DIST_DIR}/${APP_NAME}.app"
APP_VERSION_OVERRIDE="${TOTALSEGMENTATOR_WRAPPER_MAC_APP_VERSION:-}"
APP_VERSION=""
MINIMUM_MACOS_VERSION="14.0"
PYTHON_BIN="${PYTHON_BIN:-${ROOT}/.venv/bin/python}"
EXPECTED_SOURCE_COMMIT="${TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_SOURCE_COMMIT:-}"

path_has_safe_write_mode() {
  local mode
  mode="$(stat -f %Lp "$1" 2>/dev/null || true)"
  [[ "${mode}" =~ ^[0-7]{3,4}$ ]] && (( (8#${mode} & 8#22) == 0 ))
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
    if [[ ! -d "${parent}" || -L "${parent}" || ! -O "${parent}" ]] \
      || ! path_has_safe_write_mode "${parent}"; then
      echo "Distribution parent must be an owner-controlled non-symlink directory: ${parent}" >&2
      exit 2
    fi
    mkdir "${DIST_DIR}"
  fi
  if [[ ! -d "${DIST_DIR}" || -L "${DIST_DIR}" || ! -O "${DIST_DIR}" ]] \
    || ! path_has_safe_write_mode "${DIST_DIR}"; then
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
    || ! -O "${candidate}" ]] \
    || ! path_has_safe_write_mode "${candidate}"; then
    echo "Refusing to modify an unsafe distribution staging directory: ${candidate}" >&2
    exit 2
  fi
}

validate_dmg_target() {
  if [[ "${DMG_PATH}" != /* \
    || "$(dirname "${DMG_PATH}")" != "${DIST_DIR}" \
    || "$(basename "${DMG_PATH}")" != *.dmg ]]; then
    echo "DMG output must be a .dmg file directly inside the validated distribution directory: ${DMG_PATH}" >&2
    exit 2
  fi
  if [[ -e "${DMG_PATH}" || -L "${DMG_PATH}" ]]; then
    if [[ ! -f "${DMG_PATH}" || -L "${DMG_PATH}" || ! -O "${DMG_PATH}" ]] \
      || ! path_has_safe_write_mode "${DMG_PATH}"; then
      echo "Refusing to replace an unsafe DMG output path: ${DMG_PATH}" >&2
      exit 2
    fi
  fi
}

require_clean_source_identity() {
  local current_commit
  local status
  current_commit="$(git -C "${ROOT}" rev-parse HEAD 2>/dev/null || true)"
  status="$(git -C "${ROOT}" status --porcelain=v1 --untracked-files=all 2>/dev/null || true)"
  if [[ ! "${current_commit}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "DMG packaging requires a Git source commit." >&2
    exit 2
  fi
  if [[ -z "${EXPECTED_SOURCE_COMMIT}" ]]; then
    EXPECTED_SOURCE_COMMIT="${current_commit}"
  fi
  if [[ ! "${EXPECTED_SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ \
    || "${current_commit}" != "${EXPECTED_SOURCE_COMMIT}" ]]; then
    echo "DMG packaging source commit changed: expected ${EXPECTED_SOURCE_COMMIT:-missing}, found ${current_commit}." >&2
    exit 2
  fi
  if [[ -n "${status}" ]]; then
    echo "DMG packaging requires a clean tracked and untracked source worktree." >&2
    exit 2
  fi
}

dmg_build_exit() {
  local status=$?
  set +e
  if [[ "${status}" -ne 0 && -f "${DMG_PARTIAL_PATH:-}" && ! -L "${DMG_PARTIAL_PATH:-}" ]]; then
    echo "DMG partial retained for inspection: ${DMG_PARTIAL_PATH}" >&2
  fi
  exit "${status}"
}

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
prepare_owned_dist_directory
PROJECT_VERSION="$("${PYTHON_BIN}" -c 'import pathlib, sys, tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["project"]["version"])' "${ROOT}/pyproject.toml")"
if [[ -n "${APP_VERSION_OVERRIDE}" && "${APP_VERSION_OVERRIDE}" != "${PROJECT_VERSION}" ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_APP_VERSION=${APP_VERSION_OVERRIDE} does not match pyproject version ${PROJECT_VERSION}." >&2
  exit 2
fi
APP_VERSION="${PROJECT_VERSION}"
DMG_VERSION_TAG="${TOTALSEGMENTATOR_WRAPPER_MAC_DMG_VERSION_TAG:-${APP_VERSION}-release}"
DMG_PATH="${TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH:-${DIST_DIR}/${APP_NAME}-${DMG_VERSION_TAG}-arm64.dmg}"
DMG_RUN_ID="${TOTALSEGMENTATOR_WRAPPER_MAC_DMG_RUN_ID:-$("${PYTHON_BIN}" -c 'import secrets; print(secrets.token_hex(8))')}"
if [[ ! "${DMG_RUN_ID}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
  echo "TOTALSEGMENTATOR_WRAPPER_MAC_DMG_RUN_ID must be a safe, non-empty run identifier." >&2
  exit 2
fi
DMG_STAGING="${DIST_DIR}/.dmg-staging-${DMG_RUN_ID}"
DMG_PARTIAL_PATH="${DIST_DIR}/$(basename "${DMG_PATH}" .dmg).pending-${DMG_RUN_ID}.dmg"
validate_dmg_target
if [[ -e "${DMG_PARTIAL_PATH}" || -L "${DMG_PARTIAL_PATH}" ]]; then
  echo "Refusing to replace an existing DMG partial output: ${DMG_PARTIAL_PATH}" >&2
  exit 2
fi
if [[ -e "${DMG_STAGING}" || -L "${DMG_STAGING}" ]]; then
  validate_owned_dist_child_directory_if_present "${DMG_STAGING}" ".dmg-staging-${DMG_RUN_ID}"
  echo "Refusing to replace an existing DMG run staging directory: ${DMG_STAGING}" >&2
  exit 2
fi
require_clean_source_identity
trap dmg_build_exit EXIT

if [[ "${TOTALSEGMENTATOR_WRAPPER_MAC_SKIP_APP_BUILD:-0}" != "1" ]]; then
  "${ROOT}/scripts/build_mac_app.sh" >/dev/null
elif [[ ! -d "${APP_PATH}" ]]; then
  echo "App bundle not found and TOTALSEGMENTATOR_WRAPPER_MAC_SKIP_APP_BUILD=1 was set: ${APP_PATH}" >&2
  exit 2
fi
require_clean_source_identity
if [[ ! -d "${APP_PATH}" || -L "${APP_PATH}" || ! -O "${APP_PATH}" ]] \
  || ! path_has_safe_write_mode "${APP_PATH}"; then
  echo "App bundle must be an owner-controlled non-symlink directory: ${APP_PATH}" >&2
  exit 2
fi
if [[ ! -x "${APP_PATH}/Contents/MacOS/TotalSegmentatorWrapperForMac" ]]; then
  echo "App bundle is missing its executable: ${APP_PATH}" >&2
  exit 2
fi

MANIFEST_PATH="${APP_PATH}/Contents/Resources/setup_manifest.json"
if [[ ! -f "${MANIFEST_PATH}" ]]; then
  echo "App bundle is missing setup_manifest.json: ${APP_PATH}" >&2
  exit 2
fi
MANIFEST_APP_VERSION="$("${PYTHON_BIN}" -c 'import json, sys; manifest=json.load(open(sys.argv[1])); print(manifest.get("app_version") or manifest.get("version") or "")' "${MANIFEST_PATH}")"
if [[ "${MANIFEST_APP_VERSION}" != "${APP_VERSION}" ]]; then
  echo "App bundle version mismatch: expected ${APP_VERSION}, found ${MANIFEST_APP_VERSION:-missing}" >&2
  exit 2
fi
INFO_PLIST_PATH="${APP_PATH}/Contents/Info.plist"
if [[ ! -f "${INFO_PLIST_PATH}" ]]; then
  echo "App bundle is missing Info.plist: ${INFO_PLIST_PATH}" >&2
  exit 2
fi
APP_MINIMUM_MACOS_VERSION="$("${PYTHON_BIN}" -c 'import plistlib, sys; print(plistlib.load(open(sys.argv[1], "rb")).get("LSMinimumSystemVersion") or "")' "${INFO_PLIST_PATH}")"
MANIFEST_MINIMUM_MACOS_VERSION="$("${PYTHON_BIN}" -c 'import json, sys; print(json.load(open(sys.argv[1])).get("minimum_macos_version") or "")' "${MANIFEST_PATH}")"
if [[ "${APP_MINIMUM_MACOS_VERSION}" != "${MINIMUM_MACOS_VERSION}" || "${MANIFEST_MINIMUM_MACOS_VERSION}" != "${MINIMUM_MACOS_VERSION}" ]]; then
  echo "App bundle must require macOS ${MINIMUM_MACOS_VERSION}: Info.plist=${APP_MINIMUM_MACOS_VERSION:-missing}, setup_manifest=${MANIFEST_MINIMUM_MACOS_VERSION:-missing}" >&2
  exit 2
fi

if command -v codesign >/dev/null 2>&1; then
  codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
fi
"${PYTHON_BIN}" "${ROOT}/scripts/verify_license_distribution.py" \
  --source "${ROOT}" \
  --app "${APP_PATH}" \
  --expected-version "${APP_VERSION}" \
  --expected-source-commit "${EXPECTED_SOURCE_COMMIT}" >/dev/null

MANIFEST_NOTARIZED="$("${PYTHON_BIN}" -c 'import json, sys; print("1" if json.load(open(sys.argv[1])).get("notarized") is True else "0")' "${MANIFEST_PATH}")"
OPEN_WARNING_README="- notarized済みDMGでは、通常のダブルクリックで起動できます。"
OPEN_WARNING_TEST="5. ~/Applications/TotalSegmentator Wrapper for Mac.app を開きます。"
if [[ "${MANIFEST_NOTARIZED}" != "1" ]]; then
  OPEN_WARNING_README="- 初回起動時にmacOSが警告する場合は、Controlキーを押しながらクリックして「開く」を選びます。"
  OPEN_WARNING_TEST=$'5. ~/Applications/TotalSegmentator Wrapper for Mac.app を開きます。\n   この未公証の開発用buildでは、初回のみControlキーを押しながらクリックして「開く」が必要になる場合があります。'
fi

mkdir "${DMG_STAGING}"
if [[ ! -d "${DMG_STAGING}" || -L "${DMG_STAGING}" || ! -O "${DMG_STAGING}" ]] \
  || ! path_has_safe_write_mode "${DMG_STAGING}"; then
  echo "DMG staging directory must be owner-controlled with a safe write mode: ${DMG_STAGING}" >&2
  exit 2
fi
ditto "${APP_PATH}" "${DMG_STAGING}/${APP_NAME}.app"
ln -s /Applications "${DMG_STAGING}/Applications"
cp "${ROOT}/LICENSE" "${DMG_STAGING}/LICENSE.txt"
cp "${ROOT}/NOTICE" "${DMG_STAGING}/NOTICE.txt"
cat > "${DMG_STAGING}/Verify Test Account Install.command" <<SH
#!/bin/bash
set -euo pipefail
VERIFY_COMMAND_VOLUME="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
VERIFY_COMMAND_DMG="\$(/usr/bin/hdiutil info | /usr/bin/awk -v target="\${VERIFY_COMMAND_VOLUME}" '
  /^image-path[[:space:]]*:/ {
    image = substr(\$0, index(\$0, ":") + 2)
  }
  /^mount-point[[:space:]]*:/ {
    mount = substr(\$0, index(\$0, ":") + 2)
    if (mount == target) {
      print image
      exit
    }
  }
')"
if [[ "\${VERIFY_COMMAND_DMG}" != /* || ! -f "\${VERIFY_COMMAND_DMG}" || -L "\${VERIFY_COMMAND_DMG}" ]]; then
  echo "Mounted DMGの元image pathを安全に特定できませんでした。FinderからDMGを開き直して再実行してください。" >&2
  exit 2
fi
export TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH="\${VERIFY_COMMAND_DMG}"
export TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_APP_VERSION="${APP_VERSION}"
SH
/usr/bin/tail -n +2 "${ROOT}/scripts/collect_test_account_install_evidence.sh" >> "${DMG_STAGING}/Verify Test Account Install.command"
chmod 755 "${DMG_STAGING}/Verify Test Account Install.command"
cp "${ROOT}/scripts/collect_launch_debug_logs.sh" "${DMG_STAGING}/Collect TotalSegmentator Wrapper Logs.command"
chmod 755 "${DMG_STAGING}/Collect TotalSegmentator Wrapper Logs.command"
cat > "${DMG_STAGING}/README.txt" <<TXT
TotalSegmentator Wrapper for Mac ${APP_VERSION}

これはTotalSegmentatorを利用する非公式Mac wrapperです。TotalSegmentator公式アプリではありません。
アプリ本体は無料のオープンソースソフトウェアで、Apache License 2.0により無保証で提供されます。

インストール手順:
1. "TotalSegmentator Wrapper for Mac.app" を Applications、または管理者権限がない場合は ~/Applications へドラッグします。
2. アプリを開きます。
3. 「セットアップ開始」を押します。専用環境は以下に作成されます。
   ~/Library/Application Support/TotalSegmentatorWrapperMac/

権限と通信:
- 管理者権限、Homebrew、system Pythonの変更は不要です。
- Python依存はアプリに同梱されており、ネットワークを使わずに導入します。セットアップ中にネットワークを使用するのはモデルweightの取得だけです。
- 初回実行に必要なモデルweightはSetup時に準備します。
- ToothSeg高精細化はTotalSegmentator結果で歯を検出した場合だけ明示実行できます。初回選択時に約920 MBの追加モデルを取得し、取得完了後も自動では推論を開始しません。
- DICOM、CT、処理結果、3Dプレビュー出力は、セットアップ中もプレビュー作成中も送信しません。
- 利用状況データの送信も、専用環境内で無効化します。
- Setup中は「3Dサンプルを開く」から、同梱Sample 1のオフライン3Dプレビューをブラウザで操作できます。
- Setup完了後、アプリには同梱Sample 1のCT入力が用意されています。
- Sample 1の3Dプレビュー作成は、モデル準備済みでも入力の大きさやMacの状態により数分以上かかる場合があります。
- CTフォルダを選んだ場合、アプリ内で安全確認し、通常CTとして取り込める場合は同梱dcm2niixでプレビュー用入力を準備します。
- CTを見るソフトから「表示用の断面画像」として書き出されたデータの場合、slice確認後に救済3Dプレビューへ進めることがあります。
- 自動取り込みできないCTでも、CT画像そのものが壊れているとは限りません。対応できる場合があるため、必要であれば開発者へご連絡ください。
- 更新確認はユーザーが「更新を確認」を押した時だけversion manifestを取得します。起動時やSetup中に自動確認しません。
- 更新がある場合は、追加確認後にnotarized DMGをダウンロードし、SHA256とGatekeeper確認後にアプリを置き換えて再起動します。
- 更新ファイルはmanifestと同じ配信元または許可済み配信元だけを使います。DICOM、CT、path、log、ユーザー識別子は送信しません。

用途:
- 非臨床preview専用です。
- TotalSegmentatorを利用する非公式Mac wrapperです。TotalSegmentator公式アプリではありません。
- Sample 1は権利者が公開を許諾したCTから作成したNIfTIとprecomputed previewで、DICOMではありません。
- 元DICOMは配布物に含まれません。Sample 1の来歴、SHA256、ToothSeg表記はアプリ内
  Contents/Resources/sample1/THIRD_PARTY_NOTICES.txt に記録しています。
- wrapper本体のApache-2.0ライセンスと適用範囲はDMG内のLICENSE.txt、
  NOTICE.txt、およびアプリ内Contents/Resources/LICENSE、NOTICEに記録しています。
- 第三者コード、別途取得するモデル、モデル出力やSample由来の画像、
  第三者の名称・商標はwrapperのApache-2.0へ再ライセンスされません。
- TotalSegmentator Apache-2.0ライセンス本文はアプリ内
  Contents/Resources/licenses/TotalSegmentator-Apache-2.0.txt に同梱しています。
- DentalSegmentatorモデルの作者、DOI、CC BY 4.0 URL、変更状況はアプリ内
  Contents/Resources/licenses/DentalSegmentator-NOTICE.txt に同梱しています。
- ToothSegのコード・モデル帰属、DOI、CC BY 4.0 URL、変更状況はアプリ内
  Contents/Resources/licenses/ToothSeg-NOTICE.txt に同梱しています。
- MeshSegNet checkpointの正規出典、固定revision、SHA-256、宣言ライセンスはアプリ内
  Contents/Resources/licenses/MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt に記録しています。
- TGNet checkpointはユーザー提供で、ライセンス未確認、非同梱、非再配布です。境界はアプリ内
  Contents/Resources/licenses/TGNet-User-Provided-Checkpoint-NOTICE.txt に記録しています。
- 同梱dcm2niixのライセンス本文はアプリ内
  Contents/Resources/licenses/dcm2niix-license.txt に同梱しています。
- Python依存を含むthird-party license inventoryはアプリ内
  Contents/Resources/licenses/third_party_license_inventory.json に記録しています。
- 同梱3DサンプルはUI体験用で、精度評価用データではありません。
- 診断や治療計画には使用しないでください。
- 不具合はアカウント不要のGoogleフォーム https://forms.gle/QFPwF1Pi5C8bmSuw6 へ報告できます。
  患者データ、DICOM、識別情報を含むログは送信しないでください。

テスト用アカウントでの検証:
- Setup完了後、DMG内の "Verify Test Account Install.command" をダブルクリックします。
${OPEN_WARNING_README}
- 検証コマンドは以下へ結果を書き出します。
  ~/Library/Application Support/TotalSegmentatorWrapperMac/logs/test_account_install_evidence.json
- 共有受け渡し用コピー:
  /Users/Shared/TotalSegmentatorWrapperMac/test_account_install_evidence.json
- 詳細なrelease gate手順は TEST_ACCOUNT_INSTALL.txt を参照してください。
- アプリが開かない場合は "Collect TotalSegmentator Wrapper Logs.command" を実行し、
  /Users/Shared/TotalSegmentatorWrapperMac/launch_debug_... のログを確認してください。
- 自分のCTが読めない場合は、画面スクリーンショット、詳細ログ、CTを書き出したソフト名を確認してください。
- 回収ログにはローカルパスや実行状況が含まれる場合があります。共有前に内容を確認してください。
TXT

cat > "${DMG_STAGING}/TEST_ACCOUNT_INSTALL.txt" <<TXT
TotalSegmentator Wrapper for Mac テスト用アカウント install gate

目的:
- 開発環境のないmacOSアカウントで、このDMGからインストールできることを確認します。
- テスト用アカウントでは package manager、uv、pyenv、Python、Xcode tools を導入しません。
- 管理者権限は使いません。

手順:
1. 別のmacOSテスト用アカウントへログインします。
2. このDMGをそのアカウントへコピーします。
3. DMGを開きます。
4. "TotalSegmentator Wrapper for Mac.app" を ~/Applications へドラッグします。
   ~/Applications がない場合はFinderで作成します。
${OPEN_WARNING_TEST}
6. 「セットアップ開始」を押します。
7. Setupが完了し、アプリUIが開くまで待ちます。
8. このDMGをもう一度開きます。
9. "Verify Test Account Install.command" をダブルクリックします。
10. 表示されたJSONで "passed": true を確認します。

検証結果:
- 検証コマンドは以下へ書き出します。
  ~/Library/Application Support/TotalSegmentatorWrapperMac/logs/test_account_install_evidence.json
- 受け渡し用コピー:
  /Users/Shared/TotalSegmentatorWrapperMac/test_account_install_evidence.json
- どちらかのJSONを開発アカウントへ戻します。通常は /Users/Shared のコピーが取り出しやすいです。
- 開発checkoutで、dist/notary/notary-release-receipt.json の final_dmg_sha256 を
  必須の期待値として以下を実行します。
  TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_RECEIPT=dist/notary/notary-release-receipt.json \
    TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_DMG_SHA256=<final_dmg_sha256> \
    scripts/import_test_account_evidence.sh /path/to/test_account_install_evidence.json
- TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE なしで test_account_install_verdict.json が "passed": true の場合だけrelease gate通過です。

期待されるcheck:
- setup_state_success
- wheel_install_hashed_lock
- install_bundled_wheels_step_success
- install_locked_dependencies_step_success
- pip_check_step_success
- bundled_requirements_lock_sha256_matches_manifest
- bundled_dependency_lock_metadata_sha256_matches_manifest
- bundled_dependency_wheelhouse_manifest_sha256_matches_manifest
- installed_requirements_lock_sha256_matches_manifest
- installed_dependency_lock_metadata_sha256_matches_manifest
- installed_dependency_wheelhouse_manifest_sha256_matches_manifest
- mps_actual_device
- mps_gate_pass
- normalizer_from_app_bundle
- app_codesign_valid
- python_version_312
- python_executable_inside_app
- app_support_inside_current_home
- no_user_global_pip_cache
- pip_cache_under_app_support
- pycache_under_app_support
- bundled_python_has_no_absolute_symlinks

プライバシーと用途:
- Setupは ~/Library/Application Support/TotalSegmentatorWrapperMac/ 配下へ書き込みます。
- Python依存はアプリに同梱されており、ネットワークを使わずに導入します。セットアップ中にネットワークを使用するのはモデルweightの取得だけです。
- 初回実行に必要なモデルweightはSetup時に準備します。
- DICOM、CT、処理結果、3Dプレビュー出力は、セットアップ中もプレビュー作成中も送信しません。
- 利用状況データの送信も、専用環境内で無効化します。
- Setup中は「3Dサンプルを開く」から、同梱Sample 1のオフライン3Dプレビューをブラウザで操作できます。
- Setup完了後、アプリの入力欄には同梱Sample 1 NIfTIが自動設定されます。
- Sample 1の3Dプレビュー作成は、モデル準備済みでも入力の大きさやMacの状態により数分以上かかる場合があります。
- CTフォルダを選んだ場合、アプリ内で安全確認し、通常CTとして取り込める場合は同梱dcm2niixでプレビュー用入力を準備します。
- 更新確認はユーザーが「更新を確認」を押した時だけversion manifestを取得します。起動時やSetup中に自動確認しません。
- 更新がある場合は、追加確認後にnotarized DMGをダウンロードし、SHA256とGatekeeper確認後にアプリを置き換えて再起動します。
- 更新ファイルはmanifestと同じ配信元または許可済み配信元だけを使います。DICOM、CT、path、log、ユーザー識別子は送信しません。
- 非臨床preview専用です。診断や治療計画には使用しないでください。
- 回収ログにはローカルパスや実行状況が含まれる場合があります。共有前に内容を確認してください。
TXT

hdiutil create \
  -volname "${APP_NAME}" \
  -srcfolder "${DMG_STAGING}" \
  -ov \
  -format UDZO \
  "${DMG_PARTIAL_PATH}" >/dev/null

hdiutil verify "${DMG_PARTIAL_PATH}" >/dev/null
"${PYTHON_BIN}" "${ROOT}/scripts/verify_license_distribution.py" \
  --source "${ROOT}" \
  --dmg "${DMG_PARTIAL_PATH}" \
  --expected-version "${APP_VERSION}" \
  --expected-source-commit "${EXPECTED_SOURCE_COMMIT}" >/dev/null
require_clean_source_identity
mv -f "${DMG_PARTIAL_PATH}" "${DMG_PATH}"

echo "${DMG_PATH}"
