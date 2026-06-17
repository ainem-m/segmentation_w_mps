from __future__ import annotations

import json
from pathlib import Path
from typing import Any


NON_CLINICAL_NOTICE = (
    "This is a non-clinical research/education preview. It is not a medical device "
    "and is not intended for diagnosis, treatment planning, surgical planning, or "
    "autonomous clinical decision-making. Outputs must be treated as preliminary "
    "segmentation model outputs and manually reviewed."
)


def load_case_summary(case_dir: Path) -> dict[str, Any]:
    case_dir = case_dir.resolve()
    benchmark = _read_json(case_dir / "logs" / "benchmark.json")
    mask_stats = _read_json(case_dir / "logs" / "mask_stats.json")
    return {
        "case_dir": str(case_dir),
        "benchmark": benchmark,
        "mask_stats": mask_stats,
        "files": {
            "benchmark": str(case_dir / "logs" / "benchmark.json"),
            "environment": str(case_dir / "logs" / "environment.json"),
            "run_log": str(case_dir / "logs" / "run.log"),
            "mask_stats": str(case_dir / "logs" / "mask_stats.json"),
            "surface_preview": str(case_dir / "surface_preview" / "index.html"),
            "readme": str(case_dir / "README_OUTPUT.md"),
        },
    }


def format_case_summary_text(case_dir: Path) -> str:
    summary = load_case_summary(case_dir)
    run = summary.get("benchmark", {}).get("run", {})
    lines = ["", "Case Summary", "------------"]
    if run:
        lines.extend(
            [
                f"status: {run.get('status', 'unknown')}",
                f"task: {run.get('task', 'unknown')}",
                f"requested_device: {run.get('requested_device', 'unknown')}",
                f"actual_device: {run.get('actual_device', 'unknown')}",
                f"elapsed_seconds: {run.get('elapsed_seconds', 'unknown')}",
            ]
        )
    else:
        lines.append("benchmark.json: missing")

    masks = summary.get("mask_stats", {}).get("masks", [])
    if masks:
        lines.append("masks:")
        for item in masks:
            if item.get("status") == "ok":
                lines.append(f"  {item.get('name')}: {item.get('nonzero_voxels')} nonzero voxels")
            else:
                lines.append(f"  {item.get('name')}: unreadable")
    else:
        lines.append("mask_stats.json: missing")
    lines.append("")
    return "\n".join(lines)


def format_case_summary_markdown(case_dir: Path) -> str:
    summary = load_case_summary(case_dir)
    run = summary.get("benchmark", {}).get("run", {})
    input_meta = summary.get("benchmark", {}).get("input", {})
    masks = summary.get("mask_stats", {}).get("masks", [])
    rows = []
    for item in masks:
        nonzero = item.get("nonzero_voxels") if item.get("status") == "ok" else "unreadable"
        rows.append(f"| `{item.get('name', 'unknown')}` | {nonzero} |")
    mask_table = "\n".join(rows) if rows else "| none | n/a |"

    return "\n".join(
        [
            "# TotalSegmentator Wrapper for Mac Case Summary",
            "",
            "## Run",
            "",
            "| Field | Value |",
            "|---|---|",
            f"| Status | `{run.get('status', 'unknown')}` |",
            f"| Task | `{run.get('task', 'unknown')}` |",
            f"| Requested device | `{run.get('requested_device', 'unknown')}` |",
            f"| Actual device | `{run.get('actual_device', 'unknown')}` |",
            f"| Elapsed seconds | `{run.get('elapsed_seconds', 'unknown')}` |",
            "",
            "## Input",
            "",
            "| Field | Value |",
            "|---|---|",
            f"| Basename | `{input_meta.get('basename', 'unknown')}` |",
            f"| Dimensions | `{input_meta.get('dimensions', 'unknown')}` |",
            f"| Spacing | `{input_meta.get('spacing', 'unknown')}` |",
            "",
            "## Masks",
            "",
            "| Mask | Nonzero voxels |",
            "|---|---:|",
            mask_table,
            "",
            "## Notice",
            "",
            NON_CLINICAL_NOTICE,
            "",
        ]
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
