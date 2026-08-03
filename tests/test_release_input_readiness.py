from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_release_input_readiness import (
    ReleaseInputReadinessError,
    verify_canonical_dependency_lock,
    verify_hashed_requirement_entries,
    verify_setup_weight_revalidation_complete,
)


ROOT = Path(__file__).resolve().parents[1]


class ReleaseInputReadinessTests(unittest.TestCase):
    _GENERATION_ID = "2b03e2ef-8d40-4a02-9ad3-0d2d8f6bd0d3"

    def _write_setup_lock_consumer(self, path: Path) -> None:
        path.write_text(
            "def validate_safe_command(command):\n"
            "    return command\n\n"
            "def build_locked_dependencies_install_command(venv_python, *, requirements_lock, wheel_directory):\n"
            "    command = [str(venv_python), '-I', '-m', 'pip', '--isolated', 'install', '--require-hashes', '--no-deps', '-r', str(requirements_lock)]\n"
            "    validate_safe_command(command)\n"
            "    return command\n\n"
            "def _execute_step(name, command):\n"
            "    return name, command\n\n"
            "def run_setup(venv_python, requirements_lock, wheel_directory, allow_network):\n"
            "    if allow_network and requirements_lock is not None:\n"
            "        return _execute_step('install_locked_dependencies', build_locked_dependencies_install_command(venv_python, requirements_lock=requirements_lock, wheel_directory=wheel_directory))\n",
            encoding="utf-8",
        )

    @staticmethod
    def _excluded_bundled_overrides() -> dict[str, dict[str, object]]:
        """Metadata records resolver-input hashes, never release wheel hashes."""

        return {
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
        }

    def _metadata_payload(
        self,
        *,
        constraints: Path,
        requirements_lock: Path,
        resolved_distribution_names: list[str],
        install_distribution_names: list[str] | None = None,
        project_file: Path = ROOT / "pyproject.toml",
    ) -> dict[str, object]:
        def digest(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        if install_distribution_names is None:
            install_distribution_names = [
                name
                for name in resolved_distribution_names
                if name not in {"acvl-utils", "fpsample"}
            ]
        return {
            "schema": "totalsegmentator_wrapper_mac.dependency_lock.v4",
            "bootstrap": {
                "schema": (
                    "totalsegmentator_wrapper_mac."
                    "dependency_lock_bootstrap_binding.v1"
                ),
                "source_identity_sha256": "1" * 64,
                "sealed_toolchain": {
                    "lock_sha256": "2" * 64,
                    "metadata_sha256": "3" * 64,
                    "receipt_sha256": "4" * 64,
                },
                "pre_sign_wheel_receipt_sha256": "5" * 64,
            },
            "generation_id": self._GENERATION_ID,
            "constraints_sha256": digest(constraints),
            "project_file": project_file.name,
            "project_file_sha256": digest(project_file),
            "requirements_lock": requirements_lock.name,
            "requirements_lock_sha256": digest(requirements_lock),
            "root_install_requirement": "totalsegmentator-wrapper-mac[dicom,mps,dentalseg,toothseg,ios-meshsegnet]",
            "resolved_distribution_names": resolved_distribution_names,
            "install_distribution_names": install_distribution_names,
            "excluded_bundled_overrides": self._excluded_bundled_overrides(),
            "resolution_complete": True,
            "resolver": {
                "name": "pip-compile",
                "version": "7.5.0",
                "platform": "macos-14-arm64",
                "python": "3.12",
                "pip_version": "25.1.1",
                "python_full_version": "3.12.11",
                "macos_version": "14.7.8",
                "sysconfig_platform": "macosx-14.0-arm64",
            },
            "pip_require_hashes": True,
            "setup_consumes_requirements_lock": True,
        }

    def test_current_constraints_fail_for_ranges_and_missing_hashes(self) -> None:
        with self.assertRaisesRegex(
            ReleaseInputReadinessError,
            "not an exact == pin|no SHA-256",
        ):
            verify_hashed_requirement_entries(
                ROOT / "constraints" / "macos-arm64-py312.txt"
            )

    def test_exact_pins_still_require_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "constraints.txt"
            path.write_text("numpy==2.0.0\n", encoding="utf-8")
            with self.assertRaisesRegex(ReleaseInputReadinessError, "no SHA-256"):
                verify_hashed_requirement_entries(path)

    def test_single_hashed_entry_only_passes_entry_syntax_not_release_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "constraints.txt"
            path.write_text(
                "numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n",
                encoding="utf-8",
            )
            verify_hashed_requirement_entries(path)
            with self.assertRaisesRegex(
                ReleaseInputReadinessError,
                "dependency lock metadata",
            ):
                verify_canonical_dependency_lock(
                    constraints=path,
                    requirements_lock=path,
                    lock_metadata=Path(tmp) / "missing-lock-metadata.json",
                    setup_manager_source=ROOT
                    / "src"
                    / "totalsegmentator_wrapper_mac"
                    / "setup_manager.py",
                )

    def test_dependency_inventory_parses_ranged_source_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints = root / "constraints.txt"
            requirements_lock = root / "requirements.lock"
            metadata_path = root / "lock.json"
            setup_source = root / "setup_manager.py"
            constraints.write_text(
                "scikit-learn>=1.5,<2\nnumpy>=1.26,<3\n"
                "acvl-utils==0.2.6\nfpsample==1.0.2\n",
                encoding="utf-8",
            )
            requirements_lock.write_text(
                "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
                + self._GENERATION_ID
                + "\n"
                "scikit-learn==1.7.2 --hash=sha256:" + "a" * 64 + "\n"
                "numpy==2.3.3 --hash=sha256:" + "b" * 64 + "\n",
                encoding="utf-8",
            )
            self._write_setup_lock_consumer(setup_source)

            metadata_path.write_text(
                json.dumps(
                    self._metadata_payload(
                        constraints=constraints,
                        requirements_lock=requirements_lock,
                        resolved_distribution_names=[
                            "acvl-utils",
                            "fpsample",
                            "numpy",
                            "scikit-learn",
                        ],
                    )
                ),
                encoding="utf-8",
            )

            verify_canonical_dependency_lock(
                constraints=constraints,
                requirements_lock=requirements_lock,
                lock_metadata=metadata_path,
                setup_manager_source=setup_source,
            )

            for field, invalid_value in (
                ("name", "uv"),
                ("version", "7.4.1"),
                ("platform", "macos-arm64"),
                ("python", "3.11"),
            ):
                with self.subTest(resolver_field=field):
                    mutated = json.loads(metadata_path.read_text(encoding="utf-8"))
                    mutated["resolver"][field] = invalid_value
                    metadata_path.write_text(json.dumps(mutated), encoding="utf-8")
                    with self.assertRaisesRegex(
                        ReleaseInputReadinessError,
                        "resolver identity",
                    ):
                        verify_canonical_dependency_lock(
                            constraints=constraints,
                            requirements_lock=requirements_lock,
                            lock_metadata=metadata_path,
                            setup_manager_source=setup_source,
                        )
                    mutated["resolver"][field] = self._metadata_payload(
                        constraints=constraints,
                        requirements_lock=requirements_lock,
                        resolved_distribution_names=[
                            "acvl-utils",
                            "fpsample",
                            "numpy",
                            "scikit-learn",
                        ],
                    )["resolver"][field]
                    metadata_path.write_text(json.dumps(mutated), encoding="utf-8")

            duplicated_inventory = json.loads(
                metadata_path.read_text(encoding="utf-8")
            )
            duplicated_inventory["resolved_distribution_names"].append("numpy")
            metadata_path.write_text(
                json.dumps(duplicated_inventory),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ReleaseInputReadinessError,
                "inventory",
            ):
                verify_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=requirements_lock,
                    lock_metadata=metadata_path,
                    setup_manager_source=setup_source,
                )

    def test_canonical_lock_rejects_project_dependency_change_without_constraint_change(
        self,
    ) -> None:
        """pip-compile reads pyproject extras, so its bytes must be lock-bound."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints = root / "constraints.txt"
            requirements_lock = root / "requirements.lock"
            metadata_path = root / "lock.json"
            project_file = root / "pyproject.toml"
            setup_source = root / "setup_manager.py"
            constraints.write_text(
                "acvl-utils==0.2.6\nfpsample==1.0.2\n",
                encoding="utf-8",
            )
            requirements_lock.write_text(
                "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
                + self._GENERATION_ID
                + "\n"
                "numpy==2.3.3 --hash=sha256:"
                + "a" * 64
                + "\n",
                encoding="utf-8",
            )
            project_file.write_text(
                "[project]\nname = 'fixture'\nversion = '0'\n",
                encoding="utf-8",
            )
            self._write_setup_lock_consumer(setup_source)
            metadata_path.write_text(
                json.dumps(
                    self._metadata_payload(
                        constraints=constraints,
                        requirements_lock=requirements_lock,
                        project_file=project_file,
                        resolved_distribution_names=[
                            "acvl-utils",
                            "fpsample",
                            "numpy",
                        ],
                    )
                ),
                encoding="utf-8",
            )

            verify_canonical_dependency_lock(
                constraints=constraints,
                requirements_lock=requirements_lock,
                lock_metadata=metadata_path,
                project_file=project_file,
                setup_manager_source=setup_source,
            )

            project_file.write_text(
                "[project]\nname = 'fixture'\nversion = '0'\n"
                "dependencies = ['new-unlocked-dependency>=1']\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ReleaseInputReadinessError,
                "project dependency declarations",
            ):
                verify_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=requirements_lock,
                    lock_metadata=metadata_path,
                    project_file=project_file,
                    setup_manager_source=setup_source,
                )

    def test_canonical_lock_rejects_mismatched_excluded_override_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints = root / "constraints.txt"
            requirements_lock = root / "requirements.lock"
            metadata_path = root / "lock.json"
            setup_source = root / "setup_manager.py"
            constraints.write_text(
                "acvl-utils==0.2.6\nfpsample==1.0.2\n",
                encoding="utf-8",
            )
            requirements_lock.write_text(
                "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
                + self._GENERATION_ID
                + "\n"
                "numpy==2.3.3 --hash=sha256:" + "a" * 64 + "\n",
                encoding="utf-8",
            )
            self._write_setup_lock_consumer(setup_source)
            payload = self._metadata_payload(
                constraints=constraints,
                requirements_lock=requirements_lock,
                resolved_distribution_names=["acvl-utils", "fpsample", "numpy"],
            )
            payload["excluded_bundled_overrides"]["fpsample"][
                "release_wheel_sha256"
            ] = "c" * 64
            metadata_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                ReleaseInputReadinessError,
                "bundled override metadata is invalid",
            ):
                verify_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=requirements_lock,
                    lock_metadata=metadata_path,
                    setup_manager_source=setup_source,
                )

    def test_canonical_lock_rejects_bundled_override_left_in_install_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints = root / "constraints.txt"
            requirements_lock = root / "requirements.lock"
            metadata_path = root / "lock.json"
            setup_source = root / "setup_manager.py"
            constraints.write_text(
                "acvl-utils==0.2.6\nfpsample==1.0.2\n",
                encoding="utf-8",
            )
            requirements_lock.write_text(
                "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
                + self._GENERATION_ID
                + "\n"
                "acvl-utils==0.2.7 --hash=sha256:" + "a" * 64 + "\n"
                "fpsample==1.0.2 --hash=sha256:" + "b" * 64 + "\n",
                encoding="utf-8",
            )
            self._write_setup_lock_consumer(setup_source)

            metadata_path.write_text(
                json.dumps(
                    self._metadata_payload(
                        constraints=constraints,
                        requirements_lock=requirements_lock,
                        resolved_distribution_names=["acvl-utils", "fpsample"],
                        install_distribution_names=["acvl-utils", "fpsample"],
                    )
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ReleaseInputReadinessError,
                "must exclude bundled overrides",
            ):
                verify_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=requirements_lock,
                    lock_metadata=metadata_path,
                    setup_manager_source=setup_source,
                )

    def test_canonical_lock_requires_matching_generation_and_observed_resolver_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints = root / "constraints.txt"
            requirements_lock = root / "requirements.lock"
            metadata_path = root / "lock.json"
            setup_source = root / "setup_manager.py"
            constraints.write_text(
                "acvl-utils==0.2.6\nfpsample==1.0.2\n",
                encoding="utf-8",
            )
            requirements_lock.write_text(
                "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
                + self._GENERATION_ID
                + "\n"
                "numpy==2.3.3 --hash=sha256:" + "a" * 64 + "\n",
                encoding="utf-8",
            )
            self._write_setup_lock_consumer(setup_source)
            metadata_path.write_text(
                json.dumps(
                    self._metadata_payload(
                        constraints=constraints,
                        requirements_lock=requirements_lock,
                        resolved_distribution_names=["acvl-utils", "fpsample", "numpy"],
                    )
                ),
                encoding="utf-8",
            )

            verify_canonical_dependency_lock(
                constraints=constraints,
                requirements_lock=requirements_lock,
                lock_metadata=metadata_path,
                setup_manager_source=setup_source,
            )

            for field, invalid_value in (
                ("pip_version", "not-a-version"),
                ("python_full_version", "3.11.11"),
                ("macos_version", "15.0"),
                ("sysconfig_platform", "linux-aarch64"),
            ):
                with self.subTest(resolver_field=field):
                    mutated = json.loads(metadata_path.read_text(encoding="utf-8"))
                    mutated["resolver"][field] = invalid_value
                    metadata_path.write_text(json.dumps(mutated), encoding="utf-8")
                    with self.assertRaisesRegex(
                        ReleaseInputReadinessError,
                        "resolver provenance",
                    ):
                        verify_canonical_dependency_lock(
                            constraints=constraints,
                            requirements_lock=requirements_lock,
                            lock_metadata=metadata_path,
                            setup_manager_source=setup_source,
                        )
                    metadata_path.write_text(
                        json.dumps(
                            self._metadata_payload(
                                constraints=constraints,
                                requirements_lock=requirements_lock,
                                resolved_distribution_names=["acvl-utils", "fpsample", "numpy"],
                            )
                        ),
                        encoding="utf-8",
                    )

            mismatched = json.loads(metadata_path.read_text(encoding="utf-8"))
            mismatched["generation_id"] = "8f5120b3-23e2-47b1-b8cf-1ca12e7e3931"
            metadata_path.write_text(json.dumps(mismatched), encoding="utf-8")
            with self.assertRaisesRegex(ReleaseInputReadinessError, "generation ID"):
                verify_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=requirements_lock,
                    lock_metadata=metadata_path,
                    setup_manager_source=setup_source,
                )

    def test_canonical_lock_rejects_comment_only_hashed_lock_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints = root / "constraints.txt"
            requirements_lock = root / "requirements.lock"
            metadata_path = root / "lock.json"
            setup_source = root / "setup_manager.py"
            constraints.write_text(
                "acvl-utils==0.2.6\nfpsample==1.0.2\n",
                encoding="utf-8",
            )
            requirements_lock.write_text(
                "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
                + self._GENERATION_ID
                + "\n"
                "numpy==2.3.3 --hash=sha256:" + "a" * 64 + "\n",
                encoding="utf-8",
            )
            setup_source.write_text(
                "# requirements_lock\n# --require-hashes\n",
                encoding="utf-8",
            )
            metadata_path.write_text(
                json.dumps(
                    self._metadata_payload(
                        constraints=constraints,
                        requirements_lock=requirements_lock,
                        resolved_distribution_names=["acvl-utils", "fpsample", "numpy"],
                    )
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ReleaseInputReadinessError,
                "setup lock-consumer contract",
            ):
                verify_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=requirements_lock,
                    lock_metadata=metadata_path,
                    setup_manager_source=setup_source,
                )

    def test_canonical_lock_rejects_hashed_builder_that_is_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints = root / "constraints.txt"
            requirements_lock = root / "requirements.lock"
            metadata_path = root / "lock.json"
            setup_source = root / "setup_manager.py"
            constraints.write_text(
                "acvl-utils==0.2.6\nfpsample==1.0.2\n",
                encoding="utf-8",
            )
            requirements_lock.write_text(
                "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
                + self._GENERATION_ID
                + "\n"
                "numpy==2.3.3 --hash=sha256:" + "a" * 64 + "\n",
                encoding="utf-8",
            )
            setup_source.write_text(
                "def validate_safe_command(command):\n"
                "    return command\n\n"
                "def build_locked_dependencies_install_command(venv_python, *, requirements_lock, wheel_directory):\n"
                "    command = [str(venv_python), '-I', '-m', 'pip', '--isolated', 'install', '--require-hashes', '--no-deps', '-r', str(requirements_lock)]\n"
                "    validate_safe_command(command)\n"
                "    return command\n\n"
                "def run_setup(venv_python, requirements_lock, wheel_directory, allow_network):\n"
                "    if allow_network and requirements_lock is not None:\n"
                "        build_locked_dependencies_install_command(venv_python, requirements_lock=requirements_lock, wheel_directory=wheel_directory)\n",
                encoding="utf-8",
            )
            metadata_path.write_text(
                json.dumps(
                    self._metadata_payload(
                        constraints=constraints,
                        requirements_lock=requirements_lock,
                        resolved_distribution_names=["acvl-utils", "fpsample", "numpy"],
                    )
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ReleaseInputReadinessError,
                "setup lock-consumer contract",
            ):
                verify_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=requirements_lock,
                    lock_metadata=metadata_path,
                    setup_manager_source=setup_source,
                )

    def test_canonical_lock_rejects_builder_without_no_deps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            constraints = root / "constraints.txt"
            requirements_lock = root / "requirements.lock"
            metadata_path = root / "lock.json"
            setup_source = root / "setup_manager.py"
            constraints.write_text(
                "acvl-utils==0.2.6\nfpsample==1.0.2\n",
                encoding="utf-8",
            )
            requirements_lock.write_text(
                "# totalsegmentator_wrapper_mac.dependency_lock_generation_id: "
                + self._GENERATION_ID
                + "\n"
                "numpy==2.3.3 --hash=sha256:" + "a" * 64 + "\n",
                encoding="utf-8",
            )
            setup_source.write_text(
                "def validate_safe_command(command):\n"
                "    return command\n\n"
                "def build_locked_dependencies_install_command(venv_python, *, requirements_lock, wheel_directory):\n"
                "    command = [str(venv_python), '-I', '-m', 'pip', '--isolated', 'install', '--require-hashes', '-r', str(requirements_lock)]\n"
                "    validate_safe_command(command)\n"
                "    return command\n\n"
                "def _execute_step(name, command):\n"
                "    return name, command\n\n"
                "def run_setup(venv_python, requirements_lock, wheel_directory, allow_network):\n"
                "    if allow_network and requirements_lock is not None:\n"
                "        return _execute_step('install_locked_dependencies', build_locked_dependencies_install_command(venv_python, requirements_lock=requirements_lock, wheel_directory=wheel_directory))\n",
                encoding="utf-8",
            )
            metadata_path.write_text(
                json.dumps(
                    self._metadata_payload(
                        constraints=constraints,
                        requirements_lock=requirements_lock,
                        resolved_distribution_names=["acvl-utils", "fpsample", "numpy"],
                    )
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ReleaseInputReadinessError,
                "setup lock-consumer contract",
            ):
                verify_canonical_dependency_lock(
                    constraints=constraints,
                    requirements_lock=requirements_lock,
                    lock_metadata=metadata_path,
                    setup_manager_source=setup_source,
                )

    def test_current_task_115_and_297_revalidation_evidence_permits_release(self) -> None:
        """The checked-in manifest now carries the required strict evidence."""

        verify_setup_weight_revalidation_complete(
            ROOT
            / "src"
            / "totalsegmentator_wrapper_mac"
            / "totalseg_setup_weights_manifest.json"
        )

    def test_attested_assets_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weights.json"
            path.write_text(
                json.dumps(
                    {
                        "assets": [
                            {
                                "task_id": 113,
                                "filename": "Dataset113_ToothFairy3.zip",
                                "sha256_source": "github-release-digest",
                                "publisher_digest_available": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            verify_setup_weight_revalidation_complete(path)

    def test_locally_observed_asset_requires_strict_revalidation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weights.json"
            item = {
                "task_id": 115,
                "release_tag": "v2.5.0-weights",
                "filename": "Dataset115_mandible.zip",
                "url": "https://github.com/wasserth/TotalSegmentator/releases/download/v2.5.0-weights/Dataset115_mandible.zip",
                "size_bytes": 230321497,
                "sha256": "a" * 64,
                "sha256_source": "approved-official-asset-revalidation",
                "publisher_digest_available": False,
                "revalidation_required_before_release": False,
            }
            path.write_text(json.dumps({"assets": [item]}), encoding="utf-8")
            with self.assertRaisesRegex(
                ReleaseInputReadinessError,
                "malformed or unsubstantiated",
            ):
                verify_setup_weight_revalidation_complete(path)

            item["revalidation_evidence"] = {
                "schema": "totalsegmentator_wrapper_mac.official_asset_revalidation.v1",
                "official_url": item["url"],
                "release_tag": item["release_tag"],
                "filename": item["filename"],
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
                "verified_at_utc": "2026-08-01T00:00:00Z",
                "transport": "https-pinned-official-release-asset",
                "checks": [
                    "complete-size",
                    "sha256",
                    "zip-crc",
                    "expected-model-structure",
                ],
                "approval": "approved-for-release",
            }
            path.write_text(json.dumps({"assets": [item]}), encoding="utf-8")
            verify_setup_weight_revalidation_complete(path)

            item["revalidation_evidence"]["sha256"] = "b" * 64
            path.write_text(json.dumps({"assets": [item]}), encoding="utf-8")
            with self.assertRaisesRegex(
                ReleaseInputReadinessError,
                "malformed or unsubstantiated",
            ):
                verify_setup_weight_revalidation_complete(path)

            item["revalidation_evidence"]["sha256"] = item["sha256"]
            item["revalidation_evidence"]["verified_at_utc"] = (
                "2026-99-99T00:00:00Z"
            )
            path.write_text(json.dumps({"assets": [item]}), encoding="utf-8")
            with self.assertRaisesRegex(
                ReleaseInputReadinessError,
                "malformed or unsubstantiated",
            ):
                verify_setup_weight_revalidation_complete(path)


if __name__ == "__main__":
    unittest.main()
