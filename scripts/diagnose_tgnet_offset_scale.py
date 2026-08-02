#!/usr/bin/env python3
"""Identify TGNet coordinate scaling directly from ground-truth offsets.

This evaluation-only harness deliberately does not call DBSCAN, Mean Shift,
candidate pruning, MRM, BAPS, or FDI assignment. Ground truth is used only to
measure fixed PGM outputs and is never supplied to product inference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import torch
import trimesh
from scipy.optimize import least_squares, linear_sum_assignment
from scipy.spatial.distance import pdist

from totalsegmentator_wrapper_mac.ios_checkpoint_family import (
    load_checkpoint_analysis,
)
from totalsegmentator_wrapper_mac.ios_tgnet import _vertex_normals
from totalsegmentator_wrapper_mac.ios_tgnet_network import (
    TGNetCheckpointModel,
    enable_per_scan_batchnorm,
    farthest_point_indices,
)


NORMALIZATIONS = (
    "mean-max-radius",
    "mean-max-axis",
    "bbox-max-radius",
)
CENTER_DEFINITIONS = (
    "sampled-mean",
    "sampled-median",
    "sampled-bbox",
    "all-vertex-mean",
    "all-vertex-median",
    "all-vertex-bbox",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _float_values(text: str) -> list[float]:
    return [float(value) for value in text.split(",")]


def _string_values(text: str) -> list[str]:
    return [value.strip() for value in text.split(",") if value.strip()]


def _case_paths(root: Path, case: dict[str, Any]) -> tuple[Path, Path]:
    key = str(case["key"])
    patient = key.rsplit("_", 1)[0]
    directory = root / str(case["jaw"]) / patient
    return directory / f"{key}.obj", directory / f"{key}.json"


def _normalization(
    points: np.ndarray, formula: str, scale: float
) -> tuple[np.ndarray, dict[str, Any]]:
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    if formula.startswith("mean-"):
        origin = points.mean(axis=0)
    elif formula.startswith("bbox-"):
        origin = (minimum + maximum) / 2.0
    else:
        raise ValueError(f"unsupported normalization formula: {formula}")
    centered = points - origin
    if formula.endswith("max-radius"):
        denominator = float(np.linalg.norm(centered, axis=1).max())
    elif formula.endswith("max-axis"):
        denominator = float(np.abs(centered).max())
    else:
        raise ValueError(f"unsupported normalization formula: {formula}")
    if not np.isfinite(denominator) or denominator <= 0:
        raise ValueError("normalization denominator must be positive and finite")
    normalized = centered / denominator * scale
    return normalized.astype(np.float32), {
        "formula": formula,
        "origin": np.asarray(origin, dtype=float).tolist(),
        "denominator": denominator,
        "global_scale": scale,
    }


def _center(values: np.ndarray, statistic: str) -> np.ndarray:
    if statistic == "mean":
        return values.mean(axis=0)
    if statistic == "median":
        return np.median(values, axis=0)
    if statistic == "bbox":
        return (values.min(axis=0) + values.max(axis=0)) / 2.0
    raise ValueError(f"unsupported center statistic: {statistic}")


def _instance_centers(
    sampled_points: np.ndarray,
    sampled_instances: np.ndarray,
    all_points: np.ndarray,
    all_instances: np.ndarray,
    definition: str,
) -> dict[int, np.ndarray]:
    population, statistic = definition.rsplit("-", 1)
    instance_ids = [int(value) for value in np.unique(sampled_instances) if value]
    centers: dict[int, np.ndarray] = {}
    for instance_id in instance_ids:
        values = (
            sampled_points[sampled_instances == instance_id]
            if population == "sampled"
            else all_points[all_instances == instance_id]
        )
        if values.size == 0:
            raise ValueError(
                f"center definition {definition} has no points for "
                f"instance {instance_id}"
            )
        centers[instance_id] = _center(values, statistic)
    return centers


def _robust_scalar_coefficient(
    predicted: np.ndarray, target: np.ndarray
) -> float:
    predicted_flat = np.asarray(predicted, dtype=np.float64).reshape(-1)
    target_flat = np.asarray(target, dtype=np.float64).reshape(-1)
    denominator = float(np.dot(target_flat, target_flat))
    initial = (
        float(np.dot(target_flat, predicted_flat) / denominator)
        if denominator > 0
        else 1.0
    )
    fitted = least_squares(
        lambda value: predicted_flat - value[0] * target_flat,
        x0=np.asarray([initial], dtype=np.float64),
        loss="huber",
        f_scale=0.05,
        max_nfev=100,
    )
    return float(fitted.x[0])


def _offset_metrics(
    *,
    sampled_points: np.ndarray,
    predicted_offsets: np.ndarray,
    sampled_instances: np.ndarray,
    all_points: np.ndarray,
    all_instances: np.ndarray,
    center_definition: str,
) -> dict[str, float]:
    tooth = sampled_instances > 0
    if not tooth.any():
        raise ValueError("ground truth has no sampled tooth points")
    centers = _instance_centers(
        sampled_points,
        sampled_instances,
        all_points,
        all_instances,
        center_definition,
    )
    target = np.zeros_like(sampled_points, dtype=np.float32)
    for instance_id, center in centers.items():
        selected = sampled_instances == instance_id
        target[selected] = center - sampled_points[selected]
    predicted = predicted_offsets[tooth]
    expected = target[tooth]
    residual = predicted - expected
    absolute = np.abs(residual)
    huber_delta = 0.05
    huber = np.where(
        absolute <= huber_delta,
        0.5 * absolute**2,
        huber_delta * (absolute - 0.5 * huber_delta),
    )
    predicted_norm = np.linalg.norm(predicted, axis=1)
    target_norm = np.linalg.norm(expected, axis=1)
    valid_cosine = (predicted_norm > 1.0e-8) & (target_norm > 1.0e-8)
    cosine = (
        np.sum(predicted[valid_cosine] * expected[valid_cosine], axis=1)
        / (predicted_norm[valid_cosine] * target_norm[valid_cosine])
        if valid_cosine.any()
        else np.asarray([0.0])
    )
    vector_error = np.linalg.norm(residual, axis=1)
    mean_target_norm = float(target_norm.mean())
    coefficient = _robust_scalar_coefficient(predicted, expected)

    shifted = sampled_points[tooth] + predicted
    tooth_instances = sampled_instances[tooth]
    predicted_centers: list[np.ndarray] = []
    target_centers: list[np.ndarray] = []
    within_variances: list[float] = []
    center_errors: list[float] = []
    for instance_id, target_center in centers.items():
        instance_shifted = shifted[tooth_instances == instance_id]
        predicted_center = instance_shifted.mean(axis=0)
        predicted_centers.append(predicted_center)
        target_centers.append(target_center)
        within_variances.append(
            float(
                np.mean(
                    np.sum(
                        np.square(instance_shifted - predicted_center), axis=1
                    )
                )
            )
        )
        center_errors.append(
            float(np.linalg.norm(predicted_center - target_center))
        )
    predicted_center_array = np.asarray(predicted_centers)
    target_center_array = np.asarray(target_centers)
    predicted_min_separation = (
        float(pdist(predicted_center_array).min())
        if len(predicted_center_array) > 1
        else 0.0
    )
    target_min_separation = (
        float(pdist(target_center_array).min())
        if len(target_center_array) > 1
        else 0.0
    )
    mean_within = float(np.mean(within_variances))
    return {
        "offset_huber_component_mean": float(huber.mean()),
        "offset_mae_component_mean": float(absolute.mean()),
        "offset_vector_mae": float(vector_error.mean()),
        "offset_relative_vector_mae": (
            float(vector_error.mean() / mean_target_norm)
            if mean_target_norm > 0
            else math.inf
        ),
        "offset_cosine_mean": float(cosine.mean()),
        "robust_scale_coefficient_a": coefficient,
        "absolute_a_minus_one": abs(coefficient - 1.0),
        "shift_within_instance_variance": mean_within,
        "shift_center_to_gt_center_error": float(np.mean(center_errors)),
        "predicted_min_intertooth_separation": predicted_min_separation,
        "gt_min_intertooth_separation": target_min_separation,
        "intertooth_separation_ratio": (
            predicted_min_separation / target_min_separation
            if target_min_separation > 0
            else 0.0
        ),
        "separation_to_within_spread": (
            predicted_min_separation / math.sqrt(mean_within)
            if mean_within > 0
            else math.inf
        ),
    }


def _mask_iou(predicted: np.ndarray, golden: np.ndarray) -> float:
    intersection = int(np.logical_and(predicted, golden).sum())
    union = int(np.logical_or(predicted, golden).sum())
    return intersection / union if union else 1.0


def _tooth_type_accuracy(confusion: np.ndarray) -> dict[str, Any]:
    rows, columns = linear_sum_assignment(-confusion)
    matched = int(confusion[rows, columns].sum())
    total = int(confusion.sum())
    return {
        "optimal_mapping_upper_bound_accuracy": matched / total if total else 1.0,
        "mapping": {
            str(int(row)): int(column + 1)
            for row, column in zip(rows, columns, strict=True)
        },
        "confusion_predicted_class_by_fdi_type": confusion.tolist(),
    }


def _cache_path(
    cache_dir: Path, formula: str, scale: float, key: str
) -> Path:
    scale_name = format(scale, ".8g").replace(".", "p").replace("-", "m")
    return cache_dir / formula / f"scale_{scale_name}" / f"{key}.npz"


def _load_or_infer(
    *,
    cache_path: Path,
    model: TGNetCheckpointModel,
    normalized: np.ndarray,
    normals: np.ndarray,
    indices: np.ndarray,
    device: torch.device,
    model_sha256: str,
    formula: str,
    scale: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, bool]:
    index_hash = hashlib.sha256(indices.tobytes()).hexdigest()
    if cache_path.is_file():
        with np.load(cache_path, allow_pickle=False) as cached:
            if (
                str(cached["model_sha256"]) != model_sha256
                or str(cached["formula"]) != formula
                or float(cached["scale"]) != scale
                or str(cached["sample_index_sha256"]) != index_hash
            ):
                raise ValueError(f"stale or incompatible offset cache: {cache_path}")
            return (
                np.asarray(cached["predicted_offsets"], dtype=np.float32),
                np.asarray(cached["tooth_mask"], dtype=bool),
                np.asarray(cached["predicted_class"], dtype=np.int16),
                True,
            )
    features = np.concatenate((normalized, normals), axis=1).astype(np.float32)
    with torch.no_grad():
        output = model.first_ins_cent_model(
            torch.from_numpy(normalized[indices]).to(device),
            torch.from_numpy(features[indices]).to(device),
        )
    if device.type == "mps":
        torch.mps.synchronize()
    predicted_offsets = output.offsets.detach().cpu().numpy().astype(np.float32)
    tooth_mask = (
        output.mask_logits.detach().cpu().numpy().argmax(axis=1) == 1
    )
    predicted_class = (
        output.class_logits.detach().cpu().numpy().argmax(axis=1)
    ).astype(np.int16)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(".tmp.npz")
    np.savez_compressed(
        temporary,
        predicted_offsets=predicted_offsets,
        tooth_mask=tooth_mask,
        predicted_class=predicted_class,
        sample_indices=indices.astype(np.int64),
        model_sha256=np.asarray(model_sha256),
        formula=np.asarray(formula),
        scale=np.asarray(scale),
        sample_index_sha256=np.asarray(index_hash),
    )
    temporary.replace(cache_path)
    return predicted_offsets, tooth_mask, predicted_class, False


def _aggregate(
    case_metrics: list[dict[str, Any]], confusion: np.ndarray
) -> dict[str, Any]:
    metric_names = [
        name
        for name, value in case_metrics[0].items()
        if isinstance(value, (float, int)) and name not in {"golden_teeth"}
    ]
    macro = {
        name: float(np.mean([float(case[name]) for case in case_metrics]))
        for name in metric_names
    }
    coefficients = [
        float(case["robust_scale_coefficient_a"]) for case in case_metrics
    ]
    macro["robust_scale_coefficient_a_between_case_std"] = float(
        np.std(coefficients)
    )
    return {
        "aggregate_macro_mean": macro,
        "tooth_type": _tooth_type_accuracy(confusion),
        "cases": case_metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument(
        "--normalizations",
        type=_string_values,
        default=list(NORMALIZATIONS),
    )
    parser.add_argument(
        "--scales",
        type=_float_values,
        default=[0.75, 1.0, 1.25, 1.5],
    )
    parser.add_argument(
        "--offset-multipliers",
        type=_float_values,
        default=[1.0],
    )
    args = parser.parse_args()
    unknown = sorted(set(args.normalizations) - set(NORMALIZATIONS))
    if unknown:
        parser.error(f"unsupported normalizations: {unknown}")
    if not args.scales or any(scale <= 0 for scale in args.scales):
        parser.error("--scales must contain positive values")
    if not args.offset_multipliers or any(
        multiplier <= 0 for multiplier in args.offset_multipliers
    ):
        parser.error("--offset-multipliers must contain positive values")
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise RuntimeError("PYTORCH_ENABLE_MPS_FALLBACK=1 is forbidden")
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is required for this offset diagnostic.")

    started = time.perf_counter()
    device = torch.device("mps")
    model_sha256 = _sha256(args.model)
    _, state_dict, analysis = load_checkpoint_analysis(args.model)
    model = TGNetCheckpointModel()
    model.load_state_dict(state_dict, strict=True)
    model.to(device).eval()
    batchnorm_layers = enable_per_scan_batchnorm(model)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    variants = [
        (formula, float(scale), float(multiplier))
        for formula in args.normalizations
        for scale in args.scales
        for multiplier in args.offset_multipliers
    ]
    role_results: dict[
        str, dict[tuple[str, float, str], list[dict[str, Any]]]
    ] = {
        "tuning": defaultdict(list),
        "validation": defaultdict(list),
    }
    role_confusions: dict[
        str, dict[tuple[str, float], np.ndarray]
    ] = {
        "tuning": defaultdict(lambda: np.zeros((10, 8), dtype=np.int64)),
        "validation": defaultdict(lambda: np.zeros((10, 8), dtype=np.int64)),
    }
    cache_records: list[dict[str, Any]] = []

    def evaluate_role(role: str) -> None:
        for case in manifest["cases"]:
            if case.get("role") != role:
                continue
            mesh_path, golden_path = _case_paths(args.cases_root, case)
            mesh = trimesh.load(mesh_path, process=False, force="mesh")
            points = np.asarray(mesh.vertices, dtype=np.float32)
            normals = _vertex_normals(mesh)
            golden_document = json.loads(
                golden_path.read_text(encoding="utf-8")
            )
            all_instances = np.asarray(
                golden_document["instances"], dtype=np.int16
            )
            all_fdi = np.asarray(golden_document["labels"], dtype=np.int16)
            fps_reference, _ = _normalization(
                points, "mean-max-radius", 1.0
            )
            indices = (
                farthest_point_indices(
                    torch.from_numpy(fps_reference).to(device),
                    min(24_000, len(points)),
                )
                .cpu()
                .numpy()
                .astype(np.int64)
            )
            index_hash = hashlib.sha256(indices.tobytes()).hexdigest()
            sampled_instances = all_instances[indices]
            sampled_fdi = all_fdi[indices]
            for formula, scale, offset_multiplier in variants:
                normalized, normalization_metadata = _normalization(
                    points, formula, scale
                )
                path = _cache_path(
                    args.cache_dir, formula, scale, str(case["key"])
                )
                predicted_offsets, tooth_mask, predicted_class, reused = (
                    _load_or_infer(
                        cache_path=path,
                        model=model,
                        normalized=normalized,
                        normals=normals,
                        indices=indices,
                        device=device,
                        model_sha256=model_sha256,
                        formula=formula,
                        scale=scale,
                    )
                )
                variant_confusion = role_confusions[role][(formula, scale)]
                for predicted, fdi in zip(
                    predicted_class[sampled_fdi > 0],
                    sampled_fdi[sampled_fdi > 0],
                    strict=True,
                ):
                    tooth_type = int(fdi) % 10
                    if 1 <= tooth_type <= 8:
                        variant_confusion[int(predicted), tooth_type - 1] += 1
                auxiliary = {
                    "mask_tooth_iou": _mask_iou(
                        tooth_mask, sampled_fdi > 0
                    ),
                }
                for center_definition in CENTER_DEFINITIONS:
                    metrics = _offset_metrics(
                        sampled_points=normalized[indices],
                        predicted_offsets=(
                            predicted_offsets * offset_multiplier
                        ),
                        sampled_instances=sampled_instances,
                        all_points=normalized,
                        all_instances=all_instances,
                        center_definition=center_definition,
                    )
                    role_results[role][
                        (
                            formula,
                            scale,
                            offset_multiplier,
                            center_definition,
                        )
                    ].append(
                        {
                            "key": case["key"],
                            "jaw": case["jaw"],
                            "stratum": case["stratum"],
                            "golden_teeth": case["teeth"],
                            "sample_index_sha256": index_hash,
                            **auxiliary,
                            **metrics,
                        }
                    )
                cache_records.append(
                    {
                        "key": case["key"],
                        "role": role,
                        "formula": formula,
                        "scale": scale,
                        "offset_multiplier": offset_multiplier,
                        "path": str(path.resolve()),
                        "sha256": _sha256(path),
                        "reused": reused,
                        "sample_index_sha256": index_hash,
                        "normalization": normalization_metadata,
                    }
                )
                print(
                    role,
                    case["key"],
                    formula,
                    f"scale={scale:g}",
                    f"offset_multiplier={offset_multiplier:g}",
                    "cache" if reused else "mps",
                    flush=True,
                )

    # Candidate selection is frozen from tuning before validation is evaluated.
    evaluate_role("tuning")
    tuning_aggregates: dict[str, Any] = {}
    ranking: list[dict[str, Any]] = []
    for (
        formula,
        scale,
        offset_multiplier,
        center_definition,
    ), cases in role_results[
        "tuning"
    ].items():
        aggregate = _aggregate(
            cases, role_confusions["tuning"][(formula, scale)]
        )
        key = (
            f"{formula}|{scale:g}|offsetx{offset_multiplier:g}|"
            f"{center_definition}"
        )
        tuning_aggregates[key] = aggregate
        metrics = aggregate["aggregate_macro_mean"]
        ranking.append(
            {
                "key": key,
                "normalization": formula,
                "scale": scale,
                "offset_multiplier": offset_multiplier,
                "center_definition": center_definition,
                **metrics,
            }
        )
    ranking.sort(
        key=lambda item: (
            item["absolute_a_minus_one"],
            item["offset_relative_vector_mae"],
            -item["offset_cosine_mean"],
            item["shift_center_to_gt_center_error"],
        )
    )
    frozen_selection = dict(ranking[0])
    frozen_selection["selection_order"] = [
        "minimum macro mean |robust coefficient a - 1|",
        "minimum macro mean relative vector MAE",
        "maximum macro mean cosine agreement",
        "minimum macro mean shifted-center error",
    ]
    evaluate_role("validation")
    validation_aggregates: dict[str, Any] = {}
    for (
        formula,
        scale,
        offset_multiplier,
        center_definition,
    ), cases in role_results[
        "validation"
    ].items():
        key = (
            f"{formula}|{scale:g}|offsetx{offset_multiplier:g}|"
            f"{center_definition}"
        )
        validation_aggregates[key] = _aggregate(
            cases, role_confusions["validation"][(formula, scale)]
        )

    document = {
        "schema": "tgnet_offset_scale_diagnostic.v1",
        "evaluation_only": True,
        "ground_truth_used_by_inference": False,
        "postprocessing_used": {
            "dbscan": False,
            "mean_shift": False,
            "candidate_pruning": False,
            "mrm": False,
            "baps": False,
        },
        "device": "mps",
        "mps_fallback_env": None,
        "batchnorm_mode": "per-scan",
        "batchnorm_layers": batchnorm_layers,
        "model": {
            "model_family": "tgnet",
            "source": "user-provided",
            "license": "not-verified",
            "bundled_by_app": False,
            "sha256": model_sha256,
            "architecture_validation": analysis.architecture_validation,
        },
        "architecture_mode": {
            "attention_relation": "key-minus-query",
            "position_relation": "neighbor-minus-query",
            "first_level_neighbors": 8,
            "deeper_level_neighbors": 16,
            "multiscale_head_interpolation_neighbors": 1,
        },
        "manifest": {
            "path": str(args.manifest.resolve()),
            "sha256": _sha256(args.manifest),
            "dataset": manifest["dataset"],
            "split": manifest["split"],
        },
        "search_space": {
            "normalizations": args.normalizations,
            "scales": args.scales,
            "offset_multipliers": args.offset_multipliers,
            "center_definitions": list(CENTER_DEFINITIONS),
            "huber_delta": 0.05,
        },
        "selection": {
            "source_role": "tuning",
            "frozen_before_validation": True,
            "selected": frozen_selection,
        },
        "tuning_ranking": ranking,
        "tuning": tuning_aggregates,
        "validation": validation_aggregates,
        "cache": {
            "directory": str(args.cache_dir.resolve()),
            "records": cache_records,
        },
        "seconds": time.perf_counter() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(document["selection"], ensure_ascii=False, indent=2))
    print(f"wrote {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
