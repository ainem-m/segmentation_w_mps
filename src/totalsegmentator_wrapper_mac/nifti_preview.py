from __future__ import annotations

from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np

from totalsegmentator_wrapper_mac.benchmark import write_json
from totalsegmentator_wrapper_mac.rescue_pipeline import (
    _physical_aspect_image,
    _write_normalized_pgm,
)


PREVIEW_SCHEMA = "totalsegmentator_wrapper_mac.nifti_preview.v1"


def write_nifti_preview(
    *,
    input_path: Path,
    output_dir: Path,
    output_json: Path,
    chunk_depth: int = 32,
) -> dict[str, Any]:
    """Write non-inference center MPR images and volume-level empty-input stats."""

    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"NIfTI input not found: {input_path}")
    if chunk_depth < 1:
        raise ValueError("chunk_depth must be >= 1")

    image = nib.load(str(input_path), mmap=True, keep_file_open=True)
    if len(image.shape) != 3 or any(int(value) <= 0 for value in image.shape):
        raise ValueError("NIfTI preview requires a non-empty 3D volume")

    shape = tuple(int(value) for value in image.shape)
    spacing = tuple(float(value) for value in image.header.get_zooms()[:3])
    if len(spacing) != 3 or any(not np.isfinite(value) or value <= 0 for value in spacing):
        raise ValueError("NIfTI preview requires valid positive 3D spacing")

    finite_count = 0
    volume_min = np.inf
    volume_max = -np.inf
    for start in range(0, shape[2], chunk_depth):
        stop = min(start + chunk_depth, shape[2])
        chunk = np.asanyarray(image.dataobj[:, :, start:stop])
        finite = chunk[np.isfinite(chunk)]
        finite_count += int(finite.size)
        if finite.size:
            volume_min = min(volume_min, float(np.min(finite)))
            volume_max = max(volume_max, float(np.max(finite)))

    uniform_or_empty = finite_count == 0 or not volume_max > volume_min
    if finite_count == 0:
        volume_min = volume_max = 0.0

    center_slices = (
        (
            "axial",
            np.asanyarray(image.dataobj[:, :, shape[2] // 2]).T,
            spacing[1],
            spacing[0],
        ),
        (
            "coronal",
            np.asanyarray(image.dataobj[:, shape[1] // 2, :]).T,
            spacing[2],
            spacing[0],
        ),
        (
            "sagittal",
            np.asanyarray(image.dataobj[shape[0] // 2, :, :]).T,
            spacing[2],
            spacing[1],
        ),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    previews: list[dict[str, Any]] = []
    for plane, source, row_spacing, column_spacing in center_slices:
        display = _physical_aspect_image(
            source,
            row_spacing=row_spacing,
            column_spacing=column_spacing,
        )
        path = output_dir / f"{plane}.pgm"
        minimum, maximum, uniform = _write_normalized_pgm(path, display)
        previews.append(
            {
                "plane": plane,
                "path": str(path.resolve()),
                "width": int(display.shape[1]),
                "height": int(display.shape[0]),
                "row_spacing_mm": float(source.shape[0] * row_spacing / display.shape[0]),
                "column_spacing_mm": float(
                    source.shape[1] * column_spacing / display.shape[1]
                ),
                "min": minimum,
                "max": maximum,
                "uniform_or_empty": uniform,
            }
        )

    result: dict[str, Any] = {
        "schema": PREVIEW_SCHEMA,
        "input": str(input_path),
        "inference_started": False,
        "volume": {
            "shape_xyz": list(shape),
            "spacing_xyz": list(spacing),
            "finite_voxel_count": finite_count,
            "min": float(volume_min),
            "max": float(volume_max),
            "uniform_or_empty": uniform_or_empty,
        },
        "outputs": {
            "mpr_preview": previews,
        },
    }
    write_json(output_json, result)
    return result
