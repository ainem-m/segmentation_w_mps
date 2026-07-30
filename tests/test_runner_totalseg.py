from __future__ import annotations

import os
import json
import io
import stat
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from totalsegmentator_wrapper_mac.cli import main as cli_main
from totalsegmentator_wrapper_mac.coordinator_protocol import OperationCancelled

from totalsegmentator_wrapper_mac.runner_totalseg import (
    RUN_PROGRESS_PREFIX,
    RUN_STAGE_LAYOUTS,
    RUN_STAGE_PREFIX,
    TEETH_UNSUPPORTED_REASON,
    _emit_run_stage,
    _run_command_streamed,
    _teeth_detected_from_mask_stats,
    executable_command,
    parse_tqdm_progress,
    resolve_totalseg_executable,
    run_toothseg_refine,
    run_totalsegmentator,
    sanitized_command,
    totalseg_device_argument,
)


class RunnerTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows-only script launch behavior")
    def test_python_runner_uses_current_interpreter_on_windows(self) -> None:
        command = executable_command(r"C:\runtime\fake_totalseg.py")
        self.assertEqual(command, [sys.executable, r"C:\runtime\fake_totalseg.py"])
        self.assertEqual(
            sanitized_command(
                [*command, "-i", r"C:\private\input.nii.gz"],
                Path(r"C:\private\input.nii.gz"),
                Path(r"C:\private\output"),
            ),
            [Path(sys.executable).name, "fake_totalseg.py", "-i", "input.nii.gz"],
        )

    def test_totalseg_cuda_device_uses_upstream_gpu_index_syntax(self) -> None:
        self.assertEqual(totalseg_device_argument("cuda:0"), "gpu:0")
        self.assertEqual(totalseg_device_argument("cuda:7"), "gpu:7")
        self.assertEqual(totalseg_device_argument("cpu"), "cpu")
        self.assertEqual(totalseg_device_argument("mps"), "mps")

    def test_teeth_detection_uses_nonempty_label_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stats_path = Path(tmp) / "mask_stats.json"
            stats_path.write_text(
                json.dumps(
                    {
                        "masks": [
                            {"name": "upper_teeth.nii.gz", "label": "upper_teeth", "nonzero_voxels": 42},
                            {"name": "lower_teeth.nii.gz", "label": "lower_teeth", "nonzero_voxels": 0},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(_teeth_detected_from_mask_stats(stats_path))

    def test_run_stage_layouts_match_the_fixed_route_contract(self) -> None:
        self.assertEqual([len(stages) for stages in RUN_STAGE_LAYOUTS.values()], [4, 4, 6, 5])
        self.assertEqual(
            [stage_id for stage_id, _label in RUN_STAGE_LAYOUTS["toothseg_refine"]],
            ["roi", "semantic", "instance", "restore", "preview"],
        )

    def test_run_stage_is_saved_and_mirrored_to_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "run.log"
            stderr = io.StringIO()
            events: list[tuple[str, dict[str, object]]] = []
            with redirect_stderr(stderr):
                event = _emit_run_stage(
                    "toothseg_refine",
                    3,
                    log_path=log_path,
                    reset_log=True,
                    event_sink=lambda name, payload: events.append((name, payload)),
                )

            self.assertEqual(event["stage_id"], "instance")
            self.assertEqual(event["index"], 3)
            self.assertEqual(event["total"], 5)
            self.assertEqual(stderr.getvalue(), log_path.read_text(encoding="utf-8"))
            self.assertTrue(stderr.getvalue().startswith(RUN_STAGE_PREFIX))
            self.assertEqual(events, [("phase_started", event)])

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
        self.assertEqual(progress["eta_seconds"], 80)

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

    def test_parse_tqdm_progress_handles_download_with_sizes(self) -> None:
        progress = parse_tqdm_progress(
            "Downloading weights: 100%|##########| 12.5MB/12.5MB [00:10<00:00,  1.2MB/s]"
        )

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertEqual(progress["step"], 13107200)
        self.assertEqual(progress["total"], 13107200)
        self.assertEqual(progress["percent"], 100)
        self.assertEqual(progress["stage"], "Downloading weights")

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
            events: list[tuple[str, dict[str, object]]] = []

            rc, _elapsed, _stdout, stderr = _run_command_streamed(
                command=[sys.executable, str(fake)],
                env=os.environ.copy(),
                log_path=log_path,
                safe_command=["python", "fake_tqdm.py"],
                event_sink=lambda name, payload: events.append((name, payload)),
            )

            self.assertEqual(rc, 0)
            self.assertIn("3/3", stderr)
            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn(RUN_PROGRESS_PREFIX, log_text)
            self.assertIn('"stage": "Predicting s01"', log_text)
            self.assertIn('"step": 3', log_text)
            self.assertIn('"total": 3', log_text)
            progress_events = [payload for name, payload in events if name == "progress"]
            self.assertEqual(progress_events[-1]["step"], 3)
            self.assertEqual(progress_events[-1]["total"], 3)

    def test_streamed_command_can_name_toothseg_branch_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake = tmp_path / "fake_tqdm.py"
            fake.write_text(
                "import sys\n"
                "sys.stderr.write(' 50%|##### | 2/4 [00:01<00:03]\\r\\n')\n",
                encoding="utf-8",
            )
            log_path = tmp_path / "run.log"

            mirrored = io.StringIO()
            with redirect_stderr(mirrored):
                rc, _elapsed, _stdout, _stderr = _run_command_streamed(
                    command=[sys.executable, str(fake)],
                    env=os.environ.copy(),
                    log_path=log_path,
                    safe_command=["python", "fake_tqdm.py"],
                    progress_stage="ToothSeg semantic",
                    progress_route="toothseg_refine",
                    progress_stage_id="semantic",
                    progress_scope="stage",
                )

            self.assertEqual(rc, 0)
            progress_lines = [
                json.loads(line.removeprefix(RUN_PROGRESS_PREFIX))
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.startswith(RUN_PROGRESS_PREFIX)
            ]
            self.assertEqual(progress_lines[-1]["stage"], "ToothSeg semantic")
            self.assertEqual(progress_lines[-1]["eta_seconds"], 3)
            self.assertEqual(progress_lines[-1]["route"], "toothseg_refine")
            self.assertEqual(progress_lines[-1]["stage_id"], "semantic")
            self.assertEqual(progress_lines[-1]["scope"], "stage")
            self.assertIn(RUN_PROGRESS_PREFIX, mirrored.getvalue())

    def test_streamed_command_cancellation_terminates_child_with_bounded_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake = tmp_path / "sleeping_child.py"
            fake.write_text(
                "import time\n"
                "print('started', flush=True)\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            log_path = tmp_path / "run.log"
            cancel_event = threading.Event()
            timer = threading.Timer(0.2, cancel_event.set)
            started = time.perf_counter()
            timer.start()
            try:
                with self.assertRaises(OperationCancelled):
                    _run_command_streamed(
                        command=[sys.executable, str(fake)],
                        env=os.environ.copy(),
                        log_path=log_path,
                        safe_command=["python", "sleeping_child.py"],
                        should_cancel=cancel_event.is_set,
                    )
            finally:
                timer.cancel()
                timer.join(timeout=1)

            self.assertLess(time.perf_counter() - started, 10)
            self.assertIn(
                "CANCELLATION requested; terminating child.",
                log_path.read_text(encoding="utf-8"),
            )

    def test_streamed_command_does_not_inherit_coordinator_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            probe = tmp_path / "stdin_probe.py"
            probe.write_text(
                "import sys\n"
                "print(len(sys.stdin.read()), flush=True)\n",
                encoding="utf-8",
            )

            rc, _elapsed, stdout, _stderr = _run_command_streamed(
                command=[sys.executable, str(probe)],
                env=os.environ.copy(),
                log_path=tmp_path / "run.log",
                safe_command=["python", "stdin_probe.py"],
            )

            self.assertEqual(rc, 0)
            self.assertEqual(stdout.strip(), "0")

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
            stage_ids = [
                json.loads(line.removeprefix(RUN_STAGE_PREFIX))["stage_id"]
                for line in run_log.splitlines()
                if line.startswith(RUN_STAGE_PREFIX)
            ]
            self.assertEqual(stage_ids, ["prepare", "segment", "finalize"])
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
            self.assertFalse(benchmark["run"]["higher_order_resampling"])

    def test_cli_stdout_remains_a_single_json_document_when_stages_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "sample.nii.gz"
            input_path.write_text("fake nifti", encoding="utf-8")
            fake_bin = root / "fake_totalseg.py"
            fake_bin.write_text(
                "#!/usr/bin/env python3\n"
                "import argparse\n"
                "from pathlib import Path\n"
                "p=argparse.ArgumentParser(); p.add_argument('-i'); p.add_argument('-o'); "
                "p.add_argument('-ta'); p.add_argument('--device'); a=p.parse_args()\n"
                "o=Path(a.o); o.mkdir(parents=True, exist_ok=True); "
                "(o/'mandible.nii.gz').write_text('mask')\n",
                encoding="utf-8",
            )
            fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IXUSR)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = cli_main(
                    [
                        "run", "--input", str(input_path), "--output", str(root / "case"),
                        "--device", "mps", "--skip-device-check", "--totalseg-bin", str(fake_bin),
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["status"], "success")
            self.assertNotIn(RUN_STAGE_PREFIX, stdout.getvalue())
            self.assertIn(RUN_STAGE_PREFIX, stderr.getvalue())

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
            self.assertFalse(benchmark["run"]["higher_order_resampling"])

    def test_run_totalsegmentator_with_higher_order_resampling_passes_totalseg_flag(self) -> None:
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
                "parser.add_argument('--higher_order_resampling', action='store_true')\n"
                "args=parser.parse_args()\n"
                "out=Path(args.o); out.mkdir(parents=True, exist_ok=True)\n"
                "(out/'mandible.nii.gz').write_text('mask')\n"
                "(out/'higher_order.txt').write_text(str(args.higher_order_resampling))\n"
                "print(f'higher_order={args.higher_order_resampling}')\n",
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
                higher_order_resampling=True,
            )

            self.assertEqual(result.status, "success")
            raw_dir = tmp_path / "case" / "segmentations" / "raw_totalseg"
            self.assertEqual((raw_dir / "higher_order.txt").read_text(encoding="utf-8"), "True")
            run_log = (tmp_path / "case" / "logs" / "run.log").read_text(encoding="utf-8")
            self.assertIn("--higher_order_resampling", run_log)
            benchmark = json.loads(
                (tmp_path / "case" / "logs" / "benchmark.json").read_text(encoding="utf-8")
            )
            self.assertFalse(benchmark["run"]["robust_crop"])
            self.assertTrue(benchmark["run"]["higher_order_resampling"])

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
            self.assertFalse(benchmark["run"]["higher_order_resampling"])

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

    def test_toothseg_refine_requires_teeth_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "sample.nii.gz"
            input_path.write_text("fake nifti", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "toothseg-refine can only be used with task=teeth"):
                run_totalsegmentator(
                    input_path=input_path,
                    output_root=tmp_path / "case",
                    task="craniofacial_structures",
                    requested_device="mps",
                    backend="toothseg",
                    toothseg_refine=True,
                    copy_input=True,
                    skip_device_check=True,
                )

    def test_toothseg_refine_reuses_supplied_case_with_fixed_12mm_margin(self) -> None:
        with patch(
            "totalsegmentator_wrapper_mac.runner_totalseg.run_totalsegmentator"
        ) as run:
            sentinel = object()
            run.return_value = sentinel
            source = Path("/input/source.nii.gz")
            output = Path("/output/case")

            result = run_toothseg_refine(
                input_path=source,
                output_root=output,
                requested_device="mps",
                teeth_craniofacial_case=output,
            )

            self.assertIs(result, sentinel)
            kwargs = run.call_args.kwargs
            self.assertEqual(kwargs["backend"], "toothseg")
            self.assertEqual(kwargs["task"], "teeth")
            self.assertEqual(kwargs["teeth_crop_margin_mm"], 12.0)
            self.assertEqual(kwargs["teeth_craniofacial_case"], output)


if __name__ == "__main__":
    unittest.main()
