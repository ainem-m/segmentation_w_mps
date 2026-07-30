from __future__ import annotations

import unittest
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
        self.assertIn('mode = "cuda_required"', session)
        self.assertIn('index = 0', session)
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
        shell = (SHELL / "MainWindow.xaml.cs").read_text(encoding="utf-8")
        self.assertIn("RunEvidenceCancelSampleAsync", shell)
        self.assertIn("await RequestStopAsync()", shell)

    def test_manual_flow_and_local_preview_remain_explicit(self) -> None:
        xaml = (SHELL / "MainWindow.xaml").read_text(encoding="utf-8")
        code = (SHELL / "MainWindow.xaml.cs").read_text(encoding="utf-8")

        for label in ("目的", "入力", "実行", "結果", "Sampleから始める", "停止"):
            with self.subTest(label=label):
                self.assertIn(label, xaml)
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
