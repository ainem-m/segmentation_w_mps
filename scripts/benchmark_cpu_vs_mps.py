#!/usr/bin/env python3
"""Benchmark TotalSegmentator CPU vs MPS.

This is a template script for the coding agent. It intentionally uses subprocess
so that the exact command is easy to reproduce.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from pathlib import Path


def run_command(cmd: list[str], log_path: Path) -> dict:
    start = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    elapsed = time.perf_counter() - start
    log_path.write_text(
        "COMMAND:\n" + " ".join(cmd) + "\n\nSTDOUT:\n" + proc.stdout + "\n\nSTDERR:\n" + proc.stderr,
        encoding="utf-8",
    )
    return {
        "command": cmd,
        "returncode": proc.returncode,
        "elapsed_seconds": elapsed,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
        "status": "success" if proc.returncode == 0 else "failed",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument(
        "--task",
        choices=("craniofacial_structures", "teeth"),
        default="craniofacial_structures",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--totalseg-bin", default="TotalSegmentator")
    args = parser.parse_args()

    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    (out / "cpu").mkdir(exist_ok=True)
    (out / "mps").mkdir(exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)

    env = {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "torch": None,
        "mps_built": None,
        "mps_available": None,
    }
    try:
        import torch

        env["torch"] = torch.__version__
        env["mps_built"] = torch.backends.mps.is_built()
        env["mps_available"] = torch.backends.mps.is_available()
    except Exception as exc:  # noqa: BLE001
        env["torch_error"] = repr(exc)

    cpu_cmd = [
        args.totalseg_bin,
        "-i",
        str(args.input),
        "-o",
        str(out / "cpu"),
        "-ta",
        args.task,
        "--device",
        "cpu",
    ]
    mps_cmd = [
        args.totalseg_bin,
        "-i",
        str(args.input),
        "-o",
        str(out / "mps"),
        "-ta",
        args.task,
        "--device",
        "mps",
    ]

    result = {
        "environment": env,
        "input": str(args.input),
        "task": args.task,
        "cpu": run_command(cpu_cmd, out / "logs" / "cpu.log"),
        "mps": run_command(mps_cmd, out / "logs" / "mps.log"),
    }

    cpu_t = result["cpu"].get("elapsed_seconds")
    mps_t = result["mps"].get("elapsed_seconds")
    if cpu_t and mps_t and result["cpu"]["returncode"] == 0 and result["mps"]["returncode"] == 0:
        result["speedup"] = cpu_t / mps_t
    else:
        result["speedup"] = None

    (out / "benchmark_summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    md = [
        "# Benchmark Summary",
        "",
        f"Input: `{args.input}`",
        f"Task: `{args.task}`",
        "",
        "| Device | Status | Elapsed seconds |",
        "|---|---:|---:|",
        f"| CPU | {result['cpu']['status']} | {result['cpu']['elapsed_seconds']:.2f} |",
        f"| MPS | {result['mps']['status']} | {result['mps']['elapsed_seconds']:.2f} |",
        "",
        f"Speedup: `{result['speedup']}`",
        "",
        "Non-clinical research/education preview only.",
    ]
    (out / "benchmark_summary.md").write_text("\n".join(md), encoding="utf-8")

    return 0 if result["mps"]["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
