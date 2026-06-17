#!/usr/bin/env bash
set -euo pipefail

APP_NAME="TotalSegmentator Wrapper for Mac.app"
SUPPORT_DIR="${HOME}/Library/Application Support/TotalSegmentatorWrapperMac"
USER_APP="${HOME}/Applications/${APP_NAME}"
SYSTEM_APP="/Applications/${APP_NAME}"

echo "TotalSegmentator Wrapper for Mac のテスト用インストール状態を削除します。"
echo "user: ${USER:-unknown}"
echo "home: ${HOME}"
echo

if [[ -d "${SUPPORT_DIR}" ]]; then
  echo "削除: ${SUPPORT_DIR}"
  rm -rf "${SUPPORT_DIR}"
else
  echo "未作成: ${SUPPORT_DIR}"
fi

if [[ -d "${USER_APP}" ]]; then
  echo "削除: ${USER_APP}"
  rm -rf "${USER_APP}"
else
  echo "未作成: ${USER_APP}"
fi

if [[ -d "${SYSTEM_APP}" ]]; then
  echo "削除を試行: ${SYSTEM_APP}"
  if rm -rf "${SYSTEM_APP}" 2>/tmp/totalsegmentator-wrapper-mac_remove_system_app_error.txt; then
    echo "削除完了: ${SYSTEM_APP}"
  else
    echo "削除できませんでした: ${SYSTEM_APP}"
    echo "Finderで /Applications/TotalSegmentator Wrapper for Mac.app をゴミ箱へ移動してください。"
    echo "理由:"
    cat /tmp/totalsegmentator-wrapper-mac_remove_system_app_error.txt
  fi
else
  echo "未作成: ${SYSTEM_APP}"
fi

echo
echo "完了しました。新しいDMGから TotalSegmentator Wrapper for Mac.app を入れ直してください。"
