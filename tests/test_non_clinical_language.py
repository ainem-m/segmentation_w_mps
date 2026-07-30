from __future__ import annotations

import unittest
from pathlib import Path

from totalsegmentator_wrapper_mac.disclaimers import (
    NON_CLINICAL_NOTICE_EN,
    NON_CLINICAL_NOTICE_JA,
    NON_CLINICAL_SCOPE_JA,
    SAMPLE_NOTICE_JA,
    UNOFFICIAL_WRAPPER_NOTICE_JA,
)


ROOT = Path(__file__).resolve().parents[1]
PAGES_INDEX = ROOT / "cloudflare" / "pages" / "index.html"
APP_HUB_INDEX = ROOT / "cloudflare" / "app-hub" / "index.html"
SWIFT_APP_DIR = ROOT / "native" / "macos" / "TotalSegmentatorWrapperForMac"
WPF_SHELL_DIR = ROOT / "native" / "windows" / "CoordinatorShell"
SUPPORT_CARD = ROOT / "docs" / "34_ALPHA_DISTRIBUTION_SUPPORT_CARD.md"
CURRENT_RELEASE_NOTES = ROOT / "cloudflare" / "r2" / "releases" / "0.1.2" / "RELEASE_NOTES.txt"


def _page_section(html: str, section_id: str) -> str:
    marker = f'id="{section_id}"'
    marker_index = html.index(marker)
    section_start = html.rfind("<section", 0, marker_index)
    next_section = html.find("<section", marker_index + len(marker))
    footer = html.find("<footer", marker_index + len(marker))
    candidates = [index for index in (next_section, footer) if index != -1]
    section_end = min(candidates) if candidates else len(html)
    return html[section_start:section_end]


def _hero_section(html: str) -> str:
    start = html.index('<main class="main" id="overview">')
    end = html.index('<section class="band" aria-labelledby="what-title">')
    return html[start:end]


def _what_section(html: str) -> str:
    start = html.index('<section class="band" aria-labelledby="what-title">')
    end = html.index('<section class="band alt" id="input-output"')
    return html[start:end]


def _head_section(html: str) -> str:
    start = html.index("<head>")
    end = html.index("</head>")
    return html[start:end]


class NonClinicalLanguageTests(unittest.TestCase):
    def test_standard_disclaimers_have_single_source_of_truth(self) -> None:
        self.assertIn("not a medical device", NON_CLINICAL_NOTICE_EN)
        self.assertIn("not intended for diagnosis", NON_CLINICAL_NOTICE_EN)
        self.assertIn("医療機器ではなく", NON_CLINICAL_SCOPE_JA)
        self.assertIn("医療上の判断には使用できません", NON_CLINICAL_SCOPE_JA)
        self.assertIn("定量的な精度評価", SAMPLE_NOTICE_JA)
        self.assertIn("TotalSegmentator公式アプリではありません", UNOFFICIAL_WRAPPER_NOTICE_JA)

    def test_public_pages_use_standard_japanese_disclaimers(self) -> None:
        page = PAGES_INDEX.read_text(encoding="utf-8")
        hub = APP_HUB_INDEX.read_text(encoding="utf-8")

        self.assertIn("研究・教育・検証目的に限ってご利用ください", page)
        self.assertIn("生成結果は診断には使用できません", page)
        self.assertIn("本アプリは研究・教育・検証用です。医療機器ではなく", page)
        self.assertIn(
            "TotalSegmentator、DentalSegmentator、ToothSegを利用する",
            page,
        )
        self.assertIn("各プロジェクトの公式アプリではありません", page)
        self.assertIn(NON_CLINICAL_SCOPE_JA, hub)

    def test_public_page_keeps_safety_copy_clear_without_clinical_marketing(self) -> None:
        page = PAGES_INDEX.read_text(encoding="utf-8")
        hero_start = page.index('<section class="hero">')
        hero_end = page.index("<section", hero_start + len('<section class="hero">'))
        hero = page[hero_start:hero_end]
        dicom = _page_section(page, "dicom")
        head = _head_section(page)

        self.assertNotIn("臨床使用不可", head)
        self.assertNotIn("欠落", hero)
        self.assertNotIn("誤ラベリング", hero)
        self.assertNotIn("外部サーバー", head)
        self.assertNotIn("アップロード", head)
        self.assertIn("研究・教育・検証目的に限ってご利用ください", hero)
        self.assertIn("生成結果は診断には使用できません", hero)
        self.assertIn("アプリからCTやログを自動送信することはありません", dicom)
        self.assertIn("DICOMファイル自体も送らないでください", dicom)
        self.assertIn("元のDICOMは書き換えず", dicom)

        long_clinical_list_terms = [
            "手術",
            "インプラント",
            "矯正",
            "補綴設計",
            "技工物製作",
            "サージカルガイド",
            "誤ラベリング",
            "患者識別情報",
        ]
        for term in long_clinical_list_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, page)

    def test_swift_ui_uses_standard_japanese_disclaimers(self) -> None:
        combined = "\n".join(path.read_text(encoding="utf-8") for path in sorted(SWIFT_APP_DIR.glob("*.swift")))

        self.assertIn(NON_CLINICAL_SCOPE_JA, combined)
        self.assertIn(SAMPLE_NOTICE_JA, combined)
        self.assertIn(UNOFFICIAL_WRAPPER_NOTICE_JA, combined)

    def test_wpf_ui_uses_standard_japanese_disclaimers(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WPF_SHELL_DIR.glob("*"))
            if path.is_file()
        )

        self.assertIn(NON_CLINICAL_SCOPE_JA, combined)
        self.assertIn(SAMPLE_NOTICE_JA, combined)

    def test_support_card_uses_standard_short_notice(self) -> None:
        support_card = SUPPORT_CARD.read_text(encoding="utf-8")

        self.assertIn(NON_CLINICAL_SCOPE_JA, support_card)

    def test_current_release_notes_use_standard_english_notice(self) -> None:
        release_notes = CURRENT_RELEASE_NOTES.read_text(encoding="utf-8")

        self.assertIn(NON_CLINICAL_NOTICE_EN, " ".join(release_notes.split()))

    def test_legacy_ad_hoc_japanese_disclaimers_do_not_return(self) -> None:
        combined = "\n".join(
            [
                PAGES_INDEX.read_text(encoding="utf-8"),
                APP_HUB_INDEX.read_text(encoding="utf-8"),
                *[
                    path.read_text(encoding="utf-8")
                    for path in sorted(SWIFT_APP_DIR.glob("*.swift"))
                ],
                *[
                    path.read_text(encoding="utf-8")
                    for path in sorted(WPF_SHELL_DIR.glob("*"))
                    if path.is_file()
                ],
                SUPPORT_CARD.read_text(encoding="utf-8"),
            ]
        )

        legacy_phrases = [
            "診断、治療計画、精度評価には使わないでください",
            "診断・治療計画・精度評価には使いません",
            "診断、治療計画、精度評価、患者説明の根拠には使わないでください",
            "非臨床preview。診断、治療計画、精度評価には使わない",
        ]
        for phrase in legacy_phrases:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, combined)

    def test_python_reports_import_english_notice_from_shared_module(self) -> None:
        case_summary = (ROOT / "src" / "totalsegmentator_wrapper_mac" / "case_summary.py").read_text(
            encoding="utf-8"
        )
        output_report = (ROOT / "src" / "totalsegmentator_wrapper_mac" / "output_report.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("from totalsegmentator_wrapper_mac.disclaimers import NON_CLINICAL_NOTICE_EN", case_summary)
        self.assertIn("from totalsegmentator_wrapper_mac.disclaimers import NON_CLINICAL_NOTICE_EN", output_report)
        self.assertNotIn("NON_CLINICAL_NOTICE = (", case_summary)
        self.assertNotIn("NON_CLINICAL_NOTICE = (", output_report)


if __name__ == "__main__":
    unittest.main()
