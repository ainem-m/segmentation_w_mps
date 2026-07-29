# GPT Pro Windows Port Consultation

Date: 2026-07-30

Status: external advice; locally evaluated, not authoritative

## Consultation question

An external GPT Pro review was requested to challenge the Windows technology
selection, platform boundary, distribution strategy, phased scope, and effort
estimate. The consultant received a minimized repository summary rather than
repository access or full source files.

No credentials, patient data, private URLs, signing material, or raw runtime
logs were included.

## Consultant recommendation

The consultant recommended:

| Area | Recommendation |
| --- | --- |
| UI | C# + WPF on self-contained .NET 10 LTS, MVVM |
| Backend | versioned Python application coordinator, finite subprocess |
| Process lifetime | Windows Job Object |
| IPC | request/control via stdin, JSON Lines events via stdout |
| GPU | NVIDIA CUDA first; explicit separate CPU mode |
| Runtime | preassembled immutable private Python runtime |
| DICOM | port the existing C++/GDCM platform layer and preserve the contract |
| Installer | signed per-user Inno Setup initially |
| Updates | complete shell installer plus side-by-side runtime/model rollback |
| Viewer | continue opening offline HTML in the default browser |
| Initial OS | Windows 11 x64 |

The consultant explicitly advised against porting the current Swift
`AppState` method-for-method. Domain workflow and recovery decisions should
move into Python while the native shell retains Windows-specific lifecycle,
accessibility, bootstrap, process supervision, and update responsibilities.

## Proposed protocol direction

The consultant proposed:

```text
request:
  one versioned JSON object over stdin

stdout:
  versioned JSON Lines events only

stderr:
  diagnostics only

event types:
  operation_started
  phase_started
  progress
  warning
  device_resolved
  user_action_required
  artifact_created
  heartbeat
  operation_completed
  operation_failed
  operation_cancelled
```

Every event should contain a protocol version, opaque operation id, and
monotonically increasing sequence.

## Runtime and update direction

The consultant recommended constructing the scientific runtime in Windows CI
from exact binary artifacts. The target machine should download and verify one
complete artifact rather than resolve dependencies from package indexes.

Activation should:

```text
download partial -> verify size/hash/signature -> safe extract -> health check
-> atomic current pointer -> retain previous known-good runtime
```

The shell updater should download a complete signed installer and run it only
after the application exits. Runtime and model updates remain independently
versioned and reversible.

## CUDA direction

The consultant recommended replacing production `auto` behavior with:

```text
cuda_required(index)
cpu_required
```

A strict doctor should run representative FP32 3D operations and a small real
application inference. The run manifest must record requested and resolved
devices separately. CUDA OOM must fail the operation and offer separately
named alternatives; it must not silently lower resolution or start CPU.

The consultant proposed 12 GB VRAM as the engineering baseline and advised
qualifying 8 GB only through representative repeated runs. No public driver or
VRAM floor should be chosen before measurement.

## DICOM direction

The consultant recommended retaining the native executable boundary because
the current C++ and synthetic test harness encode accumulated behavior. The
Windows-specific child-process layer should use Windows process APIs, explicit
handles, UTF-16 paths, absolute executables, and no command shell.

Parity should be semantic rather than byte-for-byte:

```text
audit classification
series grouping
warnings and errors
NIfTI shape, dtype, affine, orientation and voxels
```

## Proposed phases and estimate

The consultant proposed:

| Milestone | Optimistic | Likely | Pessimistic |
| --- | ---: | ---: | ---: |
| Technical spike | 10 | 14 | 18 |
| Closed alpha | 75 | 105 | 150 |
| Beta with clean DICOM | 105 | 160 | 235 |
| Feature parity | 134 | 213 | 328 |

The likely calendar estimate was 11–13 months for one engineer or 27–32 weeks
for two complementary engineers at feature parity.

## Local evaluation

The consultation was evaluated against the repository and relevant existing
tests.

### Adopted direction

```text
- do not port AppState method-for-method
- move platform-neutral domain workflow toward a Python coordinator
- preserve the isolated C++ DICOM executable
- use a Windows Job Object for authoritative process-tree ownership
- use strict CUDA-required and explicit CPU-required paths
- build a preassembled private runtime rather than resolving on the user PC
- use side-by-side runtime/model activation and staging output commits
- defer AMD, ARM64, Windows 10 and embedded 3D rendering
```

### Requires local Windows verification

```text
- WPF rather than WinUI 3
- exact Python distribution and wheel closure
- one CPU/CUDA runtime versus separate artifacts
- Inno Setup rather than MSIX
- minimum VRAM and driver
- MSVC/GDCM/dcm2niix packaging
- Job Object behavior of all third-party descendants
- the person-day estimate
```

### Rejected or corrected

Do not replace the current CLI stdout contract. The repository has a test that
requires `run` stdout to remain one JSON document while stages stream elsewhere.
The JSON Lines protocol must therefore be a new coordinator entry point or
compatibility adapter.

Do not perform a big-bang workflow extraction. Migrate vertical slices:

```text
1. NIfTI + TotalSegmentator
2. clean DICOM
3. DentalSegmentator
4. recovery
5. secondary-capture rescue
6. ToothSeg
```

The consultant's ten-day plan is too dense for one engineer. Use two engineers
for the same calendar time or allow one engineer three to four weeks.

## Existing controls that the Windows track must preserve

The repository already verifies:

```text
- TotalSegmentator usage statistics are disabled
- the surface viewer contains no remote HTTP/HTTPS assets
- update links are HTTPS/host constrained
- run commands are sanitized before logging
- DICOM helper timeout/failure results are structured
- license inventory fails closed on unresolved packages
```

These controls reduce, but do not eliminate, the Windows-specific security and
distribution work.

## Additional risks retained from consultation

```text
- model/checkpoint files are executable-content risks
- PHI may leak through command lines, filenames, browser history or crash dumps
- unsafe archive entries can escape extraction roots
- DLL search order can load writable-directory binaries
- malformed DICOM can cause resource exhaustion or native decoder failures
- Windows multiprocessing uses spawn semantics
- side-by-side rollback multiplies disk requirements
- multiple app instances can race setup or activation
- antivirus and SmartScreen can quarantine or lock scientific runtimes
- sleep, power loss and GPU reset can interrupt activation or inference
- remote update selection needs downgrade and revocation protection
```

## Canonical disposition

The architecture decision and completion gates are maintained in:

```text
docs/windows/00_WINDOWS_PORT_ADR.md
docs/windows/01_WINDOWS_TECHNICAL_SPIKE.md
docs/windows/02_WINDOWS_VERIFICATION_MATRIX.md
```

This consultation record is evidence for those decisions, not an instruction
to implement unverified Windows behavior.
