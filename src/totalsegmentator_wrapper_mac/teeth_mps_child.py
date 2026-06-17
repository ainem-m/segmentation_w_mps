from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def package_version(*names: str) -> str:
    for name in names:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return "unknown"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run experimental TotalSegmentator teeth on MPS with scoped patching."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--benchmark-json", required=True, type=Path)
    parser.add_argument("--ml", action="store_true", default=True)
    parser.add_argument("--no-ml", dest="ml", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--force-split", action="store_true")
    parser.add_argument("--nr-thr-resamp", type=int, default=1)
    parser.add_argument("--nr-thr-saving", type=int, default=1)
    parser.add_argument("--require-totalseg-version", default="2.14.0")
    parser.add_argument("--allow-version-drift", action="store_true")
    return parser.parse_args(argv)


def patch_total_segmentator_device_converter(ts_api: Any) -> dict[str, Any]:
    original = ts_api.convert_device_to_string
    original_result = None
    original_error = None
    try:
        original_result = original("mps")
    except Exception as exc:  # noqa: BLE001
        original_error = repr(exc)

    def patched_convert_device_to_string(device: Any) -> str:
        if isinstance(device, str):
            if device in {"cpu", "gpu", "mps"}:
                return device
            if re.fullmatch(r"gpu:\d+", device):
                return device
            if device == "cuda":
                return "gpu"
            cuda_match = re.fullmatch(r"cuda:(\d+)", device)
            if cuda_match:
                return f"gpu:{cuda_match.group(1)}"
            raise ValueError(f"Unsupported TotalSegmentator device string: {device!r}")

        if hasattr(device, "type"):
            device_type = device.type
            if device_type == "cuda":
                index = getattr(device, "index", None)
                return "gpu" if index is None else f"gpu:{index}"
            if device_type in {"cpu", "mps"}:
                return device_type
            return str(device_type)

        raise TypeError(f"Unsupported device object: {device!r}")

    patch_applied = original_result != "mps"
    if patch_applied:
        ts_api.convert_device_to_string = patched_convert_device_to_string

    return {
        "patch_applied": patch_applied,
        "original_convert_device_to_string_mps_result": original_result,
        "original_convert_device_to_string_mps_error": original_error,
        "post_patch_string_mps": ts_api.convert_device_to_string("mps"),
    }


def run_mps_gate(torch: Any) -> dict[str, Any]:
    if not torch.backends.mps.is_available():
        raise RuntimeError("torch.backends.mps.is_available() is false")

    torch.set_default_dtype(torch.float32)
    layer = torch.nn.ConvTranspose3d(
        in_channels=2,
        out_channels=2,
        kernel_size=3,
        stride=2,
        padding=1,
        output_padding=1,
    ).to(device="mps", dtype=torch.float32)
    x = torch.randn((1, 2, 8, 8, 8), device="mps", dtype=torch.float32)
    with torch.no_grad():
        y = layer(x)
        if hasattr(torch, "mps") and hasattr(torch.mps, "synchronize"):
            torch.mps.synchronize()
    if y.dtype != torch.float32:
        raise RuntimeError(f"MPS gate produced unexpected dtype: {y.dtype}")
    return {
        "mps_available": True,
        "convtranspose3d_fp32": "passed",
        "output_shape": [int(value) for value in y.shape],
        "output_dtype": str(y.dtype),
    }


def read_nifti_meta(path: Path) -> dict[str, Any]:
    import nibabel as nib

    image = nib.load(str(path))
    return {
        "path": str(path.resolve()),
        "shape": [int(value) for value in image.shape[:3]],
        "spacing": [float(value) for value in image.header.get_zooms()[:3]],
        "dtype": str(image.get_data_dtype()),
        "size_bytes": int(path.stat().st_size),
    }


def prepare_output_path(output_path: Path, ml: bool) -> Path:
    if ml:
        if output_path.name.endswith((".nii", ".nii.gz")):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            return output_path
        output_path.mkdir(parents=True, exist_ok=True)
        return output_path / "teeth_multilabel.nii.gz"
    output_path.mkdir(parents=True, exist_ok=True)
    return output_path


def validate_multilabel_output(path: Path) -> dict[str, Any]:
    import nibabel as nib
    import numpy as np
    from totalsegmentator.map_to_binary import class_map

    image = nib.load(str(path))
    data = np.asanyarray(image.dataobj)
    labels, counts = np.unique(data, return_counts=True)
    label_to_name = {int(label): str(name) for label, name in class_map.get("teeth", {}).items()}
    non_empty = []
    for label, count in zip(labels, counts, strict=True):
        label_int = int(label)
        if label_int == 0:
            continue
        non_empty.append(
            {
                "label": label_int,
                "name": label_to_name.get(label_int, f"label_{label_int}"),
                "voxels": int(count),
            }
        )
    return {
        "path": str(path.resolve()),
        "exists": path.exists(),
        "shape": [int(value) for value in image.shape[:3]],
        "spacing": [float(value) for value in image.header.get_zooms()[:3]],
        "non_empty_label_count": len(non_empty),
        "non_empty_labels": non_empty,
    }


def validate_binary_output_dir(path: Path) -> dict[str, Any]:
    import nibabel as nib
    import numpy as np

    files = sorted(path.glob("*.nii.gz")) + sorted(path.glob("*.nii"))
    non_empty = []
    for file_path in files:
        image = nib.load(str(file_path))
        data = np.asanyarray(image.dataobj)
        voxels = int(np.count_nonzero(data))
        if voxels > 0:
            non_empty.append({"file": file_path.name, "voxels": voxels})
    return {
        "path": str(path.resolve()),
        "file_count": len(files),
        "non_empty_file_count": len(non_empty),
        "non_empty_files": non_empty,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    benchmark: dict[str, Any] = {
        "status": "started",
        "task": "teeth",
        "device_requested": "mps",
        "precision_policy": "fp32_only_no_autocast_requested",
        "started_at_utc": utc_now(),
        "argv": sys.argv if argv is None else argv,
        "platform": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "input": None,
        "output": None,
        "timing": {},
        "patch": {},
        "mps_gate": {},
        "error": None,
    }
    started = time.perf_counter()
    try:
        if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
            raise RuntimeError("PYTORCH_ENABLE_MPS_FALLBACK=1 is not allowed for the MPS proof path")
        if not args.input.exists():
            raise FileNotFoundError(args.input)

        benchmark["input"] = read_nifti_meta(args.input)

        import torch

        benchmark["torch"] = {
            "version": torch.__version__,
            "mps_built": bool(torch.backends.mps.is_built()),
            "mps_available": bool(torch.backends.mps.is_available()),
            "mps_fallback_env": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"),
        }
        benchmark["mps_gate"] = run_mps_gate(torch)

        import totalsegmentator.python_api as ts_api

        totalseg_version = package_version("TotalSegmentator", "totalsegmentator")
        benchmark["totalsegmentator"] = {"version": totalseg_version}
        if (
            args.require_totalseg_version
            and totalseg_version != args.require_totalseg_version
            and not args.allow_version_drift
        ):
            raise RuntimeError(
                f"TotalSegmentator version {totalseg_version!r} does not match "
                f"required version {args.require_totalseg_version!r}"
            )

        benchmark["patch"] = patch_total_segmentator_device_converter(ts_api)
        benchmark["patch"]["post_patch_torch_device_mps"] = ts_api.convert_device_to_string(
            torch.device("mps")
        )

        output_arg = prepare_output_path(args.output, args.ml)
        benchmark["output"] = {
            "requested": str(args.output.resolve()),
            "totalsegmentator_output_arg": str(output_arg.resolve()),
            "ml": bool(args.ml),
        }

        if args.dry_run:
            benchmark["status"] = "success"
            benchmark["dry_run"] = True
            return 0

        if hasattr(torch, "mps") and hasattr(torch.mps, "synchronize"):
            torch.mps.synchronize()
        inference_started = time.perf_counter()

        seg_img = ts_api.totalsegmentator(
            input=args.input,
            output=output_arg,
            task="teeth",
            device="mps",
            ml=args.ml,
            nr_thr_resamp=args.nr_thr_resamp,
            nr_thr_saving=args.nr_thr_saving,
            force_split=args.force_split,
            preview=args.preview,
            quiet=args.quiet,
            verbose=args.verbose,
            statistics=False,
            radiomics=False,
            no_derived_masks=True,
        )

        if hasattr(torch, "mps") and hasattr(torch.mps, "synchronize"):
            torch.mps.synchronize()
        benchmark["timing"]["inference_elapsed_sec"] = round(
            time.perf_counter() - inference_started,
            3,
        )

        if args.ml:
            if not output_arg.exists():
                import nibabel as nib

                nib.save(seg_img, str(output_arg))
            benchmark["validation"] = validate_multilabel_output(output_arg)
            if benchmark["validation"]["non_empty_label_count"] == 0:
                raise RuntimeError("teeth output completed but contained no non-zero labels")
        else:
            benchmark["validation"] = validate_binary_output_dir(output_arg)
            if benchmark["validation"]["non_empty_file_count"] == 0:
                raise RuntimeError("teeth output completed but contained no non-empty masks")

        if hasattr(torch, "mps"):
            benchmark["mps_memory"] = {
                "current_allocated_memory": int(torch.mps.current_allocated_memory())
                if hasattr(torch.mps, "current_allocated_memory")
                else None,
                "driver_allocated_memory": int(torch.mps.driver_allocated_memory())
                if hasattr(torch.mps, "driver_allocated_memory")
                else None,
            }
        benchmark["status"] = "success"
        return 0
    except BaseException as exc:  # noqa: BLE001
        benchmark["status"] = "failed"
        benchmark["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        print(benchmark["error"]["traceback"], file=sys.stderr)
        return 1
    finally:
        benchmark["finished_at_utc"] = utc_now()
        benchmark["timing"]["total_elapsed_sec"] = round(time.perf_counter() - started, 3)
        atomic_write_json(args.benchmark_json, benchmark)


if __name__ == "__main__":
    raise SystemExit(main())
