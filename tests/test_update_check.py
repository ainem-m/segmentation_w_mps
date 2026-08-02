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

    def test_rejects_manifest_url_userinfo_and_nonstandard_port(self) -> None:
        for manifest_url in (
            "https://user:secret@example.invalid/update.json",
            "https://example.invalid:444/update.json",
        ):
            with self.subTest(manifest_url=manifest_url):
                result = check_for_update(manifest_url=manifest_url)
                self.assertEqual(result.status, "failed")
                self.assertIn("HTTPS", result.error or "")

    def test_rejects_redirected_manifest_response_on_unallowed_host(self) -> None:
        manifest = _valid_manifest()
        response = _FakeResponse(
            manifest,
            final_url="https://redirected.example.invalid/update.json",
        )
        with mock.patch("urllib.request.urlopen", return_value=response):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.1.0",
            )

        self.assertEqual(result.status, "failed")
        self.assertIn("manifest response", result.error or "")

    def test_rejects_unsafe_redirected_manifest_response_url(self) -> None:
        for final_url in (
            "http://example.invalid/update.json",
            "https://user:secret@example.invalid/update.json",
            "https://example.invalid:444/update.json",
        ):
            with self.subTest(final_url=final_url), mock.patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(_valid_manifest(), final_url=final_url),
            ):
                result = check_for_update(
                    manifest_url="https://example.invalid/update.json",
                    current_version="0.1.0",
                )

            self.assertEqual(result.status, "failed")
            self.assertIn("manifest response", result.error or "")

    def test_allows_redirected_manifest_response_on_explicit_host(self) -> None:
        manifest = _valid_manifest()
        response = _FakeResponse(
            manifest,
            final_url="https://DOWNLOADS.EXAMPLE.INVALID./update.json",
        )
        with mock.patch("urllib.request.urlopen", return_value=response):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.1.0",
                allowed_link_hosts={"downloads.example.invalid"},
            )

        self.assertEqual(result.status, "update_available")

    def test_host_comparison_normalizes_case_trailing_dot_and_standard_port(self) -> None:
        manifest = _valid_manifest()
        with mock.patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(
                manifest,
                final_url="https://example.invalid/update.json",
            ),
        ):
            result = check_for_update(
                manifest_url="https://EXAMPLE.INVALID.:443/update.json",
                current_version="0.1.0",
            )

        self.assertEqual(result.status, "update_available")

    def test_detects_available_update_from_static_manifest(self) -> None:
        manifest = {
            "schema": "totalsegmentator_wrapper_mac.update_manifest.v1",
            "latest_version": "0.2.0",
            "minimum_supported_version": "0.1.0",
            "channel": "alpha",
            "download_url": "https://example.invalid/download.dmg",
            "release_notes_url": "https://example.invalid/notes",
            "sha256": "a" * 64,
            "file_size_bytes": 987654,
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
        self.assertEqual(result.sha256, "a" * 64)
        self.assertEqual(result.file_size_bytes, 987654)
        self.assertEqual(result.to_dict()["file_size_bytes"], 987654)
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
            "sha256": "a" * 64,
            "published_at": "2026-06-14T00:00:00Z",
        }

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(manifest)):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.1.0",
            )

        self.assertEqual(result.status, "update_available")
        self.assertTrue(result.update_available)

    def test_accepts_stable_v2_update_channel(self) -> None:
        manifest = {
            "schema": "totalsegmentator_wrapper_mac.update_manifest.v1",
            "latest_version": "0.4.2",
            "minimum_supported_version": "0.4.1",
            "channel": "stable-v2",
            "download_url": "https://example.invalid/download.dmg",
            "release_notes_url": "https://example.invalid/notes",
            "sha256": "a" * 64,
            "file_size_bytes": 987654,
            "published_at": "2026-08-01T00:00:00Z",
        }

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(manifest)):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.4.1",
            )

        self.assertEqual(result.status, "update_available")
        self.assertTrue(result.update_available)

    def test_stable_v2_requires_bounded_file_size(self) -> None:
        for file_size in (None, (4 * 1024 * 1024 * 1024) + 1):
            manifest = _valid_manifest()
            manifest["channel"] = "stable-v2"
            if file_size is None:
                manifest.pop("file_size_bytes")
            else:
                manifest["file_size_bytes"] = file_size
            with self.subTest(file_size=file_size), mock.patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(manifest),
            ):
                result = check_for_update(
                    manifest_url="https://example.invalid/update.json",
                    current_version="0.1.0",
                )

            self.assertEqual(result.status, "failed")
            self.assertIn("file_size_bytes", result.error or "")

    def test_detects_critical_update(self) -> None:
        manifest = {
            "schema": "totalsegmentator_wrapper_mac.update_manifest.v1",
            "latest_version": "0.3.0",
            "minimum_supported_version": "0.2.0",
            "channel": "alpha",
            "download_url": "https://example.invalid/download.dmg",
            "release_notes_url": "https://example.invalid/notes",
            "sha256": "a" * 64,
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
            "sha256": "a" * 64,
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
            "sha256": "a" * 64,
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

    def test_rejects_manifest_larger_than_one_mebibyte(self) -> None:
        payload = json.dumps(_valid_manifest()).encode("utf-8")
        oversized = payload + (b" " * ((1024 * 1024) + 1 - len(payload)))
        with mock.patch(
            "urllib.request.urlopen",
            return_value=_FakeBytesResponse(oversized),
        ):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.1.0",
            )

        self.assertEqual(result.status, "failed")
        self.assertIn("too large", result.error or "")

    def test_accepts_manifest_at_one_mebibyte_limit(self) -> None:
        payload = json.dumps(_valid_manifest()).encode("utf-8")
        at_limit = payload + (b" " * ((1024 * 1024) - len(payload)))
        with mock.patch(
            "urllib.request.urlopen",
            return_value=_FakeBytesResponse(at_limit),
        ):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.1.0",
            )

        self.assertEqual(result.status, "update_available")

    def test_rejects_noncanonical_sha256(self) -> None:
        for sha256 in ("abc123", "A" * 64, "g" * 64, "a" * 63):
            manifest = _valid_manifest()
            manifest["sha256"] = sha256
            with self.subTest(sha256=sha256), mock.patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(manifest),
            ):
                result = check_for_update(
                    manifest_url="https://example.invalid/update.json",
                    current_version="0.1.0",
                )

            self.assertEqual(result.status, "failed")
            self.assertIn("sha256", result.error or "")

    def test_rejects_invalid_optional_file_size(self) -> None:
        manifest = {
            "schema": "totalsegmentator_wrapper_mac.update_manifest.v1",
            "latest_version": "0.2.0",
            "minimum_supported_version": "0.1.0",
            "channel": "stable",
            "download_url": "https://example.invalid/download.dmg",
            "release_notes_url": "https://example.invalid/notes",
            "sha256": "a" * 64,
            "file_size_bytes": 0,
            "published_at": "2026-06-14T00:00:00Z",
        }

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(manifest)):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.1.0",
            )

        self.assertEqual(result.status, "failed")
        self.assertIn("file_size_bytes", result.error or "")

    def test_version_compare_is_numeric(self) -> None:
        self.assertGreater(compare_versions("0.10.0", "0.2.0"), 0)
        self.assertEqual(compare_versions("0.1.0", "0.1.0"), 0)
        self.assertLess(compare_versions("0.1.0", "0.1.1"), 0)
        for malformed in ("0.1", "0.1.0-alpha", "01.1.0", "v0.1.0", "0..1"):
            with self.subTest(malformed=malformed), self.assertRaisesRegex(
                ValueError,
                "semantic version triplet",
            ):
                compare_versions(malformed, "0.1.0")

    def test_rejects_manifest_with_minimum_newer_than_latest(self) -> None:
        manifest = _valid_manifest()
        manifest["latest_version"] = "0.4.2"
        manifest["minimum_supported_version"] = "0.4.3"

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(manifest)):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.4.1",
            )

        self.assertEqual(result.status, "failed")
        self.assertFalse(result.update_available)
        self.assertFalse(result.critical)
        self.assertEqual(
            result.error,
            "update manifest latest_version must not be older than minimum_supported_version",
        )

    def test_rejects_manifest_that_would_downgrade_current_app(self) -> None:
        manifest = _valid_manifest()
        manifest["latest_version"] = "0.4.1"
        manifest["minimum_supported_version"] = "0.4.0"

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(manifest)):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.4.2",
            )

        self.assertEqual(result.status, "failed")
        self.assertFalse(result.update_available)
        self.assertFalse(result.critical)
        self.assertEqual(
            result.error,
            "update manifest latest_version is older than current_version",
        )

    def test_equal_manifest_version_is_current_and_never_critical(self) -> None:
        manifest = _valid_manifest()
        manifest["latest_version"] = "0.4.2"
        manifest["minimum_supported_version"] = "0.4.2"

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(manifest)):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.4.2",
            )

        self.assertEqual(result.status, "current")
        self.assertFalse(result.update_available)
        self.assertFalse(result.critical)

    def test_rejects_malformed_manifest_and_current_versions(self) -> None:
        for field, value in (
            ("latest_version", "0.4"),
            ("minimum_supported_version", "0.4.1-alpha"),
        ):
            manifest = _valid_manifest()
            manifest[field] = value
            with self.subTest(field=field), mock.patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(manifest),
            ):
                result = check_for_update(
                    manifest_url="https://example.invalid/update.json",
                    current_version="0.4.0",
                )
            self.assertEqual(result.status, "failed")
            self.assertEqual(
                result.error,
                f"update manifest {field} must be a semantic version triplet",
            )

        with mock.patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(_valid_manifest()),
        ):
            result = check_for_update(
                manifest_url="https://example.invalid/update.json",
                current_version="0.4",
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.error,
            "current_version must be a semantic version triplet",
        )

    def test_012_stable_manifest_updates_existing_011_clients(self) -> None:
        manifest = {
            "schema": "totalsegmentator_wrapper_mac.update_manifest.v1",
            "latest_version": "0.1.2",
            "minimum_supported_version": "0.1.1",
            "channel": "stable",
            "download_url": "https://example.invalid/download.dmg",
            "release_notes_url": "https://example.invalid/notes",
            "sha256": "a" * 64,
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


def _valid_manifest() -> dict:
    return {
        "schema": "totalsegmentator_wrapper_mac.update_manifest.v1",
        "latest_version": "0.2.0",
        "minimum_supported_version": "0.1.0",
        "channel": "stable",
        "download_url": "https://example.invalid/download.dmg",
        "release_notes_url": "https://example.invalid/notes",
        "sha256": "a" * 64,
        "file_size_bytes": 987654,
        "published_at": "2026-08-01T00:00:00Z",
    }


class _FakeResponse:
    def __init__(
        self,
        payload: dict,
        *,
        final_url: str = "https://example.invalid/update.json",
    ) -> None:
        self.payload = payload
        self.final_url = final_url
        self.offset = 0

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        encoded = json.dumps(self.payload).encode("utf-8")
        chunk = encoded[self.offset : self.offset + limit]
        self.offset += len(chunk)
        return chunk

    def geturl(self) -> str:
        return self.final_url


class _FakeBytesResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        final_url: str = "https://example.invalid/update.json",
    ) -> None:
        self.payload = payload
        self.final_url = final_url
        self.offset = 0

    def __enter__(self) -> "_FakeBytesResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        chunk = self.payload[self.offset : self.offset + limit]
        self.offset += len(chunk)
        return chunk

    def geturl(self) -> str:
        return self.final_url


if __name__ == "__main__":
    unittest.main()
