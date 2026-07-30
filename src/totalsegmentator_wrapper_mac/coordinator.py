from __future__ import annotations

import codecs
import hashlib
import json
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from totalsegmentator_wrapper_mac.coordinator_protocol import (
    CANCEL_CONTROL,
    CAPABILITIES_OPERATION,
    PROTOCOL_VERSION,
    RUN_NIFTI_TOTALSEG_OPERATION,
    CoordinatorProtocolError,
    CoordinatorRequest,
    JsonlEventWriter,
    OperationCancelled,
    parse_coordinator_control,
    parse_coordinator_request,
    safe_operation_id,
)


SegmentationRunner = Callable[..., Any]
PreviewRunner = Callable[..., dict[str, Any]]
CudaDeviceChecker = Callable[[int], Any]
ShouldCancel = Callable[[], bool]
StartCancelListener = Callable[[], None]


class ArtifactVerificationError(RuntimeError):
    pass


def run_coordinator_request(
    request: CoordinatorRequest,
    writer: JsonlEventWriter,
    *,
    segmentation_runner: SegmentationRunner | None = None,
    preview_runner: PreviewRunner | None = None,
    cuda_device_checker: CudaDeviceChecker | None = None,
    should_cancel: ShouldCancel | None = None,
    start_cancel_listener: StartCancelListener | None = None,
) -> int:
    writer.emit("operation_started", operation=request.operation)
    if _cancellation_requested(should_cancel):
        return _emit_cancelled(writer)
    if request.operation == CAPABILITIES_OPERATION:
        writer.emit(
            "capabilities",
            operations=[CAPABILITIES_OPERATION, RUN_NIFTI_TOTALSEG_OPERATION],
            device_policies={
                "cpu_required": {"implementation": "available"},
                "cuda_required": {
                    "implementation": "available",
                    "verification": "strict_per_operation",
                },
            },
            cancellation={
                "graceful_control": "available",
                "authoritative_windows_job": "unverified",
            },
            input_kinds=["nifti"],
        )
        writer.emit("operation_completed", status="success")
        return 0

    if request.operation != RUN_NIFTI_TOTALSEG_OPERATION:
        return _emit_failure(
            writer,
            code="operation_unsupported",
            safe_reason="The coordinator operation is not supported.",
        )
    if request.device_policy not in {"cpu_required", "cuda_required"}:
        return _emit_failure(
            writer,
            code="device_policy_unsupported",
            safe_reason="The requested device policy is not supported.",
        )

    assert request.input_path is not None
    assert request.output_directory is not None
    if not request.input_path.is_file():
        return _emit_failure(
            writer,
            code="input_not_found",
            safe_reason="The selected NIfTI input could not be opened.",
        )
    final_case_directory = request.output_directory
    if final_case_directory.exists():
        return _emit_failure(
            writer,
            code="output_already_exists",
            safe_reason="The selected output location already exists.",
        )
    final_case_directory.parent.mkdir(parents=True, exist_ok=True)
    staging_directory = final_case_directory.parent / (
        f".tswm-{request.operation_id}.staging"
    )
    if staging_directory.exists():
        return _emit_failure(
            writer,
            code="staging_already_exists",
            safe_reason="An interrupted staging operation already exists.",
        )
    if _cancellation_requested(should_cancel):
        return _emit_cancelled(writer)

    expected_device = "cpu"
    prevalidated_device_check = None
    device_event_emitted = False
    if request.device_policy == "cuda_required":
        assert request.device_index is not None
        if cuda_device_checker is None:
            from totalsegmentator_wrapper_mac.device import smoke_test_cuda

            cuda_device_checker = smoke_test_cuda
        prevalidated_device_check = cuda_device_checker(request.device_index)
        if _cancellation_requested(should_cancel):
            return _emit_cancelled(writer)
        expected_device = f"cuda:{request.device_index}"
        if (
            getattr(prevalidated_device_check, "status", None) != "pass"
            or getattr(prevalidated_device_check, "actual_device", None)
            != expected_device
        ):
            writer.emit(
                "device_resolved",
                requested_policy=request.device_policy,
                requested_device_index=request.device_index,
                resolved_device=None,
                fallback_allowed=False,
                fallback_occurred=False,
            )
            return _emit_failure(
                writer,
                code=getattr(prevalidated_device_check, "error_code", None)
                or "cuda_validation_failed",
                safe_reason="The required CUDA device did not pass strict validation.",
            )
        writer.emit(
            "device_resolved",
            requested_policy=request.device_policy,
            requested_device_index=request.device_index,
            resolved_device=expected_device,
            fallback_allowed=False,
            fallback_occurred=False,
        )
        device_event_emitted = True

    if segmentation_runner is None:
        from totalsegmentator_wrapper_mac.runner_totalseg import run_totalsegmentator

        segmentation_runner = run_totalsegmentator
    if preview_runner is None:
        from totalsegmentator_wrapper_mac.surface_preview import run_surface_preview

        preview_runner = run_surface_preview
    if start_cancel_listener is not None:
        start_cancel_listener()

    def on_runner_event(event: str, payload: dict[str, Any]) -> None:
        if event == "phase_started":
            writer.emit(
                "phase_started",
                route=payload.get("route"),
                stage_id=payload.get("stage_id"),
                index=payload.get("index"),
                total=payload.get("total"),
                label=payload.get("label"),
            )
        elif event == "progress":
            safe_progress = {
                key: payload.get(key)
                for key in (
                    "route",
                    "stage_id",
                    "scope",
                    "stage",
                    "step",
                    "total",
                    "percent",
                    "eta_seconds",
                    "phase_only",
                )
                if key in payload
            }
            writer.emit("progress", **safe_progress)

    try:
        _raise_if_cancelled(should_cancel)
        runner_kwargs: dict[str, Any] = {
            "input_path": request.input_path,
            "output_root": staging_directory,
            "task": "craniofacial_structures",
            "requested_device": expected_device,
            "backend": "totalsegmentator",
            "copy_input": False,
            "robust_crop": request.robust_crop,
            "higher_order_resampling": request.higher_order_resampling,
            "event_sink": on_runner_event,
        }
        if prevalidated_device_check is not None:
            runner_kwargs["prevalidated_device_check"] = prevalidated_device_check
        if should_cancel is not None:
            runner_kwargs["should_cancel"] = should_cancel
        result = segmentation_runner(
            **runner_kwargs,
        )
        _raise_if_cancelled(should_cancel)
        actual_device = str(getattr(result, "actual_device", "unknown"))
        fallback_reason = getattr(result, "fallback_reason", None)
        if not device_event_emitted:
            writer.emit(
                "device_resolved",
                requested_policy=request.device_policy,
                requested_device_index=request.device_index,
                resolved_device=actual_device,
                fallback_allowed=False,
                fallback_occurred=fallback_reason is not None,
            )
        if actual_device != expected_device or fallback_reason is not None:
            return _emit_failure(
                writer,
                code="unexpected_device_fallback",
                safe_reason="The backend did not preserve the required device policy.",
            )
        if getattr(result, "status", None) != "success":
            return _emit_failure(
                writer,
                code=getattr(result, "error_code", None) or "backend_failed",
                safe_reason=getattr(result, "safe_reason", None)
                or "The segmentation backend did not complete.",
            )

        case_directory = Path(result.output_dir)
        if case_directory.resolve() != staging_directory.resolve():
            return _emit_failure(
                writer,
                code="backend_output_mismatch",
                safe_reason="The backend returned an unexpected output location.",
            )
        _raise_if_cancelled(should_cancel)
        writer.emit(
            "phase_started",
            route="totalsegmentator",
            stage_id="preview",
            index=4,
            total=4,
            label="3D表示・結果情報を作成中",
        )
        _raise_if_cancelled(should_cancel)
        preview_runner(
            case_dir=case_directory,
            detailed_stl=True,
        )
        _raise_if_cancelled(should_cancel)
        preview_path = case_directory / "surface_preview" / "index.html"
        if not preview_path.is_file():
            return _emit_failure(
                writer,
                code="preview_missing",
                safe_reason="The offline preview was not created.",
            )
        try:
            _raise_if_cancelled(should_cancel)
            artifacts = _verify_and_manifest_case(
                case_directory,
                operation_id=request.operation_id,
                expected_device=expected_device,
                requested_policy=request.device_policy,
                requested_device_index=request.device_index,
            )
        except ArtifactVerificationError:
            return _emit_failure(
                writer,
                code="artifact_verification_failed",
                safe_reason="The case output did not pass artifact verification.",
            )
        _raise_if_cancelled(should_cancel)
        if final_case_directory.exists():
            return _emit_failure(
                writer,
                code="output_commit_conflict",
                safe_reason="The selected output location changed during processing.",
            )
        _raise_if_cancelled(should_cancel)
        staging_directory.rename(final_case_directory)
        for artifact in artifacts:
            writer.emit("artifact_created", **artifact)
        writer.emit(
            "operation_completed",
            status="success",
            requested_policy=request.device_policy,
            requested_device_index=request.device_index,
            resolved_device=actual_device,
            fallback_allowed=False,
            fallback_occurred=False,
            elapsed_seconds=float(getattr(result, "elapsed_seconds", 0.0)),
            artifacts=artifacts,
        )
        return 0
    except OperationCancelled:
        if writer.terminal_event is not None:
            return 3
        return _emit_cancelled(writer)
    except Exception as exc:  # noqa: BLE001
        print(
            f"coordinator diagnostic: {type(exc).__name__}",
            file=sys.stderr,
            flush=True,
        )
        if writer.terminal_event is not None:
            return 1
        return _emit_failure(
            writer,
            code="coordinator_operation_failed",
            safe_reason="The coordinator operation did not complete.",
        )


def _read_initial_json_document(stream: TextIO) -> Any:
    decoder = json.JSONDecoder()
    buffer = ""
    byte_reader = _single_byte_reader(stream)
    while True:
        character = byte_reader() if byte_reader is not None else stream.read(1)
        if character == "":
            return json.loads(buffer)
        buffer += character
        candidate = buffer.lstrip()
        if not candidate:
            continue
        try:
            payload, _end = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        return payload


def _single_byte_reader(stream: TextIO) -> Callable[[], str] | None:
    try:
        file_descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return None
    encoding = getattr(stream, "encoding", None) or "utf-8"
    errors = getattr(stream, "errors", None) or "strict"
    decoder = codecs.getincrementaldecoder(encoding)(errors=errors)

    def read_character() -> str:
        while True:
            chunk = os.read(file_descriptor, 1)
            if chunk == b"":
                return decoder.decode(b"", final=True)
            character = decoder.decode(chunk)
            if character:
                return character

    return read_character


def _read_cancel_controls(
    stream: TextIO,
    *,
    operation_id: str,
    cancel_event: threading.Event,
) -> None:
    while True:
        line = stream.readline()
        if line == "":
            return
        if not line.strip():
            continue
        try:
            control = parse_coordinator_control(json.loads(line))
        except (CoordinatorProtocolError, json.JSONDecodeError):
            print(
                "coordinator diagnostic: control message ignored",
                file=sys.stderr,
                flush=True,
            )
            continue
        if (
            control.protocol_version == PROTOCOL_VERSION
            and control.operation_id == operation_id
            and control.control == CANCEL_CONTROL
        ):
            cancel_event.set()
            return


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print("coordinator diagnostic: arguments are not accepted", file=sys.stderr)
        return 2
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    operation_id = "unknown"
    try:
        payload = _read_initial_json_document(input_stream)
        if isinstance(payload, dict):
            operation_id = safe_operation_id(payload.get("operation_id"))
        request = parse_coordinator_request(payload)
        writer = JsonlEventWriter(
            output_stream,
            operation_id=request.operation_id,
        )
        cancel_event: threading.Event | None = None
        start_cancel_listener: StartCancelListener | None = None
        if request.operation == RUN_NIFTI_TOTALSEG_OPERATION:
            cancel_event = threading.Event()
            listener_started = False

            def start_cancel_listener() -> None:
                nonlocal listener_started
                if listener_started:
                    return
                listener_started = True
                threading.Thread(
                    target=_read_cancel_controls,
                    kwargs={
                        "stream": input_stream,
                        "operation_id": request.operation_id,
                        "cancel_event": cancel_event,
                    },
                    daemon=True,
                    name="coordinator-control-reader",
                ).start()

        return run_coordinator_request(
            request,
            writer,
            should_cancel=cancel_event.is_set if cancel_event is not None else None,
            start_cancel_listener=start_cancel_listener,
        )
    except json.JSONDecodeError:
        writer = JsonlEventWriter(output_stream, operation_id=operation_id)
        return _emit_failure(
            writer,
            code="request_json_invalid",
            safe_reason="The coordinator request is not valid JSON.",
        )
    except CoordinatorProtocolError as exc:
        writer = JsonlEventWriter(
            output_stream,
            operation_id=operation_id,
            protocol_version=PROTOCOL_VERSION,
        )
        return _emit_failure(
            writer,
            code=exc.code,
            safe_reason=exc.safe_reason,
        )


def _cancellation_requested(should_cancel: ShouldCancel | None) -> bool:
    return should_cancel is not None and should_cancel()


def _raise_if_cancelled(should_cancel: ShouldCancel | None) -> None:
    if _cancellation_requested(should_cancel):
        raise OperationCancelled


def _emit_cancelled(writer: JsonlEventWriter) -> int:
    writer.emit(
        "operation_cancelled",
        status="cancelled",
        reason_code="cancel_requested",
    )
    return 3


def _emit_failure(
    writer: JsonlEventWriter,
    *,
    code: str,
    safe_reason: str,
) -> int:
    writer.emit(
        "operation_failed",
        status="failed",
        error_code=code,
        safe_reason=safe_reason,
    )
    return 2


def _verify_and_manifest_case(
    case_directory: Path,
    *,
    operation_id: str,
    expected_device: str,
    requested_policy: str,
    requested_device_index: int | None,
) -> list[dict[str, Any]]:
    try:
        required_files = {
            "report": case_directory / "README_OUTPUT.md",
            "run_log": case_directory / "logs" / "run.log",
            "benchmark": case_directory / "logs" / "benchmark.json",
            "environment": case_directory / "logs" / "environment.json",
            "mask_stats": case_directory / "logs" / "mask_stats.json",
            "offline_preview": case_directory / "surface_preview" / "index.html",
        }
        if any(
            not path.is_file() or path.stat().st_size <= 0
            for path in required_files.values()
        ):
            raise ArtifactVerificationError

        benchmark = _read_json_object(required_files["benchmark"])
        run = benchmark.get("run")
        if (
            not isinstance(run, dict)
            or run.get("status") != "success"
            or run.get("backend") != "totalsegmentator"
            or run.get("task") != "craniofacial_structures"
            or run.get("requested_device") != expected_device
            or run.get("actual_device") != expected_device
            or run.get("fallback_reason") is not None
        ):
            raise ArtifactVerificationError
        _read_json_object(required_files["environment"])
        environment = benchmark.get("environment")
        if not isinstance(environment, dict):
            environment = {}
        device_check = benchmark.get("device_check")
        if not isinstance(device_check, dict):
            device_check = {}
        run_manifest_path = case_directory / "run-manifest.json"
        run_manifest_path.write_text(
            json.dumps(
                {
                    "schema": "totalsegmentator_wrapper.run_manifest.v1",
                    "protocol_version": PROTOCOL_VERSION,
                    "operation_id": operation_id,
                    "requested_policy": requested_policy,
                    "requested_device_index": requested_device_index,
                    "requested_device": expected_device,
                    "resolved_device": expected_device,
                    "fallback_allowed": False,
                    "fallback_occurred": False,
                    "runtime": {
                        "python_version": (
                            environment.get("python", {}).get("version")
                            if isinstance(environment.get("python"), dict)
                            else None
                        ),
                        "torch_version": device_check.get("torch_version"),
                        "cuda_build": device_check.get("cuda_build"),
                        "cuda_available": device_check.get("cuda_available"),
                        "cuda_device_count": device_check.get(
                            "cuda_device_count"
                        ),
                        "device_name": device_check.get("device_name"),
                        "device_index": device_check.get("device_index"),
                        "compute_capability": device_check.get(
                            "compute_capability"
                        ),
                        "total_memory_bytes": device_check.get(
                            "total_memory_bytes"
                        ),
                        "driver_version": device_check.get("driver_version"),
                    },
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        required_files["run_manifest"] = run_manifest_path

        mask_stats = _read_json_object(required_files["mask_stats"])
        masks = mask_stats.get("masks")
        mask_count = mask_stats.get("mask_count")
        if (
            not isinstance(masks, list)
            or isinstance(mask_count, bool)
            or not isinstance(mask_count, int)
            or mask_count != len(masks)
            or not masks
        ):
            raise ArtifactVerificationError

        raw_masks = sorted(
            (case_directory / "segmentations" / "raw_totalseg").glob("*.nii.gz")
        )
        if not raw_masks:
            raise ArtifactVerificationError
        mask_names = {path.name for path in raw_masks}
        stats_names = {
            item.get("name")
            for item in masks
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        if stats_names != mask_names:
            raise ArtifactVerificationError
        valid_nonempty_names = {
            item.get("name")
            for item in masks
            if isinstance(item, dict)
            and item.get("status") == "ok"
            and isinstance(item.get("nonzero_voxels"), int)
            and not isinstance(item.get("nonzero_voxels"), bool)
            and item["nonzero_voxels"] > 0
        }
        if not mask_names.intersection(valid_nonempty_names):
            raise ArtifactVerificationError
        mask_stats["mask_dir"] = "segmentations/raw_totalseg"
        for item in masks:
            item["path"] = (
                Path("segmentations") / "raw_totalseg" / str(item["name"])
            ).as_posix()
        required_files["mask_stats"].write_text(
            json.dumps(mask_stats, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

        manifest_entries = []
        for path in [*required_files.values(), *raw_masks]:
            relative_path = path.relative_to(case_directory).as_posix()
            manifest_entries.append(
                {
                    "relative_path": relative_path,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
        manifest_path = case_directory / "artifact-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema": "totalsegmentator_wrapper.coordinator_artifacts.v1",
                    "protocol_version": PROTOCOL_VERSION,
                    "operation_id": operation_id,
                    "artifacts": manifest_entries,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        if not manifest_path.is_file() or manifest_path.stat().st_size <= 0:
            raise ArtifactVerificationError
    except ArtifactVerificationError:
        raise
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ArtifactVerificationError from exc

    return [
        {"kind": "report", "relative_path": "README_OUTPUT.md"},
        {
            "kind": "segmentation_directory",
            "relative_path": "segmentations/raw_totalseg",
        },
        {
            "kind": "offline_preview",
            "relative_path": "surface_preview/index.html",
        },
        {"kind": "run_manifest", "relative_path": "run-manifest.json"},
        {"kind": "artifact_manifest", "relative_path": "artifact-manifest.json"},
    ]


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ArtifactVerificationError
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
