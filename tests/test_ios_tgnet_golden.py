import unittest

import numpy as np

from totalsegmentator_wrapper_mac.ios_tgnet_golden import compare_fdi_labels


class IOSTGNetGoldenTests(unittest.TestCase):
    def test_reports_binary_and_per_fdi_overlap(self) -> None:
        golden = np.array([0, 11, 11, 12, 12, 0])
        predicted = np.array([0, 11, 0, 12, 11, 0])
        result = compare_fdi_labels(predicted, golden)
        self.assertAlmostEqual(result["tooth_gingiva"]["iou"], 0.75)
        self.assertAlmostEqual(result["per_fdi"]["11"]["iou"], 1 / 3)
        self.assertAlmostEqual(result["per_fdi"]["12"]["iou"], 0.5)
        self.assertEqual(result["exact_fdi"]["correct_vertices"], 4)

    def test_rejects_vertex_count_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "vertex count"):
            compare_fdi_labels(np.array([0, 11]), np.array([0]))

    def test_optimal_instance_matching_ignores_identifier_names(self) -> None:
        golden = np.array([0, 11, 11, 12, 12])
        predicted = np.array([0, 2, 2, 1, 1])
        result = compare_fdi_labels(predicted, golden)
        self.assertAlmostEqual(
            result["optimal_instance_matching"]["mean_golden_iou"], 1.0
        )
        self.assertEqual(
            result["optimal_instance_matching"]["matched_vertices"], 4
        )


if __name__ == "__main__":
    unittest.main()
