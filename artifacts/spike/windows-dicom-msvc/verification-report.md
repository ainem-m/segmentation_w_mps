# Windows DICOM/MSVC Spike Verification

Date: 2026-07-30

## Outcome

The Windows 10 engineering spike passes for the existing native DICOM helper.
It is not a Windows release or a cross-platform parity claim.

The first official GDCM Windows DLL candidate was rejected because it uses the
VS2013 runtime and exposes a C++ ABI to the v143 consumer. The final binary
instead statically links the exact GDCM 3.2.6 libraries and codecs from the
approved binary wheel. `dumpbin /dependents` confirms that no GDCM or VS2013
runtime DLL remains.

## Done criteria

| Criterion | Result | Evidence |
| --- | --- | --- |
| Exact binary-only GDCM closure | PASS | `dependency-manifest.json`, `binary-dependencies.txt` |
| MSVC x64 Release configure/build | PASS | `toolchain.txt`, `build-result.txt` |
| Existing classification/rescue semantics | PASS | `synthetic-tests.txt` |
| No-shell Windows child process | PASS | `CreateProcessW`, explicit handle list, native tests |
| UTF-16 spaces/Japanese paths | PASS | `synthetic-tests.txt` |
| Greater-than-260-character paths | PASS | fake and real dcm2niix conversion |
| Read-only and malformed inputs | PASS | source integrity and typed classification assertions |
| JPEG/JPEG-LS/JPEG 2000/RLE | PASS | real GDCM codec corpus |
| Bounded timeout | PASS | return code 124, no NIfTI, grandchild marker absent |
| Inner Job descendant cleanup | PASS | active process count reaches zero before return |
| Outer supervisor cancellation | PASS | `job-cancellation-evidence.json` |
| Real dcm2niix conversion | PASS | `real-dcm2niix-conversion.json` |
| Python regression suite | PASS | 269 tests, 3 skipped |
| .NET supervisor Release build | PASS | 0 warnings, 0 errors |
| Cross-host macOS semantic comparison | UNVERIFIED | No macOS host used in this spike |
| Windows 11 | UNVERIFIED | Separate machine required |
| Clean-machine packaging | UNVERIFIED | Out of scope |
| License/redistribution approval | UNVERIFIED | Separate approval gate |

## Contract preservation

- The non-Windows dcm2niix execution path remains unchanged.
- The Windows-only timeout option is not advertised or accepted on macOS.
- Existing Python tests covering the macOS stdout single-JSON, `RUN_STAGE`, and
  `RUN_PROGRESS` contracts pass.
- No patient data, fake primary-path success, public package resolution, or
  source-built dependency was used.

## Out of scope

WPF, DICOM UI, installer, signing, update/rollback, and Windows 11 remain
unverified.
