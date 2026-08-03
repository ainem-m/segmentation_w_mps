from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMPORTER = ROOT / "scripts" / "import_test_account_evidence.sh"

HASHED_LOCK_CHECKS = {
    "wheel_install_hashed_lock",
    "install_bundled_wheels_step_success",
    "install_locked_dependencies_step_success",
    "pip_check_step_success",
    "manifest_has_requirements_lock_sha256",
    "manifest_has_dependency_lock_metadata_sha256",
    "manifest_has_dependency_wheelhouse_manifest_sha256",
    "bundled_requirements_lock_sha256_matches_manifest",
    "bundled_dependency_lock_metadata_sha256_matches_manifest",
    "bundled_dependency_wheelhouse_manifest_sha256_matches_manifest",
    "installed_requirements_lock_sha256_matches_manifest",
    "installed_dependency_lock_metadata_sha256_matches_manifest",
    "installed_dependency_wheelhouse_manifest_sha256_matches_manifest",
}

NATIVE_RELEASE_EVIDENCE_CHECKS = {
    "app_and_wheel_macho_macos14_arm64",
    "dicom_helpers_system_linkage_no_rpath",
    "normalizer_source_matches_bundled_receipts",
    "dcm2niix_source_matches_bundled_receipt_and_pointer",
}


def importer_required_checks(source: str) -> list[str]:
    match = re.search(
        r"required_checks = \[(?P<items>.*?)\]\nif expected_app_version:",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError("could not find importer required_checks contract")
    required = list(ast.literal_eval("[" + match.group("items") + "]"))
    if 'required_checks.append("manifest_app_version_matches_expected")' in source:
        required.append("manifest_app_version_matches_expected")
    return required


class TestAccountHashedLockEvidenceTests(unittest.TestCase):
    def _run_importer(
        self,
        checks: dict[str, bool],
        *,
        check_entries: list[dict[str, object]] | None = None,
        payload_overrides: dict[str, object] | None = None,
        source_as_symlink: bool = False,
        source_missing: bool = False,
        raw_evidence: str | None = None,
        release_binding: str = "valid",
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixture-root"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            copied_importer = scripts / IMPORTER.name
            shutil.copy2(IMPORTER, copied_importer)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "fixture"\nversion = "0.4.1"\n',
                encoding="utf-8",
            )

            evidence_payload: dict[str, object] = {
                "schema": "totalsegmentator_wrapper_mac.test_account_install_evidence.v2",
                "passed": True,
                "run_id": "0" * 32,
                "collected_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "home": "/Users/separate-release-test-account",
                "app_path": "/Applications/TotalSegmentator Wrapper for Mac.app",
                "support_dir": "/Users/separate-release-test-account/Library/Application Support/TotalSegmentatorWrapperMac",
                "state_json": "/Users/separate-release-test-account/Library/Application Support/TotalSegmentatorWrapperMac/setup_state.json",
                "manifest_path": "/Applications/TotalSegmentator Wrapper for Mac.app/Contents/Resources/setup_manifest.json",
                "shared_copy_path": "/Users/Shared/TotalSegmentatorWrapperMac/test_account_install_evidence.json",
                "expected_app_version": "0.4.1",
                "app_identity": {
                    "app_version": "0.4.1",
                    "build_id": "fixture-build",
                    "dependency_set_id": "fixture-dependencies",
                    "setup_manifest_sha256": "a" * 64,
                    "info_plist_sha256": "b" * 64,
                    "dmg_path": "/Users/separate-release-test-account/Downloads/TotalSegmentator Wrapper for Mac-0.4.1-release-arm64.dmg",
                    "dmg_sha256": "c" * 64,
                },
                "checks": check_entries
                if check_entries is not None
                else [
                    {"name": name, "passed": passed}
                    for name, passed in sorted(checks.items())
                ],
            }
            if payload_overrides:
                evidence_payload.update(payload_overrides)
            target = root / "evidence-target.json"
            target.write_text(
                raw_evidence
                if raw_evidence is not None
                else json.dumps(evidence_payload, ensure_ascii=False),
                encoding="utf-8",
            )
            evidence = root / "evidence.json"
            if source_missing:
                pass
            elif source_as_symlink:
                evidence.symlink_to(target.name)
            else:
                shutil.copy2(target, evidence)
            environment = dict(os.environ)
            environment["PYTHON_BIN"] = sys.executable
            for name in (
                "TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_RECEIPT",
                "TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_DMG_SHA256",
                "TOTALSEGMENTATOR_WRAPPER_MAC_TEST_ACCOUNT_DEVELOPMENT_PREFLIGHT",
            ):
                environment.pop(name, None)
            if release_binding in {"valid", "receipt_mismatch"}:
                receipt = root / "notary-release-receipt.json"
                receipt.write_text(
                    json.dumps(
                        {
                            "schema": "totalsegmentator_wrapper_mac.notary_release_receipt.v1",
                            "created_at_utc": datetime.now(timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z"),
                            "version": "0.4.1",
                            "source_commit": "d" * 40,
                            "bundle_identifier": "jp.chino.totalsegmentator.wrapper.mac",
                            "team_identifier": "TEAMID1234",
                            "submission_id": "00000000-0000-0000-0000-000000000000",
                            "submission_status": "Accepted",
                            "submitted_dmg_sha256": "e" * 64,
                            "submitted_dmg_size_bytes": 1,
                            "final_dmg_sha256": (
                                "f" * 64 if release_binding == "receipt_mismatch" else "c" * 64
                            ),
                            "final_dmg_size_bytes": 2,
                            "app_manifest_sha256": "a" * 64,
                            "dmg_filename": "TotalSegmentator Wrapper for Mac-0.4.1-release-arm64.dmg",
                        }
                    ),
                    encoding="utf-8",
                )
                environment["TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_RECEIPT"] = str(
                    receipt
                )
                environment["TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_DMG_SHA256"] = (
                    "c" * 64
                )
            elif release_binding == "development":
                environment[
                    "TOTALSEGMENTATOR_WRAPPER_MAC_TEST_ACCOUNT_DEVELOPMENT_PREFLIGHT"
                ] = "1"
            elif release_binding != "missing":
                raise AssertionError(f"unknown release binding: {release_binding}")
            completed = subprocess.run(
                ["/bin/bash", str(copied_importer), str(evidence)],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            verdicts = list(
                (root / "artifacts" / "test_account_install").glob(
                    "*/test_account_install_verdict.json"
                )
            )
            self.assertEqual(len(verdicts), 1, completed.stderr + completed.stdout)
            verdict = json.loads(verdicts[0].read_text(encoding="utf-8"))
            return completed, verdict

    def test_final_import_requires_notary_receipt_and_expected_digest(self) -> None:
        checks = {
            name: True
            for name in importer_required_checks(IMPORTER.read_text(encoding="utf-8"))
        }

        completed, verdict = self._run_importer(checks, release_binding="missing")

        self.assertNotEqual(completed.returncode, 0)
        self.assertIs(verdict["passed"], False)
        self.assertIn(
            "notary_receipt_required_for_final_import", verdict["identity_failures"]
        )
        self.assertIn(
            "expected_dmg_sha256_required_for_final_import",
            verdict["identity_failures"],
        )

    def test_final_import_rejects_receipt_digest_mismatch(self) -> None:
        checks = {
            name: True
            for name in importer_required_checks(IMPORTER.read_text(encoding="utf-8"))
        }

        completed, verdict = self._run_importer(
            checks, release_binding="receipt_mismatch"
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIs(verdict["passed"], False)
        self.assertIn(
            "expected_dmg_sha256_does_not_match_notary_receipt",
            verdict["identity_failures"],
        )

    def test_explicit_development_preflight_cannot_be_a_final_pass(self) -> None:
        checks = {
            name: True
            for name in importer_required_checks(IMPORTER.read_text(encoding="utf-8"))
        }

        completed, verdict = self._run_importer(
            checks, release_binding="development"
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIs(verdict["passed"], False)
        self.assertIs(verdict["development_preflight"], True)
        self.assertIn(
            "development_preflight_not_release_evidence", verdict["identity_failures"]
        )

    def test_hashed_lock_release_evidence_is_accepted(self) -> None:
        checks = {
            name: True
            for name in importer_required_checks(IMPORTER.read_text(encoding="utf-8"))
        }
        # A release setup has superseded the former constraints-only mode.
        checks["wheel_install_binary_only"] = False
        checks.update({name: True for name in HASHED_LOCK_CHECKS})

        completed, verdict = self._run_importer(checks)

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIs(verdict["passed"], True)
        self.assertTrue(HASHED_LOCK_CHECKS.issubset(set(verdict["required_checks"])))
        self.assertNotIn("wheel_install_binary_only", verdict["required_checks"])

    def test_legacy_constraints_only_release_evidence_is_rejected(self) -> None:
        checks = {
            name: True
            for name in importer_required_checks(IMPORTER.read_text(encoding="utf-8"))
        }
        checks["wheel_install_binary_only"] = True
        checks.update({name: False for name in HASHED_LOCK_CHECKS})

        completed, verdict = self._run_importer(checks)

        self.assertNotEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIs(verdict["passed"], False)
        self.assertTrue(
            HASHED_LOCK_CHECKS.intersection(set(verdict["failed_checks"]))
            or HASHED_LOCK_CHECKS.intersection(set(verdict["missing_checks"]))
        )

    def test_importer_requires_executable_native_and_provenance_evidence(self) -> None:
        required = set(importer_required_checks(IMPORTER.read_text(encoding="utf-8")))

        self.assertTrue(NATIVE_RELEASE_EVIDENCE_CHECKS.issubset(required))

    def test_importer_rejects_duplicate_required_check_even_if_last_value_is_true(self) -> None:
        checks = {
            name: True
            for name in importer_required_checks(IMPORTER.read_text(encoding="utf-8"))
        }
        entries = [
            {"name": name, "passed": passed}
            for name, passed in sorted(checks.items())
        ]
        entries.extend(
            [
                {"name": "mps_gate_pass", "passed": False},
                {"name": "mps_gate_pass", "passed": True},
            ]
        )

        completed, verdict = self._run_importer(checks, check_entries=entries)

        self.assertNotEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIs(verdict["passed"], False)
        self.assertIn("duplicate_check_name", verdict["malformed_evidence"])

    def test_importer_rejects_duplicate_json_object_keys(self) -> None:
        checks = {
            name: True
            for name in importer_required_checks(IMPORTER.read_text(encoding="utf-8"))
        }
        # The stock JSON decoder keeps the last value for duplicate keys.  A
        # forged older schema followed by the accepted v2 schema must instead
        # fail closed before any required-check evaluation.
        valid = json.dumps(
            {
                "schema": "totalsegmentator_wrapper_mac.test_account_install_evidence.v2",
                "passed": True,
            },
            ensure_ascii=False,
        )
        duplicate_schema = '{"schema":"forged-legacy",' + valid[1:]

        completed, verdict = self._run_importer(
            checks,
            raw_evidence=duplicate_schema,
        )

        self.assertNotEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIs(verdict["passed"], False)
        self.assertIn("duplicate_json_key:schema", verdict["malformed_evidence"])

    def test_importer_rejects_truthy_string_check_value(self) -> None:
        checks = {
            name: True
            for name in importer_required_checks(IMPORTER.read_text(encoding="utf-8"))
        }
        entries = [
            {"name": name, "passed": ("true" if name == "mps_gate_pass" else passed)}
            for name, passed in sorted(checks.items())
        ]

        completed, verdict = self._run_importer(checks, check_entries=entries)

        self.assertNotEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIs(verdict["passed"], False)
        self.assertIn("check_passed_not_boolean:mps_gate_pass", verdict["malformed_evidence"])

    def test_importer_rejects_noncanonical_top_level_schema_fields(self) -> None:
        checks = {
            name: True
            for name in importer_required_checks(IMPORTER.read_text(encoding="utf-8"))
        }

        completed, verdict = self._run_importer(
            checks,
            payload_overrides={"unreviewed_extension": "forged"},
        )

        self.assertNotEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIs(verdict["passed"], False)
        self.assertIn("evidence_field_set_mismatch", verdict["malformed_evidence"])

    def test_importer_rejects_stale_evidence(self) -> None:
        checks = {
            name: True
            for name in importer_required_checks(IMPORTER.read_text(encoding="utf-8"))
        }
        stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat().replace(
            "+00:00", "Z"
        )

        completed, verdict = self._run_importer(
            checks,
            payload_overrides={"collected_at_utc": stale},
        )

        self.assertNotEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIs(verdict["passed"], False)
        self.assertIn("evidence_is_stale", verdict["identity_failures"])

    def test_importer_rejects_symlink_evidence_pointer(self) -> None:
        checks = {
            name: True
            for name in importer_required_checks(IMPORTER.read_text(encoding="utf-8"))
        }

        completed, verdict = self._run_importer(checks, source_as_symlink=True)

        self.assertNotEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIs(verdict["passed"], False)
        self.assertIn("evidence_source_must_be_regular_non_symlink", verdict["malformed_evidence"])

    def test_importer_writes_a_failed_verdict_for_a_missing_source(self) -> None:
        completed, verdict = self._run_importer({}, source_missing=True)

        self.assertNotEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIs(verdict["passed"], False)
        self.assertTrue(
            any(
                str(reason).startswith("evidence_source_missing:")
                for reason in verdict["malformed_evidence"]
            )
        )

    def test_importer_binds_evidence_identity_to_project_version(self) -> None:
        checks = {
            name: True
            for name in importer_required_checks(IMPORTER.read_text(encoding="utf-8"))
        }
        identity = {
            "app_version": "0.4.0",
            "build_id": "fixture-build",
            "dependency_set_id": "fixture-dependencies",
            "setup_manifest_sha256": "a" * 64,
            "info_plist_sha256": "b" * 64,
            "dmg_path": None,
            "dmg_sha256": None,
        }

        completed, verdict = self._run_importer(
            checks,
            payload_overrides={
                "expected_app_version": "0.4.0",
                "app_identity": identity,
            },
        )

        self.assertNotEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIs(verdict["passed"], False)
        self.assertIn("app_version_does_not_match_project_version", verdict["identity_failures"])

    def test_collector_contract_supersedes_stale_evidence_and_uses_atomic_publish(self) -> None:
        collector = (ROOT / "scripts" / "collect_test_account_install_evidence.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("RUN_ID=", collector)
        self.assertIn("superseded-", collector)
        self.assertIn("write_json_atomically", collector)
        self.assertIn("collected_at_utc", collector)
        self.assertIn("app_identity", collector)
        self.assertIn('state.get("wheel_install_mode") == "network_require_hashes_lock"', collector)
        self.assertIn('state.get("wheel_install_mode") == "network_constraints_binary_only"', collector)
        self.assertIn('"dependency_wheelhouse_manifest_sha256"', collector)
        self.assertIn('bundled.get("dependency_wheelhouse_manifest")', collector)
        self.assertNotIn('mkdir -p "${SHARED_EVIDENCE_DIR}" || true', collector)
        self.assertNotIn('cp "${EVIDENCE_JSON}" "${SHARED_EVIDENCE_JSON}" || true', collector)

    def test_collector_embedded_python_compiles(self) -> None:
        collector = (ROOT / "scripts" / "collect_test_account_install_evidence.sh").read_text(
            encoding="utf-8"
        )
        match = re.search(r"<<'PY'\n(?P<body>.*?)\nPY\nPYTHON_STATUS", collector, re.DOTALL)
        self.assertIsNotNone(match)
        assert match is not None

        compile(match.group("body"), "collect_test_account_install_evidence.py", "exec")

    def test_collector_failure_supersedes_prior_local_and_shared_pass_evidence(self) -> None:
        collector = ROOT / "scripts" / "collect_test_account_install_evidence.sh"
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "test-home"
            app = home / "Applications" / "TotalSegmentator Wrapper for Mac.app"
            resources = app / "Contents" / "Resources"
            resources.mkdir(parents=True)
            (resources / "setup_manifest.json").write_text("{}", encoding="utf-8")
            support = home / "Library" / "Application Support" / "TotalSegmentatorWrapperMac"
            (support / "env" / "bin").mkdir(parents=True)
            (support / "env" / "bin" / "python").symlink_to(sys.executable)
            state = support / "setup_state.json"
            state.write_text("{}", encoding="utf-8")
            local_evidence = support / "logs" / "test_account_install_evidence.json"
            local_evidence.parent.mkdir(parents=True)
            shared = home / "Shared" / "test_account_install_evidence.json"
            shared.parent.mkdir(parents=True)
            # This is deliberately a fresh, otherwise importer-compatible v2
            # record.  A plain rename to `.superseded-*` would leave it usable
            # if an operator selected that path manually.
            old_checks = {
                name: True
                for name in importer_required_checks(IMPORTER.read_text(encoding="utf-8"))
            }
            stale = json.dumps(
                {
                    "schema": "totalsegmentator_wrapper_mac.test_account_install_evidence.v2",
                    "passed": True,
                    "run_id": "f" * 32,
                    "collected_at_utc": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "home": "/Users/separate-release-test-account",
                    "app_path": "/Applications/TotalSegmentator Wrapper for Mac.app",
                    "support_dir": "/Users/separate-release-test-account/Library/Application Support/TotalSegmentatorWrapperMac",
                    "state_json": "/Users/separate-release-test-account/Library/Application Support/TotalSegmentatorWrapperMac/setup_state.json",
                    "manifest_path": "/Applications/TotalSegmentator Wrapper for Mac.app/Contents/Resources/setup_manifest.json",
                    "shared_copy_path": "/Users/Shared/TotalSegmentatorWrapperMac/test_account_install_evidence.json",
                    "expected_app_version": "0.4.1",
                    "app_identity": {
                        "app_version": "0.4.1",
                        "build_id": "old-fixture-build",
                        "dependency_set_id": "old-fixture-dependencies",
                        "setup_manifest_sha256": "a" * 64,
                        "info_plist_sha256": "b" * 64,
                        "dmg_path": None,
                        "dmg_sha256": None,
                    },
                    "checks": [
                        {"name": name, "passed": passed}
                        for name, passed in sorted(old_checks.items())
                    ],
                },
                ensure_ascii=False,
            )
            local_evidence.write_text(stale, encoding="utf-8")
            shared.write_text(stale, encoding="utf-8")

            environment = dict(os.environ)
            environment.update(
                {
                    "HOME": str(home),
                    "PYTHONPATH": str(ROOT / "src"),
                    "TOTALSEGMENTATOR_WRAPPER_MAC_SHARED_EVIDENCE_DIR": str(shared.parent),
                }
            )
            completed = subprocess.run(
                ["/bin/bash", str(collector), str(app)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            current_local = json.loads(local_evidence.read_text(encoding="utf-8"))
            current_shared = json.loads(shared.read_text(encoding="utf-8"))
            self.assertEqual(
                current_local["schema"],
                "totalsegmentator_wrapper_mac.test_account_install_evidence.v2",
            )
            self.assertIs(current_local["passed"], False)
            self.assertEqual(current_shared["run_id"], current_local["run_id"])
            self.assertIs(current_shared["passed"], False)
            local_superseded = list(local_evidence.parent.glob("*.superseded-*"))
            shared_superseded = list(shared.parent.glob("*.superseded-*"))
            self.assertEqual(len(local_superseded), 1)
            self.assertEqual(len(shared_superseded), 1)
            self.assertTrue(local_superseded[0].name.endswith(current_local["run_id"]))
            self.assertTrue(shared_superseded[0].name.endswith(current_local["run_id"]))
            superseded_payload = json.loads(local_superseded[0].read_text(encoding="utf-8"))
            self.assertEqual(
                superseded_payload["schema"],
                "totalsegmentator_wrapper_mac.test_account_install_evidence.superseded.v1",
            )
            self.assertIs(superseded_payload["passed"], False)
            self.assertEqual(superseded_payload["superseded_by_run_id"], current_local["run_id"])
            self.assertEqual(superseded_payload["archived_evidence_encoding"], "base64")

    def test_collector_preflight_record_does_not_serialize_untrusted_app_path(self) -> None:
        """Early shell-only diagnostics remain valid JSON for hostile path text."""

        collector = ROOT / "scripts" / "collect_test_account_install_evidence.sh"
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "test-home"
            shared = home / "Shared" / "test_account_install_evidence.json"
            # The supplied app path is untrusted operator input.  It must never
            # be interpolated into the minimal JSON written before Python is
            # available; only the bounded collector-owned code is recorded.
            hostile_app_path = home / 'not-an-app-"}\\n{"forged":true}'
            environment = dict(os.environ)
            environment.update(
                {
                    "HOME": str(home),
                    "TOTALSEGMENTATOR_WRAPPER_MAC_SHARED_EVIDENCE_DIR": str(shared.parent),
                }
            )
            completed = subprocess.run(
                ["/bin/bash", str(collector), str(hostile_app_path)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            local_evidence = (
                home
                / "Library"
                / "Application Support"
                / "TotalSegmentatorWrapperMac"
                / "logs"
                / "test_account_install_evidence.json"
            )
            payload = json.loads(local_evidence.read_text(encoding="utf-8"))
            self.assertEqual(
                payload,
                {
                    "schema": "totalsegmentator_wrapper_mac.test_account_install_preflight_failure.v1",
                    "passed": False,
                    "run_id": payload["run_id"],
                    "collected_at_utc": payload["collected_at_utc"],
                    "preflight_failure": "app_path_is_not_a_directory",
                },
            )
            self.assertNotIn('"forged":true', local_evidence.read_text(encoding="utf-8"))

    def test_collector_missing_runtime_replaces_prior_pass_evidence(self) -> None:
        """A preflight failure must not leave the old normal-path PASS in place."""

        collector = ROOT / "scripts" / "collect_test_account_install_evidence.sh"
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "test-home"
            app = home / "Applications" / "TotalSegmentator Wrapper for Mac.app"
            (app / "Contents" / "Resources").mkdir(parents=True)
            support = home / "Library" / "Application Support" / "TotalSegmentatorWrapperMac"
            support.mkdir(parents=True)
            (support / "setup_state.json").write_text("{}", encoding="utf-8")
            local_evidence = support / "logs" / "test_account_install_evidence.json"
            local_evidence.parent.mkdir(parents=True)
            shared = home / "Shared" / "test_account_install_evidence.json"
            shared.parent.mkdir(parents=True)
            stale = '{"schema":"totalsegmentator_wrapper_mac.test_account_install_evidence.v1","passed":true}'
            local_evidence.write_text(stale, encoding="utf-8")
            shared.write_text(stale, encoding="utf-8")

            environment = dict(os.environ)
            environment.update(
                {
                    "HOME": str(home),
                    "TOTALSEGMENTATOR_WRAPPER_MAC_SHARED_EVIDENCE_DIR": str(shared.parent),
                }
            )
            completed = subprocess.run(
                ["/bin/bash", str(collector), str(app)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            current_local = json.loads(local_evidence.read_text(encoding="utf-8"))
            current_shared = json.loads(shared.read_text(encoding="utf-8"))
            self.assertEqual(
                current_local["schema"],
                "totalsegmentator_wrapper_mac.test_account_install_preflight_failure.v1",
            )
            self.assertEqual(
                set(current_local),
                {"schema", "passed", "run_id", "collected_at_utc", "preflight_failure"},
            )
            self.assertIs(current_local["passed"], False)
            self.assertRegex(current_local["run_id"], r"^[0-9a-f]{32}$")
            self.assertRegex(
                current_local["collected_at_utc"],
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
            )
            self.assertEqual(
                current_local["preflight_failure"],
                "setup_runtime_python_missing",
            )
            self.assertEqual(current_shared["run_id"], current_local["run_id"])
            self.assertEqual(current_shared["collected_at_utc"], current_local["collected_at_utc"])
            self.assertEqual(current_shared["preflight_failure"], current_local["preflight_failure"])
            self.assertIs(current_shared["passed"], False)
            local_superseded = list(local_evidence.parent.glob("*.superseded-*"))
            shared_superseded = list(shared.parent.glob("*.superseded-*"))
            self.assertEqual(len(local_superseded), 1)
            self.assertEqual(len(shared_superseded), 1)
            self.assertTrue(local_superseded[0].name.endswith(current_local["run_id"]))
            self.assertTrue(shared_superseded[0].name.endswith(current_local["run_id"]))

            import_root = Path(temporary) / "import-fixture"
            (import_root / "scripts").mkdir(parents=True)
            copied_importer = import_root / "scripts" / IMPORTER.name
            shutil.copy2(IMPORTER, copied_importer)
            (import_root / "pyproject.toml").write_text(
                '[project]\nname = "fixture"\nversion = "0.4.1"\n',
                encoding="utf-8",
            )
            import_environment = dict(environment)
            import_environment["PYTHON_BIN"] = sys.executable
            imported = subprocess.run(
                ["/bin/bash", str(copied_importer), str(local_evidence)],
                cwd=import_root,
                env=import_environment,
                check=False,
                capture_output=True,
                text=True,
            )
            verdicts = list(
                (import_root / "artifacts" / "test_account_install").glob(
                    "*/test_account_install_verdict.json"
                )
            )
            self.assertNotEqual(imported.returncode, 0, imported.stderr + imported.stdout)
            self.assertEqual(len(verdicts), 1, imported.stderr + imported.stdout)
            imported_verdict = json.loads(verdicts[0].read_text(encoding="utf-8"))
            self.assertIs(imported_verdict["passed"], False)
            self.assertEqual(imported_verdict["evidence_run_id"], current_local["run_id"])
            self.assertIn(
                "preflight_failure_evidence_cannot_be_imported",
                imported_verdict["malformed_evidence"],
            )
            self.assertNotIn("unexpected_evidence_schema", imported_verdict["malformed_evidence"])

            imported_superseded = subprocess.run(
                ["/bin/bash", str(copied_importer), str(local_superseded[0])],
                cwd=import_root,
                env=import_environment,
                check=False,
                capture_output=True,
                text=True,
            )
            all_verdicts = list(
                (import_root / "artifacts" / "test_account_install").glob(
                    "*/test_account_install_verdict.json"
                )
            )
            self.assertNotEqual(
                imported_superseded.returncode,
                0,
                imported_superseded.stderr + imported_superseded.stdout,
            )
            self.assertEqual(len(all_verdicts), 2, imported_superseded.stderr + imported_superseded.stdout)
            superseded_verdict = next(
                json.loads(path.read_text(encoding="utf-8"))
                for path in all_verdicts
                if json.loads(path.read_text(encoding="utf-8"))["source_evidence"]
                == str(local_superseded[0])
            )
            self.assertIs(superseded_verdict["passed"], False)
            self.assertIn("unexpected_evidence_schema", superseded_verdict["malformed_evidence"])


if __name__ == "__main__":
    unittest.main()
