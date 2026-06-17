from __future__ import annotations

import json
import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


ORIGINAL_CT_GEOMETRY_OK = "original_ct_geometry_ok"
SECONDARY_CAPTURE_RESCUE_CANDIDATE = "secondary_capture_rescue_candidate"
REJECT = "reject"

SECONDARY_CAPTURE_UID_PREFIX = "1.2.840.10008.5.1.4.1.1.7"
MIN_VOLUME_SLICES = 32


@dataclass(frozen=True)
class SeriesClassification:
    status: str
    grade: str
    reasons: list[str]
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_series(summary: dict[str, Any]) -> SeriesClassification:
    """Classify a DICOM series using metadata only.

    This intentionally does not normalize DICOM, infer missing geometry, or
    inspect pixel data. It is an intake gate for the packaged preview workflow.
    """
    reasons: list[str] = []
    modality = _upper(summary.get("modality"))
    description = _upper(summary.get("series_description"))
    image_type = [_upper(value) for value in summary.get("image_type") or []]
    sop_uid = str(summary.get("sop_class_uid") or "")
    sop_name = _upper(summary.get("sop_class_name"))
    file_count = int(summary.get("file_count") or 0)
    shape_consistent = bool(summary.get("shape_consistent"))
    has_pixel_spacing = bool(summary.get("has_pixel_spacing"))
    position_count = int(summary.get("image_position_patient_count") or 0)
    orientation_count = int(summary.get("image_orientation_patient_count") or 0)

    if _looks_like_dose_report(description, image_type, sop_name):
        return SeriesClassification(
            status=REJECT,
            grade="reject",
            reasons=["dose_report_or_structured_report"],
            recommendation="Do not use for volume segmentation.",
        )

    if _looks_like_scout(description, image_type):
        return SeriesClassification(
            status=REJECT,
            grade="reject",
            reasons=["scout_or_localizer"],
            recommendation="Do not use for dental volume segmentation.",
        )

    if file_count < MIN_VOLUME_SLICES:
        reasons.append(f"too_few_slices:{file_count}")
    if not shape_consistent:
        reasons.append("mixed_rows_or_columns")

    is_secondary_capture = (
        sop_uid.startswith(SECONDARY_CAPTURE_UID_PREFIX)
        or "SECONDARY CAPTURE" in sop_name
        or "SECONDARY" in image_type
        or "SCREEN SAVE" in image_type
    )
    is_axial_like = "AXIAL" in description or "AXIAL" in image_type

    if is_secondary_capture:
        if (
            file_count >= MIN_VOLUME_SLICES
            and shape_consistent
            and is_axial_like
            and not _looks_like_non_axial_mpr(description)
        ):
            return SeriesClassification(
                status=SECONDARY_CAPTURE_RESCUE_CANDIDATE,
                grade="C: rescue only",
                reasons=[
                    "secondary_capture",
                    "geometry_not_trusted",
                    "manual_spacing_required",
                    "not_segmentation_grade_original_ct",
                ],
                recommendation=(
                    "Do not feed directly into the packaged preview. If necessary, "
                    "document a manual pseudo-volume rescue outside this package."
                ),
            )
        secondary_reasons = ["secondary_capture_not_axial_volume_candidate"]
        secondary_reasons.extend(reasons)
        return SeriesClassification(
            status=REJECT,
            grade="reject",
            reasons=secondary_reasons,
            recommendation="Reject. Secondary-capture rescue is outside the packaged workflow.",
        )

    is_ct = modality == "CT"
    image_type_original_or_unspecified = "ORIGINAL" in image_type or not image_type
    geometry_ok = (
        has_pixel_spacing
        and position_count == file_count
        and orientation_count == file_count
        and file_count >= MIN_VOLUME_SLICES
        and shape_consistent
    )

    if is_ct and geometry_ok:
        clean_reasons = ["ct_geometry_tags_present"]
        if not image_type_original_or_unspecified:
            clean_reasons.append("image_type_not_original_but_geometry_complete")
        return SeriesClassification(
            status=ORIGINAL_CT_GEOMETRY_OK,
            grade="A: clean CT geometry",
            reasons=clean_reasons,
            recommendation=(
                "Convert with dcm2niix, review MPR/FOV, then run the NIfTI path. "
                "Use robust craniofacial preflight for high-resolution CBCT."
            ),
        )

    if modality == "CT":
        if not has_pixel_spacing:
            reasons.append("missing_pixel_spacing")
        if position_count != file_count:
            reasons.append("missing_or_incomplete_image_position_patient")
        if orientation_count != file_count:
            reasons.append("missing_or_incomplete_image_orientation_patient")
        return SeriesClassification(
            status=REJECT,
            grade="reject",
            reasons=reasons or ["ct_geometry_incomplete"],
            recommendation=(
                "Do not run directly. Prefer a fresh original axial CT export or "
                "handle in a separate DICOM normalizer project."
            ),
        )

    return SeriesClassification(
        status=REJECT,
        grade="reject",
        reasons=reasons or [f"unsupported_modality:{modality or 'unknown'}"],
        recommendation="Unsupported for the packaged preview intake path.",
    )


def audit_dicom_directory(dicom_dir: Path) -> dict[str, Any]:
    try:
        import pydicom
    except ImportError as exc:
        raise RuntimeError(
            "pydicom is required for dicom-audit. Install with "
            "`pip install totalsegmentator-wrapper-mac[dicom]`."
        ) from exc

    dicom_dir = dicom_dir.resolve()
    if not dicom_dir.exists():
        raise FileNotFoundError(f"DICOM directory not found: {dicom_dir}")
    if not dicom_dir.is_dir():
        raise NotADirectoryError(f"Not a directory: {dicom_dir}")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped = 0
    for path in sorted(item for item in dicom_dir.rglob("*") if item.is_file()):
        try:
            ds = pydicom.dcmread(str(path), stop_before_pixels=True, force=True)
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        if not hasattr(ds, "SOPClassUID") and not hasattr(ds, "SeriesInstanceUID"):
            skipped += 1
            continue
        grouped[_series_key(ds)].append(_extract_file_meta(ds))

    series = []
    for key, items in sorted(grouped.items(), key=lambda pair: _series_sort_key(pair[1])):
        summary = summarize_series(key, items)
        summary["classification"] = classify_series(summary).to_dict()
        series.append(summary)

    counts = Counter(item["classification"]["status"] for item in series)
    return {
        "schema": "totalsegmentator_wrapper_mac.dicom_audit.v1",
        "dicom_dir": {
            "basename": dicom_dir.name,
            "path_hash": hashlib.sha256(str(dicom_dir).encode("utf-8")).hexdigest(),
        },
        "file_count": sum(item["file_count"] for item in series),
        "skipped_file_count": skipped,
        "series_count": len(series),
        "classification_counts": dict(sorted(counts.items())),
        "series": series,
        "product_boundary": {
            "dicom_normalizer": False,
            "pixel_data_inspected": False,
            "secondary_capture_rescue_implemented": False,
            "segmentation_auto_run": False,
        },
    }


def summarize_series(key: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        raise ValueError("Cannot summarize an empty series")
    first = items[0]
    shapes = {(item.get("rows"), item.get("columns")) for item in items}
    image_types = _most_common_list(items, "image_type")
    pixel_spacing_count = sum(1 for item in items if item.get("pixel_spacing"))
    position_count = sum(1 for item in items if item.get("image_position_patient"))
    orientation_count = sum(1 for item in items if item.get("image_orientation_patient"))
    return {
        "series_key": key,
        "series_instance_uid": first.get("series_instance_uid"),
        "series_number": first.get("series_number"),
        "series_description": first.get("series_description"),
        "modality": first.get("modality"),
        "sop_class_uid": first.get("sop_class_uid"),
        "sop_class_name": first.get("sop_class_name"),
        "image_type": image_types,
        "file_count": len(items),
        "rows": first.get("rows"),
        "columns": first.get("columns"),
        "shape_consistent": len(shapes) == 1,
        "has_pixel_spacing": pixel_spacing_count == len(items),
        "pixel_spacing_count": pixel_spacing_count,
        "image_position_patient_count": position_count,
        "image_orientation_patient_count": orientation_count,
        "burned_in_annotation_counts": dict(
            sorted(Counter(item.get("burned_in_annotation") or "unknown" for item in items).items())
        ),
        "slice_thickness_values": _unique_limited(items, "slice_thickness"),
    }


def write_audit_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _extract_file_meta(ds: Any) -> dict[str, Any]:
    sop_uid = str(getattr(ds, "SOPClassUID", "") or "")
    return {
        "series_instance_uid": str(getattr(ds, "SeriesInstanceUID", "") or ""),
        "series_number": _optional_int(getattr(ds, "SeriesNumber", None)),
        "series_description": _optional_str(getattr(ds, "SeriesDescription", None)),
        "modality": _optional_str(getattr(ds, "Modality", None)),
        "sop_class_uid": sop_uid,
        "sop_class_name": _sop_name(ds, sop_uid),
        "image_type": _dicom_list(getattr(ds, "ImageType", None)),
        "rows": _optional_int(getattr(ds, "Rows", None)),
        "columns": _optional_int(getattr(ds, "Columns", None)),
        "pixel_spacing": _dicom_list(getattr(ds, "PixelSpacing", None)),
        "image_position_patient": _dicom_list(getattr(ds, "ImagePositionPatient", None)),
        "image_orientation_patient": _dicom_list(getattr(ds, "ImageOrientationPatient", None)),
        "slice_thickness": _optional_str(getattr(ds, "SliceThickness", None)),
        "burned_in_annotation": _optional_str(getattr(ds, "BurnedInAnnotation", None)),
    }


def _series_key(ds: Any) -> str:
    uid = str(getattr(ds, "SeriesInstanceUID", "") or "")
    if uid:
        return uid
    number = str(getattr(ds, "SeriesNumber", "") or "unknown")
    description = str(getattr(ds, "SeriesDescription", "") or "")
    return f"series-number:{number}:{description}"


def _series_sort_key(items: list[dict[str, Any]]) -> tuple[int, str]:
    first = items[0]
    number = first.get("series_number")
    return (999999 if number is None else int(number), str(first.get("series_description") or ""))


def _most_common_list(items: list[dict[str, Any]], key: str) -> list[str]:
    counter: Counter[tuple[str, ...]] = Counter(tuple(item.get(key) or []) for item in items)
    if not counter:
        return []
    return list(counter.most_common(1)[0][0])


def _unique_limited(items: list[dict[str, Any]], key: str, limit: int = 8) -> list[str]:
    values = sorted({str(item[key]) for item in items if item.get(key) is not None})
    return values[:limit]


def _looks_like_dose_report(description: str, image_type: list[str], sop_name: str) -> bool:
    haystack = " ".join([description, sop_name, *image_type])
    return "DOSE" in haystack or "REPORT" in haystack or "SR DOCUMENT" in haystack


def _looks_like_scout(description: str, image_type: list[str]) -> bool:
    haystack = " ".join([description, *image_type])
    return "SCOUT" in haystack or "LOCALIZER" in haystack or "TOPOGRAM" in haystack


def _looks_like_non_axial_mpr(description: str) -> bool:
    return "CORONAL" in description or "SAGITTAL" in description


def _sop_name(ds: Any, sop_uid: str) -> str | None:
    if hasattr(ds, "SOPClassUID"):
        try:
            name = getattr(ds.SOPClassUID, "name", None)
            if name:
                return str(name)
        except Exception:  # noqa: BLE001
            pass
    if sop_uid.startswith(SECONDARY_CAPTURE_UID_PREFIX):
        return "Secondary Capture Image Storage"
    return None


def _dicom_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(item) for item in value]
    except TypeError:
        return [str(value)]


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _upper(value: Any) -> str:
    return str(value or "").upper()
