from __future__ import annotations

from pathlib import Path
from typing import Any


def collect_mask_stats(mask_dir: Path, *, recursive: bool = False) -> dict[str, Any]:
    masks = []
    pattern = "**/*.nii.gz" if recursive else "*.nii.gz"
    for path in sorted(mask_dir.glob(pattern)):
        masks.append(_mask_entry(path))
    return {
        "mask_dir": str(mask_dir.resolve()),
        "mask_count": len(masks),
        "masks": masks,
    }


def _mask_entry(path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": path.name,
        "label": _label_key(path),
        "path": str(path.resolve()),
        "status": "error",
        "nonzero_voxels": None,
        "dimensions": None,
        "spacing": None,
        "error": None,
    }
    try:
        import nibabel as nib
        import numpy as np

        image = nib.load(str(path))
        data = np.asanyarray(image.dataobj)
        entry.update(
            {
                "status": "ok",
                "nonzero_voxels": int(np.count_nonzero(data)),
                "dimensions": list(image.shape[:3]),
                "spacing": [float(value) for value in image.header.get_zooms()[:3]],
                "error": None,
            }
        )
    except Exception as exc:  # noqa: BLE001
        entry["error"] = repr(exc)
    return entry


def _label_key(path: Path) -> str:
    name = path.name
    if name.endswith(".nii.gz"):
        return name[: -len(".nii.gz")]
    return path.stem
