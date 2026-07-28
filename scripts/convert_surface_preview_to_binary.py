#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

if __package__:
    from scripts.build_model_comparison_viewer import read_payload
else:
    from build_model_comparison_viewer import read_payload
from totalsegmentator_wrapper_mac.surface_preview import _write_offline_viewer


def convert_preview(
    *,
    source: Path,
    output: Path,
    excluded_meshes: set[str] | None = None,
) -> list[Path]:
    payload = read_payload(source)
    excluded_meshes = excluded_meshes or set()
    meshes = [
        mesh
        for mesh in payload.get("meshes", [])
        if str(mesh.get("name")) not in excluded_meshes
    ]
    if not meshes:
        raise ValueError("No preview meshes remain after filtering")
    summary = {
        "label_count": int(payload.get("labelCount", 0)),
        "smoothing": payload.get("smoothing", {"preset": "none"}),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle_path, geometry_paths = _write_offline_viewer(
        output,
        summary=summary,
        preview_meshes=meshes,
    )
    return [output, bundle_path, *geometry_paths]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert an existing surface preview to binary geometry chunks.",
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--exclude-mesh",
        action="append",
        default=[],
        help="Mesh name to omit; may be supplied more than once.",
    )
    args = parser.parse_args()
    outputs = convert_preview(
        source=args.source.expanduser().resolve(),
        output=args.output.expanduser().resolve(),
        excluded_meshes=set(args.exclude_mesh),
    )
    for path in outputs:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"{path}\t{path.stat().st_size}\t{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
