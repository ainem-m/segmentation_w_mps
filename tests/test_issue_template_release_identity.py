from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CURRENT_VERSION = tomllib.loads(
    (ROOT / "pyproject.toml").read_text(encoding="utf-8")
)["project"]["version"]
ISSUE_TEMPLATES = (
    ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml",
    ROOT / ".github" / "ISSUE_TEMPLATE" / "dicom_compatibility.yml",
)


class IssueTemplateReleaseIdentityTests(unittest.TestCase):
    def test_current_app_version_example_matches_the_canonical_package_version(self) -> None:
        for path in ISSUE_TEMPLATES:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertEqual(
                    len(re.findall(r"^\s*placeholder: 例 0\.", text, re.MULTILINE)),
                    1,
                )
                self.assertIn(f"placeholder: 例 {CURRENT_VERSION}", text)


if __name__ == "__main__":
    unittest.main()
