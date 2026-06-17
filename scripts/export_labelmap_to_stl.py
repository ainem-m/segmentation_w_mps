#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from totalsegmentator_wrapper_mac.surface_preview import (
    SMOOTH_PRESETS,
    export_labelmap_surfaces,
    smoothing_config_from_options,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a multilabel NIfTI labelmap to STL meshes.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--min-voxels", type=int, default=1)
    parser.add_argument("--combined", action="store_true", help="Also export useful combined meshes.")
    parser.add_argument(
        "--smooth-preset",
        choices=tuple(SMOOTH_PRESETS),
        default="none",
        help="Optional mesh-level Taubin smoothing preset. Default keeps raw behavior.",
    )
    parser.add_argument("--smooth-iterations", type=int, default=None)
    parser.add_argument("--smooth-lambda", dest="smooth_lambda", type=float, default=None)
    parser.add_argument("--smooth-mu", type=float, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    smoothing = smoothing_config_from_options(
        preset=args.smooth_preset,
        iterations=args.smooth_iterations,
        lambda_value=args.smooth_lambda,
        mu=args.smooth_mu,
    )
    summary = export_labelmap_surfaces(
        input_path=args.input,
        output_dir=args.output_dir,
        min_voxels=args.min_voxels,
        combined=args.combined,
        smoothing=smoothing,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
