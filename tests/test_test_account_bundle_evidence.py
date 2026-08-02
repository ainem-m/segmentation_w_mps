from __future__ import annotations

import copy
import hashlib
import json
import stat
import struct
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from totalsegmentator_wrapper_mac.test_account_bundle_evidence import (
    ARCHITECTURE,
    DCM2NIIX_LICENSE_SHA256,
    DCM2NIIX_RELEASE_TAG,
    DCM2NIIX_SOURCE_ARCHIVE_SHA256,
    DCM2NIIX_SOURCE_URL,
    GDCM_RECEIPT_SCHEMA,
    GDCM_LICENSE_SPECS,
    GDCM_SOURCE_ARCHIVE_SHA256,
    GDCM_SOURCE_URL,
    GDCM_STATIC_LIBRARIES,
    GDCM_VERSION,
    NORMALIZER_CMAKE_OPTIONS,
    NORMALIZER_ENVIRONMENT_SCRUBBED,
    NORMALIZER_LINKAGE,
    NORMALIZER_RECEIPT_SCHEMA,
    NATIVE_TOOLCHAIN_SCHEMA,
    SOURCE_DATE_EPOCH,
    TestAccountBundleEvidenceError,
    verify_dcm2niix_source_provenance,
    verify_dicom_helpers_system_linkage,
    verify_macos14_arm64_app_and_wheels,
    verify_normalizer_source_provenance,
)


CPU_TYPE_ARM64 = 0x0100000C
LC_BUILD_VERSION = 0x32
LC_DYLD_ENVIRONMENT = 0x27
LC_LOAD_DYLIB = 0xC
LC_LOAD_DYLINKER = 0xE
LC_ID_DYLIB = 0xD
LC_LAZY_LOAD_DYLIB = 0x20
LC_RPATH = 0x8000001C


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def packed_macos_version(major: int, minor: int = 0, patch: int = 0) -> int:
    return (major << 16) | (minor << 8) | patch


def padded_command(command: int, payload: bytes) -> bytes:
    size = 8 + len(payload)
    padding = (-size) % 4
    size += padding
    return struct.pack("<II", command, size) + payload + b"\0" * padding


def string_command(command: int, value: str) -> bytes:
    return padded_command(command, struct.pack("<I", 12) + value.encode("utf-8") + b"\0")


def dylib_command(value: str, *, command: int = LC_LOAD_DYLIB) -> bytes:
    return padded_command(
        command,
        struct.pack("<IIII", 24, 0, 0, 0) + value.encode("utf-8") + b"\0",
    )


def thin_arm64_macho(
    *,
    minimum_macos: tuple[int, int, int] = (14, 0, 0),
    dependencies: tuple[str, ...] = ("/usr/lib/libSystem.B.dylib",),
    lazy_dependencies: tuple[str, ...] = (),
    install_name: str | None = None,
    rpaths: tuple[str, ...] = (),
    dyld_environment: tuple[str, ...] = (),
) -> bytes:
    commands = [
        struct.pack(
            "<IIIIII",
            LC_BUILD_VERSION,
            24,
            1,
            packed_macos_version(*minimum_macos),
            0,
            0,
        ),
        string_command(LC_LOAD_DYLINKER, "/usr/lib/dyld"),
    ]
    commands.extend(dylib_command(value) for value in dependencies)
    commands.extend(
        dylib_command(value, command=LC_LAZY_LOAD_DYLIB)
        for value in lazy_dependencies
    )
    if install_name is not None:
        commands.append(dylib_command(install_name, command=LC_ID_DYLIB))
    commands.extend(string_command(LC_RPATH, value) for value in rpaths)
    commands.extend(string_command(LC_DYLD_ENVIRONMENT, value) for value in dyld_environment)
    command_bytes = b"".join(commands)
    header = struct.pack(
        "<IiiIIIII",
        0xFEEDFACF,
        CPU_TYPE_ARM64,
        0,
        2,
        len(commands),
        len(command_bytes),
        0,
        0,
    )
    return header + command_bytes


class TestAccountBundleEvidenceTests(unittest.TestCase):
    def _make_app(self, root: Path, *, main_binary: bytes | None = None) -> tuple[Path, Path]:
        app = root / "TotalSegmentator Wrapper for Mac.app"
        resources = app / "Contents" / "Resources"
        binary = main_binary or thin_arm64_macho()
        for path in (
            app / "Contents" / "MacOS" / "TotalSegmentatorWrapperForMac",
            resources / "bin" / "totalsegmentator-wrapper-dicom-normalizer",
            resources / "bin" / "dcm2niix",
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(binary)
        wheels = resources / "wheels"
        wheels.mkdir(parents=True)
        with zipfile.ZipFile(wheels / "fixture.whl", "w") as archive:
            archive.writestr("fixture/native.so", binary)
        return app, resources

    def test_app_and_wheels_require_arm64_and_macos14_or_earlier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, _ = self._make_app(Path(temporary))

            result = verify_macos14_arm64_app_and_wheels(app)

        self.assertEqual(result["wheel_count"], 1)
        self.assertEqual(result["macho_file_count"], 4)
        self.assertEqual(result["macho_slice_count"], 4)

    def test_app_and_wheels_reject_a_newer_macos_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, _ = self._make_app(
                Path(temporary), main_binary=thin_arm64_macho(minimum_macos=(15, 0, 0))
            )

            with self.assertRaisesRegex(TestAccountBundleEvidenceError, "exceeds supported macOS"):
                verify_macos14_arm64_app_and_wheels(app)

    def test_helpers_reject_non_system_dependency_and_rpath(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, resources = self._make_app(Path(temporary))
            manifest = {
                "bundled": {
                    "dicom_normalizer": "bin/totalsegmentator-wrapper-dicom-normalizer",
                    "dcm2niix": "bin/dcm2niix",
                }
            }

            self.assertEqual(
                verify_dicom_helpers_system_linkage(resources, manifest)["helper_count"],
                2,
            )

            bad = thin_arm64_macho(dependencies=("/opt/homebrew/lib/libbad.dylib",))
            (resources / "bin" / "dcm2niix").write_bytes(bad)
            with self.assertRaisesRegex(TestAccountBundleEvidenceError, "non-system Mach-O dependency"):
                verify_dicom_helpers_system_linkage(resources, manifest)

            (resources / "bin" / "dcm2niix").write_bytes(
                thin_arm64_macho(rpaths=("@loader_path/../lib",))
            )
            with self.assertRaisesRegex(TestAccountBundleEvidenceError, "LC_RPATH"):
                verify_dicom_helpers_system_linkage(resources, manifest)

    def test_helpers_reject_lazy_loaded_non_system_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, resources = self._make_app(Path(temporary))
            manifest = {
                "bundled": {
                    "dicom_normalizer": "bin/totalsegmentator-wrapper-dicom-normalizer",
                    "dcm2niix": "bin/dcm2niix",
                }
            }
            (resources / "bin" / "dcm2niix").write_bytes(
                thin_arm64_macho(
                    lazy_dependencies=("/opt/homebrew/lib/liblate.dylib",),
                )
            )

            with self.assertRaisesRegex(
                TestAccountBundleEvidenceError,
                "non-system Mach-O dependency.*liblate",
            ):
                verify_dicom_helpers_system_linkage(resources, manifest)

    def test_app_and_wheels_reject_external_dylib_install_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, _ = self._make_app(Path(temporary))
            stale_runtime = app / "Contents" / "Frameworks" / "libpython3.12.dylib"
            stale_runtime.parent.mkdir(parents=True)
            stale_runtime.write_bytes(
                thin_arm64_macho(
                    install_name="/Library/Frameworks/Python.framework/Versions/3.12/Python"
                )
            )

            with self.assertRaisesRegex(
                TestAccountBundleEvidenceError,
                "LC_ID_DYLIB.*app-relative",
            ):
                verify_macos14_arm64_app_and_wheels(app)

    def test_app_and_wheels_reject_external_rpath_in_nested_macho(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, _ = self._make_app(Path(temporary))
            nested = app / "Contents" / "Frameworks" / "libunsafe.dylib"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(thin_arm64_macho(rpaths=("/opt/homebrew/lib",)))

            with self.assertRaisesRegex(
                TestAccountBundleEvidenceError,
                "LC_RPATH.*sealed macOS or app-relative",
            ):
                verify_macos14_arm64_app_and_wheels(app)

    def test_app_and_wheels_reject_executable_relative_rpath_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, _ = self._make_app(Path(temporary))
            nested = app / "Contents" / "Frameworks" / "libunsafe.dylib"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(
                thin_arm64_macho(
                    rpaths=("@executable_path/../../../../outside",),
                )
            )

            with self.assertRaisesRegex(
                TestAccountBundleEvidenceError,
                "@executable_path escapes app Contents",
            ):
                verify_macos14_arm64_app_and_wheels(app)

    def test_app_and_wheels_reject_executable_relative_dependency_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, _ = self._make_app(Path(temporary))
            nested = app / "Contents" / "Frameworks" / "libunsafe.dylib"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(
                thin_arm64_macho(
                    dependencies=("@executable_path/../../../../outside.dylib",),
                )
            )

            with self.assertRaisesRegex(
                TestAccountBundleEvidenceError,
                "@executable_path escapes app Contents",
            ):
                verify_macos14_arm64_app_and_wheels(app)

    def test_app_and_wheels_reject_contents_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app, _ = self._make_app(root)
            outside = root / "outside.dylib"
            outside.write_bytes(thin_arm64_macho())
            link = app / "Contents" / "Frameworks" / "outside.dylib"
            link.parent.mkdir(parents=True)
            link.symlink_to("../../../outside.dylib")

            with self.assertRaisesRegex(
                TestAccountBundleEvidenceError,
                "symlink escapes app Contents",
            ):
                verify_macos14_arm64_app_and_wheels(app)

    def test_app_and_wheels_accept_relative_contents_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, _ = self._make_app(Path(temporary))
            target = app / "Contents" / "Frameworks" / "libfixture.dylib"
            target.parent.mkdir(parents=True)
            target.write_bytes(thin_arm64_macho(install_name="@rpath/libfixture.dylib"))
            alias = target.with_name("libfixture_alias.dylib")
            alias.symlink_to(target.name)

            result = verify_macos14_arm64_app_and_wheels(app)

            self.assertEqual(result["macho_file_count"], 5)

    def test_app_and_wheels_reject_unsafe_wheel_member_path_and_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, resources = self._make_app(Path(temporary))
            wheel = resources / "wheels" / "fixture.whl"
            with zipfile.ZipFile(wheel, "a") as archive:
                archive.writestr("../escape.txt", b"not extracted")

            with self.assertRaisesRegex(TestAccountBundleEvidenceError, "unsafe wheel member path"):
                verify_macos14_arm64_app_and_wheels(app)

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(wheel, "w") as archive:
                    archive.writestr("fixture/native.so", thin_arm64_macho())
                    archive.writestr("fixture/duplicate.txt", b"first")
                    archive.writestr("fixture/duplicate.txt", b"second")
            with self.assertRaisesRegex(
                TestAccountBundleEvidenceError,
                "duplicate wheel member path",
            ):
                verify_macos14_arm64_app_and_wheels(app)

    def test_app_and_wheels_reject_wheel_symlink_member_before_native_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, resources = self._make_app(Path(temporary))
            wheel = resources / "wheels" / "fixture.whl"
            link = zipfile.ZipInfo("fixture/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(wheel, "a") as archive:
                archive.writestr(link, "../../outside")

            with self.assertRaisesRegex(TestAccountBundleEvidenceError, "symlink member"):
                verify_macos14_arm64_app_and_wheels(app)

    def test_app_and_wheels_reject_casefolded_wheel_member_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, resources = self._make_app(Path(temporary))
            wheel = resources / "wheels" / "fixture.whl"
            with zipfile.ZipFile(wheel, "a") as archive:
                archive.writestr("fixture/NATIVE.so", b"not a Mach-O")

            with self.assertRaisesRegex(
                TestAccountBundleEvidenceError,
                "case-insensitive wheel member collision",
            ):
                verify_macos14_arm64_app_and_wheels(app)

    def test_app_and_wheels_reject_wheel_loader_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, resources = self._make_app(Path(temporary))
            wheel = resources / "wheels" / "fixture.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "fixture/native.so",
                    thin_arm64_macho(
                        dependencies=("@loader_path/../../../../outside.dylib",),
                    ),
                )

            with self.assertRaisesRegex(
                TestAccountBundleEvidenceError,
                "non-system Mach-O dependency.*@loader_path",
            ):
                verify_macos14_arm64_app_and_wheels(app)

    @staticmethod
    def _native_toolchain(*, cmake_digest: str = "a" * 64) -> dict[str, object]:
        return {
            "schema": NATIVE_TOOLCHAIN_SCHEMA,
            "cmake": {
                "selection": "command-v-cmake",
                "version": "cmake version 4.0.0",
                "binary_sha256": cmake_digest,
            },
            "xcrun": {
                "selection": "command-v-xcrun",
                "version": "xcrun version 70.",
                "binary_sha256": "b" * 64,
            },
            "compiler": {
                "selection": "xcrun--find-clang",
                "version": "Apple clang version 16.0.0",
                "binary_sha256": "c" * 64,
            },
            "cxx_compiler": {
                "selection": "xcrun--find-clang++",
                "version": "Apple clang version 16.0.0",
                "binary_sha256": "d" * 64,
            },
            "sdk": {
                "selection": "xcrun--sdk-macosx--show-sdk-path",
                "version": "14.5",
                "settings_sha256": "e" * 64,
            },
        }

    def _normalizer_fixture(self, resources: Path) -> dict[str, object]:
        licenses = resources / "licenses"
        licenses.mkdir(parents=True, exist_ok=True)
        component_records: list[dict[str, object]] = []
        for component, source_path, packaged_path in GDCM_LICENSE_SPECS:
            payload = f"{component} fixture license\n".encode("utf-8")
            (licenses / packaged_path).write_bytes(payload)
            component_records.append(
                {
                    "component": component,
                    "source_path": source_path,
                    "packaged_path": packaged_path,
                    "sha256": sha256_bytes(payload),
                    "size_bytes": len(payload),
                }
            )
        inventory = {
            "schema": "totalsegmentator_wrapper_mac.gdcm_static_license_inventory.v1",
            "gdcm_version": GDCM_VERSION,
            "source_url": GDCM_SOURCE_URL,
            "source_archive_sha256": GDCM_SOURCE_ARCHIVE_SHA256,
            "linkage": "static",
            "gdcmconv_bundled": False,
            "components": component_records,
        }
        inventory_bytes = json.dumps(inventory, sort_keys=True).encode("utf-8")
        (licenses / "GDCM-static-license-inventory.json").write_bytes(inventory_bytes)
        gdcm_receipt = {
            "schema": GDCM_RECEIPT_SCHEMA,
            "gdcm_version": GDCM_VERSION,
            "source_url": GDCM_SOURCE_URL,
            "source_archive_sha256": GDCM_SOURCE_ARCHIVE_SHA256,
            "minimum_macos": "14.0",
            "architecture": ARCHITECTURE,
            "source_date_epoch": 1_735_689_600,
            "linkage": "static",
            "gdcmconv_bundled": False,
            "environment_scrubbed": list(NORMALIZER_ENVIRONMENT_SCRUBBED),
            "cmake_options": [
                "CMAKE_POLICY_VERSION_MINIMUM=3.5",
                "BUILD_SHARED_LIBS=OFF",
                "GDCM_BUILD_SHARED_LIBS=OFF",
                "GDCM_BUILD_APPLICATIONS=OFF",
                "GDCM_BUILD_TESTING=OFF",
                "GDCM_BUILD_EXAMPLES=OFF",
                "GDCM_USE_VTK=OFF",
                "GDCM_WRAP_PYTHON=OFF",
                "GDCM_WRAP_JAVA=OFF",
                "GDCM_WRAP_CSHARP=OFF",
                "GDCM_WRAP_PERL=OFF",
                "GDCM_WRAP_PHP=OFF",
                "GDCM_USE_SYSTEM_ZLIB=OFF",
                "GDCM_USE_SYSTEM_OPENSSL=OFF",
                "GDCM_USE_SYSTEM_EXPAT=OFF",
                "GDCM_USE_SYSTEM_JSON=OFF",
                "GDCM_USE_SYSTEM_OPENJPEG=OFF",
                "GDCM_USE_SYSTEM_CHARLS=OFF",
                "GDCM_USE_SYSTEM_UUID=OFF",
                "GDCM_USE_SYSTEM_SOCKETXX=OFF",
                "GDCM_USE_SYSTEM_LJPEG=OFF",
                "GDCM_USE_SYSTEM_LIBXML2=OFF",
                "GDCM_USE_SYSTEM_POPPLER=OFF",
                "GDCM_USE_JPEGTURBO=OFF",
                "GDCM_USE_PVRG=OFF",
                "GDCM_USE_KAKADU=OFF",
                "CMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE",
                "CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE",
                "CMAKE_SKIP_RPATH=ON",
                "CMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local",
                "CMAKE_SYSTEM_IGNORE_PATH=/opt/homebrew;/usr/local",
            ],
            "prefix_relpath": "prefix",
            "prefix_tree_sha256": "b" * 64,
            "license_inventory_sha256": sha256_bytes(inventory_bytes),
            "required_static_libraries": {path: "c" * 64 for path in GDCM_STATIC_LIBRARIES},
            "toolchain": self._native_toolchain(),
        }
        gdcm_receipt_bytes = json.dumps(gdcm_receipt, sort_keys=True).encode("utf-8")
        (licenses / "gdcm-build-provenance.json").write_bytes(gdcm_receipt_bytes)
        normalizer_receipt = {
            "schema": NORMALIZER_RECEIPT_SCHEMA,
            "binary": "totalsegmentator-wrapper-dicom-normalizer",
            "binary_sha256": "a" * 64,
            "native_source_sha256": "d" * 64,
            "minimum_macos": "14.0",
            "architecture": ARCHITECTURE,
            "source_date_epoch": SOURCE_DATE_EPOCH,
            "linkage": NORMALIZER_LINKAGE,
            "environment_scrubbed": list(NORMALIZER_ENVIRONMENT_SCRUBBED),
            "cmake_options": list(NORMALIZER_CMAKE_OPTIONS),
            "license_inventory_sha256": sha256_bytes(inventory_bytes),
            "gdcm_build_receipt": "gdcm-build-provenance.json",
            "gdcm_build_receipt_sha256": sha256_bytes(gdcm_receipt_bytes),
            "gdcm_prefix_tree_sha256": "b" * 64,
            "gdcm_source_url": GDCM_SOURCE_URL,
            "gdcm_source_archive_sha256": GDCM_SOURCE_ARCHIVE_SHA256,
            "toolchain": self._native_toolchain(),
        }
        normalizer_receipt_bytes = json.dumps(normalizer_receipt, sort_keys=True).encode("utf-8")
        (licenses / "dicom-normalizer-build-provenance.json").write_bytes(normalizer_receipt_bytes)
        source = {
            "kind": "source-built-static-gdcm",
            "release_eligible": True,
            "binary_sha256": "a" * 64,
            "native_source_sha256": "d" * 64,
            "minimum_macos": "14.0",
            "architecture": ARCHITECTURE,
            "linkage": NORMALIZER_LINKAGE,
            "gdcm_version": GDCM_VERSION,
            "gdcm_source_url": GDCM_SOURCE_URL,
            "gdcm_source_archive_sha256": GDCM_SOURCE_ARCHIVE_SHA256,
            "build_receipt": "licenses/dicom-normalizer-build-provenance.json",
            "build_receipt_sha256": sha256_bytes(normalizer_receipt_bytes),
            "gdcm_build_receipt": "licenses/gdcm-build-provenance.json",
            "gdcm_build_receipt_sha256": sha256_bytes(gdcm_receipt_bytes),
            "license_inventory": "licenses/GDCM-static-license-inventory.json",
            "license_inventory_sha256": sha256_bytes(inventory_bytes),
        }
        return {
            "normalizer_input_sha256": "a" * 64,
            "normalizer_source": source,
            "bundled": {
                "dicom_normalizer_build_provenance": "licenses/dicom-normalizer-build-provenance.json",
                "gdcm_build_provenance": "licenses/gdcm-build-provenance.json",
                "gdcm_static_license_inventory": "licenses/GDCM-static-license-inventory.json",
            },
        }

    def _dcm2niix_fixture(self, resources: Path) -> dict[str, object]:
        licenses = resources / "licenses"
        licenses.mkdir(parents=True, exist_ok=True)
        binary_sha256 = "e" * 64
        receipt = {
            "schema": "totalsegmentator_wrapper_mac.dcm2niix_source_build.v2",
            "release_tag": DCM2NIIX_RELEASE_TAG,
            "expected_cli_version": "v1.0.20250505",
            "source_url": DCM2NIIX_SOURCE_URL,
            "source_archive_sha256": DCM2NIIX_SOURCE_ARCHIVE_SHA256,
            "license_sha256": DCM2NIIX_LICENSE_SHA256,
            "source_license_sha256": DCM2NIIX_LICENSE_SHA256,
            "bundled_license_sha256": DCM2NIIX_LICENSE_SHA256,
            "binary_sha256": binary_sha256,
            "minimum_macos": "14.0",
            "architecture": ARCHITECTURE,
            "artifact_directory": f"artifacts/{binary_sha256}",
            "source_date_epoch": 1_746_489_600,
            "binary": "dcm2niix",
            "bundled_license": "licenses/dcm2niix-license.txt",
            "linkage": {
                "result": "system-only-no-rpath",
                "allowed_dependency_prefixes": ["/System/Library/", "/usr/lib/"],
                "rpaths": [],
            },
        }
        receipt_bytes = json.dumps(receipt, sort_keys=True).encode("utf-8")
        (licenses / "dcm2niix-build-provenance.json").write_bytes(receipt_bytes)
        pointer = {
            "schema": "totalsegmentator_wrapper_mac.dcm2niix_current_artifact.v1",
            "artifact_directory": f"artifacts/{binary_sha256}",
            "binary_sha256": binary_sha256,
            "release_tag": DCM2NIIX_RELEASE_TAG,
            "source_url": DCM2NIIX_SOURCE_URL,
            "source_archive_sha256": DCM2NIIX_SOURCE_ARCHIVE_SHA256,
            "license_sha256": DCM2NIIX_LICENSE_SHA256,
        }
        pointer_bytes = json.dumps(pointer, sort_keys=True).encode("utf-8")
        (licenses / "dcm2niix-current-artifact.json").write_bytes(pointer_bytes)
        source = {
            "kind": "pinned-official-source-build",
            "release_eligible": True,
            "release_tag": DCM2NIIX_RELEASE_TAG,
            "expected_cli_version": "v1.0.20250505",
            "source_url": DCM2NIIX_SOURCE_URL,
            "source_archive_sha256": DCM2NIIX_SOURCE_ARCHIVE_SHA256,
            "source_date_epoch": 1_746_489_600,
            "minimum_macos": "14.0",
            "architecture": ARCHITECTURE,
            "binary_sha256": binary_sha256,
            "license": "licenses/dcm2niix-license.txt",
            "license_sha256": DCM2NIIX_LICENSE_SHA256,
            "build_receipt": "licenses/dcm2niix-build-provenance.json",
            "build_receipt_sha256": sha256_bytes(receipt_bytes),
            "artifact_pointer": "licenses/dcm2niix-current-artifact.json",
            "artifact_pointer_sha256": sha256_bytes(pointer_bytes),
            "linkage": "system-only-no-rpath",
        }
        return {
            "dcm2niix_input_sha256": binary_sha256,
            "dcm2niix_source": source,
            "bundled": {
                "dcm2niix_build_provenance": "licenses/dcm2niix-build-provenance.json",
                "dcm2niix_artifact_pointer": "licenses/dcm2niix-current-artifact.json",
            },
        }

    def test_normalizer_provenance_must_match_bundled_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, resources = self._make_app(Path(temporary))
            manifest = self._normalizer_fixture(resources)

            result = verify_normalizer_source_provenance(resources, manifest)

            self.assertEqual(result["source_kind"], "source-built-static-gdcm")
            bad = copy.deepcopy(manifest)
            bad["normalizer_source"]["binary_sha256"] = "0" * 64  # type: ignore[index]
            with self.assertRaisesRegex(TestAccountBundleEvidenceError, "does not match"):
                verify_normalizer_source_provenance(resources, bad)

    def test_normalizer_provenance_rejects_legacy_prefix_and_toolchain_receipts(self) -> None:
        cases = (
            ("legacy-gdcm-schema", "GDCM build receipt schema mismatch"),
            ("missing-gdcm-prefix", "GDCM build receipt field set mismatch"),
            ("wrong-gdcm-prefix", "GDCM build receipt prefix_relpath mismatch"),
            ("legacy-normalizer-schema", "normalizer build receipt schema mismatch"),
            ("flat-normalizer-toolchain", "normalizer build receipt toolchain field set mismatch"),
            ("normalizer-toolchain-mismatch", "toolchain identities differ"),
            ("normalizer-toolchain-digest-tampering", "not a lowercase SHA-256"),
        )
        for mutation, error in cases:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                _, resources = self._make_app(Path(temporary))
                manifest = self._normalizer_fixture(resources)
                licenses = resources / "licenses"
                gdcm_path = licenses / "gdcm-build-provenance.json"
                normalizer_path = licenses / "dicom-normalizer-build-provenance.json"

                if mutation == "legacy-gdcm-schema":
                    receipt = json.loads(gdcm_path.read_text(encoding="utf-8"))
                    receipt["schema"] = "totalsegmentator_wrapper_mac.gdcm_source_build.v1"
                    gdcm_path.write_text(json.dumps(receipt), encoding="utf-8")
                elif mutation == "missing-gdcm-prefix":
                    receipt = json.loads(gdcm_path.read_text(encoding="utf-8"))
                    receipt.pop("prefix_relpath")
                    gdcm_path.write_text(json.dumps(receipt), encoding="utf-8")
                elif mutation == "wrong-gdcm-prefix":
                    receipt = json.loads(gdcm_path.read_text(encoding="utf-8"))
                    receipt["prefix_relpath"] = "unexpected-prefix"
                    gdcm_path.write_text(json.dumps(receipt), encoding="utf-8")
                else:
                    receipt = json.loads(normalizer_path.read_text(encoding="utf-8"))
                    if mutation == "legacy-normalizer-schema":
                        receipt["schema"] = (
                            "totalsegmentator_wrapper_mac.dicom_normalizer_source_build.v1"
                        )
                    elif mutation == "flat-normalizer-toolchain":
                        receipt["toolchain"] = {
                            "cmake_version": "cmake version fixture",
                            "compiler_version": "Apple clang fixture",
                            "sdk_version": "14.0",
                        }
                    elif mutation == "normalizer-toolchain-mismatch":
                        receipt["toolchain"]["cmake"]["binary_sha256"] = "f" * 64
                    else:
                        receipt["toolchain"]["xcrun"]["binary_sha256"] = "A" * 64
                    normalizer_path.write_text(json.dumps(receipt), encoding="utf-8")

                with self.assertRaisesRegex(TestAccountBundleEvidenceError, error):
                    verify_normalizer_source_provenance(resources, manifest)

    def test_dcm2niix_provenance_must_match_bundled_pointer_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, resources = self._make_app(Path(temporary))
            manifest = self._dcm2niix_fixture(resources)

            result = verify_dcm2niix_source_provenance(resources, manifest)

            self.assertEqual(result["release_tag"], DCM2NIIX_RELEASE_TAG)
            bad = copy.deepcopy(manifest)
            bad["dcm2niix_source"]["artifact_pointer_sha256"] = "0" * 64  # type: ignore[index]
            with self.assertRaisesRegex(TestAccountBundleEvidenceError, "does not match"):
                verify_dcm2niix_source_provenance(resources, bad)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
