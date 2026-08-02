from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_dcm2niix_macos14_arm64.sh"
LINKAGE_SCRIPT = ROOT / "scripts" / "verify_macos_binary_linkage.py"
NOTICE = ROOT / "resources" / "third_party" / "licenses" / "dcm2niix-license.txt"
DOCUMENTATION = ROOT / "docs" / "45_DCM2NIIX_MACOS14_SOURCE_BUILD.md"
PINNED_LICENSE_SHA256 = "a423e1c074ff39d9c22843489dd81bbaf42d4fa243fd785f8e96ce084db2e503"


class Dcm2niixMacOS14BuilderTests(unittest.TestCase):
    def test_builder_pins_the_known_release_without_using_homebrew_binary(self) -> None:
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('DCM2NIIX_RELEASE_TAG="v1.0.20250506"', text)
        self.assertIn('DCM2NIIX_EXPECTED_CLI_VERSION="v1.0.20250505"', text)
        self.assertIn(
            'DCM2NIIX_SOURCE_URL="https://github.com/rordenlab/dcm2niix/archive/refs/tags/${DCM2NIIX_RELEASE_TAG}.tar.gz"',
            text,
        )
        self.assertIn(
            'DCM2NIIX_SOURCE_SHA256="1b24658678b6c24141e58760dbea9fe2786ffdd736bcc37a36d9cdabc731bafa"',
            text,
        )
        self.assertIn(
            f'DCM2NIIX_LICENSE_SHA256="{PINNED_LICENSE_SHA256}"', text
        )
        self.assertIn('DCM2NIIX_SOURCE_ROOT="dcm2niix-1.0.20250506"', text)
        self.assertIn("fetch_pinned_source_archive.py", text)
        self.assertNotIn("/opt/homebrew/bin/dcm2niix", text)

    def test_builder_enforces_macos_14_arm64_and_system_only_linkage(self) -> None:
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('MINIMUM_MACOS_VERSION="14.0"', text)
        self.assertIn("MACOSX_DEPLOYMENT_TARGET=\"${MINIMUM_MACOS_VERSION}\"", text)
        self.assertIn("CMAKE_OSX_DEPLOYMENT_TARGET=\"${MINIMUM_MACOS_VERSION}\"", text)
        self.assertIn("-DCMAKE_OSX_DEPLOYMENT_TARGET=\"${MINIMUM_MACOS_VERSION}\"", text)
        self.assertIn("-DCMAKE_OSX_ARCHITECTURES=arm64", text)
        self.assertIn("--max-macos \"${MINIMUM_MACOS_VERSION}\"", text)
        self.assertIn("--require-arm64", text)
        self.assertIn("verify_macos_binary_linkage.py", text)
        self.assertIn("-u CMAKE_PREFIX_PATH", text)
        self.assertIn("-u PKG_CONFIG_PATH", text)
        self.assertIn("-DCMAKE_SKIP_RPATH=ON", text)
        self.assertIn("-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local", text)
        self.assertIn("-DCMAKE_SYSTEM_IGNORE_PATH=/opt/homebrew;/usr/local", text)
        self.assertIn("-DCMAKE_POLICY_VERSION_MINIMUM=3.5", text)
        self.assertIn("-DBUILD_SHARED_LIBS=OFF", text)

    def test_builder_requires_upstream_and_bundled_bsd_attribution_to_match(self) -> None:
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("require_bsd_notice", text)
        self.assertIn("Redistribution and use in source and binary forms", text)
        self.assertIn("cmp -s \"${SOURCE_LICENSE}\" \"${BUNDLED_NOTICE}\"", text)
        self.assertIn("require_expected_sha256", text)
        self.assertIn("DCM2NIIX_LICENSE_SHA256", text)
        self.assertIn('SOURCE_LICENSE="${SOURCE_DIR}/license.txt"', text)
        notice = NOTICE.read_text(encoding="utf-8")
        self.assertEqual(hashlib.sha256(notice.encode("utf-8")).hexdigest(), PINNED_LICENSE_SHA256)
        self.assertIn("Chris Rorden", notice)
        self.assertIn("Copyright (c) 2014-2021 Chris Rorden", notice)
        self.assertIn("Redistribution and use in source and binary forms", notice)
        self.assertIn("THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT OWNER", notice)
        self.assertIn("Neither the name of the copyright owner", notice)

    def test_builder_keeps_a_receipt_and_documentation_for_the_version_mismatch(self) -> None:
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        documentation = DOCUMENTATION.read_text(encoding="utf-8")
        self.assertIn("SOURCE_DATE_EPOCH", text)
        self.assertIn("dcm2niix-build-provenance.json", text)
        self.assertIn("source_archive_sha256", text)
        self.assertIn("dcm2niix_source_build.v2", text)
        self.assertIn("v1.0.20250506", documentation)
        self.assertIn("v1.0.20250505", documentation)
        self.assertIn("not an upgrade", documentation)

    def test_builder_rejects_an_override_of_the_pinned_source_date_epoch_before_building(self) -> None:
        environment = os.environ.copy()
        environment["SOURCE_DATE_EPOCH"] = "1"
        completed = subprocess.run(
            ["bash", str(BUILD_SCRIPT)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("SOURCE_DATE_EPOCH is fixed", completed.stderr)

    def test_builder_uses_fresh_staging_and_atomic_content_addressed_publication(self) -> None:
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('BUILD_STAGING_PARENT="${OUTPUT_DIR}/.build-staging"', text)
        self.assertIn('mktemp -d "${BUILD_STAGING_PARENT}/.dcm2niix-cmake.XXXXXX"', text)
        self.assertNotIn('CMAKE_BUILD_DIR="${OUTPUT_DIR}/cmake-', text)
        self.assertIn('ARTIFACTS_DIR="${OUTPUT_DIR}/artifacts"', text)
        self.assertIn('CURRENT_ARTIFACT_POINTER="${OUTPUT_DIR}/current-artifact.json"', text)
        self.assertIn('PUBLISH_LOCK="${ARTIFACTS_DIR}/.dcm2niix-publish-lock"', text)
        self.assertIn('REQUESTED_SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH-}"', text)
        self.assertIn('SOURCE_DATE_EPOCH="1746489600"', text)
        self.assertIn("-u CMAKE_PREFIX_PATH", text)
        self.assertGreaterEqual(text.count("-u CMAKE_PREFIX_PATH"), 2)
        self.assertGreaterEqual(text.count("-u CPLUS_INCLUDE_PATH"), 2)
        self.assertGreaterEqual(text.count("-u DYLD_FALLBACK_LIBRARY_PATH"), 2)
        self.assertIn('verify_current_artifact', text)
        self.assertIn('verify_artifact_directory', text)
        self.assertIn('dcm2niix_current_artifact.v1', text)
        self.assertIn('artifact_directory', text)
        self.assertIn('mv "${ARTIFACT_STAGING}" "${ARTIFACT_DIR}"', text)
        self.assertIn('mv "${TEMP_POINTER}" "${CURRENT_ARTIFACT_POINTER}"', text)

    def test_existing_artifact_failures_are_not_treated_as_missing(self) -> None:
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn(
            'if CURRENT_ARTIFACT_BINARY="$(verify_current_artifact)"; then',
            text,
        )
        self.assertIn(
            'if [[ -e "${CURRENT_ARTIFACT_POINTER}" || -L "${CURRENT_ARTIFACT_POINTER}" ]]; then',
            text,
        )
        self.assertIn(
            '|| die "existing current dcm2niix artifact failed strict validation"',
            text,
        )

    def test_invalid_existing_pointer_stops_before_source_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "fetch-was-called"
            output_dir = root / "output"
            output_dir.mkdir()
            (output_dir / "current-artifact.json").write_text("not JSON", encoding="utf-8")

            def executable(name: str, body: str) -> None:
                path = fake_bin / name
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)

            executable("uname", "#!/bin/sh\nprintf 'Darwin\\n'\n")
            executable(
                "stat",
                "#!/bin/sh\nif [ \"$1\" = \"-f\" ]; then id -u; else /usr/bin/stat \"$@\"; fi\n",
            )
            for command in ("cmake", "xcrun", "otool", "shasum"):
                executable(command, "#!/bin/sh\nexit 0\n")

            python_wrapper = root / "python-wrapper"
            python_wrapper.write_text(
                "#!/bin/sh\n"
                f"if [ \"$1\" = \"{ROOT / 'scripts' / 'fetch_pinned_source_archive.py'}\" ]; then\n"
                f"  : > \"{marker}\"\n"
                "  exit 99\n"
                "fi\n"
                f"exec \"{ROOT / '.venv' / 'bin' / 'python'}\" \"$@\"\n",
                encoding="utf-8",
            )
            python_wrapper.chmod(0o755)

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "PYTHON_BIN": str(python_wrapper),
                    "TOTALSEGMENTATOR_WRAPPER_MAC_SOURCE_CACHE_DIR": str(root / "source-cache"),
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX_BUILD_DIR": str(output_dir),
                }
            )
            completed = subprocess.run(
                ["bash", str(BUILD_SCRIPT)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("existing current dcm2niix artifact failed strict validation", completed.stderr)
            self.assertFalse(marker.exists(), completed.stderr)

    def test_builder_uses_a_fresh_source_extraction_and_never_removes_an_unacquired_lock(self) -> None:
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'SOURCE_BUILD_PARENT="$(mktemp -d "${SOURCE_PARENT}/.dcm2niix-source.XXXXXX")"',
            text,
        )
        self.assertIn('--output-parent "${SOURCE_BUILD_PARENT}"', text)
        self.assertNotIn('--output-parent "${SOURCE_PARENT}"', text)
        self.assertIn('PUBLISH_LOCK_ACQUIRED=0', text)
        self.assertIn('PUBLISH_LOCK_ACQUIRED=1', text)
        self.assertIn('[[ "${PUBLISH_LOCK_ACQUIRED:-0}" == "1" ]]', text)

    def test_cmake_configure_build_and_install_all_scrub_external_package_environment(self) -> None:
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("-u CMAKE_PREFIX_PATH"), 3)
        self.assertGreaterEqual(text.count("-u DYLD_FALLBACK_LIBRARY_PATH"), 3)
        self.assertIn("-u DESTDIR", text)

    def test_linkage_verifier_is_a_real_fixture_tested_release_check(self) -> None:
        text = LINKAGE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("/opt/homebrew", (ROOT / "tests" / "test_macos_binary_linkage.py").read_text(encoding="utf-8"))
        self.assertIn("LC_RPATH", text)
        self.assertIn("non-system Mach-O dependency", text)


if __name__ == "__main__":
    unittest.main()
