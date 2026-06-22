from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_cloudflare_release.py"
PAGES_ROOT = ROOT / "cloudflare" / "pages"
R2_ROOT = ROOT / "cloudflare" / "r2"


class CloudflareDistributionTests(unittest.TestCase):
    def test_static_page_uses_r2_redirect_for_dmg(self) -> None:
        index = (PAGES_ROOT / "index.html").read_text(encoding="utf-8")
        redirects = (PAGES_ROOT / "_redirects").read_text(encoding="utf-8")
        headers = (PAGES_ROOT / "_headers").read_text(encoding="utf-8")

        self.assertIn('href="/download"', index)
        self.assertIn("sample1-preview.png", index)
        self.assertIn("6f5cf39dabd96f17035b9ffb9b3dffb23248b91d60b3c524858e4327883eada1", index)
        self.assertIn("downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/0.1.0/", redirects)
        self.assertIn("TotalSegmentator%20Wrapper%20for%20Mac-0.1.0-20260622stable2-arm64.dmg", redirects)
        self.assertNotIn("downloads.lacramy.com", index)
        self.assertNotIn('href="https://', index)
        self.assertIn("X-Content-Type-Options: nosniff", headers)
        self.assertIn("Cache-Control: public, max-age=31536000, immutable", headers)

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
        manifest = json.loads((R2_ROOT / "releases" / "stable" / "update.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["schema"], "totalsegmentator_wrapper_mac.update_manifest.v1")
        self.assertEqual(manifest["channel"], "stable")
        self.assertEqual(manifest["latest_version"], "0.1.0")
        self.assertTrue(manifest["download_url"].startswith("https://downloads.lacramy.com/"))
        self.assertIn("/totalsegmentator-wrapper-mac/releases/0.1.0/", manifest["download_url"])
        self.assertIn("TotalSegmentator%20Wrapper%20for%20Mac-0.1.0-20260622stable2-arm64.dmg", manifest["download_url"])
        self.assertEqual(manifest["sha256"], "6f5cf39dabd96f17035b9ffb9b3dffb23248b91d60b3c524858e4327883eada1")

    def test_prepare_cloudflare_release_generates_expected_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_dmg = tmp_path / "TotalSegmentator Wrapper for Mac-9.9.9-arm64.dmg"
            fake_dmg.write_bytes(b"fake dmg")
            r2_root = tmp_path / "r2"

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--version",
                    "9.9.9",
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

            update = json.loads((r2_root / "releases" / "alpha" / "update.json").read_text(encoding="utf-8"))
            release = json.loads((r2_root / "releases" / "9.9.9" / "release.json").read_text(encoding="utf-8"))
            upload_plan = json.loads((r2_root / "upload-plan.json").read_text(encoding="utf-8"))

            self.assertEqual(update["latest_version"], "9.9.9")
            self.assertEqual(update["minimum_supported_version"], "9.9.9")
            self.assertEqual(update["sha256"], release["sha256"])
            self.assertIn(
                "totalsegmentator-wrapper-mac/releases/9.9.9/TotalSegmentator%20Wrapper%20for%20Mac-9.9.9-arm64.dmg",
                update["download_url"],
            )
            self.assertEqual(release["file_size_bytes"], len(b"fake dmg"))
            self.assertEqual(upload_plan["bucket"], "lacramy-downloads")
            self.assertEqual(upload_plan["object_prefix"], "totalsegmentator-wrapper-mac")
            self.assertEqual(len(upload_plan["objects"]), 5)


if __name__ == "__main__":
    unittest.main()
