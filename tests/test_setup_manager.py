from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from totalsegmentator_wrapper_mac.setup_manager import (
    bundle_install_record,
    build_dentalseg_weights_command,
    build_bundled_wheels_install_command,
    build_installed_doctor_command,
    build_locked_dependencies_install_command,
    build_pip_check_command,
    build_setup_environment,
    build_totalseg_privacy_command,
    build_totalseg_weights_command,
    build_venv_command,
    build_wheel_install_command,
    dentalsegmentator_model_root,
    default_app_support_dir,
    read_setup_state,
    resolve_bundled_wheels,
    run_setup,
    setup_paths,
    validate_app_support_path,
    validate_safe_command,
)
from totalsegmentator_wrapper_mac.totalseg_weights_setup import setup_weight_manifest_sha256


class SetupManagerTests(unittest.TestCase):
    def test_setup_attempt_id_is_preserved_in_result_state_and_child_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_ATTEMPT_ID": "attempt-fixture-123"},
        ):
            home = Path(tmp)
            wheel = home / "app.whl"
            constraints = home / "constraints.txt"
            wheel.write_bytes(b"fake")
            constraints.write_text("# fixture\n", encoding="utf-8")
            environments: list[dict[str, str] | None] = []

            def recording_runner(
                command: list[str], cwd: Path | None, env: dict[str, str] | None
            ) -> subprocess.CompletedProcess[str]:
                environments.append(env)
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

            self.assertEqual(result.setup_attempt_id, "attempt-fixture-123")
            state = read_setup_state(result.paths.state_json)
            assert state is not None
            self.assertEqual(state["setup_attempt_id"], "attempt-fixture-123")
            self.assertTrue(environments)
            self.assertTrue(
                all(
                    env is not None
                    and env["TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_ATTEMPT_ID"] == "attempt-fixture-123"
                    for env in environments
                )
            )

    def test_cross_process_setup_lock_returns_busy_without_running_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            app_support = default_app_support_dir(home)
            wheel = home / "app.whl"
            wheel.write_bytes(b"fake")
            source_root = Path(__file__).resolve().parents[1] / "src"
            child_code = (
                "import sys\n"
                "from pathlib import Path\n"
                "from totalsegmentator_wrapper_mac.setup_manager import exclusive_app_setup_lock\n"
                "with exclusive_app_setup_lock(Path(sys.argv[1])):\n"
                " print('locked', flush=True)\n"
                " sys.stdin.readline()\n"
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(source_root)
            child = subprocess.Popen(
                [sys.executable, "-c", child_code, str(app_support)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
            try:
                assert child.stdout is not None
                self.assertEqual(child.stdout.readline().strip(), "locked")
                commands: list[list[str]] = []

                def recording_runner(
                    command: list[str], cwd: Path | None, env: dict[str, str] | None
                ) -> subprocess.CompletedProcess[str]:
                    commands.append(command)
                    return subprocess.CompletedProcess(command, 0, "", "")

                result = run_setup(
                    home=home,
                    python_executable=home / "python3.12",
                    wheel=wheel,
                    runner=recording_runner,
                    python_inspector=_python312,
                )

                self.assertEqual(result.status, "failed")
                self.assertEqual(result.reason, "setup_busy")
                self.assertEqual(commands, [])
                self.assertFalse(result.paths.state_json.exists())
            finally:
                if child.stdin is not None:
                    child.stdin.write("release\n")
                    child.stdin.flush()
                child.wait(timeout=5)
                for stream in (child.stdin, child.stdout, child.stderr):
                    if stream is not None:
                        stream.close()

    def test_native_parent_lock_token_delegates_to_its_direct_python_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_support = Path(tmp) / "support"
            app_support.mkdir()
            lock_path = app_support / ".totalsegmentator-wrapper-setup.lock"
            token = "parent-lock-fixture"
            with lock_path.open("w+") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                json.dump(
                    {
                        "schema": "totalsegmentator_wrapper_mac.parent_setup_lock.v1",
                        "token": token,
                        "pid": os.getpid(),
                    },
                    lock,
                )
                lock.flush()
                os.fsync(lock.fileno())
                environment = os.environ.copy()
                environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
                environment["TOTALSEGMENTATOR_WRAPPER_MAC_PARENT_SETUP_LOCK_TOKEN"] = token
                environment["TOTALSEGMENTATOR_WRAPPER_MAC_PARENT_SETUP_LOCK_PID"] = str(os.getpid())
                child = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import sys\n"
                            "from pathlib import Path\n"
                            "from totalsegmentator_wrapper_mac.setup_manager import exclusive_app_setup_lock\n"
                            "with exclusive_app_setup_lock(Path(sys.argv[1])):\n"
                            " print('delegated')\n"
                        ),
                        str(app_support),
                    ],
                    capture_output=True,
                    text=True,
                    env=environment,
                    timeout=5,
                    check=False,
                )
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertEqual(child.stdout.strip(), "delegated")

    def test_setup_lock_symlink_is_rejected_without_truncating_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            app_support = default_app_support_dir(home)
            app_support.mkdir(parents=True)
            unrelated = home / "unrelated.txt"
            unrelated.write_text("preserve me", encoding="utf-8")
            (app_support / ".totalsegmentator-wrapper-setup.lock").symlink_to(unrelated)
            wheel = home / "app.whl"
            wheel.write_bytes(b"fake")
            commands: list[list[str]] = []

            def recording_runner(
                command: list[str], cwd: Path | None, env: dict[str, str] | None
            ) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                runner=recording_runner,
                python_inspector=_python312,
            )

            self.assertEqual(result.reason, "setup_lock_failed")
            self.assertEqual(commands, [])
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "preserve me")

    def test_setup_lock_hardlink_is_rejected_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            app_support = default_app_support_dir(home)
            app_support.mkdir(parents=True)
            unrelated = home / "unrelated.txt"
            unrelated.write_text("preserve me", encoding="utf-8")
            os.link(unrelated, app_support / ".totalsegmentator-wrapper-setup.lock")
            wheel = home / "app.whl"
            wheel.write_bytes(b"fake")
            commands: list[list[str]] = []

            def recording_runner(
                command: list[str], cwd: Path | None, env: dict[str, str] | None
            ) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                runner=recording_runner,
                python_inspector=_python312,
            )

            self.assertEqual(result.reason, "setup_lock_failed")
            self.assertEqual(commands, [])
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "preserve me")

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
                build_totalseg_weights_command(root / "env" / "bin" / "python"),
                build_dentalseg_weights_command(
                    root / "env" / "bin" / "python",
                    root / "models" / "dentalsegmentator",
                ),
            ]

            for command in commands:
                validate_safe_command(command)
                self.assertNotIn("sudo", command)
                self.assertNotIn("brew", command)
                self.assertIsInstance(command, list)
            # The bundled Python uses an @executable_path-relative libpython;
            # copied venv launchers cannot start because env/lib has no copy.
            self.assertNotIn("--copies", commands[0])

    def test_totalseg_weights_command_forwards_progress_log(self) -> None:
        command = build_totalseg_weights_command(
            Path("/app/env/bin/python"),
            progress_log=Path("/app/logs/launcher.log"),
        )

        self.assertIn("totalsegmentator_wrapper_mac.totalseg_weights_setup", command)
        self.assertIn("--progress-log", command)
        self.assertIn("/app/logs/launcher.log", command)
        self.assertEqual(command[-3:], ["115", "297", "113"])

    def test_dentalseg_weights_command_forwards_progress_log(self) -> None:
        command = build_dentalseg_weights_command(
            Path("/app/env/bin/python"),
            Path("/app/models/dentalsegmentator"),
            progress_log=Path("/app/logs/launcher.log"),
        )

        self.assertIn("--progress-log", command)
        self.assertIn("/app/logs/launcher.log", command)

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

    def test_wrapper_install_never_requests_a_source_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.4.1-cp312-cp312-macosx_11_0_arm64.whl"
            constraints = home / "constraints.txt"
            wheel.write_bytes(b"fake")
            constraints.write_text("fpsample==1.0.2\n", encoding="utf-8")
            pip_installs: list[list[str]] = []

            def recording_runner(
                command: list[str], cwd: Path | None, env: dict[str, str] | None
            ) -> subprocess.CompletedProcess[str]:
                if "pip" in command and "install" in command:
                    pip_installs.append(command)
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
            self.assertEqual(result.wheel_install_mode, "network_constraints_binary_only")
            self.assertTrue(pip_installs)
            self.assertTrue(all("--no-index" not in command for command in pip_installs))
            self.assertTrue(all("--only-binary" in command for command in pip_installs))
            self.assertFalse(any("--use-pep517" in command for command in pip_installs))

    def test_locked_dependency_failure_keeps_pip_detail_in_local_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fixture = _packaged_setup_fixture(home)
            _configure_fixture_hashed_wheelhouse(fixture)
            _write_fixture_manifest(fixture)
            stdout_marker = "pip stdout fixture"
            stderr_marker = "/private/diagnostics/pip-stderr-fixture"

            def failing_locked_runner(
                command: list[str], cwd: Path | None, env: dict[str, str] | None
            ) -> subprocess.CompletedProcess[str]:
                if "--require-hashes" in command:
                    return subprocess.CompletedProcess(
                        command,
                        73,
                        stdout_marker,
                        f"ERROR: local wheel verification failed at {stderr_marker}",
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=fixture["wheel"],  # type: ignore[arg-type]
                constraints=fixture["constraints"],  # type: ignore[arg-type]
                bundle_manifest=fixture["manifest"],  # type: ignore[arg-type]
                allow_network=True,
                skip_mps_check=True,
                skip_dentalseg_model=True,
                runner=failing_locked_runner,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "failed")
            locked_step = next(
                step for step in result.steps if step.name == "install_locked_dependencies"
            )
            self.assertEqual(locked_step.returncode, 73)
            self.assertEqual(
                locked_step.error,
                "Hash-locked bundled dependency installation failed.",
            )
            self.assertIsNotNone(locked_step.diagnostic_log)
            assert locked_step.diagnostic_log is not None
            diagnostic = Path(locked_step.diagnostic_log).read_text(encoding="utf-8")
            self.assertIn("phase=install_locked_dependencies", diagnostic)
            self.assertIn("returncode=73", diagnostic)
            self.assertIn(stdout_marker, diagnostic)
            self.assertIn(stderr_marker, diagnostic)
            state = read_setup_state(result.paths.state_json)
            assert state is not None
            self.assertNotIn(stderr_marker, json.dumps(state, ensure_ascii=False))

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

    def test_allow_network_permits_binary_dependencies_and_model_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            wheel.write_bytes(b"fake")
            commands: list[list[str]] = []

            def recording_runner(
                command: list[str], cwd: Path | None, env: dict[str, str] | None
            ) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                allow_network=True,
                skip_mps_check=True,
                runner=recording_runner,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "success")
            pip_installs = [
                command
                for command in commands
                if "pip" in command and "install" in command
            ]
            self.assertTrue(pip_installs)
            self.assertTrue(all("--no-index" not in command for command in pip_installs))
            self.assertTrue(any("--only-binary" in command for command in pip_installs))
            self.assertTrue(
                any("totalseg_weights_setup" in " ".join(command) for command in commands)
            )

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

    def test_wrapper_install_command_resolves_binary_dependencies_when_network_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = build_wheel_install_command(
                root / "env" / "bin" / "python",
                root / "app.whl",
                allow_network=True,
                constraints=root / "constraints.txt",
            )

            self.assertNotIn("--no-index", command)
            self.assertNotIn("--no-deps", command)
            self.assertIn("--isolated", command)
            self.assertEqual(command[:5], [
                str(root / "env" / "bin" / "python"),
                "-I",
                "-m",
                "pip",
                "--isolated",
            ])
            self.assertIn("--find-links", command)
            self.assertIn("--only-binary", command)
            self.assertEqual(
                command[-1],
                str(root / "app.whl") + "[dicom,mps,dentalseg,toothseg,ios-meshsegnet]",
            )

    def test_locked_dependency_command_requires_hashes_without_broad_root_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "requirements.lock"
            command = build_locked_dependencies_install_command(
                root / "env" / "bin" / "python",
                requirements_lock=lock,
                wheel_directory=root / "wheels",
            )
            self.assertIn("--require-hashes", command)
            self.assertNotIn("--no-index", command)
            self.assertIn("--no-deps", command)
            self.assertIn("-r", command)
            self.assertEqual(command[command.index("-r") + 1], str(lock))
            self.assertIn("--only-binary", command)
            self.assertIn("--isolated", command)
            self.assertNotIn("totalsegmentator-wrapper-mac", " ".join(command))

    def test_bundled_wheel_install_command_force_reinstalls_exact_local_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fpsample = root / "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl"
            acvl_utils = root / "acvl_utils-0.2.6-py3-none-any.whl"

            command = build_bundled_wheels_install_command(
                root / "env" / "bin" / "python",
                (fpsample, acvl_utils),
            )

            self.assertEqual(
                command,
                [
                    str(root / "env" / "bin" / "python"),
                    "-I",
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--force-reinstall",
                    "--no-deps",
                    str(fpsample),
                    str(acvl_utils),
                ],
            )

    def test_bundled_wheel_resolver_rejects_manifest_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources = Path(tmp)
            wheels = resources / "wheels"
            wheels.mkdir()
            fpsample = wheels / "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl"
            acvl_utils = wheels / "acvl_utils-0.2.6-py3-none-any.whl"
            fpsample.write_bytes(b"fpsample")
            acvl_utils.write_bytes(b"acvl-utils")
            manifest_path = resources / "setup_manifest.json"
            manifest = {
                "fpsample_wheel_sha256": hashlib.sha256(fpsample.read_bytes()).hexdigest(),
                "acvl_utils_wheel_sha256": "0" * 64,
                "bundled": {
                    "fpsample_wheel": f"wheels/{fpsample.name}",
                    "acvl_utils_wheel": f"wheels/{acvl_utils.name}",
                },
            }

            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                resolve_bundled_wheels(manifest_path, manifest)

    def test_bundled_wheel_validation_keeps_filesystem_detail_in_local_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fixture = _packaged_setup_fixture(home)
            private_path = home / "private-inputs" / "unexpected-wheel.whl"

            with patch(
                "totalsegmentator_wrapper_mac.setup_manager.resolve_bundled_wheels",
                side_effect=PermissionError(13, "Permission denied", str(private_path)),
            ):
                result, commands = _run_packaged_setup(home, fixture)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "bundled_wheel_invalid")
            validation = next(
                step for step in result.steps if step.name == "validate_bundled_wheels"
            )
            self.assertEqual(
                validation.error,
                "Bundled dependency wheel validation failed.",
            )
            self.assertIsNotNone(validation.diagnostic_log)
            assert validation.diagnostic_log is not None
            self.assertIn(
                str(private_path),
                Path(validation.diagnostic_log).read_text(encoding="utf-8"),
            )
            self.assertFalse(
                any("totalseg_weights_setup" in " ".join(command) for command in commands)
            )
            state = read_setup_state(result.paths.state_json)
            assert state is not None
            self.assertNotIn(str(private_path), json.dumps(state, ensure_ascii=False))

    def test_setup_rejects_wrapper_wheel_hash_mismatch_before_any_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fixture = _packaged_setup_fixture(home)
            fixture["payload"]["wheel_sha256"] = "0" * 64
            _write_fixture_manifest(fixture)

            result, commands = _run_packaged_setup(home, fixture)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "bundle_manifest_invalid")
            self.assertEqual(commands, [])
            self.assertIsNone(result.wheel)
            self.assertIsNone(result.constraints)

    def test_setup_rejects_constraints_hash_mismatch_before_any_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fixture = _packaged_setup_fixture(home)
            fixture["payload"]["constraints_sha256"] = "f" * 64
            _write_fixture_manifest(fixture)

            result, commands = _run_packaged_setup(home, fixture)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "bundle_manifest_invalid")
            self.assertEqual(commands, [])

    def test_release_bundle_requires_manifest_bound_hashed_lock_before_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fixture = _packaged_setup_fixture(home)
            _promote_fixture_wrapper_to_release_identity(fixture)
            fixture["payload"]["signing_mode"] = "developer-id"
            _write_fixture_manifest(fixture)

            result, commands = _run_packaged_setup(home, fixture)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "bundle_manifest_invalid")
            self.assertEqual(commands, [])

    def test_release_bundle_installs_hashed_lock_then_local_wrapper_without_deps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fixture = _packaged_setup_fixture(home)
            _promote_fixture_wrapper_to_release_identity(fixture)
            _configure_fixture_hashed_wheelhouse(fixture)
            lock = fixture["lock"]
            metadata = fixture["lock_metadata"]
            assert isinstance(lock, Path)
            assert isinstance(metadata, Path)
            lock_sha256 = hashlib.sha256(lock.read_bytes()).hexdigest()
            payload = fixture["payload"]
            assert isinstance(payload, dict)
            payload["signing_mode"] = "developer-id"
            _write_fixture_manifest(fixture)

            result, commands = _run_packaged_setup(home, fixture)

            self.assertEqual(result.status, "success")
            locked = next(command for command in commands if "--require-hashes" in command)
            self.assertEqual(locked[locked.index("-r") + 1], str(lock))
            self.assertNotIn("--no-index", locked)
            self.assertIn("--no-deps", locked)
            wrapper = next(
                command
                for command in commands
                if "--no-deps" in command
                and str(fixture["wheel"]) in command
            )
            self.assertNotIn("--require-hashes", wrapper)
            self.assertEqual(result.wheel_install_mode, "network_require_hashes_lock")
            self.assertEqual(result.requirements_lock, str(lock))
            self.assertEqual(
                result.installed_bundle["dependency_wheelhouse_manifest_sha256"],
                payload["dependency_wheelhouse_manifest_sha256"],
            )
            bundled = next(
                command for command in commands if "--force-reinstall" in command
            )
            dependency_wheel = fixture["dependency_wheel"]
            assert isinstance(dependency_wheel, Path)
            self.assertIn(str(dependency_wheel.resolve()), bundled)

            for field, invalid_value in (
                ("schema", "wrong-schema"),
                ("constraints_sha256", "0" * 64),
                ("project_file_sha256", "0" * 64),
                ("root_install_requirement", "totalsegmentator-wrapper-mac"),
                (
                    "resolved_distribution_names",
                    ["acvl-utils", "fpsample", "open3d", "open3d"],
                ),
                (
                    "resolver",
                    {
                        "name": "pip-compile",
                        "version": "7.5.0",
                        "platform": "macos-14-arm64",
                        "python": "3.12",
                        "pip_version": "25.1.1",
                        "python_full_version": "3.12.11",
                        "macos_version": "26.6",
                        "sysconfig_platform": "macosx-11.0-arm64",
                        "target_compatibility": {
                            "platform": "macosx_13_0_arm64",
                            "python_version": "3.12",
                            "implementation": "cp",
                            "abi": "cp312",
                            "selection": (
                                "pip-cross-target-options-and-wheelhouse-tag-audit-v1"
                            ),
                        },
                    },
                ),
            ):
                with self.subTest(metadata_field=field):
                    mutated = json.loads(metadata.read_text(encoding="utf-8"))
                    mutated[field] = invalid_value
                    metadata.write_text(json.dumps(mutated), encoding="utf-8")
                    payload["dependency_lock_metadata_sha256"] = hashlib.sha256(
                        metadata.read_bytes()
                    ).hexdigest()
                    _write_fixture_manifest(fixture)
                    rejected, rejected_commands = _run_packaged_setup(home, fixture)

                    self.assertEqual(rejected.status, "failed")
                    self.assertEqual(rejected.reason, "bundle_manifest_invalid")
                    self.assertEqual(rejected_commands, [])

                    metadata.write_text(
                        json.dumps(
                            _release_lock_metadata(
                                constraints=fixture["constraints"],
                                project_file=fixture["project_file"],
                                requirements_lock=lock,
                                requirements_lock_sha256=lock_sha256,
                            )
                        ),
                        encoding="utf-8",
                    )
                    payload["dependency_lock_metadata_sha256"] = hashlib.sha256(
                        metadata.read_bytes()
                    ).hexdigest()
                    _write_fixture_manifest(fixture)

            project_file = fixture["project_file"]
            assert isinstance(project_file, Path)
            original_project = project_file.read_text(encoding="utf-8")
            project_file.write_text(
                original_project + "dependencies = ['unlocked-new-dependency>=1']\n",
                encoding="utf-8",
            )
            rejected, rejected_commands = _run_packaged_setup(home, fixture)
            self.assertEqual(rejected.status, "failed")
            self.assertEqual(rejected.reason, "bundle_manifest_invalid")
            self.assertEqual(rejected_commands, [])
            project_file.write_text(original_project, encoding="utf-8")

            lock.write_text(
                "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
                "2b03e2ef-8d40-4a02-9ad3-0d2d8f6bd0d3\n"
                "numpy==2.3.3 --hash=sha256:" + "c" * 64 + "\n"
                "unexpected==0.1.0 --hash=sha256:" + "d" * 64 + "\n",
                encoding="utf-8",
            )
            lock_sha256 = hashlib.sha256(lock.read_bytes()).hexdigest()
            mismatched_metadata = json.loads(metadata.read_text(encoding="utf-8"))
            mismatched_metadata["requirements_lock_sha256"] = lock_sha256
            metadata.write_text(json.dumps(mismatched_metadata), encoding="utf-8")
            payload["requirements_lock_sha256"] = lock_sha256
            payload["dependency_lock_metadata_sha256"] = hashlib.sha256(
                metadata.read_bytes()
            ).hexdigest()
            _write_fixture_manifest(fixture)

            rejected, rejected_commands = _run_packaged_setup(home, fixture)

            self.assertEqual(rejected.status, "failed")
            self.assertEqual(rejected.reason, "bundle_manifest_invalid")
            self.assertEqual(rejected_commands, [])

    def test_release_bundle_rejects_tampered_dependency_wheelhouse_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fixture = _packaged_setup_fixture(home)
            _promote_fixture_wrapper_to_release_identity(fixture)
            _configure_fixture_hashed_wheelhouse(fixture)
            payload = fixture["payload"]
            wheelhouse_manifest = fixture["wheelhouse_manifest"]
            assert isinstance(payload, dict)
            assert isinstance(wheelhouse_manifest, Path)
            payload["signing_mode"] = "developer-id"
            _write_fixture_manifest(fixture)
            wheelhouse_manifest.write_text('{"schema":"tampered"}\n', encoding="utf-8")

            result, commands = _run_packaged_setup(home, fixture)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "bundle_manifest_invalid")
            self.assertEqual(commands, [])

    def test_packaged_setup_rejects_incomplete_locked_wheelhouse_before_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fixture = _packaged_setup_fixture(home)
            _configure_fixture_hashed_wheelhouse(fixture)
            dependency_wheel = fixture["dependency_wheel"]
            assert isinstance(dependency_wheel, Path)
            dependency_wheel.unlink()
            _write_fixture_manifest(fixture)

            result, commands = _run_packaged_setup(home, fixture)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "bundled_wheel_invalid")
            self.assertEqual(commands, [])
            validation = next(
                step for step in result.steps if step.name == "validate_bundled_wheels"
            )
            self.assertIsNotNone(validation.diagnostic_log)
            assert validation.diagnostic_log is not None
            self.assertIn(
                "bundle_wheelhouse_incomplete",
                Path(validation.diagnostic_log).read_text(encoding="utf-8"),
            )

    def test_locked_wheelhouse_installs_before_model_network_is_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fixture = _packaged_setup_fixture(home)
            _configure_fixture_hashed_wheelhouse(fixture)
            _write_fixture_manifest(fixture)
            commands: list[list[str]] = []

            def recording_runner(
                command: list[str], cwd: Path | None, env: dict[str, str] | None
            ) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=fixture["wheel"],  # type: ignore[arg-type]
                constraints=fixture["constraints"],  # type: ignore[arg-type]
                bundle_manifest=fixture["manifest"],  # type: ignore[arg-type]
                allow_network=False,
                skip_mps_check=True,
                skip_dentalseg_model=True,
                runner=recording_runner,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "needs_network")
            self.assertFalse(any("--require-hashes" in command for command in commands))
            self.assertEqual(result.wheel_install_mode, "no_deps")
            self.assertFalse(
                any("totalseg_weights_setup" in " ".join(command) for command in commands)
            )

    def test_setup_rejects_extra_wrapper_wheel_before_any_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fixture = _packaged_setup_fixture(home)
            extra_wheel = fixture["wheels"] / "totalsegmentator_wrapper_mac-0.4.0-cp312-cp312-macosx_11_0_arm64.whl"
            extra_wheel.write_bytes(b"unexpected wrapper wheel")

            result, commands = _run_packaged_setup(home, fixture)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "bundle_manifest_invalid")
            self.assertEqual(commands, [])

    def test_setup_rejects_symlink_wrapper_wheel_without_leaking_target_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fixture = _packaged_setup_fixture(home)
            outside = home / "private-inputs" / "untrusted-wrapper.whl"
            outside.parent.mkdir()
            outside.write_bytes(fixture["wheel"].read_bytes())
            fixture["wheel"].unlink()
            fixture["wheel"].symlink_to(outside)

            result, commands = _run_packaged_setup(home, fixture)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "bundle_manifest_invalid")
            self.assertEqual(commands, [])
            state = read_setup_state(result.paths.state_json)
            assert state is not None
            self.assertNotIn(str(outside), json.dumps(state, ensure_ascii=False))

    def test_setup_rejects_unsafe_manifest_wrapper_relative_path_before_any_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fixture = _packaged_setup_fixture(home)
            fixture["payload"]["bundled"]["wheel"] = "../untrusted-wrapper.whl"
            _write_fixture_manifest(fixture)

            result, commands = _run_packaged_setup(home, fixture)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "bundle_manifest_invalid")
            self.assertEqual(commands, [])

    def test_pip_check_failure_stops_before_model_download_and_keeps_stderr_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fixture = _packaged_setup_fixture(home)
            _configure_fixture_hashed_wheelhouse(fixture)
            _write_fixture_manifest(fixture)
            commands: list[list[str]] = []
            stderr_marker = "/private/diagnostics/untrusted-pip-output"

            def failing_pip_check_runner(
                command: list[str], cwd: Path | None, env: dict[str, str] | None
            ) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                if command[-1:] == ["check"] and "pip" in command:
                    return subprocess.CompletedProcess(
                        command,
                        1,
                        "",
                        f"ERROR: broken dependency at {stderr_marker}",
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=fixture["wheel"],  # type: ignore[arg-type]
                constraints=fixture["constraints"],  # type: ignore[arg-type]
                bundle_manifest=fixture["manifest"],  # type: ignore[arg-type]
                allow_network=True,
                skip_mps_check=True,
                skip_dentalseg_model=True,
                runner=failing_pip_check_runner,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "dependency_consistency_failed")
            check_step = next(step for step in result.steps if step.name == "verify_dependencies")
            self.assertEqual(check_step.status, "failed")
            self.assertNotIn(stderr_marker, check_step.error or "")
            self.assertIsNotNone(check_step.diagnostic_log)
            assert check_step.diagnostic_log is not None
            diagnostic = Path(check_step.diagnostic_log).read_text(encoding="utf-8")
            self.assertIn("phase=verify_dependencies", diagnostic)
            self.assertIn("returncode=1", diagnostic)
            self.assertIn(stderr_marker, diagnostic)
            wrapper_install_index = next(
                index for index, command in enumerate(commands) if "--only-binary" in command
            )
            bundled_install_index = next(
                index for index, command in enumerate(commands) if "--force-reinstall" in command
            )
            pip_check_index = next(
                index
                for index, command in enumerate(commands)
                if command[-1:] == ["check"] and "pip" in command
            )
            self.assertGreater(pip_check_index, wrapper_install_index)
            self.assertGreater(pip_check_index, bundled_install_index)
            self.assertFalse(
                any("totalseg_weights_setup" in " ".join(command) for command in commands)
            )
            state = read_setup_state(result.paths.state_json)
            assert state is not None
            self.assertNotIn(stderr_marker, json.dumps(state, ensure_ascii=False))

    def test_setup_force_reinstalls_manifest_bundled_wheels_in_reused_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            resources = home / "Resources"
            wheels = resources / "wheels"
            wheels.mkdir(parents=True)
            wheel = wheels / "totalsegmentator_wrapper_mac-0.4.1-cp312-cp312-macosx_11_0_arm64.whl"
            fpsample = wheels / "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl"
            acvl_utils = wheels / "acvl_utils-0.2.6-py3-none-any.whl"
            constraints = resources / "constraints.txt"
            manifest = resources / "setup_manifest.json"
            wheel.write_bytes(b"wrapper")
            fpsample.write_bytes(b"fpsample bundled wheel")
            acvl_utils.write_bytes(b"acvl bundled wheel")
            constraints.write_text("acvl-utils==0.2.6\nfpsample==1.0.2\n", encoding="utf-8")
            manifest.write_text(
                json.dumps(
                    {
                        "schema": "totalsegmentator_wrapper_mac.mac_app_manifest.v1",
                        "app_version": "0.4.1",
                        "build_id": "test-build",
                        "dependency_set_id": "deps-bundled-wheels",
                        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                        "fpsample_wheel_sha256": hashlib.sha256(fpsample.read_bytes()).hexdigest(),
                        "acvl_utils_wheel_sha256": hashlib.sha256(acvl_utils.read_bytes()).hexdigest(),
                        "constraints_sha256": hashlib.sha256(constraints.read_bytes()).hexdigest(),
                        "normalizer_sha256": "normalizer-a",
                        "dcm2niix_sha256": "dcm-a",
                        "sample1_manifest_sha256": "sample-a",
                        "setup_weights_manifest_sha256": setup_weight_manifest_sha256(),
                        "update_manifest_url": "",
                        "bundled": {
                            "wheel": wheel.name,
                            "fpsample_wheel": f"wheels/{fpsample.name}",
                            "acvl_utils_wheel": f"wheels/{acvl_utils.name}",
                            "constraints": constraints.name,
                        },
                    }
                ),
                encoding="utf-8",
            )
            paths = setup_paths(home=home)
            (paths.env_dir / "bin").mkdir(parents=True)
            (paths.env_dir / "bin" / "python").write_text("existing Python 3.12", encoding="utf-8")
            installed_site = paths.env_dir / "lib" / "python3.12" / "site-packages"
            (installed_site / "acvl_utils-0.2.6.dist-info").mkdir(parents=True)
            (installed_site / "fpsample-1.0.2.dist-info").mkdir(parents=True)
            commands: list[list[str]] = []

            def recording_runner(
                command: list[str], cwd: Path | None, env: dict[str, str] | None
            ) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                constraints=constraints,
                bundle_manifest=manifest,
                allow_network=True,
                skip_mps_check=True,
                skip_dentalseg_model=True,
                runner=recording_runner,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "success")
            self.assertTrue(result.venv_reused)
            bundled_step = next(step for step in result.steps if step.name == "install_bundled_wheels")
            self.assertEqual(bundled_step.status, "success")
            self.assertIn("--force-reinstall", bundled_step.command)
            self.assertIn("--no-deps", bundled_step.command)
            self.assertNotIn("--no-index", bundled_step.command)
            self.assertIn(str(fpsample.resolve()), bundled_step.command)
            self.assertIn(str(acvl_utils.resolve()), bundled_step.command)
            wrapper_step = next(step for step in result.steps if step.name == "install_wheel")
            self.assertLess(
                commands.index(bundled_step.command),
                commands.index(wrapper_step.command),
            )

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
            self.assertIn("同梱アプリ本体の導入が完了しました。", log_text)
            self.assertIn("SETUP_PROGRESS step=configure_totalseg_privacy", log_text)
            self.assertIn("SETUP_PROGRESS step=download_totalseg_weights", log_text)
            self.assertIn("初回実行に必要なモデルを取得しています。数分かかることがあります。", log_text)
            self.assertIn("SETUP_PROGRESS step=download_dentalseg_weights", log_text)
            self.assertIn("DentalSegmentatorモデルを取得しています。数分かかることがあります。", log_text)
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
            self.assertTrue(privacy_env["nnUNet_results"].startswith(str(result.paths.app_support)))

    def test_setup_preloads_craniofacial_robust_crop_and_teeth_weights(self) -> None:
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
            step_names = [step.name for step in result.steps]
            self.assertLess(step_names.index("configure_totalseg_privacy"), step_names.index("download_totalseg_weights"))
            self.assertLess(step_names.index("download_totalseg_weights"), step_names.index("download_dentalseg_weights"))
            self.assertLess(step_names.index("download_dentalseg_weights"), step_names.index("doctor"))
            weights_step = next(step for step in result.steps if step.name == "download_totalseg_weights")
            command_text = " ".join(weights_step.command)
            self.assertIn("totalsegmentator_wrapper_mac.totalseg_weights_setup", command_text)
            self.assertNotIn("download_pretrained_weights", command_text)
            self.assertEqual(weights_step.command[-3:], ["115", "297", "113"])
            weights_env = next(env for cmd, env in commands if cmd == weights_step.command)
            assert weights_env is not None
            self.assertTrue(weights_env["TOTALSEG_WEIGHTS_PATH"].startswith(str(result.paths.app_support)))

            dentalseg_step = next(step for step in result.steps if step.name == "download_dentalseg_weights")
            dentalseg_command_text = " ".join(dentalseg_step.command)
            self.assertIn("totalsegmentator_wrapper_mac.dentalsegmentator_setup", dentalseg_command_text)
            self.assertIn("Dataset112_DentalSegmentator_v100.zip", dentalseg_command_text)
            self.assertIn("b71cd5230168d28a4f71b078265b76be", dentalseg_command_text)
            self.assertIn("Dataset112_DentalSegmentator", dentalseg_command_text)
            dentalseg_env = next(env for cmd, env in commands if cmd == dentalseg_step.command)
            assert dentalseg_env is not None
            self.assertTrue(dentalseg_env["nnUNet_raw"].startswith(str(result.paths.app_support)))
            self.assertTrue(dentalseg_env["nnUNet_preprocessed"].startswith(str(result.paths.app_support)))
            self.assertTrue(dentalseg_env["nnUNet_results"].startswith(str(result.paths.app_support)))
            self.assertIn(str(dentalsegmentator_model_root(result.paths)), dentalseg_env["nnUNet_results"])

    def test_dentalseg_weight_failure_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            constraints = home / "constraints.txt"
            wheel.write_bytes(b"fake")
            constraints.write_text("# pinned deps\n", encoding="utf-8")

            def failing_dentalseg_runner(command: list[str], cwd: Path | None, env: dict[str, str] | None) -> subprocess.CompletedProcess[str]:
                command_text = " ".join(command)
                if "dentalsegmentator_setup" in command_text:
                    return subprocess.CompletedProcess(command, 1, "", "fake dentalseg failure")
                return subprocess.CompletedProcess(command, 0, "", "")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                constraints=constraints,
                allow_network=True,
                skip_mps_check=True,
                runner=failing_dentalseg_runner,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "dentalseg_weights_download_failed")
            dentalseg_step = next(step for step in result.steps if step.name == "download_dentalseg_weights")
            self.assertEqual(dentalseg_step.status, "failed")
            self.assertIn("fake dentalseg failure", dentalseg_step.error or "")

    def test_totalseg_integrity_failure_has_specific_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            constraints = home / "constraints.txt"
            wheel.write_bytes(b"fake")
            constraints.write_text("# pinned deps\n", encoding="utf-8")

            def failing_weights_runner(
                command: list[str], cwd: Path | None, env: dict[str, str] | None
            ) -> subprocess.CompletedProcess[str]:
                if "totalseg_weights_setup" in " ".join(command):
                    return subprocess.CompletedProcess(
                        command, 1, "", "TotalSegmentator asset SHA-256 mismatch for task 115"
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                constraints=constraints,
                allow_network=True,
                skip_mps_check=True,
                runner=failing_weights_runner,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.reason, "weights_integrity_failed")

    def test_skip_dentalseg_model_defers_only_dental_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wheel = home / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            constraints = home / "constraints.txt"
            wheel.write_bytes(b"fake")
            constraints.write_text("# pinned deps\n", encoding="utf-8")
            commands: list[list[str]] = []

            def recording_runner(
                command: list[str], cwd: Path | None, env: dict[str, str] | None
            ) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            result = run_setup(
                home=home,
                python_executable=home / "python3.12",
                wheel=wheel,
                constraints=constraints,
                allow_network=True,
                skip_mps_check=True,
                skip_dentalseg_model=True,
                runner=recording_runner,
                normalizer_inspector=_normalizer_ok,
                python_inspector=_python312,
            )

            self.assertEqual(result.status, "success")
            step = next(item for item in result.steps if item.name == "download_dentalseg_weights")
            self.assertEqual(step.status, "skipped")
            self.assertFalse(any("dentalsegmentator_setup" in " ".join(command) for command in commands))

    def test_setup_records_installed_bundle_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            resources = home / "Resources"
            wheels = resources / "wheels"
            wheels.mkdir(parents=True)
            wheel = wheels / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            fpsample = wheels / "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl"
            acvl_utils = wheels / "acvl_utils-0.2.6-py3-none-any.whl"
            constraints = resources / "constraints.txt"
            manifest = resources / "setup_manifest.json"
            wheel.write_bytes(b"fake")
            fpsample.write_bytes(b"fpsample")
            acvl_utils.write_bytes(b"acvl-utils")
            constraints.write_text("# pinned deps\n", encoding="utf-8")
            manifest.write_text(
                json.dumps(
                    {
                        "schema": "totalsegmentator_wrapper_mac.mac_app_manifest.v1",
                        "app_version": "0.1.0",
                        "build_id": "test-build",
                        "dependency_set_id": "deps-a",
                        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                        "fpsample_wheel_sha256": hashlib.sha256(fpsample.read_bytes()).hexdigest(),
                        "acvl_utils_wheel_sha256": hashlib.sha256(acvl_utils.read_bytes()).hexdigest(),
                        "constraints_sha256": hashlib.sha256(constraints.read_bytes()).hexdigest(),
                        "normalizer_sha256": "normalizer-a",
                        "dcm2niix_sha256": "dcm-a",
                        "sample1_manifest_sha256": "sample-a",
                        "setup_weights_manifest_sha256": setup_weight_manifest_sha256(),
                        "python_runtime": {"fingerprint": "runtime-fixture-v1"},
                        "update_manifest_url": "",
                        "bundled": {
                            "wheel": wheel.name,
                            "fpsample_wheel": f"wheels/{fpsample.name}",
                            "acvl_utils_wheel": f"wheels/{acvl_utils.name}",
                            "constraints": constraints.name,
                        },
                    }
                ),
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
            self.assertEqual(
                state["installed_bundle"]["wheel_sha256"],
                hashlib.sha256(wheel.read_bytes()).hexdigest(),
            )
            self.assertEqual(state["installed_bundle"]["dcm2niix_sha256"], "dcm-a")
            self.assertEqual(state["installed_bundle"]["dependency_set_id"], "deps-a")
            self.assertEqual(
                state["installed_bundle"]["python_runtime_fingerprint"],
                "runtime-fixture-v1",
            )

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
                "fpsample_wheel_sha256": "fpsample-a",
                "acvl_utils_wheel_sha256": "acvl-a",
                "constraints_sha256": "constraints-a",
                "normalizer_sha256": "normalizer-a",
                "dcm2niix_sha256": "dcm-a",
                "sample1_manifest_sha256": "sample-a",
                "setup_weights_manifest_sha256": "weights-a",
                "update_manifest_url": "https://example.invalid/update.json",
            }
        )

        self.assertEqual(record["schema"], "totalsegmentator_wrapper_mac.installed_bundle.v1")
        self.assertEqual(record["app_version"], "0.1.0")
        self.assertEqual(record["wheel_sha256"], "wheel-a")
        self.assertEqual(record["fpsample_wheel_sha256"], "fpsample-a")
        self.assertEqual(record["acvl_utils_wheel_sha256"], "acvl-a")
        self.assertEqual(record["dcm2niix_sha256"], "dcm-a")
        self.assertEqual(record["setup_weights_manifest_sha256"], "weights-a")

    def test_bundle_install_record_normalizes_declared_python_runtime_fingerprint(self) -> None:
        top_level = bundle_install_record(
            {
                "python_runtime_fingerprint": "runtime-top-level",
                "python_runtime": {"fingerprint": "runtime-nested"},
            }
        )
        nested = bundle_install_record({"python_runtime": {"fingerprint": "runtime-nested"}})
        empty_top_level = bundle_install_record(
            {
                "python_runtime_fingerprint": "",
                "python_runtime": {"fingerprint": "runtime-nested"},
            }
        )
        missing = bundle_install_record({})

        self.assertEqual(top_level["python_runtime_fingerprint"], "runtime-top-level")
        self.assertEqual(nested["python_runtime_fingerprint"], "runtime-nested")
        self.assertEqual(empty_top_level["python_runtime_fingerprint"], "runtime-nested")
        self.assertEqual(missing["python_runtime_fingerprint"], "")
        self.assertNotIn("python_runtime_executable_sha256", top_level)

    def test_setup_environment_stays_under_app_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = setup_paths(home=home)
            env = build_setup_environment(paths, dicom_normalizer=paths.app_support / "bin" / "normalizer")

            self.assertEqual(env["XDG_CACHE_HOME"], str(paths.cache_dir))
            self.assertEqual(env["PIP_CACHE_DIR"], str(paths.cache_dir / "pip"))
            self.assertEqual(env["PIP_DISABLE_PIP_VERSION_CHECK"], "1")
            self.assertNotIn("PIP_NO_INDEX", env)
            self.assertEqual(env["PYTHONPYCACHEPREFIX"], str(paths.cache_dir / "pycache"))
            self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
            self.assertTrue(env["TOTALSEG_HOME_DIR"].startswith(str(paths.app_support)))
            self.assertTrue(env["nnUNet_raw"].startswith(str(paths.app_support)))
            self.assertTrue(env["nnUNet_preprocessed"].startswith(str(paths.app_support)))
            self.assertTrue(env["nnUNet_results"].startswith(str(paths.app_support)))
            self.assertTrue(env["TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER"].startswith(str(paths.app_support)))

    def test_setup_environment_discards_hostile_pip_and_python_configuration(self) -> None:
        hostile = {
            "PIP_TARGET": "/private/hostile-target",
            "PIP_PREFIX": "/private/hostile-prefix",
            "PIP_INDEX_URL": "https://hostile.invalid/simple",
            "PIP_CONFIG_FILE": "/private/hostile-pip.conf",
            "PYTHONPATH": "/private/hostile-pythonpath",
            "PYTHONHOME": "/private/hostile-pythonhome",
            "PYTHONUSERBASE": "/private/hostile-userbase",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, hostile, clear=False):
            env = build_setup_environment(setup_paths(home=Path(tmp)))

        for key in hostile:
            if key == "PIP_CONFIG_FILE":
                continue
            self.assertNotIn(key, env)
        self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
        self.assertNotIn("PIP_NO_INDEX", env)
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")


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


def _release_lock_metadata(
    *,
    constraints: object,
    project_file: Path,
    requirements_lock: Path,
    requirements_lock_sha256: str,
) -> dict[str, object]:
    """A release lock describes the full graph but installs no local overrides."""

    assert isinstance(constraints, Path)
    return {
        "schema": "totalsegmentator_wrapper_mac.dependency_lock.v5",
        "generation_id": "2b03e2ef-8d40-4a02-9ad3-0d2d8f6bd0d3",
        "constraints_sha256": hashlib.sha256(constraints.read_bytes()).hexdigest(),
        "project_file": project_file.name,
        "project_file_sha256": hashlib.sha256(project_file.read_bytes()).hexdigest(),
        "requirements_lock": requirements_lock.name,
        "requirements_lock_sha256": requirements_lock_sha256,
        "root_install_requirement": "totalsegmentator-wrapper-mac[dicom,mps,dentalseg,toothseg,ios-meshsegnet]",
        "resolved_distribution_names": ["acvl-utils", "fpsample", "open3d"],
        "install_distribution_names": ["open3d"],
        "excluded_bundled_overrides": {
            "acvl-utils": {
                "version": "0.2.6",
                "role": "separately_bundled_no_deps_override",
                "excluded_from_requirements_lock": True,
                "resolution_input_filename": "acvl_utils-0.2.6-py3-none-any.whl",
                "resolution_input_sha256": "a" * 64,
                "resolution_input_metadata_sha256": "c" * 64,
                "resolution_input_wheel_metadata_sha256": "d" * 64,
                "release_wheel_hash_binding": "setup_manifest_after_signing",
            },
            "fpsample": {
                "version": "1.0.2",
                "role": "separately_bundled_no_deps_override",
                "excluded_from_requirements_lock": True,
                "resolution_input_filename": "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl",
                "resolution_input_sha256": "b" * 64,
                "resolution_input_metadata_sha256": "e" * 64,
                "resolution_input_wheel_metadata_sha256": "f" * 64,
                "release_wheel_hash_binding": "setup_manifest_after_signing",
            },
        },
        "resolver": {
            "name": "pip-compile",
            "version": "7.5.0",
            "platform": "macos-14-arm64",
            "python": "3.12",
            "pip_version": "25.1.1",
            "python_full_version": "3.12.11",
            "macos_version": "26.6",
            "sysconfig_platform": "macosx-11.0-arm64",
            "target_compatibility": {
                "platform": "macosx_14_0_arm64",
                "python_version": "3.12",
                "implementation": "cp",
                "abi": "cp312",
                "selection": (
                    "pip-cross-target-options-and-wheelhouse-tag-audit-v1"
                ),
            },
        },
        "setup_consumes_requirements_lock": True,
        "pip_require_hashes": True,
        "resolution_complete": True,
    }


def _write_test_wheel(
    path: Path,
    *,
    name: str,
    version: str,
    module: str | None = None,
    requires: tuple[str, ...] = (),
) -> None:
    """Create a small installable wheel for local-only setup tests."""

    normalized = name.replace("-", "_")
    dist_info = f"{normalized}-{version}.dist-info"
    members: dict[str, str] = {
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\n"
            f"Name: {name}\n"
            f"Version: {version}\n"
            + "".join(f"Requires-Dist: {requirement}\n" for requirement in requires)
        ),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: test-suite\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ),
    }
    if module is not None:
        members[f"{module}/__init__.py"] = "VALUE = 'offline-wheelhouse'\n"
    members[f"{dist_info}/RECORD"] = "".join(
        f"{member},,\n" for member in sorted(members)
    )
    with zipfile.ZipFile(path, "w") as archive:
        for member, text in members.items():
            archive.writestr(member, text)


def _configure_fixture_hashed_wheelhouse(fixture: dict[str, object]) -> None:
    """Add the minimal hash-bound wheelhouse used by packaged setup tests."""

    resources = fixture["resources"]
    wheels = fixture["wheels"]
    constraints = fixture["constraints"]
    project_file = fixture["project_file"]
    payload = fixture["payload"]
    assert isinstance(resources, Path)
    assert isinstance(wheels, Path)
    assert isinstance(constraints, Path)
    assert isinstance(project_file, Path)
    assert isinstance(payload, dict)

    dependency_wheel = wheels / "open3d-0.19.0-py3-none-any.whl"
    _write_test_wheel(dependency_wheel, name="open3d", version="0.19.0")
    dependency_sha256 = hashlib.sha256(dependency_wheel.read_bytes()).hexdigest()
    lock = resources / "constraints" / "macos-arm64-py312.requirements.lock"
    lock.write_text(
        "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
        "2b03e2ef-8d40-4a02-9ad3-0d2d8f6bd0d3\n"
        f"open3d==0.19.0 --hash=sha256:{dependency_sha256}\n",
        encoding="utf-8",
    )
    lock_sha256 = hashlib.sha256(lock.read_bytes()).hexdigest()
    metadata = resources / "constraints" / "macos-arm64-py312.lock.json"
    metadata.write_text(
        json.dumps(
            _release_lock_metadata(
                constraints=constraints,
                project_file=project_file,
                requirements_lock=lock,
                requirements_lock_sha256=lock_sha256,
            )
        ),
        encoding="utf-8",
    )
    wheelhouse_manifest = (
        resources / "constraints" / "macos-arm64-py312.wheelhouse.json"
    )
    wheelhouse_manifest.write_text(
        json.dumps(
            {
                "schema": "totalsegmentator_wrapper_mac.offline_dependency_wheelhouse.v1",
                "canonical_lock": {
                    "requirements_lock_sha256": lock_sha256,
                },
                "wheels": [],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    wheelhouse_manifest_sha256 = hashlib.sha256(
        wheelhouse_manifest.read_bytes()
    ).hexdigest()
    bundled = payload["bundled"]
    assert isinstance(bundled, dict)
    bundled.update(
        {
            "requirements_lock": "constraints/macos-arm64-py312.requirements.lock",
            "dependency_lock_metadata": "constraints/macos-arm64-py312.lock.json",
            "project_file": "constraints/pyproject.toml",
            "dependency_wheelhouse_manifest": "constraints/macos-arm64-py312.wheelhouse.json",
        }
    )
    payload.update(
        {
            "requirements_lock_sha256": lock_sha256,
            "dependency_lock_metadata_sha256": hashlib.sha256(
                metadata.read_bytes()
            ).hexdigest(),
            "project_file_sha256": hashlib.sha256(project_file.read_bytes()).hexdigest(),
            "dependency_wheelhouse_manifest_sha256": wheelhouse_manifest_sha256,
        }
    )
    fixture["lock"] = lock
    fixture["lock_metadata"] = metadata
    fixture["dependency_wheel"] = dependency_wheel
    fixture["wheelhouse_manifest"] = wheelhouse_manifest


def _packaged_setup_fixture(root: Path) -> dict[str, object]:
    resources = root / "Resources"
    wheels = resources / "wheels"
    constraints_dir = resources / "constraints"
    wheels.mkdir(parents=True)
    constraints_dir.mkdir()
    wheel = wheels / "totalsegmentator_wrapper_mac-0.4.1-cp312-cp312-macosx_11_0_arm64.whl"
    fpsample = wheels / "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl"
    acvl_utils = wheels / "acvl_utils-0.2.6-py3-none-any.whl"
    constraints = constraints_dir / "macos-arm64-py312.txt"
    project_file = constraints_dir / "pyproject.toml"
    manifest = resources / "setup_manifest.json"
    wheel.write_bytes(b"wrapper wheel")
    fpsample.write_bytes(b"fpsample wheel")
    acvl_utils.write_bytes(b"acvl-utils wheel")
    constraints.write_text("acvl-utils==0.2.6\nfpsample==1.0.2\n", encoding="utf-8")
    project_file.write_text(
        "[project]\nname = 'fixture'\nversion = '0'\n",
        encoding="utf-8",
    )
    payload: dict[str, object] = {
        "schema": "totalsegmentator_wrapper_mac.mac_app_manifest.v1",
        "app_version": "0.4.1",
        "build_id": "test-build",
        "dependency_set_id": "deps-bundled-wheels",
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "fpsample_wheel_sha256": hashlib.sha256(fpsample.read_bytes()).hexdigest(),
        "acvl_utils_wheel_sha256": hashlib.sha256(acvl_utils.read_bytes()).hexdigest(),
        "constraints_sha256": hashlib.sha256(constraints.read_bytes()).hexdigest(),
        "project_file_sha256": None,
        "normalizer_sha256": "normalizer-a",
        "dcm2niix_sha256": "dcm-a",
        "sample1_manifest_sha256": "sample-a",
        "setup_weights_manifest_sha256": setup_weight_manifest_sha256(),
        "update_manifest_url": "",
        "bundled": {
            "wheel": wheel.name,
            "fpsample_wheel": f"wheels/{fpsample.name}",
            "acvl_utils_wheel": f"wheels/{acvl_utils.name}",
            "constraints": "constraints/macos-arm64-py312.txt",
            "project_file": None,
        },
    }
    fixture: dict[str, object] = {
        "resources": resources,
        "wheels": wheels,
        "wheel": wheel,
        "constraints": constraints,
        "project_file": project_file,
        "manifest": manifest,
        "payload": payload,
    }
    _write_fixture_manifest(fixture)
    return fixture


def _write_fixture_manifest(fixture: dict[str, object]) -> None:
    manifest = fixture["manifest"]
    payload = fixture["payload"]
    assert isinstance(manifest, Path)
    assert isinstance(payload, dict)
    manifest.write_text(json.dumps(payload), encoding="utf-8")


def _promote_fixture_wrapper_to_release_identity(
    fixture: dict[str, object],
) -> None:
    wheel = fixture["wheel"]
    payload = fixture["payload"]
    assert isinstance(wheel, Path)
    assert isinstance(payload, dict)
    release_wheel = wheel.with_name(
        "totalsegmentator_wrapper_mac-0.4.1-cp312-cp312-macosx_14_0_arm64.whl"
    )
    wheel.rename(release_wheel)
    fixture["wheel"] = release_wheel
    payload["wheel_sha256"] = hashlib.sha256(release_wheel.read_bytes()).hexdigest()
    bundled = payload["bundled"]
    assert isinstance(bundled, dict)
    bundled["wheel"] = release_wheel.name


def _run_packaged_setup(
    home: Path,
    fixture: dict[str, object],
) -> tuple[object, list[list[str]]]:
    wheel = fixture["wheel"]
    constraints = fixture["constraints"]
    manifest = fixture["manifest"]
    assert isinstance(wheel, Path)
    assert isinstance(constraints, Path)
    assert isinstance(manifest, Path)
    commands: list[list[str]] = []

    def recording_runner(
        command: list[str], cwd: Path | None, env: dict[str, str] | None
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    result = run_setup(
        home=home,
        python_executable=home / "python3.12",
        wheel=wheel,
        constraints=constraints,
        bundle_manifest=manifest,
        allow_network=True,
        skip_mps_check=True,
        skip_dentalseg_model=True,
        runner=recording_runner,
        normalizer_inspector=_normalizer_ok,
        python_inspector=_python312,
    )
    return result, commands


if __name__ == "__main__":
    unittest.main()
