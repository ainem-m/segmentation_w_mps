from __future__ import annotations

import hashlib
import json
import re
import struct
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
    run_surface_preview_stl_only,
    smoothing_config_from_options,
    write_binary_stl,
    _externalize_viewer_script,
    _html_document,
)
from scripts.build_model_comparison_viewer import (
    build_comparison_viewer,
    read_payload,
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
            index_html = (
                case_dir / "surface_preview" / "index.html"
            ).read_text(encoding="utf-8")
            bundle = (
                case_dir / "surface_preview" / "viewer_bundle.js"
            ).read_text(encoding="utf-8")
            self.assertIn(
                '"name":"dental_hard_tissue","labels":[11,12],"defaultVisible":true',
                index_html,
            )
            self.assertIn(
                "const visible = Object.fromEntries(DATA.meshes.map(m => [m.name, !!m.defaultVisible]));",
                bundle,
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
                hashlib.sha256(
                    (case_dir / "preview_off" / "index.html").read_bytes()
                ).hexdigest(),
                hashlib.sha256(
                    (case_dir / "preview_on" / "index.html").read_bytes()
                ).hexdigest(),
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

    def test_vectorized_binary_stl_preserves_scalar_geometry_and_normals(self) -> None:
        vertices = np.array(
            [
                [0.125, 0.25, -0.5],
                [1.75, -0.125, 0.375],
                [-0.25, 1.5, 0.625],
                [0.375, -0.75, 1.25],
            ],
            dtype=np.float32,
        )
        faces = np.array(
            [
                [0, 1, 2],
                [0, 2, 3],
                [0, 0, 0],
            ],
            dtype=np.uint32,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual.stl"
            expected = root / "expected.stl"

            write_binary_stl(actual, vertices, faces, solid_name="byte_match")
            _write_scalar_reference_stl(
                expected,
                vertices,
                faces,
                solid_name="byte_match",
            )

            actual_bytes = actual.read_bytes()
            expected_bytes = expected.read_bytes()
            self.assertEqual(actual_bytes[:84], expected_bytes[:84])
            record_dtype = np.dtype(
                [
                    ("normal", "<f4", (3,)),
                    ("vertices", "<f4", (3, 3)),
                    ("attribute", "<u2"),
                ]
            )
            actual_records = np.frombuffer(actual_bytes, dtype=record_dtype, offset=84)
            expected_records = np.frombuffer(expected_bytes, dtype=record_dtype, offset=84)
            np.testing.assert_array_equal(
                actual_records["vertices"],
                expected_records["vertices"],
            )
            np.testing.assert_array_equal(
                actual_records["attribute"],
                expected_records["attribute"],
            )
            np.testing.assert_allclose(
                actual_records["normal"],
                expected_records["normal"],
                rtol=1e-6,
                atol=2e-7,
            )

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
            self.assertTrue((output_dir / "viewer_bundle.js").exists())
            self.assertTrue((output_dir / "preview_summary.json").exists())
            self.assertTrue((output_dir / "performance_profile.json").exists())
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
            profile = json.loads(
                (output_dir / "performance_profile.json").read_text(encoding="utf-8")
            )
            self.assertGreater(profile["total_seconds"], 0.0)
            self.assertLessEqual(
                profile["milestones_seconds"]["preview_ready"],
                profile["milestones_seconds"]["all_outputs_complete"],
            )
            self.assertEqual(
                {
                    "nifti_load",
                    "label_scan_and_mask",
                    "marching_cubes",
                    "smoothing",
                    "stl_write",
                    "group_mesh_generation",
                    "browser_mesh_generation",
                    "json_html_write",
                },
                set(profile["stages_seconds"]),
            )
            self.assertEqual(saved_summary["html_viewer"], str((output_dir / "index.html").resolve()))
            self.assertEqual(
                saved_summary["viewer"]["script_bundle"],
                str((output_dir / "viewer_bundle.js").resolve()),
            )
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
            index_html = (output_dir / "index.html").read_text(encoding="utf-8")
            bundle_js = (output_dir / "viewer_bundle.js").read_text(encoding="utf-8")
            html = index_html + "\n" + bundle_js
            self.assertNotIn("http://", html)
            self.assertNotIn("https://", html)
            self.assertNotIn("cdn", html.lower())
            self.assertNotIn("<script src=", html.lower())
            bundle_digest = hashlib.sha256(bundle_js.encode("utf-8")).hexdigest()[:16]
            self.assertIn(
                f"viewer_bundle.js?sha256={bundle_digest}",
                index_html,
            )
            self.assertIn(
                '<script id="viewerData" type="application/json">',
                index_html,
            )
            self.assertNotIn("const DATA = ", index_html)
            self.assertIn(
                "const DATA = JSON.parse(document.getElementById('viewerData').textContent);",
                bundle_js,
            )
            self.assertNotIn('"vertices":[', bundle_js)
            self.assertIn('"vertices":[', index_html)
            self.assertIn("getContext('webgl'", html)
            self.assertIn("TotalSegmentator 3Dビューアー", html)
            self.assertIn(
                'id="loadingOverlay" role="status" aria-live="polite" aria-busy="true"',
                html,
            )
            self.assertIn("3Dデータを読み込んでいます", html)
            self.assertIn("3D表示を準備しています", html)
            self.assertIn("3D表示を準備できませんでした", html)
            self.assertIn("必要な表示データを読み込めませんでした。", index_html)
            self.assertIn("window.addEventListener('error'", index_html)
            self.assertIn("window.addEventListener('unhandledrejection'", index_html)
            self.assertIn("3D表示の準備中にエラーが発生しました。", index_html)
            self.assertIn("document.createElement('script')", index_html)
            self.assertIn("scheduleViewerInitialization", html)
            self.assertIn("setTimeout(initializeViewer, 0)", html)
            self.assertIn("requestAnimationFrame(finishViewerLoading)", html)
            self.assertIn("let preparedMeshes = [];", html)
            self.assertNotIn(
                "const preparedMeshes = DATA.meshes.map(prepareMesh);",
                html,
            )
            self.assertLess(
                index_html.index('id="loadingOverlay"'),
                index_html.index("document.createElement('script')"),
            )
            initialize_start = html.index("function initializeViewer()")
            initialize_end = html.index(
                "\nfunction setLoadingPhase",
                initialize_start,
            )
            initialize_body = html[initialize_start:initialize_end]
            self.assertLess(
                initialize_body.index("preparedMeshes = DATA.meshes.map(prepareMesh);"),
                initialize_body.index("const layers = document.getElementById('layers');"),
            )
            self.assertLess(
                initialize_body.index("resize();"),
                initialize_body.index("requestAnimationFrame(finishViewerLoading);"),
            )
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

    def test_deferred_stl_keeps_browser_preview_ready_and_finishes_details(self) -> None:
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
            output_dir = case_dir / "surface_preview"

            with mock.patch(
                "totalsegmentator_wrapper_mac.surface_preview.label_name_map",
                return_value=SYNTHETIC_LABELS,
            ):
                preview = run_surface_preview(
                    case_dir=case_dir,
                    smoothing=smoothing_config_from_options(preset="none"),
                    detailed_stl=False,
                )

                self.assertEqual(preview["stl_generation"]["status"], "pending")
                self.assertTrue((output_dir / "index.html").exists())
                self.assertTrue((output_dir / "viewer_bundle.js").exists())
                self.assertFalse((output_dir / "labels").exists())
                html_hash = hashlib.sha256(
                    (output_dir / "index.html").read_bytes()
                ).hexdigest()
                bundle_hash = hashlib.sha256(
                    (output_dir / "viewer_bundle.js").read_bytes()
                ).hexdigest()

                finished = run_surface_preview_stl_only(
                    case_dir=case_dir,
                    input_path=labelmap,
                    output_dir=output_dir,
                    smoothing=smoothing_config_from_options(preset="none"),
                )

            self.assertEqual(finished["stl_generation"]["status"], "complete")
            self.assertEqual(
                hashlib.sha256((output_dir / "index.html").read_bytes()).hexdigest(),
                html_hash,
            )
            self.assertEqual(
                hashlib.sha256((output_dir / "viewer_bundle.js").read_bytes()).hexdigest(),
                bundle_hash,
            )
            self.assertTrue((output_dir / "labels").is_dir())
            self.assertTrue((output_dir / "combined").is_dir())
            for entry in finished["labels"] + finished["groups"]:
                self.assertGreater(entry["vertices"], 0)
                self.assertGreater(entry["triangles"], 0)
                self.assertTrue(np.all(np.isfinite(np.asarray(entry["bounds_mm"]))))

    def test_comparison_payload_reader_supports_inline_and_external_viewers(self) -> None:
        payload = {
            "dataLabel": "test <script>",
            "labelCount": 1,
            "smoothing": {"preset": "none"},
            "meshes": [],
        }
        inline_document = _html_document(payload)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inline_path = root / "inline.html"
            inline_path.write_text(inline_document, encoding="utf-8")
            self.assertEqual(read_payload(inline_path), payload)

            index_html, bundle_js = _externalize_viewer_script(
                inline_document,
                bundle_filename="viewer_bundle.js",
            )
            bundle_digest = hashlib.sha256(bundle_js.encode("utf-8")).hexdigest()[:16]
            self.assertIn(
                f"viewer_bundle.js?sha256={bundle_digest}",
                index_html,
            )
            external_path = root / "index.html"
            external_path.write_text(index_html, encoding="utf-8")
            (root / "viewer_bundle.js").write_text(bundle_js, encoding="utf-8")
            self.assertNotIn('"test <script>"', index_html)
            self.assertIn('"test \\u003cscript>"', index_html)
            self.assertEqual(read_payload(external_path), payload)

            comparison_path = root / "comparison.html"
            build_comparison_viewer(
                sources=[("test", "Test", external_path)],
                output=comparison_path,
            )
            comparison_html = comparison_path.read_text(encoding="utf-8")
            initialize_start = comparison_html.index("function initializeViewer()")
            initialize_end = comparison_html.index(
                "\nfunction setLoadingPhase",
                initialize_start,
            )
            self.assertNotIn(
                "function selectComparisonModel",
                comparison_html[initialize_start:initialize_end],
            )
            self.assertLess(
                comparison_html.index("function selectComparisonModel"),
                comparison_html.index("function setInputMode"),
            )
            self.assertIn("window.showViewerLoadError", comparison_html)

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

    def test_xray_display_mode_contract_is_encoded_in_offline_viewer(self) -> None:
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
                    smoothing=smoothing_config_from_options(preset="none"),
                )

            viewer = summary["viewer"]
            self.assertEqual(viewer["display_mode_default"], "xray")
            self.assertEqual(viewer["display_modes"], ["normal", "wireframe", "xray"])
            self.assertEqual(viewer["xray"]["surface_color"], [1.0, 1.0, 1.0])
            self.assertEqual(viewer["xray"]["base_alpha"], 0.18)
            self.assertEqual(viewer["xray"]["outline_alpha"], 0.58)
            self.assertEqual(viewer["xray"]["outline_radius_physical_pixels"], 1)
            self.assertEqual(viewer["xray"]["background"], "#313432")
            self.assertEqual(viewer["xray"]["compositing"], "back_then_front")
            self.assertEqual(
                viewer["xray"]["target_strategy"],
                "translucent_layers_else_all",
            )
            self.assertFalse(viewer["xray"]["depth_write"])

            output_dir = case_dir / "surface_preview"
            index_html = (output_dir / "index.html").read_text(encoding="utf-8")
            bundle_js = (output_dir / "viewer_bundle.js").read_text(encoding="utf-8")
            html = index_html + "\n" + bundle_js
            for element_id in ["displayNormal", "displayWireframe", "displayXray"]:
                self.assertIn(f'id="{element_id}"', html)
            self.assertIn('id="panelToggle"', html)
            self.assertIn('id="panelBackdrop"', html)
            self.assertIn("@media (max-width: 900px)", html)
            self.assertIn("#app.panel-open #panel", html)
            self.assertIn("function setPanelOpen(open)", html)
            self.assertIn("const DISPLAY_MODE_ORDER = ['normal', 'wireframe', 'xray'];", html)
            self.assertIn("DATA.displayMode || 'xray'", html)
            self.assertIn("const activePointers = new Map();", html)
            self.assertIn("function applyTwoPointerGesture(previousPair, currentPair)", html)
            self.assertIn("zoomByLogDelta(Math.log(currentDistance / previousDistance));", html)
            self.assertIn("camera.pan[0] += currentCenter[0] - previousCenter[0];", html)
            self.assertIn("function setDisplayMode(name)", html)
            self.assertIn("function drawXrayShells(meshes)", html)
            self.assertIn("function selectXrayTargets(meshes)", html)
            self.assertIn(
                "const translucent = meshes.filter(mesh => mesh.material.opacity < 0.995);",
                html,
            )
            self.assertIn(
                "visibleMeshes.filter(mesh => !xrayTargets.has(mesh))",
                html,
            )
            self.assertIn("gl.cullFace(gl.FRONT)", html)
            self.assertIn("gl.cullFace(gl.BACK)", html)
            self.assertIn("gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA)", html)
            self.assertIn("gl.depthMask(false)", html)
            self.assertIn("float facing = abs(normalize(vNormal).z);", html)
            self.assertIn("float rim = pow(clamp(1.0 - facing, 0.0, 1.0), 2.0);", html)
            self.assertIn("vec3 xrayColor = mix(vec3(0.78), vec3(1.0), rim);", html)
            self.assertIn(
                "float xrayAlpha = clamp(0.18 * mix(0.55, 3.0, rim), 0.0, 0.62);",
                html,
            )
            self.assertIn("function drawXrayOutlineMask", html)
            self.assertIn("function compositeXrayOutline", html)
            self.assertIn("gl.uniform2f(outlineUniforms.texelSize, 1 / canvas.width, 1 / canvas.height)", html)
            self.assertIn("vec4(1.0, 1.0, 1.0, edge * 0.58)", html)
            self.assertIn("canvas.width = Math.max(1, Math.floor(rect.width * devicePixelRatio));", html)
            self.assertIn("gl.clearColor(0.1921569, 0.2039216, 0.1960784, 1.0)", html)
            self.assertIn("function drawDepthAwareGrid", html)
            self.assertIn("function drawSelectionAndGizmoOverlay", html)
            self.assertIn("uniform float uBroadSpecular;", html)
            self.assertIn("uniform float uGlazeStrength;", html)
            self.assertIn("vec3 fillLight = normalize(vec3(-0.58, 0.14, 0.80));", html)
            self.assertNotIn("vec3 backLight", html)
            self.assertIn("float sharpSpec = pow(sharpSpecBase, uShininess) * uSpecular;", html)
            self.assertIn(
                "float broadSpec = pow(broadSpecBase, max(uShininess * 0.12, 2.0)) * uBroadSpecular;",
                html,
            )
            self.assertIn("color = [0.965, 0.945, 0.88];", html)
            self.assertIn("specular = 1.10;", html)
            self.assertIn("shininess = 260;", html)
            self.assertIn("broadSpecular = 0.24;", html)
            self.assertIn("glazeStrength = 0.48;", html)
            self.assertIn("subsurface = 0.052;", html)
            self.assertIn("const porcelainRim =", html)
            self.assertIn("float studioSoftBox(", html)
            self.assertIn("vec3 proceduralStudio(vec3 reflectionDirection)", html)
            self.assertIn(
                "vec3 reflectionDirection = normalize(reflect(-viewDir, normal));",
                html,
            )
            self.assertIn(
                "float glazeFresnel = 0.18 + 0.82 * pow(1.0 - viewFacing, 2.5);",
                html,
            )
            self.assertIn("leftSoftBox * 0.92", html)
            self.assertIn("rightSoftBox * 0.58", html)
            self.assertIn("studio *= mix(1.0, 0.38, floorMask);", html)
            self.assertIn("const leftStudioBand =", html)
            self.assertIn("float studioLuma = dot(studioSample", html)
            self.assertIn("vec3 porcelainReflection = mix(", html)
            self.assertIn("color * 0.88", html)
            self.assertIn("vec3(1.0, 0.985, 0.94)", html)
            self.assertIn(
                "color = mix(color, porcelainReflection, glazeMix);",
                html,
            )
            self.assertIn("float crispHighlight = clamp(", html)
            self.assertIn(
                "sharpSpec * 1.35 * step(0.001, uGlazeStrength)",
                html,
            )
            self.assertIn("color = min(color + vec3(crispHighlight), vec3(1.0));", html)
            self.assertIn("const studioLevel =", html)
            self.assertIn("baseShade * (1 - glazeMix)", html)
            draw_body = html[
                html.index("function drawWebGl()") : html.index(
                    "function applySceneUniforms"
                )
            ]
            expected_order = [
                "for (const mesh of opaque)",
                "drawDepthAwareGrid",
                "drawXrayShells",
                "drawXrayOutlineMask",
                "compositeXrayOutline",
                "drawSelectionAndGizmoOverlay",
            ]
            positions = [draw_body.index(token) for token in expected_order]
            self.assertEqual(positions, sorted(positions))
            self.assertAlmostEqual(0.18 * 0.55, 0.099)
            self.assertAlmostEqual(0.18 * 3.0, 0.54)
            self.assertLessEqual(0.18 * 3.0, 0.62)

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
            self.assertTrue((output_dir / "viewer_bundle.js").exists())
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


def _write_scalar_reference_stl(
    path: Path,
    vertices: np.ndarray,
    faces: np.ndarray,
    *,
    solid_name: str,
) -> None:
    header = f"TotalSegWrapper {solid_name}".encode("ascii", errors="ignore")[:80]
    header = header + b" " * (80 - len(header))
    with path.open("wb") as file:
        file.write(header)
        file.write(struct.pack("<I", int(faces.shape[0])))
        for tri in faces:
            points = vertices[tri]
            normal = np.cross(points[1] - points[0], points[2] - points[0])
            norm = float(np.linalg.norm(normal))
            if norm > 0:
                normal = normal / norm
            else:
                normal = np.zeros(3, dtype=np.float32)
            file.write(struct.pack("<3f", *normal.astype(np.float32)))
            file.write(struct.pack("<9f", *points.astype(np.float32).reshape(-1)))
            file.write(struct.pack("<H", 0))


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
