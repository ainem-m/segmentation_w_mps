from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import nibabel as nib
import numpy as np

from totalsegmentator_wrapper_mac.nifti_preview import write_nifti_preview
from totalsegmentator_wrapper_mac.cli import main


class NiftiPreviewTests(unittest.TestCase):
    def test_write_nifti_preview_writes_three_planes_without_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            output_dir = root / "preview"
            output_json = root / "preview.json"
            data = np.zeros((20, 18, 16), dtype=np.int16)
            data[4:16, 5:14, 3:13] = 750
            nib.save(
                nib.Nifti1Image(data, np.diag([0.4, 0.5, 0.6, 1.0])),
                str(input_path),
            )

            result = write_nifti_preview(
                input_path=input_path,
                output_dir=output_dir,
                output_json=output_json,
            )

            self.assertFalse(result["volume"]["uniform_or_empty"])
            self.assertFalse(result["inference_started"])
            self.assertEqual(
                {item["plane"] for item in result["outputs"]["mpr_preview"]},
                {"axial", "coronal", "sagittal"},
            )
            self.assertTrue(output_json.exists())
            for item in result["outputs"]["mpr_preview"]:
                self.assertTrue(Path(item["path"]).exists())
                self.assertFalse(item["uniform_or_empty"])

    def test_write_nifti_preview_marks_all_zero_volume_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "empty.nii"
            output_json = root / "preview.json"
            nib.save(
                nib.Nifti1Image(
                    np.zeros((12, 10, 8), dtype=np.int16),
                    np.eye(4),
                ),
                str(input_path),
            )

            result = write_nifti_preview(
                input_path=input_path,
                output_dir=root / "preview",
                output_json=output_json,
            )

            self.assertTrue(result["volume"]["uniform_or_empty"])
            self.assertEqual(result["volume"]["finite_voxel_count"], 12 * 10 * 8)
            self.assertEqual(result["volume"]["min"], 0.0)
            self.assertEqual(result["volume"]["max"], 0.0)
            saved = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertTrue(saved["volume"]["uniform_or_empty"])

    def test_write_nifti_preview_marks_constant_and_nonfinite_volumes_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, data in (
                ("constant.nii", np.full((7, 6, 5), 400, dtype=np.int16)),
                ("nonfinite.nii", np.full((7, 6, 5), np.nan, dtype=np.float32)),
            ):
                input_path = root / name
                nib.save(nib.Nifti1Image(data, np.eye(4)), str(input_path))
                result = write_nifti_preview(
                    input_path=input_path,
                    output_dir=root / f"{name}.preview",
                    output_json=root / f"{name}.json",
                )
                self.assertTrue(result["volume"]["uniform_or_empty"])

    def test_nifti_preview_cli_writes_machine_readable_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            output_json = root / "preview.json"
            data = np.zeros((8, 8, 8), dtype=np.int16)
            data[2:6, 2:6, 2:6] = 900
            nib.save(nib.Nifti1Image(data, np.eye(4)), str(input_path))

            returncode = main(
                [
                    "nifti-preview",
                    "--input",
                    str(input_path),
                    "--output-dir",
                    str(root / "images"),
                    "--output-json",
                    str(output_json),
                ]
            )

            self.assertEqual(returncode, 0)
            self.assertFalse(
                json.loads(output_json.read_text(encoding="utf-8"))["inference_started"]
            )

    def test_write_nifti_preview_rejects_non_3d_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "four_dimensional.nii.gz"
            nib.save(
                nib.Nifti1Image(
                    np.zeros((4, 4, 4, 2), dtype=np.int16),
                    np.eye(4),
                ),
                str(input_path),
            )

            with self.assertRaisesRegex(ValueError, "3D"):
                write_nifti_preview(
                    input_path=input_path,
                    output_dir=root / "preview",
                    output_json=root / "preview.json",
                )


if __name__ == "__main__":
    unittest.main()
