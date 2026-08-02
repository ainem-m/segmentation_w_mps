# 04 Implementation Plan

## Current status as of 2026-06-11

```text
[x] Phase 0 repository skeleton imported from handoff pack.
[x] Phase 1 MPS ConvTranspose3D FP32 gate passed outside Codex sandbox.
[x] Phase 2 craniofacial_structures MPS smoke passed on DZ-CBCT with non-empty masks.
[x] Phase 3 backend runner implemented.
[x] Phase 4 output report and offline surface preview path implemented; Slicer handoff retired.
[~] Phase 5 benchmark command implemented, but CPU is effectively too slow for the current preview path.
[x] Phase 6 GUI path migrated to SwiftUI shell; Tk prototype has been removed.
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

## Phase 4: Output report and preview handoff

Implement:

```text
backend/output_report.py
surface_preview.py
```

Generate:

```text
out/logs/mask_stats.json
out/README_OUTPUT.md
out/surface_preview/index.html
```

Acceptance:

```text
python -m totalsegmentator_wrapper_mac surface-preview --case out
```

Must load:

```text
- source geometry in generated summary metadata
- segmentation masks if present
- offline 3D preview in the browser
```

Slicer handoff generation was removed from the active app path after the
browser-based surface preview became the default visual inspection route.

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

Only after CLI and output report generation work.

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
[ ] 3D preview can be opened
```

### Error diagnostics completion (0.4.1)

The user-copyable report and the local engineering diagnostics now preserve a
shared attempt ID without copying raw logs or local paths into the support
form payload.

Completed:

```text
[x] Add a correlation ID (`job_id` or `request_id`) to the user-copyable report
    and every related backend log/metadata record.
[x] Record the failed stage (setup, input preparation, model loading,
    preprocessing, inference, postprocessing, or 3D preview generation).
[x] Preserve a specific machine-readable cause code beneath the top-level
    `backend_failed` category (for example MPS OOM, subprocess exit,
    timeout, missing model, or invalid backend output).
[x] Record whether retry is safe and provide a cause-specific recovery hint.
[x] Retain the original exception type, sanitized message, subprocess return
    code, and stderr tail in engineering diagnostics.
[x] Include the backend/model/runtime versions and non-identifying input
    characteristics needed to reproduce the failure.
[x] Keep PHI, secrets, full local paths, and raw stack traces out of the
    user-copyable report; write detailed diagnostics only to the local log.
[x] Add a regression test that reproduces a backend failure and proves the
    same primary path emits the correlation ID, stage, specific cause, and
    diagnostic log reference without silently falling back.
```

Acceptance:

```text
[x] A copied error report identifies where and why the operation failed, tells
    the user whether/how to retry, and can be correlated with a detailed local
    diagnostic record.
[x] An unexpected backend failure remains distinguishable from known causes
    instead of being mislabeled with a guessed cause.
[x] Existing safe-report redaction tests continue to pass.
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
