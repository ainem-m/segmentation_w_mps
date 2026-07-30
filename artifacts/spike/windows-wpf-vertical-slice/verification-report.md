# Windows WPF coordinator shell verification

Date: 2026-07-30

## Outcome

**Windows 10 engineering result: PASS.**

This is a representative WPF coordinator-shell slice, not a completed Windows
product. Windows 11, clean installation, self-contained distribution,
installer/signing, update/rollback, DICOM UI, and additional models remain
UNVERIFIED.

The implementation follows the existing Japanese image manual. It adds one
WPF window and a coordinator-specific process client. The WPF shell launches
the production coordinator only through the existing Job Object supervisor.
No fake backend, mock inference, CPU fallback, dependency upgrade, public
package-index access, service layer, embedded browser, or generic process
framework was used for the primary path.

## Primary strict-CUDA completion

Operation `e80c674a-c401-4dce-9c52-75759766f302` used the bundled non-clinical
NIfTI Sample 1 and real TotalSegmentator `craniofacial_structures`.

| Criterion | Result |
| --- | --- |
| WPF to existing Job Object supervisor | PASS |
| Coordinator created suspended, assigned before resume | PASS |
| Protocol v1 stdout | PASS, 23 valid JSONL events |
| Terminal | PASS, one `operation_completed` |
| Coordinator OS exit | PASS, 0 |
| Requested policy/index | PASS, `cuda_required`, `0` |
| Resolved device | PASS, `cuda:0` |
| Fallback allowed/occurred | PASS, false/false |
| Real backend | PASS, TotalSegmentator 2.14.0 |
| Final promotion | PASS |
| Job active processes after completion | PASS, 0 |
| Artifact manifest | PASS, 14/14 size and SHA-256 |
| NIfTI masks | PASS, 7 loadable and 6 nonempty |
| Offline preview | PASS, 15 local files and no HTTP(S) reference |
| Transient request retention | PASS, deleted after run |

Measured runtime: CPython 3.12.10, PyTorch 2.11.0+cu126, CUDA build
12.6, NVIDIA GeForce RTX 2060 6144 MiB, compute capability 7.5, and NVIDIA
driver 572.83.

## Strict failure and cancellation

With `CUDA_VISIBLE_DEVICES=-1`, operation
`86ef4b3f-47f5-4b8c-aae0-eef93d8f141d` emitted one `operation_failed` with
`cuda_unavailable`. Requested policy/index stayed `cuda_required`/`0`,
resolved device was null, fallback remained false/false, application exit was
1, and no final output was promoted.

Real cancellation operation `b77ddd14-1689-465a-9147-de5eed06ab1b`
waited for production segment progress, then used the same stop-request method
as the WPF `停止` button. It emitted one `operation_cancelled`, coordinator
exit was 3, final output was not promoted, the Job cleanup left zero
survivors, and the transient request was deleted. Interrupted staging remains
available, matching the existing coordinator contract.

This real cancellation was rerun once after the final interactive-supervisor
audit fixes. A synthetic coordinator that emitted a terminal event and then
lingered was also terminated within the configured grace and produced FAIL
evidence with zero survivors, rather than hanging or reporting success.

## UI and regression verification

- Manual-aligned setup/start/input/running/success/failure images: PASS.
- Canonical non-clinical and Sample wording: PASS.
- Internal automation names and focusable controls: PASS.
- Dynamic system colors, PerMonitorV2 and long-path declarations: PASS.
- Coordinator/macOS/WPF focused tests: PASS, 68.
- Full Python suite: PASS, 275 tests with 3 skips.
- Existing macOS single-JSON stdout and `RUN_STAGE`/`RUN_PROGRESS`: PASS.
- .NET Release builds: PASS, zero warnings/errors.
- Synthetic normal/cancel stdout: valid JSONL and equal to `events.jsonl`.

## Packaging boundary and unverified work

The private .NET SDK did not contain the offline win-x64 runtime pack required
for `dotnet publish --self-contained true`; the attempt stopped with
NETSDK1112. No public source was contacted. The WPF engineering run therefore
used the existing app-private .NET 10 host. A current application wheel/source
must be placed into the final app-private Python runtime; the evidence reused
the exact dependency runtime and overlaid the current branch source without
dependency resolution.

The following remain UNVERIFIED:

- Windows 11 and a clean standard-user machine
- self-contained publish, per-user install, uninstall and signing
- external UI Automation and actual keyboard traversal
- actual high contrast, DPI values other than 96, and minimum viewport
- native OpenFileDialog interaction
- future DICOM image interaction and DICOM/MSVC integration from the WPF shell
- installer, update and rollback
- DentalSegmentator, ToothSeg, CPU-authorized inference, AMD and ARM64
