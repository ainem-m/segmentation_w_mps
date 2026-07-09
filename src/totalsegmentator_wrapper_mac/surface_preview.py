from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
) -> dict[str, Any]:
    if preview_step_size < 1:
        raise ValueError("preview_step_size must be >= 1")
    case_dir = case_dir.resolve()
    input_path, source_info = resolve_surface_preview_input(
        case_dir=case_dir,
        input_path=input_path,
    )
    output_dir = output_dir or case_dir / "surface_preview"
    smoothing = smoothing or smoothing_config_from_options(preset="slicer_like")
    summary = export_labelmap_surfaces(
        input_path=input_path,
        output_dir=output_dir,
        min_voxels=min_voxels,
        combined=True,
        smoothing=smoothing,
        suffix="_smooth",
        summary_filename="preview_summary.json",
        readme_filename="README_SURFACE_PREVIEW.md",
    )
    html_path = output_dir / "index.html"
    preview_meshes = _build_preview_meshes(
        input_path=input_path,
        summary=summary,
        smoothing=smoothing,
        step_size=preview_step_size,
    )
    _write_offline_viewer(html_path, summary=summary, preview_meshes=preview_meshes)
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
        "transparent_rendering": "jaw_depth_prepass_front_shell",
    }
    (output_dir / "preview_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return summary


def resolve_surface_preview_input(
    *,
    case_dir: Path,
    input_path: Path | None,
) -> tuple[Path, dict[str, Any]]:
    if input_path is not None:
        resolved = input_path.resolve()
        return resolved, {"source": "explicit_input", "input": str(resolved)}

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

    raw_totalseg = case_dir / "segmentations" / "raw_totalseg"
    if any((raw_totalseg / filename).exists() for filename, _label, _name in CRANIOFACIAL_SURFACE_LABELS):
        derived, metadata = build_craniofacial_surface_labelmap(case_dir=case_dir)
        return derived, metadata

    raise FileNotFoundError(
        "No default surface-preview input found. Expected either "
        f"{teeth_fullspace} or craniofacial masks under {raw_totalseg}."
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
    summary_filename: str = "stl_export_summary.json",
    readme_filename: str = "README_STL_EXPORT.md",
) -> dict[str, Any]:
    input_path = input_path.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    labels_dir = output_dir / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)

    smoothing = smoothing or smoothing_config_from_options(preset="none")
    image = nib.load(str(input_path))
    data = np.asanyarray(image.dataobj)
    label_names = label_name_map(input_path)
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
        mesh = mask_to_mesh(data == label, image.affine, smoothing=effective)
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
            mesh = mask_to_mesh(np.isin(data, labels), image.affine, smoothing=effective)
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
    (output_dir / summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_markdown_summary(output_dir / readme_filename, summary)
    return summary


def mask_to_mesh(
    mask: np.ndarray,
    affine: np.ndarray,
    *,
    smoothing: SmoothingConfig | None = None,
    step_size: int = 1,
) -> dict[str, Any]:
    coords = np.argwhere(mask)
    if coords.size == 0:
        raise RuntimeError("Cannot mesh an empty mask")
    lo = np.maximum(coords.min(axis=0) - 1, 0)
    hi = np.minimum(coords.max(axis=0) + 2, np.array(mask.shape))
    slices = tuple(slice(int(start), int(stop)) for start, stop in zip(lo, hi, strict=True))
    cropped = mask[slices]
    padded = np.pad(cropped.astype(np.uint8), 1, mode="constant", constant_values=0)
    vertices, faces, _normals, _values = measure.marching_cubes(
        padded,
        level=0.5,
        step_size=step_size,
    )
    vertices = vertices + lo - 1
    vertices_mm = nib.affines.apply_affine(affine, vertices).astype(np.float32)
    if smoothing is not None and smoothing.iterations > 0:
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
        for tri in faces:
            points = vertices[tri]
            normal = np.cross(points[1] - points[0], points[2] - points[0])
            norm = float(np.linalg.norm(normal))
            if norm > 0:
                normal = normal / norm
            else:
                normal = np.zeros(3, dtype=np.float32)
            file.write(struct.pack("<3f", *normal.astype(np.float32)))
            file.write(struct.pack("<9f", *points.astype(np.float32).reshape(-1)))
            file.write(struct.pack("<H", 0))


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
            if entry["name"] in {"lower_jawbone", "upper_jawbone"}
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
    if name in {
        "bridge",
        "crown",
        "implant",
        "upper_teeth",
        "lower_teeth",
        "teeth_upper",
        "teeth_lower",
    }:
        return True
    return "fdi" in name and "pulp" not in name


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
) -> list[dict[str, Any]]:
    image = nib.load(str(input_path))
    data = np.asanyarray(image.dataobj)
    preview = []
    group_order = ["jaws", "dental_hard_tissue", "pulp", "all_nonzero"]
    groups = {group["name"]: group for group in summary["groups"]}
    for name in group_order:
        group = groups.get(name)
        if not group:
            continue
        mesh = mask_to_mesh(
            np.isin(data, group["labels"]),
            image.affine,
            smoothing=effective_smoothing_for_group(group_name=name, smoothing=smoothing),
            step_size=step_size,
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
) -> None:
    payload = {
        "dataLabel": "選択したデータ",
        "labelCount": summary["label_count"],
        "smoothing": summary["smoothing"],
        "meshes": preview_meshes,
    }
    path.write_text(_html_document(payload), encoding="utf-8")


def _html_document(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return _webgl_html_document(payload_json)



def _webgl_html_document(payload_json: str) -> str:
    return """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TotalSegmentator 3Dビューアー</title>
<style>
body { margin: 0; background: #15171b; color: #e9edf2; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
#app { display: grid; grid-template-columns: 300px 1fr; height: 100vh; }
#panel { padding: 16px; background: #20242a; overflow: auto; border-right: 1px solid #343941; }
#panel h1 { font-size: 18px; margin: 0 0 12px; }
#panel h2 { font-size: 14px; margin: 16px 0 8px; color: #cfe4e2; }
#panel p, #panel label { font-size: 13px; line-height: 1.45; }
#panel button { padding: 8px; border: 1px solid #535b66; background: #2e343d; color: #fff; border-radius: 6px; }
#panel button.active { background: #496078; border-color: #71839a; }
#mode, #views { display: grid; gap: 6px; margin: 10px 0; }
#mode { grid-template-columns: 1fr 1fr; }
#views { grid-template-columns: 1fr 1fr; }
#views button { width: 100%; }
#layers label { display: block; margin: 8px 0; }
canvas { width: 100%; height: 100%; display: block; background: #111317; touch-action: none; }
code { color: #c9e2ff; }
</style>
</head>
<body>
<div id="app">
  <aside id="panel">
    <h1>TotalSegmentator 3Dビューアー</h1>
    <p>データ: <code id="dataName"></code></p>
    <p>このデータで検出された構造ラベル: <code id="labelCount"></code><br>表面平滑化: <code id="smooth"></code></p>
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
  </aside>
  <canvas id="view" aria-label="3Dデータ表示領域"></canvas>
</div>
<script>
const DATA = __PAYLOAD__;
const canvas = document.getElementById('view');
const gl = canvas.getContext('webgl', { antialias: true, alpha: false });
const ctx2d = gl ? null : canvas.getContext('2d');
const MIN_ZOOM = 0.0001;
const MAX_ZOOM = 1000.0;
const MAX_ZOOM_LOG_DELTA_PER_INPUT = 3.0;
const MAX_ARCBALL_STEP_PX = 12;
let inputMode = 'trackpad';
let dragging = null;
let lastPointer = null;
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
const preparedMeshes = DATA.meshes.map(prepareMesh);
let program = null;
let attribs = null;
let uniforms = null;
let uintIndexExtension = null;
document.getElementById('dataName').textContent = DATA.dataLabel || '選択したデータ';
document.getElementById('labelCount').textContent = DATA.labelCount;
document.getElementById('smooth').textContent = smoothingLabel(DATA.smoothing.preset);
const layers = document.getElementById('layers');
for (const mesh of DATA.meshes) {
  const label = document.createElement('label');
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.checked = visible[mesh.name];
  input.onchange = () => { visible[mesh.name] = input.checked; draw(); };
  label.appendChild(input);
  label.appendChild(document.createTextNode(' ' + meshDisplayName(mesh.name) + '（ポリゴン数: ' + mesh.faces.length + '）'));
  layers.appendChild(label);
}
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
function resize() {
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * devicePixelRatio));
  canvas.height = Math.max(1, Math.floor(rect.height * devicePixelRatio));
  if (gl) gl.viewport(0, 0, canvas.width, canvas.height);
  draw();
}
function onPointerDown(event) {
  const local = localPoint(event);
  lastPointer = local;
  dragging = { button: event.button, lastLocal: local };
  canvas.setPointerCapture(event.pointerId);
  event.preventDefault();
}
function onPointerMove(event) {
  const local = localPoint(event);
  lastPointer = local;
  if (!dragging) return;
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
  if (dragging) {
    try { canvas.releasePointerCapture(event.pointerId); } catch (_error) {}
  }
  dragging = null;
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
    shininess: gl.getUniformLocation(program, 'uShininess')
  };
  for (const mesh of preparedMeshes) uploadMesh(mesh);
  gl.clearColor(0.067, 0.075, 0.090, 1.0);
  gl.enable(gl.DEPTH_TEST);
  gl.disable(gl.CULL_FACE);
}
function prepareMesh(raw) {
  const vertices = new Float32Array(raw.vertices.length * 3);
  for (let i = 0; i < raw.vertices.length; i++) vertices.set(raw.vertices[i], i * 3);
  const normals = computeVertexNormals(vertices, raw.faces);
  const bounds = computeBounds(vertices);
  const material = materialFor(raw.name, hexToRgb(raw.color), raw.opacity);
  return { name: raw.name, labels: raw.labels, faces: raw.faces, vertices, normals, bounds, material, webgl: null };
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
  gl.bufferData(gl.ARRAY_BUFFER, mesh.vertices, gl.STATIC_DRAW);
  const normalBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, normalBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, mesh.normals, gl.STATIC_DRAW);
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
      indexBuffer,
      indexType: indexArray instanceof Uint32Array ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT,
      drawCount: indexArray.length,
      drawArrays: false
    };
    return;
  }
  const expanded = expandFaces(mesh.vertices, mesh.normals, mesh.faces);
  gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, expanded.vertices, gl.STATIC_DRAW);
  gl.bindBuffer(gl.ARRAY_BUFFER, normalBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, expanded.normals, gl.STATIC_DRAW);
  mesh.webgl = { positionBuffer, normalBuffer, indexBuffer: null, indexType: null, drawCount: expanded.vertices.length / 3, drawArrays: true };
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
function draw() {
  if (gl && program) drawWebGl();
  else drawFallback2d();
}
function drawWebGl() {
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.useProgram(program);
  const minSide = Math.min(canvas.width, canvas.height);
  const viewportScale = [minSide / canvas.width, minSide / canvas.height];
  const visibleMeshes = preparedMeshes.filter(mesh => visible[mesh.name]);
  const depthRange = viewDepthRange(visibleMeshes);
  gl.uniformMatrix3fv(uniforms.orientation, false, new Float32Array(columnMajorMat3(camera.orientation)));
  gl.uniform3fv(uniforms.viewCenter, new Float32Array(camera.viewCenter));
  gl.uniform1f(uniforms.viewScale, camera.viewScale);
  gl.uniform1f(uniforms.zoom, camera.zoom);
  gl.uniform2fv(uniforms.pan, new Float32Array(camera.pan));
  gl.uniform2fv(uniforms.viewportScale, new Float32Array(viewportScale));
  gl.uniform2fv(uniforms.viewportSize, new Float32Array([canvas.width, canvas.height]));
  gl.uniform1f(uniforms.depthNear, depthRange.near);
  gl.uniform1f(uniforms.depthFar, depthRange.far);
  const opaque = visibleMeshes.filter(mesh => mesh.material.opacity >= 0.995);
  const frontShellTranslucent = visibleMeshes.filter(mesh => usesFrontShellTransparency(mesh));
  const classicTranslucent = visibleMeshes.filter(mesh => mesh.material.opacity < 0.995 && !usesFrontShellTransparency(mesh));
  gl.enable(gl.DEPTH_TEST);
  gl.depthFunc(gl.LEQUAL);
  gl.colorMask(true, true, true, true);
  gl.disable(gl.BLEND);
  gl.depthMask(true);
  for (const mesh of opaque) drawMeshWebGl(mesh);
  drawTranslucentDepthPrepass(frontShellTranslucent);
  drawTranslucentFrontShell(frontShellTranslucent);
  drawClassicTranslucent(classicTranslucent);
  gl.colorMask(true, true, true, true);
  gl.depthFunc(gl.LEQUAL);
  gl.depthMask(true);
  gl.disable(gl.BLEND);
}
function usesFrontShellTransparency(mesh) {
  return mesh.name === 'jaws' && mesh.material.opacity < 0.995;
}
function drawTranslucentDepthPrepass(meshes) {
  if (!meshes.length) return;
  gl.disable(gl.BLEND);
  gl.depthFunc(gl.LEQUAL);
  gl.depthMask(true);
  gl.colorMask(false, false, false, false);
  for (const mesh of meshes) drawMeshWebGl(mesh);
  gl.colorMask(true, true, true, true);
}
function drawTranslucentFrontShell(meshes) {
  if (!meshes.length) return;
  gl.enable(gl.BLEND);
  gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  gl.depthFunc(gl.LEQUAL);
  gl.depthMask(false);
  for (const mesh of meshes) drawMeshWebGl(mesh);
  gl.depthMask(true);
  gl.disable(gl.BLEND);
}
function drawClassicTranslucent(meshes) {
  if (!meshes.length) return;
  gl.enable(gl.BLEND);
  gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  gl.depthFunc(gl.LEQUAL);
  gl.depthMask(false);
  for (const mesh of meshes) drawMeshWebGl(mesh);
  gl.depthMask(true);
  gl.disable(gl.BLEND);
}
function drawMeshWebGl(mesh) {
  gl.bindBuffer(gl.ARRAY_BUFFER, mesh.webgl.positionBuffer);
  gl.enableVertexAttribArray(attribs.position);
  gl.vertexAttribPointer(attribs.position, 3, gl.FLOAT, false, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, mesh.webgl.normalBuffer);
  gl.enableVertexAttribArray(attribs.normal);
  gl.vertexAttribPointer(attribs.normal, 3, gl.FLOAT, false, 0, 0);
  gl.uniform3fv(uniforms.color, new Float32Array(mesh.material.color));
  gl.uniform1f(uniforms.opacity, mesh.material.opacity);
  gl.uniform1f(uniforms.specular, mesh.material.specular);
  gl.uniform1f(uniforms.shininess, mesh.material.shininess);
  if (mesh.webgl.drawArrays) {
    gl.drawArrays(gl.TRIANGLES, 0, mesh.webgl.drawCount);
  } else {
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, mesh.webgl.indexBuffer);
    gl.drawElements(gl.TRIANGLES, mesh.webgl.drawCount, mesh.webgl.indexType, 0);
  }
}
function drawFallback2d() {
  ctx2d.clearRect(0, 0, canvas.width, canvas.height);
  const tris = [];
  const visibleMeshes = preparedMeshes.filter(mesh => visible[mesh.name]);
  const depthRange = viewDepthRange(visibleMeshes);
  for (const mesh of visibleMeshes) {
    for (const face of mesh.faces) {
      const a = worldToScreen(vertexAt(mesh.vertices, face[0]), depthRange);
      const b = worldToScreen(vertexAt(mesh.vertices, face[1]), depthRange);
      const c = worldToScreen(vertexAt(mesh.vertices, face[2]), depthRange);
      const depth = (a[2] + b[2] + c[2]) / 3;
      tris.push({ a, b, c, depth, material: mesh.material });
    }
  }
  tris.sort((p, q) => q.depth - p.depth);
  for (const t of tris) {
    const shade = Math.max(0.45, Math.min(1.1, 0.45 + (1.0 - t.depth) * 0.65));
    const rgb = t.material.color.map(v => Math.round(v * 255 * shade));
    ctx2d.beginPath();
    ctx2d.moveTo(t.a[0], t.a[1]);
    ctx2d.lineTo(t.b[0], t.b[1]);
    ctx2d.lineTo(t.c[0], t.c[1]);
    ctx2d.closePath();
    ctx2d.fillStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${t.material.opacity})`;
    ctx2d.fill();
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
uniform float uShininess;
varying vec3 vNormal;
varying vec3 vView;
void main() {
  vec3 normal = normalize(vNormal);
  if (!gl_FrontFacing) normal = -normal;
  vec3 keyLight = normalize(vec3(0.35, 0.65, 0.68));
  vec3 fillLight = normalize(vec3(-0.55, -0.25, 0.55));
  vec3 viewDir = normalize(vec3(0.0, 0.0, 1.0));
  float diffuse = 0.24
    + max(dot(normal, keyLight), 0.0) * 0.72
    + max(dot(normal, fillLight), 0.0) * 0.20;
  vec3 halfDir = normalize(keyLight + viewDir);
  float spec = pow(max(dot(normal, halfDir), 0.0), uShininess) * uSpecular;
  vec3 color = min(uColor * diffuse + vec3(spec), vec3(1.0));
  gl_FragColor = vec4(color, uOpacity);
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
function materialFor(name, rgb, opacity) {
  let specular = 0.22;
  let shininess = 32;
  if (name === 'dental_hard_tissue') { specular = 0.35; shininess = 48; }
  if (name === 'jaws') { specular = 0.18; shininess = 24; }
  if (name === 'pulp') { specular = 0.28; shininess = 36; }
  if (name === 'all_nonzero') { specular = 0.15; shininess = 20; }
  return { color: rgb.map(v => v / 255), opacity, specular, shininess };
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
resize();
</script>
</body>
</html>
""".replace("__PAYLOAD__", payload_json)


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


def _rounded_list(vertices: np.ndarray) -> list[list[float]]:
    return np.round(vertices.astype(float), 3).tolist()
