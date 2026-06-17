from __future__ import annotations

import platform
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class DeviceCheck:
    status: str
    requested_device: str
    actual_device: str | None
    fallback_reason: str | None
    python: str
    platform: str
    machine: str
    torch_version: str | None
    mps_built: bool | None
    mps_available: bool | None
    convtranspose3d_fp32: str
    elapsed_seconds: float | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def smoke_test_mps_convtranspose3d() -> DeviceCheck:
    result = {
        "status": "fail",
        "requested_device": "mps",
        "actual_device": None,
        "fallback_reason": None,
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "torch_version": None,
        "mps_built": None,
        "mps_available": None,
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
        result["elapsed_seconds"] = time.perf_counter() - start
        _ = float(y.sum().detach().cpu())

        result["status"] = "pass"
        result["actual_device"] = "mps"
        result["convtranspose3d_fp32"] = "pass"
    except Exception as exc:  # noqa: BLE001
        result["error"] = repr(exc)
    return DeviceCheck(**result)


def resolve_device(requested_device: str, skip_device_check: bool = False) -> DeviceCheck:
    if requested_device == "cpu":
        return DeviceCheck(
            status="pass",
            requested_device="cpu",
            actual_device="cpu",
            fallback_reason=None,
            python=sys.version,
            platform=platform.platform(),
            machine=platform.machine(),
            torch_version=None,
            mps_built=None,
            mps_available=None,
            convtranspose3d_fp32="not_run",
            elapsed_seconds=None,
            error=None,
        )

    if requested_device == "mps" and skip_device_check:
        return DeviceCheck(
            status="pass",
            requested_device="mps",
            actual_device="mps",
            fallback_reason="device check skipped by caller",
            python=sys.version,
            platform=platform.platform(),
            machine=platform.machine(),
            torch_version=None,
            mps_built=None,
            mps_available=None,
            convtranspose3d_fp32="skipped",
            elapsed_seconds=None,
            error=None,
        )

    mps_check = smoke_test_mps_convtranspose3d()
    if requested_device == "mps":
        return mps_check

    if requested_device == "auto":
        if mps_check.status == "pass":
            return DeviceCheck(
                **{**mps_check.to_dict(), "requested_device": "auto", "fallback_reason": None}
            )
        return DeviceCheck(
            **{
                **mps_check.to_dict(),
                "requested_device": "auto",
                "actual_device": "cpu",
                "fallback_reason": f"MPS smoke test failed: {mps_check.error}",
                "status": "pass",
            }
        )

    raise ValueError(f"Unsupported device: {requested_device}")
