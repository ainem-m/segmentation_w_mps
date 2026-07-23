from __future__ import annotations

import json
import math
import unittest

import numpy as np

from totalsegmentator_wrapper_mac.rescue_geometry import (
    CropXYZ,
    RescueGeometryError,
    RescueGeometryTransform,
    calibrated_axis_spacing,
    calibrated_locked_xy_spacing,
    initial_spacing_candidate,
    ordered_content_manifest_sha256,
    safe_rescue_error,
    validate_spacing_xyz,
)


class RescueGeometryTests(unittest.TestCase):
    def test_spacing_requires_three_finite_positive_values(self) -> None:
        self.assertEqual(validate_spacing_xyz([0.6, 0.6, 0.9375]), (0.6, 0.6, 0.9375))
        for invalid in (
            [0.6, 0.6],
            [0.6, 0.6, 0.0],
            [0.6, -1.0, 1.0],
            [0.6, math.nan, 1.0],
            [0.6, math.inf, 1.0],
        ):
            with self.subTest(invalid=invalid), self.assertRaises(RescueGeometryError):
                validate_spacing_xyz(invalid)

    def test_candidate_always_returns_editable_fallback_axes(self) -> None:
        candidate, fallback_axes = initial_spacing_candidate([None, math.nan, 0.9375])

        self.assertEqual(candidate, (1.0, 1.0, 0.9375))
        self.assertEqual(fallback_axes, (True, True, False))

    def test_transform_applies_permutation_rotation_reverse_and_crop(self) -> None:
        volume = np.arange(2 * 3 * 4, dtype=np.uint16).reshape((2, 3, 4))
        transform = RescueGeometryTransform(
            axis_permutation=(1, 0, 2),
            rotation_quarter_turns=1,
            slice_order_reversed=True,
            crop=CropXYZ(minimum=(0, 0, 1), maximum_exclusive=(2, 3, 4)),
        )

        transformed = transform.apply_volume_xyz(volume)
        expected = np.transpose(volume, (1, 0, 2))
        expected = np.rot90(expected, k=1, axes=(0, 1))
        expected = expected[:, :, ::-1]
        expected = expected[0:2, 0:3, 1:4]

        np.testing.assert_array_equal(transformed, expected)
        self.assertTrue(transformed.flags.c_contiguous)

    def test_shape_spacing_matches_array_transform(self) -> None:
        transform = RescueGeometryTransform(
            axis_permutation=(1, 0, 2),
            rotation_quarter_turns=1,
            crop=CropXYZ(minimum=(0, 1, 0), maximum_exclusive=(2, 3, 4)),
        )

        shape, spacing = transform.shape_spacing((2, 3, 4), (0.5, 0.75, 1.25))

        self.assertEqual(shape, (2, 2, 4))
        self.assertEqual(spacing, (0.5, 0.75, 1.25))

    def test_invalid_crop_and_permutation_are_blocked(self) -> None:
        with self.assertRaises(RescueGeometryError):
            RescueGeometryTransform(axis_permutation=(0, 0, 2))
        transform = RescueGeometryTransform(
            crop=CropXYZ(minimum=(0, 0, 0), maximum_exclusive=(3, 2, 2))
        )
        with self.assertRaises(RescueGeometryError):
            transform.shape_spacing((2, 2, 2), (1.0, 1.0, 1.0))

    def test_single_axis_calibration_rejects_diagonal_constraint(self) -> None:
        self.assertEqual(
            calibrated_axis_spacing(
                voxel_delta_xyz=(20.0, 0.0, 0.0),
                known_length_mm=10.0,
                axis=0,
            ),
            0.5,
        )
        with self.assertRaises(RescueGeometryError):
            calibrated_axis_spacing(
                voxel_delta_xyz=(20.0, 20.0, 0.0),
                known_length_mm=10.0,
                axis=0,
            )

    def test_locked_xy_calibration_uses_common_scale(self) -> None:
        self.assertEqual(
            calibrated_locked_xy_spacing(
                voxel_delta_xy=(3.0, 4.0),
                known_length_mm=2.5,
            ),
            0.5,
        )

    def test_ordered_content_manifest_hash_is_deterministic_and_order_sensitive(self) -> None:
        first = "a" * 64
        second = "b" * 64

        digest = ordered_content_manifest_sha256([("000001", first), ("000002", second)])

        self.assertEqual(
            digest,
            ordered_content_manifest_sha256([("000001", first), ("000002", second)]),
        )
        self.assertNotEqual(
            digest,
            ordered_content_manifest_sha256([("000002", second), ("000001", first)]),
        )

    def test_safe_error_has_allowlisted_fields_only(self) -> None:
        payload = safe_rescue_error(
            code="rescue_readback_mismatch",
            stage="validating_nifti",
            reason="The generated volume header did not match the confirmed geometry.",
            tool_version="0.2.0",
            source_hash="a" * 64,
        )

        self.assertEqual(
            set(payload),
            {
                "schema",
                "status",
                "code",
                "stage",
                "reason",
                "tool_version",
                "source_hash_prefix",
            },
        )
        encoded = json.dumps(payload)
        self.assertNotIn("/", encoded)
        self.assertNotIn("Series", encoded)


if __name__ == "__main__":
    unittest.main()
