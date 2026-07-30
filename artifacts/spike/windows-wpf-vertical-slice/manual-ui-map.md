# Manual-to-WPF UI map

The visual and wording references were read before implementation:

- `docs/USER_MANUAL_JA.md`
- `docs/assets/user-manual/README.md`
- `01-setup.png`
- `02-start.png`
- `03-input-sample.png`
- `07-running.png`
- `09-result-success.png`
- `10-result-failure.png`
- `docs/37_NON_CLINICAL_LANGUAGE_GUIDE.md`

| Manual state | WPF evidence | Preserved interaction |
| --- | --- | --- |
| setup | `screen-setup.png` | runtime/model/privacy readiness |
| start | `screen-start.png` | Sample first and NIfTI choice |
| input | `screen-input.png` | strict CUDA, model, output and non-clinical notice |
| running | `screen-running.png` | stage, progress, elapsed time and stop |
| success | `screen-success.png`, `wpf-real-result.png` | local preview/output and next actions |
| typed failure | `screen-failure.png`, `wpf-negative-hidden-gpu.png` | safe reason, error code and no CPU fallback |

The four visible stages remain `目的`, `入力`, `実行`, and `結果`. Canonical
Sample and non-clinical wording is tested against the shared Python constants.
The shell uses one WPF window and no external UI framework, embedded browser,
service layer, updater, DICOM flow, or extra model selection.

Internal automation names, focusable controls, dynamic system brushes,
PerMonitorV2 declaration, long-path declaration, and 96-DPI rendering passed.
External UI Automation, actual keyboard traversal, high-contrast interaction,
other DPI/viewport sizes, native file-dialog interaction, and future DICOM
image interaction remain UNVERIFIED.
