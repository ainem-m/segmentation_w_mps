#!/usr/bin/env python3
"""Evaluation-only sweep of TGNet's unpublished grouping hyperparameters.

The golden labels score fixed PGM outputs; they are never supplied to the
network or to the product inference pipeline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import torch
import trimesh
from scipy.optimize import linear_sum_assignment

from totalsegmentator_wrapper_mac.ios_checkpoint_family import (
    load_checkpoint_analysis,
)
from totalsegmentator_wrapper_mac.ios_tgnet import (
    ORIENTATION_MATRICES,
    _select_instances,
    _vertex_normals,
)
from totalsegmentator_wrapper_mac.ios_tgnet_network import (
    TGNetCheckpointModel,
    enable_per_scan_batchnorm,
    farthest_point_indices,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _float_values(text: str) -> list[float]:
    return [float(value) for value in text.split(",")]


def _int_values(text: str) -> list[int]:
    return [int(value) for value in text.split(",")]


def _score_instances(predicted: np.ndarray, golden: np.ndarray) -> dict[str, float]:
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
        else (np.array([], dtype=int), np.array([], dtype=int))
    )
    matched = int(intersections[rows, columns].sum()) if len(rows) else 0
    golden_tooth = golden > 0
    predicted_tooth = predicted > 0
    intersection = int(np.logical_and(predicted_tooth, golden_tooth).sum())
    union = int(np.logical_or(predicted_tooth, golden_tooth).sum())
    return {
        "tooth_iou": intersection / union if union else 1.0,
        "mean_golden_instance_iou": (
            float(pair_iou[rows, columns].sum() / len(golden_ids))
            if golden_ids
            else 1.0
        ),
        "matched_golden_tooth_accuracy": (
            matched / int(golden_tooth.sum()) if golden_tooth.any() else 1.0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--golden-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--orientation", default="rotate_y_180")
    parser.add_argument(
        "--attention-relation",
        choices=("query-minus-key", "key-minus-query"),
        default="key-minus-query",
    )
    parser.add_argument(
        "--position-relation",
        choices=("query-minus-neighbor", "neighbor-minus-query"),
        default="neighbor-minus-query",
    )
    parser.add_argument("--epsilons", type=_float_values)
    parser.add_argument("--min-points", type=_int_values)
    parser.add_argument("--bandwidths", type=_float_values)
    parser.add_argument("--optuna-trials", type=int, default=0)
    parser.add_argument("--seed", type=int, default=20260730)
    parser.add_argument(
        "--optuna-module-path",
        type=Path,
        default=Path("artifacts/research_python"),
    )
    args = parser.parse_args()
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise RuntimeError("PYTORCH_ENABLE_MPS_FALLBACK=1 is forbidden")
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is required for this fixed-output sweep.")
    device = torch.device("mps")

    _, state_dict, analysis = load_checkpoint_analysis(args.model)
    model = TGNetCheckpointModel(
        attention_relation=args.attention_relation,
        position_relation=args.position_relation,
    )
    model.load_state_dict(state_dict, strict=True)
    model.to(device).eval()
    batchnorm_layers = enable_per_scan_batchnorm(model)

    mesh = trimesh.load(args.input, process=False, force="mesh")
    oriented = mesh.copy()
    oriented.apply_transform(ORIENTATION_MATRICES[args.orientation])
    points = np.asarray(oriented.vertices, dtype=np.float32)
    center = points.mean(axis=0)
    radius = float(np.linalg.norm(points - center, axis=1).max())
    normalized = (points - center) / radius
    features = np.concatenate(
        (normalized, _vertex_normals(oriented)), axis=1
    ).astype(np.float32)
    indices = farthest_point_indices(
        torch.from_numpy(normalized).to(device), 24_000
    ).cpu().numpy()
    with torch.no_grad():
        output = model.first_ins_cent_model(
            torch.from_numpy(normalized[indices]).to(device),
            torch.from_numpy(features[indices]).to(device),
        )
    shifted = normalized[indices] + output.offsets.cpu().numpy()
    tooth_mask = output.mask_logits.cpu().numpy().argmax(axis=1) == 1
    class_predictions = output.class_logits.cpu().numpy().argmax(axis=1)
    golden_document = json.loads(args.golden_json.read_text(encoding="utf-8"))
    golden = np.asarray(golden_document["labels"], dtype=np.int16)[indices]
    class_by_golden_fdi = {
        str(int(fdi)): np.bincount(
            class_predictions[golden == fdi], minlength=10
        ).tolist()
        for fdi in np.unique(golden)
        if fdi
    }

    results: list[dict[str, object]] = []

    def evaluate(
        epsilon: float,
        min_points: int,
        bandwidth: float,
        *,
        trial: int | None = None,
    ) -> dict[str, object]:
        labels, clusters = _select_instances(
            shifted,
            tooth_mask,
            epsilon=epsilon,
            min_points=min_points,
            mean_shift_bandwidth=bandwidth,
        )
        result: dict[str, object] = {
            "epsilon": epsilon,
            "min_points": min_points,
            "mean_shift_bandwidth": bandwidth,
            "clusters": len(clusters),
            **_score_instances(labels, golden),
        }
        if trial is not None:
            result["trial"] = trial
        results.append(result)
        return result

    search_metadata: dict[str, object]
    if args.optuna_trials:
        sys.path.append(str(args.optuna_module_path.resolve()))
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(
            directions=("maximize", "maximize", "maximize"),
            sampler=optuna.samplers.TPESampler(seed=args.seed),
        )

        def objective(trial: optuna.Trial) -> tuple[float, float, float]:
            result = evaluate(
                trial.suggest_float("epsilon", 0.015, 0.070),
                trial.suggest_int("min_points", 5, 35),
                trial.suggest_float("mean_shift_bandwidth", 0.050, 0.160),
                trial=trial.number,
            )
            return (
                float(result["mean_golden_instance_iou"]),
                float(result["tooth_iou"]),
                float(result["matched_golden_tooth_accuracy"]),
            )

        study.optimize(objective, n_trials=args.optuna_trials)
        search_metadata = {
            "method": "optuna-tpe-multi-objective",
            "optuna_version": optuna.__version__,
            "seed": args.seed,
            "trials": args.optuna_trials,
            "directions": [
                "maximize mean_golden_instance_iou",
                "maximize tooth_iou",
                "maximize matched_golden_tooth_accuracy",
            ],
            "ranges": {
                "epsilon": [0.015, 0.070],
                "min_points": [5, 35],
                "mean_shift_bandwidth": [0.050, 0.160],
            },
            "pareto_trial_numbers": [
                trial.number for trial in study.best_trials
            ],
        }
    else:
        if not (args.epsilons and args.min_points and args.bandwidths):
            parser.error(
                "grid search requires --epsilons, --min-points, and --bandwidths"
            )
        for epsilon in args.epsilons:
            for min_points in args.min_points:
                for bandwidth in args.bandwidths:
                    evaluate(epsilon, min_points, bandwidth)
        search_metadata = {
            "method": "grid",
            "epsilons": args.epsilons,
            "min_points": args.min_points,
            "bandwidths": args.bandwidths,
        }
    results.sort(
        key=lambda item: (
            float(item["mean_golden_instance_iou"]),
            float(item["matched_golden_tooth_accuracy"]),
        ),
        reverse=True,
    )
    document = {
        "schema": "tgnet_grouping_sweep.v1",
        "evaluation_only": True,
        "golden_used_by_inference": False,
        "fixed_forward": {
            "device": "mps",
            "mps_fallback_env": None,
            "sample_points": len(indices),
            "sample_index_sha256": hashlib.sha256(
                np.asarray(indices, dtype=np.int64).tobytes()
            ).hexdigest(),
            "batchnorm_mode": "per-scan",
            "batchnorm_layers": batchnorm_layers,
            "attention_relation": args.attention_relation,
            "position_relation": args.position_relation,
            "pgm_mask_class_counts": np.bincount(
                output.mask_logits.cpu().numpy().argmax(axis=1), minlength=2
            ).tolist(),
            "pgm_tooth_type_class_counts": np.bincount(
                class_predictions, minlength=10
            ).tolist(),
            "tooth_type_class_by_golden_fdi": class_by_golden_fdi,
        },
        "artifacts": {
            "input": str(args.input.resolve()),
            "model": str(args.model.resolve()),
            "model_sha256": _sha256(args.model),
            "golden_json": str(args.golden_json.resolve()),
            "golden_sha256": _sha256(args.golden_json),
        },
        "architecture_validation": analysis.architecture_validation,
        "search": search_metadata,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(results[:10], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
