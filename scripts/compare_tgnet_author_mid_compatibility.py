#!/usr/bin/env python3
"""Evaluation-only reproduction of the published TGNet challenge-mid behavior.

This is an independent implementation of the disclosed equations, runtime
configuration, and post-processing constants.  It does not import or package
source code from the TGNet repository.
"""

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
from scipy.spatial import cKDTree
from sklearn.cluster import DBSCAN, KMeans, MeanShift

from totalsegmentator_wrapper_mac.ios_checkpoint_family import (
    load_checkpoint_analysis,
)
from totalsegmentator_wrapper_mac.ios_tgnet import _vertex_normals
from totalsegmentator_wrapper_mac.ios_tgnet_network import (
    TGNetCheckpointModel,
    enable_per_scan_batchnorm,
    farthest_point_indices,
)


AUTHOR_RUNTIME_ARCHITECTURE = {
    "input_features": 6,
    "strides": [1, 4, 4, 4, 4],
    "neighborhood_sizes": [36, 24, 24, 24, 24],
    "blocks_including_transition": [2, 3, 4, 6, 3],
    "widths": [32, 64, 128, 256, 512],
    "crop_points": 3072,
    "sample_points": 24_000,
    "attention_relation": "key-minus-query",
    "position_relation": "neighbor-minus-query",
}
AUTHOR_GROUPING = {
    "dbscan_epsilon": 0.03,
    "dbscan_min_samples": 30,
    "noise_reassignment_neighbors": 10,
    "pca_candidates": 3,
    "pca_ratio_threshold": 8.0,
    "mean_shift_bandwidth": 0.07,
}
AUTHOR_BOUNDARY_SAMPLING = {
    "neighbor_count": 40,
    "dominant_label_ratio_threshold": 0.7,
    "boundary_points": 20_000,
    "total_points": 24_000,
}
AUTHOR_BEHAVIOR_SPECIFICATION = {
    "repository": "https://github.com/limhoyeon/ToothGroupNetwork",
    "commit": "f184332d358af44dd5f96585020a6aa1d6aeb1ca",
    "files_read_only": [
        "inference_mid.py",
        "inference_pipeline_mid.py",
        "tsg_utils.py",
    ],
    "source_code_copied": False,
}
EXPECTED_CHECKPOINTS = {
    "official-mid-fps-pass": {
        "filename": "0707_cosannealing_val.h5",
        "sha256": "05fe167662da1cb9d41a5494eb56cc96506421a72e2272c54ead7f5fda5aa276",
    },
    "official-mid-boundary-pass": {
        "filename": "0711_bd_cbl_aug_test_val.h5",
        "sha256": "b54f31530726ee41136f729ec6f2f022102a7c01de07ca6f718433a4180e42ae",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _author_normalization(
    points: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    centered = np.asarray(points, dtype=np.float32) - np.asarray(
        points, dtype=np.float32
    ).mean(axis=0)
    y_min = float(centered[:, 1].min())
    y_max = float(centered[:, 1].max())
    denominator = y_max - y_min
    if not np.isfinite(denominator) or denominator <= 0:
        raise ValueError("TGNet author-mid normalization requires nonzero y extent.")
    normalized = ((centered - y_min) / denominator) * 1.8 - 0.8
    return normalized.astype(np.float32), {
        "equation": (
            "p -= mean(p,axis=0); "
            "p = ((p - min(p[:,1])) / "
            "(max(p[:,1])-min(p[:,1]))) * 1.8 - 0.8"
        ),
        "scalar_broadcast_axis": "y",
        "scale": 1.8,
        "shift": 0.8,
        "y_min_after_centering": y_min,
        "y_max_after_centering": y_max,
        "denominator": denominator,
    }


def _first_pca_eigenvalue(points: np.ndarray) -> float:
    if len(points) < 2:
        return 0.0
    covariance = np.cov(np.asarray(points, dtype=np.float64), rowvar=False)
    return float(np.linalg.eigvalsh(covariance)[-1])


def _author_group_instances(
    shifted: np.ndarray,
    tooth_mask: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    tooth_indices = np.flatnonzero(tooth_mask)
    if tooth_indices.size == 0:
        raise RuntimeError("Author-mid grouping received no predicted tooth points.")
    tooth_shifted = np.asarray(shifted, dtype=np.float64)[tooth_indices]
    dbscan = DBSCAN(
        eps=AUTHOR_GROUPING["dbscan_epsilon"],
        min_samples=AUTHOR_GROUPING["dbscan_min_samples"],
    ).fit(tooth_shifted)
    local_labels = np.asarray(dbscan.labels_, dtype=np.int64)
    non_noise_mask = local_labels >= 0
    if not non_noise_mask.any():
        raise RuntimeError(
            "Author-mid DBSCAN produced only noise; no fallback is permitted."
        )

    core_mask = np.zeros(len(local_labels), dtype=bool)
    core_mask[np.asarray(dbscan.core_sample_indices_, dtype=np.int64)] = True
    cluster_ids = [
        int(value) for value in np.unique(local_labels) if int(value) >= 0
    ]
    eigenvalues = np.asarray(
        [
            _first_pca_eigenvalue(
                tooth_shifted[(local_labels == value) & core_mask]
            )
            for value in cluster_ids
        ],
        dtype=np.float64,
    )
    split_cluster_ids: list[int] = []
    split_ratios: dict[str, float] = {}
    order = np.argsort(-eigenvalues)
    remainder = eigenvalues[order[3:]]
    if remainder.size and float(remainder.mean()) > 0:
        denominator = float(remainder.mean())
        for position in order[:3]:
            ratio = float(eigenvalues[position] / denominator)
            split_ratios[str(cluster_ids[int(position)])] = ratio
            if ratio > AUTHOR_GROUPING["pca_ratio_threshold"]:
                split_cluster_ids.append(cluster_ids[int(position)])

    next_label = max(cluster_ids) + 1
    for cluster_id in split_cluster_ids:
        members = local_labels == cluster_id
        recovered = MeanShift(
            bandwidth=AUTHOR_GROUPING["mean_shift_bandwidth"],
            cluster_all=True,
            n_jobs=1,
        ).fit(tooth_shifted[members])
        recovered_ids = np.asarray(recovered.labels_, dtype=np.int64)
        unique_recovered = np.unique(recovered_ids)
        local_labels[members] = np.asarray(
            [
                cluster_id
                if int(value) == int(unique_recovered[0])
                else next_label + int(np.where(unique_recovered == value)[0][0]) - 1
                for value in recovered_ids
            ],
            dtype=np.int64,
        )
        next_label += max(0, len(unique_recovered) - 1)

    noise_before = int((local_labels < 0).sum())
    if noise_before:
        non_noise = local_labels >= 0
        tree = cKDTree(tooth_shifted[non_noise])
        neighbours = tree.query(
            tooth_shifted[~non_noise],
            k=AUTHOR_GROUPING["noise_reassignment_neighbors"],
            workers=-1,
        )[1]
        neighbour_labels = local_labels[non_noise][
            np.asarray(neighbours, dtype=np.int64)
        ]
        replacements = []
        for values in neighbour_labels:
            unique, counts = np.unique(values, return_counts=True)
            replacements.append(int(unique[int(np.argmax(counts))]))
        local_labels[~non_noise] = np.asarray(replacements, dtype=np.int64)

    final_ids = [int(value) for value in np.unique(local_labels)]
    remap = {value: index + 1 for index, value in enumerate(final_ids)}
    labels = np.zeros(len(shifted), dtype=np.int16)
    labels[tooth_indices] = np.asarray(
        [remap[int(value)] for value in local_labels], dtype=np.int16
    )
    sizes = [int((labels == value).sum()) for value in range(1, len(remap) + 1)]
    return labels, {
        "cluster_count": len(remap),
        "cluster_sizes": sizes,
        "maximum_cluster_occupancy": max(sizes) / int(tooth_indices.size),
        "dbscan_noise_before_reassignment": noise_before,
        "noise_after_reassignment": int((local_labels < 0).sum()),
        "core_cluster_first_pca_eigenvalues": {
            str(cluster): float(value)
            for cluster, value in zip(cluster_ids, eigenvalues, strict=True)
        },
        "split_candidate_ratios": split_ratios,
        "split_clusters": split_cluster_ids,
        "pruning_events": 0,
        "fallback_events": 0,
        "minimum_cluster_pruning": False,
        "largest_cluster_recovery": False,
        "maximum_cluster_clipping": False,
        "class_or_z_pruning": False,
    }


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


def _overlap(predicted: np.ndarray, golden: np.ndarray) -> float:
    intersection = int(np.logical_and(predicted, golden).sum())
    union = int(np.logical_or(predicted, golden).sum())
    return intersection / union if union else 1.0


def _offset_metrics(
    sampled_points: np.ndarray,
    predicted_offsets: np.ndarray,
    golden_instances: np.ndarray,
) -> dict[str, Any]:
    targets: list[np.ndarray] = []
    predictions: list[np.ndarray] = []
    shifted_variances: list[float] = []
    center_errors: list[float] = []
    centers: list[np.ndarray] = []
    shifted = sampled_points + predicted_offsets
    for instance_id in [
        int(value) for value in np.unique(golden_instances) if value
    ]:
        members = golden_instances == instance_id
        center = sampled_points[members].mean(axis=0)
        centers.append(center)
        targets.append(center - sampled_points[members])
        predictions.append(predicted_offsets[members])
        shifted_variances.append(
            float(
                np.mean(
                    np.sum(
                        np.square(
                            shifted[members] - shifted[members].mean(axis=0)
                        ),
                        axis=1,
                    )
                )
            )
        )
        center_errors.append(
            float(np.linalg.norm(shifted[members].mean(axis=0) - center))
        )
    target = np.concatenate(targets, axis=0)
    prediction = np.concatenate(predictions, axis=0)
    error = prediction - target
    absolute = np.abs(error)
    huber_delta = 0.1
    huber = np.where(
        absolute <= huber_delta,
        0.5 * np.square(error),
        huber_delta * (absolute - 0.5 * huber_delta),
    )
    valid_direction = (
        np.linalg.norm(target, axis=1) > 1.0e-8
    ) & (np.linalg.norm(prediction, axis=1) > 1.0e-8)
    cosine = np.sum(
        target[valid_direction] * prediction[valid_direction], axis=1
    ) / (
        np.linalg.norm(target[valid_direction], axis=1)
        * np.linalg.norm(prediction[valid_direction], axis=1)
    )
    denominator = float(np.sum(np.square(target)))
    coefficient = (
        float(np.sum(target * prediction) / denominator)
        if denominator > 0
        else float("nan")
    )
    center_array = np.asarray(centers, dtype=np.float64)
    center_distances = np.linalg.norm(
        center_array[:, None, :] - center_array[None, :, :], axis=2
    )
    center_distances[center_distances == 0] = np.inf
    return {
        "gt_center_definition": "same-24000-sampled-points-instance-mean",
        "offset_vector_mae": float(absolute.mean()),
        "offset_vector_huber_delta_0p1": float(huber.mean()),
        "offset_cosine_mean": float(cosine.mean()),
        "diagnostic_scale_coefficient_a": coefficient,
        "shift_within_instance_variance": float(
            np.mean(shifted_variances)
        ),
        "shifted_center_to_gt_center_error": float(np.mean(center_errors)),
        "minimum_distinct_gt_center_separation": float(
            center_distances.min()
        ),
    }


def _case_paths(root: Path, case: dict[str, Any]) -> tuple[Path, Path]:
    if "_mesh_path" in case:
        return Path(case["_mesh_path"]), Path(case["_golden_path"])
    key = str(case["key"])
    patient = key.rsplit("_", 1)[0]
    directory = root / str(case["jaw"]) / patient
    return directory / f"{key}.obj", directory / f"{key}.json"


def _load_author_model(
    path: Path, device: torch.device, role: str
) -> tuple[TGNetCheckpointModel, dict[str, Any]]:
    expected = EXPECTED_CHECKPOINTS[role]
    actual_sha256 = _sha256(path)
    if path.name != expected["filename"] or actual_sha256 != expected["sha256"]:
        raise RuntimeError(
            f"{role} requires {expected['filename']} with SHA-256 "
            f"{expected['sha256']}; received {path.name} with {actual_sha256}."
        )
    _, state_dict, analysis = load_checkpoint_analysis(path)
    model = TGNetCheckpointModel(
        attention_relation=AUTHOR_RUNTIME_ARCHITECTURE["attention_relation"],
        position_relation=AUTHOR_RUNTIME_ARCHITECTURE["position_relation"],
        strides=AUTHOR_RUNTIME_ARCHITECTURE["strides"],
        nsamples=AUTHOR_RUNTIME_ARCHITECTURE["neighborhood_sizes"],
    )
    model.load_state_dict(state_dict, strict=True)
    model.to(device).eval()
    batchnorm_layers = enable_per_scan_batchnorm(model)
    return model, {
        "source": "user-provided",
        "license": "not-verified",
        "bundled_by_app": False,
        "sha256": actual_sha256,
        "role": role,
        "expected_filename": expected["filename"],
        "expected_sha256": expected["sha256"],
        "role_validation": "passed",
        "architecture_validation": analysis.architecture_validation,
        "runtime_architecture": AUTHOR_RUNTIME_ARCHITECTURE,
        "batchnorm_mode": "per-scan-statistics-batchnorm-only",
        "batchnorm_layers": batchnorm_layers,
    }


def _center_mrm_crops(
    points: np.ndarray,
    features: np.ndarray,
    crop_indices: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    crop_points = np.asarray(points[crop_indices], dtype=np.float32).copy()
    crop_features = np.asarray(features[crop_indices], dtype=np.float32).copy()
    crop_points -= crop_points.mean(axis=1, keepdims=True)
    crop_features[:, :, :3] -= crop_features[:, :, :3].mean(
        axis=1, keepdims=True
    )
    return crop_points, crop_features


def _merge_mrm_class_logits(
    point_count: int,
    crop_indices: np.ndarray,
    class_logits: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    if crop_indices.shape[:2] != class_logits.shape[:2]:
        raise ValueError("MRM crop indices and class logits have incompatible shapes.")
    if class_logits.shape[-1] != 2:
        raise ValueError("MRM semantic head must contain exactly two classes.")
    accumulated = np.zeros((point_count, 2), dtype=np.float32)
    visits = np.zeros(point_count, dtype=np.int32)
    for indices, logits in zip(crop_indices, class_logits, strict=True):
        np.add.at(accumulated, indices, logits)
        np.add.at(visits, indices, 1)
    return accumulated.argmax(axis=1) == 1, {
        "merge": "sum-class-logits-then-argmax",
        "crop_count": int(len(crop_indices)),
        "crop_points": int(crop_indices.shape[1]),
        "overlap_points": int((visits > 1).sum()),
        "unvisited_points": int((visits == 0).sum()),
        "maximum_visits": int(visits.max(initial=0)),
        "semantic_head": "second_ins_cent_model.class_logits",
    }


def _refined_mask(
    *,
    model: TGNetCheckpointModel,
    points: np.ndarray,
    features: np.ndarray,
    shifted: np.ndarray,
    preliminary_labels: np.ndarray,
    centers_from_shifted_points: bool,
    device: torch.device,
    crop_points: int = AUTHOR_RUNTIME_ARCHITECTURE["crop_points"],
) -> tuple[np.ndarray, dict[str, Any]]:
    tree = cKDTree(points)
    crop_indices: list[np.ndarray] = []
    center_source = shifted if centers_from_shifted_points else points
    for instance_id in [
        int(value) for value in np.unique(preliminary_labels) if value
    ]:
        members = preliminary_labels == instance_id
        center = center_source[members].mean(axis=0)
        crop_indices.append(
            np.asarray(
            tree.query(
                center,
                k=min(crop_points, len(points)),
                workers=-1,
            )[1],
            dtype=np.int64,
            ).reshape(-1)
        )
    if not crop_indices:
        raise RuntimeError("Author-mid MRM received no preliminary tooth clusters.")
    crop_index_array = np.stack(crop_indices, axis=0)
    crop_points, crop_features = _center_mrm_crops(
        points, features, crop_index_array
    )
    with torch.no_grad():
        output = model.second_ins_cent_model(
            torch.from_numpy(crop_points).to(device),
            torch.from_numpy(crop_features).to(device),
        )
    refined, metadata = _merge_mrm_class_logits(
        len(points),
        crop_index_array,
        output.class_logits.cpu().numpy(),
    )
    return refined, metadata | {
        "crop_center_source": (
            "shifted-preliminary-cluster-centroid"
            if centers_from_shifted_points
            else "input-seed-instance-centroid"
        ),
        "crop_coordinate_centering": "per-crop-xyz-mean",
        "batch_inference": True,
    }


def _run_grouping_module(
    *,
    model: TGNetCheckpointModel,
    points: np.ndarray,
    features: np.ndarray,
    device: torch.device,
    seed_labels: np.ndarray | None = None,
    crop_points: int = AUTHOR_RUNTIME_ARCHITECTURE["crop_points"],
) -> dict[str, Any]:
    with torch.no_grad():
        output = model.first_ins_cent_model(
            torch.from_numpy(points).to(device),
            torch.from_numpy(features).to(device),
        )
    offsets = output.offsets.cpu().numpy()
    shifted = points + offsets
    first_mask = output.mask_logits.argmax(dim=1).cpu().numpy() == 1
    point_classes = output.class_logits.argmax(dim=1).cpu().numpy()
    semantic_mask = point_classes != 0
    if seed_labels is None:
        preliminary_labels, preliminary_grouping = _author_group_instances(
            shifted, semantic_mask
        )
    else:
        preliminary_labels = np.asarray(seed_labels, dtype=np.int16)
        preliminary_grouping = {
            "cluster_count": len(
                [value for value in np.unique(preliminary_labels) if value]
            ),
            "method": "input-seed-instance-labels",
            "pruning_events": 0,
            "fallback_events": 0,
        }
    refined_mask, mrm = _refined_mask(
        model=model,
        points=points,
        features=features,
        shifted=shifted,
        preliminary_labels=preliminary_labels,
        centers_from_shifted_points=seed_labels is None,
        device=device,
        crop_points=crop_points,
    )
    if seed_labels is None:
        labels, grouping = _author_group_instances(shifted, refined_mask)
    else:
        cluster_count = len([value for value in np.unique(seed_labels) if value])
        if cluster_count <= 0:
            raise RuntimeError("Boundary pass received no initial clusters.")
        labels = np.zeros(len(points), dtype=np.int16)
        labels[refined_mask] = (
            KMeans(n_clusters=cluster_count, random_state=0, n_init=10)
            .fit_predict(shifted[refined_mask])
            .astype(np.int16)
            + 1
        )
        grouping = {
            "cluster_count": cluster_count,
            "maximum_cluster_occupancy": max(
                int((labels == value).sum())
                for value in range(1, cluster_count + 1)
            )
            / max(1, int(refined_mask.sum())),
            "pruning_events": 0,
            "fallback_events": 0,
            "method": "fixed-count-kmeans-from-initial-cluster-count",
        }
    return {
        "labels": labels,
        "mask": refined_mask,
        "first_mask": first_mask,
        "semantic_mask": semantic_mask,
        "point_classes": point_classes,
        "offsets": offsets,
        "shifted": shifted,
        "preliminary_grouping": preliminary_grouping,
        "mrm": mrm,
        "grouping": grouping,
    }


def _boundary_sample(
    *,
    all_points: np.ndarray,
    all_features: np.ndarray,
    first_points: np.ndarray,
    first_labels: np.ndarray,
    device: torch.device,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    neighbours = cKDTree(first_points).query(
        all_points,
        k=AUTHOR_BOUNDARY_SAMPLING["neighbor_count"],
        workers=-1,
    )[1]
    neighbour_labels = first_labels[np.asarray(neighbours, dtype=np.int64)]
    dominant_counts = np.asarray(
        [
            np.unique(row, return_counts=True)[1].max()
            for row in neighbour_labels
        ],
        dtype=np.int64,
    )
    boundary_mask = (
        dominant_counts / AUTHOR_BOUNDARY_SAMPLING["neighbor_count"]
        < AUTHOR_BOUNDARY_SAMPLING["dominant_label_ratio_threshold"]
    )
    nearest = cKDTree(first_points).query(all_points, k=1, workers=-1)[1]
    propagated = first_labels[np.asarray(nearest, dtype=np.int64)]
    boundary_indices = np.flatnonzero(boundary_mask)
    non_boundary_indices = np.flatnonzero(~boundary_mask)
    rng = np.random.default_rng(seed)
    selected_boundary = rng.permutation(boundary_indices)[
        : AUTHOR_BOUNDARY_SAMPLING["boundary_points"]
    ]
    remaining = (
        AUTHOR_BOUNDARY_SAMPLING["total_points"] - len(selected_boundary)
    )
    local_non_boundary = farthest_point_indices(
        torch.from_numpy(all_points[non_boundary_indices]).to(device),
        remaining,
    ).cpu().numpy()
    selected_non_boundary = non_boundary_indices[local_non_boundary]
    selected = np.concatenate((selected_boundary, selected_non_boundary))
    return (
        selected,
        all_features[selected],
        propagated[selected],
        {
            "boundary_vertices": int(len(boundary_indices)),
            "boundary_selected": int(len(selected_boundary)),
            "non_boundary_selected": int(len(selected_non_boundary)),
            "uniform_random_seed": seed,
        },
    )


def _author_boundary_merge(
    *,
    all_points: np.ndarray,
    first_points: np.ndarray,
    first_labels: np.ndarray,
    boundary_points: np.ndarray,
    boundary_labels: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    nearest_first = cKDTree(first_points).query(
        boundary_points, k=1, workers=-1
    )[1]
    nearest_first_labels = first_labels[
        np.asarray(nearest_first, dtype=np.int64)
    ]
    remapped_boundary = np.zeros_like(boundary_labels, dtype=np.int16)
    cluster_remap: dict[str, int] = {}
    for cluster_id in [
        int(value) for value in np.unique(boundary_labels) if value
    ]:
        members = boundary_labels == cluster_id
        values, counts = np.unique(
            nearest_first_labels[members], return_counts=True
        )
        target = int(values[int(np.argmax(counts))])
        cluster_remap[str(cluster_id)] = target
        remapped_boundary[members] = target

    combined_points = np.concatenate((first_points, boundary_points), axis=0)
    combined_labels = np.concatenate(
        (first_labels, remapped_boundary), axis=0
    )
    nearest_combined = cKDTree(combined_points).query(
        all_points, k=1, workers=-1
    )[1]
    return combined_labels[
        np.asarray(nearest_combined, dtype=np.int64)
    ], {
        "cluster_remap": cluster_remap,
        "boundary_clusters": len(cluster_remap),
        "first_points": int(len(first_points)),
        "boundary_points": int(len(boundary_points)),
        "propagation": "one-nearest-neighbor-over-first-plus-boundary-points",
        "pruning_events": 0,
        "fallback_events": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--cases-root", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--golden-json", type=Path)
    parser.add_argument(
        "--orientation", choices=("none", "rotate-y-180"), default="none"
    )
    parser.add_argument("--fps-model", type=Path, required=True)
    parser.add_argument("--boundary-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--sample-index-cache",
        type=Path,
        help=(
            "Directory containing reference-implementation/<case>.npz; "
            "required for index-identical comparison with the 0930 path."
        ),
    )
    args = parser.parse_args()
    if (args.input is None) != (args.golden_json is None):
        parser.error("--input and --golden-json must be provided together.")
    if args.input is None and (args.manifest is None or args.cases_root is None):
        parser.error(
            "--manifest and --cases-root are required for the multi-case run."
        )
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise RuntimeError("PYTORCH_ENABLE_MPS_FALLBACK=1 is forbidden.")
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is required.")
    device = torch.device("mps")
    fps_model, fps_metadata = _load_author_model(
        args.fps_model, device, "official-mid-fps-pass"
    )
    boundary_model, boundary_metadata = _load_author_model(
        args.boundary_model, device, "official-mid-boundary-pass"
    )

    if args.input is not None:
        requested_cases = [
            {
                "key": args.input.stem,
                "jaw": "upper",
                "role": "golden",
                "stratum": "golden",
                "_mesh_path": str(args.input.resolve()),
                "_golden_path": str(args.golden_json.resolve()),
            }
        ]
    else:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        requested_cases = manifest["cases"][: args.case_limit]
    cases: list[dict[str, Any]] = []
    if args.resume and args.output.exists():
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        cases = list(previous.get("cases", []))
    resumed_case_count = len(cases)
    completed_keys = {str(case["key"]) for case in cases}
    started = time.perf_counter()
    for case_index, case in enumerate(requested_cases):
        if str(case["key"]) in completed_keys:
            continue
        mesh_path, golden_path = _case_paths(args.cases_root, case)
        mesh = trimesh.load(mesh_path, process=False, force="mesh")
        if args.orientation == "rotate-y-180":
            mesh.apply_transform(
                np.asarray(
                    [
                        [-1.0, 0.0, 0.0, 0.0],
                        [0.0, 1.0, 0.0, 0.0],
                        [0.0, 0.0, -1.0, 0.0],
                        [0.0, 0.0, 0.0, 1.0],
                    ],
                    dtype=np.float64,
                )
            )
        points, normalization = _author_normalization(
            np.asarray(mesh.vertices, dtype=np.float32)
        )
        features = np.concatenate(
            (points, _vertex_normals(mesh)), axis=1
        ).astype(np.float32)
        if args.sample_index_cache is not None:
            index_path = (
                args.sample_index_cache
                / "reference-implementation"
                / f"{case['key']}.npz"
            )
            with np.load(index_path) as cached:
                sample_indices = np.asarray(
                    cached["sample_indices"], dtype=np.int64
                )
            if (
                sample_indices.shape
                != (min(AUTHOR_RUNTIME_ARCHITECTURE["sample_points"], len(points)),)
                or len(np.unique(sample_indices)) != len(sample_indices)
                or sample_indices.min() < 0
                or sample_indices.max() >= len(points)
            ):
                raise RuntimeError(
                    f"Invalid fixed FPS index cache for {case['key']}: {index_path}"
                )
        else:
            sample_indices = farthest_point_indices(
                torch.from_numpy(points).to(device),
                min(AUTHOR_RUNTIME_ARCHITECTURE["sample_points"], len(points)),
            ).cpu().numpy()
        first = _run_grouping_module(
            model=fps_model,
            points=points[sample_indices],
            features=features[sample_indices],
            device=device,
        )
        selected, boundary_features, seed_labels, boundary_sampling = (
            _boundary_sample(
                all_points=points,
                all_features=features,
                first_points=points[sample_indices],
                first_labels=first["labels"],
                device=device,
                seed=20260731 + case_index,
            )
        )
        boundary = _run_grouping_module(
            model=boundary_model,
            points=points[selected],
            features=boundary_features,
            device=device,
            seed_labels=seed_labels,
        )
        boundary_only_count = int(boundary_sampling["boundary_selected"])
        full_labels, boundary_merge = _author_boundary_merge(
            all_points=points,
            first_points=points[sample_indices],
            first_labels=first["labels"],
            boundary_points=points[selected[:boundary_only_count]],
            boundary_labels=boundary["labels"][:boundary_only_count],
        )
        sampled_final = full_labels[sample_indices]

        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        golden_instances = np.asarray(
            golden.get("instances", golden["labels"]), dtype=np.int16
        )[sample_indices]
        golden_fdi = np.asarray(golden["labels"], dtype=np.int16)[sample_indices]
        predicted_class = np.asarray(first["point_classes"], dtype=np.int16)
        valid_type = golden_fdi > 0
        point_type_accuracy = float(
            np.mean(predicted_class[valid_type] == (golden_fdi[valid_type] % 10))
        )
        instance_correct: list[bool] = []
        for instance_id in [
            int(value) for value in np.unique(golden_instances) if value
        ]:
            members = golden_instances == instance_id
            values, counts = np.unique(predicted_class[members], return_counts=True)
            predicted_type = int(values[int(np.argmax(counts))])
            fdi_values, fdi_counts = np.unique(
                golden_fdi[members], return_counts=True
            )
            target_type = int(fdi_values[int(np.argmax(fdi_counts))]) % 10
            instance_correct.append(predicted_type == target_type)
        offset_norm = np.linalg.norm(first["offsets"], axis=1)
        metrics = {
            "key": case["key"],
            "jaw": case["jaw"],
            "role": case["role"],
            "stratum": case["stratum"],
            "sample_index_sha256": hashlib.sha256(
                np.asarray(sample_indices, dtype=np.int64).tobytes()
            ).hexdigest(),
            "normalization": normalization,
            "mask_tooth_iou": _overlap(first["mask"], golden_fdi > 0),
            "first_head_mask_tooth_iou": _overlap(
                first["first_mask"], golden_fdi > 0
            ),
            "semantic_head_mask_tooth_iou": _overlap(
                first["semantic_mask"], golden_fdi > 0
            ),
            "tooth_type_point_accuracy": point_type_accuracy,
            "tooth_type_instance_accuracy": float(np.mean(instance_correct)),
            **_score_instances(sampled_final, golden_instances),
            "first_pass_grouping": first["grouping"],
            "first_pass_preliminary_grouping": first[
                "preliminary_grouping"
            ],
            "first_pass_mrm": first["mrm"],
            "boundary_pass_grouping": boundary["grouping"],
            "boundary_pass_mrm": boundary["mrm"],
            "boundary_merge": boundary_merge,
            "boundary_sampling": boundary_sampling,
            "offset": {
                "component_mean": first["offsets"].mean(axis=0).tolist(),
                "component_std": first["offsets"].std(axis=0).tolist(),
                "norm_mean": float(offset_norm.mean()),
                "norm_std": float(offset_norm.std()),
                "norm_max": float(offset_norm.max()),
            }
            | _offset_metrics(
                points[sample_indices],
                first["offsets"],
                golden_instances,
            ),
        }
        cases.append(metrics)
        partial = {
            "schema": "tgnet_author_challenge_mid_compatibility.v1",
            "evaluation_only": True,
            "complete": len(cases) == len(requested_cases),
            "device": "mps",
            "mps_fallback": False,
            "checkpoints": [fps_metadata, boundary_metadata],
            "runtime_architecture": AUTHOR_RUNTIME_ARCHITECTURE,
            "grouping": AUTHOR_GROUPING,
            "boundary_sampling": AUTHOR_BOUNDARY_SAMPLING,
            "behavior_specification": AUTHOR_BEHAVIOR_SPECIFICATION,
            "orientation": args.orientation,
            "fixed_fps_index_source": (
                str(args.sample_index_cache.resolve())
                if args.sample_index_cache is not None
                else "computed-by-this-harness"
            ),
            "cases": cases,
        }
        _write_json_atomic(args.output, partial)
        print(
            case["key"],
            f"mask={metrics['mask_tooth_iou']:.3f}",
            f"instance={metrics['mean_golden_instance_iou']:.3f}",
            f"clusters={metrics['first_pass_grouping']['cluster_count']}",
            flush=True,
        )

    metric_names = (
        "mask_tooth_iou",
        "tooth_type_point_accuracy",
        "tooth_type_instance_accuracy",
        "mean_golden_instance_iou",
        "matched_golden_tooth_accuracy",
    )
    document = json.loads(args.output.read_text(encoding="utf-8"))
    document["complete"] = True
    if len(cases) > resumed_case_count or "seconds" not in document:
        document["seconds"] = time.perf_counter() - started
    document["checkpoints"] = [fps_metadata, boundary_metadata]
    document["runtime_architecture"] = AUTHOR_RUNTIME_ARCHITECTURE
    document["grouping"] = AUTHOR_GROUPING
    document["boundary_sampling"] = AUTHOR_BOUNDARY_SAMPLING
    document["behavior_specification"] = AUTHOR_BEHAVIOR_SPECIFICATION
    available_roles = sorted({str(case["role"]) for case in cases})
    document["aggregate_by_role"] = {
        role: {
            name: float(
                np.mean(
                    [case[name] for case in cases if case["role"] == role]
                )
            )
            for name in metric_names
        }
        for role in available_roles
    }
    document["strict"] = {
        "pruning_events": sum(
            case["first_pass_grouping"]["pruning_events"]
            + case["boundary_pass_grouping"]["pruning_events"]
            for case in cases
        ),
        "fallback_events": sum(
            case["first_pass_grouping"]["fallback_events"]
            + case["boundary_pass_grouping"]["fallback_events"]
            for case in cases
        ),
    }
    _write_json_atomic(args.output, document)
    print(json.dumps(document["aggregate_by_role"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
