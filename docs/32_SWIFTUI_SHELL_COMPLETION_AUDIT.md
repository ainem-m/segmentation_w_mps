# SwiftUI Shell Completion Audit

Date: 2026-06-14

This document compares the SwiftUI shell migration plan with the implemented
state. It is intentionally checklist-like so packaging readiness can be judged
without rereading the whole codebase.

## Scope

The migration target is the distributed Mac app only. The Python backend,
TotalSegmentator runner, DICOM normalizer, setup manager, sample assets, and
surface preview generation remain backend components. SwiftUI is the only GUI
path; the earlier Tk prototype has been removed.

## Plan vs Implementation

| Plan item | Implementation evidence | Status |
| --- | --- | --- |
| Replace packaged app main executable with SwiftUI | `scripts/build_mac_app.sh` builds `native/macos/TotalSegmentatorWrapperForMac/*.swift` into `Contents/MacOS/TotalSegmentatorWrapperForMac`; `setup_manifest.json` records `ui_frontend: swiftui`. | Complete |
| Remove Tk fallback | The packaged app and source GUI path are SwiftUI-only; CLI remains available for backend and automation workflows. | Complete |
| Build requires full Xcode, not Command Line Tools alone | `build_mac_app.sh` runs full-Xcode preflight and accepts `TOTALSEGMENTATOR_WRAPPER_MAC_XCODE_DEVELOPER_DIR`; module cache is under `dist/swift_module_cache`. | Complete |
| Setup UI in SwiftUI | `SetupView`, `SetupCoordinator`, and headless setup flow handle setup, progress labels, elapsed time, sample viewer, log tail, and Japanese failure messages. | Complete |
| Main UI in SwiftUI | `RootView`, `StartChoiceView`, `SampleTutorialView`, `OwnDataView`, `RunProgressView`, and `ResultView` implement the two-choice interview and result flow. | Complete |
| Use Python backend only through subprocess argv lists | `ProcessRunner` uses `Process.executableURL` and `arguments = Array(command.dropFirst())`; Swift sources do not use shell execution. | Complete |
| Use App Support venv TotalSegmentator path | `CommandBuilder.runCommand()` always passes `--totalseg-bin <App Support>/env/bin/TotalSegmentator`. | Complete |
| Preserve permission policy | `AppPaths` writes runtime data under `~/Library/Application Support/TotalSegmentatorWrapperMac`; setup text states no sudo and no data upload; environment cache paths are App Support scoped. | Complete |
| Button-triggered updater | `更新を確認` fetches the static manifest only after user action. If an update exists, SwiftUI downloads the notarized DMG after confirmation, verifies SHA256, validates the app with Gatekeeper, replaces the writable app bundle, and reopens it. | Complete |
| Sample 1 remains bundled and manually opened | `resources/sample1` is copied into the bundle; SwiftUI opens the offline HTML through `NSWorkspace` only when the user presses a button. | Complete |
| DICOM folder does not run segmentation directly | `chooseDicomFolderAndAudit()` calls `runDicomAudit()`; `runCommand()` is only built for selected NIfTI input. | Complete |
| Individual teeth beta remains explicit | `RunMode.individualTeeth` maps to `--task teeth --experimental-teeth --teeth-crop-margin-mm 5.0`; default remains arch preview. | Complete |
| Stop button terminates running backend process | `stopRun()` calls `ProcessRunner.terminate()`. | Complete |
| Bundle resync keeps existing setups current | `SetupCoordinator.setupStatus()` compares installed bundle hashes and triggers wheel resync or setup-required states. | Complete |
| DMG path uses SwiftUI frontend | `scripts/build_mac_dmg.sh` checks for `Contents/MacOS/TotalSegmentatorWrapperForMac`; evidence import requires `manifest_ui_frontend_swiftui`. | Complete |

## Validation Targets

Required automated checks and current result:

```text
[x] .venv/bin/python -m compileall src tests templates scripts
[x] env PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
[x] ctest --test-dir build/dicom_normalizer --output-on-failure
[x] scripts/build_mac_app.sh
[x] scripts/build_mac_dmg.sh
[x] scripts/verify_zero_env_mac_app.sh
[x] scripts/verify_zero_env_mac_dmg.sh
```

The zero-env `.app` and DMG checks both completed with:

```text
setup_state.status: success
doctor.actual_device: mps
doctor.convtranspose3d_fp32: pass
dicom_normalizer.normalizer_source: app_bundle
codesign verify: valid
```

## Residual Risks

- Notarization and Developer ID signing are still a later milestone. Current
  alpha packaging remains ad-hoc signed.
- Manual desktop interaction is still needed for final UX judgment: file picker,
  Sample 1 viewer button, stop button, and result preview button.
- A full MPS segmentation run through the SwiftUI window should be performed
  outside Codex once the user wants release-candidate confidence. The headless
  zero-env setup checks already verify MPS availability, but they do not press
  the visible UI buttons.
