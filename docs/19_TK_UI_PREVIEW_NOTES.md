# 19 Tk UI Preview Notes

Date: 2026-06-11

## Summary

A minimal desktop UI was added using Python's standard Tkinter library. This
keeps the preview dependency-light and wraps the existing CLI/backend rather
than introducing a separate runtime.

Entry point:

```bash
scripts/launch_tk_ui.sh

# or, when PYTHONPATH/package installation is already configured:
python -m totalsegmentator_wrapper_mac.ui_tk
totalsegmentator-wrapper-mac-ui
```

Source module:

```text
src/totalsegmentator_wrapper_mac/ui_tk.py
```

## Original UI Scope

Implemented:

```text
- NIfTI input path picker
- output folder picker
- user-facing run mode selector
- device selector
- TotalSegmentator executable field
- Run button
- Stop button that sends SIGINT to the backend process
- streaming log view via logs/run.log tailing
- elapsed time/status display
- completion summary from benchmark.json and mask_stats.json
- Open Output button
- Open Slicer Script button
```

Not implemented:

```text
- 3Dプレビュー
- segmentation editor
- DICOM browser
- app bundle packaging
- codesigning/notarization
- automatic Slicer launch
```

## Legacy Status

The distributed `.app` frontend has moved to SwiftUI. Tk is retained as a
source-checkout development fallback and comparison UI, but it is no longer the
normal DMG launch path.

## Final Tk UI Direction

The final Tk fallback presents a Japanese interview-style wizard. The first view
is intentionally only a two-choice question:

```text
Sampleで流れを体験する
自分のCTを開く
```

The layout is split between a compact left-side progress rail and a right-side
task panel. Long paths, backend commands, and streaming logs are hidden by
default behind `詳細ログを表示`.

The visible flow is:

```text
- 1 目的
- 2 入力
- 3 実行
- 4 結果
```

`Sampleで流れを体験する` is a tutorial path:

```text
1. Sample 1の3Dプレビューを開く
2. Sample 1を入力に使う
3. 3Dプレビュー作成（segmentation）を実行
4. 結果フォルダ / 3Dプレビュー / 要約 を確認
```

The app does not open the browser or start a run automatically; users explicitly
press each button. In the SwiftUI distribution path, `自分のCTを開く` exposes
one primary action, `CTを選ぶ`; file inputs are prepared directly and folder
inputs are audited/converted when they are clean CT candidates.
Sample 1の3Dプレビュー作成は、このMacでおおむね100秒前後かかることを
実行前に明記する。

DICOM folders are still metadata-audited before any segmentation workflow. The
TotalSegmentator runner path remains available under advanced settings.
The main UI does not expose raw TotalSegmentator task names. It presents two
run modes:

```text
歯列と顎骨をまとめて表示
歯を1本ずつ分けて表示（ベータ）
```

`歯列と顎骨をまとめて表示` maps to `task=craniofacial_structures`.
`歯を1本ずつ分けて表示（ベータ）` maps to `task=teeth` and automatically
adds `--experimental-teeth --teeth-crop-margin-mm 5.0`.

## Backend Invocation

The UI calls:

```text
python -m totalsegmentator_wrapper_mac run ...
```

UI/public copy should keep these concepts separate:

```text
craniofacial_structures teeth_upper / teeth_lower: aggregate teeth masks
experimental teeth: individual teeth segmentation
```

It also prepares a backend environment for source-checkout usage:

```text
PYTHONPATH=src
TOTALSEG_HOME_DIR=artifacts/totalseg_home   if present
TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights   if present
MPLCONFIGDIR=artifacts/matplotlib_cache
XDG_CACHE_HOME=artifacts/cache
```

## Validation

Automated validation covers command construction and backend environment setup.
The actual GUI window was not opened from Codex.

```text
[x] unit tests pass
[x] compileall passes
[ ] manual UI launch on Mac desktop
[ ] MPS run through UI outside Codex sandbox
```

## Next Step

Future UI validation should use the SwiftUI shell. Keep this Tk entry point
working enough for source-checkout debugging, but do not treat it as the
packaged user experience.
