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

        self.assertIn("Mac内処理｜研究・教育用", page)
        self.assertIn("研究・教育用のアプリです", page)
        self.assertIn("医療機器として提供するものではなく", page)
        self.assertIn("臨床判断には使用しないでください", page)
        self.assertIn("原画像との照合と専門家による目視確認が必要です", page)
        self.assertIn("TotalSegmentatorを利用する非公式ラッパーアプリです", page)
        self.assertIn(NON_CLINICAL_SCOPE_JA, hub)

    def test_public_page_concentrates_detailed_safety_copy_in_limits_section(self) -> None:
        page = PAGES_INDEX.read_text(encoding="utf-8")
        hero = _hero_section(page)
        what = _what_section(page)
        input_output = _page_section(page, "input-output")
        install = _page_section(page, "install")
        data = _page_section(page, "data")
        limits = _page_section(page, "limits")
        roadmap = _page_section(page, "roadmap")
        head = _head_section(page)
        unofficial_wrapper_notice = "TotalSegmentator Wrapper for Macは、TotalSegmentatorを利用する非公式ラッパーアプリです。"

        self.assertNotIn("臨床使用不可", head)
        self.assertNotIn("臨床使用不可", hero)
        self.assertNotIn("診断", hero)
        self.assertNotIn("欠落", hero)
        self.assertNotIn("誤ラベリング", hero)
        self.assertNotIn("外部サーバー", head)
        self.assertNotIn("外部サーバー", hero)
        self.assertNotIn("アップロード", head)
        self.assertNotIn("アップロード", hero)
        self.assertIn("公開アルファ版（試用段階）です。画面や出力形式は変わる可能性があります。", hero)
        self.assertIn('href="#limits">使用上の制限</a>を確認してください', hero)
        self.assertIn("DICOM形式のCT／CBCTデータを選ぶと", hero)
        self.assertIn("研究用のNIfTIファイルにも対応しています", hero)
        self.assertNotIn("AIモデル取得", hero)

        self.assertIn("CT／CBCTから3D表示を作成", what)
        self.assertIn("ブラウザで3D結果を確認", what)
        self.assertNotIn("付属サンプルで試す", what)
        self.assertNotIn("<h3>CT／CBCTデータを選ぶ</h3>", what)
        self.assertNotIn("結果を3Dで確認", what)

        self.assertIn('class="spec-table"', input_output)
        self.assertIn("入力できるデータと、作成されるファイルの一覧です。", input_output)
        self.assertIn("通常のCT／CBCTとして確認できるDICOMフォルダ", input_output)
        self.assertIn("自動抽出結果の保存ファイル（NIfTI）", input_output)
        self.assertIn("3Dで確認するための画面（HTML）", input_output)
        self.assertIn("3D表示用ファイル（STL）", input_output)
        self.assertNotIn("NIfTI形式のセグメンテーションマスク／ラベルマップ", input_output)
        self.assertNotIn("STLメッシュ", input_output)
        self.assertIn("処理記録（ログ）", input_output)
        self.assertNotIn("対象外", input_output)
        self.assertNotIn("パノラマX線画像", input_output)
        self.assertNotIn("自動抽出結果をブラウザ上の3Dビューアー", input_output)
        self.assertNotIn("歯科CAD／CAM", input_output)
        self.assertNotIn("3Dプリント", input_output)
        self.assertNotIn("診断支援", input_output)
        self.assertNotIn("治療計画", input_output)

        self.assertNotIn("付属サンプルを確認", install)
        self.assertIn("事前準備", install)
        self.assertIn("M1/M2/M3/M4などのApple Silicon搭載Mac", install)
        self.assertIn("追加のアプリや専門的な設定作業は不要です", install)
        self.assertIn("DMG（Mac用のインストールファイル）", install)
        self.assertIn("下のボタンを押し", install)
        self.assertNotIn("ページ上部のダウンロードボタンを押し", install)
        self.assertIn("初回起動時に、動作に必要なファイルと初回実行に必要なモデルweightを取得します", install)
        self.assertIn("回線速度により数分以上かかる場合があります", install)
        self.assertNotIn("Homebrew", install)
        self.assertNotIn("Python", install)
        self.assertNotIn('<span class="step-number">4</span>', install)
        self.assertNotIn("Apple Silicon版をダウンロード", page)
        self.assertEqual(page.count("アプリをダウンロード"), 3)

        self.assertIn("選択したCT画像、作成された結果、保存場所の情報、処理記録は、処理のために外部へ送信しません", data)
        self.assertIn("初期設定、モデルweight準備、更新確認など、アプリ自体に必要な情報の取得ではインターネット接続を使います", data)
        self.assertIn("TotalSegmentator側の利用状況送信設定は、このアプリの初期設定時にオフにします", data)
        self.assertNotIn("ローカルパス", data)
        self.assertNotIn("依存ソフトウェア更新", data)
        self.assertNotIn("AIモデル取得", data)
        self.assertNotIn("匿名化", data)
        self.assertNotIn("患者識別情報", data)
        self.assertNotIn("患者由来情報", data)

        self.assertIn("臨床判断には使用しないでください", limits)
        self.assertIn("AIによる自動抽出結果には誤りが含まれることがあります", limits)
        self.assertIn("3D表示用データ（メッシュ）は、歯科CAD／CAMや3Dプリント用の製作用データではありません", limits)
        self.assertIn("ローカル処理は匿名化を意味しません", limits)
        self.assertIn("患者由来の情報が含まれる場合があります", limits)
        self.assertNotIn(unofficial_wrapper_notice, limits)
        self.assertEqual(page.count(unofficial_wrapper_notice), 1)

        self.assertIn("今後の展望", roadmap)
        self.assertIn("読めないDICOMの改善", roadmap)
        self.assertIn("患者情報を含むファイルは送らず、まずは読み込めなかった状況やエラー内容だけ共有してください", roadmap)
        self.assertNotIn("症状だけ共有", roadmap)
        self.assertIn("3D Slicerへの引き継ぎ", roadmap)
        self.assertIn("データ引き継ぎ機能を開発予定です", roadmap)

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
