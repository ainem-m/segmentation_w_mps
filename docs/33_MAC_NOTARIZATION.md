# 33 Mac Notarization

TotalSegmentator Wrapper for Mac の広めの配布では、Developer ID署名済み `.app` を含む
DMGをApple notary serviceへ提出し、承認後にDMGへticketをstapleする。

## 前提

- Apple Developer Program加入済みTeamを使う。
- Full Xcodeを選択する。`xcodebuild -version` が通る必要がある。
- Keychainに `Developer ID Application` 証明書を入れる。
- notary service用のKeychain profileを作成する。App Store公開は不要。
  App Store Connect API Key方式、またはApple ID + app-specific password方式を使う。
- `TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX` は同梱するdcm2niix executableを指す。

## 初回credential登録

推奨はAPI KeyをKeychain profileとして保存する方法。

```bash
xcrun notarytool store-credentials totalsegmentator-wrapper-mac-notary \
  --key /path/AuthKey_<KEY_ID>.p8 \
  --key-id <KEY_ID> \
  --issuer <ISSUER_ID> \
  --validate
```

以後のbuild scriptは `totalsegmentator-wrapper-mac-notary` というprofile名だけを参照する。
API Key path、issuer、password、Team secretはmanifestやREADMEへ書かない。

App Store Connect API Keyを使わない場合は、Apple IDとapp-specific passwordでも
profileを作れる。この場合もApp Store公開は不要。

```bash
xcrun notarytool store-credentials totalsegmentator-wrapper-mac-notary \
  --apple-id <APPLE_ID> \
  --team-id <TEAM_ID> \
  --validate
```

passwordは対話promptに入力し、repoやshell historyへ残さない。

## Notarized DMG build

```bash
export TOTALSEGMENTATOR_WRAPPER_MAC_XCODE_DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
export TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX=/path/to/dcm2niix
export TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY="Developer ID Application: Example Inc (TEAMID)"
export TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER="com.example.dentalseg.preview"
export TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_PROFILE=totalsegmentator-wrapper-mac-notary

scripts/notarize_mac_dmg.sh
```

成功すると以下を満たす。

- `.app` は Developer ID + hardened runtime で署名済み
- DMGは Developer ID 署名済み
- `xcrun notarytool submit --wait` が `Accepted`
- `xcrun stapler validate` が成功
- `spctl --assess` がDMGとmounted appで成功
- `setup_manifest.json` は `signing_mode: developer-id` と `notarized: true`

## 失敗時の確認

`scripts/notarize_mac_dmg.sh` は以下を保存する。

- `dist/notary/notary_submission.json`
- `dist/notary/notary_log.json`

`notary_log.json` の `issues` に署名漏れ、hardened runtime不足、entitlements不整合が出る。
credentialやAPI keyは保存しない。

## Release gate

notarized配布では、通常の既存チェックに加えて以下を必須にする。

- `stapler_dmg_valid`
- `spctl_app_accepted`
- `manifest_notarized`

テスト用アカウントで `Verify Test Account Install.command` を実行する場合、
自動検証では `TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH` を渡すとDMGのstapler検証も含められる。
手動でDouble clickする場合は、DMG自体のstapler検証は開発側の
`scripts/notarize_mac_dmg.sh` の結果をrelease evidenceとして扱う。
