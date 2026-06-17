#!/usr/bin/env python3
"""Smoke test for PyTorch MPS ConvTranspose3D FP32 support.

This is the first gate for TotalSegmentator Wrapper for Mac.
Exit code 0 means the core operator path is usable.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=None, help="Optional JSON result path")
    args = parser.parse_args()

    result = {
        "status": "unknown",
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "torch_version": None,
        "mps_built": False,
        "mps_available": False,
        "convtranspose3d_fp32": "not_run",
        "elapsed_seconds": None,
        "error": None,
    }

    try:
        import torch
        import torch.nn as nn

        result["torch_version"] = torch.__version__
        result["mps_built"] = bool(torch.backends.mps.is_built())
        result["mps_available"] = bool(torch.backends.mps.is_available())

        if not result["mps_available"]:
            raise RuntimeError("torch.backends.mps.is_available() is False")

        device = torch.device("mps")
        model = nn.ConvTranspose3d(16, 8, kernel_size=2, stride=2).to(
            device=device, dtype=torch.float32
        )
        x = torch.randn(1, 16, 16, 32, 32, device=device, dtype=torch.float32)

        start = time.perf_counter()
        with torch.inference_mode():
            y = model(x)
        elapsed = time.perf_counter() - start

        # Force synchronization-like behavior by reading shape and moving tiny scalar to CPU.
        y_sum = float(y.sum().detach().cpu())

        result["convtranspose3d_fp32"] = "pass"
        result["output_shape"] = list(y.shape)
        result["output_device"] = str(y.device)
        result["output_dtype"] = str(y.dtype)
        result["output_sum"] = y_sum
        result["elapsed_seconds"] = elapsed
        result["status"] = "pass"

    except Exception as exc:  # noqa: BLE001
        result["status"] = "fail"
        result["error"] = repr(exc)

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
