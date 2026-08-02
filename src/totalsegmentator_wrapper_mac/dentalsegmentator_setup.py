from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from urllib.parse import urlsplit
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
PARTIAL_DOWNLOAD_SCHEMA = "totalsegmentator_wrapper_mac.dentalsegmentator_partial_download.v1"
STAGING_METADATA_FILENAME = ".dentalsegmentator_staging.json"
DEFAULT_DATASET_ID = "112"
DEFAULT_DATASET_NAME = "Dataset112_DentalSegmentator_v100"
DEFAULT_TRAINER_DIR = "nnUNetTrainer__nnUNetPlans__3d_fullres"
MODEL_ARCHIVE_MD5 = "b71cd5230168d28a4f71b078265b76be"
MODEL_ARCHIVE_SHA256 = "bc5510cc93bc2100ab1faccb63512e09c1ca326c738b0a9939c074d82b38a4ac"
MODEL_ARCHIVE_SHA256_PROVENANCE = (
    "locally-observed official asset verified against publisher MD5"
)
# Operational ceilings, not claims about the exact published asset size.
MAX_MODEL_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
MAX_EXTRACTED_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
_CONTENT_RANGE_PATTERN = re.compile(r"bytes (\d+)-(\d+)/(\d+)")


class DentalSegmentatorSetupBusyError(RuntimeError):
    """Raised when another process owns the DentalSegmentator preparation lock."""


def dentalsegmentator_model_status(
    *,
    model_root: Path,
    expected_md5: str,
    dataset_id: str,
    dataset_name: str,
    model_zip: Path | None = None,
    nnunet_results: Path | None = None,
) -> dict[str, Any]:
    result = _model_status_payload(dataset_id=dataset_id, dataset_name=dataset_name)
    raw_model_root = model_root.expanduser()
    if raw_model_root.is_symlink() or (raw_model_root.exists() and not raw_model_root.is_dir()):
        result.update(
            _safe_error_fields(
                "invalid_model_path",
                "The DentalSegmentator model root is not a regular directory.",
            )
        )
        result.update({"status": "failed", "model_state": "failed"})
        return result
    model_root = raw_model_root.resolve()
    raw_model_zip = (model_zip or model_root / f"{dataset_name}.zip").expanduser()
    raw_results = (nnunet_results or model_root / "nnUNet_results").expanduser()
    if raw_model_zip.is_symlink() or raw_results.is_symlink():
        result.update(
            _safe_error_fields(
                "invalid_model_path",
                "A DentalSegmentator model target is a symbolic link.",
            )
        )
        result.update({"status": "failed", "model_state": "failed"})
        return result
    model_zip = raw_model_zip.resolve()
    nnunet_results = raw_results.resolve()
    dataset_root = nnunet_results / dataset_name
    marker_path = dataset_root / READY_MARKER_FILENAME

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
    partial_size = partial_zip.stat().st_size if _is_regular_file(partial_zip) else 0
    partial_is_resumable = bool(
        partial_size > 0
        and partial_state
        and _partial_state_is_compatible(
            partial_state,
            url=partial_state.get("url"),
            expected_md5=expected_md5,
            expected_sha256=_expected_archive_sha256(expected_md5),
            partial_size=partial_size,
        )
    )
    staging_present = any(model_zip.parent.glob(".dentalsegmentator-staging-*"))
    if (
        dataset_root.exists()
        or dataset_root.is_symlink()
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
    progress_log: Path | None = None,
) -> dict[str, Any]:
    _validate_https_url(model_url, label="DentalSegmentator model")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", expected_md5):
        raise RuntimeError("DentalSegmentator model MD5 must be a 32-character hexadecimal digest")
    model_zip = _prepare_file_target(model_zip, label="DentalSegmentator model archive")
    nnunet_results = _prepare_directory_target(
        nnunet_results,
        label="DentalSegmentator nnUNet_results",
    )
    nnunet_raw = _prepare_directory_target(nnunet_raw, label="DentalSegmentator nnUNet_raw")
    nnunet_preprocessed = _prepare_directory_target(
        nnunet_preprocessed,
        label="DentalSegmentator nnUNet_preprocessed",
    )
    model_root = model_zip.parent
    model_root.mkdir(parents=True, exist_ok=True)
    with _exclusive_setup_lock(model_root):
        for path in (nnunet_results, nnunet_raw, nnunet_preprocessed):
            path.mkdir(parents=True, exist_ok=True)
        _recover_orphaned_install_state(
            model_root=model_root,
            nnunet_results=nnunet_results,
            expected_md5=expected_md5.lower(),
            dataset_id=dataset_id,
            dataset_name=dataset_name,
        )
        return _install_dentalsegmentator_model_locked(
            model_url=model_url,
            model_zip=model_zip,
            expected_md5=expected_md5.lower(),
            nnunet_results=nnunet_results,
            nnunet_raw=nnunet_raw,
            nnunet_preprocessed=nnunet_preprocessed,
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            installer=installer,
            timeout_sec=timeout_sec,
            progress_log=progress_log,
        )


def _install_dentalsegmentator_model_locked(
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
    progress_log: Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    model_zip = model_zip.expanduser().resolve()
    nnunet_results = nnunet_results.expanduser().resolve()
    nnunet_raw = nnunet_raw.expanduser().resolve()
    nnunet_preprocessed = nnunet_preprocessed.expanduser().resolve()

    for path in (model_zip.parent, nnunet_results, nnunet_raw, nnunet_preprocessed):
        path.mkdir(parents=True, exist_ok=True)

    expected_sha256 = _expected_archive_sha256(expected_md5)
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
    if expected_sha256 is not None:
        result.update(
            {
                "expected_sha256": expected_sha256,
                "sha256_provenance": MODEL_ARCHIVE_SHA256_PROVENANCE,
            }
        )

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
        ready_marker = _read_json_if_exists(
            nnunet_results / dataset_name / READY_MARKER_FILENAME
        )
        archive_md5_verified = bool(
            ready_marker and ready_marker.get("archive_md5_verified") is True
        )
        if archive_md5_verified:
            result["actual_md5"] = expected_md5
        result["md5_verified"] = archive_md5_verified
        result["installed_path"] = str(nnunet_results / dataset_name)
        result["skipped_reason"] = "dataset_already_installed"
        result["model_state"] = "ready"
        result["elapsed_seconds"] = time.perf_counter() - started
        write_model_metadata(model_root / "dentalsegmentator_model.json", result)
        return result

    dataset_root = nnunet_results / dataset_name
    marker = _read_json_if_exists(dataset_root / READY_MARKER_FILENAME)
    if _legacy_dataset_can_be_migrated(
        marker,
        dataset_root=dataset_root,
        expected_md5=expected_md5,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
    ):
        _write_ready_marker(
            dataset_root,
            expected_md5=expected_md5,
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            archive_sha256=None,
            legacy_marker_migrated=True,
        )
        result.update(
            {
                "installed": True,
                "installed_path": str(dataset_root),
                "model_state": "ready",
                "reused_existing_dataset": True,
                "md5_verified": False,
                "legacy_marker_expected_md5_matched": True,
                "archive_integrity_evidence": "legacy-ready-marker-plus-deep-runtime-validation",
                "elapsed_seconds": time.perf_counter() - started,
            }
        )
        write_model_metadata(model_root / "dentalsegmentator_model.json", result)
        return result

    try:
        archive_is_valid = bool(
            _is_regular_file(model_zip)
            and file_md5(model_zip) == expected_md5
            and (expected_sha256 is None or file_sha256(model_zip) == expected_sha256)
        )
        if not archive_is_valid:
            download_with_md5(
                model_url,
                model_zip,
                expected_md5=expected_md5,
                expected_sha256=expected_sha256,
                timeout_sec=timeout_sec,
                progress_log=progress_log,
            )
            result["downloaded"] = True

        actual_md5 = file_md5(model_zip)
        result["actual_md5"] = actual_md5
        if actual_md5 != expected_md5:
            raise RuntimeError(
                f"DentalSegmentator model md5 mismatch: expected {expected_md5}, got {actual_md5}"
            )
        result["md5_verified"] = True
        actual_sha256 = file_sha256(model_zip)
        result["actual_sha256"] = actual_sha256
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise RuntimeError(
                "DentalSegmentator model SHA-256 does not match the locally observed official asset"
            )
        result["sha256_verified"] = expected_sha256 is not None
        _validate_model_archive(model_zip, dataset_name=dataset_name)
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
        staging_root.mkdir(parents=True, exist_ok=False)
        write_model_metadata(
            staging_root / STAGING_METADATA_FILENAME,
            {
                "schema": "totalsegmentator_wrapper_mac.dentalsegmentator_staging.v1",
                "expected_md5": expected_md5,
                "dataset_id": dataset_id,
                "dataset_name": dataset_name,
                "archive_sha256": expected_sha256,
            },
        )
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
        if not _validate_dataset_runtime(staged_dataset, deep_checkpoint=True):
            raise RuntimeError("DentalSegmentator model install did not produce a valid dataset.")
        _write_ready_marker(
            staged_dataset,
            expected_md5=expected_md5,
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            archive_sha256=expected_sha256,
            legacy_marker_migrated=False,
        )
        _publish_staged_dataset(staged_dataset, nnunet_results / dataset_name)
        result["installed"] = True
        result["installed_path"] = str(nnunet_results / dataset_name)
        result["model_state"] = "ready"
        result["elapsed_seconds"] = time.perf_counter() - started
        write_model_metadata(model_root / "dentalsegmentator_model.json", result)
        return result
    except Exception:  # noqa: BLE001
        _record_prepare_failure(result, model_root, started=started)
        raise
    finally:
        if staging_root.exists():
            _remove_directory(staging_root)


def download_with_md5(
    url: str,
    destination: Path,
    *,
    expected_md5: str,
    expected_sha256: str | None = None,
    timeout_sec: int,
    progress_log: Path | None = None,
) -> None:
    _validate_https_url(url, label="DentalSegmentator model")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", expected_md5):
        raise RuntimeError("DentalSegmentator model MD5 must be a 32-character hexadecimal digest")
    expected_md5 = expected_md5.lower()
    if expected_sha256 is not None:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
            raise RuntimeError(
                "DentalSegmentator model SHA-256 must be a 64-character hexadecimal digest"
            )
        expected_sha256 = expected_sha256.lower()
    destination = _prepare_file_target(
        destination,
        label="DentalSegmentator model archive",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    _ensure_regular_or_absent(destination, label="DentalSegmentator model archive")
    tmp_destination = destination.with_name(destination.name + ".part")
    partial_metadata = tmp_destination.with_name(tmp_destination.name + ".json")
    _ensure_regular_or_absent(tmp_destination, label="DentalSegmentator partial download")
    _ensure_regular_or_absent(partial_metadata, label="DentalSegmentator partial metadata")
    can_resume = _partial_download_is_resumable(
        tmp_destination,
        partial_metadata,
        url=url,
        expected_md5=expected_md5,
        expected_sha256=expected_sha256,
    )
    if not can_resume:
        _clear_partial_download(tmp_destination, partial_metadata)
    initial_bytes = tmp_destination.stat().st_size if can_resume else 0
    state = _read_json_if_exists(partial_metadata) if can_resume else None
    learned_total = state.get("total_bytes") if state else None
    was_resumed = can_resume
    restart_used = False
    if can_resume:
        _write_partial_state(
            partial_metadata,
            url=url,
            expected_md5=expected_md5,
            expected_sha256=expected_sha256,
            total_bytes=learned_total,
        )
    print(f"Downloading DentalSegmentator model from {url}")
    if can_resume:
        partial_md5_matches = file_md5(tmp_destination) == expected_md5
        partial_sha256_matches = bool(
            expected_sha256 is None or file_sha256(tmp_destination) == expected_sha256
        )
        if partial_md5_matches and partial_sha256_matches:
            _atomic_publish_file(tmp_destination, destination)
            partial_metadata.unlink(missing_ok=True)
            _write_download_progress(
                progress_log,
                status="complete",
                downloaded_bytes=initial_bytes,
                total_bytes=learned_total or initial_bytes,
                rate_bps=None,
                eta_seconds=0.0,
                resumed=True,
                resume_from_bytes=initial_bytes,
            )
            print(f"Reused complete DentalSegmentator partial: {initial_bytes} bytes")
            return
        if learned_total is not None and initial_bytes == learned_total:
            _clear_partial_download(tmp_destination, partial_metadata)
            learned_total = None
            initial_bytes = 0
            was_resumed = False
    while True:
        offset = tmp_destination.stat().st_size if _is_regular_file(tmp_destination) else 0
        request_headers = {"Accept-Encoding": "identity"}
        if offset:
            request_headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=request_headers)
        try:
            response_context = urllib.request.urlopen(request, timeout=timeout_sec)  # noqa: S310
        except urllib.error.HTTPError as exc:
            if offset and exc.code == 416 and not restart_used:
                exc.close()
                _clear_partial_download(tmp_destination, partial_metadata)
                learned_total = None
                restart_used = True
                continue
            if exc.code == 416:
                exc.close()
                _clear_partial_download(tmp_destination, partial_metadata)
                raise RuntimeError(
                    "DentalSegmentator server rejected the range after one safe restart"
                ) from exc
            raise

        with response_context as response:
            _validate_https_url(response.geturl(), label="DentalSegmentator model response")
            status = getattr(response, "status", None) or response.getcode()
            headers = getattr(response, "headers", None) or {}
            content_encoding = headers.get("Content-Encoding")
            if content_encoding not in (None, "", "identity"):
                _clear_partial_download(tmp_destination, partial_metadata)
                raise RuntimeError(
                    "DentalSegmentator response used an unsupported Content-Encoding"
                )
            if offset:
                if status == 200 and not restart_used:
                    _clear_partial_download(tmp_destination, partial_metadata)
                    learned_total = None
                    restart_used = True
                    continue
                if status != 206:
                    _clear_partial_download(tmp_destination, partial_metadata)
                    raise RuntimeError("DentalSegmentator resume response did not use HTTP 206")
                try:
                    range_start, range_end, response_total = _parse_content_range(
                        headers.get("Content-Range")
                    )
                    content_length = _parse_content_length(headers.get("Content-Length"))
                except RuntimeError:
                    _clear_partial_download(tmp_destination, partial_metadata)
                    raise
                if range_start != offset:
                    _clear_partial_download(tmp_destination, partial_metadata)
                    raise RuntimeError("DentalSegmentator resume started at an unexpected byte")
                if learned_total is not None and response_total != learned_total:
                    _clear_partial_download(tmp_destination, partial_metadata)
                    raise RuntimeError("DentalSegmentator response changed the declared total size")
                learned_total = response_total
                expected_response_bytes = range_end - range_start + 1
                if content_length is not None and content_length != expected_response_bytes:
                    _clear_partial_download(tmp_destination, partial_metadata)
                    raise RuntimeError(
                        "DentalSegmentator Content-Length disagrees with Content-Range"
                    )
            else:
                if status != 200:
                    _clear_partial_download(tmp_destination, partial_metadata)
                    raise RuntimeError("DentalSegmentator full download did not return HTTP 200")
                try:
                    content_length = _parse_content_length(headers.get("Content-Length"))
                except RuntimeError:
                    _clear_partial_download(tmp_destination, partial_metadata)
                    raise
                if content_length is not None:
                    learned_total = content_length
                expected_response_bytes = content_length

            if learned_total is not None and learned_total > MAX_MODEL_ARCHIVE_BYTES:
                _clear_partial_download(tmp_destination, partial_metadata)
                raise RuntimeError("DentalSegmentator model exceeds the operational safety limit")
            if offset > MAX_MODEL_ARCHIVE_BYTES or (
                learned_total is not None and offset > learned_total
            ):
                _clear_partial_download(tmp_destination, partial_metadata)
                raise RuntimeError("DentalSegmentator partial download exceeds its validated size")
            _write_partial_state(
                partial_metadata,
                url=url,
                expected_md5=expected_md5,
                expected_sha256=expected_sha256,
                total_bytes=learned_total,
            )

            bytes_read = offset
            response_bytes = 0
            download_started = time.perf_counter()
            _write_download_progress(
                progress_log,
                status="downloading",
                downloaded_bytes=bytes_read,
                total_bytes=learned_total,
                rate_bps=None,
                eta_seconds=None,
                resumed=was_resumed,
                resume_from_bytes=initial_bytes,
            )
            try:
                with tmp_destination.open("ab" if offset else "wb") as output:
                    while chunk := response.read(CHUNK_SIZE):
                        next_size = bytes_read + len(chunk)
                        if next_size > MAX_MODEL_ARCHIVE_BYTES:
                            raise RuntimeError(
                                "DentalSegmentator model exceeds the operational safety limit"
                            )
                        if learned_total is not None and next_size > learned_total:
                            raise RuntimeError(
                                "DentalSegmentator response exceeded its declared total size"
                            )
                        output.write(chunk)
                        bytes_read = next_size
                        response_bytes += len(chunk)
                        elapsed = max(time.perf_counter() - download_started, 1e-6)
                        rate_bps = response_bytes / elapsed
                        eta_seconds = (
                            max(0.0, (learned_total - bytes_read) / rate_bps)
                            if learned_total is not None and rate_bps > 0
                            else None
                        )
                        _write_download_progress(
                            progress_log,
                            status="downloading",
                            downloaded_bytes=bytes_read,
                            total_bytes=learned_total,
                            rate_bps=rate_bps,
                            eta_seconds=eta_seconds,
                            resumed=was_resumed,
                            resume_from_bytes=initial_bytes,
                        )
            except RuntimeError:
                _clear_partial_download(tmp_destination, partial_metadata)
                raise

        actual_size = tmp_destination.stat().st_size
        if (
            (expected_response_bytes is not None and response_bytes < expected_response_bytes)
            or (learned_total is not None and actual_size < learned_total)
        ):
            if response_bytes == 0:
                raise RuntimeError(
                    "DentalSegmentator download made no progress; partial data was preserved"
                )
            continue
        if learned_total is not None and actual_size != learned_total:
            _clear_partial_download(tmp_destination, partial_metadata)
            raise RuntimeError("DentalSegmentator download size differs from the declared total")

        actual_md5 = file_md5(tmp_destination)
        if actual_md5 != expected_md5:
            _clear_partial_download(tmp_destination, partial_metadata)
            raise RuntimeError(
                f"DentalSegmentator model download md5 mismatch: expected {expected_md5}, got {actual_md5}"
            )
        if expected_sha256 is not None and file_sha256(tmp_destination) != expected_sha256:
            _clear_partial_download(tmp_destination, partial_metadata)
            raise RuntimeError(
                "DentalSegmentator model SHA-256 does not match the locally observed official asset"
            )
        _atomic_publish_file(tmp_destination, destination)
        partial_metadata.unlink(missing_ok=True)
        _write_download_progress(
            progress_log,
            status="complete",
            downloaded_bytes=actual_size,
            total_bytes=actual_size,
            rate_bps=None,
            eta_seconds=0.0,
            resumed=was_resumed,
            resume_from_bytes=initial_bytes,
        )
        print(f"Downloaded DentalSegmentator model: {actual_size} bytes")
        return


def _write_download_progress(
    progress_log: Path | None,
    *,
    status: str,
    downloaded_bytes: int,
    total_bytes: int | None,
    rate_bps: float | None,
    eta_seconds: float | None,
    resumed: bool,
    resume_from_bytes: int,
) -> None:
    if progress_log is None:
        return
    percent = (
        min(100, int(downloaded_bytes * 100 / total_bytes))
        if total_bytes is not None and total_bytes > 0
        else None
    )
    payload = {
        "source": "dentalsegmentator",
        "status": status,
        "task_id": 112,
        "index": 1,
        "task_total": 1,
        "completed_bytes": downloaded_bytes,
        "total_bytes": total_bytes,
        "percent": percent,
        "rate_bps": rate_bps,
        "eta_seconds": eta_seconds,
        "resumed": resumed,
        "resume_from_bytes": resume_from_bytes,
    }
    progress_log.parent.mkdir(parents=True, exist_ok=True)
    with progress_log.open("a", encoding="utf-8") as log:
        log.write("SETUP_DOWNLOAD_PROGRESS " + json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        log.flush()


def _parse_content_length(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.isdigit() or int(value) < 0:
        raise RuntimeError("DentalSegmentator response has an invalid Content-Length")
    return int(value)


def _parse_content_range(value: Any) -> tuple[int, int, int]:
    match = _CONTENT_RANGE_PATTERN.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise RuntimeError("DentalSegmentator response has an invalid Content-Range")
    start, end, total = (int(part) for part in match.groups())
    if start > end or end >= total or total <= 0:
        raise RuntimeError("DentalSegmentator response has an impossible Content-Range")
    return start, end, total


def _expected_archive_sha256(expected_md5: str) -> str | None:
    return MODEL_ARCHIVE_SHA256 if expected_md5.lower() == MODEL_ARCHIVE_MD5 else None


def find_installed_dataset(nnunet_results: Path, dataset_name: str) -> Path | None:
    dataset_root = nnunet_results / dataset_name
    return dataset_root / DEFAULT_TRAINER_DIR if _validate_dataset_runtime(dataset_root) else None


def file_md5(path: Path) -> str:
    if not _is_regular_file(path):
        raise RuntimeError("DentalSegmentator MD5 target is not a regular file")
    digest = hashlib.md5()  # noqa: S324 - upstream publishes md5 for file integrity.
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    if not _is_regular_file(path):
        raise RuntimeError("DentalSegmentator SHA-256 target is not a regular file")
    digest = hashlib.sha256()
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
    _ensure_regular_or_absent(path, label="DentalSegmentator JSON target")
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        _atomic_publish_file(temporary, path)
    finally:
        if temporary.is_symlink() or (temporary.exists() and not _is_regular_file(temporary)):
            raise RuntimeError("DentalSegmentator JSON temporary path is not a regular file")
        temporary.unlink(missing_ok=True)


def _partial_download_is_resumable(
    partial_path: Path,
    metadata_path: Path,
    *,
    url: str,
    expected_md5: str,
    expected_sha256: str | None,
) -> bool:
    metadata = _read_json_if_exists(metadata_path)
    partial_size = partial_path.stat().st_size if _is_regular_file(partial_path) else 0
    return bool(
        partial_size > 0
        and metadata
        and _partial_state_is_compatible(
            metadata,
            url=url,
            expected_md5=expected_md5,
            expected_sha256=expected_sha256,
            partial_size=partial_size,
        )
    )


def _partial_state_is_compatible(
    state: dict[str, Any],
    *,
    url: Any,
    expected_md5: str,
    expected_sha256: str | None,
    partial_size: int,
) -> bool:
    schema = state.get("schema")
    if schema == PARTIAL_DOWNLOAD_SCHEMA:
        if set(state) != {
            "schema",
            "url",
            "expected_md5",
            "expected_sha256",
            "total_bytes",
        }:
            return False
    elif schema is None:
        if set(state) != {"url", "expected_md5"}:
            return False
    else:
        return False
    total = state.get("total_bytes")
    total_is_valid = total is None or (
        isinstance(total, int)
        and not isinstance(total, bool)
        and partial_size <= total <= MAX_MODEL_ARCHIVE_BYTES
    )
    sha256_matches = (
        state.get("expected_sha256") == expected_sha256
        if schema == PARTIAL_DOWNLOAD_SCHEMA
        else expected_sha256 is None or state.get("expected_sha256") is None
    )
    return bool(
        isinstance(url, str)
        and state.get("url") == url
        and state.get("expected_md5") == expected_md5
        and sha256_matches
        and total_is_valid
    )


def _write_partial_state(
    path: Path,
    *,
    url: str,
    expected_md5: str,
    expected_sha256: str | None,
    total_bytes: int | None,
) -> None:
    write_model_metadata(
        path,
        {
            "schema": PARTIAL_DOWNLOAD_SCHEMA,
            "url": url,
            "expected_md5": expected_md5,
            "expected_sha256": expected_sha256,
            "total_bytes": total_bytes,
        },
    )


def _clear_partial_download(partial: Path, metadata_path: Path) -> None:
    for path in (partial, metadata_path):
        if path.is_symlink() or (path.exists() and not _is_regular_file(path)):
            raise RuntimeError("DentalSegmentator partial target is not a regular file")
        path.unlink(missing_ok=True)


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
    if not _is_regular_file(path):
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
    if not (
        marker
        and marker.get("schema") == MODEL_STATUS_SCHEMA
        and marker.get("model_state") == "ready"
        and marker.get("expected_md5") == expected_md5
        and marker.get("dataset_id") == dataset_id
        and marker.get("dataset_name") == dataset_name
        and isinstance(marker.get("runtime_files"), list)
    ):
        return False
    archive_sha256 = marker.get("archive_sha256")
    expected_sha256 = _expected_archive_sha256(expected_md5)
    legacy_migrated = marker.get("legacy_marker_migrated") is True
    if legacy_migrated:
        if (
            archive_sha256 is not None
            or marker.get("sha256_provenance") is not None
            or marker.get("archive_md5_verified") is not False
            or marker.get("archive_sha256_verified") is not False
        ):
            return False
    elif marker.get("archive_md5_verified") is not True:
        return False
    if archive_sha256 is not None and (
        expected_sha256 is None
        or archive_sha256 != expected_sha256
        or marker.get("sha256_provenance") != MODEL_ARCHIVE_SHA256_PROVENANCE
        or marker.get("archive_sha256_verified") is not True
    ):
        return False
    if expected_sha256 is not None and not legacy_migrated and archive_sha256 != expected_sha256:
        return False
    return bool(
        _validate_dataset_runtime(dataset_root, deep_checkpoint=True)
        and _runtime_manifest_is_valid(marker["runtime_files"], dataset_root)
    )


def _write_ready_marker(
    dataset_root: Path,
    *,
    expected_md5: str,
    dataset_id: str,
    dataset_name: str,
    archive_sha256: str | None = None,
    legacy_marker_migrated: bool = False,
) -> None:
    if not _validate_dataset_runtime(dataset_root, deep_checkpoint=True):
        raise RuntimeError("DentalSegmentator runtime validation failed before marker publication")
    verified_archive_sha256 = (
        archive_sha256
        if not legacy_marker_migrated
        and archive_sha256 is not None
        and archive_sha256 == _expected_archive_sha256(expected_md5)
        else None
    )
    if archive_sha256 is not None and verified_archive_sha256 is None:
        raise RuntimeError("DentalSegmentator archive SHA-256 provenance is not validated")
    marker = {
        "schema": MODEL_STATUS_SCHEMA,
        "model_state": "ready",
        "expected_md5": expected_md5,
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "runtime_files": _runtime_manifest(dataset_root),
        "archive_md5_verified": not legacy_marker_migrated,
        "archive_sha256": verified_archive_sha256,
        "archive_sha256_verified": verified_archive_sha256 is not None,
        "sha256_provenance": (
            MODEL_ARCHIVE_SHA256_PROVENANCE if verified_archive_sha256 is not None else None
        ),
        "legacy_marker_migrated": legacy_marker_migrated,
        "integrity_manifest_source": (
            "validated-legacy-marker-and-pytorch-zip-crc"
            if legacy_marker_migrated
            else "verified-archive-and-installed-runtime"
        ),
        "validated_at": datetime.now(UTC).isoformat(),
    }
    write_model_metadata(dataset_root / READY_MARKER_FILENAME, marker)


def _publish_staged_dataset(staged_dataset: Path, destination: Path) -> None:
    if staged_dataset.is_symlink() or not staged_dataset.is_dir():
        raise RuntimeError("DentalSegmentator staged dataset is not a regular directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise RuntimeError("DentalSegmentator dataset target is not a regular directory")
    backup = destination.with_name(f".{destination.name}.previous-{uuid4().hex}")
    replaced_existing = False
    try:
        if destination.exists():
            destination.replace(backup)
            replaced_existing = True
        staged_dataset.replace(destination)
    except Exception:
        if replaced_existing and backup.is_dir() and not destination.exists():
            backup.replace(destination)
        raise
    if backup.exists():
        _remove_directory(backup)


def _required_runtime_files(dataset_root: Path) -> list[tuple[str, Path]]:
    trainer = dataset_root / DEFAULT_TRAINER_DIR
    paths = (
        trainer / "dataset.json",
        trainer / "plans.json",
        trainer / "fold_0" / "checkpoint_final.pth",
    )
    return [(path.relative_to(dataset_root).as_posix(), path) for path in paths]


def _validate_dataset_runtime(dataset_root: Path, *, deep_checkpoint: bool = False) -> bool:
    if dataset_root.is_symlink() or not dataset_root.is_dir():
        return False
    try:
        required = _required_runtime_files(dataset_root)
        for _relative, path in required:
            _reject_symlink_components(path, dataset_root)
            if not _is_regular_file(path) or path.stat().st_size <= 0:
                return False
        dataset_json = _read_json_if_exists(required[0][1])
        plans_json = _read_json_if_exists(required[1][1])
        if dataset_json is None or plans_json is None:
            return False
        return not deep_checkpoint or _pytorch_checkpoint_zip_is_valid(required[2][1])
    except (OSError, RuntimeError):
        return False


def _pytorch_checkpoint_zip_is_valid(path: Path) -> bool:
    if not _is_regular_file(path):
        return False
    try:
        file_size = path.stat().st_size
        if file_size <= 0 or file_size > MAX_MODEL_ARCHIVE_BYTES or not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as checkpoint:
            infos = checkpoint.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_MEMBERS:
                return False
            names: set[str] = set()
            by_name: dict[str, zipfile.ZipInfo] = {}
            total_uncompressed = 0
            for info in infos:
                normalized = info.filename.rstrip("/")
                member = PurePosixPath(normalized)
                mode = (info.external_attr >> 16) & 0xFFFF
                if (
                    not normalized
                    or "\\" in info.filename
                    or member.is_absolute()
                    or ".." in member.parts
                    or member.as_posix() != normalized
                    or normalized in names
                    or (mode and stat.S_ISLNK(mode))
                    or info.flag_bits & 0x1
                    or info.file_size < 0
                ):
                    return False
                names.add(normalized)
                by_name[normalized] = info
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_MODEL_ARCHIVE_BYTES:
                    return False

            data_pickles = [name for name in names if PurePosixPath(name).name == "data.pkl"]
            if len(data_pickles) != 1:
                return False
            data_pickle = data_pickles[0]
            prefix = PurePosixPath(data_pickle).parent
            version_name = str(prefix / "version")
            data_prefix = (prefix / "data").parts
            tensor_members = [
                info
                for name, info in by_name.items()
                if PurePosixPath(name).parts[: len(data_prefix)] == data_prefix
                and len(PurePosixPath(name).parts) == len(data_prefix) + 1
                and info.file_size > 0
            ]
            if (
                by_name[data_pickle].file_size <= 0
                or version_name not in by_name
                or by_name[version_name].file_size <= 0
                or not tensor_members
            ):
                return False
            # CRC-check every member without unpickling checkpoint metadata.
            return checkpoint.testzip() is None
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return False


def _runtime_manifest(dataset_root: Path) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for relative, path in _required_runtime_files(dataset_root):
        _reject_symlink_components(path, dataset_root)
        if not _is_regular_file(path):
            raise RuntimeError(f"DentalSegmentator runtime file is not regular: {relative}")
        manifest.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    return manifest


def _runtime_manifest_is_valid(manifest: list[Any], dataset_root: Path) -> bool:
    required = _required_runtime_files(dataset_root)
    if len(manifest) != len(required):
        return False
    for entry, (relative, path) in zip(manifest, required, strict=True):
        if not isinstance(entry, dict) or set(entry) != {"path", "size_bytes", "sha256"}:
            return False
        if entry.get("path") != relative:
            return False
        size = entry.get("size_bytes")
        sha256 = entry.get("sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            return False
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            return False
        try:
            _reject_symlink_components(path, dataset_root)
            if not _is_regular_file(path) or path.stat().st_size != size:
                return False
            if file_sha256(path) != sha256:
                return False
        except (OSError, RuntimeError):
            return False
    return True


def _legacy_dataset_can_be_migrated(
    marker: dict[str, Any] | None,
    *,
    dataset_root: Path,
    expected_md5: str,
    dataset_id: str,
    dataset_name: str,
) -> bool:
    return bool(
        marker
        and "runtime_files" not in marker
        and marker.get("schema") == MODEL_STATUS_SCHEMA
        and marker.get("model_state") == "ready"
        and marker.get("expected_md5") == expected_md5
        and marker.get("dataset_id") == dataset_id
        and marker.get("dataset_name") == dataset_name
        and _validate_dataset_runtime(dataset_root, deep_checkpoint=True)
    )


def _validate_model_archive(path: Path, *, dataset_name: str) -> None:
    if not _is_regular_file(path) or path.stat().st_size <= 0:
        raise RuntimeError("DentalSegmentator model archive is not a regular non-empty file")
    if path.stat().st_size > MAX_MODEL_ARCHIVE_BYTES:
        raise RuntimeError("DentalSegmentator model archive exceeds the operational safety limit")
    try:
        if not zipfile.is_zipfile(path):
            raise RuntimeError("DentalSegmentator model archive is not a valid ZIP file")
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_MEMBERS:
                raise RuntimeError("DentalSegmentator archive has an unsafe member count")
            names: set[str] = set()
            by_name: dict[str, zipfile.ZipInfo] = {}
            total_uncompressed = 0
            for info in infos:
                normalized = info.filename.rstrip("/")
                member = PurePosixPath(normalized)
                mode = (info.external_attr >> 16) & 0xFFFF
                if (
                    not normalized
                    or "\\" in info.filename
                    or member.is_absolute()
                    or ".." in member.parts
                    or member.as_posix() != normalized
                    or normalized in names
                    or (mode and stat.S_ISLNK(mode))
                    or info.flag_bits & 0x1
                    or info.file_size < 0
                ):
                    raise RuntimeError("DentalSegmentator archive contains an unsafe member")
                names.add(normalized)
                by_name[normalized] = info
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_EXTRACTED_ARCHIVE_BYTES:
                    raise RuntimeError(
                        "DentalSegmentator archive exceeds the extraction safety limit"
                    )

            trainer = PurePosixPath(dataset_name) / DEFAULT_TRAINER_DIR
            required = (
                str(trainer / "dataset.json"),
                str(trainer / "plans.json"),
                str(trainer / "fold_0" / "checkpoint_final.pth"),
            )
            if any(name not in by_name or by_name[name].file_size <= 0 for name in required):
                raise RuntimeError(
                    "DentalSegmentator archive is missing the expected Dataset112 runtime files"
                )
            if archive.testzip() is not None:
                raise RuntimeError("DentalSegmentator archive failed ZIP CRC validation")
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise RuntimeError("DentalSegmentator model archive is not a valid ZIP file") from exc


@contextmanager
def _exclusive_setup_lock(model_root: Path) -> Iterator[None]:
    model_root.mkdir(parents=True, exist_ok=True)
    lock_path = model_root / ".dentalsegmentator-setup.lock"
    if lock_path.is_symlink() or (lock_path.exists() and not _is_regular_file(lock_path)):
        raise RuntimeError("DentalSegmentator setup lock is not a regular file")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        lock_stat = os.fstat(descriptor)
        path_stat = lock_path.lstat()
        if not stat.S_ISREG(lock_stat.st_mode):
            raise RuntimeError("DentalSegmentator setup lock is not a regular file")
        if (lock_stat.st_dev, lock_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
            raise RuntimeError("DentalSegmentator setup lock changed while opening")
        if lock_stat.st_uid != os.geteuid():
            raise RuntimeError("DentalSegmentator setup lock is not owned by the current user")
        if lock_stat.st_nlink != 1:
            raise RuntimeError("DentalSegmentator setup lock has an unsafe hard-link count")
        if lock_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise RuntimeError("DentalSegmentator setup lock has unsafe write permissions")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DentalSegmentatorSetupBusyError(
                "Another DentalSegmentator model preparation is already running."
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _recover_orphaned_install_state(
    *,
    model_root: Path,
    nnunet_results: Path,
    expected_md5: str,
    dataset_id: str,
    dataset_name: str,
) -> None:
    if model_root.is_symlink() or not model_root.is_dir():
        raise RuntimeError("DentalSegmentator model root is not a regular directory")
    if nnunet_results.is_symlink() or not nnunet_results.is_dir():
        raise RuntimeError("DentalSegmentator nnUNet_results is not a regular directory")

    destination = nnunet_results / dataset_name
    if destination.is_symlink():
        raise RuntimeError("DentalSegmentator dataset target is a symlink")
    if destination.exists() and not destination.is_dir():
        raise RuntimeError("DentalSegmentator dataset target is not a regular directory")

    backup_candidates = [nnunet_results / f"{dataset_name}.previous"]
    backup_candidates.extend(sorted(nnunet_results.glob(f".{dataset_name}.previous-*")))
    backups: list[Path] = []
    for backup in backup_candidates:
        if not (backup.exists() or backup.is_symlink()) or backup in backups:
            continue
        if backup.is_symlink() or not backup.is_dir():
            raise RuntimeError("DentalSegmentator recovery backup is not a regular directory")
        backups.append(backup)

    destination_score = _dataset_recovery_score(
        destination,
        expected_md5=expected_md5,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
    )
    backup_scores = {
        backup: _dataset_recovery_score(
            backup,
            expected_md5=expected_md5,
            dataset_id=dataset_id,
            dataset_name=dataset_name,
        )
        for backup in backups
    }
    highest_backup_score = max(backup_scores.values(), default=0)
    if highest_backup_score > destination_score:
        best_backups = [path for path, score in backup_scores.items() if score == highest_backup_score]
        if len(best_backups) != 1:
            raise RuntimeError(
                "Multiple equally valid DentalSegmentator recovery backups require manual review"
            )
        selected = best_backups[0]
        invalid_destination: Path | None = None
        if destination.exists():
            invalid_destination = destination.with_name(
                f".{dataset_name}.invalid-{uuid4().hex}"
            )
            destination.replace(invalid_destination)
        try:
            selected.replace(destination)
        except Exception:
            if (
                invalid_destination is not None
                and invalid_destination.exists()
                and not destination.exists()
            ):
                invalid_destination.replace(destination)
            raise
        if invalid_destination is not None and invalid_destination.exists():
            _remove_directory(invalid_destination)
    for backup in backups:
        if backup.exists():
            _remove_directory(backup)

    staging_directories = sorted(model_root.glob(".dentalsegmentator-staging-*"))
    for staging in staging_directories:
        if staging.is_symlink() or not staging.is_dir():
            raise RuntimeError("Orphaned DentalSegmentator staging target is not a regular directory")
    destination_marker = _read_json_if_exists(destination / READY_MARKER_FILENAME)
    if _is_complete_marker(
        destination_marker,
        dataset_root=destination,
        expected_md5=expected_md5,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
    ):
        for staging in staging_directories:
            _remove_directory(staging)
        return

    recoverable_staging = [
        staging
        for staging in staging_directories
        if _staging_install_is_complete(
            staging,
            expected_md5=expected_md5,
            dataset_id=dataset_id,
            dataset_name=dataset_name,
        )
    ]
    if len(recoverable_staging) > 1:
        raise RuntimeError(
            "Multiple complete DentalSegmentator staging installs require manual review"
        )
    if len(recoverable_staging) == 1:
        staged_dataset = recoverable_staging[0] / "nnUNet_results" / dataset_name
        _publish_staged_dataset(staged_dataset, destination)
    for staging in staging_directories:
        if staging.exists():
            _remove_directory(staging)


def _dataset_recovery_score(
    dataset_root: Path,
    *,
    expected_md5: str,
    dataset_id: str,
    dataset_name: str,
) -> int:
    if dataset_root.is_symlink() or not dataset_root.is_dir():
        return 0
    marker = _read_json_if_exists(dataset_root / READY_MARKER_FILENAME)
    if _is_complete_marker(
        marker,
        dataset_root=dataset_root,
        expected_md5=expected_md5,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
    ):
        return 2
    return 1 if _validate_dataset_runtime(dataset_root, deep_checkpoint=True) else 0


def _staging_install_is_complete(
    staging: Path,
    *,
    expected_md5: str,
    dataset_id: str,
    dataset_name: str,
) -> bool:
    metadata_payload = _read_json_if_exists(staging / STAGING_METADATA_FILENAME)
    expected_sha256 = _expected_archive_sha256(expected_md5)
    if not (
        metadata_payload
        and set(metadata_payload)
        == {"schema", "expected_md5", "dataset_id", "dataset_name", "archive_sha256"}
        and metadata_payload.get("schema")
        == "totalsegmentator_wrapper_mac.dentalsegmentator_staging.v1"
        and metadata_payload.get("expected_md5") == expected_md5
        and metadata_payload.get("dataset_id") == dataset_id
        and metadata_payload.get("dataset_name") == dataset_name
        and metadata_payload.get("archive_sha256") == expected_sha256
    ):
        return False
    dataset_root = staging / "nnUNet_results" / dataset_name
    marker = _read_json_if_exists(dataset_root / READY_MARKER_FILENAME)
    return _is_complete_marker(
        marker,
        dataset_root=dataset_root,
        expected_md5=expected_md5,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
    )


def _prepare_file_target(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink() or (expanded.exists() and not _is_regular_file(expanded)):
        raise RuntimeError(f"{label} must be a regular file target")
    parent = expanded.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise RuntimeError(f"{label} parent must be a regular directory")
    return expanded.resolve()


def _prepare_directory_target(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink() or (expanded.exists() and not expanded.is_dir()):
        raise RuntimeError(f"{label} must be a regular directory target")
    parent = expanded.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise RuntimeError(f"{label} parent must be a regular directory")
    return expanded.resolve()


def _validate_https_url(url: str, *, label: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} URL is invalid") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise RuntimeError(f"{label} URL must use HTTPS on the standard port without credentials")


def _ensure_regular_or_absent(path: Path, *, label: str) -> None:
    if path.is_symlink() or (path.exists() and not _is_regular_file(path)):
        raise RuntimeError(f"{label} must be a regular file")


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _reject_symlink_components(path: Path, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("DentalSegmentator runtime path escapes the dataset root") from exc
    current = root
    if current.is_symlink():
        raise RuntimeError("DentalSegmentator runtime path contains a symlink")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError("DentalSegmentator runtime path contains a symlink")


def _atomic_publish_file(source: Path, destination: Path) -> None:
    if not _is_regular_file(source):
        raise RuntimeError("DentalSegmentator publish source is not a regular file")
    parent = destination.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise RuntimeError("DentalSegmentator publish target parent is not a regular directory")
    parent.mkdir(parents=True, exist_ok=True)
    _ensure_regular_or_absent(destination, label="DentalSegmentator publish target")
    source.replace(destination)


def _remove_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("Refusing to remove a non-directory DentalSegmentator artifact")
    shutil.rmtree(path)


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
    parser.add_argument("--progress-log", type=Path)
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
            progress_log=args.progress_log,
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
