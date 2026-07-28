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
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any
from uuid import uuid4


MODEL_LICENSE = "CC-BY-4.0"
MODEL_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
MODEL_DOI = "10.5281/zenodo.10829675"
MODEL_SOURCE = "Zenodo"
MODEL_TITLE = "DentalSegmentator nnU-Net pretrained model for CBCT image segmentation"
MODEL_CREATORS = ("Gauthier Dot",)
MODEL_CHECKPOINTS_MODIFIED = False
CHUNK_SIZE = 1024 * 1024
MODEL_STATUS_SCHEMA = "totalsegmentator_wrapper_mac.dentalsegmentator_model_status.v1"
MODEL_SETUP_SCHEMA = "totalsegmentator_wrapper_mac.dentalsegmentator_model_setup.v1"
READY_MARKER_FILENAME = ".dentalsegmentator_model_ready.json"
PARTIAL_METADATA_SUFFIX = ".part.json"


def dentalsegmentator_model_status(
    *,
    model_root: Path,
    expected_md5: str,
    dataset_id: str,
    dataset_name: str,
    model_zip: Path | None = None,
    nnunet_results: Path | None = None,
) -> dict[str, Any]:
    model_root = model_root.expanduser().resolve()
    model_zip = (model_zip or model_root / f"{dataset_name}.zip").expanduser().resolve()
    nnunet_results = (nnunet_results or model_root / "nnUNet_results").expanduser().resolve()
    dataset_root = nnunet_results / dataset_name
    marker_path = dataset_root / READY_MARKER_FILENAME
    result = _model_status_payload(dataset_id=dataset_id, dataset_name=dataset_name)

    marker = _read_json_if_exists(marker_path)
    if _is_complete_marker(
        marker,
        dataset_root=dataset_root,
        expected_md5=expected_md5,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
    ):
        result.update({"status": "ready", "model_state": "ready"})
        return result

    partial_zip = model_zip.with_name(model_zip.name + ".part")
    partial_metadata = partial_zip.with_name(partial_zip.name + ".json")
    partial_state = _read_json_if_exists(partial_metadata)
    partial_is_resumable = bool(
        partial_zip.exists()
        and partial_zip.stat().st_size > 0
        and partial_state
        and partial_state.get("expected_md5") == expected_md5
        and isinstance(partial_state.get("url"), str)
    )
    staging_present = any(model_zip.parent.glob(".dentalsegmentator-staging-*"))
    if (
        dataset_root.exists()
        or marker is not None
        or model_zip.exists()
        or partial_is_resumable
        or staging_present
    ):
        result.update({"status": "resumable", "model_state": "resumable"})
        return result

    metadata = _read_json_if_exists(model_zip.parent / "dentalsegmentator_model.json")
    if metadata and metadata.get("status") == "failed":
        result.update(
            _safe_error_fields(
                "model_prepare_failed",
                "The previous DentalSegmentator preparation attempt did not complete.",
            )
        )
        result["status"] = "failed"
        result["model_state"] = "failed"
        return result

    result.update({"status": "not_installed", "model_state": "not_installed"})
    return result


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
        "schema": MODEL_SETUP_SCHEMA,
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
        "license_url": MODEL_LICENSE_URL,
        "doi": MODEL_DOI,
        "title": MODEL_TITLE,
        "creators": list(MODEL_CREATORS),
        "checkpoints_modified": MODEL_CHECKPOINTS_MODIFIED,
        "versions": _runtime_versions(),
        "downloaded": False,
        "installed": False,
        "elapsed_seconds": None,
        "model_state": "not_installed",
    }

    model_root = model_zip.parent
    current_status = dentalsegmentator_model_status(
        model_root=model_root,
        expected_md5=expected_md5,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        model_zip=model_zip,
        nnunet_results=nnunet_results,
    )
    if current_status["model_state"] == "ready":
        result["actual_md5"] = expected_md5
        result["md5_verified"] = True
        result["installed_path"] = str(nnunet_results / dataset_name)
        result["skipped_reason"] = "dataset_already_installed"
        result["model_state"] = "ready"
        result["elapsed_seconds"] = time.perf_counter() - started
        write_model_metadata(model_root / "dentalsegmentator_model.json", result)
        return result

    try:
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
    except Exception:  # noqa: BLE001
        _record_prepare_failure(result, model_root)
        raise

    installer_path = resolve_installer(installer)
    result["installer"] = str(installer_path)
    env = os.environ.copy()
    staging_root = model_root / f".dentalsegmentator-staging-{uuid4().hex}"
    staging_results = staging_root / "nnUNet_results"
    env["nnUNet_results"] = str(staging_results)
    env["nnUNet_raw"] = str(nnunet_raw)
    env["nnUNet_preprocessed"] = str(nnunet_preprocessed)
    try:
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

        staged_dataset = staging_results / dataset_name
        if find_installed_dataset(staging_results, dataset_name) is None:
            raise RuntimeError("DentalSegmentator model install did not produce a valid dataset.")
        _write_ready_marker(
            staged_dataset,
            expected_md5=expected_md5,
            dataset_id=dataset_id,
            dataset_name=dataset_name,
        )
        _publish_staged_dataset(staged_dataset, nnunet_results / dataset_name)
        result["installed"] = True
        result["installed_path"] = str(nnunet_results / dataset_name)
        result["model_state"] = "ready"
        result["elapsed_seconds"] = time.perf_counter() - started
        write_model_metadata(model_root / "dentalsegmentator_model.json", result)
        return result
    except Exception as exc:  # noqa: BLE001
        _record_prepare_failure(result, model_root, started=started)
        raise
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)


def download_with_md5(url: str, destination: Path, *, expected_md5: str, timeout_sec: int) -> None:
    tmp_destination = destination.with_name(destination.name + ".part")
    partial_metadata = tmp_destination.with_name(tmp_destination.name + ".json")
    can_resume = _partial_download_is_resumable(
        tmp_destination,
        partial_metadata,
        url=url,
        expected_md5=expected_md5,
    )
    if not can_resume:
        tmp_destination.unlink(missing_ok=True)
        partial_metadata.unlink(missing_ok=True)
    partial_metadata.write_text(
        json.dumps({"url": url, "expected_md5": expected_md5}, sort_keys=True),
        encoding="utf-8",
    )
    bytes_read = tmp_destination.stat().st_size if can_resume else 0
    digest = hashlib.md5()  # noqa: S324 - upstream publishes md5 for file integrity.
    if can_resume:
        with tmp_destination.open("rb") as existing:
            for chunk in iter(lambda: existing.read(CHUNK_SIZE), b""):
                digest.update(chunk)
    print(f"Downloading DentalSegmentator model from {url}")
    request: str | urllib.request.Request = url
    if can_resume:
        request = urllib.request.Request(url, headers={"Range": f"bytes={bytes_read}-"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:  # noqa: S310
            if can_resume and not _valid_range_response(response, bytes_read):
                raise _UnsafeResumeError("server did not confirm the requested byte range")
            with tmp_destination.open("ab" if can_resume else "wb") as output:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    bytes_read += len(chunk)
    except _UnsafeResumeError:
        tmp_destination.unlink(missing_ok=True)
        partial_metadata.unlink(missing_ok=True)
        return download_with_md5(url, destination, expected_md5=expected_md5, timeout_sec=timeout_sec)
    actual_md5 = digest.hexdigest()
    if actual_md5 != expected_md5:
        tmp_destination.unlink(missing_ok=True)
        partial_metadata.unlink(missing_ok=True)
        raise RuntimeError(
            f"DentalSegmentator model download md5 mismatch: expected {expected_md5}, got {actual_md5}"
        )
    tmp_destination.replace(destination)
    partial_metadata.unlink(missing_ok=True)
    print(f"Downloaded DentalSegmentator model: {bytes_read} bytes")


def find_installed_dataset(nnunet_results: Path, dataset_name: str) -> Path | None:
    if not nnunet_results.exists():
        return None
    for candidate in nnunet_results.rglob("dataset.json"):
        if dataset_name in candidate.parts:
            return candidate.parent
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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


class _UnsafeResumeError(RuntimeError):
    pass


def _partial_download_is_resumable(
    partial_path: Path,
    metadata_path: Path,
    *,
    url: str,
    expected_md5: str,
) -> bool:
    metadata = _read_json_if_exists(metadata_path)
    return bool(
        partial_path.exists()
        and partial_path.stat().st_size > 0
        and metadata == {"url": url, "expected_md5": expected_md5}
    )


def _valid_range_response(response: Any, offset: int) -> bool:
    status = getattr(response, "status", None) or response.getcode()
    content_range = response.headers.get("Content-Range") if response.headers else None
    return status == 206 and isinstance(content_range, str) and content_range.startswith(f"bytes {offset}-")


def _model_status_payload(*, dataset_id: str, dataset_name: str) -> dict[str, Any]:
    return {
        "schema": MODEL_STATUS_SCHEMA,
        "status": "not_installed",
        "model_state": "not_installed",
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "error_code": None,
        "safe_reason": None,
        "mps_state": "not_applicable",
        "occurred_at": None,
    }


def _safe_error_fields(error_code: str, safe_reason: str) -> dict[str, str]:
    return {
        "error_code": error_code,
        "safe_reason": safe_reason,
        "mps_state": "not_applicable",
        "occurred_at": datetime.now(UTC).isoformat(),
    }


def _record_prepare_failure(
    result: dict[str, Any], model_root: Path, *, started: float | None = None
) -> None:
    result.update(
        _safe_error_fields(
            "model_prepare_failed",
            "DentalSegmentator model preparation did not complete.",
        )
    )
    result["status"] = "failed"
    result["model_state"] = "failed"
    if started is not None:
        result["elapsed_seconds"] = time.perf_counter() - started
    write_model_metadata(model_root / "dentalsegmentator_model.json", result)


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _is_complete_marker(
    marker: dict[str, Any] | None,
    *,
    dataset_root: Path,
    expected_md5: str,
    dataset_id: str,
    dataset_name: str,
) -> bool:
    return bool(
        marker
        and marker.get("schema") == MODEL_STATUS_SCHEMA
        and marker.get("model_state") == "ready"
        and marker.get("expected_md5") == expected_md5
        and marker.get("dataset_id") == dataset_id
        and marker.get("dataset_name") == dataset_name
        and find_installed_dataset(dataset_root.parent, dataset_name) is not None
    )


def _write_ready_marker(
    dataset_root: Path,
    *,
    expected_md5: str,
    dataset_id: str,
    dataset_name: str,
) -> None:
    marker = {
        "schema": MODEL_STATUS_SCHEMA,
        "model_state": "ready",
        "expected_md5": expected_md5,
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "validated_at": datetime.now(UTC).isoformat(),
    }
    write_model_metadata(dataset_root / READY_MARKER_FILENAME, marker)


def _publish_staged_dataset(staged_dataset: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = destination.with_name(destination.name + ".previous")
    if backup.exists():
        shutil.rmtree(backup)
    replaced_existing = False
    try:
        if destination.exists():
            destination.replace(backup)
            replaced_existing = True
        staged_dataset.replace(destination)
    except Exception:
        if replaced_existing and backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)


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
