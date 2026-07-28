# 38 DentalSegmentator Backend Notes

Date: 2026-07-09

## Scope

DentalSegmentator is treated as an explicit experimental backend for
non-clinical research/education preview. It is not a fallback for
TotalSegmentator and it does not provide individual tooth labels.

## Upstream Findings

- Zenodo record: `10.5281/zenodo.10829675`
- Creator: Gauthier Dot (`0000-0003-2014-2623`)
- Zenodo model file: `Dataset112_DentalSegmentator_v100.zip`
- Zenodo file md5: `b71cd5230168d28a4f71b078265b76be`
- Model license: `cc-by-4.0`
- License URL: `https://creativecommons.org/licenses/by/4.0/`
- Slicer implementation: `gaudot/SlicerDentalSegmentator`
- Slicer extension license: Apache-2.0
- Runtime framework: nnU-Net v2
- PyPI package named `DentalSegmentator`: not found
- nnU-Net PyPI package: `nnunetv2`

DentalSegmentator labels:

```text
1 upper_skull
2 mandible
3 upper_teeth
4 lower_teeth
5 mandibular_canal
```

The Slicer extension downloads weights on first use. This repo now mirrors the
Mac preview setup flow used for TotalSegmentator: when network setup is
allowed, setup installs `nnunetv2` and prepares the Zenodo model under:

```text
~/Library/Application Support/TotalSegmentatorWrapperMac/models/dentalsegmentator/
```

The model setup step downloads `Dataset112_DentalSegmentator_v100.zip`,
verifies the Zenodo md5, runs `nnUNetv2_install_pretrained_model_from_zip`,
and writes `dentalsegmentator_model.json` with DOI/license/source metadata.
The downloaded checkpoint parameters are not modified. The application adds
independent MPS inference, conversion, progress, and macOS integration around
the checkpoint. The canonical attribution is
`resources/third_party/licenses/DentalSegmentator-NOTICE.txt`.

The runner can also use a manually supplied model folder:

```text
--dentalseg-model-dir <trained model folder>
```

or an explicitly supplied nnU-Net results folder:

```text
--dentalseg-nnunet-results <nnUNet_results>
```

For development, a local Zenodo model zip can still be installed before
prediction:

```text
--dentalseg-model-zip Dataset112_DentalSegmentator_v100.zip
```

## Command Shape

Installed model path:

```bash
totalsegmentator-wrapper-mac run \
  --backend dentalsegmentator \
  --input input.nii.gz \
  --output case \
  --task craniofacial_structures \
  --device mps \
  --dentalseg-disable-tta \
  --dentalseg-nnunet-results "$HOME/Library/Application Support/TotalSegmentatorWrapperMac/models/dentalsegmentator/nnUNet_results"
```

Direct model folder:

```bash
totalsegmentator-wrapper-mac run \
  --backend dentalsegmentator \
  --input input.nii.gz \
  --output case \
  --task craniofacial_structures \
  --device mps \
  --dentalseg-disable-tta \
  --dentalseg-model-dir /path/to/Dataset112_DentalSegmentator_v100/nnUNetTrainer__nnUNetPlans__3d_fullres
```

The runner creates nnU-Net inference input as:

```text
<case>/input/dentalsegmentator_nnunet/case_0000.nii.gz
```

and writes the normalized output labelmap as:

```text
<case>/segmentations/dentalsegmentator/dentalsegmentator_multilabel.nii.gz
<case>/segmentations/dentalsegmentator/dentalsegmentator_multilabel.nii.gz.labels.json
```

`surface-preview` now resolves this labelmap before falling back to
TotalSegmentator raw craniofacial masks.

## MPS Handling

nnU-Net v2 exposes `-device mps`, and its setup docs describe Apple `mps` as
available with possible CPU fallback for some 3D convolution operations. The
SlicerDentalSegmentator README currently says macOS GPU acceleration is not
available in that extension due to a PyTorch issue.

This repo removes `PYTORCH_ENABLE_MPS_FALLBACK` for DentalSegmentator runs.
If MPS cannot execute the selected model, the run should fail and record the
reason instead of silently falling back to CPU or TotalSegmentator.

The Mac app uses DentalSegmentator with MPS. The Python CLI can pass `cpu` to
nnU-Net for development, but CPU inference is not a performance target for this
preview and is not used as the release verification path.

## Verification Status

Implemented contract checks:

```text
tests.test_dentalsegmentator_backend
tests.test_dentalsegmentator_setup
tests.test_setup_manager
tests.test_surface_preview
tests.test_swiftui_navigation_coverage
```

Real verification completed on 2026-07-09:

```text
Zenodo model download: 229747861 bytes
Zenodo md5: b71cd5230168d28a4f71b078265b76be
Installed dataset folder: Dataset112_DentalSegmentator_v100
Runtime: nnunetv2 2.8.1, torch 2.12.0, nibabel 5.4.2
Input: resources/sample1/input/DZ-CBCT_jawcrop_0p5mm.nii.gz
Command device: -device mps
TTA: disabled
Recorded actual_device: mps
Fallback: none
Elapsed: 679.24 s
Output labelmap: segmentations/dentalsegmentator/dentalsegmentator_multilabel.nii.gz
Output labels: 1, 2, 3, 4, 5
```

App Support launch check completed on 2026-07-10:

```text
Runtime path: ~/Library/Application Support/TotalSegmentatorWrapperMac/env/bin/python
Model path: ~/Library/Application Support/TotalSegmentatorWrapperMac/models/dentalsegmentator/nnUNet_results
Input: bundled Sample 1 NIfTI from the launch_check app copy
Command device: -device mps
TTA: disabled
Recorded actual_device: mps
Fallback: none
Elapsed: 391.85 s
Output labels: 1, 2, 3, 4, 5
Surface preview: generated
```

The generated labelmap was accepted by `surface-preview`; preview grouping used
the DentalSegmentator sidecar labels for jaws, dental hard tissue, and the
combined non-zero group.

CPU note: a CPU prediction was started only as a development smoke check. It was
stopped because the user explicitly asked not to push CPU runtime. CPU inference
is therefore not claimed as a verified release path here.
