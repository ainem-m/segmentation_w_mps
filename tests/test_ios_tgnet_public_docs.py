from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LICENSE_AUDIT = ROOT / "docs" / "41_OPEN_SOURCE_LICENSE_AUDIT.md"
USER_MANUAL = ROOT / "docs" / "USER_MANUAL_JA.md"


class IOSTGNetPublicDocsTests(unittest.TestCase):
    def test_tgnet_checkpoint_is_user_provided_and_not_redistributed(self) -> None:
        text = LICENSE_AUDIT.read_text(encoding="utf-8")
        normalized = " ".join(text.split())

        self.assertIn("Specified TGNet FPS-plus-boundary two-checkpoint set", text)
        self.assertIn("license not verified", text)
        self.assertIn("not bundled, downloaded, or redistributed", normalized)
        self.assertIn("independently implemented", normalized)
        self.assertIn(
            "The packaged application UI accepts only the specified TGNet",
            normalized,
        )
        self.assertIn("not exposed by the packaged application UI", normalized)

    def test_manual_links_only_to_the_author_checkpoint_page_and_keeps_local_selection(self) -> None:
        text = USER_MANUAL.read_text(encoding="utf-8")
        start = text.index("## 10. 口腔内スキャン")
        end = text.index("## 11.", start)
        section = text[start:end]

        self.assertIn("TGNet（重みは別途取得）", section)
        self.assertIn("処理前に画面で顎を選択します", section)
        self.assertIn("選択した顎に合わせて入力向きをアプリ側で", section)
        self.assertNotIn("上顎・下顎に対応", section)
        self.assertIn("TGNetの重みは本アプリに同梱されていません", section)
        self.assertIn("配布元が示す利用条件をご確認ください", section)
        self.assertIn("ライセンス：未確認", section)
        self.assertIn("2個", section)
        self.assertIn("フォルダ自体を選択", section)
        self.assertIn(
            "https://drive.google.com/drive/folders/15oP0CZM_O_-Bir18VbSM8wRUEzoyLXby",
            section,
        )
        self.assertIn("自動ダウンロードしません", section)
        self.assertNotIn("その他の互換checkpointを選ぶ", section)
        self.assertNotIn("信頼できるcheckpointだけ", section)
        self.assertNotIn("http://", section)
        self.assertNotIn("本アプリから重みをダウンロード", section)


if __name__ == "__main__":
    unittest.main()
