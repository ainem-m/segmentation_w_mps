# 03 MVP Spec: Mac Preview

## Product name

```text
TotalSegmentator Wrapper for Mac
```

## MVP objective

Demonstrate that an Apple Silicon Mac can run dental-relevant segmentation locally using PyTorch MPS and TotalSegmentator, then show the result in an offline HTML 3D preview.

## Supported platform

```text
OS: macOS 14+ initially
Architecture: arm64 / Apple Silicon only
GPU: Apple M-series via PyTorch MPS
```

Do not support Intel Mac in MVP.

## Input

Required:

```text
- NIfTI: .nii / .nii.gz
```

Optional best-effort:

```text
- DICOM folder, only if passed through to TotalSegmentator directly
```

Not supported in MVP:

```text
- PACS
- DICOMweb
- DICOM SEG
- RTSTRUCT
- DICOM database
- arbitrary dental scanner exports
```

## Tasks

MVP tasks:

```text
1. craniofacial_structures
2. teeth
```

Task order:

```text
First: craniofacial_structures
Second: teeth
```

Reason:

```text
craniofacial_structures is simpler and likely to demo faster.
teeth is more impressive but may be heavier.
```

## Device selection

UI options:

```text
- Auto
- MPS
- CPU
```

Behavior:

```text
Auto:
  run ConvTranspose3D smoke test
  if pass: use MPS
  else: CPU

MPS:
  fail loudly if MPS smoke test fails

CPU:
  always run CPU
```

## UI scope

Minimum UI:

```text
- input file picker
- output folder picker
- task selector
- device selector
- Run button
- progress/log view
- benchmark summary
- Open output folder button
- Open 3D preview button
```

Do not implement:

```text
- segmentation editor
- DICOM browser
- model zoo
- account system
- license activation
```

A command-line preview is acceptable before app UI.

## Output

Required:

```text
- raw TotalSegmentator output folder
- benchmark.json
- environment.json
- run.log
- mask_stats.json
- README_OUTPUT.md
- surface_preview/index.html
```

Optional:

```text
- STL exports
- merged dental5 labels
- preview PNG
```

## Benchmark output

`benchmark.json` must include:

```json
{
  "app_version": "0.1.0-preview",
  "timestamp": "ISO-8601",
  "machine": {
    "platform": "macOS",
    "arch": "arm64",
    "chip": "Apple M...",
    "memory_gb": 16
  },
  "python": {
    "version": "3.x",
    "executable": "..."
  },
  "torch": {
    "version": "...",
    "mps_built": true,
    "mps_available": true
  },
  "input": {
    "path_hash": "...",
    "format": "nifti",
    "dimensions": [0, 0, 0],
    "spacing": [0.0, 0.0, 0.0]
  },
  "run": {
    "task": "craniofacial_structures",
    "requested_device": "mps",
    "actual_device": "mps",
    "elapsed_seconds": 0.0,
    "status": "success"
  }
}
```

No patient identifiers.

## Demo success criteria

The demo is successful if it can produce:

```text
- visible segmentation in offline HTML 3D preview
- device=mps proof
- CPU vs MPS elapsed time comparison
- short video showing the workflow
```

## Public disclaimers

Use this wording:

```text
This is a non-clinical research/education preview. It is not a medical device and is not intended for diagnosis, treatment planning, surgical planning, or autonomous clinical decision-making. Outputs must be treated as preliminary segmentation model outputs and manually reviewed.
```
