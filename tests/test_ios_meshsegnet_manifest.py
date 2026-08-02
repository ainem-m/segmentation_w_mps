from __future__ import annotations

import unittest

from totalsegmentator_wrapper_mac.ios_meshsegnet_manifest import (
    EXPECTED_MODEL_SHA256,
    MODEL_COMMIT,
    MODEL_DOWNLOAD_URL,
    MODEL_FILENAME,
    MODEL_LICENSE,
    SUPPORTED_JAWS,
    model_provenance,
)


class IOSMeshSegNetManifestTests(unittest.TestCase):
    def test_provenance_is_complete_and_distribution_is_external(self) -> None:
        provenance = model_provenance()

        self.assertEqual(provenance["license"], "Apache-2.0")
        self.assertEqual(provenance["sha256"], EXPECTED_MODEL_SHA256)
        self.assertEqual(provenance["commit"], MODEL_COMMIT)
        self.assertEqual(MODEL_FILENAME, "model.tar")
        self.assertIn(f"/resolve/{MODEL_COMMIT}/model.tar", MODEL_DOWNLOAD_URL)
        self.assertEqual(provenance["supported_jaws"], ["upper", "lower"])
        self.assertFalse(provenance["checkpoint_redistributed_by_this_project"])

    def test_validated_upper_and_lower_jaw_mappings_are_exposed(self) -> None:
        self.assertEqual(MODEL_LICENSE, "Apache-2.0")
        self.assertEqual(SUPPORTED_JAWS, ("upper", "lower"))
        self.assertEqual(len(EXPECTED_MODEL_SHA256), 64)
