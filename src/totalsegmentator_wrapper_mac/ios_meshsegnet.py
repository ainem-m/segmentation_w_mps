#!/usr/bin/env python3
"""Run the licensed Teeth3DS-trained MeshSegNet checkpoint on an IOS mesh.

The checkpoint used by this script is distributed in the Hugging Face Space
``huathedev/3D-Teeth-Scan-Semantic-Segmentation-with-MeshSegNet`` under the
Apache-2.0 license.  The network definition is compatible with the MIT-licensed
MeshSegNet implementation by Tai-Hsien Wu and the MIT-licensed TeethSegModelling
implementation by Huayuan Song.

This is a research-only inference harness.  It is not a clinical device.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import open3d as o3d
import torch
import torch.nn as nn
import torch.nn.functional as F
import trimesh
from matplotlib.colors import ListedColormap
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from totalsegmentator_wrapper_mac.ios_meshsegnet_manifest import (
    EXPECTED_MODEL_SHA256,
    MODEL_COMMIT,
    MODEL_LICENSE,
    MODEL_SOURCE,
    SUPPORTED_JAWS,
    model_provenance,
)

PALETTE = np.asarray(
    [
        [225, 164, 174, 255],  # gingiva/background
        [230, 25, 75, 255],
        [60, 180, 75, 255],
        [255, 225, 25, 255],
        [0, 130, 200, 255],
        [245, 130, 48, 255],
        [145, 30, 180, 255],
        [70, 240, 240, 255],
        [240, 50, 230, 255],
        [210, 245, 60, 255],
        [250, 190, 212, 255],
        [0, 128, 128, 255],
        [220, 190, 255, 255],
        [170, 110, 40, 255],
        [255, 250, 200, 255],
        [128, 0, 0, 255],
        [0, 0, 128, 255],
    ],
    dtype=np.uint8,
)


ORIENTATION_MATRICES = {
    "identity": np.eye(4, dtype=np.float64),
    "rotate_y_180": np.asarray(
        [
            [-1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, -1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    ),
    "flip_z": np.asarray(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, -1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    ),
}


class STNkd(nn.Module):
    def __init__(self, k: int = 64) -> None:
        super().__init__()
        self.conv1 = nn.Conv1d(k, 64, 1)
        self.conv2 = nn.Conv1d(64, 128, 1)
        self.conv3 = nn.Conv1d(128, 512, 1)
        self.fc1 = nn.Linear(512, 256)
        self.fc2 = nn.Linear(256, 128)
        self.fc3 = nn.Linear(128, k * k)
        self.bn1 = nn.BatchNorm1d(64)
        self.bn2 = nn.BatchNorm1d(128)
        self.bn3 = nn.BatchNorm1d(512)
        self.bn4 = nn.BatchNorm1d(256)
        self.bn5 = nn.BatchNorm1d(128)
        self.k = k

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size = x.size(0)
        x = F.relu(self.bn1(self.conv1(x)))
        x = F.relu(self.bn2(self.conv2(x)))
        x = F.relu(self.bn3(self.conv3(x)))
        x = torch.max(x, 2, keepdim=True)[0].view(-1, 512)
        x = F.relu(self.bn4(self.fc1(x)))
        x = F.relu(self.bn5(self.fc2(x)))
        x = self.fc3(x)
        identity = torch.eye(self.k, dtype=x.dtype, device=x.device)
        x = x + identity.flatten().view(1, -1).repeat(batch_size, 1)
        return x.view(-1, self.k, self.k)


@dataclass(frozen=True)
class GraphAdjacency:
    rows: torch.Tensor
    cols: torch.Tensor
    values: torch.Tensor
    point_count: int

    def to(self, device: torch.device) -> "GraphAdjacency":
        return GraphAdjacency(
            rows=self.rows.to(device),
            cols=self.cols.to(device),
            values=self.values.to(device),
            point_count=self.point_count,
        )


def _aggregate(
    adjacency: GraphAdjacency,
    features: torch.Tensor,
) -> torch.Tensor:
    """Apply row-normalized graph edges to BxNxC features on CPU or MPS."""
    if features.size(0) != 1:
        raise ValueError("This research inference harness expects batch size 1.")
    output = torch.zeros(
        (adjacency.point_count, features.shape[-1]),
        dtype=features.dtype,
        device=features.device,
    )
    # A large-radius graph can contain millions of edges. Materializing all
    # gathered 512-D features at once exceeds practical MPS buffer limits.
    max_temporary_elements = 16_000_000
    edge_chunk = max(1, max_temporary_elements // features.shape[-1])
    for start in range(0, len(adjacency.rows), edge_chunk):
        stop = min(start + edge_chunk, len(adjacency.rows))
        rows = adjacency.rows[start:stop].long()
        cols = adjacency.cols[start:stop].long()
        weights = adjacency.values[start:stop].to(dtype=features.dtype)
        weighted = features[0, cols] * weights.unsqueeze(1)
        output.index_add_(0, rows, weighted)
    return output.unsqueeze(0)


class SparseMeshSegNet(nn.Module):
    """MeshSegNet with sparse graph aggregation and checkpoint-compatible keys."""

    def __init__(
        self,
        num_classes: int = 17,
        num_channels: int = 15,
        *,
        with_dropout: bool = True,
        dropout_p: float = 0.5,
    ) -> None:
        super().__init__()
        self.num_classes = num_classes
        self.num_channels = num_channels
        self.with_dropout = with_dropout

        self.mlp1_conv1 = nn.Conv1d(num_channels, 64, 1)
        self.mlp1_conv2 = nn.Conv1d(64, 64, 1)
        self.mlp1_bn1 = nn.BatchNorm1d(64)
        self.mlp1_bn2 = nn.BatchNorm1d(64)
        self.fstn = STNkd(k=64)

        self.glm1_conv1_1 = nn.Conv1d(64, 32, 1)
        self.glm1_conv1_2 = nn.Conv1d(64, 32, 1)
        self.glm1_bn1_1 = nn.BatchNorm1d(32)
        self.glm1_bn1_2 = nn.BatchNorm1d(32)
        self.glm1_conv2 = nn.Conv1d(64, 64, 1)
        self.glm1_bn2 = nn.BatchNorm1d(64)

        self.mlp2_conv1 = nn.Conv1d(64, 64, 1)
        self.mlp2_bn1 = nn.BatchNorm1d(64)
        self.mlp2_conv2 = nn.Conv1d(64, 128, 1)
        self.mlp2_bn2 = nn.BatchNorm1d(128)
        self.mlp2_conv3 = nn.Conv1d(128, 512, 1)
        self.mlp2_bn3 = nn.BatchNorm1d(512)

        self.glm2_conv1_1 = nn.Conv1d(512, 128, 1)
        self.glm2_conv1_2 = nn.Conv1d(512, 128, 1)
        self.glm2_conv1_3 = nn.Conv1d(512, 128, 1)
        self.glm2_bn1_1 = nn.BatchNorm1d(128)
        self.glm2_bn1_2 = nn.BatchNorm1d(128)
        self.glm2_bn1_3 = nn.BatchNorm1d(128)
        self.glm2_conv2 = nn.Conv1d(384, 512, 1)
        self.glm2_bn2 = nn.BatchNorm1d(512)

        self.mlp3_conv1 = nn.Conv1d(1600, 256, 1)
        self.mlp3_conv2 = nn.Conv1d(256, 256, 1)
        self.mlp3_bn1_1 = nn.BatchNorm1d(256)
        self.mlp3_bn1_2 = nn.BatchNorm1d(256)
        self.mlp3_conv3 = nn.Conv1d(256, 128, 1)
        self.mlp3_conv4 = nn.Conv1d(128, 128, 1)
        self.mlp3_bn2_1 = nn.BatchNorm1d(128)
        self.mlp3_bn2_2 = nn.BatchNorm1d(128)
        self.output_conv = nn.Conv1d(128, num_classes, 1)
        self.dropout = nn.Dropout(p=dropout_p)

    def forward(
        self,
        x: torch.Tensor,
        adjacency_small: GraphAdjacency,
        adjacency_large: GraphAdjacency,
    ) -> torch.Tensor:
        batch_size, _, point_count = x.shape
        x = F.relu(self.mlp1_bn1(self.mlp1_conv1(x)))
        x = F.relu(self.mlp1_bn2(self.mlp1_conv2(x)))

        transform = self.fstn(x)
        x_ftm = torch.bmm(x.transpose(2, 1), transform)
        sap = _aggregate(adjacency_small, x_ftm).transpose(2, 1)
        x_ftm = x_ftm.transpose(2, 1)
        x = F.relu(self.glm1_bn1_1(self.glm1_conv1_1(x_ftm)))
        sap = F.relu(self.glm1_bn1_2(self.glm1_conv1_2(sap)))
        x = F.relu(self.glm1_bn2(self.glm1_conv2(torch.cat([x, sap], dim=1))))

        x = F.relu(self.mlp2_bn1(self.mlp2_conv1(x)))
        x = F.relu(self.mlp2_bn2(self.mlp2_conv2(x)))
        x_mlp2 = F.relu(self.mlp2_bn3(self.mlp2_conv3(x)))
        if self.with_dropout:
            x_mlp2 = self.dropout(x_mlp2)

        x_mlp2_nxc = x_mlp2.transpose(2, 1)
        sap_1 = _aggregate(adjacency_small, x_mlp2_nxc).transpose(2, 1)
        sap_2 = _aggregate(adjacency_large, x_mlp2_nxc).transpose(2, 1)
        x = F.relu(self.glm2_bn1_1(self.glm2_conv1_1(x_mlp2)))
        sap_1 = F.relu(self.glm2_bn1_2(self.glm2_conv1_2(sap_1)))
        sap_2 = F.relu(self.glm2_bn1_3(self.glm2_conv1_3(sap_2)))
        x_glm2 = F.relu(
            self.glm2_bn2(self.glm2_conv2(torch.cat([x, sap_1, sap_2], dim=1)))
        )

        global_features = torch.max(x_glm2, 2, keepdim=True)[0]
        global_features = global_features.expand(-1, -1, point_count)
        x = torch.cat([global_features, x_ftm, x_mlp2, x_glm2], dim=1)
        x = F.relu(self.mlp3_bn1_1(self.mlp3_conv1(x)))
        x = F.relu(self.mlp3_bn1_2(self.mlp3_conv2(x)))
        x = F.relu(self.mlp3_bn2_1(self.mlp3_conv3(x)))
        if self.with_dropout:
            x = self.dropout(x)
        x = F.relu(self.mlp3_bn2_2(self.mlp3_conv4(x)))
        logits = self.output_conv(x).transpose(2, 1).contiguous()
        return torch.softmax(logits.view(-1, self.num_classes), dim=-1).view(
            batch_size, point_count, self.num_classes
        )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def custom_model_metadata(path: Path, model_hash: str) -> dict[str, Any]:
    """Describe a local checkpoint without claiming unknown provenance."""

    return {
        "path": str(path.resolve()),
        "sha256": model_hash,
        "source": "user-provided",
        "commit": None,
        "license": "not-declared",
        "standard_checkpoint": False,
        "checkpoint_redistributed_by_this_project": False,
    }


def checkpoint_state_dict(checkpoint: Any) -> dict[str, Any]:
    if not isinstance(checkpoint, dict):
        raise ValueError("Checkpoint must be a dictionary.")
    candidate = checkpoint.get("model_state_dict", checkpoint)
    if not isinstance(candidate, dict) or not candidate:
        raise ValueError(
            "Checkpoint must contain a non-empty 'model_state_dict' dictionary "
            "or be a raw state dict."
        )
    if not all(isinstance(key, str) for key in candidate):
        raise ValueError("Checkpoint state-dict keys must be strings.")
    if all(key.startswith("module.") for key in candidate):
        return {key.removeprefix("module."): value for key, value in candidate.items()}
    return candidate


def checkpoint_last_metric(checkpoint: dict[str, Any], key: str) -> float | None:
    values = checkpoint.get(key)
    if values is None:
        return None
    try:
        if len(values) == 0:
            return None
        value = values[-1]
    except TypeError:
        value = values
    return float(value)


def _safe_scale(values: np.ndarray) -> np.ndarray:
    return np.where(np.abs(values) < 1.0e-8, 1.0, values)


def mesh_features(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    vertices = np.asarray(mesh.vertices, dtype=np.float32)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    center = vertices.mean(axis=0)
    points = vertices - center
    cells = points[faces].reshape(-1, 9).astype(np.float32)
    barycenters = points[faces].mean(axis=1).astype(np.float32)
    normals = np.asarray(mesh.face_normals, dtype=np.float32)

    means = points.mean(axis=0)
    stds = _safe_scale(points.std(axis=0))
    mins = points.min(axis=0)
    spans = _safe_scale(points.max(axis=0) - mins)
    normal_means = normals.mean(axis=0)
    normal_stds = _safe_scale(normals.std(axis=0))

    for axis in range(3):
        cells[:, axis] = (cells[:, axis] - means[axis]) / stds[axis]
        cells[:, axis + 3] = (cells[:, axis + 3] - means[axis]) / stds[axis]
        cells[:, axis + 6] = (cells[:, axis + 6] - means[axis]) / stds[axis]
        barycenters[:, axis] = (barycenters[:, axis] - mins[axis]) / spans[axis]
        normals[:, axis] = (
            normals[:, axis] - normal_means[axis]
        ) / normal_stds[axis]

    features = np.column_stack((cells, barycenters, normals)).astype(np.float32)
    return features, barycenters


def official_surface_reconstruction(
    vertices: np.ndarray,
    *,
    voxel_size: float = 0.6,
    voxel_stride: int = 20,
) -> trimesh.Trimesh:
    """Reproduce the public Space's voxel sampling and ball-pivoting topology."""
    points = np.asarray(vertices, dtype=np.float64)
    mins = points.min(axis=0)
    dimensions = (
        np.floor((points.max(axis=0) - mins) / voxel_size).astype(np.int64) + 1
    )
    voxel_coordinates = np.floor((points - mins) / voxel_size).astype(np.int64)
    voxel_ids = (
        voxel_coordinates[:, 0]
        + voxel_coordinates[:, 1] * dimensions[0]
        + voxel_coordinates[:, 2] * dimensions[0] * dimensions[1]
    )
    order = np.argsort(voxel_ids)
    sorted_ids = voxel_ids[order]
    starts = np.concatenate(
        ([0], np.flatnonzero(np.diff(sorted_ids)).astype(np.int64) + 1)
    )
    ends = np.concatenate((starts[1:], [len(order)]))
    selected = np.concatenate(
        [order[start:end:voxel_stride] for start, end in zip(starts, ends)]
    )
    sampled = points[selected]

    point_cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(sampled))
    point_cloud.estimate_normals()
    distances = np.asarray(point_cloud.compute_nearest_neighbor_distance())
    average_distance = float(distances.mean())
    radius = 6.0 * average_distance
    reconstructed = o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(
        point_cloud,
        o3d.utility.DoubleVector([radius, radius * 2.0]),
    )
    reconstructed.remove_degenerate_triangles()
    reconstructed.remove_duplicated_triangles()
    reconstructed.remove_duplicated_vertices()
    return trimesh.Trimesh(
        vertices=np.asarray(reconstructed.vertices),
        faces=np.asarray(reconstructed.triangles),
        process=False,
    )


def sparse_adjacency(points: np.ndarray, radius: float) -> GraphAdjacency:
    point_count = len(points)
    pairs = np.asarray(list(cKDTree(points).query_pairs(radius)), dtype=np.int64)
    self_indices = np.arange(point_count, dtype=np.int64)
    if pairs.size:
        rows = np.concatenate((self_indices, pairs[:, 0], pairs[:, 1]))
        cols = np.concatenate((self_indices, pairs[:, 1], pairs[:, 0]))
    else:
        rows = self_indices
        cols = self_indices
    degree = np.bincount(rows, minlength=point_count).astype(np.float32)
    values = 1.0 / degree[rows]
    return GraphAdjacency(
        rows=torch.from_numpy(rows),
        cols=torch.from_numpy(cols),
        values=torch.from_numpy(values),
        point_count=point_count,
    )


def fdi_for_jaw(class_id: int, jaw: str) -> int:
    if jaw not in SUPPORTED_JAWS:
        raise ValueError(
            f"jaw must be one of: {', '.join(SUPPORTED_JAWS)}"
        )
    if 1 <= class_id <= 8:
        return class_id + (10 if jaw == "upper" else 30)
    if 9 <= class_id <= 16:
        return class_id + (12 if jaw == "upper" else 32)
    raise ValueError(f"Class {class_id} is not a tooth class.")


def output_prefix_for_jaw(jaw: str) -> str:
    if jaw not in SUPPORTED_JAWS:
        raise ValueError(f"jaw must be one of: {', '.join(SUPPORTED_JAWS)}")
    return f"ios_{jaw}_meshsegnet"


def labels_to_vertices(
    faces: np.ndarray, face_labels: np.ndarray, vertex_count: int
) -> np.ndarray:
    counts = np.zeros((vertex_count, 17), dtype=np.uint32)
    flat_vertices = faces.reshape(-1)
    flat_labels = np.repeat(face_labels, 3)
    np.add.at(counts, (flat_vertices, flat_labels), 1)
    return counts.argmax(axis=1).astype(np.int16)


def retain_largest_component_per_tooth(
    mesh: trimesh.Trimesh,
    face_labels: np.ndarray,
    jaw: str,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    cleaned = face_labels.copy()
    adjacency = np.asarray(mesh.face_adjacency, dtype=np.int64)
    face_areas = np.asarray(mesh.area_faces)
    cleanup: list[dict[str, Any]] = []
    face_count = len(mesh.faces)

    for class_id in range(1, 17):
        selected_faces = np.flatnonzero(cleaned == class_id)
        if not len(selected_faces):
            continue
        local_index = np.full(face_count, -1, dtype=np.int64)
        local_index[selected_faces] = np.arange(len(selected_faces))
        selected_edges = adjacency[
            (local_index[adjacency[:, 0]] >= 0)
            & (local_index[adjacency[:, 1]] >= 0)
        ]
        if len(selected_edges):
            rows = local_index[selected_edges[:, 0]]
            cols = local_index[selected_edges[:, 1]]
            graph = coo_matrix(
                (
                    np.ones(len(rows) * 2, dtype=np.uint8),
                    (
                        np.concatenate((rows, cols)),
                        np.concatenate((cols, rows)),
                    ),
                ),
                shape=(len(selected_faces), len(selected_faces)),
            ).tocsr()
            component_count, component_ids = connected_components(
                graph,
                directed=False,
            )
        else:
            component_count = len(selected_faces)
            component_ids = np.arange(len(selected_faces), dtype=np.int64)

        component_areas = np.bincount(
            component_ids,
            weights=face_areas[selected_faces],
            minlength=component_count,
        )
        keep_component = int(component_areas.argmax())
        removed_faces = selected_faces[component_ids != keep_component]
        removed_area = float(face_areas[removed_faces].sum())
        cleaned[removed_faces] = 0
        cleanup.append(
            {
                "class_id": class_id,
                "fdi": fdi_for_jaw(class_id, jaw),
                "components_before": int(component_count),
                "removed_faces": int(len(removed_faces)),
                "removed_area": removed_area,
                "retained_area_ratio": float(
                    component_areas[keep_component] / component_areas.sum()
                ),
            }
        )
    return cleaned, cleanup


def pca_coordinates(vertices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    centered = vertices - vertices.mean(axis=0)
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    coordinates = centered @ vh.T
    return coordinates, vh


def render_preview(
    mesh: trimesh.Trimesh,
    face_labels: np.ndarray,
    output_path: Path,
    jaw: str,
) -> None:
    coordinates, _ = pca_coordinates(np.asarray(mesh.vertices))
    triangles = coordinates[np.asarray(mesh.faces)]
    face_colors = PALETTE[face_labels] / 255.0

    fig = plt.figure(figsize=(16, 7), facecolor="#f3f4f6")
    views = [
        ("Occlusal", 90, -90),
        ("Oblique", 28, -58),
        ("Frontal", 4, -90),
    ]
    for index, (title, elevation, azimuth) in enumerate(views, start=1):
        axis = fig.add_subplot(1, 3, index, projection="3d")
        collection = Poly3DCollection(
            triangles,
            facecolors=face_colors,
            edgecolors="none",
            linewidths=0,
        )
        axis.add_collection3d(collection)
        mins = coordinates.min(axis=0)
        maxs = coordinates.max(axis=0)
        center = (mins + maxs) / 2
        radius = float(np.max(maxs - mins) / 2)
        axis.set_xlim(center[0] - radius, center[0] + radius)
        axis.set_ylim(center[1] - radius, center[1] + radius)
        axis.set_zlim(center[2] - radius, center[2] + radius)
        axis.view_init(elev=elevation, azim=azimuth)
        axis.set_box_aspect((1, 1, 0.55))
        axis.set_axis_off()
        axis.set_title(title, fontsize=13, weight="bold")

    fig.suptitle(
        f"MeshSegNet — {jaw} jaw (research preview)",
        fontsize=16,
        weight="bold",
    )
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def render_dense_preview(
    vertices: np.ndarray,
    vertex_labels: np.ndarray,
    output_path: Path,
    *,
    jaw: str,
    max_points: int = 160_000,
    figure_title: str | None = None,
) -> None:
    if figure_title is None:
        figure_title = (
            f"MeshSegNet — {jaw} jaw "
            "(labels transferred to source mesh)"
        )
    coordinates, _ = pca_coordinates(vertices)
    if len(coordinates) > max_points:
        generator = np.random.default_rng(20260730)
        selected = np.sort(
            generator.choice(len(coordinates), size=max_points, replace=False)
        )
        coordinates = coordinates[selected]
        vertex_labels = vertex_labels[selected]

    fig, axes = plt.subplots(1, 3, figsize=(17, 6), facecolor="#f3f4f6")
    projections = [
        ("Occlusal", 0, 1),
        ("Frontal", 0, 2),
        ("Lateral", 1, 2),
    ]
    for axis, (view_title, horizontal, vertical) in zip(
        axes, projections, strict=True
    ):
        background = vertex_labels == 0
        axis.scatter(
            coordinates[background, horizontal],
            coordinates[background, vertical],
            s=0.18,
            c=[PALETTE[0] / 255.0],
            alpha=0.18,
            linewidths=0,
            rasterized=True,
        )
        for class_id in range(1, 17):
            selected = vertex_labels == class_id
            if not selected.any():
                continue
            axis.scatter(
                coordinates[selected, horizontal],
                coordinates[selected, vertical],
                s=0.5,
                c=[PALETTE[class_id] / 255.0],
                alpha=0.94,
                linewidths=0,
                rasterized=True,
                label=str(fdi_for_jaw(class_id, jaw)),
            )
        axis.set_aspect("equal", adjustable="box")
        axis.set_axis_off()
        axis.set_title(view_title, fontsize=13, weight="bold")

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        title="FDI",
        loc="lower center",
        ncol=max(1, len(labels)),
        markerscale=7,
        frameon=False,
    )
    fig.suptitle(figure_title, fontsize=16, weight="bold")
    fig.tight_layout(rect=(0, 0.08, 1, 0.94))
    fig.savefig(output_path, dpi=190, bbox_inches="tight")
    plt.close(fig)


def export_results(
    original: trimesh.Trimesh,
    simplified: trimesh.Trimesh,
    simplified_labels: np.ndarray,
    output_dir: Path,
    jaw: str,
) -> dict[str, Any]:
    if output_dir.is_symlink():
        raise RuntimeError("output directory must not be a symbolic link")
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise RuntimeError("output path must be a directory")
    simple_centers = np.asarray(simplified.triangles_center)
    original_vertices = np.asarray(original.vertices)
    _, vertex_neighbors = cKDTree(simple_centers).query(
        original_vertices, k=3, workers=-1
    )
    neighbor_labels = simplified_labels[
        np.asarray(vertex_neighbors, dtype=np.int64)
    ]
    votes = np.zeros((len(original_vertices), 17), dtype=np.uint8)
    vertex_indices = np.arange(len(original_vertices))
    for neighbor_index in range(neighbor_labels.shape[1]):
        np.add.at(
            votes,
            (vertex_indices, neighbor_labels[:, neighbor_index]),
            1,
        )
    vertex_labels = votes.argmax(axis=1).astype(np.int16)

    faces = np.asarray(original.faces)
    labels_at_corners = vertex_labels[faces]
    a, b, c = labels_at_corners.T
    original_centers = np.asarray(original.triangles_center)
    _, nearest_faces = cKDTree(simple_centers).query(
        original_centers, k=1, workers=-1
    )
    tie_labels = simplified_labels[np.asarray(nearest_faces, dtype=np.int64)]
    face_labels = np.where(
        (a == b) | (a == c),
        a,
        np.where(b == c, b, tie_labels),
    ).astype(np.int16)
    face_labels, component_cleanup = retain_largest_component_per_tooth(
        original,
        face_labels,
        jaw,
    )
    vertex_labels = labels_to_vertices(
        np.asarray(original.faces),
        face_labels,
        len(original.vertices),
    )

    colored = original.copy()
    colored.visual = trimesh.visual.ColorVisuals(
        mesh=colored,
        vertex_colors=PALETTE[vertex_labels],
    )
    output_prefix = output_prefix_for_jaw(jaw)
    colored_path = output_dir / f"{output_prefix}_colored.ply"
    colored.export(colored_path)

    np.savez_compressed(
        output_dir / f"{output_prefix}_labels.npz",
        face_labels=face_labels,
        vertex_labels=vertex_labels,
        simplified_face_labels=simplified_labels,
    )

    area_faces = np.asarray(original.area_faces)
    gingiva_mask = face_labels == 0
    gingiva_face_count = int(gingiva_mask.sum())
    gingiva_path = output_dir / "gingiva.stl"
    gingiva_path.unlink(missing_ok=True)
    if gingiva_face_count:
        original.submesh([gingiva_mask], append=True, repair=False).export(
            gingiva_path
        )
    gingiva = {
        "present": bool(gingiva_face_count),
        "label_id": 0,
        "interpretation": "gingiva-or-background-candidate",
        "face_count": gingiva_face_count,
        "surface_area": float(area_faces[gingiva_mask].sum()),
        "stl": str(gingiva_path.resolve()) if gingiva_face_count else None,
    }

    tooth_dir = output_dir / "teeth_stl"
    if tooth_dir.is_symlink():
        raise RuntimeError("teeth_stl output directory must not be a symbolic link")
    tooth_dir.mkdir(exist_ok=True)
    if not tooth_dir.is_dir():
        raise RuntimeError("teeth_stl output path must be a directory")
    for stale_tooth in tooth_dir.glob("*.stl"):
        if stale_tooth.is_file() or stale_tooth.is_symlink():
            stale_tooth.unlink()
    classes: list[dict[str, Any]] = []
    for class_id in range(1, 17):
        mask = face_labels == class_id
        face_count = int(mask.sum())
        if face_count == 0:
            continue
        fdi = fdi_for_jaw(class_id, jaw)
        submesh = original.submesh([mask], append=True, repair=False)
        stl_path = tooth_dir / f"tooth_{fdi}.stl"
        submesh.export(stl_path)
        classes.append(
            {
                "class_id": class_id,
                "fdi": fdi,
                "face_count": face_count,
                "surface_area": float(area_faces[mask].sum()),
                "stl": str(stl_path.resolve()),
            }
        )

    render_preview(
        simplified,
        simplified_labels,
        output_dir / f"{output_prefix}_preview.png",
        jaw,
    )
    dense_preview_path = output_dir / f"{output_prefix}_dense_preview.png"
    render_dense_preview(
        np.asarray(original.vertices),
        vertex_labels,
        dense_preview_path,
        jaw=jaw,
    )
    return {
        "colored_ply": str(colored_path.resolve()),
        "labels_npz": str(
            (output_dir / f"{output_prefix}_labels.npz").resolve()
        ),
        "preview_png": str(
            (output_dir / f"{output_prefix}_preview.png").resolve()
        ),
        "dense_preview_png": str(dense_preview_path.resolve()),
        "gingiva": gingiva,
        "teeth": classes,
        "component_cleanup": component_cleanup,
        "full_face_label_counts": {
            str(label): int(count)
            for label, count in zip(
                *np.unique(face_labels, return_counts=True), strict=True
            )
        },
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    if args.jaw not in SUPPORTED_JAWS:
        raise ValueError(
            f"Unsupported jaw: {args.jaw}. "
            f"Validated jaws: {', '.join(SUPPORTED_JAWS)}."
        )
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise RuntimeError("PYTORCH_ENABLE_MPS_FALLBACK=1 is forbidden")
    if args.device == "mps":
        if not torch.backends.mps.is_built() or not torch.backends.mps.is_available():
            raise RuntimeError("Apple MPS was requested but is unavailable")
        device = torch.device("mps")
    else:
        device = torch.device("cpu")
    model_hash = sha256(args.model)
    is_standard_model = model_hash == EXPECTED_MODEL_SHA256
    if not is_standard_model and not args.allow_custom_model:
        raise RuntimeError(
            "Model SHA-256 mismatch. "
            f"Expected {EXPECTED_MODEL_SHA256}, got {model_hash}. "
            "Pass --allow-custom-model only when intentionally validating a "
            "user-provided compatible checkpoint."
        )
    model_metadata = (
        {
            "path": str(args.model.resolve()),
            "sha256": model_hash,
            "source": MODEL_SOURCE,
            "commit": MODEL_COMMIT,
            "license": MODEL_LICENSE,
            "standard_checkpoint": True,
            "provenance": model_provenance(),
        }
        if is_standard_model
        else custom_model_metadata(args.model, model_hash)
    )

    loaded = trimesh.load(args.input, process=False, force="mesh")
    if not isinstance(loaded, trimesh.Trimesh):
        raise TypeError("Input did not resolve to a single triangle mesh.")
    if loaded.faces.shape[1] != 3:
        raise ValueError("Input must contain triangular faces.")
    source_original = loaded
    original = source_original.copy()
    orientation_matrix = ORIENTATION_MATRICES[args.orientation]
    original.apply_transform(orientation_matrix)
    if args.preprocess == "official":
        reconstructed = official_surface_reconstruction(
            np.asarray(original.vertices),
            voxel_size=args.voxel_size,
            voxel_stride=args.voxel_stride,
        )
        if len(reconstructed.faces) > args.target_faces:
            simplified = reconstructed.simplify_quadric_decimation(
                face_count=args.target_faces,
                aggression=args.simplification_aggression,
            )
        else:
            simplified = reconstructed
    else:
        reconstructed = None
        simplified = original.simplify_quadric_decimation(
            face_count=args.target_faces,
            aggression=args.simplification_aggression,
        )

    features, graph_points = mesh_features(simplified)
    adjacency_small = sparse_adjacency(graph_points, 0.1)
    adjacency_large = sparse_adjacency(graph_points, 0.2)

    checkpoint = torch.load(args.model, map_location="cpu", weights_only=True)
    model = SparseMeshSegNet(num_classes=17, num_channels=15)
    try:
        model.load_state_dict(checkpoint_state_dict(checkpoint), strict=True)
    except (RuntimeError, TypeError) as exc:
        raise ValueError(
            "The selected checkpoint is not compatible with this MeshSegNet "
            "implementation (17 classes, 15 input features)."
        ) from exc
    model.to(device).eval()

    inputs = torch.from_numpy(features.T[None, ...]).to(device)
    adjacency_small = adjacency_small.to(device)
    adjacency_large = adjacency_large.to(device)
    if device.type == "mps" and hasattr(torch.mps, "reset_peak_memory_stats"):
        torch.mps.reset_peak_memory_stats()
    if device.type == "mps":
        torch.mps.synchronize()
    inference_started = time.perf_counter()
    with torch.inference_mode():
        probabilities_tensor = model(inputs, adjacency_small, adjacency_large)[0]
    if device.type == "mps":
        torch.mps.synchronize()
    inference_seconds = time.perf_counter() - inference_started
    probabilities = probabilities_tensor.cpu().numpy()
    simplified_labels = probabilities.argmax(axis=1).astype(np.int16)

    simplified_for_export = simplified.copy()
    simplified_for_export.apply_transform(np.linalg.inv(orientation_matrix))
    result = export_results(
        source_original,
        simplified_for_export,
        simplified_labels,
        args.output_dir,
        args.jaw,
    )
    confidence = probabilities.max(axis=1)
    summary: dict[str, Any] = {
        "schema": "meshsegnet_ios_research_result.v1",
        "research_only": True,
        "input": {
            "path": str(args.input.resolve()),
            "jaw": args.jaw,
            "vertices": int(len(source_original.vertices)),
            "faces": int(len(source_original.faces)),
        },
        "simplified": {
            "preprocess": args.preprocess,
            "orientation": args.orientation,
            "reconstructed_vertices": (
                int(len(reconstructed.vertices)) if reconstructed is not None else None
            ),
            "reconstructed_faces": (
                int(len(reconstructed.faces)) if reconstructed is not None else None
            ),
            "vertices": int(len(simplified.vertices)),
            "faces": int(len(simplified.faces)),
            "target_faces": args.target_faces,
            "predicted_class_counts": {
                str(label): int(count)
                for label, count in zip(
                    *np.unique(simplified_labels, return_counts=True), strict=True
                )
            },
            "mean_max_probability": float(confidence.mean()),
            "median_max_probability": float(np.median(confidence)),
        },
        "model": model_metadata
        | {
            "checkpoint_epoch": int(checkpoint.get("epoch", -1)),
            "checkpoint_val_mdsc_last": checkpoint_last_metric(
                checkpoint, "val_mdsc"
            ),
        },
        "runtime": {
            "torch": torch.__version__,
            "device": str(device),
            "mps_built": bool(torch.backends.mps.is_built()),
            "mps_available": bool(torch.backends.mps.is_available()),
            "mps_fallback_env": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"),
            "mps_memory": (
                {
                    "current_allocated_memory": int(
                        torch.mps.current_allocated_memory()
                    ),
                    "driver_allocated_memory": int(
                        torch.mps.driver_allocated_memory()
                    ),
                }
                if device.type == "mps"
                else None
            ),
            "inference_seconds": inference_seconds,
            "total_seconds": time.perf_counter() - started,
        },
        "outputs": result,
        "limitations": [
            "No case-specific ground truth was available.",
            "Graph-cut refinement from the public demo was not used.",
            "Labels were transferred to source vertices by 3-nearest face-center "
            "voting, following the public demo's KNN strategy.",
        ],
    }
    summary_path = args.output_dir / "result_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ios-meshsegnet",
        description=(
            "Run the Apache-2.0 Teeth3DS-trained MeshSegNet checkpoint on an "
            "upper- or lower-jaw IOS mesh for non-clinical research."
        ),
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument(
        "--allow-custom-model",
        action="store_true",
        help=(
            "Explicitly allow a user-provided checkpoint whose SHA-256 differs "
            "from the pinned standard model. Architecture compatibility remains "
            "strictly validated."
        ),
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--jaw",
        choices=SUPPORTED_JAWS,
        default="upper",
        help="Select the jaw-specific FDI mapping used for exported tooth STL files.",
    )
    parser.add_argument(
        "--preprocess",
        choices=("official", "quadric"),
        default="official",
    )
    parser.add_argument(
        "--orientation",
        choices=tuple(ORIENTATION_MATRICES),
        default="identity",
    )
    parser.add_argument("--target-faces", type=int, default=12000)
    parser.add_argument("--simplification-aggression", type=int, default=7)
    parser.add_argument("--voxel-size", type=float, default=0.6)
    parser.add_argument("--voxel-stride", type=int, default=20)
    parser.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
