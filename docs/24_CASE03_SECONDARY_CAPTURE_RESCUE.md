# Case03 Secondary Capture Rescue Notes

## Summary

Case03 was a problematic DICOM export that 3D Slicer could not load as a normal
volume. The folder was not a standard original CT series. Most image files were
secondary-capture screen-save images, but the `AXIAL BO` stack could be rescued
as a non-diagnostic pseudo volume for experimentation.

This case is not segmentation-grade original CT data. Prefer a fresh original
axial CT DICOM export if available.

This rescue was intentionally kept as a manual procedure. Do not add an
automatic DICOM rescue CLI to the Mac/MPS preview package yet; broad DICOM
repair and secondary-capture normalization belong to a future DICOM normalizer.

## DICOM Findings

The input folder contained 545 DICOM files across 8 series.

Key series:

```text
Series 200 AXIAL BO:       138 files
Series 201 CORONAL BO:     125 files
Series 202 SAGITTAL BO:    143 files
Series 300 AXIAL ST:        46 files
Series 301 CORONAL ST:      42 files
Series 302 SAGITTAL ST:     48 files
Series 999 Dose Report:      1 file
```

Important findings:

```text
SOP Class: Secondary Capture Image Storage
ImageType: DERIVED\SECONDARY\SCREEN SAVE\AVERAGE
BurnedInAnnotation: YES
PixelSpacing: missing
ImagePositionPatient: missing
ImageOrientationPatient: missing
```

This explains why Slicer could not load it as a regular CT volume.

## Rescue Artifacts

Artifacts:

```text
artifacts/samples/case03_secondary_capture_rescue/
  case03_dicom_series_audit.json
  case03_pseudo_volume_metadata.json
  case03_rescue_validation.json
  case03_axial_bo_raw_dcm2niix.nii.gz
  case03_axial_bo_patched_spacing_0p6_0p6_0p9375.nii.gz
  dcm2niix/dcm2niix_axial_bo.log
  qa/case03_axial_bo_patched_mpr_montage.png
  qa/case03_craniofacial_mask_overlay_montage.png
```

The rescued pseudo volume used only:

```text
SeriesNumber: 200
SeriesDescription: AXIAL BO
shape: 512 x 512 x 138
```

`dcm2niix` produced a raw NIfTI with inferred spacing:

```text
1.0 x 1.0 x 0.9375 mm
```

The patched pseudo volume uses spacing inferred from burned-in screen text and
the `SliceThickness` tag:

```text
0.6 x 0.6 x 0.9375 mm
```

## Manual Rescue Procedure

This procedure documents what was done for Case03. It is not a supported import
pipeline and should not be generalized without a dedicated DICOM normalizer.

1. Audit the folder as DICOM metadata first, not as an image volume.
   - Confirm that the main files are Secondary Capture rather than original CT.
   - Record `SOPClassUID`, `ImageType`, `BurnedInAnnotation`, series number,
     series description, rows, columns, and file count.
   - Check whether `PixelSpacing`, `ImagePositionPatient`, and
     `ImageOrientationPatient` are missing.
2. Reject non-volume series from the rescue attempt.
   - Exclude Scout/localizer, Dose Report, coronal MPR, sagittal MPR, and
     short or mixed-shape series.
   - For Case03, only `Series 200 AXIAL BO` was used.
3. Isolate that one axial-looking series into a new folder.
   - Preserve the original files elsewhere.
   - Keep the isolated folder inside a local artifact directory because
     secondary-capture images can contain burned-in PHI.
4. Run `dcm2niix` on the isolated series and keep the raw output plus log.
   - For Case03, `dcm2niix` produced `512 x 512 x 138`.
   - The expected warnings were missing orientation, non-spatial ordering, and
     bogus spatial matrix.
5. Create a patched pseudo NIfTI only with explicitly chosen spacing.
   - Do not infer spacing automatically in product code.
   - For Case03, the raw dcm2niix spacing was `1.0 x 1.0 x 0.9375 mm`.
   - The patched spacing was `0.6 x 0.6 x 0.9375 mm`, based on burned-in
     screen text and the `SliceThickness` tag.
   - Write qform/sform consistently and save metadata that flags inferred
     geometry.
6. Generate an MPR montage before segmentation.
   - Confirm that the stack is visually coherent enough for an experiment.
   - Confirm that black borders and screen text are obvious hazards.
7. If testing segmentation, run only an explicit experimental craniofacial pass.
   - Use the patched pseudo NIfTI.
   - Use `craniofacial_structures`, `--device mps`, and `--robust-crop`.
   - Do not automatically proceed to `teeth`.
8. Review mask overlays before treating the result as usable.
   - Stop if masks follow screen overlays, burned-in text, or black borders.
   - If masks roughly follow anatomy, label the case as
     `pass_with_warnings` and keep it precomputed only.

Every artifact from this procedure should carry these warnings:

```text
secondary_capture: true
geometry_inferred: true
burned_in_annotation: true
not_segmentation_grade_original_ct: true
```

## MPS Craniofacial Rescue

Command family:

```text
PYTHONPATH=src \
TOTALSEG_HOME_DIR=artifacts/totalseg_home \
TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights \
.venv/bin/python -m totalsegmentator_wrapper_mac run \
  --input artifacts/samples/case03_secondary_capture_rescue/case03_axial_bo_patched_spacing_0p6_0p6_0p9375.nii.gz \
  --output artifacts/cli_smoke/case03_axial_bo_pseudo_craniofacial_mps_robust \
  --task craniofacial_structures \
  --device mps \
  --totalseg-bin .venv/bin/TotalSegmentator \
  --no-copy-input \
  --robust-crop
```

Result:

```text
status: success
actual_device: mps
fallback_reason: null
benchmark.run.robust_crop: true
elapsed_seconds: 3773.001
elapsed_minutes: 62.88
```

The run completed, but it is not live-demo friendly. The main prediction step
alone took:

```text
Predicted in 3707.32s
```

Mask nonzero counts:

```text
head:           15,799,403
skull:           2,591,547
mandible:          380,250
teeth_upper:        74,954
teeth_lower:        53,772
sinus_maxillary:   465,527
sinus_frontal:      57,294
```

Overlay QA did not show an obvious black-border or screen-text-only failure.
The masks roughly follow anatomy, but the source is still secondary-capture
screen-save data with inferred geometry.

## Decision

```text
craniofacial rescue status: pass_with_warnings
teeth_upper non-empty: true
teeth_lower non-empty: true
overlay / black border failure observed: false
live demo suitability: poor, due to ~63 min craniofacial runtime
```

Recommended next step:

```text
Prefer original axial CT DICOM.
If continuing experimentally, reuse the completed craniofacial case and do not
treat any downstream teeth output as segmentation-grade.
```

Do not run this case as a public proof path unless precomputed.

## Product Boundary

The preview package should continue to prefer NIfTI input or clean DICOM that an
external converter can handle. Secondary-capture rescue is useful as engineering
knowledge, but shipping it as a rule-based feature would create a packaging,
support, and safety burden that belongs to a separate DICOM normalizer project.
