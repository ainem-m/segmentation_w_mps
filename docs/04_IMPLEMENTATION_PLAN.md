# 04 Implementation Plan

## Current status as of 2026-06-11

```text
[x] Phase 0 repository skeleton imported from handoff pack.
[x] Phase 1 MPS ConvTranspose3D FP32 gate passed outside Codex sandbox.
[x] Phase 2 craniofacial_structures MPS smoke passed on DZ-CBCT with non-empty masks.
[x] Phase 3 backend runner implemented.
[x] Phase 4 Slicer handoff files generated; Slicer visual validation deferred.
[~] Phase 5 benchmark command implemented, but CPU is effectively too slow for the current preview path.
[x] Phase 6 minimal Tk UI wrapper implemented; manual desktop validation still required.
[ ] Phase 7 demo materials not started.
```

## Phase 0: Repository skeleton

Deliverables:

```text
[ ] README
[ ] backend package skeleton
[ ] scripts folder
[ ] templates folder
[ ] sample config
[ ] smoke test scripts
```

Acceptance:

```text
[ ] `python scripts/smoke_test_mps_convtranspose3d.py` can run independently.
[ ] repository documents non-clinical scope.
```

## Phase 1: MPS operator verification

Tasks:

```text
[ ] Create environment on Apple Silicon Mac.
[ ] Install candidate PyTorch version.
[ ] Run ConvTranspose3D FP32 smoke test.
[ ] Record result in docs or logs.
```

Acceptance:

```text
[ ] MPS available = True.
[ ] ConvTranspose3D forward pass succeeds on MPS in FP32.
[ ] FP16/BF16 are not used.
```

Stop condition:

```text
If ConvTranspose3D FP32 fails, stop app work and resolve PyTorch version first.
```

## Phase 2: TotalSegmentator CLI smoke test

Tasks:

```text
[ ] Install TotalSegmentator.
[ ] Prepare a NIfTI sample.
[ ] Run craniofacial_structures on CPU.
[ ] Run craniofacial_structures on MPS.
[ ] Run teeth on CPU if feasible.
[ ] Run teeth on MPS if feasible.
[ ] Save logs.
```

Acceptance:

```text
[ ] craniofacial_structures succeeds on MPS.
[ ] elapsed_seconds recorded.
[ ] if teeth fails, failure reason is documented.
```

## Phase 3: Backend runner

Implement:

```text
backend/device.py
backend/runner_totalseg.py
backend/benchmark.py
backend/outputs.py
```

Features:

```text
[ ] device check
[ ] smoke test API
[ ] TotalSegmentator subprocess runner
[ ] benchmark log generation
[ ] stdout/stderr capture
[ ] no PHI in logs
```

Acceptance:

```text
python -m totalsegmentator_wrapper_mac run \
  --input sample.nii.gz \
  --task craniofacial_structures \
  --device mps \
  --output out/
```

Generates:

```text
out/logs/benchmark.json
out/logs/environment.json
out/logs/run.log
out/segmentations/raw_totalseg/
```

## Phase 4: Slicer handoff

Implement:

```text
backend/slicer_export.py
```

Generate:

```text
out/slicer/open_in_slicer.py
out/slicer/label_names.json
out/slicer/label_colors.json
```

Acceptance:

```text
Slicer --python-script out/slicer/open_in_slicer.py
```

Must load:

```text
- source NIfTI volume
- segmentation labelmaps if present
- segment display colors/names if possible
```

If conversion to Slicer Segmentation node is not robust in v0.1, load labelmaps and document manual conversion steps.

## Phase 5: CPU vs MPS benchmark command

Implement:

```text
totalsegmentator_wrapper_mac benchmark \
  --input sample.nii.gz \
  --task craniofacial_structures \
  --output bench_out/
```

Behavior:

```text
[ ] run CPU once
[ ] run MPS once
[ ] save both logs
[ ] produce summary markdown/table
```

Acceptance:

```text
bench_out/benchmark_summary.md
bench_out/benchmark_summary.json
```

## Phase 6: Mac Preview UI

Only after CLI and Slicer handoff work.

Minimum UI options:

```text
- CLI wrapped in a minimal app
- SwiftUI shell calling backend subprocess
- PySide6 app
```

Choose the fastest path that produces a runnable Apple Silicon preview.

UI acceptance:

```text
[ ] user selects NIfTI
[ ] user selects task
[ ] user chooses MPS or CPU
[ ] run starts
[ ] progress/log visible
[ ] benchmark visible
[ ] output folder opens
[ ] Slicer script can be run/opened
```

## Phase 7: Demo materials

Deliverables:

```text
[ ] 30–60 sec screen recording
[ ] benchmark table
[ ] README launch instructions
[ ] known limitations
[ ] non-clinical disclaimer
```

## Explicit deferrals

Do not implement in these phases:

```text
- DICOM normalizer
- dcm2niix bundling
- C++
- ONNX/Core ML
- DentalSegmentator nnU-Net
- 3D preview
- Slicer extension
- App Store distribution
```
