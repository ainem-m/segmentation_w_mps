# Test Account Install Verification

This is the release gate for the current Mac alpha: a separate macOS test
account with no developer environment must be able to install and run setup
from the DMG.

## Scope

This verifies the distribution shape only:

- no Homebrew requirement
- no `uv` requirement
- no existing Python requirement
- no `sudo`
- app writes only under `~/Library/Application Support/TotalSegmentatorWrapperMac/`
- pip and Python bytecode caches stay under App Support
- the signed app bundle remains valid after setup
- dependency download is limited to first setup
- DICOM/CT/processing results are not uploaded during setup or preview
  creation

It does not verify model accuracy or clinical suitability. For notarized
release builds, the evidence also records notarization and Gatekeeper checks.

## Build

From the development account:

```bash
scripts/build_mac_dmg.sh
```

Expected artifact:

```text
dist/TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg
```

## Automated Preflight

Before switching accounts, run:

```bash
scripts/verify_zero_env_mac_dmg.sh
```

This mounts the DMG, copies the app into a temporary clean home directory,
runs first setup with an empty inherited environment, verifies MPS, verifies
the app-bundled DICOM normalizer, checks that pip and Python bytecode caches
stay under App Support, verifies codesign after setup, and writes
test-account-style evidence.

## Manual Test Account Gate

1. 別のmacOSテスト用アカウントへログインする。
2. そのアカウントには Homebrew、`uv`、pyenv、Python をインストールしない。
3. `TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg` をそのアカウントへコピーする。
4. DMGを開く。
5. `TotalSegmentator Wrapper for Mac.app` を `~/Applications` へドラッグする。
   `~/Applications` がない場合はFinderで作成する。
6. `~/Applications/TotalSegmentator Wrapper for Mac.app` を開く。
   notarized済みDMGでは通常のダブルクリックで起動できる。ad-hoc buildを
   内部検証する場合だけ、初回にControlキーを押しながらクリックして「開く」が
   必要になる場合がある。
7. `セットアップ開始` を押す。
8. 日本語Setup画面で現在ステップ、progress bar、経過時間、ログ更新が見えることを確認する。
9. `3Dサンプルを開く` を押し、同梱Sample 1のオフライン3Dプレビューがブラウザで開くことを確認する。
10. Setupは継続したまま、完了後にアプリUIが開くまで待つ。
11. アプリUIの入力欄に同梱Sample 1 NIfTIが自動設定されていることを確認する。
12. DMGをもう一度開き、`Verify Test Account Install.command` をダブルクリックする。
13. 表示されたJSONで `"passed": true` を確認する。

The evidence file should be written to:

```text
~/Library/Application Support/TotalSegmentatorWrapperMac/logs/test_account_install_evidence.json
```

The verification command also writes a handoff copy that is easier to retrieve
from another account:

```text
/Users/Shared/TotalSegmentatorWrapperMac/test_account_install_evidence.json
```

Required evidence:

- `passed: true`
- `setup_state_success`
- `mps_actual_device`
- `mps_gate_pass`
- `normalizer_from_app_bundle`
- `app_codesign_valid`
- `spctl_app_accepted`
- `stapler_dmg_valid`
- `python_version_312`
- `python_executable_inside_app`
- `app_support_inside_current_home`
- `no_user_global_pip_cache`
- `pip_cache_under_app_support`
- `pycache_under_app_support`
- `manifest_notarized`
- `manifest_bundled_python312`
- `bundled_python_has_no_absolute_symlinks`

If any check fails, the goal is not complete.

## Import Evidence

After the test account writes the evidence JSON, copy either the App Support
file or the `/Users/Shared` handoff copy back to the development account and
run:

```bash
scripts/import_test_account_evidence.sh /path/to/test_account_install_evidence.json
```

This copies the evidence under:

```text
artifacts/test_account_install/<timestamp>/
```

and writes `test_account_install_verdict.json`. The goal is complete only when
that verdict has `passed: true` for a real separate macOS test account, without
setting `TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE`.

The import script rejects evidence from `/tmp` clean-home simulations and from
the current development account by default. For automated preflight evidence
only, use:

```bash
TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE=1 \
  scripts/import_test_account_evidence.sh /path/to/test_account_install_evidence.json
```

Do not use that override for the final release-gate verdict.
