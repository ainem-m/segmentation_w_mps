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
TARGET_VERSION = "0.3.0"
TARGET_DMG_NAME = "TotalSegmentator Wrapper for Mac-0.3.0-20260729-final-arm64.dmg"


def stable_update_manifest() -> dict:
    return json.loads((R2_ROOT / "releases" / "stable" / "update.json").read_text(encoding="utf-8"))


def release_metadata(version: str) -> dict:
    return json.loads((R2_ROOT / "releases" / version / "release.json").read_text(encoding="utf-8"))


def release_file_name_from_url(url: str) -> str:
    return unquote(Path(urlparse(url).path).name)


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


class CloudflareDistributionTests(unittest.TestCase):
    def test_launch_preview_assets_fit_cloudflare_pages_and_keep_fixed_title_lines(self) -> None:
        launch = (PAGES_ROOT / "launch2.html").read_text(encoding="utf-8")
        preview_dir = PAGES_ROOT / "preview"

        for path in preview_dir.iterdir():
            if path.is_file():
                self.assertLessEqual(
                    path.stat().st_size,
                    25 * 1024 * 1024,
                    f"{path.name} exceeds the Cloudflare Pages per-file limit",
                )
        for line in [
            "MacBook Airでも",
            "もっと簡単・高速に！",
            "CTから顎骨・歯を",
            "3Dデータ化！",
        ]:
            self.assertIn(f'<span class="hero-title-line', launch)
            self.assertIn(line, launch)
        self.assertEqual(launch.count('class="hero-title-line'), 4)
        self.assertIn("white-space: nowrap", launch)
        self.assertIn(
            'src="/preview/dentalsegmentator-0.3.0.html"',
            launch,
        )
        self.assertIn('rel="canonical" href="https://totalsegmentator.lacramy.com/"', launch)

    def test_public_preview_assets_have_hash_and_scope_provenance(self) -> None:
        provenance = json.loads(
            (PAGES_ROOT / "assets" / "ASSET_PROVENANCE.json").read_text(encoding="utf-8")
        )
        self.assertFalse(provenance["apache_2_0_relicensed"])
        for name, metadata in provenance["files"].items():
            actual = hashlib.sha256((PAGES_ROOT / "assets" / name).read_bytes()).hexdigest()
            self.assertEqual(actual, metadata["sha256"])

        preview_provenance = json.loads(
            (PAGES_ROOT / "preview" / "PROVENANCE.json").read_text(encoding="utf-8")
        )
        self.assertFalse(preview_provenance["apache_2_0_relicensed"])
        self.assertIn("raw DICOM is not included", preview_provenance["description"])
        for name, metadata in preview_provenance["files"].items():
            actual = hashlib.sha256((PAGES_ROOT / "preview" / name).read_bytes()).hexdigest()
            self.assertEqual(actual, metadata["sha256"])

    def test_totalsegmentator_page_uses_canonical_domain_and_r2_redirect_for_dmg(self) -> None:
        index = (PAGES_ROOT / "index.html").read_text(encoding="utf-8")
        launch = (PAGES_ROOT / "launch2.html").read_text(encoding="utf-8")
        redirects = (PAGES_ROOT / "_redirects").read_text(encoding="utf-8")
        headers = (PAGES_ROOT / "_headers").read_text(encoding="utf-8")

        self.assertEqual(index, launch)
        self.assertIn('rel="canonical" href="https://totalsegmentator.lacramy.com/"', index)
        self.assertEqual(index.count('href="/download"'), 3)
        self.assertIn("/preview/dentalsegmentator-0.3.0.html", index)
        self.assertIn("バージョン0.3.0・Apple Silicon搭載のMac・macOS 13以降", index)
        self.assertIn("研究・教育・検証目的に限ってご利用ください", index)
        self.assertIn('id="setup"', index)
        self.assertIn('id="sample"', index)
        self.assertIn('id="gpu"', index)
        self.assertIn('id="models"', index)
        self.assertIn('id="dicom"', index)
        self.assertIn('id="support"', index)
        self.assertIn("TotalSegmentator Wrapper for Mac 0.3.0／Developer ID署名・Apple公証済み", index)
        update = stable_update_manifest()
        release = release_metadata(update["latest_version"])
        self.assertIn(f"downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/{update['latest_version']}/", redirects)
        self.assertIn(release["file_name"].replace(" ", "%20"), redirects)
        self.assertNotIn("downloads.lacramy.com", index)
        self.assertIn("X-Content-Type-Options: nosniff", headers)
        self.assertIn("X-Frame-Options: SAMEORIGIN", headers)
        self.assertNotIn("X-Frame-Options: DENY", headers)
        self.assertIn("Content-Security-Policy: frame-ancestors 'self'", headers)
        self.assertIn("Cache-Control: public, max-age=31536000, immutable", headers)
        self.assertFalse((PAGES_ROOT / "preview" / "sample1.html").exists())

    def test_app_hub_links_canonical_app_and_preserves_legacy_download(self) -> None:
        index = (APP_HUB_ROOT / "index.html").read_text(encoding="utf-8")
        redirects = (APP_HUB_ROOT / "_redirects").read_text(encoding="utf-8")
        headers = (APP_HUB_ROOT / "_headers").read_text(encoding="utf-8")

        self.assertIn("Lacramy Apps", index)
        self.assertIn('rel="canonical" href="https://app.lacramy.com/"', index)
        self.assertIn('href="https://totalsegmentator.lacramy.com/"', index)
        self.assertIn('href="/download"', index)
        self.assertIn(
            "https://totalsegmentator.lacramy.com/assets/benchmark-dentalseg.png",
            index,
        )
        self.assertNotIn("sample1-preview.png", index)
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
                    "--channel",
                    "candidate",
                    "--minimum-supported-version",
                    "0.1.2",
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

            update = json.loads((r2_root / "releases" / "candidate" / "update.json").read_text(encoding="utf-8"))
            release_dir = r2_root / "releases" / TARGET_VERSION
            release = json.loads((release_dir / "release.json").read_text(encoding="utf-8"))
            checksums = (release_dir / "SHA256SUMS.txt").read_text(encoding="utf-8")
            release_notes = (release_dir / "RELEASE_NOTES.txt").read_text(encoding="utf-8")
            upload_plan = json.loads((r2_root / "upload-plan.json").read_text(encoding="utf-8"))
            expected_sha = hashlib.sha256(b"fake dmg").hexdigest()

            self.assertEqual(update["latest_version"], TARGET_VERSION)
            self.assertEqual(update["minimum_supported_version"], "0.1.2")
            self.assertEqual(update["sha256"], release["sha256"])
            self.assertEqual(update["sha256"], expected_sha)
            self.assertIn(
                f"totalsegmentator-wrapper-mac/releases/{TARGET_VERSION}/{TARGET_DMG_NAME.replace(' ', '%20')}",
                update["download_url"],
            )
            self.assertIn(f"{expected_sha}  {TARGET_DMG_NAME}", checksums)
            self.assertEqual(release["file_size_bytes"], len(b"fake dmg"))
            self.assertEqual(release["file_name"], TARGET_DMG_NAME)
            self.assertFalse(release["notarized"])
            self.assertIn("has not been verified as Developer ID signed and Apple notarized", release_notes)
            self.assertNotIn("- Developer ID signed and Apple notarized DMG", release_notes)
            self.assertEqual(upload_plan["bucket"], "lacramy-downloads")
            self.assertEqual(upload_plan["object_prefix"], "totalsegmentator-wrapper-mac")
            self.assertEqual(len(upload_plan["objects"]), 5)

    def test_prepare_stable_release_rejects_fake_dmg_even_when_marked_notarized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_dmg = Path(tmp) / TARGET_DMG_NAME
            fake_dmg.write_bytes(b"fake dmg")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--version",
                    TARGET_VERSION,
                    "--minimum-supported-version",
                    "0.1.2",
                    "--dmg",
                    str(fake_dmg),
                    "--r2-root",
                    str(Path(tmp) / "r2"),
                    "--notarized",
                ],
                check=False,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("failed notarization verification", completed.stderr)

    def test_prepare_stable_release_requires_explicit_minimum_supported_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_dmg = Path(tmp) / TARGET_DMG_NAME
            fake_dmg.write_bytes(b"fake dmg")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--version",
                    TARGET_VERSION,
                    "--dmg",
                    str(fake_dmg),
                    "--r2-root",
                    str(Path(tmp) / "r2"),
                ],
                check=False,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("--minimum-supported-version is required", completed.stderr)

    def test_prepare_stable_release_rejects_non_notarized_dmg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_dmg = Path(tmp) / TARGET_DMG_NAME
            fake_dmg.write_bytes(b"fake dmg")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--version",
                    TARGET_VERSION,
                    "--minimum-supported-version",
                    "0.1.2",
                    "--dmg",
                    str(fake_dmg),
                    "--r2-root",
                    str(Path(tmp) / "r2"),
                    "--no-notarized",
                ],
                check=False,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("stable releases require a notarized DMG", completed.stderr)


if __name__ == "__main__":
    unittest.main()
