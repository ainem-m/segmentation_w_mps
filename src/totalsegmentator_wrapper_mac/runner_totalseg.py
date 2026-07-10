from __future__ import annotations

import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from totalsegmentator_wrapper_mac.benchmark import environment_metadata, input_metadata, write_json
from totalsegmentator_wrapper_mac import __version__
from totalsegmentator_wrapper_mac.device import DeviceCheck, resolve_device
from totalsegmentator_wrapper_mac.output_report import generate_output_report
from totalsegmentator_wrapper_mac.outputs import CaseOutput, copy_source_if_requested, prepare_case_output
from totalsegmentator_wrapper_mac.teeth_roi import create_teeth_roi_for_case, reembed_labelmap_to_full_space


TEETH_UNSUPPORTED_REASON = (
    "TotalSegmentator 2.14.0 teeth is blocked in the CLI path because crop_model "
    "device propagation passes None for string devices. The task is deferred until "
    "the runner carries a tested workaround or upstream fixes the issue."
)

PROGRESS_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
PROGRESS_PERCENT_RE = re.compile(r"(?<!\d)(100|[1-9]?\d)%")
PROGRESS_STAGE_RE = re.compile(r"^\s*([^:|]{2,80}):\s*(?:100|[1-9]?\d)%\|")
PROGRESS_PHASE_RE = re.compile(
    r"^\s*((?:Resampling|Predicting(?:\s+\S+)?|Saving segmentations|"
    r"Preprocessing|Postprocessing|Cropping|Collecting results))\s*(?::|\.\.\.)?\s*$",
    re.IGNORECASE,
)
RUN_PROGRESS_PREFIX = "RUN_PROGRESS "
TEETH_SUPPLIED_PREFLIGHT_ROBUST_WARNING = (
    "existing craniofacial case supplied; internal robust preflight skipped"
)
DENTALSEGMENTATOR_LABELS = {
    1: "upper_skull",
    2: "mandible",
    3: "upper_teeth",
    4: "lower_teeth",
    5: "mandibular_canal",
}
DENTALSEGMENTATOR_ZENODO_DOI = "10.5281/zenodo.10829675"
DENTALSEGMENTATOR_MODEL_ZIP = "Dataset112_DentalSegmentator_v100.zip"
MACOS_APP_EXECUTION_PROFILE = "macos-app"


@dataclass(frozen=True)
class TotalSegRunResult:
    status: str
    returncode: int
    elapsed_seconds: float
    requested_device: str
    actual_device: str
    fallback_reason: str | None
    task: str
    output_dir: str
    stdout_tail: str
    stderr_tail: str
    error_code: str | None = None
    safe_reason: str | None = None
    mps_state: str = "not_required"
    occurred_at: str | None = None
    execution_profile: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(_result_safe_fields(self))
        return payload


def sanitized_command(command: list[str], input_path: Path, output_dir: Path) -> list[str]:
    result = []
    skip_next = None
    for index, part in enumerate(command):
        if skip_next == index:
            continue
        if index == 0:
            result.append(Path(part).name)
        elif part in {"-i", "--input"} and index + 1 < len(command):
            result.extend([part, input_path.name])
            skip_next = index + 1
        elif part in {"-o", "--output"} and index + 1 < len(command):
            result.extend([part, f"<output:{output_dir.name}>"])
            skip_next = index + 1
        else:
            result.append(part)
    return result


def resolve_totalseg_executable(totalseg_bin: str) -> str:
    if os.sep in totalseg_bin or (os.altsep and os.altsep in totalseg_bin):
        return totalseg_bin
    executable_candidate = Path(sys.executable).parent / totalseg_bin
    if executable_candidate.exists():
        return str(executable_candidate)
    found = shutil.which(totalseg_bin)
    return found or totalseg_bin


def _is_strict_mps_profile(execution_profile: str | None, require_mps: bool) -> bool:
    if execution_profile not in {None, MACOS_APP_EXECUTION_PROFILE}:
        raise ValueError(f"Unsupported execution profile: {execution_profile}")
    return execution_profile == MACOS_APP_EXECUTION_PROFILE or require_mps


def _strict_preflight_failure(
    *,
    requested_device: str,
    task: str,
    execution_profile: str | None,
    error_code: str,
    safe_reason: str,
    mps_state: str,
) -> TotalSegRunResult:
    return TotalSegRunResult(
        status="failed",
        returncode=2,
        elapsed_seconds=0.0,
        requested_device=requested_device,
        actual_device="unknown",
        fallback_reason=None,
        task=task,
        output_dir="",
        stdout_tail="",
        stderr_tail=safe_reason,
        error_code=error_code,
        safe_reason=safe_reason,
        mps_state=mps_state,
        occurred_at=datetime.now(UTC).isoformat(),
        execution_profile=execution_profile,
    )


def _mps_state(device_check: DeviceCheck) -> str:
    if device_check.actual_device == "mps" and device_check.status == "pass":
        return "validated"
    if device_check.actual_device == "cpu":
        return "cpu"
    return "unavailable"


def _result_safe_fields(result: TotalSegRunResult) -> dict[str, str | None]:
    if result.status != "failed":
        return {
            "error_code": result.error_code,
            "safe_reason": result.safe_reason,
            "mps_state": result.mps_state,
            "occurred_at": result.occurred_at,
        }
    return {
        "error_code": result.error_code or "runner_failed",
        "safe_reason": result.safe_reason or "The segmentation run did not complete.",
        "mps_state": result.mps_state,
        "occurred_at": result.occurred_at or datetime.now(UTC).isoformat(),
    }


def run_totalsegmentator(
    *,
    input_path: Path,
    output_root: Path,
    task: str,
    requested_device: str,
    backend: str = "totalsegmentator",
    totalseg_bin: str = "TotalSegmentator",
    totalseg_home: Path | None = None,
    totalseg_weights: Path | None = None,
    dentalseg_bin: str = "nnUNetv2_predict",
    dentalseg_model_dir: Path | None = None,
    dentalseg_model_zip: Path | None = None,
    dentalseg_nnunet_raw: Path | None = None,
    dentalseg_nnunet_preprocessed: Path | None = None,
    dentalseg_nnunet_results: Path | None = None,
    dentalseg_dataset_id: str = "112",
    dentalseg_configuration: str = "3d_fullres",
    dentalseg_trainer: str = "nnUNetTrainer",
    dentalseg_plans: str = "nnUNetPlans",
    dentalseg_folds: tuple[str, ...] = ("0",),
    dentalseg_disable_tta: bool = False,
    dentalseg_not_on_device: bool = False,
    dentalseg_npp: int = 1,
    dentalseg_nps: int = 1,
    dentalseg_timeout_sec: int = 7200,
    copy_input: bool = True,
    skip_device_check: bool = False,
    robust_crop: bool = False,
    higher_order_resampling: bool = False,
    experimental_teeth: bool = False,
    teeth_dry_run: bool = False,
    teeth_timeout_sec: int = 3600,
    teeth_crop_margin_mm: float = 20.0,
    teeth_craniofacial_case: Path | None = None,
    teeth_force_split: bool = False,
    teeth_robust_craniofacial_preflight: bool = False,
    execution_profile: str | None = None,
    require_mps: bool = False,
) -> TotalSegRunResult:
    input_path = input_path.resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")
    if backend not in {"totalsegmentator", "dentalsegmentator"}:
        raise ValueError(f"Unsupported backend: {backend}")
    if robust_crop and task != "craniofacial_structures":
        raise ValueError(
            "--robust-crop is only supported with task=craniofacial_structures; "
            "use --teeth-robust-craniofacial-preflight for experimental teeth preflight."
        )
    if backend == "dentalsegmentator" and robust_crop:
        raise ValueError("--robust-crop is only supported by the TotalSegmentator backend")
    if backend == "dentalsegmentator" and higher_order_resampling:
        raise ValueError(
            "--higher-order-resampling is only supported by the TotalSegmentator backend"
        )

    strict_mps = _is_strict_mps_profile(execution_profile, require_mps)
    if strict_mps and requested_device != "mps":
        return _strict_preflight_failure(
            requested_device=requested_device,
            task=task,
            execution_profile=execution_profile,
            error_code="mps_required",
            safe_reason="This app execution profile requires MPS.",
            mps_state="required",
        )
    if execution_profile == MACOS_APP_EXECUTION_PROFILE and not require_mps:
        return _strict_preflight_failure(
            requested_device=requested_device,
            task=task,
            execution_profile=execution_profile,
            error_code="mps_required",
            safe_reason="The macOS app execution profile requires --require-mps.",
            mps_state="required",
        )
    if strict_mps and backend == "dentalsegmentator":
        from totalsegmentator_wrapper_mac.dentalsegmentator_setup import (
            dentalsegmentator_model_status,
        )
        from totalsegmentator_wrapper_mac.setup_manager import (
            DENTALSEGMENTATOR_DATASET_NAME,
            DENTALSEGMENTATOR_MODEL_MD5,
        )

        model_ready = False
        if dentalseg_model_zip is None and dentalseg_nnunet_results is not None:
            model_status = dentalsegmentator_model_status(
                model_root=dentalseg_nnunet_results.parent,
                nnunet_results=dentalseg_nnunet_results,
                expected_md5=DENTALSEGMENTATOR_MODEL_MD5,
                dataset_id=dentalseg_dataset_id,
                dataset_name=DENTALSEGMENTATOR_DATASET_NAME,
            )
            model_ready = model_status["model_state"] == "ready"
        if not model_ready:
            return _strict_preflight_failure(
                requested_device=requested_device,
                task=task,
                execution_profile=execution_profile,
                error_code="dentalseg_prepare_required",
                safe_reason="Prepare the DentalSegmentator model before starting an app-profile run.",
                mps_state="required",
            )

    device_check: DeviceCheck | None = None
    if strict_mps:
        device_check = resolve_device("mps", skip_device_check=False)
        if device_check.status != "pass" or device_check.actual_device != "mps":
            return _strict_preflight_failure(
                requested_device=requested_device,
                task=task,
                execution_profile=execution_profile,
                error_code="mps_unavailable",
                safe_reason="MPS validation did not pass for this app run.",
                mps_state="unavailable",
            )

    case = prepare_case_output(output_root)
    copied_source = copy_source_if_requested(input_path, case, copy_input)
    source_for_summary = copied_source or input_path

    device_check = device_check or resolve_device(requested_device, skip_device_check=skip_device_check)
    if device_check.status != "pass" or not device_check.actual_device:
        _write_failed_device_check(
            case,
            input_path,
            task,
            device_check,
            robust_crop=robust_crop,
            higher_order_resampling=higher_order_resampling,
        )
        raise RuntimeError(
            f"MPS smoke test failed for requested device {requested_device}: {device_check.error}"
        )

    if backend == "dentalsegmentator":
        return _run_dentalsegmentator(
            case=case,
            input_path=input_path,
            source_for_summary=source_for_summary,
            requested_device=requested_device,
            device_check=device_check,
            task=task,
            dentalseg_bin=dentalseg_bin,
            dentalseg_model_dir=dentalseg_model_dir,
            dentalseg_model_zip=dentalseg_model_zip,
            dentalseg_nnunet_raw=dentalseg_nnunet_raw,
            dentalseg_nnunet_preprocessed=dentalseg_nnunet_preprocessed,
            dentalseg_nnunet_results=dentalseg_nnunet_results,
            dentalseg_dataset_id=dentalseg_dataset_id,
            dentalseg_configuration=dentalseg_configuration,
            dentalseg_trainer=dentalseg_trainer,
            dentalseg_plans=dentalseg_plans,
            dentalseg_folds=dentalseg_folds,
            dentalseg_disable_tta=dentalseg_disable_tta,
            dentalseg_not_on_device=dentalseg_not_on_device,
            dentalseg_npp=dentalseg_npp,
            dentalseg_nps=dentalseg_nps,
            dentalseg_timeout_sec=dentalseg_timeout_sec,
            execution_profile=execution_profile,
        )

    if task == "teeth" and not experimental_teeth:
        result = TotalSegRunResult(
            status="failed",
            returncode=2,
            elapsed_seconds=0.0,
            requested_device=requested_device,
            actual_device=device_check.actual_device,
            fallback_reason=device_check.fallback_reason,
            task=task,
            output_dir=str(case.root),
            stdout_tail="",
            stderr_tail=TEETH_UNSUPPORTED_REASON,
        )
        case.run_log_path.write_text(
            "TASK BLOCKED\n" + TEETH_UNSUPPORTED_REASON + "\n",
            encoding="utf-8",
        )
        _write_metadata(
            case,
            input_path,
            task,
            result,
            device_check,
            robust_crop=robust_crop,
            higher_order_resampling=higher_order_resampling,
        )
        generate_output_report(
            case=case,
            source_volume_path=source_for_summary,
            task=task,
            run_result=result,
        )
        return result

    if task == "teeth":
        return _run_experimental_teeth(
            case=case,
            input_path=input_path,
            source_for_summary=source_for_summary,
            requested_device=requested_device,
            device_check=device_check,
            totalseg_bin=totalseg_bin,
            totalseg_home=totalseg_home,
            totalseg_weights=totalseg_weights,
            teeth_dry_run=teeth_dry_run,
            teeth_timeout_sec=teeth_timeout_sec,
            teeth_crop_margin_mm=teeth_crop_margin_mm,
            teeth_craniofacial_case=teeth_craniofacial_case,
            teeth_force_split=teeth_force_split,
            teeth_robust_craniofacial_preflight=teeth_robust_craniofacial_preflight,
            execution_profile=execution_profile,
            require_mps=require_mps,
            higher_order_resampling=higher_order_resampling,
            skip_device_check=skip_device_check,
        )

    resolved_totalseg_bin = resolve_totalseg_executable(totalseg_bin)
    command = [
        resolved_totalseg_bin,
        "-i",
        str(input_path),
        "-o",
        str(case.raw_segmentations_dir),
        "-ta",
        task,
        "--device",
        device_check.actual_device,
    ]
    if robust_crop:
        command.append("--robust_crop")
    if higher_order_resampling:
        command.append("--higher_order_resampling")
    env = os.environ.copy()
    if strict_mps:
        env.pop("PYTORCH_ENABLE_MPS_FALLBACK", None)
    if totalseg_home is not None:
        env["TOTALSEG_HOME_DIR"] = str(totalseg_home)
    if totalseg_weights is not None:
        env["TOTALSEG_WEIGHTS_PATH"] = str(totalseg_weights)

    proc_returncode, elapsed, stdout, stderr = _run_command_streamed(
        command=command,
        env=env,
        log_path=case.run_log_path,
        safe_command=sanitized_command(command, input_path, case.raw_segmentations_dir),
    )

    result = TotalSegRunResult(
        status="success" if proc_returncode == 0 else "failed",
        returncode=proc_returncode,
        elapsed_seconds=elapsed,
        requested_device=requested_device,
        actual_device=device_check.actual_device,
        fallback_reason=device_check.fallback_reason,
        task=task,
        output_dir=str(case.root),
        stdout_tail=stdout[-4000:],
        stderr_tail=stderr[-4000:],
        error_code="backend_failed" if proc_returncode != 0 else None,
        safe_reason="The segmentation backend did not complete." if proc_returncode != 0 else None,
        mps_state=_mps_state(device_check),
        occurred_at=datetime.now(UTC).isoformat() if proc_returncode != 0 else None,
        execution_profile=execution_profile,
    )
    _write_metadata(
        case,
        input_path,
        task,
        result,
        device_check,
        robust_crop=robust_crop,
        higher_order_resampling=higher_order_resampling,
    )
    generate_output_report(
        case=case,
        source_volume_path=source_for_summary,
        task=task,
        run_result=result,
    )
    return result


def _write_failed_device_check(
    case: CaseOutput,
    input_path: Path,
    task: str,
    device_check: DeviceCheck,
    robust_crop: bool = False,
    higher_order_resampling: bool = False,
) -> None:
    env = environment_metadata()
    write_json(case.environment_path, env)
    benchmark = {
        "app_version": f"{__version__}-preview",
        "environment": env,
        "input": input_metadata(input_path),
        "run": {
            "task": task,
            "requested_device": device_check.requested_device,
            "actual_device": device_check.actual_device,
            "fallback_reason": device_check.fallback_reason,
            "status": "failed",
            "error": device_check.error,
            "error_code": "mps_unavailable",
            "safe_reason": "MPS validation did not pass for this run.",
            "mps_state": _mps_state(device_check),
            "occurred_at": datetime.now(UTC).isoformat(),
            "robust_crop": robust_crop,
            "higher_order_resampling": higher_order_resampling,
        },
        "device_check": device_check.to_dict(),
    }
    write_json(case.benchmark_path, benchmark)
    case.run_log_path.write_text(
        "DEVICE CHECK FAILED\n" + str(device_check.error) + "\n", encoding="utf-8"
    )


def _run_experimental_teeth(
    *,
    case: CaseOutput,
    input_path: Path,
    source_for_summary: Path,
    requested_device: str,
    device_check: DeviceCheck,
    totalseg_bin: str,
    totalseg_home: Path | None,
    totalseg_weights: Path | None,
    teeth_dry_run: bool,
    teeth_timeout_sec: int,
    teeth_crop_margin_mm: float,
    teeth_craniofacial_case: Path | None,
    teeth_force_split: bool,
    teeth_robust_craniofacial_preflight: bool,
    higher_order_resampling: bool,
    skip_device_check: bool,
    execution_profile: str | None,
    require_mps: bool,
) -> TotalSegRunResult:
    start = time.perf_counter()
    preflight_source = "none" if teeth_dry_run else ("provided" if teeth_craniofacial_case else "internal")
    preflight_case_dir = None
    if not teeth_dry_run:
        preflight_case_dir = str(
            (teeth_craniofacial_case or (case.root / "preflight_craniofacial")).resolve()
        )
    preflight_info: dict[str, Any] = {
        "source": preflight_source,
        "case_dir": preflight_case_dir,
        "robust_crop_requested": teeth_robust_craniofacial_preflight,
        "robust_crop_used": False,
        "status": "skipped" if teeth_dry_run else "pending",
    }
    if teeth_craniofacial_case is not None and teeth_robust_craniofacial_preflight:
        preflight_info["warning"] = TEETH_SUPPLIED_PREFLIGHT_ROBUST_WARNING
    extra: dict[str, Any] = {
        "experimental_teeth": {
            "enabled": True,
            "dry_run": teeth_dry_run,
            "timeout_sec": teeth_timeout_sec,
            "crop_margin_mm": teeth_crop_margin_mm,
            "force_split": teeth_force_split,
            "higher_order_resampling": higher_order_resampling,
            "robust_craniofacial_preflight": False,
            "robust_craniofacial_preflight_requested": teeth_robust_craniofacial_preflight,
            "craniofacial_preflight": preflight_info,
            "child_benchmark_path": str(case.teeth_child_benchmark_path),
            "roi_json_path": str(case.teeth_roi_path),
            "mps_fallback_env_removed": True,
        }
    }
    try:
        if device_check.actual_device != "mps":
            raise RuntimeError(
                f"Experimental teeth is MPS-only; resolved device was {device_check.actual_device!r}"
            )

        roi_input = input_path
        roi_metadata: dict[str, Any] | None = None
        if not teeth_dry_run:
            craniofacial_case = teeth_craniofacial_case
            if craniofacial_case is None:
                craniofacial_case = case.root / "preflight_craniofacial"
                extra["experimental_teeth"][
                    "robust_craniofacial_preflight"
                ] = teeth_robust_craniofacial_preflight
                preflight_info["robust_crop_used"] = teeth_robust_craniofacial_preflight
                preflight_info["status"] = "running"
                preflight = run_totalsegmentator(
                    input_path=input_path,
                    output_root=craniofacial_case,
                    task="craniofacial_structures",
                    requested_device="mps",
                    totalseg_bin=totalseg_bin,
                    totalseg_home=totalseg_home,
                    totalseg_weights=totalseg_weights,
                    copy_input=False,
                    skip_device_check=skip_device_check,
                    robust_crop=teeth_robust_craniofacial_preflight,
                    higher_order_resampling=higher_order_resampling,
                    execution_profile=execution_profile,
                    require_mps=require_mps,
                )
                if preflight.status != "success":
                    preflight_info["status"] = "failed"
                    raise RuntimeError(
                        f"craniofacial_structures preflight failed: {preflight.stderr_tail}"
                    )
                preflight_info["status"] = "success"
            else:
                preflight_info["status"] = "provided"

            roi_metadata = create_teeth_roi_for_case(
                case=case,
                input_path=input_path,
                craniofacial_case_dir=craniofacial_case,
                margin_mm=teeth_crop_margin_mm,
            )
            roi_input = case.teeth_roi_input_path
            extra["experimental_teeth"]["roi"] = roi_metadata

        command = _teeth_child_command(
            input_path=roi_input,
            output_path=case.teeth_multilabel_roi_path,
            benchmark_path=case.teeth_child_benchmark_path,
            dry_run=teeth_dry_run,
            force_split=teeth_force_split,
            higher_order_resampling=higher_order_resampling,
        )
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env.pop("PYTORCH_ENABLE_MPS_FALLBACK", None)
        if totalseg_home is not None:
            env["TOTALSEG_HOME_DIR"] = str(totalseg_home)
        if totalseg_weights is not None:
            env["TOTALSEG_WEIGHTS_PATH"] = str(totalseg_weights)

        returncode, child_elapsed, stdout, stderr = _run_command_streamed(
            command=command,
            env=env,
            log_path=case.run_log_path,
            safe_command=_sanitize_teeth_child_command(command, roi_input, case.teeth_experimental_dir),
            timeout_sec=teeth_timeout_sec,
        )
        child_benchmark = _read_json_if_exists(case.teeth_child_benchmark_path)
        total_elapsed = time.perf_counter() - start
        last_progress = _extract_last_progress(stdout, stderr)
        child_status = child_benchmark.get("status")
        if returncode == 124:
            child_status = "timeout"
        elif child_status is None:
            child_status = "success" if returncode == 0 else "failed"

        extra["experimental_teeth"]["child_elapsed_seconds"] = child_elapsed
        extra["experimental_teeth"]["elapsed_sec"] = child_elapsed
        extra["experimental_teeth"]["child_benchmark"] = child_benchmark
        extra["experimental_teeth"]["child_benchmark_json_path"] = str(
            case.teeth_child_benchmark_path
        )
        extra["experimental_teeth"]["log_path"] = str(case.run_log_path)
        extra["experimental_teeth"]["patch"] = child_benchmark.get("patch")
        extra["experimental_teeth"]["mps_gate"] = child_benchmark.get("mps_gate")
        extra["experimental_teeth"]["validation"] = child_benchmark.get("validation")
        extra["experimental_teeth"]["last_progress"] = last_progress
        extra["experimental_teeth"]["child_status"] = child_status
        extra["experimental_teeth"]["child_returncode"] = returncode
        if returncode == 124:
            extra["experimental_teeth"]["timeout"] = {
                "timeout_sec": teeth_timeout_sec,
                "elapsed_sec": child_elapsed,
                "last_progress": last_progress,
            }
        if "warning" in preflight_info:
            _append_run_log_warning(case.run_log_path, str(preflight_info["warning"]))
        if returncode == 0 and not teeth_dry_run and roi_metadata is not None:
            extra["experimental_teeth"]["fullspace"] = reembed_labelmap_to_full_space(
                cropped_label_nii=case.teeth_multilabel_roi_path,
                source_nii=input_path,
                roi_metadata=roi_metadata,
                output_full_nii=case.teeth_multilabel_fullspace_path,
            )

        result = TotalSegRunResult(
            status="success" if returncode == 0 else "failed",
            returncode=returncode,
            elapsed_seconds=total_elapsed,
            requested_device=requested_device,
            actual_device=device_check.actual_device,
            fallback_reason=device_check.fallback_reason,
            task="teeth",
            output_dir=str(case.root),
            stdout_tail=stdout[-4000:],
            stderr_tail=stderr[-4000:],
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - start
        result = TotalSegRunResult(
            status="failed",
            returncode=1,
            elapsed_seconds=elapsed,
            requested_device=requested_device,
            actual_device=device_check.actual_device or "unknown",
            fallback_reason=device_check.fallback_reason,
            task="teeth",
            output_dir=str(case.root),
            stdout_tail="",
            stderr_tail=repr(exc),
        )
        case.run_log_path.write_text("EXPERIMENTAL TEETH FAILED\n" + repr(exc) + "\n", encoding="utf-8")
        if "warning" in preflight_info:
            _append_run_log_warning(case.run_log_path, str(preflight_info["warning"]))
        extra["experimental_teeth"]["error"] = repr(exc)

    _write_metadata(
        case,
        input_path,
        "teeth",
        result,
        device_check,
        higher_order_resampling=higher_order_resampling,
        extra=extra,
    )
    generate_output_report(
        case=case,
        source_volume_path=source_for_summary,
        task="teeth",
        run_result=result,
    )
    return result


def _run_dentalsegmentator(
    *,
    case: CaseOutput,
    input_path: Path,
    source_for_summary: Path,
    requested_device: str,
    device_check: DeviceCheck,
    task: str,
    dentalseg_bin: str,
    dentalseg_model_dir: Path | None,
    dentalseg_model_zip: Path | None,
    dentalseg_nnunet_raw: Path | None,
    dentalseg_nnunet_preprocessed: Path | None,
    dentalseg_nnunet_results: Path | None,
    dentalseg_dataset_id: str,
    dentalseg_configuration: str,
    dentalseg_trainer: str,
    dentalseg_plans: str,
    dentalseg_folds: tuple[str, ...],
    dentalseg_disable_tta: bool,
    dentalseg_not_on_device: bool,
    dentalseg_npp: int,
    dentalseg_nps: int,
    dentalseg_timeout_sec: int,
    execution_profile: str | None,
) -> TotalSegRunResult:
    start = time.perf_counter()
    extra: dict[str, Any] = {
        "dentalsegmentator": {
            "enabled": True,
            "source": "nnunetv2",
            "zenodo_doi": DENTALSEGMENTATOR_ZENODO_DOI,
            "expected_model_zip": DENTALSEGMENTATOR_MODEL_ZIP,
            "labels": {str(label): name for label, name in DENTALSEGMENTATOR_LABELS.items()},
            "dataset_id": dentalseg_dataset_id,
            "configuration": dentalseg_configuration,
            "trainer": dentalseg_trainer,
            "plans": dentalseg_plans,
            "folds": list(dentalseg_folds),
            "device": device_check.actual_device,
            "disable_tta": dentalseg_disable_tta,
            "not_on_device": dentalseg_not_on_device,
            "npp": dentalseg_npp,
            "nps": dentalseg_nps,
            "timeout_sec": dentalseg_timeout_sec,
            "versions": _dentalseg_versions(),
            "mps_fallback_env_removed": True,
        }
    }
    try:
        if task != "craniofacial_structures":
            raise ValueError(
                "DentalSegmentator backend supports the arch/jaw preview path only; "
                "it does not provide individual tooth labels."
            )
        if device_check.actual_device not in {"cpu", "mps"}:
            raise RuntimeError(f"Unsupported DentalSegmentator device: {device_check.actual_device!r}")
        if requested_device == "auto" and device_check.actual_device == "cpu":
            raise RuntimeError(
                "DentalSegmentator auto device resolved to CPU. "
                "Use --device mps for the Mac preview path, or pass --device cpu explicitly "
                "for development-only CPU checks."
            )
        if not dentalseg_folds:
            raise ValueError("At least one DentalSegmentator fold must be specified")
        if dentalseg_npp < 0 or dentalseg_nps < 0:
            raise ValueError("DentalSegmentator npp/nps must be >= 0")

        _prepare_dentalseg_input(input_path=input_path, case=case)
        env = _dentalseg_environment(
            case=case,
            nnunet_raw=dentalseg_nnunet_raw,
            nnunet_preprocessed=dentalseg_nnunet_preprocessed,
            nnunet_results=dentalseg_nnunet_results,
            require_results=dentalseg_model_dir is None,
        )
        extra["dentalsegmentator"]["nnunet_env"] = {
            key: env.get(key)
            for key in ("nnUNet_raw", "nnUNet_preprocessed", "nnUNet_results")
        }
        if dentalseg_model_zip is not None:
            install_command = _dentalseg_install_command(dentalseg_model_zip)
            extra["dentalsegmentator"]["install_command"] = _sanitize_dentalseg_command(
                install_command,
                case=case,
            )
            install_returncode, install_elapsed, install_stdout, install_stderr = _run_command_streamed(
                command=install_command,
                env=env,
                log_path=case.run_log_path,
                safe_command=extra["dentalsegmentator"]["install_command"],
                timeout_sec=dentalseg_timeout_sec,
            )
            extra["dentalsegmentator"]["install"] = {
                "returncode": install_returncode,
                "elapsed_seconds": install_elapsed,
                "stdout_tail": install_stdout[-2000:],
                "stderr_tail": install_stderr[-2000:],
            }
            if install_returncode != 0:
                raise RuntimeError(
                    "DentalSegmentator model install failed: "
                    + (install_stderr[-1000:] or install_stdout[-1000:])
                )

        command = _dentalseg_predict_command(
            case=case,
            dentalseg_bin=dentalseg_bin,
            model_dir=dentalseg_model_dir,
            dataset_id=dentalseg_dataset_id,
            configuration=dentalseg_configuration,
            trainer=dentalseg_trainer,
            plans=dentalseg_plans,
            folds=dentalseg_folds,
            device=str(device_check.actual_device),
            disable_tta=dentalseg_disable_tta,
            not_on_device=dentalseg_not_on_device,
            npp=dentalseg_npp,
            nps=dentalseg_nps,
        )
        safe_command = _sanitize_dentalseg_command(command, case=case)
        extra["dentalsegmentator"]["command"] = safe_command
        returncode, child_elapsed, stdout, stderr = _run_command_streamed(
            command=command,
            env=env,
            log_path=case.run_log_path,
            safe_command=safe_command,
            timeout_sec=dentalseg_timeout_sec,
            append=dentalseg_model_zip is not None,
        )
        total_elapsed = time.perf_counter() - start
        extra["dentalsegmentator"]["child_elapsed_seconds"] = child_elapsed
        if returncode == 0:
            validation = _finalize_dentalseg_output(case=case)
            extra["dentalsegmentator"]["validation"] = validation
            extra["dentalsegmentator"]["output_labelmap"] = str(case.dentalseg_multilabel_path)
        else:
            extra["dentalsegmentator"]["error"] = stderr[-2000:] or stdout[-2000:]
        result = TotalSegRunResult(
            status="success" if returncode == 0 else "failed",
            returncode=returncode,
            elapsed_seconds=total_elapsed,
            requested_device=requested_device,
            actual_device=str(device_check.actual_device),
            fallback_reason=device_check.fallback_reason,
            task=task,
            output_dir=str(case.root),
            stdout_tail=stdout[-4000:],
            stderr_tail=stderr[-4000:],
            mps_state=_mps_state(device_check),
            execution_profile=execution_profile,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - start
        result = TotalSegRunResult(
            status="failed",
            returncode=1,
            elapsed_seconds=elapsed,
            requested_device=requested_device,
            actual_device=device_check.actual_device or "unknown",
            fallback_reason=device_check.fallback_reason,
            task=task,
            output_dir=str(case.root),
            stdout_tail="",
            stderr_tail=repr(exc),
            error_code="dentalseg_failed",
            safe_reason="DentalSegmentator did not complete.",
            mps_state=_mps_state(device_check),
            occurred_at=datetime.now(UTC).isoformat(),
            execution_profile=execution_profile,
        )
        case.run_log_path.parent.mkdir(parents=True, exist_ok=True)
        if not case.run_log_path.exists():
            case.run_log_path.write_text(
                "DENTALSEGMENTATOR FAILED\n" + repr(exc) + "\n",
                encoding="utf-8",
            )
        else:
            with case.run_log_path.open("a", encoding="utf-8") as handle:
                handle.write("\n\nDENTALSEGMENTATOR FAILED\n")
                handle.write(repr(exc))
                handle.write("\n")
        extra["dentalsegmentator"]["error"] = repr(exc)

    _write_metadata(
        case,
        input_path,
        task,
        result,
        device_check,
        backend="dentalsegmentator",
        extra=extra,
    )
    generate_output_report(
        case=case,
        source_volume_path=source_for_summary,
        task=task,
        run_result=result,
    )
    return result


def _prepare_dentalseg_input(*, input_path: Path, case: CaseOutput) -> Path:
    case.dentalseg_input_dir.mkdir(parents=True, exist_ok=True)
    destination = case.dentalseg_input_dir / "case_0000.nii.gz"
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    try:
        os.symlink(input_path.resolve(), destination)
    except OSError:
        shutil.copy2(input_path, destination)
    return destination


def _dentalseg_environment(
    *,
    case: CaseOutput,
    nnunet_raw: Path | None,
    nnunet_preprocessed: Path | None,
    nnunet_results: Path | None,
    require_results: bool,
) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PYTORCH_ENABLE_MPS_FALLBACK", None)
    env["nnUNet_raw"] = str((nnunet_raw or Path(env.get("nnUNet_raw", case.root / "nnunet_raw"))).resolve())
    env["nnUNet_preprocessed"] = str(
        (nnunet_preprocessed or Path(env.get("nnUNet_preprocessed", case.root / "nnunet_preprocessed"))).resolve()
    )
    if nnunet_results is not None:
        env["nnUNet_results"] = str(nnunet_results.resolve())
    elif "nnUNet_results" not in env and require_results:
        raise RuntimeError(
            "DentalSegmentator requires nnUNet_results or --dentalseg-model-dir. "
            "Install the Zenodo model zip with nnUNetv2_install_pretrained_model_from_zip "
            "or pass --dentalseg-model-dir."
        )
    for key in ("nnUNet_raw", "nnUNet_preprocessed", "nnUNet_results"):
        if key in env:
            Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env


def _dentalseg_install_command(model_zip: Path) -> list[str]:
    model_zip = model_zip.expanduser().resolve()
    if not model_zip.exists():
        raise FileNotFoundError(f"DentalSegmentator model zip not found: {model_zip}")
    executable = resolve_totalseg_executable("nnUNetv2_install_pretrained_model_from_zip")
    return [executable, str(model_zip)]


def _dentalseg_predict_command(
    *,
    case: CaseOutput,
    dentalseg_bin: str,
    model_dir: Path | None,
    dataset_id: str,
    configuration: str,
    trainer: str,
    plans: str,
    folds: tuple[str, ...],
    device: str,
    disable_tta: bool,
    not_on_device: bool,
    npp: int,
    nps: int,
) -> list[str]:
    case.dentalseg_predictions_dir.mkdir(parents=True, exist_ok=True)
    if model_dir is not None:
        model_dir = model_dir.expanduser().resolve()
        if not model_dir.exists():
            raise FileNotFoundError(f"DentalSegmentator model folder not found: {model_dir}")
        command = [
            resolve_totalseg_executable("nnUNetv2_predict_from_modelfolder"),
            "-i",
            str(case.dentalseg_input_dir),
            "-o",
            str(case.dentalseg_predictions_dir),
            "-m",
            str(model_dir),
        ]
    else:
        command = [
            resolve_totalseg_executable(dentalseg_bin),
            "-i",
            str(case.dentalseg_input_dir),
            "-o",
            str(case.dentalseg_predictions_dir),
            "-d",
            dataset_id,
            "-c",
            configuration,
            "-tr",
            trainer,
            "-p",
            plans,
        ]
    command.extend(["-f", *folds])
    command.extend(["-device", device, "-npp", str(npp), "-nps", str(nps)])
    if disable_tta:
        command.append("--disable_tta")
    if not_on_device:
        command.append("--not_on_device")
    return command


def _finalize_dentalseg_output(*, case: CaseOutput) -> dict[str, Any]:
    prediction = case.dentalseg_predictions_dir / "case.nii.gz"
    if not prediction.exists():
        raise FileNotFoundError(f"DentalSegmentator prediction not found: {prediction}")
    shutil.copy2(prediction, case.dentalseg_multilabel_path)

    import nibabel as nib
    import numpy as np

    image = nib.load(str(case.dentalseg_multilabel_path))
    data = np.asanyarray(image.dataobj)
    labels, counts = np.unique(data, return_counts=True)
    non_empty = []
    unexpected = []
    for label, count in zip(labels, counts, strict=True):
        label_int = int(label)
        if label_int == 0:
            continue
        item = {
            "label": label_int,
            "name": DENTALSEGMENTATOR_LABELS.get(label_int, f"label_{label_int}"),
            "voxels": int(count),
        }
        non_empty.append(item)
        if label_int not in DENTALSEGMENTATOR_LABELS:
            unexpected.append(label_int)
    if not non_empty:
        raise RuntimeError("DentalSegmentator output completed but contained no non-zero labels")
    sidecar = case.dentalseg_multilabel_path.with_name(
        case.dentalseg_multilabel_path.name + ".labels.json"
    )
    sidecar.write_text(
        json.dumps(
            {
                "source": "dentalsegmentator",
                "zenodo_doi": DENTALSEGMENTATOR_ZENODO_DOI,
                "labels": {str(label): name for label, name in DENTALSEGMENTATOR_LABELS.items()},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "path": str(case.dentalseg_multilabel_path.resolve()),
        "label_sidecar": str(sidecar.resolve()),
        "shape": [int(value) for value in image.shape[:3]],
        "spacing": [float(value) for value in image.header.get_zooms()[:3]],
        "non_empty_label_count": len(non_empty),
        "non_empty_labels": non_empty,
        "unexpected_labels": unexpected,
    }


def _sanitize_dentalseg_command(command: list[str], *, case: CaseOutput) -> list[str]:
    result = []
    skip_next = None
    for index, part in enumerate(command):
        if skip_next == index:
            continue
        if index == 0:
            result.append(Path(part).name)
        elif part in {"-i"} and index + 1 < len(command):
            result.extend([part, f"<input:{case.dentalseg_input_dir.name}>"])
            skip_next = index + 1
        elif part in {"-o"} and index + 1 < len(command):
            result.extend([part, f"<output:{case.dentalseg_predictions_dir.name}>"])
            skip_next = index + 1
        elif part in {"-m"} and index + 1 < len(command):
            result.extend([part, f"<model:{Path(command[index + 1]).name}>"])
            skip_next = index + 1
        elif part.endswith(".zip") and Path(part).exists():
            result.append(Path(part).name)
        else:
            result.append(part)
    return result


def _dentalseg_versions() -> dict[str, str]:
    versions = {}
    for package in ("nnunetv2", "torch", "nibabel"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "not_installed"
    return versions


def _teeth_child_command(
    *,
    input_path: Path,
    output_path: Path,
    benchmark_path: Path,
    dry_run: bool,
    force_split: bool,
    higher_order_resampling: bool,
) -> list[str]:
    command = [
        sys.executable,
        "-u",
        "-m",
        "totalsegmentator_wrapper_mac.teeth_mps_child",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--benchmark-json",
        str(benchmark_path),
        "--require-totalseg-version",
        "2.14.0",
        "--ml",
        "--verbose",
        "--nr-thr-resamp",
        "1",
        "--nr-thr-saving",
        "1",
    ]
    if dry_run:
        command.append("--dry-run")
    if force_split:
        command.append("--force-split")
    if higher_order_resampling:
        command.append("--higher-order-resampling")
    return command


def _sanitize_teeth_child_command(command: list[str], input_path: Path, output_dir: Path) -> list[str]:
    result = []
    skip_next = None
    for index, part in enumerate(command):
        if skip_next == index:
            continue
        if part == "--input" and index + 1 < len(command):
            result.extend([part, input_path.name])
            skip_next = index + 1
        elif part == "--output" and index + 1 < len(command):
            result.extend([part, f"<output:{output_dir.name}>"])
            skip_next = index + 1
        elif part == "--benchmark-json" and index + 1 < len(command):
            result.extend([part, "<logs:teeth_child_benchmark.json>"])
            skip_next = index + 1
        else:
            result.append(part)
    return result


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _append_run_log_warning(log_path: Path, warning: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n\nWARNING:\n")
        handle.write(warning)
        handle.write("\n")


def _extract_last_progress(*streams: str) -> dict[str, Any] | None:
    last = None
    for text in streams:
        for line in re.split(r"[\r\n]+", text):
            progress = parse_tqdm_progress(line)
            if progress and progress.get("step") is not None and progress.get("total") is not None:
                last = progress
    return last


def parse_tqdm_progress(text: str) -> dict[str, Any] | None:
    line = text.replace("\r", "").strip()
    if not line or "Collecting results" in line:
        return None

    count_match = None
    for match in PROGRESS_RE.finditer(line):
        count_match = match
    percent_match = PROGRESS_PERCENT_RE.search(line)
    stage = progress_stage_from_line(line) or progress_phase_from_line(line)
    if count_match is None and percent_match is None:
        if not stage:
            return None
        return {
            "step": None,
            "total": None,
            "percent": None,
            "stage": stage,
            "line": line[-240:],
            "phase_only": True,
        }

    step: int | None = None
    total: int | None = None
    percent: int | None = int(percent_match.group(1)) if percent_match else None
    if count_match is not None:
        step = int(count_match.group(1))
        total = int(count_match.group(2))
        if total > 0 and percent is None:
            percent = max(0, min(100, round(step * 100 / total)))
    if percent is not None:
        percent = max(0, min(100, percent))

    progress: dict[str, Any] = {
        "step": step,
        "total": total,
        "percent": percent,
        "line": line[-240:],
    }
    if stage:
        progress["stage"] = stage
    return progress


def progress_stage_from_line(line: str) -> str | None:
    match = PROGRESS_STAGE_RE.search(line)
    if not match:
        return None
    stage = " ".join(match.group(1).strip().split())
    return stage or None


def progress_phase_from_line(line: str) -> str | None:
    match = PROGRESS_PHASE_RE.search(line)
    if not match:
        return None
    stage = " ".join(match.group(1).strip().rstrip(":.").split())
    return stage or None


def _progress_key(progress: dict[str, Any], stream_label: str) -> tuple[Any, ...]:
    return (
        stream_label,
        progress.get("stage"),
        progress.get("step"),
        progress.get("total"),
        progress.get("percent"),
    )


def _run_command_streamed(
    *,
    command: list[str],
    env: dict[str, str],
    log_path: Path,
    safe_command: list[str],
    timeout_sec: int | None = None,
    append: bool = False,
) -> tuple[int, float, str, str]:
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    lock = threading.Lock()
    last_progress_keys: dict[str, tuple[Any, ...]] = {}
    current_progress_stage: dict[str, str] = {}

    def pump(stream: Any, label: str, chunks: list[str]) -> None:
        segment: list[str] = []

        def flush_segment() -> None:
            raw = "".join(segment)
            segment.clear()
            if not raw:
                return
            progress = parse_tqdm_progress(raw)
            with lock:
                log_file.write(raw.rstrip("\r\n"))
                log_file.write("\n")
                if progress is not None:
                    progress["stream"] = label
                    stage = progress.get("stage")
                    if isinstance(stage, str) and stage:
                        current_progress_stage[label] = stage
                    elif label in current_progress_stage:
                        progress["stage"] = current_progress_stage[label]
                    key = _progress_key(progress, label)
                    if last_progress_keys.get(label) != key:
                        last_progress_keys[label] = key
                        log_file.write(
                            RUN_PROGRESS_PREFIX
                            + json.dumps(progress, ensure_ascii=False, sort_keys=True)
                            + "\n"
                        )
                log_file.flush()

        with lock:
            log_file.write(f"\n\n{label}:\n")
            log_file.flush()
        try:
            while True:
                char = stream.read(1)
                if char == "":
                    break
                chunks.append(char)
                if char in ("\n", "\r"):
                    flush_segment()
                else:
                    segment.append(char)
            flush_segment()
        finally:
            stream.close()

    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with log_path.open("a" if append else "w", encoding="utf-8") as log_file:
        if append:
            log_file.write("\n\n")
        log_file.write("COMMAND:\n" + " ".join(safe_command))
        log_file.flush()
        proc = subprocess.Popen(  # noqa: S603
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=True,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None
        stdout_thread = threading.Thread(
            target=pump, args=(proc.stdout, "STDOUT", stdout_chunks), daemon=True
        )
        stderr_thread = threading.Thread(
            target=pump, args=(proc.stderr, "STDERR", stderr_chunks), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            returncode = proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            with lock:
                log_file.write(f"\n\nTIMEOUT after {timeout_sec} seconds; terminating child.\n")
                log_file.flush()
            _terminate_process_group(proc)
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)
            elapsed = time.perf_counter() - start
            return 124, elapsed, "".join(stdout_chunks), "".join(stderr_chunks)
        except BaseException:
            _terminate_process_group(proc)
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)
            raise
        else:
            stdout_thread.join()
            stderr_thread.join()
    elapsed = time.perf_counter() - start
    return returncode, elapsed, "".join(stdout_chunks), "".join(stderr_chunks)


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def _write_metadata(
    case: CaseOutput,
    input_path: Path,
    task: str,
    result: TotalSegRunResult,
    device_check: DeviceCheck,
    robust_crop: bool = False,
    higher_order_resampling: bool = False,
    backend: str = "totalsegmentator",
    extra: dict[str, Any] | None = None,
) -> None:
    env = environment_metadata()
    safe_fields = _result_safe_fields(result)
    write_json(case.environment_path, env)
    benchmark = {
        "app_version": f"{__version__}-preview",
        "environment": env,
        "input": input_metadata(input_path),
        "run": {
            "backend": backend,
            "task": task,
            "requested_device": result.requested_device,
            "actual_device": result.actual_device,
            "fallback_reason": result.fallback_reason,
            "elapsed_seconds": result.elapsed_seconds,
            "status": result.status,
            "returncode": result.returncode,
            "error_code": safe_fields["error_code"],
            "safe_reason": safe_fields["safe_reason"],
            "mps_state": safe_fields["mps_state"],
            "occurred_at": safe_fields["occurred_at"],
            "execution_profile": result.execution_profile,
            "robust_crop": robust_crop,
            "higher_order_resampling": higher_order_resampling,
        },
        "device_check": device_check.to_dict(),
    }
    if extra:
        benchmark.update(extra)
    write_json(case.benchmark_path, benchmark)
