# Coordinator Protocol v1

Date: 2026-07-30

This document describes the platform-neutral coordinator boundary implemented
on the Windows spike branch. Runtime, strict CUDA, normal completion, and
cancellation have engineering evidence on the Windows 10 spike host. The WPF
shell and its clean-DICOM-to-NIfTI intake integration also have Windows 10
engineering evidence. The fixed DentalSegmentator operation has Windows 10
strict-CUDA completion and hidden-GPU failure evidence. Windows 11, external UI
accessibility interaction, clean-machine/model distribution, DICOM rescue, and
the installer remain unverified.

## Entrypoint and transport

The coordinator has a separate entrypoint:

```text
totalsegmentator-wrapper-coordinator
```

It does not replace the existing macOS CLI. The coordinator:

- accepts no command-line arguments;
- reads one initial JSON request from standard input without waiting for EOF;
- accepts later newline-delimited control messages for a running operation;
- writes versioned JSON Lines events to standard output;
- reserves standard error for local diagnostics;
- flushes every event immediately.

On Windows, the initial JSON reader consumes the underlying anonymous pipe
without requiring the host to close stdin, while leaving later control lines
for the cancellation listener. This behavior has a regression test using an
open stdin pipe and real Windows supervisor evidence.

The Windows shell should create the process with inherited pipes and send
patient paths through standard input. The existing Python runner still passes
input paths to its backend child process. Removing those child command-line
paths is separate privacy work and remains unverified. The existing sanitized
run log also retains the input basename, so filenames must be treated as a
remaining PHI-leakage risk.

`operation_id` is present in stdout events and in the staging-directory name.
The shell must generate an opaque, non-PHI identifier such as a random UUID.
It must never derive this value from a patient name, DICOM identifier, source
filename, or case description.

## Supported operations

### Capabilities

```json
{
  "protocol_version": 1,
  "operation_id": "capabilities-1",
  "operation": "capabilities"
}
```

Capabilities report the current coordinator truth. `cpu_required`,
`cuda_required`, and graceful control are implemented. Authoritative Job Object
ownership remains a host responsibility, so the platform-neutral coordinator
continues to report that field as `unverified`.

### NIfTI TotalSegmentator run

```json
{
  "protocol_version": 1,
  "operation_id": "opaque-operation-id",
  "operation": "run_nifti_totalsegmentator",
  "input": {
    "kind": "nifti",
    "path": "/absolute/path/input.nii.gz"
  },
  "output_directory": "/absolute/path/final-case",
  "device_policy": {
    "mode": "cpu_required"
  },
  "options": {
    "robust_crop": true,
    "higher_order_resampling": false
  }
}
```

On Windows, paths use normal absolute Windows path strings such as
`C:\\Cases\\input.nii.gz`. Relative paths are rejected.

The operation surface is intentionally narrow:

- NIfTI input only;
- `craniofacial_structures` only;
- TotalSegmentator only;
- explicit `cpu_required` or `cuda_required`;
- synchronous detailed STL and offline-preview generation.

Requests cannot select an arbitrary backend, task, executable, environment, or
model path, and cannot bypass device verification. Unknown optional fields may
be ignored for forward compatibility, but a different protocol version fails
before data processing.

`cuda_required` performs strict per-operation validation of the requested
device index. A failed check emits a typed failure and never starts CPU
inference.

### NIfTI DentalSegmentator run

The fixed additional-model operation uses the same request envelope with:

```json
{
  "operation": "run_nifti_dentalsegmentator",
  "device_policy": {
    "mode": "cuda_required",
    "device_index": 0
  },
  "options": {
    "robust_crop": false,
    "higher_order_resampling": false
  }
}
```

It is limited to the DentalSegmentator `craniofacial_structures` five-label
path. The host supplies a verified app-private model root outside the request;
clients cannot select a model path, backend, task, fold, TTA mode, or CPU
fallback. The Windows WPF shell uses fold 0 with TTA disabled, matching the
existing product choice. A missing model emits `dentalseg_prepare_required`;
a hidden or invalid CUDA device fails before inference.

Protocol v1 remains NIfTI-only. The Windows WPF clean-DICOM path audits and
converts a selected clean series in a pre-coordinator adapter, verifies exactly
one nonempty NIfTI output, and invokes this operation only after the user starts
the run. DICOM operation names and DICOM input kinds are rejected by protocol
tests.

## Event envelope

Every stdout line is one JSON object containing:

```json
{
  "protocol_version": 1,
  "operation_id": "opaque-operation-id",
  "sequence": 1,
  "event": "operation_started"
}
```

`sequence` is monotonically increasing within an operation. Exactly one
terminal event is allowed:

- `operation_completed`
- `operation_failed`
- `operation_cancelled`

Implemented nonterminal events are:

- `operation_started`
- `capabilities`
- `phase_started`
- `progress`
- `device_resolved`
- `artifact_created`

Progress is obtained through a typed callback added beside the existing
`RUN_STAGE` and `RUN_PROGRESS` log protocol. The existing stderr and log output
remain unchanged for macOS compatibility. Raw third-party output, absolute
paths, and stdout/stderr tails are not copied into JSONL events.

## Device policy

`cpu_required` must resolve to CPU without a fallback reason.

`cuda_required` includes an optional zero-based `index`, defaulting to `0`.
The coordinator performs a strict CUDA tensor doctor before starting the
backend and passes the validated device check into the production runner. An
unavailable index, hidden GPU, mismatched runtime device, or fallback produces
a typed failure without starting a CPU run.

## Staging and commit

The coordinator writes an operation beside the requested final directory:

```text
<parent>/.tswm-<operation-id>.staging/
```

It verifies the segmentation result and synchronous offline preview before
renaming the staging directory to the requested final directory. A failed
operation leaves staging available for later cleanup or diagnosis and never
emits a successful terminal event.

The coordinator requires a nonempty report, run log, benchmark,
environment record, mask-statistics record, offline preview, and at least one
nonempty raw NIfTI mask. It writes `artifact-manifest.json` with relative
paths, sizes, and SHA-256 hashes before promotion. It also writes
`run-manifest.json`, keeping requested policy/index, resolved device, fallback
state, and CUDA runtime/device facts separate. Unit tests may use a fake
executable, but primary-path spike evidence must use the real installed
TotalSegmentator.

The current implementation rejects an existing final or staging directory.
Cross-process locking, Windows power-loss testing, disk-full testing, and
atomicity under concurrent creation remain Windows-spike work.

A real supervised Windows 10 run completed with one `operation_completed`
terminal event, coordinator OS exit code 0, zero remaining Job members, and a
verified staging-to-final promotion. The same result now passes from a
synthetic clean DICOM folder through WPF audit, real dcm2niix conversion, and
strict `cuda:0` TotalSegmentator execution. Windows 11 remains unverified.

## Cancellation

After the initial request, the host may send:

```json
{
  "protocol_version": 1,
  "operation_id": "opaque-operation-id",
  "control": "cancel"
}
```

The coordinator accepts only protocol version 1, the active operation ID, and
the `cancel` control. It stops starting new stages, terminates a running backend
with bounded waits, does not promote staging, emits one terminal
`operation_cancelled` event, and exits with code 3. Backend stdin is closed so
that it cannot consume coordinator control messages.

The Windows process supervisor creates the coordinator suspended, assigns it to
a kill-on-close Job Object, resumes it, sends graceful control first, and uses
`TerminateJobObject` when descendants remain. Synthetic parent/child/grandchild
and real TotalSegmentator model-load/`Predicting` runs passed with zero Job
survivors on the Windows 10 engineering host. NVIDIA PID polling also confirmed
that the observed inference GPU processes were Job members and absent after
cancellation.

The Windows 10 WPF shell now invokes this supervisor for real completion and
cancellation, while the platform-neutral coordinator correctly keeps Job
ownership outside its own capability claim. Windows 11 remains unverified.

## Compatibility rule

The original `python -m totalsegmentator_wrapper_mac` command continues to
write one JSON document on stdout. It must not be changed to JSONL. Windows
work should evolve this separate coordinator entrypoint and keep existing
macOS tests green.
