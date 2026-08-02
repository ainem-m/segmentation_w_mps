"""Product adapter for a user-provided compatible TGNet checkpoint pair.

The Point Transformer, ensemble, grouping, boundary, and FDI behavior are
independently implemented from the paper and the publicly disclosed challenge
inference behavior. No TGNet checkpoint or upstream source is distributed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import time
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

import numpy as np
import torch
import trimesh
from scipy.spatial import cKDTree
from scipy.optimize import linear_sum_assignment
from sklearn.cluster import DBSCAN, KMeans, MeanShift
from sklearn.decomposition import PCA

from .ios_tgnet import ORIENTATION_MATRICES, _export, _vertex_normals
from .ios_tgnet_network import (
    TGNetCheckpointModel,
    TGNetPointTransformer,
    enable_per_scan_batchnorm,
    farthest_point_indices,
)


TGNET_FINAL_CHECKPOINTS: dict[str, dict[str, Any]] = {
    "fps": {
        "filename": "tgnet_fps.h5",
        "size_bytes": 64_037_327,
        "sha256": "024f585f20924c08eafced8fdc633015b0cc8bba04301d585b4cf7a0c02206b6",
        "strides": (1, 4, 4, 4, 4),
        "nsamples": (36, 24, 24, 24, 24),
        "widths": (32, 64, 128, 256, 512),
        "blocks": (1, 2, 3, 5, 2),
    },
    "boundary": {
        "filename": "tgnet_bdl.h5",
        "size_bytes": 511_103,
        "sha256": "5ec7780d7d645af522c6f2888093e5ca8e11c631d0e13798d208ba2a157554d1",
        "strides": (1, 1),
        "nsamples": (36, 24),
        "widths": (16, 32),
        "blocks": (1, 2),
    },
}

RUNTIME_ARCHITECTURE = {
    "input_features": 6,
    "strides": [1, 4, 4, 4, 4],
    "neighborhood_sizes": [36, 24, 24, 24, 24],
    "blocks_including_transition": [2, 3, 4, 6, 3],
    "widths": [32, 64, 128, 256, 512],
    "sample_points": 24_000,
    "second_crop_points": 3_072,
    "boundary_crop_points": 3_072,
    "attention_relation": "key-minus-query",
    "position_relation": "neighbor-minus-query",
}
GROUPING = {
    "dbscan_epsilon": 0.03,
    "dbscan_min_samples": 30,
    "noise_reassignment_neighbors": 10,
    "pca_candidates": 3,
    "pca_ratio_threshold": 8.0,
    "mean_shift_bandwidth": 0.07,
}
BOUNDARY_SAMPLING = {
    "neighbor_count": 40,
    "nearest_label_ratio_threshold": 0.7,
    "label_ratio_source": "nearest-neighbor-label-count-among-neighbors",
    "boundary_points": 20_000,
    "total_points": 24_000,
    "random_seed": 20_260_731,
}
BOUNDARY_SAMPLING_SELECTION = {
    "method": "published-author-behavior-then-isolated-GT-verification",
    "cases": {"tuning": 10, "validation": 10},
    "artifacts": {
        "tuning_sha256": (
            "10c08ce942db26cb267e8ff1799bfa16dee8ac4f17960903e1eec192a17d60ac"
        ),
        "validation_sha256": (
            "e2ef975d37d933309ec93ead0480bea5e848d11aff8389013ba054f17fa5f775"
        ),
    },
    "mean_golden_instance_iou": {
        "tuning": 0.9562162188351981,
        "validation": 0.9421648000903817,
    },
    "tooth_only_fdi_accuracy": {
        "tuning": 0.8117668084214292,
        "validation": 0.7594224655438682,
    },
    "ground_truth_consumed_by_inference": False,
}
BEHAVIOR_SPECIFICATION = {
    "basis": [
        "SNU thesis: End-To-End Deep Learning Network for 3D Tooth Segmentation",
        "published TGNet inference behavior and disclosed model configurations",
        "strict user-provided checkpoint tensor names and shapes",
    ],
    "independent_implementation": True,
    "upstream_source_copied": False,
}
SEMANTIC_ASSIGNMENT_SELECTION = {
    "method": "isolated-reference-GT-tuning-then-validation",
    "cases": {"tuning": 10, "validation": 10},
    "artifact_sha256": (
        "9b411383add2583c06b37814b756f127f1576a0e86c5067ecb22dff301889b2f"
    ),
    "tooth_only_fdi_accuracy": {
        "tuning_baseline": 0.7602500043044527,
        "tuning_selected": 0.8118810382536961,
        "validation_baseline": 0.7585321300630105,
        "validation_selected": 0.7593755933831774,
    },
    "ground_truth_consumed_by_inference": False,
}
MAX_CHECKPOINT_SCAN_DEPTH = 4
MAX_CHECKPOINT_SCAN_ENTRIES = 4_096
MAX_CHECKPOINT_ARCHIVE_ENTRIES = 4_096


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sample_index_sha256(indices: np.ndarray) -> str:
    canonical = np.asarray(indices, dtype="<i8")
    return hashlib.sha256(canonical.tobytes(order="C")).hexdigest()


def _write_json_atomic(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_checkpoint_directory_layout(root: Path) -> dict[str, Path]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(
            "TGNet Final model selection must be a directory containing "
            "the two user-provided compatible checkpoints."
        )
    expected_by_name = {
        str(specification["filename"]): role
        for role, specification in TGNET_FINAL_CHECKPOINTS.items()
    }
    matches: dict[str, list[Path]] = {
        filename: [] for filename in expected_by_name
    }
    pending: list[tuple[Path, int]] = [(root, 0)]
    scanned_entries = 0
    while pending:
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    scanned_entries += 1
                    if scanned_entries > MAX_CHECKPOINT_SCAN_ENTRIES:
                        raise ValueError(
                            "TGNet Final checkpoint directory contains too many "
                            "entries. Select the ckpts(new) folder itself."
                        )
                    candidate = Path(entry.path)
                    if entry.name in matches:
                        if entry.is_symlink():
                            raise ValueError(
                                "TGNet Final checkpoint directory must not use "
                                "symbolic links for required file "
                                f"{entry.name}."
                            )
                        try:
                            candidate_stat = candidate.stat(follow_symlinks=False)
                        except OSError as exc:
                            raise ValueError(
                                "TGNet Final required checkpoint could not be read."
                            ) from exc
                        if not stat.S_ISREG(candidate_stat.st_mode):
                            raise ValueError(
                                "TGNet Final required checkpoint must be a regular "
                                f"file: {entry.name}."
                            )
                        matches[entry.name].append(candidate)
                    elif depth < MAX_CHECKPOINT_SCAN_DEPTH:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                pending.append((candidate, depth + 1))
                        except OSError as exc:
                            raise ValueError(
                                "TGNet Final checkpoint directory could not be scanned."
                            ) from exc
        except ValueError:
            raise
        except OSError as exc:
            raise ValueError(
                "TGNet Final checkpoint directory could not be scanned."
            ) from exc
    missing = sorted(
        filename for filename, paths in matches.items() if not paths
    )
    if missing:
        raise ValueError(
            "TGNet Final checkpoint directory is missing required files: "
            + ", ".join(missing)
        )
    duplicates = sorted(
        filename for filename, paths in matches.items() if len(paths) > 1
    )
    if duplicates:
        raise ValueError(
            "TGNet Final checkpoint directory contains multiple files with "
            "required names: "
            + ", ".join(duplicates)
        )
    layout = {
        role: matches[filename][0]
        for filename, role in expected_by_name.items()
    }
    for role, path in layout.items():
        specification = TGNET_FINAL_CHECKPOINTS[role]
        expected_size = int(specification["size_bytes"])
        actual_size = path.stat(follow_symlinks=False).st_size
        if actual_size != expected_size:
            raise ValueError(
                f"TGNet Final checkpoint size mismatch for {path.name}: "
                f"expected {expected_size}, got {actual_size}."
            )
        expected_sha256 = str(specification["sha256"])
        actual_sha256 = _sha256(path)
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"TGNet Final checkpoint SHA-256 mismatch for {path.name}: "
                f"expected {expected_sha256}, got {actual_sha256}."
            )
    return layout


def materialize_checkpoint_archive(
    archive: Path,
    destination: Path,
) -> dict[str, Path]:
    """Extract only the two strictly pinned compatible TGNet checkpoints."""
    if (
        archive.suffix.lower() != ".zip"
        or archive.is_symlink()
        or not archive.is_file()
        or not stat.S_ISREG(archive.stat(follow_symlinks=False).st_mode)
    ):
        raise ValueError("TGNet Final archive selection must be a local ZIP file.")
    expected_by_name = {
        str(specification["filename"]): (role, specification)
        for role, specification in TGNET_FINAL_CHECKPOINTS.items()
    }
    selected: dict[str, zipfile.ZipInfo] = {}
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if len(members) > MAX_CHECKPOINT_ARCHIVE_ENTRIES:
                raise ValueError(
                    "TGNet checkpoint ZIP contains too many entries."
                )
            for member in members:
                normalized = member.filename.replace("\\", "/")
                member_path = PurePosixPath(normalized)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError(
                        "TGNet checkpoint ZIP contains an unsafe member path."
                    )
                if member.is_dir():
                    continue
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError(
                        "TGNet checkpoint ZIP must not contain symbolic links."
                    )
                filename = member_path.name
                if filename not in expected_by_name:
                    continue
                if member.flag_bits & 0x1:
                    raise ValueError(
                        "TGNet checkpoint ZIP contains an encrypted required file."
                    )
                _, specification = expected_by_name[filename]
                expected_size = int(specification["size_bytes"])
                if member.file_size != expected_size:
                    raise ValueError(
                        f"TGNet checkpoint ZIP size mismatch for {filename}: "
                        f"expected {expected_size}, got {member.file_size}."
                    )
                if filename in selected:
                    raise ValueError(
                        "TGNet checkpoint ZIP contains multiple files named "
                        f"{filename}."
                    )
                selected[filename] = member
            missing = sorted(set(expected_by_name) - set(selected))
            if missing:
                raise ValueError(
                    "TGNet checkpoint ZIP is missing required files: "
                    + ", ".join(missing)
                )
            if destination.is_symlink():
                raise ValueError(
                    "TGNet checkpoint extraction destination must not be a symbolic link."
                )
            destination.mkdir(parents=True, exist_ok=True)
            layout: dict[str, Path] = {}
            for filename, member in selected.items():
                role, specification = expected_by_name[filename]
                target = destination / filename
                partial = destination / f".{filename}.part"
                partial.unlink(missing_ok=True)
                digest = hashlib.sha256()
                extracted_size = 0
                with bundle.open(member) as source, partial.open("wb") as output:
                    while block := source.read(1024 * 1024):
                        digest.update(block)
                        output.write(block)
                        extracted_size += len(block)
                expected_size = int(specification["size_bytes"])
                if extracted_size != expected_size:
                    partial.unlink(missing_ok=True)
                    raise ValueError(
                        f"TGNet checkpoint ZIP extracted size mismatch for "
                        f"{filename}: expected {expected_size}, got {extracted_size}."
                    )
                actual_sha256 = digest.hexdigest()
                expected_sha256 = str(specification["sha256"])
                if actual_sha256 != expected_sha256:
                    partial.unlink(missing_ok=True)
                    raise ValueError(
                        f"TGNet checkpoint ZIP SHA-256 mismatch for {filename}: "
                        f"expected {expected_sha256}, got {actual_sha256}."
                    )
                partial.replace(target)
                layout[role] = target
    except zipfile.BadZipFile as exc:
        raise ValueError(f"TGNet checkpoint ZIP is invalid: {exc}") from exc
    return validate_checkpoint_directory_layout(destination)


def _strict_component_state(
    checkpoint_state: Mapping[str, torch.Tensor],
    *,
    prefix: str,
    expected_state: Mapping[str, torch.Tensor],
    permitted_inactive_prefixes: Sequence[str] = (),
) -> tuple[dict[str, torch.Tensor], dict[str, Any]]:
    prefix_dot = prefix + "."
    component = {
        key[len(prefix_dot) :]: value
        for key, value in checkpoint_state.items()
        if key.startswith(prefix_dot)
    }
    expected_keys = set(expected_state)
    actual_keys = set(component)
    missing = sorted(expected_keys - actual_keys)
    extra = sorted(actual_keys - expected_keys)
    mismatched = sorted(
        key
        for key in expected_keys & actual_keys
        if tuple(expected_state[key].shape) != tuple(component[key].shape)
    )
    inactive = {
        key: value
        for key, value in checkpoint_state.items()
        if any(
            key.startswith(inactive_prefix + ".")
            for inactive_prefix in permitted_inactive_prefixes
        )
    }
    accounted = {prefix_dot + key for key in component} | set(inactive)
    unexpected = sorted(set(checkpoint_state) - accounted)
    if missing or extra or mismatched:
        raise RuntimeError(
            "Strict TGNet Final component architecture mismatch: "
            f"missing={missing[:5]}, extra={extra[:5]}, "
            f"shape_mismatch={mismatched[:5]}."
        )
    if unexpected:
        raise RuntimeError(
            "TGNet Final checkpoint contains unexpected tensors: "
            f"{unexpected[:5]}."
        )
    return component, {
        "architecture_validation": "passed",
        "component_prefix": prefix,
        "active_tensor_count": len(component),
        "missing_tensor_count": 0,
        "extra_active_tensor_count": 0,
        "shape_mismatch_count": 0,
        "permitted_inactive_prefixes": list(permitted_inactive_prefixes),
        "inactive_extra_tensor_count": len(inactive),
    }


def _load_state(path: Path) -> Mapping[str, torch.Tensor]:
    state = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(state, Mapping) or not state:
        raise RuntimeError(
            f"TGNet Final checkpoint is not a nonempty state dict: {path}"
        )
    if not all(isinstance(key, str) for key in state):
        raise RuntimeError(
            f"TGNet Final checkpoint contains non-string keys: {path}"
        )
    return state


def _new_transformer(class_count: int) -> TGNetPointTransformer:
    return TGNetPointTransformer(
        class_count=class_count,
        attention_relation=RUNTIME_ARCHITECTURE["attention_relation"],
        position_relation=RUNTIME_ARCHITECTURE["position_relation"],
        strides=RUNTIME_ARCHITECTURE["strides"],
        nsamples=RUNTIME_ARCHITECTURE["neighborhood_sizes"],
    )


def _checkpoint_metadata(
    *,
    role: str,
    path: Path,
    sha256: str,
    validation: Mapping[str, Any],
    batchnorm_layers: int,
) -> dict[str, Any]:
    return {
        "role": role,
        "filename": path.name,
        "sha256": sha256,
        "source": "user-provided",
        "license": "not-verified",
        "bundled_by_app": False,
        "role_validation": "passed",
        **dict(validation),
        "batchnorm_mode": "per-scan-statistics-batchnorm-only",
        "batchnorm_layers": batchnorm_layers,
    }


def _load_checkpoint_model(
    paths: Mapping[str, Path],
    role: str,
    device: torch.device,
) -> tuple[TGNetCheckpointModel, dict[str, Any]]:
    specification = TGNET_FINAL_CHECKPOINTS[role]
    path = paths[role]
    actual_sha = _sha256(path)
    if actual_sha != specification["sha256"]:
        raise RuntimeError(
            f"TGNet role {role} requires {path.name} with SHA-256 "
            f"{specification['sha256']}; received {actual_sha}."
        )
    state = _load_state(path)
    model = TGNetCheckpointModel(
        attention_relation=RUNTIME_ARCHITECTURE["attention_relation"],
        position_relation=RUNTIME_ARCHITECTURE["position_relation"],
        strides=tuple(specification["strides"]),
        nsamples=tuple(specification["nsamples"]),
        widths=tuple(specification["widths"]),
        blocks=tuple(specification["blocks"]),
    )
    expected = model.state_dict()
    missing = sorted(set(expected) - set(state))
    extra = sorted(set(state) - set(expected))
    mismatched = sorted(
        key
        for key in set(expected) & set(state)
        if tuple(expected[key].shape) != tuple(state[key].shape)
    )
    if missing or extra or mismatched:
        raise RuntimeError(
            f"Strict TGNet {role} architecture mismatch: "
            f"missing={missing[:5]}, extra={extra[:5]}, "
            f"shape_mismatch={mismatched[:5]}."
        )
    model.load_state_dict(state, strict=True)
    model.to(device).eval()
    batchnorm_layers = enable_per_scan_batchnorm(model)
    return model, _checkpoint_metadata(
        role=role,
        path=path,
        sha256=actual_sha,
        validation={
            "architecture_validation": "passed",
            "active_tensor_count": len(state),
            "missing_tensor_count": 0,
            "extra_active_tensor_count": 0,
            "shape_mismatch_count": 0,
            "strides": list(specification["strides"]),
            "nsamples": list(specification["nsamples"]),
            "widths": list(specification["widths"]),
            "blocks_excluding_transition": list(specification["blocks"]),
        },
        batchnorm_layers=batchnorm_layers,
    )


def _author_normalization(points: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    centered = np.asarray(points, dtype=np.float32) - np.asarray(
        points, dtype=np.float32
    ).mean(axis=0)
    y_min = float(centered[:, 1].min())
    y_max = float(centered[:, 1].max())
    denominator = y_max - y_min
    if not np.isfinite(denominator) or denominator <= 0:
        raise ValueError(
            "TGNet Final normalization requires a nonzero y extent."
        )
    normalized = ((centered - y_min) / denominator) * 1.8 - 0.8
    return normalized.astype(np.float32), {
        "equation": (
            "p -= mean(p,axis=0); p = ((p - min(p[:,1])) / "
            "(max(p[:,1])-min(p[:,1]))) * 1.8 - 0.8"
        ),
        "scalar_broadcast_axis": "y",
        "scale": 1.8,
        "shift": 0.8,
        "denominator": denominator,
    }


def _first_pca_eigenvalue(points: np.ndarray) -> float:
    if len(points) < 2:
        return 0.0
    covariance = np.cov(np.asarray(points, dtype=np.float64), rowvar=False)
    return float(np.linalg.eigvalsh(covariance)[-1])


def _group_instances(
    shifted: np.ndarray,
    tooth_mask: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    tooth_indices = np.flatnonzero(tooth_mask)
    if tooth_indices.size == 0:
        raise RuntimeError("TGNet Final grouping received no tooth points.")
    tooth_shifted = np.asarray(shifted, dtype=np.float64)[tooth_indices]
    dbscan = DBSCAN(
        eps=GROUPING["dbscan_epsilon"],
        min_samples=GROUPING["dbscan_min_samples"],
    ).fit(tooth_shifted)
    local_labels = np.asarray(dbscan.labels_, dtype=np.int64)
    non_noise = local_labels >= 0
    if not non_noise.any():
        raise RuntimeError(
            "TGNet Final DBSCAN produced only noise; fallback is forbidden."
        )
    core = np.zeros(len(local_labels), dtype=bool)
    core[np.asarray(dbscan.core_sample_indices_, dtype=np.int64)] = True
    cluster_ids = [
        int(value) for value in np.unique(local_labels) if int(value) >= 0
    ]
    eigenvalues = np.asarray(
        [
            _first_pca_eigenvalue(
                tooth_shifted[(local_labels == value) & core]
            )
            for value in cluster_ids
        ],
        dtype=np.float64,
    )
    order = np.argsort(-eigenvalues)
    remainder = eigenvalues[order[3:]]
    split_ids: list[int] = []
    split_ratios: dict[str, float] = {}
    if remainder.size and float(remainder.mean()) > 0:
        denominator = float(remainder.mean())
        for position in order[:3]:
            ratio = float(eigenvalues[position] / denominator)
            cluster_id = cluster_ids[int(position)]
            split_ratios[str(cluster_id)] = ratio
            if ratio > GROUPING["pca_ratio_threshold"]:
                split_ids.append(cluster_id)
    next_label = max(cluster_ids) + 1
    for cluster_id in split_ids:
        members = local_labels == cluster_id
        recovered = MeanShift(
            bandwidth=GROUPING["mean_shift_bandwidth"],
            cluster_all=True,
            n_jobs=1,
        ).fit(tooth_shifted[members])
        recovered_ids = np.asarray(recovered.labels_, dtype=np.int64)
        unique_recovered = np.unique(recovered_ids)
        remap = {
            int(value): (
                cluster_id if index == 0 else next_label + index - 1
            )
            for index, value in enumerate(unique_recovered)
        }
        local_labels[members] = np.asarray(
            [remap[int(value)] for value in recovered_ids],
            dtype=np.int64,
        )
        next_label += max(0, len(unique_recovered) - 1)
    noise_before = int((local_labels < 0).sum())
    if noise_before:
        non_noise = local_labels >= 0
        neighbours = cKDTree(tooth_shifted[non_noise]).query(
            tooth_shifted[~non_noise],
            k=GROUPING["noise_reassignment_neighbors"],
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
    compact = {value: index + 1 for index, value in enumerate(final_ids)}
    local_labels = np.asarray(
        [compact[int(value)] for value in local_labels], dtype=np.int16
    )
    labels = np.zeros(len(shifted), dtype=np.int16)
    labels[tooth_indices] = local_labels
    maximum = max(
        int((local_labels == value).sum())
        for value in np.unique(local_labels)
    )
    return labels, {
        "method": "DBSCAN-PCA-conditional-MeanShift",
        "cluster_count": len(final_ids),
        "dbscan_noise_before_reassignment": noise_before,
        "noise_after_reassignment": 0,
        "split_cluster_ids": split_ids,
        "split_ratios": split_ratios,
        "maximum_cluster_occupancy": maximum / max(1, len(local_labels)),
        "pruning_events": 0,
        "fallback_events": 0,
    }


def _center_crops(
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


def _combine_first_heads(
    *,
    class_logits: Sequence[np.ndarray],
    mask_logits: Sequence[np.ndarray],
    offsets: Sequence[np.ndarray],
) -> dict[str, Any]:
    if not class_logits or not mask_logits or not offsets:
        raise ValueError("TGNet Final requires class, mask, and offset heads.")
    class_shape = np.asarray(class_logits[0]).shape
    mask_shape = np.asarray(mask_logits[0]).shape
    offset_shape = np.asarray(offsets[0]).shape
    if (
        any(np.asarray(value).shape != class_shape for value in class_logits)
        or any(np.asarray(value).shape != mask_shape for value in mask_logits)
        or any(np.asarray(value).shape != offset_shape for value in offsets)
        or class_shape[0] != mask_shape[0]
        or class_shape[0] != offset_shape[0]
        or class_shape[1] < 2
        or mask_shape[1] != 2
        or offset_shape[1] != 3
    ):
        raise ValueError("TGNet Final ensemble head shapes are incompatible.")
    summed_classes = np.sum(np.stack(class_logits), axis=0)
    summed_masks = np.sum(np.stack(mask_logits), axis=0)
    averaged_offsets = np.mean(np.stack(offsets), axis=0)
    tooth_types = np.argmax(summed_classes[:, 1:], axis=1) + 1
    tooth_mask = np.argmax(summed_masks, axis=1) == 1
    classes = tooth_types.astype(np.int16)
    classes[~tooth_mask] = 0
    return {
        "classes": classes,
        "mask": tooth_mask,
        "offsets": averaged_offsets.astype(np.float32),
        "metadata": {
            "class_checkpoint_count": len(class_logits),
            "mask_checkpoint_count": len(mask_logits),
            "offset_checkpoint_count": len(offsets),
            "class_fusion": "sum-logits",
            "class_background_handling": "exclude-class-0-before-argmax",
            "mask_fusion": "sum-logits",
            "mask_gate": "class-is-zero-where-mask-is-background",
            "offset_fusion": "mean",
        },
    }


def _decode_official_first_heads(
    *,
    class_logits: np.ndarray,
    mask_logits: np.ndarray,
    offsets: np.ndarray,
) -> dict[str, Any]:
    """Decode the heads described by the published main-branch behavior."""
    classes_array = np.asarray(class_logits)
    masks_array = np.asarray(mask_logits)
    offsets_array = np.asarray(offsets)
    if (
        classes_array.ndim != 2
        or classes_array.shape[1] != 10
        or masks_array.shape != (classes_array.shape[0], 2)
        or offsets_array.shape != (classes_array.shape[0], 3)
    ):
        raise ValueError("TGNet first-head shapes are incompatible.")
    classes = np.argmax(classes_array, axis=1).astype(np.int16)
    preliminary_mask = classes != 0
    diagnostic_mask = np.argmax(masks_array, axis=1) == 1
    return {
        "classes": classes,
        "class_logits": classes_array.astype(np.float32),
        "preliminary_mask": preliminary_mask,
        "mask": diagnostic_mask,
        "offsets": offsets_array.astype(np.float32),
        "metadata": {
            "class_checkpoint_count": 1,
            "mask_checkpoint_count": 1,
            "offset_checkpoint_count": 1,
            "class_background_handling": "include-class-0-in-argmax",
            "preliminary_crop_mask_source": "semantic-class-argmax-is-nonzero",
            "first_mask_head_role": "diagnostic-only",
            "offset_fusion": "none-single-checkpoint",
        },
    }


def _crop_indices_from_labels(
    points: np.ndarray,
    labels: np.ndarray,
    crop_points: int,
) -> np.ndarray:
    tree = cKDTree(points)
    crops = []
    for instance_id in [int(value) for value in np.unique(labels) if value]:
        center = points[labels == instance_id].mean(axis=0)
        crops.append(
            np.asarray(
                tree.query(
                    center,
                    k=min(crop_points, len(points)),
                    workers=-1,
                )[1],
                dtype=np.int64,
            ).reshape(-1)
        )
    if not crops:
        raise RuntimeError("TGNet Final crop stage received no instances.")
    return np.stack(crops)


def _merge_second_logits(
    *,
    point_count: int,
    crop_indices: np.ndarray,
    model_logits: Sequence[np.ndarray],
) -> tuple[np.ndarray, dict[str, Any]]:
    if not model_logits:
        raise ValueError("TGNet Final second stage requires checkpoints.")
    expected = (len(crop_indices), crop_indices.shape[1], 2)
    if any(np.asarray(value).shape != expected for value in model_logits):
        raise ValueError("TGNet Final second logits have incompatible shapes.")
    crop_logits = np.sum(np.stack(model_logits), axis=0)
    accumulated = np.zeros((point_count, 2), dtype=np.float32)
    visits = np.zeros(point_count, dtype=np.int32)
    for indices, logits in zip(crop_indices, crop_logits, strict=True):
        np.add.at(accumulated, indices, logits)
        np.add.at(visits, indices, 1)
    visited = visits != 0
    accumulated[visited] /= visits[visited, None]
    return np.argmax(accumulated, axis=1) == 1, {
        "checkpoint_count": len(model_logits),
        "checkpoint_fusion": "sum-logits",
        "crop_overlap_fusion": "sum-logits",
        "visit_normalization_before_argmax": True,
        "crop_count": int(len(crop_indices)),
        "crop_points": int(crop_indices.shape[1]),
        "overlap_points": int((visits > 1).sum()),
        "unvisited_points": int((visits == 0).sum()),
        "maximum_visits": int(visits.max(initial=0)),
    }


def _run_second_stage(
    *,
    models: Sequence[TGNetPointTransformer],
    points: np.ndarray,
    features: np.ndarray,
    labels: np.ndarray,
    crop_points: int,
    device: torch.device,
) -> tuple[np.ndarray, dict[str, Any]]:
    indices = _crop_indices_from_labels(points, labels, crop_points)
    centered_points, centered_features = _center_crops(
        points, features, indices
    )
    tensor_points = torch.from_numpy(centered_points).to(device)
    tensor_features = torch.from_numpy(centered_features).to(device)
    logits: list[np.ndarray] = []
    for model in models:
        with torch.no_grad():
            output = model(tensor_points, tensor_features)
        logits.append(output.class_logits.cpu().numpy())
    mask, metadata = _merge_second_logits(
        point_count=len(points),
        crop_indices=indices,
        model_logits=logits,
    )
    return mask, metadata | {
        "crop_center_source": "input-instance-centroid",
        "crop_coordinate_centering": "per-crop-xyz-mean",
        "batch_inference": True,
    }


def _run_official_first(
    *,
    model: TGNetCheckpointModel,
    points: np.ndarray,
    features: np.ndarray,
    device: torch.device,
) -> dict[str, Any]:
    tensor_points = torch.from_numpy(points).to(device)
    tensor_features = torch.from_numpy(features).to(device)
    with torch.no_grad():
        output = model.first_ins_cent_model(tensor_points, tensor_features)
    combined = _decode_official_first_heads(
        class_logits=output.class_logits.cpu().numpy(),
        mask_logits=output.mask_logits.cpu().numpy(),
        offsets=output.offsets.cpu().numpy(),
    )
    shifted = points + combined["offsets"]
    preliminary, preliminary_grouping = _group_instances(
        shifted, combined["preliminary_mask"]
    )
    refined_mask, second_metadata = _run_second_stage(
        models=[model.second_ins_cent_model],
        points=points,
        features=features,
        labels=preliminary,
        crop_points=RUNTIME_ARCHITECTURE["second_crop_points"],
        device=device,
    )
    labels, final_grouping = _group_instances(shifted, refined_mask)
    return {
        **combined,
        "shifted": shifted,
        "preliminary_labels": preliminary,
        "preliminary_grouping": preliminary_grouping,
        "refined_mask": refined_mask,
        "second_ensemble": second_metadata,
        "labels": labels,
        "final_grouping": final_grouping,
    }


def _boundary_sample(
    *,
    all_points: np.ndarray,
    all_features: np.ndarray,
    first_points: np.ndarray,
    first_labels: np.ndarray,
    device: torch.device,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    neighbours = cKDTree(first_points).query(
        all_points, k=BOUNDARY_SAMPLING["neighbor_count"], workers=-1
    )[1]
    neighbour_labels = first_labels[np.asarray(neighbours, dtype=np.int64)]
    nearest_label_counts = _nearest_label_counts(neighbour_labels)
    boundary_mask = (
        nearest_label_counts / BOUNDARY_SAMPLING["neighbor_count"]
        < BOUNDARY_SAMPLING["nearest_label_ratio_threshold"]
    )
    nearest = cKDTree(first_points).query(all_points, k=1, workers=-1)[1]
    propagated = first_labels[np.asarray(nearest, dtype=np.int64)]
    boundary_indices = np.flatnonzero(boundary_mask)
    non_boundary_indices = np.flatnonzero(~boundary_mask)
    rng = np.random.default_rng(BOUNDARY_SAMPLING["random_seed"])
    selected_boundary = rng.permutation(boundary_indices)[
        : BOUNDARY_SAMPLING["boundary_points"]
    ]
    remaining = BOUNDARY_SAMPLING["total_points"] - len(selected_boundary)
    if remaining <= 0:
        selected_non_boundary = np.asarray([], dtype=np.int64)
    else:
        local_non_boundary = (
            farthest_point_indices(
                torch.from_numpy(all_points[non_boundary_indices]).to(device),
                remaining,
            )
            .cpu()
            .numpy()
        )
        selected_non_boundary = non_boundary_indices[local_non_boundary]
    selected = np.concatenate((selected_boundary, selected_non_boundary))
    if len(selected) != min(BOUNDARY_SAMPLING["total_points"], len(all_points)):
        raise RuntimeError(
            "TGNet Final boundary sampling could not produce the required "
            "number of unique points."
        )
    return (
        selected,
        all_features[selected],
        propagated[selected],
        {
            "boundary_vertices": int(len(boundary_indices)),
            "boundary_selected": int(len(selected_boundary)),
            "non_boundary_selected": int(len(selected_non_boundary)),
            "uniform_random_seed": BOUNDARY_SAMPLING["random_seed"],
        },
    )


def _nearest_label_counts(neighbour_labels: np.ndarray) -> np.ndarray:
    labels = np.asarray(neighbour_labels)
    if labels.ndim != 2 or labels.shape[1] < 1:
        raise ValueError("TGNet boundary neighbours must have shape N×K.")
    nearest = labels[:, :1]
    return np.sum(labels == nearest, axis=1, dtype=np.int64)


def _run_boundary(
    *,
    model: TGNetCheckpointModel,
    points: np.ndarray,
    features: np.ndarray,
    seed_labels: np.ndarray,
    device: torch.device,
) -> dict[str, Any]:
    tensor_points = torch.from_numpy(points).to(device)
    tensor_features = torch.from_numpy(features).to(device)
    with torch.no_grad():
        first = model.first_ins_cent_model(tensor_points, tensor_features)
    shifted = points + first.offsets.cpu().numpy()
    mask, second_metadata = _run_second_stage(
        models=[model.second_ins_cent_model],
        points=points,
        features=features,
        labels=seed_labels,
        crop_points=RUNTIME_ARCHITECTURE["boundary_crop_points"],
        device=device,
    )
    cluster_count = len([value for value in np.unique(seed_labels) if value])
    if cluster_count <= 0 or int(mask.sum()) < cluster_count:
        raise RuntimeError(
            "TGNet Final boundary stage has insufficient tooth points."
        )
    labels = np.zeros(len(points), dtype=np.int16)
    labels[mask] = (
        KMeans(n_clusters=cluster_count, random_state=0, n_init=10)
        .fit_predict(shifted[mask])
        .astype(np.int16)
        + 1
    )
    return {
        "labels": labels,
        "mask": mask,
        "offsets": first.offsets.cpu().numpy(),
        "second_stage": second_metadata,
        "grouping": {
            "method": "fixed-count-kmeans-from-initial-cluster-count",
            "cluster_count": cluster_count,
            "pruning_events": 0,
            "fallback_events": 0,
        },
    }


def _assign_internal_semantics(
    points: np.ndarray,
    instance_labels: np.ndarray,
    half_classes: np.ndarray,
    half_class_logits: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    gingiva = instance_labels == 0
    teeth = instance_labels != 0
    instance_ids = [int(value) for value in np.unique(instance_labels) if value]
    if not gingiva.any() or len(instance_ids) < 3:
        raise RuntimeError(
            "TGNet Final semantic orientation lacks gingiva or tooth instances."
        )
    centers = np.asarray(
        [points[instance_labels == value].mean(axis=0) for value in instance_ids]
    )
    pca_axis = PCA(n_components=3).fit(centers).components_
    if np.dot(
        points[teeth].mean(axis=0) - points[gingiva].mean(axis=0),
        pca_axis[2],
    ) <= 0:
        pca_axis[2] *= -1
    count_one_nine = int(((half_classes == 1) | (half_classes == 9)).sum())
    if count_one_nine > 20:
        if not (half_classes == 1).any() or not (half_classes == 9).any():
            raise RuntimeError(
                "TGNet Final orientation requires both semantic class 1 and 9."
            )
        anterior = np.mean(
            [
                points[half_classes == 1].mean(axis=0),
                points[half_classes == 9].mean(axis=0),
            ],
            axis=0,
        )
        reference_source = "mean-of-class-1-and-class-9-centers"
    else:
        anterior = None
        reference_source = ""
        for tooth_type in range(2, 9):
            if int((half_classes == tooth_type).sum()) > 20:
                anterior = np.mean(
                    [
                        points[half_classes == tooth_type].mean(axis=0),
                        centers.mean(axis=0),
                    ],
                    axis=0,
                )
                reference_source = (
                    f"mean-of-class-{tooth_type}-and-instance-centers"
                )
                break
        if anterior is None:
            raise RuntimeError(
                "TGNet Final orientation found no disclosed reference class."
            )
    checking_axis = np.cross(pca_axis[2], anterior - centers.mean(axis=0))
    internal = np.zeros(len(points), dtype=np.int16)
    effective = np.asarray(instance_labels, dtype=np.int16).copy()
    removed = 0
    cluster_semantics: dict[str, int] = {}
    mirrored_side_by_instance: dict[int, bool] = {}
    for instance_id in instance_ids:
        members = instance_labels == instance_id
        values = half_classes[members]
        values = values[values != 0]
        if not len(values):
            effective[members] = 0
            removed += 1
            continue
        unique, counts = np.unique(values, return_counts=True)
        semantic = int(unique[int(np.argmax(counts))])
        if semantic == 1:
            mirrored_side = False
        elif semantic == 9:
            mirrored_side = True
        else:
            center = points[members].mean(axis=0)
            mirrored_side = bool(
                np.dot(center - anterior, checking_axis) < 0
            )
            if mirrored_side:
                semantic += 8
        internal[members] = semantic
        cluster_semantics[str(instance_id)] = semantic
        mirrored_side_by_instance[instance_id] = mirrored_side
    baseline_cluster_semantics = dict(cluster_semantics)
    structured_candidate: dict[str, Any] | None = None
    if half_class_logits is not None:
        logits = np.asarray(half_class_logits, dtype=np.float32)
        if logits.shape != (len(points), 10):
            raise ValueError(
                "TGNet structured semantic candidate requires N×10 logits."
            )
        candidate_mapping: dict[str, int] = {}
        side_counts: dict[str, int] = {}
        unavailable_reason: str | None = None
        for mirrored_side in (False, True):
            side_instances = [
                instance_id
                for instance_id in instance_ids
                if instance_id in mirrored_side_by_instance
                and mirrored_side_by_instance[instance_id] == mirrored_side
            ]
            side_name = "mirrored" if mirrored_side else "reference"
            side_counts[side_name] = len(side_instances)
            if len(side_instances) > 8:
                unavailable_reason = (
                    "more-than-eight-instances-on-"
                    f"{side_name}-side"
                )
                break
            class_indices = (
                np.asarray([9, 2, 3, 4, 5, 6, 7, 8], dtype=np.int64)
                if mirrored_side
                else np.arange(1, 9, dtype=np.int64)
            )
            if not side_instances:
                continue
            scores = np.stack(
                [
                    logits[instance_labels == instance_id].mean(axis=0)[
                        class_indices
                    ]
                    for instance_id in side_instances
                ],
                axis=0,
            )
            assignments = _maximum_unique_type_assignment(scores)
            for instance_id, tooth_type in zip(
                side_instances, assignments, strict=True
            ):
                candidate_mapping[str(instance_id)] = int(
                    tooth_type + (8 if mirrored_side else 0)
                )
        if unavailable_reason is None:
            for instance_id_text, semantic in candidate_mapping.items():
                internal[
                    instance_labels == int(instance_id_text)
                ] = int(semantic)
            cluster_semantics = dict(candidate_mapping)
            structured_candidate = {
                "status": "selected",
                "method": "per-side-mean-logit-maximum-unique-type-assignment",
                "ground_truth_consumed": False,
                "side_counts": side_counts,
                "cluster_semantics": candidate_mapping,
                "selection": SEMANTIC_ASSIGNMENT_SELECTION,
            }
        else:
            structured_candidate = {
                "status": "not-applicable",
                "method": "per-side-mean-logit-maximum-unique-type-assignment",
                "ground_truth_consumed": False,
                "side_counts": side_counts,
                "reason": unavailable_reason,
                "cluster_semantics": {},
                "selection": SEMANTIC_ASSIGNMENT_SELECTION,
            }
    return internal, effective, {
        "method": (
            "per-side-mean-logit-maximum-unique-type-assignment"
            if structured_candidate is not None
            and structured_candidate["status"] == "selected"
            else "instance-majority-half-class-plus-pca-side-disambiguation"
        ),
        "baseline_method": (
            "instance-majority-half-class-plus-pca-side-disambiguation"
        ),
        "reference_source": reference_source,
        "removed_instances_without_semantic_points": removed,
        "instance_without_semantic_behavior": "relabel-instance-to-gingiva",
        "effective_cluster_count": len(
            [value for value in np.unique(effective) if value]
        ),
        "cluster_semantics": cluster_semantics,
        "baseline_cluster_semantics": baseline_cluster_semantics,
        "structured_unique_candidate": structured_candidate,
        "pruning_events": 0,
        "fallback_events": 0,
    }


def _maximum_unique_type_assignment(scores: np.ndarray) -> np.ndarray:
    values = np.asarray(scores, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 8 or values.shape[0] > 8:
        raise ValueError("TGNet unique tooth-type scores must have shape N×8.")
    if values.shape[0] == 0:
        return np.asarray([], dtype=np.int16)
    rows, columns = linear_sum_assignment(-values)
    if len(rows) != values.shape[0]:
        raise RuntimeError("TGNet unique tooth-type assignment is incomplete.")
    result = np.zeros(values.shape[0], dtype=np.int16)
    result[rows] = columns.astype(np.int16) + 1
    return result


def _boundary_merge(
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
    nearest_labels = first_labels[np.asarray(nearest_first, dtype=np.int64)]
    remapped = np.zeros_like(boundary_labels, dtype=np.int16)
    cluster_remap: dict[str, int] = {}
    for cluster_id in [int(value) for value in np.unique(boundary_labels) if value]:
        members = boundary_labels == cluster_id
        values, counts = np.unique(nearest_labels[members], return_counts=True)
        target = int(values[int(np.argmax(counts))])
        cluster_remap[str(cluster_id)] = target
        remapped[members] = target
    combined_points = np.concatenate((first_points, boundary_points), axis=0)
    combined_labels = np.concatenate((first_labels, remapped), axis=0)
    nearest = cKDTree(combined_points).query(
        all_points, k=1, workers=-1
    )[1]
    return combined_labels[np.asarray(nearest, dtype=np.int64)], {
        "cluster_remap": cluster_remap,
        "boundary_clusters": len(cluster_remap),
        "propagation": "one-nearest-neighbor-over-first-plus-boundary-points",
        "pruning_events": 0,
        "fallback_events": 0,
    }


def _internal_to_fdi(internal: np.ndarray, jaw: str) -> np.ndarray:
    labels = np.asarray(internal, dtype=np.int16).copy()
    labels[labels >= 9] += 2
    labels[labels > 0] += 10
    if jaw == "lower":
        labels[labels > 0] += 20
    elif jaw != "upper":
        raise ValueError("jaw must be upper or lower")
    return labels


def _instance_fdi_mapping(
    effective_instances: np.ndarray,
    sampled_internal: np.ndarray,
    jaw: str,
) -> dict[int, int]:
    sampled_fdi = _internal_to_fdi(sampled_internal, jaw)
    mapping: dict[int, int] = {}
    for instance_id in [
        int(value) for value in np.unique(effective_instances) if value
    ]:
        values = np.unique(sampled_fdi[effective_instances == instance_id])
        values = values[values != 0]
        if len(values) != 1:
            raise RuntimeError(
                f"TGNet Final instance {instance_id} has ambiguous FDI labels: "
                f"{values.astype(int).tolist()}."
            )
        mapping[instance_id] = int(values[0])
    return mapping


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise RuntimeError("PYTORCH_ENABLE_MPS_FALLBACK=1 is forbidden.")
    if args.device == "mps":
        if not torch.backends.mps.is_built() or not torch.backends.mps.is_available():
            raise RuntimeError("Apple MPS was requested but is unavailable.")
        device = torch.device("mps")
    else:
        device = torch.device("cpu")
    paths = validate_checkpoint_directory_layout(args.model)
    checkpoints: list[dict[str, Any]] = []
    fps_model, metadata = _load_checkpoint_model(paths, "fps", device)
    checkpoints.append(metadata)
    boundary_model, metadata = _load_checkpoint_model(paths, "boundary", device)
    checkpoints.append(metadata)

    loaded = trimesh.load(args.input, process=False, force="mesh")
    if not isinstance(loaded, trimesh.Trimesh) or loaded.faces.shape[1] != 3:
        raise ValueError("Input must be one triangular PLY/STL mesh.")
    source_mesh = loaded
    oriented = source_mesh.copy()
    oriented.apply_transform(ORIENTATION_MATRICES[args.orientation])
    normalized, normalization = _author_normalization(
        np.asarray(oriented.vertices, dtype=np.float32)
    )
    features = np.concatenate(
        (normalized, _vertex_normals(oriented)), axis=1
    ).astype(np.float32)
    sample_indices = (
        farthest_point_indices(
            torch.from_numpy(normalized).to(device),
            min(RUNTIME_ARCHITECTURE["sample_points"], len(normalized)),
        )
        .cpu()
        .numpy()
    )
    sampled_points = normalized[sample_indices]
    sampled_features = features[sample_indices]
    first = _run_official_first(
        model=fps_model,
        points=sampled_points,
        features=sampled_features,
        device=device,
    )
    selected, boundary_features, seed_labels, boundary_sampling = _boundary_sample(
        all_points=normalized,
        all_features=features,
        first_points=sampled_points,
        first_labels=first["labels"],
        device=device,
    )
    boundary = _run_boundary(
        model=boundary_model,
        points=normalized[selected],
        features=boundary_features,
        seed_labels=seed_labels,
        device=device,
    )
    sampled_internal, effective_instances, semantic = _assign_internal_semantics(
        sampled_points,
        first["labels"],
        first["classes"],
        first["class_logits"],
    )
    boundary_count = int(boundary_sampling["boundary_selected"])
    full_instances, boundary_merge = _boundary_merge(
        all_points=normalized,
        first_points=sampled_points,
        first_labels=effective_instances,
        boundary_points=normalized[selected[:boundary_count]],
        boundary_labels=boundary["labels"][:boundary_count],
    )
    fdi_by_instance = _instance_fdi_mapping(
        effective_instances, sampled_internal, args.jaw
    )
    fdi_values = list(fdi_by_instance.values())
    duplicate_fdi_labels = sorted(
        value for value in set(fdi_values) if fdi_values.count(value) > 1
    )
    semantic["duplicate_fdi_labels"] = duplicate_fdi_labels
    semantic["duplicate_fdi_behavior"] = (
        "preserve-author-instance-and-semantic-labels-with-unique-stl-filenames"
    )
    structured_candidate = semantic.get("structured_unique_candidate")
    if (
        structured_candidate is not None
        and structured_candidate["status"] == "selected"
    ):
        structured_candidate["fdi_by_instance"] = {
            instance_id: int(
                _internal_to_fdi(
                    np.asarray([internal_label], dtype=np.int16), args.jaw
                )[0]
            )
            for instance_id, internal_label in structured_candidate[
                "cluster_semantics"
            ].items()
        }
    outputs = _export(
        source_mesh, full_instances, fdi_by_instance, args.output_dir
    )
    if device.type == "mps":
        torch.mps.synchronize()
    summary = {
        "schema": "tgnet_final_ios_research_result.v1",
        "research_only": True,
        "input": {
            "path": str(args.input.resolve()),
            "vertices": int(len(source_mesh.vertices)),
            "faces": int(len(source_mesh.faces)),
            "jaw": args.jaw,
            "orientation": args.orientation,
            "sampling": {
                "method": "exact-farthest-point-sampling",
                "point_count": int(len(sample_indices)),
                "index_dtype": "little-endian-int64",
                "index_sha256": _sample_index_sha256(sample_indices),
            },
        },
        "model": {
            "model_family": "tgnet",
            "variant": "published-behavior-fps-plus-boundary",
            "source": "user-provided",
            "license": "not-verified",
            "bundled_by_app": False,
            "checkpoint_set_directory": (
                None
                if args.source_archive_sha256
                else str(args.model.resolve())
            ),
            "source_archive": (
                {
                    "filename": args.source_archive_name,
                    "sha256": args.source_archive_sha256,
                    "selection": "user-provided",
                    "extracted_required_checkpoints_only": True,
                }
                if args.source_archive_sha256
                else None
            ),
            "checkpoint_count": len(checkpoints),
            "checkpoints": checkpoints,
            "architecture_validation": {
                "passed": True,
                "strict_active_state_dicts": True,
                "runtime_architecture": RUNTIME_ARCHITECTURE,
            },
        },
        "pipeline": {
            "normalization": normalization,
            "runtime_architecture": RUNTIME_ARCHITECTURE,
            "grouping": GROUPING,
            "boundary_sampling": BOUNDARY_SAMPLING,
            "boundary_sampling_selection": BOUNDARY_SAMPLING_SELECTION,
            "boundary_sampling_result": boundary_sampling,
            "behavior_specification": BEHAVIOR_SPECIFICATION,
            "first_ensemble": first["metadata"],
            "preliminary_grouping": first["preliminary_grouping"],
            "second_ensemble": first["second_ensemble"],
            "final_grouping": first["final_grouping"],
            "semantic_assignment": semantic,
            "boundary_grouping": boundary["grouping"],
            "boundary_second_stage": boundary["second_stage"],
            "boundary_merge": boundary_merge,
        },
        "runtime": {
            "torch": torch.__version__,
            "device": str(device),
            "mps_fallback_env": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"),
            "batchnorm_compatibility": {
                "mode": "per-scan-statistics-batchnorm-only",
                "dropout_enabled": False,
            },
            "total_seconds": time.perf_counter() - started,
        },
        "strict": {
            "fallback_events": 0,
            "non_author_pruning_events": 0,
        },
        "instances": [
            {"instance_id": instance_id, "fdi": fdi}
            for instance_id, fdi in sorted(
                fdi_by_instance.items(), key=lambda item: item[1]
            )
        ],
        "outputs": outputs,
        "limitations": [
            "The application does not provide or redistribute these checkpoints.",
            "The user must obtain them lawfully and confirm their use conditions.",
            "Checkpoint license is not verified by this application.",
            "No ground truth is consumed by product inference.",
        ],
    }
    _write_json_atomic(args.output_dir / "result_summary.json", summary)
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
    parser.add_argument("--source-archive-name")
    parser.add_argument("--source-archive-sha256")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    summary = run(parse_args(argv))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
