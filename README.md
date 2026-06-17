# TotalSegmentator Wrapper for Mac — Coding Agent Handoff Pack

This is an unofficial Mac wrapper powered by TotalSegmentator. It is not the
official TotalSegmentator application or project.

目的: **Apple Silicon Macで歯科CBCT由来volumeをローカル処理し、offline HTML 3Dプレビューで確認できるMac Preview** まで進める。

このpackは、コーディングエージェントに渡すための設計・規律・実装順序・検証条件をまとめたものです。

## 最重要方針

今回のセンセーションは **「Macでも高速segmentationできる」** です。したがって、MVPでは以下を守る。

- **CT入力を安全に取り込む**。通常CTは自動変換し、危ないDICOMは理由付きで止める。
- **TotalSegmentator + PyTorch MPS** を先に動かす。
- **Slicer内推論はしない**。通常の確認導線は同梱/生成されるoffline HTML 3Dプレビューにする。
- **ConvTranspose3D/MPSのsmoke testを最初に通す**。
- **CPU vs MPSの実測表を出す**。バズの核は数字と動画。
- **DICOM normalizerはPython preview packageには入れない**。必要なDICOM
  ingest/rescueは別C++ Mac binaryとして切り出す。
- **ONNX / DentalSegmentator純正対応は後回し**。
- 医療機器・診断支援・治療計画支援を名乗らない。非臨床・研究/教育・検証用に限定する。

## 想定成果物

Mac Preview v0.1:

```text
TotalSegmentator Wrapper for Mac.app
- Apple Silicon Mac / macOS 14+ / arm64 only
- unofficial Mac wrapper powered by TotalSegmentator
- NIfTI input
- TotalSegmentator task: craniofacial_structures, teeth
- device: mps / cpu
- benchmark log
- output folder
- offline HTML 3D preview / smoothed STL export
```

## ドキュメント一覧

```text
docs/00_AGENT_DIRECTIVE.md          エージェントへの最上位命令
docs/01_PRODUCT_STRATEGY.md         プロダクト・バズ戦略
docs/02_ARCHITECTURE.md             技術アーキテクチャ
docs/03_MVP_SPEC_MAC_PREVIEW.md     MVP仕様
docs/04_IMPLEMENTATION_PLAN.md      実装ロードマップ
docs/05_VALIDATION_BENCHMARKS.md    検証・ベンチマーク
docs/06_PACKAGING_DISTRIBUTION.md   配布・サイズ・依存管理
docs/07_RISK_REGISTER.md            リスク管理
docs/09_CODING_RULES.md             コーディング規律
docs/10_DEFERRED_SCOPE.md           後回しにするもの
docs/12_REFERENCES.md               参照リンク・根拠
```

Current implementation notes:

```text
docs/13_MPS_OPERATOR_VERIFICATION_NOTES.md
docs/14_TOTALSEGMENTATOR_SMOKE_NOTES.md
docs/15_DZ_CBCT_SAMPLE_VALIDATION.md
docs/16_BACKEND_RUNNER_NOTES.md
docs/17_CPU_AND_ROI_BENCHMARK_NOTES.md
docs/20_CASE_SUMMARY_AND_DEMO_NOTES.md
docs/21_EXPERIMENTAL_TEETH_MPS_WRAPPER_NOTES.md
docs/22_SURFACE_PREVIEW_NOTES.md
docs/23_CASE02_DICOM_NATIVE_VALIDATION.md
docs/24_CASE03_SECONDARY_CAPTURE_RESCUE.md
docs/25_CASE04_ALTERNATE_ANGLE_CBCT_NOTES.md
docs/26_OPEN_DATA_STS24_CBCT_0026_NOTES.md
docs/27_THREE_CASE_DEMO_READINESS.md
docs/28_DICOM_INTAKE_AND_DISTRIBUTION_BOUNDARY.md
docs/29_CPP_DICOM_NORMALIZER_MAC_PLAN.md
docs/30_UNREADABLE_DICOM_RESCUE_PATTERNS.md
docs/31_MAIN_APP_UI_AND_GUI_MIGRATION_NOTES.md
docs/32_SWIFTUI_SHELL_COMPLETION_AUDIT.md
docs/33_MAC_NOTARIZATION.md
docs/34_ALPHA_DISTRIBUTION_SUPPORT_CARD.md
docs/future/SLICER_EXPORT_REINTRODUCTION.md
native/dicom_normalizer/README.md
```

## 付属テンプレート

```text
scripts/smoke_test_mps_convtranspose3d.py
scripts/smoke_test_totalseg_mps.sh
scripts/benchmark_cpu_vs_mps.py
templates/benchmark_result.schema.json
templates/model_manifest.example.json
templates/app_config.example.toml
```

## 使い方

1. まず `docs/00_AGENT_DIRECTIVE.md` を読ませる。
2. 次に `docs/03_MVP_SPEC_MAC_PREVIEW.md` と `docs/04_IMPLEMENTATION_PLAN.md` を読ませる。
3. 実装前に `scripts/smoke_test_mps_convtranspose3d.py` を実機で通す。
4. `craniofacial_structures` のMPS smoke testを通す。
5. `teeth` のMPS smoke testを通す。
6. CPU vs MPSの実測表を生成する。
7. 最後にMac Preview UIを載せる。

## Current Preview Commands

```bash
python -m totalsegmentator_wrapper_mac doctor

python -m totalsegmentator_wrapper_mac setup \
  --python /path/to/python3.12 \
  --wheel dist/totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl \
  --constraints constraints/macos-arm64-py312.txt \
  --json /tmp/totalsegmentator-wrapper-mac_setup.json \
  --dry-run \
  --skip-mps-check

python -m totalsegmentator_wrapper_mac dicom-audit \
  --dicom-dir path/to/dicom_folder \
  --json artifacts/dicom_audit.json

python -m totalsegmentator_wrapper_mac dicom-normalizer-audit \
  --dicom-dir path/to/dicom_folder \
  --output runs/case_001/logs/dicom_normalizer_audit.json

python -m totalsegmentator_wrapper_mac dicom-normalizer-convert-clean \
  --dicom-dir path/to/dicom_folder \
  --series-number 3 \
  --output runs/case_001/dicom_clean

python -m totalsegmentator_wrapper_mac dicom-normalizer-prepare-rescue \
  --dicom-dir path/to/dicom_folder \
  --series-number 200 \
  --patched-spacing 0.6,0.6,0.9375 \
  --output runs/case_001/dicom_rescue

python -m totalsegmentator_wrapper_mac run \
  --input path/to/input.nii.gz \
  --output runs/case_001 \
  --task craniofacial_structures \
  --device mps \
  --totalseg-bin .venv/bin/TotalSegmentator

python -m totalsegmentator_wrapper_mac summary \
  --case runs/case_001 \
  --output runs/case_001/CASE_SUMMARY.md

python -m totalsegmentator_wrapper_mac surface-preview \
  --case artifacts/cli_smoke/teeth_smoke_15min_margin10_mps

python -m totalsegmentator_wrapper_mac run \
  --input path/to/input.nii.gz \
  --output runs/teeth_exp_001 \
  --task teeth \
  --device mps \
  --experimental-teeth \
  --teeth-dry-run \
  --totalseg-bin .venv/bin/TotalSegmentator

scripts/build_mac_wheel.sh

scripts/build_mac_app.sh

scripts/build_mac_dmg.sh

scripts/verify_zero_env_mac_app.sh

scripts/verify_zero_env_mac_dmg.sh

# later, from a normal Mac terminal outside the Codex sandbox
scripts/run_dz_cbct_mps_demo.sh
```

Current gate:

```text
- MPS craniofacial_structures on representative CBCT inputs passed.
- Slicer handoff generation has been retired from the active app path; the
  inspection path is the offline HTML surface preview.
- Future optional Slicer export should be reintroduced as an explicit export
  layer; see `docs/future/SLICER_EXPORT_REINTRODUCTION.md`.
- CPU is treated as effectively too slow for the current preview path; exact CPU
  timing is deferred to a later unattended run.
- `teeth` remains fast-fail by default. The experimental opt-in subprocess
  wrapper completed three representative MPS runs with no MPS-to-CPU fallback:
  DZ-CBCT 98.48 s / 56 labels, Case02 112.12 s / 54 labels, and STS24 open
  data 85.40 s / 55 labels.
- `surface-preview` exports smoothed STL files and a fully offline HTML viewer
  from either standard craniofacial masks or the experimental teeth full-space
  labelmap.
  The three successful teeth cases have full-space NIfTI labelmaps, per-label
  smoothed STL exports, four combined smoothed STLs, and offline `index.html`
  viewers with no external script or CDN references. This is visual
  inspection/export only, not a diagnostic viewer.
- For the cropped DZ-CBCT jaw sample, ROI margins 5 mm and 10 mm pass the
  near-whole-volume gate; 15 mm and 20 mm are rejected as too close to the
  already-cropped input volume. The completed DZ-CBCT demo run used 10 mm.
- Three-case demo readiness is recorded in
  `docs/27_THREE_CASE_DEMO_READINESS.md`.
- Distribution packaging is now treated as mandatory. `pyproject.toml` defines
  an installable package with optional `dicom`, `mps`, and `dev` extras.
  `scripts/build_mac_wheel.sh` builds the C++ normalizer and stages it into the
  Python wheel as `totalsegmentator_wrapper_mac/bin/totalsegmentator-wrapper-dicom-normalizer`.
  `scripts/build_mac_app.sh` builds a thin `TotalSegmentator Wrapper for Mac.app` skeleton
  with bundled wheel, launcher, manifest, and C++ normalizer. The default is
  ad-hoc signing for local alpha validation; Developer ID signing is enabled by
  `TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE=developer-id` plus `TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY` and
  `TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER`.
- `totalsegmentator-wrapper-mac setup` now owns the permission-safe one-click setup path.
  It writes only under `~/Library/Application Support/TotalSegmentatorWrapperMac/`,
  records `setup_state.json`, and reports clear reasons such as
  `python312_missing`, `python_version_unsupported`, `needs_network`,
  `runtime_install_failed`, `mps_unavailable`, and `wheel_missing`.
- The thin `.app` now uses a SwiftUI shell for both Setup and the main workflow.
  Setup shows Japanese current-step labels, an indeterminate progress bar,
  elapsed time, and a live tail of `logs/launcher.log`. Setup also offers a
  `3Dサンプルを開く` button that opens the bundled offline Sample 1
  surface-preview HTML in the default browser while dependency installation
  continues. After setup, the SwiftUI main window starts from
  `Sampleで流れを体験する` / `自分のCTを開く`, defaults Sample flows to the
  bundled Sample 1 CT input, and writes runs under App Support. The own-data
  path exposes a single `CTを選ぶ` action: files are prepared as CT volume
  inputs, folders are audited first, clean CT folders are converted with the
  bundled `dcm2niix`, and unsafe or ambiguous folders stop with a reason before
  preview creation. The UI states that
  Sample 1 3D preview creation takes `100秒前後` on the target Mac when model
  weights are already present; the first preview creation may download model
  weights and take longer. Internally, the app uses a segmentation model for
  preview generation; it is not for diagnosis, treatment planning, or accuracy
  evaluation. Setup disables TotalSegmentator usage stats
  (`利用状況データ`) under the private App Support runtime, and the UI/DMG docs
  state that DICOM/CT/processing results are not sent. Sample 1 is
  Slicer SampleData-derived NIfTI plus precomputed preview artifacts for
  non-clinical UI inspection, not DICOM, diagnosis, or accuracy evaluation. Its app-bundled
  `THIRD_PARTY_NOTICES.txt` records the Slicer unrestricted-use source note,
  source SHA256, TotalSegmentator Apache-2.0 attribution, and non-clinical
  limitation. `scripts/build_mac_app.sh` now bundles a Python 3.12 runtime by
  default, so test accounts do not need pyenv, uv, Homebrew, or system Python.
  External Python builds are opt-in with `TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_EXTERNAL_PYTHON_RUNTIME=1`.
  Python 3.14 is rejected before a runtime venv is created. Headless/offline
  validation with bundled Python creates the private venv, uses the bundled
  constraints file, and stops at `needs_network` without touching system
  locations. The app executable is native SwiftUI, not a shell script. Bundled
  Python files are read-only while directories remain copyable, so setup cannot
  mutate the signed app bundle and users can still copy the app with Finder/ditto.
- The `.app` launcher now records a bundle fingerprint in `setup_state.json`.
  When a user replaces the app with a newer build, launcher startup compares the
  bundled wheel/dependency hashes with the installed state. If only the app
  wheel changed, it performs an offline `--force-reinstall --no-deps` resync
  into the existing App Support venv so UI changes are reflected without asking
  the user to reset setup. If the dependency set changed, it shows Setup again
  and waits for explicit user action before any network install.
- The packaged app bundles `Contents/Resources/bin/dcm2niix` and records
  `dcm2niix_sha256`, `dcm2niix_version`, and `dcm2niix_source` in
  `setup_manifest.json`. Packaging fails unless `TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX` points to
  an executable, so clean CT intake does not rely on Homebrew or a user PATH.
- Update checking runs only when the user presses `更新を確認`. If no HTTPS
  `update_manifest_url` is configured, the button reports that update checking
  is not configured and performs no network request. If an update exists, the
  UI asks before downloading the notarized DMG, verifies the manifest SHA256,
  mounts it, validates the replacement app with Gatekeeper, replaces the
  current app when the install location is writable, and reopens the app.
  Update links must use the same origin as the manifest unless explicitly
  allowlisted in the local app manifest at build time with
  `TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_ALLOWED_HOSTS`. The update request does not send DICOM/CT
  paths, logs, processing output, or user identifiers.
- `scripts/build_mac_dmg.sh` creates a user-installable DMG with the `.app`,
  `/Applications` symlink, and a short README. `scripts/verify_zero_env_mac_dmg.sh`
  mounts that DMG, copies the app into a clean `~/Applications`, runs first
  setup with an empty inherited environment, checks MPS doctor, verifies that
  pip and Python bytecode caches stay under App Support, and verifies codesign
  again after setup.
- `scripts/notarize_mac_dmg.sh` is the release distribution path. It builds a
  Developer ID signed app with hardened runtime, signs the DMG, submits it with
  `xcrun notarytool submit --keychain-profile --wait`, staples the accepted
  ticket with `xcrun stapler`, and validates both the DMG and mounted app with
  Gatekeeper. API keys and certificates are never written to the repo; only the
  Keychain profile name is referenced.
- Actual separate-account validation is tracked in
  `docs/28_TEST_ACCOUNT_INSTALL_VERIFICATION.md`. The DMG includes
  `Verify Test Account Install.command`, which writes
  `logs/test_account_install_evidence.json` after Setup completes in the test
  account. Bring that JSON back to the development account and run
  `scripts/import_test_account_evidence.sh` to write a release-gate verdict
  under `artifacts/test_account_install/`.
- CT folder handling remains split from inference. The SwiftUI app launches the
  separate C++ normalizer binary for metadata audit. If exactly one clean CT
  series is accepted, it converts that series with the bundled `dcm2niix` and
  sets the generated NIfTI as the current input automatically. If multiple clean
  series are accepted, the app asks the user to choose the `撮影データ`. Rescue, compressed,
  geometry-missing, DICOMDIR-only, or rejected folders stop before preview
  creation with a reason and next action.
- CT folders exported from viewing software as display-oriented slice images are
  treated as a rescue 3D preview path, not as recovered original CT. The app only
  offers axial-like sparse volumes for preview creation, requires a slice
  confirmation screen, preserves the original spacing/affine, and labels the
  result as non-clinical preview. Coronal/sagittal groups are not fused into a
  high-resolution CT in the distribution build.
- If a CT cannot be imported automatically, the UI explains that the CT image is
  not necessarily broken and asks the user to contact the developer with a
  screenshot, detailed log, and the name of the CT viewing/export software when
  support is needed.
- A separate Mac-oriented C++ DICOM normalizer/rescue binary has been started
  under `native/dicom_normalizer/`. It now implements metadata audit,
  `convert-clean` for `original_ct_geometry_ok`, and explicit
  `prepare-rescue` for Secondary Capture axial-looking stacks.
- The SwiftUI app launches the C++ normalizer through `dicom_normalizer_bridge`.
  Selecting a CT folder routes the folder to the C++ binary, writes
  audit/convert logs under App Support, and never starts preview creation until
  the user explicitly presses `3Dプレビューを作成` after CT intake is ready.
- The C++ normalizer now has an explicit `prepare-rescue` path for
  secondary-capture axial-looking stacks. It requires a series number and
  human-specified spacing, isolates the selected DICOM files, runs dcm2niix,
  patches an uncompressed NIfTI header/affine to the requested spacing, and
  writes rescue warning metadata plus `rescue_validation.json`.
- `totalsegmentator-wrapper-dicom-normalizer audit` now records DICM prefix/File Meta presence,
  transfer syntax/compression, pixel-data presence, and multi-frame counts. It
  can classify single-file multi-frame Secondary Capture axial stacks as rescue
  candidates, classify DICOMDIR as `dicomdir_only`, classify compressed syntax
  as `compressed_pixel_data`, and report optional DCMTK/GDCM tool availability.
- `python -m totalsegmentator_wrapper_mac doctor` now includes a `dicom_normalizer` block
  so packaged installs can confirm whether the bundled/native binary is
  discoverable.
```

## 禁止事項

MVP中に以下を始めない。

```text
- DICOM完全対応
- ONNX/Core ML化
- DentalSegmentator純正nnU-Net runner
- Slicer extension開発
- Slicer内MPS推論対応
- 診断用viewer/Segment Editor自作
- App Store配布
- 診断/治療計画用途の表現
```
