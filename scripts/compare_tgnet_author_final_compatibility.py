#!/usr/bin/env python3
"""Evaluation-only reproduction of the disclosed TGNet Final ensemble.

The implementation composes the independently implemented TGNet network and
author-mid compatibility primitives in this repository. The public challenge
repository is used only as a read-only behavior specification; none of its
source code is imported or packaged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import torch
import trimesh
from scipy.spatial import cKDTree
from sklearn.decomposition import PCA

from scripts.compare_tgnet_author_mid_compatibility import (
    AUTHOR_BOUNDARY_SAMPLING,
    AUTHOR_GROUPING,
    AUTHOR_RUNTIME_ARCHITECTURE,
    _author_boundary_merge,
    _author_group_instances,
    _author_normalization,
    _boundary_sample,
    _case_paths,
    _center_mrm_crops,
    _offset_metrics,
    _overlap,
    _run_grouping_module,
    _score_instances,
    _sha256,
    _write_json_atomic,
)
from totalsegmentator_wrapper_mac.ios_tgnet import _vertex_normals
from totalsegmentator_wrapper_mac.ios_tgnet_network import (
    TGNetCheckpointModel,
    TGNetPointTransformer,
    enable_per_scan_batchnorm,
    farthest_point_indices,
)


FINAL_BEHAVIOR_SPECIFICATION = {
    "repository": "https://github.com/limhoyeon/ToothGroupNetwork",
    "commit": "f184332d358af44dd5f96585020a6aa1d6aeb1ca",
    "files_read_only": [
        "inference_final.py",
        "inference_pipeline_final.py",
        "predict_utils.py",
        "models/tf_cbl_first_model.py",
        "models/tf_cbl_first_mask_model.py",
        "models/tf_cbl_second_model.py",
        "models/tf_cbl_two_step_half_num_model.py",
    ],
    "source_code_copied": False,
}
FINAL_RUNTIME = {
    **AUTHOR_RUNTIME_ARCHITECTURE,
    "second_crop_points": 3072,
    "boundary_crop_points": 4608,
    "batchnorm_mode": "per-scan-statistics-batchnorm-only",
    "gradient_mode": "no_grad",
}
FINAL_CHECKPOINTS = {
    "offset": {
        "filename": "0707_cosannealing_val.h5",
        "sha256": "05fe167662da1cb9d41a5494eb56cc96506421a72e2272c54ead7f5fda5aa276",
        "prefix": "first_ins_cent_model",
        "class_count": 10,
        "inactive_prefixes": ("second_ins_cent_model",),
    },
    "class-forward": {
        "filename": "0809_sched_v2_fixed_Flip(fixed)_weight0.1_val.h5",
        "sha256": "b9886d1a2f0eb93e8302805ae54ea1c1d9260d74d4c8e9f77d6a94b5c881c727",
        "prefix": "first_ins_cent_model",
        "class_count": 10,
        "inactive_prefixes": (),
    },
    "class-reverse": {
        "filename": "0809_reverse_sched_v2_fixed_Flip(fixed)_weight0.1_val.h5",
        "sha256": "099788fc67e6a1cecff6fa7f5470c85c988d0e01dcbe56c4f461ef991ead0c0f",
        "prefix": "first_ins_cent_model",
        "class_count": 10,
        "inactive_prefixes": (),
    },
    "mask": {
        "filename": "0805_mask_model_val.h5",
        "sha256": "95125d86b6f9e3aaed5f8a94182691608eb83bb14b77f41a1531923b5cd10647",
        "prefix": "first_ins_cent_model",
        "class_count": 2,
        "inactive_prefixes": (),
    },
    "second-forward": {
        "filename": "0808_sched_fixed_tf_cbl_2.0_second_Flip_val.h5",
        "sha256": "6845ded644fd1cae10294be39f759ee52ba784fd71d53c5d7b887d721f9510ce",
        "prefix": "second_ins_cent_model",
        "class_count": 2,
        "inactive_prefixes": ("cent_model",),
    },
    "second-reverse": {
        "filename": "0809_reverse_sched_fixed_tf_cbl_2.0_second_Flip_val.h5",
        "sha256": "29120d31ff729b8d3ce081187f49468a9e85b48861a55cb46adc002760617868",
        "prefix": "second_ins_cent_model",
        "class_count": 2,
        "inactive_prefixes": ("cent_model",),
    },
    "boundary": {
        "filename": "0805_bd_cbl_normal_rand_crop_4608_val.h5",
        "sha256": "08a6a7cc70321e1caafda42d80079ee33fe06672ecf33ebd59a89ac576771a07",
        "prefix": "full-checkpoint",
        "class_count": None,
        "inactive_prefixes": (),
    },
}


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
    accounted = {
        prefix_dot + key for key in component
    } | set(inactive)
    unexpected = sorted(set(checkpoint_state) - accounted)
    if missing or extra or mismatched:
        raise RuntimeError(
            "Strict TGNet component architecture mismatch: "
            f"missing={missing[:5]}, extra={extra[:5]}, "
            f"shape_mismatch={mismatched[:5]}."
        )
    if unexpected:
        raise RuntimeError(
            "TGNet checkpoint contains unexpected checkpoint tensors: "
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
        raise RuntimeError(f"TGNet checkpoint is not a nonempty state dict: {path}")
    if not all(isinstance(key, str) for key in state):
        raise RuntimeError(f"TGNet checkpoint contains non-string keys: {path}")
    return state


def _new_transformer(class_count: int) -> TGNetPointTransformer:
    return TGNetPointTransformer(
        class_count=class_count,
        attention_relation=FINAL_RUNTIME["attention_relation"],
        position_relation=FINAL_RUNTIME["position_relation"],
        strides=FINAL_RUNTIME["strides"],
        nsamples=FINAL_RUNTIME["neighborhood_sizes"],
    )


def _load_component(
    checkpoint_dir: Path,
    role: str,
    device: torch.device,
) -> tuple[TGNetPointTransformer, dict[str, Any]]:
    specification = FINAL_CHECKPOINTS[role]
    path = checkpoint_dir / str(specification["filename"])
    actual_sha = _sha256(path)
    if actual_sha != specification["sha256"]:
        raise RuntimeError(
            f"{role} requires {specification['filename']} with SHA-256 "
            f"{specification['sha256']}; received {actual_sha}."
        )
    state = _load_state(path)
    model = _new_transformer(int(specification["class_count"]))
    component, validation = _strict_component_state(
        state,
        prefix=str(specification["prefix"]),
        expected_state=model.state_dict(),
        permitted_inactive_prefixes=tuple(specification["inactive_prefixes"]),
    )
    model.load_state_dict(component, strict=True)
    model.to(device).eval()
    batchnorm_layers = enable_per_scan_batchnorm(model)
    return model, {
        "source": "user-provided",
        "license": "not-verified",
        "bundled_by_app": False,
        "role": role,
        "filename": path.name,
        "sha256": actual_sha,
        "role_validation": "passed",
        **validation,
        "batchnorm_mode": FINAL_RUNTIME["batchnorm_mode"],
        "batchnorm_layers": batchnorm_layers,
    }


def _load_boundary(
    checkpoint_dir: Path,
    device: torch.device,
) -> tuple[TGNetCheckpointModel, dict[str, Any]]:
    role = "boundary"
    specification = FINAL_CHECKPOINTS[role]
    path = checkpoint_dir / str(specification["filename"])
    actual_sha = _sha256(path)
    if actual_sha != specification["sha256"]:
        raise RuntimeError(
            f"{role} requires {specification['filename']} with SHA-256 "
            f"{specification['sha256']}; received {actual_sha}."
        )
    state = _load_state(path)
    model = TGNetCheckpointModel(
        attention_relation=FINAL_RUNTIME["attention_relation"],
        position_relation=FINAL_RUNTIME["position_relation"],
        strides=FINAL_RUNTIME["strides"],
        nsamples=FINAL_RUNTIME["neighborhood_sizes"],
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
            "Strict TGNet boundary architecture mismatch: "
            f"missing={missing[:5]}, extra={extra[:5]}, "
            f"shape_mismatch={mismatched[:5]}."
        )
    model.load_state_dict(state, strict=True)
    model.to(device).eval()
    batchnorm_layers = enable_per_scan_batchnorm(model)
    return model, {
        "source": "user-provided",
        "license": "not-verified",
        "bundled_by_app": False,
        "role": role,
        "filename": path.name,
        "sha256": actual_sha,
        "role_validation": "passed",
        "architecture_validation": "passed",
        "active_tensor_count": len(state),
        "missing_tensor_count": 0,
        "extra_active_tensor_count": 0,
        "shape_mismatch_count": 0,
        "batchnorm_mode": FINAL_RUNTIME["batchnorm_mode"],
        "batchnorm_layers": batchnorm_layers,
    }


def _combine_final_first_heads(
    *,
    class_logits: Sequence[np.ndarray],
    mask_logits: Sequence[np.ndarray],
    offsets: Sequence[np.ndarray],
) -> dict[str, Any]:
    if not class_logits or not mask_logits or not offsets:
        raise ValueError("TGNet Final first ensemble requires all three heads.")
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


def _merge_final_second_logits(
    *,
    point_count: int,
    crop_indices: np.ndarray,
    model_class_logits: Sequence[np.ndarray],
) -> tuple[np.ndarray, dict[str, Any]]:
    if not model_class_logits:
        raise ValueError("TGNet Final second ensemble requires checkpoints.")
    expected_shape = (len(crop_indices), crop_indices.shape[1], 2)
    if any(np.asarray(logits).shape != expected_shape for logits in model_class_logits):
        raise ValueError("TGNet Final second logits have incompatible shapes.")
    crop_logits = np.sum(np.stack(model_class_logits), axis=0)
    accumulated = np.zeros((point_count, 2), dtype=np.float32)
    visits = np.zeros(point_count, dtype=np.int32)
    for indices, logits in zip(crop_indices, crop_logits, strict=True):
        np.add.at(accumulated, indices, logits)
        np.add.at(visits, indices, 1)
    visited = visits != 0
    accumulated[visited] /= visits[visited, None]
    return np.argmax(accumulated, axis=1) == 1, {
        "checkpoint_count": len(model_class_logits),
        "checkpoint_fusion": "sum-logits",
        "crop_overlap_fusion": "sum-logits",
        "visit_normalization_before_argmax": True,
        "crop_count": int(len(crop_indices)),
        "crop_points": int(crop_indices.shape[1]),
        "overlap_points": int((visits > 1).sum()),
        "unvisited_points": int((visits == 0).sum()),
        "maximum_visits": int(visits.max(initial=0)),
    }


def _run_second_ensemble(
    *,
    models: Sequence[TGNetPointTransformer],
    points: np.ndarray,
    features: np.ndarray,
    preliminary_labels: np.ndarray,
    device: torch.device,
) -> tuple[np.ndarray, dict[str, Any]]:
    tree = cKDTree(points)
    crop_indices = []
    for instance_id in [int(value) for value in np.unique(preliminary_labels) if value]:
        members = preliminary_labels == instance_id
        center = points[members].mean(axis=0)
        crop_indices.append(
            np.asarray(
                tree.query(
                    center,
                    k=min(FINAL_RUNTIME["second_crop_points"], len(points)),
                    workers=-1,
                )[1],
                dtype=np.int64,
            ).reshape(-1)
        )
    if not crop_indices:
        raise RuntimeError("TGNet Final second ensemble received no clusters.")
    indices = np.stack(crop_indices)
    crop_points, crop_features = _center_mrm_crops(points, features, indices)
    tensor_points = torch.from_numpy(crop_points).to(device)
    tensor_features = torch.from_numpy(crop_features).to(device)
    logits = []
    for model in models:
        with torch.no_grad():
            output = model(tensor_points, tensor_features)
        logits.append(output.class_logits.cpu().numpy())
    mask, metadata = _merge_final_second_logits(
        point_count=len(points),
        crop_indices=indices,
        model_class_logits=logits,
    )
    return mask, metadata | {
        "crop_center_source": "input-preliminary-instance-centroid",
        "crop_coordinate_centering": "per-crop-xyz-mean",
        "batch_inference": True,
    }


def _run_final_first(
    *,
    class_models: Sequence[TGNetPointTransformer],
    mask_models: Sequence[TGNetPointTransformer],
    offset_models: Sequence[TGNetPointTransformer],
    second_models: Sequence[TGNetPointTransformer],
    points: np.ndarray,
    features: np.ndarray,
    device: torch.device,
) -> dict[str, Any]:
    tensor_points = torch.from_numpy(points).to(device)
    tensor_features = torch.from_numpy(features).to(device)
    class_logits = []
    mask_logits = []
    offsets = []
    for model in class_models:
        with torch.no_grad():
            output = model(tensor_points, tensor_features)
        class_logits.append(output.class_logits.cpu().numpy())
    for model in mask_models:
        with torch.no_grad():
            output = model(tensor_points, tensor_features)
        mask_logits.append(output.class_logits.cpu().numpy())
    for model in offset_models:
        with torch.no_grad():
            output = model(tensor_points, tensor_features)
        offsets.append(output.offsets.cpu().numpy())
    combined = _combine_final_first_heads(
        class_logits=class_logits,
        mask_logits=mask_logits,
        offsets=offsets,
    )
    shifted = points + combined["offsets"]
    preliminary_labels, preliminary_grouping = _author_group_instances(
        shifted, combined["classes"] != 0
    )
    refined_mask, second_metadata = _run_second_ensemble(
        models=second_models,
        points=points,
        features=features,
        preliminary_labels=preliminary_labels,
        device=device,
    )
    labels, grouping = _author_group_instances(shifted, refined_mask)
    return {
        **combined,
        "shifted": shifted,
        "preliminary_labels": preliminary_labels,
        "preliminary_grouping": preliminary_grouping,
        "refined_mask": refined_mask,
        "second_ensemble": second_metadata,
        "labels": labels,
        "grouping": grouping,
    }


def _assign_internal_semantics(
    points: np.ndarray,
    instance_labels: np.ndarray,
    half_classes: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    gingiva = instance_labels == 0
    teeth = instance_labels != 0
    instance_ids = [int(value) for value in np.unique(instance_labels) if value]
    if not gingiva.any() or len(instance_ids) < 3:
        raise RuntimeError("TGNet Final semantic orientation lacks gingiva or teeth.")
    centers = np.asarray(
        [points[instance_labels == value].mean(axis=0) for value in instance_ids]
    )
    pca_axis = PCA(n_components=3).fit(centers).components_
    gingiva_mean = points[gingiva].mean(axis=0)
    teeth_mean = points[teeth].mean(axis=0)
    if np.dot(teeth_mean - gingiva_mean, pca_axis[2]) <= 0:
        pca_axis[2] *= -1

    count_one_nine = int(((half_classes == 1) | (half_classes == 9)).sum())
    if count_one_nine > 20:
        if not (half_classes == 1).any() or not (half_classes == 9).any():
            raise RuntimeError(
                "TGNet Final semantic orientation requires both class 1 and 9."
            )
        anterior_reference = np.mean(
            [
                points[half_classes == 1].mean(axis=0),
                points[half_classes == 9].mean(axis=0),
            ],
            axis=0,
        )
        reference_source = "mean-of-class-1-and-class-9-centers"
    else:
        anterior_reference = None
        reference_source = ""
        for tooth_type in range(2, 9):
            if int((half_classes == tooth_type).sum()) > 20:
                anterior_reference = np.mean(
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
        if anterior_reference is None:
            raise RuntimeError(
                "TGNet Final semantic orientation found no disclosed reference class."
            )
    checking_axis = np.cross(
        pca_axis[2], anterior_reference - centers.mean(axis=0)
    )
    internal = np.zeros(len(points), dtype=np.int16)
    effective_instances = np.asarray(instance_labels, dtype=np.int16).copy()
    removed_instances = 0
    cluster_semantics: dict[str, int] = {}
    for instance_id in instance_ids:
        members = instance_labels == instance_id
        values = half_classes[members]
        values = values[values != 0]
        if not len(values):
            removed_instances += 1
            effective_instances[members] = 0
            continue
        unique, counts = np.unique(values, return_counts=True)
        semantic = int(unique[int(np.argmax(counts))])
        if semantic not in (1, 9):
            center = points[members].mean(axis=0)
            if np.dot(center - anterior_reference, checking_axis) < 0:
                semantic += 8
        internal[members] = semantic
        cluster_semantics[str(instance_id)] = semantic
    return internal, effective_instances, {
        "method": "instance-majority-half-class-plus-pca-side-disambiguation",
        "reference_source": reference_source,
        "removed_instances_without_semantic_points": removed_instances,
        "cluster_semantics": cluster_semantics,
        "instance_without_semantic_behavior": "relabel-instance-to-gingiva",
        "effective_cluster_count": len(
            [value for value in np.unique(effective_instances) if value]
        ),
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
        raise ValueError(f"Unsupported jaw: {jaw}")
    return labels


def _propagate_semantics(
    *,
    full_instances: np.ndarray,
    sampled_instances: np.ndarray,
    sampled_internal_semantics: np.ndarray,
) -> np.ndarray:
    result = np.zeros(len(full_instances), dtype=np.int16)
    for instance_id in [int(value) for value in np.unique(full_instances) if value]:
        values = sampled_internal_semantics[sampled_instances == instance_id]
        values = values[values != 0]
        if len(values):
            unique, counts = np.unique(values, return_counts=True)
            result[full_instances == instance_id] = int(
                unique[int(np.argmax(counts))]
            )
    return result


def _sample_indices(
    *,
    points: np.ndarray,
    device: torch.device,
    cache_root: Path | None,
    case_key: str,
) -> np.ndarray:
    if cache_root is None:
        return (
            farthest_point_indices(
                torch.from_numpy(points).to(device),
                min(FINAL_RUNTIME["sample_points"], len(points)),
            )
            .cpu()
            .numpy()
        )
    path = cache_root / "reference-implementation" / f"{case_key}.npz"
    with np.load(path) as cached:
        indices = np.asarray(cached["sample_indices"], dtype=np.int64)
    expected = min(FINAL_RUNTIME["sample_points"], len(points))
    if (
        indices.shape != (expected,)
        or len(np.unique(indices)) != expected
        or indices.min() < 0
        or indices.max() >= len(points)
    ):
        raise RuntimeError(f"Invalid fixed FPS index cache: {path}")
    return indices


def _aggregate(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    names = (
        "mask_tooth_iou",
        "tooth_type_point_accuracy",
        "tooth_type_instance_accuracy",
        "fdi_point_accuracy",
        "fdi_instance_accuracy",
        "mean_golden_instance_iou",
        "matched_golden_tooth_accuracy",
    )
    roles = sorted({str(case["role"]) for case in cases})
    return {
        role: {
            name: float(
                np.mean([case[name] for case in cases if case["role"] == role])
            )
            for name in names
        }
        for role in roles
    }


def _golden_arrays(
    document: Mapping[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    if "labels" not in document:
        raise RuntimeError("Golden JSON does not contain a labels array.")
    labels = np.asarray(document["labels"], dtype=np.int16)
    instances = np.asarray(
        document["instances"] if "instances" in document else document["labels"],
        dtype=np.int16,
    )
    if labels.ndim != 1 or instances.shape != labels.shape:
        raise RuntimeError("Golden labels and instances must be aligned 1D arrays.")
    return instances, labels


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--cases-root", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--golden-json", type=Path)
    parser.add_argument("--jaw", choices=("upper", "lower"))
    parser.add_argument(
        "--orientation", choices=("none", "rotate-y-180"), default="none"
    )
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--sample-index-cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-limit", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if (args.input is None) != (args.golden_json is None):
        parser.error("--input and --golden-json must be provided together.")
    if args.input is not None and args.jaw is None:
        parser.error("--jaw is required with --input.")
    if args.input is None and (args.manifest is None or args.cases_root is None):
        parser.error("--manifest and --cases-root are required for dataset runs.")
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise RuntimeError("PYTORCH_ENABLE_MPS_FALLBACK=1 is forbidden.")
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is required.")
    device = torch.device("mps")

    checkpoints: list[dict[str, Any]] = []
    offset_model, metadata = _load_component(args.checkpoint_dir, "offset", device)
    checkpoints.append(metadata)
    class_models = []
    for role in ("class-forward", "class-reverse"):
        model, metadata = _load_component(args.checkpoint_dir, role, device)
        class_models.append(model)
        checkpoints.append(metadata)
    mask_model, metadata = _load_component(args.checkpoint_dir, "mask", device)
    checkpoints.append(metadata)
    second_models = []
    for role in ("second-forward", "second-reverse"):
        model, metadata = _load_component(args.checkpoint_dir, role, device)
        second_models.append(model)
        checkpoints.append(metadata)
    boundary_model, metadata = _load_boundary(args.checkpoint_dir, device)
    checkpoints.append(metadata)

    if args.input is not None:
        requested = [
            {
                "key": args.input.stem,
                "jaw": args.jaw,
                "role": "golden",
                "stratum": "golden",
                "_mesh_path": str(args.input.resolve()),
                "_golden_path": str(args.golden_json.resolve()),
            }
        ]
    else:
        requested = json.loads(args.manifest.read_text(encoding="utf-8"))["cases"]
        requested = requested[: args.case_limit]
    cases: list[dict[str, Any]] = []
    if args.resume and args.output.exists():
        cases = list(json.loads(args.output.read_text(encoding="utf-8")).get("cases", []))
    completed = {str(case["key"]) for case in cases}
    started = time.perf_counter()
    for case_index, case in enumerate(requested):
        if str(case["key"]) in completed:
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
        features = np.concatenate((points, _vertex_normals(mesh)), axis=1).astype(
            np.float32
        )
        sample_indices = _sample_indices(
            points=points,
            device=device,
            cache_root=args.sample_index_cache,
            case_key=str(case["key"]),
        )
        first = _run_final_first(
            class_models=class_models,
            mask_models=[mask_model],
            offset_models=[offset_model],
            second_models=second_models,
            points=points[sample_indices],
            features=features[sample_indices],
            device=device,
        )
        selected, boundary_features, seed_labels, boundary_sampling = _boundary_sample(
            all_points=points,
            all_features=features,
            first_points=points[sample_indices],
            first_labels=first["labels"],
            device=device,
            seed=20260731 + case_index,
        )
        boundary = _run_grouping_module(
            model=boundary_model,
            points=points[selected],
            features=boundary_features,
            device=device,
            seed_labels=seed_labels,
            crop_points=FINAL_RUNTIME["boundary_crop_points"],
        )
        boundary_count = int(boundary_sampling["boundary_selected"])
        (
            sampled_internal,
            effective_sampled_instances,
            semantic_metadata,
        ) = _assign_internal_semantics(
            points[sample_indices], first["labels"], first["classes"]
        )
        full_instances, boundary_merge = _author_boundary_merge(
            all_points=points,
            first_points=points[sample_indices],
            first_labels=effective_sampled_instances,
            boundary_points=points[selected[:boundary_count]],
            boundary_labels=boundary["labels"][:boundary_count],
        )
        full_internal = _propagate_semantics(
            full_instances=full_instances,
            sampled_instances=effective_sampled_instances,
            sampled_internal_semantics=sampled_internal,
        )
        full_fdi = _internal_to_fdi(full_internal, str(case["jaw"]))

        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        golden_instances_full, golden_fdi_full = _golden_arrays(golden)
        golden_instances = golden_instances_full[sample_indices]
        golden_fdi = golden_fdi_full[sample_indices]
        predicted_instances = full_instances[sample_indices]
        predicted_fdi = full_fdi[sample_indices]
        valid = golden_fdi > 0
        predicted_type = first["classes"]
        target_type = golden_fdi % 10
        type_instance = []
        fdi_instance = []
        for instance_id in [int(value) for value in np.unique(golden_instances) if value]:
            members = golden_instances == instance_id
            target_values, target_counts = np.unique(
                golden_fdi[members], return_counts=True
            )
            target = int(target_values[int(np.argmax(target_counts))])
            predicted_type_values, predicted_type_counts = np.unique(
                predicted_type[members], return_counts=True
            )
            predicted_type_majority = int(
                predicted_type_values[int(np.argmax(predicted_type_counts))]
            )
            predicted_fdi_values, predicted_fdi_counts = np.unique(
                predicted_fdi[members], return_counts=True
            )
            predicted_fdi_majority = int(
                predicted_fdi_values[int(np.argmax(predicted_fdi_counts))]
            )
            type_instance.append(predicted_type_majority == target % 10)
            fdi_instance.append(predicted_fdi_majority == target)
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
            "mask_tooth_iou": _overlap(first["refined_mask"], golden_fdi > 0),
            "first_mask_tooth_iou": _overlap(first["mask"], golden_fdi > 0),
            "tooth_type_point_accuracy": float(
                np.mean(predicted_type[valid] == target_type[valid])
            ),
            "tooth_type_instance_accuracy": float(np.mean(type_instance)),
            "fdi_point_accuracy": float(np.mean(predicted_fdi == golden_fdi)),
            "fdi_instance_accuracy": float(np.mean(fdi_instance)),
            **_score_instances(predicted_instances, golden_instances),
            "first_ensemble": first["metadata"],
            "preliminary_grouping": first["preliminary_grouping"],
            "second_ensemble": first["second_ensemble"],
            "final_grouping": first["grouping"],
            "semantic_assignment": semantic_metadata,
            "boundary_pass_grouping": boundary["grouping"],
            "boundary_pass_mrm": boundary["mrm"],
            "boundary_sampling": boundary_sampling,
            "boundary_merge": boundary_merge,
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
        document = {
            "schema": "tgnet_author_challenge_final_compatibility.v1",
            "evaluation_only": True,
            "complete": len(cases) == len(requested),
            "device": "mps",
            "mps_fallback": False,
            "checkpoints": checkpoints,
            "runtime": FINAL_RUNTIME,
            "grouping": AUTHOR_GROUPING,
            "boundary_sampling": AUTHOR_BOUNDARY_SAMPLING,
            "behavior_specification": FINAL_BEHAVIOR_SPECIFICATION,
            "orientation": args.orientation,
            "fixed_fps_index_source": (
                str(args.sample_index_cache.resolve())
                if args.sample_index_cache is not None
                else "computed-by-this-harness"
            ),
            "cases": cases,
        }
        _write_json_atomic(args.output, document)
        print(
            case["key"],
            f"mask={metrics['mask_tooth_iou']:.3f}",
            f"instance={metrics['mean_golden_instance_iou']:.3f}",
            f"fdi={metrics['fdi_point_accuracy']:.3f}",
            flush=True,
        )

    document = json.loads(args.output.read_text(encoding="utf-8"))
    document["complete"] = True
    document["seconds"] = time.perf_counter() - started
    document["aggregate_by_role"] = _aggregate(cases)
    document["strict"] = {
        "pruning_events": sum(
            case["preliminary_grouping"]["pruning_events"]
            + case["final_grouping"]["pruning_events"]
            + case["semantic_assignment"]["pruning_events"]
            + case["boundary_pass_grouping"]["pruning_events"]
            for case in cases
        ),
        "fallback_events": sum(
            case["preliminary_grouping"]["fallback_events"]
            + case["final_grouping"]["fallback_events"]
            + case["semantic_assignment"]["fallback_events"]
            + case["boundary_pass_grouping"]["fallback_events"]
            for case in cases
        ),
    }
    _write_json_atomic(args.output, document)
    print(json.dumps(document["aggregate_by_role"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
