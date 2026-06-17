# 02 Architecture

## Architecture summary

Use a thin Mac-facing shell with an embedded or managed Python backend. The backend runs TotalSegmentator outside Slicer using PyTorch MPS. Outputs are saved to a case folder and opened in Slicer via generated Python script.

```text
NIfTI input
  ↓
Mac Preview UI / CLI
  ↓
Python backend
  ├─ torch MPS smoke test
  ├─ TotalSegmentator runner
  ├─ benchmark logger
  ├─ label/output organizer
  └─ Slicer handoff script generator
  ↓
output folder
  ├─ source volume
  ├─ segmentation outputs
  ├─ STL / mesh outputs if available
  ├─ benchmark.json
  └─ open_in_slicer.py
  ↓
3D Slicer
  ├─ volume review
  ├─ segmentation review/correction
  └─ STL export
```

## Main modules

```text
app/
  UI or CLI layer

backend/
  device.py              MPS/CPU availability and smoke tests
  runner_totalseg.py     TotalSegmentator invocation
  benchmark.py           timing and environment logging
  slicer_export.py       open_in_slicer.py generation
  outputs.py             case folder layout
  disclaimers.py         non-clinical notices
```

## Why outside Slicer

Slicer is excellent for review and correction, but inference inside Slicer inherits Slicer’s Python, extension, architecture, and PyTorch constraints. The preview’s differentiation is that it can use a modern, controlled PyTorch/MPS environment outside Slicer.

Slicer is used as:

```text
- viewer
- segmentation editor
- STL exporter
```

Slicer is not used as:

```text
- inference runtime
- package manager
- Python environment
- MPS controller
```

## Input policy

MVP primary input:

```text
- .nii
- .nii.gz
```

Best-effort optional input:

```text
- DICOM folder only if TotalSegmentator handles it directly
```

Deferred:

```text
- DICOM normalizer
- series picker
- DICOM database
- DICOMweb
- PACS
```

## Device policy

Device modes:

```text
mps       Apple Silicon GPU/MPS
cpu       CPU fallback
auto      prefer MPS if smoke tests pass; otherwise CPU
```

Do not silently claim MPS if the model fails or falls back. Log device used.

## Precision policy

Use FP32 for MPS.

Rationale:

```text
ConvTranspose3D support is specifically relevant for FP32. Half precision paths may still be unsupported or unstable.
```

Do not enable autocast or mixed precision in MVP unless explicitly tested.

## Output folder layout

```text
case_output/
  input/
    source.nii.gz
  segmentations/
    raw_totalseg/
    dental5_merged.nii.gz           optional future output
  stl/
    *.stl                           optional, task dependent
  slicer/
    open_in_slicer.py
    label_names.json
    label_colors.json
  logs/
    benchmark.json
    environment.json
    run.log
  README_OUTPUT.md
```

## Process isolation

Prefer subprocess-based runners for TotalSegmentator.

Benefits:

```text
- clear logs
- less import-time coupling
- easier packaging
- simpler crash containment
- easier reproduction from terminal
```

## Future backend interface

Design the backend so later engines can be plugged in:

```text
class SegmentationBackend:
    id: str
    display_name: str
    supported_devices: list[str]
    def check(self) -> BackendStatus
    def run(input_path, output_dir, task, device) -> RunResult
```

Initial backend:

```text
TotalSegmentatorBackend
```

Future backends:

```text
DentalSegmentatorNNUNetBackend
DentalSegmentatorONNXBackend
```
