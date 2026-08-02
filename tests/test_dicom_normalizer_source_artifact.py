from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.collect_gdcm_source_licenses import LICENSE_SPECS, collect_gdcm_source_licenses
from scripts.verify_dicom_normalizer_artifact import (
    BINARY_NAME,
    RECEIPT_NAME,
    DicomNormalizerArtifactError,
    create_receipt,
    source_manifest,
    validate_packaged_provenance,
    verify_artifact,
)
from scripts.verify_gdcm_source_artifact import (
    RECEIPT_NAME as GDCM_RECEIPT_NAME,
    REQUIRED_STATIC_LIBRARIES,
    TOOLCHAIN_SCHEMA,
    create_receipt as create_gdcm_receipt,
)


class DicomNormalizerSourceArtifactTests(unittest.TestCase):
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

    def _fixture(self, root: Path) -> tuple[Path, Path]:
        source = root / "GDCM-3.2.7"
        for spec in LICENSE_SPECS:
            path = source / spec.source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{spec.marker}\nfixture {spec.component}\n", encoding="utf-8")
        gdcm = root / "gdcm-artifact"
        for relative in REQUIRED_STATIC_LIBRARIES:
            path = gdcm / "prefix" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"static fixture {relative}".encode())
        collect_gdcm_source_licenses(source, gdcm / "licenses")
        create_gdcm_receipt(
            gdcm,
            toolchain=self._toolchain(),
        )

        native_source = root / "native-source"
        (native_source / "src").mkdir(parents=True)
        (native_source / "CMakeLists.txt").write_text("fixture", encoding="utf-8")
        (native_source / "src" / "main.cpp").write_text("int main() {}", encoding="utf-8")
        artifact = root / "normalizer-artifact"
        artifact.mkdir()
        binary = artifact / BINARY_NAME
        binary.write_bytes(b"normalizer fixture")
        binary.chmod(0o755)
        shutil.copytree(gdcm / "licenses", artifact / "licenses")
        create_receipt(
            artifact,
            source_directory=native_source,
            gdcm_artifact_directory=gdcm,
            toolchain=self._toolchain(),
        )
        return artifact, native_source

    def test_valid_static_normalizer_provenance_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact, source = self._fixture(Path(tmp))
            self.assertEqual(verify_artifact(artifact, source_directory=source), (artifact / BINARY_NAME).resolve())
            manifest = source_manifest(artifact, source_directory=source)
            self.assertTrue(manifest["release_eligible"])
            self.assertEqual(manifest["kind"], "source-built-static-gdcm")
            validate_packaged_provenance(
                manifest,
                binary_input_sha256=manifest["binary_sha256"],
                receipt_bytes=(artifact / RECEIPT_NAME).read_bytes(),
                gdcm_receipt_bytes=(artifact / GDCM_RECEIPT_NAME).read_bytes(),
                license_inventory_bytes=(
                    artifact / "licenses" / "GDCM-static-license-inventory.json"
                ).read_bytes(),
            )

    def test_stale_source_or_binary_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact, source = self._fixture(Path(tmp))
            (source / "src" / "main.cpp").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(DicomNormalizerArtifactError, "native_source_sha256"):
                verify_artifact(artifact, source_directory=source)
        with tempfile.TemporaryDirectory() as tmp:
            artifact, source = self._fixture(Path(tmp))
            binary = artifact / BINARY_NAME
            binary.write_bytes(b"changed")
            binary.chmod(0o755)
            with self.assertRaisesRegex(DicomNormalizerArtifactError, "binary_sha256"):
                verify_artifact(artifact, source_directory=source)

    def test_normalizer_and_gdcm_toolchain_identities_are_bound_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact, source = self._fixture(Path(tmp))
            with self.assertRaisesRegex(
                DicomNormalizerArtifactError,
                "differs from the expected toolchain",
            ):
                verify_artifact(
                    artifact,
                    source_directory=source,
                    expected_toolchain=self._toolchain(cmake_digest="f" * 64),
                )

            receipt_path = artifact / RECEIPT_NAME
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["toolchain"]["sdk"]["settings_sha256"] = "f" * 64
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(
                DicomNormalizerArtifactError,
                "toolchain identities differ",
            ):
                verify_artifact(artifact, source_directory=source)

    def test_homebrew_contaminated_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact, source = self._fixture(Path(tmp))
            receipt_path = artifact / RECEIPT_NAME
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["cmake_options"].append("CMAKE_PREFIX_PATH=/opt/homebrew")
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(DicomNormalizerArtifactError, "cmake_options"):
                verify_artifact(artifact, source_directory=source)

    def test_copied_gdcm_receipt_and_license_inventory_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact, source = self._fixture(Path(tmp))
            (artifact / GDCM_RECEIPT_NAME).write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(DicomNormalizerArtifactError, "copied GDCM"):
                verify_artifact(artifact, source_directory=source)

    def test_copied_gdcm_receipt_rejects_epoch_and_library_digest_tampering(self) -> None:
        for mutation in ("epoch", "library-set", "uppercase-digest"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                artifact, source = self._fixture(Path(tmp))
                receipt_path = artifact / GDCM_RECEIPT_NAME
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if mutation == "epoch":
                    receipt["source_date_epoch"] += 1
                elif mutation == "library-set":
                    receipt["required_static_libraries"].pop(
                        next(iter(receipt["required_static_libraries"]))
                    )
                else:
                    key = next(iter(receipt["required_static_libraries"]))
                    receipt["required_static_libraries"][key] = "A" * 64
                receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
                with self.assertRaisesRegex(
                    DicomNormalizerArtifactError,
                    "copied GDCM build receipt",
                ):
                    verify_artifact(artifact, source_directory=source)


if __name__ == "__main__":
    unittest.main()
