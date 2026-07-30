from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "native" / "windows" / "CoordinatorShell"
SUPERVISOR = ROOT / "native" / "windows" / "ProcessSupervisor" / "Program.cs"


class WindowsWpfContractTests(unittest.TestCase):
    def test_project_is_one_package_free_wpf_shell(self) -> None:
        project = (SHELL / "CoordinatorShell.csproj").read_text(encoding="utf-8")
        manifest = (SHELL / "app.manifest").read_text(encoding="utf-8")

        self.assertIn("<UseWPF>true</UseWPF>", project)
        self.assertIn("<TargetFramework>net10.0-windows</TargetFramework>", project)
        self.assertIn("<RuntimeIdentifier>win-x64</RuntimeIdentifier>", project)
        self.assertNotIn("PackageReference", project)
        self.assertIn('level="asInvoker"', manifest)
        self.assertIn("PerMonitorV2", manifest)
        self.assertIn("longPathAware", manifest)

    def test_shell_uses_supervisor_protocol_v1_and_strict_cuda_zero(self) -> None:
        session = (SHELL / "CoordinatorSession.cs").read_text(encoding="utf-8")

        self.assertIn('FileName = _configuration.SupervisorPath', session)
        self.assertIn('"--interactive-cancel"', session)
        self.assertIn('"12000"', session)
        self.assertIn("StandardOutputEncoding = new UTF8Encoding(false)", session)
        self.assertIn('protocol_version = 1', session)
        self.assertIn('operation = "run_nifti_totalsegmentator"', session)
        self.assertIn('mode = "cuda_required"', session)
        self.assertIn('index = 0', session)
        self.assertNotIn("dicom", session.lower())
        self.assertNotIn('mode = "auto"', session)
        self.assertNotIn('mode = "cpu"', session)
        self.assertIn('await process.StandardInput.WriteLineAsync("cancel")', session)
        self.assertIn(
            'terminal.EventName == "operation_cancelled"',
            session,
        )
        self.assertIn(
            '"host_cancellation_verification_failed"',
            session,
        )
        shell = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(SHELL.glob("MainWindow*.cs"))
        )
        self.assertIn("RunEvidenceCancelSampleAsync", shell)
        self.assertIn("await RequestStopAsync()", shell)

    def test_dicom_intake_is_clean_only_private_and_boundary_checked(self) -> None:
        session_path = SHELL / "DicomIntakeSession.cs"
        self.assertTrue(session_path.is_file())
        intake = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(SHELL.glob("Dicom*.cs"))
        )
        shell = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(SHELL.glob("MainWindow*.cs"))
        )

        self.assertIn('"audit"', intake)
        self.assertIn('"convert-clean"', intake)
        self.assertIn('"--series-key"', intake)
        self.assertNotIn('"--series-number"', intake)
        self.assertIn('"series_instance_uid"', intake)
        self.assertIn("candidate.NativeSeriesKey", intake)
        self.assertIn("public string DisplayTitle", intake)
        self.assertIn("public string DisplayDetail", intake)
        self.assertIn(
            'new DicomCleanCandidate(\n'
            '                        "preview-1"',
            shell,
        )
        self.assertIn('"original_ct_geometry_ok"', intake)
        self.assertIn('"convert_clean"', intake)
        self.assertIn('"requires_external_tool"', intake)
        for unsupported_command in (
            '"prepare-rescue"',
            '"export-rescue-stack"',
            '"prepare-viewer-export"',
        ):
            with self.subTest(unsupported_command=unsupported_command):
                self.assertNotIn(unsupported_command, intake)
        self.assertNotIn("CoordinatorSession", intake)
        self.assertNotIn('"run_nifti_totalsegmentator"', intake)

        self.assertIn("DrainAsync(process.StandardOutput)", intake)
        self.assertIn("DrainAsync(process.StandardError)", intake)
        self.assertIn("reader.ReadAsync", intake)
        self.assertNotIn("StandardOutput", shell)
        self.assertNotIn("StandardError", shell)
        self.assertNotRegex(shell.lower(), r"dicom.{0,80}(stdout|stderr)")
        self.assertNotRegex(shell.lower(), r"(stdout|stderr).{0,80}dicom")
        self.assertIn("_dicomStopRequested", shell)
        self.assertIn('"dicom_audit_cancelled"', shell)
        self.assertIn('"dicom_conversion_cancelled"', shell)
        self.assertIn(
            "jobBecameEmptyWithoutForcedCleanup",
            intake,
        )

        self.assertIn("convert_clean_metadata.json", intake)
        self.assertIn('"product_boundary"', intake)
        self.assertIn('"segmentation_started"', intake)
        self.assertIn('"secondary_capture_rescue"', intake)
        self.assertRegex(
            intake,
            r"(Count|Length)\s*!=\s*1|\.Single(?:OrDefault)?\(",
        )
        self.assertRegex(
            intake,
            r"FileInfo\s*\(|new\s+FileInfo|\.Length\s*(?:<=|==)\s*0",
        )
        self.assertIn("Path.GetFullPath", intake)
        self.assertRegex(
            intake,
            r"Path\.GetRelativePath|StartsWith\s*\(",
        )

    def test_manual_flow_and_local_preview_remain_explicit(self) -> None:
        xaml = (SHELL / "MainWindow.xaml").read_text(encoding="utf-8")
        code = (SHELL / "MainWindow.xaml.cs").read_text(encoding="utf-8")

        automation_names = (
            "準備を始める",
            "Sampleから始める",
            "手元のCTデータを使う",
            "手元のCTを選ぶ",
            "NIfTIファイルを選ぶ",
            "DICOMフォルダを選ぶ",
            "使用する撮影を変更",
            "この撮影を使う",
            "撮影選択を閉じる",
            "Sampleで3Dプレビューを作る",
            "停止",
            "3Dプレビューを開く",
            "結果フォルダを開く",
            "エラー情報をコピー",
            "詳細情報を見る",
            "入力と作成内容へ戻る",
            "最初に戻る",
        )
        visible_copy = (
            "NIfTI形式のCTファイルまたはDICOM撮影フォルダを選びます。",
            "NIfTIファイルを選ぶ",
            "DICOMフォルダを選ぶ",
            "使用する撮影を変更",
            "最初の候補を選択しています。別の撮影を使う場合だけ変更してください。",
            "この撮影を使う",
            "閉じる",
        )
        for label in (*automation_names, *visible_copy):
            with self.subTest(label=label):
                self.assertIn(label, xaml)
        root = ET.fromstring(xaml)
        presentation = "http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        automation = (
            "clr-namespace:System.Windows.Automation;assembly=PresentationCore"
        )
        automation_name = f"{{{automation}}}AutomationProperties.Name"
        buttons = root.findall(f".//{{{presentation}}}Button")
        self.assertEqual(len(buttons), 17)
        self.assertTrue(all(button.get("Click") for button in buttons))
        self.assertTrue(all(button.get(automation_name) for button in buttons))
        self.assertEqual(
            {button.get(automation_name) for button in buttons},
            set(automation_names),
        )
        for dynamic_label in (
            "別のCTを選ぶ",
            "このCTで3Dプレビューを作る",
            "詳細情報を閉じる",
            "停止要求済み。終了処理中です。",
            "停止情報をコピー",
        ):
            with self.subTest(dynamic_label=dynamic_label):
                self.assertIn(dynamic_label, code)
        self.assertIn('"status=cancelled"', code)
        self.assertIn('"reason_code=', code)
        self.assertNotIn(
            '"error_code={_lastResult.ErrorCode ?? "unknown"}"',
            code.split('if (_lastResult.TerminalEvent == "operation_cancelled")')[1]
            .split("return;", 1)[0],
        )
        self.assertIn('"surface_preview"', code)
        self.assertIn('"index.html"', code)
        self.assertNotIn("WebView", xaml + code)

    def test_runtime_gate_requires_privacy_config_and_cached_models(self) -> None:
        configuration = (SHELL / "ShellConfiguration.cs").read_text(
            encoding="utf-8"
        )

        self.assertIn('"send_usage_stats"', configuration)
        self.assertIn("JsonValueKind.False", configuration)
        self.assertIn('"Dataset115_mandible"', configuration)
        self.assertIn(
            '"Dataset297_TotalSegmentator_total_3mm_1559subj"',
            configuration,
        )
        self.assertIn('"checkpoint_final.pth"', configuration)
        self.assertIn("RecoveryMessage", configuration)
        self.assertIn("failures.Distinct(StringComparer.Ordinal)", configuration)
        self.assertIn('"dicom_normalizer_path"', configuration)
        self.assertIn('"dcm2niix_path"', configuration)
        self.assertIn("CheckDicomRuntime", configuration)
        for safe_component in (
            "Windowsの処理管理機能",
            "3Dプレビュー作成機能",
            "同梱済みの実行環境",
            "同梱Sample 1",
            "同梱済みのモデル",
        ):
            with self.subTest(safe_component=safe_component):
                self.assertIn(safe_component, configuration)

    def test_runtime_ui_contract_covers_all_buttons_and_dynamic_labels(self) -> None:
        app = (SHELL / "App.xaml.cs").read_text(encoding="utf-8")
        code = (SHELL / "MainWindow.xaml.cs").read_text(encoding="utf-8")

        self.assertIn("expectedNames.Count", code)
        self.assertIn("dynamicLabelsPassed", code)
        self.assertIn("dynamic_labels = ui.DynamicLabels", app)
        self.assertIn("button_count = ui.ButtonCount", app)

    def test_interactive_cancel_records_typed_terminal_and_exit_code(self) -> None:
        supervisor = SUPERVISOR.read_text(encoding="utf-8")

        self.assertIn("terminalReached.Task", supervisor)
        self.assertIn(
            "Console.OutputEncoding = new UTF8Encoding(false)",
            supervisor,
        )
        self.assertIn('terminalEvent == "operation_cancelled"', supervisor)
        self.assertIn("terminalEventCount == 1", supervisor)
        self.assertIn("cancelCoordinatorExitCode == 3", supervisor)
        self.assertIn('cancel_trigger = interactiveCancel', supervisor)
        self.assertIn("rootExitWait.Elapsed >= grace", supervisor)
        self.assertIn("root_exit_timed_out = rootExitTimedOut", supervisor)


if __name__ == "__main__":
    unittest.main()
