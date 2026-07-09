from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import nibabel as nib
import numpy as np

from totalsegmentator_wrapper_mac.runner_totalseg import _teeth_child_command, run_totalsegmentator
from totalsegmentator_wrapper_mac.teeth_mps_child import patch_total_segmentator_device_converter
from totalsegmentator_wrapper_mac.teeth_roi import crop_to_mask_bbox, reembed_labelmap_to_full_space


class TeethExperimentalTests(unittest.TestCase):
    def test_child_command_uses_module_ml_and_benchmark_json(self) -> None:
        command = _teeth_child_command(
            input_path=Path("/case/input/teeth_roi.nii.gz"),
            output_path=Path("/case/segmentations/teeth_experimental/teeth_multilabel.nii.gz"),
            benchmark_path=Path("/case/logs/teeth_child_benchmark.json"),
            dry_run=True,
            force_split=True,
            higher_order_resampling=True,
        )

        self.assertIn("totalsegmentator_wrapper_mac.teeth_mps_child", command)
        self.assertIn("--ml", command)
        self.assertIn("--benchmark-json", command)
        self.assertIn("--dry-run", command)
        self.assertIn("--force-split", command)
        self.assertIn("--higher-order-resampling", command)

    def test_patch_total_segmentator_device_converter_handles_strings_and_devices(self) -> None:
        ts_api = SimpleNamespace(convert_device_to_string=lambda device: None)

        metadata = patch_total_segmentator_device_converter(ts_api)

        self.assertTrue(metadata["patch_applied"])
        self.assertEqual(ts_api.convert_device_to_string("mps"), "mps")
        self.assertEqual(ts_api.convert_device_to_string("cpu"), "cpu")
        self.assertEqual(ts_api.convert_device_to_string("gpu"), "gpu")
        self.assertEqual(ts_api.convert_device_to_string("gpu:0"), "gpu:0")
        self.assertEqual(ts_api.convert_device_to_string(SimpleNamespace(type="mps")), "mps")
        self.assertEqual(
            ts_api.convert_device_to_string(SimpleNamespace(type="cuda", index=2)),
            "gpu:2",
        )

    def test_crop_to_mask_bbox_preserves_affine_shift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            affine = np.eye(4)
            data = np.zeros((64, 64, 64), dtype=np.int16)
            input_path = root / "input.nii.gz"
            nib.save(nib.Nifti1Image(data, affine), str(input_path))

            upper = np.zeros((64, 64, 64), dtype=np.uint8)
            lower = np.zeros((64, 64, 64), dtype=np.uint8)
            upper[20:30, 22:32, 24:34] = 1
            lower[30:40, 34:44, 36:46] = 1
            upper_path = root / "teeth_upper.nii.gz"
            lower_path = root / "teeth_lower.nii.gz"
            nib.save(nib.Nifti1Image(upper, affine), str(upper_path))
            nib.save(nib.Nifti1Image(lower, affine), str(lower_path))

            output_path = root / "roi.nii.gz"
            metadata = crop_to_mask_bbox(
                input_path=input_path,
                mask_paths=[upper_path, lower_path],
                output_path=output_path,
                margin_mm=5.0,
            )

            cropped = nib.load(str(output_path))
            self.assertEqual(metadata["roi_bbox"]["start"], [15, 17, 19])
            self.assertEqual(metadata["roi_bbox"]["stop"], [45, 49, 51])
            self.assertEqual(metadata["roi_shape"], [30, 32, 32])
            self.assertFalse(metadata["near_whole_volume"])
            self.assertAlmostEqual(metadata["voxel_volume_ratio"], (30 * 32 * 32) / (64 * 64 * 64))
            self.assertEqual(list(cropped.shape[:3]), [30, 32, 32])
            np.testing.assert_allclose(cropped.affine[:3, 3], [15, 17, 19])
            self.assertEqual(int(cropped.header["qform_code"]), 1)
            self.assertEqual(int(cropped.header["sform_code"]), 1)

    def test_crop_to_mask_bbox_fails_on_empty_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            affine = np.eye(4)
            input_path = root / "input.nii.gz"
            upper_path = root / "teeth_upper.nii.gz"
            lower_path = root / "teeth_lower.nii.gz"
            nib.save(nib.Nifti1Image(np.zeros((32, 32, 32), dtype=np.int16), affine), str(input_path))
            nib.save(nib.Nifti1Image(np.zeros((32, 32, 32), dtype=np.uint8), affine), str(upper_path))
            nib.save(nib.Nifti1Image(np.zeros((32, 32, 32), dtype=np.uint8), affine), str(lower_path))

            with self.assertRaisesRegex(RuntimeError, "empty"):
                crop_to_mask_bbox(
                    input_path=input_path,
                    mask_paths=[upper_path, lower_path],
                    output_path=root / "roi.nii.gz",
                    margin_mm=5.0,
                )

    def test_reembed_labelmap_to_full_space_preserves_source_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            affine = np.eye(4)
            source_path = root / "source.nii.gz"
            nib.save(nib.Nifti1Image(np.zeros((32, 32, 32), dtype=np.int16), affine), str(source_path))

            crop_data = np.zeros((4, 5, 6), dtype=np.uint8)
            crop_data[1, 2, 3] = 7
            crop_path = root / "teeth_multilabel_roi.nii.gz"
            nib.save(nib.Nifti1Image(crop_data, affine), str(crop_path))
            roi_metadata = {
                "slices": {
                    "x": [10, 14],
                    "y": [11, 16],
                    "z": [12, 18],
                }
            }

            output_path = root / "teeth_multilabel_fullspace.nii.gz"
            metadata = reembed_labelmap_to_full_space(
                cropped_label_nii=crop_path,
                source_nii=source_path,
                roi_metadata=roi_metadata,
                output_full_nii=output_path,
            )

            full = nib.load(str(output_path))
            full_data = np.asanyarray(full.dataobj)
            self.assertEqual(list(full.shape[:3]), [32, 32, 32])
            np.testing.assert_allclose(full.affine, affine)
            self.assertEqual(int(full_data[11, 13, 15]), 7)
            self.assertEqual(metadata["nonzero_voxels"], 1)
            self.assertTrue(metadata["affine_matches_source"])

    def test_parent_experimental_teeth_dry_run_records_child_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            input_path.write_text("fake", encoding="utf-8")

            def fake_run_command(**kwargs):
                command = kwargs["command"]
                self.assertIn("--higher-order-resampling", command)
                benchmark_path = Path(command[command.index("--benchmark-json") + 1])
                benchmark_path.parent.mkdir(parents=True, exist_ok=True)
                benchmark_path.write_text(
                    json.dumps(
                        {
                            "status": "success",
                            "dry_run": True,
                            "patch": {"patch_applied": True, "post_patch_string_mps": "mps"},
                            "mps_gate": {"convtranspose3d_fp32": "passed"},
                            "torch": {"mps_fallback_env": None},
                        }
                    ),
                    encoding="utf-8",
                )
                return 0, 1.25, "child ok", ""

            with mock.patch(
                "totalsegmentator_wrapper_mac.runner_totalseg._run_command_streamed",
                side_effect=fake_run_command,
            ):
                result = run_totalsegmentator(
                    input_path=input_path,
                    output_root=root / "case",
                    task="teeth",
                    requested_device="mps",
                    totalseg_bin="unused",
                    copy_input=False,
                    skip_device_check=True,
                    experimental_teeth=True,
                    teeth_dry_run=True,
                    higher_order_resampling=True,
                )

            self.assertEqual(result.status, "success")
            benchmark = json.loads(
                (root / "case" / "logs" / "benchmark.json").read_text(encoding="utf-8")
            )
            self.assertTrue(benchmark["experimental_teeth"]["enabled"])
            self.assertTrue(benchmark["experimental_teeth"]["dry_run"])
            self.assertTrue(benchmark["run"]["higher_order_resampling"])
            self.assertTrue(benchmark["experimental_teeth"]["higher_order_resampling"])
            self.assertTrue(benchmark["experimental_teeth"]["patch"]["patch_applied"])
            preflight = benchmark["experimental_teeth"]["craniofacial_preflight"]
            self.assertEqual(preflight["source"], "none")
            self.assertEqual(preflight["status"], "skipped")
            self.assertFalse(preflight["robust_crop_used"])

    def test_parent_experimental_teeth_timeout_records_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            input_path.write_text("fake", encoding="utf-8")

            def fake_run_command(**_kwargs):
                return 124, 5.5, "starting\n31/231\n", ""

            with mock.patch(
                "totalsegmentator_wrapper_mac.runner_totalseg._run_command_streamed",
                side_effect=fake_run_command,
            ):
                result = run_totalsegmentator(
                    input_path=input_path,
                    output_root=root / "case",
                    task="teeth",
                    requested_device="mps",
                    totalseg_bin="unused",
                    copy_input=False,
                    skip_device_check=True,
                    experimental_teeth=True,
                    teeth_dry_run=True,
                    teeth_timeout_sec=5,
                )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.returncode, 124)
            benchmark = json.loads(
                (root / "case" / "logs" / "benchmark.json").read_text(encoding="utf-8")
            )
            exp = benchmark["experimental_teeth"]
            self.assertEqual(exp["child_status"], "timeout")
            self.assertEqual(exp["child_returncode"], 124)
            self.assertEqual(exp["last_progress"]["step"], 31)
            self.assertEqual(exp["last_progress"]["total"], 231)
            self.assertEqual(exp["timeout"]["timeout_sec"], 5)

    def test_internal_craniofacial_preflight_can_use_robust_crop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            nib.save(
                nib.Nifti1Image(np.zeros((40, 40, 40), dtype=np.int16), np.eye(4)),
                str(input_path),
            )

            def fake_run_command(**kwargs):
                command = kwargs["command"]
                if "totalsegmentator_wrapper_mac.teeth_mps_child" in command:
                    roi_path = Path(command[command.index("--input") + 1])
                    output_path = Path(command[command.index("--output") + 1])
                    benchmark_path = Path(command[command.index("--benchmark-json") + 1])
                    roi = nib.load(str(roi_path))
                    data = np.zeros(roi.shape[:3], dtype=np.uint8)
                    data[1, 1, 1] = 11
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    nib.save(nib.Nifti1Image(data, roi.affine, roi.header), str(output_path))
                    benchmark_path.parent.mkdir(parents=True, exist_ok=True)
                    benchmark_path.write_text(
                        json.dumps(
                            {
                                "status": "success",
                                "patch": {"patch_applied": True, "post_patch_string_mps": "mps"},
                                "mps_gate": {"convtranspose3d_fp32": "passed"},
                                "torch": {"mps_fallback_env": None},
                                "validation": {"non_empty_label_count": 1},
                            }
                        ),
                        encoding="utf-8",
                    )
                    return 0, 0.5, "child ok", ""

                output_dir = Path(command[command.index("-o") + 1])
                output_dir.mkdir(parents=True, exist_ok=True)
                image = nib.load(str(input_path))
                upper = np.zeros(image.shape[:3], dtype=np.uint8)
                lower = np.zeros(image.shape[:3], dtype=np.uint8)
                upper[10:14, 10:14, 10:14] = 1
                lower[18:22, 18:22, 18:22] = 1
                nib.save(nib.Nifti1Image(upper, image.affine, image.header), str(output_dir / "teeth_upper.nii.gz"))
                nib.save(nib.Nifti1Image(lower, image.affine, image.header), str(output_dir / "teeth_lower.nii.gz"))
                (output_dir / "robust_crop.txt").write_text(str("--robust_crop" in command), encoding="utf-8")
                return 0, 0.25, "preflight ok", ""

            with mock.patch(
                "totalsegmentator_wrapper_mac.runner_totalseg._run_command_streamed",
                side_effect=fake_run_command,
            ):
                result = run_totalsegmentator(
                    input_path=input_path,
                    output_root=root / "case",
                    task="teeth",
                    requested_device="mps",
                    totalseg_bin="fake_totalseg",
                    copy_input=False,
                    skip_device_check=True,
                    experimental_teeth=True,
                    teeth_crop_margin_mm=2.0,
                    teeth_robust_craniofacial_preflight=True,
                )

            self.assertEqual(result.status, "success")
            marker = (
                root
                / "case"
                / "preflight_craniofacial"
                / "segmentations"
                / "raw_totalseg"
                / "robust_crop.txt"
            )
            self.assertEqual(marker.read_text(encoding="utf-8"), "True")
            benchmark = json.loads(
                (root / "case" / "logs" / "benchmark.json").read_text(encoding="utf-8")
            )
            self.assertTrue(benchmark["experimental_teeth"]["robust_craniofacial_preflight"])
            preflight = benchmark["experimental_teeth"]["craniofacial_preflight"]
            self.assertEqual(preflight["source"], "internal")
            self.assertEqual(preflight["status"], "success")
            self.assertTrue(preflight["robust_crop_requested"])
            self.assertTrue(preflight["robust_crop_used"])
            self.assertTrue(benchmark["experimental_teeth"]["fullspace"]["affine_matches_source"])

    def test_supplied_craniofacial_case_skips_requested_robust_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            affine = np.eye(4)
            nib.save(
                nib.Nifti1Image(np.zeros((40, 40, 40), dtype=np.int16), affine),
                str(input_path),
            )
            raw_dir = root / "craniofacial" / "segmentations" / "raw_totalseg"
            raw_dir.mkdir(parents=True)
            upper = np.zeros((40, 40, 40), dtype=np.uint8)
            lower = np.zeros((40, 40, 40), dtype=np.uint8)
            upper[10:14, 10:14, 10:14] = 1
            lower[18:22, 18:22, 18:22] = 1
            nib.save(nib.Nifti1Image(upper, affine), str(raw_dir / "teeth_upper.nii.gz"))
            nib.save(nib.Nifti1Image(lower, affine), str(raw_dir / "teeth_lower.nii.gz"))

            def fake_child(**kwargs):
                command = kwargs["command"]
                self.assertIn("totalsegmentator_wrapper_mac.teeth_mps_child", command)
                roi_path = Path(command[command.index("--input") + 1])
                output_path = Path(command[command.index("--output") + 1])
                benchmark_path = Path(command[command.index("--benchmark-json") + 1])
                roi = nib.load(str(roi_path))
                data = np.zeros(roi.shape[:3], dtype=np.uint8)
                data[1, 1, 1] = 11
                output_path.parent.mkdir(parents=True, exist_ok=True)
                nib.save(nib.Nifti1Image(data, roi.affine, roi.header), str(output_path))
                benchmark_path.parent.mkdir(parents=True, exist_ok=True)
                benchmark_path.write_text(
                    json.dumps(
                        {
                            "status": "success",
                            "patch": {"patch_applied": True, "post_patch_string_mps": "mps"},
                            "mps_gate": {"convtranspose3d_fp32": "passed"},
                            "torch": {"mps_fallback_env": None},
                            "validation": {"non_empty_label_count": 1},
                        }
                    ),
                    encoding="utf-8",
                )
                return 0, 0.5, "child ok", ""

            with mock.patch(
                "totalsegmentator_wrapper_mac.runner_totalseg._run_command_streamed",
                side_effect=fake_child,
            ) as child:
                result = run_totalsegmentator(
                    input_path=input_path,
                    output_root=root / "case",
                    task="teeth",
                    requested_device="mps",
                    totalseg_bin="unused",
                    copy_input=False,
                    skip_device_check=True,
                    experimental_teeth=True,
                    teeth_crop_margin_mm=2.0,
                    teeth_craniofacial_case=root / "craniofacial",
                    teeth_robust_craniofacial_preflight=True,
                )

            self.assertEqual(result.status, "success")
            self.assertEqual(child.call_count, 1)
            benchmark = json.loads(
                (root / "case" / "logs" / "benchmark.json").read_text(encoding="utf-8")
            )
            preflight = benchmark["experimental_teeth"]["craniofacial_preflight"]
            self.assertEqual(preflight["source"], "provided")
            self.assertEqual(preflight["status"], "provided")
            self.assertTrue(preflight["robust_crop_requested"])
            self.assertFalse(preflight["robust_crop_used"])
            self.assertIn("existing craniofacial case supplied", preflight["warning"])
            run_log = (root / "case" / "logs" / "run.log").read_text(encoding="utf-8")
            self.assertIn(
                "existing craniofacial case supplied; internal robust preflight skipped",
                run_log,
            )

    def test_empty_craniofacial_teeth_masks_stop_before_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            affine = np.eye(4)
            nib.save(
                nib.Nifti1Image(np.zeros((32, 32, 32), dtype=np.int16), affine),
                str(input_path),
            )
            raw_dir = root / "craniofacial" / "segmentations" / "raw_totalseg"
            raw_dir.mkdir(parents=True)
            empty = np.zeros((32, 32, 32), dtype=np.uint8)
            nib.save(nib.Nifti1Image(empty, affine), str(raw_dir / "teeth_upper.nii.gz"))
            nib.save(nib.Nifti1Image(empty, affine), str(raw_dir / "teeth_lower.nii.gz"))

            with mock.patch("totalsegmentator_wrapper_mac.runner_totalseg._run_command_streamed") as child:
                result = run_totalsegmentator(
                    input_path=input_path,
                    output_root=root / "case",
                    task="teeth",
                    requested_device="mps",
                    totalseg_bin="unused",
                    copy_input=False,
                    skip_device_check=True,
                    experimental_teeth=True,
                    teeth_crop_margin_mm=2.0,
                    teeth_craniofacial_case=root / "craniofacial",
                )

            self.assertEqual(result.status, "failed")
            self.assertIn("empty", result.stderr_tail)
            child.assert_not_called()
            benchmark = json.loads(
                (root / "case" / "logs" / "benchmark.json").read_text(encoding="utf-8")
            )
            self.assertIn("empty", benchmark["experimental_teeth"]["error"])


if __name__ == "__main__":
    unittest.main()
