# Windows / macOS UI parity closeout verification

Date: 2026-07-31

Host: Windows 10 build 19045 x64 (engineering evidence)

Branch: `agent/windows-mac-parity-closeout`

Base: `7444012b1491d0649a5d9d57f0f513366375defe`

## Implemented

- DICOM Secondary Capture rescue now binds spacing and orientation
  (`axis_permutation`, quarter-turn rotation, slice-order reversal) to a
  confirmation token.
- Rescue preview remains pre-inference. The NIfTI is finalized only after
  token, transform, spacing, affine, voxel payload, source hash, and output
  hash readback pass. Only then is it handed to the existing strict-CUDA
  coordinator operation.
- The input screen now provides a persisted output-root selector, standard
  TotalSegmentator-only higher-order resampling, and an input/output details
  expander.
- The result screen now lists verified artifact-manifest entries using only
  relative paths and sizes. It also exposes app-private Slicer export and
  offline-preview rebuild operations without rerunning inference.
- Additional-model comparison cards show current app-private runtime readiness;
  they do not download or resolve dependencies.
- The placeholder folder glyph was replaced with a vector path.

## Checks

| Check | Result |
|---|---|
| .NET `CoordinatorShell` Release build | PASS, 0 warnings / 0 errors |
| .NET `ProcessSupervisor` Release build | PASS, 0 warnings / 0 errors |
| WPF contract self-test | PASS, 40 buttons, automation names/focus/dynamic labels PASS |
| Focused rescue/Slicer/WPF tests | PASS, 33 tests |
| Full Python suite | PASS, 300 tests; 3 skipped |
| `git diff --check` | PASS |
| Fresh fixed UI rendering | PASS: input, DICOM rescue, result |
| Real TotalSegmentator sample through WPF + Job supervisor | PASS |
| Strict CUDA contract | PASS: requested `cuda_required`, index 0, resolved `cuda:0`, no fallback |
| Terminal/exit contract | PASS: one `operation_completed`, supervisor exit code 0 |
| Promotion/artifact/offline preview | PASS |
| Non-empty NIfTI masks | PASS: 8 of 8 non-empty |
| Offline-only preview | PASS: non-empty, no remote URL |

The three Python skips were two native C++/Python integration cases without
`DICOM_NORMALIZER_BINARY` and one optional Node browser-decoder harness. The
Python rescue pipeline itself and the WPF rescue contract passed. No native
DICOM normalizer source changed in this branch.

## Measured runtime

- GPU: NVIDIA GeForce RTX 2060
- Driver: 572.83
- VRAM: 6,442,123,264 bytes
- Compute capability: 7.5
- Python: 3.12.10 x64
- PyTorch: 2.11.0+cu126
- CUDA build: 12.6
- Task: `craniofacial_structures`
- Requested / resolved device: `cuda:0` / `cuda:0`
- Fallback allowed / occurred: false / false

## Evidence

- `input.png`
- `dicom-rescue.png`
- `result.png`
- `wpf-real-result.png`
- `ui-contract.json`
- `strict-cuda-sample.json`
- `run-manifest.json`
- `artifact-manifest.json`

No patient data, usernames, secrets, raw third-party output, or unnecessary
absolute paths are stored in this evidence directory.

## Unverified

- Windows 11
- Real patient DICOM (intentionally not used)
- WPF, Job Object, DICOM/MSVC, installer, signing, update, and rollback as a
  complete product
- External UI Automation traversal, high contrast, and non-96-DPI captures
- Interactive Slicer export and preview rebuild against this real case (the
  underlying Python Slicer export tests passed)
