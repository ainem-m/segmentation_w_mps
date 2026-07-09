from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Any


MODEL_LICENSE = "CC-BY-4.0"
MODEL_DOI = "10.5281/zenodo.10829675"
MODEL_SOURCE = "Zenodo"
CHUNK_SIZE = 1024 * 1024


def install_dentalsegmentator_model(
    *,
    model_url: str,
    model_zip: Path,
    expected_md5: str,
    nnunet_results: Path,
    nnunet_raw: Path,
    nnunet_preprocessed: Path,
    dataset_id: str,
    dataset_name: str,
    installer: Path | None = None,
    timeout_sec: int = 3600,
) -> dict[str, Any]:
    started = time.perf_counter()
    model_zip = model_zip.expanduser().resolve()
    nnunet_results = nnunet_results.expanduser().resolve()
    nnunet_raw = nnunet_raw.expanduser().resolve()
    nnunet_preprocessed = nnunet_preprocessed.expanduser().resolve()

    for path in (model_zip.parent, nnunet_results, nnunet_raw, nnunet_preprocessed):
        path.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "schema": "totalsegmentator_wrapper_mac.dentalsegmentator_model_setup.v1",
        "status": "success",
        "source": MODEL_SOURCE,
        "model_url": model_url,
        "model_zip": str(model_zip),
        "expected_md5": expected_md5,
        "nnUNet_results": str(nnunet_results),
        "nnUNet_raw": str(nnunet_raw),
        "nnUNet_preprocessed": str(nnunet_preprocessed),
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "license": MODEL_LICENSE,
        "doi": MODEL_DOI,
        "versions": _runtime_versions(),
        "downloaded": False,
        "installed": False,
        "elapsed_seconds": None,
    }

    installed_path = find_installed_dataset(nnunet_results, dataset_name)
    if installed_path is not None:
        if model_zip.exists():
            actual_md5 = file_md5(model_zip)
            result["actual_md5"] = actual_md5
            result["md5_verified"] = actual_md5 == expected_md5
        result["installed_path"] = str(installed_path)
        result["skipped_reason"] = "dataset_already_installed"
        result["elapsed_seconds"] = time.perf_counter() - started
        write_model_metadata(model_zip.parent / "dentalsegmentator_model.json", result)
        return result

    if not model_zip.exists() or file_md5(model_zip) != expected_md5:
        download_with_md5(model_url, model_zip, expected_md5=expected_md5, timeout_sec=timeout_sec)
        result["downloaded"] = True

    actual_md5 = file_md5(model_zip)
    result["actual_md5"] = actual_md5
    if actual_md5 != expected_md5:
        raise RuntimeError(
            f"DentalSegmentator model md5 mismatch: expected {expected_md5}, got {actual_md5}"
        )
    result["md5_verified"] = True

    installer_path = resolve_installer(installer)
    result["installer"] = str(installer_path)
    env = os.environ.copy()
    env["nnUNet_results"] = str(nnunet_results)
    env["nnUNet_raw"] = str(nnunet_raw)
    env["nnUNet_preprocessed"] = str(nnunet_preprocessed)
    proc = subprocess.run(  # noqa: S603
        [str(installer_path), str(model_zip)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=timeout_sec,
        check=False,
    )
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    result["install_returncode"] = proc.returncode
    result["install_stdout_tail"] = proc.stdout[-4000:]
    result["install_stderr_tail"] = proc.stderr[-4000:]
    if proc.returncode != 0:
        raise RuntimeError(
            "DentalSegmentator nnU-Net model install failed: "
            f"exit={proc.returncode}; stderr={proc.stderr[-1000:]}"
        )

    installed_path = find_installed_dataset(nnunet_results, dataset_name)
    if installed_path is None:
        raise RuntimeError(
            f"DentalSegmentator model install completed, but {dataset_name} was not found under {nnunet_results}"
        )

    result["installed"] = True
    result["installed_path"] = str(installed_path)
    result["elapsed_seconds"] = time.perf_counter() - started
    write_model_metadata(model_zip.parent / "dentalsegmentator_model.json", result)
    return result


def download_with_md5(url: str, destination: Path, *, expected_md5: str, timeout_sec: int) -> None:
    tmp_destination = destination.with_name(destination.name + ".tmp")
    digest = hashlib.md5()  # noqa: S324 - upstream publishes md5 for file integrity.
    bytes_read = 0
    print(f"Downloading DentalSegmentator model from {url}")
    with urllib.request.urlopen(url, timeout=timeout_sec) as response:  # noqa: S310
        with tmp_destination.open("wb") as output:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                bytes_read += len(chunk)
    actual_md5 = digest.hexdigest()
    if actual_md5 != expected_md5:
        raise RuntimeError(
            f"DentalSegmentator model download md5 mismatch: expected {expected_md5}, got {actual_md5}"
        )
    tmp_destination.replace(destination)
    print(f"Downloaded DentalSegmentator model: {bytes_read} bytes")


def find_installed_dataset(nnunet_results: Path, dataset_name: str) -> Path | None:
    if not nnunet_results.exists():
        return None
    for candidate in nnunet_results.rglob("dataset.json"):
        if dataset_name in candidate.parts:
            return candidate.parent
    dataset_root = nnunet_results / dataset_name
    if dataset_root.exists():
        return dataset_root
    return None


def file_md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - upstream publishes md5 for file integrity.
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_installer(installer: Path | None) -> Path:
    if installer is not None:
        resolved = installer.expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"nnU-Net installer not found: {resolved}")
        return resolved
    candidate = Path(sys.executable).parent / "nnUNetv2_install_pretrained_model_from_zip"
    if candidate.exists():
        return candidate
    found = shutil.which("nnUNetv2_install_pretrained_model_from_zip")
    if found:
        return Path(found)
    raise FileNotFoundError(
        "nnUNetv2_install_pretrained_model_from_zip was not found. "
        "Install the dentalseg extra or nnunetv2 package first."
    )


def write_model_metadata(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _runtime_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ("nnunetv2", "torch", "nibabel"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "not_installed"
    return versions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download and install the Zenodo DentalSegmentator nnU-Net model."
    )
    parser.add_argument("--model-url", required=True)
    parser.add_argument("--model-zip", required=True, type=Path)
    parser.add_argument("--expected-md5", required=True)
    parser.add_argument("--nnunet-results", required=True, type=Path)
    parser.add_argument("--nnunet-raw", required=True, type=Path)
    parser.add_argument("--nnunet-preprocessed", required=True, type=Path)
    parser.add_argument("--dataset-id", default="112")
    parser.add_argument("--dataset-name", default="Dataset112_DentalSegmentator_v100")
    parser.add_argument("--installer", type=Path, default=None)
    parser.add_argument("--timeout-sec", type=int, default=3600)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = install_dentalsegmentator_model(
            model_url=args.model_url,
            model_zip=args.model_zip,
            expected_md5=args.expected_md5,
            nnunet_results=args.nnunet_results,
            nnunet_raw=args.nnunet_raw,
            nnunet_preprocessed=args.nnunet_preprocessed,
            dataset_id=args.dataset_id,
            dataset_name=args.dataset_name,
            installer=args.installer,
            timeout_sec=args.timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001
        payload = {
            "schema": "totalsegmentator_wrapper_mac.dentalsegmentator_model_setup.v1",
            "status": "failed",
            "error": repr(exc),
            "model_url": args.model_url,
            "model_zip": str(args.model_zip),
            "nnUNet_results": str(args.nnunet_results),
            "dataset_id": args.dataset_id,
            "dataset_name": args.dataset_name,
            "license": MODEL_LICENSE,
            "doi": MODEL_DOI,
            "versions": _runtime_versions(),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
