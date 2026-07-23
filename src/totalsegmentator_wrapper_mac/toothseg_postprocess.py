from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
from nibabel.processing import resample_from_to, resample_to_output
from scipy.stats import multivariate_normal


FDI_BY_CLASS_INDEX = (
    0,
    11, 12, 13, 14, 15, 16, 17, 18,
    21, 22, 23, 24, 25, 26, 27, 28,
    31, 32, 33, 34, 35, 36, 37, 38,
    41, 42, 43, 44, 45, 46, 47, 48,
)


def resample_image_to_spacing(
    input_path: Path,
    output_path: Path,
    *,
    spacing: tuple[float, float, float] = (0.2, 0.2, 0.2),
) -> dict[str, Any]:
    image = nib.load(str(input_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resampled = resample_to_output(image, voxel_sizes=spacing, order=1)
    resampled.set_data_dtype(np.float32)
    nib.save(resampled, str(output_path))
    return {
        "source_shape": [int(value) for value in image.shape[:3]],
        "source_spacing": [float(value) for value in image.header.get_zooms()[:3]],
        "target_shape": [int(value) for value in resampled.shape[:3]],
        "target_spacing": [float(value) for value in resampled.header.get_zooms()[:3]],
    }


def border_core_to_instances(input_path: Path, output_path: Path) -> dict[str, Any]:
    from acvl_utils.instance_segmentation.instance_as_semantic_seg import (
        convert_semantic_to_instanceseg,
        postprocess_instance_segmentation,
    )

    image = nib.load(str(input_path))
    data = np.asanyarray(image.dataobj).astype(np.uint8, copy=True)
    spacing = np.asarray(image.header.get_zooms()[:3], dtype=float)
    instances = convert_semantic_to_instanceseg(
        data,
        spacing,
        small_center_threshold=16,
        isolated_border_as_separate_instance_threshold=0,
    )
    instances = postprocess_instance_segmentation(instances)
    labels, counts = np.unique(instances, return_counts=True)
    for label, count in zip(labels, counts, strict=True):
        if label != 0 and float(count) * float(np.prod(spacing)) < 16:
            instances[instances == label] = 0
    instances = postprocess_instance_segmentation(instances).astype(np.uint16, copy=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = image.header.copy()
    header.set_data_dtype(np.uint16)
    nib.save(nib.Nifti1Image(instances, image.affine, header), str(output_path))
    return {"instance_count": int(np.unique(instances).size - 1)}


def resample_segmentation_to_reference(
    input_path: Path,
    reference_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    segmentation = nib.load(str(input_path))
    reference = nib.load(str(reference_path))
    resampled = resample_from_to(segmentation, reference, order=0)
    data = np.asanyarray(resampled.dataobj).astype(np.uint16, copy=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = reference.header.copy()
    header.set_data_dtype(np.uint16)
    nib.save(nib.Nifti1Image(data, reference.affine, header), str(output_path))
    return {
        "shape": [int(value) for value in data.shape],
        "spacing": [float(value) for value in reference.header.get_zooms()[:3]],
    }


def assign_mincost_tooth_labels(
    *,
    instance_path: Path,
    semantic_probabilities_path: Path,
    distributions_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    instance_image = nib.load(str(instance_path))
    instance_data = np.asanyarray(instance_image.dataobj)
    orientation = nib.io_orientation(instance_image.affine)
    canonical_reference = instance_image.as_reoriented(orientation)
    canonical_instances = nib.apply_orientation(instance_data, orientation)
    original_ids, inverse = np.unique(canonical_instances, return_inverse=True)
    canonical_instances = inverse.reshape(canonical_instances.shape).astype(np.uint16)

    probabilities = np.load(semantic_probabilities_path)["probabilities"]
    if probabilities.ndim != 4 or probabilities.shape[0] != 33:
        raise RuntimeError(f"ToothSeg semantic probabilities have unexpected shape: {probabilities.shape}")
    probabilities = np.maximum(probabilities, 1e-6)
    canonical_probabilities = nib.apply_orientation(
        probabilities.transpose(0, 3, 2, 1),
        np.concatenate((np.array([[0, 1]]), np.column_stack((orientation[:, 0] + 1, orientation[:, 1])))),
    )
    if canonical_probabilities.shape[1:] != canonical_instances.shape:
        raise RuntimeError(
            "ToothSeg semantic/instance grids do not match after resampling: "
            f"{canonical_probabilities.shape[1:]} != {canonical_instances.shape}"
        )

    centroids, instance_probabilities, split_instances = _prepare_instances(
        canonical_instances,
        canonical_probabilities,
        np.asarray(canonical_reference.header.get_zooms()[:3]),
    )
    if centroids.shape[0] == 0:
        raise RuntimeError("ToothSeg instance branch produced no usable tooth instances")

    pair_dists = json.loads(distributions_path.read_text(encoding="utf-8"))
    normals = _build_normals(pair_dists)
    background = instance_probabilities[:, 0] >= 0.95
    centroids = centroids[~background]
    instance_probabilities = instance_probabilities[~background]
    if centroids.shape[0] == 0:
        raise RuntimeError("ToothSeg semantic branch classified every instance as background")

    is_lower = instance_probabilities[:, 17:].sum(-1) > instance_probabilities[:, 1:17].sum(-1)
    class_indices = np.zeros(centroids.shape[0], dtype=np.uint8)
    for lower_arch in (False, True):
        arch_indices = np.flatnonzero(is_lower == lower_arch)
        if arch_indices.size == 0:
            continue
        arch_centroids = centroids[arch_indices]
        arch_probs = instance_probabilities[arch_indices, 17:] if lower_arch else instance_probabilities[arch_indices, 1:17]
        arch_probs /= np.maximum(arch_probs.sum(axis=1, keepdims=True), 1e-6)
        sequence, _inverse = _determine_sequence(arch_centroids)
        transitions = _transition_log_probabilities(normals, arch_centroids, lower_arch, sequence)
        path = _dynamic_programming(arch_probs, sequence, transitions)
        class_indices[arch_indices[sequence]] = path + (16 if lower_arch else 0) + 1

    instance_map = np.zeros(background.shape[0] + 1, dtype=np.uint8)
    instance_map[np.flatnonzero(~background) + 1] = class_indices
    class_segmentation = instance_map[split_instances]
    fdi_segmentation = np.asarray(FDI_BY_CLASS_INDEX, dtype=np.uint8)[class_segmentation]
    canonical_output = nib.Nifti1Image(
        fdi_segmentation,
        canonical_reference.affine,
        dtype=np.uint8,
    )
    restored_image = resample_from_to(canonical_output, instance_image, order=0)
    restored = np.asanyarray(restored_image.dataobj).astype(np.uint8, copy=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(restored, instance_image.affine, dtype=np.uint8), str(output_path))
    labels, counts = np.unique(restored, return_counts=True)
    nonzero = [
        {"label": int(label), "name": f"FDI {int(label)}", "voxels": int(count)}
        for label, count in zip(labels, counts, strict=True)
        if label != 0
    ]
    return {
        "source_instance_ids": int(original_ids.size - 1),
        "output_tooth_count": len(nonzero),
        "non_empty_labels": nonzero,
    }


def _prepare_instances(
    instances: np.ndarray,
    probabilities: np.ndarray,
    spacing: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    centroids: list[np.ndarray] = []
    scores: list[np.ndarray] = []
    output = np.zeros_like(instances, dtype=np.uint16)
    for instance_id in range(1, int(instances.max()) + 1):
        mask = instances == instance_id
        if not np.any(mask):
            continue
        voxel_probs = probabilities[:, mask]
        class_ids = voxel_probs.argmax(0)
        class_scores = np.zeros(33)
        for class_id in np.flatnonzero(voxel_probs.mean(1) >= 0.1):
            selected = class_ids == class_id
            if np.any(selected):
                class_scores[class_id] = voxel_probs[class_id, selected].mean()
        split_ids = (
            voxel_probs[np.flatnonzero(class_scores[1:] >= 0.95) + 1].argmax(0)
            if (class_scores[1:] >= 0.95).sum() > 1
            else np.zeros(mask.sum(), dtype=int)
        )
        voxel_indices = np.column_stack(np.nonzero(mask))
        for split_id in np.unique(split_ids):
            split_mask = split_ids == split_id
            centroids.append(voxel_indices[split_mask].mean(0) * spacing)
            scores.append(voxel_probs[:, split_mask].mean(1))
            output[tuple(voxel_indices[split_mask].T)] = int(output.max()) + 1
    return np.asarray(centroids), np.asarray(scores), output


def _build_normals(pair_dists: dict[str, Any]) -> list[list[Any]]:
    normals: list[list[Any]] = []
    for i in range(32):
        row = []
        for j in range(32):
            if i // 16 != j // 16:
                row.append(None)
            else:
                row.append(
                    multivariate_normal(
                        mean=pair_dists["means"][i][j][:2],
                        cov=np.asarray(pair_dists["covs"][i][j])[:2, :2],
                        allow_singular=True,
                    )
                )
        normals.append(row)
    return normals


def _determine_sequence(centroids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    sequence = np.full(centroids.shape[0], -1, dtype=int)
    inverse = np.full(centroids.shape[0], -1, dtype=int)
    first = int(centroids[:, 1].argmin())
    sequence[0] = first
    inverse[first] = 0
    for index in range(1, centroids.shape[0]):
        remaining = np.flatnonzero(inverse == -1)
        distances = np.linalg.norm(centroids[remaining] - centroids[sequence[index - 1]], axis=-1)
        selected = int(remaining[distances.argmin()])
        sequence[index] = selected
        inverse[selected] = index
    return sequence, inverse


def _transition_log_probabilities(
    normals: list[list[Any]],
    centroids: np.ndarray,
    lower_arch: bool,
    sequence: np.ndarray,
) -> np.ndarray:
    offset = 16 if lower_arch else 0
    values = np.zeros((centroids.shape[0] - 1, 16, 16))
    for index, (first, second) in enumerate(zip(sequence[:-1], sequence[1:], strict=True)):
        displacement = centroids[second] - centroids[first]
        for source in range(16):
            for target in range(16):
                values[index, source, target] = normals[offset + source][offset + target].logpdf(displacement[:2])
    return values


def _dynamic_programming(
    tooth_probabilities: np.ndarray,
    sequence: np.ndarray,
    transition_log_probabilities: np.ndarray,
    *,
    tooth_factor: float = 4.0,
) -> np.ndarray:
    log_probs = np.log(np.maximum(tooth_probabilities, 1e-12))
    costs = np.zeros_like(log_probs)
    backpointers = np.zeros_like(costs, dtype=int)
    costs[0] = -tooth_factor * log_probs[sequence[0]]
    for index in range(1, tooth_probabilities.shape[0]):
        for label in range(16):
            candidates = costs[index - 1] - transition_log_probabilities[index - 1, :, label]
            backpointers[index, label] = int(candidates.argmin())
            costs[index, label] = candidates.min() - tooth_factor * log_probs[sequence[index], label]
    path = np.zeros(tooth_probabilities.shape[0], dtype=int)
    path[-1] = int(costs[-1].argmin())
    for index in range(path.size - 1, 0, -1):
        path[index - 1] = backpointers[index, path[index]]
    return path
