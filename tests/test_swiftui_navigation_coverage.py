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
APP_ENTRY = (APP / "TotalSegmentatorWrapperForMacApp.swift").read_text(encoding="utf-8")


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
        self.assertEqual(
            screens,
            {"setup", "start", "inputAndCreation", "running", "dicomRescue", "ctPreview", "result"},
        )
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
        shared_input = body(VIEWS, "struct InputAndCreationView")
        self.assertIn("chooseCTInput", shared_input)
        self.assertNotIn("openSampleViewer", shared_input)
        self.assertNotIn("useSampleInput", shared_input)
        self.assertIn("手元のCTを選ぶ", shared_input)
        self.assertIn("別のCTを選ぶ", shared_input)
        self.assertIn("NextPhaseButton(", shared_input)

    def test_secondary_capture_rescue_has_an_explicit_confirmation_gate(self):
        app_screen = body(STATE, "enum AppScreen")
        self.assertIn("case dicomRescue", app_screen)
        root = body(VIEWS, "struct RootView")
        self.assertIn("case .dicomRescue:", root)
        self.assertIn("DicomRescueView()", root)

        audit = body(STATE, "func runDicomAudit")
        clean_branch = audit.index("let cleanCandidates")
        rescue_branch = audit.index("let rescueCandidates")
        viewer_branch = audit.index("let viewerExportCandidates")
        self.assertLess(clean_branch, rescue_branch)
        self.assertLess(rescue_branch, viewer_branch)
        self.assertIn("secondaryCaptureRescueCandidates", audit)
        self.assertIn("beginSecondaryCaptureRescue", audit)

        rescue_view = body(VIEWS, "struct DicomRescueView")
        for text in (
            "三方向の形が自然に見えるよう、画像の端を動かしてください。",
            "同じ色のハンドルは連動します。",
            "推定の確かさ",
            "理由を見る",
            "推定形状に戻す",
            "スライス順を反転",
            "画像の向きを修正",
            "この形状で作成",
            "別のCTを選ぶ",
            "AXIAL",
            "CORONAL",
            "SAGITTAL",
        ):
            with self.subTest(text=text):
                self.assertIn(text, rescue_view)
        self.assertIn("rescueRotationQuarterTurns", rescue_view)
        self.assertIn("rescueAxisPermutation", rescue_view)
        self.assertIn("rescueMPRPreviewSlices", rescue_view)
        self.assertIn("rescueConfirmationUnavailableReason", rescue_view)
        self.assertIn("rescueImageUpdateFailed", rescue_view)
        self.assertIn("chooseAnotherCTFromRescue", rescue_view)
        self.assertIn("RescueStretchPlaneCard", rescue_view)
        self.assertIn("horizontalAxis: .x", rescue_view)
        self.assertIn("verticalAxis: .y", rescue_view)
        self.assertIn("verticalAxis: .z", rescue_view)
        for removed_control in (
            "RescueSpacingEditor",
            "RescueMeasurementImage",
            "既知の長さ",
            "rescuePseudo3DPreviewURL",
            "rescueCropMinX",
            "TextField(",
            "Stepper(",
        ):
            with self.subTest(removed_control=removed_control):
                self.assertNotIn(removed_control, rescue_view)

        stretch_slider = body(VIEWS, "private struct RescueStretchSlider")
        self.assertIn("Slider(", stretch_slider)
        self.assertIn("log2(", stretch_slider)
        self.assertIn("pow(2, position)", stretch_slider)
        self.assertIn("case .x: return .blue", stretch_slider)
        self.assertIn("case .y: return .green", stretch_slider)
        self.assertIn("case .z: return .orange", stretch_slider)

        mpr_canvas = body(VIEWS, "private struct RescueMPRCanvas")
        self.assertIn("if isUpdating, imageURL != nil", mpr_canvas)
        self.assertIn("画像を更新中", mpr_canvas)
        self.assertNotIn('Text("preview', mpr_canvas)

        begin_stretch = body(STATE, "func beginRescueStretchAdjustment")
        self.assertIn('rescueConfirmationToken = ""', begin_stretch)
        set_stretch = body(STATE, "func setRescueStretchSpacing")
        self.assertIn("rescueXYLocked = false", set_stretch)
        self.assertIn("rescueSpacingX = clamped", set_stretch)
        self.assertIn("rescueSpacingY = clamped", set_stretch)
        self.assertIn("rescueSpacingZ = clamped", set_stretch)
        finish_stretch = body(STATE, "func finishRescueStretchAdjustment")
        self.assertIn("rescueSpacingDidChange(axis: axis)", finish_stretch)

        transform_change = body(STATE, "func rescueTransformDidChange")
        self.assertNotIn("rescueMPRPreviewSlices = []", transform_change)
        self.assertNotIn("rescuePreviewShapeXYZ = []", transform_change)
        self.assertIn("scheduleRescuePreviewUpdate()", transform_change)

        choose_another = body(STATE, "func chooseAnotherCTFromRescue")
        self.assertIn("guard !isRunning", choose_another)
        self.assertIn("resetSecondaryCaptureRescue()", choose_another)
        self.assertIn("runDicomAudit(dicomDir: url)", choose_another)
        self.assertIn("prepareNiftiInput(url)", choose_another)

        unavailable_reason = body(STATE, "var rescueConfirmationUnavailableReason")
        for text in (
            "画像を更新しています",
            "三方向の画像を確認すると作成できます",
            "画像の更新に失敗しました",
        ):
            self.assertIn(text, unavailable_reason)

        confirm = body(STATE, "func confirmSecondaryCaptureRescue")
        self.assertIn("canFinalizeRescueTransform", confirm)
        self.assertIn("finalizeSecondaryCaptureRescue()", confirm)
        self.assertNotIn("dicomPrepareRescueCommand", confirm)
        self.assertNotIn("CommandBuilder.runCommand", confirm)
        prepared = body(STATE, "private func acceptPreparedRescueNifti")
        self.assertIn("guard rescueConfirmationWasExplicit", prepared)
        self.assertIn("startRun()", prepared)
        self.assertNotIn("寸法を確認してください", STATE)

    def test_rescue_command_uses_existing_prepare_rescue_cli_contract(self):
        command = body(COMMANDS, "static func dicomPrepareRescueCommand")
        self.assertIn('"dicom-normalizer-prepare-rescue"', command)
        self.assertIn('"--patched-spacing"', command)
        self.assertIn("spacing.commandValue", command)
        self.assertIn('"--series-key"', command)
        export_stack = body(COMMANDS, "static func dicomExportRescueStackCommand")
        self.assertIn('"export-rescue-stack"', export_stack)
        self.assertIn('"--series-key"', export_stack)
        for declaration, subcommand in (
            ("static func dicomRescueEstimateCommand", "dicom-rescue-estimate"),
            ("static func dicomRescuePreviewCommand", "dicom-rescue-preview"),
            ("static func dicomRescueFinalizeCommand", "dicom-rescue-finalize"),
        ):
            with self.subTest(declaration=declaration):
                self.assertIn(f'"{subcommand}"', body(COMMANDS, declaration))
        finalize = body(COMMANDS, "static func dicomRescueFinalizeCommand")
        self.assertIn('"--confirmation-token"', finalize)
        begin_rescue = body(STATE, "func beginSecondaryCaptureRescue")
        self.assertIn("exportPrimaryRescueStackIfAvailable", begin_rescue)
        export = body(STATE, "private func exportPrimaryRescueStackIfAvailable")
        self.assertIn("source_manifest.json", export)
        self.assertIn("startSecondaryCaptureSpacingEstimation", export)
        self.assertIn(".sourceStackUnavailable", export)
        self.assertIn("三方向の画像を安全に準備できませんでした", export)
        self.assertNotIn("NIfTI作成へ進めません", export)
        private_directory = body(STATE, "private func makeRescueDirectoryPrivate")
        self.assertIn(".posixPermissions: 0o700", private_directory)
        self.assertIn("permissions.intValue & 0o777 == 0o700", private_directory)

    def test_partial_geometry_rescue_uses_standard_tag_precedence(self):
        candidates = body(STATE, "func secondaryCaptureRescueCandidates(payload:")
        self.assertIn('"geometry_rescue_candidate"', candidates)
        self.assertIn('"pixel_spacing_mm"', candidates)
        self.assertIn('"spacing_between_slices"', candidates)
        self.assertIn('"projected_slice_spacing_mm"', candidates)

        candidate = body(STATE, "struct SecondaryCaptureRescueCandidate")
        self.assertIn("x: validSpacing(pixelSpacingColumn)", candidate)
        self.assertIn("y: validSpacing(pixelSpacingRow)", candidate)
        projected = candidate.index("validSpacing(projectedSliceSpacing)")
        between = candidate.index("validSpacing(spacingBetweenSlices)")
        thickness = candidate.index("validSpacing(sliceThickness)")
        self.assertLess(projected, between)
        self.assertLess(between, thickness)

        estimate = body(STATE, "func startSecondaryCaptureSpacingEstimation")
        self.assertIn("selectedCandidate?.pixelSpacingColumn", estimate)
        self.assertIn("selectedCandidate?.pixelSpacingRow", estimate)
        self.assertIn('"SpacingBetweenSlices"', estimate)
        self.assertIn('"IPPProjectedSliceSpacing"', estimate)
        self.assertIn("selectedCandidate?.preferredSliceStep", estimate)

    def test_rescue_estimate_metadata_is_localized_before_display(self):
        display = body(STATE, "func rescueEvidenceDisplayText")
        for token in (
            "x_spacing_uses_fallback",
            "y_spacing_uses_fallback",
            "z_spacing_uses_fallback",
            "series_count_crop_and_zoom_unknown",
            "screen_capture_crop_offset_and_zoom_may_be_non_unique",
            "registration_not_validated_on_target_real_data",
            "registration_evidence_unavailable",
            "large_border_or_burned_in_overlay_candidate",
            "fallback_initial_candidate",
            "tri_planar_registration",
        ):
            with self.subTest(token=token):
                self.assertIn(token, display)
        self.assertIn("画像だけでは推定の確かさを十分に判断できません", display)

        confidence = body(STATE, "var rescueConfidenceDisplayText")
        self.assertIn('case "unknown"', confidence)
        self.assertIn('return "未推定"', confidence)
        self.assertNotIn("default: return rescueConfidence", confidence)

        apply_metadata = body(STATE, "func applyRescueEstimateMetadata")
        self.assertIn(".map(rescueEvidenceDisplayText)", apply_metadata)
        self.assertIn("seenEvidence", apply_metadata)

    def test_creation_choices_offer_equal_default_and_other_routes_without_toothseg(self):
        creation = body(STATE, "enum CreationChoice")
        self.assertEqual(
            set(re.findall(r"case ([A-Za-z][A-Za-z0-9_]*)", creation)),
            {"standardArchJaw", "individualTeethBeta", "dentalSegmentatorExperimental", "toothSegExperimental"},
        )
        self.assertIn("static let primaryChoices", creation)
        self.assertIn("static let advancedChoices", creation)
        primary = creation[creation.index("static let primaryChoices"):creation.index("static let advancedChoices")]
        advanced = creation[creation.index("static let advancedChoices"):creation.index("var runMode")]
        self.assertNotIn("toothSegExperimental", primary)
        self.assertNotIn("individualTeethBeta", primary)
        self.assertIn("individualTeethBeta", advanced)
        self.assertNotIn("toothSegExperimental", advanced)
        self.assertIn("var runMode: RunMode", creation)
        self.assertIn("var backend: SegmentationBackend", creation)
        self.assertIn("creationChoice = .standardArchJaw", STATE)
        shared_input = body(VIEWS, "struct InputAndCreationView")
        self.assertEqual(shared_input.count("CreationCategoryCard("), 2)
        self.assertIn('title: "標準モデル"', shared_input)
        self.assertIn('title: "その他のモデル"', shared_input)
        self.assertIn('modelName: "TotalSegmentator"', shared_input)
        self.assertNotIn("CreationChoice.advancedChoices", shared_input)
        self.assertNotIn("CreationChoice.allCases", shared_input)
        self.assertIn("CreationMethodComparisonSheet", shared_input)
        self.assertIn("state.creationChoice != .standardArchJaw", shared_input)
        self.assertIn("alternateModelName", shared_input)
        category_card = body(VIEWS, "struct CreationCategoryCard")
        self.assertIn('"checkmark.circle.fill"', category_card)
        self.assertIn('accessibilityValue(isSelected ? "選択中" : "未選択")', category_card)

    def test_creation_comparison_sheet_uses_fixed_result_images_and_gated_choices(self):
        comparison = body(VIEWS, "struct CreationMethodComparisonSheet")
        card = body(VIEWS, "struct CreationMethodComparisonCard")
        image = body(VIEWS, "struct ModelComparisonImage")

        self.assertIn("同じCT・同じ角度・同じ倍率", comparison)
        for image_name in ("totalseg", "dentalseg", "individual", "toothseg"):
            with self.subTest(image_name=image_name):
                self.assertIn(f'imageName: "{image_name}"', comparison)
        self.assertIn("onSelect(.standardArchJaw)", comparison)
        self.assertIn("onSelect(.dentalSegmentatorExperimental)", comparison)
        self.assertIn("onSelect(.individualTeethBeta)", comparison)
        self.assertNotIn("onSelect(.toothSegExperimental)", comparison)
        self.assertIn("この比較画面から選択できます", comparison)
        self.assertIn("結果画面から明示的に実行", comparison)
        for duration in ("約3〜6分", "約7〜12分", "約2〜7分", "追加で約15〜40分"):
            with self.subTest(duration=duration):
                self.assertIn(f'estimatedDuration: "{duration}"', comparison)
        self.assertIn("M1 Mac・メモリ16 GB", comparison)
        self.assertIn("処理時間の目安", card)
        self.assertIn("selectedChoice == .standardArchJaw", comparison)
        self.assertIn("selectedChoice == .dentalSegmentatorExperimental", comparison)
        self.assertIn("selectedChoice == .individualTeethBeta", comparison)
        self.assertIn("borderedProminent", card)
        self.assertIn('appendingPathComponent("model_comparison"', image)

    def test_no_technical_picker_or_cpu_restore_remains(self):
        shared = body(VIEWS, "struct InputAndCreationView")
        self.assertNotIn("Picker", shared)
        self.assertNotIn("struct RunSettingsView", VIEWS)
        self.assertIn('device = "mps"', STATE)
        restore = body(STATE, "private func restoreUserSettings")
        self.assertIn('device = "mps"', restore)
        self.assertIn('defaults.set("mps", forKey: UserSettingKey.device)', body(STATE, "private func saveUserSettings"))

    def test_debug_ui_preview_mode_is_explicit_and_release_safe(self):
        app = body(APP_ENTRY, "struct TotalSegmentatorWrapperForMacApp")
        preview = body(STATE, "func applyUIPreview")
        shared_input = body(VIEWS, "struct InputAndCreationView")
        self.assertIn("#if DEBUG", APP_ENTRY)
        self.assertIn("appState.applyUIPreview()", app)
        self.assertIn(".defaultSize(width: 1280, height: 800)", app)
        self.assertIn('arguments.firstIndex(of: "--ui-preview")', preview)
        self.assertIn('"input-advanced"', preview)
        self.assertIn('"input-comparison"', preview)
        self.assertIn('"dicom-rescue"', preview)
        self.assertIn('scenario == "input-advanced"', preview)
        self.assertIn("creationChoice = .individualTeethBeta", preview)
        self.assertIn('guard !isUIPreviewMode else { return }', body(STATE, "private func saveUserSettings"))
        self.assertNotIn('state.uiPreviewScenario == "input-advanced"', shared_input)
        self.assertIn('state.uiPreviewScenario == "input-comparison"', shared_input)
        self.assertIn("UI PREVIEW", body(VIEWS, "struct HeaderView"))

    def test_setup_idle_and_shared_input_details(self):
        setup = body(VIEWS, "struct SetupView")
        self.assertIn("はじめの準備", body(VIEWS, "struct HeaderView"))
        self.assertIn("準備を始める", setup)
        self.assertIn("DisclosureGroup(\"データの扱い\")", setup)
        self.assertIn("if state.setupRunning", setup)
        self.assertNotIn("openSampleViewer", setup)
        self.assertNotIn("3Dサンプルを開く", setup)
        self.assertIn("このMacのGPUを使用", VIEWS)
        shared_input = body(VIEWS, "struct InputAndCreationView")
        self.assertIn("保存先", shared_input)
        self.assertIn('Text("仕上がり")', shared_input)
        self.assertIn('Toggle("境界を滑らかにする"', shared_input)
        self.assertNotIn('DisclosureGroup("詳細設定"', shared_input)
        self.assertIn('DisclosureGroup("入力・保存情報")', shared_input)

        refresh_launch = body(STATE, "func refreshLaunchState")
        self.assertNotIn("startSetup()", refresh_launch)
        self.assertIn("準備を始めるまで通信しません", refresh_launch)

    def test_running_uses_parsed_progress_with_indeterminate_fallback(self):
        running = body(VIEWS, "struct RunProgressView")
        self.assertIn("ProgressView()", running)
        self.assertIn("ProgressView(value: fraction)", running)
        self.assertIn("runElapsed", running)
        self.assertIn("使用機能", running)
        self.assertIn("保存先", running)
        progress_model = body(STATE, "struct RunLogProgress")
        self.assertIn("\\(percent)%", progress_model)
        self.assertIn("etaSeconds", progress_model)
        self.assertIn("CTデータを処理しています。", STATE)
        self.assertNotIn("歯列と顎骨をまとめて表示する結果", STATE)
        self.assertNotIn("歯列と顎骨を5つの領域に分けています。", STATE)

    def test_indeterminate_stage_distinguishes_internal_subtask_progress(self):
        running = body(VIEWS, "struct RunProgressView")
        progress_bar = body(VIEWS, "private struct WeightedRunProgressBar")
        self.assertIn('var text = "内部処理"', running)
        self.assertIn('text += " \\(step) / \\(total)（\\(percent)%）"', running)
        self.assertIn('text += "・残り約\\(formatCompactDuration(eta))"', running)
        self.assertNotIn("工程全体の進捗率は取得できません", running)
        self.assertNotIn("工程全体の進捗率は算出できません", running)
        self.assertNotIn("処理は継続しています", running)
        self.assertNotIn("全体の進捗範囲", running)
        self.assertNotIn("この工程の進捗率は取得できません", running)
        self.assertNotIn('var text = "現在の処理：\\(label)"', running)
        self.assertIn('["現在の処理", "現在の内部処理", "処理"]', running)
        self.assertIn("index == currentIndex", progress_bar)
        self.assertIn("currentFraction == nil", progress_bar)
        self.assertIn(".onChange(of: currentFraction)", progress_bar)
        self.assertIn("repeatForever(autoreverses: true)", progress_bar)
        self.assertIn('text != "進捗ログを受信しました。"', running)
        self.assertIn('!text.contains("処理を継続しています")', running)
        self.assertIn('!text.contains("処理は継続中です")', running)

    def test_forward_actions_share_one_prominent_full_width_component(self):
        component = body(VIEWS, "struct NextPhaseButton")
        self.assertIn("frame(maxWidth: .infinity, minHeight: 44)", component)
        self.assertIn("Color.accentColor", component)
        self.assertIn('Image(systemName: "arrow.right")', component)
        self.assertIn("disabled(!isEnabled)", component)

        for view_name in (
            "SetupView",
            "InputAndCreationView",
            "DentalPreparationConfirmationSheet",
            "DicomSeriesSelectionSheet",
            "CTPreviewView",
            "ResultView",
        ):
            with self.subTest(view=view_name):
                self.assertIn("NextPhaseButton", body(VIEWS, f"struct {view_name}"))

        expected_labels = (
            "準備を始める",
            "CTデータを選ぶ",
            "Sampleで3Dプレビューを作る",
            "このCTで3Dプレビューを作る",
            "この撮影を使う",
            "表示中の撮影で3Dプレビュー作成へ進む",
            "3Dプレビューを開く",
            "高精細歯分割（ToothSeg）を実行",
        )
        for label in expected_labels:
            with self.subTest(label=label):
                self.assertIn(label, VIEWS)

    def test_every_phase_uses_the_component_matching_its_navigation_role(self):
        self.assertEqual(VIEWS.count("NextPhaseButton("), 10)

        setup = body(VIEWS, "struct SetupView")
        start = body(VIEWS, "struct StartChoiceView")
        input_and_creation = body(VIEWS, "struct InputAndCreationView")
        running = body(VIEWS, "struct RunProgressView")
        ct_preview = body(VIEWS, "struct CTPreviewView")
        result = body(VIEWS, "struct ResultView")

        self.assertIn("NextPhaseButton", setup)
        self.assertIn("PhaseChoiceCard", start)
        self.assertNotIn("NextPhaseButton", start)
        self.assertIn("NextPhaseButton", input_and_creation)
        self.assertNotIn("NextPhaseButton", running)
        self.assertIn("state.stopRun()", running)
        self.assertIn("NextPhaseButton", ct_preview)
        self.assertIn("NextPhaseButton", result)

        dental_confirmation = body(VIEWS, "struct DentalPreparationConfirmationSheet")
        dental_preparation = body(VIEWS, "struct DentalPreparationSheet")
        dicom_selection = body(VIEWS, "struct DicomSeriesSelectionSheet")
        self.assertIn("NextPhaseButton", dental_confirmation)
        self.assertNotIn("NextPhaseButton", dental_preparation)
        self.assertIn("NextPhaseButton", dicom_selection)

        self.assertNotIn("struct ChoiceCard", VIEWS)

    def test_dental_confirmation_preparation_and_cancel(self):
        self.assertIn("showDentalPreparationConfirmation", STATE)
        self.assertIn("showDentalPreparationSheet", STATE)
        self.assertIn("dentalsegStatusCommand", STATE)
        self.assertIn("dentalsegPrepareCommand", STATE)
        self.assertIn("choice == .dentalSegmentatorExperimental && !isDentalSegmentatorModelReady", body(STATE, "func requestCreationChoice"))
        self.assertIn("choice == .toothSegExperimental && !isToothSegModelReady", body(STATE, "func requestCreationChoice"))
        self.assertIn("toothsegStatusCommand", STATE)
        self.assertIn("toothsegPrepareCommand", STATE)
        self.assertIn("dentalPreparationRunner.resetTerminationRequest()", body(STATE, "func confirmDentalPreparation"))
        self.assertIn("func cancelDentalPreparation", STATE)
        shared = body(VIEWS, "struct InputAndCreationView")
        root = body(VIEWS, "struct RootView")
        self.assertNotIn("DentalPreparationConfirmationSheet", shared)
        self.assertNotIn("DentalPreparationSheet", shared)
        self.assertIn("DentalPreparationConfirmationSheet", root)
        self.assertIn("DentalPreparationSheet", root)
        confirmation = body(VIEWS, "struct DentalPreparationConfirmationSheet")
        self.assertIn("追加モデルデータを取得するので少し時間がかかります。", confirmation)
        self.assertIn("約920 MB", confirmation)
        self.assertNotIn("CPUやTotalSegmentator", confirmation)
        self.assertIn("キャンセル", body(VIEWS, "struct DentalPreparationSheet"))

    def test_result_toothseg_preparation_is_explicit_and_preserves_primary_choice(self):
        request = body(STATE, "func requestToothSegRefine")
        preparation = body(STATE, "func confirmDentalPreparation")
        result = body(VIEWS, "struct ResultView")

        self.assertIn("if isToothSegModelReady", request)
        self.assertIn("canShowToothSegRefine || canRetryToothSegRefine", request)
        self.assertIn("startToothSegRefineRun()", request)
        self.assertIn("modelPreparationPurpose = .toothSegRefine", request)
        self.assertIn("showDentalPreparationConfirmation = true", request)
        self.assertIn("modelPreparationPurpose == .creationSelection", preparation)
        self.assertIn("self.creationChoice = pendingChoice", preparation)
        self.assertIn("結果画面のボタンをもう一度押す", preparation)
        self.assertNotIn("startToothSegRefineRun()", preparation)
        self.assertIn("state.requestToothSegRefine()", result)
        self.assertIn("元の歯列・顎骨結果は利用できます。", result)
        self.assertIn("state.failureReasonText", result)

    def test_dicom_defaults_first_candidate_and_preview_actions(self):
        audit = body(STATE, "func runDicomAudit")
        self.assertIn("selectedDicomSeriesID = cleanCandidates.first?.id", audit)
        self.assertIn("startDicomCleanConversion", audit)
        self.assertNotIn("cleanCandidates.count > 1", audit)
        preview = body(VIEWS, "struct CTPreviewView")
        self.assertIn("CT画像を確認", body(VIEWS, "struct HeaderView"))
        self.assertIn("表示中の撮影で3Dプレビュー作成へ進む", preview)
        self.assertIn("同じフォルダのほかの撮影を見る", preview)
        self.assertIn("別のDICOMフォルダを選ぶ", preview)
        self.assertIn("chooseDicomFolderAndAudit", preview)
        self.assertIn("上から", preview)
        self.assertIn("正面から", preview)
        self.assertIn("横から", preview)
        self.assertNotIn("slice.detailText", preview)
        self.assertIn("複数の撮影データがあります。", preview)
        self.assertIn("Set(state.dicomViewerExportCandidates.map(\\.seriesKey)).count", preview)
        self.assertNotIn("詳細ログを表示", preview)
        provenance = body(STATE, "private func inputProvenancePayload")
        self.assertIn('"first_geometry_ok"', provenance)
        self.assertIn('"user_selected"', provenance)
        self.assertIn('"series_description"', provenance)
        self.assertNotIn("inputURL", provenance)

    def test_dental_cancel_waits_for_its_dedicated_runner_before_enabling_runs(self):
        confirmation = body(STATE, "func confirmDentalPreparation")
        cancel = body(STATE, "func cancelDentalPreparation")

        self.assertIn("let runner = dentalPreparationRunner", confirmation)
        self.assertIn("dentalPreparationCancellationRequested = true", cancel)
        self.assertIn("dentalPreparationRunner.terminate", cancel)
        self.assertNotIn("dentalPreparationRunning = false", cancel)
        self.assertIn("self.dentalPreparationRunning = false", confirmation)
        self.assertLess(
            confirmation.index("self.dentalPreparationRunning = false"),
            confirmation.index("if self.dentalPreparationCancellationRequested"),
        )
        self.assertIn("if dentalPreparationRunning", body(STATE, "var runPreflightBlockingReason"))

    def test_app_does_not_create_case_output_before_strict_cli_preflight(self):
        start_run = body(STATE, "func startRun")
        run_call = "let rc = runner.run(command, environment: environment, logURL: appRunLogURL)"
        provenance_write = "writeJSON(provenance, to: output.appendingPathComponent(\"input_provenance.json\"))"

        self.assertIn("let appRunLogURL = paths.appRunLog", start_run)
        self.assertNotIn("createDirectory(at: output", start_run)
        self.assertIn(run_call, start_run)
        self.assertIn("let caseWasCreated = FileManager.default.fileExists(atPath: output.path)", start_run)
        self.assertIn(provenance_write, start_run)
        self.assertLess(start_run.index(run_call), start_run.index(provenance_write))
        self.assertIn("stoppedBeforeSummary || rc != 0", start_run)

    def test_success_failure_actions_and_safe_error_copy(self):
        result = body(VIEWS, "struct ResultView")
        self.assertIn("3Dプレビューを開く", result)
        self.assertIn("3D Slicer用に書き出す", result)
        self.assertIn("エラー情報をコピー", result)
        self.assertIn("結果表示対象", result)
        self.assertIn("ToothSegを再実行", result)
        self.assertIn("state.canRetryToothSegRefine", result)
        self.assertIn("state.canShowToothSegRefine", result)
        self.assertIn("!state.primaryRunTeethDetected", result)
        self.assertIn("func copySafeErrorInfo", STATE)
        safe = body(STATE, "var safeErrorCopyText")
        self.assertIn("app_version", safe)
        self.assertIn("feature", safe)
        self.assertIn("mps_state", safe)
        self.assertIn("timestamp", safe)
        self.assertIn("error_code", safe)
        self.assertIn("safeErrorFeatureText", safe)
        self.assertNotIn("creationChoice.rawValue", safe)
        feature = body(STATE, "var safeErrorFeatureText")
        self.assertIn('safeErrorCode.hasPrefix("toothseg_")', feature)
        self.assertIn("ToothSeg高精細化", feature)
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
        self.assertIn("restoredChoice == .toothSegExperimental ? .standardArchJaw", restore)
        self.assertNotIn("creationChoice = .toothSegExperimental", restore)
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

    def test_toothseg_refine_command_uses_fixed_12mm_margin(self):
        refine = body(COMMANDS, "static func toothSegRefineCommand")
        start_refine = body(STATE, "func startToothSegRefineRun")
        self.assertIn('"--toothseg-refine"', refine)
        self.assertIn('"--teeth-crop-margin-mm"', refine)
        self.assertIn("toothsegRefineMarginMM", refine)
        self.assertIn('let toothsegRefineMarginMM = "12"', COMMANDS)
        self.assertIn('"--teeth-craniofacial-case"', refine)
        self.assertIn("craniofacialCase.path", refine)
        self.assertNotIn('"--teeth-robust-craniofacial-preflight"', refine)
        self.assertIn("craniofacialCase: outputURL", start_refine)

    def test_toothseg_preview_is_separate_and_selected_by_result_flavor(self):
        preview_output = body(STATE, "private func expectedSurfacePreviewOutputURL")
        preview_url = body(STATE, "private func expectedSurfacePreviewURL")
        open_preview = body(STATE, "func openResultPreview")
        regenerate = body(STATE, "func regenerateSurfacePreview")
        refine = body(STATE, "func startToothSegRefineRun")

        self.assertIn('case .toothSeg:', preview_output)
        self.assertIn('"surface_preview/toothseg"', preview_output)
        self.assertIn("expectedSurfacePreviewOutputURL", preview_url)
        self.assertIn("expectedSurfacePreviewURL", open_preview)
        self.assertIn("expectedSurfacePreviewOutputURL", regenerate)
        self.assertIn("outputDir: previewOutput", regenerate)
        self.assertIn("smoothSurfaces: higherOrderResampling", regenerate)
        self.assertIn("expectedSurfacePreviewOutputURL", refine)
        self.assertIn("outputDir: previewOutput", refine)
        self.assertIn("smoothSurfaces: smoothSurfacesForRun", refine)
        self.assertGreaterEqual(
            refine.count("self?.activeResultFlavor = .craniofacial"),
            2,
            "stopped and failed ToothSeg runs must restore the primary result viewer",
        )

        start_run = body(STATE, "func startRun")
        self.assertIn("let smoothSurfacesForRun = higherOrderResampling", start_run)
        self.assertIn("smoothSurfaces: smoothSurfacesForRun", start_run)

        surface_command = body(COMMANDS, "static func surfacePreviewCommand")
        self.assertIn('command.append("--smooth-preset")', surface_command)
        self.assertIn('smoothSurfaces ? "slicer_like" : "none"', surface_command)
        self.assertIn('command.append("--defer-stl")', surface_command)

    def test_specific_toothseg_failure_classes_precede_generic_markers(self):
        failure = body(STATE, "func runFailureReason")
        marker_index = failure.index("let markers")
        self.assertLess(failure.index("mps backend out of memory"), marker_index)
        self.assertLess(failure.index("ダウンロード関連で失敗しました"), marker_index)
        self.assertLess(failure.index('lower.contains("no teeth")'), marker_index)
        refine_failure = body(STATE, "func toothSegRefineFailureReason")
        self.assertIn('case "toothseg_mps_oom"', refine_failure)
        self.assertIn('case "toothseg_input_invalid"', refine_failure)
        self.assertIn('case "toothseg_download_failed", "toothseg_model_preparation_failed"', refine_failure)

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
