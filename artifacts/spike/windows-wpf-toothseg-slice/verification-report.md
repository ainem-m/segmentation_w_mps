# Windows WPF ToothSeg slice verification

Date: 2026-07-30

Result: **PASS on the Windows 10 engineering host.** This is not a Windows
release or Windows 11 pass.

## Primary run

- WPF -> .NET Job Object supervisor -> production coordinator -> real
  TotalSegmentator craniofacial preflight -> real ToothSeg completed.
- Operation: `run_nifti_toothseg`; backend/task: `toothseg` / `teeth`.
- Policy/index/resolution: `cuda_required`, `0`, `cuda:0`.
- CPU fallback: disallowed and did not occur.
- Dataset121 semantic and Dataset123 instance used fold 5 with TTA disabled.
- Standalone dental ROI margin was 5 mm; semantic/instance spacing remained
  0.3/0.2 mm. No model, resolution, ROI, or class substitution occurred.
- Operation elapsed time was 200.025 seconds.
- Final FDI NIfTI shape was 209 x 209 x 161 with 168,971 nonzero voxels and
  23 nonzero FDI labels.
- Offline preview contains no external HTTP(S) asset.

## Host and artifacts

- Supervisor created the coordinator suspended, assigned it before resume,
  observed real descendants, recorded coordinator OS exit code 0, and ended
  with active process count 0 and no survivors.
- Exactly one terminal event was emitted: `operation_completed`.
- Artifact verification completed before staging promotion.
- The artifact manifest contains the final FDI NIfTI and its label sidecar,
  report/log/environment/mask stats, run manifest, and offline preview.
- All 48 stdout lines decode as UTF-8 JSON objects. No event contains an
  absolute path, raw third-party output, or stdout/stderr tail.

## Negative and regression checks

- GPU hidden: typed `cuda_unavailable`, no resolved device, no fallback,
  no artifact manifest, no final promotion, no Job survivors.
- Invalid device index: coordinator protocol/unit test PASS.
- Strict CUDA doctor: tensor creation, Conv3d, normalization, activation,
  ConvTranspose3d, synchronize, and finite output all PASS.
- Python tests: 300 PASS, 3 skipped.
- `pip check`: PASS.
- Production import matrix: PASS.
- ProcessSupervisor Release build: PASS, 0 warnings/errors.
- CoordinatorShell Release build: PASS, 0 warnings/errors.
- WPF UI contract: PASS, 24 buttons; external UI Automation UNVERIFIED.
- `git diff --check`: PASS.
- Existing macOS CLI/RUN_STAGE/RUN_PROGRESS contract tests: PASS in the full
  Python suite.

## Remaining unverified scope

Windows 11, clean model/runtime distribution, self-contained installation,
signing, update/rollback, repeated-run qualification, other GPUs/VRAM,
external UI Automation, high contrast, non-96-DPI interaction, DICOM rescue,
AMD, and ARM64 remain UNVERIFIED.
