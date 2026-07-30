# Windows WPF clean-DICOM slice verification

Date: 2026-07-30
Evidence class: Windows 10 x64 engineering evidence
Windows 11: **UNVERIFIED**

## Result

The bounded clean-DICOM vertical slice passed on the engineering host:

```text
synthetic non-clinical DICOM folder
  -> native clean-series audit
  -> first original_ct_geometry_ok series
  -> series-key conversion with real dcm2niix
  -> exactly one verified nonempty NIfTI
  -> explicit WPF Run action
  -> unchanged coordinator protocol v1
  -> strict cuda:0 TotalSegmentator craniofacial_structures
  -> verified final artifacts and local-only preview
```

The WPF implementation mirrors the existing Swift flow only where required:
NIfTI/DICOM input choice, automatic use of the first clean candidate, optional
shooting-series selection, explicit conversion, and explicit segmentation
start. DICOM rescue, image interaction, and additional models were not added.

## Primary-path evidence

- Input: 193 synthetic DICOM files in two series, derived from the bundled
  non-clinical sample; aggregate SHA-256 is recorded in
  `synthetic-dicom-fixture.json`.
- Audit: two clean candidates; series 3 with 161 files selected using
  `first_geometry_ok`.
- Selection integrity: conversion is requested by native series key and the
  returned series UID, file count, classification, dcm2niix status, product
  boundary, and output provenance are verified.
- Converted volume: `209 x 209 x 161`, `int16`, `0.5 mm` isotropic, finite and
  nonempty.
- Coordinator operation: `6b1ecd36-ff19-4df1-8f54-8fcba00b5b83`.
- Device: requested `cuda_required`, index `0`; resolved `cuda:0`;
  fallback allowed/occurred are both false.
- Runtime: Python 3.12.10, PyTorch 2.11.0+cu126, CUDA build 12.6,
  RTX 2060, driver 572.83.
- Terminal result: one `operation_completed`; coordinator OS exit code `0`.
- Job evidence: created suspended, assigned before resume, 50 observed Job
  process IDs, zero active processes after completion, no survivors.
- Commit boundary: verified staging promoted to final; staging absent.
- Artifacts: 14 manifest entries and hashes verified; seven NIfTI masks, six
  nonempty; 15 offline-preview files and zero remote references.

## Negative and regression evidence

- A synthetic secondary-capture-only folder produced
  `dicom_clean_series_unavailable`; no NIfTI, coordinator operation, fallback,
  or segmentation was started.
- The modified coordinator request reader was verified with stdin kept open.
  It consumes the initial JSON document without consuming the following cancel
  line. The production entrypoint then returned the expected typed
  `input_not_found` diagnostic request result.
- Before that reader fix, two diagnostic WPF attempts completed DICOM intake
  but emitted no coordinator event and were manually terminated. Their Job
  descendants were confirmed absent and those attempts are not counted as
  success.
- Real bundled-sample cancellation returned one `operation_cancelled`,
  coordinator OS exit code `3`, zero survivors, and no final promotion.
- Synthetic parent/child/grandchild cancellation also left zero survivors.
- Python: 280 tests passed, 3 skipped.
- ProcessSupervisor and CoordinatorShell Release builds: 0 warnings, 0 errors.
- WPF contract: 17 buttons; automation names, keyboard-focusable controls,
  dynamic colors, dynamic labels, per-monitor-v2 and long-path-aware checks
  passed.

## UI/manual comparison

The current captures verify these Swift/manual-aligned controls and copy:

- `NIfTIファイルを選ぶ`
- `DICOMフォルダを選ぶ`
- `使用する撮影を変更`
- first-candidate explanation
- `この撮影を使う`
- `閉じる`
- explicit `このCTで3Dプレビューを作る`
- typed failure copy/details/recovery controls

The series preview uses the same `DicomCleanCandidate` type as real audit
results, so the capture exercises the production WPF bindings rather than an
anonymous preview-only object.

## Privacy boundary

No patient data was used. Repository evidence contains no user name, secret,
absolute input/output path, DICOM UID/key/description, raw third-party output,
or stderr tail. DICOM tool output is drained without retention. Coordinator
stderr is represented only by byte count and SHA-256.

## PASS / UNVERIFIED

| Criterion | Status |
| --- | --- |
| Clean DICOM audit and first-series conversion | PASS |
| Series-key identity and exactly-one NIfTI verification | PASS |
| Explicit strict-CUDA TotalSegmentator completion | PASS |
| No-clean-series typed failure without segmentation | PASS |
| Coordinator JSONL/staging/artifact contracts | PASS |
| Real and synthetic cancellation regression | PASS |
| Swift/manual control and wording comparison | PASS |
| Real user selection of the second clean series | UNVERIFIED |
| DICOM timeout/cancel through interactive UI | UNVERIFIED |
| Native file-dialog interaction automation | UNVERIFIED |
| Secondary-capture rescue/image interaction | UNVERIFIED |
| Additional models | UNVERIFIED |
| Clean/self-contained installation and signing | UNVERIFIED |
| Windows 11 | UNVERIFIED |

There is no blocking failure for this bounded Windows 10 clean-DICOM slice.
This is not a Windows product-completion claim.
