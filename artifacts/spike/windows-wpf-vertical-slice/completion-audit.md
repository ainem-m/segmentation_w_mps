# Windows WPF coordinator shell completion audit

Date: 2026-07-30

## Blocking determination

**Blocking FAIL: none.**

Two read-only reviewers audited the WPF host and ProcessSupervisor paths.
Their initial findings were fixed and independently rechecked:

- a coordinator that emits a terminal event and then lingers is bounded by
  the configured grace, cleaned up through its Job, and recorded as FAIL;
- `operation_cancelled` is accepted as a successful stop only when the
  supervisor also exits 0.

The complete-linger regression exited in 687 ms with supervisor exit 1,
`root_exit_timed_out=true`, Job termination recorded, valid JSONL stdout, and
zero survivors. The real strict-CUDA cancellation path was rerun once after
these changes and passed. The final binaries also reran the real strict-CUDA
normal path and the hidden-GPU expected-failure path; both passed their
respective criteria.

## PASS

- dedicated branch/worktree and exact starting commit
- Windows 10 x64 engineering-host Release builds for both .NET projects
- image-manual-aligned setup, start, input, running, success, and failure UI
- canonical non-clinical and Sample wording
- four-stage navigation, progress, typed failure, stop, and result states
- one-window WPF shell without a service layer or generic process framework
- production coordinator launched only through the existing Job supervisor
- suspended creation and Job assignment before resume
- protocol v1 JSONL, monotonic sequence, and exactly one terminal event
- bundled Sample 1 with real TotalSegmentator 2.14.0
  `craniofacial_structures`
- `cuda_required(0)` resolved to `cuda:0`
- fallback allowed/occurred remained false/false
- normal coordinator OS exit 0, final promotion, and zero Job survivors
- hidden-GPU `cuda_unavailable` typed failure without CPU fallback or final
  promotion
- real WPF Stop-method cancellation after production segment progress
- cancellation terminal count 1, coordinator exit 3, no final promotion, and
  zero Job survivors
- terminal-after-linger bounded cleanup and FAIL evidence
- nonzero supervisor exit cannot produce a completed or stopped-success UI
- artifact manifest 14/14 size and SHA-256 verification
- NIfTI 7/7 loadable and 6/7 nonempty
- offline preview with 15 local files and no HTTP(S) references
- evidence privacy scan: no user name, credential, unnecessary absolute path, raw
  third-party output, or stdout/stderr tail
- transient request deletion after normal, failure, and cancellation paths
- `send_usage_stats=false` and required cached-model gate
- existing private runtime/cache reused without dependency resolution or
  public package-index access
- full Python suite: 275 passed, 3 skipped
- macOS single-JSON stdout, `RUN_STAGE`, and `RUN_PROGRESS` regression
- .NET Release builds with zero warnings/errors
- WPF contract self-test
- `git diff --check`
- documentation limits the result to a Windows 10 engineering slice
- excluded scope and minimal implementation boundary preserved

## FAIL

None.

## UNVERIFIED

- Windows 11 and a clean standard-user machine
- self-contained publish because the private offline runtime pack is absent
- formal installation of the current application wheel into the private
  Python runtime
- per-user install, uninstall, signing, update, and rollback
- external UI Automation and actual keyboard traversal
- actual high contrast, DPI values other than 96, and minimum viewport
- native OpenFileDialog interaction
- repeated stress of an exactly simultaneous terminal/cancel tie
- crash or power-loss transient-request cleanup
- WPF integration with DICOM/MSVC and future DICOM image interaction
- DentalSegmentator, ToothSeg, explicit CPU inference, AMD, and ARM64
- legacy stage-trigger cancellation rerun with the final binary; its branch
  was not changed and timer/interactive cancellation regressions passed

## Final decision

**Windows 10 WPF coordinator engineering slice: PASS.**

This does not mean that the Windows product is complete.
