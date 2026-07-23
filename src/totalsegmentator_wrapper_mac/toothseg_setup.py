from __future__ import annotations

import hashlib
import json
import shutil
import time
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4


MODEL_LICENSE = "CC-BY-4.0"
MODEL_DOI = "10.5281/zenodo.14893540"
MODEL_SOURCE = "Zenodo"
MODEL_STATUS_SCHEMA = "totalsegmentator_wrapper_mac.toothseg_model_status.v1"
MODEL_SETUP_SCHEMA = "totalsegmentator_wrapper_mac.toothseg_model_setup.v1"
PREP_PROGRESS_PREFIX = "TOOTHSEG_PREP_PROGRESS "
READY_MARKER_FILENAME = ".toothseg_model_ready.json"
CHUNK_SIZE = 1024 * 1024

SEMANTIC_DATASET_ID = "121"
SEMANTIC_DATASET_NAME = "Dataset121_ToothFairy2_Teeth"
SEMANTIC_TRAINER_DIR = (
    "nnUNetTrainer_onlyMirror01_DASegOrd0__nnUNetPlans__"
    "3d_fullres_resample_torch_256_bs8_ctnorm"
)
INSTANCE_DATASET_ID = "123"
INSTANCE_DATASET_NAME = "Dataset123_ToothFairy2fixed_teeth_spacing02_brd3px"
INSTANCE_TRAINER_DIR = "nnUNetTrainer__nnUNetPlans__3d_fullres_resample_torch_192_bs8_ctnorm"
PAIR_DISTRIBUTIONS_FILENAME = "fdi_pair_distrs.json"
PAIR_DISTRIBUTIONS_URL = (
    "https://raw.githubusercontent.com/MIC-DKFZ/ToothSeg/"
    "b29d1017fa124f89645fa56f98649e3f3f43bdb0/"
    "toothseg/datasets/toothfairy2/fdi_pair_distrs.json"
)
PAIR_DISTRIBUTIONS_SHA256 = "82ab04892277d36013be5ba9763ac334ea073fca7ebe8679086f1e33ed64ff29"
SEMANTIC_MPS_PATCH_SIZE = (192, 192, 192)

BRANCHES = (
    (SEMANTIC_DATASET_ID, SEMANTIC_DATASET_NAME, SEMANTIC_TRAINER_DIR),
    (INSTANCE_DATASET_ID, INSTANCE_DATASET_NAME, INSTANCE_TRAINER_DIR),
)


def toothseg_model_status(
    *,
    model_root: Path,
    expected_md5: str,
    expected_pair_distributions_sha256: str = PAIR_DISTRIBUTIONS_SHA256,
    model_zip: Path | None = None,
    nnunet_results: Path | None = None,
) -> dict[str, Any]:
    model_root = model_root.expanduser().resolve()
    model_zip = (model_zip or model_root / "ToothSeg.zip").expanduser().resolve()
    nnunet_results = (nnunet_results or model_root / "nnUNet_results").expanduser().resolve()
    marker_path = nnunet_results / READY_MARKER_FILENAME
    payload = _status_payload()

    marker = _read_json(marker_path)
    if _marker_is_ready(
        marker,
        expected_md5=expected_md5,
        expected_pair_distributions_sha256=expected_pair_distributions_sha256,
        nnunet_results=nnunet_results,
    ):
        payload.update({"status": "ready", "model_state": "ready"})
        return payload

    partial = model_zip.with_name(model_zip.name + ".part")
    partial_metadata = partial.with_name(partial.name + ".json")
    partial_state = _read_json(partial_metadata)
    resumable_partial = bool(
        partial.exists()
        and partial.stat().st_size > 0
        and partial_state
        and partial_state.get("expected_md5") == expected_md5
        and isinstance(partial_state.get("url"), str)
    )
    staging_present = any(model_root.glob(".toothseg-staging-*"))
    installed_present = any((nnunet_results / dataset).exists() for _id, dataset, _trainer in BRANCHES)
    if marker is not None or resumable_partial or model_zip.exists() or staging_present or installed_present:
        payload.update({"status": "resumable", "model_state": "resumable"})
        return payload

    metadata = _read_json(model_root / "toothseg_model.json")
    if metadata and metadata.get("status") == "failed":
        payload.update(
            {
                "status": "failed",
                "model_state": "failed",
                **_safe_error("model_prepare_failed", "The previous ToothSeg preparation attempt did not complete."),
            }
        )
    return payload


def install_toothseg_model(
    *,
    model_url: str,
    model_zip: Path,
    expected_md5: str,
    nnunet_results: Path,
    pair_distributions_url: str = PAIR_DISTRIBUTIONS_URL,
    pair_distributions_sha256: str = PAIR_DISTRIBUTIONS_SHA256,
    timeout_sec: int = 7200,
) -> dict[str, Any]:
    started = time.perf_counter()
    model_zip = model_zip.expanduser().resolve()
    model_root = model_zip.parent
    nnunet_results = nnunet_results.expanduser().resolve()
    model_root.mkdir(parents=True, exist_ok=True)
    nnunet_results.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "schema": MODEL_SETUP_SCHEMA,
        "status": "success",
        "model_state": "not_installed",
        "source": MODEL_SOURCE,
        "model_url": model_url,
        "model_zip": str(model_zip),
        "expected_md5": expected_md5,
        "nnUNet_results": str(nnunet_results),
        "dataset_ids": [branch[0] for branch in BRANCHES],
        "dataset_names": [branch[1] for branch in BRANCHES],
        "license": MODEL_LICENSE,
        "doi": MODEL_DOI,
        "downloaded": False,
        "installed": False,
        "archive_removed_after_install": False,
        "semantic_mps_patch_size": list(SEMANTIC_MPS_PATCH_SIZE),
        "elapsed_seconds": None,
    }

    status = toothseg_model_status(
        model_root=model_root,
        expected_md5=expected_md5,
        expected_pair_distributions_sha256=pair_distributions_sha256,
        model_zip=model_zip,
        nnunet_results=nnunet_results,
    )
    if status["model_state"] == "ready":
        result.update(
            {
                "model_state": "ready",
                "md5_verified": True,
                "actual_md5": expected_md5,
                "skipped_reason": "model_already_installed",
                "elapsed_seconds": time.perf_counter() - started,
            }
        )
        _write_json(model_root / "toothseg_model.json", result)
        return result

    staging_root = model_root / f".toothseg-staging-{uuid4().hex}"
    try:
        if _existing_checkpoints_are_trusted(nnunet_results, expected_md5=expected_md5):
            _emit_prepare_progress("verify", "running", "既存のToothSegモデルを確認しています。")
            _apply_semantic_mps_plan(nnunet_results)
            _validate_results(nnunet_results)
            installed_distributions = model_root / PAIR_DISTRIBUTIONS_FILENAME
            if not (
                installed_distributions.is_file()
                and _file_sha256(installed_distributions) == pair_distributions_sha256
            ):
                _download_with_sha256(
                    pair_distributions_url,
                    installed_distributions,
                    expected_sha256=pair_distributions_sha256,
                    timeout_sec=timeout_sec,
                )
            _write_ready_marker(
                nnunet_results,
                expected_md5=expected_md5,
                pair_distributions_sha256=pair_distributions_sha256,
            )
            archive_was_present = model_zip.exists()
            model_zip.unlink(missing_ok=True)
            result.update(
                {
                    "installed": True,
                    "model_state": "ready",
                    "reused_existing_checkpoints": True,
                    "md5_verified": True,
                    "actual_md5": expected_md5,
                    "archive_removed_after_install": archive_was_present,
                    "elapsed_seconds": time.perf_counter() - started,
                }
            )
            _write_json(model_root / "toothseg_model.json", result)
            _emit_prepare_progress("complete", "success", "ToothSegモデルの準備が完了しました。", percent=100)
            return result

        if not model_zip.exists() or _file_md5(model_zip) != expected_md5:
            _download_with_md5(model_url, model_zip, expected_md5=expected_md5, timeout_sec=timeout_sec)
            result["downloaded"] = True
        actual_md5 = _file_md5(model_zip)
        result["actual_md5"] = actual_md5
        if actual_md5 != expected_md5:
            raise RuntimeError(f"ToothSeg model md5 mismatch: expected {expected_md5}, got {actual_md5}")
        result["md5_verified"] = True

        _emit_prepare_progress("extract", "running", "ToothSegモデルを展開しています。")
        staged_results = staging_root / "nnUNet_results"
        _extract_runtime_files(model_zip, staged_results)
        _validate_results(staged_results)
        distributions_path = staging_root / PAIR_DISTRIBUTIONS_FILENAME
        _download_with_sha256(
            pair_distributions_url,
            distributions_path,
            expected_sha256=pair_distributions_sha256,
            timeout_sec=timeout_sec,
        )
        for _dataset_id, dataset_name, _trainer in BRANCHES:
            _publish_staged_dataset(staged_results / dataset_name, nnunet_results / dataset_name)
        distributions_path.replace(model_root / PAIR_DISTRIBUTIONS_FILENAME)
        _write_ready_marker(
            nnunet_results,
            expected_md5=expected_md5,
            pair_distributions_sha256=pair_distributions_sha256,
        )
        model_zip.unlink(missing_ok=True)
        result.update(
            {
                "installed": True,
                "model_state": "ready",
                "archive_removed_after_install": True,
                "elapsed_seconds": time.perf_counter() - started,
            }
        )
        _write_json(model_root / "toothseg_model.json", result)
        _emit_prepare_progress("complete", "success", "ToothSegモデルの準備が完了しました。", percent=100)
        return result
    except Exception:  # noqa: BLE001
        result.update(
            {
                "status": "failed",
                "model_state": "failed",
                "elapsed_seconds": time.perf_counter() - started,
                **_safe_error("model_prepare_failed", "ToothSeg model preparation did not complete."),
            }
        )
        _write_json(model_root / "toothseg_model.json", result)
        raise
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)


def _extract_runtime_files(model_zip: Path, staged_results: Path) -> None:
    wanted: dict[str, Path] = {}
    for _dataset_id, dataset_name, trainer in BRANCHES:
        prefix = PurePosixPath("ToothSeg") / dataset_name / trainer
        for relative in ("dataset.json", "plans.json", "fold_5/checkpoint_final.pth"):
            member = str(prefix / relative)
            wanted[member] = staged_results / dataset_name / trainer / relative

    with zipfile.ZipFile(model_zip) as archive:
        names = set()
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"unsafe ToothSeg archive member: {info.filename}")
            names.add(info.filename.rstrip("/"))
        missing = sorted(set(wanted) - names)
        if missing:
            raise RuntimeError(f"ToothSeg archive is missing required runtime files: {missing}")
        for member, destination in wanted.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, length=CHUNK_SIZE)
    _apply_semantic_mps_plan(staged_results)


def _validate_results(nnunet_results: Path) -> None:
    for _dataset_id, dataset_name, trainer in BRANCHES:
        root = nnunet_results / dataset_name / trainer
        required = (root / "dataset.json", root / "plans.json", root / "fold_5" / "checkpoint_final.pth")
        if any(not path.is_file() or path.stat().st_size == 0 for path in required):
            raise RuntimeError(f"ToothSeg model extraction did not produce a valid {dataset_name} dataset")
    semantic_plan = _semantic_plan_path(nnunet_results)
    plan = _read_json(semantic_plan)
    configuration = (plan or {}).get("configurations", {}).get(
        "3d_fullres_resample_torch_256_bs8_ctnorm",
        {},
    )
    if configuration.get("patch_size") != list(SEMANTIC_MPS_PATCH_SIZE):
        raise RuntimeError("ToothSeg semantic plan is not adapted to the validated MPS patch size")


def _runtime_files_present(nnunet_results: Path) -> bool:
    for _dataset_id, dataset_name, trainer in BRANCHES:
        root = nnunet_results / dataset_name / trainer
        required = (root / "dataset.json", root / "plans.json", root / "fold_5" / "checkpoint_final.pth")
        if any(not path.is_file() or path.stat().st_size == 0 for path in required):
            return False
    return True


def _existing_checkpoints_are_trusted(nnunet_results: Path, *, expected_md5: str) -> bool:
    marker = _read_json(nnunet_results / READY_MARKER_FILENAME)
    return bool(
        _runtime_files_present(nnunet_results)
        and marker
        and marker.get("schema") == MODEL_STATUS_SCHEMA
        and marker.get("model_state") == "ready"
        and marker.get("expected_md5") == expected_md5
        and marker.get("dataset_names") == [branch[1] for branch in BRANCHES]
    )


def _semantic_plan_path(nnunet_results: Path) -> Path:
    return nnunet_results / SEMANTIC_DATASET_NAME / SEMANTIC_TRAINER_DIR / "plans.json"


def _apply_semantic_mps_plan(nnunet_results: Path) -> None:
    plan_path = _semantic_plan_path(nnunet_results)
    plan = _read_json(plan_path)
    configurations = (plan or {}).get("configurations")
    configuration_name = "3d_fullres_resample_torch_256_bs8_ctnorm"
    if not isinstance(configurations, dict) or not isinstance(configurations.get(configuration_name), dict):
        raise RuntimeError("ToothSeg semantic plans.json is missing the published inference configuration")
    configurations[configuration_name]["patch_size"] = list(SEMANTIC_MPS_PATCH_SIZE)
    _write_json(plan_path, plan)


def _marker_is_ready(
    marker: dict[str, Any] | None,
    *,
    expected_md5: str,
    expected_pair_distributions_sha256: str,
    nnunet_results: Path,
) -> bool:
    if not (
        marker
        and marker.get("schema") == MODEL_STATUS_SCHEMA
        and marker.get("model_state") == "ready"
        and marker.get("expected_md5") == expected_md5
        and marker.get("pair_distributions_sha256") == expected_pair_distributions_sha256
        and marker.get("semantic_mps_patch_size") == list(SEMANTIC_MPS_PATCH_SIZE)
    ):
        return False
    try:
        _validate_results(nnunet_results)
    except RuntimeError:
        return False
    distributions = nnunet_results.parent / PAIR_DISTRIBUTIONS_FILENAME
    return bool(
        distributions.is_file()
        and _file_sha256(distributions) == expected_pair_distributions_sha256
    )


def _write_ready_marker(
    nnunet_results: Path,
    *,
    expected_md5: str,
    pair_distributions_sha256: str,
) -> None:
    _write_json(
        nnunet_results / READY_MARKER_FILENAME,
        {
            "schema": MODEL_STATUS_SCHEMA,
            "model_state": "ready",
            "expected_md5": expected_md5,
            "pair_distributions_sha256": pair_distributions_sha256,
            "semantic_mps_patch_size": list(SEMANTIC_MPS_PATCH_SIZE),
            "dataset_ids": [branch[0] for branch in BRANCHES],
            "dataset_names": [branch[1] for branch in BRANCHES],
            "validated_at": datetime.now(UTC).isoformat(),
        },
    )


def _download_with_md5(url: str, destination: Path, *, expected_md5: str, timeout_sec: int) -> None:
    partial = destination.with_name(destination.name + ".part")
    metadata_path = partial.with_name(partial.name + ".json")
    state = _read_json(metadata_path)
    can_resume = bool(
        partial.exists()
        and partial.stat().st_size > 0
        and state == {"url": url, "expected_md5": expected_md5}
    )
    if not can_resume:
        partial.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
    _write_json(metadata_path, {"url": url, "expected_md5": expected_md5})
    offset = partial.stat().st_size if can_resume else 0
    digest = hashlib.md5()  # noqa: S324 - Zenodo publishes MD5 for file integrity.
    if can_resume:
        with partial.open("rb") as existing:
            for chunk in iter(lambda: existing.read(CHUNK_SIZE), b""):
                digest.update(chunk)
    request: str | urllib.request.Request = url
    if can_resume:
        request = urllib.request.Request(url, headers={"Range": f"bytes={offset}-"})
    print(f"Downloading ToothSeg model from {url}")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:  # noqa: S310
        if can_resume and not _valid_range_response(response, offset):
            partial.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            return _download_with_md5(url, destination, expected_md5=expected_md5, timeout_sec=timeout_sec)
        total_bytes = _response_total_bytes(response, offset=offset)
        downloaded = offset
        transfer_started = time.perf_counter()
        last_emitted = 0.0
        _emit_download_progress(downloaded, total_bytes, rate_bps=None, eta_seconds=None, resumed=can_resume)
        with partial.open("ab" if can_resume else "wb") as output:
            while chunk := response.read(CHUNK_SIZE):
                output.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                now = time.perf_counter()
                transfer_elapsed = max(now - transfer_started, 1e-6)
                rate_bps = (downloaded - offset) / transfer_elapsed
                eta_seconds = (
                    max(0.0, (total_bytes - downloaded) / rate_bps)
                    if total_bytes is not None and rate_bps > 0
                    else None
                )
                if now - last_emitted >= 0.25 or (total_bytes is not None and downloaded >= total_bytes):
                    _emit_download_progress(
                        downloaded,
                        total_bytes,
                        rate_bps=rate_bps,
                        eta_seconds=eta_seconds,
                        resumed=can_resume,
                    )
                    last_emitted = now
    actual_md5 = digest.hexdigest()
    if actual_md5 != expected_md5:
        partial.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        raise RuntimeError(f"ToothSeg model download md5 mismatch: expected {expected_md5}, got {actual_md5}")
    partial.replace(destination)
    metadata_path.unlink(missing_ok=True)


def _valid_range_response(response: Any, offset: int) -> bool:
    status = getattr(response, "status", None) or response.getcode()
    content_range = response.headers.get("Content-Range") if response.headers else None
    return status == 206 and isinstance(content_range, str) and content_range.startswith(f"bytes {offset}-")


def _response_total_bytes(response: Any, *, offset: int) -> int | None:
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    content_range = headers.get("Content-Range")
    if isinstance(content_range, str) and "/" in content_range:
        total_text = content_range.rsplit("/", 1)[-1]
        if total_text.isdigit():
            return int(total_text)
    content_length = headers.get("Content-Length")
    if isinstance(content_length, str) and content_length.isdigit():
        return offset + int(content_length)
    return None


def _emit_download_progress(
    downloaded_bytes: int,
    total_bytes: int | None,
    *,
    rate_bps: float | None,
    eta_seconds: float | None,
    resumed: bool,
) -> None:
    percent = (
        max(0, min(100, round(downloaded_bytes * 100 / total_bytes)))
        if total_bytes and total_bytes > 0
        else None
    )
    _emit_prepare_progress(
        "download",
        "running",
        "ToothSegモデルをダウンロードしています。",
        downloaded_bytes=downloaded_bytes,
        total_bytes=total_bytes,
        percent=percent,
        rate_bps=rate_bps,
        eta_seconds=eta_seconds,
        resumed=resumed,
    )


def _emit_prepare_progress(stage: str, status: str, message: str, **values: Any) -> None:
    payload = {"stage": stage, "status": status, "message": message, **values}
    print(PREP_PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _download_with_sha256(
    url: str,
    destination: Path,
    *,
    expected_sha256: str,
    timeout_sec: int,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=timeout_sec) as response:  # noqa: S310
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output, length=CHUNK_SIZE)
    actual = _file_sha256(destination)
    if actual != expected_sha256:
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            f"ToothSeg pair-distribution sha256 mismatch: expected {expected_sha256}, got {actual}"
        )


def _publish_staged_dataset(staged: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = destination.with_name(destination.name + ".previous")
    if backup.exists():
        shutil.rmtree(backup)
    replaced = False
    try:
        if destination.exists():
            destination.replace(backup)
            replaced = True
        staged.replace(destination)
    except Exception:
        if replaced and backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def _file_md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - Zenodo publishes MD5 for file integrity.
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _status_payload() -> dict[str, Any]:
    return {
        "schema": MODEL_STATUS_SCHEMA,
        "status": "not_installed",
        "model_state": "not_installed",
        "dataset_ids": [branch[0] for branch in BRANCHES],
        "dataset_names": [branch[1] for branch in BRANCHES],
        "error_code": None,
        "safe_reason": None,
        "mps_state": "not_applicable",
        "occurred_at": None,
    }


def _safe_error(code: str, reason: str) -> dict[str, str]:
    return {
        "error_code": code,
        "safe_reason": reason,
        "mps_state": "not_applicable",
        "occurred_at": datetime.now(UTC).isoformat(),
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
