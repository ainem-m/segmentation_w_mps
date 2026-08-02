from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "35_CLOUDFLARE_DISTRIBUTION.md"


class CloudflareTopologyGateTests(unittest.TestCase):
    def test_stable_v2_requires_external_deployment_topology_attestation(self) -> None:
        runbook = RUNBOOK.read_text(encoding="utf-8")

        for required in (
            "Cloudflare deployment-topology gate",
            "LOCAL PASS",
            "EXTERNAL-STATE UNVERIFIED",
            "totalsegmentator-wrapper-mac",
            "lacramy-apps",
            "Git auto-deploy",
            "direct source deployment",
            "integration-paused",
            "PROMOTION_RECEIPT.json",
            "does not prove",
            "stable-v2 promotion",
            "R2 upload",
            "wrangler pages deploy",
        ):
            self.assertIn(required, runbook)

        self.assertIn(
            "Do not upload the immutable R2 objects, stable-v2/update.json, or Pages stage",
            runbook,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
