from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

from totalsegmentator_wrapper_mac.rescue_geometry import validate_spacing_xyz
from totalsegmentator_wrapper_mac.rescue_pipeline import (
    RescuePipelineError,
    create_estimate,
    validate_decoded_volume,
)


def foreground_bbox_xyz(volume_xyz: np.ndarray) -> dict[str, Any]:
    """Find a conservative whole-series foreground box and border-overlay warning."""

    volume = validate_decoded_volume(volume_xyz).astype(np.float32, copy=False)
    corners = np.concatenate(
        [
            volume[:2, :2, :].ravel(),
            volume[-2:, :2, :].ravel(),
            volume[:2, -2:, :].ravel(),
            volume[-2:, -2:, :].ravel(),
        ]
    )
    background = float(np.median(corners))
    deviation = np.abs(volume - background)
    nonzero = deviation[deviation > 0]
    if nonzero.size == 0:
        return {
            "bbox": {"min": [0, 0, 0], "max_exclusive": list(volume.shape)},
            "foreground_fraction": 0.0,
            "background_value": background,
            "detected": False,
            "warnings": ["foreground_not_detected"],
        }
    threshold = max(float(np.percentile(nonzero, 10)) * 0.5, 1e-6)
    mask = deviation > threshold
    coordinates = np.argwhere(mask)
    lower = coordinates.min(axis=0)
    upper = coordinates.max(axis=0) + 1

    border_width_x = max(1, min(4, volume.shape[0] // 8))
    border_width_y = max(1, min(4, volume.shape[1] // 8))
    border_mask = np.zeros(volume.shape, dtype=bool)
    border_mask[:border_width_x, :, :] = True
    border_mask[-border_width_x:, :, :] = True
    border_mask[:, :border_width_y, :] = True
    border_mask[:, -border_width_y:, :] = True
    border_coverage = float(np.count_nonzero(mask & border_mask) / np.count_nonzero(border_mask))
    warnings: list[str] = []
    if border_coverage > 0.25:
        warnings.append("large_border_or_burned_in_overlay_candidate")
    return {
        "bbox": {
            "min": [int(value) for value in lower],
            "max_exclusive": [int(value) for value in upper],
        },
        "foreground_fraction": float(np.mean(mask)),
        "background_value": background,
        "detected": True,
        "border_foreground_coverage": border_coverage,
        "warnings": warnings,
    }


def series_count_fov_seed(
    *,
    primary_foreground_shape_xyz: Sequence[int],
    axial_slice_step_mm: float | None,
    coronal_count: int | None = None,
    coronal_slice_step_mm: float | None = None,
    sagittal_count: int | None = None,
    sagittal_slice_step_mm: float | None = None,
    fallback_spacing_mm: float = 1.0,
) -> dict[str, Any]:
    if len(primary_foreground_shape_xyz) != 3 or any(
        int(value) <= 0 for value in primary_foreground_shape_xyz
    ):
        raise RescuePipelineError("primary foreground shape must contain positive XYZ sizes")
    if not math.isfinite(fallback_spacing_mm) or fallback_spacing_mm <= 0:
        raise RescuePipelineError("fallback spacing must be finite and positive")
    width_x, width_y, _depth_z = (int(value) for value in primary_foreground_shape_xyz)
    evidence: list[str] = []

    x_spacing = _extent_spacing(
        count=sagittal_count,
        step=sagittal_slice_step_mm,
        foreground_size=width_x,
    )
    if x_spacing is not None:
        evidence.append("sagittal_series_count_fov_seed")
    y_spacing = _extent_spacing(
        count=coronal_count,
        step=coronal_slice_step_mm,
        foreground_size=width_y,
    )
    if y_spacing is not None:
        evidence.append("coronal_series_count_fov_seed")
    z_spacing = _positive_or_none(axial_slice_step_mm)
    if z_spacing is not None:
        evidence.append("axial_slice_step")
    spacing = (
        x_spacing or fallback_spacing_mm,
        y_spacing or fallback_spacing_mm,
        z_spacing or fallback_spacing_mm,
    )
    fallback_axes = [
        axis
        for axis, value in zip(("x", "y", "z"), (x_spacing, y_spacing, z_spacing), strict=True)
        if value is None
    ]
    return {
        "spacing_xyz": list(validate_spacing_xyz(spacing)),
        "spacing_source": evidence or ["fallback_initial_candidate"],
        "confidence": "unknown" if len(fallback_axes) == 3 else "low",
        "fallback_axes": fallback_axes,
        "limitations": ["series_count_crop_and_zoom_unknown"],
    }


def tri_planar_spacing_search(
    primary_volume_xyz: np.ndarray,
    reference_images: Mapping[str, np.ndarray],
    *,
    seed_spacing_xyz: Sequence[float],
    scale_factors: Sequence[float] = (0.75, 0.875, 1.0, 1.125, 1.25),
    pyramid_scales: Sequence[float] = (0.25, 0.5),
    max_evaluations: int = 64,
    top_k: int = 5,
    ambiguity_margin: float = 0.01,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Bounded deterministic grid search using normalized mutual information.

    References are representative 2-D coronal (X,Z) and/or sagittal (Y,Z)
    images already decoded by the native layer. This routine never reads DICOM
    and never starts inference.
    """

    volume = validate_decoded_volume(primary_volume_xyz).astype(np.float32, copy=False)
    seed = validate_spacing_xyz(seed_spacing_xyz)
    factors = tuple(
        float(value)
        for value in scale_factors
        if math.isfinite(float(value)) and float(value) > 0
    )
    if not factors or max_evaluations <= 0 or top_k <= 0:
        raise RescuePipelineError("registration search bounds are invalid")
    references = _validated_references(reference_images)
    fallback = _registration_fallback(seed, reason="reference_planes_unavailable")
    if not references:
        return fallback

    source_planes = {
        "coronal": volume[:, volume.shape[1] // 2, :],
        "sagittal": volume[volume.shape[0] // 2, :, :],
    }
    candidates: list[dict[str, Any]] = []
    evaluations = 0
    cancelled = False
    for x_factor, y_factor in itertools.product(factors, factors):
        if evaluations >= max_evaluations:
            break
        if should_cancel is not None and should_cancel():
            cancelled = True
            break
        spacing = (seed[0] * x_factor, seed[1] * y_factor, seed[2])
        scores: list[float] = []
        for plane, reference in references.items():
            source = source_planes[plane]
            plane_spacing = (
                (spacing[0], spacing[2])
                if plane == "coronal"
                else (spacing[1], spacing[2])
            )
            for pyramid_scale in pyramid_scales:
                if pyramid_scale <= 0 or pyramid_scale > 1:
                    continue
                target_shape = tuple(
                    max(8, int(round(size * pyramid_scale))) for size in reference.shape
                )
                target = _resize_image(reference, target_shape)
                moving = _physical_letterbox(source, plane_spacing, target_shape)
                score = _normalized_mutual_information(moving, target)
                if math.isfinite(score):
                    scores.append(score)
        evaluations += 1
        if scores:
            candidates.append(
                {
                    "spacing_xyz": [float(value) for value in spacing],
                    "score": float(np.mean(scores)),
                }
            )

    if not candidates:
        fallback["cancelled"] = cancelled
        fallback["evaluations"] = evaluations
        fallback["reason"] = "cancelled" if cancelled else "registration_metric_unavailable"
        return fallback
    candidates.sort(
        key=lambda value: (
            -value["score"],
            value["spacing_xyz"],
        )
    )
    ranked = candidates[:top_k]
    margin = (
        float(ranked[0]["score"] - ranked[1]["score"])
        if len(ranked) > 1
        else None
    )
    ambiguous = margin is None or margin <= ambiguity_margin
    return {
        "status": "estimated",
        "estimated_spacing_xyz": ranked[0]["spacing_xyz"],
        "confidence": "unknown" if cancelled else ("low" if ambiguous else "medium"),
        "converged": not cancelled,
        "cancelled": cancelled,
        "metric": "multi_scale_normalized_mutual_information",
        "residual": float(2.0 - ranked[0]["score"]),
        "top2_score_margin": margin,
        "ambiguous": ambiguous,
        "evaluations": evaluations,
        "max_evaluations": max_evaluations,
        "alternatives": ranked,
        "limitations": [
            "screen_capture_crop_offset_and_zoom_may_be_non_unique",
            "registration_not_validated_on_target_real_data",
        ],
    }


def cross_validate_reconstruction_spacing(
    spacing_by_group: Mapping[str, Sequence[float]],
    *,
    tolerance_fraction: float = 0.15,
) -> dict[str, Any]:
    """Compare reconstruction groups without fusing or substituting their voxels."""

    if not math.isfinite(tolerance_fraction) or tolerance_fraction <= 0:
        raise RescuePipelineError("cross-validation tolerance must be positive")
    validated: list[tuple[str, tuple[float, float, float]]] = []
    for group, spacing in sorted(spacing_by_group.items()):
        try:
            validated.append((str(group), validate_spacing_xyz(spacing)))
        except (TypeError, ValueError):
            continue
    if len(validated) < 2:
        return {
            "available": False,
            "consistent": None,
            "max_disagreement_mm": None,
            "max_disagreement_fraction": None,
            "groups": [group for group, _spacing in validated],
            "confidence_effect": "none",
            "reason": "fewer_than_two_valid_reconstruction_groups",
        }
    disagreements_mm = [0.0, 0.0, 0.0]
    disagreements_fraction = [0.0, 0.0, 0.0]
    for (_left_group, left), (_right_group, right) in itertools.combinations(validated, 2):
        for axis in range(3):
            difference = abs(left[axis] - right[axis])
            denominator = max(left[axis], right[axis])
            disagreements_mm[axis] = max(disagreements_mm[axis], difference)
            disagreements_fraction[axis] = max(
                disagreements_fraction[axis],
                difference / denominator,
            )
    consistent = max(disagreements_fraction) <= tolerance_fraction
    return {
        "available": True,
        "consistent": consistent,
        "max_disagreement_mm": disagreements_mm,
        "max_disagreement_fraction": disagreements_fraction,
        "groups": [group for group, _spacing in validated],
        "confidence_effect": "support" if consistent else "decrease",
        "reason": (
            "independent_reconstruction_spacing_consistent"
            if consistent
            else "independent_reconstruction_spacing_disagrees"
        ),
    }


def estimate_rescue_spacing(
    primary_volume_xyz: np.ndarray,
    *,
    source_manifest_sha256: str,
    spacing_hints_xyz: Sequence[float | None] = (None, None, None),
    reference_images: Mapping[str, np.ndarray] | None = None,
    axial_slice_step_mm: float | None = None,
    coronal_count: int | None = None,
    coronal_slice_step_mm: float | None = None,
    sagittal_count: int | None = None,
    sagittal_slice_step_mm: float | None = None,
    max_registration_evaluations: int = 64,
    should_cancel: Callable[[], bool] | None = None,
    used_series: Sequence[Mapping[str, Any]] = (),
    used_dicom_tags: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Compose foreground, FOV seed, and bounded registration into schema v2."""

    volume = validate_decoded_volume(primary_volume_xyz)
    foreground = foreground_bbox_xyz(volume)
    bounds = foreground["bbox"]
    foreground_shape = tuple(
        int(upper) - int(lower)
        for lower, upper in zip(
            bounds["min"],
            bounds["max_exclusive"],
            strict=True,
        )
    )
    seed = series_count_fov_seed(
        primary_foreground_shape_xyz=foreground_shape,
        axial_slice_step_mm=axial_slice_step_mm,
        coronal_count=coronal_count,
        coronal_slice_step_mm=coronal_slice_step_mm,
        sagittal_count=sagittal_count,
        sagittal_slice_step_mm=sagittal_slice_step_mm,
    )
    if len(spacing_hints_xyz) != 3:
        raise RescuePipelineError("spacing hints must contain X, Y, Z")
    candidate = [
        float(hint) if _positive_or_none(hint) is not None else float(seed["spacing_xyz"][axis])
        for axis, hint in enumerate(spacing_hints_xyz)
    ]
    sources = list(seed["spacing_source"])
    for axis, hint in zip(("x", "y", "z"), spacing_hints_xyz, strict=True):
        if _positive_or_none(hint) is not None:
            sources.append(f"{axis}_standard_or_vendor_tag_hint")
    registration = tri_planar_spacing_search(
        volume,
        reference_images or {},
        seed_spacing_xyz=candidate,
        max_evaluations=max_registration_evaluations,
        should_cancel=should_cancel,
    )
    if registration["converged"]:
        candidate = list(registration["estimated_spacing_xyz"])
        sources.append("tri_planar_registration")
    metadata = create_estimate(
        volume,
        spacing_hints_xyz=candidate,
        source_manifest_sha256=source_manifest_sha256,
        spacing_sources=sources,
        used_series=used_series,
        used_dicom_tags=used_dicom_tags,
        registration=registration,
        foreground=foreground,
        alternatives=registration["alternatives"],
    )
    metadata["estimate"]["status"] = registration["status"]
    metadata["estimate"]["confidence"]["overall"] = registration["confidence"]
    metadata["estimate"]["confidence"]["score"] = (
        registration["alternatives"][0].get("score")
        if registration["alternatives"]
        else None
    )
    metadata["estimate"]["confidence"]["convergence"] = registration["converged"]
    metadata["estimate"]["confidence"]["top2_score_margin"] = registration[
        "top2_score_margin"
    ]
    metadata["estimate"]["confidence"]["limitations"] = list(
        dict.fromkeys(
            metadata["estimate"]["confidence"]["limitations"]
            + seed["limitations"]
            + foreground["warnings"]
            + registration["limitations"]
        )
    )
    if registration["confidence"] == "unknown":
        metadata["estimate"]["confidence"]["per_axis"] = {
            axis: "unknown" for axis in ("x", "y", "z")
        }
    elif registration["converged"]:
        metadata["estimate"]["confidence"]["per_axis"] = {
            "x": registration["confidence"],
            "y": registration["confidence"],
            "z": "low",
        }
    return metadata


def _validated_references(
    references: Mapping[str, np.ndarray],
) -> dict[str, np.ndarray]:
    result: dict[str, np.ndarray] = {}
    for plane in ("coronal", "sagittal"):
        if plane not in references:
            continue
        image = np.asarray(references[plane], dtype=np.float32)
        if (
            image.ndim == 2
            and min(image.shape) >= 2
            and np.all(np.isfinite(image))
            and float(np.ptp(image)) > 0
        ):
            result[plane] = image
    return result


def _physical_letterbox(
    image: np.ndarray,
    spacing: tuple[float, float],
    target_shape: tuple[int, int],
) -> np.ndarray:
    physical_aspect = (image.shape[0] * spacing[0]) / (image.shape[1] * spacing[1])
    target_aspect = target_shape[0] / target_shape[1]
    if physical_aspect >= target_aspect:
        fitted = (target_shape[0], max(1, int(round(target_shape[0] / physical_aspect))))
    else:
        fitted = (max(1, int(round(target_shape[1] * physical_aspect))), target_shape[1])
    scaled = _resize_image(image, fitted)
    canvas = np.full(target_shape, float(np.median(image)), dtype=np.float32)
    start_x = (target_shape[0] - fitted[0]) // 2
    start_y = (target_shape[1] - fitted[1]) // 2
    canvas[start_x : start_x + fitted[0], start_y : start_y + fitted[1]] = scaled
    return canvas


def _normalized_mutual_information(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape or float(np.ptp(left)) == 0 or float(np.ptp(right)) == 0:
        return float("nan")
    histogram, _, _ = np.histogram2d(left.ravel(), right.ravel(), bins=32)
    probability = histogram / np.sum(histogram)
    px = probability.sum(axis=1)
    py = probability.sum(axis=0)
    hx = _entropy(px)
    hy = _entropy(py)
    hxy = _entropy(probability.ravel())
    if hxy <= 0:
        return float("nan")
    return (hx + hy) / hxy


def _resize_image(image: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    """Dependency-light deterministic bilinear resize for bounded registration."""

    source = np.asarray(image, dtype=np.float32)
    if source.shape == target_shape:
        return source.copy()
    source_x = np.arange(source.shape[0], dtype=np.float64)
    target_x = np.linspace(0.0, source.shape[0] - 1, target_shape[0])
    intermediate = np.empty((target_shape[0], source.shape[1]), dtype=np.float32)
    for column in range(source.shape[1]):
        intermediate[:, column] = np.interp(target_x, source_x, source[:, column])
    source_y = np.arange(source.shape[1], dtype=np.float64)
    target_y = np.linspace(0.0, source.shape[1] - 1, target_shape[1])
    output = np.empty(target_shape, dtype=np.float32)
    for row in range(target_shape[0]):
        output[row, :] = np.interp(target_y, source_y, intermediate[row, :])
    return output


def _entropy(probability: np.ndarray) -> float:
    positive = probability[probability > 0]
    return float(-np.sum(positive * np.log(positive)))


def _registration_fallback(
    seed: tuple[float, float, float],
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "status": "fallback_initial_candidate",
        "estimated_spacing_xyz": list(seed),
        "confidence": "unknown",
        "converged": False,
        "cancelled": False,
        "metric": "multi_scale_normalized_mutual_information",
        "residual": None,
        "top2_score_margin": None,
        "ambiguous": True,
        "evaluations": 0,
        "alternatives": [{"spacing_xyz": list(seed), "score": None}],
        "reason": reason,
        "limitations": ["registration_evidence_unavailable"],
    }


def _extent_spacing(
    *,
    count: int | None,
    step: float | None,
    foreground_size: int,
) -> float | None:
    numeric_step = _positive_or_none(step)
    if count is None or int(count) <= 0 or numeric_step is None:
        return None
    return int(count) * numeric_step / foreground_size


def _positive_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) and numeric > 0 else None
