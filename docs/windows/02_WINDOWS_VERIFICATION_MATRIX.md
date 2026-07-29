# Windows Verification Matrix

Date: 2026-07-30

Use `pass`, `fail`, or `unverified`. Do not convert fallback, mock, proxy,
cached, or macOS-only evidence into a Windows pass.

## Canonical primary path

```text
clean Windows 11 x64 standard-user machine
  -> signed per-user shell install
  -> verified app-private runtime activation
  -> bundled NIfTI sample
  -> cuda_required on an NVIDIA GPU
  -> TotalSegmentator craniofacial_structures
  -> verified case output
  -> offline HTML viewer in the default browser
```

## Technical-spike criteria

| Criterion | Required evidence | Status |
| --- | --- | --- |
| Windows binary dependency closure | exact versions, hashes, no source builds | unverified |
| Clean runtime import | import matrix on clean Windows | unverified |
| Strict CUDA doctor | structured result on selected GPU | unverified |
| Real CUDA application run | run manifest proves requested/resolved CUDA | unverified |
| Negative CUDA test | hidden/invalid GPU fails without CPU run | unverified |
| Explicit CPU path | separate user-authorized CPU result | unverified: real TotalSegmentator CPU inference not run |
| Coordinator protocol v1 | request/event/error contract tests | partial: Mac contract and fake-backend integration harness pass; real backend and Windows transport unverified |
| Existing CLI compatibility | current macOS CLI tests remain green | partial: Mac automated suite passes; Windows unverified |
| Job Object containment | real child/grandchild membership evidence | unverified |
| Authoritative cancellation | no surviving inference/GPU processes | unverified |
| Staging commit | incomplete output never promoted | partial: Mac required-artifact negative tests pass; real backend and Windows interruption/race injection unverified |
| C++ helper MSVC build | reproducible configure/build log | unverified |
| DICOM synthetic parity | Windows and macOS semantic comparison | unverified |
| Japanese/long paths | runtime, coordinator and DICOM cases | unverified |
| WPF representative UI | keyboard, UIA, high contrast, DPI | unverified |
| Standard-user install | clean-machine install without elevation | unverified |
| Offline second launch | no network after completed setup | unverified |
| Setup recovery | interruption/fault matrix | unverified |
| Uninstall boundary | shell removed, user output preserved | unverified |
| License inventory | zero unresolved Windows artifacts | unverified |
| Network/privacy observation | only approved setup/update/model endpoints | unverified |

## Closed-alpha gate

All of these must pass:

```text
- technical-spike primary path
- strict CUDA and negative fallback tests
- cancellation at model-load and inference phases
- NIfTI, STL, report, log and viewer artifact checks
- at least two qualified NVIDIA hardware classes
- installer install/upgrade/uninstall
- no telemetry or remote viewer assets
- non-clinical language checks
```

Alpha exclusions must be visible in product copy:

```text
DICOM
DentalSegmentator
ToothSeg
secondary-capture rescue
AMD
ARM64
machine-wide deployment
```

## Beta gate

In addition to alpha:

```text
- clean DICOM audit, selection and conversion
- C++/GDCM/dcm2niix parity
- cancellation and timeout during DICOM processing
- DentalSegmentator strict CUDA path when its dependency gate passes
- expanded driver, VRAM and hybrid-laptop matrix
- runtime/model rollback
- redacted support bundle
```

## Feature-parity gate

In addition to beta:

```text
- ToothSeg strict CUDA path
- removal or explicit redesign of all MPS-specific patches
- secondary-capture rescue and image interaction
- all recovery paths
- output and sample parity
- upgrade/rollback across supported previous Windows releases
```

## Hardware matrix template

| Dimension | Spike | Beta/release |
| --- | --- | --- |
| OS | current Windows 11 x64 | supported current/previous Windows 11 |
| GPU | 12 GB engineering baseline | intended oldest architecture plus newer classes |
| VRAM | attempt 8 GB; negative 6 GB | separate minimum per backend if needed |
| Topology | one GPU | no GPU, disabled, multi-GPU, nonzero index |
| Driver | current | candidate minimum, current, too-old negative |
| Input | bundled sample | small, median, maximum supported envelope |
| Repetition | three strict runs | cold/warm and cancellation phases |

Do not publish the lower bound until all included workflows pass repeatedly
without OOM, silent fallback, or unnamed output-altering substitutions.

## Semantic output comparison

Compare:

```text
NIfTI shape, dtype, labels, affine and orientation
STL vertex/face count, bounds and orientation
report schema and required fields
case directory layout
viewer asset inventory
absence of remote viewer dependencies
```

Document numerical tolerances. Bitwise neural-network equality is not required
unless an existing product contract explicitly requires it.

## Security and privacy checks

```text
- model/runtime manifests are signed and hashed
- unsafe archive entries are rejected
- DLL lookup does not depend on writable current directories
- arbitrary user checkpoints are not loaded
- DICOM scans are bounded by count, size and time
- support bundles redact paths and DICOM identifiers
- viewer text is escaped and local-only
- update metadata prevents unintended downgrade/revoked versions
- setup and runtime activation are single-instance
- uninstall never deletes user-selected case output
```
