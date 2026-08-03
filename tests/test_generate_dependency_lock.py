from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import generate_macos_arm64_py312_lock as lock_generator
from scripts.generate_macos_arm64_py312_lock import (
    ApprovedRepairedWheelInput,
    LockGenerationError,
    ResolverHost,
    _clean_pip_environment,
    generate_canonical_dependency_lock,
    load_approved_repaired_wheels,
)
from scripts.repair_macos_release_dependency_wheels import WheelSpec
from scripts.verify_release_input_readiness import (
    CANONICAL_TARGET_COMPATIBILITY,
    verify_canonical_dependency_lock,
)


ROOT = Path(__file__).resolve().parents[1]


class DependencyLockGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        repaired = {
            "open3d": ApprovedRepairedWheelInput(
                distribution="open3d",
                version="0.19.0",
                path=Path("/test/open3d.whl"),
                sha256=(
                    "b71b3ffd13427a01a6d1caab8af98d6dc9d1eb3c60ce2b32cbe4ce602168153d"
                ),
            ),
        }
        loader = patch(
            "scripts.generate_macos_arm64_py312_lock.load_approved_repaired_wheels",
            return_value=repaired,
        )
        loader.start()
        self.addCleanup(loader.stop)

    def test_approved_repair_manifest_binds_exact_local_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repair"
            wheels = root / "wheels"
            wheels.mkdir(parents=True)
            open3d = wheels / "open3d.whl"
            open3d.write_bytes(b"open3d-repaired")
            open3d_sha = hashlib.sha256(open3d.read_bytes()).hexdigest()
            specs = {
                "open3d": (
                    WheelSpec("open3d", "open3d.whl", "b" * 64, open3d_sha, "open3d.dist-info"),
                    "0.19.0",
                    3,
                ),
            }
            manifest = {
                "schema": lock_generator.SIGNED_REPAIR_SCHEMA,
                "policy": lock_generator.SIGNED_REPAIR_POLICY,
                "target": lock_generator.APPROVED_REPAIR_TARGET,
                "wheel": {
                    "distribution": "open3d",
                    "input": {
                        "filename": specs["open3d"][0].filename,
                        "sha256": specs["open3d"][0].repaired_sha256,
                        "repair_manifest_sha256": "c" * 64,
                    },
                    "operations": {
                        "developer_id_signatures": 3,
                        "codesign_identity": "Developer ID Application",
                        "codesign_team_identifier": lock_generator.RELEASE_TEAM_IDENTIFIER,
                        "codesign_timestamp": "secure",
                        "codesign_options": "runtime",
                    },
                    "output": {
                        "filename": specs["open3d"][0].filename,
                        "macho_count": specs["open3d"][2],
                        "sha256": specs["open3d"][0].repaired_sha256,
                        "size_bytes": open3d.stat().st_size,
                    },
                },
            }
            (root / "repair-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with patch.dict(
                lock_generator.APPROVED_REPAIRED_WHEEL_SPECS,
                specs,
                clear=True,
            ), patch(
                "scripts.generate_macos_arm64_py312_lock.verify_rewritten_open3d_wheel",
                return_value=manifest,
            ):
                approved = load_approved_repaired_wheels(root)
                self.assertEqual(approved["open3d"].sha256, open3d_sha)
                open3d.write_bytes(b"tampered")
                with self.assertRaisesRegex(LockGenerationError, "size mismatch"):
                    load_approved_repaired_wheels(root)

    def test_approved_repair_directory_is_required(self) -> None:
        with self.assertRaisesRegex(LockGenerationError, "required"):
            load_approved_repaired_wheels(None)

    def _fixture(self, root: Path) -> tuple[Path, Path, Path, Path, Path]:
        constraints_dir = root / "constraints"
        constraints_dir.mkdir()
        constraints = constraints_dir / "macos-arm64-py312.txt"
        constraints.write_text(
            "demo>=1,<2\nacvl-utils==0.2.6\nfpsample==1.0.2\n",
            encoding="utf-8",
        )
        lock = constraints_dir / "macos-arm64-py312.requirements.lock"
        metadata = constraints_dir / "macos-arm64-py312.lock.json"
        project = root / "pyproject.toml"
        project.write_text("[project]\nname = 'fixture'\nversion = '0'\n", encoding="utf-8")
        setup_manager = root / "setup_manager.py"
        setup_manager.write_text(
            "def validate_safe_command(command):\n"
            "    return command\n\n"
            "def build_locked_dependencies_install_command(venv_python, *, requirements_lock, wheel_directory):\n"
            "    command = [str(venv_python), '-I', '-m', 'pip', '--isolated', 'install', '--require-hashes', '--no-deps', '--only-binary', ':all:', '--find-links', str(wheel_directory), '-r', str(requirements_lock)]\n"
            "    validate_safe_command(command)\n"
            "    return command\n\n"
            "def _execute_step(name, command):\n"
            "    return name, command\n\n"
            "def run_setup(venv_python, requirements_lock, wheel_directory, allow_network):\n"
            "    if allow_network and requirements_lock is not None:\n"
            "        return _execute_step('install_locked_dependencies', build_locked_dependencies_install_command(venv_python, requirements_lock=requirements_lock, wheel_directory=wheel_directory))\n",
            encoding="utf-8",
        )
        return constraints, lock, metadata, project, setup_manager

    @staticmethod
    def _write_resolution_wheel(
        path: Path,
        *,
        dist_info: str,
        name: str,
        version: str,
        tag: str,
    ) -> None:
        """Write the minimal, valid wheel metadata needed by the resolver check."""

        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                f"{dist_info}/METADATA",
                "Metadata-Version: 2.1\n"
                f"Name: {name}\n"
                f"Version: {version}\n",
            )
            archive.writestr(
                f"{dist_info}/WHEEL",
                "Wheel-Version: 1.0\n"
                "Generator: test\n"
                "Root-Is-Purelib: true\n"
                f"Tag: {tag}\n",
            )
            archive.writestr(f"{dist_info}/RECORD", "")

    def _resolution_wheel_directory(
        self,
        root: Path,
        *,
        acvl_name: str = "acvl_utils",
        acvl_version: str = "0.2.6",
        acvl_tag: str = "py3-none-any",
        fpsample_name: str = "fpsample",
        fpsample_version: str = "1.0.2",
        fpsample_tag: str = "cp312-cp312-macosx_13_0_arm64",
    ) -> Path:
        wheelhouse = root / "resolver-wheels"
        wheelhouse.mkdir()
        self._write_resolution_wheel(
            wheelhouse / "acvl_utils-0.2.6-py3-none-any.whl",
            dist_info="acvl_utils-0.2.6.dist-info",
            name=acvl_name,
            version=acvl_version,
            tag=acvl_tag,
        )
        self._write_resolution_wheel(
            wheelhouse / "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl",
            dist_info="fpsample-1.0.2.dist-info",
            name=fpsample_name,
            version=fpsample_version,
            tag=fpsample_tag,
        )
        return wheelhouse

    def _pre_sign_wheel_receipt(self, root: Path, wheelhouse: Path) -> Path:
        """Fixture equivalent of the sealed bootstrap handoff.

        The canonical resolver must receive the two pre-sign wheel identities
        from this artifact, rather than inventing a hash before either wheel
        has been built.
        """

        receipt = root / "pre-sign-wheels.json"
        wheels: dict[str, dict[str, str]] = {}
        for name, filename, version, tag in (
            ("acvl-utils", "acvl_utils-0.2.6-py3-none-any.whl", "0.2.6", "py3-none-any"),
            (
                "fpsample",
                "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl",
                "1.0.2",
                "cp312-cp312-macosx_13_0_arm64",
            ),
        ):
            wheel = wheelhouse / filename
            with zipfile.ZipFile(wheel) as archive:
                dist_info = (
                    "acvl_utils-0.2.6.dist-info"
                    if name == "acvl-utils"
                    else "fpsample-1.0.2.dist-info"
                )
                metadata = archive.read(f"{dist_info}/METADATA")
                wheel_metadata = archive.read(f"{dist_info}/WHEEL")
            wheels[name] = {
                "filename": filename,
                "version": version,
                "wheel_tag": tag,
                "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "metadata_sha256": hashlib.sha256(metadata).hexdigest(),
                "wheel_metadata_sha256": hashlib.sha256(wheel_metadata).hexdigest(),
            }
        receipt.write_text(
            json.dumps(
                {
                    "schema": "totalsegmentator_wrapper_mac.release_pre_sign_wheel_receipt.v1",
                    "source_identity_sha256": "1" * 64,
                    "sealed_toolchain": {
                        "lock_sha256": "2" * 64,
                        "metadata_sha256": "3" * 64,
                        "receipt_sha256": "4" * 64,
                    },
                    "component_receipt_sha256": {
                        "acvl-utils": "5" * 64,
                        "fpsample": "6" * 64,
                    },
                    "wheels": wheels,
                }
            ),
            encoding="utf-8",
        )
        return receipt

    @staticmethod
    def _macos14_host() -> ResolverHost:
        return ResolverHost(
            system="Darwin",
            machine="arm64",
            python_implementation="CPython",
            python_version=(3, 12, 11),
            macos_version="14.7.8",
            sysconfig_platform="macosx-14.0-arm64",
        )

    @staticmethod
    def _fake_atomic_directory_swap(live: Path, staged: Path) -> None:
        """Test double for Darwin renameatx_np(RENAME_SWAP)."""
        retired = live.parent / ".retired-constraints"
        os.rename(live, retired)
        os.rename(staged, live)
        os.rename(retired, staged)

    def _successful_runner(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.assertEqual(command[:5], [
            os.environ.get("PYTHON_FOR_TEST", command[0]),
            "-I",
            "-m",
            "piptools",
            "compile",
        ])
        self.assertIn("--generate-hashes", command)
        self.assertIn("--rebuild", command)
        self.assertIn("--resolver=backtracking", command)
        self.assertIn("--allow-unsafe", command)
        self.assertIn("--no-config", command)
        self.assertIn("--index-url", command)
        self.assertEqual(command[command.index("--index-url") + 1], "https://pypi.org/simple")
        self.assertIn("--find-links", command)
        wheelhouse = Path(command[command.index("--find-links") + 1])
        self.assertEqual(wheelhouse.name, "bundled-override-resolver-wheels")
        self.assertTrue(wheelhouse.parent.name.startswith(".dependency-lock-compile-"))
        self.assertEqual(
            sorted(path.name for path in wheelhouse.iterdir()),
            [
                "acvl_utils-0.2.6-py3-none-any.whl",
                "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl",
            ],
        )
        self.assertTrue(
            (wheelhouse / "acvl_utils-0.2.6-py3-none-any.whl").is_file()
        )
        self.assertTrue(
            (wheelhouse / "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl").is_file()
        )
        self.assertIn(
            "--pip-args=--isolated --only-binary=:all: "
            "--platform macosx_14_0_arm64 --implementation cp "
            "--python-version 3.12 --abi cp312",
            command,
        )
        self.assertIn("--no-emit-options", command)
        self.assertIn("--no-emit-index-url", command)
        self.assertIn("--no-emit-trusted-host", command)
        self.assertIn("--strip-extras", command)
        self.assertEqual(command.count("--extra"), 5)
        self.assertEqual(command.count("--constraint"), 2)
        constraint_paths = [
            Path(command[index + 1])
            for index, token in enumerate(command)
            if token == "--constraint"
        ]
        direct_by_name = {
            line.split(" @ ", 1)[0]: line
            for line in constraint_paths[1].read_text(encoding="utf-8").splitlines()
        }
        self.assertEqual(set(direct_by_name), {"acvl-utils", "fpsample"})
        environment = kwargs["env"]
        self.assertIsInstance(environment, dict)
        assert isinstance(environment, dict)
        self.assertEqual(environment["PIP_CONFIG_FILE"], os.devnull)
        self.assertEqual(environment["PIP_ONLY_BINARY"], ":all:")
        self.assertNotIn("PIP_TARGET", environment)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("PYTHONHOME", environment)
        output = Path(command[command.index("--output-file") + 1])
        output.write_text(
            "demo==1.5.0 --hash=sha256:" + "a" * 64 + "\\\n"
            "    --hash=sha256:" + "b" * 64 + "\n"
            "dependency==2.0.0 --hash=sha256:" + "c" * 64 + "\n"
            "imagecodecs==2026.6.26 --hash=sha256:"
            "2d3298028a74d748e5b7a00bd736d41cdf2372861376e4af916818e853ca5fc6\n"
            "open3d==0.19.0 --hash=sha256:"
            "9e4a8d29443ba4c83010d199d56c96bf553dd970d3351692ab271759cbe2d7ac\n"
            "setuptools==81.0.0 --hash=sha256:" + "d" * 64 + "\n"
            + direct_by_name["acvl-utils"]
            + " --hash=sha256:"
            + hashlib.sha256(
                Path(direct_by_name["acvl-utils"].split("file://", 1)[1]).read_bytes()
            ).hexdigest()
            + "\n"
            + direct_by_name["fpsample"]
            + " --hash=sha256:"
            + hashlib.sha256(
                Path(direct_by_name["fpsample"].split("file://", 1)[1]).read_bytes()
            ).hexdigest()
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_resolver_environment_ignores_hostile_pip_and_python_overrides(self) -> None:
        hostile = {
            "PIP_TARGET": "/private/hostile-target",
            "PIP_INDEX_URL": "https://hostile.invalid/simple",
            "PIP_CONFIG_FILE": "/private/hostile-pip.conf",
            "PIP_TOOLS_CACHE_DIR": "/private/hostile-pip-tools-cache",
            "PYTHONPATH": "/private/hostile-pythonpath",
            "PYTHONHOME": "/private/hostile-pythonhome",
            "PYTHONUSERBASE": "/private/hostile-userbase",
        }
        with patch.dict(os.environ, hostile, clear=False):
            environment = _clean_pip_environment()

        for key in hostile:
            if key == "PIP_CONFIG_FILE":
                continue
            self.assertNotIn(key, environment)
        self.assertEqual(environment["PIP_CONFIG_FILE"], os.devnull)
        self.assertEqual(environment["PIP_NO_INPUT"], "1")
        self.assertEqual(environment["PIP_ONLY_BINARY"], ":all:")

    def test_rejects_pre_macos14_host_before_running_pip_compile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            constraints, lock, metadata, project, setup_manager = self._fixture(Path(tmp))
            calls: list[list[str]] = []

            with self.assertRaisesRegex(LockGenerationError, "macOS 14"):
                generate_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=lock,
                    lock_metadata=metadata,
                    project_file=project,
                    setup_manager_source=setup_manager,
                    host=ResolverHost(
                        system="Darwin",
                        machine="arm64",
                        python_implementation="CPython",
                        python_version=(3, 12, 11),
                        macos_version="13.7.3",
                        sysconfig_platform="macosx-13.0-arm64",
                    ),
                    pip_tools_version="7.5.0",
                    pip_version="25.1.1",
                    runner=lambda command, **kwargs: calls.append(command),
                    directory_swap=self._fake_atomic_directory_swap,
                )

            self.assertEqual(calls, [])
            self.assertFalse(lock.exists())
            self.assertFalse(metadata.exists())

    def test_accepts_macos26_host_with_explicit_macos14_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints, lock, metadata, project, setup_manager = self._fixture(root)
            wheelhouse = self._resolution_wheel_directory(root)
            result = generate_canonical_dependency_lock(
                constraints=constraints,
                requirements_lock=lock,
                lock_metadata=metadata,
                project_file=project,
                setup_manager_source=setup_manager,
                bundled_override_wheel_directory=wheelhouse,
                host=ResolverHost(
                    system="Darwin",
                    machine="arm64",
                    python_implementation="CPython",
                    python_version=(3, 12, 11),
                    macos_version="26.6",
                    sysconfig_platform="macosx-11.0-arm64",
                ),
                pip_tools_version="7.5.0",
                pip_version="25.1.1",
                runner=self._successful_runner,
                directory_swap=self._fake_atomic_directory_swap,
            )
            self.assertEqual(result["resolver"]["macos_version"], "26.6")
            self.assertEqual(result["resolver"]["sysconfig_platform"], "macosx-11.0-arm64")
            self.assertEqual(result["resolver"]["target_compatibility"], CANONICAL_TARGET_COMPATIBILITY)

    def test_rejects_wrong_pip_compile_version_before_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            constraints, lock, metadata, project, setup_manager = self._fixture(Path(tmp))
            calls: list[list[str]] = []

            with self.assertRaisesRegex(LockGenerationError, "pip-tools 7.5.0"):
                generate_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=lock,
                    lock_metadata=metadata,
                    project_file=project,
                    setup_manager_source=setup_manager,
                    host=self._macos14_host(),
                    pip_tools_version="7.5.1",
                    pip_version="25.1.1",
                    runner=lambda command, **kwargs: calls.append(command),
                    directory_swap=self._fake_atomic_directory_swap,
                )

            self.assertEqual(calls, [])

    def test_requires_explicit_local_bundled_override_resolution_wheels(self) -> None:
        """The release resolver must not depend on unavailable PyPI override wheels."""

        with tempfile.TemporaryDirectory() as tmp:
            constraints, lock, metadata, project, setup_manager = self._fixture(Path(tmp))
            calls: list[list[str]] = []

            with self.assertRaisesRegex(
                LockGenerationError,
                "explicit local bundled override resolution wheel",
            ):
                generate_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=lock,
                    lock_metadata=metadata,
                    project_file=project,
                    setup_manager_source=setup_manager,
                    host=self._macos14_host(),
                    pip_tools_version="7.5.0",
                    pip_version="25.1.1",
                    runner=lambda command, **_: calls.append(command),
                    directory_swap=self._fake_atomic_directory_swap,
                )

            self.assertEqual(calls, [])

    def test_explicit_local_override_constraints_bind_the_resolved_wheels(self) -> None:
        """Equal PyPI candidates must not be able to replace local overrides.

        A ``--find-links`` directory only exposes candidates; it does not make
        one candidate authoritative when an equally-versioned artifact is also
        visible from the index.  The resolver command must therefore carry a
        second, generated direct-reference constraint file for the two local
        override wheels.  The temporary direct references are allowed only in
        the complete resolver output and must be removed before publication.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints, lock, metadata, project, setup_manager = self._fixture(root)
            wheelhouse = self._resolution_wheel_directory(root)

            def direct_reference_runner(
                command: list[str], **_: object
            ) -> subprocess.CompletedProcess[str]:
                constraint_paths = [
                    Path(command[index + 1])
                    for index, token in enumerate(command)
                    if token == "--constraint"
                ]
                self.assertEqual(len(constraint_paths), 2)
                direct_constraints = constraint_paths[1]
                self.assertTrue(direct_constraints.is_file())
                direct_lines = direct_constraints.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(direct_lines), 2)
                direct_by_name = {
                    line.split(" @ ", 1)[0]: line
                    for line in direct_lines
                }
                self.assertEqual(set(direct_by_name), {"acvl-utils", "fpsample"})
                for line in direct_lines:
                    self.assertIn(" @ file://", line)
                    self.assertIn("bundled-override-resolver-wheels", line)

                output = Path(command[command.index("--output-file") + 1])
                output.write_text(
                    "demo==1.5.0 --hash=sha256:" + "a" * 64 + "\n"
                    "dependency==2.0.0 --hash=sha256:" + "b" * 64 + "\n"
                    "imagecodecs==2026.6.26 --hash=sha256:"
                    "2d3298028a74d748e5b7a00bd736d41cdf2372861376e4af916818e853ca5fc6\n"
                    "open3d==0.19.0 --hash=sha256:"
                    "9e4a8d29443ba4c83010d199d56c96bf553dd970d3351692ab271759cbe2d7ac\n"
                    + direct_by_name["acvl-utils"]
                    + " --hash=sha256:"
                    + hashlib.sha256(
                        Path(
                            direct_by_name["acvl-utils"].split("file://", 1)[1]
                        ).read_bytes()
                    ).hexdigest()
                    + "\n"
                    + direct_by_name["fpsample"]
                    + " --hash=sha256:"
                    + hashlib.sha256(
                        Path(
                            direct_by_name["fpsample"].split("file://", 1)[1]
                        ).read_bytes()
                    ).hexdigest()
                    + "\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            generate_canonical_dependency_lock(
                constraints=constraints,
                requirements_lock=lock,
                lock_metadata=metadata,
                project_file=project,
                setup_manager_source=setup_manager,
                bundled_override_wheel_directory=wheelhouse,
                host=self._macos14_host(),
                pip_tools_version="7.5.0",
                pip_version="25.1.1",
                runner=direct_reference_runner,
                directory_swap=self._fake_atomic_directory_swap,
            )

            published = lock.read_text(encoding="utf-8")
            self.assertNotIn("file://", published)
            self.assertNotIn("acvl-utils", published)
            self.assertNotIn("fpsample", published)

    def test_generates_valid_pair_from_staging_and_publishes_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints, lock, metadata, project, setup_manager = self._fixture(root)
            unrelated = constraints.parent / "keep.txt"
            unrelated.write_text("keep", encoding="utf-8")
            lock.write_text("old==1 --hash=sha256:" + "d" * 64 + "\n", encoding="utf-8")
            metadata.write_text("{\"old\": true}\n", encoding="utf-8")
            source_sha256 = hashlib.sha256(constraints.read_bytes()).hexdigest()
            wheelhouse = self._resolution_wheel_directory(root)
            # `dist/` commonly also has the wrapper wheel. It must not become
            # an unreviewed --find-links candidate for another dependency.
            (wheelhouse / "unrelated-9.9.9-py3-none-any.whl").write_bytes(
                b"not a resolver input"
            )
            result = generate_canonical_dependency_lock(
                constraints=constraints,
                requirements_lock=lock,
                lock_metadata=metadata,
                project_file=project,
                setup_manager_source=setup_manager,
                bundled_override_wheel_directory=wheelhouse,
                host=self._macos14_host(),
                pip_tools_version="7.5.0",
                pip_version="25.1.1",
                runner=self._successful_runner,
                directory_swap=self._fake_atomic_directory_swap,
            )

            payload = json.loads(metadata.read_text(encoding="utf-8"))
            self.assertEqual(payload["constraints_sha256"], source_sha256)
            self.assertEqual(payload["requirements_lock_sha256"], hashlib.sha256(lock.read_bytes()).hexdigest())
            self.assertEqual(
                payload["resolved_distribution_names"],
                [
                    "acvl-utils",
                    "demo",
                    "dependency",
                    "fpsample",
                    "imagecodecs",
                    "open3d",
                    "setuptools",
                ],
            )
            self.assertEqual(
                payload["install_distribution_names"],
                ["demo", "dependency", "imagecodecs", "open3d", "setuptools"],
            )
            self.assertEqual(set(payload["excluded_bundled_overrides"]), {
                "acvl-utils",
                "fpsample",
            })
            self.assertNotIn("acvl-utils==", lock.read_text(encoding="utf-8"))
            self.assertNotIn("fpsample==", lock.read_text(encoding="utf-8"))
            self.assertIn("dependency==2.0.0", lock.read_text(encoding="utf-8"))
            self.assertIn("setuptools==81.0.0", lock.read_text(encoding="utf-8"))
            self.assertIn(
                "imagecodecs==2026.6.26 --hash=sha256:"
                "2d3298028a74d748e5b7a00bd736d41cdf2372861376e4af916818e853ca5fc6",
                lock.read_text(encoding="utf-8"),
            )
            self.assertIn(
                "open3d==0.19.0 --hash=sha256:"
                "b71b3ffd13427a01a6d1caab8af98d6dc9d1eb3c60ce2b32cbe4ce602168153d",
                lock.read_text(encoding="utf-8"),
            )
            self.assertNotIn(
                "9e4a8d29443ba4c83010d199d56c96bf553dd970d3351692ab271759cbe2d7ac",
                lock.read_text(encoding="utf-8"),
            )
            self.assertEqual(payload["resolver"], {
                "name": "pip-compile",
                "version": "7.5.0",
                "platform": "macos-14-arm64",
                "python": "3.12",
                "pip_version": "25.1.1",
                "python_full_version": "3.12.11",
                "macos_version": "14.7.8",
                "sysconfig_platform": "macosx-14.0-arm64",
                "target_compatibility": CANONICAL_TARGET_COMPATIBILITY,
            })
            self.assertRegex(payload["generation_id"], r"^[0-9a-f-]{36}$")
            self.assertIn(
                f"dependency_lock_generation_id: {payload['generation_id']}",
                lock.read_text(encoding="utf-8"),
            )
            self.assertEqual(result, payload)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
            verify_canonical_dependency_lock(
                constraints=constraints,
                requirements_lock=lock,
                lock_metadata=metadata,
                project_file=project,
                setup_manager_source=setup_manager,
            )
            self.assertEqual(list(root.glob(".constraints.lock-*")), [])

    def test_rejects_project_change_during_resolution_and_preserves_live_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints, lock, metadata, project, setup_manager = self._fixture(root)
            old_lock = "old==1 --hash=sha256:" + "d" * 64 + "\n"
            old_metadata = "{\"old\": true}\n"
            lock.write_text(old_lock, encoding="utf-8")
            metadata.write_text(old_metadata, encoding="utf-8")
            wheelhouse = self._resolution_wheel_directory(root)

            def project_mutating_runner(
                command: list[str], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                result = self._successful_runner(command, **kwargs)
                project.write_text(
                    "[project]\nname = 'fixture'\nversion = '0'\n"
                    "dependencies = ['new-unlocked-dependency>=1']\n",
                    encoding="utf-8",
                )
                return result

            with self.assertRaisesRegex(
                LockGenerationError,
                "project dependency declarations changed while resolving",
            ):
                generate_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=lock,
                    lock_metadata=metadata,
                    project_file=project,
                    setup_manager_source=setup_manager,
                    bundled_override_wheel_directory=wheelhouse,
                    host=self._macos14_host(),
                    pip_tools_version="7.5.0",
                    pip_version="25.1.1",
                    runner=project_mutating_runner,
                    directory_swap=self._fake_atomic_directory_swap,
                )

            self.assertEqual(lock.read_text(encoding="utf-8"), old_lock)
            self.assertEqual(metadata.read_text(encoding="utf-8"), old_metadata)
            self.assertEqual(list(root.glob(".constraints.lock-*")), [])

    def test_rejects_duplicate_generated_distribution_and_preserves_live_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints, lock, metadata, project, setup_manager = self._fixture(root)
            old_lock = "old==1 --hash=sha256:" + "d" * 64 + "\n"
            old_metadata = "{\"old\": true}\n"
            lock.write_text(old_lock, encoding="utf-8")
            metadata.write_text(old_metadata, encoding="utf-8")
            wheelhouse = self._resolution_wheel_directory(root)

            def duplicate_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                direct_by_name = {
                    line.split(" @ ", 1)[0]: line
                    for line in Path(
                        [
                            command[index + 1]
                            for index, token in enumerate(command)
                            if token == "--constraint"
                        ][1]
                    )
                    .read_text(encoding="utf-8")
                    .splitlines()
                }
                output = Path(command[command.index("--output-file") + 1])
                output.write_text(
                    "demo==1.5.0 --hash=sha256:" + "a" * 64 + "\n"
                    "demo==1.5.0 --hash=sha256:" + "b" * 64 + "\n"
                    + direct_by_name["acvl-utils"]
                    + " --hash=sha256:"
                    + hashlib.sha256(
                        Path(direct_by_name["acvl-utils"].split("file://", 1)[1]).read_bytes()
                    ).hexdigest()
                    + "\n"
                    + direct_by_name["fpsample"]
                    + " --hash=sha256:"
                    + hashlib.sha256(
                        Path(direct_by_name["fpsample"].split("file://", 1)[1]).read_bytes()
                    ).hexdigest()
                    + "\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            with self.assertRaisesRegex(LockGenerationError, "duplicate distribution"):
                generate_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=lock,
                    lock_metadata=metadata,
                    project_file=project,
                    setup_manager_source=setup_manager,
                    bundled_override_wheel_directory=wheelhouse,
                    host=self._macos14_host(),
                    pip_tools_version="7.5.0",
                    pip_version="25.1.1",
                    runner=duplicate_runner,
                    directory_swap=self._fake_atomic_directory_swap,
                )

            self.assertEqual(lock.read_text(encoding="utf-8"), old_lock)
            self.assertEqual(metadata.read_text(encoding="utf-8"), old_metadata)

    def test_rejects_regular_override_pins_when_direct_references_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints, lock, metadata, project, setup_manager = self._fixture(root)
            old_lock = "old==1 --hash=sha256:" + "d" * 64 + "\n"
            old_metadata = "{\"old\": true}\n"
            lock.write_text(old_lock, encoding="utf-8")
            metadata.write_text(old_metadata, encoding="utf-8")
            wheelhouse = self._resolution_wheel_directory(root)

            def mismatched_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                output = Path(command[command.index("--output-file") + 1])
                output.write_text(
                    "demo==1.5.0 --hash=sha256:" + "a" * 64 + "\n"
                    "acvl-utils==0.2.7 --hash=sha256:" + "b" * 64 + "\n"
                    "fpsample==1.0.2 --hash=sha256:" + "c" * 64 + "\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            with self.assertRaisesRegex(
                LockGenerationError,
                "did not retain exactly the staged bundled override",
            ):
                generate_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=lock,
                    lock_metadata=metadata,
                    project_file=project,
                    setup_manager_source=setup_manager,
                    bundled_override_wheel_directory=wheelhouse,
                    host=self._macos14_host(),
                    pip_tools_version="7.5.0",
                    pip_version="25.1.1",
                    runner=mismatched_runner,
                    directory_swap=self._fake_atomic_directory_swap,
                )

            self.assertEqual(lock.read_text(encoding="utf-8"), old_lock)
            self.assertEqual(metadata.read_text(encoding="utf-8"), old_metadata)

    def test_rejects_symlinked_existing_output_before_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints, lock, metadata, project, setup_manager = self._fixture(root)
            external = root / "external.lock"
            external.write_text("external", encoding="utf-8")
            lock.symlink_to(external)
            wheelhouse = self._resolution_wheel_directory(root)
            calls: list[list[str]] = []

            with self.assertRaisesRegex(LockGenerationError, "non-symlink"):
                generate_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=lock,
                    lock_metadata=metadata,
                    project_file=project,
                    setup_manager_source=setup_manager,
                    bundled_override_wheel_directory=wheelhouse,
                    host=self._macos14_host(),
                    pip_tools_version="7.5.0",
                    pip_version="25.1.1",
                    runner=lambda command, **kwargs: calls.append(command),
                    directory_swap=self._fake_atomic_directory_swap,
                )

            self.assertEqual(calls, [])

    def test_rejects_incompatible_local_bundled_override_wheel_before_pip_compile(self) -> None:
        """Name, version, tag, and symlink checks are all enforced locally."""

        variants = (
            ("name", {"fpsample_name": "wrong-fpsample"}),
            ("version", {"fpsample_version": "1.0.3"}),
            ("tag", {"fpsample_tag": "cp312-cp312-macosx_14_0_arm64"}),
        )
        for label, overrides in variants:
            with self.subTest(variant=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                constraints, lock, metadata, project, setup_manager = self._fixture(root)
                wheelhouse = self._resolution_wheel_directory(root, **overrides)
                calls: list[list[str]] = []
                with self.assertRaisesRegex(
                    LockGenerationError,
                    "name/version/tag mismatch",
                ):
                    generate_canonical_dependency_lock(
                        constraints=constraints,
                        requirements_lock=lock,
                        lock_metadata=metadata,
                        project_file=project,
                        setup_manager_source=setup_manager,
                        bundled_override_wheel_directory=wheelhouse,
                        host=self._macos14_host(),
                        pip_tools_version="7.5.0",
                        pip_version="25.1.1",
                        runner=lambda command, **_: calls.append(command),
                        directory_swap=self._fake_atomic_directory_swap,
                    )
                self.assertEqual(calls, [])

    def test_rejects_symlinked_local_bundled_override_wheel_before_pip_compile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints, lock, metadata, project, setup_manager = self._fixture(root)
            wheelhouse = self._resolution_wheel_directory(root)
            wheel = wheelhouse / "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl"
            external = root / "fpsample.wheel"
            wheel.replace(external)
            wheel.symlink_to(external)
            calls: list[list[str]] = []
            with self.assertRaisesRegex(LockGenerationError, "regular non-symlink"):
                generate_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=lock,
                    lock_metadata=metadata,
                    project_file=project,
                    setup_manager_source=setup_manager,
                    bundled_override_wheel_directory=wheelhouse,
                    host=self._macos14_host(),
                    pip_tools_version="7.5.0",
                    pip_version="25.1.1",
                    runner=lambda command, **_: calls.append(command),
                    directory_swap=self._fake_atomic_directory_swap,
                )
            self.assertEqual(calls, [])

    def test_rejects_wheelhouse_without_the_exact_expected_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints, lock, metadata, project, setup_manager = self._fixture(root)
            wheelhouse = self._resolution_wheel_directory(root)
            expected = wheelhouse / "acvl_utils-0.2.6-py3-none-any.whl"
            expected.rename(wheelhouse / "acvl_utils-0.2.6-renamed.whl")
            calls: list[list[str]] = []
            with self.assertRaisesRegex(
                LockGenerationError,
                "bundled override resolution wheel for acvl-utils is missing",
            ):
                generate_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=lock,
                    lock_metadata=metadata,
                    project_file=project,
                    setup_manager_source=setup_manager,
                    bundled_override_wheel_directory=wheelhouse,
                    host=self._macos14_host(),
                    pip_tools_version="7.5.0",
                    pip_version="25.1.1",
                    runner=lambda command, **_: calls.append(command),
                    directory_swap=self._fake_atomic_directory_swap,
                )
            self.assertEqual(calls, [])

    def test_rejects_pip_compile_output_that_leaks_local_resolution_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints, lock, metadata, project, setup_manager = self._fixture(root)
            wheelhouse = self._resolution_wheel_directory(root)

            def leaked_path_runner(
                command: list[str], **_: object
            ) -> subprocess.CompletedProcess[str]:
                output = Path(command[command.index("--output-file") + 1])
                output.write_text(
                    "demo==1.5.0 --hash=sha256:" + "a" * 64 + "\n"
                    "dependency==2.0.0 --hash=sha256:" + "b" * 64 + "\n"
                    "acvl-utils @ file:///private/not-portable/acvl_utils.whl "
                    "--hash=sha256:" + "c" * 64 + "\n"
                    "fpsample==1.0.2 --hash=sha256:" + "d" * 64 + "\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            with self.assertRaisesRegex(LockGenerationError, "unapproved local direct reference"):
                generate_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=lock,
                    lock_metadata=metadata,
                    project_file=project,
                    setup_manager_source=setup_manager,
                    bundled_override_wheel_directory=wheelhouse,
                    host=self._macos14_host(),
                    pip_tools_version="7.5.0",
                    pip_version="25.1.1",
                    runner=leaked_path_runner,
                    directory_swap=self._fake_atomic_directory_swap,
                )
