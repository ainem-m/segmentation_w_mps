from __future__ import annotations

import unittest

import numpy as np

from totalsegmentator_wrapper_mac.ios_checkpoint_family import (
    CheckpointCompatibilityError,
    analyze_checkpoint_state_dict,
    analyze_model_family_state_dict,
    tgnet_model_metadata,
    validate_fdi_mapping,
)


def tensor(*shape: int) -> np.ndarray:
    return np.empty(shape, dtype=np.float32)


def tgnet_anchor_state() -> dict[str, np.ndarray]:
    state: dict[str, np.ndarray] = {}
    for prefix, class_count in (
        ("first_ins_cent_model", 10),
        ("second_ins_cent_model", 2),
    ):
        state[f"{prefix}.enc1.0.linear.weight"] = tensor(32, 6)
        state[f"{prefix}.enc2.0.linear.weight"] = tensor(64, 35)
        state[f"{prefix}.enc3.0.linear.weight"] = tensor(128, 67)
        state[f"{prefix}.enc4.0.linear.weight"] = tensor(256, 131)
        state[f"{prefix}.enc5.0.linear.weight"] = tensor(512, 259)
        state[f"{prefix}.mask_head.cls.weight"] = tensor(2, 160)
        state[f"{prefix}.cls_head.cls.weight"] = tensor(class_count, 160)
        state[f"{prefix}.offset_head.cls.weight"] = tensor(3, 160)
    return state


def meshsegnet_anchor_state() -> dict[str, np.ndarray]:
    return {
        "mlp1_conv1.weight": tensor(64, 15, 1),
        "fstn.fc3.weight": tensor(4096, 128),
        "glm2_conv2.weight": tensor(512, 384, 1),
        "mlp3_conv1.weight": tensor(256, 1600, 1),
        "output_conv.weight": tensor(17, 128, 1),
        "output_conv.bias": tensor(17),
    }


class IOSCheckpointFamilyTests(unittest.TestCase):
    def test_detects_checkpoint_compatible_with_tgnet_thesis_architecture(self) -> None:
        result = analyze_checkpoint_state_dict(tgnet_anchor_state())

        self.assertEqual(result.model_family, "tgnet")
        self.assertEqual(result.class_count, 10)
        self.assertEqual(result.input_features, 6)
        self.assertTrue(result.architecture_validation["passed"])
        self.assertEqual(
            result.architecture_validation["encoder_widths"],
            [32, 64, 128, 256, 512],
        )
        self.assertEqual(
            result.architecture_validation["neighborhood_sizes"],
            [8, 16, 16, 16, 16],
        )
        self.assertEqual(
            result.architecture_validation["head_interpolation_neighbors"],
            1,
        )

    def test_rejects_partial_tgnet_signature_instead_of_guessing(self) -> None:
        state = tgnet_anchor_state()
        del state["second_ins_cent_model.cls_head.cls.weight"]

        with self.assertRaisesRegex(
            CheckpointCompatibilityError,
            "second_ins_cent_model.cls_head.cls.weight",
        ):
            analyze_checkpoint_state_dict(state)

    def test_rejects_wrong_tgnet_class_count(self) -> None:
        state = tgnet_anchor_state()
        state["first_ins_cent_model.cls_head.cls.weight"] = tensor(17, 160)

        with self.assertRaisesRegex(
            CheckpointCompatibilityError,
            "expected shape",
        ):
            analyze_checkpoint_state_dict(state)

    def test_family_detection_validates_meshsegnet_tensor_signature(self) -> None:
        result = analyze_model_family_state_dict(meshsegnet_anchor_state())

        self.assertEqual(result.model_family, "meshsegnet")
        self.assertEqual(result.input_features, 15)
        self.assertEqual(result.class_count, 17)
        self.assertTrue(result.architecture_validation["passed"])

    def test_partial_tgnet_is_not_reclassified_as_meshsegnet(self) -> None:
        state = tgnet_anchor_state()
        del state["second_ins_cent_model.cls_head.cls.weight"]

        with self.assertRaisesRegex(
            CheckpointCompatibilityError,
            "second_ins_cent_model.cls_head.cls.weight",
        ):
            analyze_model_family_state_dict(state)

    def test_unknown_checkpoint_family_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            CheckpointCompatibilityError,
            "supported TGNet or MeshSegNet",
        ):
            analyze_model_family_state_dict({"layer.weight": tensor(2, 2)})

    def test_user_tgnet_metadata_never_claims_a_bundled_license(self) -> None:
        metadata = tgnet_model_metadata(
            checkpoint_sha256="a" * 64,
            architecture_validation={"passed": True},
        )

        self.assertEqual(metadata["model_family"], "tgnet")
        self.assertEqual(metadata["source"], "user-provided")
        self.assertEqual(metadata["license"], "not-verified")
        self.assertFalse(metadata["bundled_by_app"])
        self.assertEqual(metadata["sha256"], "a" * 64)

    def test_fdi_mapping_is_jaw_specific_and_strict(self) -> None:
        upper = validate_fdi_mapping("upper")
        lower = validate_fdi_mapping("lower")

        self.assertEqual(upper, list(range(11, 19)) + list(range(21, 29)))
        self.assertEqual(lower, list(range(31, 39)) + list(range(41, 49)))
        with self.assertRaisesRegex(ValueError, "jaw"):
            validate_fdi_mapping("unknown")


if __name__ == "__main__":
    unittest.main()
