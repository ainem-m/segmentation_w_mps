from __future__ import annotations

import unittest

import numpy as np

from totalsegmentator_wrapper_mac.rescue_estimation import (
    cross_validate_reconstruction_spacing,
    estimate_rescue_spacing,
    foreground_bbox_xyz,
    series_count_fov_seed,
    tri_planar_spacing_search,
)


class RescueEstimationTests(unittest.TestCase):
    def test_foreground_bbox_excludes_black_margin(self) -> None:
        volume = np.zeros((20, 18, 6), dtype=np.int16)
        volume[4:16, 3:15, 1:5] = 100
        result = foreground_bbox_xyz(volume)
        self.assertEqual(result["bbox"]["min"], [4, 3, 1])
        self.assertEqual(result["bbox"]["max_exclusive"], [16, 15, 5])
        self.assertLess(result["foreground_fraction"], 1.0)

    def test_series_count_seed_uses_foreground_and_slice_steps(self) -> None:
        result = series_count_fov_seed(
            primary_foreground_shape_xyz=(100, 80, 40),
            axial_slice_step_mm=1.0,
            coronal_count=80,
            coronal_slice_step_mm=0.5,
            sagittal_count=100,
            sagittal_slice_step_mm=0.6,
        )
        self.assertEqual(result["spacing_xyz"], [0.6, 0.5, 1.0])
        self.assertEqual(result["confidence"], "low")

    def test_registration_returns_ranked_bounded_candidates(self) -> None:
        volume = np.zeros((32, 24, 16), dtype=np.float32)
        volume[5:27, 4:20, 3:13] = 1.0
        volume[10:22, 8:16, 5:11] = 3.0
        references = {
            "coronal": volume[:, volume.shape[1] // 2, :],
            "sagittal": volume[volume.shape[0] // 2, :, :],
        }
        result = tri_planar_spacing_search(
            volume,
            references,
            seed_spacing_xyz=(0.8, 0.8, 1.0),
            scale_factors=(0.8, 1.0, 1.2),
            max_evaluations=20,
            top_k=3,
        )
        self.assertTrue(result["converged"])
        self.assertLessEqual(result["evaluations"], 20)
        self.assertGreaterEqual(len(result["alternatives"]), 1)
        self.assertIn("top2_score_margin", result)
        self.assertNotEqual(result["confidence"], "high")

    def test_registration_failure_returns_fallback_candidate(self) -> None:
        volume = np.zeros((8, 8, 4), dtype=np.int16)
        result = tri_planar_spacing_search(
            volume,
            {},
            seed_spacing_xyz=(1.0, 1.0, 0.9),
        )
        self.assertFalse(result["converged"])
        self.assertEqual(result["confidence"], "unknown")
        self.assertEqual(result["estimated_spacing_xyz"], [1.0, 1.0, 0.9])
        self.assertEqual(result["status"], "fallback_initial_candidate")

    def test_registration_can_be_cancelled_without_losing_candidate(self) -> None:
        volume = np.arange(8 * 8 * 4, dtype=np.float32).reshape((8, 8, 4))
        result = tri_planar_spacing_search(
            volume,
            {"coronal": volume[:, 4, :]},
            seed_spacing_xyz=(1.0, 1.0, 1.0),
            should_cancel=lambda: True,
        )
        self.assertFalse(result["converged"])
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["estimated_spacing_xyz"], [1.0, 1.0, 1.0])

    def test_composed_estimate_keeps_fallback_and_registration_failure_evidence(self) -> None:
        metadata = estimate_rescue_spacing(
            np.zeros((10, 9, 8), dtype=np.int16),
            source_manifest_sha256="a" * 64,
            spacing_hints_xyz=(None, None, 0.9375),
        )
        self.assertEqual(metadata["estimate"]["estimated_spacing_xyz"], [1.0, 1.0, 0.9375])
        self.assertEqual(metadata["estimate"]["status"], "fallback_initial_candidate")
        self.assertEqual(metadata["estimate"]["confidence"]["overall"], "unknown")
        self.assertFalse(metadata["evidence"]["registration"]["converged"])
        self.assertIn(
            "foreground_not_detected",
            metadata["estimate"]["confidence"]["limitations"],
        )

    def test_cross_reconstruction_validation_reports_disagreement_without_fusion(self) -> None:
        consistent = cross_validate_reconstruction_spacing(
            {"BO": (0.6, 0.6, 0.9375), "ST": (0.62, 0.59, 0.94)}
        )
        inconsistent = cross_validate_reconstruction_spacing(
            {"BO": (0.6, 0.6, 0.9375), "ST": (1.1, 0.9, 3.125)}
        )
        self.assertTrue(consistent["consistent"])
        self.assertEqual(consistent["confidence_effect"], "support")
        self.assertFalse(inconsistent["consistent"])
        self.assertEqual(inconsistent["confidence_effect"], "decrease")


if __name__ == "__main__":
    unittest.main()
