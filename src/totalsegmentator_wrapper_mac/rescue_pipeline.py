from __future__ import annotations

import hashlib
import io
import json
import math
import os
import struct
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from totalsegmentator_wrapper_mac.rescue_geometry import (
    AXIS_NAMES,
    SCHEMA_VERSION,
    CropXYZ,
    RescueGeometryError,
    RescueGeometryTransform,
    initial_spacing_candidate,
    validate_spacing_xyz,
)


CONFIRMATION_SCHEMA = "totalsegmentator_wrapper_mac.rescue_confirmation.v1"
VALIDATION_SCHEMA = "totalsegmentator_wrapper_mac.dicom_normalizer.rescue_validation.v2"
PIPELINE_VERSION = "1"

_NIFTI_DTYPES: dict[np.dtype[Any], tuple[int, int]] = {
    np.dtype("uint8"): (2, 8),
    np.dtype("int16"): (4, 16),
    np.dtype("int32"): (8, 32),
    np.dtype("float32"): (16, 32),
    np.dtype("uint16"): (512, 16),
}
_NIFTI_CODES = {code: dtype for dtype, (code, _bits) in _NIFTI_DTYPES.items()}
_SAFE_SERIES_FIELDS = {
    "series_hash",
    "role",
    "plane",
    "reconstruction_group",
    "file_count",
    "frame_count",
    "rows",
    "columns",
}
_SAFE_TAG_FIELDS = {"tag", "name", "value_mm", "consistency", "source"}


class RescuePipelineError(RuntimeError):
    """A safe, user-actionable rescue pipeline failure."""


def load_decoded_volume(path: Path) -> np.ndarray:
    """Load a native-decoded scalar XYZ volume without reading DICOM metadata."""

    try:
        volume = np.load(path, allow_pickle=False)
    except Exception as exc:  # noqa: BLE001
        raise RescuePipelineError("decoded preview volume could not be loaded") from exc
    return validate_decoded_volume(volume)


def load_decoded_reference_image(path: Path) -> np.ndarray:
    try:
        image = np.load(path, allow_pickle=False)
    except Exception as exc:  # noqa: BLE001
        raise RescuePipelineError("decoded reference image could not be loaded") from exc
    image = np.asarray(image)
    if image.ndim == 3:
        image = image[:, :, image.shape[2] // 2]
    if image.ndim != 2 or min(image.shape) <= 0 or not np.issubdtype(image.dtype, np.number):
        raise RescuePipelineError("decoded reference image must be a non-empty scalar array")
    return np.asarray(image, dtype=np.float32)


def write_decoded_volume(path: Path, volume_xyz: np.ndarray) -> None:
    volume = validate_decoded_volume(volume_xyz)
    buffer = io.BytesIO()
    np.save(buffer, volume, allow_pickle=False)
    _atomic_write_bytes(path, buffer.getvalue())


def validate_decoded_volume(volume_xyz: np.ndarray) -> np.ndarray:
    volume = np.asarray(volume_xyz)
    if volume.ndim != 3 or any(int(size) <= 0 for size in volume.shape):
        raise RescuePipelineError("decoded preview volume must be a non-empty XYZ array")
    dtype = volume.dtype.newbyteorder("=")
    if dtype not in _NIFTI_DTYPES:
        raise RescuePipelineError("decoded preview volume scalar type is unsupported")
    return np.ascontiguousarray(volume.astype(dtype, copy=False))


def decoded_volume_sha256(volume_xyz: np.ndarray) -> str:
    volume = validate_decoded_volume(volume_xyz)
    digest = hashlib.sha256()
    digest.update(b"rescue-decoded-volume-v1\0")
    digest.update(volume.dtype.str.encode("ascii"))
    digest.update(b"\0")
    digest.update(json.dumps(list(volume.shape), separators=(",", ":")).encode("ascii"))
    digest.update(b"\0")
    digest.update(volume.tobytes(order="C"))
    return digest.hexdigest()


def create_estimate(
    volume_xyz: np.ndarray,
    *,
    spacing_hints_xyz: Sequence[float | None] = (None, None, None),
    source_manifest_sha256: str | None = None,
    spacing_sources: Sequence[str] = (),
    used_series: Sequence[Mapping[str, Any]] = (),
    used_dicom_tags: Sequence[Mapping[str, Any]] = (),
    registration: Mapping[str, Any] | None = None,
    foreground: Mapping[str, Any] | None = None,
    alternatives: Sequence[Mapping[str, Any]] = (),
    fallback_spacing_mm: float = 1.0,
) -> dict[str, Any]:
    """Create a candidate every time, while keeping weak evidence explicit."""

    volume = validate_decoded_volume(volume_xyz)
    candidate, fallback_axes = initial_spacing_candidate(
        spacing_hints_xyz,
        fallback=fallback_spacing_mm,
    )
    source_hash = _validate_sha256(
        source_manifest_sha256 or decoded_volume_sha256(volume),
        field="source_manifest_sha256",
    )
    per_axis = {
        axis: ("unknown" if fallback else "low")
        for axis, fallback in zip(AXIS_NAMES, fallback_axes, strict=True)
    }
    limitations = [
        f"{axis}_spacing_uses_fallback"
        for axis, fallback in zip(AXIS_NAMES, fallback_axes, strict=True)
        if fallback
    ]
    reasons = [str(value) for value in spacing_sources if str(value)]
    status = "fallback_initial_candidate" if any(fallback_axes) else "estimated"
    overall = "unknown" if any(fallback_axes) else "low"

    return {
        "schema": SCHEMA_VERSION,
        "workflow_status": "estimated",
        "source": {
            "content_manifest_sha256": source_hash,
            "hash_algorithm": "sha256",
            "decoded_volume_sha256": decoded_volume_sha256(volume),
            "shape_xyz": list(volume.shape),
            "dtype": volume.dtype.name,
        },
        "axis_convention": _axis_convention(),
        "estimate": {
            "estimated_spacing_xyz": list(candidate),
            "spacing_source": reasons or ["fallback_initial_candidate"],
            "status": status,
            "confidence": {
                "overall": overall,
                "per_axis": per_axis,
                "score": None,
                "reasons": reasons,
                "limitations": limitations,
                "convergence": None,
                "top2_score_margin": None,
                "cross_series_disagreement_mm": None,
            },
            "alternatives": _safe_alternatives(alternatives),
        },
        "evidence": {
            "used_series": _allowlist_records(used_series, _SAFE_SERIES_FIELDS),
            "used_dicom_tags": _allowlist_records(used_dicom_tags, _SAFE_TAG_FIELDS),
            "registration": _safe_registration(registration),
            "foreground": dict(foreground) if foreground is not None else None,
        },
        "confirmed": None,
        "transform": RescueGeometryTransform().to_dict(),
        "calibrations": [],
        "output_validation": None,
        "algorithm": {
            "normalizer_version": None,
            "estimator_version": PIPELINE_VERSION,
            "configuration_version": "manual-seed-v1",
            "random_seed": 0,
        },
        "warnings": [
            "secondary_capture",
            "geometry_inferred",
            "burned_in_annotation",
            "non_diagnostic_preview",
        ],
    }


def create_preview(
    volume_xyz: np.ndarray,
    *,
    estimated_spacing_xyz: Sequence[float],
    confirmed_spacing_xyz: Sequence[float],
    transform: RescueGeometryTransform,
    source_manifest_sha256: str,
    estimate_metadata: Mapping[str, Any] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Apply the canonical transform for MPR; this function never runs inference."""

    volume = validate_decoded_volume(volume_xyz)
    estimated = validate_spacing_xyz(estimated_spacing_xyz)
    confirmed = validate_spacing_xyz(confirmed_spacing_xyz)
    source_hash = _validate_sha256(source_manifest_sha256, field="source_manifest_sha256")
    transformed = transform.apply_volume_xyz(volume)
    transformed_shape, transformed_spacing = transform.shape_spacing(volume.shape, confirmed)
    if transformed.shape != transformed_shape:
        raise RescuePipelineError("preview transform shape contract failed")
    token = build_confirmation_token(
        source_manifest_sha256=source_hash,
        confirmed_spacing_xyz=confirmed,
        transform=transform,
    )
    metadata = _metadata_for_geometry(
        estimated=estimated,
        confirmed=confirmed,
        source_hash=source_hash,
        source_volume=volume,
        transform=transform,
        workflow_status="preview_ready",
        estimate_metadata=estimate_metadata,
    )
    metadata["preview"] = {
        "shape": list(transformed_shape),
        "spacing_xyz": list(transformed_spacing),
        "inference_started": False,
    }
    metadata["confirmation"] = {
        "schema": CONFIRMATION_SCHEMA,
        "confirmed": False,
        "token": token,
        "binds": ["source_manifest_sha256", "confirmed_spacing_xyz", "transform"],
    }
    # Flat aliases are kept for the app command bridge; canonical data remains nested.
    metadata["inference_started"] = False
    metadata["confirmation_token"] = token
    return transformed, metadata


def build_confirmation_token(
    *,
    source_manifest_sha256: str,
    confirmed_spacing_xyz: Sequence[float],
    transform: RescueGeometryTransform,
) -> str:
    source_hash = _validate_sha256(source_manifest_sha256, field="source_manifest_sha256")
    spacing = validate_spacing_xyz(confirmed_spacing_xyz)
    payload = {
        "schema": CONFIRMATION_SCHEMA,
        "source_manifest_sha256": source_hash,
        "confirmed_spacing_xyz": list(spacing),
        "transform": transform.to_dict(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def finalize_rescue(
    volume_xyz: np.ndarray,
    *,
    output_path: Path,
    estimated_spacing_xyz: Sequence[float],
    confirmed_spacing_xyz: Sequence[float],
    transform: RescueGeometryTransform,
    source_manifest_sha256: str,
    confirmation_token: str,
    estimate_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write and read back a pseudo-NIfTI only after an explicit bound confirmation."""

    volume = validate_decoded_volume(volume_xyz)
    estimated = validate_spacing_xyz(estimated_spacing_xyz)
    confirmed = validate_spacing_xyz(confirmed_spacing_xyz)
    source_hash = _validate_sha256(source_manifest_sha256, field="source_manifest_sha256")
    expected_token = build_confirmation_token(
        source_manifest_sha256=source_hash,
        confirmed_spacing_xyz=confirmed,
        transform=transform,
    )
    if not _constant_time_equal(confirmation_token, expected_token):
        raise RescuePipelineError("confirmation token does not match source and geometry")
    if output_path.exists():
        raise RescuePipelineError("rescue output already exists")

    transformed = transform.apply_volume_xyz(volume)
    expected_shape, output_spacing = transform.shape_spacing(volume.shape, confirmed)
    if transformed.shape != expected_shape:
        raise RescuePipelineError("final transform shape contract failed")
    write_nifti(output_path, transformed, output_spacing)
    try:
        readback, readback_metadata = read_nifti(output_path)
        voxel_equal = np.array_equal(readback, transformed)
        shape_equal = tuple(readback.shape) == tuple(expected_shape)
        spacing_equal = np.allclose(
            readback_metadata["spacing_xyz"],
            output_spacing,
            rtol=0.0,
            atol=1e-6,
        )
        affine_equal = np.allclose(
            np.asarray(readback_metadata["affine"], dtype=float),
            np.diag([*output_spacing, 1.0]),
            rtol=0.0,
            atol=1e-6,
        )
        if not all((voxel_equal, shape_equal, spacing_equal, affine_equal)):
            raise RescuePipelineError("pseudo-NIfTI readback did not match confirmed geometry")
    except Exception:
        output_path.unlink(missing_ok=True)
        raise

    metadata = _metadata_for_geometry(
        estimated=estimated,
        confirmed=confirmed,
        source_hash=source_hash,
        source_volume=volume,
        transform=transform,
        workflow_status="finalized",
        estimate_metadata=estimate_metadata,
    )
    metadata["confirmation"] = {
        "schema": CONFIRMATION_SCHEMA,
        "confirmed": True,
        "token_sha256": hashlib.sha256(confirmation_token.encode("ascii")).hexdigest(),
    }
    metadata["inference_started"] = False
    metadata["output_validation"] = {
        "schema": VALIDATION_SCHEMA,
        "shape": list(expected_shape),
        "spacing_xyz": list(output_spacing),
        "affine": readback_metadata["affine"],
        "affine_consistent": affine_equal,
        "voxel_payload_consistent": voxel_equal,
        "input_hash_matches": decoded_volume_sha256(volume)
        == metadata["source"]["decoded_volume_sha256"],
        "nifti_sha256": _file_sha256(output_path),
    }
    return metadata


def write_nifti(path: Path, volume_xyz: np.ndarray, spacing_xyz: Sequence[float]) -> None:
    """Write a deterministic little-endian single-file NIfTI-1 in rescue-local axes."""

    volume = validate_decoded_volume(volume_xyz)
    spacing = validate_spacing_xyz(spacing_xyz)
    dtype = volume.dtype.newbyteorder("=")
    datatype, bitpix = _NIFTI_DTYPES[dtype]
    header = bytearray(348)
    struct.pack_into("<i", header, 0, 348)
    struct.pack_into("<8h", header, 40, 3, *volume.shape, 1, 1, 1, 1)
    struct.pack_into("<h", header, 70, datatype)
    struct.pack_into("<h", header, 72, bitpix)
    struct.pack_into("<8f", header, 76, 1.0, *spacing, 0.0, 0.0, 0.0, 0.0)
    struct.pack_into("<f", header, 108, 352.0)
    struct.pack_into("<f", header, 112, 1.0)
    struct.pack_into("<B", header, 123, 2)  # millimetres
    struct.pack_into("<h", header, 252, 0)  # qform intentionally unknown
    struct.pack_into("<h", header, 254, 1)  # rescue-local affine
    struct.pack_into("<4f", header, 280, spacing[0], 0.0, 0.0, 0.0)
    struct.pack_into("<4f", header, 296, 0.0, spacing[1], 0.0, 0.0)
    struct.pack_into("<4f", header, 312, 0.0, 0.0, spacing[2], 0.0)
    header[344:348] = b"n+1\0"
    little_endian = volume.astype(dtype.newbyteorder("<"), copy=False)
    payload = bytes(header) + b"\0\0\0\0" + little_endian.tobytes(order="F")
    _atomic_write_bytes(path, payload)


def read_nifti(path: Path) -> tuple[np.ndarray, dict[str, Any]]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise RescuePipelineError("pseudo-NIfTI could not be read") from exc
    if len(payload) < 352 or struct.unpack_from("<i", payload, 0)[0] != 348:
        raise RescuePipelineError("pseudo-NIfTI header is invalid")
    if payload[344:348] != b"n+1\0":
        raise RescuePipelineError("pseudo-NIfTI magic is invalid")
    ndim, *dims = struct.unpack_from("<8h", payload, 40)
    if ndim != 3 or any(value <= 0 for value in dims[:3]):
        raise RescuePipelineError("pseudo-NIfTI shape is invalid")
    datatype = struct.unpack_from("<h", payload, 70)[0]
    dtype = _NIFTI_CODES.get(datatype)
    if dtype is None:
        raise RescuePipelineError("pseudo-NIfTI datatype is unsupported")
    spacing = tuple(float(value) for value in struct.unpack_from("<8f", payload, 76)[1:4])
    try:
        validate_spacing_xyz(spacing)
    except RescueGeometryError as exc:
        raise RescuePipelineError("pseudo-NIfTI spacing is invalid") from exc
    vox_offset = int(struct.unpack_from("<f", payload, 108)[0])
    count = math.prod(dims[:3])
    byte_count = count * dtype.itemsize
    if vox_offset < 352 or len(payload) != vox_offset + byte_count:
        raise RescuePipelineError("pseudo-NIfTI voxel payload length is invalid")
    volume = np.frombuffer(
        payload,
        dtype=dtype.newbyteorder("<"),
        count=count,
        offset=vox_offset,
    ).reshape(tuple(dims[:3]), order="F")
    sform_code = struct.unpack_from("<h", payload, 254)[0]
    if sform_code <= 0:
        raise RescuePipelineError("pseudo-NIfTI local affine is missing")
    affine = [
        list(struct.unpack_from("<4f", payload, 280)),
        list(struct.unpack_from("<4f", payload, 296)),
        list(struct.unpack_from("<4f", payload, 312)),
        [0.0, 0.0, 0.0, 1.0],
    ]
    return np.array(volume, copy=True), {
        "shape": list(dims[:3]),
        "spacing_xyz": list(spacing),
        "affine": affine,
        "qform_code": struct.unpack_from("<h", payload, 252)[0],
        "sform_code": sform_code,
        "coordinate_frame": "rescue_local",
    }


def write_metadata(path: Path, metadata: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(metadata, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(path, encoded)


def transform_from_mapping(payload: Mapping[str, Any] | None) -> RescueGeometryTransform:
    value = payload or {}
    raw_permutation = value.get("axis_permutation", ["x", "y", "z"])
    if not isinstance(raw_permutation, Sequence) or isinstance(raw_permutation, (str, bytes)):
        raise RescuePipelineError("axis_permutation must contain X, Y, Z")
    try:
        permutation = tuple(
            AXIS_NAMES.index(str(axis).lower()) if not isinstance(axis, int) else axis
            for axis in raw_permutation
        )
    except ValueError as exc:
        raise RescuePipelineError("axis_permutation contains an unknown axis") from exc
    crop_payload = value.get("crop_voxels_xyz")
    crop = None
    if crop_payload is not None:
        if not isinstance(crop_payload, Mapping):
            raise RescuePipelineError("crop_voxels_xyz must be an object")
        try:
            crop = CropXYZ(
                tuple(int(item) for item in crop_payload["min"]),
                tuple(int(item) for item in crop_payload["max_exclusive"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RescuePipelineError("crop_voxels_xyz is invalid") from exc
    try:
        return RescueGeometryTransform(
            axis_permutation=permutation,  # type: ignore[arg-type]
            rotation_quarter_turns=int(value.get("rotation_quarter_turns", 0)),
            slice_order_reversed=bool(value.get("slice_order_reversed", False)),
            crop=crop,
        )
    except (RescueGeometryError, TypeError, ValueError) as exc:
        raise RescuePipelineError("rescue transform is invalid") from exc


def geometry_values_from_mapping(
    payload: Mapping[str, Any],
) -> tuple[tuple[float, float, float], tuple[float, float, float], str, RescueGeometryTransform]:
    try:
        confirmed_payload = payload.get("confirmed")
        estimate_payload = payload.get("estimate")
        if isinstance(confirmed_payload, Mapping):
            confirmed = validate_spacing_xyz(confirmed_payload["confirmed_spacing_xyz"])
        elif isinstance(estimate_payload, Mapping):
            confirmed = validate_spacing_xyz(estimate_payload["estimated_spacing_xyz"])
        else:
            raise RescuePipelineError(
                "rescue geometry requires estimated or confirmed spacing"
            )
        if isinstance(estimate_payload, Mapping):
            estimated = validate_spacing_xyz(estimate_payload["estimated_spacing_xyz"])
        else:
            estimated = confirmed
        source_hash = _validate_sha256(
            payload["source"]["content_manifest_sha256"],
            field="source_manifest_sha256",
        )
        transform = transform_from_mapping(payload.get("transform"))
    except (KeyError, TypeError, RescueGeometryError) as exc:
        raise RescuePipelineError("rescue geometry JSON is incomplete or invalid") from exc
    return estimated, confirmed, source_hash, transform


def _metadata_for_geometry(
    *,
    estimated: tuple[float, float, float],
    confirmed: tuple[float, float, float],
    source_hash: str,
    source_volume: np.ndarray,
    transform: RescueGeometryTransform,
    workflow_status: str,
    estimate_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if estimate_metadata is None:
        metadata = create_estimate(
            source_volume,
            spacing_hints_xyz=estimated,
            source_manifest_sha256=source_hash,
            spacing_sources=("manual_or_supplied_candidate",),
        )
    else:
        incoming = json.loads(json.dumps(dict(estimate_metadata)))
        if incoming.get("schema") != SCHEMA_VERSION:
            raise RescuePipelineError("estimate metadata schema is unsupported")
        if incoming.get("source", {}).get("content_manifest_sha256") != source_hash:
            raise RescuePipelineError("estimate metadata source hash does not match")
        incoming_decoded_hash = incoming.get("source", {}).get("decoded_volume_sha256")
        if incoming_decoded_hash is not None and incoming_decoded_hash != decoded_volume_sha256(
            source_volume
        ):
            raise RescuePipelineError("decoded preview volume changed after estimation")
        incoming_estimate = incoming.get("estimate")
        incoming_evidence = incoming.get("evidence")
        metadata = create_estimate(
            source_volume,
            spacing_hints_xyz=estimated,
            source_manifest_sha256=source_hash,
            spacing_sources=_safe_spacing_sources(incoming_estimate),
            used_series=(
                incoming_evidence.get("used_series", ())
                if isinstance(incoming_evidence, Mapping)
                else ()
            ),
            used_dicom_tags=(
                incoming_evidence.get("used_dicom_tags", ())
                if isinstance(incoming_evidence, Mapping)
                else ()
            ),
            registration=(
                incoming_evidence.get("registration")
                if isinstance(incoming_evidence, Mapping)
                else None
            ),
            foreground=(
                _safe_foreground(incoming_evidence.get("foreground"))
                if isinstance(incoming_evidence, Mapping)
                else None
            ),
            alternatives=(
                incoming_estimate.get("alternatives", ())
                if isinstance(incoming_estimate, Mapping)
                else ()
            ),
        )
        metadata["calibrations"] = _safe_calibrations(incoming.get("calibrations"))
        if isinstance(incoming.get("algorithm"), Mapping):
            for key in (
                "normalizer_version",
                "estimator_version",
                "configuration_version",
                "random_seed",
            ):
                value = incoming["algorithm"].get(key)
                if value is None or isinstance(value, (str, int, float, bool)):
                    metadata["algorithm"][key] = value
    metadata["workflow_status"] = workflow_status
    metadata["estimate"]["estimated_spacing_xyz"] = list(estimated)
    changed_axes = [
        axis
        for axis, estimated_value, confirmed_value in zip(
            AXIS_NAMES,
            estimated,
            confirmed,
            strict=True,
        )
        if not math.isclose(estimated_value, confirmed_value, rel_tol=0.0, abs_tol=1e-9)
    ]
    transform_payload = transform.to_dict()
    transform_changed = transform_payload != RescueGeometryTransform().to_dict()
    metadata["confirmed"] = {
        "confirmed_spacing_xyz": list(confirmed),
        "manual_changed": bool(changed_axes) or transform_changed,
        "changed_axes": changed_axes,
        "manual_changes": {
            "spacing": bool(changed_axes),
            "axis_permutation": transform.axis_permutation != (0, 1, 2),
            "rotation": transform.rotation_quarter_turns != 0,
            "slice_order_reversed": transform.slice_order_reversed,
            "crop": transform.crop is not None,
            "calibration": bool(metadata.get("calibrations")),
        },
    }
    metadata["transform"] = transform_payload
    metadata["output_validation"] = None
    if any(value < 0.01 or value > 20.0 for value in confirmed):
        metadata.setdefault("warnings", []).append("extreme_confirmed_spacing")
    return metadata


def _axis_convention() -> dict[str, Any]:
    return {
        "dicom_pixel_spacing_order": ["row", "column"],
        "estimated_spacing_order": ["x", "y", "z"],
        "initial_mapping": {"x": "column", "y": "row", "z": "slice"},
        "coordinate_frame": "rescue_local",
    }


def _allowlist_records(
    records: Sequence[Mapping[str, Any]],
    allowed_fields: set[str],
) -> list[dict[str, Any]]:
    return [
        {key: record[key] for key in sorted(allowed_fields) if key in record}
        for record in records
    ]


def _safe_registration(registration: Mapping[str, Any] | None) -> dict[str, Any]:
    allowed = {
        "metric",
        "converged",
        "residual",
        "top2_score_margin",
        "cross_series_disagreement_mm",
        "ambiguous",
    }
    if registration is None:
        return {
            "metric": None,
            "converged": False,
            "residual": None,
            "top2_score_margin": None,
            "cross_series_disagreement_mm": None,
            "ambiguous": False,
        }
    return {key: registration[key] for key in sorted(allowed) if key in registration}


def _safe_alternatives(alternatives: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for alternative in alternatives:
        if "spacing_xyz" not in alternative:
            continue
        spacing = validate_spacing_xyz(alternative["spacing_xyz"])
        item: dict[str, Any] = {"spacing_xyz": list(spacing)}
        score = alternative.get("score")
        if score is not None and math.isfinite(float(score)):
            item["score"] = float(score)
        safe.append(item)
    return safe


def _safe_spacing_sources(estimate: Any) -> list[str]:
    if not isinstance(estimate, Mapping):
        return ["manual_or_supplied_candidate"]
    sources = estimate.get("spacing_source")
    if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)):
        return ["manual_or_supplied_candidate"]
    safe = [
        str(value)
        for value in sources
        if isinstance(value, str)
        and 0 < len(value) <= 64
        and all(character.isalnum() or character in {"_", "-", "."} for character in value)
    ]
    return safe or ["manual_or_supplied_candidate"]


def _safe_foreground(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    bbox = value.get("bbox")
    if isinstance(bbox, Mapping):
        try:
            result["bbox"] = {
                "min": [int(item) for item in bbox["min"]],
                "max_exclusive": [int(item) for item in bbox["max_exclusive"]],
            }
        except (KeyError, TypeError, ValueError):
            pass
    for key in ("foreground_fraction", "background_value", "border_foreground_coverage"):
        scalar = value.get(key)
        if isinstance(scalar, (int, float)) and math.isfinite(float(scalar)):
            result[key] = float(scalar)
    if isinstance(value.get("detected"), bool):
        result["detected"] = value["detected"]
    warnings = value.get("warnings")
    if isinstance(warnings, Sequence) and not isinstance(warnings, (str, bytes)):
        result["warnings"] = [
            item
            for item in warnings
            if isinstance(item, str)
            and 0 < len(item) <= 64
            and all(character.isalnum() or character in {"_", "-", "."} for character in item)
        ]
    return result or None


def _safe_calibrations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            safe: dict[str, Any] = {}
            for key in ("plane", "method"):
                token = item.get(key)
                if (
                    isinstance(token, str)
                    and 0 < len(token) <= 32
                    and all(
                        character.isalnum() or character in {"_", "-", "."}
                        for character in token
                    )
                ):
                    safe[key] = token
            for key in ("known_length_mm", "residual_mm"):
                scalar = item.get(key)
                if isinstance(scalar, (int, float)) and math.isfinite(float(scalar)):
                    safe[key] = float(scalar)
            points = item.get("voxel_points_xyz")
            if isinstance(points, Sequence) and not isinstance(points, (str, bytes)):
                try:
                    safe["voxel_points_xyz"] = [
                        [float(coordinate) for coordinate in point] for point in points
                    ]
                except (TypeError, ValueError):
                    pass
            axes = item.get("updated_axes")
            if isinstance(axes, Sequence) and not isinstance(axes, (str, bytes)):
                safe["updated_axes"] = [
                    axis for axis in axes if axis in {"x", "y", "z"}
                ]
            result.append(safe)
    return result


def _validate_sha256(value: str, *, field: str) -> str:
    normalized = str(value).lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise RescuePipelineError(f"{field} must be a SHA-256 hex digest")
    return normalized


def _constant_time_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(str(left), str(right))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
