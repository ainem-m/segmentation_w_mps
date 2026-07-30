# Windows WPF coordinator shell technical slice

This project is the representative Windows shell for the technical spike. It
is not an installer or a complete Windows product.

The visual and wording references are:

- `docs/USER_MANUAL_JA.md`
- `docs/assets/user-manual/README.md`
- screenshots `01-setup`, `02-start`, `03-input-sample`, `07-running`,
  `09-result-success`, and `10-result-failure`
- the canonical non-clinical copy in
  `docs/37_NON_CLINICAL_LANGUAGE_GUIDE.md`

The slice intentionally contains one WPF window and one coordinator-specific
process client. It has no external UI framework, service, dependency resolver,
installer, updater, DICOM flow, extra model, or embedded browser.

The shell starts `tswm-process-supervisor supervise --interactive-cancel`.
The supervisor owns the coordinator Job Object. The shell reads coordinator
protocol v1 JSONL from supervisor stdout and writes the literal host command
`cancel` to supervisor stdin when the user presses `停止`. The supervisor turns
that into the typed coordinator control message.

Default app-private layout:

```text
tswm-windows-shell.exe
tswm-process-supervisor.exe
runtime/python/Scripts/totalsegmentator-wrapper-coordinator.exe
models/totalseg-home/
sample1/input/owner_cbct_jawcrop_0p5mm.nii.gz
```

An engineering run may pass one absolute JSON path:

```text
--engineering-config <path>
```

The JSON keys are `supervisor_path`, `coordinator_path`,
`coordinator_working_directory`, `bundled_sample_path`, `output_root`, and
`totalseg_home`. This configuration is for spike evidence only and is not a
dependency installation mechanism.

Focused verification modes:

```text
--contract-self-test [absolute-evidence-json]
--capture-ui <setup|start|input|running|success|failure> <absolute-png>
--evidence-run-sample <absolute-evidence-json> --engineering-config <path>
--evidence-cancel-sample <absolute-evidence-json> --engineering-config <path>
```

`--evidence-run-sample` uses the same runtime check, request builder,
supervisor, JSONL event mapping, and result verification as the visible Sample
button flow. It does not use a fake coordinator or CPU fallback.

`--evidence-cancel-sample` waits until the real segment phase reports progress,
then uses the same stop-request method as the `停止` button.
