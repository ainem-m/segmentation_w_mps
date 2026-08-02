import unittest

import numpy as np
import torch

from scripts.diagnose_tgnet_offset_scale import (
    CENTER_DEFINITIONS,
    _normalization,
    _offset_metrics,
)
from scripts.compare_tgnet_compatibility import _compose_features
from scripts.compare_tgnet_author_mid_compatibility import (
    AUTHOR_GROUPING,
    AUTHOR_RUNTIME_ARCHITECTURE,
    EXPECTED_CHECKPOINTS,
    _author_group_instances,
    _author_boundary_merge,
    _author_normalization,
    _center_mrm_crops,
    _merge_mrm_class_logits,
)
from scripts.compare_tgnet_author_final_compatibility import (
    _assign_internal_semantics,
    _combine_final_first_heads,
    _golden_arrays,
    _merge_final_second_logits,
    _strict_component_state,
)


class TGNetOffsetDiagnosticTests(unittest.TestCase):
    def test_final_semantic_empty_instance_is_relabelled_to_gingiva(self) -> None:
        points = np.concatenate(
            (
                np.repeat([[0.0, 0.0, -1.0]], 10, axis=0),
                np.repeat([[-1.0, 0.0, 0.0]], 30, axis=0),
                np.repeat([[1.0, 0.0, 0.0]], 30, axis=0),
                np.repeat([[0.0, 1.0, 0.0]], 30, axis=0),
            )
        ).astype(np.float32)
        instances = np.concatenate(
            (
                np.zeros(10, dtype=np.int16),
                np.ones(30, dtype=np.int16),
                np.full(30, 2, dtype=np.int16),
                np.full(30, 3, dtype=np.int16),
            )
        )
        classes = np.concatenate(
            (
                np.zeros(10, dtype=np.int16),
                np.ones(30, dtype=np.int16),
                np.full(30, 9, dtype=np.int16),
                np.zeros(30, dtype=np.int16),
            )
        )

        internal, effective, metadata = _assign_internal_semantics(
            points, instances, classes
        )

        np.testing.assert_array_equal(effective[instances == 3], 0)
        np.testing.assert_array_equal(internal[instances == 3], 0)
        self.assertEqual(metadata["removed_instances_without_semantic_points"], 1)
        self.assertEqual(
            metadata["instance_without_semantic_behavior"],
            "relabel-instance-to-gingiva",
        )

    def test_final_golden_arrays_accept_explicit_instances(self) -> None:
        instances, labels = _golden_arrays(
            {"instances": [1, 1, 2], "labels": [11, 11, 12]}
        )
        np.testing.assert_array_equal(instances, [1, 1, 2])
        np.testing.assert_array_equal(labels, [11, 11, 12])
        with self.assertRaisesRegex(RuntimeError, "labels"):
            _golden_arrays({"instances": [1]})

    def test_final_first_ensemble_uses_mask_gate_and_excludes_background(self) -> None:
        class_logits = [
            np.asarray(
                [[9.0, 1.0, 8.0], [9.0, 8.0, 1.0]], dtype=np.float32
            ),
            np.asarray(
                [[9.0, 2.0, 7.0], [9.0, 7.0, 2.0]], dtype=np.float32
            ),
        ]
        mask_logits = [
            np.asarray([[8.0, 1.0], [1.0, 8.0]], dtype=np.float32)
        ]
        offsets = [
            np.asarray([[1.0, 0.0, 0.0], [3.0, 0.0, 0.0]], dtype=np.float32),
            np.asarray([[3.0, 0.0, 0.0], [5.0, 0.0, 0.0]], dtype=np.float32),
        ]

        combined = _combine_final_first_heads(
            class_logits=class_logits,
            mask_logits=mask_logits,
            offsets=offsets,
        )

        np.testing.assert_array_equal(combined["classes"], [0, 1])
        np.testing.assert_allclose(
            combined["offsets"],
            [[2.0, 0.0, 0.0], [4.0, 0.0, 0.0]],
        )
        self.assertEqual(combined["metadata"]["class_fusion"], "sum-logits")
        self.assertEqual(combined["metadata"]["mask_fusion"], "sum-logits")
        self.assertEqual(combined["metadata"]["offset_fusion"], "mean")

    def test_final_second_ensemble_sums_models_and_overlapping_crops(self) -> None:
        indices = np.asarray([[0, 1], [1, 2]], dtype=np.int64)
        model_logits = [
            np.asarray(
                [
                    [[5.0, 1.0], [1.0, 5.0]],
                    [[1.0, 5.0], [5.0, 1.0]],
                ],
                dtype=np.float32,
            ),
            np.asarray(
                [
                    [[4.0, 1.0], [1.0, 4.0]],
                    [[1.0, 4.0], [4.0, 1.0]],
                ],
                dtype=np.float32,
            ),
        ]

        mask, metadata = _merge_final_second_logits(
            point_count=3,
            crop_indices=indices,
            model_class_logits=model_logits,
        )

        np.testing.assert_array_equal(mask, [False, True, False])
        self.assertEqual(metadata["checkpoint_fusion"], "sum-logits")
        self.assertEqual(metadata["crop_overlap_fusion"], "sum-logits")
        self.assertEqual(metadata["overlap_points"], 1)

    def test_final_checkpoint_component_is_strict_and_extra_prefix_limited(
        self,
    ) -> None:
        expected = {
            "weight": torch.zeros((2, 3)),
            "bias": torch.zeros((2,)),
        }
        checkpoint = {
            "second_ins_cent_model.weight": torch.zeros((2, 3)),
            "second_ins_cent_model.bias": torch.zeros((2,)),
            "cent_model.old": torch.zeros((1,)),
        }

        component, metadata = _strict_component_state(
            checkpoint,
            prefix="second_ins_cent_model",
            expected_state=expected,
            permitted_inactive_prefixes=("cent_model",),
        )

        self.assertEqual(set(component), set(expected))
        self.assertEqual(metadata["architecture_validation"], "passed")
        self.assertEqual(metadata["inactive_extra_tensor_count"], 1)
        with self.assertRaisesRegex(RuntimeError, "unexpected checkpoint"):
            _strict_component_state(
                checkpoint | {"mystery.old": torch.zeros((1,))},
                prefix="second_ins_cent_model",
                expected_state=expected,
                permitted_inactive_prefixes=("cent_model",),
            )

    def test_normalization_scale_is_explicit(self) -> None:
        points = np.asarray(
            [[-2.0, 0.0, 0.0], [2.0, 0.0, 0.0]], dtype=np.float32
        )
        normalized, metadata = _normalization(
            points, "mean-max-radius", 1.5
        )
        np.testing.assert_allclose(normalized[:, 0], [-1.5, 1.5])
        self.assertEqual(metadata["global_scale"], 1.5)
        self.assertEqual(metadata["denominator"], 2.0)

    def test_perfect_gt_offsets_have_unit_robust_coefficient(self) -> None:
        points = np.asarray(
            [
                [-1.1, 0.0, 0.0],
                [-0.9, 0.0, 0.0],
                [0.9, 0.0, 0.0],
                [1.1, 0.0, 0.0],
            ],
            dtype=np.float32,
        )
        instances = np.asarray([1, 1, 2, 2], dtype=np.int16)
        predicted = np.asarray(
            [
                [0.1, 0.0, 0.0],
                [-0.1, 0.0, 0.0],
                [0.1, 0.0, 0.0],
                [-0.1, 0.0, 0.0],
            ],
            dtype=np.float32,
        )
        metrics = _offset_metrics(
            sampled_points=points,
            predicted_offsets=predicted,
            sampled_instances=instances,
            all_points=points,
            all_instances=instances,
            center_definition="sampled-mean",
        )
        self.assertAlmostEqual(
            metrics["robust_scale_coefficient_a"], 1.0, places=6
        )
        self.assertAlmostEqual(metrics["offset_vector_mae"], 0.0)
        self.assertAlmostEqual(metrics["shift_within_instance_variance"], 0.0)

    def test_all_requested_center_definitions_are_distinct_candidates(self) -> None:
        self.assertEqual(
            set(CENTER_DEFINITIONS),
            {
                "sampled-mean",
                "sampled-median",
                "sampled-bbox",
                "all-vertex-mean",
                "all-vertex-median",
                "all-vertex-bbox",
            },
        )

    def test_tgnet_feature_order_candidates_are_explicit(self) -> None:
        coordinates = np.asarray([[1.0, 2.0, 3.0]], dtype=np.float32)
        normals = np.asarray([[4.0, 5.0, 6.0]], dtype=np.float32)
        np.testing.assert_array_equal(
            _compose_features(coordinates, normals, "coordinates-normals"),
            [[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]],
        )
        np.testing.assert_array_equal(
            _compose_features(coordinates, normals, "normals-coordinates"),
            [[4.0, 5.0, 6.0, 1.0, 2.0, 3.0]],
        )

    def test_author_mid_normalization_broadcasts_y_min_to_xyz(self) -> None:
        points = np.asarray(
            [[2.0, 4.0, 8.0], [6.0, 8.0, 10.0]], dtype=np.float32
        )
        normalized, metadata = _author_normalization(points)
        centered = points - points.mean(axis=0)
        expected = (
            (centered - centered[:, 1].min())
            / (centered[:, 1].max() - centered[:, 1].min())
            * 1.8
            - 0.8
        )
        np.testing.assert_allclose(normalized, expected)
        self.assertEqual(metadata["scalar_broadcast_axis"], "y")
        self.assertEqual(metadata["scale"], 1.8)
        self.assertEqual(metadata["shift"], 0.8)

    def test_author_mid_runtime_architecture_is_fixed(self) -> None:
        self.assertEqual(
            AUTHOR_RUNTIME_ARCHITECTURE["strides"], [1, 4, 4, 4, 4]
        )
        self.assertEqual(
            AUTHOR_RUNTIME_ARCHITECTURE["neighborhood_sizes"],
            [36, 24, 24, 24, 24],
        )
        self.assertEqual(AUTHOR_RUNTIME_ARCHITECTURE["crop_points"], 3072)
        self.assertEqual(AUTHOR_GROUPING["dbscan_epsilon"], 0.03)
        self.assertEqual(AUTHOR_GROUPING["dbscan_min_samples"], 30)
        self.assertEqual(AUTHOR_GROUPING["noise_reassignment_neighbors"], 10)
        self.assertEqual(AUTHOR_GROUPING["mean_shift_bandwidth"], 0.07)
        self.assertEqual(
            EXPECTED_CHECKPOINTS["official-mid-fps-pass"]["filename"],
            "0707_cosannealing_val.h5",
        )
        self.assertEqual(
            EXPECTED_CHECKPOINTS["official-mid-boundary-pass"]["filename"],
            "0711_bd_cbl_aug_test_val.h5",
        )

    def test_author_grouping_reassigns_noise_without_pruning(self) -> None:
        rng = np.random.default_rng(20260731)
        first = rng.normal(
            loc=(-1.0, 0.0, 0.0), scale=0.002, size=(40, 3)
        )
        second = rng.normal(
            loc=(1.0, 0.0, 0.0), scale=0.002, size=(40, 3)
        )
        noise = np.asarray([[0.0, 0.0, 0.0]], dtype=np.float64)
        shifted = np.concatenate((first, second, noise), axis=0)
        labels, metadata = _author_group_instances(
            shifted, np.ones(len(shifted), dtype=bool)
        )
        self.assertEqual(set(np.unique(labels)), {1, 2})
        self.assertEqual(metadata["dbscan_noise_before_reassignment"], 1)
        self.assertEqual(metadata["noise_after_reassignment"], 0)
        self.assertEqual(metadata["pruning_events"], 0)
        self.assertEqual(metadata["fallback_events"], 0)

    def test_author_mrm_centers_only_crop_coordinates(self) -> None:
        points = np.asarray(
            [[1.0, 2.0, 3.0], [3.0, 6.0, 9.0], [5.0, 10.0, 15.0]],
            dtype=np.float32,
        )
        normals = np.asarray(
            [[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]],
            dtype=np.float32,
        )
        features = np.concatenate((points, normals), axis=1)
        indices = np.asarray([[0, 1], [1, 2]], dtype=np.int64)
        crop_points, crop_features = _center_mrm_crops(
            points, features, indices
        )
        np.testing.assert_allclose(crop_points.mean(axis=1), 0.0)
        np.testing.assert_allclose(crop_features[:, :, :3].mean(axis=1), 0.0)
        np.testing.assert_array_equal(
            crop_features[:, :, 3:], normals[indices]
        )

    def test_author_mrm_adds_overlapping_semantic_logits_before_argmax(
        self,
    ) -> None:
        indices = np.asarray([[0, 1], [1, 2]], dtype=np.int64)
        logits = np.asarray(
            [
                [[4.0, 1.0], [1.0, 3.0]],
                [[5.0, 0.0], [0.0, 2.0]],
            ],
            dtype=np.float32,
        )
        mask, metadata = _merge_mrm_class_logits(3, indices, logits)
        np.testing.assert_array_equal(mask, [False, False, True])
        self.assertEqual(metadata["overlap_points"], 1)
        self.assertEqual(metadata["unvisited_points"], 0)
        self.assertEqual(metadata["merge"], "sum-class-logits-then-argmax")

    def test_author_boundary_clusters_are_remapped_then_propagated(self) -> None:
        full_labels, metadata = _author_boundary_merge(
            all_points=np.asarray(
                [[0.0, 0.0, 0.0], [0.15, 0.0, 0.0], [9.9, 0.0, 0.0]],
                dtype=np.float32,
            ),
            first_points=np.asarray(
                [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], dtype=np.float32
            ),
            first_labels=np.asarray([1, 2], dtype=np.int16),
            boundary_points=np.asarray(
                [[0.1, 0.0, 0.0], [0.2, 0.0, 0.0], [9.8, 0.0, 0.0]],
                dtype=np.float32,
            ),
            boundary_labels=np.asarray([5, 5, 7], dtype=np.int16),
        )
        np.testing.assert_array_equal(full_labels, [1, 1, 2])
        self.assertEqual(metadata["cluster_remap"], {"5": 1, "7": 2})
        self.assertEqual(
            metadata["propagation"],
            "one-nearest-neighbor-over-first-plus-boundary-points",
        )


if __name__ == "__main__":
    unittest.main()
