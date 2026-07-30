# ADR: Windows Port Architecture

Date: 2026-07-30

Status: provisional; accepted for technical-spike implementation only

## Context

Users have requested a Windows build of the existing macOS application. The
processing code should be reused, but the current Swift layer is not a purely
visual shell:

```text
Swift sources: approximately 8,961 lines
Python backend: approximately 13,051 lines
C++ DICOM helper: approximately 4,000 lines
```

`AppState.swift` currently owns setup, model preparation, DICOM workflow
decisions, run orchestration, progress interpretation, recovery, and updates in
addition to navigation. A method-for-method C# port would duplicate product
workflow logic and preserve the largest platform boundary.

The existing Python CLI is nevertheless a useful compatibility surface:

```text
- Swift invokes Python with argv lists, never shell command strings.
- `run` prints one final JSON document to stdout.
- normalized `RUN_STAGE` and `RUN_PROGRESS` records are written to run.log and
  mirrored to stderr.
- safe result JSON and case artifacts are written to explicit paths.
```

Those contracts must remain valid for the released macOS application while a
new coordinator surface is introduced.

## Decision

### Windows shell

Use C# with WPF on self-contained .NET 10 LTS for the technical spike.

The decision is provisional until a representative WPF vertical slice proves:

```text
- Japanese text and layout
- keyboard navigation and UI Automation
- high contrast and DPI scaling
- NIfTI file selection
- progress, log, typed failure, cancellation, and result views
- the image interaction needed by the future DICOM rescue screen
```

WinUI 3 remains a viable alternative. Reconsider WPF only if a required
Windows App SDK feature or a hard Fluent-design requirement appears.

### Responsibility boundary

The Windows shell owns only Windows-specific or pre-Python responsibilities:

```text
- windows, views, view models, accessibility, and file dialogs
- bootstrapping and recovering the private runtime
- generic artifact download, verification, extraction, and activation
- Job Object creation and process-tree supervision
- shell update, rollback UI, and default-browser launch
- per-user settings, locks, and redacted support-bundle creation
```

The Python application coordinator owns domain workflow:

```text
- protocol and capability negotiation
- device policy and doctor results
- model prerequisites and preparation semantics
- NIfTI/DICOM workflow transitions
- backend sequencing and progress normalization
- typed product errors and recovery decisions
- case layout, reports, STL, and offline viewer generation
```

The coordinator is a finite process for one operation. It is not a background
service or daemon.

### Compatibility boundary

Add a versioned coordinator protocol without changing the current CLI contract.

```text
existing CLI:
  stdout = one final JSON document
  progress = run.log + stderr

coordinator protocol v1:
  request = one JSON object over stdin
  stdout = JSON Lines protocol events only
  stderr = diagnostics only
```

The initial coordinator slice supported only:

```text
- capabilities
- NIfTI input
- TotalSegmentator
- craniofacial_structures
- explicit required device policy
```

Protocol v1 now also exposes the fixed
`run_nifti_dentalsegmentator` operation. It remains NIfTI-only and
`craniofacial_structures`-only, accepts no backend/task/model selector, and
requires the host-provided app-private model gate. Windows 10 engineering
evidence proves strict CUDA completion without fallback; Windows 11 and model
distribution remain unverified.

Protocol v1 also exposes the fixed `run_nifti_individual_teeth` beta
operation. It remains NIfTI-only, fixes TotalSegmentator task `teeth` plus the
existing 5 mm robust craniofacial preflight, accepts no task/model/split
selector, and requires the app-private `Dataset113_ToothFairy3` gate. Windows
10 engineering evidence proves strict CUDA completion without fallback;
Windows 11 and clean model distribution remain unverified.

Compatibility adapters remain until both macOS and Windows have independently
validated the coordinator surface.

### Process lifetime

On Windows, launch the coordinator suspended, assign it to a Windows Job
Object, then resume it. Use graceful protocol cancellation first and
`TerminateJobObject` as the authoritative escalation.

Do not use `taskkill`, PID-recursive enumeration, Unix-signal emulation, or log
parsing as the primary process-tree mechanism.

This remains unverified until real TotalSegmentator, nnU-Net, and dcm2niix
descendants have been observed on Windows.

### Device policy

The production-facing policy must be explicit:

```text
cuda_required(index)
cpu_required
```

`diagnostic_auto` may be used by diagnostics only. A CUDA-required run must
either prove CUDA execution or fail. It must never continue on CPU.

Memory-saving profiles such as lower resolution, ROI subsets, or altered patch
sizes are separately named modes because they may change output behavior.

Initial hardware scope:

```text
OS: Windows 11 x64
GPU: NVIDIA CUDA
Engineering baseline: 12 GB VRAM
CPU: explicit degraded path
```

Do not publish a minimum VRAM or driver version until measured with the exact
runtime, models, and representative inputs.

Deferred:

```text
AMD / DirectML / ROCm
Windows ARM64
Windows 10
automatic low-memory substitution
```

### Python runtime

Build a preassembled, immutable, app-private Windows runtime in Windows CI.
The customer machine must not resolve arbitrary dependency versions or build
the scientific stack from source.

The exact CPython distribution and whether CPU and CUDA share one runtime are
spike decisions. Required properties are:

```text
- exact versions and artifact hashes
- binary-only dependency closure
- runtime manifest, SBOM, and license inventory
- no user Python, Conda, compiler, or administrator-right requirement
- side-by-side installation and atomic activation
- previous known-good runtime retained for rollback
```

### DICOM

Preserve the C++/GDCM helper as an isolated executable and keep its
machine-readable process/JSON contract. Port the process platform layer to
Windows instead of rewriting the DICOM classification logic.

The Windows process implementation is expected to use `CreateProcessW`,
explicit inherited handles, UTF-16 paths, bounded waits, and no shell.

This decision is conditional on the MSVC build and semantic parity tests.

### Installer and update

Evaluate a signed per-user Inno Setup installer first. Compare it with MSIX
using the same vertical slice before making the final distribution ADR.

The initial update policy is:

```text
shell:
  download and launch a complete signed installer after the app exits

runtime/models:
  verified side-by-side artifact activation with rollback
```

Do not copy the macOS behavior that replaces a writable running app bundle.
Do not add a privileged updater or background service.

### Output commit and privacy

Every operation writes to an operation-specific staging directory. Only a
verified successful output is promoted into the final case directory.

Paths and patient-derived metadata must not appear in:

```text
- command lines where the coordinator protocol can avoid them
- normal logs
- crash reports
- update metadata
- viewer titles or remote requests
```

The offline viewer keeps local assets only. TotalSegmentator usage statistics
remain disabled in the app-private configuration.

## Phased scope

### Technical spike

Prove runtime closure, strict CUDA execution, Job Object cancellation,
coordinator protocol, WPF vertical slice, DICOM build feasibility, and a
standard-user clean-machine setup.

### Closed alpha

```text
- Windows 11 x64
- NIfTI and bundled sample
- TotalSegmentator only
- strict CUDA plus explicit CPU
- progress, cancellation, logs, typed errors
- offline viewer
- signed per-user installer
```

### Beta

Add clean DICOM audit/conversion, series selection, DentalSegmentator and
Individual Teeth when dependency gates pass, expanded NVIDIA qualification,
and update/rollback flows.

### Feature parity

The fixed ToothSeg strict-CUDA slice has Windows 10 engineering evidence.
Feature parity still requires clean Windows 11/model distribution evidence,
secondary-capture rescue, remaining recovery paths, and the documented
hardware/support matrix.

## Estimate

The current planning ranges are provisional:

| Milestone | Person-days | One engineer | Two complementary engineers |
| --- | ---: | ---: | ---: |
| Closed alpha | 85–115 | 4–6 months | 10–15 weeks |
| Beta with clean DICOM | 140–190 | 7–10 months | 18–24 weeks |
| macOS feature parity | 180–250 | 9–13 months | 27–34 weeks |

Re-estimate after the technical spike. A maintained private PyTorch build,
multiple incompatible inference runtimes, or a semantic DICOM rewrite moves
the work toward the pessimistic range.

## Consequences

Positive:

```text
- Python algorithms and output formats stay shared.
- the Windows shell can recover even when inference Python is broken.
- device and fallback claims become explicit and testable.
- Windows process-tree ownership has an authoritative mechanism.
- runtime/model rollback is independent of the shell installer.
```

Costs:

```text
- a new protocol and coordinator must coexist with the current CLI.
- some Swift-owned workflow decisions must move into Python incrementally.
- C#, Python, and C++ remain in the product.
- Windows runtime, driver, installer, and hardware matrices require dedicated
  verification.
```

## Decision gates

Revisit this ADR if:

```text
- binary-only Windows dependency closure fails
- a real strict CUDA application run cannot be proven
- critical descendants escape the Job Object
- the DICOM helper needs a semantic rewrite
- WPF cannot meet the representative accessibility or image-interaction needs
- licensing blocks the chosen runtime/model distribution
```
