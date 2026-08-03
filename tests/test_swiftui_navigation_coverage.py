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
            {"setup", "start", "inputAndCreation", "iosMesh", "running", "dicomRescue", "ctPreview", "result"},
        )
        root = body(VIEWS, "struct RootView")
        header = body(VIEWS, "struct HeaderView")
        for screen in screens:
            with self.subTest(screen=screen):
                self.assertIn(f"case .{screen}:", root)
                self.assertGreaterEqual(header.count(f"case .{screen}:"), 2)

    def test_ios_mesh_mode_is_separate_strict_mps_flow(self):
        start = body(VIEWS, "struct StartChoiceView")
        ios_view = body(VIEWS, "struct IOSMeshView")
        ios_run = body(STATE, "func startIOSMeshRun")
        prepare = body(COMMANDS, "static func iosMeshSegNetPrepareCommand")
        command = body(COMMANDS, "static func iosMeshSegNetRunCommand")

        self.assertIn("口腔内スキャンを使う", start)
        self.assertIn("goToIOSMesh()", start)
        self.assertNotIn("互換モデル", start)
        self.assertNotIn("選択した互換モデル", STATE)
        self.assertNotIn("MeshSegNet・Apache-2.0", start)
        self.assertIn("PLY/STLを選ぶ", ios_view)
        self.assertIn('Picker("顎"', ios_view)
        self.assertIn('case .upper: return "上顎"', STATE)
        self.assertIn('case .lower: return "下顎"', STATE)
        self.assertIn("iosMeshJaw", ios_view)
        self.assertIn("MeshSegNet", ios_view)
        self.assertIn("Apache-2.0", ios_view)
        self.assertIn("TGNet（重みは別途取得）", ios_view)
        self.assertIn("ライセンス：未確認", ios_view)
        self.assertIn("TGNetの重みは本アプリに同梱されていません", ios_view)
        self.assertIn("配布元が示す利用条件をご確認ください", ios_view)
        self.assertIn("診断・治療には使用しないでください", ios_view)
        self.assertIn("同梱モデル（MeshSegNet）を使用する", ios_view)
        self.assertIn("TGNet用ZIP／フォルダを選ぶ", ios_view)
        self.assertIn("chooseIOSTGNetSet()", ios_view)
        self.assertIn("ckpts(new).zip の配布ページを開く", ios_view)
        self.assertIn("TGNetの重みを確認しています", ios_view)
        self.assertIn("TGNetの重みを確認しました", ios_view)
        self.assertIn("TGNetの重みを確認できませんでした", ios_view)
        self.assertIn("もう一度選ぶ", ios_view)
        self.assertIn("詳細を表示", ios_view)
        self.assertNotIn("その他の互換checkpointを選ぶ", ios_view)
        self.assertNotIn("信頼できるcheckpointだけ", ios_view)
        self.assertNotIn("source: user-provided", ios_view)
        self.assertNotIn("実行時にSHA-256を記録", ios_view)
        self.assertNotIn("Text(customModel.lastPathComponent)", ios_view)
        self.assertIn("canChooseDirectories = true", STATE)
        self.assertNotIn("func chooseIOSMeshModel()", STATE)
        self.assertIn("resetIOSMeshModel()", ios_view)
        tgnet_picker = body(STATE, "func chooseIOSTGNetSet")
        self.assertNotIn("iosMeshJaw != .lower", tgnet_picker)
        self.assertNotIn("TGNetは現在、上顎のみ対応しています", tgnet_picker)
        self.assertIn("canChooseFiles = true", tgnet_picker)
        self.assertIn("canChooseDirectories = true", tgnet_picker)
        self.assertIn('"zip"', tgnet_picker)
        self.assertIn("ckpts(new).zip", tgnet_picker)
        self.assertIn("validateIOSTGNetSet(url)", tgnet_picker)
        validation = body(STATE, "private func validateIOSTGNetSet")
        self.assertIn("iosMeshTGNetValidationRunning = true", validation)
        self.assertIn("iosMeshTGNetValidationError", validation)
        self.assertIn("iosMeshTGNetValidationDetail", validation)
        self.assertIn("iosMeshTGNetValidateCommand", validation)
        tgnet_source = body(STATE, "var tgnetCheckpointPageURL")
        self.assertIn(
            "https://drive.google.com/drive/folders/15oP0CZM_O_-Bir18VbSM8wRUEzoyLXby",
            tgnet_source,
        )
        self.assertIn(
            "openURLInWorkspace(tgnetCheckpointPageURL)",
            body(STATE, "func openTGNetCheckpointPage"),
        )
        self.assertIn("startIOSMeshRun()", ios_view)
        self.assertNotIn("TGNetは現在、上顎のみ対応しています", ios_view)
        self.assertNotIn("iosMeshJaw == .lower && iosMeshUsesTGNetFinal", ios_run)
        self.assertIn("ios_meshsegnet_setup", prepare)
        self.assertIn('"prepare"', prepare)
        self.assertIn('"--progress-log"', prepare)
        self.assertIn("paths.iosMeshSegNetRunLog.path", prepare)
        self.assertIn("if customModel == nil {", ios_run)
        self.assertIn("iosMeshDownloadProgressFromLog", STATE)
        self.assertIn("state.iosMeshDownloadProgress", ios_view)
        self.assertIn("ProgressView(value: fraction)", ios_view)
        self.assertIn("ios_model_dispatch", command)
        self.assertIn('"--jaw"', command)
        self.assertIn("jaw", command)
        self.assertNotIn('"upper"', command)
        self.assertIn('"--device"', command)
        self.assertIn('"mps"', command)
        self.assertIn("model.path", command)
        self.assertIn('"--allow-custom-model"', command)
        self.assertIn("isCustomModel", command)
        self.assertIn("mps_fallback_env", ios_run)
        self.assertIn("iosMeshCustomModelURL != nil", ios_run)
        self.assertIn("jaw: selectedJaw.rawValue", ios_run)
        self.assertNotIn("cpu", command.lower())

    def test_ios_mesh_success_describes_gingiva_from_result_json(self):
        ios_view = body(VIEWS, "struct IOSMeshView")
        ios_run = body(STATE, "func startIOSMeshRun")

        self.assertIn('["gingiva"]', ios_run)
        self.assertIn('["present"]', ios_run)
        self.assertIn("iosMeshGingivaPresent", ios_run)
        self.assertIn("state.iosMeshGingivaStatusText", ios_view)
        self.assertNotIn(
            "歯肉を gingiva.stl として保存します。",
            ios_view,
        )

    def test_ios_mesh_model_preparation_failure_is_not_reported_as_inference(self):
        ios_run = body(STATE, "func startIOSMeshRun")
        mapping = body(STATE, "func iosMeshModelPreparationFailure")

        self.assertIn("var modelPreparationFailed = false", ios_run)
        self.assertIn("paths.iosMeshSegNetStatusJSON", ios_run)
        self.assertIn('["error_code"] as? String', ios_run)
        self.assertIn("iosMeshModelPreparationFailure", ios_run)
        self.assertIn('mpsState: "unknown"', ios_run)
        for source_code, app_code in (
            ("model_download_failed", "ios_mesh_model_download_failed"),
            ("model_integrity_failed", "ios_mesh_model_integrity_failed"),
            ("model_prepare_busy", "ios_mesh_model_prepare_busy"),
        ):
            self.assertIn(f'case "{source_code}"', mapping)
            self.assertIn(f'code: "{app_code}"', mapping)
        for raw_field in ('["message"]', '["details"]', '["error_type"]'):
            self.assertNotIn(raw_field, ios_run)

    def test_ios_mesh_completion_promotes_results_and_demotes_rerun(self):
        ios_view = body(VIEWS, "struct IOSMeshView")
        ios_run = body(STATE, "func startIOSMeshRun")

        self.assertIn("} else if state.iosMeshSucceeded {", ios_view)
        self.assertIn('title: "結果を開く"', ios_view)
        self.assertIn('Button("同じ設定で再実行")', ios_view)
        self.assertLess(
            ios_view.index('title: "結果を開く"'),
            ios_view.index('Button("同じ設定で再実行")'),
        )
        self.assertIn("selectedStep = 2", ios_run)
        success = ios_run.split(
            "} else if rc == 0 && usedMPS && !teeth.isEmpty {", maxsplit=1
        )[1].split("} else {", maxsplit=1)[0]
        self.assertIn("selectedStep = 3", success)

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

    def test_single_dicom_file_is_audited_instead_of_treated_as_nifti(self):
        choose = body(STATE, "func chooseCTInput")
        choose_another = body(STATE, "func chooseAnotherCTFromRescue")
        prepare = body(STATE, "private func prepareSelectedCTInput")

        self.assertIn("prepareSelectedCTInput(url)", choose)
        self.assertIn("prepareSelectedCTInput(url)", choose_another)
        self.assertIn("isNiftiFile(url)", prepare)
        self.assertIn("prepareNiftiInput(url)", prepare)
        self.assertIn("runDicomAudit(dicomDir: url)", prepare)

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
        self.assertIn("prepareSelectedCTInput(url)", choose_another)

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

    def test_dicom_command_builders_prefer_series_key_over_nonunique_series_number(self):
        for declaration in (
            "static func dicomConvertCleanCommand",
            "static func dicomPrepareRescueCommand",
            "static func dicomExportRescueStackCommand",
            "static func dicomPrepareViewerExportCommand",
        ):
            with self.subTest(declaration=declaration):
                command = body(COMMANDS, declaration)
                self.assertIn("if !seriesKey.isEmpty", command)
                self.assertIn("--series-key", command)
                self.assertIn("--series-number", command)
                self.assertLess(
                    command.index("if !seriesKey.isEmpty"),
                    command.index("--series-number"),
                )

    def test_rescue_input_context_survives_confirmation_without_exposing_paths(self):
        self.assertIn("struct RescueInputContext", STATE)
        self.assertIn("private var rescueInputContext", STATE)
        self.assertIn("func makeRescueInputContext", STATE)
        self.assertIn("func clearRescueInputContext", STATE)

        finalize = body(STATE, "private func finalizeSecondaryCaptureRescue")
        self.assertIn("makeRescueInputContext", finalize)
        accepted = body(STATE, "private func acceptPreparedRescueNifti")
        self.assertIn("RescueInputContext", accepted)
        self.assertIn("rescueInputContext = context", accepted)

        provenance = body(STATE, "private func inputProvenancePayload")
        for key in (
            '"source_kind": "dicom_rescue"',
            '"non_diagnostic_preview": true',
            '"classification"',
            '"source_manifest_sha256"',
            '"confirmation_sha256"',
            '"transform_sha256"',
        ):
            with self.subTest(key=key):
                self.assertIn(key, provenance)
        self.assertNotIn("inputURL.path", provenance)
        self.assertNotIn("series_instance_uid", provenance)

        safe_error = body(STATE, "var safeDicomErrorCopyLines")
        self.assertIn("rescueInputContext", safe_error)
        self.assertIn("dicom_rescue_source_manifest_sha256", safe_error)
        safe_input = body(STATE, "var safeErrorInputKind")
        self.assertIn('return "dicom_rescue"', safe_input)

        reset = body(STATE, "private func resetSecondaryCaptureRescue")
        self.assertIn("clearRescueInputContext()", reset)
        self.assertIn("clearRescueInputContext()", body(STATE, "func useSampleInput"))
        self.assertIn("clearRescueInputContext()", body(STATE, "private func prepareNiftiInput"))

        result = body(VIEWS, "struct ResultView")
        self.assertIn("state.hasRescueInputContext", result)
        self.assertIn("参考用3Dプレビュー", result)

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
        self.assertIn('"input-dicom-preview"', preview)
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
        self.assertIn("ProgressView(value: progress.fraction)", setup)
        self.assertIn("state.setupDownloadProgress", setup)
        self.assertIn("progress.displayText", setup)
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
        start_setup = body(STATE, "func startSetup")
        self.assertIn("activeLogURL = paths.launcherLog", start_setup)
        self.assertIn("self?.activeLogURL = nil", start_setup)
        refresh_log = body(STATE, "func refreshLog")
        self.assertIn("SetupStep.downloadTotalsegWeights.rawValue", refresh_log)
        self.assertIn("SetupStep.downloadDentalsegWeights.rawValue", refresh_log)

        progress_model = body(STATE, "struct SetupDownloadProgress")
        self.assertIn("taskTotal", progress_model)
        self.assertIn("completedBytes", progress_model)
        self.assertIn("totalBytes", progress_model)
        self.assertIn("etaSeconds", progress_model)
        self.assertIn("rateBPS", progress_model)
        self.assertIn("resumed", progress_model)
        self.assertIn("resumeFromBytes", progress_model)
        self.assertIn("weights_integrity_failed", COMMANDS)
        self.assertIn("weights_manifest_incompatible", COMMANDS)
        self.assertIn("func setupExecutionStateFromLog", STATE)

    def test_hashed_dependency_install_has_a_dedicated_visible_step(self):
        setup_step = body(COMMANDS, "enum SetupStep")
        self.assertIn(
            'case installLockedDependencies = "install_locked_dependencies"',
            setup_step,
        )
        self.assertIn('case .installLockedDependencies: return "固定済み依存導入"', setup_step)
        self.assertIn("SHA-256で固定された同梱依存パッケージ", setup_step)
        self.assertNotIn("通信状況", setup_step)

    def test_dependency_lock_identity_changes_recreate_the_managed_venv(self):
        current_record = body(PROCESS, "func currentBundleRecord")
        setup_status = body(PROCESS, "static func setupStatus")
        refresh = body(PROCESS, "func managedVenvRefreshDecision")
        for key in (
            "requirements_lock_sha256",
            "dependency_lock_metadata_sha256",
            "dependency_wheelhouse_manifest_sha256",
        ):
            with self.subTest(key=key):
                self.assertIn(key, current_record)
                self.assertIn(f'"{key}"', setup_status)
                self.assertIn(f'"{key}_changed"', refresh)
        self.assertIn("optionalBundleFingerprint", current_record)
        self.assertIn("guard let currentFingerprint", setup_status)

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
        self.assertEqual(VIEWS.count("NextPhaseButton("), 12)

        setup = body(VIEWS, "struct SetupView")
        start = body(VIEWS, "struct StartChoiceView")
        input_and_creation = body(VIEWS, "struct InputAndCreationView")
        ios_mesh = body(VIEWS, "struct IOSMeshView")
        running = body(VIEWS, "struct RunProgressView")
        ct_preview = body(VIEWS, "struct CTPreviewView")
        result = body(VIEWS, "struct ResultView")

        self.assertIn("NextPhaseButton", setup)
        self.assertIn("PhaseChoiceCard", start)
        self.assertNotIn("NextPhaseButton", start)
        self.assertIn("NextPhaseButton", input_and_creation)
        self.assertIn("NextPhaseButton", ios_mesh)
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
        preparation = body(STATE, "func confirmDentalPreparation")
        cancellation = body(STATE, "func cancelDentalPreparation")
        sheet = body(VIEWS, "struct DentalPreparationSheet")
        self.assertIn("dentalSegmentatorPreparationProgressFromLog", STATE)
        self.assertIn("@Published var dentalPreparationFailed = false", STATE)
        self.assertIn("self.dentalPreparationFailed = true", preparation)
        self.assertIn("dentalseg_model_preparation_failed", preparation)
        self.assertIn("toothseg_model_preparation_failed", preparation)
        self.assertIn('reportedPreparationErrorCode == "insufficient_disk_space"', preparation)
        self.assertIn('failureCode = "insufficient_disk_space"', preparation)
        self.assertIn('mpsState: "unknown"', preparation)
        self.assertIn("clearModelPreparationAttemptArtifacts", preparation)
        self.assertLess(
            preparation.index("clearModelPreparationAttemptArtifacts"),
            preparation.index("dentalPreparationRunning = true"),
        )
        self.assertLess(
            preparation.index("clearModelPreparationAttemptArtifacts"),
            preparation.index("startDentalPreparationTimer()"),
        )
        self.assertIn("dentalPreparationFailed = false", cancellation)
        self.assertIn("state.showsDentalPreparationFailureActions", sheet)
        self.assertIn("エラー情報をコピーして相談フォームを開く", sheet)
        self.assertIn("state.openErrorReportForm()", sheet)

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
        toothseg_progress = body(STATE, "struct ToothSegPreparationProgress")
        toothseg_progress_parser = body(STATE, "func toothSegPreparationProgressFromLog")
        self.assertIn("resumeFromBytes", toothseg_progress)
        self.assertIn("の中断位置から再開", toothseg_progress)
        self.assertIn('payload["resume_from_bytes"]', toothseg_progress_parser)

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

    def test_dicom_series_selection_is_draft_until_confirmed(self):
        sheet = body(VIEWS, "struct DicomSeriesSelectionSheet")
        self.assertIn("state.pendingDicomSeriesID", sheet)
        self.assertIn("state.cancelDicomSeriesSelection()", sheet)
        self.assertNotIn("showDicomSeriesSelection = false", sheet)

        select = body(STATE, "func selectDicomSeries")
        self.assertIn("pendingDicomSeriesID = candidate.id", select)
        self.assertNotIn("selectedDicomSeriesID = candidate.id", select)

        use = body(STATE, "func useSelectedDicomSeries")
        self.assertIn("let pendingDicomSeriesID", use)
        self.assertIn("selectedDicomSeriesID = candidate.id", use)
        self.assertIn("clearInputCTPreview()", use)

        cancel = body(STATE, "func cancelDicomSeriesSelection")
        self.assertIn("pendingDicomSeriesID = nil", cancel)
        self.assertIn("showDicomSeriesSelection = false", cancel)
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
        self.assertIn("相談フォームを開く", result)
        self.assertIn("state.openErrorReportForm()", result)
        self.assertIn("結果表示対象", result)
        self.assertIn("ToothSegを再実行", result)
        self.assertIn("state.canRetryToothSegRefine", result)
        self.assertIn("state.canShowToothSegRefine", result)
        self.assertIn("!state.primaryRunTeethDetected", result)
        self.assertIn("func copySafeErrorInfo", STATE)
        safe = body(STATE, "var safeErrorCopyText")
        self.assertIn("safeRunErrorReportText", safe)
        report_formatter = body(STATE, "func safeRunErrorReportText")
        for field in (
            "app_version",
            "report_schema",
            "feature",
            "os_version",
            "architecture",
            "mps_state",
            "timestamp",
            "error_code",
        ):
            with self.subTest(field=field):
                self.assertIn(field, report_formatter)
        for field in (
            "run_attempt_id",
            "failed_stage",
            "specific_cause",
            "retryable",
            "recovery_hint_code",
            "diagnostic_log_kind",
            "diagnostic_log_reference",
            "backend_version",
            "model_version",
            "runtime_python_version",
            "runtime_torch_version",
            "input_size_bucket",
            "actual_device",
            "fallback_used",
        ):
            with self.subTest(field=field):
                self.assertIn(field, report_formatter)
        self.assertIn("safeErrorFeatureText", safe)
        self.assertNotIn("creationChoice.rawValue", safe)
        feature = body(STATE, "var safeErrorFeatureText")
        self.assertIn("canonicalSafeErrorCode(safeErrorCode)", feature)
        self.assertIn('code.hasPrefix("toothseg_")', feature)
        self.assertIn("ToothSeg高精細化", feature)
        self.assertNotIn("inputURL", safe)
        self.assertNotIn("outputURL", safe)
        self.assertIn("safeDicomErrorCopyLines", safe)
        self.assertIn("safeRunResultFields(from:", report_formatter)
        self.assertIn("safeSystemDiagnosticValue", report_formatter)
        dicom = body(STATE, "var safeDicomErrorCopyLines")
        self.assertIn("series_count", dicom)
        self.assertIn("classification_counts", dicom)
        self.assertIn("possible_causes", dicom)
        self.assertNotIn("stdout_tail", dicom)
        self.assertNotIn("stderr_tail", dicom)
        self.assertNotIn("dicom_dir", dicom)
        self.assertIn("enum ResultOutcome", STATE)
        self.assertIn("state.resultOutcome == .failure", result)
        self.assertIn("state.resultOutcome == .success", result)

        loader = body(STATE, "func loadSafeRunResult")
        self.assertIn("safeRunResultFields(from: payload)", loader)
        self.assertIn("isCurrentSafeRunResultPayload", loader)
        self.assertNotIn("runAttemptID.isEmpty ||", loader)
        self.assertNotIn('payload["safe_reason"] as? String', loader)
        self.assertNotIn('payload["mps_state"] as? String ??', loader)
        normalizer = body(STATE, "func safeRunResultFields")
        self.assertIn("canonicalSafeErrorCode", normalizer)
        self.assertNotIn("safe_reason", normalizer)
        self.assertIn("canonicalDiagnosticTimestamp", normalizer)
        self.assertIn("safeMPSDiagnosticState", normalizer)
        self.assertIn("safeRunAttemptID", normalizer)
        self.assertIn("safeRunDiagnosticToken", normalizer)
        self.assertNotIn("stderr_tail", normalizer)
        self.assertNotIn("stdout_tail", normalizer)
        primary_failure = body(STATE, "private func safePrimaryRunFailureText")
        self.assertIn("totalseg_backend_nonzero_exit", primary_failure)
        self.assertNotIn("runFailureReason", primary_failure)
        refine_failure = body(STATE, "private func toothSegRefineFailureReason")
        self.assertNotIn("runFailureReason", refine_failure)
        ui_error = body(STATE, "private func setSafeError")
        self.assertIn("resetSafeRunDiagnostics()", ui_error)

    def test_tgnet_validation_exposes_only_allowlisted_public_details(self):
        validation = body(STATE, "private func validateIOSTGNetSet")
        sanitizer = body(STATE, "func safeTGNetValidationDetail")
        self.assertIn("safeTGNetValidationDetail(from: result)", validation)
        self.assertNotIn('result?["details"]', validation)
        self.assertNotIn('result?["message"]', validation)
        self.assertNotIn("logURL.path", validation)
        for code in (
            "tgnet_selection_invalid",
            "tgnet_checkpoint_set_incomplete",
            "tgnet_checkpoint_hash_mismatch",
            "tgnet_checkpoint_archive_invalid",
            "tgnet_validation_failed",
        ):
            self.assertIn(f'"{code}"', sanitizer)
        self.assertNotIn("details", sanitizer)
        self.assertNotIn("safe_detail", sanitizer)

    def test_error_report_form_uses_account_optional_google_form_without_uploading_logs(self):
        route = body(STATE, "var errorReportFormURL")
        action = body(STATE, "func openErrorReportForm")
        self.assertIn("https://forms.gle/QFPwF1Pi5C8bmSuw6", route)
        self.assertNotIn("github.com", route)
        self.assertNotIn("resultKind", route)
        self.assertNotIn("safeErrorCode", route)
        self.assertIn("copySafeErrorInfo()", action)
        self.assertIn("openURLInWorkspace(errorReportFormURL)", action)
        self.assertNotIn("logText", action)
        self.assertNotIn("currentLogURL", action)
        self.assertNotIn("URLSession", action)

    def test_root_log_sheet_offers_copy_open_finder_and_close(self):
        root = body(VIEWS, "struct RootView")
        log = body(VIEWS, "struct LogSheetView")
        self.assertIn(".sheet(isPresented: $state.showLog)", root)
        for token in ("ログをコピー", "ログファイルを開く", "Finderで表示", "閉じる"):
            self.assertIn(token, log)
        self.assertIn("詳細ログにはローカルパスや入力ファイル名が含まれる場合があります。", log)
        self.assertIn("相談フォームには貼り付けず", log)
        self.assertIn("エラー情報をコピー", log)
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
            "CommandBuilder.niftiPreviewCommand",
            "seriesKey: candidate.seriesKey",
            "convert_clean_metadata.json",
            "input_preview",
            "inputCTPreviewRequired = true",
            "inputCTPreviewSlices = previewSlices",
            "inputCTPreviewVolumeEmpty = previewVolumeEmpty",
            "inputSource = .nifti",
            "screen = .inputAndCreation",
            "dicom_conversion_cancelled",
            "dicom_conversion_failed",
        ):
            self.assertIn(token, conversion)
        self.assertNotIn("viewer_export_cancelled", conversion)
        self.assertNotIn("viewer_export_failed", conversion)
        self.assertNotIn("CommandBuilder.runCommand", conversion)

    def test_shared_input_shows_dicom_mpr_and_blocks_empty_volume(self):
        shared_input = body(VIEWS, "struct InputAndCreationView")
        for token in (
            "state.inputCTPreviewRequired",
            "選択したCTの簡易プレビュー",
            '("axial", "上から")',
            '("coronal", "正面から")',
            '("sagittal", "横から")',
            "state.inputCTPreviewWarning",
            "診断や治療計画には使用できません",
        ):
            self.assertIn(token, shared_input)

        preflight = body(STATE, "var runPreflightBlockingReason")
        self.assertIn("inputCTPreviewVolumeEmpty", preflight)
        self.assertIn("inputCTPreviewFailed", preflight)
        self.assertIn("空の画像", preflight)

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

    def test_result_exposes_current_flavor_stl_folder_and_generation_state(self):
        result = body(VIEWS, "struct ResultView")
        for token in (
            "state.openSTLFolder()",
            "state.stlFolderButtonTitle",
            "state.canOpenSTLFolder",
            "state.stlGenerationStatusText",
            "state.openSTLGenerationLog()",
            "state.startSTLStatusMonitoring()",
            "state.stopSTLStatusMonitoring()",
        ):
            self.assertIn(token, result)

        open_folder = body(STATE, "func openSTLFolder")
        self.assertIn("expectedSTLDirectoryURL", open_folder)
        self.assertIn("openURLInWorkspace(directory)", open_folder)
        self.assertNotIn("surfacePreviewCommand", open_folder)

        preview_output = body(STATE, "private func expectedSurfacePreviewOutputURL")
        self.assertIn('case .craniofacial:', preview_output)
        self.assertIn('case .toothSeg:', preview_output)
        self.assertIn('"surface_preview/toothseg"', preview_output)

        set_flavor = body(STATE, "private func setResultFlavor")
        self.assertIn("startSTLStatusMonitoring()", set_flavor)

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
        self.assertIn('"acvl_utils_wheel_sha256"', setup_status)
        self.assertIn("fileStatus.st_nlink == 1", PROCESS)
        self.assertIn("専用Python環境を作り直しています", run_setup)
        self.assertIn("managedVenvRefreshDecision", run_setup)
        self.assertIn("safelyRemoveManagedVenv", run_setup)
        self.assertIn("bundledSetupResources", run_setup)
        self.assertIn("pythonRuntimeCanSafelyCreateManagedVenv", run_setup)
        self.assertIn('markBundleCurrent(paths: paths, reason: "setup_completed")', run_setup)
        self.assertLess(run_setup.index("bundledSetupResources"), run_setup.index("safelyRemoveManagedVenv"))
        self.assertLess(run_setup.index("pythonRuntimeCanSafelyCreateManagedVenv"), run_setup.index("safelyRemoveManagedVenv"))
        self.assertLess(run_setup.index("let pythonRC"), run_setup.index("safelyRemoveManagedVenv"))
        self.assertIn("dependency_set_id_changed", PROCESS)
        self.assertIn("installed_bundled_dependency_missing_or_invalid", PROCESS)

    def test_setup_pip_bootstrap_is_forced_offline(self):
        launch_environment = body(COMMANDS, "static func launchEnvironment")
        bootstrap = body(COMMANDS, "static func bootstrapInstallCommand")

        self.assertIn('env["PIP_NO_INDEX"] = "1"', launch_environment)
        self.assertIn('"--no-index"', bootstrap)
        self.assertIn('"--no-deps"', bootstrap)
        self.assertIn(
            'onProgress(.installWheel, "同梱アプリ本体を導入しています。")',
            PROCESS,
        )

    def test_public_setup_copy_says_only_model_weights_use_network(self):
        surfaces = {
            relative: (ROOT / relative).read_text(encoding="utf-8")
            for relative in (
                "docs/USER_MANUAL_JA.md",
                "docs/34_ALPHA_DISTRIBUTION_SUPPORT_CARD.md",
                "scripts/build_mac_dmg.sh",
            )
        }
        for relative, surface in surfaces.items():
            with self.subTest(relative=relative):
                self.assertIn("Python依存はアプリに同梱", surface)
                self.assertIn(
                    "セットアップ中にネットワークを使用するのはモデルweightの取得だけ",
                    surface,
                )
                self.assertNotIn("Pythonパッケージとモデルweight取得", surface)
                self.assertNotIn("Python依存とモデルweightを取得", surface)

    def test_wheel_resync_uses_the_same_cross_process_setup_lock(self):
        resync = body(PROCESS, "static func resyncWheel")
        self.assertIn("NativeSetupFileLock.acquire", resync)
        self.assertIn("defer { setupFileLock.release() }", resync)
        self.assertIn('reason: "setup_busy"', resync)
        self.assertIn("return 75", resync)
        self.assertLess(
            resync.index("NativeSetupFileLock.acquire"),
            resync.index("bootstrapInstallCommand"),
        )

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
        self.assertIn('"--run-attempt-id"', run_command)
        self.assertIn("runAttemptID", run_command)
        self.assertIn("backend.cliValue", run_command)
        self.assertIn("mode.task", run_command)
        start_run = body(STATE, "func startRun")
        self.assertIn("runAttemptID = UUID().uuidString.lowercased()", start_run)
        self.assertIn("runAttemptID: runAttemptID", start_run)

    def test_toothseg_refine_command_uses_fixed_12mm_margin(self):
        refine = body(COMMANDS, "static func toothSegRefineCommand")
        start_refine = body(STATE, "func startToothSegRefineRun")
        self.assertIn('"--toothseg-refine"', refine)
        self.assertIn('"--teeth-crop-margin-mm"', refine)
        self.assertIn("toothsegRefineMarginMM", refine)
        self.assertIn('let toothsegRefineMarginMM = "12"', COMMANDS)
        self.assertIn('"--teeth-craniofacial-case"', refine)
        self.assertIn("craniofacialCase.path", refine)
        self.assertIn('"--run-attempt-id"', refine)
        self.assertIn("runAttemptID", refine)
        self.assertNotIn('"--teeth-robust-craniofacial-preflight"', refine)
        self.assertIn("craniofacialCase: outputURL", start_refine)
        self.assertIn("runAttemptID: runAttemptID", start_refine)

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
        self.assertLess(failure.index("totalseg_setup_weights_missing_or_invalid"), marker_index)
        self.assertIn("セットアップをやり直してください", failure)
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
