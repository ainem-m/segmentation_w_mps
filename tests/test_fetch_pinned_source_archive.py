from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
import urllib.request
import urllib.error
from pathlib import Path

from scripts.fetch_pinned_source_archive import (
    PROVENANCE_NAME,
    PinnedSourceError,
    download_pinned_archive,
    extract_pinned_tar_gz,
    main,
)


class FakeResponse(io.BytesIO):
    def __init__(
        self,
        payload: bytes,
        *,
        status: int,
        headers: dict[str, str],
        url: str = "https://codeload.github.com/example/source/tar.gz/v1",
    ) -> None:
        super().__init__(payload)
        self.status = status
        self.headers = headers
        self._url = url

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self._url


class PinnedSourceArchiveTests(unittest.TestCase):
    def test_interrupted_download_leaves_partial_and_resumes_with_strict_range(self) -> None:
        payload = b"abcdef"
        digest = hashlib.sha256(payload).hexdigest()
        url = "https://github.com/example/source/archive/refs/tags/v1.tar.gz"
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "source.tar.gz"
            requests: list[urllib.request.Request] = []

            def interrupted(request: urllib.request.Request, **_: object) -> FakeResponse:
                requests.append(request)
                return FakeResponse(
                    payload[:3],
                    status=200,
                    headers={"Content-Length": str(len(payload))},
                )

            with self.assertRaisesRegex(PinnedSourceError, "ended early"):
                download_pinned_archive(
                    url=url,
                    expected_sha256=digest,
                    archive=archive,
                    opener=interrupted,
                )
            self.assertEqual((Path(tmp) / "source.tar.gz.part").read_bytes(), b"abc")

            def resumed(request: urllib.request.Request, **_: object) -> FakeResponse:
                requests.append(request)
                self.assertEqual(request.get_header("Range"), "bytes=3-")
                return FakeResponse(
                    payload[3:],
                    status=206,
                    headers={
                        "Content-Range": "bytes 3-5/6",
                        "Content-Length": "3",
                    },
                )

            result = download_pinned_archive(
                url=url,
                expected_sha256=digest,
                archive=archive,
                opener=resumed,
            )
            self.assertEqual(result.read_bytes(), payload)
            self.assertFalse((Path(tmp) / "source.tar.gz.part").exists())
            self.assertEqual(len(requests), 2)

    def test_range_ignored_restarts_without_concatenating_partial(self) -> None:
        payload = b"complete"
        digest = hashlib.sha256(payload).hexdigest()
        url = "https://github.com/example/source/archive/refs/tags/v1.tar.gz"
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "source.tar.gz"
            part = Path(tmp) / "source.tar.gz.part"
            sidecar = Path(tmp) / "source.tar.gz.part.json"
            part.write_bytes(b"partial")
            sidecar.write_text(
                json.dumps(
                    {
                        "schema": "totalsegmentator_wrapper_mac.pinned_source_download.v1",
                        "url": url,
                        "sha256": digest,
                    }
                ),
                encoding="utf-8",
            )

            def ignored(request: urllib.request.Request, **_: object) -> FakeResponse:
                self.assertEqual(request.get_header("Range"), "bytes=7-")
                return FakeResponse(
                    payload,
                    status=200,
                    headers={"Content-Length": str(len(payload))},
                )

            download_pinned_archive(
                url=url,
                expected_sha256=digest,
                archive=archive,
                opener=ignored,
            )
            self.assertEqual(archive.read_bytes(), payload)

    def test_http_416_with_stale_partial_restarts_once(self) -> None:
        payload = b"fresh-after-416"
        digest = hashlib.sha256(payload).hexdigest()
        url = "https://github.com/example/source/archive/refs/tags/v1.tar.gz"
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "source.tar.gz"
            part = Path(tmp) / "source.tar.gz.part"
            sidecar = Path(tmp) / "source.tar.gz.part.json"
            part.write_bytes(b"stale")
            sidecar.write_text(
                json.dumps(
                    {
                        "schema": "totalsegmentator_wrapper_mac.pinned_source_download.v1",
                        "url": url,
                        "sha256": digest,
                    }
                ),
                encoding="utf-8",
            )
            calls = 0

            def opener(request: urllib.request.Request, **_: object) -> FakeResponse:
                nonlocal calls
                calls += 1
                if calls == 1:
                    self.assertEqual(request.get_header("Range"), "bytes=5-")
                    raise urllib.error.HTTPError(url, 416, "range", {}, None)
                self.assertIsNone(request.get_header("Range"))
                return FakeResponse(
                    payload,
                    status=200,
                    headers={"Content-Length": str(len(payload))},
                )

            download_pinned_archive(
                url=url,
                expected_sha256=digest,
                archive=archive,
                opener=opener,
            )
            self.assertEqual(calls, 2)
            self.assertEqual(archive.read_bytes(), payload)

    def test_symlink_partial_is_rejected_without_touching_target(self) -> None:
        payload = b"outside"
        digest = hashlib.sha256(b"expected").hexdigest()
        url = "https://github.com/example/source/archive/refs/tags/v1.tar.gz"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.write_bytes(payload)
            part = root / "source.tar.gz.part"
            part.symlink_to(outside)
            with self.assertRaisesRegex(PinnedSourceError, "owner-controlled regular"):
                download_pinned_archive(
                    url=url,
                    expected_sha256=digest,
                    archive=root / "source.tar.gz",
                    opener=lambda *_args, **_kwargs: self.fail("network must not be used"),
                )
            self.assertEqual(outside.read_bytes(), payload)

    def test_source_url_rejects_credentials_and_non_https_default_port(self) -> None:
        digest = hashlib.sha256(b"expected").hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            for url in (
                "https://user@github.com/example/source.tar.gz",
                "https://github.com:444/example/source.tar.gz",
            ):
                with self.subTest(url=url), self.assertRaisesRegex(
                    PinnedSourceError, "approved host"
                ):
                    download_pinned_archive(
                        url=url,
                        expected_sha256=digest,
                        archive=Path(tmp) / "source.tar.gz",
                        opener=lambda *_args, **_kwargs: self.fail("network must not be used"),
                    )

    def test_sidecar_identity_mismatch_discards_only_partial_cache(self) -> None:
        payload = b"fresh"
        digest = hashlib.sha256(payload).hexdigest()
        url = "https://github.com/example/source/archive/refs/tags/v1.tar.gz"
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "source.tar.gz"
            (Path(tmp) / "source.tar.gz.part").write_bytes(b"wrong")
            (Path(tmp) / "source.tar.gz.part.json").write_text(
                json.dumps({"url": "https://github.com/other/source"}),
                encoding="utf-8",
            )

            def fresh(request: urllib.request.Request, **_: object) -> FakeResponse:
                self.assertIsNone(request.get_header("Range"))
                return FakeResponse(
                    payload,
                    status=200,
                    headers={"Content-Length": str(len(payload))},
                )

            download_pinned_archive(
                url=url,
                expected_sha256=digest,
                archive=archive,
                opener=fresh,
            )
            self.assertEqual(archive.read_bytes(), payload)

    def test_safe_extract_records_provenance_and_rejects_traversal(self) -> None:
        url = "https://github.com/example/source/archive/refs/tags/v1.tar.gz"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                content = b"project fixture\n"
                info = tarfile.TarInfo("Source-1.0/CMakeLists.txt")
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            extracted = extract_pinned_tar_gz(
                archive=archive,
                output_parent=root / "sources",
                expected_root="Source-1.0",
                url=url,
                expected_sha256=digest,
            )
            self.assertEqual(
                (extracted / "CMakeLists.txt").read_text(encoding="utf-8"),
                "project fixture\n",
            )
            provenance = json.loads(
                (extracted / PROVENANCE_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(provenance["sha256"], digest)
            self.assertEqual(
                extract_pinned_tar_gz(
                    archive=archive,
                    output_parent=root / "sources",
                    expected_root="Source-1.0",
                    url=url,
                    expected_sha256=digest,
                ),
                extracted,
            )

            unsafe = root / "unsafe.tar.gz"
            with tarfile.open(unsafe, "w:gz") as tar:
                content = b"escape"
                info = tarfile.TarInfo("Source-1.0/../escape")
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            unsafe_digest = hashlib.sha256(unsafe.read_bytes()).hexdigest()
            with self.assertRaisesRegex(PinnedSourceError, "unsafe source archive member"):
                extract_pinned_tar_gz(
                    archive=unsafe,
                    output_parent=root / "unsafe-output",
                    expected_root="Source-1.0",
                    url=url,
                    expected_sha256=unsafe_digest,
                )

    def test_extract_rejects_symlink_archive_and_output_parent(self) -> None:
        url = "https://github.com/example/source/archive/refs/tags/v1.tar.gz"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual_archive = root / "actual.tar.gz"
            with tarfile.open(actual_archive, "w:gz") as tar:
                content = b"fixture"
                info = tarfile.TarInfo("Source-1.0/file")
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            digest = hashlib.sha256(actual_archive.read_bytes()).hexdigest()
            linked_archive = root / "linked.tar.gz"
            linked_archive.symlink_to(actual_archive)
            with self.assertRaisesRegex(PinnedSourceError, "owner-controlled regular"):
                extract_pinned_tar_gz(
                    archive=linked_archive,
                    output_parent=root / "output-a",
                    expected_root="Source-1.0",
                    url=url,
                    expected_sha256=digest,
                )

            outside = root / "outside"
            outside.mkdir()
            linked_output = root / "linked-output"
            linked_output.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(PinnedSourceError, "non-symlink directory"):
                extract_pinned_tar_gz(
                    archive=actual_archive,
                    output_parent=linked_output,
                    expected_root="Source-1.0",
                    url=url,
                    expected_sha256=digest,
                )
            self.assertEqual(list(outside.iterdir()), [])

    def test_cli_rejects_symlink_archive_and_output_parent_before_resolution(self) -> None:
        url = "https://github.com/example/source/archive/refs/tags/v1.tar.gz"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual_archive = root / "actual.tar.gz"
            with tarfile.open(actual_archive, "w:gz") as tar:
                content = b"fixture"
                info = tarfile.TarInfo("Source-1.0/file")
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            digest = hashlib.sha256(actual_archive.read_bytes()).hexdigest()
            linked_archive = root / "linked.tar.gz"
            linked_archive.symlink_to(actual_archive)
            with self.assertRaisesRegex(PinnedSourceError, "owner-controlled regular"):
                main(
                    [
                        "--url", url,
                        "--sha256", digest,
                        "--archive", str(linked_archive),
                        "--output-parent", str(root / "output-a"),
                        "--expected-root", "Source-1.0",
                    ]
                )

            outside = root / "outside"
            outside.mkdir()
            linked_output = root / "linked-output"
            linked_output.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(PinnedSourceError, "non-symlink directory"):
                main(
                    [
                        "--url", url,
                        "--sha256", digest,
                        "--archive", str(actual_archive),
                        "--output-parent", str(linked_output),
                        "--expected-root", "Source-1.0",
                    ]
                )
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
