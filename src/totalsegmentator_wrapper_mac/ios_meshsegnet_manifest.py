from __future__ import annotations

from typing import Any


MODEL_ID = "huathedev/3D-Teeth-Scan-Semantic-Segmentation-with-MeshSegNet"
MODEL_SOURCE = f"https://huggingface.co/spaces/{MODEL_ID}"
MODEL_COMMIT = "4178a5c748be683b68fc85423e09bd53096e3daf"
MODEL_FILENAME = "model.tar"
EXPECTED_MODEL_SHA256 = (
    "3d2e44db8865ff3968803e86dadcf73cf9c4b738ddc35bfb3bc42c02347d7a0c"
)
MODEL_LICENSE = "Apache-2.0"
MODEL_LICENSE_URL = "https://www.apache.org/licenses/LICENSE-2.0"
MODEL_CARD_URL = f"{MODEL_SOURCE}/blob/{MODEL_COMMIT}/README.md"
MODEL_DOWNLOAD_URL = (
    f"{MODEL_SOURCE}/resolve/{MODEL_COMMIT}/{MODEL_FILENAME}?download=true"
)
SUPPORTED_JAWS = ("upper", "lower")


def model_provenance() -> dict[str, Any]:
    """Return immutable attribution data recorded with every IOS result."""
    return {
        "model_id": MODEL_ID,
        "source": MODEL_SOURCE,
        "commit": MODEL_COMMIT,
        "filename": MODEL_FILENAME,
        "sha256": EXPECTED_MODEL_SHA256,
        "license": MODEL_LICENSE,
        "license_url": MODEL_LICENSE_URL,
        "model_card_url": MODEL_CARD_URL,
        "download_url": MODEL_DOWNLOAD_URL,
        "supported_jaws": list(SUPPORTED_JAWS),
        "checkpoint_redistributed_by_this_project": False,
    }
