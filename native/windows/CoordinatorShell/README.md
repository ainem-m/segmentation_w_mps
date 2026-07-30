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
installer, updater, or embedded browser. DICOM intake is limited to the
existing native audit and `convert-clean` path; rescue conversion is outside
this slice. The enabled additional-model paths are fixed DentalSegmentator
five-label, Individual Teeth beta, and ToothSeg FDI operations.

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
runtime/native/totalsegmentator-wrapper-dicom-normalizer.exe
runtime/native/dcm2niix.exe
models/totalseg-home/
models/dentalseg/
models/toothseg/
sample1/input/owner_cbct_jawcrop_0p5mm.nii.gz
```

An engineering run may pass one absolute JSON path:

```text
--engineering-config <path>
```

The JSON keys are `supervisor_path`, `coordinator_path`,
`coordinator_working_directory`, `bundled_sample_path`, `output_root`, and
`totalseg_home`. Optional `dicom_normalizer_path` and `dcm2niix_path` keys
override the two app-private native defaults above. Optional
`dentalseg_model_root` overrides the app-private DentalSegmentator model root.
Optional `toothseg_model_root` overrides the app-private ToothSeg model root.
Existing NIfTI-only engineering configurations remain valid. This
configuration is for spike evidence only and is not a dependency installation
mechanism.

`DicomIntakeSession` audits a selected folder and exposes only series classified
as `original_ct_geometry_ok`, `convert_clean`, and requiring no external tool.
Audit and conversion children are created suspended, assigned to a Job Object,
then resumed. Cancellation or the fixed 120-second audit / 900-second conversion
limit terminates the whole Job. Process stdout and stderr are drained but never
returned to the shell.

Each audit uses a random UUID workspace below
`<output_root>/.dicom-intake/`. A clean conversion must produce exactly one
non-empty `.nii` or `.nii.gz` beneath its conversion output, with matching
metadata and `segmentation_started=false`, before it can be passed to the
unchanged NIfTI coordinator operation. A successful conversion also writes
`dicom-intake-manifest.json`; it contains no paths, DICOM identifiers,
descriptions, or raw process output.

Focused verification modes:

```text
--contract-self-test [absolute-evidence-json]
--capture-ui <setup|start|input|dicom-input|dicom-series|running|success|failure> <absolute-png>
--evidence-run-sample <absolute-evidence-json> --engineering-config <path>
--evidence-cancel-sample <absolute-evidence-json> --engineering-config <path>
--evidence-run-dicom <absolute-dicom-folder> <absolute-evidence-json> --engineering-config <path>
--evidence-run-dentalseg <absolute-evidence-json> --engineering-config <path>
--evidence-run-individual-teeth <absolute-evidence-json> --engineering-config <path>
--evidence-run-toothseg <absolute-evidence-json> --engineering-config <path>
```

`--evidence-run-sample` uses the same runtime check, request builder,
supervisor, JSONL event mapping, and result verification as the visible Sample
button flow. It does not use a fake coordinator or CPU fallback.

`--evidence-cancel-sample` waits until the real segment phase reports progress,
then uses the same stop-request method as the `停止` button.

`--evidence-run-dicom` uses the visible clean-DICOM audit and conversion
implementation, verifies that no coordinator started during intake, and then
explicitly invokes the unchanged strict-CUDA NIfTI run. Its evidence JSON
contains no input path, series key/UID, description, or native process output.

`--evidence-run-dentalseg` uses the same visible fixed-model selection,
app-private ready-marker gate, strict `cuda:0` policy, Job Object supervisor,
JSONL parser, promotion, and artifact verification as the interactive flow.

`--evidence-run-individual-teeth` uses the visible Individual Teeth beta
selection and the fixed protocol operation. It requires the app-private
`Dataset113_ToothFairy3` checkpoint, uses the existing 5 mm robust
craniofacial preflight, and does not permit a client-selected task, model,
device fallback, or split policy.

`--evidence-run-toothseg` uses the visible fixed ToothSeg selection, verified
app-private Dataset121/Dataset123 model pair, 5 mm robust craniofacial
preflight, fold 5 with TTA disabled, strict `cuda:0`, FDI output, Job Object
supervision, promotion, and artifact verification. It does not permit
client-selected model, dataset, fold, ROI, resolution, or fallback.
