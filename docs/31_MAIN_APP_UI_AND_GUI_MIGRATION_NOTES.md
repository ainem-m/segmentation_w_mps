# Main App UI and GUI Migration Notes

Date: 2026-06-14

## Summary

The distributed Mac app is moving from Tk to a native SwiftUI shell. SwiftUI is
now the packaged `.app` frontend for both Setup and the main workflow. The
Python package remains the processing backend and is invoked through argv-list
subprocess calls.

Tk remains in the source tree as a legacy development fallback:

```bash
python -m totalsegmentator_wrapper_mac.ui_tk
totalsegmentator-wrapper-mac-ui
```

It is no longer the normal DMG launch path.

## Current UI Direction

- `Contents/MacOS/TotalSegmentatorWrapperForMac` is a SwiftUI executable.
- Setup and the main app are Japanese, Mac-native screens.
- The first app view remains a two-choice interview:

```text
Sampleで流れを体験する
自分のCTを開く
```

- Long paths, backend commands, and raw logs stay hidden behind `詳細ログを表示`.
  The SwiftUI drawer shows only the tail of large logs (`LOG_TAIL_BYTES`) so
  opening details cannot freeze the app; full logs are opened through Finder via
  `ログファイルを開く` / `ログフォルダを開く`.
- During inference, TotalSegmentator/tqdm progress is normalized into
  `RUN_PROGRESS` lines in `logs/run.log`; SwiftUI reads those lines and shows a
  determinate progress bar when percentage data is available.
  Plain TotalSegmentator phase lines such as `Predicting...`, `Predicting s01:`,
  `Resampling...`, and `Saving segmentations...` are also normalized so the UI
  can show what stage is active even when no percentage is available.
  These percentages are treated as internal section progress, not whole-run
  completion. If a section reaches `100%` while the process is still running,
  the GUI switches back to an indeterminate bar and shows that the next step is
  being prepared. The SwiftUI screen also shows when the last progress update
  arrived, so a long quiet period reads as continued processing rather than a
  frozen app.
- Sample 1 3Dプレビュー still opens in the default browser; no WebView is embedded
  in this migration step.
- The Sample tutorial explicitly tells users that Sample 1 preview creation
  takes roughly 100 seconds on the target Mac. The precomputed Sample 1 3D
  viewer remains instant to open; the 100-second note applies to running the
  local preview workflow.
- After a successful run, SwiftUI launches
  `totalsegmentator_wrapper_mac surface-preview --case <output>` before showing the result
  screen. For `歯を1本ずつ分けて表示（ベータ）`, this uses the individual-teeth
  fullspace labelmap. For standard `歯列と顎骨をまとめて表示`, it derives a compact
  arch/jaw labelmap from `raw_totalseg` masks (`teeth_upper`, `teeth_lower`,
  `mandible`, and `skull`) and writes the same offline HTML viewer path:
  `surface_preview/index.html`.
- Own-data selection is one visible path: `CTを選ぶ`. A file is prepared as the
  CT volume input, while a folder routes to metadata audit first. Segmentation
  does not run directly on a selected folder.
- If the folder has exactly one clean CT candidate
  (`original_ct_geometry_ok` + `convert_clean`), SwiftUI automatically runs
  `dicom-normalizer-convert-clean` with the bundled `dcm2niix`, then sets the
  generated NIfTI as the current input. The user does not need to reopen the
  converted file.
- If multiple clean CT candidates are found, SwiftUI shows a short Japanese
  picker and the user presses `この撮影を使う` before conversion. Rescue,
  compressed, geometry-missing, timeout, DICOMDIR-only, and reject results remain
  blocked from preview creation and show a reason plus recovery buttons.
- SwiftUI tracks the selected input as `InputSource`: `none`, `sample`, `nifti`,
  or `dicomFolder`. Entering the Sample flow fixes the input to bundled Sample
  1. Entering the own-data flow clears Sample input unless the user explicitly
  chooses a new NIfTI or DICOM folder. This prevents accidental Sample runs from
  the own-data screen.
- A folder is never used as a direct preview input. Each preview run creates a fresh case
  directory below the selected output root instead of reusing the previous case
  directory.
- Stopping a run now enters `停止要求済み` and terminates the child process. If
  it remains alive after the grace period, the process is killed and the app
  returns to a recoverable result screen.
- If segmentation model inference succeeds but `surface-preview` fails, the result screen offers
  `3Dプレビューを再生成`. This calls only `surface-preview --case <output>` and
  does not rerun inference.
- `歯を1本ずつ分けて表示（ベータ）` maps to the experimental teeth CLI path and
  uses the existing MPS-only safeguards.
- Setup disables TotalSegmentator usage stats (`利用状況データ`) inside the
  private App Support runtime. The Setup and app text explain that
  DICOM/CT/processing results are not sent, and that first preview creation may
  download model weights. The Sample 1 `100秒前後` estimate is documented as
  the model-already-downloaded case.

## Framework Decision

| Option | Decision | Reason |
| --- | --- | --- |
| SwiftUI shell | Adopt | Best fit for Mac-only distribution, native file panels, trustworthy setup UX, and future notarization. |
| PySide6 | Do not adopt now | Python-native, but increases Qt runtime/deployment surface and setup weight. |
| Tauri/Electron | Do not adopt now | Modern UI, but adds JS/Rust or Chromium/Node layers around an already separate Python backend. |
| Tk maintained | Legacy fallback only | Useful for source checkout debugging, but too plain for the target user experience. |

## Packaging Notes

The SwiftUI frontend is built from:

```text
native/macos/TotalSegmentatorWrapperForMac/
```

`scripts/build_mac_app.sh` requires full Xcode, not Command Line Tools alone,
because SwiftUI builds need a matching Swift compiler and macOS SDK. End users
do not need Xcode; this is only a build-machine requirement.

The app bundle still includes the same backend parts:

```text
Contents/Resources/wheels/totalsegmentator_wrapper_mac-*.whl
Contents/Resources/constraints/macos-arm64-py312.txt
Contents/Resources/bin/totalsegmentator-wrapper-dicom-normalizer
Contents/Resources/python/cpython-3.12/
Contents/Resources/sample1/
Contents/Resources/setup_manifest.json
```

`setup_manifest.json` records:

```text
ui_frontend: swiftui
legacy_tk_ui: true
```

## Backend Boundary

SwiftUI does not import Python. It launches:

```text
python -m totalsegmentator_wrapper_mac setup ...
python -m totalsegmentator_wrapper_mac run ...
python -m totalsegmentator_wrapper_mac dicom-normalizer-audit ...
python -m totalsegmentator_wrapper_mac update-check ...
```

All commands are argv lists through `Process`; shell string execution is not
used. `TotalSegmentator` defaults to the App Support venv path:

```text
~/Library/Application Support/TotalSegmentatorWrapperMac/env/bin/TotalSegmentator
```

This avoids the previous `TotalSegmentator` not found failure when the GUI
inherited a sparse PATH.

## Validation

Automated coverage is split:

```text
[x] Python backend unit tests
[x] static SwiftUI source/package invariants
[x] SwiftUI navigation dead-end coverage
[x] C++ DICOM normalizer tests
[x] xcodebuild/swiftc app build on a full Xcode machine
[x] zero-env `.app` setup with MPS doctor
[x] zero-env DMG install setup with MPS doctor
[ ] manual SwiftUI desktop interaction pass
[ ] full segmentation run through SwiftUI window outside Codex sandbox
```

Navigation coverage is implemented in `tests/test_swiftui_navigation_coverage.py`.
It checks that every `AppScreen` is rendered, has the expected user actions, and
that result/failure screens expose non-output-dependent recovery routes:
`入力へ戻る`, `もう一度実行` / `もう一度確認`, and `最初に戻る`.
It also checks that Sample and own-data screens have explicit cross-branch
escape buttons, and that DICOM audit result retry runs DICOM audit again rather
than accidentally starting segmentation.
