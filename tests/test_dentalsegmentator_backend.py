from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nibabel as nib
import numpy as np

from totalsegmentator_wrapper_mac.device import DeviceCheck
from totalsegmentator_wrapper_mac.runner_totalseg import run_totalsegmentator
from totalsegmentator_wrapper_mac.surface_preview import (
    resolve_surface_preview_input,
    run_surface_preview,
    smoothing_config_from_options,
)


class DentalSegmentatorBackendTests(unittest.TestCase):
    def test_dentalsegmentator_backend_runs_fake_nnunet_and_writes_labelmap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            nib.save(nib.Nifti1Image(np.zeros((24, 24, 24), dtype=np.int16), np.eye(4)), str(input_path))
            fake_nnunet = _write_fake_nnunet_predict(root / "fake_nnunet.py")

            result = run_totalsegmentator(
                input_path=input_path,
                output_root=root / "case",
                backend="dentalsegmentator",
                task="craniofacial_structures",
                requested_device="cpu",
                dentalseg_bin=str(fake_nnunet),
                dentalseg_nnunet_results=root / "nnUNet_results",
                copy_input=False,
                skip_device_check=True,
            )

            self.assertEqual(result.status, "success")
            case_dir = root / "case"
            labelmap = (
                case_dir
                / "segmentations"
                / "dentalsegmentator"
                / "dentalsegmentator_multilabel.nii.gz"
            )
            self.assertTrue(labelmap.exists())
            self.assertTrue((labelmap.with_name(labelmap.name + ".labels.json")).exists())
            source, source_info = resolve_surface_preview_input(case_dir=case_dir, input_path=None)
            self.assertTrue(source.samefile(labelmap))
            self.assertEqual(source_info["source"], "dentalsegmentator_multilabel")

            benchmark = json.loads((case_dir / "logs" / "benchmark.json").read_text(encoding="utf-8"))
            self.assertEqual(benchmark["run"]["backend"], "dentalsegmentator")
            self.assertEqual(benchmark["run"]["status"], "success")
            self.assertEqual(benchmark["dentalsegmentator"]["validation"]["non_empty_label_count"], 5)
            self.assertEqual(benchmark["dentalsegmentator"]["versions"]["nnunetv2"], "not_installed")
            run_log = (case_dir / "logs" / "run.log").read_text(encoding="utf-8")
            self.assertIn("-i <input:dentalsegmentator_nnunet>", run_log)
            self.assertIn("-d 112", run_log)
            self.assertIn("-device cpu", run_log)
            self.assertNotIn(str(input_path.parent), run_log)

            preview = run_surface_preview(
                case_dir=case_dir,
                smoothing=smoothing_config_from_options(preset="none"),
            )
            groups = {group["name"]: group["labels"] for group in preview["groups"]}
            self.assertEqual(groups["jaws"], [1, 2])
            self.assertEqual(groups["dental_hard_tissue"], [3, 4])
            self.assertTrue((case_dir / "surface_preview" / "index.html").exists())

    def test_dentalsegmentator_backend_fails_fast_without_nnunet_results_or_model_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            nib.save(nib.Nifti1Image(np.zeros((8, 8, 8), dtype=np.int16), np.eye(4)), str(input_path))

            result = run_totalsegmentator(
                input_path=input_path,
                output_root=root / "case",
                backend="dentalsegmentator",
                task="craniofacial_structures",
                requested_device="cpu",
                copy_input=False,
                skip_device_check=True,
            )

            self.assertEqual(result.status, "failed")
            self.assertIn("nnUNet_results", result.stderr_tail)
            benchmark = json.loads(
                (root / "case" / "logs" / "benchmark.json").read_text(encoding="utf-8")
            )
            self.assertEqual(benchmark["run"]["backend"], "dentalsegmentator")
            self.assertIn("nnUNet_results", benchmark["dentalsegmentator"]["error"])
            self.assertFalse(
                (root / "case" / "segmentations" / "raw_totalseg" / "mandible.nii.gz").exists()
            )

    def test_dentalsegmentator_auto_cpu_fallback_requires_explicit_cpu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            nib.save(nib.Nifti1Image(np.zeros((8, 8, 8), dtype=np.int16), np.eye(4)), str(input_path))
            device_check = DeviceCheck(
                status="pass",
                requested_device="auto",
                actual_device="cpu",
                fallback_reason="MPS smoke test failed: test",
                python=sys.version,
                platform="test",
                machine="arm64",
                torch_version="test",
                mps_built=False,
                mps_available=False,
                convtranspose3d_fp32="fail",
                elapsed_seconds=0.0,
                error=None,
            )

            with patch("totalsegmentator_wrapper_mac.runner_totalseg.resolve_device", return_value=device_check):
                result = run_totalsegmentator(
                    input_path=input_path,
                    output_root=root / "case",
                    backend="dentalsegmentator",
                    task="craniofacial_structures",
                    requested_device="auto",
                    dentalseg_nnunet_results=root / "nnUNet_results",
                    copy_input=False,
                )

            self.assertEqual(result.status, "failed")
            self.assertIn("resolved to CPU", result.stderr_tail)
            benchmark = json.loads(
                (root / "case" / "logs" / "benchmark.json").read_text(encoding="utf-8")
            )
            self.assertEqual(benchmark["run"]["backend"], "dentalsegmentator")
            self.assertEqual(benchmark["run"]["actual_device"], "cpu")
            self.assertEqual(benchmark["run"]["fallback_reason"], "MPS smoke test failed: test")
            self.assertIn("resolved to CPU", benchmark["dentalsegmentator"]["error"])

    def test_dentalsegmentator_rejects_individual_teeth_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            nib.save(nib.Nifti1Image(np.zeros((8, 8, 8), dtype=np.int16), np.eye(4)), str(input_path))

            result = run_totalsegmentator(
                input_path=input_path,
                output_root=root / "case",
                backend="dentalsegmentator",
                task="teeth",
                requested_device="cpu",
                dentalseg_nnunet_results=root / "nnUNet_results",
                copy_input=False,
                skip_device_check=True,
            )

            self.assertEqual(result.status, "failed")
            self.assertIn("individual tooth labels", result.stderr_tail)


def _write_fake_nnunet_predict(path: Path) -> Path:
    path.write_text(
        f"#!{sys.executable}\n"
        "import argparse\n"
        "from pathlib import Path\n"
        "import nibabel as nib\n"
        "import numpy as np\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('-i')\n"
        "parser.add_argument('-o')\n"
        "parser.add_argument('-d')\n"
        "parser.add_argument('-c')\n"
        "parser.add_argument('-tr')\n"
        "parser.add_argument('-p')\n"
        "parser.add_argument('-f', nargs='+')\n"
        "parser.add_argument('-device')\n"
        "parser.add_argument('-npp')\n"
        "parser.add_argument('-nps')\n"
        "args = parser.parse_args()\n"
        "image = nib.load(str(Path(args.i) / 'case_0000.nii.gz'))\n"
        "data = np.zeros(image.shape[:3], dtype=np.uint8)\n"
        "data[2:10, 2:10, 2:8] = 1\n"
        "data[11:20, 2:10, 2:8] = 2\n"
        "data[5:12, 12:18, 12:18] = 3\n"
        "data[13:20, 12:18, 12:18] = 4\n"
        "data[8:16, 19:21, 6:14] = 5\n"
        "out = Path(args.o)\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "nib.save(nib.Nifti1Image(data, image.affine, image.header), str(out / 'case.nii.gz'))\n"
        "print(f'dataset={args.d} config={args.c} device={args.device}')\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


if __name__ == "__main__":
    unittest.main()
