from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

from totalsegmentator_wrapper_mac.dentalsegmentator_setup import (
    READY_MARKER_FILENAME,
    dentalsegmentator_model_status,
    download_with_md5,
    file_md5,
    install_dentalsegmentator_model,
    resolve_installer,
)


class DentalSegmentatorSetupTests(unittest.TestCase):
    @unittest.skipUnless(
        os.name == "nt",
        "Windows Scripts layout only",
    )
    def test_default_installer_resolves_windows_scripts_executable(
        self,
    ) -> None:
        installer = resolve_installer(None)

        self.assertEqual(installer.parent.name, "Scripts")
        self.assertEqual(installer.suffix.lower(), ".exe")

    def test_downloads_verifies_and_installs_model_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_zip = root / "Dataset112_DentalSegmentator_v100.zip"
            source_zip.write_bytes(b"fake dentalsegmentator zip")
            expected_md5 = hashlib.md5(source_zip.read_bytes()).hexdigest()  # noqa: S324
            installer = _write_fake_installer(root / "fake_nnunet_install.py")

            with _captured_output():
                result = install_dentalsegmentator_model(
                    model_url=source_zip.as_uri(),
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
                    model_url=source_zip.as_uri(),
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

            with self.assertRaises(RuntimeError), _captured_output():
                install_dentalsegmentator_model(
                    model_url=source_zip.as_uri(),
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
        from unittest.mock import patch

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
            self.assertEqual(requests[1], "https://example.invalid/model.zip")


def _write_file(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def _write_fake_installer(path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "zip_path = Path(sys.argv[1])\n"
        "root = Path(os.environ['nnUNet_results'])\n"
        "target = root / 'Dataset112_DentalSegmentator_v100' / 'nnUNetTrainer__nnUNetPlans__3d_fullres'\n"
        "target.mkdir(parents=True, exist_ok=True)\n"
        "(target / 'dataset.json').write_text(json.dumps({'name': 'Dataset112_DentalSegmentator_v100'}), encoding='utf-8')\n"
        "print(f'installed {zip_path.name} into {target}')\n",
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
