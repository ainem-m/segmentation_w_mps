# 13 MPS Operator Verification Notes

Date: 2026-06-11

## Summary

Phase 1 gate passed when executed outside the Codex filesystem sandbox:

```text
Python: 3.12.8
Environment manager: uv .venv
PyTorch: 2.12.0
Platform: macOS 26.5 arm64
MPS built: true
MPS available: true
ConvTranspose3D FP32: pass
Output device: mps:0
Output dtype: torch.float32
```

This means the first technical gate for the Mac Preview is open: PyTorch can run
the relevant ConvTranspose3D FP32 operator on Apple Silicon MPS in this
environment.

## Commands Used

Create the environment:

```bash
env UV_CACHE_DIR=.uv-cache uv venv --python 3.12 .venv
env UV_CACHE_DIR=.uv-cache uv pip install --python .venv/bin/python torch
```

Run the smoke test:

```bash
.venv/bin/python scripts/smoke_test_mps_convtranspose3d.py \
  --json artifacts/mps_smoke/convtranspose3d_python312_torch212_unsandboxed.json
```

The `artifacts/` directory is intentionally gitignored. This note records the
decision-relevant result in tracked documentation.

## Important Finding: Sandbox Visibility

The same smoke test failed inside the Codex sandbox:

```text
MPS built: true
MPS available: false
ConvTranspose3D FP32: not_run
```

It passed outside the sandbox:

```text
MPS built: true
MPS available: true
ConvTranspose3D FP32: pass
Output device: mps:0
Elapsed seconds: 0.2171735829906538
```

Interpretation:

```text
The PyTorch/Python combination is viable. The sandboxed execution environment
can hide MPS availability, so future GPU/MPS validation commands should be run
with explicit unsandboxed approval when using Codex.
```

## Decision

Proceed to Phase 2 planning/execution:

```text
TotalSegmentator craniofacial_structures CPU smoke test
TotalSegmentator craniofacial_structures MPS smoke test
TotalSegmentator teeth MPS attempt, if feasible
```

Do not begin backend, UI, packaging, or demo polish until the TotalSegmentator
MPS path is verified on a sample NIfTI.

## Constraints Preserved

```text
FP32 only
No autocast
No fp16
No bf16
No silent CPU fallback when MPS is requested
```
