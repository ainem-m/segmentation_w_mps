#!/usr/bin/env python3
"""Evaluate the product TGNet official-pair path against isolated official GT.

Ground truth is read only after product inference has completed.  STL export is
replaced by a compact prediction NPZ so a multi-case MPS run is resumable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from argparse import Namespace
from pathlib import Path
from typing import Any

import numpy as np
import torch
from scipy.optimize import linear_sum_assignment

import totalsegmentator_wrapper_mac.ios_tgnet_final as product


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _case_paths(root: Path, case: dict[str, Any]) -> tuple[Path, Path]:
    key = str(case["key"])
    patient = key.rsplit("_", 1)[0]
    directory = root / str(case["jaw"]) / patient
    return directory / f"{key}.obj", directory / f"{key}.json"


def _overlap(first: np.ndarray, second: np.ndarray) -> float:
    intersection = int(np.logical_and(first, second).sum())
    union = int(np.logical_or(first, second).sum())
    return intersection / union if union else 1.0


def _instance_metrics(
    predicted: np.ndarray, golden: np.ndarray
) -> dict[str, float]:
    predicted_ids = [int(value) for value in np.unique(predicted) if value]
    golden_ids = [int(value) for value in np.unique(golden) if value]
    pair_iou = np.zeros((len(predicted_ids), len(golden_ids)), dtype=np.float64)
    intersections = np.zeros_like(pair_iou, dtype=np.int64)
    for row, predicted_id in enumerate(predicted_ids):
        predicted_mask = predicted == predicted_id
        for column, golden_id in enumerate(golden_ids):
            golden_mask = golden == golden_id
            intersection = int(np.logical_and(predicted_mask, golden_mask).sum())
            union = int(np.logical_or(predicted_mask, golden_mask).sum())
            intersections[row, column] = intersection
            pair_iou[row, column] = intersection / union if union else 0.0
    rows, columns = (
        linear_sum_assignment(-pair_iou)
        if pair_iou.size
        else (np.asarray([], dtype=int), np.asarray([], dtype=int))
    )
    matched = int(intersections[rows, columns].sum()) if len(rows) else 0
    golden_tooth = golden != 0
    return {
        "mean_golden_instance_iou": (
            float(pair_iou[rows, columns].sum() / len(golden_ids))
            if golden_ids
            else 1.0
        ),
        "matched_golden_tooth_accuracy": (
            matched / int(golden_tooth.sum()) if golden_tooth.any() else 1.0
        ),
    }


def _aggregate(cases: list[dict[str, Any]], role: str) -> dict[str, Any]:
    selected = [case for case in cases if case["role"] == role]
    metrics = (
        "mask_tooth_iou",
        "mean_golden_instance_iou",
        "matched_golden_tooth_accuracy",
        "exact_fdi_accuracy",
        "tooth_only_fdi_accuracy",
        "structured_exact_fdi_accuracy",
        "structured_tooth_only_fdi_accuracy",
    )
    return {
        "case_count": len(selected),
        "means": {
            metric: float(np.mean([case[metric] for case in selected]))
            for metric in metrics
        },
        "mean_absolute_cluster_count_error": float(
            np.mean(
                [
                    abs(case["predicted_instance_count"] - case["golden_instance_count"])
                    for case in selected
                ]
            )
        ),
        "duplicate_fdi_case_count": sum(
            bool(case["duplicate_fdi_labels"]) for case in selected
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--role",
        choices=("tuning", "validation"),
        help="Evaluate only the selected isolated split.",
    )
    parser.add_argument("--case-limit", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise RuntimeError("PYTORCH_ENABLE_MPS_FALLBACK=1 is forbidden.")
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is required.")
    checkpoint_paths = product.validate_checkpoint_directory_layout(args.model)
    checkpoint_sha256 = {
        role: _sha256(path) for role, path in checkpoint_paths.items()
    }
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    requested = [
        case
        for case in manifest["cases"]
        if args.role is None or case["role"] == args.role
    ]
    requested = requested[: args.case_limit]
    summary_path = args.output_dir / "evaluation_summary.json"
    cases: list[dict[str, Any]] = []
    if args.resume and summary_path.is_file():
        previous = json.loads(summary_path.read_text(encoding="utf-8"))
        if previous.get("checkpoint_sha256") != checkpoint_sha256:
            raise RuntimeError("Resume checkpoint SHA-256 does not match.")
        cases = list(previous.get("cases", []))
    completed = {str(case["key"]) for case in cases}
    started = time.perf_counter()

    original_export = product._export
    try:
        for case in requested:
            key = str(case["key"])
            if key in completed:
                continue
            mesh_path, golden_path = _case_paths(args.cases_root, case)
            case_dir = args.output_dir / "cases" / key
            captured: dict[str, Any] = {}

            def compact_export(
                mesh: Any,
                vertex_labels: np.ndarray,
                fdi_by_instance: dict[int, int],
                output_dir: Path,
            ) -> dict[str, Any]:
                output_dir.mkdir(parents=True, exist_ok=True)
                predicted_instances = np.asarray(vertex_labels, dtype=np.int16)
                predicted_fdi = np.asarray(
                    [
                        fdi_by_instance.get(int(instance_id), 0)
                        for instance_id in predicted_instances
                    ],
                    dtype=np.int16,
                )
                prediction_path = output_dir / "prediction.npz"
                np.savez_compressed(
                    prediction_path,
                    instances=predicted_instances,
                    labels=predicted_fdi,
                )
                captured["instances"] = predicted_instances
                captured["labels"] = predicted_fdi
                return {
                    "evaluation_prediction_npz": str(prediction_path.resolve()),
                    "teeth": [
                        {"instance_id": instance_id, "fdi": fdi}
                        for instance_id, fdi in sorted(fdi_by_instance.items())
                    ],
                }

            product._export = compact_export
            inference_started = time.perf_counter()
            result = product.run(
                Namespace(
                    input=mesh_path,
                    model=args.model,
                    output_dir=case_dir,
                    jaw=case["jaw"],
                    orientation="none",
                    device="mps",
                    source_archive_name=None,
                    source_archive_sha256=None,
                )
            )
            inference_seconds = time.perf_counter() - inference_started
            golden = json.loads(golden_path.read_text(encoding="utf-8"))
            golden_fdi = np.asarray(golden["labels"], dtype=np.int16)
            golden_instances = np.asarray(
                golden.get("instances", golden["labels"]), dtype=np.int16
            )
            predicted_fdi = np.asarray(captured["labels"], dtype=np.int16)
            predicted_instances = np.asarray(captured["instances"], dtype=np.int16)
            if not (
                predicted_fdi.shape
                == predicted_instances.shape
                == golden_fdi.shape
                == golden_instances.shape
            ):
                raise RuntimeError(f"{key} prediction and GT shapes differ.")
            golden_tooth = golden_fdi != 0
            duplicate_fdi = result["pipeline"]["semantic_assignment"][
                "duplicate_fdi_labels"
            ]
            candidate_mapping = result["pipeline"]["semantic_assignment"][
                "structured_unique_candidate"
            ]["fdi_by_instance"]
            structured_fdi = np.asarray(
                [
                    candidate_mapping.get(str(int(instance_id)), 0)
                    for instance_id in predicted_instances
                ],
                dtype=np.int16,
            )
            metrics = {
                "key": key,
                "jaw": case["jaw"],
                "role": case["role"],
                "stratum": case["stratum"],
                "inference_seconds": inference_seconds,
                "sample_index_sha256": result["input"]["sampling"]["index_sha256"],
                "mask_tooth_iou": _overlap(
                    predicted_fdi != 0, golden_tooth
                ),
                **_instance_metrics(predicted_instances, golden_instances),
                "exact_fdi_accuracy": float(np.mean(predicted_fdi == golden_fdi)),
                "tooth_only_fdi_accuracy": (
                    float(np.mean(predicted_fdi[golden_tooth] == golden_fdi[golden_tooth]))
                    if golden_tooth.any()
                    else 1.0
                ),
                "structured_exact_fdi_accuracy": float(
                    np.mean(structured_fdi == golden_fdi)
                ),
                "structured_tooth_only_fdi_accuracy": (
                    float(
                        np.mean(
                            structured_fdi[golden_tooth]
                            == golden_fdi[golden_tooth]
                        )
                    )
                    if golden_tooth.any()
                    else 1.0
                ),
                "predicted_instance_count": len(
                    [value for value in np.unique(predicted_instances) if value]
                ),
                "golden_instance_count": len(
                    [value for value in np.unique(golden_instances) if value]
                ),
                "duplicate_fdi_labels": duplicate_fdi,
                "first_grouping": result["pipeline"]["final_grouping"],
                "boundary_grouping": result["pipeline"]["boundary_grouping"],
                "strict": result["strict"],
                "result_summary": str(
                    (case_dir / "result_summary.json").resolve()
                ),
            }
            cases.append(metrics)
            document = {
                "schema": "tgnet_official_pair_gt_evaluation.v1",
                "evaluation_only": True,
                "ground_truth_used_by_inference": False,
                "complete": len(cases) == len(requested),
                "device": "mps",
                "mps_fallback": False,
                "checkpoint_sha256": checkpoint_sha256,
                "manifest": str(args.manifest.resolve()),
                "manifest_sha256": _sha256(args.manifest),
                "cases": cases,
                "aggregates": {
                    role: _aggregate(cases, role)
                    for role in ("tuning", "validation")
                    if any(item["role"] == role for item in cases)
                },
                "seconds_this_invocation": time.perf_counter() - started,
            }
            _write_json_atomic(summary_path, document)
            print(
                key,
                f"mask={metrics['mask_tooth_iou']:.4f}",
                f"instance={metrics['mean_golden_instance_iou']:.4f}",
                f"fdi={metrics['tooth_only_fdi_accuracy']:.4f}",
                (
                    f"clusters={metrics['predicted_instance_count']}/"
                    f"{metrics['golden_instance_count']}"
                ),
                flush=True,
            )
    finally:
        product._export = original_export

    document = json.loads(summary_path.read_text(encoding="utf-8"))
    document["complete"] = len(cases) == len(requested)
    document["aggregates"] = {
        role: _aggregate(cases, role)
        for role in ("tuning", "validation")
        if any(item["role"] == role for item in cases)
    }
    _write_json_atomic(summary_path, document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
