from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.release_build_toolchain import (
    ReleaseBuildToolchainError,
    _run,
    bootstrap_release_build_toolchain,
    capture_release_build_toolchain_identity,
    generate_release_source_identity,
    generate_release_build_toolchain_metadata,
    prepare_release_build_toolchain,
    verify_release_build_toolchain_bootstrap,
    verify_release_build_toolchain_inputs,
    verify_prepared_release_build_toolchain,
    verify_release_build_toolchain_receipt,
)
from scripts.verify_release_input_readiness import (
    ReleaseInputReadinessError,
    verify_release_toolchain_bootstrap_artifact,
)


class ReleaseBuildToolchainTests(unittest.TestCase):
    """Offline fixtures for the release-only builder toolchain contract."""

    @staticmethod
    def _write_wheel(path: Path, *, name: str, version: str) -> str:
        normalized = name.replace("-", "_")
        dist_info = f"{normalized}-{version}.dist-info"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                f"{dist_info}/METADATA",
                "Metadata-Version: 2.4\n"
                f"Name: {name}\n"
                f"Version: {version}\n",
            )
            archive.writestr(
                f"{dist_info}/WHEEL",
                "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            )
            archive.writestr(f"{dist_info}/RECORD", "")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        project = root / "pyproject.toml"
        constraints = root / "constraints.txt"
        fpsample_builder = root / "build_fpsample_wheel_macos.sh"
        acvl_utils_builder = root / "build_acvl_utils_wheel.sh"
        for path, content in (
            (project, "[project]\nname = 'fixture'\nversion = '0'\n"),
            (constraints, "fixture==1\n"),
            (fpsample_builder, "#!/bin/bash\n# fixture fpsample builder\n"),
            (acvl_utils_builder, "#!/bin/bash\n# fixture acvl-utils builder\n"),
        ):
            path.write_text(content, encoding="utf-8")
        source_identity = root / "release-source-identity.json"
        generate_release_source_identity(
            output_path=source_identity,
            project_file=project,
            constraints=constraints,
            fpsample_builder=fpsample_builder,
            acvl_utils_builder=acvl_utils_builder,
        )
        source_identity_sha256 = hashlib.sha256(source_identity.read_bytes()).hexdigest()
        declaration = root / "release-build-toolchain-bootstrap-declaration.json"
        wheelhouse = root / "wheelhouse"
        wheelhouse.mkdir()
        packages = {
            "pip": "25.1.1",
            "build": "1.2.2",
            "setuptools": "77.0.3",
            "wheel": "0.45.1",
            "scikit-build-core": "0.11.6",
            "pybind11": "2.13.6",
            "cmake": "3.31.4",
            "ninja": "1.11.1.3",
        }
        wheels: dict[str, dict[str, str]] = {}
        lock_lines: list[str] = []
        for name, version in packages.items():
            filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
            digest = self._write_wheel(wheelhouse / filename, name=name, version=version)
            wheels[name] = {"filename": filename, "sha256": digest, "version": version}
            lock_lines.append(f"{name}=={version} --hash=sha256:{digest}")
        lock = root / "release-build-toolchain.requirements.lock"
        lock.write_text("\n".join(lock_lines) + "\n", encoding="utf-8")
        declaration.write_text(
            json.dumps(
                {
                    "schema": (
                        "totalsegmentator_wrapper_mac."
                        "release_build_toolchain_bootstrap_declaration.v1"
                    ),
                    "source_identity_sha256": source_identity_sha256,
                    "requirements": [
                        {"name": name, "version": version}
                        for name, version in sorted(packages.items())
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        metadata = root / "release-build-toolchain.lock.json"
        metadata.write_text(
            json.dumps(
                {
                    "schema": "totalsegmentator_wrapper_mac.release_build_toolchain.v2",
                    "bootstrap": {
                        "schema": (
                            "totalsegmentator_wrapper_mac."
                            "release_build_toolchain_bootstrap.v1"
                        ),
                        "declaration_sha256": hashlib.sha256(
                            declaration.read_bytes()
                        ).hexdigest(),
                        "source_identity_sha256": source_identity_sha256,
                    },
                    "lock_filename": lock.name,
                    "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
                    "resolved_distribution_names": sorted(packages),
                    "wheel_inputs": wheels,
                    "components": {
                        "wrapper": ["build", "setuptools", "wheel"],
                        "acvl-utils": ["build", "setuptools", "wheel"],
                        "fpsample": [
                            "build",
                            "scikit-build-core",
                            "pybind11",
                            "cmake",
                            "ninja",
                        ],
                    },
                    "toolchain": {
                        "installer": "uv-pip-offline-no-index-require-hashes-no-deps-v1",
                        "uv": {"version": "0.5.22", "binary_sha256": "a" * 64},
                        "python": {
                            "implementation": "CPython",
                            "full_version": "3.12.11",
                            "machine": "arm64",
                            "sysconfig_platform": "macosx-14.0-arm64",
                            "executable_sha256": "b" * 64,
                        },
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return lock, metadata, wheelhouse

    @staticmethod
    def _bootstrap_kwargs(root: Path) -> dict[str, Path]:
        return {
            "bootstrap_declaration_path": (
                root / "release-build-toolchain-bootstrap-declaration.json"
            ),
            "source_identity_path": root / "release-source-identity.json",
            "project_file": root / "pyproject.toml",
            "constraints": root / "constraints.txt",
            "fpsample_builder": root / "build_fpsample_wheel_macos.sh",
            "acvl_utils_builder": root / "build_acvl_utils_wheel.sh",
        }

    def test_declarative_bootstrap_selects_exact_local_wheels_and_emits_hashed_lock(self) -> None:
        """The first toolchain lock may be generated, but never guessed.

        The declaration supplies only reviewed name/version choices.  The
        bootstrapper inventories the supplied local wheel bytes and writes the
        exact hashes that later strict phases consume; it is not a resolver or
        downloader.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pyproject.toml"
            constraints = root / "constraints.txt"
            fpsample_builder = root / "build_fpsample_wheel_macos.sh"
            acvl_builder = root / "build_acvl_utils_wheel.sh"
            for path, content in (
                (project, "[project]\nname = 'fixture'\nversion = '0'\n"),
                (constraints, "fixture==1\n"),
                (fpsample_builder, "#!/bin/bash\n# fpsample source identity\n"),
                (acvl_builder, "#!/bin/bash\n# acvl source identity\n"),
            ):
                path.write_text(content, encoding="utf-8")
            fpsample_builder.chmod(0o755)
            acvl_builder.chmod(0o755)
            source_identity = root / "source-identity.json"
            generate_release_source_identity(
                output_path=source_identity,
                project_file=project,
                constraints=constraints,
                fpsample_builder=fpsample_builder,
                acvl_utils_builder=acvl_builder,
            )

            source_wheelhouse = root / "source-wheels"
            source_wheelhouse.mkdir()
            packages = {
                "pip": "25.1.1",
                "build": "1.2.2",
                "setuptools": "77.0.3",
                "wheel": "0.45.1",
                "scikit-build-core": "0.11.6",
                "pybind11": "2.13.6",
                "cmake": "3.31.4",
                "ninja": "1.11.1.3",
            }
            for name, version in packages.items():
                self._write_wheel(
                    source_wheelhouse / f"{name.replace('-', '_')}-{version}-py3-none-any.whl",
                    name=name,
                    version=version,
                )
            declaration = root / "bootstrap-declaration.json"
            declaration.write_text(
                json.dumps(
                    {
                        "schema": "totalsegmentator_wrapper_mac.release_build_toolchain_bootstrap_declaration.v1",
                        "source_identity_sha256": hashlib.sha256(
                            source_identity.read_bytes()
                        ).hexdigest(),
                        "requirements": [
                            {"name": name, "version": version}
                            for name, version in sorted(packages.items())
                        ],
                    }
                ),
                encoding="utf-8",
            )
            python = root / "python"
            uv = root / "uv"
            python.write_bytes(b"fixture-python")
            uv.write_bytes(b"fixture-uv")
            python.chmod(0o755)
            uv.chmod(0o755)
            expected_identity = {
                "installer": "uv-pip-offline-no-index-require-hashes-no-deps-v1",
                "uv": {"version": "0.5.22", "binary_sha256": "a" * 64},
                "python": {
                    "implementation": "CPython",
                    "full_version": "3.12.11",
                    "machine": "arm64",
                    "sysconfig_platform": "macosx-14.0-arm64",
                    "executable_sha256": "b" * 64,
                },
            }
            output = root / "bootstrap-output"
            with patch(
                "scripts.release_build_toolchain.capture_release_build_toolchain_identity",
                return_value=expected_identity,
            ):
                result = bootstrap_release_build_toolchain(
                    declaration_path=declaration,
                    source_identity_path=source_identity,
                    source_wheelhouse=source_wheelhouse,
                    output_directory=output,
                    python_executable=python,
                    uv_executable=uv,
                    project_file=project,
                    constraints=constraints,
                    fpsample_builder=fpsample_builder,
                    acvl_utils_builder=acvl_builder,
                )

            lock = output / "release-build-toolchain.requirements.lock"
            self.assertEqual(result["lock_path"], str(lock))
            self.assertIn("pip==25.1.1 --hash=sha256:", lock.read_text(encoding="utf-8"))
            self.assertTrue((output / "wheelhouse" / "pip-25.1.1-py3-none-any.whl").is_file())
            verified = verify_release_build_toolchain_inputs(
                lock_path=lock,
                metadata_path=output / "release-build-toolchain.lock.json",
                wheelhouse=output / "wheelhouse",
            )
            self.assertEqual(verified["wheel_inputs"]["pip"]["version"], "25.1.1")

    def test_source_identity_closes_over_bootstrap_execution_scripts(self) -> None:
        """Every checked-in implementation that can authorize or alter bytes is bound."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pyproject.toml"
            constraints = root / "constraints.txt"
            fpsample_builder = root / "build_fpsample_wheel_macos.sh"
            acvl_utils_builder = root / "build_acvl_utils_wheel.sh"
            for path in (
                project,
                constraints,
                fpsample_builder,
                acvl_utils_builder,
            ):
                path.write_text(f"fixture: {path.name}\n", encoding="utf-8")
            identity = generate_release_source_identity(
                output_path=root / "source-identity.json",
                project_file=project,
                constraints=constraints,
                fpsample_builder=fpsample_builder,
                acvl_utils_builder=acvl_utils_builder,
            )

            self.assertEqual(
                identity["schema"],
                "totalsegmentator_wrapper_mac.release_source_identity.v2",
            )
            files = identity["files"]
            self.assertIsInstance(files, dict)
            assert isinstance(files, dict)
            self.assertEqual(
                set(files),
                {
                    "project_file",
                    "constraints",
                    "fpsample_builder",
                    "acvl_utils_builder",
                    "release_toolchain",
                    "component_runner",
                    "fpsample_signer",
                    "license_verifier",
                    "dependency_lock_generator",
                },
            )
            self.assertEqual(
                {entry["filename"] for entry in files.values()},
                {
                    "pyproject.toml",
                    "constraints.txt",
                    "build_fpsample_wheel_macos.sh",
                    "build_acvl_utils_wheel.sh",
                    "release_build_toolchain.py",
                    "run_release_component_build.sh",
                    "sign_fpsample_wheel_macos.py",
                    "verify_license_distribution.py",
                    "generate_macos_arm64_py312_lock.py",
                },
            )
            for entry in files.values():
                self.assertEqual(Path(entry["filename"]).name, entry["filename"])
                self.assertFalse(Path(entry["filename"]).is_absolute())

    def test_bootstrap_environment_variable_cannot_bypass_component_authorization(self) -> None:
        """A direct component invocation must reject bootstrap mode early.

        This runs only the bootstrap guard; no source archive, wheel build, or
        network operation can be reached without the receipt-backed
        authorization produced by the release runner.
        """

        script = Path(__file__).resolve().parents[1] / "scripts" / "build_fpsample_wheel_macos.sh"
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_REQUIRED": "1",
            "TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_COMPONENT_RUNNER": "1",
            "TOTALSEGMENTATOR_WRAPPER_MAC_BOOTSTRAP_PRE_SIGN": "1",
            "TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_PYTHON": sys.executable,
        }
        completed = subprocess.run(
            ["/bin/bash", str(script)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("bootstrap authorization", completed.stderr)

    def test_hash_bound_wheelhouse_covers_every_backend_and_transitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock, metadata, wheelhouse = self._fixture(Path(tmp))
            verified = verify_release_build_toolchain_inputs(
                lock_path=lock,
                metadata_path=metadata,
                wheelhouse=wheelhouse,
            )
            self.assertEqual(verified["lock_sha256"], hashlib.sha256(lock.read_bytes()).hexdigest())
            self.assertEqual(set(verified["wheel_inputs"]), {
                "pip",
                "build",
                "setuptools",
                "wheel",
                "scikit-build-core",
                "pybind11",
                "cmake",
                "ninja",
            })

    def test_bootstrap_rejects_source_bytes_changed_after_identity_generation(self) -> None:
        """The pre-sign permission cannot outlive its checked source identity."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, metadata, wheelhouse = self._fixture(root)
            source = self._bootstrap_kwargs(root)
            (root / "build_fpsample_wheel_macos.sh").write_text(
                "#!/bin/bash\n# changed after review\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(
                ReleaseBuildToolchainError,
                "source identity no longer matches",
            ):
                verify_release_build_toolchain_bootstrap(
                    lock_path=lock,
                    metadata_path=metadata,
                    wheelhouse=wheelhouse,
                    declaration_path=source["bootstrap_declaration_path"],
                    source_identity_path=source["source_identity_path"],
                    project_file=source["project_file"],
                    constraints=source["constraints"],
                    fpsample_builder=source["fpsample_builder"],
                    acvl_utils_builder=source["acvl_utils_builder"],
                )

    def test_bootstrap_rejects_authorization_pipeline_change_after_declaration(self) -> None:
        """Producer or authorization drift revokes bootstrap and readiness."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir()
            protected = {
                "release_toolchain": scripts / "release_build_toolchain.py",
                "component_runner": scripts / "run_release_component_build.sh",
                "fpsample_signer": scripts / "sign_fpsample_wheel_macos.py",
                "license_verifier": scripts / "verify_license_distribution.py",
                "dependency_lock_generator": (
                    scripts / "generate_macos_arm64_py312_lock.py"
                ),
            }
            for name, path in protected.items():
                path.write_text(f"# fixture {name}\n", encoding="utf-8")

            with patch("scripts.release_build_toolchain.ROOT", root):
                lock, metadata, wheelhouse = self._fixture(root)
                source = self._bootstrap_kwargs(root)
                for name, path in protected.items():
                    with self.subTest(source=name):
                        original = path.read_text(encoding="utf-8")
                        try:
                            path.write_text(
                                original + "# changed after review\n", encoding="utf-8"
                            )
                            with self.assertRaisesRegex(
                                ReleaseBuildToolchainError,
                                "source identity no longer matches",
                            ):
                                verify_release_build_toolchain_bootstrap(
                                    lock_path=lock,
                                    metadata_path=metadata,
                                    wheelhouse=wheelhouse,
                                    declaration_path=source[
                                        "bootstrap_declaration_path"
                                    ],
                                    source_identity_path=source["source_identity_path"],
                                    project_file=source["project_file"],
                                    constraints=source["constraints"],
                                    fpsample_builder=source["fpsample_builder"],
                                    acvl_utils_builder=source["acvl_utils_builder"],
                                )
                            with self.assertRaisesRegex(
                                ReleaseInputReadinessError,
                                "source identity no longer matches",
                            ):
                                verify_release_toolchain_bootstrap_artifact(
                                    lock_path=lock,
                                    metadata_path=metadata,
                                    wheelhouse=wheelhouse,
                                    declaration_path=source[
                                        "bootstrap_declaration_path"
                                    ],
                                    source_identity_path=source[
                                        "source_identity_path"
                                    ],
                                    receipt_path=root / "unused-receipt.json",
                                    pre_sign_wheel_receipt_path=(
                                        root / "unused-pre-sign-receipt.json"
                                    ),
                                    pre_sign_wheel_directory=root / "unused-dist",
                                    project_file=source["project_file"],
                                    constraints=source["constraints"],
                                    fpsample_builder=source["fpsample_builder"],
                                    acvl_utils_builder=source["acvl_utils_builder"],
                                )
                        finally:
                            path.write_text(original, encoding="utf-8")

    def test_missing_fpsample_backend_tool_is_rejected_before_any_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock, metadata, wheelhouse = self._fixture(Path(tmp))
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            payload["components"]["fpsample"].remove("ninja")
            metadata.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ReleaseBuildToolchainError, "fpsample"):
                verify_release_build_toolchain_inputs(
                    lock_path=lock,
                    metadata_path=metadata,
                    wheelhouse=wheelhouse,
                )

    def test_wheelhouse_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock, metadata, wheelhouse = self._fixture(Path(tmp))
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            payload["wheel_inputs"]["build"]["sha256"] = "0" * 64
            metadata.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ReleaseBuildToolchainError, "SHA-256"):
                verify_release_build_toolchain_inputs(
                    lock_path=lock,
                    metadata_path=metadata,
                    wheelhouse=wheelhouse,
                )

    def test_wheelhouse_symlink_member_is_rejected_before_offline_install(self) -> None:
        """A hash-bound wheel still may not carry extractor-control entries."""

        with tempfile.TemporaryDirectory() as tmp:
            lock, metadata, wheelhouse = self._fixture(Path(tmp))
            wheel = wheelhouse / "build-1.2.2-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "a") as archive:
                link = zipfile.ZipInfo("build/link")
                link.create_system = 3
                link.external_attr = (0o120777 << 16)
                archive.writestr(link, "outside")

            payload = json.loads(metadata.read_text(encoding="utf-8"))
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            original_digest = payload["wheel_inputs"]["build"]["sha256"]
            payload["wheel_inputs"]["build"]["sha256"] = digest
            lock.write_text(
                lock.read_text(encoding="utf-8").replace(original_digest, digest),
                encoding="utf-8",
            )
            payload["lock_sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest()
            metadata.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                ReleaseBuildToolchainError, "unsafe archive member type"
            ):
                verify_release_build_toolchain_inputs(
                    lock_path=lock,
                    metadata_path=metadata,
                    wheelhouse=wheelhouse,
                )

    def test_metadata_rejects_operator_path_in_persisted_wheel_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock, metadata, wheelhouse = self._fixture(Path(tmp))
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            payload["wheel_inputs"]["build"]["filename"] = (
                r"C:\\Users\\operator\\build-1.2.2-py3-none-any.whl"
            )
            metadata.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ReleaseBuildToolchainError, "wheel input is invalid"):
                verify_release_build_toolchain_inputs(
                    lock_path=lock,
                    metadata_path=metadata,
                    wheelhouse=wheelhouse,
                )

    def test_metadata_generator_inventories_only_prepared_hashed_wheelhouse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, _metadata, wheelhouse = self._fixture(root)
            generated = root / "generated-release-build-toolchain.lock.json"
            expected_identity = {
                "installer": "uv-pip-offline-no-index-require-hashes-no-deps-v1",
                "uv": {"version": "0.5.22", "binary_sha256": "a" * 64},
                "python": {
                    "implementation": "CPython",
                    "full_version": "3.12.11",
                    "machine": "arm64",
                    "sysconfig_platform": "macosx-14.0-arm64",
                    "executable_sha256": "b" * 64,
                },
            }
            with patch(
                "scripts.release_build_toolchain.capture_release_build_toolchain_identity",
                return_value=expected_identity,
            ):
                result = generate_release_build_toolchain_metadata(
                    lock_path=lock,
                    metadata_path=generated,
                    wheelhouse=wheelhouse,
                    python_executable=Path("/unused/python"),
                    uv_executable=Path("/unused/uv"),
                    **self._bootstrap_kwargs(root),
                )

            self.assertTrue(generated.is_file())
            self.assertEqual(result["toolchain"], expected_identity)
            self.assertEqual(
                result["metadata_sha256"],
                hashlib.sha256(generated.read_bytes()).hexdigest(),
            )
            with self.assertRaisesRegex(ReleaseBuildToolchainError, "must be absent"):
                generate_release_build_toolchain_metadata(
                    lock_path=lock,
                    metadata_path=generated,
                    wheelhouse=wheelhouse,
                    python_executable=Path("/unused/python"),
                    uv_executable=Path("/unused/uv"),
                    **self._bootstrap_kwargs(root),
                )

    def test_preparation_command_starts_from_an_allowlisted_environment(self) -> None:
        observed: dict[str, str] = {}

        def recording_runner(
            command: list[str], *, capture_output: bool, text: bool, env: dict[str, str]
        ) -> subprocess.CompletedProcess[str]:
            self.assertTrue(capture_output)
            self.assertTrue(text)
            observed.update(env)
            return subprocess.CompletedProcess(command, 0, "", "")

        hostile = {
            "PIP_INDEX_URL": "https://hostile.invalid/simple",
            "UV_INDEX_URL": "https://hostile.invalid/uv",
            "PYTHONPATH": "/private/hostile-pythonpath",
            "CMAKE_GENERATOR": "Ninja",
            "NINJA_STATUS": "hostile",
            "CC": "/private/hostile-clang",
            "CFLAGS": "-hostile",
            "DYLD_LIBRARY_PATH": "/private/hostile-dylib",
            "HTTP_PROXY": "http://hostile.invalid",
            "DEVELOPER_DIR": "/private/hostile-xcode",
        }
        with patch.dict(os.environ, hostile, clear=False), patch(
            "scripts.release_build_toolchain.subprocess.run",
            side_effect=recording_runner,
        ):
            _run(["/usr/bin/true"], label="fixture command")

        self.assertEqual(observed["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")
        self.assertEqual(observed["PIP_CONFIG_FILE"], os.devnull)
        self.assertEqual(observed["PIP_DISABLE_PIP_VERSION_CHECK"], "1")
        self.assertEqual(observed["PIP_NO_INPUT"], "1")
        self.assertEqual(observed["PYTHONNOUSERSITE"], "1")
        self.assertEqual(observed["LC_ALL"], "C")
        for key in hostile:
            self.assertNotIn(key, observed)

    def test_preparation_checks_declared_backend_dependency_closure(self) -> None:
        source = inspect.getsource(prepare_release_build_toolchain)
        self.assertIn('"pip", "--isolated", "check"', source)
        self.assertIn("dependency closure check", source)

    def test_uv_and_python_identity_probes_are_sealed_from_host_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = root / "python"
            uv = root / "uv"
            python.write_bytes(b"fixture-python")
            uv.write_bytes(b"fixture-uv")
            python.chmod(0o755)
            uv.chmod(0o755)
            environments: list[dict[str, str]] = []

            def recording_runner(
                command: list[str], *, check: bool, capture_output: bool, text: bool, env: dict[str, str]
            ) -> subprocess.CompletedProcess[str]:
                self.assertTrue(check)
                self.assertTrue(capture_output)
                self.assertTrue(text)
                environments.append(env)
                if command[1:] == ["--version"]:
                    return subprocess.CompletedProcess(command, 0, "uv 0.5.22\n", "")
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        {
                            "implementation": "CPython",
                            "full_version": "3.12.11",
                            "machine": "arm64",
                            "sysconfig_platform": "macosx-11.0-arm64",
                        }
                    )
                    + "\n",
                    "",
                )

            hostile = {
                "PYTHONPATH": "/private/hostile-pythonpath",
                "UV_INDEX_URL": "https://hostile.invalid/uv",
                "DYLD_LIBRARY_PATH": "/private/hostile-dylib",
                "DEVELOPER_DIR": "/private/hostile-xcode",
            }
            with patch.dict(os.environ, hostile, clear=False), patch(
                "scripts.release_build_toolchain.subprocess.run",
                side_effect=recording_runner,
            ):
                identity = capture_release_build_toolchain_identity(
                    python_executable=python,
                    uv_executable=uv,
                )

            self.assertEqual(identity["uv"]["version"], "0.5.22")
            self.assertEqual(identity["python"]["full_version"], "3.12.11")
            self.assertEqual(identity["python"]["sysconfig_platform"], "macosx-11.0-arm64")
            self.assertEqual(len(environments), 2)
            for environment in environments:
                self.assertEqual(environment["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")
                self.assertEqual(environment["LC_ALL"], "C")
                for key in hostile:
                    self.assertNotIn(key, environment)

    def test_receipt_binds_locked_python_tools_and_records_external_xcode_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, metadata, wheelhouse = self._fixture(root)
            verified = verify_release_build_toolchain_inputs(
                lock_path=lock,
                metadata_path=metadata,
                wheelhouse=wheelhouse,
            )
            receipt = root / "release-build-toolchain-receipt.json"
            native_toolchain = {
                "boundary": "apple-xcode-clang-external-recorded-not-hash-bound-v1",
                "developer_selection": "selected-full-xcode",
                "xcode_version": "16.2",
                "xcode_build_version": "16C5032a",
                "clang_version": "Apple clang version 16.0.0",
                "clang_binary_sha256": "c" * 64,
                "clang_selection": "xcrun--find-clang",
                "sealed_path_policy": (
                    "prepared-toolchain-bin-plus-apple-system-tools-v1"
                ),
            }
            receipt.write_text(
                json.dumps(
                    {
                        "schema": (
                            "totalsegmentator_wrapper_mac."
                            "release_build_toolchain_receipt.v2"
                        ),
                        "bootstrap": verified["bootstrap"],
                        "lock_sha256": verified["lock_sha256"],
                        "metadata_sha256": verified["metadata_sha256"],
                        "toolchain": {
                            "python": verified["toolchain"]["python"],
                            "uv": verified["toolchain"]["uv"],
                            "native_toolchain": native_toolchain,
                        },
                        "components": verified["components"],
                        "wheel_inputs": verified["wheel_inputs"],
                        "installed_distribution_versions": {
                            name: entry["version"]
                            for name, entry in verified["wheel_inputs"].items()
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            checked = verify_release_build_toolchain_receipt(
                receipt_path=receipt,
                lock_path=lock,
                metadata_path=metadata,
            )
            self.assertEqual(
                checked["toolchain"]["native_toolchain"], native_toolchain
            )

            payload = json.loads(receipt.read_text(encoding="utf-8"))
            payload["toolchain"]["native_toolchain"].pop("boundary")
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ReleaseBuildToolchainError, "native toolchain"):
                verify_release_build_toolchain_receipt(
                    receipt_path=receipt,
                    lock_path=lock,
                    metadata_path=metadata,
                )

    def test_receipt_rejects_absolute_native_toolchain_path_leakage(self) -> None:
        """Release provenance must never carry local Xcode paths or usernames."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, metadata, wheelhouse = self._fixture(root)
            verified = verify_release_build_toolchain_inputs(
                lock_path=lock,
                metadata_path=metadata,
                wheelhouse=wheelhouse,
            )
            receipt = root / "release-build-toolchain-receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "schema": (
                            "totalsegmentator_wrapper_mac."
                            "release_build_toolchain_receipt.v2"
                        ),
                        "bootstrap": verified["bootstrap"],
                        "lock_sha256": verified["lock_sha256"],
                        "metadata_sha256": verified["metadata_sha256"],
                        "toolchain": {
                            "python": verified["toolchain"]["python"],
                            "uv": verified["toolchain"]["uv"],
                            "native_toolchain": {
                                "boundary": (
                                    "apple-xcode-clang-external-recorded-not-hash-bound-v1"
                                ),
                                "developer_selection": "selected-full-xcode",
                                "xcode_version": "16.2",
                                "xcode_build_version": "16C5032a",
                                "clang_version": "Apple clang version 16.0.0",
                                "clang_binary_sha256": "c" * 64,
                                "clang_selection": "xcrun--find-clang",
                                "sealed_path_policy": (
                                    "prepared-toolchain-bin-plus-apple-system-tools-v1"
                                ),
                                "developer_dir": (
                                    "/Users/example/Applications/Xcode.app/"
                                    "Contents/Developer"
                                ),
                            },
                        },
                        "components": verified["components"],
                        "wheel_inputs": verified["wheel_inputs"],
                        "installed_distribution_versions": {
                            name: entry["version"]
                            for name, entry in verified["wheel_inputs"].items()
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ReleaseBuildToolchainError, "native toolchain"):
                verify_release_build_toolchain_receipt(
                    receipt_path=receipt,
                    lock_path=lock,
                    metadata_path=metadata,
                )

    def test_prepared_venv_rejects_ambient_site_packages_and_checks_all_backend_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, metadata, wheelhouse = self._fixture(root)
            metadata_payload = json.loads(metadata.read_text(encoding="utf-8"))
            metadata_payload["toolchain"]["python"]["executable_sha256"] = hashlib.sha256(
                Path(sys.executable).resolve().read_bytes()
            ).hexdigest()
            metadata.write_text(json.dumps(metadata_payload), encoding="utf-8")
            verified = verify_release_build_toolchain_inputs(
                lock_path=lock,
                metadata_path=metadata,
                wheelhouse=wheelhouse,
            )
            native_toolchain = {
                "boundary": "apple-xcode-clang-external-recorded-not-hash-bound-v1",
                "developer_selection": "selected-full-xcode",
                "xcode_version": "16.2",
                "xcode_build_version": "16C5032a",
                "clang_version": "Apple clang version 16.0.0",
                "clang_binary_sha256": "c" * 64,
                "clang_selection": "xcrun--find-clang",
                "sealed_path_policy": (
                    "prepared-toolchain-bin-plus-apple-system-tools-v1"
                ),
            }
            receipt = root / "release-build-toolchain-receipt.json"
            expected_versions = {
                name: entry["version"]
                for name, entry in verified["wheel_inputs"].items()
            }
            receipt.write_text(
                json.dumps(
                    {
                        "schema": (
                            "totalsegmentator_wrapper_mac."
                            "release_build_toolchain_receipt.v2"
                        ),
                        "bootstrap": verified["bootstrap"],
                        "lock_sha256": verified["lock_sha256"],
                        "metadata_sha256": verified["metadata_sha256"],
                        "toolchain": {
                            "python": verified["toolchain"]["python"],
                            "uv": verified["toolchain"]["uv"],
                            "native_toolchain": native_toolchain,
                        },
                        "components": verified["components"],
                        "wheel_inputs": verified["wheel_inputs"],
                        "installed_distribution_versions": expected_versions,
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            venv = root / "prepared-venv"
            (venv / "bin").mkdir(parents=True)
            prepared_python = venv / "bin" / "python"
            shutil.copyfile(Path(sys.executable).resolve(), prepared_python)
            os.chmod(prepared_python, 0o755)
            (venv / "pyvenv.cfg").write_text(
                "include-system-site-packages = false\n", encoding="utf-8"
            )
            for executable in ("cmake", "ninja"):
                path = venv / "bin" / executable
                path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                os.chmod(path, 0o755)

            with patch(
                "scripts.release_build_toolchain._installed_versions",
                return_value=expected_versions,
            ), patch(
                "scripts.release_build_toolchain._verify_prepared_pip"
            ), patch(
                "scripts.release_build_toolchain.capture_trusted_native_toolchain",
                return_value=native_toolchain,
            ):
                checked = verify_prepared_release_build_toolchain(
                    receipt_path=receipt,
                    lock_path=lock,
                    metadata_path=metadata,
                    prepared_python=prepared_python,
                    component="fpsample",
                )
            self.assertEqual(checked["toolchain_bin"], str(venv / "bin"))

            (venv / "pyvenv.cfg").write_text(
                "include-system-site-packages = true\n", encoding="utf-8"
            )
            with patch(
                "scripts.release_build_toolchain._installed_versions",
                return_value=expected_versions,
            ), patch(
                "scripts.release_build_toolchain._verify_prepared_pip"
            ), patch(
                "scripts.release_build_toolchain.capture_trusted_native_toolchain",
                return_value=native_toolchain,
            ), self.assertRaisesRegex(ReleaseBuildToolchainError, "system site packages"):
                verify_prepared_release_build_toolchain(
                    receipt_path=receipt,
                    lock_path=lock,
                    metadata_path=metadata,
                    prepared_python=prepared_python,
                    component="fpsample",
                )

    def test_preparation_keeps_console_scripts_bound_to_the_published_venv(self) -> None:
        """Moving a venv after installation leaves its script shebangs stale.

        ``cmake`` and ``ninja`` are console scripts installed into the prepared
        toolchain.  They must be created at the same final path that the
        component runner later places first in PATH; a post-install rename
        would leave their absolute Python shebangs pointing at a deleted
        temporary directory.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, metadata, wheelhouse = self._fixture(root)
            verified = verify_release_build_toolchain_inputs(
                lock_path=lock,
                metadata_path=metadata,
                wheelhouse=wheelhouse,
            )
            expected_versions = {
                name: entry["version"]
                for name, entry in verified["wheel_inputs"].items()
            }
            native_toolchain = {
                "boundary": "apple-xcode-clang-external-recorded-not-hash-bound-v1",
                "developer_selection": "selected-full-xcode",
                "xcode_version": "16.2",
                "xcode_build_version": "16C5032a",
                "clang_version": "Apple clang version 16.0.0",
                "clang_binary_sha256": "c" * 64,
                "clang_selection": "xcrun--find-clang",
                "sealed_path_policy": (
                    "prepared-toolchain-bin-plus-apple-system-tools-v1"
                ),
            }
            runtime = {
                "python": verified["toolchain"]["python"],
                "uv": verified["toolchain"]["uv"],
                "native_toolchain": native_toolchain,
            }
            python = root / "python"
            uv = root / "uv"
            python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            python.chmod(0o755)
            uv.chmod(0o755)
            work_directory = root / "work"
            work_directory.mkdir()
            receipt = root / "release-build-toolchain-receipt.json"

            def fake_run(command: list[str], *, label: str) -> None:
                if len(command) >= 2 and command[1] == "venv":
                    venv = Path(command[-1])
                    venv_bin = venv / "bin"
                    venv_bin.mkdir(parents=True)
                    venv_python = venv_bin / "python"
                    venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    venv_python.chmod(0o755)
                    (venv / "pyvenv.cfg").write_text(
                        "include-system-site-packages = false\n", encoding="utf-8"
                    )
                    for executable in ("cmake", "ninja"):
                        (venv_bin / executable).write_text(
                            f"#!{venv_python}\nexit 0\n", encoding="utf-8"
                        )
                        (venv_bin / executable).chmod(0o755)

            with patch(
                "scripts.release_build_toolchain.verify_release_build_toolchain_runtime",
                return_value=runtime,
            ), patch(
                "scripts.release_build_toolchain._run", side_effect=fake_run
            ), patch(
                "scripts.release_build_toolchain._installed_versions",
                return_value=expected_versions,
            ), patch(
                "scripts.release_build_toolchain._verify_prepared_pip"
            ):
                result = prepare_release_build_toolchain(
                    lock_path=lock,
                    metadata_path=metadata,
                    wheelhouse=wheelhouse,
                    python_executable=python,
                    uv_executable=uv,
                    work_directory=work_directory,
                    receipt_path=receipt,
                    **self._bootstrap_kwargs(root),
                )

            published_python = Path(result["prepared_python"])
            self.assertTrue(published_python.is_file())
            for executable in ("cmake", "ninja"):
                first_line = (published_python.parent / executable).read_text(
                    encoding="utf-8"
                ).splitlines()[0]
                self.assertEqual(first_line, f"#!{published_python}")

    def test_failed_preparation_removes_only_its_unique_final_venv(self) -> None:
        """A failed install must not leave a candidate venv usable by accident."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, metadata, wheelhouse = self._fixture(root)
            verified = verify_release_build_toolchain_inputs(
                lock_path=lock,
                metadata_path=metadata,
                wheelhouse=wheelhouse,
            )
            native_toolchain = {
                "boundary": "apple-xcode-clang-external-recorded-not-hash-bound-v1",
                "developer_selection": "selected-full-xcode",
                "xcode_version": "16.2",
                "xcode_build_version": "16C5032a",
                "clang_version": "Apple clang version 16.0.0",
                "clang_binary_sha256": "c" * 64,
                "clang_selection": "xcrun--find-clang",
                "sealed_path_policy": (
                    "prepared-toolchain-bin-plus-apple-system-tools-v1"
                ),
            }
            runtime = {
                "python": verified["toolchain"]["python"],
                "uv": verified["toolchain"]["uv"],
                "native_toolchain": native_toolchain,
            }
            python = root / "python"
            uv = root / "uv"
            python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            python.chmod(0o755)
            uv.chmod(0o755)
            work_directory = root / "work"
            work_directory.mkdir()
            unrelated = work_directory / "unrelated"
            unrelated.mkdir()
            sentinel = unrelated / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")

            def failing_run(command: list[str], *, label: str) -> None:
                if len(command) >= 2 and command[1] == "venv":
                    venv_bin = Path(command[-1]) / "bin"
                    venv_bin.mkdir(parents=True)
                    venv_python = venv_bin / "python"
                    venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    venv_python.chmod(0o755)
                    return
                raise ReleaseBuildToolchainError("fixture install failure")

            with patch(
                "scripts.release_build_toolchain.verify_release_build_toolchain_runtime",
                return_value=runtime,
            ), patch(
                "scripts.release_build_toolchain._run", side_effect=failing_run
            ), self.assertRaisesRegex(ReleaseBuildToolchainError, "fixture install failure"):
                prepare_release_build_toolchain(
                    lock_path=lock,
                    metadata_path=metadata,
                    wheelhouse=wheelhouse,
                    python_executable=python,
                    uv_executable=uv,
                    work_directory=work_directory,
                    receipt_path=root / "release-build-toolchain-receipt.json",
                    **self._bootstrap_kwargs(root),
                )

            self.assertEqual(list(work_directory.glob("prepared-venv-*")), [])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_preparation_rejects_group_writable_work_directory_before_venv_creation(
        self,
    ) -> None:
        """A shared work directory can be swapped between validation and use."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, metadata, wheelhouse = self._fixture(root)
            verified = verify_release_build_toolchain_inputs(
                lock_path=lock,
                metadata_path=metadata,
                wheelhouse=wheelhouse,
            )
            runtime = {
                "python": verified["toolchain"]["python"],
                "uv": verified["toolchain"]["uv"],
                "native_toolchain": {
                    "boundary": "apple-xcode-clang-external-recorded-not-hash-bound-v1",
                    "developer_selection": "selected-full-xcode",
                    "xcode_version": "16.2",
                    "xcode_build_version": "16C5032a",
                    "clang_version": "Apple clang version 16.0.0",
                    "clang_binary_sha256": "c" * 64,
                    "clang_selection": "xcrun--find-clang",
                    "sealed_path_policy": (
                        "prepared-toolchain-bin-plus-apple-system-tools-v1"
                    ),
                },
            }
            python = root / "python"
            uv = root / "uv"
            python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            python.chmod(0o755)
            uv.chmod(0o755)
            work_directory = root / "work"
            work_directory.mkdir()
            work_directory.chmod(0o770)

            with patch(
                "scripts.release_build_toolchain.verify_release_build_toolchain_runtime",
                return_value=runtime,
            ), patch(
                "scripts.release_build_toolchain._run",
                side_effect=AssertionError("venv creation must not be reached"),
            ), self.assertRaisesRegex(
                ReleaseBuildToolchainError, "work directory.*group- or other-writable"
            ):
                prepare_release_build_toolchain(
                    lock_path=lock,
                    metadata_path=metadata,
                    wheelhouse=wheelhouse,
                    python_executable=python,
                    uv_executable=uv,
                    work_directory=work_directory,
                    receipt_path=root / "release-build-toolchain-receipt.json",
                    **self._bootstrap_kwargs(root),
                )

    def test_preparation_rejects_work_directory_not_owned_by_current_user(self) -> None:
        """Ownership is checked before a fresh candidate venv is allocated."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, metadata, wheelhouse = self._fixture(root)
            verified = verify_release_build_toolchain_inputs(
                lock_path=lock,
                metadata_path=metadata,
                wheelhouse=wheelhouse,
            )
            runtime = {
                "python": verified["toolchain"]["python"],
                "uv": verified["toolchain"]["uv"],
                "native_toolchain": {
                    "boundary": "apple-xcode-clang-external-recorded-not-hash-bound-v1",
                    "developer_selection": "selected-full-xcode",
                    "xcode_version": "16.2",
                    "xcode_build_version": "16C5032a",
                    "clang_version": "Apple clang version 16.0.0",
                    "clang_binary_sha256": "c" * 64,
                    "clang_selection": "xcrun--find-clang",
                    "sealed_path_policy": (
                        "prepared-toolchain-bin-plus-apple-system-tools-v1"
                    ),
                },
            }
            python = root / "python"
            uv = root / "uv"
            python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            python.chmod(0o755)
            uv.chmod(0o755)
            work_directory = root / "work"
            work_directory.mkdir()

            with patch(
                "scripts.release_build_toolchain.verify_release_build_toolchain_runtime",
                return_value=runtime,
            ), patch(
                "scripts.release_build_toolchain._run",
                side_effect=AssertionError("venv creation must not be reached"),
            ), patch(
                "scripts.release_build_toolchain.os.getuid",
                return_value=os.getuid() + 1,
            ), self.assertRaisesRegex(
                ReleaseBuildToolchainError, "work directory.*owned by the current user"
            ):
                prepare_release_build_toolchain(
                    lock_path=lock,
                    metadata_path=metadata,
                    wheelhouse=wheelhouse,
                    python_executable=python,
                    uv_executable=uv,
                    work_directory=work_directory,
                    receipt_path=root / "release-build-toolchain-receipt.json",
                    **self._bootstrap_kwargs(root),
                )

    def test_preparation_rejects_group_writable_receipt_parent_before_publish(self) -> None:
        """The atomic receipt replacement must not target a shared directory."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, metadata, wheelhouse = self._fixture(root)
            verified = verify_release_build_toolchain_inputs(
                lock_path=lock,
                metadata_path=metadata,
                wheelhouse=wheelhouse,
            )
            expected_versions = {
                name: entry["version"]
                for name, entry in verified["wheel_inputs"].items()
            }
            runtime = {
                "python": verified["toolchain"]["python"],
                "uv": verified["toolchain"]["uv"],
                "native_toolchain": {
                    "boundary": "apple-xcode-clang-external-recorded-not-hash-bound-v1",
                    "developer_selection": "selected-full-xcode",
                    "xcode_version": "16.2",
                    "xcode_build_version": "16C5032a",
                    "clang_version": "Apple clang version 16.0.0",
                    "clang_binary_sha256": "c" * 64,
                    "clang_selection": "xcrun--find-clang",
                    "sealed_path_policy": (
                        "prepared-toolchain-bin-plus-apple-system-tools-v1"
                    ),
                },
            }
            python = root / "python"
            uv = root / "uv"
            python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            python.chmod(0o755)
            uv.chmod(0o755)
            work_directory = root / "work"
            work_directory.mkdir()
            receipt_parent = root / "receipt-parent"
            receipt_parent.mkdir()
            receipt_parent.chmod(0o770)
            receipt = receipt_parent / "release-build-toolchain-receipt.json"

            def fake_run(command: list[str], *, label: str) -> None:
                if len(command) >= 2 and command[1] == "venv":
                    venv = Path(command[-1])
                    venv_bin = venv / "bin"
                    venv_bin.mkdir(parents=True)
                    venv_python = venv_bin / "python"
                    venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    venv_python.chmod(0o755)
                    (venv / "pyvenv.cfg").write_text(
                        "include-system-site-packages = false\n", encoding="utf-8"
                    )

            with patch(
                "scripts.release_build_toolchain.verify_release_build_toolchain_runtime",
                return_value=runtime,
            ), patch(
                "scripts.release_build_toolchain._run", side_effect=fake_run
            ), patch(
                "scripts.release_build_toolchain._installed_versions",
                return_value=expected_versions,
            ), patch(
                "scripts.release_build_toolchain._verify_prepared_pip"
            ), self.assertRaisesRegex(
                ReleaseBuildToolchainError, "receipt parent.*group- or other-writable"
            ):
                prepare_release_build_toolchain(
                    lock_path=lock,
                    metadata_path=metadata,
                    wheelhouse=wheelhouse,
                    python_executable=python,
                    uv_executable=uv,
                    work_directory=work_directory,
                    receipt_path=receipt,
                    **self._bootstrap_kwargs(root),
                )

            self.assertFalse(receipt.exists())
            self.assertEqual(list(work_directory.glob("prepared-venv-*")), [])
