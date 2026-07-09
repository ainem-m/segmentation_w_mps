from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import nibabel as nib
import numpy as np

from totalsegmentator_wrapper_mac.cli import main
from totalsegmentator_wrapper_mac.slicer_export import run_slicer_export


class SlicerExportTests(unittest.TestCase):
    def test_file_only_export_prefers_fullspace_labelmap_and_writes_import_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = root / "case"
            labelmap = (
                case_dir
                / "segmentations"
                / "teeth_experimental"
                / "teeth_multilabel_fullspace.nii.gz"
            )
            source = root / "source_input.nii.gz"
            _write_labelmap(labelmap)
            _write_source(source)

            summary = run_slicer_export(case_dir=case_dir, source_path=source)

            export_dir = case_dir / "slicer_export"
            self.assertEqual(summary["schema"], "totalsegmentator_wrapper_mac.slicer_export.v1")
            self.assertEqual(summary["mode"], "file_only_drag_and_drop")
            self.assertFalse(summary["slicer_launch"])
            self.assertFalse(summary["python_script_required"])
            self.assertEqual(
                summary["segmentation"]["input"],
                str(labelmap.resolve()),
            )
            self.assertTrue((export_dir / "source.nii.gz").exists())
            self.assertTrue((export_dir / "segmentation_labelmap.nii.gz").exists())
            self.assertTrue((export_dir / "segmentation_ColorTable.ctbl").exists())
            self.assertTrue((export_dir / "label_names.json").exists())
            self.assertTrue((export_dir / "label_colors.json").exists())
            self.assertTrue((export_dir / "README_SLICER_IMPORT.md").exists())
            self.assertTrue((export_dir / "slicer_export_summary.json").exists())
            self.assertFalse((export_dir / "open_in_slicer.py").exists())
            self.assertFalse((case_dir / "slicer").exists())

            names = json.loads((export_dir / "label_names.json").read_text(encoding="utf-8"))
            self.assertEqual(
                names["labels"],
                {
                    "1": "lower_jawbone",
                    "11": "upper_right_central_incisor_fdi11",
                    "51": "upper_right_central_incisor_pulp_fdi11",
                },
            )
            color_table = (export_dir / "segmentation_ColorTable.ctbl").read_text(encoding="utf-8")
            self.assertIn("0 Background 0 0 0 0", color_table)
            self.assertIn("11 upper_right_central_incisor_fdi11", color_table)
            self.assertIn("51 upper_right_central_incisor_pulp_fdi11", color_table)
            readme = (export_dir / "README_SLICER_IMPORT.md").read_text(encoding="utf-8")
            self.assertIn("file-only handoff", readme)
            self.assertIn("does not launch Slicer", readme)
            self.assertIn("does not require running a Python script", readme)
            self.assertIn("Drag this folder's files into Slicer", readme)

    def test_cli_slicer_export_writes_default_export_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = root / "case"
            labelmap = (
                case_dir
                / "segmentations"
                / "teeth_experimental"
                / "teeth_multilabel_fullspace.nii.gz"
            )
            _write_labelmap(labelmap)

            rc = main(["slicer-export", "--case", str(case_dir)])

            self.assertEqual(rc, 0)
            export_dir = case_dir / "slicer_export"
            self.assertTrue((export_dir / "segmentation_labelmap.nii.gz").exists())
            self.assertTrue((export_dir / "segmentation_ColorTable.ctbl").exists())
            self.assertFalse((export_dir / "open_in_slicer.py").exists())

    def test_slicer_export_rejects_missing_explicit_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = root / "case"
            labelmap = (
                case_dir
                / "segmentations"
                / "teeth_experimental"
                / "teeth_multilabel_fullspace.nii.gz"
            )
            _write_labelmap(labelmap)

            with self.assertRaisesRegex(FileNotFoundError, "Source volume not found"):
                run_slicer_export(case_dir=case_dir, source_path=root / "missing.nii.gz")

    def test_slicer_export_builds_craniofacial_labelmap_when_teeth_output_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = root / "case"
            raw_dir = case_dir / "segmentations" / "raw_totalseg"
            _write_binary_mask(raw_dir / "mandible.nii.gz", (slice(2, 10), slice(2, 10), slice(2, 8)))
            _write_binary_mask(raw_dir / "skull.nii.gz", (slice(12, 22), slice(12, 22), slice(12, 20)))
            _write_binary_mask(raw_dir / "teeth_lower.nii.gz", (slice(6, 12), slice(8, 14), slice(7, 13)))

            summary = run_slicer_export(case_dir=case_dir)

            derived = case_dir / "segmentations" / "derived" / "craniofacial_arch_jaw_multilabel.nii.gz"
            self.assertTrue(derived.exists())
            self.assertEqual(summary["segmentation"]["input"], str(derived.resolve()))
            self.assertEqual(summary["segmentation"]["source_info"]["source"], "craniofacial_raw_totalseg")
            names = json.loads(
                (case_dir / "slicer_export" / "label_names.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                names["labels"],
                {
                    "1": "lower_jawbone",
                    "2": "upper_jawbone",
                    "11": "lower_teeth",
                },
            )


def _write_labelmap(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros((24, 24, 24), dtype=np.uint16)
    data[3:11, 3:11, 3:9] = 1
    data[9:16, 9:16, 9:14] = 11
    data[12:15, 12:15, 12:16] = 51
    image = nib.Nifti1Image(data, np.eye(4))
    nib.save(image, str(path))
    sidecar = path.with_name(path.name + ".labels.json")
    sidecar.write_text(
        json.dumps(
            {
                "labels": {
                    "1": "lower_jawbone",
                    "11": "upper_right_central_incisor_fdi11",
                    "51": "upper_right_central_incisor_pulp_fdi11",
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _write_source(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros((24, 24, 24), dtype=np.int16)
    image = nib.Nifti1Image(data, np.eye(4))
    nib.save(image, str(path))
    return path


def _write_binary_mask(path: Path, block: tuple[slice, slice, slice]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros((28, 28, 28), dtype=np.uint8)
    data[block] = 1
    image = nib.Nifti1Image(data, np.eye(4))
    nib.save(image, str(path))
    return path


if __name__ == "__main__":
    unittest.main()
