from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import nibabel as nib
import numpy as np

from totalsegmentator_wrapper_mac.surface_preview import (
    resolve_surface_preview_input,
    effective_smoothing_for_group,
    effective_smoothing_for_label,
    export_labelmap_surfaces,
    is_dental_hard_tissue,
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
    def test_uppercase_fdi_labels_are_visible_as_dental_hard_tissue_by_default(self) -> None:
        self.assertTrue(is_dental_hard_tissue("FDI 11"))

        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "case"
            labelmap = case_dir / "segmentations" / "toothseg" / "toothseg_fdi_multilabel.nii.gz"
            _write_toothseg_labelmap_with_sidecar(labelmap)

            summary = run_surface_preview(
                case_dir=case_dir,
                input_path=labelmap,
                smoothing=smoothing_config_from_options(preset="none"),
            )

            groups = {group["name"]: group["labels"] for group in summary["groups"]}
            self.assertEqual(groups["dental_hard_tissue"], [11, 12])
            preview = {mesh["name"]: mesh for mesh in summary["preview"]["meshes"]}
            self.assertTrue(preview["dental_hard_tissue"]["default_visible"])
            html = (case_dir / "surface_preview" / "index.html").read_text(encoding="utf-8")
            self.assertIn('"name":"dental_hard_tissue","labels":[11,12],"defaultVisible":true', html)
            self.assertIn(
                "const visible = Object.fromEntries(DATA.meshes.map(m => [m.name, !!m.defaultVisible]));",
                html,
            )

    def test_toothseg_smoothing_changes_mesh_without_changing_fdi_labelmap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "case"
            labelmap = case_dir / "segmentations" / "toothseg" / "toothseg_fdi_multilabel.nii.gz"
            _write_toothseg_labelmap_with_sidecar(labelmap)
            before = hashlib.sha256(labelmap.read_bytes()).hexdigest()

            off = run_surface_preview(
                case_dir=case_dir,
                input_path=labelmap,
                output_dir=case_dir / "preview_off",
                smoothing=smoothing_config_from_options(preset="none"),
            )
            on = run_surface_preview(
                case_dir=case_dir,
                input_path=labelmap,
                output_dir=case_dir / "preview_on",
                smoothing=smoothing_config_from_options(preset="slicer_like"),
            )

            after = hashlib.sha256(labelmap.read_bytes()).hexdigest()
            off_stl = Path(next(group for group in off["groups"] if group["name"] == "dental_hard_tissue")["stl"])
            on_stl = Path(next(group for group in on["groups"] if group["name"] == "dental_hard_tissue")["stl"])
            self.assertEqual(before, after)
            self.assertNotEqual(hashlib.sha256(off_stl.read_bytes()).hexdigest(), hashlib.sha256(on_stl.read_bytes()).hexdigest())
            self.assertNotEqual(
                hashlib.sha256((case_dir / "preview_off" / "index.html").read_bytes()).hexdigest(),
                hashlib.sha256((case_dir / "preview_on" / "index.html").read_bytes()).hexdigest(),
            )
            self.assertEqual(off["smoothing"]["preset"], "none")
            self.assertEqual(on["smoothing"]["preset"], "slicer_like")

    def test_resolve_prefers_toothseg_fdi_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            toothseg = case / "segmentations" / "toothseg" / "toothseg_fdi_multilabel.nii.gz"
            toothseg.parent.mkdir(parents=True)
            nib.save(nib.Nifti1Image(np.zeros((4, 4, 4), dtype=np.uint8), np.eye(4)), str(toothseg))

            resolved, metadata = resolve_surface_preview_input(case_dir=case, input_path=None)

            self.assertEqual(resolved, toothseg)
            self.assertEqual(metadata["source"], "toothseg_fdi_multilabel")

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
            self.assertTrue(saved_summary["viewer"]["runtime_smoothing"])
            self.assertEqual(
                saved_summary["viewer"]["runtime_smoothing_presets"],
                ["none", "slicer_like", "medium", "strong"],
            )
            self.assertEqual(saved_summary["viewer"]["material_default"], "rich")
            self.assertEqual(
                saved_summary["viewer"]["material_presets"],
                ["standard", "rich", "realistic", "neutral", "high_contrast"],
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
            self.assertIn("TotalSegmentator 3Dビューアー", html)
            self.assertIn("データ: <code id=\"dataName\"></code>", html)
            self.assertNotIn("inputName", html)
            self.assertIn("形状: <code id=\"geometryModeLabel\"></code>", html)
            self.assertIn("表面平滑化: <code id=\"smoothingModeLabel\"></code>", html)
            self.assertIn("id=\"geometryControl\"", html)
            self.assertIn("id=\"displayControls\"", html)
            self.assertIn("id=\"geometryOriginal\"", html)
            self.assertIn("id=\"geometrySdf\"", html)
            self.assertIn("geometryPresetNames", html)
            self.assertIn("setGeometryPreset", html)
            self.assertIn("updateGeometryButtons", html)
            self.assertIn("applyRawGeometry", html)
            self.assertIn("rebuildMeshBuffers", html)
            self.assertIn("元の形状", html)
            self.assertIn("なめらか補完", html)
            self.assertIn("詳細設定", html)
            self.assertIn("トラックパッド", html)
            self.assertIn("マウス", html)
            self.assertIn("操作方法", html)
            self.assertIn("標準方向", html)
            self.assertIn("全体に合わせる", html)
            self.assertIn("表面平滑化", html)
            self.assertIn("id=\"smoothingPreset\"", html)
            self.assertIn("smoothingModeLabel", html)
            self.assertNotIn("getElementById('smooth')", html)
            self.assertIn("smoothingPresets", html)
            self.assertIn("applySmoothingPreset", html)
            self.assertIn("setSmoothingPreset", html)
            self.assertIn("smoothMeshVertices", html)
            self.assertIn("meshAdjacency", html)
            self.assertIn("laplacianSmoothStep", html)
            self.assertIn("refreshMeshBuffers", html)
            self.assertIn("質感", html)
            self.assertIn("id=\"materialPreset\"", html)
            self.assertIn("materialPreset", html)
            self.assertIn("materialModeLabel", html)
            self.assertIn("applyMaterialMode", html)
            self.assertIn("setMaterialMode", html)
            self.assertIn("uRimStrength", html)
            self.assertIn("uAmbient", html)
            self.assertIn("uWrapDiffuse", html)
            self.assertIn("uEmission", html)
            self.assertIn("uSubsurface", html)
            self.assertIn("リッチ", html)
            self.assertIn("リアル", html)
            self.assertIn("ニュートラル", html)
            self.assertIn("高コントラスト", html)
            self.assertNotIn("臨床", html)
            self.assertIn("ポリゴン数", html)
            self.assertIn("meshDisplayName", html)
            self.assertIn("歯", html)
            self.assertIn("顎骨", html)
            self.assertIn("歯髄腔（推定）", html)
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
            defined_ids = set(re.findall(r'id="([^"]+)"', html))
            referenced_static_ids = set(re.findall(r"getElementById\('([^']+)'\)", html))
            self.assertLessEqual(referenced_static_ids, defined_ids)

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


def _write_toothseg_labelmap_with_sidecar(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros((36, 36, 36), dtype=np.uint8)
    data[7:20, 8:21, 7:25] = 11
    data[20:31, 15:29, 10:30] = 12
    nib.save(nib.Nifti1Image(data, np.eye(4)), str(path))
    path.with_name(path.name + ".labels.json").write_text(
        json.dumps({"labels": {"11": "FDI 11", "12": "FDI 12"}}),
        encoding="utf-8",
    )
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
