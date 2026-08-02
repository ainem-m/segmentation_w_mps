"""Dispatch an intra-oral mesh checkpoint after strict family inspection."""

from __future__ import annotations

import argparse
import hashlib
import tempfile
from pathlib import Path

from .ios_checkpoint_family import (
    CheckpointCompatibilityError,
    load_model_family_analysis,
)


def detect_model_family(path: Path) -> str:
    if path.suffix.lower() == ".zip":
        from .ios_tgnet_final import materialize_checkpoint_archive

        with tempfile.TemporaryDirectory(prefix="tgnet-checkpoint-inspect-") as tmp:
            materialize_checkpoint_archive(path, Path(tmp))
        return "tgnet-final"
    if path.is_dir():
        from .ios_tgnet_final import validate_checkpoint_directory_layout

        validate_checkpoint_directory_layout(path)
        return "tgnet-final"
    try:
        _, _, analysis = load_model_family_analysis(path)
    except CheckpointCompatibilityError as exc:
        raise ValueError(
            f"Selected checkpoint is not a strictly compatible TGNet or "
            f"MeshSegNet model: {exc}"
        ) from exc
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ValueError(f"Could not safely inspect selected checkpoint: {exc}") from exc
    return analysis.model_family


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--jaw", choices=("upper", "lower"), required=True)
    parser.add_argument("--preprocess", default="official")
    parser.add_argument("--orientation", default="rotate_y_180")
    parser.add_argument("--device", choices=("mps", "cpu"), default="mps")
    parser.add_argument("--allow-custom-model", action="store_true")
    return parser.parse_args(argv)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def orientation_for_family(
    family: str,
    jaw: str,
    requested_orientation: str,
) -> str:
    if jaw == "lower" and family in {"tgnet", "tgnet-final"}:
        return "none"
    return requested_orientation


def _dispatch(
    args: argparse.Namespace,
    model: Path,
    *,
    source_archive: Path | None = None,
) -> int:
    family = "tgnet-final" if source_archive is not None else detect_model_family(model)
    orientation = orientation_for_family(family, args.jaw, args.orientation)
    common = [
        "--input",
        str(args.input),
        "--model",
        str(model),
        "--output-dir",
        str(args.output_dir),
        "--jaw",
        args.jaw,
        "--orientation",
        orientation,
        "--device",
        args.device,
    ]
    if family == "tgnet":
        from .ios_tgnet import main as tgnet_main

        return tgnet_main(common)
    if family == "tgnet-final":
        from .ios_tgnet_final import main as tgnet_final_main

        if source_archive is not None:
            common += [
                "--source-archive-name",
                source_archive.name,
                "--source-archive-sha256",
                _sha256(source_archive),
            ]
        return tgnet_final_main(common)

    from .ios_meshsegnet import main as meshsegnet_main

    mesh_args = common + ["--preprocess", args.preprocess]
    if args.allow_custom_model:
        mesh_args.append("--allow-custom-model")
    return meshsegnet_main(mesh_args)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.model.suffix.lower() == ".zip":
        from .ios_tgnet_final import materialize_checkpoint_archive

        with tempfile.TemporaryDirectory(prefix="tgnet-checkpoints-") as tmp:
            extracted = Path(tmp)
            materialize_checkpoint_archive(args.model, extracted)
            return _dispatch(args, extracted, source_archive=args.model)
    return _dispatch(args, args.model)


if __name__ == "__main__":
    raise SystemExit(main())
