# Test Account Install Verification

This is the release gate for the current Mac preview: a separate macOS test
account with no developer environment must be able to install and run setup
from the DMG.

## Scope

This verifies the distribution shape only:

- no Homebrew requirement
- no `uv` requirement
- no existing Python requirement
- no `sudo`
- Apple Silicon macOS 14 or later
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
dist/TotalSegmentator Wrapper for Mac-0.4.1-release-arm64.dmg
```

## Automated Preflight

Before switching accounts, run:

```bash
scripts/verify_zero_env_mac_dmg.sh
```

This mounts the DMG, copies the app into a temporary clean home directory,
runs first setup with an empty inherited environment, verifies the macOS 14
minimum-version contract, verifies MPS, verifies
the app-bundled DICOM normalizer, checks that pip and Python bytecode caches
stay under App Support, verifies codesign after setup, and writes
test-account-style evidence.

## Manual Test Account Gate

1. 別のmacOSテスト用アカウントへログインする。
2. そのアカウントには Homebrew、`uv`、pyenv、Python をインストールしない。
3. `pyproject.toml` のversionから生成された0.4.1 DMG（既定名は
   `TotalSegmentator Wrapper for Mac-0.4.1-release-arm64.dmg`）をそのアカウントへコピーする。
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

Each execution creates a new run ID.  A pre-existing local or shared evidence
file is first atomically replaced with a non-importable supersession tombstone,
then that tombstone is placed at `*.superseded-<run-id>` before verification
starts.  It records `passed: false`, `superseded_by_run_id`, and a bounded
base64 diagnostic copy of the old JSON; the raw prior PASS is not retained as a
separately importable file.  Only the current run's final JSON is published at
the normal path.  A failed verification therefore replaces the normal-path
JSON with `"passed": false` rather than leaving a previous pass result in place.

This also applies to an early preflight failure such as a missing setup-state
file or private runtime Python: where the evidence locations are writable, the
collector publishes a minimal current-run
`totalsegmentator_wrapper_mac.test_account_install_preflight_failure.v1` JSON.
It contains `passed: false`, the current run ID, timestamp, and a bounded
`preflight_failure` reason.  This is deliberately a different, non-importable
diagnostic schema rather than incomplete v2 release evidence.  The importer
rejects it as `preflight_failure_evidence_cannot_be_imported`; its purpose is to
make a prior PASS unusable while preserving the superseded file and current
failure details for diagnosis.

Required evidence:

- `passed: true`
- `setup_state_success`
- `install_wheel_step_success`
- `wheel_install_hashed_lock`（`offline_require_hashes_wheelhouse`）
- `install_bundled_wheels_step_success`
- `install_locked_dependencies_step_success`
- `pip_check_step_success`
- `manifest_has_requirements_lock_sha256` / `manifest_has_dependency_lock_metadata_sha256` /
  `manifest_has_dependency_wheelhouse_manifest_sha256`
- `bundled_requirements_lock_sha256_matches_manifest` /
  `bundled_dependency_lock_metadata_sha256_matches_manifest` /
  `bundled_dependency_wheelhouse_manifest_sha256_matches_manifest`
- `installed_requirements_lock_sha256_matches_manifest` /
  `installed_dependency_lock_metadata_sha256_matches_manifest` /
  `installed_dependency_wheelhouse_manifest_sha256_matches_manifest`
- `installed_fpsample_version` / `installed_fpsample_import_sample`
- `installed_acvl_utils_version` / `installed_acvl_utils_import`
- `manifest_has_fpsample_wheel_sha256` / `bundled_fpsample_wheel_sha256_matches_manifest`
- `manifest_has_acvl_utils_wheel_sha256` / `bundled_acvl_utils_wheel_sha256_matches_manifest`
- `manifest_has_setup_weights_manifest_sha256`
- `mps_actual_device`
- `mps_gate_pass`
- `normalizer_from_app_bundle`
- `app_and_wheel_macho_macos14_arm64`（all app/wheel Mach-O slices are
  arm64-compatible and target macOS 14 or earlier; unsafe wheel member paths,
  case/Unicode-colliding wheel members, escaping `Contents` symlinks, external
  absolute dyld metadata, and malformed `LC_ID_DYLIB` are rejected; native
  wheel members are limited to sealed macOS dependencies with no `LC_RPATH`）
- `dicom_helpers_system_linkage_no_rpath`（normalizer and dcm2niix use only
  macOS system libraries and no `LC_RPATH`）
- `normalizer_source_matches_bundled_receipts`（`normalizer_source` matches the
  bundled normalizer/GDCM source-build receipts）
- `dcm2niix_source_matches_bundled_receipt_and_pointer`（`dcm2niix_source`
  matches the bundled content-addressed pointer and source-build receipt）
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

`offline_local_no_deps` は、lockを同梱しない開発用smoke testだけの経路です。
その証跡は最終release evidenceとしてimportできません。release DMGでは、上記の
hashed-lock / bundled-wheel / `pip check` のすべてが成功している必要があります。

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

The importer accepts only the exact evidence schema-v2 field set with a unique
boolean result for each required check, and rejects duplicate JSON object keys
before interpreting the payload. A preflight-failure v1 diagnostic record is
always rejected with `preflight_failure_evidence_cannot_be_imported`, even when
its diagnostic fields are well formed. It rejects symlinked evidence files,
stale evidence (seven days by default), and evidence whose app version does not
match the current checkout's `pyproject.toml`. The collector records the installed app's version,
build/dependency IDs, `setup_manifest.json` and `Info.plist` hashes, and, when
the DMG was supplied for stapler validation, its SHA-256. Final release import
requires both `TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_RECEIPT` and
`TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_DMG_SHA256`. The expected SHA must equal
the receipt's `final_dmg_sha256`, the evidence's DMG SHA, and the DMG filename
recorded by the receipt. The receipt's app-manifest SHA must also equal the
evidence's `setup_manifest.json` SHA. Missing or inconsistent receipt binding is
a failed final verdict, not a warning.

```bash
TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_RECEIPT=dist/notary/notary-release-receipt.json \
TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_DMG_SHA256=<final_dmg_sha256> \
  scripts/import_test_account_evidence.sh /path/to/test_account_install_evidence.json
```

This is an operational test-account record, not a cryptographic remote
attestation. Keep the original DMG and imported verdict together, review the
recorded identity fields, and do not treat a manually edited JSON as proof of a
release.

The import script rejects evidence from `/tmp` clean-home simulations and from
the current development account by default. For automated preflight evidence
only, use the explicit development mode:

```bash
TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE=1 \
TOTALSEGMENTATOR_WRAPPER_MAC_TEST_ACCOUNT_DEVELOPMENT_PREFLIGHT=1 \
  scripts/import_test_account_evidence.sh /path/to/test_account_install_evidence.json
```

Development preflight always writes `passed: false` with
`development_preflight_not_release_evidence`; it can never satisfy the final
release-gate verdict.
