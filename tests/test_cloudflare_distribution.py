from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_cloudflare_release.py"
PAGES_ROOT = ROOT / "cloudflare" / "pages"
APP_HUB_ROOT = ROOT / "cloudflare" / "app-hub"
R2_ROOT = ROOT / "cloudflare" / "r2"
TARGET_VERSION = "0.1.2"
TARGET_DMG_NAME = "TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg"


def stable_update_manifest() -> dict:
    return json.loads((R2_ROOT / "releases" / "stable" / "update.json").read_text(encoding="utf-8"))


def release_metadata(version: str) -> dict:
    return json.loads((R2_ROOT / "releases" / version / "release.json").read_text(encoding="utf-8"))


def release_file_name_from_url(url: str) -> str:
    return unquote(Path(urlparse(url).path).name)


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


class CloudflareDistributionTests(unittest.TestCase):
    def test_totalsegmentator_page_uses_canonical_domain_and_r2_redirect_for_dmg(self) -> None:
        index = (PAGES_ROOT / "index.html").read_text(encoding="utf-8")
        redirects = (PAGES_ROOT / "_redirects").read_text(encoding="utf-8")
        headers = (PAGES_ROOT / "_headers").read_text(encoding="utf-8")

        self.assertIn('rel="canonical" href="https://totalsegmentator.lacramy.com/"', index)
        self.assertIn('href="/download"', index)
        self.assertIn("sample1-web-preview.jpg", index)
        self.assertIn("/preview/sample1.html", index)
        self.assertIn("3Dサンプルを読み込む（約7.2 MB）", index)
        self.assertIn("Mac内処理｜研究・教育用", index)
        self.assertIn("公開アルファ版（試用段階）です。画面や出力形式は変わる可能性があります。", index)
        self.assertIn("詳しくは", index)
        self.assertIn('href="#limits">使用上の制限</a>を確認してください', index)
        self.assertIn("医科用CT／歯科用CBCTから、顎骨・歯などを自動抽出して3D表示", index)
        self.assertIn("DICOM形式のCT／CBCTデータを選ぶと", index)
        self.assertIn("研究用のNIfTIファイルにも対応しています", index)
        self.assertNotIn("Apple Silicon版をダウンロード", index)
        self.assertEqual(index.count("アプリをダウンロード"), 3)
        self.assertEqual(index.count('href="/download"'), 3)
        self.assertIn("CT／CBCTデータからAIによる自動抽出結果を作成し", index)
        self.assertIn("CT／CBCTから3D表示を作成", index)
        self.assertIn("ブラウザで3D結果を確認", index)
        self.assertNotIn("付属サンプルで試す", index)
        self.assertNotIn("<h3>CT／CBCTデータを選ぶ</h3>", index)
        self.assertNotIn("結果を3Dで確認", index)
        self.assertIn('class="spec-table"', index)
        self.assertIn("入力できるデータと、作成されるファイルの一覧です。", index)
        self.assertIn("通常のCT／CBCTとして確認できるDICOMフォルダ", index)
        self.assertIn("自動抽出結果の保存ファイル（NIfTI）", index)
        self.assertIn("3Dで確認するための画面（HTML）", index)
        self.assertIn("3D表示用ファイル（STL）", index)
        self.assertNotIn("NIfTI形式のセグメンテーションマスク／ラベルマップ", index)
        self.assertNotIn("STLメッシュ", index)
        self.assertIn("処理記録（ログ）", index)
        self.assertNotIn("<dt>対象外</dt>", index)
        self.assertNotIn("パノラマX線画像、口腔内スキャン、一般画像ファイル", index)
        self.assertIn("ここでは試用までの流れを簡単にまとめます。", index)
        self.assertIn("事前準備", index)
        self.assertIn("M1/M2/M3/M4などのApple Silicon搭載Mac", index)
        self.assertIn("追加のアプリや専門的な設定作業は不要です", index)
        self.assertIn("Apple Silicon Mac用インストーラー（DMG）：", index)
        self.assertIn("インストールファイルを保存", index)
        self.assertIn("下のボタンを押し", index)
        self.assertNotIn("ページ上部のダウンロードボタンを押し", index)
        self.assertIn("DMG（Mac用のインストールファイル）", index)
        self.assertIn('<a class="button primary compact step-download" href="/download">アプリをダウンロード</a>', index)
        self.assertIn("「アプリケーション」フォルダへ移動", index)
        self.assertNotIn("付属サンプルを確認", index)
        self.assertNotIn("Homebrew", index)
        self.assertNotIn("Python", index)
        self.assertIn("初回起動時に、動作に必要なファイルとモデルweightを取得します", index)
        self.assertIn("回線速度により数分以上かかる場合があります", index)
        self.assertNotIn("<span class=\"step-number\">4</span>", index)
        self.assertIn("選択したCT画像、作成された結果、保存場所の情報、処理記録は、処理のために外部へ送信しません", index)
        self.assertIn("初期設定、モデルweight準備、更新確認など、アプリ自体に必要な情報の取得ではインターネット接続を使います", index)
        self.assertIn("TotalSegmentator側の利用状況送信設定は、このアプリの初期設定時にオフにします", index)
        self.assertNotIn("入力画像と生成結果を外部サーバーへアップロードせず", index)
        self.assertIn("ローカル処理は匿名化を意味しません", index)
        self.assertIn("3D表示用データ（メッシュ）は、歯科CAD／CAMや3Dプリント用の製作用データではありません", index)
        self.assertIn("作成された確認画面（HTML）、自動抽出結果には患者由来の情報が含まれる場合があります", index)
        self.assertIn("今後の展望", index)
        self.assertIn("読めないDICOMの改善", index)
        self.assertIn("患者情報を含むファイルは送らず、まずは読み込めなかった状況やエラー内容だけ共有してください", index)
        self.assertNotIn("症状だけ共有", index)
        self.assertIn("3D Slicerへの引き継ぎ", index)
        self.assertIn("データ引き継ぎ機能を開発予定です", index)
        self.assertIn('<a class="button primary compact roadmap-download" href="/download">アプリをダウンロード</a>', index)
        self.assertIn("Developer ID署名済み・Apple notarization済みDMG", index)
        self.assertNotIn("release-download", index)
        self.assertIn('aria-label="TotalSegmentator関連リンク"', index)
        self.assertIn("TotalSegmentatorのライセンス（Apache-2.0）", index)
        self.assertNotIn(">ライセンス</a>", index)
        self.assertIn('aria-label="TotalSegmentator Wrapper for Mac 関連リンク"', index)
        update = stable_update_manifest()
        release = release_metadata(update["latest_version"])
        self.assertIn(update["sha256"], index)
        self.assertIn(release["file_name"], index)
        self.assertIn(f"downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/{update['latest_version']}/", redirects)
        self.assertIn(release["file_name"].replace(" ", "%20"), redirects)
        self.assertNotIn("downloads.lacramy.com", index)
        self.assertIn("X-Content-Type-Options: nosniff", headers)
        self.assertIn("X-Frame-Options: SAMEORIGIN", headers)
        self.assertNotIn("X-Frame-Options: DENY", headers)
        self.assertIn("Content-Security-Policy: frame-ancestors 'self'", headers)
        self.assertIn("Cache-Control: public, max-age=31536000, immutable", headers)
        self.assertTrue((PAGES_ROOT / "preview" / "sample1.html").is_file())

    def test_app_hub_links_canonical_app_and_preserves_legacy_download(self) -> None:
        index = (APP_HUB_ROOT / "index.html").read_text(encoding="utf-8")
        redirects = (APP_HUB_ROOT / "_redirects").read_text(encoding="utf-8")
        headers = (APP_HUB_ROOT / "_headers").read_text(encoding="utf-8")

        self.assertIn("Lacramy Apps", index)
        self.assertIn('rel="canonical" href="https://app.lacramy.com/"', index)
        self.assertIn('href="https://totalsegmentator.lacramy.com/"', index)
        self.assertIn('href="/download"', index)
        update = stable_update_manifest()
        release = release_metadata(update["latest_version"])
        self.assertIn(f"downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/{update['latest_version']}/", redirects)
        self.assertIn(release["file_name"].replace(" ", "%20"), redirects)
        self.assertIn("/totalsegmentator-wrapper-mac https://totalsegmentator.lacramy.com/ 302", redirects)
        self.assertIn("/totalsegmentator-wrapper-mac/* https://totalsegmentator.lacramy.com/:splat 302", redirects)
        self.assertIn("X-Content-Type-Options: nosniff", headers)
        self.assertIn("Cache-Control: public, max-age=300", headers)

    def test_alpha_update_manifest_matches_app_updater_schema(self) -> None:
        manifest = json.loads((R2_ROOT / "releases" / "alpha" / "update.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["schema"], "totalsegmentator_wrapper_mac.update_manifest.v1")
        self.assertEqual(manifest["channel"], "alpha")
        self.assertEqual(manifest["latest_version"], "0.1.0")
        self.assertTrue(manifest["download_url"].startswith("https://downloads.lacramy.com/"))
        self.assertIn("/totalsegmentator-wrapper-mac/releases/0.1.0/", manifest["download_url"])
        self.assertIn("TotalSegmentator%20Wrapper%20for%20Mac-0.1.0-20260622b-arm64.dmg", manifest["download_url"])
        self.assertEqual(len(manifest["sha256"]), 64)

    def test_stable_update_manifest_matches_public_download(self) -> None:
        manifest = stable_update_manifest()
        release = release_metadata(manifest["latest_version"])

        self.assertEqual(manifest["schema"], "totalsegmentator_wrapper_mac.update_manifest.v1")
        self.assertEqual(manifest["channel"], "stable")
        self.assertEqual(manifest["latest_version"], release["version"])
        self.assertLessEqual(
            version_tuple(manifest["minimum_supported_version"]),
            version_tuple(manifest["latest_version"]),
        )
        self.assertTrue(manifest["download_url"].startswith("https://downloads.lacramy.com/"))
        self.assertIn(f"/totalsegmentator-wrapper-mac/releases/{release['version']}/", manifest["download_url"])
        self.assertIn(release["file_name"].replace(" ", "%20"), manifest["download_url"])
        self.assertEqual(release_file_name_from_url(manifest["download_url"]), release["file_name"])
        self.assertEqual(manifest["sha256"], release["sha256"])
        self.assertEqual(len(manifest["sha256"]), 64)

    def test_public_download_sha_matches_release_metadata_and_local_dmg_when_present(self) -> None:
        update = stable_update_manifest()
        release_dir = R2_ROOT / "releases" / update["latest_version"]
        release = json.loads((release_dir / "release.json").read_text(encoding="utf-8"))
        checksums = (release_dir / "SHA256SUMS.txt").read_text(encoding="utf-8")
        index = (PAGES_ROOT / "index.html").read_text(encoding="utf-8")
        redirects = (PAGES_ROOT / "_redirects").read_text(encoding="utf-8")
        hub_redirects = (APP_HUB_ROOT / "_redirects").read_text(encoding="utf-8")

        self.assertEqual(release["version"], update["latest_version"])
        self.assertEqual(release["file_name"], release_file_name_from_url(update["download_url"]))
        self.assertEqual(update["sha256"], release["sha256"])
        self.assertIn(f"{release['sha256']}  {release['file_name']}", checksums)
        self.assertIn(release["sha256"], index)
        self.assertIn(release["file_name"], index)
        self.assertIn(release["file_name"].replace(" ", "%20"), redirects)
        self.assertIn(release["file_name"].replace(" ", "%20"), hub_redirects)

        local_dmg = ROOT / "dist" / release["file_name"]
        if local_dmg.exists():
            digest = hashlib.sha256(local_dmg.read_bytes()).hexdigest()
            self.assertEqual(digest, update["sha256"])

    def test_prepare_cloudflare_release_generates_expected_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_dmg = tmp_path / TARGET_DMG_NAME
            fake_dmg.write_bytes(b"fake dmg")
            r2_root = tmp_path / "r2"

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--version",
                    TARGET_VERSION,
                    "--dmg",
                    str(fake_dmg),
                    "--download-origin",
                    "https://downloads.example.test",
                    "--published-at",
                    "2026-06-18T00:00:00Z",
                    "--r2-root",
                    str(r2_root),
                ],
                check=True,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            update = json.loads((r2_root / "releases" / "stable" / "update.json").read_text(encoding="utf-8"))
            release_dir = r2_root / "releases" / TARGET_VERSION
            release = json.loads((release_dir / "release.json").read_text(encoding="utf-8"))
            checksums = (release_dir / "SHA256SUMS.txt").read_text(encoding="utf-8")
            upload_plan = json.loads((r2_root / "upload-plan.json").read_text(encoding="utf-8"))
            expected_sha = hashlib.sha256(b"fake dmg").hexdigest()

            self.assertEqual(update["latest_version"], TARGET_VERSION)
            self.assertEqual(update["minimum_supported_version"], "0.1.1")
            self.assertEqual(update["sha256"], release["sha256"])
            self.assertEqual(update["sha256"], expected_sha)
            self.assertIn(
                f"totalsegmentator-wrapper-mac/releases/{TARGET_VERSION}/{TARGET_DMG_NAME.replace(' ', '%20')}",
                update["download_url"],
            )
            self.assertIn(f"{expected_sha}  {TARGET_DMG_NAME}", checksums)
            self.assertEqual(release["file_size_bytes"], len(b"fake dmg"))
            self.assertEqual(release["file_name"], TARGET_DMG_NAME)
            self.assertEqual(upload_plan["bucket"], "lacramy-downloads")
            self.assertEqual(upload_plan["object_prefix"], "totalsegmentator-wrapper-mac")
            self.assertEqual(len(upload_plan["objects"]), 5)


if __name__ == "__main__":
    unittest.main()
