from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_dcm2niix_source_artifact import (
    ARCHITECTURE,
    BUILD_RECEIPT_SCHEMA,
    CURRENT_POINTER_SCHEMA,
    EXPECTED_CLI_VERSION,
    LICENSE_SHA256,
    MINIMUM_MACOS,
    RELEASE_TAG,
    SOURCE_ARCHIVE_SHA256,
    SOURCE_DATE_EPOCH,
    SOURCE_URL,
    Dcm2niixSourceArtifactError,
    validate_source_manifest,
    verify_build_root,
)


ROOT = Path(__file__).resolve().parents[1]
TRACKED_LICENSE = ROOT / "resources/third_party/licenses/dcm2niix-license.txt"


class Dcm2niixSourceArtifactTests(unittest.TestCase):
    def _write_fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        build_root = root / "build"
        binary_bytes = b"fixture dcm2niix binary"
        binary_sha256 = hashlib.sha256(binary_bytes).hexdigest()
        artifact = build_root / "artifacts" / binary_sha256
        binary = artifact / "dcm2niix"
        license_path = artifact / "licenses" / "dcm2niix-license.txt"
        receipt_path = artifact / "dcm2niix-build-provenance.json"
        license_path.parent.mkdir(parents=True)
        binary.write_bytes(binary_bytes)
        binary.chmod(0o755)
        license_path.write_bytes(TRACKED_LICENSE.read_bytes())
        receipt = {
            "schema": BUILD_RECEIPT_SCHEMA,
            "release_tag": RELEASE_TAG,
            "expected_cli_version": EXPECTED_CLI_VERSION,
            "source_url": SOURCE_URL,
            "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
            "license_sha256": LICENSE_SHA256,
            "source_license_sha256": LICENSE_SHA256,
            "bundled_license_sha256": LICENSE_SHA256,
            "binary_sha256": binary_sha256,
            "minimum_macos": MINIMUM_MACOS,
            "architecture": ARCHITECTURE,
            "artifact_directory": f"artifacts/{binary_sha256}",
            "binary": "dcm2niix",
            "bundled_license": "licenses/dcm2niix-license.txt",
            "linkage": {
                "result": "system-only-no-rpath",
                "allowed_dependency_prefixes": ["/System/Library/", "/usr/lib/"],
                "rpaths": [],
            },
            "source_date_epoch": SOURCE_DATE_EPOCH,
        }
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        pointer_path = build_root / "current-artifact.json"
        pointer_path.write_text(
            json.dumps(
                {
                    "schema": CURRENT_POINTER_SCHEMA,
                    "artifact_directory": f"artifacts/{binary_sha256}",
                    "binary_sha256": binary_sha256,
                    "release_tag": RELEASE_TAG,
                    "source_url": SOURCE_URL,
                    "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
                    "license_sha256": LICENSE_SHA256,
                }
            ),
            encoding="utf-8",
        )
        return build_root, binary, receipt_path, pointer_path

    def test_valid_content_addressed_artifact_and_manifest_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build_root, binary, receipt_path, pointer_path = self._write_fixture(Path(tmp))
            verified = verify_build_root(build_root, expected_license=TRACKED_LICENSE)

            self.assertEqual(verified.binary, binary.resolve())
            source = verified.source_manifest()
            self.assertTrue(source["release_eligible"])
            self.assertEqual(source["release_tag"], RELEASE_TAG)
            validate_source_manifest(
                source,
                binary_sha256=verified.binary_sha256,
                receipt_bytes=receipt_path.read_bytes(),
                pointer_bytes=pointer_path.read_bytes(),
            )

    def test_binary_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build_root, binary, _, _ = self._write_fixture(Path(tmp))
            binary.write_bytes(b"changed")
            binary.chmod(0o755)
            with self.assertRaisesRegex(Dcm2niixSourceArtifactError, "binary SHA-256"):
                verify_build_root(build_root, expected_license=TRACKED_LICENSE)

    def test_pointer_cannot_select_a_non_content_addressed_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build_root, _, _, pointer_path = self._write_fixture(Path(tmp))
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            pointer["artifact_directory"] = "artifacts/latest"
            pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
            with self.assertRaisesRegex(Dcm2niixSourceArtifactError, "artifact_directory mismatch"):
                verify_build_root(build_root, expected_license=TRACKED_LICENSE)

    def test_receipt_source_or_linkage_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build_root, _, receipt_path, _ = self._write_fixture(Path(tmp))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["source_archive_sha256"] = "0" * 64
            receipt["linkage"]["rpaths"] = ["/opt/homebrew/lib"]
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(Dcm2niixSourceArtifactError, "source_archive_sha256"):
                verify_build_root(build_root, expected_license=TRACKED_LICENSE)

    def test_receipt_must_name_its_exact_content_addressed_artifact_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build_root, _, receipt_path, _ = self._write_fixture(Path(tmp))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["artifact_directory"] = "artifacts/not-the-binary-digest"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(Dcm2niixSourceArtifactError, "artifact_directory"):
                verify_build_root(build_root, expected_license=TRACKED_LICENSE)

    def test_artifact_license_must_match_pinned_official_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build_root, binary, _, _ = self._write_fixture(Path(tmp))
            artifact_license = binary.parent / "licenses" / "dcm2niix-license.txt"
            artifact_license.write_text("not the upstream license", encoding="utf-8")
            with self.assertRaisesRegex(Dcm2niixSourceArtifactError, "artifact license"):
                verify_build_root(build_root, expected_license=TRACKED_LICENSE)

    def test_symlinked_pointer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build_root, _, _, pointer_path = self._write_fixture(Path(tmp))
            real_pointer = build_root / "real-pointer.json"
            pointer_path.rename(real_pointer)
            pointer_path.symlink_to(real_pointer)
            with self.assertRaisesRegex(Dcm2niixSourceArtifactError, "non-symlink"):
                verify_build_root(build_root, expected_license=TRACKED_LICENSE)

    def test_manifest_cannot_downgrade_release_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build_root, _, receipt_path, pointer_path = self._write_fixture(Path(tmp))
            verified = verify_build_root(build_root, expected_license=TRACKED_LICENSE)
            source = verified.source_manifest()
            source["release_eligible"] = False
            with self.assertRaisesRegex(Dcm2niixSourceArtifactError, "pinned source-build"):
                validate_source_manifest(
                    source,
                    binary_sha256=verified.binary_sha256,
                    receipt_bytes=receipt_path.read_bytes(),
                    pointer_bytes=pointer_path.read_bytes(),
                )

    def test_pointer_and_receipt_reject_unrecognized_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build_root, _, _, pointer_path = self._write_fixture(Path(tmp))
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            pointer["latest"] = True
            pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
            with self.assertRaisesRegex(Dcm2niixSourceArtifactError, "field set mismatch"):
                verify_build_root(build_root, expected_license=TRACKED_LICENSE)

    def test_symlinked_build_root_is_rejected_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root, _, _, _ = self._write_fixture(root)
            linked_root = root / "linked-build"
            linked_root.symlink_to(build_root, target_is_directory=True)
            with self.assertRaisesRegex(Dcm2niixSourceArtifactError, "non-symlink directory"):
                verify_build_root(linked_root, expected_license=TRACKED_LICENSE)

    def test_symlinked_expected_license_is_rejected_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root, _, _, _ = self._write_fixture(root)
            linked_license = root / "license.txt"
            linked_license.symlink_to(TRACKED_LICENSE)
            with self.assertRaisesRegex(Dcm2niixSourceArtifactError, "non-symlink file"):
                verify_build_root(build_root, expected_license=linked_license)


if __name__ == "__main__":
    unittest.main()
