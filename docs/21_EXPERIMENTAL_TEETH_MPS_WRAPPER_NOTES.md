# 21 Experimental Teeth MPS Wrapper Notes

Date: 2026-06-11

## Summary

`task=teeth` now has an opt-in experimental MPS path. The default behavior is
unchanged: `teeth` without `--experimental-teeth` fast-fails with the known
TotalSegmentator 2.14.0 CLI issue.

The experimental path runs `teeth` in a separate Python subprocess:

```bash
python -m totalsegmentator_wrapper_mac run \
  --input artifacts/samples/DZ-CBCT_jawcrop_0p5mm.nii.gz \
  --output artifacts/cli_smoke/teeth_parent_dry_run \
  --task teeth \
  --device mps \
  --experimental-teeth \
  --teeth-dry-run \
  --totalseg-bin .venv/bin/TotalSegmentator \
  --no-copy-input
```

The child command is:

```bash
python -u -m totalsegmentator_wrapper_mac.teeth_mps_child ...
```

It patches only `totalsegmentator.python_api.convert_device_to_string()` inside
the child process, rejects `PYTORCH_ENABLE_MPS_FALLBACK=1`, runs the MPS FP32
ConvTranspose3D gate, requires TotalSegmentator 2.14.0, and calls
`totalsegmentator(..., task="teeth", device="mps", ml=True)` for real runs.

## Implemented

- CLI flags:
  - `--experimental-teeth`
  - `--teeth-dry-run`
  - `--teeth-timeout-sec`
  - `--teeth-crop-margin-mm`
  - `--teeth-craniofacial-case`
  - `--teeth-force-split`
- Child subprocess wrapper:
  - module: `totalsegmentator_wrapper_mac.teeth_mps_child`
  - output benchmark: `logs/teeth_child_benchmark.json`
  - ROI output labelmap: `segmentations/teeth_experimental/teeth_multilabel_roi.nii.gz`
  - full-space output labelmap:
    `segmentations/teeth_experimental/teeth_multilabel_fullspace.nii.gz`
- ROI preflight:
  - uses `teeth_upper.nii.gz` and `teeth_lower.nii.gz` from a
    `craniofacial_structures` case
  - writes `input/teeth_roi.nii.gz`
  - writes `logs/teeth_roi.json`
  - fails on empty masks, affine/shape mismatch, tiny bbox, and near-whole-volume
    bbox
  - records `source_shape`, `roi_shape`, `voxel_volume_ratio`,
    `axis_extent_ratios`, `bbox_min_ijk`, and `bbox_max_ijk`
  - writes qform/sform code 1 on cropped ROI NIfTI
- Parent benchmark integration:
  - `logs/benchmark.json` records `experimental_teeth`, timeout, fallback state,
    child benchmark, patch metadata, MPS gate metadata, and validation metadata
  - parent records `child_status: timeout`, return code 124, log path, child
    benchmark path, and last parsed progress when the child is killed
- Slicer handoff:
  - recursively loads labelmaps under `segmentations/`
  - prefers `teeth_multilabel_fullspace.nii.gz` over ROI output when both exist
- Tk UI:
  - adds an `Experimental teeth` checkbox
  - checkbox default is off
  - command builder adds `--experimental-teeth` only when enabled

## Dry Validation Results

Child dry-run outside the Codex sandbox:

```text
artifact: artifacts/cli_smoke/teeth_child_dry_run/teeth_child_benchmark.json
status: success
torch: 2.12.0
TotalSegmentator: 2.14.0
mps_available: true
convtranspose3d_fp32: passed
mps_fallback_env: null
patch_applied: true
post_patch_string_mps: mps
post_patch_torch_device_mps: mps
```

Parent CLI dry-run outside the Codex sandbox:

```text
artifact: artifacts/cli_smoke/teeth_parent_dry_run/logs/benchmark.json
status: success
task: teeth
requested_device: mps
actual_device: mps
returncode: 0
experimental_teeth.dry_run: true
experimental_teeth.child_status: success
experimental_teeth.child_returncode: 0
```

Unit tests:

```text
env PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
14 tests passed
```

Syntax check:

```text
.venv/bin/python -m compileall src tests scripts
passed
```

## ROI Preflight Result

Actual DZ-CBCT jaw-crop craniofacial result was reused to generate an ROI only:

```text
craniofacial case:
artifacts/cli_smoke/dz_cbct_jawcrop_0p5mm_craniofacial_mps

margin table:
artifacts/cli_smoke/teeth_roi_margin_table_jawcrop_0p5mm/teeth_roi_margin_table.json
artifacts/cli_smoke/teeth_roi_margin_table_jawcrop_0p5mm/teeth_roi_margin_table.md
```

Important result:

```text
The previous volume_ratio 0.7682 was relative to the already-cropped
DZ-CBCT_jawcrop_0p5mm input, not the original full DZ-CBCT volume.
```

Current near-whole-volume rule:

```text
voxel_volume_ratio > 0.50
or max(axis_extent_ratios) > 0.90
```

Margin table on `DZ-CBCT_jawcrop_0p5mm`:

```text
5 mm:  success, roi_shape [170, 141, 112], voxel_volume_ratio 0.3316
10 mm: success, roi_shape [190, 152, 132], voxel_volume_ratio 0.4709
15 mm: failed, voxel_volume_ratio 0.639, max axis extent 0.950
20 mm: failed, voxel_volume_ratio 0.768, max axis extent 1.000
```

For this cropped input, the next smoke/full run should use 10 mm first.

## Timeout Smoke Result

Short timeout smoke outside the Codex sandbox:

```text
artifact: artifacts/cli_smoke/teeth_timeout_smoke_2sec/logs/benchmark.json
timeout_sec: 2
status: failed
returncode: 124
actual_device: mps
experimental_teeth.child_status: timeout
experimental_teeth.child_returncode: 124
experimental_teeth.roi.voxel_volume_ratio: 0.4708695652173913
experimental_teeth.roi.near_whole_volume: false
experimental_teeth.roi.geometry.qform_code: 1
experimental_teeth.roi.geometry.sform_code: 1
```

The child was terminated before it wrote `teeth_child_benchmark.json`, and the
parent still wrote a complete `logs/benchmark.json`. This is the desired failure
mode for smoke/timeout runs.

## First Full Run Result

The 15-minute smoke command with a 10 mm ROI margin completed before timeout:

```text
artifact: artifacts/cli_smoke/teeth_smoke_15min_margin10_mps
task: teeth
status: success
returncode: 0
actual_device: mps
elapsed_seconds: 98.48210795898922
child inference_elapsed_sec: 94.285
torch.mps_fallback_env: null
patch_applied: true
TotalSegmentator: 2.14.0
last_progress: 8/8
```

ROI:

```text
margin_mm: 10.0
roi_shape: [190, 152, 132]
voxel_volume_ratio: 0.4708695652173913
axis_extent_ratios: [0.8260869565217391, 0.6909090909090909, 0.825]
near_whole_volume: false
```

Outputs:

```text
segmentations/teeth_experimental/teeth_multilabel_roi.nii.gz
segmentations/teeth_experimental/teeth_multilabel_fullspace.nii.gz
```

Geometry and labels:

```text
ROI output shape: [190, 152, 132]
full-space output shape: [230, 220, 160]
full-space affine_matches_source: true
qform_code: 1
sform_code: 1
non_empty_label_count: 56
nonzero_total: 707,107
```

This clears the first engineering gate for experimental individual teeth:
MPS execution completed, no CPU fallback was used, the child patch path worked,
and the multilabel output is non-empty.

## STL Export Result

The full-space multilabel output was converted to STL for visual inspection:

```text
script: scripts/export_labelmap_to_stl.py
input:
artifacts/cli_smoke/teeth_smoke_15min_margin10_mps/segmentations/teeth_experimental/teeth_multilabel_fullspace.nii.gz

output:
artifacts/cli_smoke/teeth_smoke_15min_margin10_mps/stl/
```

STL export summary:

```text
label STL count: 56
total STL directory size: 80 MB
combined/all_nonzero.stl: 56 labels, 390,204 triangles
combined/dental_hard_tissue.stl: 30 labels, 171,668 triangles
combined/pulp.stl: 19 labels, 16,032 triangles
combined/jaws.stl: 2 labels, 383,004 triangles
```

Recommended visual inspection order:

```text
1. stl/combined/dental_hard_tissue.stl
2. stl/combined/jaws.stl
3. stl/combined/all_nonzero.stl
4. stl/labels/*.stl for individual labels
```

STL does not retain multilabel color/category metadata. Label identity is kept
in the per-label filenames and in `stl/stl_export_summary.json`.

For smoothed STL output and the Slicer-free offline HTML viewer, see
`docs/22_SURFACE_PREVIEW_NOTES.md`.

## Demo Readiness

The experimental `teeth` path now has three representative MPS completions on
the target Mac:

```text
DZ-CBCT jawcrop 0.5 mm: 98.48 s, 56 labels
Case02 native CBCT:     112.12 s, 54 labels
STS24 open data 0026:    85.40 s, 55 labels
```

Precomputed demo GO is allowed for the non-clinical preview path. The demo
should use the generated full-space labelmaps, STL exports, and offline
surface-preview HTML rather than rerunning long preflight steps live.

Common GO criteria satisfied:

```text
logs/benchmark.json run.status: success
logs/teeth_child_benchmark.json torch.mps_fallback_env: null
logs/teeth_child_benchmark.json mps_gate.convtranspose3d_fp32: passed
logs/teeth_child_benchmark.json validation.non_empty_label_count > 0
segmentations/teeth_experimental/teeth_multilabel_roi.nii.gz exists
segmentations/teeth_experimental/teeth_multilabel_fullspace.nii.gz exists
full-space output shape and affine match source CT
surface_preview/index.html exists
```

Remaining caveats:

```text
Slicer visual validation remains optional and deferred.
STS24 open data is unlabeled, so it is workflow proof rather than accuracy proof.
Clinical, diagnostic, and treatment-planning claims remain out of scope.
```

See `docs/27_THREE_CASE_DEMO_READINESS.md` for the consolidated table.
