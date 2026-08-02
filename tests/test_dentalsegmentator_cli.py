from __future__ import annotations

import errno
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from totalsegmentator_wrapper_mac.cli import main as cli_main


class DentalSegmentatorCLIContractTests(unittest.TestCase):
    def test_lazy_prepare_forwards_progress_log_to_downloader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            progress_log = root / "dentalseg-prepare.log"
            result_json = root / "dentalseg-prepare.json"
            observed: dict[str, object] = {}

            def fake_install(**kwargs: object) -> dict[str, object]:
                observed.update(kwargs)
                return {"status": "success", "model_state": "ready"}

            with (
                patch(
                    "totalsegmentator_wrapper_mac.dentalsegmentator_setup."
                    "install_dentalsegmentator_model",
                    side_effect=fake_install,
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                returncode = cli_main(
                    [
                        "dentalseg-prepare",
                        "--model-root",
                        str(root / "models"),
                        "--json",
                        str(result_json),
                        "--progress-log",
                        str(progress_log),
                    ]
                )

            self.assertEqual(returncode, 0)
            self.assertEqual(observed.get("progress_log"), progress_log)

    def test_disk_full_is_safe_and_raw_detail_stays_in_local_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            progress_log = root / "dentalseg-prepare.log"
            result_json = root / "dentalseg-prepare.json"
            private_path = "/Users/patient/private-model.zip"
            stderr = io.StringIO()
            with (
                patch(
                    "totalsegmentator_wrapper_mac.dentalsegmentator_setup."
                    "install_dentalsegmentator_model",
                    side_effect=OSError(
                        errno.ENOSPC,
                        f"No space left on device: {private_path}",
                    ),
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(stderr),
            ):
                returncode = cli_main(
                    [
                        "dentalseg-prepare",
                        "--model-root",
                        str(root / "models"),
                        "--json",
                        str(result_json),
                        "--progress-log",
                        str(progress_log),
                    ]
                )

            payload = json.loads(result_json.read_text(encoding="utf-8"))
            self.assertEqual(returncode, 1)
            self.assertEqual(payload["error_code"], "insufficient_disk_space")
            self.assertNotIn(private_path, json.dumps(payload))
            self.assertNotIn(private_path, stderr.getvalue())
            self.assertIn(private_path, progress_log.read_text(encoding="utf-8"))

    def test_non_disk_failure_keeps_generic_safe_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result_json = root / "dentalseg-prepare.json"
            with (
                patch(
                    "totalsegmentator_wrapper_mac.dentalsegmentator_setup."
                    "install_dentalsegmentator_model",
                    side_effect=RuntimeError("private diagnostic"),
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                returncode = cli_main(
                    [
                        "dentalseg-prepare",
                        "--model-root",
                        str(root / "models"),
                        "--json",
                        str(result_json),
                        "--progress-log",
                        str(root / "progress.log"),
                    ]
                )
            payload = json.loads(result_json.read_text(encoding="utf-8"))
            self.assertEqual(returncode, 1)
            self.assertEqual(payload["error_code"], "model_prepare_failed")


if __name__ == "__main__":
    unittest.main()
