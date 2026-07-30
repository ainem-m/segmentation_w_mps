# Windows Process-Tree Ownership Spike

Date: 2026-07-30

## Outcome

The Windows 10 engineering host passed the bounded process-tree ownership
spike. Windows 11 remains UNVERIFIED and is assigned to a separate machine.
This is not a Windows product completion claim.

## Implemented scope

- Package-free .NET 10 console supervisor, pinned by `global.json`.
- Kill-on-close Job Object creation.
- Suspended coordinator creation, assignment before resume, and pipe transport.
- Coordinator JSONL forwarding without host records on stdout.
- Versioned, operation-bound `cancel` control after the initial request.
- Typed `operation_cancelled` terminal event and exit code 3.
- Bounded backend termination and authoritative Job termination fallback.
- Backend stdin isolation from coordinator control messages.
- Staging retention with no final-directory promotion.

WPF, Job Object integration into a WPF shell, DICOM/MSVC, installer,
update/rollback, and Windows 11 are not part of this result.

## Results

| Check | Result |
| --- | --- |
| .NET Release build | PASS, 0 warnings, 0 errors |
| Synthetic parent/child/grandchild | PASS, 3 members before cancel, 0 after `TerminateJobObject` |
| Model-load cancellation, real TotalSegmentator | PASS, 4 Job members, typed cancellation, 0 survivors |
| `Predicting` cancellation, real TotalSegmentator | PASS, 4 Job members, typed cancellation, 0 survivors |
| GPU PID association | PASS, PIDs 20920 and 17072 were both NVIDIA processes and Job members |
| GPU PID post-cancel check | PASS, both observed PIDs absent from NVIDIA and OS process lists |
| Final output promotion | PASS, neither cancelled run created its final directory |
| Staging behavior | PASS, both cancelled runs retained staging |
| Focused coordinator/runner tests | PASS, 50 tests |
| Full Python suite | PASS, 269 tests, 3 skipped |
| Existing stdout/RUN_STAGE/RUN_PROGRESS contracts | PASS in the automated regression suite |

The real inference used the bundled NIfTI sample, TotalSegmentator 2.14.0,
PyTorch 2.11.0+cu126, CUDA 12.6, device `cuda:0`, and the NVIDIA 572.83
driver. No fake or mock backend was used for the primary cancellation runs.

## Important findings

Starting a blocking text-mode stdin reader before the CUDA preflight stopped
PyTorch import on this Windows pipe configuration. The control reader is now
started only after CUDA preflight and delayed production imports. A control
already written by the host remains buffered and is then consumed.

Allowing the backend child to inherit the same stdin pipe also blocked the
TotalSegmentator child while the coordinator reader owned a pending read.
The backend is non-interactive, so its stdin is now `DEVNULL`; the coordinator
alone owns control messages.

The real runs exited during the graceful window, so their evidence records
`terminate_job_called: false`. The synthetic tree intentionally left
descendants alive and proves the `TerminateJobObject` escalation path.

## Remaining work

- Windows 11 x64 repetition.
- WPF integration and user-driven cancellation.
- DICOM/MSVC, installer, signing, update, and rollback.
- Fault/race injection beyond the bounded cancellation cases above.
