from __future__ import annotations

import hashlib
import io
import json
import stat
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

from totalsegmentator_wrapper_mac.dentalsegmentator_setup import (
    file_md5,
    install_dentalsegmentator_model,
)


class DentalSegmentatorSetupTests(unittest.TestCase):
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


def _write_fake_installer(path: Path) -> Path:
    path.write_text(
        f"#!{sys.executable}\n"
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
