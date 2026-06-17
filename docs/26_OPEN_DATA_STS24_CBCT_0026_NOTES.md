# Open Data STS24 CBCT 0026 Validation Notes

## Summary

An open-data dental CBCT candidate was found in the STS 2024 Challenge public
Google Drive. The usable case is an unlabeled 3D CBCT NIfTI:

```text
STS24_Train_Unlabeled_0026.nii.gz
```

It is suitable for Mac/MPS proof validation because it contains the dental
arches, is already in NIfTI format, and passes both the robust craniofacial
preflight and the experimental `teeth` MPS path.

## Source

Primary source:

```text
STS-Challenge-2024 GitHub
https://github.com/ricoleehduu/STS-Challenge-2024
```

Relevant public folder:

```text
STS24 Google Drive
  STS24-3DCBCT
    Train-Unlabeled
      STS24_Train_Unlabeled_0026.nii.gz
      STS24_Train_Unlabeled_0045.nii.gz
```

The Zenodo `Train-Labeled.zip` listed for STS 2024 was downloaded and inspected,
but it contains JPEG images and JSON masks, so it is not the 3D CBCT volume data
needed for this MPS proof path.

## Input QA

Downloaded artifact:

```text
artifacts/open_data/sts2024/STS24_Train_Unlabeled_0026.nii.gz
```

Input metadata:

```text
shape: 640 x 640 x 400
spacing: 0.25 x 0.25 x 0.25 mm
dtype: int16
qform_code: 1
sform_code: 1
compressed size: 144 MB
```

Generated MPR montage:

```text
artifacts/open_data/sts2024/STS24_Train_Unlabeled_0026_mpr_montage.png
```

MPR review shows upper and lower dental arches in the field of view. This is a
better teeth test candidate than Case04, which was a clean CT but outside the
dental-arch FOV.

## Craniofacial MPS Preflight

Command family:

```text
PYTHONPATH=src \
TOTALSEG_HOME_DIR=artifacts/totalseg_home \
TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights \
.venv/bin/python -m totalsegmentator_wrapper_mac run \
  --input artifacts/open_data/sts2024/STS24_Train_Unlabeled_0026.nii.gz \
  --output artifacts/cli_smoke/sts24_0026_craniofacial_mps_robust \
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
elapsed_seconds: 612.910
robust_crop: true
```

Key masks:

```text
teeth_upper:      727,020 voxels
teeth_lower:      463,932 voxels
mandible:       4,657,868 voxels
skull:          7,992,424 voxels
sinus_maxillary:1,047,544 voxels
```

The non-empty `teeth_upper` and `teeth_lower` masks make this case valid for the
experimental teeth ROI path.

## Experimental Teeth MPS

Command family:

```text
PYTHONPATH=src \
TOTALSEG_HOME_DIR=artifacts/totalseg_home \
TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights \
.venv/bin/python -m totalsegmentator_wrapper_mac run \
  --input artifacts/open_data/sts2024/STS24_Train_Unlabeled_0026.nii.gz \
  --output artifacts/cli_smoke/sts24_0026_teeth_mps_margin5 \
  --task teeth \
  --device mps \
  --totalseg-bin .venv/bin/TotalSegmentator \
  --no-copy-input \
  --experimental-teeth \
  --teeth-crop-margin-mm 5.0 \
  --teeth-timeout-sec 3600 \
  --teeth-craniofacial-case artifacts/cli_smoke/sts24_0026_craniofacial_mps_robust
```

Result:

```text
status: success
actual_device: mps
fallback_reason: null
elapsed_seconds: 85.403
child inference_elapsed_sec: 76.708
torch.mps_fallback_env: null
patch_applied: true
mps_gate.convtranspose3d_fp32: passed
```

ROI:

```text
margin: 5.0 mm
roi_shape: 302 x 265 x 234
voxel_volume_ratio: 0.114
near_whole_volume: false
```

Teeth output:

```text
non_empty_label_count: 55
roi labelmap:       segmentations/teeth_experimental/teeth_multilabel_roi.nii.gz
fullspace labelmap: segmentations/teeth_experimental/teeth_multilabel_fullspace.nii.gz
fullspace shape:    640 x 640 x 400
nonzero_voxels:     4,405,972
```

## Decision

```text
case status: open-data dental CBCT candidate
craniofacial MPS preflight: pass
experimental teeth MPS: pass
recommended next step: generate surface-preview and perform visual QA
```

This case can serve as an open-data demo candidate, subject to license/citation
requirements and manual visual inspection. It is unlabeled, so it is useful for
workflow proof and visual QA, not quantitative accuracy evaluation.
