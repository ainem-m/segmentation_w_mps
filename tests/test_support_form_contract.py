from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_STATE = (
    ROOT / "native/macos/TotalSegmentatorWrapperForMac/AppState.swift"
).read_text(encoding="utf-8")
APP_MAIN = (
    ROOT
    / "native/macos/TotalSegmentatorWrapperForMac/TotalSegmentatorWrapperForMacApp.swift"
).read_text(encoding="utf-8")
VIEWS = (
    ROOT / "native/macos/TotalSegmentatorWrapperForMac/Views.swift"
).read_text(encoding="utf-8")
MANUAL = (ROOT / "docs/USER_MANUAL_JA.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
RELEASE_PREPARER = (ROOT / "scripts/prepare_cloudflare_release.py").read_text(
    encoding="utf-8"
)
PAGES = (
    ROOT / "cloudflare/pages/index.html",
    ROOT / "cloudflare/pages/launch2.html",
)
FORM_URL = "https://forms.gle/QFPwF1Pi5C8bmSuw6"


class SupportFormContractTests(unittest.TestCase):
    def test_app_uses_the_existing_google_support_form(self) -> None:
        self.assertIn(FORM_URL, APP_STATE)
        self.assertIn("相談フォームを開く", VIEWS)
        self.assertIn(FORM_URL, MANUAL)
        self.assertNotIn("issues/new", APP_STATE)
        self.assertNotIn("dicom_compatibility.yml", APP_STATE)
        self.assertNotIn("bug_report.yml", APP_STATE)
        self.assertNotIn("github.com/ainem-m/segmentation_w_mps/issues", APP_MAIN)

    def test_stl_generation_failure_also_offers_the_support_form(self) -> None:
        failed_block = VIEWS.split(
            'if state.stlGenerationStatus == "failed" {', maxsplit=1
        )[1].split("\n                    }", maxsplit=1)[0]
        self.assertIn("state.openSTLGenerationLog()", failed_block)
        self.assertIn("state.openSTLGenerationErrorReportForm()", failed_block)
        stl_action = APP_STATE.split(
            "func openSTLGenerationErrorReportForm()", maxsplit=1
        )[1].split("\n    }", maxsplit=1)[0]
        self.assertIn('code: "stl_generation_failed"', stl_action)
        self.assertIn("openErrorReportForm()", stl_action)

    def test_public_pages_and_app_share_one_form_url(self) -> None:
        for path in PAGES:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn(FORM_URL, text)
                self.assertIn("Googleアカウントへのログインは不要", text)
        self.assertIn("Google account sign-in is not required", RELEASE_PREPARER)

    def test_readme_routes_end_users_to_account_free_google_form(self) -> None:
        self.assertIn(FORM_URL, README)
        self.assertIn("Googleフォーム・ログイン不要", README)
        self.assertNotIn(
            "[不具合を報告](https://github.com/ainem-m/segmentation_w_mps/issues)",
            README,
        )


if __name__ == "__main__":
    unittest.main()
