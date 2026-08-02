from __future__ import annotations

import unittest

import torch

from totalsegmentator_wrapper_mac.ios_tgnet_network import (
    TGNetCheckpointModel,
    TGNetPointTransformer,
    enable_per_scan_batchnorm,
    farthest_point_indices,
    interpolate_features,
)


class IOSTGNetNetworkTests(unittest.TestCase):
    def test_author_mid_mrm_supports_batched_crops(self) -> None:
        model = TGNetPointTransformer(
            class_count=2,
            strides=(1, 4, 4, 4, 4),
            nsamples=(8, 8, 8, 8, 8),
        ).eval()
        points = torch.randn(2, 128, 3)
        features = torch.randn(2, 128, 6)
        with torch.no_grad():
            output = model(points, features)
        self.assertEqual(tuple(output.class_logits.shape), (2, 128, 2))
        self.assertEqual(tuple(output.offsets.shape), (2, 128, 3))

    def test_batched_interpolation_normalizes_each_query_neighborhood(
        self,
    ) -> None:
        source_points = torch.tensor(
            [
                [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]],
                [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]],
            ]
        )
        query_points = torch.tensor(
            [
                [[0.1, 0.0, 0.0], [9.9, 0.0, 0.0]],
                [[9.9, 0.0, 0.0], [0.1, 0.0, 0.0]],
            ]
        )
        source_features = torch.tensor(
            [
                [[1.0], [2.0]],
                [[3.0], [4.0]],
            ]
        )
        interpolated = interpolate_features(
            query_points, source_points, source_features, k=1
        )
        torch.testing.assert_close(
            interpolated,
            torch.tensor([[[1.0], [2.0]], [[4.0], [3.0]]]),
        )

    def test_batch_size_one_matches_single_cloud_in_eval_mode(self) -> None:
        torch.manual_seed(20260731)
        model = TGNetPointTransformer(
            class_count=2,
            strides=(1, 4, 4, 4, 4),
            nsamples=(8, 8, 8, 8, 8),
        ).eval()
        points = torch.randn(128, 3)
        features = torch.randn(128, 6)
        with torch.no_grad():
            single = model(points, features)
            batched = model(points.unsqueeze(0), features.unsqueeze(0))
        torch.testing.assert_close(
            single.class_logits, batched.class_logits[0]
        )
        torch.testing.assert_close(single.offsets, batched.offsets[0])

    def test_cbl_reference_neighbourhoods_are_used(self) -> None:
        model = TGNetCheckpointModel()
        first = model.first_ins_cent_model
        self.assertEqual(first.enc1[1].transformer2.nsample, 8)
        self.assertEqual(first.dec1[1].transformer2.nsample, 8)
        self.assertEqual(first.enc2[1].transformer2.nsample, 16)
        self.assertEqual(first.mask_head.interpolation_neighbors, 1)

    def test_per_scan_mode_trains_only_batchnorm_layers(self) -> None:
        import torch

        model = TGNetCheckpointModel()
        count = enable_per_scan_batchnorm(model)
        batchnorm = [
            module
            for module in model.modules()
            if isinstance(module, torch.nn.modules.batchnorm._BatchNorm)
        ]
        self.assertEqual(count, len(batchnorm))
        self.assertGreater(count, 0)
        self.assertTrue(all(module.training for module in batchnorm))
        self.assertFalse(model.training)
        self.assertFalse(model.first_ins_cent_model.enc1[0].training)

    def test_checkpoint_architecture_exposes_both_paper_modules(self) -> None:
        model = TGNetCheckpointModel()
        state = model.state_dict()

        self.assertEqual(
            tuple(state["first_ins_cent_model.enc1.0.linear.weight"].shape),
            (32, 6),
        )
        self.assertEqual(
            tuple(state["first_ins_cent_model.cls_head.cls.weight"].shape),
            (10, 160),
        )
        self.assertEqual(
            tuple(state["second_ins_cent_model.cls_head.cls.weight"].shape),
            (2, 160),
        )

    def test_checkpoint_reference_attention_mode_is_available(self) -> None:
        model = TGNetCheckpointModel()
        layers = [
            module
            for module in model.modules()
            if module.__class__.__name__ == "PointTransformerLayer"
        ]
        self.assertGreater(len(layers), 0)
        self.assertTrue(
            all(
                module.attention_relation == "key-minus-query"
                for module in layers
            )
        )
        self.assertTrue(
            all(
                module.position_relation == "neighbor-minus-query"
                for module in layers
            )
        )

    def test_small_point_cloud_forward_preserves_input_point_count(self) -> None:
        model = TGNetCheckpointModel().eval()
        points = torch.randn(64, 3)
        features = torch.randn(64, 6)

        with torch.inference_mode():
            output = model.first_ins_cent_model(points, features)

        self.assertEqual(tuple(output.mask_logits.shape), (64, 2))
        self.assertEqual(tuple(output.class_logits.shape), (64, 10))
        self.assertEqual(tuple(output.offsets.shape), (64, 3))

    def test_default_fps_matches_deterministic_exact_reference(self) -> None:
        import fpsample
        import numpy as np

        points = torch.column_stack(
            (
                torch.linspace(-1.0, 1.0, 128),
                torch.sin(torch.linspace(0.0, 3.0, 128)),
                torch.cos(torch.linspace(0.0, 5.0, 128)),
            )
        ).float()
        expected = fpsample.fps_sampling(
            np.ascontiguousarray(points.numpy()),
            16,
            start_idx=0,
        )
        actual = farthest_point_indices(points, 16).numpy()
        np.testing.assert_array_equal(actual, expected)


if __name__ == "__main__":
    unittest.main()
