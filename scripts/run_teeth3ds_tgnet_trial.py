#!/usr/bin/env python3
"""Run the Teeth3DS challenge-winning TGNet checkpoint as a research trial.

This repository-owned wrapper prepares an IOS mesh, invokes an explicitly
provided upstream TGNet checkout, validates strict MPS execution evidence, and
exports colored PLY/NPZ/per-tooth STL artifacts.  The upstream code and weights
have no explicit license grant and are therefore not vendored here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
import trimesh

from totalsegmentator_wrapper_mac.ios_meshsegnet import (
    PALETTE,
    labels_to_vertices,
    render_dense_preview,
    retain_largest_component_per_tooth,
)


SOURCE_URL = "https://github.com/limhoyeon/ToothGroupNetwork"
SOURCE_COMMIT = "f184332d358af44dd5f96585020a6aa1d6aeb1ca"
WEIGHTS_URL = (
    "https://drive.google.com/drive/folders/"
    "15oP0CZM_O_-Bir18VbSM8wRUEzoyLXby"
)
WEIGHTS_ARCHIVE_SHA256 = (
    "6586c0a6f6e0ab1a1ee68a07a21ee70b2fb36b4185189b61ff626bd83ad9df18"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fdi_to_class(labels: np.ndarray) -> np.ndarray:
    labels = np.asarray(labels, dtype=np.int16)
    classes = np.zeros(labels.shape, dtype=np.int16)
    upper_right = (11 <= labels) & (labels <= 18)
    upper_left = (21 <= labels) & (labels <= 28)
    classes[upper_right] = labels[upper_right] - 10
    classes[upper_left] = labels[upper_left] - 12
    return classes


def write_plain_obj(mesh: trimesh.Trimesh, path: Path) -> None:
    """Write only v/f records, preserving the PLY vertex order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        stream.write("# upper\n")
        np.savetxt(stream, np.asarray(mesh.vertices), fmt="v %.9f %.9f %.9f")
        np.savetxt(stream, np.asarray(mesh.faces) + 1, fmt="f %d %d %d")


def parse_mps_proof(stdout: str) -> dict[str, Any]:
    prefix = "TGNET_MPS_PROOF="
    matches = [line[len(prefix) :] for line in stdout.splitlines() if line.startswith(prefix)]
    if len(matches) != 1:
        raise RuntimeError("TGNet child did not emit exactly one MPS proof record")
    proof = json.loads(matches[0])
    if proof.get("device") != "mps:0" or proof.get("mps_available") is not True:
        raise RuntimeError(f"Invalid MPS proof: {proof}")
    return proof


def local_mps_proof() -> dict[str, Any]:
    device = torch.device("mps")
    layer = torch.nn.Conv1d(3, 8, 1).to(device)
    output = layer(torch.randn((1, 3, 1024), device=device))
    torch.mps.synchronize()
    return {
        "device": str(output.device),
        "dtype": str(output.dtype),
        "shape": list(output.shape),
        "mps_available": bool(torch.backends.mps.is_available()),
        "mps_fallback_env": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"),
        "current_allocated_memory": int(torch.mps.current_allocated_memory()),
        "driver_allocated_memory": int(torch.mps.driver_allocated_memory()),
    }


def export_results(
    source: trimesh.Trimesh,
    vertex_classes: np.ndarray,
    output_dir: Path,
) -> dict[str, Any]:
    faces = np.asarray(source.faces, dtype=np.int64)
    corner_labels = vertex_classes[faces]
    face_classes = np.empty(len(faces), dtype=np.int16)
    for index, row in enumerate(corner_labels):
        face_classes[index] = int(np.bincount(row, minlength=17).argmax())
    face_classes, cleanup = retain_largest_component_per_tooth(source, face_classes)
    vertex_classes = labels_to_vertices(faces, face_classes, len(source.vertices))

    colored = source.copy()
    colored.visual = trimesh.visual.ColorVisuals(
        mesh=colored,
        vertex_colors=PALETTE[vertex_classes],
    )
    colored_path = output_dir / "ios_upper_tgnet_colored.ply"
    colored.export(colored_path)
    labels_path = output_dir / "ios_upper_tgnet_labels.npz"
    np.savez_compressed(
        labels_path,
        vertex_labels=vertex_classes,
        face_labels=face_classes,
    )

    teeth_dir = output_dir / "teeth_stl"
    teeth_dir.mkdir(exist_ok=True)
    teeth: list[dict[str, Any]] = []
    for class_id in range(1, 17):
        mask = face_classes == class_id
        if not mask.any():
            continue
        fdi = class_id + 10 if class_id <= 8 else class_id + 12
        submesh = source.submesh([mask], append=True, repair=False)
        stl_path = teeth_dir / f"tooth_{fdi}.stl"
        submesh.export(stl_path)
        teeth.append(
            {
                "class_id": class_id,
                "fdi": fdi,
                "face_count": int(mask.sum()),
                "surface_area": float(np.asarray(source.area_faces)[mask].sum()),
                "stl": str(stl_path.resolve()),
            }
        )

    preview_path = output_dir / "ios_upper_tgnet_dense_preview.png"
    render_dense_preview(
        np.asarray(source.vertices),
        vertex_classes,
        preview_path,
        figure_title="Teeth3DS / TGNet — ios_upper.ply (research preview)",
    )
    return {
        "colored_ply": str(colored_path.resolve()),
        "labels_npz": str(labels_path.resolve()),
        "dense_preview_png": str(preview_path.resolve()),
        "teeth_dir": str(teeth_dir.resolve()),
        "teeth": teeth,
        "component_cleanup": cleanup,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise RuntimeError("PYTORCH_ENABLE_MPS_FALLBACK=1 is forbidden")
    if not torch.backends.mps.is_built() or not torch.backends.mps.is_available():
        raise RuntimeError("Apple MPS is required and unavailable")

    source = trimesh.load_mesh(args.input, process=False)
    if not isinstance(source, trimesh.Trimesh):
        raise TypeError("Input did not resolve to one triangle mesh")
    oriented = source.copy()
    oriented.apply_transform(
        np.asarray(
            [
                [-1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, -1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
    )

    output_dir = args.output_dir.resolve()
    work_input = output_dir / "tgnet_input" / "IOSUPPER" / "IOSUPPER_upper.obj"
    raw_output = output_dir / "raw_prediction"
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_output.mkdir(exist_ok=True)
    if not args.reuse_raw_prediction or not work_input.exists():
        write_plain_obj(oriented, work_input)

    env = os.environ.copy()
    env.pop("PYTORCH_ENABLE_MPS_FALLBACK", None)
    env["MPLCONFIGDIR"] = "/tmp/tgnet-matplotlib"
    prediction_path = raw_output / "IOSUPPER_upper.json"
    if args.reuse_raw_prediction:
        if not prediction_path.exists():
            raise FileNotFoundError(
                "--reuse-raw-prediction was set but the complete JSON is absent"
            )
        inference_seconds = None
        proof = local_mps_proof()
        proof["evidence_scope"] = (
            "post-run MPS smoke; prediction was produced by the strict MPS child "
            "before its parent wrapper was interrupted"
        )
    else:
        command = [
            sys.executable,
            "inference_final.py",
            "--input_path",
            str(work_input.parent.parent),
            "--save_path",
            str(raw_output),
        ]
        inference_started = time.perf_counter()
        completed = subprocess.run(
            command,
            cwd=args.tgnet_source,
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )
        inference_seconds = time.perf_counter() - inference_started
        (output_dir / "tgnet_stdout.log").write_text(
            completed.stdout, encoding="utf-8"
        )
        (output_dir / "tgnet_stderr.log").write_text(
            completed.stderr, encoding="utf-8"
        )
        proof = parse_mps_proof(completed.stdout)

    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    if len(prediction["labels"]) != len(source.vertices):
        raise RuntimeError("TGNet output vertex count does not match source mesh")
    vertex_classes = fdi_to_class(np.asarray(prediction["labels"]))
    outputs = export_results(source, vertex_classes, output_dir)

    summary = {
        "schema": "teeth3ds_tgnet_mps_research_result.v1",
        "research_only": True,
        "input": {
            "path": str(args.input.resolve()),
            "vertices": int(len(source.vertices)),
            "faces": int(len(source.faces)),
            "orientation": "rotate_y_180",
        },
        "model": {
            "name": "ToothGroupNetwork FinalModel",
            "training_dataset": "Teeth3DS / 3DTeethSeg'22",
            "source": SOURCE_URL,
            "source_commit": SOURCE_COMMIT,
            "weights_source": WEIGHTS_URL,
            "weights_archive": str(args.weights_archive.resolve()),
            "weights_archive_sha256": sha256(args.weights_archive),
            "expected_weights_archive_sha256": WEIGHTS_ARCHIVE_SHA256,
            "license": "NO LICENSE FILE / permission required",
        },
        "runtime": {
            "torch": torch.__version__,
            "device": "mps:0",
            "cpu_geometry_ops": ["farthest-point sampling", "k-nearest-neighbour lookup"],
            "mps_proof": proof,
            "inference_seconds": inference_seconds,
            "inference_timing_status": (
                "unavailable_after_parent_interrupt"
                if args.reuse_raw_prediction
                else "measured"
            ),
            "total_seconds": time.perf_counter() - started,
        },
        "outputs": outputs,
        "limitations": [
            "No case-specific ground truth was available.",
            "The upstream repository and checkpoint distribution contain no explicit license grant.",
            "CUDA-only geometric point operators were replaced for this MPS trial; neural-network tensors and layers ran on MPS.",
        ],
    }
    if summary["model"]["weights_archive_sha256"] != WEIGHTS_ARCHIVE_SHA256:
        raise RuntimeError("Checkpoint archive hash mismatch")
    (output_dir / "result_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--tgnet-source", required=True, type=Path)
    parser.add_argument("--weights-archive", required=True, type=Path)
    parser.add_argument("--reuse-raw-prediction", action="store_true")
    return parser.parse_args()


def main() -> int:
    result = run(parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
