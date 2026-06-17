#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from totalsegmentator_wrapper_mac.teeth_roi import create_teeth_roi_from_craniofacial_case


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate teeth ROI metadata for several margins.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--craniofacial-case", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--margins-mm", nargs="+", type=float, default=[5.0, 10.0, 15.0, 20.0])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for margin in args.margins_mm:
        margin_dir = args.output / f"margin_{margin:g}mm"
        roi_path = margin_dir / "input" / "teeth_roi.nii.gz"
        roi_json_path = margin_dir / "logs" / "teeth_roi.json"
        try:
            metadata = create_teeth_roi_from_craniofacial_case(
                input_path=args.input,
                craniofacial_case_dir=args.craniofacial_case,
                output_path=roi_path,
                roi_json_path=roi_json_path,
                margin_mm=margin,
            )
            rows.append(_row_from_metadata(margin, metadata, "success", None))
        except Exception as exc:  # noqa: BLE001
            rows.append(
                {
                    "margin_mm": margin,
                    "status": "failed",
                    "error": repr(exc),
                    "roi_json": str(roi_json_path),
                    "roi_nii": str(roi_path),
                }
            )

    payload = {
        "input": str(args.input.resolve()),
        "craniofacial_case": str(args.craniofacial_case.resolve()),
        "rows": rows,
    }
    (args.output / "teeth_roi_margin_table.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (args.output / "teeth_roi_margin_table.md").write_text(
        _markdown_table(rows),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _row_from_metadata(
    margin: float,
    metadata: dict[str, Any],
    status: str,
    error: str | None,
) -> dict[str, Any]:
    return {
        "margin_mm": margin,
        "status": status,
        "error": error,
        "roi_shape": metadata.get("roi_shape"),
        "voxel_volume_ratio": metadata.get("voxel_volume_ratio"),
        "axis_extent_ratios": metadata.get("axis_extent_ratios"),
        "near_whole_volume": metadata.get("near_whole_volume"),
        "bbox_min_ijk": metadata.get("bbox_min_ijk"),
        "bbox_max_ijk": metadata.get("bbox_max_ijk"),
        "mask_nonzero_voxels": metadata.get("mask_nonzero_voxels"),
        "roi_json": metadata.get("output", "").replace("/input/teeth_roi.nii.gz", "/logs/teeth_roi.json"),
        "roi_nii": metadata.get("output"),
    }


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Teeth ROI Margin Table",
        "",
        "| Margin mm | Status | ROI shape | Voxel ratio | Axis ratios | Near whole | Error |",
        "|---:|---|---|---:|---|---|---|",
    ]
    for row in rows:
        axis = row.get("axis_extent_ratios")
        axis_text = "-"
        if isinstance(axis, list):
            axis_text = ", ".join(f"{float(value):.3f}" for value in axis)
        ratio = row.get("voxel_volume_ratio")
        ratio_text = "-" if ratio is None else f"{float(ratio):.4f}"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{float(row['margin_mm']):g}",
                    str(row.get("status", "unknown")),
                    str(row.get("roi_shape") or "-"),
                    ratio_text,
                    axis_text,
                    str(row.get("near_whole_volume", "-")),
                    str(row.get("error") or ""),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

