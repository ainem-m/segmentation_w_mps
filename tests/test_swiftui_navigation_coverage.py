from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SWIFT_APP_DIR = ROOT / "native" / "macos" / "TotalSegmentatorWrapperForMac"
APP_STATE = SWIFT_APP_DIR / "AppState.swift"
COMMAND_BUILDER = SWIFT_APP_DIR / "CommandBuilder.swift"
VIEWS = SWIFT_APP_DIR / "Views.swift"


SCREEN_VIEW_MAP = {
    "setup": "SetupView",
    "start": "StartChoiceView",
    "sample": "SampleTutorialView",
    "ownData": "OwnDataView",
    "running": "RunProgressView",
    "ctPreview": "CTPreviewView",
    "result": "ResultView",
}


VIEW_REQUIRED_ACTIONS = {
    "SetupView": {
        "startSetup",
        "openSampleViewer",
        "セットアップ開始",
        "3Dサンプルを開く",
    },
    "StartChoiceView": {
        "goToSample",
        "goToOwnData",
        "openSampleViewer",
        "Sampleで流れを体験する",
        "Sample 1の3Dプレビューを開く",
        "自分のCTを開く",
        "ChoiceCardContent",
        "primaryAction",
    },
    "SampleTutorialView": {
        "useSampleInput",
        "startRun",
        "chooseOutputRoot",
        "goToOwnData",
        "goToStart",
        "sampleInputButtonTitle",
        "sampleInputButtonIcon",
        "本番ではここで自分のCTを選びます。Sampleでは同梱CTを使って同じ流れを練習できます。",
        "3Dプレビューを作成",
        "自分のCTを開く",
        "最初に戻る",
    },
    "OwnDataView": {
        "chooseCTInput",
        "startRun",
        "chooseOutputRoot",
        "goToSample",
        "goToStart",
        "ownDataPrimaryButtonTitle",
        "canStartOwnDataRun",
        "CTを選ぶ",
        "Sampleで流れを体験する",
        "最初に戻る",
    },
    "RunProgressView": {
        "stopRun",
        "停止",
        "停止要求済み",
        "終了処理中",
    },
    "CTPreviewView": {
        "acceptPreparedCTPreview",
        "returnToViewerExportSelection",
        "goToInput",
        "showDetailedLog",
        "CT確認プレビュー",
        "このCTで3Dプレビューを作成",
        "断面群を選び直す",
        "CTを選び直す",
        "詳細ログを表示",
    },
    "ResultView": {
        "goToInput",
        "retryRunFromResult",
        "goToStart",
        "openOutputFolder",
        "openResultPreview",
        "regenerateSurfacePreview",
        "exportForSlicer",
        "useSelectedDicomSeries",
        "useSelectedViewerExportCandidate",
        "showDetailedLog",
        "CTを選び直す",
        "retryButtonTitle",
        "canRetryFromResult",
        "この撮影を使う",
        "この断面群を確認する",
        "3Dプレビューを再生成",
        "Slicerで開くファイルを書き出す",
        "詳細ログを表示",
        "最初に戻る",
    },
}


class SwiftUINavigationCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app_state = APP_STATE.read_text(encoding="utf-8")
        self.command_builder = COMMAND_BUILDER.read_text(encoding="utf-8")
        self.views = VIEWS.read_text(encoding="utf-8")

    def test_all_app_screens_are_rendered_and_labeled(self) -> None:
        screen_cases = _enum_cases(self.app_state, "AppScreen")
        self.assertEqual(set(screen_cases), set(SCREEN_VIEW_MAP))

        for screen, view_name in SCREEN_VIEW_MAP.items():
            self.assertIn(f"case .{screen}:", self.views)
            self.assertIn(f"{view_name}()", self.views)
            self.assertIn(f"case .{screen}:", _computed_property_body(self.views, "title"))
            self.assertIn(f"case .{screen}:", _computed_property_body(self.views, "subtitle"))

    def test_each_screen_has_required_user_actions(self) -> None:
        for view_name, required_tokens in VIEW_REQUIRED_ACTIONS.items():
            body = _struct_body(self.views, view_name)
            for token in required_tokens:
                with self.subTest(view=view_name, token=token):
                    self.assertIn(token, body)

    def test_result_screen_has_non_disabled_escape_routes(self) -> None:
        body = _struct_body(self.views, "ResultView")
        recovery_actions = {
            "goToInput": "CTを選び直す",
            "retryRunFromResult": "retryButtonTitle",
            "goToStart": "最初に戻る",
        }
        for action, label in recovery_actions.items():
            with self.subTest(action=action):
                button_block = _button_block_containing(body, action)
                self.assertIn(label, button_block)
                self.assertNotIn("outputURL.flatMap(caseSurfacePreview)", button_block)
                self.assertNotIn("outputURL == nil", button_block)
                if action == "retryRunFromResult":
                    self.assertNotIn("inputURL == nil", button_block)
                    self.assertIn("canRetryFromResult", button_block)

    def test_recovery_actions_mutate_to_expected_screens(self) -> None:
        go_to_start = _function_body(self.app_state, "goToStart")
        self.assertIn("guard !isRunning else { return }", go_to_start)
        self.assertIn("screen = .start", go_to_start)
        self.assertIn("selectedStep = 0", go_to_start)

        go_to_input = _function_body(self.app_state, "goToInput")
        self.assertIn("guard !isRunning else { return }", go_to_input)
        self.assertIn("inputSource == .sample", go_to_input)
        self.assertIn("sameFileURL($0, paths.sampleInput)", go_to_input)
        self.assertIn("goToSample()", go_to_input)
        self.assertIn("goToOwnData()", go_to_input)

        retry = _function_body(self.app_state, "retryRunFromResult")
        self.assertIn("guard !isRunning else { return }", retry)
        self.assertIn("resultKind == .dicomAudit", retry)
        self.assertIn("runDicomAudit(dicomDir: lastDicomDirURL)", retry)
        self.assertIn("startRun()", retry)

        can_retry = _computed_property_body(self.app_state, "canRetryFromResult")
        self.assertIn("guard !isRunning else { return false }", can_retry)
        self.assertIn("resultKind == .dicomAudit", can_retry)
        self.assertIn("lastDicomDirURL != nil", can_retry)
        self.assertIn("inputURL != nil", can_retry)
        self.assertIn("inputSource == .sample || inputSource == .nifti", can_retry)

        go_to_sample = _function_body(self.app_state, "goToSample")
        self.assertNotIn("inputURL = paths.sampleInput", go_to_sample)
        self.assertNotIn("inputSource = .sample", go_to_sample)
        self.assertIn("inputURL = nil", go_to_sample)
        self.assertIn("inputSource = .none", go_to_sample)
        self.assertIn("outputURL = nil", go_to_sample)
        self.assertIn("screen = .sample", go_to_sample)
        self.assertIn("selectedStep = 1", go_to_sample)

        use_sample = _function_body(self.app_state, "useSampleInput")
        self.assertIn("inputURL = paths.sampleInput", use_sample)
        self.assertIn("inputSource = .sample", use_sample)
        self.assertIn("screen = .sample", use_sample)
        self.assertIn("selectedStep = 1", use_sample)

        go_to_own_data = _function_body(self.app_state, "goToOwnData")
        self.assertIn("if inputSource == .sample", go_to_own_data)
        self.assertIn("inputURL = nil", go_to_own_data)
        self.assertIn("inputSource = .none", go_to_own_data)
        self.assertIn("outputURL = nil", go_to_own_data)
        self.assertIn("screen = .ownData", go_to_own_data)
        self.assertIn("selectedStep = 1", go_to_own_data)

    def test_pre_result_screens_have_visible_cross_branch_and_start_escape(self) -> None:
        start_body = _struct_body(self.views, "StartChoiceView")
        self.assertIn("Sample 1の3Dプレビューを開く", start_body)
        self.assertNotIn("Sample 1の3Dを開く", start_body)
        self.assertIn("ChoiceCardContent", start_body)
        self.assertIn("primaryAction", start_body)
        self.assertIn("state.openSampleViewer()", start_body)
        self.assertIn("state.goToSample()", start_body)
        self.assertIn("state.goToOwnData()", start_body)
        self.assertNotIn("Sampleを使う", start_body)

        card_body = _struct_body(self.views, "ChoiceCardContent")
        self.assertIn("Button(action: primaryAction)", card_body)
        self.assertIn("RoundedRectangle(cornerRadius: 8", card_body)
        self.assertIn("allowsHitTesting(false)", card_body)
        self.assertIn("contentShape(RoundedRectangle", card_body)
        self.assertIn("frame(maxWidth: .infinity, minHeight: 220", card_body)

        sample_body = _struct_body(self.views, "SampleTutorialView")
        self.assertNotIn("ActionRow", sample_body)
        self.assertNotIn("完成イメージを見る", sample_body)
        self.assertIn("sampleInputButtonTitle", sample_body)
        self.assertIn("sampleInputButtonIcon", sample_body)
        self.assertNotIn("開始しても自分のCTには触れません", sample_body)
        self.assertIn("自分のCTを開く", sample_body)
        self.assertIn("state.goToOwnData()", sample_body)
        self.assertIn("最初に戻る", sample_body)
        self.assertIn("state.goToStart()", sample_body)

        own_data_body = _struct_body(self.views, "OwnDataView")
        self.assertIn("Sampleで流れを体験する", own_data_body)
        self.assertIn("state.goToSample()", own_data_body)
        self.assertIn("最初に戻る", own_data_body)
        self.assertIn("state.goToStart()", own_data_body)

    def test_dicom_audit_result_retries_audit_not_segmentation(self) -> None:
        run_dicom = _function_body(self.app_state, "runDicomAudit")
        self.assertIn("inputURL = dicomDir", run_dicom)
        self.assertIn("lastDicomDirURL = dicomDir", run_dicom)
        self.assertIn("resultKind = .dicomAudit", run_dicom)

        start_run = _function_body(self.app_state, "startRun")
        self.assertIn("if inputSource == .dicomFolder || isDirectory(inputURL)", start_run)
        self.assertIn("runDicomAudit(dicomDir: inputURL)", start_run)
        self.assertIn("guard inputSource == .sample || inputSource == .nifti", start_run)
        self.assertIn("let output = nextCaseOutput()", start_run)

        retry = _function_body(self.app_state, "retryRunFromResult")
        self.assertIn("if resultKind == .dicomAudit", retry)
        self.assertIn("runDicomAudit(dicomDir: lastDicomDirURL)", retry)

        self.assertIn("case dicomAudit", self.app_state)
        self.assertIn("もう一度確認", self.app_state)
        self.assertIn("resultKind == .dicomAudit ? \"もう一度確認\" : \"もう一度実行\"", self.app_state)
        self.assertIn("dicomAuditFailureMessage(auditJSON: auditJSON)", run_dicom)

    def test_dicom_clean_candidate_can_be_converted_and_used_as_input(self) -> None:
        self.assertIn("dicomCleanCandidates", self.app_state)
        self.assertIn("var canUseSelectedDicomSeries", self.app_state)
        self.assertIn("cleanDicomSeriesCandidates(auditJSON: auditJSON)", self.app_state)
        self.assertIn("通常のCTとして取り込める候補があります。自動で準備します。", self.app_state)

        run_audit = _function_body(self.app_state, "runDicomAudit")
        self.assertIn("cleanCandidates.count == 1", run_audit)
        self.assertIn("startDicomCleanConversion(dicomDir: dicomDir, candidate: candidate)", run_audit)
        self.assertIn("cleanCandidates.count > 1", run_audit)
        self.assertIn("取り込める撮影候補が複数あります", run_audit)

        convert_body = _function_body(self.app_state, "startDicomCleanConversion")
        self.assertIn("CommandBuilder.dicomConvertCleanCommand", convert_body)
        self.assertIn("seriesKey: candidate.seriesKey", convert_body)
        self.assertIn("convertedNiftiURL(metadataJSON: metadataJSON)", convert_body)
        self.assertIn("inputSource = .nifti", convert_body)
        self.assertIn("CTを取り込みました。3Dプレビューを作成できます。", convert_body)
        self.assertIn("プレビュー作成はまだ開始していません", convert_body)
        self.assertNotIn("CommandBuilder.runCommand", convert_body)

        result_view = _struct_body(self.views, "ResultView")
        self.assertIn("state.dicomCleanCandidates", result_view)
        self.assertIn("state.canUseSelectedDicomSeries", result_view)
        self.assertIn("state.useSelectedDicomSeries()", result_view)

        self.assertIn("series_key", self.app_state)
        self.assertIn("seriesNumber = jsonInt(item[\"series_number\"])", self.app_state)

    def test_viewer_export_candidate_can_be_prepared_and_used_as_input(self) -> None:
        self.assertIn("struct ViewerExportCandidate", self.app_state)
        self.assertIn("struct CTPreviewSlice", self.app_state)
        self.assertIn("dicomViewerExportCandidates", self.app_state)
        self.assertIn("var canUseSelectedViewerExportCandidate", self.app_state)
        self.assertIn("var canAcceptCTPreview", self.app_state)
        self.assertIn("var hasSparseSliceDirection", self.app_state)
        self.assertIn("viewerExportCandidates(auditJSON: auditJSON)", self.app_state)
        self.assertIn("CTを見るソフトから表示用の断面画像として書き出されたデータの可能性があります", self.app_state)
        self.assertIn('planeLabel == "axial_like" || planeLabel == "oblique_axial_like"', self.app_state)

        run_audit = _function_body(self.app_state, "runDicomAudit")
        self.assertIn("let viewerExportCandidates", run_audit)
        self.assertIn("!viewerExportCandidates.isEmpty", run_audit)
        self.assertIn("表示用断面画像の可能性があります", run_audit)

        prepare_body = _function_body(self.app_state, "startDicomViewerExportConversion")
        self.assertIn("CommandBuilder.dicomPrepareViewerExportCommand", prepare_body)
        self.assertIn("groupID: candidate.groupID", prepare_body)
        self.assertIn("viewer_export_metadata.json", prepare_body)
        self.assertIn("convertedNiftiURL(metadataJSON: metadataJSON)", prepare_body)
        self.assertIn("pendingPreparedInputURL = niftiURL", prepare_body)
        self.assertIn("viewerExportPreviewSlices(metadataJSON: metadataJSON)", prepare_body)
        self.assertIn("screen = .ctPreview", prepare_body)
        self.assertIn("非診断preview", prepare_body)
        self.assertNotIn("CommandBuilder.runCommand", prepare_body)

        accept_body = _function_body(self.app_state, "acceptPreparedCTPreview")
        self.assertIn("inputURL = acceptedInputURL", accept_body)
        self.assertIn("inputSource = .nifti", accept_body)
        self.assertIn("screen = .ownData", accept_body)

        result_view = _struct_body(self.views, "ResultView")
        self.assertIn("state.dicomViewerExportCandidates", result_view)
        self.assertIn("state.canUseSelectedViewerExportCandidate", result_view)
        self.assertIn("state.useSelectedViewerExportCandidate()", result_view)
        self.assertIn("CTを見るソフトから表示用に書き出された断面画像", result_view)

        ct_preview = _struct_body(self.views, "CTPreviewView")
        self.assertIn("このCTで3Dプレビューを作成", ct_preview)
        self.assertIn("断面群を選び直す", ct_preview)
        self.assertIn("state.canAcceptCTPreview", ct_preview)
        self.assertIn("SlicePreviewCard", ct_preview)
        self.assertIn("slice方向は面内より粗い場合があります", self.app_state)
        self.assertIn("3D結果が階段状に見えることがあります", self.app_state)

        self.assertIn("func loadPGMImage", self.views)
        self.assertIn("slice previewを作成できませんでした", self.views)
        self.assertIn("画像がほぼ空に見えます", self.views)

        command_builder = (SWIFT_APP_DIR / "CommandBuilder.swift").read_text(encoding="utf-8")
        self.assertIn("dicom-normalizer-prepare-viewer-export", command_builder)
        self.assertIn("--group-id", command_builder)
        self.assertIn("paths.dcm2niix.path", command_builder)

        self.assertNotIn("fusion", ct_preview.lower())
        self.assertNotIn("高解像度CT", ct_preview)

    def test_dicom_audit_failure_json_is_rendered_with_reason_and_next_actions(self) -> None:
        format_summary = _function_body(self.app_state, "formatDicomSummary")
        self.assertIn("(payload[\"status\"] as? String) == \"failed\"", format_summary)
        self.assertIn("formatDicomAuditFailure(payload)", format_summary)

        failure_body = _function_body(self.app_state, "formatDicomAuditFailure")
        self.assertIn("dicomAutoImportUnavailableMessage()", failure_body)
        self.assertIn("このCTは自動取り込みできませんでした。", self.app_state)
        self.assertIn("CTを見るソフトから「表示用の断面画像」として書き出されたデータの可能性があります。", self.app_state)
        self.assertIn("dicomAuditReasonLabel(reason)", failure_body)
        self.assertIn("possible_causes", failure_body)
        self.assertIn("next_actions", failure_body)
        self.assertIn("入力は変更されていません。プレビュー作成は開始していません。", failure_body)

        format_summary = _function_body(self.app_state, "formatDicomSummary")
        self.assertIn("dicomClassificationLabel", format_summary)
        self.assertIn("dicomNextActionShortLabel", format_summary)

        reason_body = _function_body(self.app_state, "dicomAuditReasonLabel")
        self.assertIn("case \"timeout\"", reason_body)
        self.assertIn("確認が時間切れになりました", reason_body)
        self.assertIn("case \"normalizer_failed\"", reason_body)

        classification_body = _function_body(self.app_state, "dicomClassificationLabel")
        self.assertIn("通常CTとして取り込み可能", classification_body)
        self.assertIn("圧縮形式（自動取り込み不可）", classification_body)

        next_action_body = _function_body(self.app_state, "dicomNextActionShortLabel")
        self.assertIn("アプリが取り込み準備", next_action_body)
        self.assertIn("手動確認が必要", next_action_body)

    def test_surface_preview_can_be_regenerated_without_ai_rerun(self) -> None:
        regenerate = _function_body(self.app_state, "regenerateSurfacePreview")
        self.assertIn("CommandBuilder.surfacePreviewCommand", regenerate)
        self.assertIn("CommandBuilder.summaryCommand", regenerate)
        self.assertIn("stoppedBeforeSummary", regenerate)
        self.assertIn("CT解析は再実行せず", regenerate)
        self.assertNotIn("CommandBuilder.runCommand", regenerate)

        can_regenerate = _computed_property_body(self.app_state, "canRegenerateSurfacePreview")
        self.assertIn("resultKind == .inference", can_regenerate)
        self.assertIn("outputURL != nil", can_regenerate)

    def test_slicer_export_is_file_only_and_does_not_auto_launch_slicer(self) -> None:
        command_builder = _function_body(self.command_builder, "slicerExportCommand")
        self.assertIn('"slicer-export"', command_builder)
        self.assertIn('"--case"', command_builder)
        self.assertIn('"--source"', command_builder)
        self.assertNotIn("open -a", command_builder)
        self.assertNotIn("Slicer.app", command_builder)

        export_body = _function_body(self.app_state, "exportForSlicer")
        self.assertIn("CommandBuilder.slicerExportCommand", export_body)
        self.assertIn("slicer_export", export_body)
        self.assertIn("openURLInWorkspace(exportURL)", export_body)
        self.assertNotIn("openURLInWorkspace(paths", export_body)
        self.assertNotIn("open -a", export_body)
        self.assertNotIn("Slicer.app", export_body)

        can_export = _computed_property_body(self.app_state, "canExportForSlicer")
        self.assertIn("resultKind == .inference", can_export)
        self.assertIn("outputURL != nil", can_export)

    def test_stop_request_returns_to_result_after_termination_or_kill(self) -> None:
        stop_run = _function_body(self.app_state, "stopRun")
        self.assertIn("stopRequested = true", stop_run)
        self.assertIn("runner.terminate(graceSeconds: 10.0)", stop_run)
        self.assertIn("停止要求済み", stop_run)
        self.assertIn("終了処理中", stop_run)

        process_support = (SWIFT_APP_DIR / "ProcessSupport.swift").read_text(encoding="utf-8")
        self.assertIn("terminationRequested = true", process_support)
        self.assertIn("resetTerminationRequest", process_support)
        self.assertIn("isTerminationRequested", process_support)
        self.assertIn("Process skipped: stop requested", process_support)
        self.assertIn("SIGKILL", process_support)
        self.assertIn("kill(current.processIdentifier", process_support)

        start_run = _function_body(self.app_state, "startRun")
        self.assertIn("runner.resetTerminationRequest()", start_run)
        self.assertIn("stoppedBeforeSummary", start_run)

    def test_setup_rebuilds_venv_when_bundle_python_changes(self) -> None:
        process_support = (SWIFT_APP_DIR / "ProcessSupport.swift").read_text(encoding="utf-8")
        setup_status = _function_body(process_support, "setupStatus")
        run_setup = _function_body(process_support, "runSetup")
        venv_check = _function_body(process_support, "venvPythonMatchesBundle")

        self.assertIn("venvPythonMatchesBundle(paths: paths, python312: python312)", setup_status)
        self.assertIn("venv_python_changed", setup_status)
        self.assertIn("専用Python環境を作り直しています", run_setup)
        self.assertIn("removeItem(at: paths.support.appendingPathComponent(\"env\"", run_setup)
        self.assertIn("pyvenv.cfg", venv_check)
        self.assertIn("executable = ", venv_check)

    def test_update_check_clears_stale_pending_download_url(self) -> None:
        check_updates = self.app_state
        self.assertIn("updateCheckRunning", check_updates)
        self.assertIn("updateInstallRunning", check_updates)
        self.assertIn("pendingDownloadURL = nil", check_updates)
        self.assertIn("showingUpdateConfirmation = false", check_updates)
        self.assertIn("status == \"current\"", check_updates)
        self.assertIn("更新確認に失敗しました", check_updates)
        self.assertIn("downloadAndInstallPendingUpdate", check_updates)
        self.assertIn("sha256Hex", check_updates)
        self.assertIn("writeUpdateInstallerScript", check_updates)

    def test_higher_order_resampling_is_selectable_and_passed_to_run_command(self) -> None:
        settings = _struct_body(self.views, "RunSettingsView")
        self.assertIn("$state.higherOrderResampling", settings)
        self.assertIn("境界を滑らかにする（高次補間）", settings)

        start_run = _function_body(self.app_state, "startRun")
        self.assertIn("higherOrderResampling: higherOrderResampling", start_run)

        run_command = _function_body(self.command_builder, "runCommand")
        self.assertIn("higherOrderResampling: Bool", self.command_builder)
        self.assertIn("if higherOrderResampling", run_command)
        self.assertIn("--higher-order-resampling", run_command)

    def test_log_drawer_refreshes_current_case_log_when_opened(self) -> None:
        refresh_log = _function_body(self.app_state, "refreshLog")
        self.assertIn("let target = url ?? currentLogURL", refresh_log)
        self.assertIn("readLogTail(target", refresh_log)

        show_detailed_log = _function_body(self.app_state, "showDetailedLog")
        self.assertIn("refreshLog(from: currentLogURL)", show_detailed_log)
        self.assertIn("showLog = true", show_detailed_log)

        log_drawer = _struct_body(self.views, "LogDrawer")
        self.assertIn("Binding(", log_drawer)
        self.assertIn("state.showDetailedLog()", log_drawer)
        self.assertIn("isExpanded: logExpanded", log_drawer)

    def test_sidebar_provides_global_navigation_without_interrupting_running_or_setup(self) -> None:
        body = _struct_body(self.views, "SidebarView")
        self.assertIn("contentShape(Rectangle())", body)
        self.assertIn(".onTapGesture", body)
        self.assertIn("guard !state.isRunning && state.screen != .setup else { return }", body)
        self.assertIn("state.goToStart()", body)
        self.assertIn("state.goToInput()", body)

    def test_running_screen_is_the_only_temporarily_modal_screen(self) -> None:
        body = _struct_body(self.views, "RunProgressView")
        self.assertIn("stopRun", body)
        self.assertIn("disabled(!state.isRunning || state.stopRequested)", body)
        self.assertNotIn("goToStart", body)
        self.assertNotIn("goToInput", body)


def _enum_cases(source: str, enum_name: str) -> list[str]:
    body = _declaration_body(source, f"enum {enum_name}")
    return re.findall(r"\bcase\s+([A-Za-z_][A-Za-z0-9_]*)", body)


def _struct_body(source: str, struct_name: str) -> str:
    return _declaration_body(source, f"struct {struct_name}")


def _function_body(source: str, function_name: str) -> str:
    return _declaration_body(source, f"func {function_name}")


def _computed_property_body(source: str, property_name: str) -> str:
    try:
        return _declaration_body(source, f"private var {property_name}")
    except AssertionError:
        return _declaration_body(source, f"var {property_name}")


def _declaration_body(source: str, needle: str) -> str:
    start = source.find(needle)
    if start == -1:
        raise AssertionError(f"Declaration not found: {needle}")
    brace = source.find("{", start)
    if brace == -1:
        raise AssertionError(f"Declaration has no body: {needle}")
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"Declaration body did not close: {needle}")


def _button_block_containing(source: str, token: str) -> str:
    token_index = source.find(token)
    if token_index == -1:
        raise AssertionError(f"Token not found in source: {token}")
    button_start = source.rfind("Button", 0, token_index)
    if button_start == -1:
        raise AssertionError(f"Button start not found for token: {token}")
    next_button = source.find("\n                Button", token_index + len(token))
    if next_button == -1:
        next_button = len(source)
    return source[button_start:next_button]


if __name__ == "__main__":
    unittest.main()
