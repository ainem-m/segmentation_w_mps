from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from totalsegmentator_wrapper_mac.ios_meshsegnet import (
    checkpoint_last_metric,
    checkpoint_state_dict,
    custom_model_metadata,
    export_results,
    fdi_for_jaw,
    output_prefix_for_jaw,
    parse_args,
)


class IOSMeshSegNetCustomModelTests(unittest.TestCase):
    def test_fdi_mapping_is_jaw_specific_and_strict(self) -> None:
        self.assertEqual(
            [fdi_for_jaw(class_id, "upper") for class_id in range(1, 17)],
            list(range(11, 19)) + list(range(21, 29)),
        )
        self.assertEqual(
            [fdi_for_jaw(class_id, "lower") for class_id in range(1, 17)],
            list(range(31, 39)) + list(range(41, 49)),
        )
        with self.assertRaisesRegex(ValueError, "jaw"):
            fdi_for_jaw(1, "unknown")

    def test_lower_jaw_is_accepted_by_cli(self) -> None:
        args = parse_args(
            [
                "--input",
                "lower.ply",
                "--model",
                "model.tar",
                "--output-dir",
                "out",
                "--jaw",
                "lower",
            ]
        )

        self.assertEqual(args.jaw, "lower")
        self.assertEqual(output_prefix_for_jaw("lower"), "ios_lower_meshsegnet")

    def test_output_prefix_rejects_unknown_jaw(self) -> None:
        with self.assertRaisesRegex(ValueError, "jaw"):
            output_prefix_for_jaw("unknown")

    def test_custom_model_requires_explicit_opt_in(self) -> None:
        args = parse_args(
            [
                "--input",
                "scan.ply",
                "--model",
                "custom.tar",
                "--output-dir",
                "out",
                "--allow-custom-model",
            ]
        )

        self.assertEqual(args.model, Path("custom.tar"))
        self.assertTrue(args.allow_custom_model)

    def test_custom_model_metadata_does_not_claim_standard_license(self) -> None:
        metadata = custom_model_metadata(
            Path("/tmp/custom.tar"),
            "a" * 64,
        )

        self.assertEqual(metadata["source"], "user-provided")
        self.assertEqual(metadata["license"], "not-declared")
        self.assertEqual(metadata["sha256"], "a" * 64)
        self.assertFalse(metadata["standard_checkpoint"])

    def test_checkpoint_accepts_wrapped_raw_and_dataparallel_state_dicts(self) -> None:
        wrapped = {"model_state_dict": {"layer.weight": object()}, "epoch": 3}
        raw = {"layer.weight": object()}
        parallel = {"model_state_dict": {"module.layer.weight": object()}}

        self.assertEqual(list(checkpoint_state_dict(wrapped)), ["layer.weight"])
        self.assertEqual(list(checkpoint_state_dict(raw)), ["layer.weight"])
        self.assertEqual(list(checkpoint_state_dict(parallel)), ["layer.weight"])

    def test_checkpoint_rejects_missing_state_dict(self) -> None:
        with self.assertRaisesRegex(ValueError, "model_state_dict"):
            checkpoint_state_dict({"model_state_dict": {}})

    def test_optional_training_metric_may_be_absent_or_scalar(self) -> None:
        self.assertIsNone(checkpoint_last_metric({}, "val_mdsc"))
        self.assertEqual(checkpoint_last_metric({"val_mdsc": 0.75}, "val_mdsc"), 0.75)
        self.assertEqual(
            checkpoint_last_metric({"val_mdsc": [0.5, 0.8]}, "val_mdsc"),
            0.8,
        )

    def test_export_results_writes_label_zero_as_gingiva_stl(self) -> None:
        gingiva = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        tooth = trimesh.creation.box(
            extents=(1.0, 1.0, 1.0),
            transform=trimesh.transformations.translation_matrix(
                (3.0, 0.0, 0.0)
            ),
        )
        mesh = trimesh.util.concatenate((gingiva, tooth))
        face_labels = np.concatenate(
            (
                np.zeros(len(gingiva.faces), dtype=np.int16),
                np.ones(len(tooth.faces), dtype=np.int16),
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            result = export_results(
                mesh,
                mesh.copy(),
                face_labels,
                Path(tmp),
                "upper",
            )

            gingiva_result = result["gingiva"]
            self.assertTrue(gingiva_result["present"])
            self.assertEqual(gingiva_result["label_id"], 0)
            self.assertEqual(
                gingiva_result["interpretation"],
                "gingiva-or-background-candidate",
            )
            gingiva_path = Path(gingiva_result["stl"])
            self.assertEqual(gingiva_path.name, "gingiva.stl")
            self.assertTrue(gingiva_path.is_file())
            exported = trimesh.load(gingiva_path, process=False, force="mesh")
            self.assertGreater(len(exported.faces), 0)

    def test_export_results_removes_stale_gingiva_when_label_zero_is_absent(self) -> None:
        mesh = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        face_labels = np.ones(len(mesh.faces), dtype=np.int16)

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            stale = output_dir / "gingiva.stl"
            stale.write_bytes(b"stale")
            teeth_dir = output_dir / "teeth_stl"
            teeth_dir.mkdir()
            stale_tooth = teeth_dir / "stale_previous_run.stl"
            stale_tooth.write_bytes(b"stale")

            result = export_results(
                mesh,
                mesh.copy(),
                face_labels,
                output_dir,
                "upper",
            )

            self.assertFalse(result["gingiva"]["present"])
            self.assertIsNone(result["gingiva"]["stl"])
            self.assertFalse(stale.exists())
            self.assertFalse(stale_tooth.exists())

    def test_export_results_replaces_only_gingiva_symlink(self) -> None:
        gingiva = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        tooth = trimesh.creation.box(
            extents=(1.0, 1.0, 1.0),
            transform=trimesh.transformations.translation_matrix((3.0, 0.0, 0.0)),
        )
        combined = trimesh.util.concatenate((gingiva, tooth))

        for label_zero_present in (True, False):
            with self.subTest(label_zero_present=label_zero_present), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                output_dir = root / "output"
                output_dir.mkdir()
                protected_target = root / "protected-target.stl"
                protected_target.write_bytes(b"must remain unchanged")
                gingiva_path = output_dir / "gingiva.stl"
                gingiva_path.symlink_to(protected_target)

                if label_zero_present:
                    mesh = combined
                    face_labels = np.concatenate(
                        (
                            np.zeros(len(gingiva.faces), dtype=np.int16),
                            np.ones(len(tooth.faces), dtype=np.int16),
                        )
                    )
                else:
                    mesh = tooth
                    face_labels = np.ones(len(tooth.faces), dtype=np.int16)

                result = export_results(
                    mesh,
                    mesh.copy(),
                    face_labels,
                    output_dir,
                    "upper",
                )

                self.assertEqual(protected_target.read_bytes(), b"must remain unchanged")
                self.assertFalse(gingiva_path.is_symlink())
                self.assertEqual(
                    set(result["gingiva"]),
                    {
                        "present",
                        "label_id",
                        "interpretation",
                        "face_count",
                        "surface_area",
                        "stl",
                    },
                )
                self.assertEqual(
                    [tooth_result["fdi"] for tooth_result in result["teeth"]],
                    [11],
                )
                self.assertTrue((output_dir / "teeth_stl" / "tooth_11.stl").is_file())
                if label_zero_present:
                    self.assertTrue(gingiva_path.is_file())
                    self.assertEqual(result["gingiva"]["stl"], str(gingiva_path.resolve()))
                    self.assertTrue(result["gingiva"]["present"])
                else:
                    self.assertFalse(gingiva_path.exists())
                    self.assertIsNone(result["gingiva"]["stl"])
                    self.assertFalse(result["gingiva"]["present"])

    def test_export_results_rejects_symlinked_teeth_directory(self) -> None:
        mesh = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        face_labels = np.ones(len(mesh.faces), dtype=np.int16)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "output"
            external = root / "external"
            output_dir.mkdir()
            external.mkdir()
            victim = external / "must_not_be_removed.stl"
            victim.write_bytes(b"keep")
            (output_dir / "teeth_stl").symlink_to(external, target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "teeth_stl"):
                export_results(
                    mesh,
                    mesh.copy(),
                    face_labels,
                    output_dir,
                    "upper",
                )

            self.assertEqual(victim.read_bytes(), b"keep")

    def test_export_results_rejects_symlinked_output_directory(self) -> None:
        mesh = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        face_labels = np.ones(len(mesh.faces), dtype=np.int16)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = root / "external"
            external.mkdir()
            victim = external / "gingiva.stl"
            victim.write_bytes(b"keep")
            output_dir = root / "output"
            output_dir.symlink_to(external, target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "output directory"):
                export_results(
                    mesh,
                    mesh.copy(),
                    face_labels,
                    output_dir,
                    "upper",
                )

            self.assertEqual(victim.read_bytes(), b"keep")
