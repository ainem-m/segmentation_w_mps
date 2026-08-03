# 33 Mac Notarization

TotalSegmentator Wrapper for Mac の広めの配布では、Developer ID署名済み `.app` を含む
DMGをApple notary serviceへ提出し、承認後にDMGへticketをstapleする。

## 前提

- Apple Developer Program加入済みTeamを使う。
- Full Xcodeを選択する。`xcodebuild -version` が通る必要がある。
- 0.4.1以降はApple Silicon / macOS 14以降を対象にする。DMG作成前に
  `Info.plist` と `setup_manifest.json` の最小OSがともに`14.0`であることを検査する。
- Keychainに `Developer ID Application` 証明書を入れる。
- notary service用のKeychain profileを作成する。App Store公開は不要。
  App Store Connect API Key方式、またはApple ID + app-specific password方式を使う。
- release入力をこの順序で準備する。`scripts/build_gdcm_macos14_arm64.sh` が
  GDCM 3.2.7 source artifactを作り、
  `scripts/build_dicom_normalizer_mac.sh` がそのartifactだけを使ってnormalizerを作り、
  `scripts/build_dcm2niix_macos14_arm64.sh` がdcm2niix macOS 14 artifactを作る。
  normalizer builderはGDCMを暗黙にdownload/buildしない。
- Developer ID/notarized buildは
  `build/dcm2niix-macos14-arm64/current-artifact.json` で選ばれた検証済みdcm2niixだけを
  使用する。`TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX` のpath overrideは
  development-onlyであり、Developer ID/notarized buildでは拒否される。

## 初回credential登録

推奨はAPI KeyをKeychain profileとして保存する方法。

```bash
xcrun notarytool store-credentials totalsegmentator-wrapper-mac-notary \
  --key /path/AuthKey_<KEY_ID>.p8 \
  --key-id <KEY_ID> \
  --issuer <ISSUER_ID> \
  --validate
```

以後のbuild scriptは `totalsegmentator-wrapper-mac-notary` というローカルの
keychain profile名だけを参照する。このprofile名はappに同梱せず、
`setup_manifest.json` には真偽値の
`notarization_credentials_configured` だけを記録する。API Key path、issuer、
password、Team secretはmanifestやREADMEへ書かない。

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
# 署名前のverified native release inputs（順序を変えない）
scripts/build_gdcm_macos14_arm64.sh
scripts/build_dicom_normalizer_mac.sh
scripts/build_dcm2niix_macos14_arm64.sh

# TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX は設定しない。
export TOTALSEGMENTATOR_WRAPPER_MAC_XCODE_DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
export TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY="Developer ID Application: Example Inc (TEAMID)"
export TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER="jp.chino.totalsegmentator.wrapper.mac"
export TOTALSEGMENTATOR_WRAPPER_MAC_TEAM_IDENTIFIER="TEAMID1234"
export TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_PROFILE=totalsegmentator-wrapper-mac-notary

scripts/notarize_mac_dmg.sh
```

The pointer/receipt contract and the development-only override boundary are
documented in [`docs/45_DCM2NIIX_MACOS14_SOURCE_BUILD.md`](45_DCM2NIIX_MACOS14_SOURCE_BUILD.md).

成功すると以下を満たす。

- `.app` は Developer ID + hardened runtime で署名済み
- DMGは Developer ID 署名済み
- `xcrun notarytool submit --wait` が `Accepted`
- `xcrun stapler validate` が成功
- `spctl --assess` がDMGとmounted appで成功
- `setup_manifest.json` は `signing_mode: developer-id` と `notarized: true`
- build中のDMGはrun固有の`pending-notarization`名に置かれ、上記の検査がすべて
  成功した場合だけ最終名へpublishされる。DMG更新後・receipt更新前の失敗もrollbackし、
  既存の合格DMGとcanonical receiptを組として維持する。
- `dist/notary/notary-release-receipt.json` が、提出時とstaple後のDMG SHA-256、size、
  source commit、bundle/team identity、submission ID/status、app manifest SHA-256を結ぶ。
  Keychain profile名やcredential pathはreceiptへ記録しない。

## 失敗時の確認

`scripts/notarize_mac_dmg.sh` はrun固有directoryへ以下を保存する。

- `dist/notary/run-<ID>/notary_submission.json`
- `dist/notary/run-<ID>/notary_log.json`（提出が非Acceptedで、logを取得できた場合だけ）
- `dist/notary/run-<ID>/notary-release-receipt.json`（全gate成功時だけ）
- `dist/notary/run-<ID>/notary-failure-state.json`（失敗stage、pending DMGのbasenameと
  存在有無だけを記録し、Keychain profileやcredentialは記録しない）
- `dist/notary/notary-release-receipt.json`（全gate成功時だけatomic更新されるcanonical receipt）

`notary_log.json` の `issues` に署名漏れ、hardened runtime不足、entitlements不整合が出る。
credentialやAPI keyは保存しない。

## Release gate

notarized配布では、通常の既存チェックに加えて以下を必須にする。

- `stapler_dmg_valid`
- `spctl_app_accepted`
- `manifest_notarized`
- test-account evidenceのDMG SHA-256が`notary-release-receipt.json`の
  `final_dmg_sha256`と一致すること

テスト用アカウントで `Verify Test Account Install.command` を実行する場合、
DMG内のcommandがmounted volumeを元のDMG imageへ安全に対応付け、
`TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH`をcollectorへ渡すため、DMGのstapler検証も含まれる。
最終evidence importではcanonical receiptの`final_dmg_sha256`を
`TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_DMG_SHA256`へ設定し、同じreceiptを
`TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_RECEIPT`へ指定する。receiptなしで許される
`TOTALSEGMENTATOR_WRAPPER_MAC_ZERO_ENV_DEVELOPMENT_PREFLIGHT=1`は開発用確認だけで、
最終release gateには使用しない。
