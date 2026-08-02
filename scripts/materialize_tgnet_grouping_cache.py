#!/usr/bin/env python3
"""Convert fixed offset-diagnostic outputs into grouping-only GT caches."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from scripts.diagnose_tgnet_offset_scale import (
    _cache_path,
    _case_paths,
    _normalization,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--offset-cache-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--normalization", required=True)
    parser.add_argument("--scale", type=float, required=True)
    parser.add_argument("--offset-multiplier", type=float, default=1.0)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        mesh_path, golden_path = _case_paths(args.cases_root, case)
        source_cache = _cache_path(
            args.offset_cache_dir,
            args.normalization,
            args.scale,
            str(case["key"]),
        )
        if not source_cache.is_file():
            raise FileNotFoundError(f"missing offset cache: {source_cache}")
        mesh = trimesh.load(mesh_path, process=False, force="mesh")
        points = np.asarray(mesh.vertices, dtype=np.float32)
        normalized, _ = _normalization(
            points, args.normalization, args.scale
        )
        golden_document = json.loads(
            golden_path.read_text(encoding="utf-8")
        )
        with np.load(source_cache, allow_pickle=False) as cached:
            if (
                str(cached["formula"]) != args.normalization
                or float(cached["scale"]) != args.scale
            ):
                raise ValueError(f"incompatible offset cache: {source_cache}")
            indices = np.asarray(cached["sample_indices"], dtype=np.int64)
            predicted_offsets = np.asarray(
                cached["predicted_offsets"], dtype=np.float32
            )
            tooth_mask = np.asarray(cached["tooth_mask"], dtype=bool)
            predicted_class = np.asarray(
                cached["predicted_class"], dtype=np.int16
            )
            model_sha256 = str(cached["model_sha256"])
        expected_shape = (len(indices),)
        if (
            predicted_offsets.shape != (len(indices), 3)
            or tooth_mask.shape != expected_shape
            or predicted_class.shape != expected_shape
        ):
            raise ValueError(f"incompatible arrays in {source_cache}")
        output_path = args.output_dir / f"{case['key']}.npz"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.npz")
        np.savez_compressed(
            temporary,
            shifted=(
                normalized[indices]
                + predicted_offsets * args.offset_multiplier
            ).astype(np.float32),
            tooth_mask=tooth_mask,
            golden_instances=np.asarray(
                golden_document["instances"], dtype=np.int16
            )[indices],
            golden_fdi=np.asarray(
                golden_document["labels"], dtype=np.int16
            )[indices],
            predicted_class=predicted_class,
            sample_indices=indices,
        )
        temporary.replace(output_path)
        records.append(
            {
                "key": case["key"],
                "role": case["role"],
                "source_offset_cache": str(source_cache.resolve()),
                "source_offset_cache_sha256": _sha256(source_cache),
                "grouping_cache": str(output_path.resolve()),
                "grouping_cache_sha256": _sha256(output_path),
                "model_sha256": model_sha256,
            }
        )
        print(case["key"], output_path, flush=True)
    document = {
        "schema": "tgnet_grouping_cache_materialization.v1",
        "evaluation_only": True,
        "ground_truth_used_by_inference": False,
        "normalization": args.normalization,
        "scale": args.scale,
        "offset_multiplier": args.offset_multiplier,
        "source_manifest": str(args.manifest.resolve()),
        "source_manifest_sha256": _sha256(args.manifest),
        "records": records,
    }
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
