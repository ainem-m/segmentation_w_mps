#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${ROOT}/dist"
APP_NAME="TotalSegmentator Wrapper for Mac"
APP_PATH="${DIST_DIR}/${APP_NAME}.app"
DMG_STAGING="${DIST_DIR}/dmg_staging"
APP_VERSION="${TOTALSEGMENTATOR_WRAPPER_MAC_APP_VERSION:-0.3.0}"
DMG_VERSION_TAG="${TOTALSEGMENTATOR_WRAPPER_MAC_DMG_VERSION_TAG:-${APP_VERSION}-20260728-oss1}"
DMG_PATH="${TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH:-${DIST_DIR}/${APP_NAME}-${DMG_VERSION_TAG}-arm64.dmg}"
PYTHON_BIN="${PYTHON_BIN:-${ROOT}/.venv/bin/python}"

if [[ "${TOTALSEGMENTATOR_WRAPPER_MAC_SKIP_APP_BUILD:-0}" != "1" ]]; then
  "${ROOT}/scripts/build_mac_app.sh" >/dev/null
elif [[ ! -d "${APP_PATH}" ]]; then
  echo "App bundle not found and TOTALSEGMENTATOR_WRAPPER_MAC_SKIP_APP_BUILD=1 was set: ${APP_PATH}" >&2
  exit 2
fi
if [[ ! -x "${APP_PATH}/Contents/MacOS/TotalSegmentatorWrapperForMac" ]]; then
  echo "App bundle is missing its executable: ${APP_PATH}" >&2
  exit 2
fi

if command -v codesign >/dev/null 2>&1; then
  codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
fi

MANIFEST_PATH="${APP_PATH}/Contents/Resources/setup_manifest.json"
MANIFEST_NOTARIZED="$("${PYTHON_BIN}" -c 'import json, sys; print("1" if json.load(open(sys.argv[1])).get("notarized") is True else "0")' "${MANIFEST_PATH}")"
OPEN_WARNING_README="- notarized済みDMGでは、通常のダブルクリックで起動できます。"
OPEN_WARNING_TEST="5. ~/Applications/TotalSegmentator Wrapper for Mac.app を開きます。"
if [[ "${MANIFEST_NOTARIZED}" != "1" ]]; then
  OPEN_WARNING_README="- 初回起動時にmacOSが警告する場合は、Controlキーを押しながらクリックして「開く」を選びます。"
  OPEN_WARNING_TEST=$'5. ~/Applications/TotalSegmentator Wrapper for Mac.app を開きます。\n   この未notarize alphaでは、初回のみControlキーを押しながらクリックして「開く」が必要になる場合があります。'
fi

if [[ -d "${DMG_STAGING}" ]]; then
  chmod -R u+rwX "${DMG_STAGING}" || true
fi
rm -rf "${DMG_STAGING}" "${DMG_PATH}"
mkdir -p "${DMG_STAGING}"
ditto "${APP_PATH}" "${DMG_STAGING}/${APP_NAME}.app"
ln -s /Applications "${DMG_STAGING}/Applications"
cp "${ROOT}/LICENSE" "${DMG_STAGING}/LICENSE.txt"
cp "${ROOT}/NOTICE" "${DMG_STAGING}/NOTICE.txt"
cp "${ROOT}/scripts/collect_test_account_install_evidence.sh" "${DMG_STAGING}/Verify Test Account Install.command"
chmod 755 "${DMG_STAGING}/Verify Test Account Install.command"
cp "${ROOT}/scripts/collect_launch_debug_logs.sh" "${DMG_STAGING}/Collect TotalSegmentator Wrapper Logs.command"
chmod 755 "${DMG_STAGING}/Collect TotalSegmentator Wrapper Logs.command"
cat > "${DMG_STAGING}/README.txt" <<TXT
TotalSegmentator Wrapper for Mac alpha

これはTotalSegmentatorを利用する非公式Mac wrapperです。TotalSegmentator公式アプリではありません。
アプリ本体は無料のオープンソースソフトウェアで、Apache License 2.0により無保証で提供されます。

インストール手順:
1. "TotalSegmentator Wrapper for Mac.app" を Applications、または管理者権限がない場合は ~/Applications へドラッグします。
2. アプリを開きます。
3. 「セットアップ開始」を押します。専用環境は以下に作成されます。
   ~/Library/Application Support/TotalSegmentatorWrapperMac/

権限と通信:
- 管理者権限、Homebrew、system Pythonの変更は不要です。
- 初回Setupまたは明示的な依存更新時のみ、Pythonパッケージとモデルweight取得のためにネットワークを使用します。
- 初回実行に必要なモデルweightはSetup時に準備します。
- ToothSeg高精細化はTotalSegmentator結果で歯を検出した場合だけ明示実行できます。初回選択時に約920 MBの追加モデルを取得し、取得完了後も自動では推論を開始しません。
- DICOM、CT、処理結果、3Dプレビュー出力は、セットアップ中もプレビュー作成中も送信しません。
- 利用状況データの送信も、専用環境内で無効化します。
- Setup中は「3Dサンプルを開く」から、同梱Sample 1のオフライン3Dプレビューをブラウザで操作できます。
- Setup完了後、アプリには同梱Sample 1のCT入力が用意されています。
- Sample 1の3Dプレビュー作成は、モデル準備済みの場合、このMacでおおむね100秒前後かかります。
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
- 同梱dcm2niixのライセンス本文はアプリ内
  Contents/Resources/licenses/dcm2niix-license.txt に同梱しています。
- Python依存を含むthird-party license inventoryはアプリ内
  Contents/Resources/licenses/third_party_license_inventory.json に記録しています。
- 同梱3DサンプルはUI体験用で、精度評価用データではありません。
- 診断や治療計画には使用しないでください。
- バグは https://github.com/ainem-m/segmentation_w_mps/issues へ報告してください。
  患者データ、DICOM、識別情報を含むログは添付しないでください。

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
- 開発checkoutで以下を実行します。
  scripts/import_test_account_evidence.sh /path/to/test_account_install_evidence.json
- TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE なしで test_account_install_verdict.json が "passed": true の場合だけrelease gate通過です。

期待されるcheck:
- setup_state_success
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
- 初回Setupまたは明示的な依存更新時のみPython依存とモデルweightを取得します。
- 初回実行に必要なモデルweightはSetup時に準備します。
- DICOM、CT、処理結果、3Dプレビュー出力は、セットアップ中もプレビュー作成中も送信しません。
- 利用状況データの送信も、専用環境内で無効化します。
- Setup中は「3Dサンプルを開く」から、同梱Sample 1のオフライン3Dプレビューをブラウザで操作できます。
- Setup完了後、アプリの入力欄には同梱Sample 1 NIfTIが自動設定されます。
- Sample 1の3Dプレビュー作成は、モデル準備済みの場合、このMacでおおむね100秒前後かかります。
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
  "${DMG_PATH}" >/dev/null

hdiutil verify "${DMG_PATH}" >/dev/null
"${PYTHON_BIN}" "${ROOT}/scripts/verify_license_distribution.py" --dmg "${DMG_PATH}" >/dev/null

echo "${DMG_PATH}"
