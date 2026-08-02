from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.verify_license_distribution import (
    validate_setup_weights_manifest as validate_distribution_setup_weights_manifest,
)
import totalsegmentator_wrapper_mac.totalseg_weights_setup as weights_setup
from totalsegmentator_wrapper_mac.setup_manager import _classify_totalseg_weights_failure


class TotalSegWeightsIntegrationTests(unittest.TestCase):
    def _manifest(self) -> dict[str, object]:
        manifest_path = Path(weights_setup.__file__).with_name(
            "totalseg_setup_weights_manifest.json"
        )
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def test_distribution_schema_accepts_only_strict_revalidation_evidence(self) -> None:
        manifest = self._manifest()
        assets = manifest["assets"]
        assert isinstance(assets, list)
        local_asset = next(asset for asset in assets if asset["task_id"] == 115)
        validate_distribution_setup_weights_manifest(manifest)

        local_asset["revalidation_evidence"]["verified_at_utc"] = (
            "2026-99-99T00:00:00Z"
        )
        with self.assertRaisesRegex(RuntimeError, "revalidation evidence"):
            validate_distribution_setup_weights_manifest(manifest)

        local_asset["revalidation_evidence"]["verified_at_utc"] = (
            "2026-08-01T00:00:00Z"
        )
        local_asset["revalidation_evidence"]["official_url"] = (
            "https://example.invalid/unapproved.zip"
        )
        with self.assertRaisesRegex(RuntimeError, "revalidation evidence"):
            validate_distribution_setup_weights_manifest(manifest)

    def test_distribution_schema_rejects_unpreserved_observation_date(self) -> None:
        manifest = self._manifest()
        assets = manifest["assets"]
        assert isinstance(assets, list)
        local_asset = next(asset for asset in assets if asset["task_id"] == 297)
        local_asset["sha256_observed_at"] = "2026-08-01"

        with self.assertRaisesRegex(RuntimeError, "checksum provenance"):
            validate_distribution_setup_weights_manifest(manifest)

    def test_disk_full_has_specific_setup_error_classification(self) -> None:
        self.assertEqual(
            _classify_totalseg_weights_failure(
                "OSError: [Errno 28] No space left on device"
            ),
            "insufficient_disk_space",
        )


if __name__ == "__main__":
    unittest.main()
