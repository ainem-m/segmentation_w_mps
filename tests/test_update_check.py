from __future__ import annotations

import json
import unittest
from unittest import mock

from totalsegmentator_wrapper_mac.update_check import check_for_update, compare_versions, update_request_metadata


class UpdateCheckTests(unittest.TestCase):
    def test_rejects_non_https_manifest_url(self) -> None:
        result = check_for_update(manifest_url="http://example.invalid/update.json")

        self.assertEqual(result.status, "failed")
        self.assertIn("HTTPS", result.error or "")

    def test_detects_available_update_from_static_manifest(self) -> None:
        manifest = {
            "schema": "totalsegmentator_wrapper_mac.update_manifest.v1",
            "latest_version": "0.2.0",
            "minimum_supported_version": "0.1.0",
            "channel": "alpha",
            "download_url": "https://example.invalid/download.dmg",
            "release_notes_url": "https://example.invalid/notes",
            "sha256": "abc123",
            "published_at": "2026-06-14T00:00:00Z",
        }

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(manifest)) as urlopen:
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.1.0",
                timeout_sec=5,
            )

        self.assertEqual(result.status, "update_available")
        self.assertTrue(result.update_available)
        self.assertFalse(result.critical)
        self.assertEqual(result.download_url, "https://example.invalid/download.dmg")
        self.assertEqual(result.sha256, "abc123")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.invalid/update.json")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("User-agent"), "TotalSegmentatorWrapperMac/0.1.0")
        self.assertNotIn("dicom", request.full_url.lower())
        self.assertNotIn("ct", request.full_url.lower())
        self.assertNotIn("path", request.full_url.lower())

    def test_accepts_stable_update_channel(self) -> None:
        manifest = {
            "schema": "totalsegmentator_wrapper_mac.update_manifest.v1",
            "latest_version": "0.2.0",
            "minimum_supported_version": "0.1.0",
            "channel": "stable",
            "download_url": "https://example.invalid/download.dmg",
            "release_notes_url": "https://example.invalid/notes",
            "sha256": "abc123",
            "published_at": "2026-06-14T00:00:00Z",
        }

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(manifest)):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.1.0",
            )

        self.assertEqual(result.status, "update_available")
        self.assertTrue(result.update_available)

    def test_detects_critical_update(self) -> None:
        manifest = {
            "schema": "totalsegmentator_wrapper_mac.update_manifest.v1",
            "latest_version": "0.3.0",
            "minimum_supported_version": "0.2.0",
            "channel": "alpha",
            "download_url": "https://example.invalid/download.dmg",
            "release_notes_url": "https://example.invalid/notes",
            "sha256": "abc123",
            "published_at": "2026-06-14T00:00:00Z",
        }

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(manifest)):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.1.0",
            )

        self.assertEqual(result.status, "critical_update_available")
        self.assertTrue(result.critical)

    def test_rejects_cross_origin_update_links(self) -> None:
        manifest = {
            "schema": "totalsegmentator_wrapper_mac.update_manifest.v1",
            "latest_version": "0.2.0",
            "minimum_supported_version": "0.1.0",
            "channel": "alpha",
            "download_url": "https://downloads.example.invalid/download.dmg",
            "release_notes_url": "https://example.invalid/notes",
            "sha256": "abc123",
            "published_at": "2026-06-14T00:00:00Z",
        }

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(manifest)):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.1.0",
            )

        self.assertEqual(result.status, "failed")
        self.assertIn("manifest host", result.error or "")

    def test_allows_explicit_update_link_host_allowlist(self) -> None:
        manifest = {
            "schema": "totalsegmentator_wrapper_mac.update_manifest.v1",
            "latest_version": "0.2.0",
            "minimum_supported_version": "0.1.0",
            "channel": "alpha",
            "download_url": "https://downloads.example.invalid/download.dmg",
            "release_notes_url": "https://example.invalid/notes",
            "sha256": "abc123",
            "published_at": "2026-06-14T00:00:00Z",
        }

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(manifest)):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.1.0",
                allowed_link_hosts={"downloads.example.invalid"},
            )

        self.assertEqual(result.status, "update_available")
        self.assertEqual(result.download_url, "https://downloads.example.invalid/download.dmg")

    def test_invalid_manifest_utf8_returns_failed_result(self) -> None:
        with mock.patch("urllib.request.urlopen", return_value=_FakeBytesResponse(b"\xff")):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.1.0",
            )

        self.assertEqual(result.status, "failed")

    def test_version_compare_is_numeric(self) -> None:
        self.assertGreater(compare_versions("0.10.0", "0.2.0"), 0)
        self.assertEqual(compare_versions("0.1.0", "0.1"), 0)
        self.assertLess(compare_versions("0.1.0", "0.1.1"), 0)

    def test_012_stable_manifest_updates_existing_011_clients(self) -> None:
        manifest = {
            "schema": "totalsegmentator_wrapper_mac.update_manifest.v1",
            "latest_version": "0.1.2",
            "minimum_supported_version": "0.1.1",
            "channel": "stable",
            "download_url": "https://example.invalid/download.dmg",
            "release_notes_url": "https://example.invalid/notes",
            "sha256": "abc123",
            "published_at": "2026-07-08T00:00:00Z",
        }

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(manifest)):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.1.1",
            )

        self.assertEqual(result.status, "update_available")
        self.assertTrue(result.update_available)
        self.assertFalse(result.critical)

    def test_update_request_metadata_excludes_user_data(self) -> None:
        metadata = update_request_metadata()
        joined = " ".join(f"{key}={value}" for key, value in metadata.items()).lower()

        self.assertIn("app_version", metadata)
        self.assertNotIn("dicom", joined)
        self.assertNotIn("ct=", joined)
        self.assertNotIn("path", joined)
        self.assertNotIn("user", joined)
        self.assertNotIn("log", joined)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _FakeBytesResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeBytesResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.payload


if __name__ == "__main__":
    unittest.main()
