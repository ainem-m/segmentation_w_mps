# Read-only completion audit

Date: 2026-07-30

| Criterion | Result | Evidence |
| --- | --- | --- |
| Fixed WPF ToothSeg selection and wording | PASS | UI contract and screenshots |
| Existing protocol v1, no client model/fold/ROI selection | PASS | protocol/unit tests |
| Real TotalSegmentator craniofacial preflight | PASS | events and successful benchmark/artifacts |
| Real ToothSeg Dataset121 + Dataset123, fold 5, TTA off | PASS | model and run evidence |
| Strict `cuda:0`, no CPU fallback | PASS | run manifest and CUDA doctor |
| Hidden GPU typed failure | PASS | negative WPF/device evidence |
| CUDA OOM changes no model/resolution/ROI | PASS | fixed command contract; OOM not observed |
| Final FDI mask nonempty and geometry restored | PASS | 168,971 nonzero voxels, 23 labels |
| Offline local-only preview | PASS | URL scan and artifact manifest |
| Verification before promotion | PASS | supervisor evidence |
| One terminal event and OS exit 0 | PASS | events and supervisor evidence |
| Job/GPU descendants absent after completion | PASS | active count 0, no survivors |
| stdout JSONL privacy/validity | PASS | 48/48 UTF-8 JSON objects; privacy scan |
| Python/coordinator/macOS regression | PASS | 300 tests passed, 3 skipped |
| .NET Release builds and UI self-test | PASS | both builds clean; 24-button contract |
| Windows 11 | UNVERIFIED | no Windows 11 host used |
| Clean model/runtime distribution | UNVERIFIED | existing internal wheelhouse/runtime reused |
| Installer/signing/update/rollback | UNVERIFIED | outside slice |
| External UI Automation/high contrast/DPI matrix | UNVERIFIED | outside this engineering pass |
| DICOM rescue/AMD/ARM64 | UNVERIFIED | outside slice |

Blocking FAIL: none.
