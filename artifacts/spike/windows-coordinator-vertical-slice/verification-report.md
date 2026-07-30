# Windows Coordinator Vertical Slice Verification

Date: 2026-07-30

## Outcome

The non-cancelled production coordinator vertical slice passed on the Windows
10 engineering host. Windows 11 remains UNVERIFIED. This is not a Windows
product completion claim.

The run used the bundled non-clinical NIfTI sample, the existing app-private
runtime and model cache, the real TotalSegmentator executable, and strict
`cuda:0`. No dependency was updated or resolved, and no fake backend, mock
inference, CPU fallback, or output-altering substitution was used.

## Host and runtime

- Windows 10 IoT Enterprise 22H2, build 19045.6456, x64
- CPython 3.12.10
- PyTorch 2.11.0+cu126; CUDA build 12.6
- torchvision 0.26.0+cu126
- TotalSegmentator 2.14.0
- nnU-Net v2 2.8.1
- acvl-utils 0.2.6
- NVIDIA GeForce RTX 2060, 6144 MiB, compute capability 7.5
- NVIDIA driver 572.83
- .NET SDK 10.0.302

## Primary-path results

| Criterion | Result |
| --- | --- |
| Starting commit `6f861610...` | PASS |
| Dedicated branch/worktree | PASS |
| Existing app-private runtime and cached model only | PASS |
| `pip check` | PASS |
| Create suspended, assign to Job, then resume | PASS |
| Production coordinator protocol v1 | PASS |
| Real TotalSegmentator `craniofacial_structures` | PASS |
| Requested policy/index | PASS, `cuda_required`, `0` |
| Resolved device and fallback | PASS, `cuda:0`, false/false |
| Terminal event | PASS, one `operation_completed` |
| Coordinator OS exit code | PASS, 0 |
| Job completion | PASS, 53 process IDs observed, 0 active after completion |
| Forced cleanup | PASS, not required |
| GPU process completion | PASS, 2 observed Job-member GPU PIDs, 0 survivors |
| Staging promotion | PASS, final present and staging absent |
| Artifact manifest | PASS, 14/14 entries matched size and SHA-256 |
| NIfTI masks | PASS, 7 loadable and 6 nonempty |
| Offline preview static check | PASS, 15 local files and no HTTP(S) reference |
| Offline preview actual open | PASS, network-disabled headless Edge reached the completed state |
| Supervisor stdout | PASS, 23 valid coordinator JSONL lines only |
| Stdout privacy fields | PASS, no absolute path, raw output, or stdout/stderr tail |

The supervisor-forwarded stdout was byte-for-byte equal to `events.jsonl`.
The host diagnostic stderr was empty. Raw coordinator and third-party output
was retained only in private scratch storage and is not checked in.

The operation ID was the random, non-PHI UUID
`cde5936e-e4a4-4508-b82f-c145d780d6ec`. The bundled sample was
`resources/sample1/input/owner_cbct_jawcrop_0p5mm.nii.gz`, marked
`clinical_use: false` in its repository manifest.

## Regression results

| Check | Result |
| --- | --- |
| .NET Release build | PASS, 0 warnings and 0 errors |
| Synthetic parent/child/grandchild cancellation | PASS, no survivors |
| Focused coordinator/runner tests | PASS, 50 tests |
| Full Python suite | PASS, 269 tests and 3 skipped |
| Existing typed cancellation and non-promotion tests | PASS in the focused/full suites |
| Existing macOS stdout/RUN_STAGE/RUN_PROGRESS contracts | PASS in the full suite |
| `git diff --check` | PASS |

The cancel-control and `TerminateJobObject` branch was not changed. The two
heavy real cancellation runs were therefore not repeated; synthetic
cancellation, unit tests, and the previously recorded real cancellation
evidence were checked instead.

## Deferred and unverified

- Windows 11 x64
- WPF and WPF-to-supervisor integration
- DICOM/MSVC
- Installer and signing
- Update and rollback
- Fault/race injection beyond the existing bounded cases
