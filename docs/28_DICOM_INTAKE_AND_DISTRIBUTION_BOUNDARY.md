# CT Intake and Distribution Boundary

Date: 2026-06-15

## Summary

The distributed Mac app now presents CT intake as one user path:

```text
CTを選ぶ -> 取り込み確認 -> 3Dプレビューを作成 -> 3Dプレビューを開く
```

The UI does not ask ordinary users to choose between DICOM, NIfTI, conversion,
or `dcm2niix`. Those words remain in logs, CLI commands, and developer notes.

## User-Facing Flow

`CTを選ぶ` accepts either a file or a folder:

- File: treat it as a CT volume input and return to `3Dプレビューを作成`.
- Folder: run the bundled C++ normalizer audit first.
- One clean CT candidate: convert automatically with the bundled `dcm2niix`,
  set the generated NIfTI as the current input, and return to
  `3Dプレビューを作成`.
- Multiple clean CT candidates: show series number, description, and file count;
  the user presses `この撮影を使う` for the desired candidate.
- Rescue, compressed, geometry-missing, timeout, DICOMDIR-only, or rejected data:
  stop before preview creation and show the short reason plus recovery actions.

Preview creation never starts automatically after CT intake. The user must press
`3Dプレビューを作成`.

## Packaged Tools

The `.app` bundle includes:

```text
Contents/Resources/bin/totalsegmentator-wrapper-dicom-normalizer
Contents/Resources/bin/dcm2niix
```

`scripts/build_mac_app.sh` requires `TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX` and fails the packaging
build if the executable is missing or not executable. The app manifest records:

```text
dcm2niix_sha256
dcm2niix_version
dcm2niix_source
```

The bundled `dcm2niix` notice is written to
`Contents/Resources/THIRD_PARTY_NOTICES.txt`.

## Boundary

This is still not a clinical DICOM normalizer. The app supports conservative
mainline CT intake only:

```text
original_ct_geometry_ok + next_action=convert_clean
```

The app does not:

- infer missing geometry
- OCR burned-in spacing text
- automatically rescue Secondary Capture screen saves
- fuse 3-direction display slice exports into a high-resolution CT
- anonymize DICOM data
- validate diagnostic accuracy
- make treatment-planning claims

CT folders exported from viewing software as display-oriented slice images are
handled as a rescue 3D preview lane. Only axial-like sparse volumes can proceed
to preview creation after slice confirmation; coronal/sagittal groups are reference-only.
Spacing and affine are preserved. If original axial CT DICOM exists, it should
always be preferred over rescue data.

## Safety Rules

- DICOM/CT/processing results are not uploaded.
- Update checks run only when the user presses `更新を確認`. They do not send
  DICOM/CT paths, logs, processing output, or user IDs.
- Conversion and processing results are written under App Support or the user-selected
  output root.
- A selected folder is never passed directly to segmentation model inference.
- Failed intake must always expose `CTを選び直す`, `詳細ログを表示`, and
  `最初に戻る` style recovery routes.
- Failed intake should explain that a CT viewing software may have exported
  display-oriented slice images, and that the CT image itself is not necessarily
  broken.

## Public Wording

Use wording like:

```text
Mac上でCTを選ぶと、通常CTとして安全に取り込めるか確認し、3Dプレビューと3D確認までローカルに進めます。
```

Avoid:

```text
- diagnostic
- treatment planning
- surgical planning
- automatic DICOM repair
- validated accuracy
- clinical-grade
```
