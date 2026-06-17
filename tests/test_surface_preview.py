from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import nibabel as nib
import numpy as np

from totalsegmentator_wrapper_mac.surface_preview import (
    effective_smoothing_for_group,
    effective_smoothing_for_label,
    export_labelmap_surfaces,
    mask_to_mesh,
    run_surface_preview,
    smoothing_config_from_options,
)


SYNTHETIC_LABELS = {
    1: "lower_jawbone",
    2: "upper_jawbone",
    11: "upper_right_central_incisor_fdi11",
    51: "upper_right_central_incisor_pulp_fdi11",
}


class SurfacePreviewTests(unittest.TestCase):
    def test_marching_cubes_stl_export_creates_non_empty_binary_stl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            labelmap = _write_synthetic_labelmap(root / "labels.nii.gz")

            with mock.patch(
                "totalsegmentator_wrapper_mac.surface_preview.label_name_map",
                return_value=SYNTHETIC_LABELS,
            ):
                summary = export_labelmap_surfaces(
                    input_path=labelmap,
                    output_dir=root / "stl",
                    combined=True,
                    smoothing=smoothing_config_from_options(preset="none"),
                )

            self.assertEqual(summary["label_count"], 4)
            first_stl = Path(summary["labels"][0]["stl"])
            self.assertTrue(first_stl.exists())
            self.assertGreater(first_stl.stat().st_size, 84)

    def test_taubin_smoothing_is_finite_stable_and_moves_vertices(self) -> None:
        mask = _sphere_mask(shape=(24, 24, 24), center=(12, 12, 12), radius=6)
        raw = mask_to_mesh(mask, np.eye(4), smoothing=smoothing_config_from_options(preset="none"))
        smoothed = mask_to_mesh(
            mask,
            np.eye(4),
            smoothing=smoothing_config_from_options(
                preset="slicer_like",
                iterations=2,
            ),
        )

        self.assertEqual(raw["faces"].shape, smoothed["faces"].shape)
        self.assertTrue(np.all(np.isfinite(smoothed["vertices"])))
        self.assertGreater(float(np.max(np.abs(raw["vertices"] - smoothed["vertices"]))), 0.0)
        raw_bounds = np.array(raw["bounds_mm"])
        smooth_bounds = np.array(smoothed["bounds_mm"])
        self.assertLessEqual(float(np.max(np.abs(raw_bounds - smooth_bounds))), 1.0)

    def test_small_pulp_like_label_uses_reduced_smoothing_iterations(self) -> None:
        smoothing = smoothing_config_from_options(preset="slicer_like")
        effective = effective_smoothing_for_label(
            name="upper_right_central_incisor_pulp_fdi11",
            voxels=200,
            smoothing=smoothing,
        )

        self.assertEqual(effective.iterations, 3)
        self.assertEqual(effective.lambda_value, smoothing.lambda_value)
        self.assertEqual(effective.mu, smoothing.mu)

        group_effective = effective_smoothing_for_group(
            group_name="pulp",
            smoothing=smoothing,
        )
        self.assertEqual(group_effective.iterations, 3)

    def test_surface_preview_writes_offline_html_and_expected_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = root / "case"
            labelmap = (
                case_dir
                / "segmentations"
                / "teeth_experimental"
                / "teeth_multilabel_fullspace.nii.gz"
            )
            _write_synthetic_labelmap(labelmap)

            with mock.patch(
                "totalsegmentator_wrapper_mac.surface_preview.label_name_map",
                return_value=SYNTHETIC_LABELS,
            ):
                summary = run_surface_preview(
                    case_dir=case_dir,
                    smoothing=smoothing_config_from_options(
                        preset="slicer_like",
                        iterations=1,
                    ),
                )

            output_dir = case_dir / "surface_preview"
            self.assertTrue((output_dir / "index.html").exists())
            self.assertTrue((output_dir / "preview_summary.json").exists())
            for name in [
                "all_nonzero_smooth.stl",
                "dental_hard_tissue_smooth.stl",
                "jaws_smooth.stl",
                "pulp_smooth.stl",
            ]:
                self.assertTrue((output_dir / "combined" / name).exists())
            self.assertEqual(summary["label_count"], 4)

            saved_summary = json.loads(
                (output_dir / "preview_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_summary["html_viewer"], str((output_dir / "index.html").resolve()))
            self.assertEqual(saved_summary["viewer"]["renderer"], "webgl")
            self.assertEqual(saved_summary["viewer"]["fallback_renderer"], "canvas2d")
            self.assertEqual(saved_summary["viewer"]["camera_mode_default"], "trackpad")
            self.assertEqual(
                saved_summary["viewer"]["transparent_rendering"],
                "jaw_depth_prepass_front_shell",
            )
            self.assertEqual(saved_summary["preview"]["step_size"], 2)
            preview_opacity = {
                mesh["name"]: mesh["opacity"]
                for mesh in saved_summary["preview"]["meshes"]
            }
            self.assertEqual(preview_opacity["jaws"], 0.35)
            self.assertEqual(preview_opacity["dental_hard_tissue"], 1.0)
            self.assertEqual(preview_opacity["pulp"], 1.0)
            self.assertEqual(preview_opacity["all_nonzero"], 1.0)
            html = (output_dir / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("http://", html)
            self.assertNotIn("https://", html)
            self.assertNotIn("cdn", html.lower())
            self.assertNotIn("<script src=", html.lower())
            self.assertIn("getContext('webgl'", html)
            self.assertIn("TotalSegmentator Wrapper 3Dプレビュー", html)
            self.assertIn("トラックパッド", html)
            self.assertIn("マウス", html)
            self.assertIn("正面", html)
            self.assertIn("全体表示", html)
            self.assertIn("なめらかさ", html)
            self.assertIn("面数", html)
            self.assertIn("meshDisplayName", html)
            self.assertIn("歯", html)
            self.assertIn("顎骨", html)
            self.assertIn("modeTrackpad", html)
            self.assertIn("modeMouse", html)
            self.assertIn("resetCamera", html)
            self.assertIn("fitAll", html)
            self.assertIn("orientationFromYawPitch", html)
            self.assertIn("uDepthNear", html)
            self.assertIn("uDepthFar", html)
            self.assertIn("arcballPoint", html)
            self.assertIn("applyRotation", html)
            self.assertIn("orthonormalizeOrientation", html)
            self.assertIn("applyArcballDragMotion", html)
            self.assertIn("applyArcballWheelMotion", html)
            self.assertIn("MAX_ARCBALL_STEP_PX", html)
            self.assertIn("const fingerDelta = [-delta[0], -delta[1]]", html)
            self.assertIn("columnMajorMat3", html)
            self.assertIn("gl_FrontFacing", html)
            self.assertIn("drawTranslucentDepthPrepass", html)
            self.assertIn("drawTranslucentFrontShell", html)
            self.assertIn("usesFrontShellTransparency", html)
            self.assertIn("gl.colorMask(false, false, false, false)", html)
            self.assertIn("gl.blendFuncSeparate", html)

    def test_surface_preview_records_custom_preview_step_size_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = root / "case"
            labelmap = (
                case_dir
                / "segmentations"
                / "teeth_experimental"
                / "teeth_multilabel_fullspace.nii.gz"
            )
            _write_large_synthetic_labelmap(labelmap)

            with mock.patch(
                "totalsegmentator_wrapper_mac.surface_preview.label_name_map",
                return_value=SYNTHETIC_LABELS,
            ):
                summary = run_surface_preview(
                    case_dir=case_dir,
                    preview_step_size=5,
                    smoothing=smoothing_config_from_options(
                        preset="slicer_like",
                        iterations=1,
                    ),
                )

            self.assertEqual(summary["preview"]["step_size"], 5)
            self.assertEqual(summary["preview"]["warning"], "small structures may be under-sampled")
            saved_summary = json.loads(
                (case_dir / "surface_preview" / "preview_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(saved_summary["preview"]["step_size"], 5)
            self.assertEqual(
                saved_summary["preview"]["warning"],
                "small structures may be under-sampled",
            )

    def test_surface_preview_rejects_invalid_preview_step_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = root / "case"
            labelmap = (
                case_dir
                / "segmentations"
                / "teeth_experimental"
                / "teeth_multilabel_fullspace.nii.gz"
            )
            _write_synthetic_labelmap(labelmap)

            with self.assertRaisesRegex(ValueError, "preview_step_size"):
                run_surface_preview(
                    case_dir=case_dir,
                    preview_step_size=0,
                    smoothing=smoothing_config_from_options(preset="none"),
                )

    def test_surface_preview_builds_craniofacial_arch_jaw_preview_from_raw_masks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = root / "case"
            raw_dir = case_dir / "segmentations" / "raw_totalseg"
            _write_binary_mask(raw_dir / "mandible.nii.gz", (slice(2, 13), slice(2, 15), slice(2, 8)))
            _write_binary_mask(raw_dir / "skull.nii.gz", (slice(11, 22), slice(10, 25), slice(12, 20)))
            _write_binary_mask(raw_dir / "teeth_lower.nii.gz", (slice(6, 11), slice(6, 12), slice(6, 13)))
            _write_binary_mask(raw_dir / "teeth_upper.nii.gz", (slice(14, 20), slice(14, 21), slice(16, 24)))

            summary = run_surface_preview(
                case_dir=case_dir,
                smoothing=smoothing_config_from_options(preset="none"),
            )

            output_dir = case_dir / "surface_preview"
            derived = case_dir / "segmentations" / "derived" / "craniofacial_arch_jaw_multilabel.nii.gz"
            self.assertTrue(derived.exists())
            self.assertTrue((derived.with_name(derived.name + ".labels.json")).exists())
            self.assertTrue((output_dir / "index.html").exists())
            self.assertTrue((output_dir / "combined" / "jaws_smooth.stl").exists())
            self.assertTrue((output_dir / "combined" / "dental_hard_tissue_smooth.stl").exists())
            self.assertEqual(summary["source"]["source"], "craniofacial_raw_totalseg")
            self.assertEqual(summary["source"]["non_empty_mask_count"], 4)
            self.assertEqual(summary["label_count"], 4)
            label_names = {entry["name"] for entry in summary["labels"]}
            self.assertEqual(
                label_names,
                {"lower_jawbone", "upper_jawbone", "lower_teeth", "upper_teeth"},
            )
            groups = {group["name"]: group["labels"] for group in summary["groups"]}
            self.assertEqual(groups["jaws"], [1, 2])
            self.assertEqual(groups["dental_hard_tissue"], [11, 12])
            self.assertEqual(groups["all_nonzero"], [1, 2, 11, 12])


def _write_synthetic_labelmap(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros((28, 28, 28), dtype=np.int16)
    data[3:12, 3:12, 3:8] = 1
    data[14:23, 14:23, 16:21] = 2
    data[8:15, 8:15, 8:16] = 11
    data[10:13, 10:13, 10:14] = 51
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


def _write_large_synthetic_labelmap(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros((72, 72, 72), dtype=np.int16)
    data[8:32, 8:32, 8:24] = 1
    data[40:64, 40:64, 44:60] = 2
    data[24:48, 24:48, 24:52] = 11
    data[30:42, 30:42, 30:46] = 51
    image = nib.Nifti1Image(data, np.eye(4))
    nib.save(image, str(path))
    return path


def _sphere_mask(*, shape: tuple[int, int, int], center: tuple[int, int, int], radius: float) -> np.ndarray:
    grid = np.indices(shape)
    distance = np.sqrt(
        (grid[0] - center[0]) ** 2
        + (grid[1] - center[1]) ** 2
        + (grid[2] - center[2]) ** 2
    )
    return distance <= radius


if __name__ == "__main__":
    unittest.main()
