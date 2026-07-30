# Read-Only Completion Audit

Date: 2026-07-30

Conclusion: PASS for the Windows 10 process-tree and Job Object engineering
spike. There are no blocking FAIL results. This is not a Windows 11 or Windows
product completion claim.

| Criterion | Result | Evidence |
| --- | --- | --- |
| Dedicated branch and bounded scope | PASS | `agent/windows-job-object-spike`; no WPF or packaging implementation |
| Exact package-free .NET toolchain | PASS | SDK 10.0.302 pinned; roll-forward disabled; no external package reference |
| Kill-on-close Job Object | PASS | Job limit configured by the native supervisor |
| Suspended create, assign, then resume | PASS | Root process cannot run before Job assignment |
| Graceful cancellation and bounded escalation | PASS | Real runs exited gracefully; synthetic run exercised `TerminateJobObject` |
| Synthetic three-level process tree | PASS | 3 members before cancellation and 0 after escalation |
| Real model-load cancellation | PASS | 4 Job members, typed cancellation, 0 survivors |
| Real inference cancellation | PASS | `Predicting` observed, 4 Job members, typed cancellation, 0 survivors |
| GPU process ownership and disappearance | PASS | Both observed GPU PIDs were Job members and absent after cancellation |
| Typed terminal and exit code 3 | PASS | Real terminal event observed; return code implemented and unit-tested |
| Staging retained and final not promoted | PASS | Both real cancellation runs retained staging and created no final output |
| JSONL stdout and legacy macOS contracts | PASS | Full regression suite passed |
| Backend stdin isolation | PASS | Backend uses `DEVNULL`; regression test passed |
| Evidence privacy and redaction | PASS | No username, absolute path, secret, patient data, or raw third-party tail |
| Build, tests, and diff checks | PASS | Release build clean; focused 50 and full 269 tests passed |
| Windows 11 x64 | UNVERIFIED | Assigned to a separate machine |
| WPF and WPF Job integration | UNVERIFIED | Outside this spike |
| DICOM/MSVC, installer, signing, update, rollback | UNVERIFIED | Outside this spike |

Evidence limitation: the real-run supervisor evidence does not record the
coordinator OS exit code directly. Exit code 3 is supported by implementation
and unit-test evidence; the real runs directly prove the typed terminal event.
