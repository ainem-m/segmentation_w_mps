# 12 References and Source Notes

This file records the sources that justify the technical strategy. Re-check before publishing because dependencies may change.

## PyTorch MPS ConvTranspose3D

- PyTorch PR: “Enable ConvTranspose3D for FP32 and Complex64”
- URL: https://github.com/pytorch/pytorch/pull/154696
- Notes:
  - The PR enables ConvTranspose3D support for FP32 and Complex64 on MPS.
  - It states that macOS 14 and 15 have support.
  - FP16/BF16 were left unsupported due to discrepancy/error concerns.
  - Merged June 2025.

Related issue:

- URL: https://github.com/pytorch/pytorch/issues/130256
- Notes:
  - Earlier issue described `torch.nn.functional.conv_transpose3d` as unsupported on MPS and blocking nnU-Net-style segmentation on Mac.

## TotalSegmentator

- GitHub: https://github.com/wasserth/TotalSegmentator
- Audited source tag: `v2.14.0`
- README notes:
  - Works on Ubuntu, Mac, Windows; CPU and GPU.
  - Input can be NIfTI or a folder/zip with all DICOM slices of one patient.
  - M-series Mac can use `--device mps` for speedup.
  - It is not a medical device and not intended for clinical usage.
  - Openly available Apache-2.0 subtasks include dental-relevant tasks such as `craniofacial_structures` and `teeth`.
- Pinned source audit:
  - `craniofacial_structures` maps to task ID 115.
  - `teeth` maps to task ID 113.
  - both appear before the upstream `Commercial models` section and do not call
    the upstream license gate.
  - robust crop uses open 3 mm `total` helper ID 297; ID 298 is the 6 mm
    non-robust helper and is not predownloaded by this app.

## SlicerTotalSegmentator

- GitHub file: https://github.com/lassoan/SlicerTotalSegmentator/blob/main/TotalSegmentator/TotalSegmentator.py
- Notes:
  - The extension currently has `ENABLE_MPS = False`.
  - The code comment says MPS is disabled because some convolution operators were unsupported and caused segmentation failure on Apple Silicon macOS.
  - This supports the strategy: run inference outside Slicer with a controlled modern PyTorch.

## SlicerDentalSegmentator

- GitHub: https://github.com/gaudot/SlicerDentalSegmentator
- README notes:
  - Outputs maxilla/upper skull, mandible, upper teeth, lower teeth, mandibular canal.
  - Segmentation results can be modified in Slicer Segment Editor.
  - MacOS GPU acceleration is unavailable due to ongoing Mac/PyTorch issue.
  - This explains why a Mac/MPS route is a meaningful difference from prior DentalSegmentator workflows.

## 3D Slicer

- Main site: https://www.slicer.org/
- Slicer remains useful context and an optional external viewer/editor, but the packaged preview path is the app/browser surface preview.

## Non-clinical scope

Use explicit disclaimers because TotalSegmentator itself states it is not a medical device and not intended for clinical usage. The preview should not be described as diagnosis, treatment planning, surgical planning, implant planning, airway assessment, or clinical decision support.
