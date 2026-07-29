# Windows runtime/CUDA spike verification report

Date: 2026-07-30

## Outcome

**FAIL — stopped at the binary-only dependency closure gate.**

The requested Windows 11 x64 primary path was not executed. The available host
is Windows 10 IoT Enterprise 22H2 (build 19045.6456), so it cannot provide
Windows 11 evidence. Independently, the exact binary-only dependency resolver
failed because the required `acvl-utils==0.2.6` release publishes only an sdist.
No source build, mock backend, fake executable, or CPU fallback was used.

## Completion criteria

| Criterion | Result | Evidence |
| --- | --- | --- |
| Starting commit matches | PASS | `baseline.txt` |
| Dedicated branch/worktree | PASS | `baseline.txt` |
| Required documents read | PASS | `baseline.txt` |
| Existing command/dependency/sample/device survey | PASS | `dependency-resolution.txt` |
| Windows 11 x64 host | FAIL | `baseline.txt`, `toolchain.txt` |
| Python binary dependency closure | FAIL | `dependency-resolution.txt` |
| Exact wheel hashes | FAIL | `wheelhouse-sha256.txt` |
| Runtime installation | UNVERIFIED | `runtime-manifest.json` |
| `pip check` | UNVERIFIED | runtime not installed |
| Production import matrix | UNVERIFIED | runtime not installed |
| Strict CUDA tensor smoke | UNVERIFIED | `device-doctor-cuda.json` |
| Invalid index test | UNVERIFIED | runtime not installed |
| Hidden-GPU negative test | UNVERIFIED | `device-doctor-no-visible-gpu.json` |
| Real TotalSegmentator CUDA inference | UNVERIFIED | `run-manifest.json` |
| Requested/resolved device proof | UNVERIFIED | inference not started |
| Artifact manifest and offline preview | UNVERIFIED | no case output |
| Nonempty NIfTI mask | UNVERIFIED | no case output |
| Python tests | UNVERIFIED | dependency closure failed |
| Coordinator entrypoint/protocol tests | UNVERIFIED | dependency closure failed |
| Existing macOS contract regression | UNVERIFIED | dependency closure failed |
| `git diff --check` | PASS | no whitespace errors |

## Original blocker

`TotalSegmentator==2.14.0` requires `nnunetv2>=2.3.1`.
The compatible nnU-Net releases require `acvl-utils>=0.2.6,<0.3`.
PyPI metadata for 0.2.6 contains only:

```text
acvl_utils-0.2.6.tar.gz
sha256 d6bd68a916fb2451ab3dd640b2494e545edc204c839ae1d4dd49f88f89999b74
```

With `--only-binary=:all:`, pip reports `ResolutionImpossible`. This is a
dependency-artifact availability failure, not proof of an incompatible Python
or PyTorch ABI.

## Acquired artifacts

- Official CPython 3.12.10 Windows x64 installer, SHA-256 recorded in
  `toolchain.txt`.
- A private Python 3.12.10 candidate outside the repository.
- pip resolver metadata cache outside the repository.
- No completed wheels and no model weights.

## Decision required before resuming

Choose one reviewed distribution route:

1. obtain an upstream universal wheel for `acvl-utils==0.2.6`; or
2. authorize a reproducible, audited internal pure-Python wheel build and
   distribution, with exact source hash, build recipe, wheel hash, SBOM,
   licenses, and binary-only customer installation.

After that decision, rerun on an actual Windows 11 x64 host. The next gate is
closure installation, `pip check`, production imports, and strict CUDA tensor
smoke before any real TotalSegmentator inference.

## Intentionally unverified

Job Object cancellation, WPF, DICOM/MSVC, installer, update, and rollback remain
UNVERIFIED and were not started.
