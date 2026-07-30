# Windows WPF Individual Teeth beta verification

Date: 2026-07-30

Scope: Windows 10 engineering evidence for the fixed Individual Teeth beta
operation. This is not a Windows release or model-distribution approval.

## Result

PASS. The WPF shell selected the fixed `run_nifti_individual_teeth`
coordinator operation and completed the bundled non-patient NIfTI sample with
the real TotalSegmentator `teeth` task on strict `cuda:0`.

- TotalSegmentator 2.14.0, PyTorch 2.11.0+cu126, CUDA build 12.6
- NVIDIA GeForce RTX 2060, driver 572.83, compute capability 7.5
- fixed 5 mm ROI margin, robust craniofacial preflight, force-split disabled
- app-private Dataset113 checkpoint; no dependency resolution or public index
- 54 nonempty labels in the ROI labelmap
- full-space labelmap shape 209 x 209 x 161, 581022 nonzero voxels
- full-space affine matches the bundled source
- exactly one `operation_completed`, coordinator OS exit code 0
- Job members after completion: 0; no survivors
- verified staging promotion, two nonempty NIfTI masks, local-only preview

The run manifest separately records `requested_policy=cuda_required`,
`requested_device_index=0`, `resolved_device=cuda:0`,
`fallback_allowed=false`, and `fallback_occurred=false`.

## Negative path

With `CUDA_VISIBLE_DEVICES=-1`, the coordinator emitted exactly one
`operation_failed` event with `cuda_unavailable`, exited with code 2, did not
promote final output, left no active Job process, and did not start a CPU run.
The invalid-index and missing-Dataset113 paths are covered by coordinator
tests.

## UI and compatibility

The four comparison cards retain their reference images, names, descriptions,
and selection controls. Individual Teeth beta is selectable only after the
app-private model gate passes. Its selected state explicitly identifies the
beta feature, potentially longer runtime, strict CUDA use, and no CPU
fallback. ToothSeg remains comparison-only.

The WPF contract self-test passed with 23 buttons, automation names, keyboard
focusability, dynamic system colors/labels, per-monitor-v2, and long-path
awareness. External UI Automation interaction remains unverified.

## Checks

- `pip check`: PASS
- production import matrix: PASS
- Python `unittest` discovery: PASS, 293 tests, 3 skipped
- ProcessSupervisor Release build: PASS, 0 warnings, 0 errors
- CoordinatorShell Release build: PASS, 0 warnings, 0 errors
- WPF contract self-test: PASS
- strict CUDA Conv3d/normalization/activation/ConvTranspose3d smoke: PASS
- real TotalSegmentator Individual Teeth CUDA run: PASS
- hidden-GPU typed failure: PASS
- artifact manifest and local offline preview: PASS
- stdout JSONL sensitive-pattern scan: PASS
- `git diff --check`: PASS

## Unverified

Windows 11, clean-machine model packaging/distribution, external UI
Automation, actual keyboard traversal, high contrast and non-96-DPI layouts,
Individual Teeth mid-inference cancellation, ToothSeg execution, DICOM rescue,
installer/signing, update, and rollback remain UNVERIFIED.
