from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO


PROTOCOL_VERSION = 1
CAPABILITIES_OPERATION = "capabilities"
RUN_NIFTI_TOTALSEG_OPERATION = "run_nifti_totalsegmentator"
RUN_NIFTI_DENTALSEG_OPERATION = "run_nifti_dentalsegmentator"
RUN_NIFTI_INDIVIDUAL_TEETH_OPERATION = "run_nifti_individual_teeth"
CANCEL_CONTROL = "cancel"
SUPPORTED_OPERATIONS = frozenset(
    {
        CAPABILITIES_OPERATION,
        RUN_NIFTI_DENTALSEG_OPERATION,
        RUN_NIFTI_INDIVIDUAL_TEETH_OPERATION,
        RUN_NIFTI_TOTALSEG_OPERATION,
    }
)
SUPPORTED_DEVICE_POLICIES = frozenset({"cpu_required", "cuda_required"})
TERMINAL_EVENTS = frozenset(
    {
        "operation_completed",
        "operation_failed",
        "operation_cancelled",
    }
)
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_FORBIDDEN_REQUEST_FIELDS = frozenset(
    {
        "backend",
        "task",
        "skip_device_check",
        "totalseg_bin",
        "environment",
        "model_path",
    }
)


class CoordinatorProtocolError(ValueError):
    def __init__(self, code: str, safe_reason: str) -> None:
        super().__init__(safe_reason)
        self.code = code
        self.safe_reason = safe_reason


class OperationCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class CoordinatorRequest:
    protocol_version: int
    operation_id: str
    operation: str
    input_path: Path | None = None
    output_directory: Path | None = None
    device_policy: str | None = None
    device_index: int | None = None
    robust_crop: bool = True
    higher_order_resampling: bool = False


@dataclass(frozen=True)
class CoordinatorControl:
    protocol_version: int
    operation_id: str
    control: str


class JsonlEventWriter:
    def __init__(
        self,
        stream: TextIO,
        *,
        operation_id: str,
        protocol_version: int = PROTOCOL_VERSION,
    ) -> None:
        self._stream = stream
        self._operation_id = operation_id
        self._protocol_version = protocol_version
        self._sequence = 0
        self._terminal_event: str | None = None
        self._lock = threading.Lock()

    @property
    def terminal_event(self) -> str | None:
        return self._terminal_event

    def emit(self, event: str, **payload: Any) -> dict[str, Any]:
        if not isinstance(event, str) or not event:
            raise ValueError("event must be a non-empty string")
        reserved = {"protocol_version", "operation_id", "sequence", "event"}
        if reserved.intersection(payload):
            raise ValueError("event payload contains a reserved envelope field")
        with self._lock:
            if self._terminal_event is not None:
                raise RuntimeError("cannot emit an event after the terminal event")
            self._sequence += 1
            envelope = {
                "protocol_version": self._protocol_version,
                "operation_id": self._operation_id,
                "sequence": self._sequence,
                "event": event,
                **payload,
            }
            self._stream.write(
                json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n"
            )
            self._stream.flush()
            if event in TERMINAL_EVENTS:
                self._terminal_event = event
            return envelope


def safe_operation_id(value: Any) -> str:
    if isinstance(value, str) and _OPERATION_ID_RE.fullmatch(value) is not None:
        return value
    return "unknown"


def parse_coordinator_request(payload: Any) -> CoordinatorRequest:
    request = _require_mapping(payload, code="request_invalid", name="request")
    if _FORBIDDEN_REQUEST_FIELDS.intersection(request):
        raise CoordinatorProtocolError(
            "request_field_forbidden",
            "The coordinator request contains a forbidden field.",
        )
    protocol_version = request.get("protocol_version")
    if isinstance(protocol_version, bool) or protocol_version != PROTOCOL_VERSION:
        raise CoordinatorProtocolError(
            "protocol_version_unsupported",
            "The coordinator protocol version is not supported.",
        )

    operation_id = request.get("operation_id")
    if safe_operation_id(operation_id) == "unknown" and operation_id != "unknown":
        raise CoordinatorProtocolError(
            "operation_id_invalid",
            "The operation identifier is invalid.",
        )

    operation = request.get("operation")
    if operation not in SUPPORTED_OPERATIONS:
        raise CoordinatorProtocolError(
            "operation_unsupported",
            "The coordinator operation is not supported.",
        )

    if operation == CAPABILITIES_OPERATION:
        return CoordinatorRequest(
            protocol_version=protocol_version,
            operation_id=operation_id,
            operation=operation,
        )

    input_payload = _require_mapping(
        request.get("input"),
        code="input_invalid",
        name="input",
    )
    if input_payload.get("kind") != "nifti":
        raise CoordinatorProtocolError(
            "input_kind_unsupported",
            "The coordinator accepts NIfTI input only.",
        )
    input_path = _require_path(input_payload.get("path"), code="input_path_invalid")
    if not (input_path.name.endswith(".nii") or input_path.name.endswith(".nii.gz")):
        raise CoordinatorProtocolError(
            "input_format_unsupported",
            "The coordinator accepts .nii or .nii.gz input only.",
        )

    output_directory = _require_path(
        request.get("output_directory"),
        code="output_directory_invalid",
    )
    device_payload = _require_mapping(
        request.get("device_policy"),
        code="device_policy_invalid",
        name="device_policy",
    )
    device_policy = device_payload.get("mode")
    if device_policy not in SUPPORTED_DEVICE_POLICIES:
        raise CoordinatorProtocolError(
            "device_policy_unsupported",
            "The requested device policy is not supported.",
        )
    raw_device_index = device_payload.get("index")
    device_index: int | None = None
    if device_policy == "cuda_required":
        if raw_device_index is None:
            device_index = 0
        elif (
            isinstance(raw_device_index, bool)
            or not isinstance(raw_device_index, int)
            or raw_device_index < 0
            or raw_device_index > 63
        ):
            raise CoordinatorProtocolError(
                "device_index_invalid",
                "The requested CUDA device index is invalid.",
            )
        else:
            device_index = raw_device_index
    elif raw_device_index is not None:
        raise CoordinatorProtocolError(
            "device_index_invalid",
            "A CPU device policy cannot select a device index.",
        )

    options_payload = request.get("options", {})
    options = _require_mapping(
        options_payload,
        code="options_invalid",
        name="options",
    )
    robust_crop = _optional_bool(
        options,
        "robust_crop",
        default=operation == RUN_NIFTI_TOTALSEG_OPERATION,
    )
    higher_order_resampling = _optional_bool(
        options,
        "higher_order_resampling",
        default=False,
    )
    if operation in {
        RUN_NIFTI_DENTALSEG_OPERATION,
        RUN_NIFTI_INDIVIDUAL_TEETH_OPERATION,
    } and (
        robust_crop or higher_order_resampling
    ):
        raise CoordinatorProtocolError(
            "options_unsupported",
            "The selected coordinator operation does not support these options.",
        )

    return CoordinatorRequest(
        protocol_version=protocol_version,
        operation_id=operation_id,
        operation=operation,
        input_path=input_path,
        output_directory=output_directory,
        device_policy=device_policy,
        device_index=device_index,
        robust_crop=robust_crop,
        higher_order_resampling=higher_order_resampling,
    )


def parse_coordinator_control(payload: Any) -> CoordinatorControl:
    control = _require_mapping(payload, code="control_invalid", name="control")
    protocol_version = control.get("protocol_version")
    if isinstance(protocol_version, bool) or protocol_version != PROTOCOL_VERSION:
        raise CoordinatorProtocolError(
            "control_protocol_version_unsupported",
            "The coordinator control protocol version is not supported.",
        )
    operation_id = control.get("operation_id")
    if safe_operation_id(operation_id) == "unknown" and operation_id != "unknown":
        raise CoordinatorProtocolError(
            "control_operation_id_invalid",
            "The coordinator control operation identifier is invalid.",
        )
    control_name = control.get("control")
    if control_name != CANCEL_CONTROL:
        raise CoordinatorProtocolError(
            "control_unsupported",
            "The coordinator control message is not supported.",
        )
    return CoordinatorControl(
        protocol_version=protocol_version,
        operation_id=operation_id,
        control=control_name,
    )


def _require_mapping(value: Any, *, code: str, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CoordinatorProtocolError(code, f"The {name} object is invalid.")
    if not all(isinstance(key, str) for key in value):
        raise CoordinatorProtocolError(code, f"The {name} object is invalid.")
    return value


def _require_path(value: Any, *, code: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CoordinatorProtocolError(code, "A coordinator path is invalid.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise CoordinatorProtocolError(
            code,
            "A coordinator path must be absolute.",
        )
    return path


def _optional_bool(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise CoordinatorProtocolError(
            "options_invalid",
            "A coordinator option is invalid.",
        )
    return value
