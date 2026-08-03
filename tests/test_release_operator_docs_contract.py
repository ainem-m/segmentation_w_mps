from __future__ import annotations

import unittest
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseOperatorDocumentationContractTests(unittest.TestCase):
    def test_release_native_artifact_preparation_has_one_canonical_order(self) -> None:
        expected_commands = (
            "scripts/build_gdcm_macos14_arm64.sh",
            "scripts/build_dicom_normalizer_mac.sh",
            "scripts/build_dcm2niix_macos14_arm64.sh",
        )

        for relative in (
            "docs/06_PACKAGING_DISTRIBUTION.md",
            "docs/33_MAC_NOTARIZATION.md",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            positions = [text.index(command) for command in expected_commands]
            self.assertEqual(positions, sorted(positions), relative)

        packaging = (ROOT / "docs/06_PACKAGING_DISTRIBUTION.md").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            packaging,
            r"will not download or build\s+GDCM implicitly",
        )
        self.assertIn(
            "GDCM 3.2.7 source artifact", packaging
        )

    def test_release_docs_use_the_verified_dcm2niix_artifact_not_an_env_override(self) -> None:
        for relative in (
            "docs/28_DICOM_INTAKE_AND_DISTRIBUTION_BOUNDARY.md",
            "docs/33_MAC_NOTARIZATION.md",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("scripts/build_dcm2niix_macos14_arm64.sh", text, relative)
            self.assertIn("current-artifact.json", text, relative)
            self.assertIn("development-only", text, relative)
            self.assertIn("Developer ID", text, relative)
            self.assertNotIn(
                "export TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX=/path/to/dcm2niix",
                text,
                relative,
            )

    def test_lacramy_handoff_is_explicitly_historical(self) -> None:
        text = (ROOT / "docs/36_LACRAMY_COM_CONTENT_HANDOFF.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Historical handoff", text)
        self.assertIn("must not be used for current release", text)
        self.assertIn("stable-v2", text)
        self.assertIn("0.1.2", text)

    def test_test_account_manual_names_the_fail_closed_native_evidence(self) -> None:
        text = (ROOT / "docs/28_TEST_ACCOUNT_INSTALL_VERIFICATION.md").read_text(
            encoding="utf-8"
        )

        for check_name in (
            "app_and_wheel_macho_macos14_arm64",
            "dicom_helpers_system_linkage_no_rpath",
            "normalizer_source_matches_bundled_receipts",
            "dcm2niix_source_matches_bundled_receipt_and_pointer",
        ):
            self.assertIn(check_name, text)

    def test_test_account_manual_requires_notary_receipt_bound_final_import(self) -> None:
        text = (ROOT / "docs/28_TEST_ACCOUNT_INSTALL_VERIFICATION.md").read_text(
            encoding="utf-8"
        )

        for marker in (
            "TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_RECEIPT",
            "TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_DMG_SHA256",
            "final_dmg_sha256",
            "TOTALSEGMENTATOR_WRAPPER_MAC_TEST_ACCOUNT_DEVELOPMENT_PREFLIGHT",
            "development_preflight_not_release_evidence",
        ):
            self.assertIn(marker, text)
        self.assertIn("can never satisfy the final", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
