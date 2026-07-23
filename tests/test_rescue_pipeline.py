from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import numpy as np

from totalsegmentator_wrapper_mac.rescue_geometry import CropXYZ, RescueGeometryTransform
from totalsegmentator_wrapper_mac.cli import main
from totalsegmentator_wrapper_mac.rescue_pipeline import (
    CONFIRMATION_SCHEMA,
    RescuePipelineError,
    build_confirmation_token,
    create_estimate,
    create_preview,
    finalize_rescue,
    read_nifti,
    write_preview_artifacts,
)


class RescuePipelineTests(unittest.TestCase):
    def test_estimate_always_returns_editable_candidate_and_safe_schema(self) -> None:
        volume = np.arange(5 * 4 * 3, dtype=np.int16).reshape((5, 4, 3))
        result = create_estimate(
            volume,
            spacing_hints_xyz=(None, None, 0.9375),
            source_manifest_sha256="a" * 64,
            used_series=[
                {
                    "series_hash": "b" * 64,
                    "role": "primary",
                    "plane": "axial",
                    "file_count": 3,
                    "unsafe_description": "PATIENT NAME",
                }
            ],
            used_dicom_tags=[
                {
                    "tag": "0018,0050",
                    "name": "SliceThickness",
                    "value_mm": 0.9375,
                    "consistency": "all_equal",
                    "patient_name": "PATIENT NAME",
                }
            ],
        )

        self.assertEqual(result["schema"], "totalsegmentator_wrapper_mac.rescue_geometry.v2")
        self.assertEqual(result["workflow_status"], "estimated")
        self.assertEqual(result["estimate"]["estimated_spacing_xyz"], [1.0, 1.0, 0.9375])
        self.assertEqual(result["estimate"]["confidence"]["overall"], "unknown")
        self.assertEqual(result["estimate"]["confidence"]["per_axis"]["z"], "low")
        self.assertNotIn("unsafe_description", result["evidence"]["used_series"][0])
        self.assertNotIn("patient_name", result["evidence"]["used_dicom_tags"][0])
        self.assertNotIn("PATIENT NAME", json.dumps(result))

    def test_preview_applies_canonical_transform_without_writing_nifti(self) -> None:
        volume = np.arange(4 * 3 * 2, dtype=np.int16).reshape((4, 3, 2))
        transform = RescueGeometryTransform(
            axis_permutation=(1, 0, 2),
            rotation_quarter_turns=1,
            slice_order_reversed=True,
            crop=CropXYZ((1, 0, 0), (4, 3, 2)),
        )

        preview, metadata = create_preview(
            volume,
            estimated_spacing_xyz=(0.5, 0.6, 0.9),
            confirmed_spacing_xyz=(0.55, 0.65, 0.95),
            transform=transform,
            source_manifest_sha256="c" * 64,
        )

        np.testing.assert_array_equal(preview, transform.apply_volume_xyz(volume))
        self.assertEqual(metadata["workflow_status"], "preview_ready")
        self.assertEqual(metadata["confirmed"]["confirmed_spacing_xyz"], [0.55, 0.65, 0.95])
        self.assertEqual(metadata["confirmation"]["schema"], CONFIRMATION_SCHEMA)
        self.assertFalse(metadata["confirmation"]["confirmed"])

    def test_finalize_requires_token_bound_to_source_and_geometry(self) -> None:
        volume = np.arange(3 * 4 * 5, dtype=np.int16).reshape((3, 4, 5))
        transform = RescueGeometryTransform(slice_order_reversed=True)
        source_hash = "d" * 64
        confirmed = (0.4, 0.5, 0.6)

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "rescue.nii"
            with self.assertRaisesRegex(RescuePipelineError, "confirmation token"):
                finalize_rescue(
                    volume,
                    output_path=output,
                    estimated_spacing_xyz=(1.0, 1.0, 1.0),
                    confirmed_spacing_xyz=confirmed,
                    transform=transform,
                    source_manifest_sha256=source_hash,
                    confirmation_token="wrong",
                )
            self.assertFalse(output.exists())

            token = build_confirmation_token(
                source_manifest_sha256=source_hash,
                confirmed_spacing_xyz=confirmed,
                transform=transform,
            )
            metadata = finalize_rescue(
                volume,
                output_path=output,
                estimated_spacing_xyz=(1.0, 1.0, 1.0),
                confirmed_spacing_xyz=confirmed,
                transform=transform,
                source_manifest_sha256=source_hash,
                confirmation_token=token,
            )

            readback, readback_meta = read_nifti(output)
            np.testing.assert_array_equal(readback, volume[:, :, ::-1])
            self.assertEqual(readback_meta["shape"], [3, 4, 5])
            np.testing.assert_allclose(
                readback_meta["spacing_xyz"],
                [0.4, 0.5, 0.6],
                rtol=0.0,
                atol=1e-6,
            )
            self.assertTrue(metadata["output_validation"]["affine_consistent"])
            self.assertEqual(metadata["workflow_status"], "finalized")

    def test_same_input_and_confirmation_produce_byte_identical_nifti(self) -> None:
        volume = np.arange(24, dtype=np.uint16).reshape((2, 3, 4))
        transform = RescueGeometryTransform(axis_permutation=(2, 1, 0))
        source_hash = "e" * 64
        spacing = (0.7, 0.8, 0.9)
        token = build_confirmation_token(
            source_manifest_sha256=source_hash,
            confirmed_spacing_xyz=spacing,
            transform=transform,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.nii"
            second = Path(temp_dir) / "second.nii"
            for output in (first, second):
                finalize_rescue(
                    volume,
                    output_path=output,
                    estimated_spacing_xyz=spacing,
                    confirmed_spacing_xyz=spacing,
                    transform=transform,
                    source_manifest_sha256=source_hash,
                    confirmation_token=token,
                )
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_readback_rejects_truncated_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.nii"
            path.write_bytes(b"\0" * 352)
            with self.assertRaises(RescuePipelineError):
                read_nifti(path)

    def test_preview_artifacts_include_three_planes_and_spacing_aspect(self) -> None:
        volume = np.arange(6 * 4 * 3, dtype=np.int16).reshape((6, 4, 3))
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_preview_artifacts(
                Path(temp_dir),
                volume,
                (0.5, 1.0, 2.0),
            )

            self.assertFalse(outputs["inference_started"])
            self.assertEqual(
                {item["plane"] for item in outputs["mpr_preview"]},
                {"axial", "coronal", "sagittal"},
            )
            axial = next(
                item for item in outputs["mpr_preview"] if item["plane"] == "axial"
            )
            self.assertEqual((axial["width"], axial["height"]), (3, 4))
            for item in outputs["mpr_preview"]:
                self.assertTrue(Path(item["path"]).read_bytes().startswith(b"P5\n"))
                self.assertGreater(item["row_spacing_mm"], 0)
                self.assertGreater(item["column_spacing_mm"], 0)
            self.assertTrue(
                Path(outputs["pseudo_3d_preview"]).read_bytes().startswith(b"P5\n")
            )

    def test_cli_estimate_preview_finalize_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            volume_path = root / "decoded.npy"
            np.save(volume_path, np.arange(24, dtype=np.int16).reshape((2, 3, 4)))
            estimate_path = root / "estimate.json"
            preview_path = root / "preview.npy"
            preview_metadata_path = root / "preview.json"
            nifti_path = root / "output.nii"
            final_path = root / "final.json"
            source_hash = "f" * 64

            with redirect_stdout(StringIO()):
                estimate_code = main(
                    [
                        "dicom-rescue-estimate",
                        "--volume",
                        str(volume_path),
                        "--source-manifest-sha256",
                        source_hash,
                        "--spacing-hints",
                        "unknown,unknown,0.9",
                        "--output",
                        str(estimate_path),
                    ]
                )
                preview_code = main(
                    [
                        "dicom-rescue-preview",
                        "--volume",
                        str(volume_path),
                        "--geometry",
                        str(estimate_path),
                        "--output-volume",
                        str(preview_path),
                        "--output",
                        str(preview_metadata_path),
                    ]
                )
            preview_metadata = json.loads(preview_metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(len(preview_metadata["outputs"]["mpr_preview"]), 3)
            self.assertTrue(
                Path(preview_metadata["outputs"]["pseudo_3d_preview"]).exists()
            )
            with redirect_stdout(StringIO()):
                finalize_code = main(
                    [
                        "dicom-rescue-finalize",
                        "--volume",
                        str(volume_path),
                        "--geometry",
                        str(preview_metadata_path),
                        "--confirmation-token",
                        preview_metadata["confirmation"]["token"],
                        "--output-nifti",
                        str(nifti_path),
                        "--output",
                        str(final_path),
                    ]
                )

            self.assertEqual((estimate_code, preview_code, finalize_code), (0, 0, 0))
            self.assertTrue(nifti_path.exists())
            final_metadata = json.loads(final_path.read_text(encoding="utf-8"))
            self.assertEqual(final_metadata["workflow_status"], "finalized")
            self.assertTrue(final_metadata["confirmation"]["confirmed"])

    def test_cli_error_json_does_not_include_paths_or_source_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret = "PATIENT_JANE_DOE"
            missing = root / secret / "missing.npy"
            output = root / "error.json"
            with redirect_stderr(StringIO()):
                code = main(
                    [
                        "dicom-rescue-estimate",
                        "--volume",
                        str(missing),
                        "--source-manifest-sha256",
                        "0" * 64,
                        "--output",
                        str(output),
                    ]
                )
            payload = output.read_text(encoding="utf-8")
            self.assertEqual(code, 2)
            self.assertNotIn(secret, payload)
            self.assertNotIn(str(root), payload)

    def test_cli_estimator_failure_degrades_to_previewable_manual_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            volume_path = root / "decoded.npy"
            np.save(volume_path, np.arange(60, dtype=np.int16).reshape((3, 4, 5)))
            estimate_path = root / "estimate.json"
            preview_volume = root / "preview.npy"
            preview_path = root / "preview.json"
            with patch(
                "totalsegmentator_wrapper_mac.rescue_estimation.estimate_rescue_spacing",
                side_effect=RescuePipelineError("forced estimator failure"),
            ), redirect_stdout(StringIO()):
                estimate_code = main(
                    [
                        "dicom-rescue-estimate",
                        "--volume",
                        str(volume_path),
                        "--source-manifest-sha256",
                        "7" * 64,
                        "--spacing-hints",
                        "0.4,0.6,unknown",
                        "--output",
                        str(estimate_path),
                    ]
                )
            estimate = json.loads(estimate_path.read_text(encoding="utf-8"))
            self.assertEqual(estimate_code, 0)
            self.assertEqual(
                estimate["estimate"]["estimated_spacing_xyz"],
                [0.4, 0.6, 1.0],
            )
            self.assertEqual(estimate["estimate"]["confidence"]["overall"], "unknown")
            self.assertIn(
                "automatic_estimation_failed",
                estimate["estimate"]["confidence"]["limitations"],
            )
            self.assertIsNot(estimate.get("inference_started"), True)
            with redirect_stdout(StringIO()):
                preview_code = main(
                    [
                        "dicom-rescue-preview",
                        "--volume",
                        str(volume_path),
                        "--geometry",
                        str(estimate_path),
                        "--output-volume",
                        str(preview_volume),
                        "--output",
                        str(preview_path),
                    ]
                )
            preview = json.loads(preview_path.read_text(encoding="utf-8"))
            self.assertEqual(preview_code, 0)
            self.assertEqual(len(preview["confirmation_token"]), 64)
            self.assertFalse(preview["inference_started"])

    def test_cli_preview_finalize_accepts_minimal_geometry_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            volume_path = root / "decoded.npy"
            volume = np.arange(24, dtype=np.int16).reshape((2, 3, 4))
            np.save(volume_path, volume)
            request_path = root / "minimal.json"
            preview_volume = root / "preview.npy"
            preview_json = root / "preview.json"
            output_nifti = root / "output.nii"
            final_json = root / "final.json"
            request_path.write_text(
                json.dumps(
                    {
                        "schema": "totalsegmentator_wrapper_mac.rescue_geometry.v2",
                        "source": {"content_manifest_sha256": "9" * 64},
                        "confirmed": {"confirmed_spacing_xyz": [0.5, 0.6, 0.9]},
                        "transform": {
                            "axis_permutation": ["x", "y", "z"],
                            "rotation_quarter_turns": 0,
                            "slice_order_reversed": False,
                            "crop_voxels_xyz": None,
                        },
                        "calibrations": [
                            {
                                "plane": "axial",
                                "voxel_points_xyz": [[0, 0, 0], [10, 0, 0]],
                                "known_length_mm": 5.0,
                                "updated_axes": ["x"],
                                "method": "known_length",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                preview_code = main(
                    [
                        "dicom-rescue-preview",
                        "--volume",
                        str(volume_path),
                        "--geometry",
                        str(request_path),
                        "--output-volume",
                        str(preview_volume),
                        "--output",
                        str(preview_json),
                    ]
                )
            preview = json.loads(preview_json.read_text(encoding="utf-8"))
            with redirect_stdout(StringIO()):
                final_code = main(
                    [
                        "dicom-rescue-finalize",
                        "--volume",
                        str(volume_path),
                        "--geometry",
                        str(preview_json),
                        "--confirmation-token",
                        preview["confirmation_token"],
                        "--output-nifti",
                        str(output_nifti),
                        "--output",
                        str(final_json),
                    ]
                )

            self.assertEqual((preview_code, final_code), (0, 0))
            self.assertIn("estimate", preview)
            self.assertIn("evidence", preview)
            self.assertIn("algorithm", preview)
            self.assertEqual(preview["calibrations"][0]["known_length_mm"], 5.0)
            self.assertEqual(
                preview["calibrations"][0]["voxel_points_xyz"],
                [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]],
            )
            final = json.loads(final_json.read_text(encoding="utf-8"))
            self.assertEqual(final["workflow_status"], "finalized")


if __name__ == "__main__":
    unittest.main()
