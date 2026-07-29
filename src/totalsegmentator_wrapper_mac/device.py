from __future__ import annotations

import platform
import subprocess
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
    error_code: str | None = None
    cuda_build: str | None = None
    cuda_available: bool | None = None
    cuda_device_count: int | None = None
    device_index: int | None = None
    device_name: str | None = None
    compute_capability: str | None = None
    total_memory_bytes: int | None = None
    driver_version: str | None = None
    peak_allocated_bytes: int | None = None
    tensor_smoke: dict[str, Any] | None = None

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


def smoke_test_cuda(index: int = 0) -> DeviceCheck:
    requested_device = f"cuda:{index}"
    result: dict[str, Any] = {
        "status": "fail",
        "requested_device": requested_device,
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
        "error_code": None,
        "cuda_build": None,
        "cuda_available": None,
        "cuda_device_count": None,
        "device_index": index,
        "device_name": None,
        "compute_capability": None,
        "total_memory_bytes": None,
        "driver_version": _nvidia_driver_version(),
        "peak_allocated_bytes": None,
        "tensor_smoke": {
            "tensor_creation": "not_run",
            "conv3d": "not_run",
            "normalization": "not_run",
            "activation": "not_run",
            "convtranspose3d": "not_run",
            "synchronize": "not_run",
            "finite_output": "not_run",
        },
    }
    try:
        import torch
        import torch.nn as nn

        result["torch_version"] = torch.__version__
        result["cuda_build"] = torch.version.cuda
        result["cuda_available"] = bool(torch.cuda.is_available())
        result["cuda_device_count"] = int(torch.cuda.device_count())
        if not result["cuda_available"]:
            result["error_code"] = "cuda_unavailable"
            raise RuntimeError("torch.cuda.is_available() is False")
        if index >= result["cuda_device_count"]:
            result["error_code"] = "cuda_device_index_unavailable"
            raise RuntimeError(
                f"CUDA device index {index} is unavailable; "
                f"visible device count is {result['cuda_device_count']}"
            )

        device = torch.device(requested_device)
        properties = torch.cuda.get_device_properties(device)
        result["device_name"] = properties.name
        result["compute_capability"] = f"{properties.major}.{properties.minor}"
        result["total_memory_bytes"] = int(properties.total_memory)
        torch.cuda.set_device(device)
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats(device)

        started = time.perf_counter()
        x = torch.randn(1, 4, 8, 16, 16, device=device, dtype=torch.float32)
        result["tensor_smoke"]["tensor_creation"] = "pass"
        conv = nn.Conv3d(4, 8, kernel_size=3, padding=1).to(
            device=device,
            dtype=torch.float32,
        )
        norm = nn.InstanceNorm3d(8, affine=True).to(
            device=device,
            dtype=torch.float32,
        )
        activation = nn.ReLU()
        transpose = nn.ConvTranspose3d(8, 4, kernel_size=2, stride=2).to(
            device=device,
            dtype=torch.float32,
        )
        with torch.inference_mode():
            y = conv(x)
            result["tensor_smoke"]["conv3d"] = "pass"
            y = norm(y)
            result["tensor_smoke"]["normalization"] = "pass"
            y = activation(y)
            result["tensor_smoke"]["activation"] = "pass"
            y = transpose(y)
            result["tensor_smoke"]["convtranspose3d"] = "pass"
        torch.cuda.synchronize(device)
        result["tensor_smoke"]["synchronize"] = "pass"
        finite_output = bool(torch.isfinite(y).all().item())
        result["tensor_smoke"]["finite_output"] = (
            "pass" if finite_output else "fail"
        )
        if not finite_output:
            result["error_code"] = "cuda_nonfinite_output"
            raise RuntimeError("CUDA tensor smoke produced non-finite output")

        result["elapsed_seconds"] = time.perf_counter() - started
        result["peak_allocated_bytes"] = int(
            torch.cuda.max_memory_allocated(device)
        )
        result["status"] = "pass"
        result["actual_device"] = requested_device
        result["convtranspose3d_fp32"] = "pass"
    except Exception as exc:  # noqa: BLE001
        result["error_code"] = result["error_code"] or "cuda_tensor_smoke_failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return DeviceCheck(**result)


def _nvidia_driver_version() -> str | None:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=driver_version",
                "--format=csv,noheader",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if completed.returncode != 0:
            return None
        first_line = completed.stdout.splitlines()[0].strip()
        return first_line or None
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


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

    if requested_device.startswith("cuda:"):
        try:
            index = int(requested_device.removeprefix("cuda:"))
        except ValueError as exc:
            raise ValueError(f"Unsupported device: {requested_device}") from exc
        if index < 0:
            raise ValueError(f"Unsupported device: {requested_device}")
        return smoke_test_cuda(index)

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
