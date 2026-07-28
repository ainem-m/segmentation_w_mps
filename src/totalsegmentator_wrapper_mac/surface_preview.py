from __future__ import annotations

import hashlib
import json
import re
import resource
import struct
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import nibabel as nib
import numpy as np
from skimage import measure


SMOOTH_PRESETS: dict[str, dict[str, float | int]] = {
    "none": {"iterations": 0, "lambda_value": 0.0, "mu": 0.0},
    "slicer_like": {"iterations": 10, "lambda_value": 0.5, "mu": -0.53},
    "medium": {"iterations": 20, "lambda_value": 0.5, "mu": -0.53},
    "strong": {"iterations": 30, "lambda_value": 0.5, "mu": -0.53},
}

GROUP_COLORS = {
    "dental_hard_tissue": "#f5f1dc",
    "jaws": "#d6a455",
    "pulp": "#d24b5a",
    "all_nonzero": "#b6d7f0",
}

PREVIEW_STEP_SIZE_WARNING_THRESHOLD = 4
PREVIEW_STEP_SIZE_WARNING = "small structures may be under-sampled"
VIEWER_BUNDLE_FILENAME = "viewer_bundle.js"
PERFORMANCE_PROFILE_FILENAME = "performance_profile.json"
STL_PERFORMANCE_PROFILE_FILENAME = "stl_performance_profile.json"
STL_GENERATION_LOG_FILENAME = "stl_generation.log"
STL_WRITE_CHUNK_TRIANGLES = 100_000
STL_TRIANGLE_DTYPE = np.dtype(
    [
        ("normal", "<f4", (3,)),
        ("vertices", "<f4", (3, 3)),
        ("attribute", "<u2"),
    ],
    align=False,
)

CRANIOFACIAL_SURFACE_LABELS: tuple[tuple[str, int, str], ...] = (
    ("mandible.nii.gz", 1, "lower_jawbone"),
    ("skull.nii.gz", 2, "upper_jawbone"),
    ("teeth_lower.nii.gz", 11, "lower_teeth"),
    ("teeth_upper.nii.gz", 12, "upper_teeth"),
)


@dataclass(frozen=True)
class SmoothingConfig:
    preset: str
    iterations: int
    lambda_value: float
    mu: float


class _StageProfiler:
    def __init__(self) -> None:
        self._started = time.perf_counter()
        self._events: list[dict[str, Any]] = []
        self._milestones: dict[str, float] = {}

    @contextmanager
    def measure(self, stage: str, **details: Any) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self._events.append(
                {
                    "stage": stage,
                    "seconds": time.perf_counter() - started,
                    **details,
                }
            )

    def milestone(self, name: str) -> None:
        self._milestones[name] = time.perf_counter() - self._started

    def snapshot(self) -> dict[str, Any]:
        stages: dict[str, float] = {}
        for event in self._events:
            stage = str(event["stage"])
            stages[stage] = stages.get(stage, 0.0) + float(event["seconds"])
        return {
            "total_seconds": time.perf_counter() - self._started,
            "max_rss_bytes": _max_rss_bytes(),
            "stages_seconds": stages,
            "milestones_seconds": dict(self._milestones),
            "events": list(self._events),
        }


def smoothing_config_from_options(
    *,
    preset: str,
    iterations: int | None = None,
    lambda_value: float | None = None,
    mu: float | None = None,
) -> SmoothingConfig:
    if preset not in SMOOTH_PRESETS:
        raise ValueError(f"Unknown smoothing preset: {preset}")
    defaults = SMOOTH_PRESETS[preset]
    return SmoothingConfig(
        preset=preset,
        iterations=int(defaults["iterations"] if iterations is None else iterations),
        lambda_value=float(defaults["lambda_value"] if lambda_value is None else lambda_value),
        mu=float(defaults["mu"] if mu is None else mu),
    )


def run_surface_preview(
    *,
    case_dir: Path,
    input_path: Path | None = None,
    output_dir: Path | None = None,
    min_voxels: int = 1,
    preview_step_size: int = 2,
    smoothing: SmoothingConfig | None = None,
    detailed_stl: bool = True,
) -> dict[str, Any]:
    profiler = _StageProfiler()
    if preview_step_size < 1:
        raise ValueError("preview_step_size must be >= 1")
    case_dir = case_dir.resolve()
    input_path, source_info = resolve_surface_preview_input(
        case_dir=case_dir,
        input_path=input_path,
    )
    output_dir = output_dir or case_dir / "surface_preview"
    output_dir.mkdir(parents=True, exist_ok=True)
    smoothing = smoothing or smoothing_config_from_options(preset="slicer_like")
    summary, image, data = _scan_preview_labelmap(
        input_path=input_path,
        output_dir=output_dir,
        min_voxels=min_voxels,
        smoothing=smoothing,
        profiler=profiler,
    )
    html_path = output_dir / "index.html"
    viewer_base_smoothing = smoothing_config_from_options(preset="none")
    with profiler.measure("browser_mesh_generation"):
        preview_meshes = _build_preview_meshes(
            input_path=input_path,
            summary=summary,
            smoothing=viewer_base_smoothing,
            step_size=preview_step_size,
            profiler=profiler,
            image=image,
            data=data,
        )
    with profiler.measure("json_html_write", artifact="offline_viewer"):
        viewer_bundle_path = _write_offline_viewer(
            html_path,
            summary=summary,
            preview_meshes=preview_meshes,
        )
    profiler.milestone("preview_ready")
    summary["html_viewer"] = str(html_path.resolve())
    summary["preview"] = {
        "step_size": preview_step_size,
        "meshes": [
            {
                "name": mesh["name"],
                "labels": mesh["labels"],
                "vertices": len(mesh["vertices"]),
                "triangles": len(mesh["faces"]),
                "default_visible": mesh["defaultVisible"],
                "opacity": mesh["opacity"],
                "color": mesh["color"],
            }
            for mesh in preview_meshes
        ],
    }
    if preview_step_size > PREVIEW_STEP_SIZE_WARNING_THRESHOLD:
        summary["preview"]["warning"] = PREVIEW_STEP_SIZE_WARNING
    summary["source"] = source_info
    summary["viewer"] = {
        "renderer": "webgl",
        "fallback_renderer": "canvas2d",
        "camera_mode_default": "trackpad",
        "display_mode_default": "xray",
        "display_modes": ["normal", "wireframe", "xray"],
        "transparent_rendering": "jaw_depth_prepass_front_shell",
        "runtime_smoothing": True,
        "runtime_smoothing_presets": list(SMOOTH_PRESETS.keys()),
        "material_default": "rich",
        "material_presets": ["standard", "rich", "realistic", "neutral", "high_contrast"],
        "xray": {
            "surface_color": [1.0, 1.0, 1.0],
            "base_alpha": 0.18,
            "rim_power": 2.0,
            "alpha_max": 0.62,
            "compositing": "back_then_front",
            "target_strategy": "translucent_layers_else_all",
            "blend": "src_alpha_one_minus_src_alpha",
            "depth_test": True,
            "depth_write": False,
            "outline_alpha": 0.58,
            "outline_radius_physical_pixels": 1,
            "background": "#313432",
        },
        "script_bundle": str(viewer_bundle_path.resolve()),
    }
    profile_path = output_dir / PERFORMANCE_PROFILE_FILENAME
    summary["performance_profile"] = str(profile_path.resolve())
    summary["stl_generation"] = {
        "status": "running" if detailed_stl else "pending",
    }
    with profiler.measure("json_html_write", artifact="preview_summary"):
        _write_json_atomic(output_dir / "preview_summary.json", summary)
    profiler.milestone("preview_outputs_complete")
    if not detailed_stl:
        profile = profiler.snapshot()
        _write_json_atomic(profile_path, profile)
        summary["profile"] = profile
        return summary

    detailed_summary = export_labelmap_surfaces(
        input_path=input_path,
        output_dir=output_dir,
        min_voxels=min_voxels,
        combined=True,
        smoothing=smoothing,
        suffix="_smooth",
        summary_filename=None,
        readme_filename="README_SURFACE_PREVIEW.md",
        profiler=profiler,
    )
    detailed_summary.update(
        {
            "html_viewer": summary["html_viewer"],
            "preview": summary["preview"],
            "source": summary["source"],
            "viewer": summary["viewer"],
            "performance_profile": summary["performance_profile"],
            "stl_generation": {"status": "complete"},
        }
    )
    summary = detailed_summary
    with profiler.measure("json_html_write", artifact="final_summary"):
        _write_json_atomic(output_dir / "preview_summary.json", summary)
    profiler.milestone("all_stl_complete")
    profiler.milestone("all_outputs_complete")
    profile = profiler.snapshot()
    _write_json_atomic(profile_path, profile)
    summary["profile"] = profile
    return summary


def _scan_preview_labelmap(
    *,
    input_path: Path,
    output_dir: Path,
    min_voxels: int,
    smoothing: SmoothingConfig,
    profiler: _StageProfiler,
) -> tuple[dict[str, Any], nib.Nifti1Image, np.ndarray]:
    with profiler.measure("nifti_load", purpose="browser_preview"):
        image = nib.load(str(input_path))
        data = np.asanyarray(image.dataobj)
    label_names = label_name_map(input_path)
    with profiler.measure("label_scan_and_mask", operation="unique_labels"):
        label_values, counts = np.unique(data, return_counts=True)
    label_entries = [
        {
            "label": int(value),
            "name": label_names.get(int(value), f"label_{int(value)}"),
            "voxels": int(count),
        }
        for value, count in zip(label_values, counts, strict=True)
        if int(value) != 0 and int(count) >= min_voxels
    ]
    groups = [
        {"name": name, "labels": labels}
        for name, labels in group_specs(label_entries).items()
        if labels
    ]
    return (
        {
            "input": str(input_path.resolve()),
            "output_dir": str(output_dir.resolve()),
            "source_shape": [int(value) for value in image.shape[:3]],
            "source_spacing": [float(value) for value in image.header.get_zooms()[:3]],
            "label_count": len(label_entries),
            "labels": label_entries,
            "groups": groups,
            "smoothing": _smoothing_to_dict(smoothing),
        },
        image,
        data,
    )


def run_surface_preview_stl_only(
    *,
    case_dir: Path,
    input_path: Path | None = None,
    output_dir: Path | None = None,
    min_voxels: int = 1,
    smoothing: SmoothingConfig | None = None,
) -> dict[str, Any]:
    profiler = _StageProfiler()
    case_dir = case_dir.resolve()
    input_path, _source_info = resolve_surface_preview_input(
        case_dir=case_dir,
        input_path=input_path,
    )
    output_dir = (output_dir or case_dir / "surface_preview").resolve()
    summary_path = output_dir / "preview_summary.json"
    html_path = output_dir / "index.html"
    bundle_path = output_dir / VIEWER_BUNDLE_FILENAME
    if not summary_path.exists() or not html_path.exists() or not bundle_path.exists():
        raise RuntimeError("Browser preview must be complete before deferred STL generation")
    preview_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if Path(preview_summary.get("input", "")).resolve() != input_path:
        raise RuntimeError("Deferred STL input does not match the browser preview input")
    smoothing = smoothing or smoothing_config_from_options(preset="slicer_like")
    if preview_summary.get("smoothing") != _smoothing_to_dict(smoothing):
        raise RuntimeError("Deferred STL smoothing does not match the browser preview")
    detailed_summary = export_labelmap_surfaces(
        input_path=input_path,
        output_dir=output_dir,
        min_voxels=min_voxels,
        combined=True,
        smoothing=smoothing,
        suffix="_smooth",
        summary_filename=None,
        readme_filename="README_SURFACE_PREVIEW.md",
        profiler=profiler,
    )
    for key in (
        "html_viewer",
        "preview",
        "source",
        "viewer",
        "performance_profile",
    ):
        detailed_summary[key] = preview_summary[key]
    stl_profile_path = output_dir / STL_PERFORMANCE_PROFILE_FILENAME
    detailed_summary["stl_performance_profile"] = str(stl_profile_path)
    detailed_summary["stl_generation"] = {"status": "complete"}
    with profiler.measure("json_html_write", artifact="final_summary"):
        _write_json_atomic(summary_path, detailed_summary)
    profiler.milestone("all_stl_complete")
    profiler.milestone("all_outputs_complete")
    profile = profiler.snapshot()
    _write_json_atomic(stl_profile_path, profile)
    detailed_summary["profile"] = profile
    return detailed_summary


def mark_surface_preview_stl_status(
    *,
    output_dir: Path,
    status: str,
    error_type: str | None = None,
) -> None:
    if status not in {"pending", "running", "complete", "failed"}:
        raise ValueError(f"Unknown STL generation status: {status}")
    summary_path = output_dir / "preview_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    state: dict[str, Any] = {"status": status}
    if error_type is not None:
        state["error_type"] = error_type
    summary["stl_generation"] = state
    _write_json_atomic(summary_path, summary)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def resolve_surface_preview_input(
    *,
    case_dir: Path,
    input_path: Path | None,
) -> tuple[Path, dict[str, Any]]:
    if input_path is not None:
        resolved = input_path.resolve()
        return resolved, {"source": "explicit_input", "input": str(resolved)}

    toothseg_fullspace = (
        case_dir
        / "segmentations"
        / "toothseg"
        / "toothseg_fdi_multilabel.nii.gz"
    )
    if toothseg_fullspace.exists():
        return toothseg_fullspace, {
            "source": "toothseg_fdi_multilabel",
            "input": str(toothseg_fullspace.resolve()),
        }

    teeth_fullspace = (
        case_dir
        / "segmentations"
        / "teeth_experimental"
        / "teeth_multilabel_fullspace.nii.gz"
    )
    if teeth_fullspace.exists():
        return teeth_fullspace, {
            "source": "teeth_experimental_fullspace",
            "input": str(teeth_fullspace.resolve()),
        }

    dentalseg_fullspace = (
        case_dir
        / "segmentations"
        / "dentalsegmentator"
        / "dentalsegmentator_multilabel.nii.gz"
    )
    if dentalseg_fullspace.exists():
        return dentalseg_fullspace, {
            "source": "dentalsegmentator_multilabel",
            "input": str(dentalseg_fullspace.resolve()),
        }

    raw_totalseg = case_dir / "segmentations" / "raw_totalseg"
    if any((raw_totalseg / filename).exists() for filename, _label, _name in CRANIOFACIAL_SURFACE_LABELS):
        derived, metadata = build_craniofacial_surface_labelmap(case_dir=case_dir)
        return derived, metadata

    raise FileNotFoundError(
        "No default surface-preview input found. Expected either "
        f"{toothseg_fullspace}, {teeth_fullspace}, {dentalseg_fullspace}, "
        f"or craniofacial masks under {raw_totalseg}."
    )


def build_craniofacial_surface_labelmap(*, case_dir: Path) -> tuple[Path, dict[str, Any]]:
    raw_totalseg = case_dir / "segmentations" / "raw_totalseg"
    output_path = (
        case_dir
        / "segmentations"
        / "derived"
        / "craniofacial_arch_jaw_multilabel.nii.gz"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    existing_specs = [
        (raw_totalseg / filename, label, name)
        for filename, label, name in CRANIOFACIAL_SURFACE_LABELS
        if (raw_totalseg / filename).exists()
    ]
    if not existing_specs:
        raise FileNotFoundError(f"No craniofacial masks found under {raw_totalseg}")

    reference_img = nib.load(str(existing_specs[0][0]))
    reference_shape = tuple(int(value) for value in reference_img.shape[:3])
    reference_affine = reference_img.affine
    data = np.zeros(reference_shape, dtype=np.uint16)
    labels: dict[str, str] = {}
    source_masks: list[dict[str, Any]] = []
    non_empty_count = 0

    for mask_path, label, name in existing_specs:
        image = nib.load(str(mask_path))
        shape = tuple(int(value) for value in image.shape[:3])
        if shape != reference_shape:
            raise ValueError(
                f"Craniofacial mask shape mismatch for {mask_path.name}: "
                f"{shape} != {reference_shape}"
            )
        if not np.allclose(image.affine, reference_affine, atol=1e-5):
            raise ValueError(f"Craniofacial mask affine mismatch for {mask_path.name}")
        mask = np.asanyarray(image.dataobj) > 0
        voxels = int(np.count_nonzero(mask))
        source_masks.append(
            {
                "file": mask_path.name,
                "label": label,
                "name": name,
                "voxels": voxels,
            }
        )
        if voxels == 0:
            continue
        data[mask] = np.uint16(label)
        labels[str(label)] = name
        non_empty_count += 1

    if non_empty_count == 0:
        raise RuntimeError("No non-empty craniofacial masks found for surface preview")

    header = reference_img.header.copy()
    header.set_data_dtype(np.uint16)
    nib.save(nib.Nifti1Image(data, reference_affine, header), str(output_path))
    sidecar_path = label_sidecar_path(output_path)
    sidecar_path.write_text(
        json.dumps(
            {
                "source": "craniofacial_raw_totalseg",
                "labels": labels,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return output_path, {
        "source": "craniofacial_raw_totalseg",
        "input": str(output_path.resolve()),
        "raw_totalseg_dir": str(raw_totalseg.resolve()),
        "label_sidecar": str(sidecar_path.resolve()),
        "source_masks": source_masks,
        "non_empty_mask_count": non_empty_count,
    }


def export_labelmap_surfaces(
    *,
    input_path: Path,
    output_dir: Path,
    min_voxels: int = 1,
    combined: bool = False,
    smoothing: SmoothingConfig | None = None,
    suffix: str = "",
    summary_filename: str | None = "stl_export_summary.json",
    readme_filename: str = "README_STL_EXPORT.md",
    profiler: _StageProfiler | None = None,
) -> dict[str, Any]:
    input_path = input_path.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    labels_dir = output_dir / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)

    smoothing = smoothing or smoothing_config_from_options(preset="none")
    profiler = profiler or _StageProfiler()
    with profiler.measure("nifti_load", purpose="stl_export"):
        image = nib.load(str(input_path))
        data = np.asanyarray(image.dataobj)
    label_names = label_name_map(input_path)
    with profiler.measure("label_scan_and_mask", operation="unique_labels"):
        label_values, counts = np.unique(data, return_counts=True)

    label_entries = []
    for value, count in zip(label_values, counts, strict=True):
        label = int(value)
        voxels = int(count)
        if label == 0 or voxels < min_voxels:
            continue
        name = label_names.get(label, f"label_{label}")
        stl_path = labels_dir / f"label_{label:03d}_{safe_name(name)}{suffix}.stl"
        effective = effective_smoothing_for_label(name=name, voxels=voxels, smoothing=smoothing)
        with profiler.measure("label_scan_and_mask", operation="label_mask", label=label):
            mask = data == label
        mesh = mask_to_mesh(
            mask,
            image.affine,
            smoothing=effective,
            profiler=profiler,
            mesh_kind="label",
        )
        with profiler.measure("stl_write", mesh_kind="label", name=name):
            write_binary_stl(stl_path, mesh["vertices"], mesh["faces"], solid_name=name)
        label_entries.append(
            {
                "label": label,
                "name": name,
                "voxels": voxels,
                "stl": str(stl_path.resolve()),
                "vertices": int(mesh["vertices"].shape[0]),
                "triangles": int(mesh["faces"].shape[0]),
                "bounds_mm": mesh["bounds_mm"],
                "smoothing": _smoothing_to_dict(effective),
            }
        )

    groups = []
    if combined:
        combined_dir = output_dir / "combined"
        combined_dir.mkdir(parents=True, exist_ok=True)
        for group_name, labels in group_specs(label_entries).items():
            if not labels:
                continue
            stl_path = combined_dir / f"{group_name}{suffix}.stl"
            effective = effective_smoothing_for_group(group_name=group_name, smoothing=smoothing)
            with profiler.measure("group_mesh_generation", name=group_name):
                with profiler.measure(
                    "label_scan_and_mask",
                    operation="group_mask",
                    name=group_name,
                ):
                    mask = np.isin(data, labels)
                mesh = mask_to_mesh(
                    mask,
                    image.affine,
                    smoothing=effective,
                    profiler=profiler,
                    mesh_kind="group",
                )
            with profiler.measure("stl_write", mesh_kind="group", name=group_name):
                write_binary_stl(stl_path, mesh["vertices"], mesh["faces"], solid_name=group_name)
            groups.append(
                {
                    "name": group_name,
                    "labels": labels,
                    "stl": str(stl_path.resolve()),
                    "vertices": int(mesh["vertices"].shape[0]),
                    "triangles": int(mesh["faces"].shape[0]),
                    "bounds_mm": mesh["bounds_mm"],
                    "smoothing": _smoothing_to_dict(effective),
                }
            )

    summary = {
        "input": str(input_path),
        "output_dir": str(output_dir.resolve()),
        "source_shape": [int(value) for value in image.shape[:3]],
        "source_spacing": [float(value) for value in image.header.get_zooms()[:3]],
        "label_count": len(label_entries),
        "labels": label_entries,
        "groups": groups,
        "smoothing": _smoothing_to_dict(smoothing),
    }
    with profiler.measure("json_html_write", artifact="stl_summary"):
        if summary_filename is not None:
            _write_json_atomic(output_dir / summary_filename, summary)
        write_markdown_summary(output_dir / readme_filename, summary)
    return summary


def mask_to_mesh(
    mask: np.ndarray,
    affine: np.ndarray,
    *,
    smoothing: SmoothingConfig | None = None,
    step_size: int = 1,
    profiler: _StageProfiler | None = None,
    mesh_kind: str = "unspecified",
) -> dict[str, Any]:
    profiler = profiler or _StageProfiler()
    with profiler.measure(
        "label_scan_and_mask",
        operation="bounding_box",
        mesh_kind=mesh_kind,
    ):
        coords = np.argwhere(mask)
    if coords.size == 0:
        raise RuntimeError("Cannot mesh an empty mask")
    lo = np.maximum(coords.min(axis=0) - 1, 0)
    hi = np.minimum(coords.max(axis=0) + 2, np.array(mask.shape))
    slices = tuple(slice(int(start), int(stop)) for start, stop in zip(lo, hi, strict=True))
    cropped = mask[slices]
    padded = np.pad(cropped.astype(np.uint8), 1, mode="constant", constant_values=0)
    with profiler.measure("marching_cubes", mesh_kind=mesh_kind, step_size=step_size):
        vertices, faces, _normals, _values = measure.marching_cubes(
            padded,
            level=0.5,
            step_size=step_size,
        )
    vertices = vertices + lo - 1
    vertices_mm = nib.affines.apply_affine(affine, vertices).astype(np.float32)
    if smoothing is not None and smoothing.iterations > 0:
        with profiler.measure(
            "smoothing",
            mesh_kind=mesh_kind,
            iterations=smoothing.iterations,
        ):
            vertices_mm = taubin_smooth(
                vertices_mm,
                faces.astype(np.uint32),
                iterations=smoothing.iterations,
                lambda_value=smoothing.lambda_value,
                mu=smoothing.mu,
            )
    bounds = np.vstack([vertices_mm.min(axis=0), vertices_mm.max(axis=0)])
    return {
        "vertices": vertices_mm,
        "faces": faces.astype(np.uint32),
        "bounds_mm": [[float(value) for value in row] for row in bounds],
    }


def taubin_smooth(
    vertices: np.ndarray,
    faces: np.ndarray,
    *,
    iterations: int,
    lambda_value: float,
    mu: float,
) -> np.ndarray:
    if iterations <= 0:
        return vertices.astype(np.float32, copy=True)
    try:
        from scipy import sparse
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("scipy is required for mesh smoothing") from exc

    vertex_count = int(vertices.shape[0])
    if vertex_count == 0:
        return vertices.astype(np.float32, copy=True)
    edges = np.vstack(
        [
            faces[:, [0, 1]],
            faces[:, [1, 2]],
            faces[:, [2, 0]],
            faces[:, [1, 0]],
            faces[:, [2, 1]],
            faces[:, [0, 2]],
        ]
    )
    adjacency = sparse.coo_matrix(
        (np.ones(edges.shape[0], dtype=np.float32), (edges[:, 0], edges[:, 1])),
        shape=(vertex_count, vertex_count),
    ).tocsr()
    degree = np.asarray(adjacency.sum(axis=1)).reshape(-1, 1)
    degree[degree == 0] = 1.0
    result = vertices.astype(np.float32, copy=True)
    for _ in range(iterations):
        result = _laplacian_step(adjacency, degree, result, lambda_value)
        result = _laplacian_step(adjacency, degree, result, mu)
    if not np.all(np.isfinite(result)):
        raise RuntimeError("Mesh smoothing produced non-finite vertices")
    return result.astype(np.float32)


def write_binary_stl(path: Path, vertices: np.ndarray, faces: np.ndarray, *, solid_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"TotalSegWrapper {solid_name}".encode("ascii", errors="ignore")[:80]
    header = header + b" " * (80 - len(header))
    with path.open("wb") as file:
        file.write(header)
        file.write(struct.pack("<I", int(faces.shape[0])))
        for start in range(0, int(faces.shape[0]), STL_WRITE_CHUNK_TRIANGLES):
            face_chunk = faces[start : start + STL_WRITE_CHUNK_TRIANGLES]
            points = np.asarray(vertices[face_chunk], dtype=np.float32)
            normals = np.cross(
                points[:, 1] - points[:, 0],
                points[:, 2] - points[:, 0],
            )
            norms = np.linalg.norm(normals, axis=1)
            np.divide(
                normals,
                norms[:, np.newaxis],
                out=normals,
                where=norms[:, np.newaxis] > 0,
            )
            normals[norms == 0] = 0
            records = np.empty(face_chunk.shape[0], dtype=STL_TRIANGLE_DTYPE)
            records["normal"] = normals
            records["vertices"] = points
            records["attribute"] = 0
            file.write(records.tobytes(order="C"))


def _max_rss_bytes() -> int:
    max_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return max_rss if sys.platform == "darwin" else max_rss * 1024


def group_specs(label_entries: list[dict[str, Any]]) -> dict[str, list[int]]:
    return {
        "all_nonzero": [entry["label"] for entry in label_entries],
        "dental_hard_tissue": [
            entry["label"]
            for entry in label_entries
            if is_dental_hard_tissue(entry["name"])
        ],
        "pulp": [
            entry["label"]
            for entry in label_entries
            if "pulp" in entry["name"]
        ],
        "jaws": [
            entry["label"]
            for entry in label_entries
            if entry["name"] in {
                "lower_jawbone",
                "upper_jawbone",
                "mandible",
                "upper_skull",
                "maxilla_upper_skull",
            }
        ],
    }


def effective_smoothing_for_label(
    *,
    name: str,
    voxels: int,
    smoothing: SmoothingConfig,
) -> SmoothingConfig:
    if smoothing.iterations <= 0:
        return smoothing
    if "pulp" in name or "canal" in name or voxels < 500:
        return SmoothingConfig(
            preset=smoothing.preset,
            iterations=min(smoothing.iterations, 3),
            lambda_value=smoothing.lambda_value,
            mu=smoothing.mu,
        )
    return smoothing


def effective_smoothing_for_group(
    *,
    group_name: str,
    smoothing: SmoothingConfig,
) -> SmoothingConfig:
    if smoothing.iterations <= 0:
        return smoothing
    if group_name == "pulp":
        return SmoothingConfig(
            preset=smoothing.preset,
            iterations=min(smoothing.iterations, 3),
            lambda_value=smoothing.lambda_value,
            mu=smoothing.mu,
        )
    return smoothing


def label_name_map(input_path: Path | None = None) -> dict[int, str]:
    mapping: dict[int, str] = {}
    try:
        from totalsegmentator.map_to_binary import class_map

        mapping.update(
            {int(label): str(name) for label, name in class_map.get("teeth", {}).items()}
        )
    except Exception:  # noqa: BLE001
        pass
    if input_path is not None:
        sidecar = label_sidecar_path(input_path)
        if sidecar.exists():
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            labels = payload.get("labels", payload)
            mapping.update({int(label): str(name) for label, name in labels.items()})
    return mapping


def label_sidecar_path(input_path: Path) -> Path:
    return input_path.with_name(input_path.name + ".labels.json")


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return value.strip("_") or "label"


def is_dental_hard_tissue(name: str) -> bool:
    normalized = name.lower()
    if normalized in {
        "bridge",
        "crown",
        "implant",
        "upper_teeth",
        "lower_teeth",
        "teeth_upper",
        "teeth_lower",
    }:
        return True
    return "fdi" in normalized and "pulp" not in normalized


def write_markdown_summary(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Surface Preview Export",
        "",
        f"Input: `{Path(summary['input']).name}`",
        f"Label STL count: {summary['label_count']}",
        f"Smoothing preset: `{summary['smoothing']['preset']}`",
        "",
        "## Combined Meshes",
        "",
        "| Name | Labels | Triangles | File |",
        "|---|---:|---:|---|",
    ]
    for group in summary["groups"]:
        lines.append(
            f"| {group['name']} | {len(group['labels'])} | "
            f"{group['triangles']} | `{Path(group['stl']).name}` |"
        )
    lines.extend(
        [
            "",
            "## Label Meshes",
            "",
            "| Label | Name | Voxels | Triangles | File |",
            "|---:|---|---:|---:|---|",
        ]
    )
    for entry in summary["labels"]:
        lines.append(
            f"| {entry['label']} | {entry['name']} | {entry['voxels']} | "
            f"{entry['triangles']} | `{Path(entry['stl']).name}` |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _build_preview_meshes(
    *,
    input_path: Path,
    summary: dict[str, Any],
    smoothing: SmoothingConfig,
    step_size: int,
    profiler: _StageProfiler | None = None,
    image: nib.Nifti1Image | None = None,
    data: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    profiler = profiler or _StageProfiler()
    if image is None or data is None:
        with profiler.measure("nifti_load", purpose="browser_preview"):
            image = nib.load(str(input_path))
            data = np.asanyarray(image.dataobj)
    preview = []
    group_order = ["jaws", "dental_hard_tissue", "pulp", "all_nonzero"]
    groups = {group["name"]: group for group in summary["groups"]}
    for name in group_order:
        group = groups.get(name)
        if not group:
            continue
        with profiler.measure(
            "label_scan_and_mask",
            operation="browser_group_mask",
            name=name,
        ):
            mask = np.isin(data, group["labels"])
        mesh = mask_to_mesh(
            mask,
            image.affine,
            smoothing=effective_smoothing_for_group(group_name=name, smoothing=smoothing),
            step_size=step_size,
            profiler=profiler,
            mesh_kind="browser",
        )
        preview.append(
            {
                "name": name,
                "labels": group["labels"],
                "defaultVisible": name in {"dental_hard_tissue", "jaws"},
                "opacity": 0.35 if name == "jaws" else 1.0,
                "color": GROUP_COLORS.get(name, "#cccccc"),
                "vertices": _rounded_list(mesh["vertices"]),
                "faces": mesh["faces"].astype(int).tolist(),
            }
        )
    return preview


def _write_offline_viewer(
    path: Path,
    *,
    summary: dict[str, Any],
    preview_meshes: list[dict[str, Any]],
) -> Path:
    payload = {
        "dataLabel": "選択したデータ",
        "labelCount": summary["label_count"],
        "smoothing": summary["smoothing"],
        "smoothingPresets": _viewer_smoothing_presets(),
        "materialPreset": "rich",
        "meshes": preview_meshes,
    }
    document = _html_document(payload)
    index_html, bundle_js = _externalize_viewer_script(
        document,
        bundle_filename=VIEWER_BUNDLE_FILENAME,
    )
    bundle_path = path.with_name(VIEWER_BUNDLE_FILENAME)
    path.write_text(index_html, encoding="utf-8")
    bundle_path.write_text(bundle_js, encoding="utf-8")
    return bundle_path


def _html_document(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return _webgl_html_document(payload_json)


def _externalize_viewer_script(
    document: str,
    *,
    bundle_filename: str,
) -> tuple[str, str]:
    script_open = "<script>\n"
    script_close = "\n</script>"
    script_end = document.rfind(script_close)
    script_start = document.rfind(script_open, 0, script_end)
    if script_start < 0 or script_end <= script_start:
        raise ValueError("Viewer document does not contain the expected inline script")
    body_start = script_start + len(script_open)
    bundle_js = document[body_start:script_end] + "\n"
    bundle_digest = hashlib.sha256(bundle_js.encode("utf-8")).hexdigest()[:16]
    bundle_url = f"{bundle_filename}?sha256={bundle_digest}"
    loader_script = """<script>
(function () {
  requestAnimationFrame(function () {
    const bundle = document.createElement('script');
    bundle.src = '__BUNDLE_FILENAME__';
    bundle.onerror = function () {
      window.showViewerLoadError('必要な表示データを読み込めませんでした。結果フォルダ内のファイルを移動せず、ページを再読み込みしてください。');
    };
    document.head.appendChild(bundle);
  });
})();
</script>""".replace("__BUNDLE_FILENAME__", bundle_url)
    index_html = (
        document[:script_start]
        + loader_script
        + document[script_end + len(script_close):]
    )
    return index_html, bundle_js



def _webgl_html_document(payload_json: str) -> str:
    script_safe_payload_json = payload_json.replace("<", "\\u003c")
    return """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TotalSegmentator 3Dビューアー</title>
<style>
body { margin: 0; background: #15171b; color: #e9edf2; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
#loadingOverlay { position: fixed; inset: 0; z-index: 10; display: grid; place-items: center; padding: 24px; background: #111317; opacity: 1; visibility: visible; transition: opacity 160ms ease, visibility 160ms ease; }
#loadingOverlay.is-complete { opacity: 0; visibility: hidden; pointer-events: none; }
#loadingOverlay.is-error .loadingSpinner { display: none; }
#loadingOverlay.is-error .loadingCard { border-color: #a96e68; }
.loadingCard { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 14px; align-items: center; width: min(420px, 100%); padding: 18px 20px; border: 1px solid #46515d; border-radius: 12px; background: #20242a; box-shadow: 0 14px 40px rgba(0, 0, 0, 0.35); }
.loadingSpinner { width: 28px; height: 28px; border: 3px solid #46515d; border-top-color: #9bc9d0; border-radius: 50%; animation: loadingSpin 0.9s linear infinite; }
#loadingTitle { margin: 0 0 5px; font-size: 16px; font-weight: 700; }
#loadingDetail { margin: 0; color: #c8d0d8; font-size: 13px; line-height: 1.5; }
@keyframes loadingSpin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  #loadingOverlay { transition: none; }
  .loadingSpinner { animation: none; border-top-color: #46515d; }
}
#app { position: relative; display: grid; grid-template-columns: 300px minmax(0, 1fr); height: 100vh; }
#panel { z-index: 4; padding: 16px; background: #20242a; overflow: auto; border-right: 1px solid #343941; }
#panelToggle { display: none; position: fixed; z-index: 6; top: max(12px, env(safe-area-inset-top)); left: max(12px, env(safe-area-inset-left)); min-width: 44px; min-height: 44px; padding: 0 13px; border: 1px solid #66717d; border-radius: 9px; background: rgba(32, 36, 42, 0.94); color: #fff; font: inherit; font-weight: 700; box-shadow: 0 6px 18px rgba(0, 0, 0, 0.28); }
#panelBackdrop { display: none; position: fixed; z-index: 3; inset: 0; background: rgba(5, 7, 10, 0.50); }
#panel h1 { font-size: 18px; margin: 0 0 12px; }
#panel h2 { font-size: 14px; margin: 16px 0 8px; color: #cfe4e2; }
#panel p, #panel label { font-size: 13px; line-height: 1.45; }
#panel button, #panel select { padding: 8px; border: 1px solid #535b66; background: #2e343d; color: #fff; border-radius: 6px; }
#panel button.active { background: #496078; border-color: #71839a; }
#mode, #views, .segmented { display: grid; gap: 6px; margin: 10px 0; }
#mode { grid-template-columns: 1fr 1fr; }
#displayControls { display: grid; gap: 10px; margin: 10px 0 14px; }
#views { grid-template-columns: 1fr 1fr; }
#views button, .segmented button { width: 100%; }
.segmented { grid-template-columns: 1fr 1fr; }
#displayModeButtons { grid-template-columns: repeat(3, 1fr); }
.controlRow { display: grid; gap: 6px; }
.controlLabel { color: #cfe4e2; font-size: 13px; font-weight: 700; }
#geometryControl[hidden] { display: none; }
#smoothingControl, #materialControl { display: grid; gap: 6px; margin: 10px 0; }
#advancedControls { border-top: 1px solid #343941; margin-top: 12px; padding-top: 10px; }
#advancedControls summary { color: #cfe4e2; cursor: pointer; font-size: 14px; font-weight: 700; }
#layers label { display: block; margin: 8px 0; }
canvas { width: 100%; height: 100%; display: block; background: #111317; touch-action: none; }
code { color: #c9e2ff; }
@media (max-width: 900px) {
  #app { grid-template-columns: minmax(0, 1fr); }
  #panel { position: fixed; inset: 0 auto 0 0; width: min(320px, calc(100vw - 48px)); box-sizing: border-box; transform: translateX(-105%); transition: transform 180ms ease; box-shadow: 12px 0 30px rgba(0, 0, 0, 0.34); }
  #app.panel-open #panel { transform: translateX(0); }
  #panelToggle { display: block; }
  #app.panel-open #panelToggle { left: min(272px, calc(100vw - 96px)); }
  #app.panel-open #panelBackdrop { display: block; }
}
@media (prefers-reduced-motion: reduce) {
  #panel { transition: none; }
}
</style>
</head>
<body>
<div id="loadingOverlay" role="status" aria-live="polite" aria-busy="true">
  <div class="loadingCard">
    <div class="loadingSpinner" aria-hidden="true"></div>
    <div>
      <p id="loadingTitle">3Dデータを読み込んでいます</p>
      <p id="loadingDetail">データ量によって、表示までしばらくかかることがあります。</p>
    </div>
  </div>
</div>
<script>
(function () {
  const overlay = document.getElementById('loadingOverlay');
  const title = document.getElementById('loadingTitle');
  const detail = document.getElementById('loadingDetail');
  window.showViewerLoadError = function (message) {
    if (overlay.getAttribute('aria-busy') !== 'true') return;
    overlay.setAttribute('aria-busy', 'false');
    overlay.classList.add('is-error');
    title.textContent = '3D表示を準備できませんでした';
    detail.textContent = message;
  };
  window.addEventListener('error', function () {
    window.showViewerLoadError('3D表示の準備中にエラーが発生しました。ページを再読み込みしてください。繰り返す場合は、結果フォルダのログを確認してください。');
  });
  window.addEventListener('unhandledrejection', function () {
    window.showViewerLoadError('3D表示の準備中にエラーが発生しました。ページを再読み込みしてください。繰り返す場合は、結果フォルダのログを確認してください。');
  });
})();
</script>
<div id="app">
  <button id="panelToggle" type="button" aria-controls="panel" aria-expanded="false" aria-label="表示設定を開く">設定</button>
  <div id="panelBackdrop" aria-hidden="true"></div>
  <aside id="panel" aria-label="表示設定">
    <h1>TotalSegmentator 3Dビューアー</h1>
    <p>データ: <code id="dataName"></code></p>
    <p>検出された構造ラベル: <code id="labelCount"></code><br>表示モード: <code id="displayModeLabel"></code><br>形状: <code id="geometryModeLabel"></code><br>表面平滑化: <code id="smoothingModeLabel"></code><br>質感: <code id="materialModeLabel"></code></p>
    <h2>表示</h2>
    <div id="displayControls">
    <div id="displayModeControl" class="controlRow">
      <span class="controlLabel">表示モード</span>
      <div class="segmented" id="displayModeButtons">
        <button id="displayNormal" type="button">通常</button>
        <button id="displayWireframe" type="button">ワイヤー</button>
        <button id="displayXray" type="button">X-ray</button>
      </div>
    </div>
    <div id="geometryControl" class="controlRow" hidden>
      <span class="controlLabel">形状</span>
      <div class="segmented" id="geometryButtons">
        <button id="geometryOriginal" type="button">元の形状</button>
        <button id="geometrySdf" type="button">なめらか補完</button>
      </div>
    </div>
    <label id="materialControl" class="controlRow">
      <span class="controlLabel">質感</span>
      <select id="materialPreset" aria-label="質感"></select>
    </label>
    </div>
    <details id="advancedControls">
      <summary>詳細設定</summary>
      <h2>表面平滑化</h2>
      <div id="smoothingControl">
        <select id="smoothingPreset" aria-label="表面平滑化"></select>
      </div>
    <h2>操作方法</h2>
    <div id="mode">
      <button id="modeTrackpad" class="active" type="button" aria-label="操作方法: トラックパッド">トラックパッド</button>
      <button id="modeMouse" type="button" aria-label="操作方法: マウス">マウス</button>
    </div>
    <h2>表示方向</h2>
    <div id="views">
      <button id="viewFront" type="button" aria-label="標準方向で表示">標準方向</button>
      <button id="viewBack" type="button" aria-label="標準方向の反対側から表示">反対方向</button>
      <button id="viewLeft" type="button" aria-label="左回転方向で表示">左回転方向</button>
      <button id="viewRight" type="button" aria-label="右回転方向で表示">右回転方向</button>
      <button id="viewTop" type="button" aria-label="上方向から表示">上方向</button>
      <button id="viewBottom" type="button" aria-label="下方向から表示">下方向</button>
      <button id="fitAll" type="button" aria-label="全体に合わせる">全体に合わせる</button>
      <button id="reset" type="button" aria-label="初期表示に戻す">初期表示に戻す</button>
    </div>
    <h2>表示する構造</h2>
    <div id="layers"></div>
    </details>
  </aside>
  <canvas id="view" aria-label="3Dデータ表示領域"></canvas>
</div>
<script id="viewerData" type="application/json">__PAYLOAD__</script>
<script>
const DATA = JSON.parse(document.getElementById('viewerData').textContent);
const canvas = document.getElementById('view');
const gl = canvas.getContext('webgl', { antialias: true, alpha: false });
const ctx2d = gl ? null : canvas.getContext('2d');
const MIN_ZOOM = 0.0001;
const MAX_ZOOM = 1000.0;
const MAX_ZOOM_LOG_DELTA_PER_INPUT = 3.0;
const MAX_ARCBALL_STEP_PX = 12;
const DEFAULT_SMOOTHING_PRESETS = {
  none: { iterations: 0, lambda: 0.0, mu: 0.0 },
  slicer_like: { iterations: 10, lambda: 0.5, mu: -0.53 },
  medium: { iterations: 20, lambda: 0.5, mu: -0.53 },
  strong: { iterations: 30, lambda: 0.5, mu: -0.53 }
};
const SMOOTHING_PRESETS = DATA.smoothingPresets || DEFAULT_SMOOTHING_PRESETS;
const SMOOTHING_PRESET_ORDER = ['none', 'slicer_like', 'medium', 'strong'];
const MATERIAL_MODE_ORDER = ['standard', 'rich', 'realistic', 'neutral', 'high_contrast'];
const DISPLAY_MODE_ORDER = ['normal', 'wireframe', 'xray'];
const GEOMETRY_PRESET_ORDER = geometryPresetNames();
let inputMode = 'trackpad';
let dragging = null;
let lastPointer = null;
const activePointers = new Map();
let commandScrollFrame = 0;
let pendingCommandScroll = [0, 0];
let commandScrollSuppressZoom = false;
const camera = {
  orbitYawDegrees: 0,
  orbitPitchDegrees: 0,
  orientation: identity3(),
  pan: [0, 0],
  zoom: 0.05,
  viewCenter: [0, 0, 0],
  viewScale: 1,
  projection: 'Perspective'
};
const visible = Object.fromEntries(DATA.meshes.map(m => [m.name, !!m.defaultVisible]));
let currentGeometryPreset = normalizeGeometryPreset(DATA.geometryPreset || '');
let currentMaterialMode = normalizeMaterialMode(DATA.materialPreset || 'rich');
let currentDisplayMode = normalizeDisplayMode(DATA.displayMode || 'xray');
let preparedMeshes = [];
let currentSmoothingPreset = normalizeSmoothingPreset((DATA.smoothing && DATA.smoothing.preset) || 'slicer_like');
let program = null;
let attribs = null;
let uniforms = null;
let outlineProgram = null;
let outlineAttribs = null;
let outlineUniforms = null;
let outlineResources = null;
let gridResources = null;
let uintIndexExtension = null;
const loadingOverlay = document.getElementById('loadingOverlay');
const loadingTitle = document.getElementById('loadingTitle');
const loadingDetail = document.getElementById('loadingDetail');
const geometryControl = document.getElementById('geometryControl');
const geometryOriginalButton = document.getElementById('geometryOriginal');
const geometrySdfButton = document.getElementById('geometrySdf');
const smoothingPresetSelect = document.getElementById('smoothingPreset');
const materialPresetSelect = document.getElementById('materialPreset');
const displayNormalButton = document.getElementById('displayNormal');
const displayWireframeButton = document.getElementById('displayWireframe');
const displayXrayButton = document.getElementById('displayXray');
const app = document.getElementById('app');
const panelToggle = document.getElementById('panelToggle');
const panelBackdrop = document.getElementById('panelBackdrop');
configureResponsivePanel();
scheduleViewerInitialization();

function configureResponsivePanel() {
  panelToggle.onclick = () => setPanelOpen(!app.classList.contains('panel-open'));
  panelBackdrop.onclick = () => setPanelOpen(false);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') setPanelOpen(false);
  });
}
function setPanelOpen(open) {
  app.classList.toggle('panel-open', open);
  panelToggle.setAttribute('aria-expanded', String(open));
  panelToggle.setAttribute('aria-label', open ? '表示設定を閉じる' : '表示設定を開く');
  panelToggle.textContent = open ? '閉じる' : '設定';
}

function scheduleViewerInitialization() {
  requestAnimationFrame(() => {
    setLoadingPhase(
      '3D表示を準備しています',
      '形状と表示候補を準備しています。'
    );
    setTimeout(initializeViewer, 0);
  });
}

function initializeViewer() {
  try {
    preparedMeshes = DATA.meshes.map(prepareMesh);
    document.getElementById('dataName').textContent = DATA.dataLabel || '選択したデータ';
    document.getElementById('labelCount').textContent = DATA.labelCount;
    populateGeometryControl();
    populateSmoothingControl();
    populateMaterialControl();
    populateDisplayModeControl();
    applyMaterialMode(currentMaterialMode, false);
    applySmoothingPreset(currentSmoothingPreset, false);
    const layers = document.getElementById('layers');
    for (const mesh of preparedMeshes) {
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = visible[mesh.name];
      input.onchange = () => { visible[mesh.name] = input.checked; draw(); };
      const countNode = document.createElement('span');
      mesh.layerCountNode = countNode;
      label.appendChild(input);
      label.appendChild(document.createTextNode(' ' + meshDisplayName(mesh.name)));
      label.appendChild(countNode);
      layers.appendChild(label);
    }
    updateLayerStats();
    document.getElementById('modeTrackpad').onclick = () => setInputMode('trackpad');
    document.getElementById('modeMouse').onclick = () => setInputMode('mouse');
    document.getElementById('viewFront').onclick = () => applyAxisView(0, 0);
    document.getElementById('viewBack').onclick = () => applyAxisView(180, 0);
    document.getElementById('viewLeft').onclick = () => applyAxisView(270, 0);
    document.getElementById('viewRight').onclick = () => applyAxisView(90, 0);
    document.getElementById('viewTop').onclick = () => applyAxisView(0, 89);
    document.getElementById('viewBottom').onclick = () => applyAxisView(0, 271);
    document.getElementById('fitAll').onclick = () => { fitAll(); draw(); };
    document.getElementById('reset').onclick = () => { resetCamera(); draw(); };
    if (gl) initWebGl();
    resetCamera();
    fitAll();
    window.onresize = resize;
    canvas.addEventListener('pointerdown', onPointerDown);
    canvas.addEventListener('pointermove', onPointerMove);
    canvas.addEventListener('pointerup', onPointerUp);
    canvas.addEventListener('pointercancel', onPointerUp);
    canvas.addEventListener('wheel', onWheel, { passive: false });
    canvas.addEventListener('contextmenu', event => event.preventDefault());
    resize();
    requestAnimationFrame(finishViewerLoading);
  } catch (error) {
    console.error('3Dビューアーの初期化に失敗しました', error);
    failViewerLoading();
  }
}

function setLoadingPhase(title, detail) {
  loadingTitle.textContent = title;
  loadingDetail.textContent = detail;
}

function finishViewerLoading() {
  loadingOverlay.setAttribute('aria-busy', 'false');
  loadingOverlay.setAttribute('aria-hidden', 'true');
  loadingOverlay.classList.add('is-complete');
}

function failViewerLoading() {
  loadingOverlay.setAttribute('aria-busy', 'false');
  loadingOverlay.classList.add('is-error');
  setLoadingPhase(
    '3D表示を準備できませんでした',
    'ページを再読み込みしてください。繰り返す場合は、結果フォルダのログを確認してください。'
  );
}

function setInputMode(mode) {
  inputMode = mode;
  document.getElementById('modeTrackpad').classList.toggle('active', mode === 'trackpad');
  document.getElementById('modeMouse').classList.toggle('active', mode === 'mouse');
}

function meshDisplayName(name) {
  const labels = {
    dental_hard_tissue: '歯',
    jaws: '顎骨',
    pulp: '歯髄腔（推定）',
    all_nonzero: '全構造'
  };
  return labels[name] || name;
}

function smoothingLabel(name) {
  const labels = {
    none: 'なし',
    slicer_like: '標準',
    medium: '中',
    strong: '強'
  };
  return labels[name] || name;
}
function geometryPresetNames() {
  const names = [];
  if (Array.isArray(DATA.geometryPresetOrder)) {
    for (const name of DATA.geometryPresetOrder) {
      if (!names.includes(name)) names.push(name);
    }
  }
  for (const mesh of DATA.meshes || []) {
    const variants = mesh.variants || {};
    for (const name of Object.keys(variants)) {
      if (!names.includes(name)) names.push(name);
    }
  }
  if (!names.length) names.push('base');
  return names;
}
function geometryPresetLabel(name) {
  const preset = DATA.geometryPresets && DATA.geometryPresets[name];
  if (typeof preset === 'string') return preset;
  if (preset && typeof preset.label === 'string') return preset.label;
  const labels = {
    base: '標準',
    original: '元の形状',
    sdf: 'なめらか補完'
  };
  return labels[name] || name;
}
function normalizeGeometryPreset(name) {
  if (GEOMETRY_PRESET_ORDER.includes(name)) return name;
  if (GEOMETRY_PRESET_ORDER.includes('sdf')) return 'sdf';
  if (GEOMETRY_PRESET_ORDER.includes('base')) return 'base';
  return GEOMETRY_PRESET_ORDER[0] || 'base';
}
function hasGeometryVariants() {
  return GEOMETRY_PRESET_ORDER.length > 1;
}
function materialModeLabel(name) {
  const labels = {
    standard: '標準',
    rich: 'リッチ',
    realistic: 'リアル',
    neutral: 'ニュートラル',
    high_contrast: '高コントラスト'
  };
  return labels[name] || name;
}
function displayModeLabel(name) {
  const labels = {
    normal: '通常',
    wireframe: 'ワイヤーフレーム',
    xray: 'X-ray'
  };
  return labels[name] || name;
}
function smoothingPresetNames() {
  const known = SMOOTHING_PRESET_ORDER.filter(name => Object.prototype.hasOwnProperty.call(SMOOTHING_PRESETS, name));
  const extra = Object.keys(SMOOTHING_PRESETS).filter(name => !known.includes(name)).sort();
  return known.concat(extra);
}
function normalizeSmoothingPreset(name) {
  if (Object.prototype.hasOwnProperty.call(SMOOTHING_PRESETS, name)) return name;
  if (Object.prototype.hasOwnProperty.call(SMOOTHING_PRESETS, 'slicer_like')) return 'slicer_like';
  if (Object.prototype.hasOwnProperty.call(SMOOTHING_PRESETS, 'none')) return 'none';
  const names = Object.keys(SMOOTHING_PRESETS);
  return names.length ? names[0] : 'none';
}
function normalizeMaterialMode(name) {
  if (name === 'clinical') return 'neutral';
  if (MATERIAL_MODE_ORDER.includes(name)) return name;
  return 'rich';
}
function normalizeDisplayMode(name) {
  return DISPLAY_MODE_ORDER.includes(name) ? name : 'normal';
}
function populateGeometryControl() {
  const hasVariants = hasGeometryVariants();
  geometryControl.hidden = !hasVariants;
  document.getElementById('geometryModeLabel').textContent = geometryPresetLabel(currentGeometryPreset);
  if (!hasVariants) return;
  geometryOriginalButton.onclick = () => setGeometryPreset('original');
  geometrySdfButton.onclick = () => setGeometryPreset('sdf');
  updateGeometryButtons();
}
function populateSmoothingControl() {
  smoothingPresetSelect.innerHTML = '';
  for (const name of smoothingPresetNames()) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = smoothingLabel(name);
    smoothingPresetSelect.appendChild(option);
  }
  smoothingPresetSelect.value = currentSmoothingPreset;
  smoothingPresetSelect.onchange = () => setSmoothingPreset(smoothingPresetSelect.value);
}
function populateMaterialControl() {
  materialPresetSelect.innerHTML = '';
  for (const name of MATERIAL_MODE_ORDER) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = materialModeLabel(name);
    materialPresetSelect.appendChild(option);
  }
  materialPresetSelect.value = currentMaterialMode;
  materialPresetSelect.onchange = () => setMaterialMode(materialPresetSelect.value);
}
function populateDisplayModeControl() {
  displayNormalButton.onclick = () => setDisplayMode('normal');
  displayWireframeButton.onclick = () => setDisplayMode('wireframe');
  displayXrayButton.onclick = () => setDisplayMode('xray');
  updateDisplayModeControl();
}
function setDisplayMode(name) {
  currentDisplayMode = normalizeDisplayMode(name);
  updateDisplayModeControl();
  if (gl && currentDisplayMode === 'xray') resizeOutlineResources();
  draw();
}
function updateDisplayModeControl() {
  displayNormalButton.classList.toggle('active', currentDisplayMode === 'normal');
  displayWireframeButton.classList.toggle('active', currentDisplayMode === 'wireframe');
  displayXrayButton.classList.toggle('active', currentDisplayMode === 'xray');
  displayNormalButton.setAttribute('aria-pressed', String(currentDisplayMode === 'normal'));
  displayWireframeButton.setAttribute('aria-pressed', String(currentDisplayMode === 'wireframe'));
  displayXrayButton.setAttribute('aria-pressed', String(currentDisplayMode === 'xray'));
  materialPresetSelect.disabled = currentDisplayMode !== 'normal';
  document.getElementById('displayModeLabel').textContent = displayModeLabel(currentDisplayMode);
}
function setSmoothingPreset(name) {
  const preset = normalizeSmoothingPreset(name);
  currentSmoothingPreset = preset;
  smoothingPresetSelect.value = preset;
  applySmoothingPreset(preset, true);
  draw();
}
function setGeometryPreset(name) {
  const preset = normalizeGeometryPreset(name);
  currentGeometryPreset = preset;
  updateGeometryButtons();
  for (const mesh of preparedMeshes) {
    applyRawGeometry(mesh, geometryForPreset(mesh.raw, preset));
  }
  applySmoothingPreset(currentSmoothingPreset, false);
  if (gl) {
    for (const mesh of preparedMeshes) rebuildMeshBuffers(mesh);
  }
  updateLayerStats();
  document.getElementById('geometryModeLabel').textContent = geometryPresetLabel(currentGeometryPreset);
  draw();
}
function updateGeometryButtons() {
  if (!hasGeometryVariants()) return;
  geometryOriginalButton.hidden = !GEOMETRY_PRESET_ORDER.includes('original');
  geometrySdfButton.hidden = !GEOMETRY_PRESET_ORDER.includes('sdf');
  geometryOriginalButton.classList.toggle('active', currentGeometryPreset === 'original');
  geometrySdfButton.classList.toggle('active', currentGeometryPreset === 'sdf');
}
function setMaterialMode(name) {
  applyMaterialMode(name, true);
}
function applyMaterialMode(name, redraw) {
  currentMaterialMode = normalizeMaterialMode(name);
  materialPresetSelect.value = currentMaterialMode;
  for (const mesh of preparedMeshes) {
    mesh.material = materialFor(mesh.name, mesh.baseRgb, mesh.baseOpacity, currentMaterialMode);
  }
  document.getElementById('materialModeLabel').textContent = materialModeLabel(currentMaterialMode);
  if (redraw) draw();
}
function applySmoothingPreset(presetName, refreshGpu) {
  for (const mesh of preparedMeshes) {
    const config = effectiveSmoothingConfig(mesh.name, presetName);
    const vertices = smoothMeshVertices(mesh, config);
    mesh.vertices = verticesFinite(vertices) ? vertices : new Float32Array(mesh.baseVertices);
    mesh.normals = computeVertexNormals(mesh.vertices, mesh.faces);
    mesh.bounds = computeBounds(mesh.vertices);
    if (refreshGpu) refreshMeshBuffers(mesh);
  }
  const smoothingModeLabel = document.getElementById('smoothingModeLabel');
  if (smoothingModeLabel) smoothingModeLabel.textContent = smoothingLabel(presetName);
}
function geometryForPreset(raw, presetName) {
  const variants = raw.variants || {};
  const preset = normalizeGeometryPreset(presetName);
  if (variants[preset]) return variants[preset];
  if (variants.base) return variants.base;
  return { vertices: raw.vertices, faces: raw.faces };
}
function applyRawGeometry(mesh, geometry) {
  mesh.baseVertices = flattenVertices(geometry.vertices);
  mesh.faces = geometry.faces;
  mesh.vertices = new Float32Array(mesh.baseVertices);
  mesh.normals = computeVertexNormals(mesh.vertices, mesh.faces);
  mesh.bounds = computeBounds(mesh.vertices);
  mesh.adjacency = null;
}
function flattenVertices(vertices) {
  const out = new Float32Array(vertices.length * 3);
  for (let i = 0; i < vertices.length; i++) out.set(vertices[i], i * 3);
  return out;
}
function updateLayerStats() {
  for (const mesh of preparedMeshes) {
    if (mesh.layerCountNode) {
      mesh.layerCountNode.textContent = '（ポリゴン数: ' + mesh.faces.length + '）';
    }
  }
}
function smoothingConfigForPreset(name) {
  const preset = SMOOTHING_PRESETS[normalizeSmoothingPreset(name)] || DEFAULT_SMOOTHING_PRESETS.none;
  const lambdaValue = preset.lambda !== undefined
    ? preset.lambda
    : (preset.lambda_value !== undefined ? preset.lambda_value : (preset.lambdaValue || 0));
  return {
    iterations: Math.max(0, Math.floor(Number(preset.iterations || 0))),
    lambdaValue: Number(lambdaValue || 0),
    mu: Number(preset.mu || 0)
  };
}
function effectiveSmoothingConfig(meshName, presetName) {
  const config = smoothingConfigForPreset(presetName);
  if ((meshName === 'pulp' || meshName === 'all_nonzero') && config.iterations > 0) {
    config.iterations = Math.min(config.iterations, 3);
  }
  return config;
}
function smoothMeshVertices(mesh, config) {
  if (!config || config.iterations <= 0) return new Float32Array(mesh.baseVertices);
  const adjacency = meshAdjacency(mesh);
  let result = new Float32Array(mesh.baseVertices);
  for (let i = 0; i < config.iterations; i++) {
    result = laplacianSmoothStep(result, adjacency, config.lambdaValue);
    result = laplacianSmoothStep(result, adjacency, config.mu);
  }
  return result;
}
function meshAdjacency(mesh) {
  if (mesh.adjacency) return mesh.adjacency;
  const vertexCount = mesh.baseVertices.length / 3;
  const sets = Array.from({ length: vertexCount }, () => new Set());
  for (const face of mesh.faces) {
    addMeshEdge(sets, face[0], face[1]);
    addMeshEdge(sets, face[1], face[2]);
    addMeshEdge(sets, face[2], face[0]);
  }
  mesh.adjacency = sets.map(set => Array.from(set));
  return mesh.adjacency;
}
function addMeshEdge(sets, a, b) {
  if (a === b) return;
  sets[a].add(b);
  sets[b].add(a);
}
function laplacianSmoothStep(vertices, adjacency, weight) {
  const out = new Float32Array(vertices.length);
  for (let i = 0; i < adjacency.length; i++) {
    const neighbors = adjacency[i];
    const offset = i * 3;
    if (!neighbors.length) {
      out[offset] = vertices[offset];
      out[offset + 1] = vertices[offset + 1];
      out[offset + 2] = vertices[offset + 2];
      continue;
    }
    let sx = 0, sy = 0, sz = 0;
    for (const neighbor of neighbors) {
      const source = neighbor * 3;
      sx += vertices[source];
      sy += vertices[source + 1];
      sz += vertices[source + 2];
    }
    const scale = 1 / neighbors.length;
    out[offset] = vertices[offset] + weight * (sx * scale - vertices[offset]);
    out[offset + 1] = vertices[offset + 1] + weight * (sy * scale - vertices[offset + 1]);
    out[offset + 2] = vertices[offset + 2] + weight * (sz * scale - vertices[offset + 2]);
  }
  return out;
}
function verticesFinite(vertices) {
  for (let i = 0; i < vertices.length; i++) {
    if (!Number.isFinite(vertices[i])) return false;
  }
  return true;
}
function resize() {
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * devicePixelRatio));
  canvas.height = Math.max(1, Math.floor(rect.height * devicePixelRatio));
  if (gl) {
    gl.viewport(0, 0, canvas.width, canvas.height);
    if (currentDisplayMode === 'xray') resizeOutlineResources();
  }
  draw();
}
function onPointerDown(event) {
  const local = localPoint(event);
  lastPointer = local;
  activePointers.set(event.pointerId, { local, button: event.button });
  dragging = activePointers.size === 1
    ? { pointerId: event.pointerId, button: event.button, lastLocal: local }
    : null;
  canvas.setPointerCapture(event.pointerId);
  event.preventDefault();
}
function onPointerMove(event) {
  const local = localPoint(event);
  lastPointer = local;
  if (!activePointers.has(event.pointerId)) return;
  if (activePointers.size >= 2) {
    const previousPair = Array.from(activePointers.values()).slice(0, 2);
    activePointers.set(event.pointerId, {
      local,
      button: activePointers.get(event.pointerId).button
    });
    const currentPair = Array.from(activePointers.values()).slice(0, 2);
    applyTwoPointerGesture(previousPair, currentPair);
    dragging = null;
    draw();
    event.preventDefault();
    return;
  }
  activePointers.set(event.pointerId, {
    local,
    button: activePointers.get(event.pointerId).button
  });
  if (!dragging || dragging.pointerId !== event.pointerId) {
    dragging = {
      pointerId: event.pointerId,
      button: activePointers.get(event.pointerId).button,
      lastLocal: local
    };
    return;
  }
  const dx = local[0] - dragging.lastLocal[0];
  const dy = local[1] - dragging.lastLocal[1];
  if (inputMode === 'trackpad') {
    if (dragging.button === 2) {
      camera.pan[0] += dx * 1.20;
      camera.pan[1] += dy * 1.20;
    } else {
      applyArcballDragMotion(dragging.lastLocal, local, 1.35);
    }
  } else if (dragging.button === 1) {
    camera.pan[0] += dx;
    camera.pan[1] += dy;
  } else {
    applyArcballDragMotion(dragging.lastLocal, local, 1.45);
  }
  dragging.lastLocal = local;
  draw();
  event.preventDefault();
}
function onPointerUp(event) {
  try { canvas.releasePointerCapture(event.pointerId); } catch (_error) {}
  activePointers.delete(event.pointerId);
  const remaining = activePointers.entries().next();
  if (!remaining.done) {
    const [pointerId, pointer] = remaining.value;
    dragging = { pointerId, button: pointer.button, lastLocal: pointer.local };
  } else {
    dragging = null;
  }
}
function applyTwoPointerGesture(previousPair, currentPair) {
  const previousCenter = midpoint2(previousPair[0].local, previousPair[1].local);
  const currentCenter = midpoint2(currentPair[0].local, currentPair[1].local);
  camera.pan[0] += currentCenter[0] - previousCenter[0];
  camera.pan[1] += currentCenter[1] - previousCenter[1];
  const previousDistance = distance2(previousPair[0].local, previousPair[1].local);
  const currentDistance = distance2(currentPair[0].local, currentPair[1].local);
  if (previousDistance > 1 && currentDistance > 1) {
    zoomByLogDelta(Math.log(currentDistance / previousDistance));
  }
}
function midpoint2(a, b) {
  return [(a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5];
}
function distance2(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}
function onWheel(event) {
  event.preventDefault();
  lastPointer = localPoint(event);
  const delta = normalizedWheel(event);
  if (inputMode === 'trackpad') {
    if (event.metaKey) {
      let dx = delta[0];
      let dy = delta[1];
      if (event.shiftKey) {
        dx = dx + dy;
        dy = 0;
      }
      scheduleCommandScroll(dx, dy);
      return;
    }
    if (event.ctrlKey && !commandScrollSuppressZoom) {
      const pinchLogDelta = -delta[1] * 0.002;
      if (Math.abs(pinchLogDelta) > 0.001) {
        zoomByLogDelta(pinchLogDelta * 0.8);
        draw();
      }
      return;
    }
    if (Math.hypot(delta[0], delta[1]) > 0.1) {
      const fingerDelta = [-delta[0], -delta[1]];
      const base = lastPointer || [canvas.clientWidth * 0.5, canvas.clientHeight * 0.5];
      applyArcballWheelMotion(base, fingerDelta, 0.216);
      draw();
    }
    return;
  }
  if (event.ctrlKey) {
    const pinchLogDelta = -delta[1] * 0.002;
    if (Math.abs(pinchLogDelta) > 0.001) {
      zoomByLogDelta(pinchLogDelta * 0.8);
      draw();
    }
    return;
  }
  const wheelLogDelta = delta[1] * 0.0025;
  if (Math.abs(wheelLogDelta) > 0.0001) {
    zoomByLogDelta(wheelLogDelta);
    draw();
  }
}
function scheduleCommandScroll(dx, dy) {
  pendingCommandScroll[0] += dx;
  pendingCommandScroll[1] += dy;
  commandScrollSuppressZoom = true;
  if (commandScrollFrame) return;
  commandScrollFrame = requestAnimationFrame(() => {
    commandScrollFrame = 0;
    const panDelta = pendingCommandScroll;
    pendingCommandScroll = [0, 0];
    if (Math.hypot(panDelta[0], panDelta[1]) > 0.1) {
      camera.pan[0] += panDelta[0] * 0.95;
      camera.pan[1] += panDelta[1] * 0.95;
      draw();
    }
    commandScrollSuppressZoom = false;
  });
}
function normalizedWheel(event) {
  let scale = 1;
  if (event.deltaMode === 1) scale = 16;
  if (event.deltaMode === 2) scale = Math.max(canvas.clientHeight, 1);
  return [event.deltaX * scale, event.deltaY * scale];
}
function zoomByLogDelta(delta) {
  const clamped = clamp(delta, -MAX_ZOOM_LOG_DELTA_PER_INPUT, MAX_ZOOM_LOG_DELTA_PER_INPUT);
  camera.zoom = clamp(camera.zoom * Math.exp(clamped), MIN_ZOOM, MAX_ZOOM);
}
function resetCamera() {
  camera.orbitYawDegrees = 0;
  camera.orbitPitchDegrees = 0;
  camera.orientation = identity3();
  camera.pan = [0, 0];
  camera.zoom = 0.05;
  camera.viewCenter = [0, 0, 0];
  camera.viewScale = 1;
  camera.projection = 'Perspective';
}
function fitAll() {
  fitVisible(0.45);
}
function fitVisible(targetZoom) {
  const bounds = visibleBounds();
  camera.viewCenter = [
    (bounds.min[0] + bounds.max[0]) * 0.5,
    (bounds.min[1] + bounds.max[1]) * 0.5,
    (bounds.min[2] + bounds.max[2]) * 0.5
  ];
  const extent = Math.max(
    bounds.max[0] - bounds.min[0],
    bounds.max[1] - bounds.min[1],
    bounds.max[2] - bounds.min[2]
  );
  camera.viewScale = extent > 0 ? 1.6 / extent : 1.0;
  camera.pan = [0, 0];
  camera.zoom = targetZoom;
}
function applyAxisView(yaw, pitch) {
  camera.orbitYawDegrees = yaw;
  camera.orbitPitchDegrees = pitch;
  camera.orientation = orthonormalizeOrientation(orientationFromYawPitch(yaw, pitch));
  draw();
}
function visibleBounds() {
  const active = preparedMeshes.filter(mesh => visible[mesh.name]);
  const targets = active.length ? active : preparedMeshes;
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (const mesh of targets) {
    for (let i = 0; i < 3; i++) {
      min[i] = Math.min(min[i], mesh.bounds.min[i]);
      max[i] = Math.max(max[i], mesh.bounds.max[i]);
    }
  }
  if (!Number.isFinite(min[0])) return { min: [-1, -1, -1], max: [1, 1, 1] };
  return { min, max };
}
function localPoint(event) {
  const rect = canvas.getBoundingClientRect();
  return [event.clientX - rect.left, event.clientY - rect.top];
}
function arcballPoint(local) {
  const width = Math.max(canvas.clientWidth, 1);
  const height = Math.max(canvas.clientHeight, 1);
  const radius = Math.max(Math.min(width, height) * 0.5, 1);
  const x = (local[0] - width * 0.5) / radius;
  const y = (height * 0.5 - local[1]) / radius;
  const lengthSq = x * x + y * y;
  if (lengthSq <= 1) return [x, y, Math.sqrt(1 - lengthSq)];
  return normalize3([x, y, 0]);
}
function applyArcballDragMotion(fromLocal, toLocal, sensitivity) {
  applyArcballMotion(toLocal, toLocal[0] - fromLocal[0], toLocal[1] - fromLocal[1], sensitivity);
}
function applyArcballWheelMotion(baseLocal, motionDelta, sensitivity) {
  applyArcballMotion(baseLocal, motionDelta[0], motionDelta[1], sensitivity);
}
function applyArcballMotion(endLocal, dx, dy, sensitivity) {
  const distance = Math.hypot(dx, dy);
  if (distance < 1e-7) return;
  const steps = Math.max(1, Math.ceil(distance / MAX_ARCBALL_STEP_PX));
  const start = [endLocal[0] - dx, endLocal[1] - dy];
  let previousLocal = start;
  for (let i = 1; i <= steps; i++) {
    const currentLocal = [
      start[0] + dx * i / steps,
      start[1] + dy * i / steps
    ];
    const stepDx = currentLocal[0] - previousLocal[0];
    const stepDy = currentLocal[1] - previousLocal[1];
    const changed = applyArcballPoints(arcballPoint(previousLocal), arcballPoint(currentLocal), sensitivity);
    if (!changed) applyDirectedDelta(stepDx, stepDy, sensitivity);
    previousLocal = currentLocal;
  }
}
function applyArcballPoints(previous, current, sensitivity) {
  const axis = cross3(previous, current);
  const axisLength = length3(axis);
  const pointDot = dot3(previous, current);
  if (pointDot < -0.98) return false;
  if (axisLength < 1e-7) return false;
  const angle = Math.atan2(axisLength, clamp(pointDot, -1, 1)) * sensitivity;
  applyRotation(axisAngleMatrix(axis, angle));
  return true;
}
function applyDirectedDelta(dx, dy, sensitivity) {
  const directed = [dx, -dy];
  const axis = [-directed[1], directed[0], 0];
  const magnitude = Math.hypot(directed[0], directed[1]);
  if (magnitude < 1e-7) return;
  const angle = magnitude * sensitivity * Math.PI / 180;
  applyRotation(axisAngleMatrix(axis, angle));
}
function applyRotation(rotationMatrix) {
  camera.orientation = orthonormalizeOrientation(mat3Multiply(rotationMatrix, camera.orientation));
}
function initWebGl() {
  uintIndexExtension = gl.getExtension('OES_element_index_uint');
  program = makeProgram(vertexShaderSource(), fragmentShaderSource());
  attribs = {
    position: gl.getAttribLocation(program, 'aPosition'),
    normal: gl.getAttribLocation(program, 'aNormal')
  };
  uniforms = {
    orientation: gl.getUniformLocation(program, 'uOrientation'),
    viewCenter: gl.getUniformLocation(program, 'uViewCenter'),
    viewScale: gl.getUniformLocation(program, 'uViewScale'),
    zoom: gl.getUniformLocation(program, 'uZoom'),
    pan: gl.getUniformLocation(program, 'uPan'),
    viewportScale: gl.getUniformLocation(program, 'uViewportScale'),
    viewportSize: gl.getUniformLocation(program, 'uViewportSize'),
    depthNear: gl.getUniformLocation(program, 'uDepthNear'),
    depthFar: gl.getUniformLocation(program, 'uDepthFar'),
    color: gl.getUniformLocation(program, 'uColor'),
    opacity: gl.getUniformLocation(program, 'uOpacity'),
    specular: gl.getUniformLocation(program, 'uSpecular'),
    broadSpecular: gl.getUniformLocation(program, 'uBroadSpecular'),
    glazeStrength: gl.getUniformLocation(program, 'uGlazeStrength'),
    shininess: gl.getUniformLocation(program, 'uShininess'),
    ambient: gl.getUniformLocation(program, 'uAmbient'),
    diffuseBoost: gl.getUniformLocation(program, 'uDiffuseBoost'),
    rimStrength: gl.getUniformLocation(program, 'uRimStrength'),
    rimPower: gl.getUniformLocation(program, 'uRimPower'),
    warmth: gl.getUniformLocation(program, 'uWarmth'),
    wrapDiffuse: gl.getUniformLocation(program, 'uWrapDiffuse'),
    emission: gl.getUniformLocation(program, 'uEmission'),
    subsurface: gl.getUniformLocation(program, 'uSubsurface'),
    renderMode: gl.getUniformLocation(program, 'uRenderMode')
  };
  for (const mesh of preparedMeshes) uploadMesh(mesh);
  outlineProgram = makeProgram(outlineVertexShaderSource(), outlineFragmentShaderSource());
  outlineAttribs = {
    position: gl.getAttribLocation(outlineProgram, 'aPosition')
  };
  outlineUniforms = {
    mask: gl.getUniformLocation(outlineProgram, 'uMask'),
    texelSize: gl.getUniformLocation(outlineProgram, 'uTexelSize')
  };
  outlineResources = createOutlineResources();
  gridResources = createGridResources();
  gl.enable(gl.DEPTH_TEST);
  gl.disable(gl.CULL_FACE);
}
function prepareMesh(raw) {
  const baseRgb = hexToRgb(raw.color);
  const baseOpacity = raw.opacity;
  const material = materialFor(raw.name, baseRgb, baseOpacity, currentMaterialMode);
  const mesh = { raw, name: raw.name, labels: raw.labels, faces: [], baseRgb, baseOpacity, baseVertices: new Float32Array(0), vertices: new Float32Array(0), normals: new Float32Array(0), bounds: null, material, webgl: null, adjacency: null, layerCountNode: null };
  applyRawGeometry(mesh, geometryForPreset(raw, currentGeometryPreset));
  return mesh;
}
function computeVertexNormals(vertices, faces) {
  const normals = new Float32Array(vertices.length);
  for (const face of faces) {
    const ia = face[0] * 3;
    const ib = face[1] * 3;
    const ic = face[2] * 3;
    const normal = normalize3(cross3(
      sub3(vertexAt(vertices, face[1]), vertexAt(vertices, face[0])),
      sub3(vertexAt(vertices, face[2]), vertexAt(vertices, face[0]))
    ));
    for (const idx of [ia, ib, ic]) {
      normals[idx] += normal[0];
      normals[idx + 1] += normal[1];
      normals[idx + 2] += normal[2];
    }
  }
  for (let i = 0; i < normals.length; i += 3) {
    const n = normalize3([normals[i], normals[i + 1], normals[i + 2]]);
    normals[i] = n[0];
    normals[i + 1] = n[1];
    normals[i + 2] = n[2];
  }
  return normals;
}
function computeBounds(vertices) {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < vertices.length; i += 3) {
    min[0] = Math.min(min[0], vertices[i]); max[0] = Math.max(max[0], vertices[i]);
    min[1] = Math.min(min[1], vertices[i + 1]); max[1] = Math.max(max[1], vertices[i + 1]);
    min[2] = Math.min(min[2], vertices[i + 2]); max[2] = Math.max(max[2], vertices[i + 2]);
  }
  return { min, max };
}
function uploadMesh(mesh) {
  const positionBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, mesh.vertices, gl.DYNAMIC_DRAW);
  const normalBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, normalBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, mesh.normals, gl.DYNAMIC_DRAW);
  if (mesh.vertices.length / 3 <= 65535 || uintIndexExtension) {
    const indexArray = mesh.vertices.length / 3 > 65535
      ? new Uint32Array(mesh.faces.length * 3)
      : new Uint16Array(mesh.faces.length * 3);
    for (let i = 0; i < mesh.faces.length; i++) indexArray.set(mesh.faces[i], i * 3);
    const indexBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuffer);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indexArray, gl.STATIC_DRAW);
    mesh.webgl = {
      positionBuffer,
      normalBuffer,
      wirePositionBuffer: null,
      wireNormalBuffer: null,
      wireDrawCount: 0,
      indexBuffer,
      indexType: indexArray instanceof Uint32Array ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT,
      drawCount: indexArray.length,
      drawArrays: false
    };
    return;
  }
  const expanded = expandFaces(mesh.vertices, mesh.normals, mesh.faces);
  gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, expanded.vertices, gl.DYNAMIC_DRAW);
  gl.bindBuffer(gl.ARRAY_BUFFER, normalBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, expanded.normals, gl.DYNAMIC_DRAW);
  mesh.webgl = {
    positionBuffer,
    normalBuffer,
    wirePositionBuffer: null,
    wireNormalBuffer: null,
    wireDrawCount: 0,
    indexBuffer: null,
    indexType: null,
    drawCount: expanded.vertices.length / 3,
    drawArrays: true
  };
}
function refreshMeshBuffers(mesh) {
  if (!gl || !mesh.webgl) return;
  if (mesh.webgl.wirePositionBuffer) refreshWireframeBuffers(mesh);
  if (mesh.webgl.drawArrays) {
    const expanded = expandFaces(mesh.vertices, mesh.normals, mesh.faces);
    gl.bindBuffer(gl.ARRAY_BUFFER, mesh.webgl.positionBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, expanded.vertices, gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, mesh.webgl.normalBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, expanded.normals, gl.DYNAMIC_DRAW);
    mesh.webgl.drawCount = expanded.vertices.length / 3;
    return;
  }
  gl.bindBuffer(gl.ARRAY_BUFFER, mesh.webgl.positionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, mesh.vertices, gl.DYNAMIC_DRAW);
  gl.bindBuffer(gl.ARRAY_BUFFER, mesh.webgl.normalBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, mesh.normals, gl.DYNAMIC_DRAW);
}
function rebuildMeshBuffers(mesh) {
  if (!gl) return;
  if (mesh.webgl) {
    gl.deleteBuffer(mesh.webgl.positionBuffer);
    gl.deleteBuffer(mesh.webgl.normalBuffer);
    if (mesh.webgl.wirePositionBuffer) gl.deleteBuffer(mesh.webgl.wirePositionBuffer);
    if (mesh.webgl.wireNormalBuffer) gl.deleteBuffer(mesh.webgl.wireNormalBuffer);
    if (mesh.webgl.indexBuffer) gl.deleteBuffer(mesh.webgl.indexBuffer);
  }
  mesh.webgl = null;
  uploadMesh(mesh);
}
function expandFaces(vertices, normals, faces) {
  const outVertices = new Float32Array(faces.length * 9);
  const outNormals = new Float32Array(faces.length * 9);
  for (let i = 0; i < faces.length; i++) {
    for (let j = 0; j < 3; j++) {
      const source = faces[i][j] * 3;
      const target = i * 9 + j * 3;
      outVertices.set(vertices.subarray(source, source + 3), target);
      outNormals.set(normals.subarray(source, source + 3), target);
    }
  }
  return { vertices: outVertices, normals: outNormals };
}
function expandWireframe(vertices, normals, faces) {
  const outVertices = new Float32Array(faces.length * 18);
  const outNormals = new Float32Array(faces.length * 18);
  const edgeOrder = [0, 1, 1, 2, 2, 0];
  for (let i = 0; i < faces.length; i++) {
    for (let j = 0; j < edgeOrder.length; j++) {
      const source = faces[i][edgeOrder[j]] * 3;
      const target = i * 18 + j * 3;
      outVertices.set(vertices.subarray(source, source + 3), target);
      outNormals.set(normals.subarray(source, source + 3), target);
    }
  }
  return { vertices: outVertices, normals: outNormals };
}
function ensureWireframeBuffers(mesh) {
  if (mesh.webgl.wirePositionBuffer) return;
  mesh.webgl.wirePositionBuffer = gl.createBuffer();
  mesh.webgl.wireNormalBuffer = gl.createBuffer();
  refreshWireframeBuffers(mesh);
}
function refreshWireframeBuffers(mesh) {
  const wireframe = expandWireframe(mesh.vertices, mesh.normals, mesh.faces);
  gl.bindBuffer(gl.ARRAY_BUFFER, mesh.webgl.wirePositionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, wireframe.vertices, gl.DYNAMIC_DRAW);
  gl.bindBuffer(gl.ARRAY_BUFFER, mesh.webgl.wireNormalBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, wireframe.normals, gl.DYNAMIC_DRAW);
  mesh.webgl.wireDrawCount = wireframe.vertices.length / 3;
}
function draw() {
  if (gl && program) drawWebGl();
  else drawFallback2d();
}
function drawWebGl() {
  if (currentDisplayMode === 'xray') {
    gl.clearColor(0.1921569, 0.2039216, 0.1960784, 1.0);
  } else {
    gl.clearColor(0.067, 0.075, 0.090, 1.0);
  }
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.useProgram(program);
  const minSide = Math.min(canvas.width, canvas.height);
  const viewportScale = [minSide / canvas.width, minSide / canvas.height];
  const visibleMeshes = preparedMeshes.filter(mesh => visible[mesh.name]);
  const depthRange = viewDepthRange(visibleMeshes);
  applySceneUniforms(viewportScale, depthRange);
  const xrayTargets = currentDisplayMode === 'xray'
    ? selectXrayTargets(visibleMeshes)
    : new Set();
  const normallyRendered = currentDisplayMode === 'wireframe'
    ? []
    : visibleMeshes.filter(mesh => !xrayTargets.has(mesh));
  const opaque = normallyRendered.filter(mesh => mesh.material.opacity >= 0.995);
  const frontShellTranslucent = normallyRendered.filter(mesh => usesFrontShellTransparency(mesh));
  const classicTranslucent = normallyRendered.filter(
    mesh => mesh.material.opacity < 0.995 && !usesFrontShellTransparency(mesh)
  );
  const wireframe = currentDisplayMode === 'wireframe' ? visibleMeshes : [];
  const xray = currentDisplayMode === 'xray'
    ? visibleMeshes.filter(mesh => xrayTargets.has(mesh))
    : [];

  // Render order: opaque -> depth-aware grid -> X-ray back/front ->
  // physical-pixel outline -> selection/gizmo overlay.
  gl.enable(gl.DEPTH_TEST);
  gl.depthFunc(gl.LEQUAL);
  gl.colorMask(true, true, true, true);
  gl.disable(gl.BLEND);
  gl.disable(gl.CULL_FACE);
  gl.depthMask(true);
  for (const mesh of opaque) drawMeshWebGl(mesh, 0);
  drawTranslucentDepthPrepass(frontShellTranslucent);
  drawTranslucentFrontShell(frontShellTranslucent);
  drawClassicTranslucent(classicTranslucent);
  drawWireframeMeshes(wireframe);
  drawDepthAwareGrid(xray.length > 0);
  drawXrayShells(xray);
  if (xray.length) {
    drawXrayOutlineMask(opaque, xray);
    compositeXrayOutline();
  }
  drawSelectionAndGizmoOverlay();
  gl.useProgram(program);
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.colorMask(true, true, true, true);
  gl.depthFunc(gl.LEQUAL);
  gl.depthMask(true);
  gl.disable(gl.BLEND);
  gl.disable(gl.CULL_FACE);
}
function applySceneUniforms(viewportScale, depthRange) {
  gl.uniformMatrix3fv(uniforms.orientation, false, new Float32Array(columnMajorMat3(camera.orientation)));
  gl.uniform3fv(uniforms.viewCenter, new Float32Array(camera.viewCenter));
  gl.uniform1f(uniforms.viewScale, camera.viewScale);
  gl.uniform1f(uniforms.zoom, camera.zoom);
  gl.uniform2fv(uniforms.pan, new Float32Array(camera.pan));
  gl.uniform2fv(uniforms.viewportScale, new Float32Array(viewportScale));
  gl.uniform2fv(uniforms.viewportSize, new Float32Array([canvas.width, canvas.height]));
  gl.uniform1f(uniforms.depthNear, depthRange.near);
  gl.uniform1f(uniforms.depthFar, depthRange.far);
}
function usesFrontShellTransparency(mesh) {
  return mesh.name === 'jaws' && mesh.material.opacity < 0.995;
}
function selectXrayTargets(meshes) {
  const translucent = meshes.filter(mesh => mesh.material.opacity < 0.995);
  return new Set(translucent.length ? translucent : meshes);
}
function drawTranslucentDepthPrepass(meshes) {
  if (!meshes.length) return;
  gl.disable(gl.BLEND);
  gl.depthFunc(gl.LEQUAL);
  gl.depthMask(true);
  gl.colorMask(false, false, false, false);
  for (const mesh of meshes) drawMeshWebGl(mesh, 0);
  gl.colorMask(true, true, true, true);
}
function drawTranslucentFrontShell(meshes) {
  if (!meshes.length) return;
  gl.enable(gl.BLEND);
  gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  gl.depthFunc(gl.LEQUAL);
  gl.depthMask(false);
  for (const mesh of meshes) drawMeshWebGl(mesh, 0);
  gl.depthMask(true);
  gl.disable(gl.BLEND);
}
function drawClassicTranslucent(meshes) {
  if (!meshes.length) return;
  gl.enable(gl.BLEND);
  gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  gl.depthFunc(gl.LEQUAL);
  gl.depthMask(false);
  for (const mesh of meshes) drawMeshWebGl(mesh, 0);
  gl.depthMask(true);
  gl.disable(gl.BLEND);
}
function drawWireframeMeshes(meshes) {
  if (!meshes.length) return;
  gl.disable(gl.BLEND);
  gl.disable(gl.CULL_FACE);
  gl.depthFunc(gl.LEQUAL);
  gl.depthMask(true);
  for (const mesh of meshes) {
    ensureWireframeBuffers(mesh);
    drawMeshWebGl(mesh, 2, true);
  }
}
function drawXrayShells(meshes) {
  if (!meshes.length) return;
  gl.enable(gl.DEPTH_TEST);
  gl.depthFunc(gl.LEQUAL);
  gl.depthMask(false);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.enable(gl.CULL_FACE);
  gl.cullFace(gl.FRONT);
  for (const mesh of meshes) drawMeshWebGl(mesh, 1);
  gl.cullFace(gl.BACK);
  for (const mesh of meshes) drawMeshWebGl(mesh, 1);
  gl.disable(gl.CULL_FACE);
  gl.depthMask(true);
  gl.disable(gl.BLEND);
}
function drawMeshWebGl(mesh, renderMode, wireframe = false) {
  gl.bindBuffer(gl.ARRAY_BUFFER, wireframe ? mesh.webgl.wirePositionBuffer : mesh.webgl.positionBuffer);
  gl.enableVertexAttribArray(attribs.position);
  gl.vertexAttribPointer(attribs.position, 3, gl.FLOAT, false, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, wireframe ? mesh.webgl.wireNormalBuffer : mesh.webgl.normalBuffer);
  gl.enableVertexAttribArray(attribs.normal);
  gl.vertexAttribPointer(attribs.normal, 3, gl.FLOAT, false, 0, 0);
  gl.uniform1i(uniforms.renderMode, renderMode);
  gl.uniform3fv(uniforms.color, new Float32Array(mesh.material.color));
  gl.uniform1f(uniforms.opacity, mesh.material.opacity);
  gl.uniform1f(uniforms.specular, mesh.material.specular);
  gl.uniform1f(uniforms.broadSpecular, mesh.material.broadSpecular);
  gl.uniform1f(uniforms.glazeStrength, mesh.material.glazeStrength);
  gl.uniform1f(uniforms.shininess, mesh.material.shininess);
  gl.uniform1f(uniforms.ambient, mesh.material.ambient);
  gl.uniform1f(uniforms.diffuseBoost, mesh.material.diffuseBoost);
  gl.uniform1f(uniforms.rimStrength, mesh.material.rimStrength);
  gl.uniform1f(uniforms.rimPower, mesh.material.rimPower);
  gl.uniform1f(uniforms.warmth, mesh.material.warmth);
  gl.uniform1f(uniforms.wrapDiffuse, mesh.material.wrapDiffuse);
  gl.uniform1f(uniforms.emission, mesh.material.emission);
  gl.uniform1f(uniforms.subsurface, mesh.material.subsurface);
  if (wireframe) {
    gl.drawArrays(gl.LINES, 0, mesh.webgl.wireDrawCount);
    return;
  }
  if (mesh.webgl.drawArrays) {
    gl.drawArrays(gl.TRIANGLES, 0, mesh.webgl.drawCount);
  } else {
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, mesh.webgl.indexBuffer);
    gl.drawElements(gl.TRIANGLES, mesh.webgl.drawCount, mesh.webgl.indexType, 0);
  }
}
function createGridResources() {
  const bounds = visibleBounds();
  const extentX = Math.max(bounds.max[0] - bounds.min[0], 1);
  const extentY = Math.max(bounds.max[1] - bounds.min[1], 1);
  const margin = Math.max(extentX, extentY) * 0.18;
  const minX = bounds.min[0] - margin;
  const maxX = bounds.max[0] + margin;
  const minY = bounds.min[1] - margin;
  const maxY = bounds.max[1] + margin;
  const z = bounds.min[2] - Math.max(extentX, extentY) * 0.02;
  const vertices = [];
  const normals = [];
  const divisions = 20;
  for (let i = 0; i <= divisions; i++) {
    const t = i / divisions;
    const x = minX + (maxX - minX) * t;
    const y = minY + (maxY - minY) * t;
    vertices.push(x, minY, z, x, maxY, z, minX, y, z, maxX, y, z);
    for (let j = 0; j < 4; j++) normals.push(0, 0, 1);
  }
  const positionBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vertices), gl.STATIC_DRAW);
  const normalBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, normalBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(normals), gl.STATIC_DRAW);
  return { positionBuffer, normalBuffer, drawCount: vertices.length / 3 };
}
function drawDepthAwareGrid(enabled) {
  if (!enabled || !gridResources) return;
  gl.enable(gl.DEPTH_TEST);
  gl.depthFunc(gl.LEQUAL);
  gl.depthMask(false);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.bindBuffer(gl.ARRAY_BUFFER, gridResources.positionBuffer);
  gl.enableVertexAttribArray(attribs.position);
  gl.vertexAttribPointer(attribs.position, 3, gl.FLOAT, false, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, gridResources.normalBuffer);
  gl.enableVertexAttribArray(attribs.normal);
  gl.vertexAttribPointer(attribs.normal, 3, gl.FLOAT, false, 0, 0);
  gl.uniform1i(uniforms.renderMode, 2);
  gl.uniform3fv(uniforms.color, new Float32Array([0.72, 0.75, 0.73]));
  gl.uniform1f(uniforms.opacity, 0.16);
  gl.drawArrays(gl.LINES, 0, gridResources.drawCount);
  gl.depthMask(true);
  gl.disable(gl.BLEND);
}
function createOutlineResources() {
  const framebuffer = gl.createFramebuffer();
  const texture = gl.createTexture();
  const depth = gl.createRenderbuffer();
  const quadBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, quadBuffer);
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
    gl.STATIC_DRAW
  );
  return { framebuffer, texture, depth, quadBuffer, width: 0, height: 0 };
}
function resizeOutlineResources() {
  if (!outlineResources) return;
  if (outlineResources.width === canvas.width && outlineResources.height === canvas.height) return;
  outlineResources.width = canvas.width;
  outlineResources.height = canvas.height;
  gl.bindTexture(gl.TEXTURE_2D, outlineResources.texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texImage2D(
    gl.TEXTURE_2D,
    0,
    gl.RGBA,
    canvas.width,
    canvas.height,
    0,
    gl.RGBA,
    gl.UNSIGNED_BYTE,
    null
  );
  gl.bindRenderbuffer(gl.RENDERBUFFER, outlineResources.depth);
  gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT16, canvas.width, canvas.height);
  gl.bindFramebuffer(gl.FRAMEBUFFER, outlineResources.framebuffer);
  gl.framebufferTexture2D(
    gl.FRAMEBUFFER,
    gl.COLOR_ATTACHMENT0,
    gl.TEXTURE_2D,
    outlineResources.texture,
    0
  );
  gl.framebufferRenderbuffer(
    gl.FRAMEBUFFER,
    gl.DEPTH_ATTACHMENT,
    gl.RENDERBUFFER,
    outlineResources.depth
  );
  if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
    throw new Error('X-ray outline framebuffer is incomplete');
  }
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
}
function drawXrayOutlineMask(opaqueMeshes, xrayMeshes) {
  gl.bindFramebuffer(gl.FRAMEBUFFER, outlineResources.framebuffer);
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clearColor(0.0, 0.0, 0.0, 0.0);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.useProgram(program);
  gl.enable(gl.DEPTH_TEST);
  gl.depthFunc(gl.LEQUAL);
  gl.disable(gl.BLEND);
  gl.disable(gl.CULL_FACE);
  gl.depthMask(true);
  gl.colorMask(false, false, false, false);
  for (const mesh of opaqueMeshes) drawMeshWebGl(mesh, 3);
  gl.colorMask(true, true, true, true);
  gl.depthMask(false);
  for (const mesh of xrayMeshes) drawMeshWebGl(mesh, 3);
  gl.depthMask(true);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  gl.viewport(0, 0, canvas.width, canvas.height);
}
function compositeXrayOutline() {
  gl.useProgram(outlineProgram);
  gl.disable(gl.DEPTH_TEST);
  gl.depthMask(false);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, outlineResources.texture);
  gl.uniform1i(outlineUniforms.mask, 0);
  gl.uniform2f(outlineUniforms.texelSize, 1 / canvas.width, 1 / canvas.height);
  gl.bindBuffer(gl.ARRAY_BUFFER, outlineResources.quadBuffer);
  gl.enableVertexAttribArray(outlineAttribs.position);
  gl.vertexAttribPointer(outlineAttribs.position, 2, gl.FLOAT, false, 0, 0);
  gl.drawArrays(gl.TRIANGLES, 0, 6);
  gl.disable(gl.BLEND);
  gl.depthMask(true);
}
function drawSelectionAndGizmoOverlay() {
  // This release has no selection/gizmo implementation. Keep this final pass
  // explicit so those overlays remain above X-ray compositing when introduced.
}
function drawFallback2d() {
  ctx2d.fillStyle = currentDisplayMode === 'xray' ? '#313432' : '#111317';
  ctx2d.fillRect(0, 0, canvas.width, canvas.height);
  const tris = [];
  const visibleMeshes = preparedMeshes.filter(mesh => visible[mesh.name]);
  const fallbackXrayTargets = currentDisplayMode === 'xray'
    ? selectXrayTargets(visibleMeshes)
    : new Set();
  const depthRange = viewDepthRange(visibleMeshes);
  for (const mesh of visibleMeshes) {
    for (const face of mesh.faces) {
      const a = worldToScreen(vertexAt(mesh.vertices, face[0]), depthRange);
      const b = worldToScreen(vertexAt(mesh.vertices, face[1]), depthRange);
      const c = worldToScreen(vertexAt(mesh.vertices, face[2]), depthRange);
      const depth = (a[2] + b[2] + c[2]) / 3;
      const worldNormal = normalize3(cross3(
        sub3(vertexAt(mesh.vertices, face[1]), vertexAt(mesh.vertices, face[0])),
        sub3(vertexAt(mesh.vertices, face[2]), vertexAt(mesh.vertices, face[0]))
      ));
      const viewNormal = normalize3(mat3Vec(camera.orientation, worldNormal));
      const facing = Math.abs(viewNormal[2]);
      const renderMode = currentDisplayMode === 'wireframe'
        ? 'wireframe'
        : (fallbackXrayTargets.has(mesh) ? 'xray' : 'normal');
      tris.push({ a, b, c, depth, material: mesh.material, facing, viewNormal, renderMode });
    }
  }
  tris.sort((p, q) => q.depth - p.depth);
  for (const t of tris) {
    const keyLight = normalize3([0.30, 0.68, 0.64]);
    const fillLight = normalize3([-0.58, 0.14, 0.80]);
    const viewNormal = t.viewNormal[2] < 0 ? scale3(t.viewNormal, -1) : t.viewNormal;
    const keyDiffuse = Math.max(dot3(viewNormal, keyLight), 0);
    const fillDiffuse = Math.max(dot3(viewNormal, fillLight), 0);
    const sharpHighlight = Math.pow(
      Math.max(dot3(viewNormal, normalize3([keyLight[0], keyLight[1], keyLight[2] + 1])), 0),
      Math.max(t.material.shininess * 0.30, 12)
    ) * t.material.specular * 1.15;
    const broadHighlight = Math.pow(
      Math.max(dot3(viewNormal, normalize3([fillLight[0], fillLight[1], fillLight[2] + 1])), 0),
      Math.max(t.material.shininess * 0.06, 2)
    ) * (t.material.broadSpecular || 0);
    const viewDot = clamp(viewNormal[2], 0, 1);
    const reflection = normalize3([
      2 * viewDot * viewNormal[0],
      2 * viewDot * viewNormal[1],
      2 * viewDot * viewNormal[2] - 1
    ]);
    const leftStudioBand = Math.pow(
      clamp(1 - Math.abs(reflection[0] + 0.38) / 0.38, 0, 1),
      3
    ) * clamp(1 - Math.abs(reflection[1] - 0.16) / 0.78, 0, 1);
    const rightStudioBand = Math.pow(
      clamp(1 - Math.abs(reflection[0] - 0.46) / 0.28, 0, 1),
      3
    ) * clamp(1 - Math.abs(reflection[1] - 0.02) / 0.62, 0, 1);
    const ceilingReflection = Math.pow(clamp((reflection[1] - 0.48) / 0.52, 0, 1), 2) * 0.18;
    const floorShadow = 1 - Math.pow(clamp((-reflection[1] - 0.18) / 0.82, 0, 1), 2) * 0.62;
    const glazeFresnel = 0.18 + 0.82 * Math.pow(1 - viewDot, 2.5);
    const studioLevel = (
      0.055
      + leftStudioBand * 0.90
      + rightStudioBand * 0.58
      + ceilingReflection
    ) * floorShadow;
    const glazeMix = clamp(
      (t.material.glazeStrength || 0) * glazeFresnel,
      0,
      0.42
    );
    const porcelainRim = Math.pow(clamp(1 - t.facing, 0, 1), 1.55)
      * ((t.material.rimStrength || 0) + (t.material.subsurface || 0));
    const materialLift = (t.material.emission || 0) * 1.2 + porcelainRim;
    const baseShade = Math.max(
      0.40,
      Math.min(
        1.22,
        t.material.ambient
          + keyDiffuse * 0.72 * t.material.diffuseBoost
          + fillDiffuse * 0.24
          + sharpHighlight
          + broadHighlight
          + materialLift
      )
    );
    const shade = clamp(
      baseShade * (1 - glazeMix) + studioLevel * 1.18 * glazeMix,
      0.32,
      1.18
    );
    const rgb = t.material.color.map(v => Math.round(v * 255 * shade));
    ctx2d.beginPath();
    ctx2d.moveTo(t.a[0], t.a[1]);
    ctx2d.lineTo(t.b[0], t.b[1]);
    ctx2d.lineTo(t.c[0], t.c[1]);
    ctx2d.closePath();
    if (t.renderMode === 'wireframe') {
      ctx2d.strokeStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},1)`;
      ctx2d.lineWidth = Math.max(devicePixelRatio, 1);
      ctx2d.stroke();
    } else if (t.renderMode === 'xray') {
      const rim = Math.pow(clamp(1 - t.facing, 0, 1), 2);
      const xray = Math.round((0.78 + 0.22 * rim) * 255);
      const alpha = clamp(0.18 * (0.55 + (3.0 - 0.55) * rim), 0, 0.62);
      ctx2d.fillStyle = `rgba(${xray},${xray},${xray},${alpha})`;
      ctx2d.fill();
      ctx2d.strokeStyle = 'rgba(255,255,255,0.58)';
      ctx2d.lineWidth = Math.max(devicePixelRatio, 1);
      ctx2d.stroke();
    } else {
      ctx2d.fillStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${t.material.opacity})`;
      ctx2d.fill();
    }
  }
}
function worldToScreen(world, depthRange) {
  const view = worldToView(world);
  const minSide = Math.min(canvas.width, canvas.height);
  const viewportScale = [minSide / canvas.width, minSide / canvas.height];
  const panClip = [camera.pan[0] / canvas.width * 2, -camera.pan[1] / canvas.height * 2];
  const clipX = view[0] * camera.zoom * viewportScale[0] + panClip[0];
  const clipY = view[1] * camera.zoom * viewportScale[1] + panClip[1];
  return [
    (clipX + 1) * 0.5 * canvas.width,
    (1 - clipY) * 0.5 * canvas.height,
    viewDepth01(view[2], depthRange)
  ];
}
function worldToView(world) {
  const display = [
    (world[0] - camera.viewCenter[0]) * camera.viewScale,
    (world[1] - camera.viewCenter[1]) * camera.viewScale,
    (world[2] - camera.viewCenter[2]) * camera.viewScale
  ];
  return mat3Vec(camera.orientation, display);
}
function viewDepthRange(meshes) {
  const targets = meshes.length ? meshes : preparedMeshes;
  let minZ = Infinity;
  let maxZ = -Infinity;
  for (const mesh of targets) {
    for (const corner of boundsCorners(mesh.bounds)) {
      const z = worldToView(corner)[2];
      minZ = Math.min(minZ, z);
      maxZ = Math.max(maxZ, z);
    }
  }
  if (!Number.isFinite(minZ) || !Number.isFinite(maxZ)) return { near: 1, far: -1 };
  const span = Math.max(maxZ - minZ, 0.0001);
  const margin = span * 0.08;
  return { near: maxZ + margin, far: minZ - margin };
}
function boundsCorners(bounds) {
  const min = bounds.min;
  const max = bounds.max;
  return [
    [min[0], min[1], min[2]], [min[0], min[1], max[2]],
    [min[0], max[1], min[2]], [min[0], max[1], max[2]],
    [max[0], min[1], min[2]], [max[0], min[1], max[2]],
    [max[0], max[1], min[2]], [max[0], max[1], max[2]]
  ];
}
function viewDepth01(viewZ, depthRange) {
  const span = Math.max(depthRange.near - depthRange.far, 0.0001);
  return clamp((depthRange.near - viewZ) / span * 0.96 + 0.02, 0.02, 0.98);
}
function vertexAt(vertices, index) {
  const offset = index * 3;
  return [vertices[offset], vertices[offset + 1], vertices[offset + 2]];
}
function vertexShaderSource() {
  return `
attribute vec3 aPosition;
attribute vec3 aNormal;
uniform mat3 uOrientation;
uniform vec3 uViewCenter;
uniform float uViewScale;
uniform float uZoom;
uniform vec2 uPan;
uniform vec2 uViewportScale;
uniform vec2 uViewportSize;
uniform float uDepthNear;
uniform float uDepthFar;
varying vec3 vNormal;
varying vec3 vView;
void main() {
  vec3 display = (aPosition - uViewCenter) * uViewScale;
  vec3 view = uOrientation * display;
  vec2 panClip = vec2(uPan.x / uViewportSize.x * 2.0, -uPan.y / uViewportSize.y * 2.0);
  vec2 clipXY = view.xy * uZoom * uViewportScale + panClip;
  float depth01 = (uDepthNear - view.z) / max(uDepthNear - uDepthFar, 0.0001);
  float depth = clamp(depth01 * 0.96 + 0.02, 0.02, 0.98) * 2.0 - 1.0;
  gl_Position = vec4(clipXY, depth, 1.0);
  vNormal = normalize(uOrientation * aNormal);
  vView = view;
}`;
}
function fragmentShaderSource() {
  return `
precision mediump float;
uniform vec3 uColor;
uniform float uOpacity;
uniform float uSpecular;
uniform float uBroadSpecular;
uniform float uGlazeStrength;
uniform float uShininess;
uniform float uAmbient;
uniform float uDiffuseBoost;
uniform float uRimStrength;
uniform float uRimPower;
uniform float uWarmth;
uniform float uWrapDiffuse;
uniform float uEmission;
uniform float uSubsurface;
uniform int uRenderMode;
varying vec3 vNormal;
varying vec3 vView;
float studioSoftBox(vec2 direction, vec2 center, vec2 halfSize, float feather) {
  vec2 outside = max(abs(direction - center) - halfSize, vec2(0.0));
  return 1.0 - smoothstep(0.0, feather, length(outside));
}
vec3 proceduralStudio(vec3 reflectionDirection) {
  vec3 studio = vec3(0.050, 0.056, 0.064);
  float leftSoftBox = studioSoftBox(
    reflectionDirection.xy,
    vec2(-0.38, 0.16),
    vec2(0.19, 0.54),
    0.18
  );
  float rightSoftBox = studioSoftBox(
    reflectionDirection.xy,
    vec2(0.46, 0.02),
    vec2(0.12, 0.40),
    0.14
  );
  float ceiling = smoothstep(0.48, 0.96, reflectionDirection.y);
  float floorMask = 1.0 - smoothstep(-0.82, -0.18, reflectionDirection.y);
  studio += vec3(1.00, 0.965, 0.90) * leftSoftBox * 0.92;
  studio += vec3(0.88, 0.94, 1.00) * rightSoftBox * 0.58;
  studio += vec3(0.72, 0.79, 0.88) * ceiling * 0.16;
  studio *= mix(1.0, 0.38, floorMask);
  return studio;
}
void main() {
  if (uRenderMode == 1) {
    float facing = abs(normalize(vNormal).z);
    float rim = pow(clamp(1.0 - facing, 0.0, 1.0), 2.0);
    vec3 xrayColor = mix(vec3(0.78), vec3(1.0), rim);
    float xrayAlpha = clamp(0.18 * mix(0.55, 3.0, rim), 0.0, 0.62);
    gl_FragColor = vec4(xrayColor, xrayAlpha);
    return;
  }
  if (uRenderMode == 2) {
    gl_FragColor = vec4(uColor, uOpacity);
    return;
  }
  if (uRenderMode == 3) {
    gl_FragColor = vec4(1.0);
    return;
  }
  vec3 normal = normalize(vNormal);
  if (!gl_FrontFacing) normal = -normal;
  vec3 keyLight = normalize(vec3(0.30, 0.68, 0.64));
  vec3 fillLight = normalize(vec3(-0.58, 0.14, 0.80));
  vec3 viewDir = normalize(vec3(0.0, 0.0, 1.0));
  float viewFacing = max(dot(normal, viewDir), 0.0);
  float keyDiffuse = max((dot(normal, keyLight) + uWrapDiffuse) / (1.0 + uWrapDiffuse), 0.0);
  float fillDiffuse = max(dot(normal, fillLight), 0.0);
  float diffuse = uAmbient
    + keyDiffuse * 0.74 * uDiffuseBoost
    + fillDiffuse * 0.26;
  vec3 keyHalfDir = normalize(keyLight + viewDir);
  vec3 fillHalfDir = normalize(fillLight + viewDir);
  float sharpSpecBase = max(dot(normal, keyHalfDir), 0.0);
  float broadSpecBase = max(dot(normal, fillHalfDir), 0.0);
  float sharpSpec = pow(sharpSpecBase, uShininess) * uSpecular;
  float broadSpec = pow(broadSpecBase, max(uShininess * 0.12, 2.0)) * uBroadSpecular;
  float rim = pow(1.0 - viewFacing, uRimPower) * uRimStrength;
  float subsurface = pow(1.0 - viewFacing, 1.55) * uSubsurface;
  vec3 reflectionDirection = normalize(reflect(-viewDir, normal));
  float glazeFresnel = 0.18 + 0.82 * pow(1.0 - viewFacing, 2.5);
  float glazeMix = clamp(uGlazeStrength * glazeFresnel, 0.0, 0.34);
  vec3 studioSample = proceduralStudio(reflectionDirection);
  vec3 warmColor = mix(uColor, vec3(1.0, 0.92, 0.78), clamp(uWarmth, 0.0, 1.0) * 0.24);
  vec3 coolFill = vec3(0.55, 0.68, 0.95) * fillDiffuse * 0.020;
  vec3 highlight = vec3(sharpSpec)
    + vec3(1.0, 0.96, 0.88) * broadSpec;
  vec3 glow = warmColor * rim + vec3(1.0, 0.91, 0.78) * subsurface + warmColor * uEmission;
  vec3 color = warmColor * diffuse + highlight + glow + coolFill;
  color = color / (color + vec3(0.48));
  color = min(color * 1.28, vec3(1.0));
  float studioLuma = dot(studioSample, vec3(0.2126, 0.7152, 0.0722));
  float softboxPresence = smoothstep(0.16, 0.72, studioLuma);
  vec3 porcelainReflection = mix(
    color * 0.88,
    vec3(1.0, 0.985, 0.94),
    softboxPresence
  );
  color = mix(color, porcelainReflection, glazeMix);
  float crispHighlight = clamp(
    sharpSpec * 1.35 * step(0.001, uGlazeStrength),
    0.0,
    0.82
  );
  color = min(color + vec3(crispHighlight), vec3(1.0));
  gl_FragColor = vec4(color, uOpacity);
}`;
}
function outlineVertexShaderSource() {
  return `
attribute vec2 aPosition;
varying vec2 vUv;
void main() {
  vUv = aPosition * 0.5 + 0.5;
  gl_Position = vec4(aPosition, 0.0, 1.0);
}`;
}
function outlineFragmentShaderSource() {
  return `
precision mediump float;
uniform sampler2D uMask;
uniform vec2 uTexelSize;
varying vec2 vUv;
void main() {
  float center = texture2D(uMask, vUv).r;
  float neighbor = 0.0;
  neighbor = max(neighbor, texture2D(uMask, vUv + vec2(uTexelSize.x, 0.0)).r);
  neighbor = max(neighbor, texture2D(uMask, vUv - vec2(uTexelSize.x, 0.0)).r);
  neighbor = max(neighbor, texture2D(uMask, vUv + vec2(0.0, uTexelSize.y)).r);
  neighbor = max(neighbor, texture2D(uMask, vUv - vec2(0.0, uTexelSize.y)).r);
  float edge = (1.0 - center) * neighbor;
  gl_FragColor = vec4(1.0, 1.0, 1.0, edge * 0.58);
}`;
}
function makeProgram(vertexSource, fragmentSource) {
  const vertex = compileShader(gl.VERTEX_SHADER, vertexSource);
  const fragment = compileShader(gl.FRAGMENT_SHADER, fragmentSource);
  const linked = gl.createProgram();
  gl.attachShader(linked, vertex);
  gl.attachShader(linked, fragment);
  gl.linkProgram(linked);
  if (!gl.getProgramParameter(linked, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(linked));
  return linked;
}
function compileShader(type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
  return shader;
}
function materialFor(name, rgb, opacity, mode) {
  let color = rgb.map(v => v / 255);
  let specular = 0.22;
  let broadSpecular = 0.035;
  let glazeStrength = 0.0;
  let shininess = 32;
  let ambient = 0.24;
  let diffuseBoost = 1.0;
  let rimStrength = 0.0;
  let rimPower = 2.4;
  let warmth = 0.0;
  let wrapDiffuse = 0.0;
  let emission = 0.0;
  let subsurface = 0.0;
  if (name === 'dental_hard_tissue') { specular = 0.35; shininess = 48; }
  if (name === 'jaws') { specular = 0.18; shininess = 24; }
  if (name === 'pulp') { specular = 0.28; shininess = 36; }
  if (name === 'all_nonzero') { specular = 0.15; shininess = 20; }
  if (mode === 'rich') {
    ambient = 0.25;
    diffuseBoost = 1.04;
    rimStrength = 0.16;
    rimPower = 1.85;
    warmth = 0.26;
    wrapDiffuse = 0.16;
    subsurface = 0.02;
    if (name === 'dental_hard_tissue') {
      color = [0.965, 0.945, 0.88];
      specular = 1.10;
      broadSpecular = 0.24;
      glazeStrength = 0.48;
      shininess = 260;
      ambient = 0.22;
      diffuseBoost = 0.90;
      rimStrength = 0.15;
      rimPower = 1.72;
      warmth = 0.18;
      wrapDiffuse = 0.08;
      emission = 0.0;
      subsurface = 0.052;
    } else if (name === 'jaws') {
      color = [0.83, 0.68, 0.49];
      opacity = Math.max(opacity, 0.50);
      specular = 0.11;
      shininess = 22;
      ambient = 0.40;
      diffuseBoost = 0.82;
      rimStrength = 0.10;
      rimPower = 1.95;
      warmth = 0.38;
      wrapDiffuse = 0.34;
      emission = 0.018;
      subsurface = 0.055;
    } else if (name === 'pulp') {
      color = [0.98, 0.10, 0.16];
      specular = 0.42;
      shininess = 64;
      ambient = 0.22;
      diffuseBoost = 1.06;
      rimStrength = 0.28;
      rimPower = 1.40;
      warmth = 0.04;
      wrapDiffuse = 0.08;
      emission = 0.105;
      subsurface = 0.08;
    } else if (name === 'all_nonzero') {
      color = [0.60, 0.80, 0.92];
      specular = 0.16;
      shininess = 26;
      ambient = 0.28;
      rimStrength = 0.16;
      warmth = 0.02;
      wrapDiffuse = 0.18;
      emission = 0.025;
    }
  } else if (mode === 'realistic') {
    ambient = 0.27;
    diffuseBoost = 0.95;
    rimStrength = 0.10;
    rimPower = 2.0;
    warmth = 0.30;
    wrapDiffuse = 0.10;
    subsurface = 0.01;
    if (name === 'dental_hard_tissue') {
      color = [0.96, 0.91, 0.76];
      specular = 0.58;
      shininess = 86;
      rimStrength = 0.16;
      warmth = 0.42;
      wrapDiffuse = 0.08;
      subsurface = 0.02;
    } else if (name === 'jaws') {
      color = [0.70, 0.50, 0.30];
      specular = 0.10;
      shininess = 18;
      ambient = 0.31;
      diffuseBoost = 0.86;
      rimStrength = 0.07;
      warmth = 0.18;
      wrapDiffuse = 0.18;
      subsurface = 0.025;
    } else if (name === 'pulp') {
      color = [0.74, 0.13, 0.17];
      specular = 0.30;
      shininess = 42;
      ambient = 0.22;
      rimStrength = 0.08;
      warmth = 0.05;
      emission = 0.035;
      subsurface = 0.04;
    } else if (name === 'all_nonzero') {
      color = [0.70, 0.82, 0.88];
      specular = 0.08;
      shininess = 18;
      rimStrength = 0.06;
      wrapDiffuse = 0.12;
    }
  } else if (mode === 'neutral') {
    ambient = 0.32;
    diffuseBoost = 0.78;
    rimStrength = 0.02;
    wrapDiffuse = 0.05;
    shininess *= 0.75;
    specular *= 0.55;
    if (name === 'dental_hard_tissue') color = [0.91, 0.89, 0.80];
    if (name === 'jaws') color = [0.70, 0.57, 0.38];
    if (name === 'pulp') color = [0.75, 0.18, 0.20];
  } else if (mode === 'high_contrast') {
    ambient = 0.22;
    diffuseBoost = 1.15;
    rimStrength = 0.22;
    rimPower = 1.7;
    wrapDiffuse = 0.04;
    emission = 0.02;
    specular *= 0.90;
    shininess *= 1.2;
    if (name === 'dental_hard_tissue') color = [1.00, 0.96, 0.76];
    if (name === 'jaws') color = [0.92, 0.62, 0.24];
    if (name === 'pulp') { color = [1.00, 0.16, 0.22]; emission = 0.10; }
    if (name === 'all_nonzero') color = [0.46, 0.80, 1.00];
  }
  return { color, opacity, specular, broadSpecular, glazeStrength, shininess, ambient, diffuseBoost, rimStrength, rimPower, warmth, wrapDiffuse, emission, subsurface };
}
function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function identity3() { return [1,0,0, 0,1,0, 0,0,1]; }
function columnMajorMat3(m) {
  return [
    m[0], m[3], m[6],
    m[1], m[4], m[7],
    m[2], m[5], m[8]
  ];
}
function orthonormalizeOrientation(m) {
  let x = [m[0], m[1], m[2]];
  x = length3(x) > 1e-7 ? normalize3(x) : [1, 0, 0];
  const yRaw = [m[3], m[4], m[5]];
  let y = sub3(yRaw, scale3(x, dot3(yRaw, x)));
  y = length3(y) > 1e-7 ? normalize3(y) : orthogonalUnitVector(x);
  let z = normalize3(cross3(x, y));
  let result = [x[0], x[1], x[2], y[0], y[1], y[2], z[0], z[1], z[2]];
  if (determinant3(result) < 0) {
    result[6] = -result[6];
    result[7] = -result[7];
    result[8] = -result[8];
  }
  return result;
}
function orthogonalUnitVector(axis) {
  const reference = Math.abs(axis[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
  return normalize3(sub3(reference, scale3(axis, dot3(reference, axis))));
}
function mat3Multiply(a, b) {
  return [
    a[0]*b[0]+a[1]*b[3]+a[2]*b[6], a[0]*b[1]+a[1]*b[4]+a[2]*b[7], a[0]*b[2]+a[1]*b[5]+a[2]*b[8],
    a[3]*b[0]+a[4]*b[3]+a[5]*b[6], a[3]*b[1]+a[4]*b[4]+a[5]*b[7], a[3]*b[2]+a[4]*b[5]+a[5]*b[8],
    a[6]*b[0]+a[7]*b[3]+a[8]*b[6], a[6]*b[1]+a[7]*b[4]+a[8]*b[7], a[6]*b[2]+a[7]*b[5]+a[8]*b[8]
  ];
}
function mat3Vec(m, v) {
  return [
    m[0]*v[0]+m[1]*v[1]+m[2]*v[2],
    m[3]*v[0]+m[4]*v[1]+m[5]*v[2],
    m[6]*v[0]+m[7]*v[1]+m[8]*v[2]
  ];
}
function axisAngleMatrix(axis, angle) {
  const a = normalize3(axis);
  const x = a[0], y = a[1], z = a[2];
  const c = Math.cos(angle), s = Math.sin(angle), t = 1 - c;
  return [
    t*x*x+c, t*x*y-s*z, t*x*z+s*y,
    t*x*y+s*z, t*y*y+c, t*y*z-s*x,
    t*x*z-s*y, t*y*z+s*x, t*z*z+c
  ];
}
function orientationFromYawPitch(yawDegrees, pitchDegrees) {
  const yaw = yawDegrees * Math.PI / 180;
  const pitch = pitchDegrees * Math.PI / 180;
  return mat3Multiply(axisAngleMatrix([1, 0, 0], pitch), axisAngleMatrix([0, 1, 0], yaw));
}
function sub3(a, b) { return [a[0]-b[0], a[1]-b[1], a[2]-b[2]]; }
function cross3(a, b) { return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]; }
function dot3(a, b) { return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]; }
function determinant3(m) {
  return m[0]*(m[4]*m[8]-m[5]*m[7]) - m[1]*(m[3]*m[8]-m[5]*m[6]) + m[2]*(m[3]*m[7]-m[4]*m[6]);
}
function length3(v) { return Math.hypot(v[0], v[1], v[2]); }
function scale3(v, scalar) { return [v[0]*scalar, v[1]*scalar, v[2]*scalar]; }
function normalize3(v) {
  const length = length3(v);
  return length > 1e-8 ? [v[0]/length, v[1]/length, v[2]/length] : [0, 0, 1];
}
function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
</script>
</body>
</html>
""".replace("__PAYLOAD__", script_safe_payload_json)


def _laplacian_step(adjacency: Any, degree: np.ndarray, vertices: np.ndarray, weight: float) -> np.ndarray:
    average = (adjacency @ vertices) / degree
    return vertices + float(weight) * (average - vertices)


def _smoothing_to_dict(smoothing: SmoothingConfig) -> dict[str, Any]:
    return {
        "preset": smoothing.preset,
        "iterations": smoothing.iterations,
        "lambda": smoothing.lambda_value,
        "mu": smoothing.mu,
    }


def _viewer_smoothing_presets() -> dict[str, dict[str, float | int]]:
    return {
        name: {
            "iterations": int(values["iterations"]),
            "lambda": float(values["lambda_value"]),
            "mu": float(values["mu"]),
        }
        for name, values in SMOOTH_PRESETS.items()
    }


def _rounded_list(vertices: np.ndarray) -> list[list[float]]:
    return np.round(vertices.astype(float), 3).tolist()
