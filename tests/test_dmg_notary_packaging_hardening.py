from __future__ import annotations

import hashlib
import tempfile
import os
import subprocess
import sys
import json
import unittest
import zipfile
from pathlib import Path

from scripts.verify_license_distribution import (
    find_tree_model_payloads,
    verified_authorized_sample_nifti_paths,
)


ROOT = Path(__file__).resolve().parents[1]


def make_clean_script_repo(tmp: str, script_name: str) -> tuple[Path, Path]:
    repo = Path(tmp) / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    (scripts / script_name).write_text(
        (ROOT / "scripts" / script_name).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.4.1"\n',
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("dist/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Fixture"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "fixture"],
        check=True,
    )
    dist = repo / "dist"
    dist.mkdir(mode=0o700)
    return repo, dist


class DmgNotaryPackagingHardeningTests(unittest.TestCase):
    def test_distribution_scanner_rejects_private_meshes_and_all_model_formats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payloads = []
            for name in (
                "private_scan.ply",
                "private_result.stl",
                "private_result.obj",
                "private_result.off",
                "private_result.glb",
                "private_result.gltf",
                "private_result.3mf",
                "renamed-small-checkpoint.h5",
                "renamed.safetensors",
                "renamed.onnx",
            ):
                path = root / name
                path.write_bytes(b"payload")
                payloads.append(path)

            self.assertEqual(
                find_tree_model_payloads(
                    root,
                    payloads,
                    reject_all_checkpoint_extensions=True,
                    reject_private_meshes=True,
                ),
                sorted(path.name for path in payloads),
            )

            benign_pth = root / "python-path.pth"
            benign_pth.write_text("# path configuration\nimport site\nrelative-path\n", encoding="utf-8")
            self.assertEqual(
                find_tree_model_payloads(
                    root,
                    [benign_pth],
                    reject_all_checkpoint_extensions=True,
                    reject_private_meshes=True,
                ),
                [],
            )

    def test_distribution_scanner_allows_only_exact_cpython_lib2to3_pickles_in_verified_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "app" / "python" / "cpython-3.12"
            lib2to3 = runtime / "lib" / "python3.12" / "lib2to3"
            lib2to3.mkdir(parents=True)
            grammar = lib2to3 / "Grammar3.12.11.final.0.pickle"
            pattern = lib2to3 / "PatternGrammar3.12.11.final.0.pickle"
            grammar.write_bytes(b"cpython grammar cache")
            pattern.write_bytes(b"cpython pattern grammar cache")
            renamed_inside = lib2to3 / "TGNet3.12.11.final.0.pickle"
            renamed_inside.write_bytes(b"model")
            arbitrary_outside = root / "arbitrary.pickle"
            arbitrary_outside.write_bytes(b"model")

            self.assertEqual(
                find_tree_model_payloads(
                    root,
                    [grammar, pattern, renamed_inside, arbitrary_outside],
                    reject_all_checkpoint_extensions=True,
                    reject_private_meshes=True,
                    verified_cpython_runtime_root=runtime,
                ),
                [
                    "app/python/cpython-3.12/lib/python3.12/lib2to3/TGNet3.12.11.final.0.pickle",
                    "arbitrary.pickle",
                ],
            )

    def test_distribution_scanner_rejects_uninspected_compound_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archives = []
            for name in (
                "ckpts.tar.gz",
                "payload.tgz",
                "payload.tar.xz",
                "payload.txz",
                "payload.tar.bz2",
                "payload.7z",
                "payload.rar",
            ):
                path = root / name
                path.write_bytes(b"opaque archive")
                archives.append(path)

            self.assertEqual(
                find_tree_model_payloads(
                    root,
                    archives,
                    reject_all_checkpoint_extensions=True,
                    reject_private_meshes=True,
                ),
                sorted(path.name for path in archives),
            )
            carrier = root / "carrier.whl"
            with zipfile.ZipFile(carrier, "w") as archive:
                archive.writestr("package/opaque.tar.gz", b"hidden")
            self.assertEqual(
                find_tree_model_payloads(
                    root,
                    [carrier],
                    reject_all_checkpoint_extensions=True,
                    reject_private_meshes=True,
                ),
                ["carrier.whl!package/opaque.tar.gz"],
            )

    def test_distribution_scanner_rejects_zip_beyond_nested_depth_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deepest = root / "deepest.zip"
            with zipfile.ZipFile(deepest, "w") as archive:
                archive.writestr("weights/model.h5", b"weight")
                archive.writestr("scan/private.ply", b"mesh")
                archive.writestr(
                    "scan/private.bin",
                    b"\x00" * 128 + b"DICM" + b"dicom",
                )
            middle = root / "middle.zip"
            with zipfile.ZipFile(middle, "w") as archive:
                archive.writestr("nested/deepest.zip", deepest.read_bytes())
            outer = root / "outer.zip"
            with zipfile.ZipFile(outer, "w") as archive:
                archive.writestr("nested/middle.zip", middle.read_bytes())

            found = find_tree_model_payloads(
                root,
                [outer],
                reject_all_checkpoint_extensions=True,
                reject_private_meshes=True,
                reject_private_medical_images=True,
            )
            self.assertEqual(len(found), 1)
            self.assertIn("nested ZIP depth limit", found[0])

    def test_resource_symlink_policy_allows_only_safe_verified_runtime_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Resources"
            runtime = root / "python" / "cpython-3.12"
            runtime_lib = runtime / "lib"
            runtime_lib.mkdir(parents=True)
            target = runtime_lib / "libpython3.12.dylib"
            target.write_bytes(b"runtime")
            safe_link = runtime / "libpython3.12.dylib"
            safe_link.symlink_to("lib/libpython3.12.dylib")

            outside = Path(tmp) / "patient.bin"
            outside.write_bytes(b"\x00" * 128 + b"DICM" + b"private")
            escaping = root / "innocent.txt"
            escaping.symlink_to("../../patient.bin")
            absolute = root / "absolute.txt"
            absolute.symlink_to(outside)
            ordinary_target = root / "ordinary.txt"
            ordinary_target.write_text("ordinary", encoding="utf-8")
            ordinary_link = root / "ordinary-link.txt"
            ordinary_link.symlink_to("ordinary.txt")

            found = find_tree_model_payloads(
                root,
                [safe_link, escaping, absolute, ordinary_link],
                reject_all_checkpoint_extensions=True,
                reject_private_meshes=True,
                reject_private_medical_images=True,
                verified_cpython_runtime_root=runtime,
            )
            self.assertEqual(
                found,
                ["absolute.txt", "innocent.txt", "ordinary-link.txt"],
            )

    def test_distribution_scanner_keeps_the_three_expected_benign_wheels(self) -> None:
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheels = []
            for name in (
                "totalsegmentator_wrapper_mac-0.4.1-cp312-cp312-macosx_14_0_arm64.whl",
                "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl",
                "acvl_utils-0.2.6-py3-none-any.whl",
            ):
                path = root / name
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("package/__init__.py", "")
                wheels.append(path)

            self.assertEqual(
                find_tree_model_payloads(
                    root,
                    wheels,
                    reject_all_checkpoint_extensions=True,
                    reject_private_meshes=True,
                ),
                [],
            )

    def test_distribution_scanner_rejects_medical_images_except_exact_sample1(self) -> None:
        resources = ROOT / "resources"
        authorized = verified_authorized_sample_nifti_paths(resources)
        self.assertEqual(len(authorized), 2)
        self.assertEqual(
            find_tree_model_payloads(
                resources,
                sorted(authorized),
                reject_all_checkpoint_extensions=True,
                reject_private_meshes=True,
                reject_private_medical_images=True,
                authorized_medical_image_paths=authorized,
            ),
            [],
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private_images = []
            for name in (
                "patient.dcm",
                "patient.dicom",
                "patient.ima",
                "patient.nii",
                "patient.nii.gz",
            ):
                path = root / name
                path.write_bytes(b"private medical image")
                private_images.append(path)
            magic = root / "renamed-private.bin"
            magic.write_bytes(b"\x00" * 128 + b"DICM" + b"private")
            private_images.append(magic)

            self.assertEqual(
                find_tree_model_payloads(
                    root,
                    private_images,
                    reject_all_checkpoint_extensions=True,
                    reject_private_meshes=True,
                    reject_private_medical_images=True,
                ),
                sorted(path.name for path in private_images),
            )

    def test_notary_rejects_symlinked_notary_directory_before_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            protected = root / "protected"
            protected.mkdir()
            sentinel = protected / "notary_submission.json"
            sentinel.write_text("preserve", encoding="utf-8")
            (dist / "notary").symlink_to(protected, target_is_directory=True)
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHON_BIN": sys.executable,
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR": str(dist),
                }
            )

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "notarize_mac_dmg.sh")],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("Notary directory must be owner-controlled and non-symlink", result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_packaging_rejects_group_or_world_writable_control_directories(self) -> None:
        scripts = (
            "build_mac_dmg.sh",
            "build_mac_wheel.sh",
            "notarize_mac_dmg.sh",
        )
        for script_name in scripts:
            with self.subTest(script=script_name), tempfile.TemporaryDirectory() as tmp:
                dist = Path(tmp) / "dist"
                dist.mkdir(mode=0o777)
                dist.chmod(0o777)
                environment = os.environ.copy()
                environment.update(
                    {
                        "PYTHON_BIN": sys.executable,
                        "TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR": str(dist),
                    }
                )
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts" / script_name)],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertTrue(
                    "owner-controlled" in result.stderr
                    or "unsafe wheel distribution directory" in result.stderr,
                    result.stderr,
                )

    def test_notary_rejects_group_or_world_writable_notary_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            dist.mkdir(mode=0o700)
            notary = dist / "notary"
            notary.mkdir(mode=0o777)
            notary.chmod(0o777)
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHON_BIN": sys.executable,
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR": str(dist),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "notarize_mac_dmg.sh")],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("Notary directory must be owner-controlled", result.stderr)

    def test_notary_requires_existing_canonical_dmg_and_receipt_as_a_bound_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, dist = make_clean_script_repo(tmp, "notarize_mac_dmg.sh")
            notary = dist / "notary"
            notary.mkdir(mode=0o700)
            dmg = dist / "TotalSegmentator Wrapper for Mac-0.4.1-release-arm64.dmg"
            receipt = notary / "notary-release-receipt.json"
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHON_BIN": sys.executable,
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR": str(dist),
                }
            )

            dmg.write_bytes(b"old canonical")
            only_dmg = subprocess.run(
                ["bash", str(repo / "scripts" / "notarize_mac_dmg.sh")],
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(only_dmg.returncode, 2)
            self.assertIn("both exist or both be absent", only_dmg.stderr)

            dmg.unlink()
            receipt.write_text("{}\n", encoding="utf-8")
            only_receipt = subprocess.run(
                ["bash", str(repo / "scripts" / "notarize_mac_dmg.sh")],
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(only_receipt.returncode, 2)
            self.assertIn("both exist or both be absent", only_receipt.stderr)

    def test_notary_rejects_existing_receipt_not_bound_to_canonical_dmg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, dist = make_clean_script_repo(tmp, "notarize_mac_dmg.sh")
            notary = dist / "notary"
            notary.mkdir(mode=0o700)
            dmg = dist / "TotalSegmentator Wrapper for Mac-0.4.1-release-arm64.dmg"
            dmg.write_bytes(b"old canonical")
            source_commit = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            receipt = notary / "notary-release-receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "schema": "totalsegmentator_wrapper_mac.notary_release_receipt.v1",
                        "version": "0.4.1",
                        "source_commit": source_commit,
                        "dmg_filename": dmg.name,
                        "final_dmg_sha256": "0" * 64,
                        "final_dmg_size_bytes": dmg.stat().st_size,
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHON_BIN": sys.executable,
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR": str(dist),
                }
            )
            result = subprocess.run(
                ["bash", str(repo / "scripts" / "notarize_mac_dmg.sh")],
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("canonical DMG SHA-256 mismatch", result.stderr)

    def test_notary_rollback_restores_exact_previous_pair_bytes(self) -> None:
        source = (ROOT / "scripts" / "notarize_mac_dmg.sh").read_text(
            encoding="utf-8"
        )
        rollback_function = source[
            source.index("rollback_publication()") : source.index(
                "write_failure_state()"
            )
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            notary = dist / "notary"
            run = notary / "run-fixture"
            run.mkdir(parents=True)
            canonical_dmg = dist / "candidate.dmg"
            canonical_receipt = notary / "notary-release-receipt.json"
            backup_dmg = run / "previous-canonical.dmg"
            backup_receipt = run / "previous-receipt.json"
            old_dmg = b"old-dmg-exact-bytes"
            old_receipt = b'{"old":"receipt-exact-bytes"}\n'
            new_dmg = b"new-dmg"
            new_receipt = b'{"new":"receipt"}\n'
            canonical_dmg.write_bytes(new_dmg)
            canonical_receipt.write_bytes(new_receipt)
            backup_dmg.write_bytes(old_dmg)
            backup_receipt.write_bytes(old_receipt)
            pending = dist / ".pending.dmg"
            harness = root / "rollback.sh"
            harness.write_text(
                "set -euo pipefail\n"
                + rollback_function
                + f'''\nDIST_DIR={json.dumps(str(dist))}
NOTARY_DIR={json.dumps(str(notary))}
NOTARY_RUN_ID=fixture
DMG_PATH={json.dumps(str(canonical_dmg))}
RECEIPT_PATH={json.dumps(str(canonical_receipt))}
PENDING_DMG_PATH={json.dumps(str(pending))}
RECEIPT_STAGED={json.dumps(str(run / "staged-receipt.json"))}
PREVIOUS_DMG_BACKUP={json.dumps(str(backup_dmg))}
PREVIOUS_RECEIPT_BACKUP={json.dumps(str(backup_receipt))}
PREVIOUS_DMG_PRESENT=1
PREVIOUS_RECEIPT_PRESENT=1
PUBLICATION_STARTED=1
NOTARY_COMPLETED=0
rollback_publication
''',
                encoding="utf-8",
            )
            subprocess.run(["bash", str(harness)], check=True)
            self.assertEqual(canonical_dmg.read_bytes(), old_dmg)
            self.assertEqual(canonical_receipt.read_bytes(), old_receipt)
            self.assertEqual(pending.read_bytes(), new_dmg)

    def test_dmg_run_staging_collision_preserves_other_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, dist = make_clean_script_repo(tmp, "build_mac_dmg.sh")
            collision = dist / ".dmg-staging-collision"
            other = dist / ".dmg-staging-other-run"
            collision.mkdir(mode=0o700)
            other.mkdir(mode=0o700)
            collision_sentinel = collision / "sentinel"
            other_sentinel = other / "sentinel"
            collision_sentinel.write_text("collision", encoding="utf-8")
            other_sentinel.write_text("other", encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHON_BIN": sys.executable,
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR": str(dist),
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DMG_RUN_ID": "collision",
                }
            )
            result = subprocess.run(
                ["bash", str(repo / "scripts" / "build_mac_dmg.sh")],
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("existing DMG run staging directory", result.stderr)
            self.assertEqual(collision_sentinel.read_text(encoding="utf-8"), "collision")
            self.assertEqual(other_sentinel.read_text(encoding="utf-8"), "other")

    def test_dmg_build_is_source_bound_and_publishes_only_verified_partial(self) -> None:
        text = (ROOT / "scripts" / "build_mac_dmg.sh").read_text(encoding="utf-8")

        self.assertIn("EXPECTED_SOURCE_COMMIT", text)
        self.assertIn("--expected-source-commit", text)
        self.assertIn("DMG_PARTIAL_PATH", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DMG_RUN_ID", text)
        self.assertIn('DMG_STAGING="${DIST_DIR}/.dmg-staging-${DMG_RUN_ID}"', text)
        self.assertNotIn('DMG_STAGING="${DIST_DIR}/dmg_staging"', text)
        self.assertIn("dmg_build_exit", text)
        self.assertIn("DMG partial retained", text)
        self.assertIn('mv -f "${DMG_PARTIAL_PATH}" "${DMG_PATH}"', text)
        self.assertLess(
            text.index('verify_license_distribution.py'),
            text.index('mv -f "${DMG_PARTIAL_PATH}" "${DMG_PATH}"'),
        )
        self.assertIn("/usr/bin/hdiutil info", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH", text)
        self.assertIn("Mounted DMGの元image pathを安全に特定できませんでした", text)

    def test_notary_uses_safe_pending_artifact_and_atomic_receipt(self) -> None:
        text = (ROOT / "scripts" / "notarize_mac_dmg.sh").read_text(encoding="utf-8")

        for marker in (
            "validate_owned_notary_paths",
            "PENDING_DMG_PATH",
            "notary-release-receipt.json",
            "submitted_dmg_sha256",
            "final_dmg_sha256",
            "source_commit",
            "app_manifest_sha256",
            "atomic_write_json",
            "--expected-source-commit",
            "umask 077",
            "rollback_publication",
            "notary-failure-state.json",
            "PREVIOUS_DMG_BACKUP",
            "PREVIOUS_RECEIPT_BACKUP",
            "CURRENT_STAGE",
            "TOTALSEGMENTATOR_WRAPPER_MAC_TEST_FAIL_AFTER_DMG_PUBLISH",
        ):
            self.assertIn(marker, text)
        self.assertLess(text.index("validate_owned_notary_paths"), text.index("notarytool history"))
        self.assertLess(text.index("notarytool submit"), text.index("stapler staple"))
        self.assertLess(text.index("stapler staple"), text.index("spctl --assess --type open"))
        receipt_write = text.index('atomic_write_json "${RECEIPT_STAGED}"')
        self.assertLess(text.index("spctl --assess --type execute"), receipt_write)
        self.assertLess(receipt_write, text.index('mv -f "${PENDING_DMG_PATH}" "${DMG_PATH}"'))
        failure_state_body = text[
            text.index("write_failure_state()") : text.index("notarization_exit()")
        ]
        self.assertNotIn("NOTARY_PROFILE", failure_state_body)
        dmg_move = text.index('mv -f "${PENDING_DMG_PATH}" "${DMG_PATH}"')
        injected_failure = text.index(
            "TOTALSEGMENTATOR_WRAPPER_MAC_TEST_FAIL_AFTER_DMG_PUBLISH"
        )
        receipt_move = text.index('mv -f "${RECEIPT_TEMP}" "${RECEIPT_PATH}"')
        self.assertLess(dmg_move, injected_failure)
        self.assertLess(injected_failure, receipt_move)

    def test_wheel_build_uses_run_staging_exact_output_and_receipt(self) -> None:
        text = (ROOT / "scripts" / "build_mac_wheel.sh").read_text(encoding="utf-8")

        for marker in (
            "WHEEL_RUN_DIR",
            "WHEEL_BUILD_OUT_DIR",
            "wheel-build-receipt.json",
            "wheel_sha256",
            'mv -f "${BUILT_WHEEL_PATH}" "${EXPECTED_WHEEL_PATH}"',
            'echo "${EXPECTED_WHEEL_PATH}"',
            "umask 077",
            "path_has_safe_write_mode",
        ):
            self.assertIn(marker, text)

    def test_final_test_account_gate_requires_notary_receipt_digest(self) -> None:
        text = (ROOT / "scripts" / "verify_zero_env_mac_dmg.sh").read_text(encoding="utf-8")

        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_RECEIPT", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_ZERO_ENV_DEVELOPMENT_PREFLIGHT", text)
        self.assertIn("final_dmg_sha256", text)
        self.assertIn("DMG SHA-256 does not match the notary receipt", text)

    def test_zero_env_gate_rejects_receipt_digest_mismatch_before_mount(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg = root / "candidate.dmg"
            dmg.write_bytes(b"not-the-receipted-dmg")
            receipt = root / "receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "schema": "totalsegmentator_wrapper_mac.notary_release_receipt.v1",
                        "version": "0.4.1",
                        "source_commit": "a" * 40,
                        "final_dmg_sha256": "b" * 64,
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHON_BIN": sys.executable,
                    "TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_RECEIPT": str(receipt),
                }
            )

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "verify_zero_env_mac_dmg.sh"), str(dmg)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("DMG SHA-256 does not match the notary receipt", result.stderr)

    def test_dmg_verifier_scans_the_exact_root_allowlist(self) -> None:
        text = (ROOT / "scripts" / "verify_license_distribution.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("DMG_ROOT_ALLOWLIST", text)
        self.assertIn("DMG root entry set mismatch", text)
        self.assertIn(
            "DMG contains non-bundled model, private mesh, or medical-image payloads",
            text,
        )

    def test_notary_docs_include_required_team_and_receipt_contract(self) -> None:
        text = (ROOT / "docs" / "33_MAC_NOTARIZATION.md").read_text(encoding="utf-8")

        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_TEAM_IDENTIFIER", text)
        self.assertIn("notary-release-receipt.json", text)
        self.assertIn("pending", text.lower())
        self.assertIn("成功した場合だけ", text)


if __name__ == "__main__":
    unittest.main()
