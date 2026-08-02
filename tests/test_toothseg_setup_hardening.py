from __future__ import annotations

import hashlib
import io
import json
import errno
import os
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import totalsegmentator_wrapper_mac.toothseg_setup as sut


class _Response:
    def __init__(
        self,
        chunks: list[bytes | BaseException],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        url: str = "https://example.test/ToothSeg.zip",
    ) -> None:
        self._chunks = list(chunks)
        self.status = status
        self.headers = headers or {}
        self._url = url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        value = self._chunks.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self._url


def _md5(payload: bytes) -> str:
    return hashlib.md5(payload).hexdigest()  # noqa: S324 - fixture integrity.


def _request_range(request: str | object) -> str | None:
    if isinstance(request, str):
        return None
    return request.get_header("Range")  # type: ignore[no-any-return, union-attr]


def _request_accept_encoding(request: str | object) -> str | None:
    if isinstance(request, str):
        return None
    return request.get_header("Accept-encoding")  # type: ignore[no-any-return, union-attr]


def _seed_partial(destination: Path, payload: bytes, *, url: str, expected_md5: str) -> None:
    partial = destination.with_name(destination.name + ".part")
    partial.write_bytes(payload)
    sut._write_json(  # noqa: SLF001 - exercise the on-disk contract.
        partial.with_name(partial.name + ".json"),
        {
            "schema": sut.PARTIAL_DOWNLOAD_SCHEMA,
            "url": url,
            "expected_md5": expected_md5,
            "total_bytes": None,
        },
    )


class ToothSegDownloadHardeningTests(unittest.TestCase):
    def test_main_download_requests_identity_encoding(self) -> None:
        payload = b"complete"
        url = "https://example.test/ToothSeg.zip"
        requests: list[str | object] = []

        def responder(request: str | object, **_kwargs: object) -> _Response:
            requests.append(request)
            return _Response(
                [payload],
                headers={"Content-Length": str(len(payload)), "Content-Encoding": "identity"},
                url="https://cdn.example.test:443/ToothSeg.zip",
            )

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            sut.urllib.request,
            "urlopen",
            side_effect=responder,
        ):
            sut._download_with_md5(  # noqa: SLF001
                url,
                Path(tmp) / "ToothSeg.zip",
                expected_md5=_md5(payload),
                timeout_sec=30,
            )

        self.assertEqual(_request_accept_encoding(requests[0]), "identity")

    def test_main_download_rejects_insecure_redirect_and_nonidentity_encoding(self) -> None:
        payload = b"complete"
        cases = (
            _Response([payload], url="http://cdn.example.test/ToothSeg.zip"),
            _Response([payload], headers={"Content-Encoding": "gzip"}),
        )
        for index, response in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp, patch.object(
                sut.urllib.request,
                "urlopen",
                return_value=response,
            ):
                with self.assertRaisesRegex(RuntimeError, "HTTPS|Content-Encoding"):
                    sut._download_with_md5(  # noqa: SLF001
                        "https://example.test/ToothSeg.zip",
                        Path(tmp) / "ToothSeg.zip",
                        expected_md5=_md5(payload),
                        timeout_sec=30,
                    )

    def test_known_download_size_has_disk_space_preflight(self) -> None:
        payload = b"complete"
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            sut.shutil,
            "disk_usage",
            return_value=SimpleNamespace(free=len(payload) - 1),
        ), patch.object(
            sut.urllib.request,
            "urlopen",
            return_value=_Response([payload], headers={"Content-Length": str(len(payload))}),
        ):
            with self.assertRaises(OSError) as caught:
                sut._download_with_md5(  # noqa: SLF001
                    "https://example.test/ToothSeg.zip",
                    Path(tmp) / "ToothSeg.zip",
                    expected_md5=_md5(payload),
                    timeout_sec=30,
                )

        self.assertEqual(caught.exception.errno, errno.ENOSPC)

    def test_interruption_preserves_total_and_next_call_resumes(self) -> None:
        payload = b"abcdefghij"
        url = "https://example.test/ToothSeg.zip"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "ToothSeg.zip"
            interrupted = _Response(
                [payload[:4], OSError("connection reset")],
                headers={"Content-Length": str(len(payload))},
            )
            with patch.object(sut.urllib.request, "urlopen", return_value=interrupted):
                with self.assertRaisesRegex(OSError, "connection reset"):
                    sut._download_with_md5(  # noqa: SLF001
                        url,
                        destination,
                        expected_md5=_md5(payload),
                        timeout_sec=30,
                    )

            sidecar = json.loads(
                destination.with_name("ToothSeg.zip.part.json").read_text(encoding="utf-8")
            )
            self.assertEqual(sidecar["total_bytes"], len(payload))

            requests: list[str | object] = []

            def resume(request: str | object, **_kwargs: object) -> _Response:
                requests.append(request)
                return _Response(
                    [payload[4:]],
                    status=206,
                    headers={
                        "Content-Range": f"bytes 4-{len(payload) - 1}/{len(payload)}",
                        "Content-Length": str(len(payload) - 4),
                    },
                )

            with patch.object(sut.urllib.request, "urlopen", side_effect=resume):
                sut._download_with_md5(  # noqa: SLF001
                    url,
                    destination,
                    expected_md5=_md5(payload),
                    timeout_sec=30,
                )
            self.assertEqual(_request_range(requests[0]), "bytes=4-")
            self.assertEqual(_request_accept_encoding(requests[0]), "identity")
            self.assertEqual(destination.read_bytes(), payload)

    def test_legacy_partial_sidecar_is_migrated_and_resumed(self) -> None:
        payload = b"abcdefghij"
        url = "https://example.test/ToothSeg.zip"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "ToothSeg.zip"
            partial = destination.with_name("ToothSeg.zip.part")
            partial.write_bytes(payload[:4])
            partial.with_name("ToothSeg.zip.part.json").write_text(
                json.dumps({"url": url, "expected_md5": _md5(payload)}),
                encoding="utf-8",
            )
            requests: list[str | object] = []

            def responder(request: str | object, **_kwargs: object) -> _Response:
                requests.append(request)
                return _Response(
                    [payload[4:]],
                    status=206,
                    headers={"Content-Range": "bytes 4-9/10", "Content-Length": "6"},
                )

            with patch.object(sut.urllib.request, "urlopen", side_effect=responder):
                sut._download_with_md5(  # noqa: SLF001
                    url,
                    destination,
                    expected_md5=_md5(payload),
                    timeout_sec=30,
                )
            self.assertEqual(_request_range(requests[0]), "bytes=4-")
            self.assertEqual(destination.read_bytes(), payload)

    def test_short_206_continues_with_the_next_range(self) -> None:
        payload = b"abcdefghij"
        url = "https://example.test/ToothSeg.zip"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "ToothSeg.zip"
            _seed_partial(destination, payload[:4], url=url, expected_md5=_md5(payload))
            requests: list[str | object] = []
            responses = iter(
                (
                    _Response(
                        [payload[4:6]],
                        status=206,
                        headers={"Content-Range": "bytes 4-9/10", "Content-Length": "6"},
                    ),
                    _Response(
                        [payload[6:]],
                        status=206,
                        headers={"Content-Range": "bytes 6-9/10", "Content-Length": "4"},
                    ),
                )
            )

            def open_next(request: str | object, **_kwargs: object) -> _Response:
                requests.append(request)
                return next(responses)

            with patch.object(sut.urllib.request, "urlopen", side_effect=open_next):
                sut._download_with_md5(  # noqa: SLF001
                    url,
                    destination,
                    expected_md5=_md5(payload),
                    timeout_sec=30,
                )
            self.assertEqual([_request_range(value) for value in requests], ["bytes=4-", "bytes=6-"])
            self.assertEqual(destination.read_bytes(), payload)

    def test_http_416_restarts_once_without_joining_old_bytes(self) -> None:
        payload = b"complete"
        url = "https://example.test/ToothSeg.zip"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "ToothSeg.zip"
            _seed_partial(destination, b"stale", url=url, expected_md5=_md5(payload))
            requests: list[str | object] = []

            def responder(request: str | object, **_kwargs: object) -> _Response:
                requests.append(request)
                if len(requests) == 1:
                    raise urllib.error.HTTPError(url, 416, "range", {}, io.BytesIO())
                return _Response([payload], headers={"Content-Length": str(len(payload))})

            with patch.object(sut.urllib.request, "urlopen", side_effect=responder):
                sut._download_with_md5(  # noqa: SLF001
                    url,
                    destination,
                    expected_md5=_md5(payload),
                    timeout_sec=30,
                )
            self.assertEqual([_request_range(value) for value in requests], ["bytes=5-", None])
            self.assertEqual(destination.read_bytes(), payload)

    def test_second_http_416_fails_closed_after_single_restart(self) -> None:
        payload = b"complete"
        url = "https://example.test/ToothSeg.zip"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "ToothSeg.zip"
            _seed_partial(destination, b"stale", url=url, expected_md5=_md5(payload))
            calls = 0

            def responder(_request: str | object, **_kwargs: object) -> _Response:
                nonlocal calls
                calls += 1
                raise urllib.error.HTTPError(url, 416, "range", {}, io.BytesIO())

            with patch.object(sut.urllib.request, "urlopen", side_effect=responder):
                with self.assertRaisesRegex(RuntimeError, "after a safe restart"):
                    sut._download_with_md5(  # noqa: SLF001
                        url,
                        destination,
                        expected_md5=_md5(payload),
                        timeout_sec=30,
                    )
            self.assertEqual(calls, 2)
            self.assertFalse(destination.with_name("ToothSeg.zip.part").exists())

    def test_range_ignored_restarts_once_and_records_resume_origin(self) -> None:
        payload = b"complete"
        url = "https://example.test/ToothSeg.zip"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "ToothSeg.zip"
            _seed_partial(destination, b"old", url=url, expected_md5=_md5(payload))
            responses = iter(
                (
                    _Response([payload], status=200, headers={"Content-Length": str(len(payload))}),
                    _Response([payload], status=200, headers={"Content-Length": str(len(payload))}),
                )
            )
            requests: list[str | object] = []

            def responder(request: str | object, **_kwargs: object) -> _Response:
                requests.append(request)
                return next(responses)

            output = io.StringIO()
            with (
                patch.object(sut.urllib.request, "urlopen", side_effect=responder),
                patch("sys.stdout", output),
            ):
                sut._download_with_md5(  # noqa: SLF001
                    url,
                    destination,
                    expected_md5=_md5(payload),
                    timeout_sec=30,
                )
            self.assertEqual([_request_range(value) for value in requests], ["bytes=3-", None])
            progress = [
                json.loads(line.removeprefix(sut.PREP_PROGRESS_PREFIX))
                for line in output.getvalue().splitlines()
                if line.startswith(sut.PREP_PROGRESS_PREFIX)
            ]
            self.assertTrue(any(event.get("resumed") and event.get("resume_from_bytes") == 3 for event in progress))
            self.assertEqual(destination.read_bytes(), payload)

    def test_sidecar_mismatch_discards_partial_before_full_download(self) -> None:
        payload = b"complete"
        url = "https://example.test/ToothSeg.zip"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "ToothSeg.zip"
            _seed_partial(destination, b"wrong-prefix", url=url, expected_md5="other")
            requests: list[str | object] = []

            def responder(request: str | object, **_kwargs: object) -> _Response:
                requests.append(request)
                return _Response([payload], headers={"Content-Length": str(len(payload))})

            with patch.object(sut.urllib.request, "urlopen", side_effect=responder):
                sut._download_with_md5(  # noqa: SLF001
                    url,
                    destination,
                    expected_md5=_md5(payload),
                    timeout_sec=30,
                )
            self.assertIsNone(_request_range(requests[0]))
            self.assertEqual(destination.read_bytes(), payload)

    def test_content_range_total_mismatch_clears_unjoinable_partial(self) -> None:
        payload = b"abcdefghij"
        url = "https://example.test/ToothSeg.zip"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "ToothSeg.zip"
            _seed_partial(destination, payload[:4], url=url, expected_md5=_md5(payload))
            sidecar_path = destination.with_name("ToothSeg.zip.part.json")
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            sidecar["total_bytes"] = len(payload)
            sut._write_json(sidecar_path, sidecar)  # noqa: SLF001
            with patch.object(
                sut.urllib.request,
                "urlopen",
                return_value=_Response(
                    [payload[4:]],
                    status=206,
                    headers={"Content-Range": "bytes 4-10/11", "Content-Length": "7"},
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "changed the declared total"):
                    sut._download_with_md5(  # noqa: SLF001
                        url,
                        destination,
                        expected_md5=_md5(payload),
                        timeout_sec=30,
                    )
            self.assertFalse(destination.with_name("ToothSeg.zip.part").exists())
            self.assertFalse(sidecar_path.exists())

    def test_declared_and_streamed_oversize_are_rejected(self) -> None:
        url = "https://example.test/ToothSeg.zip"
        with tempfile.TemporaryDirectory() as tmp, patch.object(sut, "MAX_MODEL_ARCHIVE_BYTES", 8):
            destination = Path(tmp) / "ToothSeg.zip"
            with patch.object(
                sut.urllib.request,
                "urlopen",
                return_value=_Response([b"123456789"], headers={"Content-Length": "9"}),
            ):
                with self.assertRaisesRegex(RuntimeError, "safety limit"):
                    sut._download_with_md5(  # noqa: SLF001
                        url,
                        destination,
                        expected_md5=_md5(b"123456789"),
                        timeout_sec=30,
                    )

            with patch.object(sut.urllib.request, "urlopen", return_value=_Response([b"12345", b"6789"])):
                with self.assertRaisesRegex(RuntimeError, "safety limit"):
                    sut._download_with_md5(  # noqa: SLF001
                        url,
                        destination,
                        expected_md5=_md5(b"123456789"),
                        timeout_sec=30,
                    )

    def test_hash_mismatch_removes_unusable_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "ToothSeg.zip"
            with patch.object(
                sut.urllib.request,
                "urlopen",
                return_value=_Response([b"wrong"], headers={"Content-Length": "5"}),
            ):
                with self.assertRaisesRegex(RuntimeError, "md5 mismatch"):
                    sut._download_with_md5(  # noqa: SLF001
                        "https://example.test/ToothSeg.zip",
                        destination,
                        expected_md5=_md5(b"right"),
                        timeout_sec=30,
                    )
            self.assertFalse(destination.with_name("ToothSeg.zip.part").exists())
            self.assertFalse(destination.with_name("ToothSeg.zip.part.json").exists())


class ToothSegInstallHardeningTests(unittest.TestCase):
    def test_extraction_uses_declared_runtime_sizes_for_disk_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "ToothSeg.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as model:
                for _dataset_id, dataset_name, trainer in sut.BRANCHES:
                    prefix = f"ToothSeg/{dataset_name}/{trainer}"
                    model.writestr(f"{prefix}/dataset.json", b"{}")
                    model.writestr(f"{prefix}/plans.json", b"{}")
                    model.writestr(
                        f"{prefix}/fold_5/checkpoint_final.pth",
                        _fake_pytorch_checkpoint_bytes(),
                    )

            with patch.object(
                sut.shutil,
                "disk_usage",
                return_value=SimpleNamespace(free=0),
            ), self.assertRaises(OSError) as caught:
                sut._extract_runtime_files(archive, root / "staging")  # noqa: SLF001

            self.assertEqual(caught.exception.errno, errno.ENOSPC)
            self.assertFalse((root / "staging" / sut.SEMANTIC_DATASET_NAME).exists())

    def test_install_rejects_unpinned_pair_distribution_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "pinned URL and SHA-256"):
                sut.install_toothseg_model(
                    model_url="https://example.test/ToothSeg.zip",
                    model_zip=root / "ToothSeg.zip",
                    expected_md5="a" * 32,
                    nnunet_results=root / "nnUNet_results",
                    pair_distributions_url="https://example.test/not-pinned.json",
                    pair_distributions_sha256="b" * 64,
                )

    def test_second_process_lock_is_rejected_without_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_root = Path(tmp)
            with sut._exclusive_setup_lock(model_root):  # noqa: SLF001
                with self.assertRaises(sut.ToothSegSetupBusyError):
                    with sut._exclusive_setup_lock(model_root):  # noqa: SLF001
                        self.fail("second lock unexpectedly acquired")

    def test_hardlinked_setup_lock_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_root = Path(tmp)
            other = model_root / "other-file"
            other.write_bytes(b"do-not-touch")
            os.chmod(other, 0o600)
            os.link(other, model_root / ".toothseg-setup.lock")
            with self.assertRaisesRegex(RuntimeError, "hard-link count"):
                with sut._exclusive_setup_lock(model_root):  # noqa: SLF001
                    self.fail("unsafe hardlinked lock unexpectedly acquired")
            self.assertEqual(other.read_bytes(), b"do-not-touch")

    def test_group_writable_setup_lock_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_root = Path(tmp)
            lock = model_root / ".toothseg-setup.lock"
            lock.write_bytes(b"")
            os.chmod(lock, 0o660)
            with self.assertRaisesRegex(RuntimeError, "unsafe write permissions"):
                with sut._exclusive_setup_lock(model_root):  # noqa: SLF001
                    self.fail("group-writable lock unexpectedly acquired")

    def test_ready_marker_detects_truncated_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "nnUNet_results"
            _write_runtime_tree(results)
            pair = root / sut.PAIR_DISTRIBUTIONS_FILENAME
            pair.write_bytes(b"pair")
            pair_hash = hashlib.sha256(pair.read_bytes()).hexdigest()
            sut._write_ready_marker(  # noqa: SLF001
                results,
                expected_md5="archive-md5",
                pair_distributions_sha256=pair_hash,
            )
            checkpoint = (
                results
                / sut.INSTANCE_DATASET_NAME
                / sut.INSTANCE_TRAINER_DIR
                / "fold_5"
                / "checkpoint_final.pth"
            )
            checkpoint.write_bytes(b"w")
            status = sut.toothseg_model_status(
                model_root=root,
                expected_md5="archive-md5",
                expected_pair_distributions_sha256=pair_hash,
            )
            self.assertEqual(status["status"], "resumable")

    def test_legacy_marker_is_migrated_without_model_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_md5 = "a" * 32
            results = root / "nnUNet_results"
            _write_runtime_tree(results)
            pair = root / sut.PAIR_DISTRIBUTIONS_FILENAME
            pair.write_bytes(b"pair")
            pair_hash = hashlib.sha256(pair.read_bytes()).hexdigest()
            sut._write_json(  # noqa: SLF001
                results / sut.READY_MARKER_FILENAME,
                {
                    "schema": sut.MODEL_STATUS_SCHEMA,
                    "model_state": "ready",
                    "expected_md5": archive_md5,
                    "pair_distributions_sha256": pair_hash,
                    "semantic_mps_patch_size": list(sut.SEMANTIC_MPS_PATCH_SIZE),
                    "dataset_ids": [branch[0] for branch in sut.BRANCHES],
                    "dataset_names": [branch[1] for branch in sut.BRANCHES],
                },
            )
            pair_url = "https://example.test/fdi_pair_distrs.json"
            with (
                patch.object(sut, "PAIR_DISTRIBUTIONS_URL", pair_url),
                patch.object(sut, "PAIR_DISTRIBUTIONS_SHA256", pair_hash),
                patch.object(sut.urllib.request, "urlopen") as urlopen,
            ):
                result = sut.install_toothseg_model(
                    model_url="https://example.test/ToothSeg.zip",
                    model_zip=root / "ToothSeg.zip",
                    expected_md5=archive_md5,
                    nnunet_results=results,
                    pair_distributions_url=pair_url,
                    pair_distributions_sha256=pair_hash,
                )
            urlopen.assert_not_called()
            self.assertTrue(result["reused_existing_checkpoints"])
            marker = json.loads((results / sut.READY_MARKER_FILENAME).read_text(encoding="utf-8"))
            self.assertTrue(marker["legacy_marker_migrated"])
            self.assertEqual(len(marker["runtime_files"]), 6)

    def test_truncated_legacy_checkpoint_is_not_resigned_and_reacquires_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "nnUNet_results"
            _write_runtime_tree(results)
            archive_md5 = "c" * 32
            checkpoint = (
                results
                / sut.INSTANCE_DATASET_NAME
                / sut.INSTANCE_TRAINER_DIR
                / "fold_5"
                / "checkpoint_final.pth"
            )
            checkpoint.write_bytes(b"truncated-checkpoint")
            pair = root / sut.PAIR_DISTRIBUTIONS_FILENAME
            pair.write_bytes(b"pair")
            pair_hash = hashlib.sha256(pair.read_bytes()).hexdigest()
            sut._write_json(  # noqa: SLF001
                results / sut.READY_MARKER_FILENAME,
                {
                    "schema": sut.MODEL_STATUS_SCHEMA,
                    "model_state": "ready",
                    "expected_md5": archive_md5,
                    "pair_distributions_sha256": pair_hash,
                    "dataset_ids": [branch[0] for branch in sut.BRANCHES],
                    "dataset_names": [branch[1] for branch in sut.BRANCHES],
                },
            )
            pair_url = "https://example.test/fdi_pair_distrs.json"
            with (
                patch.object(sut, "PAIR_DISTRIBUTIONS_URL", pair_url),
                patch.object(sut, "PAIR_DISTRIBUTIONS_SHA256", pair_hash),
                patch.object(
                    sut,
                    "_download_with_md5",
                    side_effect=RuntimeError("archive reacquire attempted"),
                ) as download,
            ):
                with self.assertRaisesRegex(RuntimeError, "archive reacquire attempted"):
                    sut.install_toothseg_model(
                        model_url="https://example.test/ToothSeg.zip",
                        model_zip=root / "ToothSeg.zip",
                        expected_md5=archive_md5,
                        nnunet_results=results,
                        pair_distributions_url=pair_url,
                        pair_distributions_sha256=pair_hash,
                    )
            download.assert_called_once()
            marker = json.loads((results / sut.READY_MARKER_FILENAME).read_text(encoding="utf-8"))
            self.assertNotIn("runtime_files", marker)

    @unittest.skipIf(not hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_runtime_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            results = root / "nnUNet_results"
            results.mkdir()
            os.symlink(outside, results / sut.SEMANTIC_DATASET_NAME)
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                sut._recover_orphaned_install_state(results)  # noqa: SLF001

    def test_regular_file_dataset_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "nnUNet_results"
            results.mkdir()
            (results / sut.SEMANTIC_DATASET_NAME).write_bytes(b"not-a-directory")
            with self.assertRaisesRegex(RuntimeError, "not a regular directory"):
                sut._recover_orphaned_install_state(results)  # noqa: SLF001

    def test_orphan_backup_is_restored_and_staging_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "nnUNet_results"
            results.mkdir()
            staging = root / ".toothseg-staging-orphan"
            staging.mkdir()
            backup = results / f".{sut.SEMANTIC_DATASET_NAME}.previous-test"
            _write_branch(backup, sut.SEMANTIC_TRAINER_DIR, semantic=True)
            sut._recover_orphaned_install_state(results)  # noqa: SLF001
            self.assertFalse(staging.exists())
            self.assertTrue((results / sut.SEMANTIC_DATASET_NAME).is_dir())

    def test_complete_verified_staging_is_recovered_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "nnUNet_results"
            results.mkdir()
            staging = root / ".toothseg-staging-complete"
            staged_results = staging / "nnUNet_results"
            _write_runtime_tree(staged_results)
            archive_md5 = "b" * 32
            pair = staging / sut.PAIR_DISTRIBUTIONS_FILENAME
            pair.write_bytes(b"pair")
            pair_hash = hashlib.sha256(pair.read_bytes()).hexdigest()
            sut._write_json(  # noqa: SLF001
                staging / sut.STAGING_METADATA_FILENAME,
                {
                    "schema": "totalsegmentator_wrapper_mac.toothseg_staging.v1",
                    "expected_md5": archive_md5,
                    "pair_distributions_sha256": pair_hash,
                },
            )
            sut._recover_orphaned_install_state(  # noqa: SLF001
                results,
                expected_md5=archive_md5,
                expected_pair_distributions_sha256=pair_hash,
            )
            self.assertFalse(staging.exists())
            self.assertEqual(
                sut.toothseg_model_status(
                    model_root=root,
                    expected_md5=archive_md5,
                    expected_pair_distributions_sha256=pair_hash,
                )["status"],
                "ready",
            )
            marker = json.loads((results / sut.READY_MARKER_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(
                marker["integrity_manifest_source"],
                "recovered-complete-md5-verified-staging",
            )

    def test_pair_distribution_download_requires_https_and_is_atomic_and_bounded(self) -> None:
        payload = b'{"means": [], "covs": []}'
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / sut.PAIR_DISTRIBUTIONS_FILENAME
            with self.assertRaisesRegex(RuntimeError, "HTTPS"):
                sut._download_with_sha256(  # noqa: SLF001
                    "file:///tmp/pair.json",
                    destination,
                    expected_sha256=expected,
                    timeout_sec=30,
                )
            with (
                patch.object(sut, "MAX_PAIR_DISTRIBUTIONS_BYTES", 8),
                patch.object(sut.urllib.request, "urlopen", return_value=_Response([payload])),
            ):
                with self.assertRaisesRegex(RuntimeError, "safety limit"):
                    sut._download_with_sha256(  # noqa: SLF001
                        "https://example.test/pair.json",
                        destination,
                        expected_sha256=expected,
                        timeout_sec=30,
                    )
            self.assertFalse(destination.exists())
            self.assertFalse(list(destination.parent.glob(f".{destination.name}.part-*")))

    def test_pair_distribution_requests_identity_and_rejects_bad_transport(self) -> None:
        payload = b'{"means": [], "covs": []}'
        expected = hashlib.sha256(payload).hexdigest()
        requests: list[str | object] = []

        def responder(request: str | object, **_kwargs: object) -> _Response:
            requests.append(request)
            return _Response(
                [payload],
                headers={"Content-Length": str(len(payload)), "Content-Encoding": "identity"},
                url="https://raw.example.test:443/pair.json",
            )

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            sut.urllib.request,
            "urlopen",
            side_effect=responder,
        ):
            destination = Path(tmp) / sut.PAIR_DISTRIBUTIONS_FILENAME
            sut._download_with_sha256(  # noqa: SLF001
                "https://example.test/pair.json",
                destination,
                expected_sha256=expected,
                timeout_sec=30,
            )
            self.assertEqual(_request_accept_encoding(requests[0]), "identity")

        cases = (
            _Response([payload], url="https://user@example.test/pair.json"),
            _Response([payload], headers={"Content-Encoding": "br"}),
        )
        for index, response in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp, patch.object(
                sut.urllib.request,
                "urlopen",
                return_value=response,
            ):
                with self.assertRaisesRegex(RuntimeError, "standard port|Content-Encoding"):
                    sut._download_with_sha256(  # noqa: SLF001
                        "https://example.test/pair.json",
                        Path(tmp) / sut.PAIR_DISTRIBUTIONS_FILENAME,
                        expected_sha256=expected,
                        timeout_sec=30,
                    )


def _write_runtime_tree(results: Path) -> None:
    _write_branch(results / sut.SEMANTIC_DATASET_NAME, sut.SEMANTIC_TRAINER_DIR, semantic=True)
    _write_branch(results / sut.INSTANCE_DATASET_NAME, sut.INSTANCE_TRAINER_DIR, semantic=False)


def _write_branch(dataset: Path, trainer: str, *, semantic: bool) -> None:
    root = dataset / trainer
    (root / "fold_5").mkdir(parents=True)
    (root / "dataset.json").write_text("{}", encoding="utf-8")
    if semantic:
        plan = {
            "configurations": {
                "3d_fullres_resample_torch_256_bs8_ctnorm": {
                    "patch_size": list(sut.SEMANTIC_MPS_PATCH_SIZE)
                }
            }
        }
    else:
        plan = {"configurations": {}}
    (root / "plans.json").write_text(json.dumps(plan), encoding="utf-8")
    (root / "fold_5" / "checkpoint_final.pth").write_bytes(_fake_pytorch_checkpoint_bytes())


def _fake_pytorch_checkpoint_bytes() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_STORED) as checkpoint:
        checkpoint.writestr("archive/data.pkl", b"fixture-pickle-metadata")
        checkpoint.writestr("archive/version", b"3\n")
        checkpoint.writestr("archive/byteorder", b"little")
        checkpoint.writestr("archive/data/0", b"tensor-storage")
    return payload.getvalue()


if __name__ == "__main__":
    unittest.main()
