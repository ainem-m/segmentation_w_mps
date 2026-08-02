from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.collect_gdcm_source_licenses import LICENSE_SPECS, collect_gdcm_source_licenses
from scripts.verify_gdcm_source_artifact import (
    CMAKE_OPTIONS,
    RECEIPT_NAME,
    REQUIRED_STATIC_LIBRARIES,
    TOOLCHAIN_SCHEMA,
    GDCMSourceArtifactError,
    create_receipt,
    verify_artifact,
)


class GDCMSourceArtifactTests(unittest.TestCase):
    @staticmethod
    def _toolchain(*, cmake_digest: str = "a" * 64) -> dict[str, object]:
        return {
            "schema": TOOLCHAIN_SCHEMA,
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

    def _fixture(self, root: Path) -> Path:
        source = root / "GDCM-3.2.7"
        for spec in LICENSE_SPECS:
            path = source / spec.source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{spec.marker}\nfixture {spec.component}\n", encoding="utf-8")
        artifact = root / "artifact"
        prefix = artifact / "prefix"
        for relative in REQUIRED_STATIC_LIBRARIES:
            path = prefix / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"static fixture {relative}".encode())
        collect_gdcm_source_licenses(source, artifact / "licenses")
        create_receipt(
            artifact,
            toolchain=self._toolchain(),
        )
        return artifact

    def test_valid_immutable_static_artifact_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = self._fixture(Path(tmp))
            self.assertEqual(verify_artifact(artifact), (artifact / "prefix").resolve())

    def test_tampered_static_library_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = self._fixture(Path(tmp))
            (artifact / "prefix" / REQUIRED_STATIC_LIBRARIES[0]).write_bytes(b"tampered")
            with self.assertRaisesRegex(GDCMSourceArtifactError, "prefix integrity"):
                verify_artifact(artifact)

    def test_receipt_declares_and_strictly_verifies_the_prefix_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = self._fixture(Path(tmp))
            receipt_path = artifact / RECEIPT_NAME
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["prefix_relpath"], "prefix")
            receipt["prefix_relpath"] = "other-prefix"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(GDCMSourceArtifactError, "prefix_relpath mismatch"):
                verify_artifact(artifact)

    def test_toolchain_identity_is_strict_and_checked_when_verifying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = self._fixture(Path(tmp))
            with self.assertRaisesRegex(GDCMSourceArtifactError, "differs from the expected"):
                verify_artifact(
                    artifact,
                    expected_toolchain=self._toolchain(cmake_digest="f" * 64),
                )

            receipt_path = artifact / RECEIPT_NAME
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["toolchain"]["xcrun"]["binary_sha256"] = "A" * 64
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(GDCMSourceArtifactError, "toolchain"):
                verify_artifact(artifact)

    def test_stale_or_contaminated_build_options_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = self._fixture(Path(tmp))
            receipt_path = artifact / RECEIPT_NAME
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["cmake_options"] = [*CMAKE_OPTIONS, "CMAKE_PREFIX_PATH=/opt/homebrew"]
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(GDCMSourceArtifactError, "cmake_options mismatch"):
                verify_artifact(artifact)

    def test_dynamic_library_or_gdcmconv_is_rejected(self) -> None:
        for forbidden in ("lib/libbrew.dylib", "bin/gdcmconv"):
            with self.subTest(forbidden=forbidden), tempfile.TemporaryDirectory() as tmp:
                artifact = self._fixture(Path(tmp))
                path = artifact / "prefix" / forbidden
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"forbidden")
                with self.assertRaisesRegex(GDCMSourceArtifactError, "dynamic libraries|gdcmconv"):
                    verify_artifact(artifact)

    def test_symlink_inside_prefix_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = self._fixture(Path(tmp))
            target = artifact / "prefix" / "README"
            target.write_text("fixture", encoding="utf-8")
            (artifact / "prefix" / "linked").symlink_to(target)
            with self.assertRaisesRegex(GDCMSourceArtifactError, "symlink"):
                verify_artifact(artifact)


if __name__ == "__main__":
    unittest.main()
