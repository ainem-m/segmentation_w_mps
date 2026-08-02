"""Research TGNet adapter for a user-provided compatible checkpoint.

The architecture and pipeline are independently implemented from the SNU
thesis. Values absent from the thesis are explicitly recorded as inferences in
the result JSON. No TGNet weights or upstream implementation are distributed.
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
from scipy.spatial import cKDTree

from .ios_checkpoint_family import (
    load_checkpoint_analysis,
    tgnet_model_metadata,
)
from .ios_tgnet_network import (
    TGNetCheckpointModel,
    enable_per_scan_batchnorm,
    farthest_point_indices,
)


ORIENTATION_MATRICES = {
    "none": np.eye(4),
    "rotate_y_180": np.diag((-1.0, 1.0, -1.0, 1.0)),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


_GROUPING_SELECTION = {
    "method": "evaluation-only Optuna tuning with isolated validation",
    "dataset_cases": {"tuning": 10, "validation": 10},
    "selected_trial": 67,
    "artifact_sha256": (
        "2c337353e790f8ef094d7191111a8d829686ee70a34057012e799369c0915c8b"
    ),
    "ground_truth_consumed_by_inference": False,
    "arch_candidate_pruning": False,
}


def inferred_pipeline_metadata(
    *,
    coordinate_scale: float = 1.0,
    dbscan_epsilon: float = 0.04244754504798836,
    dbscan_min_points: int = 4,
    mean_shift_bandwidth: float = 0.0912189568837504,
    minimum_cluster_points: int | None = 33,
    maximum_clusters: int = 16,
) -> dict[str, Any]:
    return {
        "status": "paper-derived-with-inferences",
        "paper_specified": {
            "sample_points": 24_000,
            "mask_crop_points": 3_072,
            "boundary_neighbors": 30,
            "tooth_type_assignment": (
                "eight FDI tooth types; majority point label within each "
                "predicted tooth instance"
            ),
            "point_grouping": (
                "DBSCAN; apply Mean Shift only to clusters whose variance "
                "exceeds three times the mean cluster variance"
            ),
        },
        "checkpoint_compatibility_assumptions": {
            "status": "user-approved",
            "approved_on": "2026-07-31",
            "point_group_class_encoding": {
                "0": "gingiva/background",
                "1-8": "FDI tooth type (ones digit)",
                "9": "unused/reserved",
            },
            "fdi_composition": "jaw quadrant + patient side + tooth type",
            "invalid_or_duplicate_policy": "fail without pruning or renumbering",
        },
        "inferred_parameters": {
            "coordinate_normalization": "subtract centroid; divide by maximum radius",
            "coordinate_scale": coordinate_scale,
            "dbscan_epsilon": dbscan_epsilon,
            "dbscan_min_points": dbscan_min_points,
            "mean_shift_bandwidth": mean_shift_bandwidth,
            "minimum_cluster_points": (
                minimum_cluster_points
                if minimum_cluster_points is not None
                else "max(40, predicted_tooth_points // 500)"
            ),
            "maximum_clusters": maximum_clusters,
            "arch_side_limit": 8,
            "left_right_axis": "oriented mesh x-axis; ordered arch position",
        },
        "parameter_selection": dict(_GROUPING_SELECTION),
        "not_implemented": [],
    }


def assign_fdi_by_arch_position(
    centers: np.ndarray,
    jaw: str,
    *,
    patient_right_is_positive_x: bool = True,
) -> list[int]:
    """Assign FDI by patient side and anterior-to-posterior arch order."""
    if jaw == "upper":
        right_prefix, left_prefix = 10, 20
    elif jaw == "lower":
        right_prefix, left_prefix = 40, 30
    else:
        raise ValueError("jaw must be upper or lower")
    centers = np.asarray(centers)
    positive = np.flatnonzero(centers[:, 0] >= 0)
    negative = np.flatnonzero(centers[:, 0] < 0)
    right = positive if patient_right_is_positive_x else negative
    left = negative if patient_right_is_positive_x else positive
    result = [0] * len(centers)
    for indices, prefix in ((right, right_prefix), (left, left_prefix)):
        order = indices[np.argsort(centers[indices, 1])][:8]
        for rank, original_index in enumerate(order, start=1):
            result[int(original_index)] = prefix + rank
    return result


def _instance_tooth_type_evidence(
    instance_labels: np.ndarray,
    point_tooth_types: np.ndarray,
    *,
    instance_count: int,
) -> list[dict[str, Any]]:
    """Apply the thesis' per-instance majority vote to PGM tooth-type labels."""
    labels = np.asarray(instance_labels)
    point_types = np.asarray(point_tooth_types)
    if labels.shape != point_types.shape:
        raise ValueError("instance and tooth-type labels must have equal shape")
    evidence: list[dict[str, Any]] = []
    for instance_id in range(1, instance_count + 1):
        values = point_types[labels == instance_id]
        if values.size == 0:
            raise RuntimeError(
                f"TGNet instance {instance_id} has no points for tooth-type vote."
            )
        unique, counts = np.unique(values.astype(np.int16), return_counts=True)
        maximum = int(counts.max())
        winners = unique[counts == maximum]
        if len(winners) != 1:
            raise RuntimeError(
                f"TGNet instance {instance_id} has a tied tooth-type vote: "
                f"{winners.astype(int).tolist()}."
            )
        winner = int(winners[0])
        evidence.append(
            {
                "instance_id": instance_id,
                "tooth_type_class": winner,
                "winning_votes": maximum,
                "total_votes": int(values.size),
                "winning_fraction": maximum / int(values.size),
                "class_histogram": {
                    str(int(class_id)): int(count)
                    for class_id, count in zip(unique, counts, strict=True)
                },
            }
        )
    return evidence


def _instance_tooth_types(
    instance_labels: np.ndarray,
    point_tooth_types: np.ndarray,
    *,
    instance_count: int,
) -> list[int]:
    return [
        int(item["tooth_type_class"])
        for item in _instance_tooth_type_evidence(
            instance_labels,
            point_tooth_types,
            instance_count=instance_count,
        )
    ]


def assign_fdi_by_tooth_type(
    centers: np.ndarray,
    tooth_types: list[int] | np.ndarray,
    jaw: str,
    *,
    patient_right_is_positive_x: bool,
) -> list[int]:
    """Combine jaw, patient side, and the approved 1..8 tooth-type encoding."""
    centers = np.asarray(centers)
    types = np.asarray(tooth_types, dtype=np.int16)
    if centers.ndim != 2 or centers.shape[1] != 3:
        raise ValueError("centers must have shape (instances, 3)")
    if types.shape != (len(centers),):
        raise ValueError("one tooth-type class is required for each instance")
    invalid = sorted({int(value) for value in types if not 1 <= int(value) <= 8})
    if invalid:
        raise RuntimeError(
            "TGNet produced reserved or invalid tooth-type class values "
            f"{invalid}; approved compatibility encoding allows only 1..8 "
            "for tooth instances (0=gingiva, 9=unused). "
            f"Per-instance majority classes were {types.astype(int).tolist()}."
        )
    if jaw == "upper":
        right_prefix, left_prefix = 10, 20
    elif jaw == "lower":
        right_prefix, left_prefix = 40, 30
    else:
        raise ValueError("jaw must be upper or lower")
    positive_is_right = bool(patient_right_is_positive_x)
    mapping: list[int] = []
    for center, tooth_type in zip(centers, types, strict=True):
        is_positive = bool(center[0] >= 0)
        is_right = is_positive == positive_is_right
        prefix = right_prefix if is_right else left_prefix
        mapping.append(prefix + int(tooth_type))
    duplicates = sorted(
        fdi for fdi in set(mapping) if mapping.count(fdi) > 1
    )
    if duplicates:
        raise RuntimeError(
            "TGNet tooth-type majority vote produced duplicate FDI labels "
            f"{duplicates}; refusing to prune or renumber instances without "
            "paper-supported evidence."
        )
    return mapping


def _validated_fdi_assignment(
    centers: np.ndarray,
    tooth_types: list[int] | np.ndarray,
    jaw: str,
    *,
    patient_right_is_positive_x: bool,
) -> list[int]:
    mapping = assign_fdi_by_tooth_type(
        centers,
        tooth_types,
        jaw,
        patient_right_is_positive_x=patient_right_is_positive_x,
    )
    if len(mapping) != len(set(mapping)):
        raise AssertionError("validated FDI assignment must be unique")
    return mapping


def _connected_dbscan(
    points: np.ndarray,
    *,
    epsilon: float,
    min_points: int,
) -> np.ndarray:
    """Small deterministic DBSCAN implementation using a KD tree."""
    neighbours = cKDTree(points).query_ball_point(points, epsilon, workers=-1)
    core = np.fromiter(
        (len(items) >= min_points for items in neighbours),
        dtype=bool,
        count=len(points),
    )
    labels = np.full(len(points), -1, dtype=np.int32)
    cluster = 0
    for seed in np.flatnonzero(core):
        if labels[seed] >= 0:
            continue
        labels[seed] = cluster
        stack = [int(seed)]
        while stack:
            current = stack.pop()
            for neighbour in neighbours[current]:
                if labels[neighbour] < 0:
                    labels[neighbour] = cluster
                    if core[neighbour]:
                        stack.append(int(neighbour))
        cluster += 1
    return labels


def _select_instances(
    shifted: np.ndarray,
    tooth_mask: np.ndarray,
    *,
    epsilon: float,
    min_points: int,
    mean_shift_bandwidth: float,
    minimum_cluster_points: int | None = None,
    maximum_clusters: int = 32,
) -> tuple[np.ndarray, list[np.ndarray]]:
    if minimum_cluster_points is not None and minimum_cluster_points <= 0:
        raise ValueError("minimum_cluster_points must be positive.")
    if maximum_clusters <= 0:
        raise ValueError("maximum_clusters must be positive.")
    tooth_indices = np.flatnonzero(tooth_mask)
    if tooth_indices.size == 0:
        raise RuntimeError("TGNet PGM predicted no tooth points.")
    local_labels = _connected_dbscan(
        shifted[tooth_indices],
        epsilon=epsilon,
        min_points=min_points,
    )
    non_noise = local_labels[local_labels >= 0]
    dbscan_clusters = [
        np.flatnonzero(local_labels == label)
        for label in np.unique(non_noise)
    ]
    variances = np.asarray(
        [
            np.mean(
                np.sum(
                    np.square(
                        shifted[tooth_indices[members]]
                        - shifted[tooth_indices[members]].mean(axis=0)
                    ),
                    axis=1,
                )
            )
            for members in dbscan_clusters
        ],
        dtype=np.float64,
    )
    average_variance = float(variances.mean()) if variances.size else 0.0
    clusters: list[np.ndarray] = []
    from sklearn.cluster import MeanShift

    for members, variance in zip(dbscan_clusters, variances, strict=True):
        if average_variance > 0 and variance > 3.0 * average_variance:
            recovered = MeanShift(
                bandwidth=mean_shift_bandwidth,
                bin_seeding=True,
                min_bin_freq=min_points,
                cluster_all=True,
                n_jobs=1,
            ).fit(shifted[tooth_indices[members]])
            for label in np.unique(recovered.labels_):
                clusters.append(
                    tooth_indices[members[recovered.labels_ == label]]
                )
        else:
            clusters.append(tooth_indices[members])
    minimum_cluster = (
        minimum_cluster_points
        if minimum_cluster_points is not None
        else max(40, len(tooth_indices) // 500)
    )
    clusters = [members for members in clusters if len(members) >= minimum_cluster]
    clusters.sort(key=len, reverse=True)
    clusters = clusters[:maximum_clusters]
    if not clusters:
        raise RuntimeError(
            "TGNet grouping produced no valid tooth instance; inferred "
            "DBSCAN parameters are incompatible with this scan/checkpoint."
        )
    labels = np.zeros(len(shifted), dtype=np.int16)
    for instance_id, members in enumerate(clusters, start=1):
        labels[members] = instance_id
    return labels, clusters


def _vertex_normals(mesh: trimesh.Trimesh) -> np.ndarray:
    normals = np.asarray(mesh.vertex_normals, dtype=np.float32)
    if normals.shape != np.asarray(mesh.vertices).shape:
        raise ValueError("Could not compute one normal per mesh vertex.")
    return normals


def _refine_with_mrm(
    *,
    model: TGNetCheckpointModel,
    sampled_points: np.ndarray,
    sampled_features: np.ndarray,
    clusters: list[np.ndarray],
    device: torch.device,
    crop_points: int,
    per_scan_batchnorm: bool,
) -> tuple[np.ndarray, dict[str, Any]]:
    tree = cKDTree(sampled_points)
    centers = np.asarray(
        [sampled_points[members].mean(axis=0) for members in clusters],
        dtype=np.float32,
    )
    union_tooth = np.zeros(len(sampled_points), dtype=bool)
    started = time.perf_counter()
    for center in centers:
        crop_indices = np.asarray(
            tree.query(
                center,
                k=min(crop_points, len(sampled_points)),
                workers=-1,
            )[1],
            dtype=np.int64,
        ).reshape(-1)
        context = (
            torch.no_grad() if per_scan_batchnorm else torch.inference_mode()
        )
        with context:
            output = model.second_ins_cent_model(
                torch.from_numpy(sampled_points[crop_indices]).to(device),
                torch.from_numpy(sampled_features[crop_indices]).to(device),
            )
        crop_mask = output.mask_logits.argmax(dim=1).cpu().numpy() == 1
        union_tooth[crop_indices[crop_mask]] = True
    if device.type == "mps":
        torch.mps.synchronize()
    nearest_center = cKDTree(centers).query(
        sampled_points, k=1, workers=-1
    )[1]
    labels = np.zeros(len(sampled_points), dtype=np.int16)
    labels[union_tooth] = (
        np.asarray(nearest_center, dtype=np.int16)[union_tooth] + 1
    )
    return labels, {
        "enabled": True,
        "crop_points": min(crop_points, len(sampled_points)),
        "crop_count": len(centers),
        "tooth_union_points": int(union_tooth.sum()),
        "conflict_resolution": "nearest predicted instance center",
        "mask_tooth_class": 1,
        "seconds": time.perf_counter() - started,
    }


def _run_tgnet_pass(
    *,
    model: TGNetCheckpointModel,
    sampled_points: np.ndarray,
    sampled_features: np.ndarray,
    device: torch.device,
    args: argparse.Namespace,
) -> dict[str, Any]:
    points_tensor = torch.from_numpy(sampled_points).to(device)
    features_tensor = torch.from_numpy(sampled_features).to(device)
    if device.type == "mps":
        torch.mps.synchronize()
    started = time.perf_counter()
    context = (
        torch.no_grad()
        if args.batchnorm_mode == "per-scan"
        else torch.inference_mode()
    )
    with context:
        pgm = model.first_ins_cent_model(points_tensor, features_tensor)
    if device.type == "mps":
        torch.mps.synchronize()
    pgm_seconds = time.perf_counter() - started
    shifted = sampled_points + pgm.offsets.detach().cpu().numpy()
    tooth_mask = pgm.mask_logits.detach().cpu().numpy().argmax(axis=1) == 1
    _, clusters = _select_instances(
        shifted,
        tooth_mask,
        epsilon=args.dbscan_epsilon,
        min_points=args.dbscan_min_points,
        mean_shift_bandwidth=args.mean_shift_bandwidth,
        minimum_cluster_points=args.minimum_cluster_points,
        maximum_clusters=args.maximum_clusters,
    )
    centers = np.asarray(
        [sampled_points[members].mean(axis=0) for members in clusters],
        dtype=np.float32,
    )
    labels, mrm = _refine_with_mrm(
        model=model,
        sampled_points=sampled_points,
        sampled_features=sampled_features,
        clusters=clusters,
        device=device,
        crop_points=args.mrm_crop_points,
        per_scan_batchnorm=args.batchnorm_mode == "per-scan",
    )
    point_tooth_types = (
        pgm.class_logits.detach().cpu().numpy().argmax(axis=1).astype(np.int16)
    )
    tooth_type_evidence = _instance_tooth_type_evidence(
        labels,
        point_tooth_types,
        instance_count=len(clusters),
    )
    return {
        "labels": labels,
        "clusters": clusters,
        "centers": centers,
        "point_tooth_types": point_tooth_types,
        "tooth_type_evidence": tooth_type_evidence,
        "instance_tooth_types": [
            int(item["tooth_type_class"]) for item in tooth_type_evidence
        ],
        "pgm_seconds": pgm_seconds,
        "mrm": mrm,
    }


def _baps_indices(
    points: np.ndarray,
    initial_labels: np.ndarray,
    *,
    global_count: int,
    boundary_count: int,
) -> tuple[np.ndarray, dict[str, Any], np.ndarray]:
    neighbour_count = min(30, len(points))
    neighbours = cKDTree(points).query(
        points, k=neighbour_count, workers=-1
    )[1]
    boundary = np.any(initial_labels[neighbours] != initial_labels[:, None], axis=1)
    boundary_indices = np.flatnonzero(boundary)
    if boundary_indices.size == 0:
        raise RuntimeError("BAPS found no predicted label boundary.")
    cpu_points = torch.from_numpy(np.asarray(points, dtype=np.float32))
    global_indices = farthest_point_indices(
        cpu_points, min(global_count, len(points))
    ).cpu().numpy()
    boundary_local = farthest_point_indices(
        cpu_points[boundary_indices],
        min(boundary_count, len(boundary_indices)),
    ).cpu().numpy()
    selected = list(dict.fromkeys(
        np.concatenate((global_indices, boundary_indices[boundary_local])).tolist()
    ))
    target = min(global_count + boundary_count, len(points))
    if len(selected) < target:
        selected_set = set(selected)
        for index in boundary_indices:
            if int(index) not in selected_set:
                selected.append(int(index))
                selected_set.add(int(index))
                if len(selected) == target:
                    break
    return np.asarray(selected[:target], dtype=np.int64), {
        "enabled": True,
        "boundary_neighbors": neighbour_count,
        "boundary_vertices": int(boundary_indices.size),
        "global_fps_points": int(len(global_indices)),
        "boundary_points": int(len(boundary_local)),
        "combined_unique_points": int(min(len(selected), target)),
    }, boundary


def _face_labels(vertex_labels: np.ndarray, faces: np.ndarray) -> np.ndarray:
    values = vertex_labels[faces]
    output = np.zeros(len(faces), dtype=np.int16)
    for row, labels in enumerate(values):
        nonzero = labels[labels > 0]
        if nonzero.size:
            unique, counts = np.unique(nonzero, return_counts=True)
            output[row] = unique[int(np.argmax(counts))]
    return output


def _export(
    mesh: trimesh.Trimesh,
    vertex_labels: np.ndarray,
    fdi_by_instance: dict[int, int],
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.is_symlink():
        raise RuntimeError("output directory must not be a symbolic link")
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise RuntimeError("output path must be a directory")
    faces = np.asarray(mesh.faces)
    face_labels = _face_labels(vertex_labels, faces)
    gingiva_mask = face_labels == 0
    gingiva_face_count = int(gingiva_mask.sum())
    gingiva_path = output_dir / "gingiva.stl"
    gingiva_path.unlink(missing_ok=True)
    if gingiva_face_count:
        mesh.submesh([gingiva_mask], append=True, repair=False).export(
            gingiva_path
        )
    gingiva = {
        "present": bool(gingiva_face_count),
        "label_id": 0,
        "interpretation": "gingiva",
        "face_count": gingiva_face_count,
        "surface_area": float(np.asarray(mesh.area_faces)[gingiva_mask].sum()),
        "stl": str(gingiva_path.resolve()) if gingiva_face_count else None,
    }
    teeth_dir = output_dir / "teeth_stl"
    if teeth_dir.is_symlink():
        raise RuntimeError("teeth_stl output directory must not be a symbolic link")
    teeth_dir.mkdir(exist_ok=True)
    if not teeth_dir.is_dir():
        raise RuntimeError("teeth_stl output path must be a directory")
    for stale_tooth in teeth_dir.glob("*.stl"):
        if stale_tooth.is_file() or stale_tooth.is_symlink():
            stale_tooth.unlink()
    teeth: list[dict[str, Any]] = []
    fdi_counts = {
        fdi: list(fdi_by_instance.values()).count(fdi)
        for fdi in set(fdi_by_instance.values())
    }
    for instance_id, fdi in sorted(fdi_by_instance.items(), key=lambda item: item[1]):
        mask = face_labels == instance_id
        if not mask.any():
            continue
        filename = (
            f"tooth_{fdi}.stl"
            if fdi_counts[fdi] == 1
            else f"tooth_{fdi}_instance_{instance_id}.stl"
        )
        path = teeth_dir / filename
        mesh.submesh([mask], append=True, repair=False).export(path)
        teeth.append(
            {
                "instance_id": instance_id,
                "fdi": fdi,
                "duplicate_fdi": fdi_counts[fdi] > 1,
                "face_count": int(mask.sum()),
                "stl": str(path.resolve()),
            }
        )
    palette = np.array(
        [[150, 150, 150, 255]]
        + [
            [
                (53 * index) % 205 + 50,
                (97 * index) % 205 + 50,
                (151 * index) % 205 + 50,
                255,
            ]
            for index in range(1, 17)
        ],
        dtype=np.uint8,
    )
    colored = mesh.copy()
    colored.visual = trimesh.visual.ColorVisuals(
        mesh=colored, vertex_colors=palette[np.clip(vertex_labels, 0, 16)]
    )
    colored_path = output_dir / "ios_tgnet_colored.ply"
    colored.export(colored_path)
    labels_path = output_dir / "ios_tgnet_labels.npz"
    np.savez_compressed(
        labels_path, vertex_labels=vertex_labels, face_labels=face_labels
    )
    return {
        "colored_ply": str(colored_path.resolve()),
        "labels_npz": str(labels_path.resolve()),
        "gingiva": gingiva,
        "teeth": teeth,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise RuntimeError("PYTORCH_ENABLE_MPS_FALLBACK=1 is forbidden")
    if args.device == "mps":
        if not torch.backends.mps.is_built() or not torch.backends.mps.is_available():
            raise RuntimeError("Apple MPS was requested but is unavailable.")
        device = torch.device("mps")
    else:
        device = torch.device("cpu")

    _, state_dict, analysis = load_checkpoint_analysis(args.model)
    model = TGNetCheckpointModel()
    try:
        model.load_state_dict(state_dict, strict=True)
    except RuntimeError as exc:
        raise ValueError(
            "TGNet architecture signature matched, but strict full state-dict "
            "loading failed."
        ) from exc
    model.to(device).eval()
    if args.batchnorm_mode == "per-scan":
        batchnorm_layer_count = enable_per_scan_batchnorm(model)
    else:
        batchnorm_layer_count = sum(
            1
            for module in model.modules()
            if isinstance(module, torch.nn.modules.batchnorm._BatchNorm)
        )
    baps_model = model
    baps_model_metadata: dict[str, Any] | None = None
    if args.baps_model is not None:
        _, baps_state, baps_analysis = load_checkpoint_analysis(args.baps_model)
        baps_model = TGNetCheckpointModel()
        baps_model.load_state_dict(baps_state, strict=True)
        baps_model.to(device).eval()
        if args.batchnorm_mode == "per-scan":
            enable_per_scan_batchnorm(baps_model)
        baps_model_metadata = tgnet_model_metadata(
            checkpoint_sha256=_sha256(args.baps_model),
            architecture_validation=baps_analysis.architecture_validation,
        ) | {
            "path": str(args.baps_model.resolve()),
            "role": "baps-second-pass",
        }

    loaded = trimesh.load(args.input, process=False, force="mesh")
    if not isinstance(loaded, trimesh.Trimesh) or loaded.faces.shape[1] != 3:
        raise ValueError("Input must be one triangular PLY/STL mesh.")
    source_mesh = loaded
    oriented = source_mesh.copy()
    orientation = ORIENTATION_MATRICES[args.orientation]
    oriented.apply_transform(orientation)
    points = np.asarray(oriented.vertices, dtype=np.float32)
    center = points.mean(axis=0)
    radius = float(np.linalg.norm(points - center, axis=1).max())
    if not np.isfinite(radius) or radius <= 0:
        raise ValueError("Input mesh has invalid spatial extent.")
    normalized = (points - center) / radius * args.coordinate_scale
    normals = _vertex_normals(oriented)
    all_features = np.concatenate((normalized, normals), axis=1).astype(np.float32)

    points_tensor = torch.from_numpy(normalized).to(device)
    sample_indices = farthest_point_indices(
        points_tensor, min(args.sample_points, len(points))
    )
    sampled_cpu_indices = sample_indices.detach().cpu().numpy()
    initial = _run_tgnet_pass(
        model=model,
        sampled_points=normalized[sampled_cpu_indices],
        sampled_features=all_features[sampled_cpu_indices],
        device=device,
        args=args,
    )
    instance_sampled = initial["labels"]
    clusters = initial["clusters"]
    centers = initial["centers"]
    tooth_type_evidence = initial["tooth_type_evidence"]
    instance_tooth_types = initial["instance_tooth_types"]
    inference_seconds = float(initial["pgm_seconds"])
    mrm_metadata = initial["mrm"]
    if len(clusters) > 16:
        raise RuntimeError("TGNet produced more than 16 retained instances.")
    full_order = _validated_fdi_assignment(
        centers,
        instance_tooth_types,
        args.jaw,
        patient_right_is_positive_x=bool(orientation[0, 0] < 0),
    )
    fdi_by_instance = {
        instance_id: int(full_order[instance_id - 1])
        for instance_id in range(1, len(clusters) + 1)
    }

    nearest_sample = cKDTree(points[sampled_cpu_indices]).query(
        points, k=1, workers=-1
    )[1]
    initial_vertex_labels = instance_sampled[
        np.asarray(nearest_sample, dtype=np.int64)
    ]
    baps_metadata: dict[str, Any] = {"enabled": False}
    if not args.disable_baps:
        baps_indices, baps_metadata, boundary_region = _baps_indices(
            normalized,
            initial_vertex_labels,
            global_count=args.baps_global_points,
            boundary_count=args.baps_boundary_points,
        )
        second = _run_tgnet_pass(
            model=baps_model,
            sampled_points=normalized[baps_indices],
            sampled_features=all_features[baps_indices],
            device=device,
            args=args,
        )
        from scipy.optimize import linear_sum_assignment

        distances = np.linalg.norm(
            second["centers"][:, None, :] - centers[None, :, :], axis=2
        )
        rows, columns = linear_sum_assignment(distances)
        remap = {
            int(row) + 1: int(column) + 1
            for row, column in zip(rows, columns, strict=True)
        }
        second_labels = np.zeros_like(second["labels"])
        for source_id, target_id in remap.items():
            second_labels[second["labels"] == source_id] = target_id
        nearest_second = cKDTree(normalized[baps_indices]).query(
            normalized, k=1, workers=-1
        )[1]
        second_vertex_labels = second_labels[
            np.asarray(nearest_second, dtype=np.int64)
        ]
        vertex_labels = initial_vertex_labels.copy()
        vertex_labels[boundary_region] = second_vertex_labels[boundary_region]
        combined_unique = np.unique(
            np.concatenate((sampled_cpu_indices, baps_indices))
        )
        baps_metadata |= {
            "second_pass_pgm_seconds": float(second["pgm_seconds"]),
            "second_pass_mrm": second["mrm"],
            "center_matches": len(remap),
            "combined_unique_samples": int(len(combined_unique)),
            "replacement_scope": "initial-pass 30-neighbour boundary only",
            "replaced_vertices": int(boundary_region.sum()),
            "checkpoint": baps_model_metadata or {
                "role": "same-as-initial-pass",
                "sha256": _sha256(args.model),
            },
        }
        inference_seconds += float(second["pgm_seconds"])
    else:
        vertex_labels = initial_vertex_labels
    outputs = _export(source_mesh, vertex_labels, fdi_by_instance, args.output_dir)
    model_metadata = tgnet_model_metadata(
        checkpoint_sha256=_sha256(args.model),
        architecture_validation=analysis.architecture_validation,
    ) | {"path": str(args.model.resolve())}
    summary = {
        "schema": "tgnet_ios_research_result.v1",
        "research_only": True,
        "input": {
            "path": str(args.input.resolve()),
            "vertices": int(len(source_mesh.vertices)),
            "faces": int(len(source_mesh.faces)),
            "jaw": args.jaw,
        },
        "model": model_metadata,
        "pipeline": inferred_pipeline_metadata(
            coordinate_scale=args.coordinate_scale,
            dbscan_epsilon=args.dbscan_epsilon,
            dbscan_min_points=args.dbscan_min_points,
            mean_shift_bandwidth=args.mean_shift_bandwidth,
            minimum_cluster_points=args.minimum_cluster_points,
            maximum_clusters=args.maximum_clusters,
        ),
        "mask_refinement": mrm_metadata,
        "boundary_aware_point_sampling": baps_metadata,
        "runtime": {
            "torch": torch.__version__,
            "device": str(device),
            "mps_fallback_env": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"),
            "inference_seconds": inference_seconds,
            "total_seconds": time.perf_counter() - started,
            "batchnorm_compatibility": {
                "mode": args.batchnorm_mode,
                "batchnorm_layers": batchnorm_layer_count,
                "per_scan_statistics": args.batchnorm_mode == "per-scan",
                "dropout_enabled": False,
            },
        },
        "instances": [
            {
                "instance_id": index,
                "fdi": fdi_by_instance[index],
                "tooth_type_class": instance_tooth_types[index - 1],
                "tooth_type_vote": tooth_type_evidence[index - 1],
                "sampled_points": int(len(clusters[index - 1])),
            }
            for index in fdi_by_instance
        ],
        "outputs": outputs,
        "limitations": [
            "No ground truth is consumed by product inference.",
            "Unpublished preprocessing/grouping values are inferred and recorded.",
            (
                "FDI tooth-type encoding 1..8 is a user-approved checkpoint "
                "compatibility assumption; the thesis specifies eight tooth "
                "types and per-instance majority voting but not numeric indices."
            ),
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "result_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--jaw", choices=("upper", "lower"), required=True)
    parser.add_argument(
        "--orientation",
        choices=tuple(ORIENTATION_MATRICES),
        default="rotate_y_180",
    )
    parser.add_argument("--device", choices=("mps", "cpu"), default="mps")
    parser.add_argument("--sample-points", type=int, default=24_000)
    parser.add_argument("--coordinate-scale", type=float, default=1.0)
    parser.add_argument(
        "--dbscan-epsilon", type=float, default=0.04244754504798836
    )
    parser.add_argument("--dbscan-min-points", type=int, default=4)
    parser.add_argument(
        "--mean-shift-bandwidth", type=float, default=0.0912189568837504
    )
    parser.add_argument("--minimum-cluster-points", type=int, default=33)
    parser.add_argument("--maximum-clusters", type=int, default=16)
    parser.add_argument("--mrm-crop-points", type=int, default=3_072)
    parser.add_argument("--baps-global-points", type=int, default=4_000)
    parser.add_argument("--baps-boundary-points", type=int, default=20_000)
    parser.add_argument("--baps-model", type=Path)
    parser.add_argument("--disable-baps", action="store_true")
    parser.add_argument(
        "--batchnorm-mode",
        choices=("per-scan", "running"),
        default="per-scan",
        help=(
            "per-scan keeps the network in eval mode but makes BatchNorm use "
            "the current scan statistics for TGNet checkpoint compatibility"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    summary = run(parse_args(argv))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
