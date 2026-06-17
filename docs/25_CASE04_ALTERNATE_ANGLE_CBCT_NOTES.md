# Case04 Alternate-Angle CBCT Notes

## Summary

Case04 is a separate CBCT acquisition from Case02. It is a clean original CT
series with valid DICOM geometry, but the field of view is not a dental-arch
volume. The MPR review shows a temporal bone / TMJ-oriented scan, so it is not
appropriate for the experimental `teeth` workflow.

## DICOM Conversion

Input was converted with `dcm2niix` into a PHI-free artifact name:

```text
dcm2niix -z y -b n -f case04_cbct \
  -o artifacts/samples/case04_dicom_convert \
  <local DICOM folder>
```

DICOM audit:

```text
files: 514
series_count: 1
modality: CT
SOP class: CT Image Storage
ImageType: ORIGINAL\PRIMARY\AXIAL
rows x columns: 512 x 512
PixelSpacing: 0.15524 x 0.15524 mm
SliceThickness: 0.15524 mm
ImagePositionPatient: present on all slices
ImageOrientationPatient: present on all slices
```

`dcm2niix` output:

```text
artifacts/samples/case04_dicom_convert/case04_cbct.nii.gz
shape: 512 x 512 x 514
spacing: 0.15524 x 0.15524 x 0.15524 mm
size: 133 MB
```

Conversion warnings were limited to missing patient position and vendor tuning:

```text
Warning: Patient Position (0018,5100) not specified
Warning: Unable to determine manufacturer (0008,0070), so conversion is not tuned for vendor
```

## Visual QA

Generated MPR montage:

```text
artifacts/samples/case04_dicom_convert/case04_cbct_mpr_montage.png
```

The montage shows the acquisition is centered around temporal bone / TMJ
structures rather than the full maxillary and mandibular dental arches.

## Craniofacial MPS Smoke

Wrapper command family:

```text
PYTHONPATH=src \
TOTALSEG_HOME_DIR=artifacts/totalseg_home \
TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights \
.venv/bin/python -m totalsegmentator_wrapper_mac run \
  --input artifacts/samples/case04_dicom_convert/case04_cbct.nii.gz \
  --output artifacts/cli_smoke/case04_craniofacial_native_mps_robust \
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
elapsed_seconds: 107.177
robust_crop: true
```

Mask nonzero counts:

```text
head:            74,695,791
skull:           17,600,685
mandible:         1,490,889
sinus_maxillary:    408,283
sinus_frontal:            0
teeth_upper:              0
teeth_lower:              0
```

## Decision

```text
case status: clean DICOM, not dental-arch FOV
craniofacial MPS smoke: pass
experimental teeth path: stop
reason: teeth_upper and teeth_lower preflight masks are empty
```

This case is useful as an example of a valid CT that is outside the teeth
segmentation target domain. It should not be counted as a failed teeth model
case, because the dental arches are not present in the required field of view.
