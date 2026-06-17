from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path
from typing import Any


def input_metadata(input_path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "basename": input_path.name,
        "path_hash": hashlib.sha256(str(input_path.resolve()).encode("utf-8")).hexdigest(),
        "format": "nifti" if input_path.name.endswith((".nii", ".nii.gz")) else "unknown",
        "dimensions": None,
        "spacing": None,
    }
    try:
        import nibabel as nib

        img = nib.load(str(input_path))
        metadata["dimensions"] = list(img.shape)
        metadata["spacing"] = [float(value) for value in img.header.get_zooms()[:3]]
    except Exception as exc:  # noqa: BLE001
        metadata["read_error"] = repr(exc)
    return metadata


def environment_metadata() -> dict[str, Any]:
    env: dict[str, Any] = {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "python": {
            "version": sys.version,
            "executable": sys.executable,
        },
        "torch": {
            "version": None,
            "mps_built": None,
            "mps_available": None,
        },
        "totalsegmentator": {
            "version": None,
        },
    }
    try:
        import torch

        env["torch"] = {
            "version": torch.__version__,
            "mps_built": bool(torch.backends.mps.is_built()),
            "mps_available": bool(torch.backends.mps.is_available()),
        }
    except Exception as exc:  # noqa: BLE001
        env["torch"]["error"] = repr(exc)

    try:
        import importlib.metadata

        env["totalsegmentator"]["version"] = importlib.metadata.version("TotalSegmentator")
    except Exception as exc:  # noqa: BLE001
        env["totalsegmentator"]["error"] = repr(exc)
    return env


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
