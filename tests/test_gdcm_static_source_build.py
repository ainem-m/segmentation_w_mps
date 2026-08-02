from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.collect_gdcm_source_licenses import (
    GDCMLicenseError,
    GDCM_SOURCE_SHA256,
    GDCM_SOURCE_URL,
    GDCM_VERSION,
    LICENSE_SPECS,
    collect_gdcm_source_licenses,
    verify_gdcm_license_directory,
)
from scripts.verify_license_distribution import (
    GDCM_STATIC_LICENSE_INVENTORY,
    GDCM_STATIC_LICENSE_SOURCES,
    validate_gdcm_static_license_inventory,
)


ROOT = Path(__file__).resolve().parents[1]


class GDCMStaticSourceBuildTests(unittest.TestCase):
    def test_canonical_native_build_scripts_are_executable(self) -> None:
        for name in (
            "build_gdcm_macos14_arm64.sh",
            "build_dicom_normalizer_mac.sh",
        ):
            with self.subTest(name=name):
                path = ROOT / "scripts" / name
                self.assertTrue(
                    os.access(path, os.X_OK),
                    f"canonical native build script is not executable: {path}",
                )
                self.assertEqual(
                    path.stat().st_mode & 0o777,
                    0o755,
                    f"canonical native build script mode changed: {path}",
                )

    def test_normalizer_cache_miss_requires_explicit_prepared_gdcm_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            for name, body in (
                ("uname", "#!/bin/sh\necho Darwin\n"),
                ("cmake", "#!/bin/sh\nexit 0\n"),
                ("xcrun", "#!/bin/sh\nexit 0\n"),
            ):
                path = fake_bin / name
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment.get('PATH', '')}",
                    "PYTHON_BIN": sys.executable,
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_WORK_PARENT": str(
                        root / "work"
                    ),
                    "TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_ARTIFACT_DIR": str(
                        root / "normalizer-artifact"
                    ),
                    "TOTALSEGMENTATOR_WRAPPER_MAC_GDCM_ARTIFACT_DIR": str(
                        root / "missing-gdcm-artifact"
                    ),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "build_dicom_normalizer_mac.sh")],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "Run scripts/build_gdcm_macos14_arm64.sh explicitly first",
                result.stderr,
            )
            self.assertFalse((root / "missing-gdcm-artifact").exists())
            self.assertFalse((root / "normalizer-artifact").exists())

    def test_license_collector_uses_exact_pinned_source_paths_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "GDCM-3.2.7"
            for spec in LICENSE_SPECS:
                path = source / spec.source
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{spec.marker}\nfixture {spec.component}\n", encoding="utf-8")
            output = root / "licenses"
            manifest_path = collect_gdcm_source_licenses(source, output)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["gdcm_version"], GDCM_VERSION)
            self.assertEqual(manifest["source_url"], GDCM_SOURCE_URL)
            self.assertEqual(manifest["source_archive_sha256"], GDCM_SOURCE_SHA256)
            self.assertEqual(manifest["linkage"], "static")
            self.assertFalse(manifest["gdcmconv_bundled"])
            self.assertEqual(
                {item["source_path"] for item in manifest["components"]},
                {spec.source for spec in LICENSE_SPECS},
            )
            self.assertEqual(
                {path.name for path in output.iterdir()},
                {spec.output for spec in LICENSE_SPECS}
                | {GDCM_STATIC_LICENSE_INVENTORY},
            )
            validate_gdcm_static_license_inventory(
                manifest,
                {
                    name: (output / name).read_bytes()
                    for name in GDCM_STATIC_LICENSE_SOURCES
                },
            )
            self.assertEqual(verify_gdcm_license_directory(output), manifest_path)

            packaged = output / LICENSE_SPECS[-1].output
            packaged.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(GDCMLicenseError, "integrity mismatch"):
                verify_gdcm_license_directory(output)

            first = LICENSE_SPECS[0]
            (source / first.source).write_text("unexpected text\n", encoding="utf-8")
            with self.assertRaisesRegex(GDCMLicenseError, "marker is missing"):
                collect_gdcm_source_licenses(source, root / "invalid-licenses")

    def test_build_contract_is_pinned_static_arm64_macos14_and_not_homebrew(self) -> None:
        gdcm = (ROOT / "scripts" / "build_gdcm_macos14_arm64.sh").read_text(
            encoding="utf-8"
        )
        normalizer = (ROOT / "scripts" / "build_dicom_normalizer_mac.sh").read_text(
            encoding="utf-8"
        )
        wheel = (ROOT / "scripts" / "build_mac_wheel.sh").read_text(encoding="utf-8")
        app = (ROOT / "scripts" / "build_mac_app.sh").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts" / "verify_license_distribution.py").read_text(
            encoding="utf-8"
        )
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn(GDCM_SOURCE_URL, gdcm)
        self.assertIn(GDCM_SOURCE_SHA256, gdcm)
        self.assertIn('-DCMAKE_OSX_ARCHITECTURES="${ARCHITECTURE}"', gdcm)
        self.assertIn('-DCMAKE_OSX_DEPLOYMENT_TARGET="${DEPLOYMENT_TARGET}"', gdcm)
        self.assertIn("-DCMAKE_POLICY_VERSION_MINIMUM=3.5", gdcm)
        self.assertIn("-DGDCM_BUILD_SHARED_LIBS=OFF", gdcm)
        self.assertIn("-DGDCM_BUILD_APPLICATIONS=OFF", gdcm)
        for option in (
            "ZLIB",
            "OPENSSL",
            "EXPAT",
            "JSON",
            "OPENJPEG",
            "CHARLS",
            "UUID",
        ):
            self.assertIn(f"-DGDCM_USE_SYSTEM_{option}=OFF", gdcm)
        self.assertIn("-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local", gdcm)
        self.assertIn("-DCMAKE_SYSTEM_IGNORE_PATH=/opt/homebrew;/usr/local", gdcm)
        self.assertIn("-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local", normalizer)
        self.assertIn("-u CMAKE_PREFIX_PATH", gdcm + normalizer)
        self.assertIn("-u PKG_CONFIG_PATH", gdcm + normalizer)
        self.assertIn("mktemp -d", gdcm + normalizer)
        self.assertIn('SOURCE_WORK="$(mktemp -d "${WORK_PARENT}/.gdcm-source.XXXXXX")"', gdcm)
        self.assertIn('--output-parent "${SOURCE_WORK}"', gdcm)
        self.assertNotIn('--output-parent "${SOURCE_PARENT}"', gdcm)
        self.assertIn("os.rename", gdcm + normalizer)
        self.assertIn("verify_gdcm_source_artifact.py", gdcm)
        self.assertIn("gdcm-build-provenance.json", (
            ROOT / "scripts" / "verify_gdcm_source_artifact.py"
        ).read_text(encoding="utf-8"))
        self.assertNotIn("brew install", gdcm + normalizer)
        self.assertNotIn("GDCM_LIB_DIR", gdcm + normalizer)
        self.assertNotIn("bundle_dicom_normalizer_runtime_macos.sh", normalizer)
        self.assertIn("verify_macos_deployment_target.py", normalizer)
        self.assertIn("verify_macos_binary_linkage.py", normalizer)
        self.assertIn("verify_dicom_normalizer_artifact.py", normalizer)
        self.assertIn("verify_gdcm_source_artifact.py", normalizer)
        self.assertIn("--capture-toolchain", gdcm + normalizer)
        self.assertIn("--expected-toolchain-json", gdcm + normalizer)
        self.assertIn('"${CMAKE_PATH}" -S', gdcm + normalizer)
        self.assertIn('"${CMAKE_PATH}" --build', gdcm + normalizer)
        self.assertIn("chmod 755", normalizer)
        self.assertIn(
            'GDCM_PREFIX="$("${PYTHON_BIN}" "${GDCM_ARTIFACT_VERIFY_SCRIPT}"',
            normalizer,
        )
        self.assertNotIn("EXPECTED_GDCM_PREFIX", normalizer)
        self.assertIn("Run scripts/build_gdcm_macos14_arm64.sh explicitly first", normalizer)
        self.assertIn("will not download or build GDCM implicitly", normalizer)
        self.assertNotIn(
            'GDCM_PREFIX="$("${ROOT_DIR}/scripts/build_gdcm_macos14_arm64.sh")"',
            normalizer,
        )
        self.assertIn("CMake cache contains a forbidden Homebrew", normalizer)

        self.assertIn(
            'NORMALIZER_PATH="${NORMALIZER_ARTIFACT_DIR}/totalsegmentator-wrapper-dicom-normalizer"',
            wheel,
        )
        self.assertIn("will not download or build GDCM implicitly", wheel)
        self.assertNotIn('NORMALIZER_PATH="$("', wheel)
        self.assertIn('CANONICAL_PLAT_NAME="macosx_14_0_arm64"', wheel)
        self.assertIn('PLAT_NAME="${PLAT_NAME:-${CANONICAL_PLAT_NAME}}"', wheel)
        self.assertNotIn('NATIVE_BUILD_DIR="${ROOT}/build/dicom_normalizer"', wheel)
        self.assertNotIn("bin/lib", wheel)
        self.assertIn("--wheel \"${WHEEL_PATH}\"", wheel)
        self.assertNotIn('"bin/lib/*.dylib"', pyproject)

        self.assertIn("dicom_normalizer-macos14-arm64", app)
        self.assertNotIn('cp -R "${ROOT}/build/dicom_normalizer/lib"', app)
        self.assertNotIn('"dicom_normalizer_libraries": "bin/lib"', app)
        self.assertIn('"dicom_normalizer_linkage": "static-gdcm-3.2.7"', app)
        self.assertIn("GDCM-static-license-inventory.json", app + verifier)
        self.assertIn("Expat-MIT.txt", app + verifier)
        self.assertIn("zlib-Zlib.txt", app + verifier)
        self.assertIn("GDCM-UUID-BSD-3-Clause.txt", app + verifier)


if __name__ == "__main__":
    unittest.main()
