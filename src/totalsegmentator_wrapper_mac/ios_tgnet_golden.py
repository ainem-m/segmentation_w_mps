"""Evaluation-only comparison of TGNet output with a local golden case.

This module is deliberately separate from inference. Golden labels are never
used to generate, relabel, or repair a product prediction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _overlap(predicted: np.ndarray, golden: np.ndarray) -> dict[str, Any]:
    intersection = int(np.logical_and(predicted, golden).sum())
    union = int(np.logical_or(predicted, golden).sum())
    predicted_count = int(predicted.sum())
    golden_count = int(golden.sum())
    denominator = predicted_count + golden_count
    return {
        "intersection": intersection,
        "union": union,
        "predicted_vertices": predicted_count,
        "golden_vertices": golden_count,
        "iou": float(intersection / union) if union else 1.0,
        "dice": float(2 * intersection / denominator) if denominator else 1.0,
    }


def compare_fdi_labels(
    predicted: np.ndarray,
    golden: np.ndarray,
) -> dict[str, Any]:
    predicted = np.asarray(predicted, dtype=np.int16).reshape(-1)
    golden = np.asarray(golden, dtype=np.int16).reshape(-1)
    if predicted.shape != golden.shape:
        raise ValueError(
            "Prediction and golden vertex count differ: "
            f"{len(predicted)} != {len(golden)}."
        )
    predicted_tooth = predicted > 0
    golden_tooth = golden > 0
    labels = sorted(
        int(value)
        for value in np.union1d(predicted[predicted_tooth], golden[golden_tooth])
    )
    per_fdi = {
        str(label): _overlap(predicted == label, golden == label)
        for label in labels
    }
    present_golden = [
        metrics
        for label, metrics in per_fdi.items()
        if np.any(golden == int(label))
    ]
    exact = predicted == golden
    predicted_instances = sorted(int(value) for value in np.unique(predicted) if value)
    golden_instances = sorted(int(value) for value in np.unique(golden) if value)
    pair_iou = np.zeros(
        (len(predicted_instances), len(golden_instances)), dtype=np.float64
    )
    pair_intersection = np.zeros_like(pair_iou, dtype=np.int64)
    for row, predicted_label in enumerate(predicted_instances):
        predicted_mask = predicted == predicted_label
        for column, golden_label in enumerate(golden_instances):
            golden_mask = golden == golden_label
            intersection = int(np.logical_and(predicted_mask, golden_mask).sum())
            union = int(np.logical_or(predicted_mask, golden_mask).sum())
            pair_intersection[row, column] = intersection
            pair_iou[row, column] = intersection / union if union else 0.0
    if pair_iou.size:
        matched_rows, matched_columns = linear_sum_assignment(-pair_iou)
    else:
        matched_rows = np.array([], dtype=np.int64)
        matched_columns = np.array([], dtype=np.int64)
    matches = [
        {
            "predicted_label": predicted_instances[int(row)],
            "golden_fdi": golden_instances[int(column)],
            "iou": float(pair_iou[row, column]),
            "intersection": int(pair_intersection[row, column]),
        }
        for row, column in zip(matched_rows, matched_columns, strict=True)
    ]
    matched_vertices = int(
        sum(item["intersection"] for item in matches)
    )
    return {
        "schema": "tgnet_ios_golden_comparison.v1",
        "evaluation_only": True,
        "golden_used_by_inference": False,
        "vertices": int(len(golden)),
        "tooth_gingiva": _overlap(predicted_tooth, golden_tooth)
        | {
            "accuracy": float(
                np.mean(predicted_tooth == golden_tooth)
            )
        },
        "exact_fdi": {
            "correct_vertices": int(exact.sum()),
            "accuracy": float(exact.mean()),
        },
        "optimal_instance_matching": {
            "predicted_instances": len(predicted_instances),
            "golden_instances": len(golden_instances),
            "matches": matches,
            "matched_vertices": matched_vertices,
            "matched_tooth_vertex_accuracy": float(
                matched_vertices / golden_tooth.sum()
            )
            if golden_tooth.any()
            else 1.0,
            "mean_golden_iou": float(
                sum(item["iou"] for item in matches) / len(golden_instances)
            )
            if golden_instances
            else 1.0,
        },
        "per_fdi": per_fdi,
        "macro_fdi": {
            "labels_in_golden": len(present_golden),
            "mean_iou": float(
                np.mean([item["iou"] for item in present_golden])
            )
            if present_golden
            else 1.0,
            "mean_dice": float(
                np.mean([item["dice"] for item in present_golden])
            )
            if present_golden
            else 1.0,
        },
    }


def _prediction_fdi(summary_path: Path, labels_path: Path) -> np.ndarray:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    labels = np.load(labels_path)
    vertex_instances = np.asarray(labels["vertex_labels"], dtype=np.int16)
    mapping = {
        int(item["instance_id"]): int(item["fdi"])
        for item in summary.get("instances", [])
    }
    unknown = sorted(
        int(value)
        for value in np.unique(vertex_instances)
        if value and int(value) not in mapping
    )
    if unknown:
        raise ValueError(f"Prediction has unmapped instance labels: {unknown}.")
    predicted = np.zeros_like(vertex_instances)
    for instance_id, fdi in mapping.items():
        predicted[vertex_instances == instance_id] = fdi
    return predicted


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--golden-json", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    predicted = _prediction_fdi(args.summary, args.labels)
    golden_document = json.loads(args.golden_json.read_text(encoding="utf-8"))
    golden = np.asarray(golden_document["labels"], dtype=np.int16)
    result = compare_fdi_labels(predicted, golden) | {
        "artifacts": {
            "prediction_summary": str(args.summary.resolve()),
            "prediction_summary_sha256": _sha256(args.summary),
            "prediction_labels": str(args.labels.resolve()),
            "prediction_labels_sha256": _sha256(args.labels),
            "golden_json": str(args.golden_json.resolve()),
            "golden_json_sha256": _sha256(args.golden_json),
        }
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
