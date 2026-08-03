from __future__ import annotations

import base64
import hashlib
import json
import os
import plistlib
import shlex
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.verify_license_distribution import (
    read_wrapper_setup_manager_source,
    validate_tgnet_policy_notice,
    validate_setup_weights_manifest,
    verify_app_version_identity,
    verify_bundled_acvl_utils_wheel,
    verify_bundled_override_release_hash_boundary,
    verify_wheel_release_identity,
)


ROOT = Path(__file__).resolve().parents[1]
WRAPPER_LICENSE = ROOT / "LICENSE"
WRAPPER_NOTICE = ROOT / "NOTICE"
SWIFT_APP_DIR = ROOT / "native" / "macos" / "TotalSegmentatorWrapperForMac"
PROCESS_SUPPORT_SWIFT = SWIFT_APP_DIR / "ProcessSupport.swift"
BUILD_SCRIPT = ROOT / "scripts" / "build_mac_app.sh"
WHEEL_BUILD_SCRIPT = ROOT / "scripts" / "build_mac_wheel.sh"
FPSAMPLE_WHEEL_BUILD_SCRIPT = ROOT / "scripts" / "build_fpsample_wheel_macos.sh"
ACVL_UTILS_WHEEL_BUILD_SCRIPT = ROOT / "scripts" / "build_acvl_utils_wheel.sh"
FPSAMPLE_WHEEL_SIGN_SCRIPT = ROOT / "scripts" / "sign_fpsample_wheel_macos.py"
DICOM_RUNTIME_BUNDLE_SCRIPT = ROOT / "scripts" / "bundle_dicom_normalizer_runtime_macos.sh"
DMG_BUILD_SCRIPT = ROOT / "scripts" / "build_mac_dmg.sh"
NOTARIZE_SCRIPT = ROOT / "scripts" / "notarize_mac_dmg.sh"
DMG_VERIFY_SCRIPT = ROOT / "scripts" / "verify_zero_env_mac_dmg.sh"
EVIDENCE_SCRIPT = ROOT / "scripts" / "collect_test_account_install_evidence.sh"
EVIDENCE_IMPORT_SCRIPT = ROOT / "scripts" / "import_test_account_evidence.sh"
SAMPLE1_ROOT = ROOT / "resources" / "sample1"
SAMPLE1_VIEWER_HTML = SAMPLE1_ROOT / "surface_preview" / "index.html"
SAMPLE1_MANIFEST = SAMPLE1_ROOT / "sample_manifest.json"
SAMPLE1_NOTICES = SAMPLE1_ROOT / "THIRD_PARTY_NOTICES.txt"
TOTALSEGMENTATOR_LICENSE = ROOT / "resources" / "third_party" / "licenses" / "TotalSegmentator-Apache-2.0.txt"
DCM2NIIX_LICENSE = ROOT / "resources" / "third_party" / "licenses" / "dcm2niix-license.txt"
TOOTHSEG_NOTICE = ROOT / "resources" / "third_party" / "licenses" / "ToothSeg-NOTICE.txt"
DENTALSEG_NOTICE = ROOT / "resources" / "third_party" / "licenses" / "DentalSegmentator-NOTICE.txt"
MESHSEGNET_NOTICE = ROOT / "resources" / "third_party" / "licenses" / "MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt"
FPSAMPLE_NOTICE = ROOT / "resources" / "third_party" / "licenses" / "fpsample-1.0.2-MIT-and-nanoflann-BSD.txt"
TGNET_NOTICE = ROOT / "resources" / "third_party" / "licenses" / "TGNet-User-Provided-Checkpoint-NOTICE.txt"
MANUAL_LICENSE_OVERRIDES = ROOT / "resources" / "third_party" / "licenses" / "manual-overrides.json"
LICENSE_INVENTORY_SCRIPT = ROOT / "scripts" / "generate_third_party_license_inventory.py"
LICENSE_DISTRIBUTION_SCRIPT = ROOT / "scripts" / "verify_license_distribution.py"
PYTHON_RUNTIME_FINGERPRINT_SCRIPT = ROOT / "scripts" / "python_runtime_fingerprint.py"
RELEASE_INPUT_READINESS_SCRIPT = ROOT / "scripts" / "verify_release_input_readiness.py"
RELEASE_BUILD_TOOLCHAIN_SCRIPT = ROOT / "scripts" / "release_build_toolchain.py"
RELEASE_COMPONENT_BUILD_RUNNER = ROOT / "scripts" / "run_release_component_build.sh"
OPEN3D_WHEEL_REWRITE_SCRIPT = (
    ROOT / "scripts" / "repair_macos_release_dependency_wheels.py"
)
TOTALSEGMENTATOR_TASK_INVENTORY = ROOT / "resources" / "third_party" / "totalsegmentator_task_inventory.json"
DICOM_RUNTIME_LICENSES = (
    "GDCM-BSD-3-Clause.txt",
    "GDCM-IJG-JPEG-README.txt",
    "OpenJPEG-BSD-2-Clause.txt",
    "CharLS-BSD-3-Clause.txt",
    "Expat-MIT.txt",
    "zlib-Zlib.txt",
    "GDCM-UUID-BSD-3-Clause.txt",
    "GDCM-static-license-inventory.json",
)


class MacAppPackagingTests(unittest.TestCase):
    def test_release_lock_contract_reads_setup_manager_from_the_wrapper_wheel(self) -> None:
        setup_source = (
            "def build_locked_dependencies_install_command(venv_python, *, requirements_lock, wheel_directory):\n"
            "    return [str(venv_python), '-I', '-m', 'pip', '--isolated', 'install', '--require-hashes', '--no-deps', '-r', str(requirements_lock)]\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "totalsegmentator_wrapper_mac-0.4.1-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "totalsegmentator_wrapper_mac/setup_manager.py",
                    setup_source,
                )
            self.assertEqual(read_wrapper_setup_manager_source(wheel), setup_source)

            with zipfile.ZipFile(wheel, "a") as archive:
                archive.writestr(
                    "totalsegmentator_wrapper_mac/setup_manager.py",
                    "# duplicate",
                )
            with self.assertRaisesRegex(RuntimeError, "missing or ambiguous"):
                read_wrapper_setup_manager_source(wheel)

        verifier_source = LICENSE_DISTRIBUTION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("read_wrapper_setup_manager_source(wheels[0])", verifier_source)
        self.assertIn("setup_manager_source_text=", verifier_source)

    def test_app_builder_uses_explicit_or_build_python_base_prefix(self) -> None:
        build_source = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_PYTHON_RUNTIME_DIR", build_source)
        self.assertIn("sys.base_prefix", build_source)
        self.assertIn('PYTHON_RUNTIME_INPUT_KIND="python-base-prefix"', build_source)
        self.assertIn('PYTHON_RUNTIME_INPUT_KIND="explicit-runtime"', build_source)
        self.assertIn("sys.version_info[:2] == (3, 12)", build_source)
        self.assertNotIn("current-runtime.json", build_source)
        self.assertNotIn("python_runtime_bootstrap.py", build_source)
        self.assertNotIn("build_python_runtime_macos14_arm64.sh", build_source)

    def test_bundled_python_runtime_is_fingerprinted_and_smoke_tested_before_inventory(self) -> None:
        fingerprint_source = PYTHON_RUNTIME_FINGERPRINT_SCRIPT.read_text(encoding="utf-8")
        build_source = BUILD_SCRIPT.read_text(encoding="utf-8")
        packaging_docs = (ROOT / "docs" / "06_PACKAGING_DISTRIBUTION.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("FINGERPRINT_FORMAT", fingerprint_source)
        self.assertIn("stat.S_IMODE", fingerprint_source)
        self.assertIn("_safe_relative_symlink_target", fingerprint_source)
        self.assertIn("unsupported filesystem entry", fingerprint_source)
        self.assertIn("assert_self_contained_runtime", fingerprint_source)
        self.assertIn("assert_venv_uses_copied_runtime", fingerprint_source)

        self.assertIn("PYTHON_RUNTIME_FINGERPRINT_SCRIPT", build_source)
        self.assertIn("verify_copied_python_runtime_smoke", build_source)
        self.assertIn("--check-self-contained", build_source)
        self.assertIn("--check-venv-base", build_source)
        self.assertIn("-m ensurepip --version", build_source)
        self.assertIn("-m venv", build_source)
        self.assertIn("-m pip --version", build_source)
        self.assertIn("env -i", build_source)
        self.assertIn('"python_runtime_fingerprint": "${PYTHON_RUNTIME_FINGERPRINT}"', build_source)
        self.assertIn('"fingerprint": "${PYTHON_RUNTIME_FINGERPRINT}"', build_source)
        self.assertIn("PYTHON_RUNTIME_FINGERPRINT:0:12", build_source)
        self.assertNotIn("external-runtime-marker-v1", build_source)
        self.assertIn('PYTHON_RUNTIME_FINGERPRINT=""', build_source)
        self.assertIn('PYTHON_RUNTIME_FINGERPRINT_SCOPE=""', build_source)
        smoke_call = 'verify_copied_python_runtime_smoke "${BUNDLED_PYTHON_RUNTIME_ROOT}"'
        self.assertLess(
            build_source.index('rsync -a "${PYTHON_RUNTIME_SOURCE}/"'),
            build_source.index(smoke_call),
        )
        self.assertLess(
            build_source.index(smoke_call),
            build_source.index("LICENSE_INVENTORY_ARGS=("),
        )
        self.assertIn("copied-runtime-payload-pre-sign-v1", packaging_docs)
        self.assertIn("not** an attestation of final", packaging_docs)
        self.assertIn("codesign, notarization, and DMG verification", packaging_docs)
        self.assertIn("required major/minor `3`/`12`", packaging_docs)

    def test_release_build_fails_closed_on_unhashed_dependencies_and_unverified_weights(self) -> None:
        build_source = BUILD_SCRIPT.read_text(encoding="utf-8")
        readiness_source = RELEASE_INPUT_READINESS_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("verify_release_input_readiness.py", build_source)
        self.assertIn("--constraints", build_source)
        self.assertIn("--setup-weights-manifest", build_source)
        self.assertIn('"${SIGNING_MODE}" == "developer-id"', build_source)
        self.assertIn("verify_setup_manager_hashed_lock_contract", readiness_source)
        self.assertIn("does not structurally install the canonical hashed lock", readiness_source)
        self.assertNotIn("Python runtime source-build receipt", readiness_source)
        self.assertIn("revalidation_required_before_release", readiness_source)

    def test_release_lock_is_required_only_for_release_and_build_identity_binds_it(self) -> None:
        build_source = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_DEVELOPMENT_LICENSE_INVENTORY", build_source)
        self.assertIn("development_constraints", build_source)
        self.assertIn("release_hashed_lock", build_source)
        self.assertIn("RELEASE_DEPENDENCY_LOCK_ATTESTED", build_source)
        self.assertIn("REQUIREMENTS_LOCK_SHA256:0:12", build_source)
        self.assertIn("DEPENDENCY_LOCK_METADATA_SHA256:0:12", build_source)
        self.assertIn('"inventory_mode": "${LICENSE_INVENTORY_MODE}"', build_source)
        self.assertIn('"release_eligible": ${LICENSE_INVENTORY_RELEASE_ELIGIBLE_JSON}', build_source)
        verifier_source = LICENSE_DISTRIBUTION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('third_party_licenses.get("inventory_mode") == "release_hashed_lock"', verifier_source)
        self.assertIn('third_party_licenses.get("release_eligible") is True', verifier_source)
        self.assertIn('"development_constraints", "development_explicit_site_path"', verifier_source)

    def test_swift_setup_registry_check_does_not_hash_large_models_on_ui_refresh(self) -> None:
        source = PROCESS_SUPPORT_SWIFT.read_text(encoding="utf-8")
        start = source.index("func setupWeightsRegistryIsValid")
        end = source.index("\nfunc isLowercaseSHA256", start)
        body = source[start:end]

        self.assertIn('setup_weights_registry.v2', body)
        self.assertIn('required["sha256"]', body)
        self.assertNotIn("SHA256.hash", body)
        self.assertNotIn("setupSHA256HexFile", body)
        self.assertNotIn("FileHandle(forReadingFrom:", body)

    def test_current_packaging_docs_use_041_normal_release_identity(self) -> None:
        packaging = (ROOT / "docs" / "06_PACKAGING_DISTRIBUTION.md").read_text(
            encoding="utf-8"
        )
        test_account = (
            ROOT / "docs" / "28_TEST_ACCOUNT_INSTALL_VERIFICATION.md"
        ).read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        user_manual = (ROOT / "docs" / "USER_MANUAL_JA.md").read_text(
            encoding="utf-8"
        )
        constraints = (
            ROOT / "constraints" / "macos-arm64-py312.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("0.4.1-release-arm64.dmg", packaging)
        self.assertIn("0.4.1-release-arm64.dmg", test_account)
        self.assertIn("macOS 14以降", readme)
        self.assertIn("macOS 14以降", user_manual)
        self.assertIn("macOS 14 or later", packaging)
        self.assertIn("macOS 14 or later", test_account)
        self.assertNotIn("0.4.0-20260731-final", packaging)
        self.assertNotIn("0.1.2-20260708-modelsetup", test_account)
        self.assertNotIn("alpha app", constraints)

    def test_wrapper_source_license_is_apache_2_0_with_explicit_scope(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        license_text = WRAPPER_LICENSE.read_text(encoding="utf-8")
        notice = WRAPPER_NOTICE.read_text(encoding="utf-8")

        self.assertIn('license = "Apache-2.0"', pyproject)
        self.assertIn('license-files = ["LICENSE", "NOTICE"]', pyproject)
        self.assertNotIn("License :: OSI Approved", pyproject)
        self.assertIn("setuptools>=77", pyproject)
        self.assertIn("Apache License", license_text)
        self.assertIn("TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION", license_text)
        self.assertIn("Third-party software", notice)
        self.assertIn("not relicensed", notice)
        self.assertNotIn("LicenseRef-Proprietary", pyproject + notice)
        self.assertIn('"licenses/*.json"', pyproject)
        self.assertIn('"totalseg_setup_weights_manifest.json"', pyproject)

    def test_release_version_is_consistent_across_first_party_components(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        package_init = (
            ROOT / "src" / "totalsegmentator_wrapper_mac" / "__init__.py"
        ).read_text(encoding="utf-8")
        cmake = (ROOT / "native" / "dicom_normalizer" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        normalizer = (
            ROOT / "native" / "dicom_normalizer" / "src" / "main.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn('version = "0.4.1"', pyproject)
        self.assertIn('__version__ = "0.4.1"', package_init)
        self.assertIn("VERSION 0.4.1", cmake)
        self.assertIn('kVersion = "0.4.1"', normalizer)
        self.assertIn("Development Status :: 4 - Beta", pyproject)
        self.assertNotIn("Development Status :: 3 - Alpha", pyproject)

    def test_setup_weights_manifest_is_strict_and_pinned(self) -> None:
        manifest_path = (
            ROOT
            / "src"
            / "totalsegmentator_wrapper_mac"
            / "totalseg_setup_weights_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_setup_weights_manifest(manifest)
        self.assertEqual(
            {asset["task_id"] for asset in manifest["assets"]},
            {113, 115, 297},
        )

        tampered = json.loads(json.dumps(manifest))
        tampered["assets"][0]["url"] = "http://example.test/model.zip"
        with self.assertRaisesRegex(RuntimeError, "official HTTPS GitHub release URL"):
            validate_setup_weights_manifest(tampered)

        swapped = json.loads(json.dumps(manifest))
        swapped["assets"][0]["task_id"], swapped["assets"][1]["task_id"] = (
            swapped["assets"][1]["task_id"],
            swapped["assets"][0]["task_id"],
        )
        with self.assertRaisesRegex(RuntimeError, "asset mapping mismatch"):
            validate_setup_weights_manifest(swapped)

    def test_wheel_release_identity_requires_version_and_setup_weights_manifest(self) -> None:
        canonical_manifest = json.loads(
            (
                ROOT
                / "src"
                / "totalsegmentator_wrapper_mac"
                / "totalseg_setup_weights_manifest.json"
            ).read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "totalsegmentator_wrapper_mac-0.4.1.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "totalsegmentator_wrapper_mac-0.4.1.dist-info/METADATA",
                    "Metadata-Version: 2.4\nName: totalsegmentator-wrapper-mac\nVersion: 0.4.1\n",
                )
                archive.writestr(
                    "totalsegmentator_wrapper_mac/__init__.py",
                    '__version__ = "0.4.1"\n',
                )
                archive.writestr(
                    "totalsegmentator_wrapper_mac/totalseg_setup_weights_manifest.json",
                    json.dumps(canonical_manifest),
                )
            verify_wheel_release_identity(wheel, "0.4.1")

            without_manifest = Path(tmp) / "without-manifest.whl"
            with zipfile.ZipFile(without_manifest, "w") as archive:
                archive.writestr(
                    "totalsegmentator_wrapper_mac-0.4.1.dist-info/METADATA",
                    "Metadata-Version: 2.4\nName: totalsegmentator-wrapper-mac\nVersion: 0.4.1\n",
                )
                archive.writestr(
                    "totalsegmentator_wrapper_mac/__init__.py",
                    '__version__ = "0.4.1"\n',
                )
            with self.assertRaisesRegex(RuntimeError, "setup weights manifest"):
                verify_wheel_release_identity(without_manifest, "0.4.1")

    def test_app_release_identity_rejects_internal_version_drift(self) -> None:
        canonical_manifest = json.loads(
            (
                ROOT
                / "src"
                / "totalsegmentator_wrapper_mac"
                / "totalseg_setup_weights_manifest.json"
            ).read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "TotalSegmentator Wrapper for Mac.app"
            resources = app / "Contents" / "Resources"
            wheels = resources / "wheels"
            binary = resources / "bin" / "totalsegmentator-wrapper-dicom-normalizer"
            wheels.mkdir(parents=True)
            binary.parent.mkdir(parents=True)
            with (app / "Contents" / "Info.plist").open("wb") as handle:
                plistlib.dump(
                    {
                        "CFBundleShortVersionString": "0.4.0",
                        "CFBundleVersion": "0.4.1",
                    },
                    handle,
                )
            (resources / "setup_manifest.json").write_text(
                json.dumps({"version": "0.4.1", "app_version": "0.4.1"}),
                encoding="utf-8",
            )
            wheel = wheels / "totalsegmentator_wrapper_mac-0.4.1.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "totalsegmentator_wrapper_mac-0.4.1.dist-info/METADATA",
                    "Metadata-Version: 2.4\nName: totalsegmentator-wrapper-mac\nVersion: 0.4.1\n",
                )
                archive.writestr(
                    "totalsegmentator_wrapper_mac/__init__.py",
                    '__version__ = "0.4.1"\n',
                )
                archive.writestr(
                    "totalsegmentator_wrapper_mac/totalseg_setup_weights_manifest.json",
                    json.dumps(canonical_manifest),
                )
            binary.write_text("#!/bin/sh\necho 0.4.1\n", encoding="utf-8")
            binary.chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, "CFBundleShortVersionString"):
                verify_app_version_identity(app, "0.4.1")

            with (app / "Contents" / "Info.plist").open("wb") as handle:
                plistlib.dump(
                    {
                        "CFBundleShortVersionString": "0.4.1",
                        "CFBundleVersion": "0.4.1",
                        "CFBundleIdentifier": "jp.example.wrapper",
                        "LSMinimumSystemVersion": "13.0",
                    },
                    handle,
                )
            packaged_manifest = resources / "totalseg_setup_weights_manifest.json"
            packaged_manifest.write_text(
                json.dumps(canonical_manifest),
                encoding="utf-8",
            )
            (resources / "setup_manifest.json").write_text(
                json.dumps(
                    {
                        "version": "0.4.1",
                        "app_version": "0.4.1",
                        "bundle_identifier": "jp.example.wrapper",
                        "signing_mode": "ad-hoc",
                        "team_identifier": None,
                        "bundle_identity_status": "degraded-ad-hoc",
                        "notarization_credentials_configured": False,
                        "source_commit": "1" * 40,
                        "source_tree_dirty": True,
                        "minimum_macos_version": "14.0",
                        "python_runtime": {"bundled": False},
                        "setup_weights_manifest_sha256": hashlib.sha256(
                            packaged_manifest.read_bytes()
                        ).hexdigest(),
                        "bundled": {
                            "totalseg_setup_weights_manifest": packaged_manifest.name,
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "LSMinimumSystemVersion"):
                verify_app_version_identity(app, "0.4.1")

            with (app / "Contents" / "Info.plist").open("wb") as handle:
                plistlib.dump(
                    {
                        "CFBundleShortVersionString": "0.4.1",
                        "CFBundleVersion": "0.4.1",
                        "CFBundleIdentifier": "jp.example.wrapper",
                        "LSMinimumSystemVersion": "14.0",
                    },
                    handle,
            )
            self.assertEqual(verify_app_version_identity(app, "0.4.1"), "0.4.1")
            leaking_profile_manifest = json.loads(
                (resources / "setup_manifest.json").read_text(encoding="utf-8")
            )
            leaking_profile_manifest["notarization_profile_name"] = "operator-local-profile"
            (resources / "setup_manifest.json").write_text(
                json.dumps(leaking_profile_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RuntimeError, "local notarization profile name"
            ):
                verify_app_version_identity(app, "0.4.1")
            leaking_profile_manifest.pop("notarization_profile_name")
            (resources / "setup_manifest.json").write_text(
                json.dumps(leaking_profile_manifest),
                encoding="utf-8",
            )
            external_runtime_manifest = json.loads(
                (resources / "setup_manifest.json").read_text(encoding="utf-8")
            )
            external_runtime_manifest["python_runtime_fingerprint"] = "a" * 64
            external_runtime_manifest["python_runtime"] = {
                "bundled": False,
                "fingerprint": "a" * 64,
            }
            (resources / "setup_manifest.json").write_text(
                json.dumps(external_runtime_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "external app Python runtime"):
                verify_app_version_identity(app, "0.4.1")

            malformed_runtime_manifest = json.loads(json.dumps(external_runtime_manifest))
            malformed_runtime_manifest.pop("python_runtime_fingerprint", None)
            malformed_runtime_manifest["python_runtime"] = {"bundled": "false"}
            (resources / "setup_manifest.json").write_text(
                json.dumps(malformed_runtime_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "bundled flag"):
                verify_app_version_identity(app, "0.4.1")

            bundled_runtime_manifest = json.loads(json.dumps(external_runtime_manifest))
            bundled_runtime_manifest["python_runtime_fingerprint"] = "a" * 64
            bundled_runtime_manifest["python_runtime"] = {
                "bundled": True,
                "bundle_path": "python/cpython-3.12",
                "python_executable": "python/cpython-3.12/bin/python3.12",
                "fingerprint": "a" * 64,
                "fingerprint_scope": "copied-runtime-payload-pre-sign-v1",
                "required_major": 3,
                "required_minor": 12,
            }
            runtime_executable = resources / "python" / "cpython-3.12" / "bin" / "python3.12"
            runtime_executable.parent.mkdir(parents=True)
            runtime_executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            runtime_executable.chmod(0o755)
            (resources / "setup_manifest.json").write_text(
                json.dumps(bundled_runtime_manifest),
                encoding="utf-8",
            )
            self.assertEqual(verify_app_version_identity(app, "0.4.1"), "0.4.1")
            unsafe_runtime_manifest = json.loads(json.dumps(bundled_runtime_manifest))
            unsafe_runtime_manifest["python_runtime"]["python_executable"] = (
                "../outside/python3.12"
            )
            (resources / "setup_manifest.json").write_text(
                json.dumps(unsafe_runtime_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "safe relative path"):
                verify_app_version_identity(app, "0.4.1")
            unsafe_runtime_manifest = json.loads(json.dumps(bundled_runtime_manifest))
            unsafe_runtime_manifest["python_runtime"]["bundle_path"] = "../outside"
            (resources / "setup_manifest.json").write_text(
                json.dumps(unsafe_runtime_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "safe relative path"):
                verify_app_version_identity(app, "0.4.1")
            unsafe_runtime_manifest = json.loads(json.dumps(bundled_runtime_manifest))
            unsafe_runtime_manifest["python_runtime"]["bundle_path"] = "/outside"
            (resources / "setup_manifest.json").write_text(
                json.dumps(unsafe_runtime_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "safe relative path"):
                verify_app_version_identity(app, "0.4.1")
            unsafe_runtime_manifest = json.loads(json.dumps(bundled_runtime_manifest))
            unsafe_runtime_manifest["python_runtime"]["python_executable"] = (
                "python/other/python3.12"
            )
            (resources / "setup_manifest.json").write_text(
                json.dumps(unsafe_runtime_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "inside bundle_path"):
                verify_app_version_identity(app, "0.4.1")
            unsafe_runtime_manifest = json.loads(json.dumps(bundled_runtime_manifest))
            unsafe_runtime_manifest["python_runtime"]["required_minor"] = 11
            (resources / "setup_manifest.json").write_text(
                json.dumps(unsafe_runtime_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "Python 3.12"):
                verify_app_version_identity(app, "0.4.1")
            runtime_executable.unlink()
            runtime_executable.symlink_to("../../../../outside/python3.12")
            (resources / "setup_manifest.json").write_text(
                json.dumps(bundled_runtime_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "symlinked"):
                verify_app_version_identity(app, "0.4.1")
            runtime_executable.unlink()
            runtime_executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            runtime_executable.chmod(0o755)
            bundled_runtime_manifest["python_runtime"]["fingerprint"] = "b" * 64
            (resources / "setup_manifest.json").write_text(
                json.dumps(bundled_runtime_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "runtime fingerprint does not match"):
                verify_app_version_identity(app, "0.4.1")
            bundled_runtime_manifest["python_runtime"]["fingerprint"] = "a" * 64
            (resources / "setup_manifest.json").write_text(
                json.dumps(bundled_runtime_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "source_commit does not match"):
                verify_app_version_identity(
                    app,
                    "0.4.1",
                    expected_source_commit="2" * 40,
                )

            developer_manifest = json.loads(
                (resources / "setup_manifest.json").read_text(encoding="utf-8")
            )
            developer_manifest.update(
                {
                    "signing_mode": "developer-id",
                    "team_identifier": "ABCDE12345",
                    "bundle_identity_status": "verified-developer-id",
                    "source_tree_dirty": False,
                }
            )
            (resources / "setup_manifest.json").write_text(
                json.dumps(developer_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "canonical bundle identifier"):
                verify_app_version_identity(app, "0.4.1")

            release_external_manifest = json.loads(json.dumps(developer_manifest))
            release_external_manifest["python_runtime_fingerprint"] = ""
            release_external_manifest["python_runtime"] = {"bundled": False}
            (resources / "setup_manifest.json").write_text(
                json.dumps(release_external_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "bundled Python runtime"):
                verify_app_version_identity(app, "0.4.1")

            notarized_external_manifest = json.loads(json.dumps(external_runtime_manifest))
            notarized_external_manifest["notarized"] = True
            notarized_external_manifest["notarization_credentials_configured"] = True
            (resources / "setup_manifest.json").write_text(
                json.dumps(notarized_external_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "bundled Python runtime"):
                verify_app_version_identity(app, "0.4.1")

    def test_mac_app_exposes_first_party_and_third_party_license_documents(self) -> None:
        app_source = (
            ROOT
            / "native"
            / "macos"
            / "TotalSegmentatorWrapperForMac"
            / "TotalSegmentatorWrapperForMacApp.swift"
        ).read_text(encoding="utf-8")
        self.assertIn('openBundledDocument("LICENSE")', app_source)
        self.assertIn('openBundledDocument("NOTICE")', app_source)
        self.assertIn(
            'openBundledDocument("licenses/THIRD_PARTY_LICENSES.txt")',
            app_source,
        )

    def test_wheel_build_includes_model_notices_and_task_audit(self) -> None:
        text = WHEEL_BUILD_SCRIPT.read_text(encoding="utf-8")
        for required_name in (
            "TotalSegmentator-Apache-2.0.txt",
            "DentalSegmentator-NOTICE.txt",
            "ToothSeg-NOTICE.txt",
            "MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt",
            "TGNet-User-Provided-Checkpoint-NOTICE.txt",
            "totalsegmentator_task_inventory.json",
            "TotalSegmentator-task-inventory.json",
        ):
            with self.subTest(required_name=required_name):
                self.assertIn(required_name, text)

    def test_fpsample_binary_wheel_is_built_for_supported_macos_and_bundled(self) -> None:
        build_text = FPSAMPLE_WHEEL_BUILD_SCRIPT.read_text(encoding="utf-8")
        app_text = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('FPSAMPLE_VERSION="1.0.2"', build_text)
        self.assertIn('FPSAMPLE_SDIST="fpsample-${FPSAMPLE_VERSION}.tar.gz"', build_text)
        self.assertIn("5e25f97c03412d243767fb9e47f7b6d6c736c7ce1e9d51918894e3fd327749f2", build_text)
        self.assertIn("MACOSX_DEPLOYMENT_TARGET=13.0", build_text)
        self.assertIn("CMAKE_OSX_DEPLOYMENT_TARGET=13.0", build_text)
        self.assertIn("arm64", build_text)
        self.assertIn("sign_fpsample_wheel_macos.py", build_text)
        self.assertIn(
            "TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_FPSAMPLE_PRE_SIGN_SHA256",
            build_text,
        )
        self.assertLess(
            build_text.index("TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_FPSAMPLE_PRE_SIGN_SHA256"),
            build_text.index("sign_fpsample_wheel_macos.py"),
        )
        sign_text = FPSAMPLE_WHEEL_SIGN_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("codesign", sign_text)
        self.assertIn("--timestamp", sign_text)
        self.assertIn("RECORD", sign_text)
        self.assertIn("urlsafe_b64encode", sign_text)
        self.assertIn("fpsample-1.0.2", app_text)
        self.assertIn("FPSAMPLE_WHEEL_SHA256", app_text)
        self.assertIn('"fpsample_wheel_sha256"', app_text)
        self.assertIn('"fpsample_wheel"', app_text)
        verify_text = LICENSE_DISTRIBUTION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("verify_bundled_wheel_code_signing", verify_text)
        self.assertIn("Timestamp=", verify_text)

    def test_acvl_utils_pure_wheel_is_pinned_bundled_and_binary_only(self) -> None:
        build_text = ACVL_UTILS_WHEEL_BUILD_SCRIPT.read_text(encoding="utf-8")
        app_text = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('ACVL_UTILS_VERSION="0.2.6"', build_text)
        self.assertIn(
            "d6bd68a916fb2451ab3dd640b2494e545edc204c839ae1d4dd49f88f89999b74",
            build_text,
        )
        self.assertIn("files.pythonhosted.org", build_text)
        self.assertIn("acvl_utils-0.2.6-py3-none-any.whl", build_text)
        self.assertIn("License-Expression: Apache-2.0", build_text)
        self.assertIn(
            "TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_ACVL_UTILS_WHEEL_SHA256",
            build_text,
        )
        self.assertIn("build_acvl_utils_wheel.sh", app_text)
        self.assertIn("ACVL_UTILS_WHEEL_SHA256", app_text)
        self.assertIn('"acvl_utils_wheel_sha256"', app_text)
        self.assertIn('"acvl_utils_wheel"', app_text)
        self.assertIn("--only-binary :all:", app_text)
        self.assertIn('"${FPSAMPLE_WHEEL_PATH}"', app_text)
        self.assertIn('"${ACVL_UTILS_WHEEL_PATH}"', app_text)
        self.assertNotIn("--only-binary fpsample", app_text)

    def test_release_app_bundles_only_verified_rewritten_open3d_wheel(self) -> None:
        app_text = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(str(OPEN3D_WHEEL_REWRITE_SCRIPT.relative_to(ROOT)), app_text)
        self.assertIn(
            "TOTALSEGMENTATOR_WRAPPER_MAC_OPEN3D_WHEEL_REWRITE",
            app_text,
        )
        for required in (
            "--verify-existing",
            '--output-directory "${OPEN3D_WHEEL_REWRITE_ROOT}"',
            "--require-developer-id",
            '--team-identifier "${TEAM_IDENTIFIER}"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, app_text)
        verify_existing_index = app_text.index("--verify-existing")
        self.assertLess(
            verify_existing_index,
            app_text.index('"${ROOT}/scripts/build_mac_wheel.sh"'),
        )
        self.assertLess(
            verify_existing_index,
            app_text.index('remove_owned_dist_child_directory "${APP_DIR}"'),
        )

        self.assertIn("DEPENDENCY_WHEELHOUSE_MANIFEST_SHA256", app_text)
        self.assertIn('"dependency_wheelhouse_manifest_sha256"', app_text)
        self.assertIn(
            '"dependency_wheelhouse_manifest": ${DEPENDENCY_WHEELHOUSE_MANIFEST_BUNDLED_JSON}',
            app_text,
        )
        self.assertIn("constraints/macos-arm64-py312.wheelhouse.json", app_text)
        self.assertIn('-wheelhouse-${DEPENDENCY_WHEELHOUSE_MANIFEST_SHA256:0:12}', app_text)

        release_inventory_start = app_text.index(
            'if [[ "${RELEASE_DEPENDENCY_LOCK_ATTESTED}" == "1" ]]; then',
            app_text.index("LICENSE_INVENTORY_BASE_PYTHON="),
        )
        release_inventory_end = app_text.index(
            'elif [[ -n "${LICENSE_SITE_PACKAGES}" ]]',
            release_inventory_start,
        )
        release_inventory = app_text[release_inventory_start:release_inventory_end]
        for required in (
            '--find-links "${OPEN3D_WHEEL_DIRECTORY}"',
            "--require-hashes",
            "--no-deps",
            "--only-binary :all:",
        ):
            with self.subTest(required=required):
                self.assertIn(required, release_inventory)
        self.assertNotIn('--find-links "${DIST_DIR}"', release_inventory)

        locked_install = release_inventory[
            release_inventory.index('--find-links "${OPEN3D_WHEEL_DIRECTORY}"') :
        ]
        self.assertNotIn("--no-index", locked_install.split("-r ", 1)[0])
        self.assertIn("verify_and_copy_open3d_release_wheel", app_text)
        self.assertIn(
            'for component_wheel in "${FPSAMPLE_WHEEL_PATH}" '
            '"${ACVL_UTILS_WHEEL_PATH}" "${WHEEL_PATH}"; do',
            app_text,
        )
        self.assertIn(
            "Component wheel filename collides with an offline dependency wheel",
            app_text,
        )

    def test_release_builds_use_a_prepared_offline_hash_bound_toolchain(self) -> None:
        app_text = BUILD_SCRIPT.read_text(encoding="utf-8")
        wrapper_text = WHEEL_BUILD_SCRIPT.read_text(encoding="utf-8")
        fpsample_text = FPSAMPLE_WHEEL_BUILD_SCRIPT.read_text(encoding="utf-8")
        acvl_text = ACVL_UTILS_WHEEL_BUILD_SCRIPT.read_text(encoding="utf-8")
        toolchain_text = RELEASE_BUILD_TOOLCHAIN_SCRIPT.read_text(encoding="utf-8")
        runner_text = RELEASE_COMPONENT_BUILD_RUNNER.read_text(encoding="utf-8")

        self.assertIn("release_build_toolchain.py", app_text)
        self.assertIn("RELEASE_BUILD_TOOLCHAIN", app_text)
        self.assertIn("run_release_component_build.sh", app_text)
        self.assertIn(
            "TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_INPUTS_REQUIRED", app_text
        )
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_REQUIREMENTS_LOCK", app_text)
        self.assertIn(
            "TOTALSEGMENTATOR_WRAPPER_MAC_DEPENDENCY_LOCK_METADATA", app_text
        )
        self.assertIn(
            'if [[ "${RELEASE_INPUTS_REQUIRED}" == "1" ]]; then', app_text
        )
        self.assertLess(
            app_text.index('RELEASE_INPUT_READINESS_JSON="$('),
            app_text.index('RELEASE_BUILD_TOOLCHAIN_PREPARE_JSON="$('),
            "dependency readiness must not require preparing the signed-build toolchain",
        )
        for required in (
            '--bootstrap-declaration "${RELEASE_BUILD_TOOLCHAIN_BOOTSTRAP_DECLARATION_PATH}"',
            '--source-identity "${RELEASE_BUILD_TOOLCHAIN_SOURCE_IDENTITY_PATH}"',
            '--pre-sign-wheel-receipt "${RELEASE_PRE_SIGN_WHEEL_RECEIPT_PATH}"',
            '--pre-sign-wheel-directory "${RELEASE_PRE_SIGN_WHEEL_DIRECTORY}"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, app_text)
        for required in ("--offline", "--no-index", "--require-hashes", "--no-deps"):
            with self.subTest(required=required):
                self.assertIn(required, toolchain_text)
        self.assertIn("env -i", runner_text)
        self.assertIn(
            "TOTALSEGMENTATOR_WRAPPER_MAC_FPSAMPLE_BUILD_DIR", runner_text
        )
        self.assertIn(
            "TOTALSEGMENTATOR_WRAPPER_MAC_ACVL_UTILS_BUILD_DIR", runner_text
        )
        self.assertIn("RELEASE_COMPONENT_RUNNER=1", runner_text)
        self.assertIn("SEALED_PATH", runner_text)
        self.assertIn("/usr/bin:/bin:/usr/sbin:/sbin", runner_text)
        self.assertIn("command -v cmake", runner_text)
        self.assertIn("command -v ninja", runner_text)
        self.assertIn("--verify-prepared-python", runner_text)
        self.assertIn("apple-xcode-clang-external-recorded-not-hash-bound-v1", toolchain_text)
        self.assertIn('"release_build_toolchain"', app_text)
        self.assertIn('"fpsample_pre_sign_wheel_sha256"', app_text)
        self.assertIn("require_release_project_file_unchanged", app_text)
        self.assertIn(
            '/usr/bin/xcrun install_name_tool -id "@rpath/libpython3.12.dylib"',
            app_text,
        )
        self.assertNotIn(
            '"${MACHO_LINKAGE_VERIFY_SCRIPT}" --path "${bundled_libpython}"',
            app_text,
        )
        self.assertIn("--project-file", app_text)
        self.assertIn('"project_file_sha256"', app_text)
        self.assertIn('"constraints/pyproject.toml"', app_text)
        self.assertIn("developer_selection", toolchain_text)
        self.assertNotIn('"developer_dir"', toolchain_text)
        self.assertNotIn('"clang_path"', toolchain_text)
        for text in (wrapper_text, fpsample_text, acvl_text):
            with self.subTest(script=text[:40]):
                self.assertIn("RELEASE_BUILD_TOOLCHAIN_REQUIRED", text)
                self.assertIn("RELEASE_COMPONENT_RUNNER", text)
                self.assertIn("-m build --wheel --no-isolation", text)

    def test_release_input_mode_rejects_invalid_value_before_build(self) -> None:
        environment = dict(os.environ)
        environment["TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_INPUTS_REQUIRED"] = "yes"
        completed = subprocess.run(
            ["/bin/bash", str(BUILD_SCRIPT)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn(
            "TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_INPUTS_REQUIRED must be 0 or 1",
            completed.stderr,
        )

    def test_release_component_scripts_reject_direct_unsealed_invocation(self) -> None:
        """A release flag alone must not bypass receipt/PATH verification."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for script in (
                WHEEL_BUILD_SCRIPT,
                FPSAMPLE_WHEEL_BUILD_SCRIPT,
                ACVL_UTILS_WHEEL_BUILD_SCRIPT,
            ):
                with self.subTest(script=script.name):
                    environment = dict(os.environ)
                    environment.update(
                        {
                            "TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_REQUIRED": "1",
                            "TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_BUILD_TOOLCHAIN_PYTHON": "/usr/bin/true",
                            "TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR": str(
                                root / f"dist-{script.stem}"
                            ),
                        }
                    )
                    environment.pop(
                        "TOTALSEGMENTATOR_WRAPPER_MAC_RELEASE_COMPONENT_RUNNER",
                        None,
                    )
                    completed = subprocess.run(
                        ["/bin/bash", str(script)],
                        check=False,
                        capture_output=True,
                        text=True,
                        env=environment,
                    )
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(
                        "must run through run_release_component_build.sh",
                        completed.stderr,
                    )

    def test_release_component_runner_seals_verifier_and_json_parser_environment(self) -> None:
        """The pre-exec verifier must not inherit hostile build-host variables."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fixture-repository"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            runner = scripts / "run_release_component_build.sh"
            runner.write_text(
                RELEASE_COMPONENT_BUILD_RUNNER.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            runner.chmod(0o755)

            toolchain_bin = root / "toolchain" / "bin"
            toolchain_bin.mkdir(parents=True)
            developer_dir = root / "Xcode.app" / "Contents" / "Developer"
            developer_dir.mkdir(parents=True)
            verifier_log = root / "verifier.env"
            parser_log = root / "parser.env"
            component_log = root / "component.env"
            prepared_python = toolchain_bin / "python"
            prepared_python.write_text(
                "#!/bin/bash\n"
                "set -eu\n"
                "if [[ \"${2:-}\" == *release_build_toolchain.py ]]; then\n"
                f"  /usr/bin/env | /usr/bin/sort > {shlex.quote(str(verifier_log))}\n"
                f"  printf '%s\\n' {shlex.quote(json.dumps({'toolchain_bin': str(toolchain_bin), 'wheels': {'fpsample': {'sha256': 'a' * 64}, 'acvl-utils': {'sha256': 'b' * 64}}}))}\n"
                "elif [[ \"${2:-}\" == \"-c\" ]]; then\n"
                f"  /usr/bin/env | /usr/bin/sort > {shlex.quote(str(parser_log))}\n"
                "  case \"${3:-}\" in\n"
                "    *fpsample*) printf '%s\\n' '" + "a" * 64 + "' ;;\n"
                "    *acvl-utils*) printf '%s\\n' '" + "b" * 64 + "' ;;\n"
                "    *) printf '%s\\n' " + shlex.quote(str(toolchain_bin)) + " ;;\n"
                "  esac\n"
                "else\n"
                "  exit 91\n"
                "fi\n",
                encoding="utf-8",
            )
            prepared_python.chmod(0o755)
            for executable in ("cmake", "ninja"):
                path = toolchain_bin / executable
                path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                path.chmod(0o755)
            component = root / "component.sh"
            component.write_text(
                "#!/bin/bash\n"
                f"/usr/bin/env | /usr/bin/sort > {shlex.quote(str(component_log))}\n",
                encoding="utf-8",
            )
            component.chmod(0o755)

            hostile = {
                "DYLD_LIBRARY_PATH": "/private/hostile-dylib",
                "DYLD_INSERT_LIBRARIES": "/private/hostile-inject.dylib",
                "PYTHONHOME": "/private/hostile-python-home",
                "PYTHONPATH": "/private/hostile-pythonpath",
                "PIP_INDEX_URL": "https://hostile.invalid/simple",
                "UV_INDEX_URL": "https://hostile.invalid/uv",
                "CC": "/private/hostile-clang",
                "CXX": "/private/hostile-clang++",
                "CFLAGS": "-hostile",
                "CMAKE_GENERATOR": "Hostile Generator",
                "NINJA_STATUS": "hostile",
            }
            environment = dict(os.environ)
            environment.update(hostile)
            environment["DEVELOPER_DIR"] = str(developer_dir)
            environment["TOTALSEGMENTATOR_WRAPPER_MAC_FPSAMPLE_BUILD_DIR"] = str(
                root / "fpsample-build"
            )
            environment["TOTALSEGMENTATOR_WRAPPER_MAC_ACVL_UTILS_BUILD_DIR"] = str(
                root / "acvl-utils-build"
            )
            completed = subprocess.run(
                [
                    "/bin/bash",
                    str(runner),
                    "--lock",
                    str(root / "lock"),
                    "--metadata",
                    str(root / "metadata"),
                    "--receipt",
                    str(root / "receipt"),
                    "--prepared-python",
                    str(prepared_python),
                    "--wheelhouse",
                    str(root / "wheelhouse"),
                    "--bootstrap-declaration",
                    str(root / "bootstrap-declaration.json"),
                    "--source-identity",
                    str(root / "source-identity.json"),
                    "--pre-sign-wheel-receipt",
                    str(root / "pre-sign-wheels.json"),
                    "--pre-sign-wheel-directory",
                    str(root / "pre-sign-wheels"),
                    "--component",
                    "fpsample",
                    "--",
                    str(component),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            expected_developer_dir = f"DEVELOPER_DIR={developer_dir}"
            for log_path in (verifier_log, parser_log):
                values = set(log_path.read_text(encoding="utf-8").splitlines())
                self.assertIn(expected_developer_dir, values)
                self.assertIn("LC_ALL=C", values)
                self.assertIn("PIP_NO_INDEX=1", values)
                self.assertIn("UV_OFFLINE=1", values)
                self.assertIn("UV_NO_CONFIG=1", values)
                for name in hostile:
                    self.assertFalse(
                        any(value.startswith(f"{name}=") for value in values),
                        f"{name} leaked into {log_path.name}",
                    )
            component_values = set(component_log.read_text(encoding="utf-8").splitlines())
            self.assertIn(expected_developer_dir, component_values)
            self.assertIn(
                "TOTALSEGMENTATOR_WRAPPER_MAC_FPSAMPLE_BUILD_DIR="
                f"{root / 'fpsample-build'}",
                component_values,
            )
            self.assertIn(
                "TOTALSEGMENTATOR_WRAPPER_MAC_ACVL_UTILS_BUILD_DIR="
                f"{root / 'acvl-utils-build'}",
                component_values,
            )
            self.assertIn("LC_ALL=C", component_values)
            self.assertIn("PIP_NO_INDEX=1", component_values)
            self.assertIn("UV_OFFLINE=1", component_values)
            self.assertIn("UV_NO_CONFIG=1", component_values)
            for name in hostile:
                self.assertFalse(
                    any(value.startswith(f"{name}=") for value in component_values),
                    f"{name} leaked into component process",
                )

    def test_acvl_utils_wheel_verifier_requires_pure_wheel_and_license(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            def write_fixture(
                path: Path,
                *,
                include_license: bool = True,
                include_native: bool = False,
            ) -> None:
                dist_info = "acvl_utils-0.2.6.dist-info"
                files = {
                    f"{dist_info}/METADATA": (
                        b"Metadata-Version: 2.4\nName: acvl_utils\nVersion: 0.2.6\n"
                        b"License-Expression: Apache-2.0\n"
                    ),
                    f"{dist_info}/WHEEL": (
                        b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
                    ),
                }
                if include_license:
                    files[f"{dist_info}/licenses/LICENCE"] = (
                        b"Apache License\nTERMS AND CONDITIONS FOR USE, "
                        b"REPRODUCTION, AND DISTRIBUTION\n"
                    )
                if include_native:
                    files["acvl_utils/native.so"] = b"\xcf\xfa\xed\xfe"
                record_path = f"{dist_info}/RECORD"
                record_lines = []
                for name, payload in files.items():
                    encoded = base64.urlsafe_b64encode(
                        hashlib.sha256(payload).digest()
                    ).rstrip(b"=").decode("ascii")
                    record_lines.append(
                        f"{name},sha256={encoded},{len(payload)}"
                    )
                record_lines.append(f"{record_path},,")
                files[record_path] = ("\n".join(record_lines) + "\n").encode()
                path.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(path, "w") as archive:
                    for name, payload in files.items():
                        archive.writestr(name, payload)

            valid = Path(tmp) / "valid" / "acvl_utils-0.2.6-py3-none-any.whl"
            write_fixture(valid)
            verify_bundled_acvl_utils_wheel(valid)

            invalid = Path(tmp) / "invalid" / "acvl_utils-0.2.6-py3-none-any.whl"
            write_fixture(invalid, include_license=False)
            with self.assertRaisesRegex(RuntimeError, "license"):
                verify_bundled_acvl_utils_wheel(invalid)

            native = Path(tmp) / "native" / "acvl_utils-0.2.6-py3-none-any.whl"
            write_fixture(native, include_native=True)
            with self.assertRaisesRegex(RuntimeError, "native code"):
                verify_bundled_acvl_utils_wheel(native)

    def test_override_resolution_hash_is_not_the_signed_release_wheel_hash(self) -> None:
        """A signed fpsample remains bound to its pre-sign resolver input."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            acvl = root / "acvl_utils-0.2.6-py3-none-any.whl"
            fpsample = root / "fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl"
            fpsample_pre_sign = root / "fpsample-pre-sign.whl"

            def write_wheel(
                path: Path,
                *,
                dist_info: str,
                name: str,
                version: str,
                tag: str,
                signed_marker: bytes = b"",
            ) -> tuple[str, str, str]:
                metadata = (
                    "Metadata-Version: 2.4\n"
                    f"Name: {name}\n"
                    f"Version: {version}\n"
                ).encode("utf-8")
                wheel_metadata = (
                    "Wheel-Version: 1.0\n"
                    "Root-Is-Purelib: true\n"
                    f"Tag: {tag}\n"
                ).encode("utf-8")
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(f"{dist_info}/METADATA", metadata)
                    archive.writestr(f"{dist_info}/WHEEL", wheel_metadata)
                    archive.writestr(f"{dist_info}/RECORD", "")
                    if signed_marker:
                        archive.writestr("fpsample/_signature.bin", signed_marker)
                return (
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    hashlib.sha256(metadata).hexdigest(),
                    hashlib.sha256(wheel_metadata).hexdigest(),
                )

            acvl_sha, acvl_metadata_sha, acvl_wheel_metadata_sha = write_wheel(
                acvl,
                dist_info="acvl_utils-0.2.6.dist-info",
                name="acvl_utils",
                version="0.2.6",
                tag="py3-none-any",
            )
            (
                fpsample_pre_sign_sha,
                fpsample_metadata_sha,
                fpsample_wheel_metadata_sha,
            ) = write_wheel(
                fpsample_pre_sign,
                dist_info="fpsample-1.0.2.dist-info",
                name="fpsample",
                version="1.0.2",
                tag="cp312-cp312-macosx_13_0_arm64",
            )
            write_wheel(
                fpsample,
                dist_info="fpsample-1.0.2.dist-info",
                name="fpsample",
                version="1.0.2",
                tag="cp312-cp312-macosx_13_0_arm64",
                signed_marker=b"Developer ID signed final fpsample wheel",
            )
            lock_metadata: dict[str, object] = {
                "excluded_bundled_overrides": {
                    "acvl-utils": {
                        "version": "0.2.6",
                        "role": "separately_bundled_no_deps_override",
                        "excluded_from_requirements_lock": True,
                        "resolution_input_filename": acvl.name,
                        "resolution_input_sha256": acvl_sha,
                        "resolution_input_metadata_sha256": acvl_metadata_sha,
                        "resolution_input_wheel_metadata_sha256": acvl_wheel_metadata_sha,
                        "release_wheel_hash_binding": "setup_manifest_after_signing",
                    },
                    "fpsample": {
                        "version": "1.0.2",
                        "role": "separately_bundled_no_deps_override",
                        "excluded_from_requirements_lock": True,
                        "resolution_input_filename": fpsample.name,
                        "resolution_input_sha256": fpsample_pre_sign_sha,
                        "resolution_input_metadata_sha256": fpsample_metadata_sha,
                        "resolution_input_wheel_metadata_sha256": fpsample_wheel_metadata_sha,
                        "release_wheel_hash_binding": "setup_manifest_after_signing",
                    },
                }
            }
            manifest = {
                "acvl_utils_wheel_sha256": hashlib.sha256(acvl.read_bytes()).hexdigest(),
                "fpsample_wheel_sha256": hashlib.sha256(fpsample.read_bytes()).hexdigest(),
                "fpsample_pre_sign_wheel_sha256": fpsample_pre_sign_sha,
            }

            verify_bundled_override_release_hash_boundary(
                manifest=manifest,
                lock_metadata=lock_metadata,
                wheels={"acvl-utils": acvl, "fpsample": fpsample},
            )

            signed_hash_claim = json.loads(json.dumps(lock_metadata))
            signed_hash_claim["excluded_bundled_overrides"]["fpsample"][
                "release_wheel_sha256"
            ] = manifest["fpsample_wheel_sha256"]
            with self.assertRaisesRegex(RuntimeError, "metadata"):
                verify_bundled_override_release_hash_boundary(
                    manifest=manifest,
                    lock_metadata=signed_hash_claim,
                    wheels={"acvl-utils": acvl, "fpsample": fpsample},
                )

            manifest["fpsample_wheel_sha256"] = "0" * 64
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                verify_bundled_override_release_hash_boundary(
                    manifest=manifest,
                    lock_metadata=lock_metadata,
                    wheels={"acvl-utils": acvl, "fpsample": fpsample},
                )

    def test_fpsample_binary_redistribution_notices_are_present(self) -> None:
        text = FPSAMPLE_NOTICE.read_text(encoding="utf-8")

        self.assertIn("MIT License", text)
        self.assertIn("Copyright (c) 2023 AyajiLin", text)
        self.assertIn("Software License Agreement (BSD License)", text)
        self.assertIn("Marius Muja", text)
        self.assertIn("David G. Lowe", text)
        self.assertIn("Jose Luis Blanco", text)
        self.assertIn("fpsample-1.0.2-MIT-and-nanoflann-BSD.txt", BUILD_SCRIPT.read_text(encoding="utf-8"))

    def test_meshsegnet_checkpoint_notice_is_pinned_and_non_bundled(self) -> None:
        text = MESHSEGNET_NOTICE.read_text(encoding="utf-8")

        self.assertIn("Apache License 2.0", text)
        self.assertIn(
            "3d2e44db8865ff3968803e86dadcf73cf9c4b738ddc35bfb3bc42c02347d7a0c",
            text,
        )
        self.assertIn("not included in the source distribution", text)
        self.assertIn("MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt", BUILD_SCRIPT.read_text(encoding="utf-8"))

    def test_tgnet_notice_makes_user_provided_license_boundary_explicit(self) -> None:
        text = TGNET_NOTICE.read_text(encoding="utf-8")

        validate_tgnet_policy_notice(text)
        self.assertIn("source: user-provided", text)
        self.assertIn("license: not-verified", text)
        self.assertIn("not bundled", text)
        self.assertIn("not redistributed", text)
        self.assertIn("tgnet_fps.h5", text)
        self.assertIn("tgnet_bdl.h5", text)

        with self.assertRaisesRegex(RuntimeError, "TGNet policy statement"):
            validate_tgnet_policy_notice(
                text.replace("license: not-verified", "license: Apache-2.0")
            )

    def test_totalsegmentator_public_task_inventory_matches_runtime_allowlists(self) -> None:
        from totalsegmentator_wrapper_mac.cli import TASKS
        from totalsegmentator_wrapper_mac.setup_manager import DEFAULT_TOTALSEG_WEIGHT_TASK_IDS

        inventory = json.loads(TOTALSEGMENTATOR_TASK_INVENTORY.read_text(encoding="utf-8"))
        user_tasks = inventory["user_selectable_tasks"]
        helpers = inventory["helper_weights"]
        self.assertEqual(tuple(item["name"] for item in user_tasks), TASKS)
        self.assertEqual(
            set(DEFAULT_TOTALSEG_WEIGHT_TASK_IDS),
            {item["task_id"] for item in user_tasks} | {item["task_id"] for item in helpers},
        )
        self.assertFalse(
            any(item["requires_upstream_license_gate"] for item in [*user_tasks, *helpers])
        )
        template = json.loads((ROOT / "templates" / "model_manifest.example.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [item["id"] for item in template["engines"][0]["tasks"]],
            list(TASKS),
        )

    def test_build_mac_app_script_has_expected_bundle_steps(self) -> None:
        text = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('APP_NAME="TotalSegmentator Wrapper for Mac"', text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR", text)
        self.assertIn('APP_DIR="${DIST_DIR}/${APP_NAME}.app"', text)
        self.assertIn('MACOS_DIR="${CONTENTS_DIR}/MacOS"', text)
        self.assertIn('RESOURCES_DIR="${CONTENTS_DIR}/Resources"', text)
        self.assertIn("SWIFT_APP_SOURCE_DIR", text)
        self.assertIn("native/macos/TotalSegmentatorWrapperForMac", text)
        self.assertIn("require_full_xcode", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_XCODE_DEVELOPER_DIR", text)
        self.assertIn("/Applications/Xcode.app/Contents/Developer", text)
        self.assertIn("export DEVELOPER_DIR", text)
        self.assertIn("xcodebuild -version", text)
        self.assertIn("Command Line Tools alone are not enough", text)
        self.assertIn("build_swiftui_frontend", text)
        self.assertIn("xcrun --sdk macosx swiftc", text)
        self.assertIn("SWIFT_MODULE_CACHE_PATH", text)
        self.assertIn("-module-cache-path", text)
        self.assertIn("-fmodules-cache-path", text)
        self.assertIn("-framework SwiftUI", text)
        self.assertIn('MINIMUM_MACOS_VERSION="14.0"', text)
        self.assertIn("-target arm64-apple-macos${MINIMUM_MACOS_VERSION}", text)
        self.assertIn("<key>LSMinimumSystemVersion</key>", text)
        self.assertIn("<string>${MINIMUM_MACOS_VERSION}</string>", text)
        self.assertIn("CommandBuilder.swift", text)
        self.assertIn("TotalSegmentatorWrapperForMacApp.swift", text)
        self.assertNotIn('cc "${ROOT}/templates/mac_app_' + 'entrypoint.c"', text)
        self.assertNotIn('${RESOURCES_DIR}/launcher', text)
        self.assertNotIn("launcher/mac_app_" + "launcher.py", text)
        self.assertNotIn("mac_app_" + "launcher.py", text)
        self.assertIn("resources/sample1", text)
        self.assertIn("resources/model_comparison", text)
        self.assertIn('"${RESOURCES_DIR}/model_comparison"', text)
        self.assertIn('"model_comparison": {', text)
        self.assertIn('"provenance": "model_comparison/ASSET_PROVENANCE.json"', text)
        self.assertIn('"toothseg": "model_comparison/toothseg.png"', text)
        self.assertIn("non-clinical preview", text)
        self.assertIn("sample1/surface_preview/index.html", text)
        self.assertIn("sample1/input/owner_cbct_jawcrop_0p5mm.nii.gz", text)
        self.assertIn(
            "sample1/teeth_result/toothseg_fdi_multilabel_0p5mm.nii.gz",
            text,
        )
        self.assertIn("sample1/THIRD_PARTY_NOTICES.txt", text)
        for image_name in ("totalseg", "dentalseg", "individual", "toothseg"):
            with self.subTest(image_name=image_name):
                self.assertTrue((ROOT / "resources" / "model_comparison" / f"{image_name}.png").is_file())
        provenance = json.loads(
            (ROOT / "resources" / "model_comparison" / "ASSET_PROVENANCE.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(provenance["apache_2_0_relicensed"])
        for image_name, metadata in provenance["files"].items():
            actual = hashlib.sha256(
                (ROOT / "resources" / "model_comparison" / image_name).read_bytes()
            ).hexdigest()
            self.assertEqual(actual, metadata["sha256"])
        self.assertIn("setup_manifest.json", text)
        self.assertIn('"ui_frontend": "swiftui"', text)
        self.assertNotIn("legacy_" + "tk_ui", text)
        self.assertIn("constraints/macos-arm64-py312.txt", text)
        self.assertIn("bundled_python312", text)
        self.assertIn("sys.base_prefix", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_PYTHON_RUNTIME_DIR", text)
        self.assertIn("require_release_python_runtime", text)
        release_runtime_preflight = text.index("require_release_python_runtime\n")
        self.assertLess(
            release_runtime_preflight,
            text.index('"${ROOT}/scripts/build_mac_wheel.sh"'),
        )
        self.assertLess(
            release_runtime_preflight,
            text.index("require_full_xcode\n", release_runtime_preflight),
        )
        self.assertNotIn("current-runtime.json", text)
        self.assertIn("python/cpython-3.12/bin/python3.12", text)
        self.assertIn("bundled_site_packages", text)
        self.assertIn('[[ -L "${bundled_site_packages}" && ! -e "${bundled_site_packages}" ]]', text)
        self.assertIn("xattr -cr", text)
        self.assertIn("find \"${RESOURCES_DIR}/python/cpython-3.12\" -type d", text)
        self.assertIn("find \"${RESOURCES_DIR}/python/cpython-3.12\" -type f -exec chmod a-w", text)
        self.assertIn("codesign --force --deep --sign -", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER", text)
        self.assertIn("CANONICAL_BUNDLE_IDENTIFIER", text)
        self.assertIn("jp.chino.totalsegmentator.wrapper.mac", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_TEAM_IDENTIFIER", text)
        self.assertIn("require_developer_id_signing", text)
        self.assertIn("codesign_developer_id", text)
        self.assertIn("Retrying codesign once", text)
        self.assertIn(
            'python_framework_binary="${RESOURCES_DIR}/python/cpython-3.12/Python"',
            text,
        )
        self.assertNotIn(
            "Frameworks/Python.framework/Versions/3.12/Python",
            text,
        )
        self.assertIn("--timestamp", text)
        self.assertIn("--options runtime", text)
        self.assertIn("--entitlements", text)
        self.assertIn("resources/entitlements/app.entitlements", text)
        self.assertIn("resources/entitlements/python-runtime.entitlements", text)
        self.assertIn("security find-identity -v -p codesigning", text)
        self.assertIn('"signing_mode": "${SIGNING_MODE}"', text)
        self.assertIn('"bundle_identifier": ${BUNDLE_IDENTIFIER_JSON}', text)
        self.assertIn(
            '"notarization_credentials_configured": '
            "${NOTARIZATION_CREDENTIALS_CONFIGURED_JSON}",
            text,
        )
        self.assertNotIn('"notarization_profile_name"', text)
        self.assertNotIn("NOTARY_PROFILE_JSON", text)
        self.assertIn('"notarized": ${NOTARIZED_JSON}', text)
        self.assertIn('"sample1": {', text)
        self.assertIn("sha256_file", text)
        self.assertIn('BUILD_ID="${TOTALSEGMENTATOR_WRAPPER_MAC_BUILD_ID:-}"', text)
        self.assertIn('BUILD_ID="app-${APP_VERSION}-${WHEEL_SHA256:0:12}', text)
        self.assertIn("SWIFT_SOURCE_FILES", text)
        self.assertIn("SWIFT_SOURCE_SHA256", text)
        self.assertIn("swift_source_sha256", text)
        self.assertIn("wheel_sha256", text)
        self.assertIn("SETUP_WEIGHTS_MANIFEST_PATH", text)
        self.assertIn("SETUP_WEIGHTS_MANIFEST_SHA256", text)
        self.assertIn('cp "${SETUP_WEIGHTS_MANIFEST_PATH}" "${RESOURCES_DIR}/totalseg_setup_weights_manifest.json"', text)
        self.assertIn('"setup_weights_manifest_sha256": "${SETUP_WEIGHTS_MANIFEST_SHA256}"', text)
        self.assertIn('"totalseg_setup_weights_manifest": "totalseg_setup_weights_manifest.json"', text)
        self.assertIn("constraints_sha256", text)
        self.assertIn("normalizer_sha256", text)
        self.assertIn("dcm2niix_sha256", text)
        self.assertIn("build-input-before-copy-and-code-sign-v1", text)
        self.assertIn("normalizer_input_sha256", text)
        self.assertIn("dcm2niix_input_sha256", text)
        self.assertIn("build-input-before-copy-and-code-sign-v1", text)
        self.assertIn("dcm2niix_version", text)
        self.assertIn("dcm2niix_source", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX", text)
        self.assertIn("current-artifact.json", text)
        self.assertIn("verify_dcm2niix_source_artifact.py", text)
        self.assertIn("explicit-development-input-unpinned", text)
        self.assertIn("development-only", text)
        self.assertIn('cp "${DCM2NIIX_PATH}" "${RESOURCES_DIR}/bin/dcm2niix"', text)
        self.assertIn("dicom_normalizer-macos14-arm64", text)
        self.assertIn('"dicom_normalizer_linkage": "static-gdcm-3.2.7"', text)
        self.assertIn('"gdcm_static_license_inventory": "licenses/GDCM-static-license-inventory.json"', text)
        self.assertNotIn('cp -R "${ROOT}/build/dicom_normalizer/lib"', text)
        self.assertNotIn('"dicom_normalizer_libraries": "bin/lib"', text)
        self.assertIn('"dcm2niix": "bin/dcm2niix"', text)
        self.assertIn("WRAPPER_LICENSE_PATH", text)
        self.assertIn('cp "${WRAPPER_LICENSE_PATH}" "${RESOURCES_DIR}/LICENSE"', text)
        self.assertIn('cp "${WRAPPER_NOTICE_PATH}" "${RESOURCES_DIR}/NOTICE"', text)
        self.assertIn("TOTALSEGMENTATOR_LICENSE_PATH", text)
        self.assertIn("resources/third_party/licenses/TotalSegmentator-Apache-2.0.txt", text)
        self.assertIn('cp "${TOTALSEGMENTATOR_LICENSE_PATH}" "${RESOURCES_DIR}/licenses/TotalSegmentator-Apache-2.0.txt"', text)
        self.assertIn('"totalsegmentator_license": "licenses/TotalSegmentator-Apache-2.0.txt"', text)
        self.assertIn(
            '"totalsegmentator_task_inventory": "licenses/TotalSegmentator-task-inventory.json"',
            text,
        )
        self.assertIn('cp "${TOOTHSEG_NOTICE_PATH}" "${RESOURCES_DIR}/licenses/ToothSeg-NOTICE.txt"', text)
        self.assertIn(
            'cp "${DENTALSEG_NOTICE_PATH}" "${RESOURCES_DIR}/licenses/DentalSegmentator-NOTICE.txt"',
            text,
        )
        self.assertIn('"toothseg_notice": "licenses/ToothSeg-NOTICE.txt"', text)
        self.assertIn('"dentalsegmentator_notice": "licenses/DentalSegmentator-NOTICE.txt"', text)
        self.assertIn("Separately downloaded model license: CC BY 4.0", text)
        self.assertIn("DCM2NIIX_LICENSE_PATH", text)
        self.assertIn("resources/third_party/licenses/dcm2niix-license.txt", text)
        self.assertIn('cp "${DCM2NIIX_LICENSE_SOURCE_PATH}" "${RESOURCES_DIR}/licenses/dcm2niix-license.txt"', text)
        self.assertIn('"dcm2niix_license": "licenses/dcm2niix-license.txt"', text)
        self.assertIn('"dcm2niix_build_provenance": ${DCM2NIIX_BUNDLED_RECEIPT_JSON}', text)
        self.assertIn('"dcm2niix_artifact_pointer": ${DCM2NIIX_BUNDLED_POINTER_JSON}', text)
        self.assertIn("LICENSE_MANUAL_OVERRIDES_PATH", text)
        self.assertIn("resources/third_party/licenses/manual-overrides.json", text)
        self.assertIn("generate_third_party_license_inventory.py", text)
        self.assertIn("LICENSE_INVENTORY_ENV_DIR", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_LICENSE_SITE_PATH", text)
        self.assertIn("--site-path", text)
        self.assertIn('--find-links "${DIST_DIR}"', text)
        self.assertIn("--only-binary :all:", text)
        inventory_preamble = text.index("LICENSE_INVENTORY_BASE_PYTHON=")
        inventory_start = text.index(
            'if [[ "${RELEASE_DEPENDENCY_LOCK_ATTESTED}" == "1" ]]; then',
            inventory_preamble,
        )
        inventory_end = text.index('elif [[ -n "${LICENSE_SITE_PACKAGES}" ]]', inventory_start)
        release_inventory_block = text[inventory_start:inventory_end]
        self.assertIn("run_isolated_inventory_python", release_inventory_block)
        self.assertIn("run_isolated_inventory_pip", release_inventory_block)
        self.assertIn('--no-index', release_inventory_block)
        self.assertIn('"${FPSAMPLE_WHEEL_PATH}" "${ACVL_UTILS_WHEEL_PATH}"', release_inventory_block)
        self.assertIn('--require-hashes', release_inventory_block)
        self.assertIn('--no-deps \\', release_inventory_block)
        self.assertIn('-r "${REQUIREMENTS_LOCK_PATH}"', release_inventory_block)
        self.assertIn('--no-deps "${WHEEL_PATH}"', release_inventory_block)
        self.assertNotIn('-c "${CONSTRAINTS_PATH}"', release_inventory_block)
        self.assertNotIn('"${WHEEL_PATH}[dicom,mps,dentalseg,toothseg,ios-meshsegnet]"', release_inventory_block)
        self.assertIn("env -i", text)
        self.assertIn('PIP_CONFIG_FILE="/dev/null"', text)
        self.assertIn('"${python_executable}" -I "$@"', text)
        self.assertIn(' -m pip --isolated "$@"', text)
        self.assertIn("--fail-on-unresolved", text)
        self.assertIn('--first-party-package "totalsegmentator-wrapper-mac"', text)
        self.assertIn('"third_party_licenses": {', text)
        self.assertIn('"inventory": "licenses/third_party_license_inventory.json"', text)
        self.assertIn('"summary": "licenses/THIRD_PARTY_LICENSES.txt"', text)
        self.assertIn('"unresolved_count": ${LICENSE_UNRESOLVED_COUNT}', text)
        self.assertIn('"third_party_license_inventory": "licenses/third_party_license_inventory.json"', text)
        self.assertIn('"third_party_license_summary": "licenses/THIRD_PARTY_LICENSES.txt"', text)
        self.assertIn('"expression": "Apache-2.0"', text)
        self.assertIn('"wrapper_license": "LICENSE"', text)
        self.assertIn('"wrapper_notice": "NOTICE"', text)
        self.assertIn("THIRD_PARTY_NOTICES.txt", text)
        self.assertIn("TotalSegmentator", text)
        self.assertIn("Apache-2.0", text)
        self.assertIn("Contents/Resources/licenses/TotalSegmentator-Apache-2.0.txt", text)
        self.assertIn("Contents/Resources/licenses/dcm2niix-license.txt", text)
        self.assertIn("Contents/Resources/licenses/third_party_license_inventory.json", text)
        self.assertIn("Contents/Resources/licenses/DentalSegmentator-NOTICE.txt", text)
        self.assertIn("Contents/Resources/LICENSE", text)
        self.assertIn("Contents/Resources/NOTICE", text)
        self.assertIn("verify_license_distribution.py", text)
        self.assertNotIn("See the upstream license.txt", text)
        self.assertIn("sample1_manifest_sha256", text)
        self.assertIn("dependency_set_id", text)
        self.assertIn(
            "pydicom3-gdcm3.2-toothseg-acvl0.2.6-bundled-scipy1", text
        )
        self.assertIn("update_manifest_url", text)
        self.assertIn("update_allowed_hosts", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_ALLOWED_HOSTS", text)
        self.assertIn('"team_identifier": ${TEAM_IDENTIFIER_JSON}', text)
        self.assertIn('"bundle_identity_status": "${BUNDLE_IDENTITY_STATUS}"', text)
        self.assertIn('"source_commit": "${SOURCE_COMMIT}"', text)
        self.assertIn('"source_tree_dirty": ${SOURCE_TREE_DIRTY_JSON}', text)
        self.assertIn("status --porcelain=v1 --untracked-files=all", text)
        self.assertIn("Developer ID builds require a clean tracked and untracked source worktree", text)
        self.assertIn("Developer ID source changed or became dirty during the build", text)
        self.assertIn("verified-developer-id", text)
        self.assertIn("degraded-ad-hoc", text)
        self.assertIn("Signed app TeamIdentifier mismatch", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("brew install", text)
        for license_name in DICOM_RUNTIME_LICENSES:
            with self.subTest(license_name=license_name):
                self.assertIn(license_name, text)

    def test_dicom_runtime_bundle_is_self_contained_and_resigned(self) -> None:
        text = DICOM_RUNTIME_BUNDLE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("otool -L", text)
        self.assertIn("install_name_tool -change", text)
        self.assertIn("@loader_path/lib", text)
        self.assertIn("@loader_path/", text)
        self.assertIn("/opt/homebrew", text)
        self.assertIn("/usr/local", text)
        self.assertIn('codesign --force --sign - "${library}"', text)
        self.assertIn('codesign --force --sign - "${BINARY}"', text)

    def test_notarization_script_submits_staples_and_validates_dmg(self) -> None:
        text = NOTARIZE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('APP_VERSION_OVERRIDE="${TOTALSEGMENTATOR_WRAPPER_MAC_APP_VERSION:-}"', text)
        self.assertIn('APP_VERSION="${PROJECT_VERSION}"', text)
        self.assertIn('DMG_VERSION_TAG="${TOTALSEGMENTATOR_WRAPPER_MAC_DMG_VERSION_TAG:-${APP_VERSION}-release}"', text)
        self.assertIn('${APP_NAME}-${DMG_VERSION_TAG}-arm64.dmg', text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_PROFILE", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER", text)
        self.assertIn("CANONICAL_BUNDLE_IDENTIFIER", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE=developer-id", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_NOTARIZED=1", text)
        self.assertIn('"${ROOT}/scripts/build_mac_app.sh"', text)
        self.assertIn('"${ROOT}/scripts/build_mac_dmg.sh"', text)
        self.assertIn("codesign --force --timestamp --sign", text)
        self.assertIn("notarytool submit", text)
        self.assertIn("--keychain-profile", text)
        self.assertIn("--wait", text)
        self.assertIn("--output-format json", text)
        self.assertIn("notary_submission.json", text)
        self.assertIn("notary_log.json", text)
        self.assertIn("stapler staple", text)
        self.assertIn("stapler validate", text)
        self.assertIn("spctl --assess --type open", text)
        self.assertIn("spctl --assess --type execute", text)
        self.assertIn("hdiutil attach", text)
        self.assertIn("notarytool history", text)
        self.assertLess(
            text.index("notarytool history"),
            text.index('TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE=developer-id'),
        )
        self.assertLess(
            text.index('"${ROOT}/scripts/build_mac_dmg.sh"'),
            text.index("notarytool submit"),
        )
        self.assertNotIn("AuthKey_", text)
        self.assertNotIn("--password", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR", text)
        self.assertIn("releases/stable-v2/update.json", text)
        self.assertIn("downloads.lacramy.com", text)

    def test_dmg_build_script_uses_configured_app_version_for_filename(self) -> None:
        text = DMG_BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('APP_VERSION_OVERRIDE="${TOTALSEGMENTATOR_WRAPPER_MAC_APP_VERSION:-}"', text)
        self.assertIn('APP_VERSION="${PROJECT_VERSION}"', text)
        self.assertIn('DMG_VERSION_TAG="${TOTALSEGMENTATOR_WRAPPER_MAC_DMG_VERSION_TAG:-${APP_VERSION}-release}"', text)
        self.assertIn('${APP_NAME}-${DMG_VERSION_TAG}-arm64.dmg', text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH", text)
        self.assertIn("App bundle version mismatch", text)
        self.assertIn('manifest.get("app_version") or manifest.get("version")', text)
        self.assertIn('MINIMUM_MACOS_VERSION="14.0"', text)
        self.assertIn("LSMinimumSystemVersion", text)
        self.assertIn("minimum_macos_version", text)
        self.assertNotIn('${APP_NAME}-0.1.0-arm64.dmg"', text)

    def test_app_build_rejects_symlinked_existing_app_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            protected = root / "protected-app"
            protected.mkdir()
            sentinel = protected / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            (dist / "TotalSegmentator Wrapper for Mac.app").symlink_to(
                protected, target_is_directory=True
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHON_BIN": sys.executable,
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR": str(dist),
                }
            )
            result = subprocess.run(
                ["bash", str(BUILD_SCRIPT)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("unsafe distribution directory", result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_dmg_build_rejects_external_target_and_symlinked_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            external_dmg = root / "outside.dmg"
            external_dmg.write_text("preserve", encoding="utf-8")
            base_environment = os.environ.copy()
            base_environment.update(
                {
                    "PYTHON_BIN": sys.executable,
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR": str(dist),
                    "TOTALSEGMENTATOR_WRAPPER_MAC_SKIP_APP_BUILD": "1",
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DMG_RUN_ID": "fixture",
                }
            )
            external_environment = {
                **base_environment,
                "TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH": str(external_dmg),
            }
            external_result = subprocess.run(
                ["bash", str(DMG_BUILD_SCRIPT)],
                cwd=ROOT,
                env=external_environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(external_result.returncode, 2)
            self.assertIn("directly inside the validated distribution directory", external_result.stderr)
            self.assertEqual(external_dmg.read_text(encoding="utf-8"), "preserve")

            protected = root / "protected-staging"
            protected.mkdir()
            sentinel = protected / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            (dist / ".dmg-staging-fixture").symlink_to(
                protected, target_is_directory=True
            )
            staging_result = subprocess.run(
                ["bash", str(DMG_BUILD_SCRIPT)],
                cwd=ROOT,
                env={
                    **base_environment,
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH": str(dist / "safe.dmg"),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(staging_result.returncode, 2)
            self.assertIn("unsafe distribution staging directory", staging_result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_developer_id_build_requires_canonical_update_configuration(self) -> None:
        text = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("CANONICAL_UPDATE_MANIFEST_URL", text)
        self.assertIn("canonical stable-v2 update manifest", text)
        self.assertIn("UPDATE_ALLOWED_HOSTS", text)
        self.assertIn("APP_VERSION_OVERRIDE", text)
        self.assertIn("does not match pyproject version", text)

    def test_swiftui_frontend_sources_cover_setup_main_and_safe_commands(self) -> None:
        texts = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(SWIFT_APP_DIR.glob("*.swift"))
        }
        combined = "\n".join(texts.values())

        for name in (
            "CommandBuilder.swift",
            "ProcessSupport.swift",
            "AppState.swift",
            "Views.swift",
            "TotalSegmentatorWrapperForMacApp.swift",
        ):
            self.assertIn(name, texts)

        self.assertIn("@main", texts["TotalSegmentatorWrapperForMacApp.swift"])
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_HEADLESS", texts["TotalSegmentatorWrapperForMacApp.swift"])
        self.assertIn("SetupCoordinator.runSetup", texts["TotalSegmentatorWrapperForMacApp.swift"])
        self.assertIn("NavigationSplitView", texts["Views.swift"])
        self.assertIn("最初はSampleで流れを確認", texts["Views.swift"])
        self.assertIn("手元のCTデータを使う", texts["Views.swift"])
        self.assertIn("CTデータを選ぶ", texts["Views.swift"])
        self.assertIn("詳細ログを見る", texts["Views.swift"])
        self.assertIn("ログファイルを開く", texts["Views.swift"])
        self.assertIn("Finderで表示", texts["Views.swift"])
        self.assertIn("logInfoText", texts["Views.swift"])
        self.assertIn("state.showDetailedLog()", texts["Views.swift"])
        self.assertIn(".sheet(isPresented: $state.showLog)", texts["Views.swift"])
        self.assertNotIn("TextEditor(text: $state.logText)", texts["Views.swift"])
        self.assertIn("Sampleで3Dプレビューを作る", texts["Views.swift"])
        self.assertIn("この撮影を使う", texts["Views.swift"])
        self.assertIn("3Dプレビューを再生成", texts["Views.swift"])
        self.assertIn("保存されたファイル", texts["Views.swift"])
        self.assertIn("エラー情報をコピー", texts["Views.swift"])
        self.assertIn("DentalPreparationConfirmationSheet", texts["Views.swift"])
        self.assertIn("Process()", texts["ProcessSupport.swift"])
        self.assertIn("executableURL", texts["ProcessSupport.swift"])
        self.assertIn("arguments = Array(command.dropFirst())", texts["ProcessSupport.swift"])
        self.assertIn("SIGKILL", texts["ProcessSupport.swift"])
        self.assertIn("env/bin/TotalSegmentator", texts["CommandBuilder.swift"])
        self.assertIn('ProcessInfo.processInfo.environment["HOME"]', texts["CommandBuilder.swift"])
        self.assertIn('if python.hasPrefix("/")', texts["CommandBuilder.swift"])
        self.assertIn("inferBundleResourcesFromExecutable", texts["CommandBuilder.swift"])
        self.assertIn("Bundle.main.executableURL", texts["CommandBuilder.swift"])
        self.assertIn("resourcesURL(fromBundle", texts["CommandBuilder.swift"])
        self.assertIn("_NSGetExecutablePath", texts["CommandBuilder.swift"])
        self.assertIn("resourcesURL(fromExecutable", texts["CommandBuilder.swift"])
        self.assertIn('"Contents"', texts["CommandBuilder.swift"])
        self.assertIn('"Resources"', texts["CommandBuilder.swift"])
        self.assertIn('"Library/Application Support"', texts["CommandBuilder.swift"])
        self.assertIn('"--totalseg-bin"', texts["CommandBuilder.swift"])
        self.assertIn('"--skip-dentalseg-model"', texts["CommandBuilder.swift"])
        self.assertIn('"--execution-profile"', texts["CommandBuilder.swift"])
        self.assertIn('"macos-app"', texts["CommandBuilder.swift"])
        self.assertIn('"--require-mps"', texts["CommandBuilder.swift"])
        self.assertIn('"--device"', texts["CommandBuilder.swift"])
        self.assertIn('"mps"', texts["CommandBuilder.swift"])
        self.assertIn('env.removeValue(forKey: "PYTORCH_ENABLE_MPS_FALLBACK")', texts["CommandBuilder.swift"])
        self.assertIn("dentalsegStatusCommand", texts["CommandBuilder.swift"])
        self.assertIn("dentalsegPrepareCommand", texts["CommandBuilder.swift"])
        self.assertIn('"dentalseg-status"', texts["CommandBuilder.swift"])
        self.assertIn('"dentalseg-prepare"', texts["CommandBuilder.swift"])
        self.assertIn("toothsegStatusCommand", texts["CommandBuilder.swift"])
        self.assertIn("toothsegPrepareCommand", texts["CommandBuilder.swift"])
        self.assertIn('"toothseg-status"', texts["CommandBuilder.swift"])
        self.assertIn('"toothseg-prepare"', texts["CommandBuilder.swift"])
        self.assertIn('"--toothseg-nnunet-results"', texts["CommandBuilder.swift"])
        self.assertIn('"--robust-crop"', texts["CommandBuilder.swift"])
        self.assertIn('"--higher-order-resampling"', texts["CommandBuilder.swift"])
        self.assertIn('"--teeth-robust-craniofacial-preflight"', texts["CommandBuilder.swift"])
        self.assertIn("dicom-normalizer-audit", texts["CommandBuilder.swift"])
        self.assertIn("dicom-normalizer-convert-clean", texts["CommandBuilder.swift"])
        self.assertIn("var dcm2niix: URL", texts["CommandBuilder.swift"])
        self.assertIn('"--dcm2niix"', texts["CommandBuilder.swift"])
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX", texts["CommandBuilder.swift"])
        self.assertNotIn("/opt/homebrew/bin", texts["CommandBuilder.swift"])
        self.assertNotIn("/usr/local/bin", texts["CommandBuilder.swift"])
        self.assertIn('"--timeout-sec"', texts["CommandBuilder.swift"])
        self.assertIn('"120"', texts["CommandBuilder.swift"])
        self.assertIn("surface-preview", texts["CommandBuilder.swift"])
        self.assertIn("surfacePreviewCommand", texts["CommandBuilder.swift"])
        self.assertIn("slicer-export", texts["CommandBuilder.swift"])
        self.assertIn("slicerExportCommand", texts["CommandBuilder.swift"])
        self.assertIn("update-check", texts["CommandBuilder.swift"])
        self.assertIn("updateCheckRunning", texts["AppState.swift"])
        self.assertIn("updateInstallRunning", texts["AppState.swift"])
        self.assertIn("configure_totalseg_privacy", texts["CommandBuilder.swift"])
        self.assertIn("download_totalseg_weights", texts["CommandBuilder.swift"])
        self.assertIn("download_dentalseg_weights", texts["CommandBuilder.swift"])
        self.assertIn("weights_download_failed", texts["CommandBuilder.swift"])
        self.assertIn("dentalseg_weights_download_failed", texts["CommandBuilder.swift"])
        self.assertIn("利用状況データ", texts["CommandBuilder.swift"])
        self.assertIn("totalseg_privacy_config_failed", texts["CommandBuilder.swift"])
        self.assertIn("setupRecoverySuggestion", texts["CommandBuilder.swift"])
        self.assertIn("let updateRunner = ProcessRunner()", texts["AppState.swift"])
        self.assertIn("downloadAndInstallPendingUpdate", texts["AppState.swift"])
        self.assertIn("pendingUpdateSHA256", texts["AppState.swift"])
        self.assertIn("sha256Hex", texts["AppState.swift"])
        self.assertIn("writeUpdateInstallerScript", texts["AppState.swift"])
        self.assertIn('setupMessage = "アプリ更新の反映が必要です。準備を始めるまで通信しません。"', texts["AppState.swift"])
        self.assertIn("startSetup()", texts["AppState.swift"])
        self.assertIn("spctl --assess --type execute", texts["AppState.swift"])
        self.assertIn("/usr/bin/ditto", texts["AppState.swift"])
        self.assertIn("update-stage", texts["AppState.swift"])
        self.assertIn("renameatx_np", texts["AppState.swift"])
        self.assertIn("RENAME_SWAP", texts["AppState.swift"])
        self.assertIn("volumeSupportsSwapRenamingKey", texts["AppState.swift"])
        self.assertIn("recoverInterruptedUpdateTransaction", texts["AppState.swift"])
        self.assertIn("venvPythonMatchesBundle", texts["ProcessSupport.swift"])
        self.assertIn("venv_python_changed", texts["ProcessSupport.swift"])
        self.assertIn("safelyRemoveManagedVenv", texts["ProcessSupport.swift"])
        self.assertIn("FileManager.default.removeItem(at: staged)", texts["ProcessSupport.swift"])
        self.assertNotIn("removeItem(at: paths.support.appendingPathComponent(\"env\"", texts["ProcessSupport.swift"])
        self.assertIn('reason: "legacy_setup_state"', texts["ProcessSupport.swift"])
        self.assertIn('action: "setup_required"', texts["ProcessSupport.swift"])
        self.assertNotIn("/bin/chmod -R u+w", texts["AppState.swift"])
        self.assertIn("--update-atomic-swap", texts["AppState.swift"])
        self.assertNotIn("/bin/mv \"$APP\" \"$BACKUP\"", texts["AppState.swift"])
        self.assertIn("更新をインストール", texts["Views.swift"])
        self.assertIn("enum InputSource", texts["AppState.swift"])
        self.assertIn("canStartSampleRun", texts["AppState.swift"])
        self.assertIn("canStartOwnDataRun", texts["AppState.swift"])
        self.assertIn("canUseSelectedDicomSeries", texts["AppState.swift"])
        self.assertIn("dicomCleanCandidates", texts["AppState.swift"])
        self.assertIn("convertDicomToNiftiFromAudit", texts["AppState.swift"])
        self.assertIn("cleanDicomSeriesCandidates", texts["AppState.swift"])
        self.assertIn("convertedNiftiURL", texts["AppState.swift"])
        self.assertIn("ownDataPrimaryButtonTitle", texts["AppState.swift"])
        self.assertIn("inputSource == .dicomFolder || isDirectory(inputURL)", texts["AppState.swift"])
        self.assertIn("guard inputSource == .sample || inputSource == .nifti", texts["AppState.swift"])
        self.assertIn("let output = nextCaseOutput()", texts["AppState.swift"])
        self.assertIn("regenerateSurfacePreview", texts["AppState.swift"])
        self.assertIn("labelmap作成は再実行せず", texts["AppState.swift"])
        self.assertIn("stopRequested", texts["AppState.swift"])
        self.assertIn("停止要求済み", texts["AppState.swift"])
        self.assertIn("showingUpdateConfirmation", texts["AppState.swift"])
        self.assertIn("confirmOpenPendingDownload", texts["AppState.swift"])
        self.assertIn("pendingDownloadURL = nil", texts["AppState.swift"])
        self.assertIn("stoppedBeforeSummary", texts["AppState.swift"])
        self.assertIn("runner.resetTerminationRequest()", texts["AppState.swift"])
        self.assertNotIn("rc == 0 && modeForRun == .individualTeeth", texts["AppState.swift"])
        self.assertIn("歯列・顎骨", texts["AppState.swift"])
        self.assertIn("CommandBuilder.surfacePreviewCommand", texts["AppState.swift"])
        self.assertIn("CommandBuilder.slicerExportCommand", texts["AppState.swift"])
        self.assertIn("Slicerで開けるファイルを書き出しました", texts["AppState.swift"])
        self.assertIn("3D Slicer用に書き出す", texts["Views.swift"])
        self.assertIn("3Dプレビュー作成中", texts["AppState.swift"])
        self.assertIn("3Dプレビューを作成しました", texts["AppState.swift"])
        self.assertIn("3Dプレビュー生成に失敗しました", texts["AppState.swift"])
        self.assertIn("runProgressFromLog", texts["AppState.swift"])
        self.assertIn("runProgressFraction", texts["AppState.swift"])
        self.assertIn("toothSegPreparationProgressFromLog", texts["AppState.swift"])
        self.assertIn("dentalPreparationFraction", texts["Views.swift"])
        self.assertIn("残り約", texts["AppState.swift"])
        self.assertIn("RUN_PROGRESS ", texts["AppState.swift"])
        self.assertIn("LOG_TAIL_BYTES", texts["AppState.swift"])
        self.assertIn("readLogTail", texts["AppState.swift"])
        self.assertIn("openCurrentLogFile", texts["AppState.swift"])
        self.assertIn("openCurrentLogFolder", texts["AppState.swift"])
        self.assertIn("showDetailedLog", texts["AppState.swift"])
        self.assertIn("let target = url ?? currentLogURL", texts["AppState.swift"])
        self.assertIn("最後の一部だけ表示", texts["AppState.swift"])
        self.assertIn("stage: stringFromJSON", texts["AppState.swift"])
        self.assertIn("percent == 100", texts["AppState.swift"])
        self.assertIn("次の処理へ進んでいます", texts["AppState.swift"])
        self.assertIn("runHeartbeatText", texts["AppState.swift"])
        self.assertIn("lastRunProgressAt", texts["AppState.swift"])
        self.assertIn("lastRunProgressSignature", texts["AppState.swift"])
        self.assertIn("updateRunHeartbeat", texts["AppState.swift"])
        self.assertIn("最終更新:", texts["AppState.swift"])
        self.assertIn("大きなデータではこの待ち時間が発生します", texts["AppState.swift"])
        self.assertIn("ProgressView()", texts["Views.swift"])
        self.assertIn("ProgressView(value: fraction)", texts["Views.swift"])
        self.assertIn("Text(heartbeatText)", texts["Views.swift"])
        self.assertIn("別のCTを選ぶ", texts["Views.swift"])
        self.assertNotIn("NIfTIへ変換して入力に使う", texts["Views.swift"])
        self.assertIn("もう一度作成", texts["Views.swift"])
        self.assertIn("もう一度実行", texts["AppState.swift"])
        self.assertIn("もう一度確認", texts["AppState.swift"])
        self.assertIn("canRetryFromResult", texts["AppState.swift"])
        self.assertIn("lastDicomDirURL != nil", texts["AppState.swift"])
        self.assertIn("最初に戻る", texts["Views.swift"])
        self.assertIn("goToInput", texts["AppState.swift"])
        self.assertIn("goToStart", texts["AppState.swift"])
        self.assertIn("goToSample", texts["AppState.swift"])
        self.assertIn("goToOwnData", texts["AppState.swift"])
        self.assertIn("retryRunFromResult", texts["AppState.swift"])
        self.assertIn("resultKind == .dicomAudit", texts["AppState.swift"])
        self.assertIn("runDicomAudit(dicomDir: lastDicomDirURL)", texts["AppState.swift"])
        self.assertIn("isDirectory(inputURL)", texts["AppState.swift"])
        self.assertIn("contentShape(Rectangle())", texts["Views.swift"])
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER", texts["CommandBuilder.swift"])
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX", texts["CommandBuilder.swift"])
        self.assertIn("外部へ送信しません", texts["Views.swift"])
        self.assertIn("DentalPreparationConfirmationSheet", texts["Views.swift"])
        self.assertIn("追加モデルデータを取得するので少し時間がかかります。", texts["Views.swift"])
        self.assertIn("setupReasonToJapanese", texts["CommandBuilder.swift"])
        self.assertNotIn("shell=True", combined)
        self.assertNotIn("/usr/bin/env python3", combined)

    def test_bundled_sample1_viewer_is_offline_html(self) -> None:
        text = SAMPLE1_VIEWER_HTML.read_text(encoding="utf-8")
        bundle_text = (
            SAMPLE1_ROOT / "surface_preview" / "viewer_bundle.js"
        ).read_text(encoding="utf-8")
        combined = text + "\n" + bundle_text

        self.assertIn("TotalSegmentator 3Dビューアー", text)
        self.assertIn('id="viewerData"', text)
        self.assertIn('"geometryFormat":"tswm-geometry-v1"', text)
        self.assertIn("viewer_bundle.js", text)
        self.assertIn('"dataLabel":"付属サンプル"', text)
        self.assertNotIn("teeth_multilabel_fullspace.nii.gz", text)
        self.assertIn("modeTrackpad", text)
        self.assertIn("トラックパッド", text)
        self.assertIn("マウス", text)
        self.assertIn("全体に合わせる", text)
        self.assertIn("表面平滑化", text)
        self.assertIn("ポリゴン数", combined)
        self.assertIn("歯髄腔（推定）", combined)
        self.assertNotIn("http://", combined)
        self.assertNotIn("https://", combined)
        self.assertNotIn("cdn", combined.lower())
        self.assertNotIn("<script src=", text.lower())

    def test_bundled_sample1_manifest_and_notices_document_license_and_purpose(self) -> None:
        manifest = json.loads(SAMPLE1_MANIFEST.read_text(encoding="utf-8"))
        notices = SAMPLE1_NOTICES.read_text(encoding="utf-8")
        license_text = TOTALSEGMENTATOR_LICENSE.read_text(encoding="utf-8")

        self.assertEqual(manifest["sample_id"], "sample1_owner_cbct_jawcrop_0p5mm")
        self.assertEqual(
            manifest["default_input"],
            "input/owner_cbct_jawcrop_0p5mm.nii.gz",
        )
        self.assertEqual(manifest["surface_preview"], "surface_preview/index.html")
        self.assertEqual(
            manifest["precomputed_teeth_labelmap"],
            "teeth_result/toothseg_fdi_multilabel_0p5mm.nii.gz",
        )
        self.assertFalse(manifest["clinical_use"])
        self.assertFalse(manifest["source"]["raw_dicom_included"])
        self.assertIn("project rights holder", manifest["source"]["description"])
        self.assertIn("raw DICOM is not", notices)
        self.assertIn("ToothSeg", notices)
        self.assertIn("CC BY 4.0", notices)
        self.assertIn("Contents/Resources/licenses/ToothSeg-NOTICE.txt", notices)
        self.assertIn("not for diagnosis", notices)
        self.assertIn("Apache License", license_text)
        self.assertIn("TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION", license_text)
        self.assertIn("Redistribution", license_text)
        for relative, metadata in manifest["derived_files"].items():
            with self.subTest(relative=relative):
                actual = hashlib.sha256((SAMPLE1_ROOT / relative).read_bytes()).hexdigest()
                self.assertEqual(actual, metadata["sha256"])
                self.assertIn(actual, notices)

    def test_static_third_party_license_files_are_present(self) -> None:
        dcm2niix_license = DCM2NIIX_LICENSE.read_text(encoding="utf-8")
        toothseg_notice = TOOTHSEG_NOTICE.read_text(encoding="utf-8")
        dentalseg_notice = DENTALSEG_NOTICE.read_text(encoding="utf-8")
        manual_overrides = json.loads(MANUAL_LICENSE_OVERRIDES.read_text(encoding="utf-8"))
        inventory_script = LICENSE_INVENTORY_SCRIPT.read_text(encoding="utf-8")
        override_keys = {
            (item["package"], item["version"], item["decision"])
            for item in manual_overrides["overrides"]
        }

        self.assertIn("Copyright (c) 2014-2021 Chris Rorden", dcm2niix_license)
        self.assertIn("Redistribution and use in source and binary forms", dcm2niix_license)
        self.assertIn("10.5281/zenodo.14893540", toothseg_notice)
        self.assertIn("CC BY 4.0", toothseg_notice)
        for creator in (
            "Fabian Isensee",
            "Niels van Nistelrooij",
            "Lars Krämer",
            "Shankeeth Vinayahalingam",
        ):
            self.assertIn(creator, toothseg_notice)
        self.assertIn("https://creativecommons.org/licenses/by/4.0/", toothseg_notice)
        self.assertIn("Changes made by this project", toothseg_notice)
        self.assertNotIn("Copyright (c) 2026", toothseg_notice)
        self.assertIn("10.5281/zenodo.10829675", dentalseg_notice)
        self.assertIn("Gauthier Dot", dentalseg_notice)
        self.assertIn("https://creativecommons.org/licenses/by/4.0/", dentalseg_notice)
        self.assertIn("checkpoint parameters are not modified", dentalseg_notice)
        self.assertEqual(
            manual_overrides["schema"],
            "totalsegmentator_wrapper_mac.manual_license_overrides.v1",
        )
        self.assertIn(("argparse", "1.4.0", "accepted"), override_keys)
        self.assertIn(("linecache2", "1.0.0", "accepted"), override_keys)
        self.assertIn(("traceback2", "1.4.0", "accepted"), override_keys)
        self.assertIn(("unittest2", "1.1.0", "accepted"), override_keys)
        self.assertIn(("connected-components-3d", "4.0.0", "accepted"), override_keys)
        self.assertIn(("pandas", "3.0.3", "accepted"), override_keys)
        self.assertIn(("matplotlib", "3.11.0", "accepted"), override_keys)
        self.assertIn(("scipy", "1.17.1", "accepted"), override_keys)
        self.assertIn(("scipy", "1.18.0", "accepted"), override_keys)
        self.assertNotIn(("totalsegmentator-wrapper-mac", "0.2.1", "accepted"), override_keys)
        self.assertIn("third_party_license_inventory.v1", inventory_script)
        self.assertIn("attention_license_requires_review", inventory_script)
        self.assertIn("license_metadata_unknown", inventory_script)
        self.assertIn("license_text_missing", inventory_script)

    def test_distribution_license_verifier_checks_wheel_app_and_dmg(self) -> None:
        text = LICENSE_DISTRIBUTION_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("License-Expression: Apache-2.0", text)
        self.assertIn("verify_wheel", text)
        self.assertIn("verify_app", text)
        self.assertIn("verify_dmg", text)
        self.assertIn('MINIMUM_SUPPORTED_MACOS_VERSION = "14.0"', text)
        self.assertIn("LSMinimumSystemVersion", text)
        self.assertIn("LicenseRef-Proprietary", text)
        self.assertIn("GDCM static license", text)
        self.assertIn("verify_app_machos", text)
        self.assertIn("verify_wheel_machos", text)
        self.assertIn("verify_app_bundle_macos_linkage", text)
        self.assertIn("verify_wheel_self_contained_macos_linkage", text)
        self.assertIn("verify_wheel_system_macos_linkage", text)

    def test_build_verifies_complete_app_linkage_before_signing(self) -> None:
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        linkage_check = text.index(
            '"${MACHO_LINKAGE_VERIFY_SCRIPT}" --app "${APP_DIR}"'
        )
        signing = text.index('if [[ "${SKIP_CODESIGN_VALUE}" != "1" ]]')
        self.assertLess(linkage_check, signing)

    def test_bundled_sample1_metadata_does_not_expose_developer_local_paths(self) -> None:
        checked = sorted(SAMPLE1_ROOT.rglob("*.json"))
        self.assertTrue(checked)
        for path in checked:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("/Users/ainem", text)
                self.assertNotIn("segmentation_w_mps", text)
                self.assertNotIn(".venv", text)

    def test_bundled_sample1_contains_no_legacy_slicer_sample(self) -> None:
        self.assertFalse(
            (SAMPLE1_ROOT / "input" / "DZ-CBCT_jawcrop_0p5mm.nii.gz").exists()
        )
        self.assertFalse(
            (SAMPLE1_ROOT / "teeth_result" / "teeth_multilabel_fullspace.nii.gz").exists()
        )
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(SAMPLE1_ROOT.rglob("*.json"))
        )
        self.assertNotIn("3D Slicer SampleData", combined)

    def test_build_mac_wheel_uses_pep517_frontend(self) -> None:
        text = WHEEL_BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR", text)
        self.assertIn("uv is required", text)
        self.assertIn("UV_CACHE_DIR", text)
        self.assertIn("SOURCE_DATE_EPOCH", text)
        self.assertIn('PYTHON_VERSION="$("${BUILD_PYTHON}" -c', text)
        self.assertIn('"${PYTHON_VERSION}" != "3.12"', text)
        self.assertIn("EXPECTED_WHEEL_BASENAME", text)
        self.assertIn("cp312-cp312-${CANONICAL_PLAT_NAME}.whl", text)
        self.assertIn("build --wheel --no-build-isolation", text)
        self.assertIn("--python \"${BUILD_PYTHON}\"", text)
        self.assertIn("--no-build-isolation", text)
        self.assertIn("-m build --wheel --no-isolation", text)
        self.assertIn("--config-setting=\"--build-option=--plat-name\"", text)
        self.assertIn("BinaryDistribution", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY", text)
        self.assertIn("codesign \\", text)
        self.assertIn("--timestamp", text)
        self.assertIn("--options runtime", text)
        self.assertIn("src/totalsegmentator_wrapper_mac/bin/totalsegmentator-wrapper-dicom-normalizer", text)
        self.assertIn('CANONICAL_PLAT_NAME="macosx_14_0_arm64"', text)
        self.assertIn('PLAT_NAME="${PLAT_NAME:-${CANONICAL_PLAT_NAME}}"', text)
        self.assertIn("dicom_normalizer-macos14-arm64", text)
        self.assertIn("verify_dicom_normalizer_artifact.py", text)
        self.assertIn("Run scripts/build_dicom_normalizer_mac.sh explicitly", text)
        self.assertNotIn('NORMALIZER_PATH="$("${ROOT}/scripts/build_dicom_normalizer_mac.sh")"', text)
        self.assertNotIn('cp -R "${NATIVE_BUILD_DIR}/lib"', text)
        self.assertNotIn('bin/lib"/*.dylib', text)
        self.assertIn("verify_macos_deployment_target.py", text)
        self.assertIn("verify_macos_binary_linkage.py", text)
        self.assertIn('--wheel "${WHEEL_PATH}"', text)
        self.assertIn('src/totalsegmentator_wrapper_mac/licenses', text)
        self.assertIn("dicom-normalizer-build-provenance.json", text)
        self.assertIn("gdcm-build-provenance.json", text)
        self.assertIn('"${NATIVE_BUILD_DIR}/licenses"/*', text)
        self.assertIn("for project_file in pyproject.toml README.md LICENSE NOTICE", text)
        self.assertIn('"${ROOT}/src/" "${STAGE_DIR}/src/"', text)
        self.assertNotIn('"${ROOT}/" "${STAGE_DIR}/"', text)
        for excluded in ("__pycache__", "*.pyc", "*.egg-info"):
            self.assertIn(f'--exclude "{excluded}"', text)
        self.assertNotIn("setup.py bdist_wheel", text)
        self.assertNotIn("from wheel.bdist_wheel", text)

    def test_build_mac_wheel_rejects_a_falsely_low_product_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            environment = os.environ.copy()
            environment.update(
                {
                    "PLAT_NAME": "macosx_11_0_arm64",
                    "PYTHON_BIN": sys.executable,
                    "UV_BIN": "/usr/bin/true",
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR": tmp,
                }
            )
            result = subprocess.run(
                ["bash", str(WHEEL_BUILD_SCRIPT)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "Wrapper product wheel tag must be exactly macosx_14_0_arm64",
                result.stderr,
            )
            self.assertNotIn("DICOM normalizer artifact is not prepared", result.stderr)

    def test_build_mac_wheel_rejects_non_cpython312_builder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_python = root / "python3.11"
            fake_python.write_text("#!/bin/sh\necho 3.11\n", encoding="utf-8")
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PLAT_NAME": "macosx_14_0_arm64",
                    "PYTHON_BIN": str(fake_python),
                    "UV_BIN": "/usr/bin/true",
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR": str(root / "dist"),
                }
            )
            result = subprocess.run(
                ["bash", str(WHEEL_BUILD_SCRIPT)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("must be built with CPython 3.12", result.stderr)
            self.assertNotIn("DICOM normalizer artifact is not prepared", result.stderr)

    def test_build_mac_wheel_rejects_unsafe_distribution_root_first(self) -> None:
        environment = os.environ.copy()
        environment.update(
            {
                "PLAT_NAME": "macosx_14_0_arm64",
                "PYTHON_BIN": sys.executable,
                "UV_BIN": "/usr/bin/true",
                "TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR": "/",
            }
        )
        result = subprocess.run(
            ["bash", str(WHEEL_BUILD_SCRIPT)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsafe wheel distribution directory", result.stderr)
        self.assertNotIn("DICOM normalizer artifact is not prepared", result.stderr)

    def test_mac_constraints_use_pydicom3_for_dicom2nifti(self) -> None:
        constraints = (ROOT / "constraints" / "macos-arm64-py312.txt").read_text(encoding="utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("pydicom>=3,<4", constraints)
        self.assertIn("nnunetv2==2.8.1", constraints)
        self.assertIn('"pydicom>=3,<4"', pyproject)
        self.assertIn('"nnunetv2>=2.8.1,<2.9"', pyproject)
        self.assertNotIn("pydicom>=2.4,<3", constraints)

    def test_ios_meshsegnet_declares_trimesh_simplification_runtime(self) -> None:
        constraints = (ROOT / "constraints" / "macos-arm64-py312.txt").read_text(
            encoding="utf-8"
        )
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('"fast-simplification>=0.1.13,<0.2"', pyproject)
        self.assertIn("fast-simplification==0.1.13", constraints)

    def test_dmg_scripts_support_user_local_install_validation(self) -> None:
        build_text = DMG_BUILD_SCRIPT.read_text(encoding="utf-8")
        verify_text = DMG_VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DIST_DIR", build_text)
        self.assertIn("hdiutil create", build_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_SKIP_APP_BUILD", build_text)
        self.assertIn('scripts/build_mac_app.sh', build_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DMG_RUN_ID", build_text)
        self.assertIn('DMG_STAGING="${DIST_DIR}/.dmg-staging-${DMG_RUN_ID}"', build_text)
        self.assertIn("existing DMG run staging directory", build_text)
        self.assertNotIn('find "${candidate}" -type d -exec chmod u+rwx {} +', build_text)
        self.assertNotIn("rm -rf", build_text)
        self.assertNotIn("chmod -R u+rwX", build_text)
        self.assertIn("validate_dmg_target", build_text)
        self.assertIn('ditto "${APP_PATH}" "${DMG_STAGING}/${APP_NAME}.app"', build_text)
        self.assertIn('cp "${ROOT}/LICENSE" "${DMG_STAGING}/LICENSE.txt"', build_text)
        self.assertIn('cp "${ROOT}/NOTICE" "${DMG_STAGING}/NOTICE.txt"', build_text)
        self.assertIn("README.txt", build_text)
        self.assertIn("TEST_ACCOUNT_INSTALL.txt", build_text)
        self.assertIn("wheel_install_hashed_lock", build_text)
        self.assertIn("install_bundled_wheels_step_success", build_text)
        self.assertIn("install_locked_dependencies_step_success", build_text)
        self.assertIn("pip_check_step_success", build_text)
        self.assertIn("ln -s /Applications", build_text)
        self.assertIn("Verify Test Account Install.command", build_text)
        self.assertIn("collect_test_account_install_evidence.sh", build_text)
        self.assertIn("Collect TotalSegmentator Wrapper Logs.command", build_text)
        self.assertIn("collect_launch_debug_logs.sh", build_text)
        self.assertIn("/Users/Shared/TotalSegmentatorWrapperMac", build_text)
        self.assertIn("セットアップ開始", build_text)
        self.assertIn("3Dサンプルを開く", build_text)
        self.assertIn("同梱Sample 1のオフライン3Dプレビュー", build_text)
        self.assertIn("同梱Sample 1のCT入力", build_text)
        self.assertIn("入力の大きさやMacの状態により数分以上", build_text)
        self.assertIn("モデル準備済みでも", build_text)
        self.assertIn("利用状況データ", build_text)
        self.assertIn("セットアップ中もプレビュー作成中も送信しません", build_text)
        self.assertIn("表示用の断面画像", build_text)
        self.assertIn("CT画像そのものが壊れているとは限りません", build_text)
        self.assertIn("CTを書き出したソフト名", build_text)
        self.assertIn("ログにはローカルパス", build_text)
        self.assertIn("更新を確認", build_text)
        self.assertIn("起動時やSetup中に自動確認しません", build_text)
        self.assertIn("notarized DMGをダウンロード", build_text)
        self.assertIn("SHA256とGatekeeper確認", build_text)
        self.assertIn("アプリを置き換えて再起動", build_text)
        self.assertIn("THIRD_PARTY_NOTICES.txt", build_text)
        self.assertIn("Apache-2.0", build_text)
        self.assertIn("TotalSegmentator Apache-2.0ライセンス本文", build_text)
        self.assertIn("Contents/Resources/licenses/TotalSegmentator-Apache-2.0.txt", build_text)
        self.assertIn("Contents/Resources/licenses/dcm2niix-license.txt", build_text)
        self.assertIn("Contents/Resources/licenses/third_party_license_inventory.json", build_text)
        self.assertIn("Contents/Resources/licenses/DentalSegmentator-NOTICE.txt", build_text)
        self.assertIn("Contents/Resources/licenses/ToothSeg-NOTICE.txt", build_text)
        self.assertIn("Contents/Resources/licenses/MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt", build_text)
        self.assertIn("Contents/Resources/licenses/TGNet-User-Provided-Checkpoint-NOTICE.txt", build_text)
        self.assertIn("verify_license_distribution.py", build_text)
        self.assertIn("精度評価用データではありません", build_text)
        self.assertIn("管理者権限", build_text)
        self.assertIn("DICOM、CT", build_text)
        self.assertIn("Controlキー", build_text)
        self.assertIn("MANIFEST_NOTARIZED", build_text)
        self.assertIn("notarized済みDMG", build_text)
        self.assertIn("https://forms.gle/QFPwF1Pi5C8bmSuw6", build_text)
        self.assertNotIn("TotalSegmentator Wrapper for Mac alpha", build_text)
        self.assertNotIn("github.com/ainem-m/segmentation_w_mps/issues へ報告", build_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE", build_text)
        self.assertIn("hdiutil attach", verify_text)
        self.assertIn('ditto "${MOUNT_ROOT}/TotalSegmentator Wrapper for Mac.app"', verify_text)
        self.assertIn("README.txt", verify_text)
        self.assertIn("TEST_ACCOUNT_INSTALL.txt", verify_text)
        self.assertIn("Verify Test Account Install.command", verify_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE", verify_text)
        self.assertIn('${TEST_HOME}/Applications', verify_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_HEADLESS=1", verify_text)
        self.assertIn('.get("status") == "success"', verify_text)
        self.assertIn('.get("actual_device") == "mps"', verify_text)
        self.assertIn('.get("normalizer_source") == "app_bundle"', verify_text)
        self.assertIn("Library/Caches/pip", verify_text)
        self.assertIn("cache/pycache", verify_text)
        self.assertIn("collect_test_account_install_evidence.sh", verify_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_SHARED_EVIDENCE_DIR", verify_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH", verify_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_APP_VERSION", verify_text)
        self.assertIn('PROJECT_VERSION=', verify_text)
        self.assertIn('TotalSegmentator Wrapper for Mac-${PROJECT_VERSION}-release-arm64.dmg', verify_text)
        self.assertIn("LSMinimumSystemVersion", verify_text)
        self.assertIn("minimum_macos_version", verify_text)
        self.assertIn("does not require macOS 14.0", verify_text)
        self.assertIn("SharedEvidence/test_account_install_evidence.json", verify_text)
        self.assertNotIn("sudo", build_text + verify_text)
        self.assertNotIn("brew install", build_text + verify_text)
        self.assertNotIn("/opt/homebrew", build_text + verify_text)

    def test_test_account_evidence_script_checks_distribution_invariants(self) -> None:
        text = EVIDENCE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("test_account_install_evidence.json", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_SHARED_EVIDENCE_DIR", text)
        self.assertIn("/Users/Shared/TotalSegmentatorWrapperMac", text)
        self.assertIn("shared_copy_path", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_APP_VERSION", text)
        self.assertIn("manifest_app_version_matches_expected", text)
        self.assertIn("spctl_app_accepted", text)
        self.assertIn("stapler_dmg_valid", text)
        self.assertIn("manifest_notarized", text)
        self.assertIn('"--assess"', text)
        self.assertIn("stapler", text)
        self.assertIn("setup_state_success", text)
        self.assertIn("mps_actual_device", text)
        self.assertIn("mps_gate_pass", text)
        self.assertIn("normalizer_from_app_bundle", text)
        self.assertIn("python_executable_inside_app", text)
        self.assertIn("app_support_inside_current_home", text)
        self.assertIn("no_user_global_pip_cache", text)
        self.assertIn("pip_cache_under_app_support", text)
        self.assertIn("pycache_under_app_support", text)
        self.assertIn("manifest_ui_frontend_swiftui", text)
        self.assertIn("app_minimum_macos_version_14", text)
        self.assertIn("LSMinimumSystemVersion", text)
        self.assertIn("manifest_bundled_python312", text)
        self.assertIn("bundled_python_has_no_absolute_symlinks", text)
        self.assertIn("manifest_includes_sample1", text)
        self.assertIn("manifest_has_{manifest_field}", text)
        self.assertIn("wheel_sha256", text)
        self.assertIn("dcm2niix_sha256", text)
        self.assertIn("dcm2niix_version", text)
        self.assertIn("dcm2niix_source", text)
        self.assertIn("third_party_licenses", text)
        self.assertIn("setup_weights_manifest_sha256", text)
        self.assertIn("bundled_dcm2niix_exists", text)
        self.assertIn("manifest_license_apache_2_0", text)
        self.assertIn("wrapper_license_exists", text)
        self.assertIn("wrapper_notice_exists", text)
        self.assertIn("totalsegmentator_license_exists", text)
        self.assertIn("dentalsegmentator_notice_exists", text)
        self.assertIn("toothseg_notice_exists", text)
        self.assertIn("dcm2niix_license_exists", text)
        self.assertIn("license_inventory_exists", text)
        self.assertIn("license_inventory_unresolved_zero", text)
        self.assertIn("license_surfaces_no_old_first_party_markers", text)
        self.assertIn("update_allowed_hosts", text)
        self.assertIn("sample1_input_exists", text)
        self.assertIn("sample1_surface_preview_exists", text)
        self.assertIn("sample1_manifest_non_clinical", text)
        self.assertIn("setup_state_installed_bundle_current", text)
        self.assertIn('bundled_regular_file(runtime.get("python_executable"))', text)
        self.assertIn("sha256_file(runtime_executable)", text)
        self.assertIn('"fpsample_wheel_sha256": manifest.get("fpsample_wheel_sha256")', text)
        self.assertIn('"acvl_utils_wheel_sha256": manifest.get("acvl_utils_wheel_sha256")', text)
        for required_check in (
            "manifest_has_fpsample_wheel_sha256",
            "bundled_fpsample_wheel_sha256_matches_manifest",
            "installed_fpsample_version",
            "installed_fpsample_import_sample",
            "manifest_has_acvl_utils_wheel_sha256",
            "bundled_acvl_utils_wheel_sha256_matches_manifest",
            "installed_acvl_utils_version",
            "installed_acvl_utils_import",
            "install_wheel_step_success",
            "wheel_install_hashed_lock",
            "install_bundled_wheels_step_success",
            "install_locked_dependencies_step_success",
            "pip_check_step_success",
            "manifest_has_requirements_lock_sha256",
            "manifest_has_dependency_lock_metadata_sha256",
            "bundled_requirements_lock_sha256_matches_manifest",
            "bundled_dependency_lock_metadata_sha256_matches_manifest",
            "installed_requirements_lock_sha256_matches_manifest",
            "installed_dependency_lock_metadata_sha256_matches_manifest",
            "mps_no_fallback",
            "normalizer_input_digest_scope_explicit",
            "dcm2niix_input_digest_scope_explicit",
        ):
            self.assertIn(required_check, text)
        self.assertIn("release_requires_hashed_lock", text)
        self.assertIn("development_no_lock_wheel_install", text)
        self.assertIn("Setup状態ファイルが見つかりません", text)
        self.assertIn("共有受け渡し用コピーを書き出しました", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("brew", text)

    def test_test_account_evidence_import_script_requires_all_checks(self) -> None:
        text = EVIDENCE_IMPORT_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("test_account_install_verdict.json", text)
        self.assertIn("artifacts", text)
        self.assertIn("test_account_install", text)
        self.assertIn("missing_checks", text)
        self.assertIn("failed_checks", text)
        self.assertIn("home_failures", text)
        self.assertIn("evidence_home_is_temporary", text)
        self.assertIn("evidence_home_is_current_development_home", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_APP_VERSION", text)
        self.assertIn("manifest_app_version_matches_expected", text)
        self.assertIn("expected_app_version", text)
        self.assertIn("pycache_under_app_support", text)
        self.assertIn("manifest_ui_frontend_swiftui", text)
        self.assertIn("app_minimum_macos_version_14", text)
        self.assertIn("manifest_notarized", text)
        self.assertIn("app_codesign_valid", text)
        self.assertIn("spctl_app_accepted", text)
        self.assertIn("stapler_dmg_valid", text)
        self.assertIn("setup_state_installed_bundle_current", text)
        for required_check in (
            "manifest_has_fpsample_wheel_sha256",
            "bundled_fpsample_wheel_sha256_matches_manifest",
            "installed_fpsample_version",
            "installed_fpsample_import_sample",
            "manifest_has_acvl_utils_wheel_sha256",
            "bundled_acvl_utils_wheel_sha256_matches_manifest",
            "installed_acvl_utils_version",
            "installed_acvl_utils_import",
            "install_wheel_step_success",
            "wheel_install_hashed_lock",
            "install_bundled_wheels_step_success",
            "install_locked_dependencies_step_success",
            "pip_check_step_success",
            "manifest_has_requirements_lock_sha256",
            "manifest_has_dependency_lock_metadata_sha256",
            "bundled_requirements_lock_sha256_matches_manifest",
            "bundled_dependency_lock_metadata_sha256_matches_manifest",
            "installed_requirements_lock_sha256_matches_manifest",
            "installed_dependency_lock_metadata_sha256_matches_manifest",
            "mps_no_fallback",
        ):
            self.assertIn(required_check, text)
        self.assertIn("manifest_has_update_allowed_hosts", text)
        self.assertIn("manifest_has_third_party_licenses", text)
        self.assertIn("manifest_has_dcm2niix_sha256", text)
        self.assertIn("manifest_has_dcm2niix_version", text)
        self.assertIn("manifest_has_dcm2niix_source", text)
        self.assertIn("manifest_has_setup_weights_manifest_sha256", text)
        self.assertIn("bundled_dcm2niix_exists", text)
        self.assertIn("manifest_license_apache_2_0", text)
        self.assertIn("wrapper_license_exists", text)
        self.assertIn("wrapper_notice_exists", text)
        self.assertIn("dentalsegmentator_notice_exists", text)
        self.assertIn("toothseg_notice_exists", text)
        self.assertIn("license_inventory_unresolved_zero", text)
        self.assertIn("license_surfaces_no_old_first_party_markers", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("brew", text)

if __name__ == "__main__":
    unittest.main()
