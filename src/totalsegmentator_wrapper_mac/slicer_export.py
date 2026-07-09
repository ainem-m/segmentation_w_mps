from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np

from totalsegmentator_wrapper_mac.disclaimers import NON_CLINICAL_NOTICE_EN
from totalsegmentator_wrapper_mac.surface_preview import (
    is_dental_hard_tissue,
    label_name_map,
    resolve_surface_preview_input,
    safe_name,
)


SCHEMA = "totalsegmentator_wrapper_mac.slicer_export.v1"

PALETTE: tuple[tuple[float, float, float], ...] = (
    (0.90, 0.52, 0.42),
    (0.38, 0.68, 0.84),
    (0.53, 0.74, 0.43),
    (0.82, 0.62, 0.86),
    (0.93, 0.74, 0.35),
    (0.42, 0.78, 0.70),
    (0.72, 0.72, 0.72),
)


def run_slicer_export(
    *,
    case_dir: Path,
    source_path: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    case_dir = case_dir.resolve()
    if source_path is not None and not source_path.exists():
        raise FileNotFoundError(f"Source volume not found: {source_path}")
    output_dir = (output_dir or case_dir / "slicer_export").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    labelmap_input, source_info = resolve_surface_preview_input(
        case_dir=case_dir,
        input_path=None,
    )
    labelmap_export = _copy_labelmap(labelmap_input, output_dir)
    labels = _collect_labels(labelmap_input)
    colors = {
        str(label): _color_for_label(label=label, name=name)
        for label, name in labels.items()
    }
    label_names_path = output_dir / "label_names.json"
    label_colors_path = output_dir / "label_colors.json"
    color_table_path = output_dir / "segmentation_ColorTable.ctbl"
    readme_path = output_dir / "README_SLICER_IMPORT.md"
    summary_path = output_dir / "slicer_export_summary.json"

    label_names_path.write_text(
        json.dumps({"labels": {str(k): v for k, v in labels.items()}}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    label_colors_path.write_text(
        json.dumps({"labels": colors}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_color_table(color_table_path, labels=labels, colors=colors)

    source_export = _copy_source_volume(
        case_dir=case_dir,
        source_path=source_path,
        output_dir=output_dir,
    )
    readme_path.write_text(
        _readme(
            labelmap_export=labelmap_export,
            color_table_path=color_table_path,
            source_export=source_export,
            label_count=len(labels),
        ),
        encoding="utf-8",
    )

    summary: dict[str, Any] = {
        "schema": SCHEMA,
        "case_dir": str(case_dir),
        "output_dir": str(output_dir),
        "mode": "file_only_drag_and_drop",
        "slicer_launch": False,
        "python_script_required": False,
        "source": {
            "input": str(source_path.resolve()) if source_path is not None else None,
            "export": str(source_export) if source_export is not None else None,
        },
        "segmentation": {
            "format": "nifti_labelmap_with_slicer_color_table",
            "input": str(labelmap_input.resolve()),
            "export": str(labelmap_export),
            "source_info": source_info,
            "label_count": len(labels),
            "labels": [
                {
                    "label": label,
                    "name": name,
                    "color": colors[str(label)],
                }
                for label, name in labels.items()
            ],
        },
        "files": {
            "labelmap": str(labelmap_export),
            "color_table": str(color_table_path),
            "label_names": str(label_names_path),
            "label_colors": str(label_colors_path),
            "readme": str(readme_path),
        },
    }
    summary["files"]["summary"] = str(summary_path)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def _copy_labelmap(labelmap_input: Path, output_dir: Path) -> Path:
    suffix = _nifti_suffix(labelmap_input)
    destination = output_dir / f"segmentation_labelmap{suffix}"
    shutil.copy2(labelmap_input, destination)
    return destination


def _copy_source_volume(
    *,
    case_dir: Path,
    source_path: Path | None,
    output_dir: Path,
) -> Path | None:
    source = source_path.resolve() if source_path is not None else _default_case_source(case_dir)
    if source is None or not source.exists():
        return None
    destination = output_dir / f"source{_nifti_suffix(source)}"
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    return destination


def _default_case_source(case_dir: Path) -> Path | None:
    input_dir = case_dir / "input"
    for name in ("source.nii.gz", "source.nii"):
        candidate = input_dir / name
        if candidate.exists():
            return candidate.resolve()
    return None


def _collect_labels(labelmap_input: Path) -> dict[int, str]:
    image = nib.load(str(labelmap_input))
    data = np.asanyarray(image.dataobj)
    names = label_name_map(labelmap_input)
    labels: dict[int, str] = {}
    for raw_value in np.unique(data):
        label = int(raw_value)
        if label == 0:
            continue
        labels[label] = names.get(label, f"label_{label}")
    return dict(sorted(labels.items()))


def _color_for_label(*, label: int, name: str) -> list[float]:
    if name in {"lower_jawbone", "upper_jawbone", "mandible", "skull"}:
        return [0.84, 0.64, 0.34]
    if "pulp" in name or "canal" in name:
        return [0.82, 0.22, 0.30]
    if is_dental_hard_tissue(name):
        return [0.94, 0.91, 0.78]
    color = PALETTE[(label - 1) % len(PALETTE)]
    return [float(value) for value in color]


def _write_color_table(
    path: Path,
    *,
    labels: dict[int, str],
    colors: dict[str, list[float]],
) -> None:
    lines = [
        "# Color table for TotalSegmentator Wrapper for Mac Slicer export",
        "# Label Name R G B A",
        "0 Background 0 0 0 0",
    ]
    for label, name in labels.items():
        r, g, b = _float_color_to_u8(colors[str(label)])
        lines.append(f"{label} {safe_name(name)} {r} {g} {b} 255")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _float_color_to_u8(color: list[float]) -> tuple[int, int, int]:
    values = [int(round(max(0.0, min(1.0, value)) * 255)) for value in color[:3]]
    return values[0], values[1], values[2]


def _nifti_suffix(path: Path) -> str:
    if path.name.endswith(".nii.gz"):
        return ".nii.gz"
    if path.suffix == ".nii":
        return ".nii"
    return path.suffix or ".nii.gz"


def _readme(
    *,
    labelmap_export: Path,
    color_table_path: Path,
    source_export: Path | None,
    label_count: int,
) -> str:
    source_line = (
        f"- `{source_export.name}`"
        if source_export is not None
        else "- source volume was not copied; drag the original CT NIfTI into Slicer if needed"
    )
    return f"""# Slicer Import Files

This folder is a file-only handoff for 3D Slicer. The app does not launch Slicer
and does not require running a Python script.

## Files

```text
{source_line}
- `{labelmap_export.name}`
- `{color_table_path.name}`
- `label_names.json`
- `label_colors.json`
```

Label count: `{label_count}`

## Import In 3D Slicer

1. Open 3D Slicer manually.
2. Drag this folder's files into Slicer.
3. If Slicer asks how to load `{labelmap_export.name}`, choose `Segmentation`.
4. If the label names/colors are not applied automatically, load
   `{color_table_path.name}` first, then load `{labelmap_export.name}` as a
   segmentation with that color table.
5. Open `Segment Editor` to inspect or edit the segmentation.

If Slicer loads the file as a labelmap volume instead of a segmentation, use the
Data module to convert the labelmap to a segmentation node before editing.

## Notice

{NON_CLINICAL_NOTICE_EN}
"""
