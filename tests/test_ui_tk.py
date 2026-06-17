from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from totalsegmentator_wrapper_mac.case_summary import format_case_summary_markdown, format_case_summary_text
from totalsegmentator_wrapper_mac.ui_tk import (
    RUN_MODE_ARCH_PREVIEW,
    RUN_MODE_INDIVIDUAL_TEETH,
    build_backend_env,
    build_run_command,
    case_surface_preview,
    default_totalseg_bin,
    format_run_progress,
    run_progress_from_log,
    run_mode_to_task,
)


ROOT = Path(__file__).resolve().parents[1]


class TkUiTests(unittest.TestCase):
    def test_build_run_command(self) -> None:
        command = build_run_command(
            python_executable="/venv/bin/python",
            input_path="/cases/input.nii.gz",
            output_dir="/cases/out",
            run_mode=RUN_MODE_ARCH_PREVIEW,
            device="mps",
            totalseg_bin="/venv/bin/TotalSegmentator",
            no_copy_input=True,
        )

        self.assertEqual(command[:4], ["/venv/bin/python", "-m", "totalsegmentator_wrapper_mac", "run"])
        self.assertIn("--input", command)
        self.assertIn("/cases/input.nii.gz", command)
        self.assertIn("--device", command)
        self.assertIn("mps", command)
        self.assertEqual(command[-1], "--no-copy-input")

    def test_build_run_command_adds_experimental_teeth_for_individual_teeth_mode(self) -> None:
        base_kwargs = {
            "python_executable": "/venv/bin/python",
            "input_path": "/cases/input.nii.gz",
            "output_dir": "/cases/out",
            "device": "mps",
            "totalseg_bin": "/venv/bin/TotalSegmentator",
            "no_copy_input": True,
        }

        default_command = build_run_command(**base_kwargs, run_mode=RUN_MODE_ARCH_PREVIEW)
        experimental_command = build_run_command(**base_kwargs, run_mode=RUN_MODE_INDIVIDUAL_TEETH)

        self.assertNotIn("--experimental-teeth", default_command)
        self.assertIn("--experimental-teeth", experimental_command)
        self.assertIn("teeth", experimental_command)
        self.assertIn("--teeth-crop-margin-mm", experimental_command)
        self.assertIn("5.0", experimental_command)

    def test_run_mode_to_task_hides_backend_task_names_from_main_ui(self) -> None:
        self.assertEqual(run_mode_to_task(RUN_MODE_ARCH_PREVIEW), "craniofacial_structures")
        self.assertEqual(run_mode_to_task(RUN_MODE_INDIVIDUAL_TEETH), "teeth")

    def test_run_progress_from_log_uses_last_tqdm_event(self) -> None:
        progress = run_progress_from_log(
            'RUN_PROGRESS {"percent": 13, "step": 31, "total": 231}\n'
            'RUN_PROGRESS {"percent": 20, "stage": "Resampling", "step": 46, "total": 231}\n'
        )

        self.assertEqual(progress, {"percent": 20, "stage": "Resampling", "step": 46, "total": 231})
        assert progress is not None
        self.assertEqual(format_run_progress(progress), "プレビュー作成中: Resampling 46/231 (20%)")

    def test_run_progress_formats_section_100_percent_as_transition(self) -> None:
        progress = {"percent": 100, "stage": "Saving", "step": 231, "total": 231}

        self.assertEqual(format_run_progress(progress), "プレビュー作成中: Saving 完了。次の処理へ進んでいます...")

    def test_run_progress_bar_returns_to_indeterminate_on_section_100_percent(self) -> None:
        text = (ROOT / "src" / "totalsegmentator_wrapper_mac" / "ui_tk.py").read_text(encoding="utf-8")

        self.assertIn("def _set_run_progress_indeterminate", text)
        self.assertIn("percent == 100 and self.process is not None", text)
        self.assertIn("bar.start(12)", text)

    def test_input_file_dialog_avoids_crashing_macos_filetype_filter(self) -> None:
        text = (ROOT / "src" / "totalsegmentator_wrapper_mac" / "ui_tk.py").read_text(encoding="utf-8")

        self.assertIn('filedialog.askopenfilename(title="NIfTI入力を選択")', text)
        self.assertNotIn("*.nii *.nii.gz", text)

    def test_case_surface_preview_detects_html_only_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            self.assertIsNone(case_surface_preview(root))
            preview = root / "surface_preview" / "index.html"
            preview.parent.mkdir()
            preview.write_text("<!doctype html>", encoding="utf-8")

            self.assertEqual(case_surface_preview(root), preview)

    def test_main_ui_contains_japanese_wizard_labels(self) -> None:
        text = (ROOT / "src" / "totalsegmentator_wrapper_mac" / "ui_tk.py").read_text(encoding="utf-8")

        for label in (
            "1 目的",
            "2 入力",
            "3 実行",
            "4 結果",
            "Sampleで流れを体験する",
            "自分のCT/NIfTIを開く",
            "Sample 1の3Dプレビューを開く",
            "Sample 1を入力に使う",
            "100秒前後",
            "NIfTIファイルを選ぶ",
            "DICOMフォルダを確認する",
            "詳細ログを表示",
            "run_progress_from_log",
            "歯列と顎骨をまとめて表示",
            "歯を1本ずつ分けて表示（ベータ）",
            "実行開始",
            "結果フォルダを開く",
            "3Dプレビューを開く",
            "結果の要約を表示",
            "DICOM確認サマリー",
        ):
            self.assertIn(label, text)

        self.assertIn("case_surface_preview", text)
        self.assertIn("grid_remove", text)
        self.assertIn("_show_start_screen", text)
        self.assertIn("_show_sample_tutorial", text)
        self.assertIn("_run_sample", text)
        self.assertIn("log_frame.grid_remove()", text)
        self.assertIn("DICOMフォルダが選択されています。プレビュー作成ではなく撮影データの確認", text)
        self.assertNotIn("個別に歯をsegmentationする(beta)", text)
        self.assertNotIn("summaryを表示", text)
        self.assertIn("GUI_EXPERIMENTAL_TEETH_MARGIN_MM = 5.0", text)
        self.assertIn("RUN_PROGRESS ", text)
        self.assertIn("ttk.Progressbar", text)
        self.assertNotIn("ttk.Label(run_frame, text=\"タスク\")", text)
        self.assertNotIn("experimental_teeth_var", text)

    def test_build_backend_env_uses_workspace_paths_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "artifacts" / "totalseg_home").mkdir(parents=True)
            (root / "artifacts" / "totalseg_weights").mkdir(parents=True)

            env = build_backend_env(root)

            resolved = root.resolve()
            self.assertIn(str(resolved / "src"), env["PYTHONPATH"].split(os.pathsep))
            self.assertEqual(
                env["TOTALSEG_HOME_DIR"], str(resolved / "artifacts" / "totalseg_home")
            )
            self.assertEqual(
                env["TOTALSEG_WEIGHTS_PATH"], str(resolved / "artifacts" / "totalseg_weights")
            )
            self.assertEqual(env["MPLCONFIGDIR"], str(resolved / "artifacts" / "matplotlib_cache"))
            self.assertEqual(env["XDG_CACHE_HOME"], str(resolved / "artifacts" / "cache"))

    def test_default_totalseg_bin_uses_unresolved_venv_executable_neighbor(self) -> None:
        import totalsegmentator_wrapper_mac.ui_tk as ui_tk

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            venv_bin = root / "env" / "bin"
            app_bin = root / "app" / "python" / "bin"
            venv_bin.mkdir(parents=True)
            app_bin.mkdir(parents=True)
            (venv_bin / "TotalSegmentator").write_text("#!/bin/sh\n", encoding="utf-8")
            original_executable = ui_tk.sys.executable
            try:
                ui_tk.sys.executable = str(venv_bin / "python")
                self.assertEqual(default_totalseg_bin(), str(venv_bin / "TotalSegmentator"))
            finally:
                ui_tk.sys.executable = original_executable

    def test_backend_env_prepends_current_python_bin_to_path(self) -> None:
        env = build_backend_env(ROOT)
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(Path(os.sys.executable).parent))

    def test_format_case_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            (logs / "benchmark.json").write_text(
                json.dumps(
                    {
                        "run": {
                            "status": "success",
                            "task": "craniofacial_structures",
                            "requested_device": "mps",
                            "actual_device": "mps",
                            "elapsed_seconds": 12.3,
                        }
                    }
                ),
                encoding="utf-8",
            )
            (logs / "mask_stats.json").write_text(
                json.dumps(
                    {
                        "masks": [
                            {
                                "name": "mandible.nii.gz",
                                "status": "ok",
                                "nonzero_voxels": 123,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            summary = format_case_summary_text(root)

            self.assertIn("actual_device: mps", summary)
            self.assertIn("mandible.nii.gz: 123 nonzero voxels", summary)

            markdown = format_case_summary_markdown(root)
            self.assertIn("| Actual device | `mps` |", markdown)
            self.assertIn("| `mandible.nii.gz` | 123 |", markdown)


if __name__ == "__main__":
    unittest.main()
