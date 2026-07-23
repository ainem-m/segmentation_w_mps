from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np


SCHEMA_VERSION = "totalsegmentator_wrapper_mac.rescue_geometry.v2"
AXIS_NAMES = ("x", "y", "z")
CONFIDENCE_LABELS = {"high", "medium", "low", "unknown"}


class RescueGeometryError(ValueError):
    """Raised when an editable rescue geometry value is not technically usable."""


def validate_spacing_xyz(values: Sequence[float]) -> tuple[float, float, float]:
    if len(values) != 3:
        raise RescueGeometryError("spacing_xyz must contain exactly three values")
    spacing = tuple(float(value) for value in values)
    if any(not math.isfinite(value) or value <= 0.0 for value in spacing):
        raise RescueGeometryError("spacing_xyz values must be finite and positive")
    return spacing  # type: ignore[return-value]


def validate_confidence(label: str) -> str:
    if label not in CONFIDENCE_LABELS:
        raise RescueGeometryError(f"unsupported confidence label: {label}")
    return label


def initial_spacing_candidate(
    values: Sequence[float | None],
    *,
    fallback: float = 1.0,
) -> tuple[tuple[float, float, float], tuple[bool, bool, bool]]:
    """Return an editable candidate and flags identifying fallback axes."""

    if not math.isfinite(fallback) or fallback <= 0.0:
        raise RescueGeometryError("fallback spacing must be finite and positive")
    if len(values) != 3:
        raise RescueGeometryError("spacing candidate must contain exactly three axes")
    candidate: list[float] = []
    used_fallback: list[bool] = []
    for value in values:
        if value is None:
            candidate.append(float(fallback))
            used_fallback.append(True)
            continue
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0.0:
            candidate.append(float(fallback))
            used_fallback.append(True)
            continue
        candidate.append(numeric)
        used_fallback.append(False)
    return (
        validate_spacing_xyz(candidate),
        (used_fallback[0], used_fallback[1], used_fallback[2]),
    )


@dataclass(frozen=True)
class CropXYZ:
    minimum: tuple[int, int, int]
    maximum_exclusive: tuple[int, int, int]

    def validate_for_shape(self, shape_xyz: Sequence[int]) -> None:
        if len(shape_xyz) != 3:
            raise RescueGeometryError("shape_xyz must contain exactly three values")
        for axis, (lower, upper, size) in enumerate(
            zip(self.minimum, self.maximum_exclusive, shape_xyz, strict=True)
        ):
            if lower < 0 or upper <= lower or upper > int(size):
                raise RescueGeometryError(
                    f"invalid crop for axis {AXIS_NAMES[axis]}: [{lower}, {upper})"
                )


@dataclass(frozen=True)
class RescueGeometryTransform:
    """Canonical transform for arrays whose axes are ordered X, Y, Z."""

    axis_permutation: tuple[int, int, int] = (0, 1, 2)
    rotation_quarter_turns: int = 0
    slice_order_reversed: bool = False
    crop: CropXYZ | None = None

    def __post_init__(self) -> None:
        if sorted(self.axis_permutation) != [0, 1, 2]:
            raise RescueGeometryError("axis_permutation must be a permutation of X, Y, Z")
        object.__setattr__(self, "rotation_quarter_turns", self.rotation_quarter_turns % 4)

    @property
    def axis_names(self) -> tuple[str, str, str]:
        return tuple(AXIS_NAMES[index] for index in self.axis_permutation)  # type: ignore[return-value]

    def shape_spacing(
        self,
        shape_xyz: Sequence[int],
        spacing_xyz: Sequence[float],
    ) -> tuple[tuple[int, int, int], tuple[float, float, float]]:
        if len(shape_xyz) != 3 or any(int(value) <= 0 for value in shape_xyz):
            raise RescueGeometryError("shape_xyz values must be positive")
        spacing = validate_spacing_xyz(spacing_xyz)
        shape = tuple(int(shape_xyz[index]) for index in self.axis_permutation)
        transformed_spacing = tuple(spacing[index] for index in self.axis_permutation)
        if self.rotation_quarter_turns % 2:
            shape = (shape[1], shape[0], shape[2])
            transformed_spacing = (
                transformed_spacing[1],
                transformed_spacing[0],
                transformed_spacing[2],
            )
        if self.crop is not None:
            self.crop.validate_for_shape(shape)
            shape = tuple(
                upper - lower
                for lower, upper in zip(
                    self.crop.minimum,
                    self.crop.maximum_exclusive,
                    strict=True,
                )
            )
        return shape, transformed_spacing

    def apply_volume_xyz(self, volume_xyz: np.ndarray) -> np.ndarray:
        if volume_xyz.ndim != 3:
            raise RescueGeometryError("rescue preview volume must be three-dimensional")
        transformed = np.transpose(volume_xyz, axes=self.axis_permutation)
        if self.rotation_quarter_turns:
            transformed = np.rot90(
                transformed,
                k=self.rotation_quarter_turns,
                axes=(0, 1),
            )
        if self.slice_order_reversed:
            transformed = transformed[:, :, ::-1]
        if self.crop is not None:
            self.crop.validate_for_shape(transformed.shape)
            transformed = transformed[
                self.crop.minimum[0] : self.crop.maximum_exclusive[0],
                self.crop.minimum[1] : self.crop.maximum_exclusive[1],
                self.crop.minimum[2] : self.crop.maximum_exclusive[2],
            ]
        return np.ascontiguousarray(transformed)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "axis_permutation": list(self.axis_names),
            "rotation_quarter_turns": self.rotation_quarter_turns,
            "slice_order_reversed": self.slice_order_reversed,
        }
        if self.crop is not None:
            payload["crop_voxels_xyz"] = {
                "min": list(self.crop.minimum),
                "max_exclusive": list(self.crop.maximum_exclusive),
            }
        else:
            payload["crop_voxels_xyz"] = None
        return payload


def calibrated_axis_spacing(
    *,
    voxel_delta_xyz: Sequence[float],
    known_length_mm: float,
    axis: int,
) -> float:
    if axis not in (0, 1, 2):
        raise RescueGeometryError("calibration axis must be X, Y, or Z")
    if len(voxel_delta_xyz) != 3:
        raise RescueGeometryError("voxel_delta_xyz must contain exactly three values")
    known = float(known_length_mm)
    delta = abs(float(voxel_delta_xyz[axis]))
    if not math.isfinite(known) or known <= 0.0:
        raise RescueGeometryError("known length must be finite and positive")
    if not math.isfinite(delta) or delta <= 0.0:
        raise RescueGeometryError("calibration line must span the selected axis")
    other_delta = sum(
        abs(float(value))
        for index, value in enumerate(voxel_delta_xyz)
        if index != axis
    )
    if other_delta > max(1e-6, 0.176327 * delta):
        raise RescueGeometryError(
            "single-axis calibration line must be within 10 degrees of the selected axis"
        )
    return known / delta


def calibrated_locked_xy_spacing(
    *,
    voxel_delta_xy: Sequence[float],
    known_length_mm: float,
) -> float:
    if len(voxel_delta_xy) != 2:
        raise RescueGeometryError("voxel_delta_xy must contain exactly two values")
    known = float(known_length_mm)
    dx, dy = (float(value) for value in voxel_delta_xy)
    pixel_length = math.hypot(dx, dy)
    if not math.isfinite(known) or known <= 0.0:
        raise RescueGeometryError("known length must be finite and positive")
    if not math.isfinite(pixel_length) or pixel_length <= 0.0:
        raise RescueGeometryError("calibration line must have non-zero length")
    return known / pixel_length


def ordered_content_manifest_sha256(
    ordered_instances: Iterable[tuple[str, str]],
) -> str:
    """Hash an ordered list of safe instance keys and lowercase SHA-256 digests."""

    normalized: list[dict[str, str]] = []
    for key, digest in ordered_instances:
        safe_key = str(key)
        safe_digest = str(digest).lower()
        if len(safe_digest) != 64 or any(
            character not in "0123456789abcdef" for character in safe_digest
        ):
            raise RescueGeometryError("instance content digest must be SHA-256 hex")
        normalized.append({"key": safe_key, "sha256": safe_digest})
    encoded = json.dumps(
        normalized,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def safe_rescue_error(
    *,
    code: str,
    stage: str,
    reason: str,
    tool_version: str,
    source_hash: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "totalsegmentator_wrapper_mac.rescue_error.v1",
        "status": "failed",
        "code": str(code),
        "stage": str(stage),
        "reason": str(reason),
        "tool_version": str(tool_version),
    }
    if source_hash:
        payload["source_hash_prefix"] = str(source_hash)[:12]
    return payload
