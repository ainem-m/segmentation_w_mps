from __future__ import annotations

import io
import json
import stat
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from totalsegmentator_wrapper_mac.cli import (
    _safe_run_result_payload,
    main as cli_main,
)
from totalsegmentator_wrapper_mac.outputs import prepare_case_output
from totalsegmentator_wrapper_mac.runner_totalseg import (
    TotalSegRunResult,
    _write_local_engineering_diagnostic,
)


class InferenceErrorDiagnosticsTests(unittest.TestCase):
    def test_diagnostic_reference_requires_matching_result_attempt_id(self) -> None:
        """A local diagnostic UUID is public only when both IDs match exactly."""

        attempt_id = "a493c8d4-460d-4dc8-9b98-877f3d5e020b"
        args = Namespace(
            run_attempt_id=attempt_id,
            backend="totalsegmentator",
            task="craniofacial_structures",
        )
        for result_attempt_id in (None, "00000000-0000-0000-0000-000000000000"):
            result = TotalSegRunResult(
                status="failed",
                returncode=1,
                elapsed_seconds=0,
                requested_device="mps",
                actual_device="mps",
                fallback_reason=None,
                task="craniofacial_structures",
                output_dir="",
                stdout_tail="",
                stderr_tail="",
                error_code="totalseg_backend_nonzero_exit",
                run_attempt_id=result_attempt_id,
                diagnostic_log_kind="local_engineering_diagnostic",
                diagnostic_log_reference=attempt_id,
            )
            safe_result = _safe_run_result_payload(args, result)
            with self.subTest(result_attempt_id=result_attempt_id):
                self.assertEqual(safe_result["diagnostic_log_kind"], "none")
                self.assertEqual(safe_result["diagnostic_log_reference"], "none")

    def test_local_engineering_diagnostic_rejects_symlink_target(self) -> None:
        """A pre-existing symlink must never redirect raw diagnostics elsewhere."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = prepare_case_output(root / "case")
            target = root / "outside.json"
            target.write_text("do-not-overwrite", encoding="utf-8")
            diagnostic_path = case.logs_dir / "engineering_diagnostic.json"
            diagnostic_path.symlink_to(target)

            kind, reference = _write_local_engineering_diagnostic(
                case=case,
                run_attempt_id="5bba3c12-5ea7-4ddc-9dac-0a5cd9c1a746",
                failed_stage="backend_inference",
                error_code="totalseg_backend_nonzero_exit",
                specific_cause="backend_process_exited_nonzero",
                exception_type="BackendProcessExit",
                sanitized_message="TotalSegmentator exited with a nonzero status.",
                subprocess_return_code=41,
                stderr_tail="private /Users/patient/input.nii.gz",
            )

            self.assertEqual((kind, reference), ("unavailable", "unavailable"))
            self.assertTrue(diagnostic_path.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "do-not-overwrite")

    def test_local_engineering_diagnostic_uses_owner_only_atomic_artifact(self) -> None:
        """Raw diagnostic payloads must be 0600 and survive an atomic replacement."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = prepare_case_output(root / "case")
            case.logs_dir.chmod(0o755)
            diagnostic_path = case.logs_dir / "engineering_diagnostic.json"
            diagnostic_path.write_text("old", encoding="utf-8")

            kind, reference = _write_local_engineering_diagnostic(
                case=case,
                run_attempt_id="e2e45f0e-7df1-48ae-8e2d-f3747c2cbd0a",
                failed_stage="backend_inference",
                error_code="totalseg_backend_nonzero_exit",
                specific_cause="backend_process_exited_nonzero",
                exception_type="BackendProcessExit",
                sanitized_message="TotalSegmentator exited with a nonzero status.",
                subprocess_return_code=41,
                stderr_tail="private /Users/patient/input.nii.gz",
            )

            self.assertEqual(kind, "local_engineering_diagnostic")
            self.assertEqual(reference, "e2e45f0e-7df1-48ae-8e2d-f3747c2cbd0a")
            self.assertEqual(
                stat.S_IMODE(diagnostic_path.stat().st_mode),
                0o600,
            )
            self.assertEqual(stat.S_IMODE(case.logs_dir.stat().st_mode), 0o700)
            self.assertEqual(
                json.loads(diagnostic_path.read_text(encoding="utf-8"))["stderr_tail"],
                "private /Users/patient/input.nii.gz",
            )
            self.assertFalse(any(case.logs_dir.glob(".engineering_diagnostic.json.*.tmp")))

    def test_local_engineering_diagnostic_write_failure_is_not_exposed(self) -> None:
        """A failed secure replace must suppress the public diagnostic reference."""

        with tempfile.TemporaryDirectory() as tmp:
            case = prepare_case_output(Path(tmp) / "case")
            with patch(
                "totalsegmentator_wrapper_mac.runner_totalseg.os.replace",
                side_effect=OSError("simulated replace failure"),
            ):
                kind, reference = _write_local_engineering_diagnostic(
                    case=case,
                    run_attempt_id="da4dfc81-81e9-4b10-95a9-3370d716efc7",
                    failed_stage="backend_inference",
                    error_code="totalseg_backend_nonzero_exit",
                    specific_cause="backend_process_exited_nonzero",
                    exception_type="BackendProcessExit",
                    sanitized_message="TotalSegmentator exited with a nonzero status.",
                    subprocess_return_code=41,
                    stderr_tail="private /Users/patient/input.nii.gz",
                )

            self.assertEqual((kind, reference), ("unavailable", "unavailable"))
            self.assertFalse(
                (case.logs_dir / "engineering_diagnostic.json").exists()
            )
            self.assertFalse(any(case.logs_dir.glob(".engineering_diagnostic.json.*.tmp")))

    def test_primary_backend_launch_failure_keeps_command_path_local(self) -> None:
        """A launch error has the same UUID contract without leaking its command path."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "patient-private-input.nii.gz"
            output_path = root / "case"
            result_json = root / "safe-result.json"
            missing_bin = root / "private-command-not-found"
            input_path.write_bytes(b"not-a-real-nifti")
            attempt_id = "1af2c690-6598-452c-a021-fad369c309f8"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                returncode = cli_main(
                    [
                        "run",
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                        "--backend",
                        "totalsegmentator",
                        "--task",
                        "craniofacial_structures",
                        "--device",
                        "mps",
                        "--skip-device-check",
                        "--totalseg-bin",
                        str(missing_bin),
                        "--run-attempt-id",
                        attempt_id,
                        "--result-json",
                        str(result_json),
                        "--no-copy-input",
                    ]
                )

            self.assertEqual(returncode, 127)
            safe_result = json.loads(result_json.read_text(encoding="utf-8"))
            self.assertEqual(safe_result["run_attempt_id"], attempt_id)
            self.assertEqual(safe_result["error_code"], "totalseg_backend_launch_failed")
            self.assertEqual(safe_result["failed_stage"], "backend_launch")
            self.assertEqual(
                safe_result["specific_cause"], "backend_process_launch_failed"
            )
            self.assertTrue(safe_result["retryable"])
            self.assertEqual(
                safe_result["diagnostic_log_kind"], "local_engineering_diagnostic"
            )
            self.assertEqual(safe_result["diagnostic_log_reference"], attempt_id)

            diagnostic = json.loads(
                (output_path / "logs" / "engineering_diagnostic.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(diagnostic["run_attempt_id"], attempt_id)
            self.assertEqual(diagnostic["failed_stage"], "backend_launch")
            self.assertEqual(diagnostic["exception_type"], "FileNotFoundError")
            self.assertIn(str(missing_bin), diagnostic["stderr_tail"])
            safe_text = "\n".join(
                [
                    json.dumps(safe_result, ensure_ascii=False),
                    stdout.getvalue(),
                    stderr.getvalue(),
                ]
            )
            for forbidden in (str(missing_bin), input_path.name, "FileNotFoundError"):
                with self.subTest(forbidden=forbidden):
                    self.assertNotIn(forbidden, safe_text)

    def test_nonzero_primary_backend_exit_has_one_safe_attempt_contract(self) -> None:
        """A backend exit must be correlated without copying patient-like output."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "patient-private-input.nii.gz"
            output_path = root / "case"
            result_json = root / "safe-result.json"
            input_path.write_bytes(b"not-a-real-nifti")
            attempt_id = "f03f7930-d9dc-4c5a-a3ca-1d5c23a0b3d9"
            private_path = "/Users/patient/Alice Example/private.nii.gz"
            uid = "1.2.840.113619.2.55.3.604688435.781.1593520132.467"
            private_url = "https://example.invalid/private-support-ticket"
            phi = "PatientName=Alice Example"
            private_model = "Dataset115_Alice_private_model"
            fake_bin = root / "fake_totalseg_failure.py"
            raw_command = (
                f"{fake_bin} --input {input_path} --model {private_model}"
            )
            fake_bin.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                f"sys.stderr.write('Traceback (most recent call last): {private_path} {uid} {private_url} {phi} {raw_command}\\n')\n"
                "raise SystemExit(41)\n",
                encoding="utf-8",
            )
            fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IXUSR)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                returncode = cli_main(
                    [
                        "run",
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                        "--backend",
                        "totalsegmentator",
                        "--task",
                        "craniofacial_structures",
                        "--device",
                        "mps",
                        "--skip-device-check",
                        "--totalseg-bin",
                        str(fake_bin),
                        "--run-attempt-id",
                        attempt_id,
                        "--result-json",
                        str(result_json),
                        "--no-copy-input",
                    ]
                )

            self.assertEqual(returncode, 41)
            safe_result = json.loads(result_json.read_text(encoding="utf-8"))
            self.assertEqual(safe_result["run_attempt_id"], attempt_id)
            self.assertRegex(
                safe_result["occurred_at"],
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$",
            )
            self.assertEqual(safe_result["failed_stage"], "backend_inference")
            self.assertEqual(
                safe_result["specific_cause"], "backend_process_exited_nonzero"
            )
            self.assertEqual(
                safe_result["error_code"], "totalseg_backend_nonzero_exit"
            )
            self.assertTrue(safe_result["retryable"])
            self.assertEqual(
                safe_result["recovery_hint_code"], "review_local_log_then_retry"
            )
            self.assertEqual(
                safe_result["diagnostic_log_kind"], "local_engineering_diagnostic"
            )
            self.assertEqual(safe_result["diagnostic_log_reference"], attempt_id)
            self.assertEqual(safe_result["actual_device"], "mps")
            self.assertFalse(safe_result["fallback_used"])
            self.assertEqual(safe_result["input_kind"], "nifti")
            self.assertIn("input_size_bucket", safe_result)
            for field in (
                "backend_version",
                "model_version",
                "runtime_python_version",
                "runtime_torch_version",
            ):
                self.assertIn(field, safe_result)

            diagnostic = json.loads(
                (output_path / "logs" / "engineering_diagnostic.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(diagnostic["run_attempt_id"], attempt_id)
            self.assertEqual(diagnostic["failed_stage"], "backend_inference")
            self.assertEqual(diagnostic["subprocess_return_code"], 41)
            self.assertEqual(diagnostic["exception_type"], "BackendProcessExit")
            self.assertIn(private_path, diagnostic["stderr_tail"])
            self.assertIn(uid, diagnostic["stderr_tail"])
            self.assertIn(private_url, diagnostic["stderr_tail"])
            self.assertIn(phi, diagnostic["stderr_tail"])
            self.assertIn(private_model, diagnostic["stderr_tail"])
            self.assertIn(raw_command, diagnostic["stderr_tail"])
            self.assertNotIn(private_path, diagnostic["sanitized_message"])

            run_log = (output_path / "logs" / "run.log").read_text(encoding="utf-8")
            self.assertIn(f"run_attempt_id={attempt_id}", run_log)
            self.assertIn("--device mps", run_log)
            self.assertNotIn("--device cpu", run_log)

            safe_text = "\n".join(
                [
                    json.dumps(safe_result, ensure_ascii=False),
                    stdout.getvalue(),
                    stderr.getvalue(),
                ]
            )
            for forbidden in (
                private_path,
                uid,
                private_url,
                phi,
                private_model,
                raw_command,
                str(fake_bin),
                input_path.name,
                "--totalseg-bin",
                "Traceback",
            ):
                with self.subTest(forbidden=forbidden):
                    self.assertNotIn(forbidden, safe_text)

    def test_success_payload_uses_neutral_failure_fields(self) -> None:
        """A successful run must not inherit a generic failure code or hint."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            output_path = root / "case"
            result_json = root / "safe-result.json"
            input_path.write_bytes(b"not-a-real-nifti")
            attempt_id = "52cc17ed-9f9d-48d7-94bf-2449f2d49507"
            success = TotalSegRunResult(
                status="success",
                returncode=0,
                elapsed_seconds=0.01,
                requested_device="mps",
                actual_device="mps",
                fallback_reason=None,
                task="craniofacial_structures",
                output_dir=str(output_path),
                stdout_tail="untrusted stdout /Users/patient/private.nii.gz",
                stderr_tail="untrusted stderr /Users/patient/private.nii.gz",
                run_attempt_id=attempt_id,
                failed_stage="backend_inference",
                specific_cause="backend_process_exited_nonzero",
                retryable=True,
                recovery_hint_code="review_local_log_then_retry",
                diagnostic_log_kind="local_engineering_diagnostic",
                diagnostic_log_reference=attempt_id,
                backend_version="2.14.0",
                model_version="2.14.0",
                runtime_python_version="3.12",
                runtime_torch_version="2.12.0",
                input_kind="nifti",
                input_size_bucket="lt_10_mib",
            )
            stdout = io.StringIO()
            with patch(
                "totalsegmentator_wrapper_mac.runner_totalseg.run_totalsegmentator",
                return_value=success,
            ), redirect_stdout(stdout):
                returncode = cli_main(
                    [
                        "run",
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                        "--backend",
                        "totalsegmentator",
                        "--task",
                        "craniofacial_structures",
                        "--device",
                        "mps",
                        "--run-attempt-id",
                        attempt_id,
                        "--result-json",
                        str(result_json),
                    ]
                )

            self.assertEqual(returncode, 0)
            safe_result = json.loads(result_json.read_text(encoding="utf-8"))
            self.assertEqual(safe_result["status"], "success")
            for field in (
                "error_code",
                "safe_reason",
                "failed_stage",
                "specific_cause",
                "retryable",
                "recovery_hint_code",
            ):
                with self.subTest(field=field):
                    self.assertIsNone(safe_result[field])
            self.assertEqual(safe_result["diagnostic_log_kind"], "none")
            self.assertEqual(safe_result["diagnostic_log_reference"], "none")
            self.assertNotIn("/Users/patient", stdout.getvalue())

    def test_strict_preflight_uses_allowlisted_diagnostic_contract(self) -> None:
        """Preflight failures retain stable stage/cause/recovery codes only."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.nii.gz"
            output_path = root / "case"
            result_json = root / "safe-result.json"
            input_path.write_bytes(b"not-a-real-nifti")
            attempt_id = "72dbe88e-84a2-4e7c-b60f-c886ed376109"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                returncode = cli_main(
                    [
                        "run",
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                        "--backend",
                        "totalsegmentator",
                        "--task",
                        "craniofacial_structures",
                        "--device",
                        "cpu",
                        "--execution-profile",
                        "macos-app",
                        "--require-mps",
                        "--run-attempt-id",
                        attempt_id,
                        "--result-json",
                        str(result_json),
                    ]
                )

            self.assertEqual(returncode, 2)
            safe_result = json.loads(result_json.read_text(encoding="utf-8"))
            self.assertEqual(safe_result["run_attempt_id"], attempt_id)
            self.assertEqual(safe_result["error_code"], "mps_required")
            self.assertEqual(
                safe_result["failed_stage"], "preflight_execution_profile"
            )
            self.assertEqual(
                safe_result["specific_cause"], "mps_requirement_not_met"
            )
            self.assertTrue(safe_result["retryable"])
            self.assertEqual(
                safe_result["recovery_hint_code"], "select_mps_then_retry"
            )
            self.assertEqual(safe_result["diagnostic_log_kind"], "none")
            self.assertEqual(safe_result["diagnostic_log_reference"], "none")


if __name__ == "__main__":
    unittest.main()
