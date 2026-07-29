# Windows runtime/CUDA spike verification report

Date: 2026-07-30

## Outcome

**FORMAL RESULT: FAIL (host mismatch).**

The strict runtime/CUDA vertical slice passed on the available x64 host, but
that host is Windows 10 IoT Enterprise 22H2 build 19045.6456, not Windows 11.
The evidence therefore cannot be promoted to a Windows 11 runtime/CUDA spike
pass.

No fake executable, mock inference, CPU fallback, patient data, silent model
change, or reduced inference configuration was used for the primary path.

## Runtime/CUDA vertical slice observed on this host

| Criterion | Result | Evidence |
| --- | --- | --- |
| Starting commit `899811c0...` | PASS | `baseline.txt` |
| Dedicated branch/worktree | PASS | `baseline.txt` |
| Windows 11 x64 host | **FAIL** | `baseline.txt`, `toolchain.txt` |
| Binary-only wheel closure | PASS | `dependency-resolution.txt` |
| Exact hashed lock, 91 wheels | PASS | `requirements-win-x64-hashed.txt`, `wheelhouse-sha256.txt` |
| Reproducible internal `acvl-utils` wheel | PASS | `dependency-resolution.txt` |
| Offline install and `pip check` | PASS | `runtime-manifest.json` |
| Production import matrix | PASS | `runtime-manifest.json` |
| Strict CUDA tensor doctor | PASS | `device-doctor-cuda.json` |
| Hidden-GPU typed failure/no backend | PASS | `device-doctor-no-visible-gpu.json` |
| Invalid-index typed failure/no backend | PASS | `device-doctor-invalid-index.json` |
| Real TotalSegmentator CUDA inference | PASS | `run-manifest.json`, `events.jsonl` |
| Requested/resolved/fallback proof | PASS | `run-manifest.json` |
| Artifact SHA-256 verification | PASS, 14/14 | `artifact-manifest.json` |
| Offline preview | PASS | `artifact-manifest.json` |
| Loadable NIfTI masks | PASS, 7/7 | `artifact-manifest.json` |
| Nonempty NIfTI masks | PASS, 6/7 | mask-statistics verification |
| Coordinator/runner/macOS profile regression | PASS, 44 tests | test record below |
| Full Python suite | **FAIL**, 259 run | test record below |
| `git diff --check` | PASS | final check |

## Exact measured runtime

- CPython 3.12.10 x64
- PyTorch 2.11.0+cu126
- PyTorch CUDA build 12.6
- torchvision 0.26.0+cu126
- TotalSegmentator 2.14.0
- nnunetv2 2.8.1
- NVIDIA GeForce RTX 2060, 6,442,123,264 bytes
- compute capability 7.5
- NVIDIA driver 572.83
- `nvidia-smi` maximum supported CUDA reported as 12.8

The strict smoke created a tensor on `cuda:0`, then ran FP32 Conv3d,
InstanceNorm3d, ReLU, ConvTranspose3d, synchronize, and finite-output checks.
All stages passed.

## Real inference

- Operation ID: `c841ecf0-2beb-4dda-a47d-83554b790cc7`
- Bundled non-clinical sample: `owner_cbct_jawcrop_0p5mm.nii.gz`
- Task: `craniofacial_structures`
- Requested policy/index: `cuda_required`, `0`
- Resolved device: `cuda:0`
- Upstream TotalSegmentator argument: `gpu:0`
- Fallback allowed/occurred: `false` / `false`
- Return status: success
- Elapsed time: 44.805 seconds after weights were cached
- Masks: 7 loadable, 6 nonempty
- Offline preview: generated synchronously before staging promotion

## Negative tests

With `CUDA_VISIBLE_DEVICES=-1`, the coordinator emitted
`cuda_unavailable`, exit code 2, `resolved_device: null`, and no output
directory. With requested index 1 while only one GPU was visible, it emitted
`cuda_device_index_unavailable` with the same no-fallback/no-output behavior.

## Test record

Focused coordinator, TotalSegmentator runner, and macOS application-profile
tests:

```text
Ran 44 tests
OK
```

The full Python discovery run did not pass:

```text
Ran 259 tests
FAILED (failures=9, errors=8, skipped=3)
```

The 17 failures are outside the implemented CUDA vertical slice: Windows
cannot directly launch POSIX-shebang fake executables used by unported
DentalSegmentator, ToothSeg, and DICOM-normalizer tests, and six bundled
text-asset hashes differ after system Git's `core.autocrlf=true` checkout.
Those failures were not hidden or changed because DICOM/MSVC and the other
model paths are outside this spike.

## Deferred and unverified

Job Object cancellation, WPF, DICOM/MSVC, DentalSegmentator/ToothSeg Windows
execution, installer, clean-machine setup, update, and rollback remain
UNVERIFIED.

## Required next decision

Repeat the exact hashed offline install, strict doctor, negative tests, and
real inference on an actual Windows 11 x64 machine. Separately decide whether
the next Windows phase should make the entire legacy Python suite portable
(Windows fake-process launch and line-ending-stable asset hashing) before
starting WPF or installer work.
