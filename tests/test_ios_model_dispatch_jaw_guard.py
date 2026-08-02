from __future__ import annotations

import unittest

from totalsegmentator_wrapper_mac.ios_model_dispatch import orientation_for_family


class IOSModelDispatchJawRoutingTests(unittest.TestCase):
    def test_lower_meshsegnet_keeps_existing_orientation(self) -> None:
        self.assertEqual(
            orientation_for_family("meshsegnet", "lower", "rotate_y_180"),
            "rotate_y_180",
        )

    def test_upper_tgnet_keeps_existing_orientation(self) -> None:
        for family in ("tgnet", "tgnet-final"):
            with self.subTest(family=family):
                self.assertEqual(
                    orientation_for_family(family, "upper", "rotate_y_180"),
                    "rotate_y_180",
                )

    def test_lower_tgnet_uses_native_scan_orientation(self) -> None:
        for family in ("tgnet", "tgnet-final"):
            with self.subTest(family=family):
                self.assertEqual(
                    orientation_for_family(family, "lower", "rotate_y_180"),
                    "none",
                )


if __name__ == "__main__":
    unittest.main()
