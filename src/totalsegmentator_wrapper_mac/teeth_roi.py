from __future__ import annotations

from pathlib import Path
from typing import Any

from totalsegmentator_wrapper_mac.benchmark import write_json
from totalsegmentator_wrapper_mac.outputs import CaseOutput


TEETH_MASK_NAMES = ("teeth_upper.nii.gz", "teeth_lower.nii.gz")
NEAR_WHOLE_VOLUME_RATIO_THRESHOLD = 0.50
NEAR_WHOLE_AXIS_EXTENT_THRESHOLD = 0.90


def create_teeth_roi_from_craniofacial_case(
    *,
    input_path: Path,
    craniofacial_case_dir: Path,
    output_path: Path,
    roi_json_path: Path,
    margin_mm: float,
) -> dict[str, Any]:
    mask_dir = craniofacial_case_dir / "segmentations" / "raw_totalseg"
    mask_paths = [mask_dir / name for name in TEETH_MASK_NAMES]
    metadata = crop_to_mask_bbox(
        input_path=input_path,
        mask_paths=mask_paths,
        output_path=output_path,
        margin_mm=margin_mm,
    )
    metadata["craniofacial_case_dir"] = str(craniofacial_case_dir.resolve())
    write_json(roi_json_path, metadata)
    return metadata


def create_teeth_roi_for_case(
    *,
    case: CaseOutput,
    input_path: Path,
    craniofacial_case_dir: Path,
    margin_mm: float,
) -> dict[str, Any]:
    return create_teeth_roi_from_craniofacial_case(
        input_path=input_path,
        craniofacial_case_dir=craniofacial_case_dir,
        output_path=case.teeth_roi_input_path,
        roi_json_path=case.teeth_roi_path,
        margin_mm=margin_mm,
    )


def crop_to_mask_bbox(
    *,
    input_path: Path,
    mask_paths: list[Path],
    output_path: Path,
    margin_mm: float,
) -> dict[str, Any]:
    import nibabel as nib
    import numpy as np

    input_path = input_path.resolve()
    image = nib.load(str(input_path))
    source_shape = np.array(image.shape[:3], dtype=int)
    spacing = np.array(image.header.get_zooms()[:3], dtype=float)
    mask_union = None
    mask_entries = []
    mask_nonzero_voxels: dict[str, int] = {}

    for mask_path in mask_paths:
        mask_path = mask_path.resolve()
        if not mask_path.exists():
            raise FileNotFoundError(f"Required teeth preflight mask not found: {mask_path}")
        mask_image = nib.load(str(mask_path))
        if tuple(mask_image.shape[:3]) != tuple(source_shape):
            raise RuntimeError(
                f"Mask shape {mask_image.shape[:3]} does not match input shape {tuple(source_shape)}: "
                f"{mask_path.name}"
            )
        if not np.allclose(mask_image.affine, image.affine, atol=1e-3):
            raise RuntimeError(f"Mask affine does not match input affine: {mask_path.name}")

        mask = np.asanyarray(mask_image.dataobj) > 0
        nonzero = int(np.count_nonzero(mask))
        if nonzero == 0:
            raise RuntimeError(f"Required teeth preflight mask is empty: {mask_path.name}")
        mask_union = mask if mask_union is None else (mask_union | mask)
        label = _label_key(mask_path)
        mask_entries.append({"path": str(mask_path), "label": label, "nonzero_voxels": nonzero})
        mask_nonzero_voxels[label] = nonzero

    if mask_union is None or not np.any(mask_union):
        raise RuntimeError("No non-empty dental preflight masks")

    coords = np.argwhere(mask_union)
    lo = coords.min(axis=0)
    hi = coords.max(axis=0) + 1
    raw_size = hi - lo
    raw_voxel_volume_ratio = float(np.prod(raw_size) / np.prod(source_shape))
    raw_axis_extent_ratios = [
        float(raw_size[index] / source_shape[index])
        for index in range(3)
    ]
    raw_near_whole_volume = (
        raw_voxel_volume_ratio > NEAR_WHOLE_VOLUME_RATIO_THRESHOLD
        or max(raw_axis_extent_ratios) > NEAR_WHOLE_AXIS_EXTENT_THRESHOLD
    )
    if raw_near_whole_volume:
        raise RuntimeError(
            "Implausibly large teeth mask bbox: "
            f"voxel_volume_ratio={raw_voxel_volume_ratio:.3f}, "
            f"axis_extent_ratios={[round(value, 3) for value in raw_axis_extent_ratios]}"
        )
    margin_vox = np.ceil(margin_mm / spacing).astype(int)

    lo = np.maximum(lo - margin_vox, 0)
    hi = np.minimum(hi + margin_vox, source_shape)
    roi_size = hi - lo

    if np.any(roi_size < 16):
        raise RuntimeError(f"Implausibly tiny teeth ROI: {roi_size.tolist()}")

    voxel_volume_ratio = float(np.prod(roi_size) / np.prod(source_shape))
    axis_extent_ratios = [
        float(roi_size[index] / source_shape[index])
        for index in range(3)
    ]
    expanded_near_whole_volume = (
        voxel_volume_ratio > NEAR_WHOLE_VOLUME_RATIO_THRESHOLD
        or max(axis_extent_ratios) > NEAR_WHOLE_AXIS_EXTENT_THRESHOLD
    )

    slices = tuple(slice(int(start), int(stop)) for start, stop in zip(lo, hi, strict=True))
    cropped_data = np.asanyarray(image.dataobj)[slices]
    crop_affine = _crop_affine(image.affine, lo)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _save_nifti_with_forms(
        data=cropped_data,
        affine=crop_affine,
        header=image.header.copy(),
        output_path=output_path,
    )
    cropped = nib.load(str(output_path))
    qform_code = int(cropped.header["qform_code"])
    sform_code = int(cropped.header["sform_code"])

    metadata: dict[str, Any] = {
        "source": str(input_path),
        "source_shape": [int(value) for value in source_shape],
        "output": str(output_path.resolve()),
        "mask_paths": mask_entries,
        "mask_nonzero_voxels": mask_nonzero_voxels,
        "margin_mm": float(margin_mm),
        "margin_voxels": [int(value) for value in margin_vox],
        "spacing": [float(value) for value in spacing],
        "input_shape": [int(value) for value in source_shape],
        "roi_shape": [int(value) for value in roi_size],
        "axis_extent_ratios": axis_extent_ratios,
        "voxel_volume_ratio": voxel_volume_ratio,
        "near_whole_volume": raw_near_whole_volume,
        "expanded_near_whole_volume": expanded_near_whole_volume,
        "near_whole_volume_thresholds": {
            "voxel_volume_ratio": NEAR_WHOLE_VOLUME_RATIO_THRESHOLD,
            "axis_extent_ratio": NEAR_WHOLE_AXIS_EXTENT_THRESHOLD,
        },
        "bbox_min_ijk": [int(value) for value in lo],
        "bbox_max_ijk": [int(value) for value in hi],
        "raw_bbox": {
            "start": [int(value) for value in coords.min(axis=0)],
            "stop": [int(value) for value in coords.max(axis=0) + 1],
            "size": [int(value) for value in raw_size],
            "voxel_volume_ratio": raw_voxel_volume_ratio,
            "axis_extent_ratios": raw_axis_extent_ratios,
            "near_whole_volume": raw_near_whole_volume,
        },
        "roi_bbox": {
            "start": [int(value) for value in lo],
            "stop": [int(value) for value in hi],
            "size": [int(value) for value in roi_size],
            "volume_ratio": voxel_volume_ratio,
            "voxel_volume_ratio": voxel_volume_ratio,
            "axis_extent_ratios": axis_extent_ratios,
            "near_whole_volume": expanded_near_whole_volume,
        },
        "slices": {
            axis: [int(lo[index]), int(hi[index])]
            for index, axis in enumerate(("x", "y", "z"))
        },
        "geometry": {
            "affine": [[float(value) for value in row] for row in crop_affine],
            "qform_code": qform_code,
            "sform_code": sform_code,
        },
        "status": "success",
    }
    return metadata


def reembed_labelmap_to_full_space(
    *,
    cropped_label_nii: Path,
    source_nii: Path,
    roi_metadata: dict[str, Any],
    output_full_nii: Path,
) -> dict[str, Any]:
    import nibabel as nib
    import numpy as np

    source = nib.load(str(source_nii))
    crop = nib.load(str(cropped_label_nii))
    slices = slices_from_roi_metadata(roi_metadata)
    expected_shape = tuple(item.stop - item.start for item in slices)
    if tuple(crop.shape[:3]) != expected_shape:
        raise RuntimeError(
            f"Cropped teeth labelmap shape {crop.shape[:3]} does not match ROI shape {expected_shape}"
        )

    crop_data = np.asanyarray(crop.dataobj)
    full = np.zeros(source.shape[:3], dtype=crop_data.dtype)
    full[slices] = crop_data

    output_full_nii.parent.mkdir(parents=True, exist_ok=True)
    _save_nifti_with_forms(
        data=full,
        affine=source.affine,
        header=source.header.copy(),
        output_path=output_full_nii,
    )
    full_image = nib.load(str(output_full_nii))
    affine_matches_source = bool(np.allclose(full_image.affine, source.affine, atol=1e-5))
    return {
        "status": "success",
        "source": str(source_nii.resolve()),
        "cropped_labelmap": str(cropped_label_nii.resolve()),
        "output": str(output_full_nii.resolve()),
        "shape": [int(value) for value in full_image.shape[:3]],
        "source_shape": [int(value) for value in source.shape[:3]],
        "affine_matches_source": affine_matches_source,
        "qform_code": int(full_image.header["qform_code"]),
        "sform_code": int(full_image.header["sform_code"]),
        "nonzero_voxels": int(np.count_nonzero(full)),
    }


def slices_from_roi_metadata(roi_metadata: dict[str, Any]) -> tuple[slice, slice, slice]:
    slices = roi_metadata.get("slices")
    if not isinstance(slices, dict):
        raise RuntimeError("ROI metadata does not contain slices")
    return tuple(
        slice(int(slices[axis][0]), int(slices[axis][1]))
        for axis in ("x", "y", "z")
    )


def _save_nifti_with_forms(*, data: Any, affine: Any, header: Any, output_path: Path) -> None:
    import nibabel as nib

    image = nib.Nifti1Image(data, affine, header=header)
    image.set_qform(affine, code=1)
    image.set_sform(affine, code=1)
    nib.save(image, str(output_path))


def _crop_affine(source_affine: Any, start_ijk: Any) -> Any:
    import numpy as np

    affine = np.array(source_affine, dtype=float, copy=True)
    start_h = np.array(
        [int(start_ijk[0]), int(start_ijk[1]), int(start_ijk[2]), 1.0]
    )
    affine[:3, 3] = (np.asarray(source_affine) @ start_h)[:3]
    return affine


def _label_key(path: Path) -> str:
    name = path.name
    if name.endswith(".nii.gz"):
        return name[: -len(".nii.gz")]
    return path.stem
