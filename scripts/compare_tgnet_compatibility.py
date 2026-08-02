#!/usr/bin/env python3
"""Evaluation-only TGNet semantic compatibility comparison on Teeth3DS GT."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
import trimesh
from scipy.optimize import linear_sum_assignment

from totalsegmentator_wrapper_mac.ios_checkpoint_family import (
    load_checkpoint_analysis,
)
from totalsegmentator_wrapper_mac.ios_tgnet import (
    _select_instances,
    _vertex_normals,
)
from totalsegmentator_wrapper_mac.ios_tgnet_network import (
    TGNetCheckpointModel,
    enable_per_scan_batchnorm,
    farthest_point_indices,
)


MODES = {
    "paper-equation": ("query-minus-key", "query-minus-neighbor"),
    "feature-reference-position-paper": (
        "key-minus-query",
        "query-minus-neighbor",
    ),
    "reference-implementation": ("key-minus-query", "neighbor-minus-query"),
}


def _compose_features(
    coordinates: np.ndarray,
    normals: np.ndarray,
    order: str,
) -> np.ndarray:
    if order == "coordinates-normals":
        values = (coordinates, normals)
    elif order == "normals-coordinates":
        values = (normals, coordinates)
    else:
        raise ValueError(f"unsupported TGNet feature order: {order}")
    return np.concatenate(values, axis=1).astype(np.float32)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _overlap(predicted: np.ndarray, golden: np.ndarray) -> float:
    intersection = int(np.logical_and(predicted, golden).sum())
    union = int(np.logical_or(predicted, golden).sum())
    return intersection / union if union else 1.0


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


def _case_paths(root: Path, case: dict[str, Any]) -> tuple[Path, Path]:
    key = str(case["key"])
    patient = key.rsplit("_", 1)[0]
    directory = root / str(case["jaw"]) / patient
    return directory / f"{key}.obj", directory / f"{key}.json"


def _tooth_type_mapping(confusion: np.ndarray) -> dict[str, Any]:
    rows, columns = linear_sum_assignment(-confusion)
    matched = int(confusion[rows, columns].sum())
    total = int(confusion.sum())
    return {
        "optimal_accuracy": matched / total if total else 1.0,
        "mapping": {
            str(int(predicted)): int(tooth_type + 1)
            for predicted, tooth_type in zip(rows, columns, strict=True)
        },
        "confusion_predicted_class_by_fdi_type": confusion.tolist(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epsilon", type=float, default=0.06477939854515423)
    parser.add_argument("--min-points", type=int, default=26)
    parser.add_argument("--bandwidth", type=float, default=0.08786247659500962)
    parser.add_argument("--coordinate-scale", type=float, default=1.0)
    parser.add_argument(
        "--normal-sign", type=int, choices=(-1, 1), default=1
    )
    parser.add_argument(
        "--feature-order",
        choices=("coordinates-normals", "normals-coordinates"),
        default="coordinates-normals",
    )
    parser.add_argument(
        "--modes",
        default=",".join(MODES),
        help="Comma-separated compatibility mode names.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help="Optional evaluation-only directory for fixed PGM arrays.",
    )
    args = parser.parse_args()
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise RuntimeError("PYTORCH_ENABLE_MPS_FALLBACK=1 is forbidden")
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is required.")
    device = torch.device("mps")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    _, state_dict, analysis = load_checkpoint_analysis(args.model)
    mode_results: dict[str, Any] = {}
    started = time.perf_counter()

    selected_modes = args.modes.split(",")
    unknown_modes = sorted(set(selected_modes) - set(MODES))
    if unknown_modes:
        parser.error(f"unknown modes: {unknown_modes}")
    for mode_name in selected_modes:
        attention_relation, position_relation = MODES[mode_name]
        model = TGNetCheckpointModel(
            attention_relation=attention_relation,
            position_relation=position_relation,
        )
        model.load_state_dict(state_dict, strict=True)
        model.to(device).eval()
        batchnorm_layers = enable_per_scan_batchnorm(model)
        cases: list[dict[str, Any]] = []
        confusion = np.zeros((10, 8), dtype=np.int64)
        for case in manifest["cases"]:
            mesh_path, golden_path = _case_paths(args.cases_root, case)
            mesh = trimesh.load(mesh_path, process=False, force="mesh")
            points = np.asarray(mesh.vertices, dtype=np.float32)
            center = points.mean(axis=0)
            radius = float(np.linalg.norm(points - center, axis=1).max())
            normalized = (points - center) / radius * args.coordinate_scale
            features = _compose_features(
                normalized,
                _vertex_normals(mesh) * args.normal_sign,
                args.feature_order,
            )
            indices = farthest_point_indices(
                torch.from_numpy(normalized).to(device),
                min(24_000, len(points)),
            ).cpu().numpy()
            with torch.no_grad():
                output = model.first_ins_cent_model(
                    torch.from_numpy(normalized[indices]).to(device),
                    torch.from_numpy(features[indices]).to(device),
                )
            shifted = normalized[indices] + output.offsets.cpu().numpy()
            mask = output.mask_logits.cpu().numpy().argmax(axis=1) == 1
            predicted_class = output.class_logits.cpu().numpy().argmax(axis=1)
            golden_document = json.loads(golden_path.read_text(encoding="utf-8"))
            golden_fdi = np.asarray(
                golden_document["labels"], dtype=np.int16
            )[indices]
            golden_instances = np.asarray(
                golden_document["instances"], dtype=np.int16
            )[indices]
            cache_path: Path | None = None
            if args.cache_dir is not None:
                cache_path = args.cache_dir / mode_name / f"{case['key']}.npz"
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(
                    cache_path,
                    shifted=shifted.astype(np.float32),
                    tooth_mask=mask,
                    golden_instances=golden_instances,
                    golden_fdi=golden_fdi,
                    predicted_class=predicted_class.astype(np.int16),
                    sample_indices=np.asarray(indices, dtype=np.int64),
                )
            for predicted, fdi in zip(
                predicted_class[golden_fdi > 0],
                golden_fdi[golden_fdi > 0],
                strict=True,
            ):
                tooth_type = int(fdi) % 10
                if 1 <= tooth_type <= 8:
                    confusion[int(predicted), tooth_type - 1] += 1
            labels, clusters = _select_instances(
                shifted,
                mask,
                epsilon=args.epsilon,
                min_points=args.min_points,
                mean_shift_bandwidth=args.bandwidth,
            )
            metrics = {
                "key": case["key"],
                "jaw": case["jaw"],
                "golden_teeth": case["teeth"],
                "stratum": case["stratum"],
                "role": case.get("role"),
                "sample_index_sha256": hashlib.sha256(
                    np.asarray(indices, dtype=np.int64).tobytes()
                ).hexdigest(),
                "mask_tooth_iou": _overlap(mask, golden_fdi > 0),
                "clusters": len(clusters),
                **_score_instances(labels, golden_instances),
            }
            if cache_path is not None:
                metrics["cache"] = str(cache_path.resolve())
                metrics["cache_sha256"] = _sha256(cache_path)
            cases.append(metrics)
            print(
                mode_name,
                case["key"],
                f"mask={metrics['mask_tooth_iou']:.3f}",
                f"instance={metrics['mean_golden_instance_iou']:.3f}",
                f"clusters={len(clusters)}",
                flush=True,
            )
        metric_names = (
            "mask_tooth_iou",
            "mean_golden_instance_iou",
            "matched_golden_tooth_accuracy",
        )
        mode_results[mode_name] = {
            "attention_relation": attention_relation,
            "position_relation": position_relation,
            "batchnorm_mode": "per-scan",
            "batchnorm_layers": batchnorm_layers,
            "aggregate_macro_mean": {
                name: float(np.mean([case[name] for case in cases]))
                for name in metric_names
            },
            "tooth_type_head": _tooth_type_mapping(confusion),
            "cases": cases,
        }
    document = {
        "schema": "tgnet_compatibility_comparison.v1",
        "evaluation_only": True,
        "golden_used_by_inference": False,
        "device": "mps",
        "mps_fallback_env": None,
        "seconds": time.perf_counter() - started,
        "model": {
            "source": "user-provided",
            "sha256": _sha256(args.model),
            "architecture_validation": analysis.architecture_validation,
        },
        "validation_manifest": str(args.manifest.resolve()),
        "validation_manifest_sha256": _sha256(args.manifest),
        "grouping": {
            "epsilon": args.epsilon,
            "min_points": args.min_points,
            "mean_shift_bandwidth": args.bandwidth,
        },
        "preprocessing": {
            "normalization": "subtract centroid; divide by maximum radius",
            "coordinate_scale": args.coordinate_scale,
            "normal_sign": args.normal_sign,
            "feature_order": args.feature_order,
        },
        "modes": mode_results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                name: result["aggregate_macro_mean"]
                | {
                    "tooth_type_optimal_accuracy": result["tooth_type_head"][
                        "optimal_accuracy"
                    ]
                }
                for name, result in mode_results.items()
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
