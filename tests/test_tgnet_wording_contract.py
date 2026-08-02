from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "src/totalsegmentator_wrapper_mac/ios_tgnet_final.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")


class TGNetWordingContractTests(unittest.TestCase):
    def test_user_visible_strings_do_not_claim_official_status(self) -> None:
        tree = ast.parse(SOURCE, filename=str(SOURCE_PATH))
        strings = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        offending = [value for value in strings if "official" in value.lower()]
        self.assertEqual(offending, [])

    def test_compatibility_and_user_provided_provenance_remain_explicit(self) -> None:
        self.assertIn("user-provided compatible TGNet checkpoint pair", SOURCE)
        self.assertIn("published TGNet inference behavior", SOURCE)
        self.assertIn('"license": "not-verified"', SOURCE)
        self.assertIn('"source": "user-provided"', SOURCE)
        self.assertIn('"bundled_by_app": False', SOURCE)


if __name__ == "__main__":
    unittest.main()
