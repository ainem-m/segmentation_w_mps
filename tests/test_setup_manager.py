from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from totalsegmentator_wrapper_mac.setup_manager import (
    bundle_install_record,
    build_installed_doctor_command,
    build_setup_environment,
    build_totalseg_privacy_command,
    build_venv_command,
    build_wheel_install_command,
    default_app_support_dir,
    read_setup_state,
    run_setup,
    setup_paths,
    validate_app_support_path,
    validate_safe_command,
)


class SetupManagerTests(unittest.TestCase):
    def test_paths_are_under_app_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = setup_paths(home=home)

            self.assertEqual(
                paths.app_support,
                home / "Library" / "Application Support" / "TotalSegmentatorWrapperMac",
            )
            validate_app_support_path(paths, home=home)
            for path in paths.to_dict().values():
                self.assertTrue(Path(path).is_relative_to(paths.app_support))

    def test_rejects_non_app_support_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = setup_paths(home / "OtherApp", home=home)

            with self.assertRaises(ValueError):
                validate_app_support_path(paths, home=home)

    def test_command_builders_do_not_use_forbidden_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commands = [
                build_venv_command(root / "Python.framework" / "python3.12", root / "env"),
                build_wheel_install_command(root / "env" / "bin" / "python", root / "app.whl", allow_network=False),
                build_wheel_install_command(
                    root / "env" / "bin" / "python",
                    root / "app.whl",
                    allow_network=True,
                    constraints=root / "constraints.txt",
                ),
                build_installed_doctor_command(root / "env" / "bin" / "python", root / "doctor.json"),
                build_totalseg_privacy_command(root / "env" / "bin" / "python"),
            ]

            for command in commands:
                validate_safe_command(command)
                self.assertNotIn("sudo", command)
                self.assertNotIn("brew", command)
                self.assertIsInstance(command, list)

    def test_dry_run_setup_does_not_write_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            wheel.write_bytes(b"fake")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                dry_run=True,
                skip_mps_check=True,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "success")
            self.assertFalse(result.paths.state_json.exists())
            self.assertEqual(result.python_version, "3.12.4")
            self.assertTrue(all(step.status == "skipped" for step in result.steps[2:]))

    def test_setup_without_network_records_needs_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            wheel.write_bytes(b"fake")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                runner=_successful_runner,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "needs_network")
            self.assertTrue(result.paths.state_json.exists())
            self.assertEqual(read_setup_state(result.paths.state_json)["reason"], "needs_network")

    def test_runtime_failure_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            wheel.write_bytes(b"fake")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                runner=_failing_runner,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "runtime_install_failed")
            self.assertTrue(result.paths.state_json.exists())

    def test_wheel_missing_fails_before_runtime_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=home / "missing.whl",
                runner=_successful_runner,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "wheel_missing")

    def test_missing_normalizer_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            wheel.write_bytes(b"fake")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                runner=_successful_runner,
                normalizer_inspector=lambda: {"status": "failed", "error": "missing"},
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "normalizer_missing")

    def test_python312_missing_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            wheel.write_bytes(b"fake")

            result = run_setup(
                home=home,
                wheel=wheel,
                runner=_successful_runner,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "python312_missing")
            self.assertIsNone(result.python_executable)

    def test_python314_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            wheel.write_bytes(b"fake")

            result = run_setup(
                home=home,
                python_executable=home / "python3.14",
                wheel=wheel,
                runner=_successful_runner,
                python_inspector=_python314,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "python_version_unsupported")
            self.assertEqual(result.python_version, "3.14.4")

    def test_allow_network_requires_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            wheel.write_bytes(b"fake")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                allow_network=True,
                runner=_successful_runner,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "constraints_missing")

    def test_existing_venv_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = setup_paths(home=home)
            (paths.env_dir / "bin").mkdir(parents=True)
            (paths.env_dir / "bin" / "python").write_text("# fake", encoding="utf-8")
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            wheel.write_bytes(b"fake")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                runner=_successful_runner,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertTrue(result.venv_reused)
            create_steps = [step for step in result.steps if step.name == "create_venv"]
            self.assertEqual(create_steps[0].status, "skipped")

    def test_use_existing_env_fails_if_venv_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            wheel.write_bytes(b"fake")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                use_existing_env=True,
                runner=_successful_runner,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "runtime_install_failed")

    def test_network_install_command_uses_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = build_wheel_install_command(
                root / "env" / "bin" / "python",
                root / "app.whl",
                allow_network=True,
                constraints=root / "constraints.txt",
            )

            self.assertIn("-c", command)
            self.assertIn(str(root / "constraints.txt"), command)

    def test_progress_log_records_user_visible_setup_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            constraints = home / "constraints.txt"
            progress_log = home / "launcher.log"
            wheel.write_bytes(b"fake")
            constraints.write_text("# pinned deps\n", encoding="utf-8")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                constraints=constraints,
                allow_network=True,
                skip_mps_check=True,
                progress_log=progress_log,
                runner=_successful_runner,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "success")
            log_text = progress_log.read_text(encoding="utf-8")
            self.assertIn("SETUP_PROGRESS step=validate_python_312 status=running", log_text)
            self.assertIn("SETUP_PROGRESS step=create_venv", log_text)
            self.assertIn("SETUP_PROGRESS step=install_wheel status=running", log_text)
            self.assertIn("依存パッケージを取得中です。数分かかることがあります。", log_text)
            self.assertIn("SETUP_PROGRESS step=configure_totalseg_privacy", log_text)
            self.assertIn("SETUP_PROGRESS step=doctor", log_text)
            self.assertIn("SETUP_PROGRESS step=complete status=success", log_text)

    def test_totalseg_privacy_step_disables_usage_stats_under_app_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            constraints = home / "constraints.txt"
            wheel.write_bytes(b"fake")
            constraints.write_text("# pinned deps\n", encoding="utf-8")
            commands: list[tuple[list[str], dict[str, str] | None]] = []

            def recording_runner(command: list[str], cwd: Path | None, env: dict[str, str] | None) -> subprocess.CompletedProcess[str]:
                commands.append((command, env))
                return subprocess.CompletedProcess(command, 0, "", "")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                constraints=constraints,
                allow_network=True,
                skip_mps_check=True,
                runner=recording_runner,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "success")
            privacy_steps = [step for step in result.steps if step.name == "configure_totalseg_privacy"]
            self.assertEqual(len(privacy_steps), 1)
            command = privacy_steps[0].command
            command_text = " ".join(command)
            self.assertIn("send_usage_stats", command_text)
            self.assertIn("False", command_text)
            self.assertIn("statistics_disclaimer_shown", command_text)
            privacy_env = next(env for cmd, env in commands if cmd == command)
            assert privacy_env is not None
            self.assertTrue(privacy_env["TOTALSEG_HOME_DIR"].startswith(str(result.paths.app_support)))
            self.assertTrue(privacy_env["TOTALSEG_WEIGHTS_PATH"].startswith(str(result.paths.app_support)))

    def test_setup_records_installed_bundle_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            constraints = home / "constraints.txt"
            manifest = home / "setup_manifest.json"
            wheel.write_bytes(b"fake")
            constraints.write_text("# pinned deps\n", encoding="utf-8")
            manifest.write_text(
                """
{
  "schema": "totalsegmentator_wrapper_mac.mac_app_manifest.v1",
  "app_version": "0.1.0",
  "build_id": "test-build",
  "dependency_set_id": "deps-a",
  "wheel_sha256": "wheel-a",
  "constraints_sha256": "constraints-a",
  "normalizer_sha256": "normalizer-a",
  "dcm2niix_sha256": "dcm-a",
  "sample1_manifest_sha256": "sample-a",
  "update_manifest_url": ""
}
""",
                encoding="utf-8",
            )

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                constraints=constraints,
                bundle_manifest=manifest,
                allow_network=True,
                skip_mps_check=True,
                progress_log=home / "launcher.log",
                runner=_successful_runner,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "success")
            state = read_setup_state(result.paths.state_json)
            assert state is not None
            self.assertEqual(state["installed_bundle"]["wheel_sha256"], "wheel-a")
            self.assertEqual(state["installed_bundle"]["dcm2niix_sha256"], "dcm-a")
            self.assertEqual(state["installed_bundle"]["dependency_set_id"], "deps-a")

    def test_setup_records_invalid_bundle_manifest_as_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            manifest = home / "setup_manifest.json"
            wheel.write_bytes(b"fake")
            manifest.write_text("{not json", encoding="utf-8")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                bundle_manifest=manifest,
                allow_network=False,
                skip_mps_check=True,
                runner=_successful_runner,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "bundle_manifest_invalid")
            state = read_setup_state(result.paths.state_json)
            assert state is not None
            self.assertEqual(state["reason"], "bundle_manifest_invalid")
            self.assertEqual(state["steps"][0]["name"], "read_bundle_manifest")

    def test_bundle_install_record_uses_stable_manifest_fields(self) -> None:
        record = bundle_install_record(
            {
                "version": "0.1.0",
                "build_id": "build-a",
                "dependency_set_id": "deps-a",
                "wheel_sha256": "wheel-a",
                "constraints_sha256": "constraints-a",
                "normalizer_sha256": "normalizer-a",
                "dcm2niix_sha256": "dcm-a",
                "sample1_manifest_sha256": "sample-a",
                "update_manifest_url": "https://example.invalid/update.json",
            }
        )

        self.assertEqual(record["schema"], "totalsegmentator_wrapper_mac.installed_bundle.v1")
        self.assertEqual(record["app_version"], "0.1.0")
        self.assertEqual(record["wheel_sha256"], "wheel-a")
        self.assertEqual(record["dcm2niix_sha256"], "dcm-a")

    def test_setup_environment_stays_under_app_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = setup_paths(home=home)
            env = build_setup_environment(paths, dicom_normalizer=paths.app_support / "bin" / "normalizer")

            self.assertEqual(env["XDG_CACHE_HOME"], str(paths.cache_dir))
            self.assertEqual(env["PIP_CACHE_DIR"], str(paths.cache_dir / "pip"))
            self.assertEqual(env["PIP_DISABLE_PIP_VERSION_CHECK"], "1")
            self.assertEqual(env["PYTHONPYCACHEPREFIX"], str(paths.cache_dir / "pycache"))
            self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
            self.assertTrue(env["TOTALSEG_HOME_DIR"].startswith(str(paths.app_support)))
            self.assertTrue(env["TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER"].startswith(str(paths.app_support)))


def _successful_runner(
    command: list[str],
    cwd: Path | None,
    env: dict[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, "", "")


def _failing_runner(
    command: list[str],
    cwd: Path | None,
    env: dict[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 1, "", "fake failure")


def _normalizer_ok() -> dict[str, object]:
    return {"status": "success", "doctor": {"status": "ok"}}


def _python312(_python: Path) -> dict[str, object]:
    return {"status": "success", "version": "3.12.4", "command": [str(_python), "-c", "version"]}


def _python314(_python: Path) -> dict[str, object]:
    return {
        "status": "failed",
        "reason": "python_version_unsupported",
        "version": "3.14.4",
        "command": [str(_python), "-c", "version"],
    }


if __name__ == "__main__":
    unittest.main()
