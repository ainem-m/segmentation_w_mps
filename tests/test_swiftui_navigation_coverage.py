from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "native/macos/TotalSegmentatorWrapperForMac"
STATE = (APP / "AppState.swift").read_text(encoding="utf-8")
VIEWS = (APP / "Views.swift").read_text(encoding="utf-8")
COMMANDS = (APP / "CommandBuilder.swift").read_text(encoding="utf-8")
PROCESS = (APP / "ProcessSupport.swift").read_text(encoding="utf-8")


def body(source: str, declaration: str) -> str:
    start = source.find(declaration)
    if start < 0:
        raise AssertionError(f"missing {declaration}")
    brace = source.find("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1:index]
    raise AssertionError(f"unclosed {declaration}")


class SwiftUINavigationCoverageTests(unittest.TestCase):
    def test_all_current_screens_are_rendered_and_labeled(self):
        screens = set(re.findall(r"case ([A-Za-z][A-Za-z0-9_]*)", body(STATE, "enum AppScreen")))
        self.assertEqual(screens, {"setup", "start", "inputAndCreation", "running", "ctPreview", "result"})
        root = body(VIEWS, "struct RootView")
        header = body(VIEWS, "struct HeaderView")
        for screen in screens:
            with self.subTest(screen=screen):
                self.assertIn(f"case .{screen}:", root)
                self.assertGreaterEqual(header.count(f"case .{screen}:"), 2)

    def test_shared_screen_replaces_sample_and_own_data_screens(self):
        app_screen = body(STATE, "enum AppScreen")
        self.assertIn("case inputAndCreation", app_screen)
        self.assertNotIn("case sample", app_screen)
        self.assertNotIn("case ownData", app_screen)
        root = body(VIEWS, "struct RootView")
        self.assertIn("case .inputAndCreation:", root)
        self.assertIn("InputAndCreationView()", root)
        self.assertIn("struct InputAndCreationView", VIEWS)
        self.assertIn("Sample 1", VIEWS)
        self.assertIn("chooseCTInput", body(VIEWS, "struct InputAndCreationView"))

    def test_creation_choices_are_exactly_three_and_map_to_existing_modes(self):
        creation = body(STATE, "enum CreationChoice")
        self.assertEqual(
            set(re.findall(r"case ([A-Za-z][A-Za-z0-9_]*)", creation)),
            {"standardArchJaw", "individualTeethBeta", "dentalSegmentatorExperimental"},
        )
        self.assertIn("var runMode: RunMode", creation)
        self.assertIn("var backend: SegmentationBackend", creation)
        self.assertIn("creationChoice = .standardArchJaw", STATE)

    def test_no_technical_picker_or_cpu_restore_remains(self):
        shared = body(VIEWS, "struct InputAndCreationView")
        self.assertNotIn("Picker", shared)
        self.assertNotIn("struct RunSettingsView", VIEWS)
        self.assertIn('device = "mps"', STATE)
        restore = body(STATE, "private func restoreUserSettings")
        self.assertIn('device = "mps"', restore)
        self.assertIn('defaults.set("mps", forKey: UserSettingKey.device)', body(STATE, "private func saveUserSettings"))

    def test_setup_idle_and_shared_input_details(self):
        setup = body(VIEWS, "struct SetupView")
        self.assertIn("はじめの準備", body(VIEWS, "struct HeaderView"))
        self.assertIn("準備を始める", setup)
        self.assertIn("DisclosureGroup(\"データの扱い\")", setup)
        self.assertIn("if state.setupRunning", setup)
        self.assertIn("このMacのGPUを使用", VIEWS)
        self.assertIn("保存先", body(VIEWS, "struct InputAndCreationView"))

        refresh_launch = body(STATE, "func refreshLaunchState")
        self.assertNotIn("startSetup()", refresh_launch)
        self.assertIn("準備を始めるまで通信しません", refresh_launch)

    def test_running_is_indeterminate_and_shows_identity_elapsed_and_destination(self):
        running = body(VIEWS, "struct RunProgressView")
        self.assertIn("ProgressView()", running)
        self.assertNotIn("ProgressView(value:", running)
        self.assertIn("runElapsed", running)
        self.assertIn("使用機能", running)
        self.assertIn("保存先", running)
        progress_model = body(STATE, "struct RunLogProgress")
        self.assertNotIn("(\\(percent)%)", progress_model)
        self.assertNotIn("\\(percent)%", progress_model)

    def test_dental_confirmation_preparation_and_cancel(self):
        self.assertIn("showDentalPreparationConfirmation", STATE)
        self.assertIn("showDentalPreparationSheet", STATE)
        self.assertIn("dentalsegStatusCommand", STATE)
        self.assertIn("dentalsegPrepareCommand", STATE)
        self.assertIn("choice == .dentalSegmentatorExperimental, !isDentalSegmentatorModelReady", body(STATE, "func requestCreationChoice"))
        self.assertIn("runner.resetTerminationRequest()", body(STATE, "func confirmDentalPreparation"))
        self.assertIn("func cancelDentalPreparation", STATE)
        shared = body(VIEWS, "struct InputAndCreationView")
        self.assertIn("DentalPreparationConfirmationSheet", shared)
        self.assertIn("DentalPreparationSheet", shared)
        confirmation = body(VIEWS, "struct DentalPreparationConfirmationSheet")
        self.assertIn("CPUやTotalSegmentatorへ切り替えません", confirmation)
        self.assertIn("キャンセル", body(VIEWS, "struct DentalPreparationSheet"))

    def test_dicom_defaults_first_candidate_and_preview_actions(self):
        audit = body(STATE, "func runDicomAudit")
        self.assertIn("selectedDicomSeriesID = cleanCandidates.first?.id", audit)
        self.assertIn("startDicomCleanConversion", audit)
        self.assertNotIn("cleanCandidates.count > 1", audit)
        preview = body(VIEWS, "struct CTPreviewView")
        self.assertIn("中央断面を確認", body(VIEWS, "struct HeaderView"))
        self.assertIn("このCTを使う", preview)
        self.assertIn("断面群を選び直す", preview)
        self.assertIn("別のCTを選ぶ", preview)
        self.assertNotIn("詳細ログを表示", preview)
        provenance = body(STATE, "private func writeInputProvenance")
        self.assertIn('"first_geometry_ok"', provenance)
        self.assertIn('"user_selected"', provenance)
        self.assertIn('"series_description"', provenance)
        self.assertNotIn("inputURL", provenance)

    def test_success_failure_actions_and_safe_error_copy(self):
        result = body(VIEWS, "struct ResultView")
        self.assertIn("3Dプレビューを開く", result)
        self.assertIn("3D Slicer用に書き出す", result)
        self.assertIn("エラー情報をコピー", result)
        self.assertIn("func copySafeErrorInfo", STATE)
        safe = body(STATE, "var safeErrorCopyText")
        self.assertIn("app_version", safe)
        self.assertIn("feature", safe)
        self.assertIn("mps_state", safe)
        self.assertIn("timestamp", safe)
        self.assertIn("error_code", safe)
        self.assertNotIn("inputURL", safe)
        self.assertNotIn("outputURL", safe)
        self.assertIn("enum ResultOutcome", STATE)
        self.assertIn("state.resultOutcome == .failure", result)
        self.assertIn("state.resultOutcome == .success", result)

    def test_root_log_sheet_offers_copy_open_finder_and_close(self):
        root = body(VIEWS, "struct RootView")
        log = body(VIEWS, "struct LogSheetView")
        self.assertIn(".sheet(isPresented: $state.showLog)", root)
        for token in ("ログをコピー", "ログファイルを開く", "Finderで表示", "閉じる"):
            self.assertIn(token, log)
        self.assertNotIn("DisclosureGroup", log)

    def test_result_recovery_actions_remain_available(self):
        result = body(VIEWS, "struct ResultView")
        for token in (
            "state.goToInput()",
            "state.chooseCTInput()",
            "state.retryRunFromResult()",
            "state.goToStart()",
            "入力と作成内容へ戻る",
            "別のCTを選ぶ",
            "もう一度作成",
            "最初に戻る",
        ):
            self.assertIn(token, result)

        self.assertIn("screen = .start", body(STATE, "func goToStart"))
        self.assertIn("goToInputAndCreation()", body(STATE, "func goToInput()"))
        retry = body(STATE, "func retryRunFromResult")
        self.assertIn("resultKind == .dicomAudit", retry)
        self.assertIn("runDicomAudit(dicomDir: lastDicomDirURL)", retry)
        self.assertIn("startRun()", retry)

    def test_dicom_clean_conversion_returns_to_shared_input(self):
        audit = body(STATE, "func runDicomAudit")
        self.assertIn("selectedDicomSeriesID = cleanCandidates.first?.id", audit)
        self.assertIn("startDicomCleanConversion(dicomDir: dicomDir, candidate: candidate)", audit)

        conversion = body(STATE, "private func startDicomCleanConversion")
        for token in (
            "CommandBuilder.dicomConvertCleanCommand",
            "seriesKey: candidate.seriesKey",
            "convert_clean_metadata.json",
            "inputSource = .nifti",
            "screen = .inputAndCreation",
            "dicom_conversion_cancelled",
            "dicom_conversion_failed",
        ):
            self.assertIn(token, conversion)
        self.assertNotIn("viewer_export_cancelled", conversion)
        self.assertNotIn("viewer_export_failed", conversion)
        self.assertNotIn("CommandBuilder.runCommand", conversion)

    def test_viewer_export_conversion_has_its_own_safe_failures(self):
        conversion = body(STATE, "private func startDicomViewerExportConversion")
        for token in (
            "CommandBuilder.dicomPrepareViewerExportCommand",
            "groupID: candidate.groupID",
            "viewer_export_metadata.json",
            "pendingPreparedInputURL = niftiURL",
            "viewerExportPreviewSlices",
            "screen = .ctPreview",
            "viewer_export_cancelled",
            "viewer_export_failed",
        ):
            self.assertIn(token, conversion)
        self.assertNotIn("dicom_conversion_cancelled", conversion)
        self.assertNotIn("dicom_conversion_failed", conversion)
        self.assertNotIn("CommandBuilder.runCommand", conversion)

        accept = body(STATE, "func acceptPreparedCTPreview")
        self.assertIn("inputSource = .nifti", accept)
        self.assertIn("screen = .inputAndCreation", accept)

    def test_surface_regeneration_and_slicer_export_do_not_rerun_inference(self):
        regenerate = body(STATE, "func regenerateSurfacePreview")
        self.assertIn("CommandBuilder.surfacePreviewCommand", regenerate)
        self.assertIn("CommandBuilder.summaryCommand", regenerate)
        self.assertNotIn("CommandBuilder.runCommand", regenerate)

        export = body(STATE, "func exportForSlicer")
        self.assertIn("CommandBuilder.slicerExportCommand", export)
        self.assertIn('appendingPathComponent("slicer_export"', export)
        self.assertNotIn("Slicer.app", export)
        slicer_command = body(COMMANDS, "static func slicerExportCommand")
        self.assertIn('"slicer-export"', slicer_command)
        self.assertNotIn("open -a", slicer_command)

    def test_stop_and_setup_runtime_contracts_are_preserved(self):
        stop = body(STATE, "func stopRun")
        self.assertIn("stopRequested = true", stop)
        self.assertIn("runner.terminate(graceSeconds: 10.0)", stop)
        self.assertIn("停止要求済み", stop)
        for token in ("terminationRequested = true", "resetTerminationRequest", "SIGKILL", "kill(current.processIdentifier"):
            self.assertIn(token, PROCESS)

        setup_status = body(PROCESS, "static func setupStatus")
        run_setup = body(PROCESS, "static func runSetup")
        self.assertIn("venvPythonMatchesBundle", setup_status)
        self.assertIn("venv_python_changed", setup_status)
        self.assertIn("専用Python環境を作り直しています", run_setup)
        self.assertIn('removeItem(at: paths.support.appendingPathComponent("env"', run_setup)

    def test_update_and_log_recovery_contracts_are_preserved(self):
        update = body(STATE, "func checkUpdates")
        for token in (
            "pendingDownloadURL = nil",
            "pendingUpdateSHA256 = \"\"",
            "showingUpdateConfirmation = false",
            "updateCheckRunning = true",
            "ProcessRunner()",
        ):
            self.assertIn(token, update)

        show_log = body(STATE, "func showDetailedLog")
        self.assertIn("refreshLog(from: currentLogURL)", show_log)
        self.assertIn("showLog = true", show_log)
        refresh_log = body(STATE, "func refreshLog")
        self.assertIn("let target = url ?? currentLogURL", refresh_log)
        self.assertIn("readLogTail", refresh_log)

    def test_creation_persistence_migrates_legacy_settings_without_device_restore(self):
        restore = body(STATE, "private func restoreUserSettings")
        self.assertIn("UserSettingKey.creationChoice", restore)
        self.assertIn("UserSettingKey.segmentationBackend", restore)
        self.assertIn("UserSettingKey.runMode", restore)
        self.assertIn("creationChoice = .dentalSegmentatorExperimental", restore)
        self.assertIn("creationChoice = .individualTeethBeta", restore)
        self.assertIn('device = "mps"', restore)
        self.assertNotIn("UserSettingKey.device", restore)

        save = body(STATE, "private func saveUserSettings")
        self.assertIn("defaults.set(creationChoice.rawValue", save)
        self.assertIn('defaults.set("mps", forKey: UserSettingKey.device)', save)

    def test_app_command_builder_forces_mps_for_every_creation_mapping(self):
        run_command = body(COMMANDS, "static func runCommand")
        self.assertIn("_ = device", run_command)
        self.assertIn('"--device",\n            "mps"', run_command)
        self.assertIn('"--execution-profile"', run_command)
        self.assertIn('"macos-app"', run_command)
        self.assertIn('"--require-mps"', run_command)
        self.assertIn("backend.cliValue", run_command)
        self.assertIn("mode.task", run_command)

    def test_sidebar_and_running_screen_keep_navigation_boundary(self):
        sidebar = body(VIEWS, "struct SidebarView")
        self.assertIn("contentShape(Rectangle())", sidebar)
        self.assertIn("guard !state.isRunning && state.screen != .setup else { return }", sidebar)
        self.assertIn("state.goToStart()", sidebar)
        self.assertIn("state.goToInput()", sidebar)

        running = body(VIEWS, "struct RunProgressView")
        self.assertIn("state.stopRun()", running)
        self.assertIn("disabled(!state.isRunning || state.stopRequested)", running)
        self.assertNotIn("goToStart", running)
        self.assertNotIn("goToInput", running)


if __name__ == "__main__":
    unittest.main()
