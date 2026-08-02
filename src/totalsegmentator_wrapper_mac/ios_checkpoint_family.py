"""Strict checkpoint-family detection for intra-oral mesh models.

TGNet weights are user-provided.  This module identifies the architecture from
tensor names and shapes without assigning provenance or a license to the file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class CheckpointCompatibilityError(ValueError):
    """Raised when a checkpoint resembles a supported family but is incompatible."""


@dataclass(frozen=True)
class CheckpointAnalysis:
    model_family: str
    input_features: int
    class_count: int
    architecture_validation: dict[str, Any]


_TGNET_ENCODER_WIDTHS = (32, 64, 128, 256, 512)
_TGNET_ANCHOR_SHAPES: dict[str, tuple[int, ...]] = {}
for _prefix, _class_count in (
    ("first_ins_cent_model", 10),
    ("second_ins_cent_model", 2),
):
    _previous_width = 3
    for _level, _width in enumerate(_TGNET_ENCODER_WIDTHS, start=1):
        _input_width = 6 if _level == 1 else _previous_width + 3
        _TGNET_ANCHOR_SHAPES[
            f"{_prefix}.enc{_level}.0.linear.weight"
        ] = (_width, _input_width)
        _previous_width = _width
    _TGNET_ANCHOR_SHAPES[f"{_prefix}.mask_head.cls.weight"] = (2, 160)
    _TGNET_ANCHOR_SHAPES[f"{_prefix}.cls_head.cls.weight"] = (
        _class_count,
        160,
    )
    _TGNET_ANCHOR_SHAPES[f"{_prefix}.offset_head.cls.weight"] = (3, 160)

_MESHSEGNET_ANCHOR_SHAPES = {
    "mlp1_conv1.weight": (64, 15, 1),
    "fstn.fc3.weight": (4096, 128),
    "glm2_conv2.weight": (512, 384, 1),
    "mlp3_conv1.weight": (256, 1600, 1),
    "output_conv.weight": (17, 128, 1),
    "output_conv.bias": (17,),
}


def _shape(value: Any) -> tuple[int, ...]:
    shape = getattr(value, "shape", None)
    if shape is None:
        raise CheckpointCompatibilityError(
            "Checkpoint state values must be tensors with a shape."
        )
    return tuple(int(dimension) for dimension in shape)


def unwrap_state_dict(checkpoint: Any) -> Mapping[str, Any]:
    if not isinstance(checkpoint, Mapping):
        raise CheckpointCompatibilityError(
            "Checkpoint must be a state-dict mapping or contain model_state_dict."
        )
    candidate = checkpoint.get("model_state_dict", checkpoint)
    if not isinstance(candidate, Mapping) or not candidate:
        raise CheckpointCompatibilityError(
            "Checkpoint must contain a non-empty model_state_dict mapping."
        )
    keys = [str(key) for key in candidate]
    if keys and all(key.startswith("module.") for key in keys):
        return {str(key)[7:]: value for key, value in candidate.items()}
    return {str(key): value for key, value in candidate.items()}


def analyze_checkpoint_state_dict(
    state_dict: Mapping[str, Any],
) -> CheckpointAnalysis:
    keys = set(state_dict)
    tgnet_like = any(
        key.startswith(("first_ins_cent_model.", "second_ins_cent_model."))
        for key in keys
    )
    if not tgnet_like:
        raise CheckpointCompatibilityError(
            "Checkpoint does not match the TGNet architecture signature."
        )

    validated: list[dict[str, Any]] = []
    for key, expected_shape in _TGNET_ANCHOR_SHAPES.items():
        if key not in state_dict:
            raise CheckpointCompatibilityError(
                f"TGNet checkpoint is missing required tensor: {key}"
            )
        actual_shape = _shape(state_dict[key])
        if actual_shape != expected_shape:
            raise CheckpointCompatibilityError(
                f"TGNet tensor {key} has shape {actual_shape}; "
                f"expected shape {expected_shape}."
            )
        validated.append({"key": key, "shape": list(actual_shape)})

    return CheckpointAnalysis(
        model_family="tgnet",
        input_features=6,
        class_count=10,
        architecture_validation={
            "passed": True,
            "basis": [
                "SNU thesis Figure 2.2 and sections 2.2-2.5",
                "strict checkpoint tensor-name and tensor-shape signature",
            ],
            "encoder_widths": list(_TGNET_ENCODER_WIDTHS),
            "neighborhood_sizes": [8, 16, 16, 16, 16],
            "head_interpolation_neighbors": 1,
            "attention_relation": "key-minus-query",
            "position_relation": "neighbor-minus-query",
            "point_group_classes": 10,
            "mask_refinement_classes": 2,
            "offset_dimensions": 3,
            "validated_tensors": validated,
        },
    )


def analyze_model_family_state_dict(
    state_dict: Mapping[str, Any],
) -> CheckpointAnalysis:
    """Identify only checkpoint families with a validated tensor signature."""
    keys = set(state_dict)
    tgnet_like = any(
        key.startswith(("first_ins_cent_model.", "second_ins_cent_model."))
        for key in keys
    )
    if tgnet_like:
        return analyze_checkpoint_state_dict(state_dict)

    meshsegnet_like = any(
        key.startswith(("mlp1_", "fstn.", "glm2_", "mlp3_", "output_conv."))
        for key in keys
    )
    if not meshsegnet_like:
        raise CheckpointCompatibilityError(
            "Checkpoint does not match a supported TGNet or MeshSegNet "
            "architecture signature."
        )

    validated: list[dict[str, Any]] = []
    for key, expected_shape in _MESHSEGNET_ANCHOR_SHAPES.items():
        if key not in state_dict:
            raise CheckpointCompatibilityError(
                f"MeshSegNet checkpoint is missing required tensor: {key}"
            )
        actual_shape = _shape(state_dict[key])
        if actual_shape != expected_shape:
            raise CheckpointCompatibilityError(
                f"MeshSegNet tensor {key} has shape {actual_shape}; "
                f"expected shape {expected_shape}."
            )
        validated.append({"key": key, "shape": list(actual_shape)})
    return CheckpointAnalysis(
        model_family="meshsegnet",
        input_features=15,
        class_count=17,
        architecture_validation={
            "passed": True,
            "basis": [
                "strict MeshSegNet tensor-name and tensor-shape signature",
                "full state-dict is loaded strictly by the selected runner",
            ],
            "validated_tensors": validated,
        },
    )


def load_checkpoint_analysis(path: Path) -> tuple[Any, Mapping[str, Any], CheckpointAnalysis]:
    import torch

    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    state_dict = unwrap_state_dict(checkpoint)
    return checkpoint, state_dict, analyze_checkpoint_state_dict(state_dict)


def load_model_family_analysis(
    path: Path,
) -> tuple[Any, Mapping[str, Any], CheckpointAnalysis]:
    import torch

    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    state_dict = unwrap_state_dict(checkpoint)
    return checkpoint, state_dict, analyze_model_family_state_dict(state_dict)


def tgnet_model_metadata(
    *,
    checkpoint_sha256: str,
    architecture_validation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "model_family": "tgnet",
        "source": "user-provided",
        "license": "not-verified",
        "bundled_by_app": False,
        "sha256": checkpoint_sha256,
        "architecture_validation": dict(architecture_validation),
    }


def validate_fdi_mapping(jaw: str) -> list[int]:
    if jaw == "upper":
        return list(range(11, 19)) + list(range(21, 29))
    if jaw == "lower":
        return list(range(31, 39)) + list(range(41, 49))
    raise ValueError("jaw must be 'upper' or 'lower' for TGNet FDI mapping.")
