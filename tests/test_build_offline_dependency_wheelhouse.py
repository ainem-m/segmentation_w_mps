from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_offline_dependency_wheelhouse import (
    BUNDLED_OVERRIDE_SPECS,
    OfflineWheelhouseError,
    TARGET_ABI,
    TARGET_IMPLEMENTATION,
    TARGET_PLATFORM,
    TargetHost,
    build_offline_dependency_wheelhouse,
    parse_hashed_requirements_lock,
    verify_existing_offline_dependency_wheelhouse,
)


class OfflineDependencyWheelhouseTests(unittest.TestCase):
    """All downloader behavior is fixture-injected; these tests never use a network."""

    @staticmethod
    def _host() -> TargetHost:
        return TargetHost(
            system="Darwin",
            machine="arm64",
            implementation="CPython",
            python_version=(3, 12),
            macos_version="14.7.8",
            sysconfig_platform="macosx-14.0-arm64",
        )

    @staticmethod
    def _write_wheel(
        path: Path,
        *,
        name: str,
        version: str,
        tag: str = "py3-none-any",
        payload: str = "fixture",
    ) -> tuple[str, str, str]:
        dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                f"{dist_info}/METADATA",
                "Metadata-Version: 2.4\n"
                f"Name: {name}\n"
                f"Version: {version}\n",
            )
            archive.writestr(
                f"{dist_info}/WHEEL",
                "Wheel-Version: 1.0\n"
                "Generator: fixture\n"
                "Root-Is-Purelib: true\n"
                f"Tag: {tag}\n",
            )
            archive.writestr(f"{dist_info}/RECORD", "")
            archive.writestr("fixture.txt", payload)
        with zipfile.ZipFile(path) as archive:
            metadata = archive.read(f"{dist_info}/METADATA")
            wheel_metadata = archive.read(f"{dist_info}/WHEEL")
        return (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            hashlib.sha256(metadata).hexdigest(),
            hashlib.sha256(wheel_metadata).hexdigest(),
        )

    def _fixture(
        self,
        root: Path,
        *,
        include_download_demo: bool = True,
        demo_tag: str = "py3-none-any",
        demo_payload: str = "fixture",
    ) -> dict[str, object]:
        root.mkdir(parents=True, exist_ok=True)
        constraints = root / "constraints.txt"
        constraints.write_text("demo==1.0.0\n", encoding="utf-8")

        sources = root / "download-sources"
        sources.mkdir()
        demo = sources / "demo-1.0.0-py3-none-any.whl"
        demo_hash = self._write_wheel(
            demo,
            name="demo",
            version="1.0.0",
            tag=demo_tag,
            payload=demo_payload,
        )[0]
        lock = root / "requirements.lock"
        lock.write_text(
            "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
            "11111111-1111-4111-8111-111111111111\n"
            f"demo==1.0.0 --hash=sha256:{demo_hash}\n",
            encoding="utf-8",
        )
        metadata = root / "lock.json"
        metadata.write_text(
            json.dumps(
                {
                    "schema": "fixture.dependency_lock.v1",
                    "generation_id": "11111111-1111-4111-8111-111111111111",
                    "constraints_sha256": hashlib.sha256(constraints.read_bytes()).hexdigest(),
                    "requirements_lock": lock.name,
                    "requirements_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
                    "resolver": {
                        "platform": "macos-14-arm64",
                        "python": "3.12",
                    },
                    "install_distribution_names": ["demo"],
                    "excluded_bundled_overrides": {
                        distribution: {
                            "version": spec["version"],
                            "excluded_from_requirements_lock": True,
                            "resolution_input_filename": spec["filename"],
                            "resolution_input_sha256": "a" * 64,
                            "resolution_input_metadata_sha256": "b" * 64,
                            "resolution_input_wheel_metadata_sha256": "c" * 64,
                        }
                        for distribution, spec in BUNDLED_OVERRIDE_SPECS.items()
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return {
            "constraints": constraints,
            "lock": lock,
            "metadata": metadata,
            "sources": sources,
            "download_demo": include_download_demo,
            "output": root / "output",
        }

    @staticmethod
    def _runner(
        sources: Path,
        *,
        filenames: tuple[str, ...] = ("demo-1.0.0-py3-none-any.whl",),
        extra: tuple[Path, ...] = (),
    ):
        def runner(command: list[str], *, cwd: Path, env: dict[str, str]):
            destination = Path(command[command.index("--dest") + 1])
            for filename in filenames:
                shutil.copyfile(sources / filename, destination / filename)
            for source in extra:
                shutil.copyfile(source, destination / source.name)
            return subprocess.CompletedProcess(command, 0, "downloaded", "")

        return runner

    @staticmethod
    def _run(fixture: dict[str, object], **kwargs: object) -> dict[str, object]:
        return build_offline_dependency_wheelhouse(
            python_executable=Path("/fixture/python"),
            constraints=fixture["constraints"],  # type: ignore[arg-type]
            requirements_lock=fixture["lock"],  # type: ignore[arg-type]
            lock_metadata=fixture["metadata"],  # type: ignore[arg-type]
            output_directory=fixture["output"],  # type: ignore[arg-type]
            host=OfflineDependencyWheelhouseTests._host(),
            **kwargs,
        )

    def test_publishes_complete_deterministic_manifest_and_exact_wheels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            manifest = self._run(
                fixture,
                download_runner=self._runner(fixture["sources"]),  # type: ignore[arg-type]
            )
            output = fixture["output"]
            assert isinstance(output, Path)
            self.assertEqual(manifest["schema"], "totalsegmentator_wrapper_mac.offline_dependency_wheelhouse.v1")
            self.assertEqual(manifest["target"], {
                "abi": "cp312",
                "implementation": "CPython",
                "machine": "arm64",
                "platform": "macosx_14_0_arm64",
                "python_version": "3.12",
            })
            self.assertEqual(
                [entry["distribution"] for entry in manifest["wheels"]],  # type: ignore[index]
                ["demo"],
            )
            for entry in manifest["wheels"]:  # type: ignore[index]
                self.assertEqual(set(entry), {"distribution", "filename", "sha256", "size_bytes", "source", "tags", "version"})
                self.assertGreater(entry["size_bytes"], 0)
            self.assertEqual(
                sorted(path.name for path in (output / "wheels").iterdir()),
                ["demo-1.0.0-py3-none-any.whl"],
            )
            self.assertTrue(
                set(spec["filename"] for spec in BUNDLED_OVERRIDE_SPECS.values())
                .isdisjoint(path.name for path in (output / "wheels").iterdir())
            )
            self.assertEqual(sorted(path.name for path in output.iterdir()), ["manifest.json", "wheels"])

            second = self._fixture(root / "second")
            second["output"] = root / "second-output"
            self._run(
                second,
                download_runner=self._runner(second["sources"]),  # type: ignore[arg-type]
            )
            self.assertEqual(
                (output / "manifest.json").read_bytes(),
                (second["output"] / "manifest.json").read_bytes(),  # type: ignore[index,operator]
            )

    def test_download_command_is_isolated_hashed_binary_and_target_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            calls: list[tuple[list[str], Path, dict[str, str]]] = []

            def runner(command: list[str], *, cwd: Path, env: dict[str, str]):
                calls.append((command, cwd, env))
                return self._runner(fixture["sources"])(command, cwd=cwd, env=env)  # type: ignore[arg-type]

            self._run(fixture, download_runner=runner)
            self.assertEqual(len(calls), 1)
            command, _cwd, environment = calls[0]
            self.assertEqual(command[:6], ["/fixture/python", "-I", "-m", "pip", "--isolated", "download"])
            self.assertIn("--require-hashes", command)
            self.assertIn("--only-binary=:all:", command)
            self.assertIn("--no-deps", command)
            self.assertEqual(command[command.index("--platform") + 1], TARGET_PLATFORM)
            self.assertEqual(command[command.index("--implementation") + 1], TARGET_IMPLEMENTATION)
            self.assertEqual(command[command.index("--python-version") + 1], "3.12")
            self.assertEqual(command[command.index("--abi") + 1], TARGET_ABI)
            self.assertEqual(environment["PIP_CONFIG_FILE"], "/dev/null")
            self.assertEqual(environment["PIP_DISABLE_PIP_VERSION_CHECK"], "1")

    def test_missing_wheel_fails_without_publishing_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            with self.assertRaisesRegex(OfflineWheelhouseError, "missing required distributions: demo"):
                self._run(
                    fixture,
                    download_runner=self._runner(fixture["sources"], filenames=()),  # type: ignore[arg-type]
                )
            self.assertFalse(fixture["output"].exists())  # type: ignore[index,union-attr]

    def test_source_distribution_is_rejected_before_any_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            sdist = root / "demo-1.0.0.tar.gz"
            sdist.write_bytes(b"not a wheel")
            with self.assertRaisesRegex(OfflineWheelhouseError, "source distribution"):
                self._run(
                    fixture,
                    download_runner=self._runner(
                        fixture["sources"], filenames=(), extra=(sdist,)  # type: ignore[arg-type]
                    ),
                )
            self.assertFalse(fixture["output"].exists())  # type: ignore[index,union-attr]

    def test_approved_local_wheel_can_cover_a_binary_unavailable_locked_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            local = root / "approved-local"
            local.mkdir()
            shutil.copyfile(fixture["sources"] / "demo-1.0.0-py3-none-any.whl", local / "demo-1.0.0-py3-none-any.whl")  # type: ignore[index,operator]

            def local_only_runner(command: list[str], *, cwd: Path, env: dict[str, str]):
                self.assertIn("--find-links", command)
                self.assertEqual(
                    command[command.index("--find-links") + 1], str(local)
                )
                destination = Path(command[command.index("--dest") + 1])
                shutil.copyfile(
                    local / "demo-1.0.0-py3-none-any.whl",
                    destination / "demo-1.0.0-py3-none-any.whl",
                )
                return subprocess.CompletedProcess(command, 0, "local wheel", "")

            self._run(
                fixture,
                approved_local_wheel_directory=local,
                download_runner=local_only_runner,
            )
            self.assertTrue((fixture["output"] / "wheels" / "demo-1.0.0-py3-none-any.whl").is_file())  # type: ignore[index,operator]
            manifest = json.loads((fixture["output"] / "manifest.json").read_text(encoding="utf-8"))  # type: ignore[index,operator]
            demo = next(item for item in manifest["wheels"] if item["distribution"] == "demo")
            self.assertEqual(demo["source"], "approved-locally-built-wheel")

    def test_cp311_abi3_wheel_is_accepted_for_cpython312(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            compatible = root / "abi3"
            compatible.mkdir()
            wheel = compatible / "demo-1.0.0-cp311-abi3-macosx_11_0_arm64.whl"
            digest = self._write_wheel(
                wheel,
                name="demo",
                version="1.0.0",
                tag="cp311-abi3-macosx_11_0_arm64",
            )[0]
            lock = fixture["lock"]
            assert isinstance(lock, Path)
            lock.write_text(
                "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
                "11111111-1111-4111-8111-111111111111\n"
                f"demo==1.0.0 --hash=sha256:{digest}\n",
                encoding="utf-8",
            )
            metadata = fixture["metadata"]
            assert isinstance(metadata, Path)
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            payload["requirements_lock_sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest()
            metadata.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            self._run(
                fixture,
                download_runner=self._runner(compatible, filenames=(wheel.name,)),
            )
            self.assertTrue((fixture["output"] / "wheels" / wheel.name).is_file())  # type: ignore[index,operator]

    def test_wrong_platform_or_python_tag_is_rejected(self) -> None:
        for tag, label in (
            ("cp311-cp311-macosx_14_0_arm64", "Python"),
            ("cp312-cp312-macosx_14_0_x86_64", "platform"),
            ("cp312-cp312-macosx_15_0_arm64", "not compatible"),
        ):
            with self.subTest(tag=tag):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    fixture = self._fixture(root)
                    wrong = root / f"demo-1.0.0-{tag}.whl"
                    self._write_wheel(wrong, name="demo", version="1.0.0", tag=tag)
                    lock = fixture["lock"]
                    assert isinstance(lock, Path)
                    lock.write_text(
                        "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
                        "11111111-1111-4111-8111-111111111111\n"
                        f"demo==1.0.0 --hash=sha256:{hashlib.sha256(wrong.read_bytes()).hexdigest()}\n",
                        encoding="utf-8",
                    )
                    metadata = fixture["metadata"]
                    assert isinstance(metadata, Path)
                    payload = json.loads(metadata.read_text(encoding="utf-8"))
                    payload["requirements_lock_sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest()
                    metadata.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
                    with self.assertRaisesRegex(OfflineWheelhouseError, "not compatible"):
                        self._run(
                            fixture,
                            download_runner=self._runner(
                                wrong.parent, filenames=(wrong.name,)
                            ),
                        )

    def test_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            replacement = root / "replacement"
            replacement.mkdir()
            self._write_wheel(
                replacement / "demo-1.0.0-py3-none-any.whl",
                name="demo",
                version="1.0.0",
                payload="changed bytes",
            )
            with self.assertRaisesRegex(OfflineWheelhouseError, "SHA-256 does not match"):
                self._run(
                    fixture,
                    download_runner=self._runner(replacement),
                )

    def test_filename_metadata_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            malformed = root / "malformed"
            malformed.mkdir()
            wrong_name = malformed / "not_demo-1.0.0-py3-none-any.whl"
            digest = self._write_wheel(
                wrong_name,
                name="demo",
                version="1.0.0",
            )[0]
            lock = fixture["lock"]
            assert isinstance(lock, Path)
            lock.write_text(
                "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
                "11111111-1111-4111-8111-111111111111\n"
                f"demo==1.0.0 --hash=sha256:{digest}\n",
                encoding="utf-8",
            )
            metadata = fixture["metadata"]
            assert isinstance(metadata, Path)
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            payload["requirements_lock_sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest()
            metadata.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            with self.assertRaisesRegex(OfflineWheelhouseError, "filename and identity metadata differ"):
                self._run(
                    fixture,
                    download_runner=self._runner(malformed, filenames=(wrong_name.name,)),
                )

    def test_extra_and_duplicate_distributions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            extra = root / "extra-2.0.0-py3-none-any.whl"
            self._write_wheel(extra, name="extra", version="2.0.0")
            with self.assertRaisesRegex(OfflineWheelhouseError, "extra distribution: extra"):
                self._run(
                    fixture,
                    download_runner=self._runner(fixture["sources"], extra=(extra,)),  # type: ignore[arg-type]
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            duplicate = root / "demo-1.0.0-py312-none-any.whl"
            self._write_wheel(
                duplicate,
                name="demo",
                version="1.0.0",
                tag="py312-none-any",
            )
            with self.assertRaisesRegex(OfflineWheelhouseError, "duplicate distribution: demo"):
                self._run(
                    fixture,
                    download_runner=self._runner(fixture["sources"], extra=(duplicate,)),  # type: ignore[arg-type]
                )

    def test_lock_metadata_mismatch_is_rejected_before_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            lock = fixture["lock"]
            assert isinstance(lock, Path)
            lock.write_text(lock.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
            calls: list[list[str]] = []

            def runner(command: list[str], *, cwd: Path, env: dict[str, str]):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            with self.assertRaisesRegex(OfflineWheelhouseError, "metadata SHA-256 mismatch"):
                self._run(fixture, download_runner=runner)
            self.assertEqual(calls, [])

    def test_verify_existing_is_read_only_and_detects_manifest_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            self._run(
                fixture,
                download_runner=self._runner(fixture["sources"]),  # type: ignore[arg-type]
            )
            output = fixture["output"]
            assert isinstance(output, Path)
            verified = verify_existing_offline_dependency_wheelhouse(
                constraints=fixture["constraints"],  # type: ignore[arg-type]
                requirements_lock=fixture["lock"],  # type: ignore[arg-type]
                lock_metadata=fixture["metadata"],  # type: ignore[arg-type]
                output_directory=output,
            )
            self.assertEqual(verified["schema"], "totalsegmentator_wrapper_mac.offline_dependency_wheelhouse.v1")
            manifest_path = output / "manifest.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["wheels"][0]["size_bytes"] += 1
            manifest_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            with self.assertRaisesRegex(OfflineWheelhouseError, "does not match"):
                verify_existing_offline_dependency_wheelhouse(
                    constraints=fixture["constraints"],  # type: ignore[arg-type]
                    requirements_lock=fixture["lock"],  # type: ignore[arg-type]
                    lock_metadata=fixture["metadata"],  # type: ignore[arg-type]
                    output_directory=output,
                )

    def test_parse_rejects_duplicate_lock_distribution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "requirements.lock"
            lock.write_text(
                "demo==1.0.0 --hash=sha256:" + "a" * 64 + "\n"
                "demo==1.0.1 --hash=sha256:" + "b" * 64 + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(OfflineWheelhouseError, "duplicate distribution"):
                parse_hashed_requirements_lock(lock)


if __name__ == "__main__":
    unittest.main()
