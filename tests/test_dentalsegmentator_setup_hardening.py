from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

import totalsegmentator_wrapper_mac.dentalsegmentator_setup as sut


class _Response:
    def __init__(
        self,
        chunks: list[bytes | BaseException],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        url: str = "https://example.test/DentalSegmentator.zip",
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


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _request_range(request: str | object) -> str | None:
    if isinstance(request, str):
        return None
    return request.get_header("Range")  # type: ignore[no-any-return, union-attr]


def _seed_partial(destination: Path, payload: bytes, *, url: str, expected_md5: str) -> None:
    partial = destination.with_name(destination.name + ".part")
    partial.write_bytes(payload)
    sut.write_model_metadata(
        partial.with_name(partial.name + ".json"),
        {
            "schema": sut.PARTIAL_DOWNLOAD_SCHEMA,
            "url": url,
            "expected_md5": expected_md5,
            "expected_sha256": None,
            "total_bytes": None,
        },
    )


class DentalDownloadHardeningTests(unittest.TestCase):
    def test_complete_partial_is_verified_and_published_without_network(self) -> None:
        payload = b"already-complete"
        url = "https://example.test/model.zip"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "model.zip"
            _seed_partial(destination, payload, url=url, expected_md5=_md5(payload))
            with patch.object(
                sut.urllib.request,
                "urlopen",
                side_effect=AssertionError("network must not be used"),
            ):
                sut.download_with_md5(
                    url,
                    destination,
                    expected_md5=_md5(payload).upper(),
                    timeout_sec=30,
                )
            self.assertEqual(destination.read_bytes(), payload)
            self.assertFalse(destination.with_name("model.zip.part").exists())
            self.assertFalse(destination.with_name("model.zip.part.json").exists())

    def test_interruption_records_total_and_next_call_resumes(self) -> None:
        payload = b"abcdefghij"
        url = "https://example.test/model.zip"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "model.zip"
            progress = Path(tmp) / "progress.log"
            with patch.object(
                sut.urllib.request,
                "urlopen",
                return_value=_Response(
                    [payload[:4], OSError("connection reset")],
                    headers={"Content-Length": str(len(payload))},
                    url=url,
                ),
            ):
                with self.assertRaisesRegex(OSError, "connection reset"):
                    sut.download_with_md5(
                        url,
                        destination,
                        expected_md5=_md5(payload),
                        timeout_sec=30,
                        progress_log=progress,
                    )
            sidecar = json.loads(destination.with_name("model.zip.part.json").read_text())
            self.assertEqual(sidecar["total_bytes"], len(payload))

            requests: list[str | object] = []

            def resume(request: str | object, **_kwargs: object) -> _Response:
                requests.append(request)
                return _Response(
                    [payload[4:]],
                    status=206,
                    headers={"Content-Range": "bytes 4-9/10", "Content-Length": "6"},
                    url=url,
                )

            with patch.object(sut.urllib.request, "urlopen", side_effect=resume):
                sut.download_with_md5(
                    url,
                    destination,
                    expected_md5=_md5(payload),
                    timeout_sec=30,
                    progress_log=progress,
                )
            self.assertEqual(_request_range(requests[0]), "bytes=4-")
            self.assertEqual(destination.read_bytes(), payload)
            events = [json.loads(line.split(" ", 1)[1]) for line in progress.read_text().splitlines()]
            self.assertTrue(any(item["resumed"] and item["resume_from_bytes"] == 4 for item in events))

    def test_short_206_continues_with_next_range(self) -> None:
        payload = b"abcdefghij"
        url = "https://example.test/model.zip"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "model.zip"
            _seed_partial(destination, payload[:4], url=url, expected_md5=_md5(payload))
            requests: list[str | object] = []
            responses = iter(
                (
                    _Response(
                        [payload[4:6]],
                        status=206,
                        headers={"Content-Range": "bytes 4-9/10", "Content-Length": "6"},
                        url=url,
                    ),
                    _Response(
                        [payload[6:]],
                        status=206,
                        headers={"Content-Range": "bytes 6-9/10", "Content-Length": "4"},
                        url=url,
                    ),
                )
            )

            def responder(request: str | object, **_kwargs: object) -> _Response:
                requests.append(request)
                return next(responses)

            with patch.object(sut.urllib.request, "urlopen", side_effect=responder):
                sut.download_with_md5(url, destination, expected_md5=_md5(payload), timeout_sec=30)
            self.assertEqual([_request_range(item) for item in requests], ["bytes=4-", "bytes=6-"])
            self.assertEqual(destination.read_bytes(), payload)

    def test_http_416_and_ignored_range_each_restart_only_once(self) -> None:
        payload = b"complete"
        url = "https://example.test/model.zip"
        for first in ("416", "ignored"):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as tmp:
                destination = Path(tmp) / "model.zip"
                _seed_partial(destination, b"stale", url=url, expected_md5=_md5(payload))
                requests: list[str | object] = []

                def responder(request: str | object, **_kwargs: object) -> _Response:
                    requests.append(request)
                    if len(requests) == 1 and first == "416":
                        raise urllib.error.HTTPError(url, 416, "range", {}, io.BytesIO())
                    return _Response(
                        [payload],
                        status=200,
                        headers={"Content-Length": str(len(payload))},
                        url=url,
                    )

                with patch.object(sut.urllib.request, "urlopen", side_effect=responder):
                    sut.download_with_md5(url, destination, expected_md5=_md5(payload), timeout_sec=30)
                self.assertEqual([_request_range(item) for item in requests], ["bytes=5-", None])
                self.assertEqual(destination.read_bytes(), payload)

    def test_sidecar_identity_mismatch_never_joins_old_bytes(self) -> None:
        payload = b"complete"
        url = "https://example.test/model.zip"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "model.zip"
            _seed_partial(destination, b"old", url=url, expected_md5="0" * 32)
            requests: list[str | object] = []

            def responder(request: str | object, **_kwargs: object) -> _Response:
                requests.append(request)
                return _Response(
                    [payload],
                    headers={"Content-Length": str(len(payload))},
                    url=url,
                )

            with patch.object(sut.urllib.request, "urlopen", side_effect=responder):
                sut.download_with_md5(url, destination, expected_md5=_md5(payload), timeout_sec=30)
            self.assertIsNone(_request_range(requests[0]))
            self.assertEqual(destination.read_bytes(), payload)

    def test_new_sidecar_requires_exact_sha256_identity(self) -> None:
        payload = b"complete"
        url = "https://example.test/model.zip"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "model.zip"
            _seed_partial(destination, b"old", url=url, expected_md5=_md5(payload))
            requests: list[str | object] = []

            def responder(request: str | object, **_kwargs: object) -> _Response:
                requests.append(request)
                return _Response(
                    [payload],
                    headers={"Content-Length": str(len(payload))},
                    url=url,
                )

            with patch.object(sut.urllib.request, "urlopen", side_effect=responder):
                sut.download_with_md5(
                    url,
                    destination,
                    expected_md5=_md5(payload),
                    expected_sha256=_sha256(payload),
                    timeout_sec=30,
                )
            self.assertIsNone(_request_range(requests[0]))
            self.assertEqual(destination.read_bytes(), payload)

    def test_declared_streamed_oversize_and_insecure_final_url_are_rejected(self) -> None:
        url = "https://example.test/model.zip"
        payload = b"123456789"
        with tempfile.TemporaryDirectory() as tmp, patch.object(sut, "MAX_MODEL_ARCHIVE_BYTES", 8):
            destination = Path(tmp) / "model.zip"
            with patch.object(
                sut.urllib.request,
                "urlopen",
                return_value=_Response([payload], headers={"Content-Length": "9"}, url=url),
            ):
                with self.assertRaisesRegex(RuntimeError, "safety limit"):
                    sut.download_with_md5(url, destination, expected_md5=_md5(payload), timeout_sec=30)
            with patch.object(
                sut.urllib.request,
                "urlopen",
                return_value=_Response([b"12345", b"6789"], url=url),
            ):
                with self.assertRaisesRegex(RuntimeError, "safety limit"):
                    sut.download_with_md5(url, destination, expected_md5=_md5(payload), timeout_sec=30)
            with patch.object(
                sut.urllib.request,
                "urlopen",
                return_value=_Response([b"ok"], headers={"Content-Length": "2"}, url="http://bad.test/x"),
            ):
                with self.assertRaisesRegex(RuntimeError, "HTTPS"):
                    sut.download_with_md5(url, destination, expected_md5=_md5(b"ok"), timeout_sec=30)
            with patch.object(
                sut.urllib.request,
                "urlopen",
                return_value=_Response(
                    [b"ok"],
                    headers={"Content-Length": "2", "Content-Encoding": "gzip"},
                    url=url,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "Content-Encoding"):
                    sut.download_with_md5(
                        url,
                        destination,
                        expected_md5=_md5(b"ok"),
                        timeout_sec=30,
                    )


class DentalInstallHardeningTests(unittest.TestCase):
    def test_setup_lock_rejects_concurrency_hardlink_and_unsafe_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with sut._exclusive_setup_lock(root):  # noqa: SLF001
                with self.assertRaises(sut.DentalSegmentatorSetupBusyError):
                    with sut._exclusive_setup_lock(root):  # noqa: SLF001
                        self.fail("second lock acquired")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            other = root / "other"
            other.write_bytes(b"safe")
            os.chmod(other, 0o600)
            os.link(other, root / ".dentalsegmentator-setup.lock")
            with self.assertRaisesRegex(RuntimeError, "hard-link"):
                with sut._exclusive_setup_lock(root):  # noqa: SLF001
                    self.fail("hardlinked lock acquired")
            self.assertEqual(other.read_bytes(), b"safe")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / ".dentalsegmentator-setup.lock"
            lock.write_bytes(b"")
            os.chmod(lock, 0o660)
            with self.assertRaisesRegex(RuntimeError, "permissions"):
                with sut._exclusive_setup_lock(root):  # noqa: SLF001
                    self.fail("unsafe-mode lock acquired")

    def test_ready_marker_detects_checkpoint_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "nnUNet_results" / sut.DEFAULT_DATASET_NAME
            _write_dataset(dataset)
            sut._write_ready_marker(  # noqa: SLF001
                dataset,
                expected_md5="a" * 32,
                dataset_id="112",
                dataset_name=sut.DEFAULT_DATASET_NAME,
            )
            _checkpoint(dataset).write_bytes(b"truncated")
            status = sut.dentalsegmentator_model_status(
                model_root=root,
                expected_md5="a" * 32,
                dataset_id="112",
                dataset_name=sut.DEFAULT_DATASET_NAME,
                nnunet_results=root / "nnUNet_results",
            )
            self.assertEqual(status["status"], "resumable")

    def test_ready_marker_detects_same_size_runtime_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "nnUNet_results" / sut.DEFAULT_DATASET_NAME
            _write_dataset(dataset)
            sut._write_ready_marker(  # noqa: SLF001
                dataset,
                expected_md5="a" * 32,
                dataset_id="112",
                dataset_name=sut.DEFAULT_DATASET_NAME,
            )
            dataset_json = dataset / sut.DEFAULT_TRAINER_DIR / "dataset.json"
            original = dataset_json.read_bytes()
            dataset_json.write_bytes(original.replace(b"Dataset", b"Changed", 1))
            self.assertEqual(dataset_json.stat().st_size, len(original))
            status = sut.dentalsegmentator_model_status(
                model_root=root,
                expected_md5="a" * 32,
                dataset_id="112",
                dataset_name=sut.DEFAULT_DATASET_NAME,
                nnunet_results=root / "nnUNet_results",
            )
            self.assertEqual(status["status"], "resumable")

    def test_legacy_marker_migrates_only_after_checkpoint_crc_and_json_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "nnUNet_results"
            dataset = results / sut.DEFAULT_DATASET_NAME
            _write_dataset(dataset)
            expected_md5 = "a" * 32
            sut.write_model_metadata(
                dataset / sut.READY_MARKER_FILENAME,
                _legacy_marker(expected_md5),
            )
            with (
                patch.object(sut, "download_with_md5") as download,
                patch.object(sut.subprocess, "run") as installer,
            ):
                result = sut.install_dentalsegmentator_model(
                    model_url="https://example.test/model.zip",
                    model_zip=root / "model.zip",
                    expected_md5=expected_md5,
                    nnunet_results=results,
                    nnunet_raw=root / "nnUNet_raw",
                    nnunet_preprocessed=root / "nnUNet_preprocessed",
                    dataset_id="112",
                    dataset_name=sut.DEFAULT_DATASET_NAME,
                )
            download.assert_not_called()
            installer.assert_not_called()
            self.assertTrue(result["reused_existing_dataset"])
            marker = json.loads((dataset / sut.READY_MARKER_FILENAME).read_text())
            self.assertTrue(marker["legacy_marker_migrated"])
            self.assertIsNone(marker["archive_sha256"])
            self.assertFalse(marker["archive_md5_verified"])
            self.assertFalse(result["md5_verified"])
            self.assertEqual(len(marker["runtime_files"]), 3)

    def test_truncated_legacy_checkpoint_forces_archive_reacquisition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "nnUNet_results"
            dataset = results / sut.DEFAULT_DATASET_NAME
            _write_dataset(dataset)
            _checkpoint(dataset).write_bytes(b"truncated")
            expected_md5 = "a" * 32
            sut.write_model_metadata(dataset / sut.READY_MARKER_FILENAME, _legacy_marker(expected_md5))
            with patch.object(
                sut,
                "download_with_md5",
                side_effect=RuntimeError("archive reacquire attempted"),
            ) as download:
                with self.assertRaisesRegex(RuntimeError, "archive reacquire attempted"):
                    sut.install_dentalsegmentator_model(
                        model_url="https://example.test/model.zip",
                        model_zip=root / "model.zip",
                        expected_md5=expected_md5,
                        nnunet_results=results,
                        nnunet_raw=root / "nnUNet_raw",
                        nnunet_preprocessed=root / "nnUNet_preprocessed",
                        dataset_id="112",
                        dataset_name=sut.DEFAULT_DATASET_NAME,
                    )
            download.assert_called_once()

    def test_recovery_restores_uuid_backup_and_rejects_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "nnUNet_results"
            results.mkdir()
            backup = results / f".{sut.DEFAULT_DATASET_NAME}.previous-test"
            _write_dataset(backup)
            sut._recover_orphaned_install_state(  # noqa: SLF001
                model_root=root,
                nnunet_results=results,
                expected_md5="a" * 32,
                dataset_id="112",
                dataset_name=sut.DEFAULT_DATASET_NAME,
            )
            self.assertTrue((results / sut.DEFAULT_DATASET_NAME).is_dir())

        if hasattr(os, "symlink"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                results = root / "nnUNet_results"
                results.mkdir()
                outside = root / "outside"
                outside.mkdir()
                os.symlink(outside, results / sut.DEFAULT_DATASET_NAME)
                with self.assertRaisesRegex(RuntimeError, "symlink"):
                    sut._recover_orphaned_install_state(  # noqa: SLF001
                        model_root=root,
                        nnunet_results=results,
                        expected_md5="a" * 32,
                        dataset_id="112",
                        dataset_name=sut.DEFAULT_DATASET_NAME,
                    )

    def test_recovery_promotes_one_complete_staging_and_rejects_ambiguous_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "nnUNet_results"
            results.mkdir()
            staging = root / ".dentalsegmentator-staging-test"
            dataset = staging / "nnUNet_results" / sut.DEFAULT_DATASET_NAME
            _write_dataset(dataset)
            sut.write_model_metadata(
                staging / sut.STAGING_METADATA_FILENAME,
                {
                    "schema": "totalsegmentator_wrapper_mac.dentalsegmentator_staging.v1",
                    "expected_md5": "a" * 32,
                    "dataset_id": "112",
                    "dataset_name": sut.DEFAULT_DATASET_NAME,
                    "archive_sha256": None,
                },
            )
            sut._write_ready_marker(  # noqa: SLF001
                dataset,
                expected_md5="a" * 32,
                dataset_id="112",
                dataset_name=sut.DEFAULT_DATASET_NAME,
            )
            sut._recover_orphaned_install_state(  # noqa: SLF001
                model_root=root,
                nnunet_results=results,
                expected_md5="a" * 32,
                dataset_id="112",
                dataset_name=sut.DEFAULT_DATASET_NAME,
            )
            destination = results / sut.DEFAULT_DATASET_NAME
            self.assertTrue(destination.is_dir())
            self.assertFalse(staging.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "nnUNet_results"
            results.mkdir()
            for suffix in ("one", "two"):
                _write_dataset(
                    results / f".{sut.DEFAULT_DATASET_NAME}.previous-{suffix}"
                )
            with self.assertRaisesRegex(RuntimeError, "Multiple equally valid"):
                sut._recover_orphaned_install_state(  # noqa: SLF001
                    model_root=root,
                    nnunet_results=results,
                    expected_md5="a" * 32,
                    dataset_id="112",
                    dataset_name=sut.DEFAULT_DATASET_NAME,
                )

    def test_archive_validation_rejects_traversal_and_extraction_bomb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            traversal = root / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as archive:
                archive.writestr("../escaped", b"x")
            with self.assertRaisesRegex(RuntimeError, "unsafe"):
                sut._validate_model_archive(traversal, dataset_name=sut.DEFAULT_DATASET_NAME)  # noqa: SLF001

            bomb = root / "bomb.zip"
            with zipfile.ZipFile(bomb, "w") as archive:
                archive.writestr("large", b"123456789")
            with patch.object(sut, "MAX_EXTRACTED_ARCHIVE_BYTES", 8):
                with self.assertRaisesRegex(RuntimeError, "extraction safety limit"):
                    sut._validate_model_archive(bomb, dataset_name=sut.DEFAULT_DATASET_NAME)  # noqa: SLF001

    def test_archive_validation_rejects_missing_structure_and_symlink_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.zip"
            with zipfile.ZipFile(missing, "w") as archive:
                archive.writestr("unrelated.txt", b"x")
            with self.assertRaisesRegex(RuntimeError, "missing the expected"):
                sut._validate_model_archive(  # noqa: SLF001
                    missing,
                    dataset_name=sut.DEFAULT_DATASET_NAME,
                )

            unsafe = root / "symlink.zip"
            member = zipfile.ZipInfo("unsafe-link")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(unsafe, "w") as archive:
                archive.writestr(member, b"destination")
            with self.assertRaisesRegex(RuntimeError, "unsafe member"):
                sut._validate_model_archive(  # noqa: SLF001
                    unsafe,
                    dataset_name=sut.DEFAULT_DATASET_NAME,
                )

    def test_locally_observed_archive_sha256_provenance_is_precise(self) -> None:
        self.assertEqual(
            sut.MODEL_ARCHIVE_SHA256,
            "bc5510cc93bc2100ab1faccb63512e09c1ca326c738b0a9939c074d82b38a4ac",
        )
        self.assertEqual(
            sut.MODEL_ARCHIVE_SHA256_PROVENANCE,
            "locally-observed official asset verified against publisher MD5",
        )
        self.assertNotIn("official digest", sut.MODEL_ARCHIVE_SHA256_PROVENANCE.lower())


def _legacy_marker(expected_md5: str) -> dict[str, object]:
    return {
        "schema": sut.MODEL_STATUS_SCHEMA,
        "model_state": "ready",
        "expected_md5": expected_md5,
        "dataset_id": "112",
        "dataset_name": sut.DEFAULT_DATASET_NAME,
    }


def _checkpoint(dataset: Path) -> Path:
    return dataset / sut.DEFAULT_TRAINER_DIR / "fold_0" / "checkpoint_final.pth"


def _write_dataset(dataset: Path) -> None:
    trainer = dataset / sut.DEFAULT_TRAINER_DIR
    (trainer / "fold_0").mkdir(parents=True)
    (trainer / "dataset.json").write_text(json.dumps({"name": sut.DEFAULT_DATASET_NAME}))
    (trainer / "plans.json").write_text(json.dumps({"plans_name": "nnUNetPlans"}))
    _checkpoint(dataset).write_bytes(_fake_pytorch_checkpoint())


def _fake_pytorch_checkpoint() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_STORED) as checkpoint:
        checkpoint.writestr("archive/data.pkl", b"fixture-pickle-metadata")
        checkpoint.writestr("archive/version", b"3\n")
        checkpoint.writestr("archive/byteorder", b"little")
        checkpoint.writestr("archive/data/0", b"tensor-storage")
    return payload.getvalue()


if __name__ == "__main__":
    unittest.main()
