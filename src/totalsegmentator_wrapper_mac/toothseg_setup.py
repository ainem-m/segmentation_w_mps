from __future__ import annotations

import hashlib
import errno
import fcntl
import json
import os
import re
import shutil
import stat
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from urllib.parse import urlsplit
from uuid import uuid4


MODEL_LICENSE = "CC-BY-4.0"
MODEL_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
MODEL_DOI = "10.5281/zenodo.14893540"
MODEL_SOURCE = "Zenodo"
MODEL_CREATORS = (
    "Fabian Isensee",
    "Niels van Nistelrooij",
    "Lars Krämer",
    "Shankeeth Vinayahalingam",
)
MODEL_CHECKPOINTS_MODIFIED = False
MODEL_STATUS_SCHEMA = "totalsegmentator_wrapper_mac.toothseg_model_status.v1"
MODEL_SETUP_SCHEMA = "totalsegmentator_wrapper_mac.toothseg_model_setup.v1"
PREP_PROGRESS_PREFIX = "TOOTHSEG_PREP_PROGRESS "
READY_MARKER_FILENAME = ".toothseg_model_ready.json"
STAGING_METADATA_FILENAME = ".toothseg_staging.json"
PARTIAL_DOWNLOAD_SCHEMA = "totalsegmentator_wrapper_mac.toothseg_partial_download.v1"
CHUNK_SIZE = 1024 * 1024
# Operational fail-closed ceiling. This is not a claim about the exact Zenodo asset size;
# it limits damage from a missing/forged HTTP length while leaving ample room for the model archive.
MAX_MODEL_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
# The pinned pair-distribution artifact is JSON. This ceiling is likewise a safety bound,
# not an assertion of its exact published size.
MAX_PAIR_DISTRIBUTIONS_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
_CONTENT_RANGE_PATTERN = re.compile(r"bytes (\d+)-(\d+)/(\d+)")

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


class ToothSegSetupBusyError(RuntimeError):
    """Raised when another process owns the ToothSeg preparation lock."""


def toothseg_model_status(
    *,
    model_root: Path,
    expected_md5: str,
    expected_pair_distributions_sha256: str = PAIR_DISTRIBUTIONS_SHA256,
    model_zip: Path | None = None,
    nnunet_results: Path | None = None,
) -> dict[str, Any]:
    payload = _status_payload()
    raw_model_root = model_root.expanduser()
    if raw_model_root.is_symlink() or (raw_model_root.exists() and not raw_model_root.is_dir()):
        payload.update(
            {
                "status": "failed",
                "model_state": "failed",
                **_safe_error("invalid_model_path", "The ToothSeg model root is not a regular directory."),
            }
        )
        return payload
    model_root = raw_model_root.resolve()
    raw_model_zip = (model_zip or model_root / "ToothSeg.zip").expanduser()
    raw_results = (nnunet_results or model_root / "nnUNet_results").expanduser()
    if raw_model_zip.is_symlink() or raw_results.is_symlink():
        payload.update(
            {
                "status": "failed",
                "model_state": "failed",
                **_safe_error("invalid_model_path", "A ToothSeg model target is a symbolic link."),
            }
        )
        return payload
    model_zip = raw_model_zip.resolve()
    nnunet_results = raw_results.resolve()
    marker_path = nnunet_results / READY_MARKER_FILENAME

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
        _is_regular_file(partial)
        and partial.stat().st_size > 0
        and partial_state
        and partial_state.get("expected_md5") == expected_md5
        and isinstance(partial_state.get("url"), str)
        and _partial_state_is_compatible(
            partial_state,
            url=partial_state["url"],
            expected_md5=expected_md5,
            partial_size=partial.stat().st_size,
        )
    )
    staging_present = any(model_root.glob(".toothseg-staging-*"))
    installed_present = any(
        (nnunet_results / dataset).exists() or (nnunet_results / dataset).is_symlink()
        for _id, dataset, _trainer in BRANCHES
    )
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
    """Install the pinned ToothSeg runtime under an exclusive process lock."""
    _validate_https_url(model_url, label="ToothSeg model")
    if (
        pair_distributions_url != PAIR_DISTRIBUTIONS_URL
        or pair_distributions_sha256 != PAIR_DISTRIBUTIONS_SHA256
    ):
        raise RuntimeError("ToothSeg pair-distribution source does not match the pinned URL and SHA-256")
    _validate_https_url(pair_distributions_url, label="ToothSeg pair distribution")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", expected_md5):
        raise RuntimeError("ToothSeg model MD5 must be a 32-character hexadecimal digest")

    model_zip = _prepare_file_target(model_zip, label="ToothSeg model archive")
    model_root = model_zip.parent
    nnunet_results = _prepare_directory_target(
        nnunet_results,
        label="ToothSeg nnUNet_results",
        create=False,
    )
    if nnunet_results.parent != model_root:
        raise RuntimeError("ToothSeg model archive and nnUNet_results must share one model root")
    model_root.mkdir(parents=True, exist_ok=True)
    with _exclusive_setup_lock(model_root):
        nnunet_results.mkdir(parents=True, exist_ok=True)
        _recover_orphaned_install_state(
            nnunet_results,
            expected_md5=expected_md5.lower(),
            expected_pair_distributions_sha256=pair_distributions_sha256.lower(),
        )
        return _install_toothseg_model_locked(
            model_url=model_url,
            model_zip=model_zip,
            expected_md5=expected_md5.lower(),
            nnunet_results=nnunet_results,
            pair_distributions_url=pair_distributions_url,
            pair_distributions_sha256=pair_distributions_sha256.lower(),
            timeout_sec=timeout_sec,
        )


def _install_toothseg_model_locked(
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
        "license_url": MODEL_LICENSE_URL,
        "doi": MODEL_DOI,
        "creators": list(MODEL_CREATORS),
        "checkpoints_modified": MODEL_CHECKPOINTS_MODIFIED,
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
        if _existing_checkpoints_are_trusted(
            nnunet_results,
            expected_md5=expected_md5,
            expected_pair_distributions_sha256=pair_distributions_sha256,
        ):
            existing_marker = _read_json(nnunet_results / READY_MARKER_FILENAME) or {}
            legacy_marker_migrated = not isinstance(existing_marker.get("runtime_files"), list)
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
                legacy_marker_migrated=legacy_marker_migrated,
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
        staging_root.mkdir(parents=True, exist_ok=False)
        _write_json(
            staging_root / STAGING_METADATA_FILENAME,
            {
                "schema": "totalsegmentator_wrapper_mac.toothseg_staging.v1",
                "expected_md5": expected_md5,
                "pair_distributions_sha256": pair_distributions_sha256,
            },
        )
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
        _atomic_publish_file(distributions_path, model_root / PAIR_DISTRIBUTIONS_FILENAME)
        _write_ready_marker(
            nnunet_results,
            expected_md5=expected_md5,
            pair_distributions_sha256=pair_distributions_sha256,
            legacy_marker_migrated=False,
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
    except Exception as exc:  # noqa: BLE001
        disk_full = isinstance(exc, OSError) and exc.errno == errno.ENOSPC
        result.update(
            {
                "status": "failed",
                "model_state": "failed",
                "elapsed_seconds": time.perf_counter() - started,
                **_safe_error(
                    "insufficient_disk_space" if disk_full else "model_prepare_failed",
                    (
                        "There is not enough free disk space to prepare the ToothSeg model."
                        if disk_full
                        else "ToothSeg model preparation did not complete."
                    ),
                ),
            }
        )
        _write_json(model_root / "toothseg_model.json", result)
        raise
    finally:
        if staging_root.exists():
            _remove_directory(staging_root)


def _extract_runtime_files(model_zip: Path, staged_results: Path) -> None:
    wanted: dict[str, Path] = {}
    for _dataset_id, dataset_name, trainer in BRANCHES:
        prefix = PurePosixPath("ToothSeg") / dataset_name / trainer
        for relative in ("dataset.json", "plans.json", "fold_5/checkpoint_final.pth"):
            member = str(prefix / relative)
            wanted[member] = staged_results / dataset_name / trainer / relative

    with zipfile.ZipFile(model_zip) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise RuntimeError("ToothSeg archive exceeds the member-count safety limit")
        names: set[str] = set()
        wanted_infos: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            path = PurePosixPath(info.filename)
            if "\\" in info.filename or path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"unsafe ToothSeg archive member: {info.filename}")
            normalized_name = info.filename.rstrip("/")
            if normalized_name in names and normalized_name in wanted:
                raise RuntimeError(f"duplicate ToothSeg archive member: {normalized_name}")
            names.add(normalized_name)
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and stat.S_ISLNK(mode):
                raise RuntimeError(f"unsafe ToothSeg archive symlink: {info.filename}")
            if info.flag_bits & 0x1:
                raise RuntimeError(f"encrypted ToothSeg archive member is not supported: {info.filename}")
            if normalized_name in wanted:
                if info.file_size <= 0:
                    raise RuntimeError(f"empty ToothSeg runtime archive member: {normalized_name}")
                if info.file_size > MAX_MODEL_ARCHIVE_BYTES:
                    raise RuntimeError("ToothSeg runtime file exceeds the extraction safety limit")
                wanted_infos[normalized_name] = info
        missing = sorted(set(wanted) - names)
        if missing:
            raise RuntimeError(f"ToothSeg archive is missing required runtime files: {missing}")
        total_uncompressed = sum(wanted_infos[member].file_size for member in wanted)
        if total_uncompressed > MAX_MODEL_ARCHIVE_BYTES:
            raise RuntimeError("ToothSeg runtime files exceed the extraction safety limit")
        _require_free_space(staged_results.parent, total_uncompressed)
        for member, destination in wanted.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            _ensure_regular_or_absent(destination, label="ToothSeg staged runtime file")
            written = 0
            with archive.open(wanted_infos[member]) as source, destination.open("xb") as output:
                while chunk := source.read(CHUNK_SIZE):
                    written += len(chunk)
                    if written > wanted_infos[member].file_size:
                        raise RuntimeError("ToothSeg archive member expanded beyond its declared size")
                    output.write(chunk)
            if written != wanted_infos[member].file_size:
                raise RuntimeError("ToothSeg archive member was truncated during extraction")
    _apply_semantic_mps_plan(staged_results)


def _validate_results(nnunet_results: Path) -> None:
    for _relative, path in _required_runtime_files(nnunet_results):
        _reject_symlink_components(path, nnunet_results)
        if not _is_regular_file(path) or path.stat().st_size == 0:
            dataset_name = path.relative_to(nnunet_results).parts[0]
            raise RuntimeError(f"ToothSeg model extraction did not produce a valid {dataset_name} dataset")
        _require_path_within(path, nnunet_results)
    semantic_plan = _semantic_plan_path(nnunet_results)
    plan = _read_json(semantic_plan)
    configuration = (plan or {}).get("configurations", {}).get(
        "3d_fullres_resample_torch_256_bs8_ctnorm",
        {},
    )
    if configuration.get("patch_size") != list(SEMANTIC_MPS_PATCH_SIZE):
        raise RuntimeError("ToothSeg semantic plan is not adapted to the validated MPS patch size")


def _runtime_files_present(nnunet_results: Path) -> bool:
    for _relative, path in _required_runtime_files(nnunet_results):
        try:
            _reject_symlink_components(path, nnunet_results)
        except RuntimeError:
            return False
        try:
            if not _is_regular_file(path) or path.stat().st_size <= 0:
                return False
        except OSError:
            return False
    return True


def _existing_checkpoints_are_trusted(
    nnunet_results: Path,
    *,
    expected_md5: str,
    expected_pair_distributions_sha256: str,
) -> bool:
    marker = _read_json(nnunet_results / READY_MARKER_FILENAME)
    if not (
        _runtime_files_present(nnunet_results)
        and marker
        and marker.get("schema") == MODEL_STATUS_SCHEMA
        and marker.get("model_state") == "ready"
        and marker.get("expected_md5") == expected_md5
        and marker.get("dataset_ids") == [branch[0] for branch in BRANCHES]
        and marker.get("dataset_names") == [branch[1] for branch in BRANCHES]
    ):
        return False
    manifest = marker.get("runtime_files")
    if isinstance(manifest, list):
        try:
            _validate_results(nnunet_results)
        except (OSError, RuntimeError):
            return False
        return _runtime_manifest_is_valid(manifest, nnunet_results)
    # Legacy ready markers predate the per-file manifest and the MPS plan patch marker.
    # Migration is allowed only after a deep, non-executing checkpoint ZIP/CRC check
    # plus JSON parsing. We never unpickle checkpoint content during this trust step.
    if not _legacy_runtime_tree_is_deeply_valid(nnunet_results):
        return False
    distributions = nnunet_results.parent / PAIR_DISTRIBUTIONS_FILENAME
    try:
        return (
            marker.get("pair_distributions_sha256") == expected_pair_distributions_sha256
            and _is_regular_file(distributions)
            and _file_sha256(distributions) == expected_pair_distributions_sha256
        )
    except (OSError, RuntimeError):
        return False


def _legacy_runtime_tree_is_deeply_valid(nnunet_results: Path) -> bool:
    for _dataset_id, dataset_name, trainer in BRANCHES:
        root = nnunet_results / dataset_name / trainer
        dataset_json = _read_json(root / "dataset.json")
        plans_json = _read_json(root / "plans.json")
        checkpoint = root / "fold_5" / "checkpoint_final.pth"
        if dataset_json is None or plans_json is None or not _pytorch_checkpoint_zip_is_valid(checkpoint):
            return False
    semantic_plan = _read_json(_semantic_plan_path(nnunet_results))
    configurations = (semantic_plan or {}).get("configurations")
    return isinstance(configurations, dict) and isinstance(
        configurations.get("3d_fullres_resample_torch_256_bs8_ctnorm"),
        dict,
    )


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
                member = PurePosixPath(info.filename)
                normalized = info.filename.rstrip("/")
                mode = (info.external_attr >> 16) & 0xFFFF
                if (
                    "\\" in info.filename
                    or member.is_absolute()
                    or ".." in member.parts
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
            prefix = PurePosixPath(data_pickles[0]).parent
            version_name = str(prefix / "version")
            data_prefix = (prefix / "data").parts
            tensor_infos = [
                info
                for name, info in by_name.items()
                if PurePosixPath(name).parts[: len(data_prefix)] == data_prefix
                and len(PurePosixPath(name).parts) == len(data_prefix) + 1
                and info.file_size > 0
            ]
            if (
                by_name[data_pickles[0]].file_size <= 0
                or version_name not in by_name
                or by_name[version_name].file_size <= 0
                or not tensor_infos
            ):
                return False
            # testzip streams and CRC-checks every member without unpickling data.pkl.
            return checkpoint.testzip() is None
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return False


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
        and marker.get("dataset_ids") == [branch[0] for branch in BRANCHES]
        and marker.get("dataset_names") == [branch[1] for branch in BRANCHES]
        and isinstance(marker.get("runtime_files"), list)
    ):
        return False
    try:
        _validate_results(nnunet_results)
    except (OSError, RuntimeError):
        return False
    if not _runtime_manifest_is_valid(marker["runtime_files"], nnunet_results):
        return False
    distributions = nnunet_results.parent / PAIR_DISTRIBUTIONS_FILENAME
    try:
        return bool(
            _is_regular_file(distributions)
            and _file_sha256(distributions) == expected_pair_distributions_sha256
        )
    except (OSError, RuntimeError):
        return False


def _write_ready_marker(
    nnunet_results: Path,
    *,
    expected_md5: str,
    pair_distributions_sha256: str,
    legacy_marker_migrated: bool = False,
    integrity_manifest_source: str | None = None,
) -> None:
    _validate_results(nnunet_results)
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
            "runtime_files": _runtime_manifest(nnunet_results),
            "integrity_manifest_source": integrity_manifest_source
            or (
                "validated-legacy-ready-marker"
                if legacy_marker_migrated
                else "md5-verified-archive-extraction"
            ),
            "legacy_marker_migrated": legacy_marker_migrated,
            "validated_at": datetime.now(UTC).isoformat(),
        },
    )


def _required_runtime_files(nnunet_results: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for _dataset_id, dataset_name, trainer in BRANCHES:
        root = nnunet_results / dataset_name / trainer
        for relative in ("dataset.json", "plans.json", "fold_5/checkpoint_final.pth"):
            path = root / relative
            files.append((path.relative_to(nnunet_results).as_posix(), path))
    return files


def _runtime_manifest(nnunet_results: Path) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for relative, path in _required_runtime_files(nnunet_results):
        if not _is_regular_file(path):
            raise RuntimeError(f"ToothSeg runtime file is not regular: {relative}")
        manifest.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
        )
    return manifest


def _runtime_manifest_is_valid(manifest: list[Any], nnunet_results: Path) -> bool:
    required = _required_runtime_files(nnunet_results)
    if len(manifest) != len(required):
        return False
    for entry, (relative, path) in zip(manifest, required, strict=True):
        if not isinstance(entry, dict) or entry.get("path") != relative:
            return False
        size = entry.get("size_bytes")
        sha256 = entry.get("sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            return False
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            return False
        try:
            if not _is_regular_file(path) or path.stat().st_size != size:
                return False
            if _file_sha256(path) != sha256:
                return False
        except (OSError, RuntimeError):
            return False
    return True


def _download_with_md5(url: str, destination: Path, *, expected_md5: str, timeout_sec: int) -> None:
    _validate_https_url(url, label="ToothSeg model")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _ensure_regular_or_absent(destination, label="ToothSeg model archive")
    partial = destination.with_name(destination.name + ".part")
    metadata_path = partial.with_name(partial.name + ".json")
    _ensure_regular_or_absent(partial, label="ToothSeg partial download")
    _ensure_regular_or_absent(metadata_path, label="ToothSeg partial metadata")
    state = _read_json(metadata_path)
    partial_size = partial.stat().st_size if _is_regular_file(partial) else 0
    can_resume = bool(
        partial_size > 0
        and state
        and _partial_state_is_compatible(
            state,
            url=url,
            expected_md5=expected_md5,
            partial_size=partial_size,
        )
    )
    if not can_resume:
        _clear_partial_download(partial, metadata_path)
        partial_size = 0
        state = None
    learned_total = state.get("total_bytes") if state else None
    if isinstance(learned_total, int):
        _require_free_space(destination.parent, max(0, learned_total - partial_size))
    resume_from_bytes = partial_size
    was_resumed = can_resume
    restart_used = False
    if can_resume:
        _write_partial_state(
            metadata_path,
            url=url,
            expected_md5=expected_md5,
            total_bytes=learned_total,
        )
    print(f"Downloading ToothSeg model from {url}")
    while True:
        offset = partial.stat().st_size if _is_regular_file(partial) else 0
        request_headers = {"Accept-Encoding": "identity"}
        if offset:
            request_headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=request_headers)
        try:
            response_context = urllib.request.urlopen(request, timeout=timeout_sec)  # noqa: S310
        except urllib.error.HTTPError as exc:
            if offset and exc.code == 416 and not restart_used:
                exc.close()
                _clear_partial_download(partial, metadata_path)
                learned_total = None
                restart_used = True
                continue
            if exc.code == 416:
                exc.close()
                _clear_partial_download(partial, metadata_path)
                raise RuntimeError("ToothSeg server rejected the download range after a safe restart") from exc
            raise

        with response_context as response:
            _validate_response_transport(response, label="ToothSeg model response")
            status = getattr(response, "status", None) or response.getcode()
            headers = getattr(response, "headers", None) or {}
            if offset:
                if status == 200 and not restart_used:
                    _clear_partial_download(partial, metadata_path)
                    learned_total = None
                    restart_used = True
                    continue
                if status != 206:
                    _clear_partial_download(partial, metadata_path)
                    raise RuntimeError("ToothSeg resume response did not use HTTP 206")
                try:
                    range_start, range_end, response_total = _parse_content_range(
                        headers.get("Content-Range")
                    )
                except RuntimeError:
                    _clear_partial_download(partial, metadata_path)
                    raise
                if range_start != offset:
                    _clear_partial_download(partial, metadata_path)
                    raise RuntimeError("ToothSeg resume response started at an unexpected byte")
                if learned_total is not None and response_total != learned_total:
                    _clear_partial_download(partial, metadata_path)
                    raise RuntimeError("ToothSeg resume response changed the declared total size")
                learned_total = response_total
                expected_response_bytes = range_end - range_start + 1
                try:
                    content_length = _parse_content_length(headers.get("Content-Length"))
                except RuntimeError:
                    _clear_partial_download(partial, metadata_path)
                    raise
                if content_length is not None and content_length != expected_response_bytes:
                    _clear_partial_download(partial, metadata_path)
                    raise RuntimeError("ToothSeg resume response length disagrees with Content-Range")
            else:
                if status != 200:
                    _clear_partial_download(partial, metadata_path)
                    raise RuntimeError("ToothSeg full download did not return HTTP 200")
                try:
                    content_length = _parse_content_length(headers.get("Content-Length"))
                except RuntimeError:
                    _clear_partial_download(partial, metadata_path)
                    raise
                if content_length is not None:
                    if learned_total is not None and content_length != learned_total:
                        _clear_partial_download(partial, metadata_path)
                        raise RuntimeError("ToothSeg response changed the declared total size")
                    learned_total = content_length
                expected_response_bytes = content_length

            if learned_total is not None and learned_total > MAX_MODEL_ARCHIVE_BYTES:
                _clear_partial_download(partial, metadata_path)
                raise RuntimeError("ToothSeg model download exceeds the operational safety limit")
            if offset > MAX_MODEL_ARCHIVE_BYTES or (
                learned_total is not None and offset > learned_total
            ):
                _clear_partial_download(partial, metadata_path)
                raise RuntimeError("ToothSeg partial download exceeds its validated size")
            if learned_total is not None:
                _require_free_space(destination.parent, max(0, learned_total - offset))
            _write_partial_state(
                metadata_path,
                url=url,
                expected_md5=expected_md5,
                total_bytes=learned_total,
            )

            downloaded = offset
            response_bytes = 0
            transfer_started = time.perf_counter()
            last_emitted = 0.0
            _emit_download_progress(
                downloaded,
                learned_total,
                rate_bps=None,
                eta_seconds=None,
                resumed=was_resumed,
                resume_from_bytes=resume_from_bytes,
            )
            try:
                with partial.open("ab" if offset else "wb") as output:
                    while chunk := response.read(CHUNK_SIZE):
                        next_size = downloaded + len(chunk)
                        if next_size > MAX_MODEL_ARCHIVE_BYTES:
                            raise RuntimeError("ToothSeg model download exceeds the operational safety limit")
                        if learned_total is not None and next_size > learned_total:
                            raise RuntimeError("ToothSeg response exceeded its declared total size")
                        output.write(chunk)
                        downloaded = next_size
                        response_bytes += len(chunk)
                        now = time.perf_counter()
                        transfer_elapsed = max(now - transfer_started, 1e-6)
                        rate_bps = response_bytes / transfer_elapsed
                        eta_seconds = (
                            max(0.0, (learned_total - downloaded) / rate_bps)
                            if learned_total is not None and rate_bps > 0
                            else None
                        )
                        if now - last_emitted >= 0.25 or (
                            learned_total is not None and downloaded >= learned_total
                        ):
                            _emit_download_progress(
                                downloaded,
                                learned_total,
                                rate_bps=rate_bps,
                                eta_seconds=eta_seconds,
                                resumed=was_resumed,
                                resume_from_bytes=resume_from_bytes,
                            )
                            last_emitted = now
            except RuntimeError:
                _clear_partial_download(partial, metadata_path)
                raise

        actual_size = partial.stat().st_size
        incomplete_response = expected_response_bytes is not None and response_bytes < expected_response_bytes
        incomplete_total = learned_total is not None and actual_size < learned_total
        if incomplete_response or incomplete_total:
            if response_bytes == 0:
                raise RuntimeError("ToothSeg download ended without making progress; partial data was preserved")
            continue
        if learned_total is not None and actual_size != learned_total:
            _clear_partial_download(partial, metadata_path)
            raise RuntimeError("ToothSeg download size does not match the declared total")

        actual_md5 = _file_md5(partial)
        if actual_md5 != expected_md5:
            _clear_partial_download(partial, metadata_path)
            raise RuntimeError(
                f"ToothSeg model download md5 mismatch: expected {expected_md5}, got {actual_md5}"
            )
        _atomic_publish_file(partial, destination)
        metadata_path.unlink(missing_ok=True)
        return


def _parse_content_range(value: Any) -> tuple[int, int, int]:
    match = _CONTENT_RANGE_PATTERN.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise RuntimeError("ToothSeg resume response has an invalid Content-Range")
    start, end, total = (int(part) for part in match.groups())
    if start > end or end >= total or total <= 0:
        raise RuntimeError("ToothSeg resume response has an impossible Content-Range")
    return start, end, total


def _parse_content_length(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.isdigit() or int(value) < 0:
        raise RuntimeError("ToothSeg response has an invalid Content-Length")
    return int(value)


def _valid_sidecar_total(value: Any, partial_size: int) -> bool:
    return value is None or (
        isinstance(value, int)
        and not isinstance(value, bool)
        and partial_size <= value <= MAX_MODEL_ARCHIVE_BYTES
    )


def _partial_state_is_compatible(
    state: dict[str, Any],
    *,
    url: str,
    expected_md5: str,
    partial_size: int,
) -> bool:
    schema = state.get("schema")
    if schema == PARTIAL_DOWNLOAD_SCHEMA:
        if set(state) != {"schema", "url", "expected_md5", "total_bytes"}:
            return False
    elif schema is None:
        # v0.4.0 partials used exactly these two identity fields. They can be
        # migrated safely because the final official MD5 is still mandatory.
        if set(state) != {"url", "expected_md5"}:
            return False
    else:
        return False
    return bool(
        state.get("url") == url
        and state.get("expected_md5") == expected_md5
        and _valid_sidecar_total(state.get("total_bytes"), partial_size)
    )


def _write_partial_state(
    path: Path,
    *,
    url: str,
    expected_md5: str,
    total_bytes: int | None,
) -> None:
    _write_json(
        path,
        {
            "schema": PARTIAL_DOWNLOAD_SCHEMA,
            "url": url,
            "expected_md5": expected_md5,
            "total_bytes": total_bytes,
        },
    )


def _clear_partial_download(partial: Path, metadata_path: Path) -> None:
    for path in (partial, metadata_path):
        if path.is_symlink() or (path.exists() and not _is_regular_file(path)):
            raise RuntimeError("ToothSeg partial download path is not a regular file")
        path.unlink(missing_ok=True)


def _emit_download_progress(
    downloaded_bytes: int,
    total_bytes: int | None,
    *,
    rate_bps: float | None,
    eta_seconds: float | None,
    resumed: bool,
    resume_from_bytes: int,
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
        resume_from_bytes=resume_from_bytes,
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
    _validate_https_url(url, label="ToothSeg pair distribution")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _ensure_regular_or_absent(destination, label="ToothSeg pair distribution")
    temporary = destination.with_name(f".{destination.name}.part-{uuid4().hex}")
    digest = hashlib.sha256()
    downloaded = 0
    try:
        request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:  # noqa: S310
            _validate_response_transport(response, label="ToothSeg pair-distribution response")
            status = getattr(response, "status", None) or response.getcode()
            if status != 200:
                raise RuntimeError("ToothSeg pair-distribution download did not return HTTP 200")
            headers = getattr(response, "headers", None) or {}
            declared_size = _parse_content_length(headers.get("Content-Length"))
            if declared_size is not None and declared_size > MAX_PAIR_DISTRIBUTIONS_BYTES:
                raise RuntimeError("ToothSeg pair distribution exceeds the operational safety limit")
            if declared_size is not None:
                _require_free_space(destination.parent, declared_size)
            with temporary.open("xb") as output:
                while chunk := response.read(CHUNK_SIZE):
                    downloaded += len(chunk)
                    if downloaded > MAX_PAIR_DISTRIBUTIONS_BYTES:
                        raise RuntimeError("ToothSeg pair distribution exceeds the operational safety limit")
                    output.write(chunk)
                    digest.update(chunk)
            if declared_size is not None and downloaded != declared_size:
                raise RuntimeError("ToothSeg pair-distribution response was truncated")
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise RuntimeError(
                f"ToothSeg pair-distribution sha256 mismatch: expected {expected_sha256}, got {actual}"
            )
        _atomic_publish_file(temporary, destination)
    finally:
        if temporary.is_symlink() or (temporary.exists() and not _is_regular_file(temporary)):
            raise RuntimeError("ToothSeg pair-distribution temporary path is not a regular file")
        temporary.unlink(missing_ok=True)


def _publish_staged_dataset(staged: Path, destination: Path) -> None:
    if staged.is_symlink() or not staged.is_dir():
        raise RuntimeError("ToothSeg staged dataset is not a regular directory")
    _require_path_within(staged, staged.parent)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise RuntimeError("ToothSeg dataset target is not a regular directory")
    backup = destination.with_name(f".{destination.name}.previous-{uuid4().hex}")
    replaced = False
    try:
        if destination.exists():
            destination.replace(backup)
            replaced = True
        staged.replace(destination)
    except Exception:
        if replaced and backup.is_dir() and not destination.exists():
            backup.replace(destination)
        raise
    if backup.exists():
        _remove_directory(backup)


@contextmanager
def _exclusive_setup_lock(model_root: Path) -> Iterator[None]:
    model_root.mkdir(parents=True, exist_ok=True)
    lock_path = model_root / ".toothseg-setup.lock"
    if lock_path.is_symlink() or (lock_path.exists() and not _is_regular_file(lock_path)):
        raise RuntimeError("ToothSeg setup lock is not a regular file")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        lock_stat = os.fstat(descriptor)
        if not stat.S_ISREG(lock_stat.st_mode):
            raise RuntimeError("ToothSeg setup lock is not a regular file")
        if lock_stat.st_uid != os.geteuid():
            raise RuntimeError("ToothSeg setup lock is not owned by the current user")
        if lock_stat.st_nlink != 1:
            raise RuntimeError("ToothSeg setup lock has an unsafe hard-link count")
        if lock_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise RuntimeError("ToothSeg setup lock has unsafe write permissions")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ToothSegSetupBusyError(
                "Another ToothSeg model preparation is already running. Try again after it finishes."
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _recover_orphaned_install_state(
    nnunet_results: Path,
    *,
    expected_md5: str | None = None,
    expected_pair_distributions_sha256: str | None = None,
) -> None:
    model_root = nnunet_results.parent
    staging_directories = sorted(model_root.glob(".toothseg-staging-*"))
    for staging in staging_directories:
        if staging.is_symlink() or not staging.is_dir():
            raise RuntimeError("Orphaned ToothSeg staging target is not a regular directory")

    for _dataset_id, dataset_name, trainer in BRANCHES:
        destination = nnunet_results / dataset_name
        if destination.is_symlink():
            raise RuntimeError("ToothSeg dataset target is a symlink")
        if destination.exists() and not destination.is_dir():
            raise RuntimeError("ToothSeg dataset target is not a regular directory")
        candidates = [nnunet_results / f"{dataset_name}.previous"]
        candidates.extend(sorted(nnunet_results.glob(f".{dataset_name}.previous-*")))
        backups = [candidate for candidate in candidates if candidate.exists() or candidate.is_symlink()]
        for backup in backups:
            if backup.is_symlink() or not backup.is_dir():
                raise RuntimeError("ToothSeg recovery backup is not a regular directory")
        valid_backups = [backup for backup in backups if _branch_structure_valid(backup, trainer)]
        destination_valid = destination.is_dir() and _branch_structure_valid(destination, trainer)
        if destination_valid:
            for backup in backups:
                _remove_directory(backup)
            continue
        if len(valid_backups) > 1:
            raise RuntimeError("Multiple valid ToothSeg recovery backups require manual review")
        if len(valid_backups) == 1:
            selected = valid_backups[0]
            invalid_destination: Path | None = None
            if destination.exists():
                invalid_destination = destination.with_name(f".{dataset_name}.invalid-{uuid4().hex}")
                destination.replace(invalid_destination)
            try:
                selected.replace(destination)
            except Exception:
                if invalid_destination is not None and invalid_destination.exists() and not destination.exists():
                    invalid_destination.replace(destination)
                raise
            if invalid_destination is not None and invalid_destination.exists():
                _remove_directory(invalid_destination)
        for backup in backups:
            if backup.exists():
                _remove_directory(backup)

    marker = _read_json(nnunet_results / READY_MARKER_FILENAME)
    if (
        expected_md5 is not None
        and expected_pair_distributions_sha256 is not None
        and _marker_is_ready(
            marker,
            expected_md5=expected_md5,
            expected_pair_distributions_sha256=expected_pair_distributions_sha256,
            nnunet_results=nnunet_results,
        )
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
            expected_pair_distributions_sha256=expected_pair_distributions_sha256,
        )
    ]
    if len(recoverable_staging) > 1:
        raise RuntimeError("Multiple complete ToothSeg staging installs require manual review")
    if len(recoverable_staging) == 1:
        staging = recoverable_staging[0]
        staged_results = staging / "nnUNet_results"
        for _dataset_id, dataset_name, _trainer in BRANCHES:
            _publish_staged_dataset(staged_results / dataset_name, nnunet_results / dataset_name)
        _atomic_publish_file(
            staging / PAIR_DISTRIBUTIONS_FILENAME,
            model_root / PAIR_DISTRIBUTIONS_FILENAME,
        )
        _write_ready_marker(
            nnunet_results,
            expected_md5=expected_md5 or "",
            pair_distributions_sha256=expected_pair_distributions_sha256 or "",
            integrity_manifest_source="recovered-complete-md5-verified-staging",
        )
    for staging in staging_directories:
        if staging.exists():
            _remove_directory(staging)


def _staging_install_is_complete(
    staging: Path,
    *,
    expected_md5: str | None,
    expected_pair_distributions_sha256: str | None,
) -> bool:
    if expected_md5 is None or expected_pair_distributions_sha256 is None:
        return False
    metadata = _read_json(staging / STAGING_METADATA_FILENAME)
    if not (
        metadata
        and metadata.get("schema") == "totalsegmentator_wrapper_mac.toothseg_staging.v1"
        and metadata.get("expected_md5") == expected_md5
        and metadata.get("pair_distributions_sha256") == expected_pair_distributions_sha256
    ):
        return False
    try:
        _validate_results(staging / "nnUNet_results")
    except RuntimeError:
        return False
    distributions = staging / PAIR_DISTRIBUTIONS_FILENAME
    return bool(
        _is_regular_file(distributions)
        and _file_sha256(distributions) == expected_pair_distributions_sha256
    )


def _branch_structure_valid(dataset: Path, trainer: str) -> bool:
    if dataset.is_symlink() or not dataset.is_dir():
        return False
    root = dataset / trainer
    required = (root / "dataset.json", root / "plans.json", root / "fold_5" / "checkpoint_final.pth")
    return all(_is_regular_file(path) and path.stat().st_size > 0 for path in required)


def _prepare_file_target(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink() or (expanded.exists() and not _is_regular_file(expanded)):
        raise RuntimeError(f"{label} must be a regular file target")
    parent = expanded.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise RuntimeError(f"{label} parent must be a regular directory")
    return expanded.resolve()


def _prepare_directory_target(path: Path, *, label: str, create: bool) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink() or (expanded.exists() and not expanded.is_dir()):
        raise RuntimeError(f"{label} must be a regular directory target")
    if create:
        expanded.mkdir(parents=True, exist_ok=True)
    return expanded.resolve()


def _validate_https_url(url: str, *, label: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{label} URL is invalid") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise RuntimeError(f"{label} URL must use HTTPS on the standard port without credentials")


def _validate_response_transport(response: Any, *, label: str) -> None:
    try:
        final_url = response.geturl()
    except (AttributeError, TypeError) as exc:
        raise RuntimeError(f"{label} URL is invalid") from exc
    if not isinstance(final_url, str):
        raise RuntimeError(f"{label} URL is invalid")
    _validate_https_url(final_url, label=label)
    headers = getattr(response, "headers", None) or {}
    content_encoding = headers.get("Content-Encoding")
    if content_encoding is not None and (
        not isinstance(content_encoding, str)
        or content_encoding.strip().lower() != "identity"
    ):
        raise RuntimeError(f"{label} used unsupported Content-Encoding")


def _require_free_space(path: Path, required_bytes: int) -> None:
    if required_bytes <= 0:
        return
    free_bytes = shutil.disk_usage(path).free
    if free_bytes < required_bytes:
        raise OSError(
            errno.ENOSPC,
            f"insufficient disk space: need {required_bytes} bytes, have {free_bytes} bytes",
        )


def _ensure_regular_or_absent(path: Path, *, label: str) -> None:
    if path.is_symlink() or (path.exists() and not _is_regular_file(path)):
        raise RuntimeError(f"{label} must be a regular file")


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _require_path_within(path: Path, root: Path) -> None:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise RuntimeError("ToothSeg runtime path escapes the model root") from exc


def _reject_symlink_components(path: Path, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("ToothSeg runtime path escapes the model root") from exc
    current = root
    if current.is_symlink():
        raise RuntimeError("ToothSeg runtime path contains a symlink")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError("ToothSeg runtime path contains a symlink")


def _atomic_publish_file(source: Path, destination: Path) -> None:
    if not _is_regular_file(source):
        raise RuntimeError("ToothSeg publish source is not a regular file")
    _ensure_regular_or_absent(destination, label="ToothSeg publish target")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)


def _remove_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("Refusing to remove a non-directory ToothSeg artifact")
    shutil.rmtree(path)


def _file_md5(path: Path) -> str:
    if not _is_regular_file(path):
        raise RuntimeError("ToothSeg MD5 target is not a regular file")
    digest = hashlib.md5()  # noqa: S324 - Zenodo publishes MD5 for file integrity.
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    if not _is_regular_file(path):
        raise RuntimeError("ToothSeg SHA-256 target is not a regular file")
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
    if not _is_regular_file(path):
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_regular_or_absent(path, label="ToothSeg JSON target")
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        _atomic_publish_file(temporary, path)
    finally:
        if temporary.is_symlink() or (temporary.exists() and not _is_regular_file(temporary)):
            raise RuntimeError("ToothSeg JSON temporary path is not a regular file")
        temporary.unlink(missing_ok=True)
