"""Paper-derived TGNet-compatible Point Transformer architecture.

This implementation was written from the Point Transformer paper, the SNU
thesis "End-To-End Deep Learning Network for 3D Tooth Segmentation" (Figure
2.2 and sections 2.2-2.5), and the tensor names/shapes of a user-provided
checkpoint.  It does not contain code copied from the TGNet repository.

The neural network runs on the selected torch device.  Farthest-point sampling
and nearest-neighbour lookup use CPU geometry libraries because the macOS MPS
backend does not provide the custom CUDA point operations used by the paper's
training environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import torch
from torch import Tensor, nn


def enable_per_scan_batchnorm(model: nn.Module) -> int:
    """Use scan statistics for BatchNorm without enabling other train behavior."""
    model.eval()
    count = 0
    for module in model.modules():
        if isinstance(module, nn.modules.batchnorm._BatchNorm):
            module.train()
            count += 1
    return count


def _as_numpy(points: Tensor) -> np.ndarray:
    return np.ascontiguousarray(
        points.detach()
        .to(device="cpu", dtype=torch.float32)
        .numpy()
        .astype(np.float64, copy=False)
    )


def knn_indices(
    query_points: Tensor,
    source_points: Tensor,
    k: int,
) -> tuple[Tensor, Tensor]:
    if query_points.ndim == 3:
        if source_points.ndim != 3 or query_points.shape[0] != source_points.shape[0]:
            raise ValueError("Batched TGNet KNN inputs must share a batch size.")
        per_batch = [
            knn_indices(query_points[index], source_points[index], k)
            for index in range(query_points.shape[0])
        ]
        return (
            torch.stack([item[0] for item in per_batch], dim=0),
            torch.stack([item[1] for item in per_batch], dim=0),
        )
    if query_points.ndim != 2 or source_points.ndim != 2:
        raise ValueError("TGNet KNN expects N×3 or B×N×3 point tensors.")
    from scipy.spatial import cKDTree

    source = _as_numpy(source_points)
    query = _as_numpy(query_points)
    if source.shape[0] == 0:
        raise ValueError("Cannot query an empty TGNet point cloud.")
    actual_k = min(int(k), int(source.shape[0]))
    distances, indices = cKDTree(source).query(query, k=actual_k, workers=-1)
    if actual_k == 1:
        distances = distances[:, None]
        indices = indices[:, None]
    index_tensor = torch.as_tensor(
        np.asarray(indices, dtype=np.int64),
        device=source_points.device,
    )
    distance_tensor = torch.as_tensor(
        np.asarray(distances, dtype=np.float32),
        device=source_points.device,
    )
    return index_tensor, distance_tensor


def farthest_point_indices(points: Tensor, count: int) -> Tensor:
    if points.ndim == 3:
        return torch.stack(
            [
                farthest_point_indices(points[index], count)
                for index in range(points.shape[0])
            ],
            dim=0,
        )
    if points.ndim != 2:
        raise ValueError("TGNet FPS expects N×3 or B×N×3 point tensors.")
    import fpsample

    points_np = _as_numpy(points)
    requested = min(int(count), int(points_np.shape[0]))
    if requested <= 0:
        raise ValueError("TGNet farthest-point sample count must be positive.")
    if requested == points_np.shape[0]:
        return torch.arange(requested, device=points.device, dtype=torch.long)

    # The paper and its cited Point Transformer use exact farthest-point
    # sampling. QuickFPS changes every subsequent neighbourhood and therefore
    # is not a checkpoint-compatible substitute.
    indices = fpsample.fps_sampling(
        points_np,
        requested,
        start_idx=0,
    )
    indices = np.asarray(indices, dtype=np.int64)
    unique = np.unique(indices)
    if unique.size != requested:
        raise RuntimeError(
            "Farthest-point sampling did not return the requested number "
            f"of unique vertices ({unique.size} != {requested})."
        )
    return torch.as_tensor(indices, device=points.device, dtype=torch.long)


def interpolate_features(
    query_points: Tensor,
    source_points: Tensor,
    source_features: Tensor,
    *,
    k: int = 3,
) -> Tensor:
    indices, distances = knn_indices(query_points, source_points, k)
    weights = 1.0 / torch.clamp(distances, min=1.0e-8)
    weights = weights / weights.sum(dim=-1, keepdim=True)
    gathered = _gather_neighbors(source_features, indices)
    return (gathered * weights.unsqueeze(-1)).sum(dim=-2)


def _gather_samples(values: Tensor, indices: Tensor) -> Tensor:
    if values.ndim == 2:
        return values[indices]
    batch = torch.arange(values.shape[0], device=values.device).unsqueeze(1)
    return values[batch, indices]


def _gather_neighbors(values: Tensor, indices: Tensor) -> Tensor:
    if values.ndim == 2:
        return values[indices]
    batch = torch.arange(values.shape[0], device=values.device).view(-1, 1, 1)
    return values[batch, indices]


def _batchnorm_last_dim(layer: nn.Module, values: Tensor) -> Tensor:
    original_shape = values.shape
    normalized = layer(values.reshape(-1, original_shape[-1]))
    return normalized.reshape(*original_shape[:-1], normalized.shape[-1])


def _apply_last_dim(sequence: nn.Sequential, values: Tensor) -> Tensor:
    original_shape = values.shape
    flattened = values.reshape(-1, original_shape[-1])
    for layer in sequence:
        flattened = layer(flattened)
    return flattened.reshape(*original_shape[:-1], flattened.shape[-1])


class TransitionDown(nn.Module):
    def __init__(
        self,
        in_planes: int,
        out_planes: int,
        *,
        stride: int,
        nsample: int,
    ) -> None:
        super().__init__()
        self.stride = stride
        self.nsample = nsample
        input_width = in_planes if stride == 1 else in_planes + 3
        self.linear = nn.Linear(input_width, out_planes, bias=False)
        self.bn = nn.BatchNorm1d(out_planes)

    def forward(self, points: Tensor, features: Tensor) -> tuple[Tensor, Tensor]:
        if self.stride == 1:
            return points, torch.relu(
                _batchnorm_last_dim(self.bn, self.linear(features))
            )

        sample_count = max(1, points.shape[-2] // self.stride)
        sample_indices = farthest_point_indices(points, sample_count)
        sampled_points = _gather_samples(points, sample_indices)
        neighbours, _ = knn_indices(sampled_points, points, self.nsample)
        relative = _gather_neighbors(points, neighbours) - sampled_points.unsqueeze(
            -2
        )
        grouped = torch.cat(
            (relative, _gather_neighbors(features, neighbours)), dim=-1
        )
        projected = _apply_last_dim(
            nn.Sequential(self.linear, self.bn, nn.ReLU(inplace=False)),
            grouped,
        )
        return sampled_points, projected.max(dim=-2).values


class PointTransformerLayer(nn.Module):
    def __init__(
        self,
        in_planes: int,
        out_planes: int,
        *,
        share_planes: int = 8,
        nsample: int = 16,
        attention_relation: str = "key-minus-query",
        position_relation: str = "neighbor-minus-query",
    ) -> None:
        super().__init__()
        if out_planes % share_planes != 0:
            raise ValueError("Point Transformer width must divide share_planes.")
        self.share_planes = share_planes
        self.nsample = nsample
        if attention_relation not in ("query-minus-key", "key-minus-query"):
            raise ValueError("Unsupported Point Transformer attention relation.")
        self.attention_relation = attention_relation
        if position_relation not in (
            "query-minus-neighbor",
            "neighbor-minus-query",
        ):
            raise ValueError("Unsupported Point Transformer position relation.")
        self.position_relation = position_relation
        self.linear_q = nn.Linear(in_planes, out_planes)
        self.linear_k = nn.Linear(in_planes, out_planes)
        self.linear_v = nn.Linear(in_planes, out_planes)
        self.linear_p = nn.Sequential(
            nn.Linear(3, 3),
            nn.BatchNorm1d(3),
            nn.ReLU(inplace=False),
            nn.Linear(3, out_planes),
        )
        shared_width = out_planes // share_planes
        self.linear_w = nn.Sequential(
            nn.BatchNorm1d(out_planes),
            nn.ReLU(inplace=False),
            nn.Linear(out_planes, shared_width),
            nn.BatchNorm1d(shared_width),
            nn.ReLU(inplace=False),
            nn.Linear(shared_width, shared_width),
        )

    def forward(self, points: Tensor, features: Tensor) -> Tensor:
        neighbours, _ = knn_indices(points, points, self.nsample)
        query = self.linear_q(features).unsqueeze(-2)
        key = _gather_neighbors(self.linear_k(features), neighbours)
        value = _gather_neighbors(self.linear_v(features), neighbours)
        relative = (
            points.unsqueeze(-2) - _gather_neighbors(points, neighbours)
            if self.position_relation == "query-minus-neighbor"
            else _gather_neighbors(points, neighbours) - points.unsqueeze(-2)
        )
        position = _apply_last_dim(self.linear_p, relative)
        difference = (
            query - key
            if self.attention_relation == "query-minus-key"
            else key - query
        )
        attention = _apply_last_dim(self.linear_w, difference + position)
        attention = torch.softmax(attention, dim=-2)

        grouped_width = attention.shape[-1]
        values = (value + position).reshape(
            *value.shape[:-1],
            self.share_planes,
            grouped_width,
        )
        weighted = values * attention.unsqueeze(-2)
        return weighted.sum(dim=-3).reshape(*features.shape[:-1], -1)


class PointTransformerBlock(nn.Module):
    def __init__(
        self,
        in_planes: int,
        planes: int,
        *,
        nsample: int,
        attention_relation: str = "key-minus-query",
        position_relation: str = "neighbor-minus-query",
    ) -> None:
        super().__init__()
        self.linear1 = nn.Linear(in_planes, planes, bias=False)
        self.bn1 = nn.BatchNorm1d(planes)
        self.transformer2 = PointTransformerLayer(
            planes,
            planes,
            share_planes=8,
            nsample=nsample,
            attention_relation=attention_relation,
            position_relation=position_relation,
        )
        self.bn2 = nn.BatchNorm1d(planes)
        self.linear3 = nn.Linear(planes, planes, bias=False)
        self.bn3 = nn.BatchNorm1d(planes)

    def forward(self, points: Tensor, features: Tensor) -> tuple[Tensor, Tensor]:
        identity = features
        features = torch.relu(
            _batchnorm_last_dim(self.bn1, self.linear1(features))
        )
        features = torch.relu(
            _batchnorm_last_dim(self.bn2, self.transformer2(points, features))
        )
        features = _batchnorm_last_dim(self.bn3, self.linear3(features))
        return points, torch.relu(features + identity)


class TransitionUp(nn.Module):
    def __init__(
        self,
        in_planes: int,
        out_planes: int | None = None,
    ) -> None:
        super().__init__()
        if out_planes is None:
            self.linear1 = nn.Sequential(
                nn.Linear(in_planes * 2, in_planes),
                nn.BatchNorm1d(in_planes),
                nn.ReLU(inplace=False),
            )
            self.linear2 = nn.Sequential(
                nn.Linear(in_planes, in_planes),
                nn.ReLU(inplace=False),
            )
        else:
            self.linear1 = nn.Sequential(
                nn.Linear(out_planes, out_planes),
                nn.BatchNorm1d(out_planes),
                nn.ReLU(inplace=False),
            )
            self.linear2 = nn.Sequential(
                nn.Linear(in_planes, out_planes),
                nn.BatchNorm1d(out_planes),
                nn.ReLU(inplace=False),
            )

    def forward(
        self,
        points: Tensor,
        features: Tensor,
        coarse_points: Tensor | None = None,
        coarse_features: Tensor | None = None,
    ) -> Tensor:
        if coarse_points is None or coarse_features is None:
            context = _apply_last_dim(
                self.linear2, features.mean(dim=-2, keepdim=True)
            )
            context = context.expand(*features.shape[:-2], features.shape[-2], -1)
            return _apply_last_dim(
                self.linear1, torch.cat((features, context), dim=-1)
            )
        skip = _apply_last_dim(self.linear1, features)
        coarse = _apply_last_dim(self.linear2, coarse_features)
        return skip + interpolate_features(points, coarse_points, coarse)


class MultiScaleInfer(nn.Module):
    def __init__(self, in_planes: int) -> None:
        super().__init__()
        self.infer = nn.Sequential(
            nn.Linear(in_planes, 32),
            nn.BatchNorm1d(32),
            nn.ReLU(inplace=False),
        )

    def forward(self, features: Tensor) -> Tensor:
        return _apply_last_dim(self.infer, features)


class MultiScaleHead(nn.Module):
    def __init__(
        self,
        class_count: int,
        widths: Sequence[int] = (32, 64, 128, 256, 512),
    ) -> None:
        super().__init__()
        # The multi-scale head cited by the TGNet thesis uses one-neighbour
        # interpolation when projecting coarser decoder features to level 0.
        self.interpolation_neighbors = 1
        self.infer_list = nn.ModuleList(
            MultiScaleInfer(int(width)) for width in widths
        )
        self.cls = nn.Linear(32 * len(widths), class_count)

    def forward(
        self,
        points: Sequence[Tensor],
        features: Sequence[Tensor],
    ) -> Tensor:
        target_points = points[0]
        outputs: list[Tensor] = []
        for index, (level_points, level_features) in enumerate(
            zip(points, features, strict=True)
        ):
            inferred = self.infer_list[index](level_features)
            if index:
                inferred = interpolate_features(
                    target_points,
                    level_points,
                    inferred,
                    k=self.interpolation_neighbors,
                )
            outputs.append(inferred)
        return self.cls(torch.cat(outputs, dim=-1))


@dataclass
class TGNetBackboneOutput:
    points: list[Tensor]
    features: list[Tensor]
    mask_logits: Tensor
    class_logits: Tensor
    offsets: Tensor


class TGNetPointTransformer(nn.Module):
    """Checkpoint-compatible PGM or MRM Point Transformer."""

    def __init__(
        self,
        class_count: int,
        *,
        attention_relation: str = "key-minus-query",
        position_relation: str = "neighbor-minus-query",
        strides: Sequence[int] = (1, 2, 2, 2, 2),
        nsamples: Sequence[int] = (8, 16, 16, 16, 16),
        widths: Sequence[int] = (32, 64, 128, 256, 512),
        blocks: Sequence[int] = (1, 2, 3, 5, 2),
    ) -> None:
        super().__init__()
        level_count = len(widths)
        if level_count < 1:
            raise ValueError("TGNet must contain at least one encoder level.")
        if len(strides) != level_count or int(strides[0]) != 1:
            raise ValueError(
                "TGNet strides must match widths and start with 1."
            )
        if len(nsamples) != level_count or any(
            int(value) <= 0 for value in nsamples
        ):
            raise ValueError("TGNet nsamples must match widths and be positive.")
        if len(blocks) != level_count or any(int(value) < 0 for value in blocks):
            raise ValueError("TGNet blocks must match widths and be nonnegative.")
        self.attention_relation = attention_relation
        self.position_relation = position_relation
        self.strides = tuple(int(value) for value in strides)
        self.nsamples = tuple(int(value) for value in nsamples)
        self.widths = tuple(int(value) for value in widths)
        self.blocks = tuple(int(value) for value in blocks)

        for index, width in enumerate(self.widths):
            input_width = 6 if index == 0 else self.widths[index - 1]
            setattr(
                self,
                f"enc{index + 1}",
                self._encoder(
                    input_width,
                    width,
                    self.blocks[index],
                    stride=self.strides[index],
                    nsample=self.nsamples[index],
                ),
            )
        deepest = level_count - 1
        setattr(
            self,
            f"dec{level_count}",
            nn.ModuleList(
                (
                    TransitionUp(self.widths[deepest]),
                    PointTransformerBlock(
                        self.widths[deepest],
                        self.widths[deepest],
                        nsample=self.nsamples[deepest],
                        attention_relation=attention_relation,
                        position_relation=position_relation,
                    ),
                )
            ),
        )
        for index in range(deepest - 1, -1, -1):
            setattr(
                self,
                f"dec{index + 1}",
                self._decoder(
                    self.widths[index + 1],
                    self.widths[index],
                    nsample=self.nsamples[index],
                ),
            )

        self.mask_head = MultiScaleHead(2, self.widths)
        self.cls_head = MultiScaleHead(class_count, self.widths)
        self.offset_head = MultiScaleHead(3, self.widths)

    def _encoder(
        self,
        in_planes: int,
        out_planes: int,
        block_count: int,
        *,
        stride: int,
        nsample: int,
    ) -> nn.ModuleList:
        layers: list[nn.Module] = [
            TransitionDown(
                in_planes,
                out_planes,
                stride=stride,
                nsample=nsample,
            )
        ]
        layers.extend(
            PointTransformerBlock(
                out_planes,
                out_planes,
                nsample=nsample,
                attention_relation=self.attention_relation,
                position_relation=self.position_relation,
            )
            for _ in range(block_count)
        )
        return nn.ModuleList(layers)

    def _decoder(
        self, in_planes: int, out_planes: int, *, nsample: int
    ) -> nn.ModuleList:
        return nn.ModuleList(
            (
                TransitionUp(in_planes, out_planes),
                PointTransformerBlock(
                    out_planes,
                    out_planes,
                    nsample=nsample,
                    attention_relation=self.attention_relation,
                    position_relation=self.position_relation,
                ),
            )
        )

    @staticmethod
    def _run_encoder(
        encoder: nn.ModuleList,
        points: Tensor,
        features: Tensor,
    ) -> tuple[Tensor, Tensor]:
        points, features = encoder[0](points, features)
        for block in encoder[1:]:
            points, features = block(points, features)
        return points, features

    @staticmethod
    def _run_decoder(
        decoder: nn.ModuleList,
        points: Tensor,
        features: Tensor,
        coarse_points: Tensor | None = None,
        coarse_features: Tensor | None = None,
    ) -> tuple[Tensor, Tensor]:
        features = decoder[0](
            points,
            features,
            coarse_points,
            coarse_features,
        )
        points, features = decoder[1](points, features)
        return points, features

    def forward(self, points: Tensor, features: Tensor) -> TGNetBackboneOutput:
        encoded_points: list[Tensor] = []
        encoded_features: list[Tensor] = []
        level_points, level_features = points, features
        for index in range(len(self.widths)):
            level_points, level_features = self._run_encoder(
                getattr(self, f"enc{index + 1}"),
                level_points,
                level_features,
            )
            encoded_points.append(level_points)
            encoded_features.append(level_features)

        decoded_points = list(encoded_points)
        decoded_features = list(encoded_features)
        deepest = len(self.widths) - 1
        decoded_points[deepest], decoded_features[deepest] = self._run_decoder(
            getattr(self, f"dec{deepest + 1}"),
            encoded_points[deepest],
            encoded_features[deepest],
        )
        for index in range(deepest - 1, -1, -1):
            decoded_points[index], decoded_features[index] = self._run_decoder(
                getattr(self, f"dec{index + 1}"),
                encoded_points[index],
                encoded_features[index],
                decoded_points[index + 1],
                decoded_features[index + 1],
            )
        return TGNetBackboneOutput(
            points=decoded_points,
            features=decoded_features,
            mask_logits=self.mask_head(decoded_points, decoded_features),
            class_logits=self.cls_head(decoded_points, decoded_features),
            offsets=self.offset_head(decoded_points, decoded_features),
        )


class TGNetCheckpointModel(nn.Module):
    def __init__(
        self,
        *,
        attention_relation: str = "key-minus-query",
        position_relation: str = "neighbor-minus-query",
        strides: Sequence[int] = (1, 2, 2, 2, 2),
        nsamples: Sequence[int] = (8, 16, 16, 16, 16),
        widths: Sequence[int] = (32, 64, 128, 256, 512),
        blocks: Sequence[int] = (1, 2, 3, 5, 2),
    ) -> None:
        super().__init__()
        self.first_ins_cent_model = TGNetPointTransformer(
            class_count=10,
            attention_relation=attention_relation,
            position_relation=position_relation,
            strides=strides,
            nsamples=nsamples,
            widths=widths,
            blocks=blocks,
        )
        self.second_ins_cent_model = TGNetPointTransformer(
            class_count=2,
            attention_relation=attention_relation,
            position_relation=position_relation,
            strides=strides,
            nsamples=nsamples,
            widths=widths,
            blocks=blocks,
        )
