from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from totalsegmentator_wrapper_mac import ios_meshsegnet_setup as setup


class _FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        fail_after: int | None = None,
        url: str | None = None,
    ) -> None:
        self.payload = payload
        self.offset = 0
        self.status = status
        self.headers = headers or {"Content-Length": str(len(payload))}
        self.fail_after = fail_after
        self.url = url or setup.MODEL_DOWNLOAD_URL

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int) -> bytes:
        if self.fail_after is not None and self.offset >= self.fail_after:
            raise ConnectionError("fixture connection interrupted")
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url


def _progress_events(path: Path) -> list[dict[str, object]]:
    prefix = "SETUP_DOWNLOAD_PROGRESS "
    return [
        json.loads(line.removeprefix(prefix))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(prefix)
    ]


class IOSMeshSegNetSetupTests(unittest.TestCase):
    def test_status_rejects_missing_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = setup.model_status(Path(tmp))
        self.assertEqual(status["model_state"], "not_installed")

    def test_prepare_verifies_hash_before_atomic_install(self) -> None:
        payload = b"licensed-checkpoint"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            setup, "EXPECTED_MODEL_SHA256", digest
        ), mock.patch.object(
            setup.urllib.request,
            "urlopen",
            return_value=_FakeResponse(payload),
        ):
            status = setup.install_model(Path(tmp))
            self.assertEqual(status["model_state"], "ready")
            self.assertEqual(status["actual_sha256"], digest)
            self.assertFalse(any(Path(tmp).glob("*.part")))
            self.assertFalse(any(Path(tmp).glob("*.part.json")))

    def test_prepare_skips_network_when_verified_model_is_already_installed(self) -> None:
        payload = b"licensed-checkpoint-already-installed"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            setup, "EXPECTED_MODEL_SHA256", digest
        ):
            root = Path(tmp)
            (root / setup.MODEL_FILENAME).write_bytes(payload)

            status = setup.install_model(
                root,
                opener=lambda *_args, **_kwargs: self.fail(
                    "verified model must not trigger a network request"
                ),
            )

        self.assertEqual(status["model_state"], "ready")

    def test_concurrent_prepare_is_rejected_by_the_model_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with setup._exclusive_model_lock(root):
                with self.assertRaises(setup.MeshSegNetModelBusyError):
                    setup.install_model(
                        root,
                        opener=lambda *_args, **_kwargs: _FakeResponse(b"unused"),
                    )

    def test_download_request_forces_identity_content_encoding(self) -> None:
        payload = b"licensed-checkpoint-identity-request"
        digest = hashlib.sha256(payload).hexdigest()
        requests: list[object] = []

        def opener(request: object, **_kwargs: object) -> _FakeResponse:
            requests.append(request)
            return _FakeResponse(payload)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            setup, "EXPECTED_MODEL_SHA256", digest
        ):
            setup.install_model(Path(tmp), opener=opener)

        self.assertEqual(requests[0].get_header("Accept-encoding"), "identity")

    def test_redirect_transport_requires_https_standard_port_and_no_credentials(self) -> None:
        cases = (
            ("http://cdn.example/model.tar", "HTTPS"),
            ("https://cdn.example:8443/model.tar", "standard HTTPS port"),
            ("https://user:secret@cdn.example/model.tar", "credentials"),
        )
        for url, message in cases:
            with self.subTest(url=url), tempfile.TemporaryDirectory() as tmp:
                response = _FakeResponse(b"unused", url=url)
                with self.assertRaisesRegex(RuntimeError, message):
                    setup.install_model(
                        Path(tmp), opener=lambda *_args, **_kwargs: response
                    )

    def test_explicit_standard_https_port_is_accepted(self) -> None:
        payload = b"licensed-checkpoint-standard-port"
        digest = hashlib.sha256(payload).hexdigest()
        response = _FakeResponse(
            payload,
            url="https://cdn.example:443/model.tar?signature=fixture",
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            setup, "EXPECTED_MODEL_SHA256", digest
        ):
            status = setup.install_model(
                Path(tmp), opener=lambda *_args, **_kwargs: response
            )
        self.assertEqual(status["model_state"], "ready")

    def test_download_rejects_non_identity_content_encoding(self) -> None:
        response = _FakeResponse(
            b"compressed",
            headers={
                "Content-Length": str(len(b"compressed")),
                "Content-Encoding": "gzip",
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "Content-Encoding"):
                setup.install_model(
                    Path(tmp), opener=lambda *_args, **_kwargs: response
                )

    def test_interrupted_download_preserves_partial_and_resumes_with_strict_range(self) -> None:
        payload = b"licensed-checkpoint-resume"
        digest = hashlib.sha256(payload).hexdigest()
        requests: list[object] = []
        responses = iter(
            [
                _FakeResponse(
                    payload,
                    headers={"Content-Length": str(len(payload))},
                    fail_after=8,
                ),
                _FakeResponse(
                    payload[8:],
                    status=206,
                    headers={
                        "Content-Length": str(len(payload) - 8),
                        "Content-Range": f"bytes 8-{len(payload) - 1}/{len(payload)}",
                    },
                ),
            ]
        )

        def opener(request: object, **_kwargs: object) -> _FakeResponse:
            requests.append(request)
            return next(responses)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            setup, "EXPECTED_MODEL_SHA256", digest
        ):
            root = Path(tmp)
            progress = root / "progress.log"
            with self.assertRaisesRegex(ConnectionError, "interrupted"):
                setup.install_model(
                    root,
                    opener=opener,
                    chunk_size=8,
                    progress_log=progress,
                )
            self.assertEqual((root / "model.tar.part").read_bytes(), payload[:8])
            self.assertTrue((root / "model.tar.part.json").is_file())

            status = setup.install_model(
                root,
                opener=opener,
                chunk_size=8,
                progress_log=progress,
            )

            self.assertEqual(status["model_state"], "ready")
            self.assertEqual((root / "model.tar").read_bytes(), payload)
            self.assertEqual(requests[1].get_header("Range"), "bytes=8-")
            resumed = [event for event in _progress_events(progress) if event.get("resumed")]
            self.assertTrue(resumed)
            self.assertTrue(all(event.get("resume_from_bytes") == 8 for event in resumed))

    def test_range_ignored_restarts_once_without_concatenating(self) -> None:
        payload = b"licensed-checkpoint-range-ignored"
        digest = hashlib.sha256(payload).hexdigest()
        requests: list[object] = []
        responses = iter(
            [
                _FakeResponse(payload, status=200),
                _FakeResponse(payload, status=200),
            ]
        )

        def opener(request: object, **_kwargs: object) -> _FakeResponse:
            requests.append(request)
            return next(responses)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            setup, "EXPECTED_MODEL_SHA256", digest
        ):
            root = Path(tmp)
            setup._write_partial_state(root / "model.tar.part.json", total_bytes=len(payload))
            (root / "model.tar.part").write_bytes(payload[:8])

            status = setup.install_model(root, opener=opener, chunk_size=8)

            self.assertEqual(status["model_state"], "ready")
            self.assertEqual((root / "model.tar").read_bytes(), payload)
            self.assertEqual(requests[0].get_header("Range"), "bytes=8-")
            self.assertIsNone(getattr(requests[1], "get_header", lambda _name: None)("Range"))

    def test_range_416_restarts_once_from_byte_zero(self) -> None:
        payload = b"licensed-checkpoint-416"
        digest = hashlib.sha256(payload).hexdigest()
        requests: list[object] = []

        def opener(request: object, **_kwargs: object) -> _FakeResponse:
            requests.append(request)
            if len(requests) == 1:
                raise urllib.error.HTTPError(
                    url="https://example.invalid/model.tar",
                    code=416,
                    msg="range not satisfiable",
                    hdrs=None,
                    fp=None,
                )
            return _FakeResponse(payload)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            setup, "EXPECTED_MODEL_SHA256", digest
        ):
            root = Path(tmp)
            setup._write_partial_state(root / "model.tar.part.json", total_bytes=len(payload))
            (root / "model.tar.part").write_bytes(payload[:8])

            status = setup.install_model(root, opener=opener, chunk_size=8)

            self.assertEqual(status["model_state"], "ready")
            self.assertEqual((root / "model.tar").read_bytes(), payload)
            self.assertEqual(requests[0].get_header("Range"), "bytes=8-")

    def test_invalid_content_range_is_rejected_without_appending(self) -> None:
        payload = b"licensed-checkpoint-invalid-range"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            setup._write_partial_state(root / "model.tar.part.json", total_bytes=len(payload))
            (root / "model.tar.part").write_bytes(payload[:8])
            response = _FakeResponse(
                payload[8:],
                status=206,
                headers={
                    "Content-Length": str(len(payload) - 8),
                    "Content-Range": f"bytes 7-{len(payload) - 1}/{len(payload)}",
                },
            )

            with self.assertRaisesRegex(RuntimeError, "Content-Range"):
                setup.install_model(root, opener=lambda *_args, **_kwargs: response)

            self.assertFalse((root / "model.tar.part").exists())
            self.assertFalse((root / "model.tar.part.json").exists())

    def test_sidecar_identity_mismatch_discards_old_partial_before_download(self) -> None:
        payload = b"licensed-checkpoint-sidecar"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            setup, "EXPECTED_MODEL_SHA256", digest
        ):
            root = Path(tmp)
            (root / "model.tar.part").write_bytes(b"stale")
            (root / "model.tar.part.json").write_text(
                json.dumps(
                    {
                        "schema": setup.PARTIAL_SCHEMA,
                        "url": "https://example.invalid/old-model.tar",
                        "sha256": digest,
                        "max_size_bytes": setup.MAX_MODEL_BYTES,
                        "total_bytes": len(payload),
                    }
                ),
                encoding="utf-8",
            )
            requests: list[object] = []

            def opener(request: object, **_kwargs: object) -> _FakeResponse:
                requests.append(request)
                return _FakeResponse(payload)

            setup.install_model(root, opener=opener)

            self.assertIsNone(getattr(requests[0], "get_header", lambda _name: None)("Range"))
            self.assertEqual((root / "model.tar").read_bytes(), payload)

    def test_prepare_removes_untrusted_complete_partial_on_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            setup.urllib.request,
            "urlopen",
            return_value=_FakeResponse(b"wrong"),
        ):
            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                setup.install_model(Path(tmp))
            self.assertFalse(any(Path(tmp).glob("*.part")))
            self.assertFalse(any(Path(tmp).glob("*.part.json")))

    def test_cli_status_json_preserves_allowlisted_integrity_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            setup.urllib.request,
            "urlopen",
            return_value=_FakeResponse(b"wrong"),
        ):
            root = Path(tmp)
            result_json = root / "status.json"
            with redirect_stdout(io.StringIO()):
                return_code = setup.main(
                    [
                        "prepare",
                        "--model-root",
                        str(root),
                        "--json",
                        str(result_json),
                    ]
                )
            payload = json.loads(result_json.read_text(encoding="utf-8"))

        self.assertEqual(return_code, 1)
        self.assertEqual(payload["error_code"], "model_integrity_failed")

    def test_download_rejects_declared_size_over_safety_cap(self) -> None:
        response = _FakeResponse(
            b"x",
            headers={"Content-Length": str(setup.MAX_MODEL_BYTES + 1)},
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "safety limit"):
                setup.install_model(
                    Path(tmp), opener=lambda *_args, **_kwargs: response
                )
            self.assertFalse(any(Path(tmp).glob("*.part")))

    def test_download_rejects_symlinked_partial_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "victim"
            victim.write_bytes(b"keep")
            (root / "model.tar.part").symlink_to(victim)

            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                setup.install_model(
                    root,
                    opener=lambda *_args, **_kwargs: _FakeResponse(b"unused"),
                )

            self.assertEqual(victim.read_bytes(), b"keep")

    def test_progress_reports_bytes_percent_rate_eta_and_source(self) -> None:
        payload = b"licensed-checkpoint-progress"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            setup, "EXPECTED_MODEL_SHA256", digest
        ):
            root = Path(tmp)
            progress = root / "progress.log"
            setup.install_model(
                root,
                opener=lambda *_args, **_kwargs: _FakeResponse(payload),
                chunk_size=4,
                progress_log=progress,
            )

            events = _progress_events(progress)
            downloading = [event for event in events if event["status"] == "downloading"]
            self.assertTrue(downloading)
            self.assertTrue(all(event["source"] == "ios-meshsegnet" for event in events))
            self.assertEqual(downloading[-1]["completed_bytes"], len(payload))
            self.assertEqual(downloading[-1]["total_bytes"], len(payload))
            self.assertEqual(downloading[-1]["percent"], 100)
            self.assertGreater(downloading[-1]["rate_bps"], 0)
            self.assertEqual(downloading[-1]["eta_seconds"], 0)
