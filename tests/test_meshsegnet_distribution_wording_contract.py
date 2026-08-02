from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
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

BUTTON_TITLE = "同梱モデル（MeshSegNet）を使用する"
WEIGHT_BOUNDARY = (
    "アプリに同梱されるのは実装です。"
    "重み（Apache-2.0）は口腔内スキャン機能の初回実行時に固定配布元から取得し、"
    "SHA-256を検証します。重みはアプリ／DMGには同梱しません。"
)


class MeshSegNetDistributionWordingContractTests(unittest.TestCase):
    def test_selected_button_title_is_preserved_with_immediate_weight_boundary(self) -> None:
        self.assertIn(BUTTON_TITLE, VIEWS)
        model_card = VIEWS.split(
            '"同梱モデル（MeshSegNet）",', maxsplit=1
        )[1].split("Divider()", maxsplit=1)[0]
        self.assertIn(WEIGHT_BOUNDARY, model_card)

    def test_manual_explains_implementation_and_weight_boundary_after_button(self) -> None:
        self.assertIn(WEIGHT_BOUNDARY, MANUAL)
        button_index = MANUAL.index(f"`{BUTTON_TITLE}`")
        explanation_index = MANUAL.index(WEIGHT_BOUNDARY)
        self.assertLess(button_index, explanation_index)
        self.assertLess(explanation_index - button_index, 600)

    def test_public_preview_calls_meshsegnet_standard_and_first_use_downloaded(self) -> None:
        expected = "標準MeshSegNet（この機能の初回実行時に重みを取得）"
        for path in PAGES:
            with self.subTest(path=path.name):
                page = path.read_text(encoding="utf-8")
                self.assertIn(expected, page)
                self.assertNotIn("標準MeshSegNet（重みは初回に取得）", page)
                self.assertNotIn("同梱のMeshSegNet", page)

    def test_future_release_notes_distinguish_implementation_from_weights(self) -> None:
        self.assertIn("built-in MeshSegNet implementation", RELEASE_PREPARER)
        self.assertIn("downloaded separately from a pinned source", RELEASE_PREPARER)
        self.assertIn("on first use of the intra-oral scan feature", RELEASE_PREPARER)
        self.assertIn("SHA-256 verified", RELEASE_PREPARER)
        self.assertIn("not bundled in the app or DMG", RELEASE_PREPARER)
        self.assertNotIn("bundled MeshSegNet option", RELEASE_PREPARER)

    def test_readme_covers_ios_input_weight_boundaries_and_gingiva_semantics(self) -> None:
        for expected in (
            "0.4.1では、口腔内スキャンのPLY／STLから歯別STLを作成できます。",
            "アプリに同梱されるのは実装のみです。",
            "口腔内スキャン機能の初回使用時に固定配布元から取得し、SHA-256を検証します。",
            "TGNetの重みは指定の配布ページから利用者が取得するもので、アプリには同梱されません。",
            "ライセンスは本アプリでは未確認です。",
            "MeshSegNetの`gingiva.stl`は背景を含む候補",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, README)


if __name__ == "__main__":
    unittest.main()
