from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse
from unittest import mock

from scripts import prepare_cloudflare_release as release_preparer
from scripts.prepare_cloudflare_release import (
    canonical_project_version,
    guard_existing_release,
    guard_production_update,
    release_notes,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_cloudflare_release.py"
PAGES_ROOT = ROOT / "cloudflare" / "pages"
APP_HUB_ROOT = ROOT / "cloudflare" / "app-hub"
R2_ROOT = ROOT / "cloudflare" / "r2"
TARGET_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
TARGET_DMG_NAME = f"TotalSegmentator Wrapper for Mac-{TARGET_VERSION}-fixture-arm64.dmg"
CANDIDATE_RELEASE_ID = f"{TARGET_VERSION}-candidate-fixture"
LIVE_LEGACY_STABLE_MANIFEST = {
    "schema": "totalsegmentator_wrapper_mac.update_manifest.v1",
    "channel": "stable",
    "latest_version": "0.3.0",
    "minimum_supported_version": "0.2.0",
    "download_url": "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/0.3.0/TotalSegmentator%20Wrapper%20for%20Mac-0.3.0-20260729-final-arm64.dmg",
    "release_notes_url": "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/0.3.0/RELEASE_NOTES.txt",
    "sha256": "4b40852f7c046191254d3db545020076b761ca7cc4d48d0cdbad8eb6c94a58ac",
    "published_at": "2026-07-28T21:31:25Z",
}


def stable_update_manifest() -> dict:
    return json.loads((R2_ROOT / "releases" / "stable" / "update.json").read_text(encoding="utf-8"))


def release_metadata(version: str) -> dict:
    return json.loads((R2_ROOT / "releases" / version / "release.json").read_text(encoding="utf-8"))


def release_file_name_from_url(url: str) -> str:
    return unquote(Path(urlparse(url).path).name)


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


class CloudflareDistributionTests(unittest.TestCase):
    def test_current_distribution_runbook_preserves_legacy_stable_and_promotes_v2_last(self) -> None:
        runbook = (ROOT / "docs" / "35_CLOUDFLARE_DISTRIBUTION.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("immutable=true相当の4 object", runbook)
        self.assertIn("releases/stable/update.json", runbook)
        self.assertIn("releases/stable-v2/update.json", runbook)
        self.assertIn("file_size_bytes", runbook)
        self.assertIn("cleanなsource checkout", runbook)
        self.assertIn("source_commit", runbook)
        self.assertIn("source_tree_dirty=false", runbook)
        self.assertIn("過去のimmutable release metadata", runbook)
        self.assertIn("latest_version=0.3.0", runbook)
        self.assertIn("0.4.0はwithdrawn", runbook)
        self.assertIn("HTTP 404", runbook)
        self.assertIn("--minimum-supported-version 0.4.1", runbook)
        self.assertIn("--promoted-pages-output", runbook)
        self.assertIn("PROMOTION_RECEIPT.json", runbook)
        self.assertIn('${PROMOTED_PAGES_ROOT}/pages', runbook)
        self.assertIn('${PROMOTED_PAGES_ROOT}/app-hub', runbook)
        self.assertIn("macOS 14以降", runbook)
        self.assertIn("直接deployしてはいけない", runbook)
        self.assertIn("live R2 objectの検証を代替しない", runbook)
        self.assertIn("ASSET_PROVENANCE.json", runbook)
        self.assertIn("台帳にないassetが0件", runbook)
        self.assertNotIn(
            "npx wrangler pages deploy cloudflare/pages --project-name",
            runbook,
        )
        self.assertNotIn(
            "npx wrangler pages deploy cloudflare/app-hub --project-name",
            runbook,
        )

    def test_release_preparation_defaults_to_next_unpublished_version(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(canonical_project_version(ROOT), "0.4.1")
        self.assertIsNone(release_preparer.parse_args([]).version)
        self.assertEqual(release_preparer.parse_args([]).channel, "stable-v2")
        self.assertIsNone(release_preparer.parse_args([]).promoted_pages_output)
        self.assertNotIn("20260731-final", text)
        self.assertIn("specified ckpts(new).zip or its expanded directory", text)
        self.assertIn("TGNet weight license terms are not verified", text)
        self.assertIn("not bundled or automatically downloaded", text)
        self.assertIn("required filenames and pinned SHA-256 values", text)
        self.assertNotIn("official TGNet", text)
        self.assertNotIn("user-provided two-checkpoint", text)
        self.assertIn("existing Google support form", text)
        self.assertIn("without automatically uploading files or logs", text)
        self.assertIn("--expected-source-commit", text)
        self.assertIn("status", text)
        self.assertIn("--porcelain=v1", text)
        self.assertIn("stable-v2 release preparation requires a clean tracked and untracked source worktree", text)
        self.assertIn("legacy stable channel is permanently frozen and read-only", text)
        self.assertNotIn("legacy stable channel is frozen at 0.4.0", text)

    def test_non_notarized_alpha_notes_are_public_but_not_stable(self) -> None:
        notes = release_notes(
            "0.4.0",
            release_id="0.4.0-alpha1",
            channel="alpha",
            notarized=False,
        )
        self.assertIn("Public alpha DMG", notes)
        self.assertIn("macOS may show an initial security warning", notes)
        self.assertIn("must not be promoted to stable", notes)
        self.assertIn("0.4.0-alpha1 public alpha", notes.splitlines()[0])
        self.assertNotIn("Requires macOS 14 or later", notes)

    def test_041_prerelease_notes_keep_stable_v2_unpublished_status(self) -> None:
        notes = release_notes(
            TARGET_VERSION,
            release_id=f"{TARGET_VERSION}-candidate1",
            channel="candidate",
            notarized=False,
        )

        self.assertIn("A future 0.4.1+ release", notes)
        self.assertIn("manually downloaded DMG", notes)
        self.assertIn("stable-v2 currently returns HTTP 404", notes)

    def test_041_release_notes_match_setup_resume_and_dicom_behavior(self) -> None:
        notes = release_notes(
            TARGET_VERSION,
            release_id=TARGET_VERSION,
            channel="stable-v2",
            notarized=True,
        )

        self.assertIn("three TotalSegmentator archives resume", notes)
        self.assertIn("strictly validated HTTP Range responses", notes)
        self.assertIn("safely fetched again from byte zero", notes)
        self.assertIn("pinned SHA-256, ZIP CRC/path-safety", notes)
        self.assertIn("exact expected model-structure validation", notes)
        self.assertIn("run directly from a DMG or App Translocation", notes)
        self.assertIn("stale or broken environment", notes)
        self.assertIn("measured download progress", notes)
        self.assertIn("resumed byte position", notes)
        self.assertIn("legacy stable update manifest is permanently frozen and read-only", notes)
        self.assertIn(
            "This release is distributed through the verified stable-v2 update manifest",
            notes,
        )
        self.assertIn("Dependency lock resolution host: macOS 26.6", notes)
        self.assertIn("macosx_14_0_arm64 target options", notes)
        self.assertIn("macOS 14 runtime E2E is unverified", notes)
        self.assertIn("macOS 15.7.3 and macOS 26", notes)
        self.assertNotIn("A future 0.4.1+ release", notes)
        self.assertNotIn("manually downloaded DMG", notes)
        self.assertNotIn("stable-v2 currently returns HTTP 404", notes)
        self.assertIn("rejected by the clean-conversion path", notes)
        self.assertIn("explicit shape-confirmation rescue path", notes)
        self.assertIn("Requires macOS 14 or later", notes)
        self.assertNotIn("Enhanced CT remains blocked", notes)

    def test_alpha_release_id_separates_immutable_objects_from_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg = root / f"TotalSegmentator Wrapper for Mac-{TARGET_VERSION}-alpha1-arm64.dmg"
            dmg.write_bytes(b"alpha")
            r2_root = root / "r2"
            release_preparer.main(
                [
                    "--version",
                    TARGET_VERSION,
                    "--release-id",
                    f"{TARGET_VERSION}-alpha1",
                    "--channel",
                    "alpha",
                    "--minimum-supported-version",
                    "0.3.0",
                    "--dmg",
                    str(dmg),
                    "--r2-root",
                    str(r2_root),
                ],
                artifact_verifier=lambda _dmg, _version: None,
            )
            update = json.loads(
                (r2_root / "releases" / "alpha" / "update.json").read_text()
            )
            release = json.loads(
                (r2_root / "releases" / f"{TARGET_VERSION}-alpha1" / "release.json").read_text()
            )
            self.assertEqual(update["latest_version"], TARGET_VERSION)
            self.assertIn(f"/releases/{TARGET_VERSION}-alpha1/", update["download_url"])
            self.assertEqual(release["version"], TARGET_VERSION)
            self.assertEqual(release["release_id"], f"{TARGET_VERSION}-alpha1")

    def test_explicit_version_cannot_differ_from_pyproject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dmg = Path(tmp) / "TotalSegmentator Wrapper for Mac-9.9.9-arm64.dmg"
            dmg.write_bytes(b"fixture")
            with self.assertRaisesRegex(SystemExit, "does not match pyproject"):
                release_preparer.main(
                    [
                        "--version",
                        "9.9.9",
                        "--channel",
                        "candidate",
                        "--release-id",
                        CANDIDATE_RELEASE_ID,
                        "--dmg",
                        str(dmg),
                        "--r2-root",
                        str(Path(tmp) / "r2"),
                    ],
                    artifact_verifier=lambda _dmg, _version: None,
                )

    def test_dmg_filename_cannot_relabel_an_old_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dmg = Path(tmp) / "TotalSegmentator Wrapper for Mac-0.4.0-final-arm64.dmg"
            dmg.write_bytes(b"fixture")
            with self.assertRaisesRegex(SystemExit, "DMG filename does not match"):
                release_preparer.main(
                    [
                        "--channel",
                        "candidate",
                        "--release-id",
                        CANDIDATE_RELEASE_ID,
                        "--dmg",
                        str(dmg),
                        "--r2-root",
                        str(Path(tmp) / "r2"),
                    ],
                    artifact_verifier=lambda _dmg, _version: None,
                )

    def test_existing_immutable_release_rejects_different_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / TARGET_VERSION
            release_dir.mkdir()
            (release_dir / "release.json").write_text(
                json.dumps(
                    {
                        "version": TARGET_VERSION,
                        "release_id": TARGET_VERSION,
                        "file_name": TARGET_DMG_NAME,
                        "file_size_bytes": 4,
                        "sha256": "a" * 64,
                        "channel": "candidate",
                        "notarized": False,
                        "published_at": "2026-08-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "immutable release collision"):
                guard_existing_release(
                    release_dir,
                    version=TARGET_VERSION,
                    release_id=TARGET_VERSION,
                    file_name=TARGET_DMG_NAME,
                    file_size_bytes=4,
                    sha256="b" * 64,
                    channel="candidate",
                    notarized=False,
                    published_at="2026-08-01T00:00:00Z",
                )

    def test_existing_immutable_release_allows_same_sha_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / TARGET_VERSION
            release_dir.mkdir()
            existing = {
                "version": TARGET_VERSION,
                "release_id": TARGET_VERSION,
                "file_name": TARGET_DMG_NAME,
                "file_size_bytes": 4,
                "sha256": "a" * 64,
                "channel": "candidate",
                "notarized": False,
                "published_at": "2026-08-01T00:00:00Z",
            }
            (release_dir / "release.json").write_text(json.dumps(existing), encoding="utf-8")
            self.assertEqual(
                guard_existing_release(
                    release_dir,
                    version=TARGET_VERSION,
                    release_id=TARGET_VERSION,
                    file_name=TARGET_DMG_NAME,
                    file_size_bytes=4,
                    sha256="a" * 64,
                    channel="candidate",
                    notarized=False,
                    published_at=None,
                ),
                "2026-08-01T00:00:00Z",
            )

    def test_stable_v2_guard_rejects_downgrade_and_same_version_different_sha(self) -> None:
        existing = {"latest_version": TARGET_VERSION, "sha256": "a" * 64}
        with self.assertRaisesRegex(SystemExit, "stable-v2 channel downgrade"):
            guard_production_update(existing, "0.4.0", "b" * 64)
        with self.assertRaisesRegex(SystemExit, "different SHA-256"):
            guard_production_update(existing, TARGET_VERSION, "b" * 64)
        guard_production_update(existing, TARGET_VERSION, "a" * 64)

    def test_legacy_stable_manifest_is_frozen_and_preparer_refuses_to_overwrite_it(self) -> None:
        legacy = stable_update_manifest()
        self.assertEqual(legacy, LIVE_LEGACY_STABLE_MANIFEST)
        withdrawn = release_metadata("0.4.0")
        self.assertEqual(withdrawn["channel"], "withdrawn")
        self.assertTrue(withdrawn["deprecated"])
        self.assertEqual(withdrawn["recommended_version"], "0.3.0")
        self.assertEqual(
            withdrawn["withdrawal_notice_url"],
            "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/0.4.0/WITHDRAWN.txt",
        )
        withdrawal_notice = (R2_ROOT / "releases" / "0.4.0" / "WITHDRAWN.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("withdrawn from stable", withdrawal_notice)
        self.assertIn("current recommended version is 0.3.0", withdrawal_notice)
        upload_plan = json.loads((R2_ROOT / "upload-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(upload_plan["objects"], [])
        self.assertEqual(upload_plan["status"], "no_pending_upload")
        self.assertFalse((R2_ROOT / "releases" / "stable-v2" / "update.json").exists())
        with self.assertRaisesRegex(SystemExit, "legacy stable channel is permanently frozen"):
            release_preparer.main(["--channel", "stable"])

    def test_minimum_supported_version_cannot_exceed_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dmg = Path(tmp) / TARGET_DMG_NAME
            dmg.write_bytes(b"fixture")
            with self.assertRaisesRegex(SystemExit, "cannot be newer"):
                release_preparer.main(
                    [
                        "--channel",
                        "candidate",
                        "--release-id",
                        CANDIDATE_RELEASE_ID,
                        "--minimum-supported-version",
                        "9.9.9",
                        "--dmg",
                        str(dmg),
                        "--r2-root",
                        str(Path(tmp) / "r2"),
                    ],
                    artifact_verifier=lambda _dmg, _version: None,
                )

    def test_release_channel_rejects_path_traversal(self) -> None:
        with self.assertRaises(SystemExit):
            release_preparer.parse_args(["--channel", "../stable"])

    def test_nonstable_release_requires_explicit_distinct_release_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dmg = Path(tmp) / TARGET_DMG_NAME
            dmg.write_bytes(b"fixture")
            with self.assertRaisesRegex(SystemExit, "distinct --release-id"):
                release_preparer.main(
                    [
                        "--channel",
                        "candidate",
                        "--dmg",
                        str(dmg),
                        "--r2-root",
                        str(Path(tmp) / "r2"),
                    ],
                    artifact_verifier=lambda _dmg, _version: None,
                )

    def test_stable_v2_release_requires_canonical_cloudflare_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dmg = Path(tmp) / TARGET_DMG_NAME
            dmg.write_bytes(b"fixture")
            base = [
                "--channel",
                "stable-v2",
                "--minimum-supported-version",
                "0.4.0",
                "--dmg",
                str(dmg),
                "--r2-root",
                str(Path(tmp) / "r2"),
                "--notarized",
            ]
            invalid_targets = (
                ["--download-origin", "https://example.invalid"],
                ["--object-prefix", "another-app"],
                ["--bucket", "another-bucket"],
            )
            for override in invalid_targets:
                with self.subTest(override=override):
                    with self.assertRaisesRegex(SystemExit, "canonical Cloudflare"):
                        release_preparer.main(
                            [*base, *override],
                            source_verifier=lambda _root: "1" * 40,
                        )

    def test_download_origin_rejects_credentials_and_nonstandard_port(self) -> None:
        for origin in (
            "https://user:secret@downloads.example.test",
            "https://downloads.example.test:444",
        ):
            with self.subTest(origin=origin):
                with self.assertRaisesRegex(SystemExit, "HTTPS origin"):
                    release_preparer.normalize_https_origin(origin)

    def test_published_at_must_be_valid_utc_rfc3339(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dmg = Path(tmp) / TARGET_DMG_NAME
            dmg.write_bytes(b"fixture")
            with self.assertRaisesRegex(SystemExit, "published-at"):
                release_preparer.main(
                    [
                        "--channel",
                        "candidate",
                        "--release-id",
                        CANDIDATE_RELEASE_ID,
                        "--dmg",
                        str(dmg),
                        "--r2-root",
                        str(Path(tmp) / "r2"),
                        "--published-at",
                        "not-a-date",
                    ],
                    artifact_verifier=lambda _dmg, _version: None,
                )

    def test_prepare_release_is_idempotent_but_rejects_replaced_dmg_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg = root / TARGET_DMG_NAME
            dmg.write_bytes(b"first immutable artifact")
            r2_root = root / "r2"
            args = [
                "--channel",
                "candidate",
                "--release-id",
                CANDIDATE_RELEASE_ID,
                "--minimum-supported-version",
                "0.4.0",
                "--dmg",
                str(dmg),
                "--r2-root",
                str(r2_root),
                "--published-at",
                "2026-08-01T00:00:00Z",
            ]
            release_preparer.main(
                args,
                artifact_verifier=lambda _dmg, _version: None,
            )
            original_release = (
                r2_root / "releases" / CANDIDATE_RELEASE_ID / "release.json"
            ).read_bytes()
            release_preparer.main(
                args,
                artifact_verifier=lambda _dmg, _version: None,
            )
            self.assertEqual(
                (
                    r2_root
                    / "releases"
                    / CANDIDATE_RELEASE_ID
                    / "release.json"
                ).read_bytes(),
                original_release,
            )

            dmg.write_bytes(b"different artifact bytes")
            with self.assertRaisesRegex(SystemExit, "immutable release collision"):
                release_preparer.main(
                    args,
                    artifact_verifier=lambda _dmg, _version: None,
                )
            self.assertEqual(
                (
                    r2_root
                    / "releases"
                    / CANDIDATE_RELEASE_ID
                    / "release.json"
                ).read_bytes(),
                original_release,
            )

    def test_stable_v2_preparation_checks_derivative_display_eligibility_before_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg = root / TARGET_DMG_NAME
            dmg.write_bytes(b"notarized fixture identity")
            r2_root = root / "r2"
            promoted_root = root / "promoted"
            with (
                mock.patch.object(
                    release_preparer,
                    "verify_production_notarized_dmg",
                    autospec=True,
                ) as verifier,
                mock.patch.object(
                    release_preparer,
                    "validate_public_asset_release_eligibility",
                    side_effect=SystemExit(
                        "fixture derivative is not explicitly approved"
                    ),
                ) as eligibility_verifier,
            ):
                with self.assertRaisesRegex(
                    SystemExit,
                    "fixture derivative is not explicitly approved",
                ):
                    release_preparer.main(
                        [
                            "--channel",
                            "stable-v2",
                            "--minimum-supported-version",
                            "0.4.0",
                            "--dmg",
                            str(dmg),
                            "--r2-root",
                            str(r2_root),
                            "--promoted-pages-output",
                            str(promoted_root),
                            "--notarized",
                            "--published-at",
                            "2026-08-01T00:00:00Z",
                        ],
                        source_verifier=lambda _root: "1" * 40,
                    )
            verifier.assert_called_once_with(dmg, TARGET_VERSION, "1" * 40)
            eligibility_verifier.assert_called_once_with(
                pages_root=ROOT / "cloudflare" / "pages",
                app_hub_root=ROOT / "cloudflare" / "app-hub",
            )
            self.assertFalse(r2_root.exists())
            self.assertFalse(promoted_root.exists())

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
            self.assertTrue(name.startswith("/"))
            actual = hashlib.sha256((PAGES_ROOT / name.lstrip("/")).read_bytes()).hexdigest()
            self.assertEqual(actual, metadata["sha256"])

        preview_provenance = json.loads(
            (PAGES_ROOT / "preview" / "PROVENANCE.json").read_text(encoding="utf-8")
        )
        self.assertFalse(preview_provenance["apache_2_0_relicensed"])
        self.assertIn("raw DICOM is not included", preview_provenance["description"])
        for name, metadata in preview_provenance["files"].items():
            actual = hashlib.sha256((PAGES_ROOT / "preview" / name).read_bytes()).hexdigest()
            self.assertEqual(actual, metadata["sha256"])

        release_preparer.validate_public_asset_provenance(
            pages_root=PAGES_ROOT,
            app_hub_root=APP_HUB_ROOT,
        )

    def test_ios_webp_display_approval_is_structured_approved_and_raw_ply_is_excluded(self) -> None:
        decisions = (ROOT / "docs" / "43_OPEN_SOURCE_PUBLICATION_DECISIONS.md").read_text(
            encoding="utf-8"
        )
        normalized_decisions = " ".join(decisions.split())
        provenance = json.loads(
            (PAGES_ROOT / "assets" / "ASSET_PROVENANCE.json").read_text(encoding="utf-8")
        )
        eligibility = provenance["stable_v2_release_eligibility"]["required_assets"]
        ios_webp = eligibility["/assets/totalsegmentator-ios-tooth-segmentation.webp"]

        self.assertIn(
            "personally scanning their own oral cavity",
            normalized_decisions,
        )
        self.assertIn(
            "explicitly authorized public display of the named derived WebP",
            normalized_decisions,
        )
        self.assertIn(
            'id="owner-explicit-public-display-consent-2026-08-03"',
            decisions,
        )
        self.assertIn(
            "owner-explicit-public-display-consent-2026-08-03",
            decisions,
        )
        self.assertIn(
            release_preparer.IOS_DERIVATIVE_WEBP_APPROVAL_RECORDED_AT,
            decisions,
        )
        self.assertIn("eligible for stable-v2 promotion", normalized_decisions)
        self.assertEqual(
            ios_webp["public_display_status"],
            "approved",
        )
        self.assertEqual(
            ios_webp["approval_evidence"]["record_id"],
            release_preparer.IOS_DERIVATIVE_WEBP_APPROVAL_DECISION_ID,
        )
        self.assertEqual(
            ios_webp["approval_evidence"]["recorded_at"],
            release_preparer.IOS_DERIVATIVE_WEBP_APPROVAL_RECORDED_AT,
        )
        self.assertEqual(
            ios_webp["approval_evidence"]["subject_attestation"],
            release_preparer.IOS_DERIVATIVE_WEBP_APPROVAL_SUBJECT_ATTESTATION,
        )
        self.assertEqual(
            ios_webp["approval_evidence"]["decision_record"],
            release_preparer.IOS_DERIVATIVE_WEBP_APPROVAL_RECORD,
        )
        self.assertEqual(ios_webp["source_input"]["filename"], "ios_upper.ply")
        self.assertEqual(ios_webp["source_input"]["distribution"], "excluded")
        self.assertEqual(
            ios_webp["source_input"]["excluded_from"],
            ["git-repository", "app-bundle", "DMG", "R2", "Pages"],
        )
        self.assertEqual(
            provenance["files"][
                "/assets/totalsegmentator-ios-tooth-segmentation.webp"
            ]["sha256"],
            release_preparer.IOS_DERIVATIVE_WEBP_SHA256,
        )
        release_preparer.validate_public_asset_release_eligibility(
            pages_root=PAGES_ROOT,
            app_hub_root=APP_HUB_ROOT,
        )

    def test_ios_webp_release_gate_rejects_tampered_approval_hash_and_tgnet_payload(self) -> None:
        """The named WebP approval cannot authorize a changed asset or model payload."""

        def assert_rejected(mutate, expected_message: str) -> None:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                fixture_pages = tmp_path / "pages"
                fixture_hub = tmp_path / "app-hub"
                shutil.copytree(PAGES_ROOT, fixture_pages)
                shutil.copytree(APP_HUB_ROOT, fixture_hub)
                provenance_path = fixture_pages / "assets" / "ASSET_PROVENANCE.json"
                provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
                mutate(provenance, fixture_pages, fixture_hub)
                provenance_path.write_text(
                    json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(SystemExit, expected_message):
                    release_preparer.validate_public_asset_release_eligibility(
                        pages_root=fixture_pages,
                        app_hub_root=fixture_hub,
                    )

        def ios_policy(provenance: dict) -> dict:
            return provenance["stable_v2_release_eligibility"]["required_assets"][
                "/assets/totalsegmentator-ios-tooth-segmentation.webp"
            ]

        with self.subTest("decision identifier"):
            assert_rejected(
                lambda provenance, _pages, _hub: ios_policy(provenance)[
                    "approval_evidence"
                ].__setitem__("record_id", "some-other-approval-record"),
                "decision identifier is invalid",
            )
        with self.subTest("approval timestamp"):
            assert_rejected(
                lambda provenance, _pages, _hub: ios_policy(provenance)[
                    "approval_evidence"
                ].__setitem__("recorded_at", "2026-08-03T00:00:00Z"),
                "approval timestamp is invalid",
            )
        with self.subTest("approval attestation"):
            assert_rejected(
                lambda provenance, _pages, _hub: ios_policy(provenance)[
                    "approval_evidence"
                ].__setitem__("subject_attestation", "unrelated-subject"),
                "approval subject attestation is invalid",
            )
        with self.subTest("WebP checksum"):
            assert_rejected(
                lambda provenance, _pages, _hub: provenance["files"][
                    "/assets/totalsegmentator-ios-tooth-segmentation.webp"
                ].__setitem__("sha256", "0" * 64),
                "WebP SHA-256 is invalid",
            )
        with self.subTest("TGNet checkpoint"):
            def add_checkpoint(_provenance: dict, pages: Path, _hub: Path) -> None:
                (pages / "assets" / "tgnet_fps.h5").write_bytes(
                    b"user-provided checkpoint fixture"
                )

            assert_rejected(add_checkpoint, "TGNet checkpoint")
        with self.subTest("raw source mesh"):
            def add_raw_mesh(_provenance: dict, pages: Path, _hub: Path) -> None:
                (pages / "assets" / "unapproved-source.ply").write_bytes(
                    b"raw source mesh fixture"
                )

            assert_rejected(add_raw_mesh, "raw PLY")

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
        self.assertNotIn("0.4.0をダウンロード", index)
        self.assertIn("研究・教育・検証目的に限ってご利用ください", index)
        self.assertIn('id="setup"', index)
        self.assertIn('id="sample"', index)
        self.assertIn('id="gpu"', index)
        self.assertIn('id="models"', index)
        self.assertIn('id="dicom"', index)
        self.assertIn('id="support"', index)
        self.assertIn('id="update-0-4-0"', index)
        self.assertIn("<strong>開発中の更新</strong>", index)
        self.assertIn("公開版 0.3.0", index)
        self.assertIn(
            "/assets/totalsegmentator-ios-tooth-segmentation.webp",
            index,
        )
        self.assertIn("口腔内スキャン → 歯別STL", index)
        self.assertIn("口腔内スキャンを読み込み", index)
        self.assertIn("歯ごとのSTLと結果JSONを保存", index)
        self.assertNotIn("上顎・下顎に対応", index)
        self.assertIn(
            "ToothGroupNetwork（TGNet）で作成した歯別セグメンテーション",
            index,
        )
        self.assertIn("ToothGroupNetwork（TGNet）も選択可能", index)
        self.assertNotIn('href="#support">導入サポート</a>', index)
        self.assertNotIn("TGNet重みの選択や互換性確認", index)
        self.assertNotIn("TGNet重みの選択・互換性確認", index)
        self.assertNotIn("TGNetの重みの準備", index)
        self.assertNotIn("重みの準備などで迷った場合", index)
        self.assertIn("エラーを報告しやすく", index)
        self.assertIn("報告内容をもとに作者側で調査・対応", index)
        self.assertIn("ファイルやログを自動送信しない", index)
        self.assertIn("単一の.dcmを直接選択", index)
        self.assertIn("フォルダ／単一ファイルの両方に対応", index)
        self.assertIn("TGNetの重みは本アプリに同梱・再配布しません", index)
        self.assertIn("指定の配布ページ</a>からご自身で取得", index)
        self.assertIn("配布元が示す利用条件をご確認ください", index)
        self.assertIn("ライセンス条件は本アプリでは未確認です", index)
        self.assertTrue(
            (PAGES_ROOT / "assets" / "totalsegmentator-ios-tooth-segmentation.webp").is_file()
        )
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

    def test_public_tgnet_boundary_excludes_paid_checkpoint_support(self) -> None:
        index = (PAGES_ROOT / "index.html").read_text(encoding="utf-8")
        launch = (PAGES_ROOT / "launch2.html").read_text(encoding="utf-8")
        self.assertEqual(index, launch)

        for page in (index, launch):
            with self.subTest(page="index" if page is index else "launch2"):
                support = page.split('id="support"', maxsplit=1)[1].split(
                    "</section>", maxsplit=1
                )[0]
                self.assertIn("ToothGroupNetwork（TGNet）も選択可能", page)
                self.assertIn(
                    "https://drive.google.com/drive/folders/15oP0CZM_O_-Bir18VbSM8wRUEzoyLXby",
                    page,
                )
                self.assertIn("指定の配布ページ</a>からご自身で取得", page)
                self.assertIn("配布元が示す利用条件をご確認ください", page)
                self.assertIn("本アプリに同梱・再配布しません", page)
                self.assertNotIn('href="#support">導入サポート</a>', page)
                self.assertNotIn("TGNet重みの選択や互換性確認", page)
                self.assertNotIn("TGNet重みの選択・互換性確認", page)
                self.assertNotIn("TGNet", support)
                self.assertIn(
                    "アプリ本体のインストール、初回セットアップ、基本操作、アプリに表示されたエラーの確認",
                    support,
                )

    def test_app_hub_links_canonical_app_and_preserves_legacy_download(self) -> None:
        index = (APP_HUB_ROOT / "index.html").read_text(encoding="utf-8")
        redirects = (APP_HUB_ROOT / "_redirects").read_text(encoding="utf-8")
        headers = (APP_HUB_ROOT / "_headers").read_text(encoding="utf-8")

        self.assertIn("Lacramy Apps", index)
        self.assertIn("<dd>0.3.0</dd>", index)
        self.assertIn("Apple Silicon Mac / macOS 13+", index)
        self.assertNotIn("Apple Silicon Mac / macOS 14+", index)
        self.assertNotIn("public alpha", index.lower())
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

    def test_explicitly_approved_ios_webp_fixture_materializes_stable_v2_pages_without_mutating_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fixture_root = tmp_path / "approved-source"
            fixture_pages = fixture_root / "cloudflare" / "pages"
            fixture_hub = fixture_root / "cloudflare" / "app-hub"
            shutil.copytree(PAGES_ROOT, fixture_pages)
            shutil.copytree(APP_HUB_ROOT, fixture_hub)
            provenance_path = fixture_pages / "assets" / "ASSET_PROVENANCE.json"
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            eligibility = provenance["stable_v2_release_eligibility"]["required_assets"]
            ios_webp = eligibility["/assets/totalsegmentator-ios-tooth-segmentation.webp"]
            ios_webp["public_display_status"] = (
                "pending-explicit-public-display-approval"
            )
            ios_webp["approval_evidence"] = None
            provenance_path.write_text(
                json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            dmg = tmp_path / TARGET_DMG_NAME
            dmg_bytes = b"verified stable-v2 fixture"
            dmg.write_bytes(dmg_bytes)
            digest = hashlib.sha256(dmg_bytes).hexdigest()
            encoded_name = TARGET_DMG_NAME.replace(" ", "%20")
            download_url = (
                "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/"
                f"releases/{TARGET_VERSION}/{encoded_name}"
            )
            release_notes_url = (
                "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/"
                f"releases/{TARGET_VERSION}/RELEASE_NOTES.txt"
            )
            update_manifest_url = (
                "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/"
                "releases/stable-v2/update.json"
            )
            source_commit = "1" * 40
            release = {
                "schema": release_preparer.RELEASE_SCHEMA,
                "app_name": release_preparer.APP_NAME,
                "channel": "stable-v2",
                "version": TARGET_VERSION,
                "release_id": TARGET_VERSION,
                "file_name": TARGET_DMG_NAME,
                "file_size_bytes": len(dmg_bytes),
                "sha256": digest,
                "download_url": download_url,
                "update_manifest_url": update_manifest_url,
                "release_notes_url": release_notes_url,
                "published_at": "2026-08-01T00:00:00Z",
                "notarized": True,
                "source_commit": source_commit,
                "source_tree_dirty": False,
                "clinical_use": False,
            }
            update = {
                "schema": release_preparer.UPDATE_SCHEMA,
                "channel": "stable-v2",
                "latest_version": TARGET_VERSION,
                "minimum_supported_version": TARGET_VERSION,
                "download_url": download_url,
                "release_notes_url": release_notes_url,
                "file_size_bytes": len(dmg_bytes),
                "sha256": digest,
                "published_at": "2026-08-01T00:00:00Z",
                "source_commit": source_commit,
                "source_tree_dirty": False,
            }
            release_json = tmp_path / "release.json"
            update_json = tmp_path / "update.json"
            release_json.write_text(json.dumps(release), encoding="utf-8")
            update_json.write_text(json.dumps(update), encoding="utf-8")
            output_root = tmp_path / "promoted"
            pending_output_root = tmp_path / "pending-promoted"
            source_page_before = (PAGES_ROOT / "index.html").read_bytes()
            source_redirects_before = (PAGES_ROOT / "_redirects").read_bytes()
            fixture_page_before = (fixture_pages / "index.html").read_bytes()
            fixture_redirects_before = (fixture_pages / "_redirects").read_bytes()

            with self.assertRaisesRegex(
                SystemExit,
                "totalsegmentator-ios-tooth-segmentation.webp.*not explicitly approved",
            ):
                release_preparer.materialize_promoted_pages(
                    repo_root=fixture_root,
                    output_root=pending_output_root,
                    release_json=release_json,
                    update_json=update_json,
                    dmg=dmg,
                )
            self.assertFalse(pending_output_root.exists())

            ios_webp["public_display_status"] = "approved"
            ios_webp["approval_evidence"] = {
                "record_id": release_preparer.IOS_DERIVATIVE_WEBP_APPROVAL_DECISION_ID,
                "recorded_at": release_preparer.IOS_DERIVATIVE_WEBP_APPROVAL_RECORDED_AT,
                "subject_attestation": release_preparer.IOS_DERIVATIVE_WEBP_APPROVAL_SUBJECT_ATTESTATION,
                "decision_record": release_preparer.IOS_DERIVATIVE_WEBP_APPROVAL_RECORD,
            }
            provenance_path.write_text(
                json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            release_preparer.materialize_promoted_pages(
                repo_root=fixture_root,
                output_root=output_root,
                release_json=release_json,
                update_json=update_json,
                dmg=dmg,
            )

            self.assertEqual((PAGES_ROOT / "index.html").read_bytes(), source_page_before)
            self.assertEqual((PAGES_ROOT / "_redirects").read_bytes(), source_redirects_before)
            self.assertEqual((fixture_pages / "index.html").read_bytes(), fixture_page_before)
            self.assertEqual((fixture_pages / "_redirects").read_bytes(), fixture_redirects_before)
            page = (output_root / "pages" / "index.html").read_text(encoding="utf-8")
            launch = (output_root / "pages" / "launch2.html").read_text(encoding="utf-8")
            hub = (output_root / "app-hub" / "index.html").read_text(encoding="utf-8")
            page_redirects = (output_root / "pages" / "_redirects").read_text(encoding="utf-8")
            hub_redirects = (output_root / "app-hub" / "_redirects").read_text(encoding="utf-8")
            receipt = json.loads(
                (output_root / "PROMOTION_RECEIPT.json").read_text(encoding="utf-8")
            )

            self.assertEqual(page, launch)
            self.assertIn(
                f"バージョン{TARGET_VERSION}・Apple Silicon搭載のMac・macOS 14以降",
                page,
            )
            self.assertIn(f"Version {TARGET_VERSION}", page)
            self.assertIn("<strong>最新バージョン</strong>", page)
            self.assertIn(f"Version {TARGET_VERSION}をダウンロード", page)
            self.assertIn(
                f"TotalSegmentator Wrapper for Mac {TARGET_VERSION}／Developer ID署名・Apple公証済み",
                page,
            )
            self.assertNotIn("公開前", page)
            self.assertNotIn("開発中", page)
            self.assertNotIn("public-0-3-0", page)
            self.assertNotIn("現在の公開版 0.3.0", page)
            self.assertNotIn("0.4.0は公開停止済み", page)
            self.assertNotIn("macOS 13", page)
            self.assertIn(f"<dd>{TARGET_VERSION}</dd>", hub)
            self.assertIn("Apple Silicon Mac / macOS 14+", hub)
            self.assertNotIn("<dd>0.3.0</dd>", hub)
            self.assertNotIn("macOS 13", hub)
            self.assertIn(f"/download {download_url} 302", page_redirects)
            self.assertIn(f"/release-notes {release_notes_url} 302", page_redirects)
            self.assertIn(f"/download {download_url} 302", hub_redirects)
            self.assertIn(f"/release-notes {release_notes_url} 302", hub_redirects)
            self.assertNotIn("/releases/0.3.0/", page_redirects)
            self.assertNotIn("/releases/0.3.0/", hub_redirects)
            self.assertEqual(receipt["version"], TARGET_VERSION)
            self.assertEqual(
                receipt["verification_scope"],
                "local-release-update-dmg-identity-and-public-asset-provenance",
            )
            self.assertIs(receipt["live_r2_verified_by_materializer"], False)
            self.assertIs(receipt["public_asset_provenance_verified"], True)
            self.assertIs(receipt["public_asset_release_eligibility_verified"], True)
            self.assertEqual(receipt["minimum_macos_version"], "14.0")
            self.assertEqual(receipt["dmg_sha256"], digest)
            self.assertEqual(receipt["dmg_size_bytes"], len(dmg_bytes))
            self.assertEqual(receipt["download_url"], download_url)
            self.assertEqual(receipt["release_notes_url"], release_notes_url)
            self.assertEqual(receipt["source_commit"], source_commit)

    def test_promoted_pages_reject_local_public_asset_missing_provenance(self) -> None:
        """A deployable Pages tree must not gain an unledgered local asset."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fixture_root = tmp_path / "source"
            fixture_pages = fixture_root / "cloudflare" / "pages"
            fixture_hub = fixture_root / "cloudflare" / "app-hub"
            shutil.copytree(PAGES_ROOT, fixture_pages)
            shutil.copytree(APP_HUB_ROOT, fixture_hub)

            unledgered_asset = fixture_pages / "assets" / "unledgered-fixture.png"
            unledgered_asset.write_bytes(b"unledgered public asset fixture")
            for name in ("index.html", "launch2.html"):
                page_path = fixture_pages / name
                page_path.write_text(
                    page_path.read_text(encoding="utf-8").replace(
                        "</body>",
                        '<img src="/assets/unledgered-fixture.png" alt="fixture">\n</body>',
                        1,
                    ),
                    encoding="utf-8",
                )

            dmg_bytes = b"verified stable-v2 fixture"
            dmg = tmp_path / TARGET_DMG_NAME
            dmg.write_bytes(dmg_bytes)
            digest = hashlib.sha256(dmg_bytes).hexdigest()
            encoded_name = TARGET_DMG_NAME.replace(" ", "%20")
            release = {
                "schema": release_preparer.RELEASE_SCHEMA,
                "app_name": release_preparer.APP_NAME,
                "channel": "stable-v2",
                "version": TARGET_VERSION,
                "release_id": TARGET_VERSION,
                "file_name": TARGET_DMG_NAME,
                "file_size_bytes": len(dmg_bytes),
                "sha256": digest,
                "download_url": (
                    "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/"
                    f"releases/{TARGET_VERSION}/{encoded_name}"
                ),
                "update_manifest_url": (
                    "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/"
                    "releases/stable-v2/update.json"
                ),
                "release_notes_url": (
                    "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/"
                    f"releases/{TARGET_VERSION}/RELEASE_NOTES.txt"
                ),
                "published_at": "2026-08-02T00:00:00Z",
                "notarized": True,
                "source_commit": "1" * 40,
                "source_tree_dirty": False,
                "clinical_use": False,
            }
            update = {
                "schema": release_preparer.UPDATE_SCHEMA,
                "channel": "stable-v2",
                "latest_version": TARGET_VERSION,
                "minimum_supported_version": TARGET_VERSION,
                "download_url": release["download_url"],
                "release_notes_url": release["release_notes_url"],
                "file_size_bytes": len(dmg_bytes),
                "sha256": digest,
                "published_at": release["published_at"],
                "source_commit": release["source_commit"],
                "source_tree_dirty": False,
            }
            release_json = tmp_path / "release.json"
            update_json = tmp_path / "update.json"
            release_json.write_text(json.dumps(release), encoding="utf-8")
            update_json.write_text(json.dumps(update), encoding="utf-8")
            output_root = tmp_path / "promoted"

            with self.assertRaisesRegex(
                SystemExit,
                "Pages asset inventory differs.*unledgered-fixture.png",
            ):
                release_preparer.materialize_promoted_pages(
                    repo_root=fixture_root,
                    output_root=output_root,
                    release_json=release_json,
                    update_json=update_json,
                    dmg=dmg,
                )

            self.assertFalse(output_root.exists())

    def test_public_asset_provenance_scans_webmanifest_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fixture_pages = tmp_path / "pages"
            fixture_hub = tmp_path / "app-hub"
            shutil.copytree(PAGES_ROOT, fixture_pages)
            shutil.copytree(APP_HUB_ROOT, fixture_hub)

            fixture_manifest = fixture_pages / "fixture.webmanifest"
            fixture_manifest.write_text(
                json.dumps({"icons": [{"src": "/preview/PROVENANCE.json"}]}),
                encoding="utf-8",
            )
            provenance_path = fixture_pages / "assets" / "ASSET_PROVENANCE.json"
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            provenance["files"]["/fixture.webmanifest"] = {
                "sha256": hashlib.sha256(fixture_manifest.read_bytes()).hexdigest(),
                "scope": "First-party fixture web manifest used to exercise local asset reference validation.",
            }
            provenance_path.write_text(
                json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            for name in ("index.html", "launch2.html"):
                page_path = fixture_pages / name
                page_path.write_text(
                    page_path.read_text(encoding="utf-8").replace(
                        "</head>",
                        '<link rel="manifest" href="/fixture.webmanifest">\n</head>',
                        1,
                    ),
                    encoding="utf-8",
                )

            with self.assertRaisesRegex(
                SystemExit,
                "fixture.webmanifest -> PROVENANCE.json",
            ):
                release_preparer.validate_public_asset_provenance(
                    pages_root=fixture_pages,
                    app_hub_root=fixture_hub,
                )

    def test_public_asset_provenance_rejects_unreferenced_app_hub_noncontrol_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fixture_pages = tmp_path / "pages"
            fixture_hub = tmp_path / "app-hub"
            shutil.copytree(PAGES_ROOT, fixture_pages)
            shutil.copytree(APP_HUB_ROOT, fixture_hub)
            (fixture_hub / "unreferenced-fixture.payload").write_bytes(
                b"unreferenced app hub arbitrary public payload fixture"
            )

            with self.assertRaisesRegex(
                SystemExit,
                "app-hub deployable file inventory differs.*unreferenced-fixture.payload",
            ):
                release_preparer.validate_public_asset_provenance(
                    pages_root=fixture_pages,
                    app_hub_root=fixture_hub,
                )

    def test_public_asset_provenance_rejects_referenced_wasm_without_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fixture_pages = tmp_path / "pages"
            fixture_hub = tmp_path / "app-hub"
            shutil.copytree(PAGES_ROOT, fixture_pages)
            shutil.copytree(APP_HUB_ROOT, fixture_hub)
            (fixture_pages / "payload.wasm").write_bytes(b"wasm fixture")
            for name in ("index.html", "launch2.html"):
                page_path = fixture_pages / name
                page_path.write_text(
                    page_path.read_text(encoding="utf-8").replace(
                        "</body>",
                        '<script src="/payload.wasm"></script>\n</body>',
                        1,
                    ),
                    encoding="utf-8",
                )

            with self.assertRaisesRegex(
                SystemExit,
                "Pages asset inventory differs.*payload.wasm",
            ):
                release_preparer.validate_public_asset_provenance(
                    pages_root=fixture_pages,
                    app_hub_root=fixture_hub,
                )

    def test_public_asset_provenance_scans_static_javascript_template_literals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fixture_pages = tmp_path / "pages"
            fixture_hub = tmp_path / "app-hub"
            shutil.copytree(PAGES_ROOT, fixture_pages)
            shutil.copytree(APP_HUB_ROOT, fixture_hub)

            fixture_script = fixture_pages / "fixture.js"
            fixture_script.write_text(
                "const previewAsset = `/preview/PROVENANCE.json`;\n",
                encoding="utf-8",
            )
            provenance_path = fixture_pages / "assets" / "ASSET_PROVENANCE.json"
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            provenance["files"]["/fixture.js"] = {
                "sha256": hashlib.sha256(fixture_script.read_bytes()).hexdigest(),
                "scope": "First-party fixture script used to exercise static template literal validation.",
            }
            provenance_path.write_text(
                json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            for name in ("index.html", "launch2.html"):
                page_path = fixture_pages / name
                page_path.write_text(
                    page_path.read_text(encoding="utf-8").replace(
                        "</body>",
                        '<script src="/fixture.js"></script>\n</body>',
                        1,
                    ),
                    encoding="utf-8",
                )

            with self.assertRaisesRegex(SystemExit, "fixture.js -> PROVENANCE.json"):
                release_preparer.validate_public_asset_provenance(
                    pages_root=fixture_pages,
                    app_hub_root=fixture_hub,
                )

    def test_public_asset_provenance_scans_each_html_srcset_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fixture_pages = tmp_path / "pages"
            fixture_hub = tmp_path / "app-hub"
            shutil.copytree(PAGES_ROOT, fixture_pages)
            shutil.copytree(APP_HUB_ROOT, fixture_hub)
            for name in ("index.html", "launch2.html"):
                page_path = fixture_pages / name
                page_path.write_text(
                    page_path.read_text(encoding="utf-8").replace(
                        "</body>",
                        (
                            '<img srcset="/assets/feature-setup.png 1x, '
                            '/preview/PROVENANCE.json 2x" alt="fixture">\n</body>'
                        ),
                        1,
                    ),
                    encoding="utf-8",
                )

            with self.assertRaisesRegex(SystemExit, "index.html -> PROVENANCE.json"):
                release_preparer.validate_public_asset_provenance(
                    pages_root=fixture_pages,
                    app_hub_root=fixture_hub,
                )

    def test_public_asset_provenance_skips_dynamic_javascript_template_literals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dynamic-fixture.js"
            path.write_text(
                "const dynamicAsset = `/assets/${name}.png`;\n",
                encoding="utf-8",
            )
            self.assertNotIn(
                "/assets/${name}.png",
                list(release_preparer._iter_local_reference_values(path)),
            )

    def test_promoted_pages_reject_metadata_or_dmg_mismatch_before_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dmg = tmp_path / TARGET_DMG_NAME
            dmg.write_bytes(b"actual bytes")
            release_json = tmp_path / "release.json"
            update_json = tmp_path / "update.json"
            common = {
                "channel": "stable-v2",
                "download_url": (
                    "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/"
                    f"releases/{TARGET_VERSION}/{TARGET_DMG_NAME.replace(' ', '%20')}"
                ),
                "release_notes_url": (
                    "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/"
                    f"releases/{TARGET_VERSION}/RELEASE_NOTES.txt"
                ),
                "file_size_bytes": len(b"actual bytes"),
                "sha256": "f" * 64,
                "published_at": "2026-08-01T00:00:00Z",
                "source_commit": "1" * 40,
                "source_tree_dirty": False,
            }
            release_json.write_text(
                json.dumps(
                    {
                        **common,
                        "schema": release_preparer.RELEASE_SCHEMA,
                        "app_name": release_preparer.APP_NAME,
                        "version": TARGET_VERSION,
                        "release_id": TARGET_VERSION,
                        "file_name": TARGET_DMG_NAME,
                        "update_manifest_url": (
                            "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/"
                            "releases/stable-v2/update.json"
                        ),
                        "notarized": True,
                        "clinical_use": False,
                    }
                ),
                encoding="utf-8",
            )
            update_json.write_text(
                json.dumps(
                    {
                        **common,
                        "schema": release_preparer.UPDATE_SCHEMA,
                        "latest_version": TARGET_VERSION,
                        "minimum_supported_version": TARGET_VERSION,
                    }
                ),
                encoding="utf-8",
            )
            output_root = tmp_path / "promoted"

            with self.assertRaisesRegex(SystemExit, "DMG SHA-256"):
                release_preparer.materialize_promoted_pages(
                    repo_root=ROOT,
                    output_root=output_root,
                    release_json=release_json,
                    update_json=update_json,
                    dmg=dmg,
                )
            self.assertFalse(output_root.exists())

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

            release_preparer.main(
                [
                    "--version",
                    TARGET_VERSION,
                    "--channel",
                    "candidate",
                    "--release-id",
                    CANDIDATE_RELEASE_ID,
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
                artifact_verifier=lambda _dmg, _version: None,
            )

            update = json.loads((r2_root / "releases" / "candidate" / "update.json").read_text(encoding="utf-8"))
            release_dir = r2_root / "releases" / CANDIDATE_RELEASE_ID
            release = json.loads((release_dir / "release.json").read_text(encoding="utf-8"))
            checksums = (release_dir / "SHA256SUMS.txt").read_text(encoding="utf-8")
            release_notes = (release_dir / "RELEASE_NOTES.txt").read_text(encoding="utf-8")
            upload_plan = json.loads((r2_root / "upload-plan.json").read_text(encoding="utf-8"))
            expected_sha = hashlib.sha256(b"fake dmg").hexdigest()

            self.assertEqual(update["latest_version"], TARGET_VERSION)
            self.assertEqual(update["minimum_supported_version"], "0.1.2")
            self.assertEqual(update["sha256"], release["sha256"])
            self.assertEqual(update["sha256"], expected_sha)
            self.assertEqual(update["file_size_bytes"], release["file_size_bytes"])
            self.assertEqual(update["file_size_bytes"], len(b"fake dmg"))
            self.assertRegex(release["source_commit"], r"^[0-9a-f]{40,64}$")
            self.assertEqual(update["source_commit"], release["source_commit"])
            self.assertIsInstance(release["source_tree_dirty"], bool)
            self.assertEqual(
                update["source_tree_dirty"], release["source_tree_dirty"]
            )
            self.assertIn(
                f"totalsegmentator-wrapper-mac/releases/{CANDIDATE_RELEASE_ID}/{TARGET_DMG_NAME.replace(' ', '%20')}",
                update["download_url"],
            )
            self.assertIn(f"{expected_sha}  {TARGET_DMG_NAME}", checksums)
            self.assertEqual(release["file_size_bytes"], len(b"fake dmg"))
            self.assertEqual(release["file_name"], TARGET_DMG_NAME)
            self.assertFalse(release["notarized"])
            self.assertIn("has not been verified as Developer ID signed and Apple notarized", release_notes)
            self.assertNotIn("- Developer ID signed and Apple notarized DMG", release_notes)
            self.assertIn("gingiva.stl", release_notes)
            self.assertIn("MeshSegNet uses label 0", release_notes)
            self.assertIn("No gingiva STL is emitted when label 0 is absent", release_notes)
            self.assertEqual(upload_plan["bucket"], "lacramy-downloads")
            self.assertEqual(upload_plan["object_prefix"], "totalsegmentator-wrapper-mac")
            self.assertEqual(len(upload_plan["objects"]), 5)
            self.assertTrue(all(item["immutable"] for item in upload_plan["objects"][:4]))
            self.assertFalse(upload_plan["objects"][-1]["immutable"])
            self.assertTrue(upload_plan["objects"][-1]["key"].endswith("/candidate/update.json"))

    def test_prepare_stable_v2_release_rejects_fake_dmg_even_when_marked_notarized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_dmg = Path(tmp) / TARGET_DMG_NAME
            fake_dmg.write_bytes(b"fake dmg")
            with self.assertRaisesRegex(SystemExit, "failed notarization verification"):
                release_preparer.main(
                    [
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
                    source_verifier=lambda _root: "1" * 40,
                )

    def test_prepare_stable_v2_release_requires_explicit_minimum_supported_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_dmg = Path(tmp) / TARGET_DMG_NAME
            fake_dmg.write_bytes(b"fake dmg")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--version",
                    TARGET_VERSION,
                    "--channel",
                    "stable-v2",
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

    def test_prepare_stable_v2_release_rejects_non_notarized_dmg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_dmg = Path(tmp) / TARGET_DMG_NAME
            fake_dmg.write_bytes(b"fake dmg")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--version",
                    TARGET_VERSION,
                    "--channel",
                    "stable-v2",
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
            self.assertIn("stable-v2 releases require a notarized DMG", completed.stderr)


if __name__ == "__main__":
    unittest.main()
