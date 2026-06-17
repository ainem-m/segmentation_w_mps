# Case02 Native DICOM Validation Notes

## Summary

Case02 was validated from a local dental CBCT DICOM folder using an anonymized
artifact name. CPU was not used. The successful path was:

```text
DICOM -> dcm2niix Eq_1 NIfTI at native ~0.203 mm
native NIfTI -> craniofacial_structures with TotalSegmentator --robust_crop on MPS
native NIfTI + robust craniofacial masks -> experimental teeth MPS, margin 5 mm
teeth fullspace labelmap -> smoothed STL + offline HTML surface preview
```

## DICOM Conversion

Command family:

```text
dcm2niix -z y -b n -f case02_cbct -o artifacts/samples/case02_dicom_convert <dicom-folder>
```

Important conversion notes:

```text
DICOM files found: 314
Warning: Patient Position not specified
Warning: Unable to determine manufacturer
Warning: Instance Number order is not spatial
Warning: Interslice distance varies in this volume
```

The normal output and dcm2niix equalized output were:

```text
case02_cbct.nii.gz      shape 512 x 512 x 314, spacing ~0.203 mm
case02_cbct_Eq_1.nii.gz shape 512 x 512 x 395, spacing ~0.203 x 0.203 x 0.202 mm
```

The successful downstream input was:

```text
artifacts/samples/case02_dicom_convert/case02_cbct_Eq_1.nii.gz
```

## Craniofacial Preflight

Downsampling to 0.5 mm was tried first:

```text
artifacts/samples/case02_cbct_eq_0p5mm.nii.gz
shape: 208 x 208 x 160
```

It ran on MPS but all `craniofacial_structures` masks were empty.

Native-resolution `case02_cbct_Eq_1.nii.gz` with the default crop path also ran
on MPS but produced all-empty masks with:

```text
INFO: Crop is empty. Returning empty segmentation.
```

The working preflight used TotalSegmentator directly with robust crop:

```text
TotalSegmentator \
  -i artifacts/samples/case02_dicom_convert/case02_cbct_Eq_1.nii.gz \
  -o artifacts/cli_smoke/case02_craniofacial_native_mps_robust/segmentations/raw_totalseg \
  -ta craniofacial_structures \
  -d mps \
  --robust_crop \
  -nr 1 \
  -ns 1 \
  -v
```

The wrapper now exposes the same option for repeat runs:

```text
PYTHONPATH=src \
TOTALSEG_HOME_DIR=artifacts/totalseg_home \
TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights \
.venv/bin/python -m totalsegmentator_wrapper_mac run \
  --input artifacts/samples/case02_dicom_convert/case02_cbct_Eq_1.nii.gz \
  --output artifacts/cli_smoke/case02_craniofacial_native_mps_robust \
  --task craniofacial_structures \
  --device mps \
  --totalseg-bin .venv/bin/TotalSegmentator \
  --no-copy-input \
  --robust-crop
```

Wrapper validation output:

```text
artifacts/cli_smoke/case02_craniofacial_native_mps_robust_wrapper
```

Wrapper validation result:

```text
status: success
actual_device: mps
fallback_reason: null
benchmark.run.robust_crop: true
elapsed_seconds: 231.900
run.log command contains: --robust_crop
teeth_upper.nii.gz nonzero: 1,664,878
teeth_lower.nii.gz nonzero: 1,385,713
```

Key nonzero masks:

```text
head.nii.gz           43,900,531
skull.nii.gz           3,793,487
mandible.nii.gz        3,855,662
teeth_upper.nii.gz     1,664,878
teeth_lower.nii.gz     1,385,713
sinus_maxillary.nii.gz 2,594,994
sinus_frontal.nii.gz           0
```

Finding:

```text
For this dental CBCT, native resolution alone was not enough. The key fix was
TotalSegmentator --robust_crop for craniofacial preflight.
```

## Experimental Teeth MPS

Successful command:

```text
PYTHONPATH=src \
TOTALSEG_HOME_DIR=artifacts/totalseg_home \
TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights \
.venv/bin/python -m totalsegmentator_wrapper_mac run \
  --input artifacts/samples/case02_dicom_convert/case02_cbct_Eq_1.nii.gz \
  --output artifacts/cli_smoke/case02_teeth_native_mps_margin5 \
  --task teeth \
  --device mps \
  --totalseg-bin .venv/bin/TotalSegmentator \
  --no-copy-input \
  --experimental-teeth \
  --teeth-crop-margin-mm 5.0 \
  --teeth-timeout-sec 3600 \
  --teeth-craniofacial-case artifacts/cli_smoke/case02_craniofacial_native_mps_robust
```

If no precomputed craniofacial case is supplied, use the internal robust
preflight path:

```text
--teeth-robust-craniofacial-preflight
```

Result:

```text
status: success
actual_device: mps
elapsed_seconds: 112.115
child inference_elapsed_sec: 104.243
torch.mps_fallback_env: null
patch_applied: true
mps_gate.convtranspose3d_fp32: passed
ROI shape: 395 x 321 x 308
ROI volume ratio: 0.377
ROI margin: 5 mm
fullspace output shape: 512 x 512 x 395
fullspace affine_matches_source: true
non_empty_label_count: 54
nonzero_voxels: 8,741,072
```

Labels absent relative to the previous 56-label case include likely missing or
not-detected distal structures, so visual inspection is required before treating
the case as demo-grade.

## Surface Preview

Generated:

```text
artifacts/cli_smoke/case02_teeth_native_mps_margin5/surface_preview/index.html
artifacts/cli_smoke/case02_teeth_native_mps_margin5/surface_preview/preview_summary.json
artifacts/cli_smoke/case02_teeth_native_mps_margin5/surface_preview/combined/*.stl
artifacts/cli_smoke/case02_teeth_native_mps_margin5/surface_preview/labels/*.stl
```

Summary:

```text
label_count: 54
label STL count: 54
combined STL count: 4
surface_preview size: ~475 MB
viewer.transparent_rendering: jaw_depth_prepass_front_shell
external URLs / CDN / script src: none
```

Combined STL triangle counts:

```text
all_nonzero:         1,861,242
dental_hard_tissue: 1,179,142
pulp:                 120,654
jaws:               1,847,442
```

Preview mesh triangle counts:

```text
jaws:                 463,202
dental_hard_tissue:   295,838
pulp:                  30,470
all_nonzero:          465,914
```

## Notes

- No CPU segmentation path was used.
- Sandbox MPS checks can report MPS unavailable; proof runs were executed
  outside the sandbox to access Apple MPS.
- `--robust_crop` downloaded TotalSegmentator Task 297 weights on first use.
- At native 0.203 mm, a 10 mm ROI margin would exceed the current near-whole
  volume guard; 5 mm passed and kept the ROI manageable. For ~0.2 mm native
  CBCT, start with `--teeth-crop-margin-mm 5.0`; treat 10 mm or larger as a
  deliberate retry because it can trip the near-whole-volume guard.
- High-resolution STL export is substantially larger than the 0.5 mm sample.
  Use `surface-preview --preview-step-size 3` or `4` to shrink only the embedded
  HTML preview meshes while preserving full-quality STL outputs. Larger values
  are allowed for coarse inspection only and record a preview warning because
  small structures may be under-sampled.
