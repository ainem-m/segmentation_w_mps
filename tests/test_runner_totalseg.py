from __future__ import annotations

import os
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from totalsegmentator_wrapper_mac.runner_totalseg import (
    RUN_PROGRESS_PREFIX,
    TEETH_UNSUPPORTED_REASON,
    _run_command_streamed,
    parse_tqdm_progress,
    resolve_totalseg_executable,
    run_totalsegmentator,
    sanitized_command,
)


class RunnerTests(unittest.TestCase):
    def test_sanitized_command_hides_full_paths(self) -> None:
        command = ["TotalSegmentator", "-i", "/secret/case/source.nii.gz", "-o", "/tmp/out", "-ta", "x"]
        safe = sanitized_command(command, Path("/secret/case/source.nii.gz"), Path("/tmp/out"))
        self.assertEqual(safe, ["TotalSegmentator", "-i", "source.nii.gz", "-o", "<output:out>", "-ta", "x"])

    def test_resolve_totalseg_executable_prefers_current_python_bin_neighbor(self) -> None:
        import totalsegmentator_wrapper_mac.runner_totalseg as runner

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "env" / "bin"
            bin_dir.mkdir(parents=True)
            runner_path = bin_dir / "TotalSegmentator"
            runner_path.write_text("#!/bin/sh\n", encoding="utf-8")
            original_executable = runner.sys.executable
            try:
                runner.sys.executable = str(bin_dir / "python")
                self.assertEqual(resolve_totalseg_executable("TotalSegmentator"), str(runner_path))
            finally:
                runner.sys.executable = original_executable

    def test_parse_tqdm_progress_handles_count_and_percent(self) -> None:
        progress = parse_tqdm_progress("Resampling:  13%|#3        | 31/231 [00:10<01:20, 2.4it/s]")

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertEqual(progress["step"], 31)
        self.assertEqual(progress["total"], 231)
        self.assertEqual(progress["percent"], 13)
        self.assertEqual(progress["stage"], "Resampling")

    def test_parse_tqdm_progress_keeps_section_complete_distinct_from_run_complete(self) -> None:
        progress = parse_tqdm_progress("Saving: 100%|##########| 231/231 [00:10<00:00, 2.4it/s]")

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertEqual(progress["step"], 231)
        self.assertEqual(progress["total"], 231)
        self.assertEqual(progress["percent"], 100)
        self.assertEqual(progress["stage"], "Saving")

    def test_parse_progress_handles_plain_totalseg_phase_lines(self) -> None:
        progress = parse_tqdm_progress("Saving segmentations...")

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertIsNone(progress["step"])
        self.assertIsNone(progress["total"])
        self.assertIsNone(progress["percent"])
        self.assertEqual(progress["stage"], "Saving segmentations")
        self.assertTrue(progress["phase_only"])

    def test_streamed_command_records_carriage_return_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake = tmp_path / "fake_tqdm.py"
            fake.write_text(
                "import sys, time\n"
                "sys.stderr.write('Predicting s01:\\n')\n"
                "sys.stderr.flush()\n"
                "for step in (1, 2, 3):\n"
                "    sys.stderr.write(f' {step * 33}%|### | {step}/3 [00:0{step}<00:00]\\r')\n"
                "    sys.stderr.flush()\n"
                "    time.sleep(0.01)\n"
                "sys.stderr.write('\\n')\n",
                encoding="utf-8",
            )
            log_path = tmp_path / "run.log"

            rc, _elapsed, _stdout, stderr = _run_command_streamed(
                command=[sys.executable, str(fake)],
                env=os.environ.copy(),
                log_path=log_path,
                safe_command=["python", "fake_tqdm.py"],
            )

            self.assertEqual(rc, 0)
            self.assertIn("3/3", stderr)
            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn(RUN_PROGRESS_PREFIX, log_text)
            self.assertIn('"stage": "Predicting s01"', log_text)
            self.assertIn('"step": 3', log_text)
            self.assertIn('"total": 3', log_text)

    def test_run_totalsegmentator_with_fake_binary_writes_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "sample.nii.gz"
            input_path.write_text("fake nifti", encoding="utf-8")
            fake_bin = tmp_path / "fake_totalseg.py"
            fake_bin.write_text(
                "#!/usr/bin/env python3\n"
                "import argparse\n"
                "from pathlib import Path\n"
                "parser=argparse.ArgumentParser()\n"
                "parser.add_argument('-i')\n"
                "parser.add_argument('-o')\n"
                "parser.add_argument('-ta')\n"
                "parser.add_argument('--device')\n"
                "args=parser.parse_args()\n"
                "out=Path(args.o); out.mkdir(parents=True, exist_ok=True)\n"
                "(out/'mandible.nii.gz').write_text('mask')\n"
                "print(f'task={args.ta} device={args.device}')\n",
                encoding="utf-8",
            )
            fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IXUSR)

            result = run_totalsegmentator(
                input_path=input_path,
                output_root=tmp_path / "case",
                task="craniofacial_structures",
                requested_device="mps",
                totalseg_bin=str(fake_bin),
                copy_input=True,
                skip_device_check=True,
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.actual_device, "mps")
            self.assertTrue((tmp_path / "case" / "logs" / "benchmark.json").exists())
            self.assertTrue((tmp_path / "case" / "logs" / "environment.json").exists())
            self.assertTrue((tmp_path / "case" / "logs" / "mask_stats.json").exists())
            self.assertTrue((tmp_path / "case" / "logs" / "run.log").exists())
            self.assertFalse((tmp_path / "case" / "slicer").exists())
            self.assertTrue((tmp_path / "case" / "README_OUTPUT.md").exists())
            self.assertTrue(
                (tmp_path / "case" / "segmentations" / "raw_totalseg" / "mandible.nii.gz").exists()
            )
            run_log = (tmp_path / "case" / "logs" / "run.log").read_text(encoding="utf-8")
            self.assertIn("-i sample.nii.gz", run_log)
            self.assertNotIn(str(input_path.parent), run_log)
            mask_stats = json.loads(
                (tmp_path / "case" / "logs" / "mask_stats.json").read_text(encoding="utf-8")
            )
            self.assertEqual(mask_stats["mask_count"], 1)
            self.assertEqual(mask_stats["masks"][0]["name"], "mandible.nii.gz")
            output_readme = (tmp_path / "case" / "README_OUTPUT.md").read_text(encoding="utf-8")
            self.assertIn("non-clinical research/education preview", output_readme)
            files_section = output_readme.split("## Files", 1)[1].split("## Segmentation Masks", 1)[0]
            self.assertNotIn("surface preview: surface_preview/index.html", files_section)
            self.assertIn("3Dプレビューを再生成", output_readme)
            self.assertIn("logs/run.log", output_readme)
            self.assertIn("結果フォルダ", output_readme)
            benchmark = json.loads(
                (tmp_path / "case" / "logs" / "benchmark.json").read_text(encoding="utf-8")
            )
            self.assertFalse(benchmark["run"]["robust_crop"])

    def test_run_totalsegmentator_with_robust_crop_passes_totalseg_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "sample.nii.gz"
            input_path.write_text("fake nifti", encoding="utf-8")
            fake_bin = tmp_path / "fake_totalseg.py"
            fake_bin.write_text(
                "#!/usr/bin/env python3\n"
                "import argparse\n"
                "from pathlib import Path\n"
                "parser=argparse.ArgumentParser()\n"
                "parser.add_argument('-i')\n"
                "parser.add_argument('-o')\n"
                "parser.add_argument('-ta')\n"
                "parser.add_argument('--device')\n"
                "parser.add_argument('--robust_crop', action='store_true')\n"
                "args=parser.parse_args()\n"
                "out=Path(args.o); out.mkdir(parents=True, exist_ok=True)\n"
                "(out/'mandible.nii.gz').write_text('mask')\n"
                "(out/'robust_crop.txt').write_text(str(args.robust_crop))\n"
                "print(f'robust_crop={args.robust_crop}')\n",
                encoding="utf-8",
            )
            fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IXUSR)

            result = run_totalsegmentator(
                input_path=input_path,
                output_root=tmp_path / "case",
                task="craniofacial_structures",
                requested_device="mps",
                totalseg_bin=str(fake_bin),
                copy_input=False,
                skip_device_check=True,
                robust_crop=True,
            )

            self.assertEqual(result.status, "success")
            raw_dir = tmp_path / "case" / "segmentations" / "raw_totalseg"
            self.assertEqual((raw_dir / "robust_crop.txt").read_text(encoding="utf-8"), "True")
            run_log = (tmp_path / "case" / "logs" / "run.log").read_text(encoding="utf-8")
            self.assertIn("--robust_crop", run_log)
            benchmark = json.loads(
                (tmp_path / "case" / "logs" / "benchmark.json").read_text(encoding="utf-8")
            )
            self.assertTrue(benchmark["run"]["robust_crop"])

    def test_teeth_task_fails_with_clear_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "sample.nii.gz"
            input_path.write_text("fake nifti", encoding="utf-8")

            result = run_totalsegmentator(
                input_path=input_path,
                output_root=tmp_path / "case",
                task="teeth",
                requested_device="mps",
                totalseg_bin="unused",
                copy_input=True,
                skip_device_check=True,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.returncode, 2)
            self.assertIn("teeth is blocked", result.stderr_tail)
            run_log = (tmp_path / "case" / "logs" / "run.log").read_text(encoding="utf-8")
            self.assertIn(TEETH_UNSUPPORTED_REASON, run_log)
            benchmark = json.loads(
                (tmp_path / "case" / "logs" / "benchmark.json").read_text(encoding="utf-8")
            )
            self.assertEqual(benchmark["run"]["status"], "failed")
            self.assertFalse(benchmark["run"]["robust_crop"])

    def test_robust_crop_is_rejected_for_teeth_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "sample.nii.gz"
            input_path.write_text("fake nifti", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "craniofacial_structures"):
                run_totalsegmentator(
                    input_path=input_path,
                    output_root=tmp_path / "case",
                    task="teeth",
                    requested_device="mps",
                    totalseg_bin="unused",
                    copy_input=False,
                    skip_device_check=True,
                    robust_crop=True,
                    experimental_teeth=True,
                )


if __name__ == "__main__":
    unittest.main()
