# 15 DZ-CBCT Sample Validation

Date: 2026-06-11

## Summary

The 3D Slicer SampleData `CBCT-MR Head` sample provides a usable public CBCT
head volume for the next TotalSegmentator MPS validation gate.

Source:

```text
SampleData name: CBCTMRHead
File used: DZ-CBCT.nrrd
SHA256: 4ce7aa75278b5a7b757ed0c8d7a6b3caccfc3e2973b020532456dbc8f3def7db
URL: https://github.com/Slicer/SlicerTestingData/releases/download/SHA256/4ce7aa75278b5a7b757ed0c8d7a6b3caccfc3e2973b020532456dbc8f3def7db
```

Slicer SampleData notes state that `MRHead`, `CBCT-MR Head`, and `CT-MR Brain`
were donated to the 3D Slicer project for unrestricted use. Treat this as
non-clinical validation/demo material, not as patient or product evidence.

## Conversion

The source NRRD was converted to NIfTI with SimpleITK:

```bash
.venv/bin/python -c "import SimpleITK as sitk; img=sitk.ReadImage('artifacts/samples/DZ-CBCT.nrrd'); sitk.WriteImage(img, 'artifacts/samples/DZ-CBCT.nii.gz')"
```

Input geometry:

```text
Shape: 667 x 667 x 433
Spacing: 0.25 x 0.25 x 0.25 mm
Pixel type: 16-bit signed integer
NIfTI size: 151 MB
```

## MPS Run

Command:

```bash
env TOTALSEG_HOME_DIR=artifacts/totalseg_home \
    TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights \
    .venv/bin/TotalSegmentator \
      -i artifacts/samples/DZ-CBCT.nii.gz \
      -o artifacts/totalseg_smoke/dz_cbct_craniofacial_mps/output \
      -ta craniofacial_structures \
      --device mps
```

Result:

```text
Exit code: 0
Device: MPS
Task: craniofacial_structures
Torch: 2.12.0
TotalSegmentator: 2.14.0
```

Important timings from `run.log`:

```text
Rough crop resampling: 8.89 s
Rough crop prediction: 12.59 s
Crop: (667, 667, 433) -> (653, 628, 433)
Main resampling: 13.25 s
Main prediction: 213.43 s
```

The full wall time was not captured by the current ad hoc command. Future
benchmark runs should use the benchmark runner so wall time is recorded
consistently.

## Output Mask Check

Nonzero voxel counts:

```text
head.nii.gz          86,742,694
mandible.nii.gz       4,629,868
sinus_frontal.nii.gz          0
sinus_maxillary.nii.gz 3,586,520
skull.nii.gz          8,589,396
teeth_lower.nii.gz      644,816
teeth_upper.nii.gz      730,186
```

Interpretation:

```text
The DZ-CBCT sample passes the non-empty-output gate for head, mandible, skull,
maxillary sinus, and upper/lower teeth labels. Frontal sinus is empty on this
sample and should not be treated as a failure by itself.
```

## Preview Artifact

A case folder was prepared at:

```text
artifacts/cases/dz_cbct_craniofacial/
```

Layout:

```text
input/source.nii.gz
segmentations/raw_totalseg/*.nii.gz
surface_preview/index.html
logs/run.log
logs/mask_stats.json
```

Open the generated preview with:

```bash
open artifacts/cases/dz_cbct_craniofacial/surface_preview/index.html
```

The preview is non-diagnostic and intended only for quick orientation and output
inspection.

## Decision

This sample is now the preferred local validation sample for the next gate.

Project decision: use the successful MPS run plus non-empty output masks and
offline surface preview as the backend runner gate.

```text
[x] MPS exit code 0
[x] head/mandible/skull/teeth labels are non-empty
[x] offline preview path is available
```

Next steps:

```text
1. Implement the backend/CLI runner.
2. Use the runner MPS result as the Phase 3 gate for this sample.
3. Defer CPU-vs-MPS benchmarking until a smaller representative input is selected.
4. Keep deeper visual QA as a later validation step, not a blocker for Phase 3.
```
