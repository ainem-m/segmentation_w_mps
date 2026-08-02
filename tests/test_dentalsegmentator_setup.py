from __future__ import annotations

import hashlib
import io
import json
import stat
import sys
import tempfile
import unittest
import zipfile
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from totalsegmentator_wrapper_mac.dentalsegmentator_setup import (
    READY_MARKER_FILENAME,
    dentalsegmentator_model_status,
    download_with_md5,
    file_md5,
    install_dentalsegmentator_model,
)


class DentalSegmentatorSetupTests(unittest.TestCase):
    def test_download_reports_actual_bytes_and_resume_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _write_file(root / "source.zip", b"progress fixture" * 1024)
            destination = root / "model.zip"
            progress_log = root / "launcher.log"
            payload = source.read_bytes()

            with _captured_output(), patch(
                "totalsegmentator_wrapper_mac.dentalsegmentator_setup.urllib.request.urlopen",
                return_value=_FakeResponse(payload, url="https://example.test/source.zip"),
            ):
                download_with_md5(
                    "https://example.test/source.zip",
                    destination,
                    expected_md5=file_md5(source),
                    timeout_sec=1,
                    progress_log=progress_log,
                )

            payloads = [
                json.loads(line.split(" ", 1)[1])
                for line in progress_log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(payloads[-1]["source"], "dentalsegmentator")
            self.assertEqual(payloads[-1]["status"], "complete")
            downloading = [item for item in payloads if item["status"] == "downloading"]
            self.assertTrue(downloading)
            self.assertEqual(downloading[-1]["completed_bytes"], source.stat().st_size)
            self.assertEqual(downloading[-1]["total_bytes"], source.stat().st_size)
            self.assertEqual(downloading[-1]["percent"], 100)
            self.assertFalse(downloading[-1]["resumed"])

    def test_downloads_verifies_and_installs_model_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_zip = _write_model_archive(
                root / "Dataset112_DentalSegmentator_v100.zip"
            )
            expected_md5 = hashlib.md5(source_zip.read_bytes()).hexdigest()  # noqa: S324
            installer = _write_fake_installer(root / "fake_nnunet_install.py")
            model_url = "https://example.test/Dataset112_DentalSegmentator_v100.zip"

            with _captured_output(), patch(
                "totalsegmentator_wrapper_mac.dentalsegmentator_setup.urllib.request.urlopen",
                return_value=_FakeResponse(source_zip.read_bytes(), url=model_url),
            ):
                result = install_dentalsegmentator_model(
                    model_url=model_url,
                    model_zip=root / "cache" / source_zip.name,
                    expected_md5=expected_md5,
                    nnunet_results=root / "models" / "nnUNet_results",
                    nnunet_raw=root / "models" / "nnUNet_raw",
                    nnunet_preprocessed=root / "models" / "nnUNet_preprocessed",
                    dataset_id="112",
                    dataset_name="Dataset112_DentalSegmentator_v100",
                    installer=installer,
                )

            self.assertEqual(result["status"], "success")
            self.assertTrue(result["downloaded"])
            self.assertTrue(result["installed"])
            self.assertEqual(result["actual_md5"], expected_md5)
            self.assertEqual(file_md5(root / "cache" / source_zip.name), expected_md5)
            dataset_json = (
                root
                / "models"
                / "nnUNet_results"
                / "Dataset112_DentalSegmentator_v100"
                / "nnUNetTrainer__nnUNetPlans__3d_fullres"
                / "dataset.json"
            )
            self.assertTrue(dataset_json.exists())
            metadata = json.loads(
                (root / "cache" / "dentalsegmentator_model.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["doi"], "10.5281/zenodo.10829675")
            self.assertEqual(metadata["license"], "CC-BY-4.0")
            self.assertEqual(
                metadata["license_url"],
                "https://creativecommons.org/licenses/by/4.0/",
            )
            self.assertEqual(metadata["creators"], ["Gauthier Dot"])
            self.assertFalse(metadata["checkpoints_modified"])

            with _captured_output():
                second = install_dentalsegmentator_model(
                    model_url=model_url,
                    model_zip=root / "cache" / source_zip.name,
                    expected_md5=expected_md5,
                    nnunet_results=root / "models" / "nnUNet_results",
                    nnunet_raw=root / "models" / "nnUNet_raw",
                    nnunet_preprocessed=root / "models" / "nnUNet_preprocessed",
                    dataset_id="112",
                    dataset_name="Dataset112_DentalSegmentator_v100",
                    installer=installer,
                )

            self.assertEqual(second["status"], "success")
            self.assertFalse(second["downloaded"])
            self.assertFalse(second["installed"])
            self.assertTrue(second["md5_verified"])
            self.assertEqual(second["skipped_reason"], "dataset_already_installed")
            self.assertTrue(
                (
                    root
                    / "models"
                    / "nnUNet_results"
                    / "Dataset112_DentalSegmentator_v100"
                    / READY_MARKER_FILENAME
                ).exists()
            )
            status = dentalsegmentator_model_status(
                model_root=root / "models",
                model_zip=root / "cache" / source_zip.name,
                nnunet_results=root / "models" / "nnUNet_results",
                expected_md5=expected_md5,
                dataset_id="112",
                dataset_name="Dataset112_DentalSegmentator_v100",
            )
            self.assertEqual(status["status"], "ready")

    def test_md5_mismatch_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_zip = root / "Dataset112_DentalSegmentator_v100.zip"
            source_zip.write_bytes(b"wrong content")
            model_url = "https://example.test/Dataset112_DentalSegmentator_v100.zip"

            with (
                self.assertRaises(RuntimeError),
                _captured_output(),
                patch(
                    "totalsegmentator_wrapper_mac.dentalsegmentator_setup.urllib.request.urlopen",
                    return_value=_FakeResponse(source_zip.read_bytes(), url=model_url),
                ),
            ):
                install_dentalsegmentator_model(
                    model_url=model_url,
                    model_zip=root / "cache" / source_zip.name,
                    expected_md5="0" * 32,
                    nnunet_results=root / "models" / "nnUNet_results",
                    nnunet_raw=root / "models" / "nnUNet_raw",
                    nnunet_preprocessed=root / "models" / "nnUNet_preprocessed",
                    dataset_id="112",
                    dataset_name="Dataset112_DentalSegmentator_v100",
                    installer=_write_fake_installer(root / "fake_nnunet_install.py"),
                )
            status = dentalsegmentator_model_status(
                model_root=root / "models",
                model_zip=root / "cache" / source_zip.name,
                nnunet_results=root / "models" / "nnUNet_results",
                expected_md5="0" * 32,
                dataset_id="112",
                dataset_name="Dataset112_DentalSegmentator_v100",
            )
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["error_code"], "model_prepare_failed")

    def test_incomplete_dataset_is_resumable_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            incomplete = root / "models" / "nnUNet_results" / "Dataset112_DentalSegmentator_v100"
            incomplete.mkdir(parents=True)

            result = dentalsegmentator_model_status(
                model_root=root / "models",
                expected_md5="a" * 32,
                dataset_id="112",
                dataset_name="Dataset112_DentalSegmentator_v100",
            )

            self.assertEqual(result["status"], "resumable")
            self.assertEqual(result["model_state"], "resumable")

    def test_verified_partial_state_takes_precedence_over_previous_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_root = root / "models"
            model_root.mkdir()
            (model_root / "Dataset112_DentalSegmentator_v100.zip.part").write_bytes(b"partial")
            (model_root / "Dataset112_DentalSegmentator_v100.zip.part.json").write_text(
                json.dumps({"url": "https://example.invalid/model.zip", "expected_md5": "a" * 32}),
                encoding="utf-8",
            )
            (model_root / "dentalsegmentator_model.json").write_text(
                json.dumps({"status": "failed"}),
                encoding="utf-8",
            )

            result = dentalsegmentator_model_status(
                model_root=model_root,
                expected_md5="a" * 32,
                dataset_id="112",
                dataset_name="Dataset112_DentalSegmentator_v100",
            )

            self.assertEqual(result["status"], "resumable")

    def test_invalid_range_response_restarts_from_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "model.zip"
            partial = destination.with_name(destination.name + ".part")
            partial.write_bytes(b"partial")
            partial.with_name(partial.name + ".json").write_text(
                json.dumps({"url": "https://example.invalid/model.zip", "expected_md5": file_md5(_write_file(root / "expected", b"complete"))}),
                encoding="utf-8",
            )
            expected_md5 = file_md5(root / "expected")
            requests: list[object] = []

            class FakeResponse:
                status = 200
                headers: dict[str, str] = {}

                def __init__(self, payload: bytes) -> None:
                    self.payload = io.BytesIO(payload)

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback) -> None:
                    self.payload.close()

                def read(self, size: int = -1) -> bytes:
                    return self.payload.read(size)

                def getcode(self) -> int:
                    return self.status

                def geturl(self) -> str:
                    return "https://example.invalid/model.zip"

            def fake_urlopen(request: object, timeout: int):
                requests.append(request)
                return FakeResponse(b"complete")

            with patch("totalsegmentator_wrapper_mac.dentalsegmentator_setup.urllib.request.urlopen", fake_urlopen):
                with _captured_output():
                    download_with_md5(
                        "https://example.invalid/model.zip",
                        destination,
                        expected_md5=expected_md5,
                        timeout_sec=1,
                    )

            self.assertEqual(destination.read_bytes(), b"complete")
            self.assertEqual(len(requests), 2)
            self.assertTrue(hasattr(requests[0], "headers"))
            self.assertIsNone(requests[1].get_header("Range"))


def _write_file(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


class _FakeResponse:
    status = 200

    def __init__(self, payload: bytes, *, url: str) -> None:
        self.payload = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.payload.close()

    def read(self, size: int = -1) -> bytes:
        return self.payload.read(size)

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url


def _write_model_archive(path: Path) -> Path:
    trainer = "Dataset112_DentalSegmentator_v100/nnUNetTrainer__nnUNetPlans__3d_fullres"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(f"{trainer}/dataset.json", json.dumps({"name": "fixture"}))
        archive.writestr(f"{trainer}/plans.json", json.dumps({"plans_name": "nnUNetPlans"}))
        archive.writestr(f"{trainer}/fold_0/checkpoint_final.pth", _fake_checkpoint())
    return path


def _fake_checkpoint() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_STORED) as checkpoint:
        checkpoint.writestr("archive/data.pkl", b"fixture-pickle-metadata")
        checkpoint.writestr("archive/version", b"3\n")
        checkpoint.writestr("archive/data/0", b"tensor-storage")
    return payload.getvalue()


def _write_fake_installer(path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import sys\n"
        "import zipfile\n"
        "from pathlib import Path\n"
        "zip_path = Path(sys.argv[1])\n"
        "root = Path(os.environ['nnUNet_results'])\n"
        "root.mkdir(parents=True, exist_ok=True)\n"
        "with zipfile.ZipFile(zip_path) as archive:\n"
        "    archive.extractall(root)\n"
        "print(f'installed {zip_path.name} into {root}')\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@contextmanager
def _captured_output():
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        yield


if __name__ == "__main__":
    unittest.main()
