from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import nibabel as nib
import numpy as np

from totalsegmentator_wrapper_mac.outputs import prepare_case_output
from totalsegmentator_wrapper_mac.runner_totalseg import _toothseg_predict_command, run_totalsegmentator
from totalsegmentator_wrapper_mac.toothseg_postprocess import (
    assign_mincost_tooth_labels,
    border_core_to_instances,
)


class ToothSegBackendTests(unittest.TestCase):
    def test_missing_primary_teeth_masks_are_classified_as_input_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.nii.gz"
            nib.save(
                nib.Nifti1Image(np.zeros((16, 16, 16), dtype=np.float32), np.eye(4)),
                str(source),
            )
            preflight = root / "preflight"
            preflight.mkdir()
            results = root / "models" / "nnUNet_results"
            results.mkdir(parents=True)
            (results.parent / "fdi_pair_distrs.json").write_text("{}", encoding="utf-8")

            result = run_totalsegmentator(
                input_path=source,
                output_root=root / "case",
                task="teeth",
                requested_device="mps",
                backend="toothseg",
                toothseg_nnunet_results=results,
                teeth_craniofacial_case=preflight,
                toothseg_refine=True,
                copy_input=False,
                skip_device_check=True,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error_code, "toothseg_input_invalid")
            self.assertEqual(
                result.safe_reason,
                "ToothSeg could not create a valid dental ROI from the existing teeth result.",
            )

    def test_primary_teeth_mask_shape_mismatch_is_classified_as_input_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.nii.gz"
            nib.save(
                nib.Nifti1Image(np.zeros((16, 16, 16), dtype=np.float32), np.eye(4)),
                str(source),
            )
            raw = root / "preflight" / "segmentations" / "raw_totalseg"
            raw.mkdir(parents=True)
            mismatched = nib.Nifti1Image(np.ones((8, 8, 8), dtype=np.uint8), np.eye(4))
            nib.save(mismatched, str(raw / "teeth_upper.nii.gz"))
            nib.save(mismatched, str(raw / "teeth_lower.nii.gz"))
            results = root / "models" / "nnUNet_results"
            results.mkdir(parents=True)
            (results.parent / "fdi_pair_distrs.json").write_text("{}", encoding="utf-8")

            result = run_totalsegmentator(
                input_path=source,
                output_root=root / "case",
                task="teeth",
                requested_device="mps",
                backend="toothseg",
                toothseg_nnunet_results=results,
                teeth_craniofacial_case=root / "preflight",
                toothseg_refine=True,
                copy_input=False,
                skip_device_check=True,
            )

            self.assertEqual(result.status, "failed")
            self.assertIn("Mask shape", result.stderr_tail)
            self.assertEqual(result.error_code, "toothseg_input_invalid")

    def test_border_core_conversion_uses_installed_acvl_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            border_core = np.zeros((28, 28, 28), dtype=np.uint8)
            border_core[2:26, 2:26, 2:26] = 2
            border_core[4:24, 4:24, 4:24] = 1
            source = root / "border_core.nii.gz"
            output = root / "instances.nii.gz"
            nib.save(nib.Nifti1Image(border_core, np.diag([0.2, 0.2, 0.2, 1])), str(source))

            result = border_core_to_instances(source, output)

            self.assertEqual(result["instance_count"], 1)
            self.assertEqual(set(np.unique(np.asanyarray(nib.load(str(output)).dataobj)).astype(int)), {0, 1})

    def test_predict_command_is_strict_mps_fold5_and_saves_semantic_probabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case = prepare_case_output(Path(tmp) / "case")
            command = _toothseg_predict_command(
                executable="nnUNetv2_predict",
                input_dir=case.toothseg_semantic_input_dir,
                output_dir=case.toothseg_semantic_predictions_dir,
                dataset_id="121",
                trainer="nnUNetTrainer_onlyMirror01_DASegOrd0",
                configuration="3d_fullres_resample_torch_256_bs8_ctnorm",
                save_probabilities=True,
            )

            self.assertIn("-device", command)
            self.assertEqual(command[command.index("-device") + 1], "mps")
            self.assertEqual(command[command.index("-f") + 1], "5")
            self.assertIn("--disable_tta", command)
            self.assertIn("--save_probabilities", command)

    def test_mincost_assignment_writes_fdi_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            instances = np.zeros((8, 8, 8), dtype=np.uint16)
            instances[2:6, 2:6, 2:6] = 1
            instance_path = root / "instances.nii.gz"
            affine = np.array([[-1, 0, 0, 7], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=float)
            nib.save(nib.Nifti1Image(instances, affine), str(instance_path))

            probabilities = np.full((33, 8, 8, 8), 1e-6, dtype=np.float32)
            probabilities[0] = 0.01
            probabilities[1, 2:6, 2:6, 2:6] = 0.99
            probability_path = root / "semantic.npz"
            np.savez_compressed(probability_path, probabilities=probabilities.transpose(0, 3, 2, 1))

            means = [[[[0.0, 0.0, 0.0] for _ in range(32)] for _ in range(32)]][0]
            covs = [[[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]] for _ in range(32)] for _ in range(32)]
            distributions = root / "distributions.json"
            distributions.write_text(json.dumps({"means": means, "covs": covs}), encoding="utf-8")
            output = root / "toothseg.nii.gz"

            result = assign_mincost_tooth_labels(
                instance_path=instance_path,
                semantic_probabilities_path=probability_path,
                distributions_path=distributions,
                output_path=output,
            )

            labels = set(np.unique(np.asanyarray(nib.load(str(output)).dataobj)).astype(int))
            self.assertEqual(labels, {0, 11})
            self.assertEqual(result["output_tooth_count"], 1)
            self.assertEqual(result["non_empty_labels"][0]["name"], "FDI 11")

    def test_full_volume_is_cropped_before_both_branches_and_reembedded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            affine = np.eye(4)
            source = root / "source.nii.gz"
            nib.save(nib.Nifti1Image(np.zeros((40, 40, 40), dtype=np.float32), affine), str(source))

            preflight = root / "preflight" / "segmentations" / "raw_totalseg"
            preflight.mkdir(parents=True)
            upper = np.zeros((40, 40, 40), dtype=np.uint8)
            lower = np.zeros_like(upper)
            upper[12:28, 12:28, 22:30] = 1
            lower[12:28, 12:28, 10:18] = 1
            nib.save(nib.Nifti1Image(upper, affine), str(preflight / "teeth_upper.nii.gz"))
            nib.save(nib.Nifti1Image(lower, affine), str(preflight / "teeth_lower.nii.gz"))

            fake_predict = root / "fake_nnunet.py"
            fake_predict.write_text(
                f"""#!{sys.executable}
import sys
from pathlib import Path
import nibabel as nib
import numpy as np

args = sys.argv[1:]
input_dir = Path(args[args.index('-i') + 1])
output_dir = Path(args[args.index('-o') + 1])
dataset = args[args.index('-d') + 1]
image = nib.load(str(input_dir / 'case_0000.nii.gz'))
shape = image.shape[:3]
output_dir.mkdir(parents=True, exist_ok=True)
if dataset == '121':
    probs = np.full((33, shape[2], shape[1], shape[0]), 1e-6, dtype=np.float32)
    probs[0] = 0.01
    probs[1, 4:-4, 4:-4, 4:-4] = 0.99
    np.savez_compressed(output_dir / 'case.npz', probabilities=probs)
    nib.save(nib.Nifti1Image(np.zeros(shape, dtype=np.uint8), image.affine), str(output_dir / 'case.nii.gz'))
else:
    border = np.zeros(shape, dtype=np.uint8)
    center = tuple(value // 2 for value in shape)
    border[center[0]-18:center[0]+18, center[1]-18:center[1]+18, center[2]-18:center[2]+18] = 2
    border[center[0]-15:center[0]+15, center[1]-15:center[1]+15, center[2]-15:center[2]+15] = 1
    nib.save(nib.Nifti1Image(border, image.affine), str(output_dir / 'case.nii.gz'))
""",
                encoding="utf-8",
            )
            fake_predict.chmod(fake_predict.stat().st_mode | stat.S_IXUSR)

            results = root / "models" / "nnUNet_results"
            results.mkdir(parents=True)
            distributions = results.parent / "fdi_pair_distrs.json"
            means = [[[[0.0, 0.0, 0.0] for _ in range(32)] for _ in range(32)]][0]
            covs = [
                [
                    [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
                    for _ in range(32)
                ]
                for _ in range(32)
            ]
            distributions.write_text(json.dumps({"means": means, "covs": covs}), encoding="utf-8")

            case_dir = root / "case"
            primary_artifacts = {
                case_dir / "logs" / "run.log": "primary run log\n",
                case_dir / "logs" / "benchmark.json": '{"run":{"backend":"totalsegmentator"}}\n',
                case_dir / "logs" / "environment.json": '{"source":"primary"}\n',
                case_dir / "README_OUTPUT.md": "# Primary output\n",
            }
            for path, content in primary_artifacts.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            result = run_totalsegmentator(
                input_path=source,
                output_root=case_dir,
                task="teeth",
                requested_device="mps",
                backend="toothseg",
                toothseg_bin=str(fake_predict),
                toothseg_nnunet_results=results,
                teeth_craniofacial_case=root / "preflight",
                teeth_crop_margin_mm=2.0,
                toothseg_refine=True,
                copy_input=False,
                skip_device_check=True,
            )

            self.assertEqual(result.status, "success", result.stderr_tail)
            refine_logs = case_dir / "logs" / "toothseg_refine"
            benchmark = json.loads((refine_logs / "benchmark.json").read_text(encoding="utf-8"))
            run_log = (refine_logs / "run.log").read_text(encoding="utf-8")
            stage_ids = [
                json.loads(line.removeprefix("RUN_STAGE "))["stage_id"]
                for line in run_log.splitlines()
                if line.startswith("RUN_STAGE ")
            ]
            self.assertEqual(stage_ids, ["roi", "semantic", "instance", "restore"])
            self.assertLess(
                np.prod(benchmark["toothseg"]["roi"]["roi_shape"]),
                np.prod(benchmark["input"]["dimensions"]),
            )
            full = nib.load(str(case_dir / "segmentations" / "toothseg" / "toothseg_fdi_multilabel.nii.gz"))
            self.assertEqual(full.shape, (40, 40, 40))
            self.assertTrue(np.allclose(full.affine, affine))
            self.assertGreater(np.count_nonzero(np.asanyarray(full.dataobj)), 0)
            self.assertTrue((case_dir / "TOOTHSEG_OUTPUT.md").is_file())
            self.assertTrue((refine_logs / "environment.json").is_file())
            self.assertTrue((refine_logs / "mask_stats.json").is_file())
            for path, content in primary_artifacts.items():
                self.assertEqual(path.read_text(encoding="utf-8"), content)


if __name__ == "__main__":
    unittest.main()
