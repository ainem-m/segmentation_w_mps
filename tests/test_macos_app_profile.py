from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from totalsegmentator_wrapper_mac.device import DeviceCheck
from totalsegmentator_wrapper_mac.cli import main as cli_main
from totalsegmentator_wrapper_mac.runner_totalseg import TotalSegRunResult, run_totalsegmentator


class MacOSAppProfileTests(unittest.TestCase):
    def test_unprepared_toothseg_model_fails_before_mps_or_case_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            results = root / "models" / "nnUNet_results"
            input_path.write_bytes(b"fake")
            results.mkdir(parents=True)

            with patch(
                "totalsegmentator_wrapper_mac.runner_totalseg.resolve_device"
            ) as mps_check, patch(
                "totalsegmentator_wrapper_mac.runner_totalseg._run_command_streamed"
            ) as child_runner:
                result = run_totalsegmentator(
                    input_path=input_path,
                    output_root=root / "case",
                    task="teeth",
                    requested_device="mps",
                    backend="toothseg",
                    toothseg_nnunet_results=results,
                    execution_profile="macos-app",
                    require_mps=True,
                )

            self.assertEqual(result.error_code, "toothseg_prepare_required")
            self.assertFalse((root / "case").exists())
            mps_check.assert_not_called()
            child_runner.assert_not_called()

    def test_unprepared_dental_model_fails_before_mps_or_case_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            results = root / "models" / "nnUNet_results"
            input_path.write_bytes(b"fake")
            results.mkdir(parents=True)

            with patch(
                "totalsegmentator_wrapper_mac.runner_totalseg.resolve_device"
            ) as mps_check, patch(
                "totalsegmentator_wrapper_mac.runner_totalseg._run_command_streamed"
            ) as child_runner:
                result = run_totalsegmentator(
                    input_path=input_path,
                    output_root=root / "case",
                    task="craniofacial_structures",
                    requested_device="mps",
                    backend="dentalsegmentator",
                    dentalseg_nnunet_results=results,
                    execution_profile="macos-app",
                    require_mps=True,
                )

            self.assertEqual(result.error_code, "dentalseg_prepare_required")
            self.assertFalse((root / "case").exists())
            mps_check.assert_not_called()
            child_runner.assert_not_called()

    def test_strict_mps_failure_does_not_create_case_or_invoke_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            input_path.write_bytes(b"fake")
            failed_mps = DeviceCheck(
                status="fail",
                requested_device="mps",
                actual_device=None,
                fallback_reason=None,
                python=sys.version,
                platform="test",
                machine="arm64",
                torch_version="test",
                mps_built=True,
                mps_available=False,
                convtranspose3d_fp32="fail",
                elapsed_seconds=0.0,
                error="raw MPS failure details",
            )

            with patch(
                "totalsegmentator_wrapper_mac.runner_totalseg.resolve_device",
                return_value=failed_mps,
            ), patch(
                "totalsegmentator_wrapper_mac.runner_totalseg._run_command_streamed"
            ) as child_runner:
                result = run_totalsegmentator(
                    input_path=input_path,
                    output_root=root / "case",
                    task="craniofacial_structures",
                    requested_device="mps",
                    execution_profile="macos-app",
                    require_mps=True,
                )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error_code, "mps_unavailable")
            self.assertEqual(result.safe_reason, "MPS validation did not pass for this app run.")
            self.assertEqual(result.mps_state, "unavailable")
            self.assertIsNotNone(result.occurred_at)
            self.assertNotIn(str(root), result.safe_reason or "")
            self.assertFalse((root / "case").exists())
            child_runner.assert_not_called()

    def test_app_result_json_contains_only_redacted_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "private-ct-name.nii.gz"
            output_path = root / "private-output"
            result_json = root / "safe-result.json"
            input_path.write_bytes(b"fake")
            failed = TotalSegRunResult(
                status="failed",
                returncode=2,
                elapsed_seconds=0.0,
                requested_device="mps",
                actual_device="unknown",
                fallback_reason=None,
                task="craniofacial_structures",
                output_dir=str(output_path),
                stdout_tail=f"input={input_path}",
                stderr_tail=f"output={output_path}",
                error_code="mps_unavailable",
                safe_reason="MPS validation did not pass for this app run.",
                mps_state="unavailable",
                occurred_at=datetime.now(UTC).isoformat(),
                execution_profile="macos-app",
            )

            with patch(
                "totalsegmentator_wrapper_mac.runner_totalseg.run_totalsegmentator",
                return_value=failed,
            ), redirect_stdout(io.StringIO()):
                rc = cli_main(
                    [
                        "run",
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                        "--device",
                        "mps",
                        "--execution-profile",
                        "macos-app",
                        "--require-mps",
                        "--result-json",
                        str(result_json),
                    ]
                )

            payload = json.loads(result_json.read_text(encoding="utf-8"))
            text = json.dumps(payload)
            self.assertEqual(rc, 2)
            self.assertEqual(payload["error_code"], "mps_unavailable")
            self.assertNotIn(str(input_path), text)
            self.assertNotIn(str(output_path), text)
            self.assertNotIn("stdout_tail", payload)
            self.assertNotIn("stderr_tail", payload)


if __name__ == "__main__":
    unittest.main()
