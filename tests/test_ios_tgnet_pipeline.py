import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from totalsegmentator_wrapper_mac.ios_tgnet import (
    _export,
    _baps_indices,
    _instance_tooth_types,
    _select_instances,
    _validated_fdi_assignment,
    assign_fdi_by_tooth_type,
    assign_fdi_by_arch_position,
    inferred_pipeline_metadata,
    parse_args,
)


class IOSTGNetPipelineTests(unittest.TestCase):
    def test_upper_arch_assignment_is_complete_and_unique(self) -> None:
        centers = np.column_stack(
            (np.linspace(-1.0, 1.0, 16), np.zeros(16), np.zeros(16))
        )
        mapping = assign_fdi_by_arch_position(centers, "upper")
        self.assertEqual(len(mapping), 16)
        self.assertEqual(set(mapping), set(range(11, 19)) | set(range(21, 29)))

    def test_arch_assignment_never_emits_a_ninth_tooth_per_side(self) -> None:
        centers = np.column_stack(
            (np.ones(9), np.linspace(-1.0, 1.0, 9), np.zeros(9))
        )
        mapping = assign_fdi_by_arch_position(centers, "upper")
        self.assertNotIn(19, mapping)
        self.assertEqual(mapping.count(0), 1)

    def test_product_fdi_assignment_rejects_unmappable_instances(self) -> None:
        centers = np.column_stack(
            (np.ones(9), np.linspace(-1.0, 1.0, 9), np.zeros(9))
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "reserved or invalid tooth-type class",
        ):
            _validated_fdi_assignment(
                centers,
                list(range(1, 10)),
                "upper",
                patient_right_is_positive_x=True,
            )

    def test_instance_tooth_type_uses_majority_vote_from_pgm_labels(self) -> None:
        instance_labels = np.asarray([1, 1, 1, 2, 2, 2, 0])
        point_types = np.asarray([3, 3, 4, 6, 6, 5, 8])
        self.assertEqual(
            _instance_tooth_types(
                instance_labels,
                point_types,
                instance_count=2,
            ),
            [3, 6],
        )

    def test_tooth_type_assignment_preserves_missing_teeth(self) -> None:
        centers = np.asarray(
            [
                [1.0, 0.0, 0.0],
                [1.0, 1.0, 0.0],
                [-1.0, 0.0, 0.0],
                [-1.0, 1.0, 0.0],
            ]
        )
        self.assertEqual(
            assign_fdi_by_tooth_type(
                centers,
                [1, 3, 2, 7],
                "upper",
                patient_right_is_positive_x=True,
            ),
            [11, 13, 22, 27],
        )

    def test_tooth_type_assignment_rejects_reserved_classes(self) -> None:
        centers = np.asarray([[1.0, 0.0, 0.0]])
        for reserved in (0, 9):
            with self.subTest(reserved=reserved), self.assertRaisesRegex(
                RuntimeError,
                "reserved or invalid tooth-type class",
            ):
                assign_fdi_by_tooth_type(
                    centers,
                    [reserved],
                    "upper",
                    patient_right_is_positive_x=True,
                )

    def test_tooth_type_assignment_rejects_duplicate_fdi(self) -> None:
        centers = np.asarray(
            [[1.0, 0.0, 0.0], [1.0, 1.0, 0.0]]
        )
        with self.assertRaisesRegex(RuntimeError, "duplicate FDI"):
            assign_fdi_by_tooth_type(
                centers,
                [4, 4],
                "upper",
                patient_right_is_positive_x=True,
            )

    def test_inferred_values_are_not_presented_as_paper_constants(self) -> None:
        metadata = inferred_pipeline_metadata()
        self.assertEqual(metadata["status"], "paper-derived-with-inferences")
        self.assertIn("dbscan_epsilon", metadata["inferred_parameters"])
        self.assertIn("coordinate_normalization", metadata["inferred_parameters"])
        self.assertNotIn("BAPS", metadata["not_implemented"])

    def test_inferred_metadata_records_actual_grouping_arguments(self) -> None:
        metadata = inferred_pipeline_metadata(
            coordinate_scale=0.4,
            dbscan_epsilon=0.046,
            dbscan_min_points=14,
            mean_shift_bandwidth=0.094,
            minimum_cluster_points=25,
            maximum_clusters=23,
        )
        inferred = metadata["inferred_parameters"]
        self.assertEqual(inferred["coordinate_scale"], 0.4)
        self.assertEqual(inferred["dbscan_epsilon"], 0.046)
        self.assertEqual(inferred["dbscan_min_points"], 14)
        self.assertEqual(inferred["mean_shift_bandwidth"], 0.094)
        self.assertEqual(inferred["minimum_cluster_points"], 25)
        self.assertEqual(inferred["maximum_clusters"], 23)
        self.assertNotIn("arch_plane_filter", inferred)
        self.assertNotIn("arch_center_nms_distance", inferred)

    def test_product_defaults_use_the_isolated_gt_grouping_selection(self) -> None:
        args = parse_args(
            [
                "--input",
                "scan.ply",
                "--model",
                "model.h5",
                "--output-dir",
                "output",
                "--jaw",
                "upper",
            ]
        )
        self.assertAlmostEqual(args.coordinate_scale, 1.0)
        self.assertAlmostEqual(args.dbscan_epsilon, 0.04244754504798836)
        self.assertEqual(args.dbscan_min_points, 4)
        self.assertAlmostEqual(args.mean_shift_bandwidth, 0.0912189568837504)
        self.assertEqual(args.minimum_cluster_points, 33)
        self.assertEqual(args.maximum_clusters, 16)

    def test_mean_shift_does_not_recombine_valid_dbscan_clusters(self) -> None:
        first = np.column_stack(
            (
                np.linspace(-0.005, 0.005, 50),
                np.zeros(50),
                np.zeros(50),
            )
        )
        second = first + np.array([0.15, 0.0, 0.0])
        shifted = np.concatenate((first, second)).astype(np.float32)
        _, clusters = _select_instances(
            shifted,
            np.ones(len(shifted), dtype=bool),
            epsilon=0.03,
            min_points=4,
            mean_shift_bandwidth=0.30,
        )
        self.assertEqual(sorted(map(len, clusters)), [50, 50])

    def test_grouping_candidate_limit_is_explicit(self) -> None:
        first = np.column_stack(
            (np.linspace(-0.005, 0.005, 50), np.zeros(50), np.zeros(50))
        )
        second = first + np.array([0.15, 0.0, 0.0])
        _, clusters = _select_instances(
            np.concatenate((first, second)).astype(np.float32),
            np.ones(100, dtype=bool),
            epsilon=0.03,
            min_points=4,
            mean_shift_bandwidth=0.30,
            minimum_cluster_points=20,
            maximum_clusters=1,
        )
        self.assertEqual(len(clusters), 1)

    def test_baps_returns_unique_global_and_boundary_samples(self) -> None:
        points = np.column_stack(
            (np.linspace(0, 1, 128), np.zeros(128), np.zeros(128))
        ).astype(np.float32)
        labels = np.zeros(128, dtype=np.int16)
        labels[64:] = 1
        indices, metadata, boundary = _baps_indices(
            points, labels, global_count=8, boundary_count=16
        )
        self.assertEqual(len(indices), 24)
        self.assertEqual(len(np.unique(indices)), 24)
        self.assertGreater(metadata["boundary_vertices"], 0)
        self.assertEqual(int(boundary.sum()), metadata["boundary_vertices"])

    def test_export_writes_label_zero_as_gingiva_stl(self) -> None:
        mesh = trimesh.Trimesh(
            vertices=np.asarray(
                [
                    [0.0, 0.0, 0.0],
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [2.0, 0.0, 0.0],
                    [3.0, 0.0, 0.0],
                    [2.0, 1.0, 0.0],
                ]
            ),
            faces=np.asarray([[0, 1, 2], [3, 4, 5]]),
            process=False,
        )
        vertex_labels = np.asarray([0, 0, 0, 1, 1, 1], dtype=np.int16)

        with tempfile.TemporaryDirectory() as tmp:
            result = _export(mesh, vertex_labels, {1: 11}, Path(tmp))

            gingiva = result["gingiva"]
            self.assertTrue(gingiva["present"])
            self.assertEqual(gingiva["label_id"], 0)
            self.assertEqual(gingiva["interpretation"], "gingiva")
            gingiva_path = Path(gingiva["stl"])
            self.assertEqual(gingiva_path.name, "gingiva.stl")
            self.assertTrue(gingiva_path.is_file())
            exported = trimesh.load(gingiva_path, process=False, force="mesh")
            self.assertEqual(len(exported.faces), 1)

    def test_export_removes_stale_gingiva_when_label_zero_is_absent(self) -> None:
        mesh = trimesh.Trimesh(
            vertices=np.asarray(
                [
                    [0.0, 0.0, 0.0],
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                ]
            ),
            faces=np.asarray([[0, 1, 2]]),
            process=False,
        )
        vertex_labels = np.asarray([1, 1, 1], dtype=np.int16)

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            stale = output_dir / "gingiva.stl"
            stale.write_bytes(b"stale")
            teeth_dir = output_dir / "teeth_stl"
            teeth_dir.mkdir()
            stale_tooth = teeth_dir / "stale_previous_run.stl"
            stale_tooth.write_bytes(b"stale")

            result = _export(mesh, vertex_labels, {1: 11}, output_dir)

            self.assertFalse(result["gingiva"]["present"])
            self.assertIsNone(result["gingiva"]["stl"])
            self.assertFalse(stale.exists())
            self.assertFalse(stale_tooth.exists())

    def test_export_replaces_only_gingiva_symlink(self) -> None:
        gingiva = trimesh.Trimesh(
            vertices=np.asarray(
                [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
            ),
            faces=np.asarray([[0, 1, 2]]),
            process=False,
        )
        tooth = trimesh.Trimesh(
            vertices=np.asarray(
                [[2.0, 0.0, 0.0], [3.0, 0.0, 0.0], [2.0, 1.0, 0.0]]
            ),
            faces=np.asarray([[0, 1, 2]]),
            process=False,
        )

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
                    mesh = trimesh.util.concatenate((gingiva, tooth))
                    vertex_labels = np.asarray([0, 0, 0, 1, 1, 1], dtype=np.int16)
                else:
                    mesh = tooth
                    vertex_labels = np.asarray([1, 1, 1], dtype=np.int16)

                result = _export(mesh, vertex_labels, {1: 11}, output_dir)

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

    def test_export_rejects_symlinked_teeth_directory(self) -> None:
        mesh = trimesh.Trimesh(
            vertices=np.asarray(
                [
                    [0.0, 0.0, 0.0],
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                ]
            ),
            faces=np.asarray([[0, 1, 2]]),
            process=False,
        )
        vertex_labels = np.asarray([1, 1, 1], dtype=np.int16)

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
                _export(mesh, vertex_labels, {1: 11}, output_dir)

            self.assertEqual(victim.read_bytes(), b"keep")

    def test_export_rejects_symlinked_output_directory(self) -> None:
        mesh = trimesh.Trimesh(
            vertices=np.asarray(
                [
                    [0.0, 0.0, 0.0],
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                ]
            ),
            faces=np.asarray([[0, 1, 2]]),
            process=False,
        )
        vertex_labels = np.asarray([1, 1, 1], dtype=np.int16)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = root / "external"
            external.mkdir()
            victim = external / "gingiva.stl"
            victim.write_bytes(b"keep")
            output_dir = root / "output"
            output_dir.symlink_to(external, target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "output directory"):
                _export(mesh, vertex_labels, {1: 11}, output_dir)

            self.assertEqual(victim.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
