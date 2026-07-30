from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import nibabel as nib
import numpy as np

from totalsegmentator_wrapper_mac.coordinator import (
    _read_cancel_controls,
    _read_initial_json_document,
    run_coordinator_request,
)
from totalsegmentator_wrapper_mac.coordinator_protocol import (
    PROTOCOL_VERSION,
    CoordinatorProtocolError,
    JsonlEventWriter,
    OperationCancelled,
    parse_coordinator_control,
    parse_coordinator_request,
)
from totalsegmentator_wrapper_mac.runner_totalseg import TotalSegRunResult
from totalsegmentator_wrapper_mac.runner_totalseg import run_totalsegmentator


def _run_request_payload(root: Path) -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "operation_id": "test-operation",
        "operation": "run_nifti_totalsegmentator",
        "input": {
            "kind": "nifti",
            "path": str(root / "private-input.nii.gz"),
        },
        "output_directory": str(root / "private-output"),
        "device_policy": {"mode": "cpu_required"},
        "options": {
            "robust_crop": True,
            "higher_order_resampling": False,
        },
    }


def _dentalseg_run_request_payload(root: Path) -> dict[str, object]:
    payload = _run_request_payload(root)
    payload["operation"] = "run_nifti_dentalsegmentator"
    payload["options"] = {
        "robust_crop": False,
        "higher_order_resampling": False,
    }
    return payload


def _events(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def _write_fake_success_case(
    case_directory: Path,
    *,
    device: str = "cpu",
    backend: str = "totalsegmentator",
) -> None:
    raw = (
        case_directory / "segmentations" / "dentalsegmentator"
        if backend == "dentalsegmentator"
        else case_directory / "segmentations" / "raw_totalseg"
    )
    logs = case_directory / "logs"
    raw.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    data = np.zeros((4, 4, 4), dtype=np.uint8)
    data[1:3, 1:3, 1:3] = 1
    mask = raw / (
        "dentalsegmentator_multilabel.nii.gz"
        if backend == "dentalsegmentator"
        else "mandible.nii.gz"
    )
    nib.save(nib.Nifti1Image(data, np.eye(4)), mask)
    if backend == "dentalsegmentator":
        (raw / "dentalsegmentator_multilabel.nii.gz.labels.json").write_text(
            '{"1":"lower_jawbone"}',
            encoding="utf-8",
        )
    (case_directory / "README_OUTPUT.md").write_text("report", encoding="utf-8")
    (logs / "run.log").write_text("safe run log", encoding="utf-8")
    (logs / "environment.json").write_text("{}\n", encoding="utf-8")
    (logs / "benchmark.json").write_text(
        json.dumps(
            {
                "run": {
                    "status": "success",
                    "backend": backend,
                    "task": "craniofacial_structures",
                    "requested_device": device,
                    "actual_device": device,
                    "fallback_reason": None,
                }
            }
        ),
        encoding="utf-8",
    )
    (logs / "mask_stats.json").write_text(
        json.dumps(
            {
                "mask_count": 1,
                "masks": [
                    {
                        "name": mask.name,
                        "status": "ok",
                        "nonzero_voxels": 8,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_ready_dentalseg_model(model_root: Path) -> None:
    dataset = (
        model_root
        / "nnUNet_results"
        / "Dataset112_DentalSegmentator_v100"
    )
    dataset.mkdir(parents=True)
    (dataset / "dataset.json").write_text("{}", encoding="utf-8")
    (
        dataset / ".dentalsegmentator_model_ready.json"
    ).write_text(
        json.dumps(
            {
                "schema": (
                    "totalsegmentator_wrapper_mac."
                    "dentalsegmentator_model_status.v1"
                ),
                "model_state": "ready",
                "expected_md5": "b71cd5230168d28a4f71b078265b76be",
                "dataset_id": "112",
                "dataset_name": "Dataset112_DentalSegmentator_v100",
            }
        ),
        encoding="utf-8",
    )


class CoordinatorProtocolTests(unittest.TestCase):
    def test_pretty_initial_request_is_complete_without_requiring_eof(self) -> None:
        payload = {
            "protocol_version": 1,
            "operation_id": "pretty-request",
            "operation": "capabilities",
        }

        parsed = _read_initial_json_document(
            io.StringIO(json.dumps(payload, indent=2) + "\n")
        )

        self.assertEqual(parsed, payload)

    def test_cancel_control_parser_accepts_only_cancel(self) -> None:
        control = parse_coordinator_control(
            {
                "protocol_version": 1,
                "operation_id": "cancel-test",
                "control": "cancel",
            }
        )
        self.assertEqual(control.operation_id, "cancel-test")
        with self.assertRaises(CoordinatorProtocolError):
            parse_coordinator_control(
                {
                    "protocol_version": 1,
                    "operation_id": "cancel-test",
                    "control": "pause",
                }
            )

    def test_control_reader_cancels_only_matching_version_and_operation(self) -> None:
        cancel_event = threading.Event()
        controls = io.StringIO(
            "\n".join(
                [
                    json.dumps(
                        {
                            "protocol_version": 2,
                            "operation_id": "target-operation",
                            "control": "cancel",
                        }
                    ),
                    json.dumps(
                        {
                            "protocol_version": 1,
                            "operation_id": "different-operation",
                            "control": "cancel",
                        }
                    ),
                    json.dumps(
                        {
                            "protocol_version": 1,
                            "operation_id": "target-operation",
                            "control": "cancel",
                        }
                    ),
                ]
            )
            + "\n"
        )

        control_stdout = io.StringIO()
        with redirect_stdout(control_stdout), redirect_stderr(io.StringIO()):
            _read_cancel_controls(
                controls,
                operation_id="target-operation",
                cancel_event=cancel_event,
            )

        self.assertTrue(cancel_event.is_set())
        self.assertEqual(control_stdout.getvalue(), "")

    def test_control_reader_ignores_other_operation(self) -> None:
        cancel_event = threading.Event()
        controls = io.StringIO(
            json.dumps(
                {
                    "protocol_version": 1,
                    "operation_id": "different-operation",
                    "control": "cancel",
                }
            )
            + "\n"
        )

        _read_cancel_controls(
            controls,
            operation_id="target-operation",
            cancel_event=cancel_event,
        )

        self.assertFalse(cancel_event.is_set())

    def test_capabilities_request_is_minimal(self) -> None:
        request = parse_coordinator_request(
            {
                "protocol_version": 1,
                "operation_id": "capabilities-1",
                "operation": "capabilities",
                "ignored_future_field": {"value": True},
            }
        )

        self.assertEqual(request.operation, "capabilities")
        self.assertIsNone(request.input_path)
        self.assertIsNone(request.device_policy)

    def test_run_request_accepts_only_narrow_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = parse_coordinator_request(_run_request_payload(root))

        self.assertEqual(request.operation, "run_nifti_totalsegmentator")
        self.assertEqual(request.device_policy, "cpu_required")
        self.assertTrue(request.robust_crop)
        self.assertFalse(request.higher_order_resampling)

    def test_dentalsegmentator_request_is_fixed_and_disables_totalseg_options(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = parse_coordinator_request(
                _dentalseg_run_request_payload(root)
            )
            self.assertEqual(
                request.operation,
                "run_nifti_dentalsegmentator",
            )
            self.assertFalse(request.robust_crop)
            self.assertFalse(request.higher_order_resampling)

            payload = _dentalseg_run_request_payload(root)
            payload["options"] = {"robust_crop": True}
            with self.assertRaises(CoordinatorProtocolError) as raised:
                parse_coordinator_request(payload)

        self.assertEqual(raised.exception.code, "options_unsupported")

    def test_protocol_v1_rejects_dicom_operation(self) -> None:
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "operation_id": "dicom-operation",
            "operation": "run_dicom_totalsegmentator",
        }

        with self.assertRaises(CoordinatorProtocolError) as raised:
            parse_coordinator_request(payload)

        self.assertEqual(raised.exception.code, "operation_unsupported")

    def test_protocol_v1_rejects_dicom_input_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _run_request_payload(Path(tmp))
            payload["input"] = {
                "kind": "dicom",
                "path": str(Path(tmp) / "dicom"),
            }

            with self.assertRaises(CoordinatorProtocolError) as raised:
                parse_coordinator_request(payload)

        self.assertEqual(raised.exception.code, "input_kind_unsupported")

    def test_rejects_unknown_protocol_version(self) -> None:
        payload = {
            "protocol_version": 2,
            "operation_id": "future",
            "operation": "capabilities",
        }
        with self.assertRaises(CoordinatorProtocolError) as raised:
            parse_coordinator_request(payload)

        self.assertEqual(raised.exception.code, "protocol_version_unsupported")

    def test_rejects_auto_and_private_escape_hatches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _run_request_payload(Path(tmp))
            payload["skip_device_check"] = True
            with self.assertRaises(CoordinatorProtocolError) as raised:
                parse_coordinator_request(payload)

        self.assertEqual(raised.exception.code, "request_field_forbidden")

    def test_rejects_auto_device_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _run_request_payload(Path(tmp))
            payload["device_policy"] = {"mode": "auto"}
            with self.assertRaises(CoordinatorProtocolError) as raised:
                parse_coordinator_request(payload)

        self.assertEqual(raised.exception.code, "device_policy_unsupported")

    def test_cuda_device_index_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _run_request_payload(Path(tmp))
            payload["device_policy"] = {"mode": "cuda_required", "index": 2}
            request = parse_coordinator_request(payload)
            self.assertEqual(request.device_index, 2)

            payload["device_policy"] = {"mode": "cuda_required", "index": -1}
            with self.assertRaises(CoordinatorProtocolError) as raised:
                parse_coordinator_request(payload)

        self.assertEqual(raised.exception.code, "device_index_invalid")

    def test_rejects_non_boolean_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _run_request_payload(Path(tmp))
            payload["options"] = {"robust_crop": 1}
            with self.assertRaises(CoordinatorProtocolError) as raised:
                parse_coordinator_request(payload)

        self.assertEqual(raised.exception.code, "options_invalid")

    def test_rejects_relative_paths(self) -> None:
        payload = _run_request_payload(Path("/tmp"))
        payload["input"] = {"kind": "nifti", "path": "relative/input.nii.gz"}

        with self.assertRaises(CoordinatorProtocolError) as raised:
            parse_coordinator_request(payload)

        self.assertEqual(raised.exception.code, "input_path_invalid")

    def test_writer_emits_monotonic_jsonl_and_one_terminal_event(self) -> None:
        stream = io.StringIO()
        writer = JsonlEventWriter(stream, operation_id="sequence-test")
        writer.emit("operation_started", operation="capabilities")
        writer.emit("operation_completed", status="success")

        events = _events(stream)
        self.assertEqual([event["sequence"] for event in events], [1, 2])
        self.assertTrue(
            all(event["protocol_version"] == PROTOCOL_VERSION for event in events)
        )
        with self.assertRaises(RuntimeError):
            writer.emit("operation_failed", status="failed")


class CoordinatorExecutionTests(unittest.TestCase):
    def test_capabilities_advertise_strict_per_operation_cuda(self) -> None:
        request = parse_coordinator_request(
            {
                "protocol_version": 1,
                "operation_id": "capabilities-test",
                "operation": "capabilities",
            }
        )
        stream = io.StringIO()
        rc = run_coordinator_request(
            request,
            JsonlEventWriter(stream, operation_id=request.operation_id),
        )

        events = _events(stream)
        self.assertEqual(rc, 0)
        self.assertEqual(events[0]["event"], "operation_started")
        self.assertEqual(events[-1]["event"], "operation_completed")
        capabilities = next(event for event in events if event["event"] == "capabilities")
        self.assertEqual(
            capabilities["operations"],
            [
                "capabilities",
                "run_nifti_dentalsegmentator",
                "run_nifti_totalsegmentator",
            ],
        )
        self.assertEqual(
            capabilities["device_policies"]["cuda_required"],
            {
                "implementation": "available",
                "verification": "strict_per_operation",
            },
        )
        self.assertEqual(
            capabilities["cancellation"]["authoritative_windows_job"],
            "unverified",
        )
        self.assertEqual(
            capabilities["cancellation"]["graceful_control"],
            "available",
        )

    def test_dentalsegmentator_requires_ready_app_private_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "private-input.nii.gz").write_text(
                "not a real nifti",
                encoding="utf-8",
            )
            request = parse_coordinator_request(
                _dentalseg_run_request_payload(root)
            )
            stream = io.StringIO()
            called = False

            def unexpected_runner(**_kwargs: object) -> object:
                nonlocal called
                called = True
                raise AssertionError("backend should not run")

            rc = run_coordinator_request(
                request,
                JsonlEventWriter(
                    stream,
                    operation_id=request.operation_id,
                ),
                segmentation_runner=unexpected_runner,
                dentalseg_model_root=root / "missing-model",
            )

        self.assertEqual(rc, 2)
        self.assertFalse(called)
        events = _events(stream)
        self.assertEqual(
            events[-1]["error_code"],
            "dentalseg_prepare_required",
        )
        self.assertFalse(
            any(event["event"] == "device_resolved" for event in events)
        )

    def test_dentalsegmentator_cuda_run_uses_fixed_backend_and_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "private-input.nii.gz").write_text(
                "not a real nifti",
                encoding="utf-8",
            )
            model_root = root / "model"
            _write_ready_dentalseg_model(model_root)
            payload = _dentalseg_run_request_payload(root)
            payload["device_policy"] = {
                "mode": "cuda_required",
                "index": 0,
            }
            request = parse_coordinator_request(payload)
            stream = io.StringIO()
            runner_kwargs: dict[str, object] = {}
            device_check = SimpleNamespace(
                status="pass",
                actual_device="cuda:0",
                fallback_reason=None,
                error_code=None,
            )

            def fake_runner(**kwargs: object) -> TotalSegRunResult:
                runner_kwargs.update(kwargs)
                case_directory = Path(kwargs["output_root"])
                _write_fake_success_case(
                    case_directory,
                    device="cuda:0",
                    backend="dentalsegmentator",
                )
                return TotalSegRunResult(
                    status="success",
                    returncode=0,
                    elapsed_seconds=2.0,
                    requested_device="cuda:0",
                    actual_device="cuda:0",
                    fallback_reason=None,
                    task="craniofacial_structures",
                    output_dir=str(case_directory),
                    stdout_tail="",
                    stderr_tail="",
                )

            def fake_preview(**kwargs: object) -> dict[str, object]:
                preview = Path(kwargs["case_dir"]) / "surface_preview"
                preview.mkdir(parents=True)
                (preview / "index.html").write_text(
                    "offline",
                    encoding="utf-8",
                )
                return {"output_dir": str(preview)}

            rc = run_coordinator_request(
                request,
                JsonlEventWriter(
                    stream,
                    operation_id=request.operation_id,
                ),
                segmentation_runner=fake_runner,
                preview_runner=fake_preview,
                cuda_device_checker=lambda _index: device_check,
                dentalseg_model_root=model_root,
            )

            output = root / "private-output"
            run_manifest = json.loads(
                (output / "run-manifest.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(rc, 0)
        self.assertEqual(
            runner_kwargs["backend"],
            "dentalsegmentator",
        )
        self.assertEqual(
            runner_kwargs["task"],
            "craniofacial_structures",
        )
        self.assertFalse(runner_kwargs["robust_crop"])
        self.assertFalse(
            runner_kwargs["higher_order_resampling"]
        )
        self.assertEqual(runner_kwargs["dentalseg_folds"], ("0",))
        self.assertTrue(runner_kwargs["dentalseg_disable_tta"])
        self.assertEqual(
            run_manifest["backend"],
            "dentalsegmentator",
        )
        self.assertEqual(run_manifest["resolved_device"], "cuda:0")
        events = _events(stream)
        self.assertEqual(events[-1]["event"], "operation_completed")
        self.assertEqual(
            events[-1]["backend"],
            "dentalsegmentator",
        )
        self.assertIn(
            "segmentations/dentalsegmentator",
            [
                event["relative_path"]
                for event in events
                if event["event"] == "artifact_created"
            ],
        )

    def test_cancel_before_backend_emits_typed_terminal_without_starting_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "private-input.nii.gz").write_text(
                "not a real nifti",
                encoding="utf-8",
            )
            request = parse_coordinator_request(_run_request_payload(root))
            stream = io.StringIO()
            cancel_event = threading.Event()
            cancel_event.set()
            called = False

            def unexpected_runner(**_kwargs: object) -> object:
                nonlocal called
                called = True
                raise AssertionError("backend should not run")

            rc = run_coordinator_request(
                request,
                JsonlEventWriter(stream, operation_id=request.operation_id),
                segmentation_runner=unexpected_runner,
                should_cancel=cancel_event.is_set,
            )

            self.assertEqual(rc, 3)
            self.assertFalse(called)
            self.assertFalse((root / "private-output").exists())
            self.assertFalse((root / ".tswm-test-operation.staging").exists())
            events = _events(stream)
            self.assertEqual(
                [event["event"] for event in events],
                ["operation_started", "operation_cancelled"],
            )
            self.assertEqual(events[-1]["status"], "cancelled")
            self.assertEqual(events[-1]["reason_code"], "cancel_requested")

    def test_cancelled_runner_keeps_staging_and_never_starts_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "private-input.nii.gz").write_text(
                "not a real nifti",
                encoding="utf-8",
            )
            request = parse_coordinator_request(_run_request_payload(root))
            stream = io.StringIO()
            cancel_event = threading.Event()
            preview_called = False

            def cancelled_runner(**kwargs: object) -> TotalSegRunResult:
                should_cancel = kwargs["should_cancel"]
                self.assertTrue(callable(should_cancel))
                staging = Path(kwargs["output_root"])
                staging.mkdir(parents=True)
                cancel_event.set()
                raise OperationCancelled

            def unexpected_preview(**_kwargs: object) -> dict[str, object]:
                nonlocal preview_called
                preview_called = True
                return {}

            rc = run_coordinator_request(
                request,
                JsonlEventWriter(stream, operation_id=request.operation_id),
                segmentation_runner=cancelled_runner,
                preview_runner=unexpected_preview,
                should_cancel=cancel_event.is_set,
            )

            self.assertEqual(rc, 3)
            self.assertFalse(preview_called)
            self.assertFalse((root / "private-output").exists())
            self.assertTrue((root / ".tswm-test-operation.staging").exists())
            events = _events(stream)
            self.assertEqual(events[-1]["event"], "operation_cancelled")
            self.assertEqual(
                sum(
                    event["event"]
                    in {
                        "operation_completed",
                        "operation_failed",
                        "operation_cancelled",
                    }
                    for event in events
                ),
                1,
            )

    def test_control_listener_starts_after_cuda_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "private-input.nii.gz").write_text(
                "not a real nifti",
                encoding="utf-8",
            )
            request = parse_coordinator_request(
                {
                    **_run_request_payload(root),
                    "device_policy": {
                        "mode": "cuda_required",
                        "index": 0,
                    },
                }
            )
            stream = io.StringIO()
            order: list[str] = []

            def cuda_checker(_index: int) -> SimpleNamespace:
                order.append("cuda_preflight")
                return SimpleNamespace(status="pass", actual_device="cuda:0")

            def cancelled_runner(**_kwargs: object) -> object:
                order.append("runner")
                raise OperationCancelled

            rc = run_coordinator_request(
                request,
                JsonlEventWriter(stream, operation_id=request.operation_id),
                segmentation_runner=cancelled_runner,
                preview_runner=lambda **_kwargs: {},
                cuda_device_checker=cuda_checker,
                should_cancel=lambda: False,
                start_cancel_listener=lambda: order.append("control_listener"),
            )

            self.assertEqual(rc, 3)
            self.assertEqual(
                order,
                ["cuda_preflight", "control_listener", "runner"],
            )

    def test_cuda_required_fails_without_running_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "private-input.nii.gz").write_text(
                "not a real nifti",
                encoding="utf-8",
            )
            payload = _run_request_payload(root)
            payload["device_policy"] = {"mode": "cuda_required"}
            request = parse_coordinator_request(payload)
            stream = io.StringIO()
            called = False

            def unexpected_runner(**_kwargs: object) -> object:
                nonlocal called
                called = True
                raise AssertionError("backend should not run")

            rc = run_coordinator_request(
                request,
                JsonlEventWriter(stream, operation_id=request.operation_id),
                segmentation_runner=unexpected_runner,
                cuda_device_checker=lambda _index: SimpleNamespace(
                    status="fail",
                    actual_device=None,
                    error_code="cuda_unavailable",
                ),
            )

        self.assertEqual(rc, 2)
        self.assertFalse(called)
        events = _events(stream)
        self.assertEqual(events[-1]["event"], "operation_failed")
        self.assertEqual(events[-1]["error_code"], "cuda_unavailable")
        resolved = next(event for event in events if event["event"] == "device_resolved")
        self.assertIsNone(resolved["resolved_device"])
        self.assertFalse(resolved["fallback_allowed"])
        self.assertFalse(resolved["fallback_occurred"])

    def test_cuda_required_passes_prevalidated_device_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "private-input.nii.gz"
            input_path.write_text("not a real nifti", encoding="utf-8")
            payload = _run_request_payload(root)
            payload["device_policy"] = {"mode": "cuda_required", "index": 2}
            request = parse_coordinator_request(payload)
            stream = io.StringIO()
            device_check = SimpleNamespace(
                status="pass",
                actual_device="cuda:2",
                fallback_reason=None,
                error_code=None,
            )
            runner_kwargs: dict[str, object] = {}

            def fake_runner(**kwargs: object) -> TotalSegRunResult:
                runner_kwargs.update(kwargs)
                case_directory = Path(kwargs["output_root"])
                _write_fake_success_case(case_directory, device="cuda:2")
                return TotalSegRunResult(
                    status="success",
                    returncode=0,
                    elapsed_seconds=1.0,
                    requested_device="cuda:2",
                    actual_device="cuda:2",
                    fallback_reason=None,
                    task="craniofacial_structures",
                    output_dir=str(case_directory),
                    stdout_tail="",
                    stderr_tail="",
                )

            def fake_preview(**kwargs: object) -> dict[str, object]:
                preview = Path(kwargs["case_dir"]) / "surface_preview"
                preview.mkdir(parents=True)
                (preview / "index.html").write_text("offline", encoding="utf-8")
                return {"output_dir": str(preview)}

            rc = run_coordinator_request(
                request,
                JsonlEventWriter(stream, operation_id=request.operation_id),
                segmentation_runner=fake_runner,
                preview_runner=fake_preview,
                cuda_device_checker=lambda index: device_check,
            )

        self.assertEqual(rc, 0)
        self.assertEqual(runner_kwargs["requested_device"], "cuda:2")
        self.assertIs(runner_kwargs["prevalidated_device_check"], device_check)
        events = _events(stream)
        resolved = next(event for event in events if event["event"] == "device_resolved")
        self.assertEqual(resolved["requested_device_index"], 2)
        self.assertEqual(resolved["resolved_device"], "cuda:2")
        self.assertFalse(resolved["fallback_allowed"])
        self.assertFalse(resolved["fallback_occurred"])
        self.assertEqual(events[-1]["event"], "operation_completed")

    def test_fake_cpu_run_emits_safe_events_and_synchronous_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "private-input.nii.gz"
            input_path.write_text("not a real nifti", encoding="utf-8")
            output = root / "private-output"
            request = parse_coordinator_request(_run_request_payload(root))
            stream = io.StringIO()
            runner_kwargs: dict[str, object] = {}

            def fake_runner(**kwargs: object) -> TotalSegRunResult:
                runner_kwargs.update(kwargs)
                case_directory = Path(kwargs["output_root"])
                event_sink = kwargs["event_sink"]
                assert callable(event_sink)
                event_sink(
                    "phase_started",
                    {
                        "route": "totalsegmentator",
                        "stage_id": "prepare",
                        "index": 1,
                        "total": 4,
                        "label": "実行準備",
                    },
                )
                event_sink(
                    "progress",
                    {
                        "route": "totalsegmentator",
                        "stage_id": "segment",
                        "scope": "subtask",
                        "stage": "Predicting",
                        "step": 1,
                        "total": 2,
                        "percent": 50,
                        "line": f"private path: {input_path}",
                        "stream": "STDERR",
                    },
                )
                _write_fake_success_case(case_directory)
                return TotalSegRunResult(
                    status="success",
                    returncode=0,
                    elapsed_seconds=1.25,
                    requested_device="cpu",
                    actual_device="cpu",
                    fallback_reason=None,
                    task="craniofacial_structures",
                    output_dir=str(case_directory),
                    stdout_tail=f"private output {case_directory}",
                    stderr_tail=f"private input {input_path}",
                )

            def fake_preview(**kwargs: object) -> dict[str, object]:
                self.assertTrue(kwargs["detailed_stl"])
                preview = Path(kwargs["case_dir"]) / "surface_preview"
                preview.mkdir(parents=True)
                (preview / "index.html").write_text("offline", encoding="utf-8")
                return {"output_dir": str(preview)}

            rc = run_coordinator_request(
                request,
                JsonlEventWriter(stream, operation_id=request.operation_id),
                segmentation_runner=fake_runner,
                preview_runner=fake_preview,
            )

            text = stream.getvalue()
            events = _events(stream)
            self.assertEqual(rc, 0)
            self.assertEqual(events[-1]["event"], "operation_completed")
            self.assertEqual(
                sum(
                    event["event"]
                    in {
                        "operation_completed",
                        "operation_failed",
                        "operation_cancelled",
                    }
                    for event in events
                ),
                1,
            )
            self.assertNotIn(str(input_path), text)
            self.assertNotIn(str(output), text)
            self.assertNotIn("stdout_tail", text)
            self.assertNotIn("stderr_tail", text)
            self.assertNotIn("private path", text)
            self.assertEqual(runner_kwargs["requested_device"], "cpu")
            self.assertFalse(runner_kwargs["copy_input"])
            self.assertTrue(runner_kwargs["robust_crop"])
            self.assertEqual(
                Path(runner_kwargs["output_root"]).name,
                ".tswm-test-operation.staging",
            )
            self.assertTrue(output.is_dir())
            self.assertFalse(
                (root / ".tswm-test-operation.staging").exists()
            )
            self.assertIn("phase_started", [event["event"] for event in events])
            self.assertIn("progress", [event["event"] for event in events])
            preview_phase = [
                event
                for event in events
                if event["event"] == "phase_started"
                and event["stage_id"] == "preview"
            ]
            self.assertEqual(len(preview_phase), 1)
            self.assertEqual(
                [
                    event["relative_path"]
                    for event in events
                    if event["event"] == "artifact_created"
                ],
                [
                    "README_OUTPUT.md",
                    "segmentations/raw_totalseg",
                    "surface_preview/index.html",
                    "run-manifest.json",
                    "artifact-manifest.json",
                ],
            )
            run_manifest = json.loads(
                (output / "run-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                run_manifest["schema"],
                "totalsegmentator_wrapper.run_manifest.v1",
            )
            self.assertEqual(run_manifest["requested_policy"], "cpu_required")
            self.assertIsNone(run_manifest["requested_device_index"])
            self.assertEqual(run_manifest["resolved_device"], "cpu")
            self.assertFalse(run_manifest["fallback_allowed"])
            self.assertFalse(run_manifest["fallback_occurred"])
            manifest = json.loads(
                (output / "artifact-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["schema"],
                "totalsegmentator_wrapper.coordinator_artifacts.v1",
            )
            self.assertTrue(
                all(
                    not Path(item["relative_path"]).is_absolute()
                    and len(item["sha256"]) == 64
                    for item in manifest["artifacts"]
                )
            )

    def test_unexpected_fallback_is_a_typed_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "private-input.nii.gz"
            input_path.write_text("not a real nifti", encoding="utf-8")
            request = parse_coordinator_request(_run_request_payload(root))
            stream = io.StringIO()

            result = TotalSegRunResult(
                status="success",
                returncode=0,
                elapsed_seconds=0.1,
                requested_device="cpu",
                actual_device="cpu",
                fallback_reason="unexpected fallback",
                task="craniofacial_structures",
                output_dir=str(root / "private-output"),
                stdout_tail="",
                stderr_tail="",
            )

            rc = run_coordinator_request(
                request,
                JsonlEventWriter(stream, operation_id=request.operation_id),
                segmentation_runner=lambda **_kwargs: result,
                preview_runner=lambda **_kwargs: {},
            )

        self.assertEqual(rc, 2)
        events = _events(stream)
        self.assertEqual(events[-1]["error_code"], "unexpected_device_fallback")
        self.assertTrue(
            next(event for event in events if event["event"] == "device_resolved")[
                "fallback_occurred"
            ]
        )

    def test_failed_preview_keeps_staging_and_never_promotes_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "private-input.nii.gz"
            input_path.write_text("not a real nifti", encoding="utf-8")
            request = parse_coordinator_request(_run_request_payload(root))
            stream = io.StringIO()

            def fake_runner(**kwargs: object) -> TotalSegRunResult:
                staging = Path(kwargs["output_root"])
                _write_fake_success_case(staging)
                return TotalSegRunResult(
                    status="success",
                    returncode=0,
                    elapsed_seconds=0.1,
                    requested_device="cpu",
                    actual_device="cpu",
                    fallback_reason=None,
                    task="craniofacial_structures",
                    output_dir=str(staging),
                    stdout_tail="",
                    stderr_tail="",
                )

            rc = run_coordinator_request(
                request,
                JsonlEventWriter(stream, operation_id=request.operation_id),
                segmentation_runner=fake_runner,
                preview_runner=lambda **_kwargs: {},
            )

            self.assertEqual(rc, 2)
            self.assertFalse((root / "private-output").exists())
            self.assertTrue((root / ".tswm-test-operation.staging").exists())
            self.assertEqual(_events(stream)[-1]["error_code"], "preview_missing")

    def test_missing_required_artifact_keeps_staging_and_never_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "private-input.nii.gz"
            input_path.write_text("not a real nifti", encoding="utf-8")
            request = parse_coordinator_request(_run_request_payload(root))
            stream = io.StringIO()

            def fake_runner(**kwargs: object) -> TotalSegRunResult:
                staging = Path(kwargs["output_root"])
                _write_fake_success_case(staging)
                (staging / "README_OUTPUT.md").unlink()
                return TotalSegRunResult(
                    status="success",
                    returncode=0,
                    elapsed_seconds=0.1,
                    requested_device="cpu",
                    actual_device="cpu",
                    fallback_reason=None,
                    task="craniofacial_structures",
                    output_dir=str(staging),
                    stdout_tail="",
                    stderr_tail="",
                )

            def fake_preview(**kwargs: object) -> dict[str, object]:
                preview = Path(kwargs["case_dir"]) / "surface_preview"
                preview.mkdir(parents=True)
                (preview / "index.html").write_text("offline", encoding="utf-8")
                return {"output_dir": str(preview)}

            rc = run_coordinator_request(
                request,
                JsonlEventWriter(stream, operation_id=request.operation_id),
                segmentation_runner=fake_runner,
                preview_runner=fake_preview,
            )

            self.assertEqual(rc, 2)
            self.assertFalse((root / "private-output").exists())
            self.assertTrue((root / ".tswm-test-operation.staging").exists())
            self.assertEqual(
                _events(stream)[-1]["error_code"],
                "artifact_verification_failed",
            )

    def test_module_entrypoint_prints_jsonl_only(self) -> None:
        request = json.dumps(
            {
                "protocol_version": 1,
                "operation_id": "subprocess-capabilities",
                "operation": "capabilities",
            }
        )
        proc = subprocess.run(
            [sys.executable, "-m", "totalsegmentator_wrapper_mac.coordinator"],
            input=request,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        events = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(events[0]["event"], "operation_started")
        self.assertEqual(events[-1]["event"], "operation_completed")
        self.assertEqual(proc.stderr, "")

    def test_module_entrypoint_starts_after_complete_request_without_stdin_eof(self) -> None:
        request = json.dumps(
            {
                "protocol_version": 1,
                "operation_id": "open-stdin-capabilities",
                "operation": "capabilities",
            },
            indent=2,
        )
        proc = subprocess.Popen(
            [sys.executable, "-m", "totalsegmentator_wrapper_mac.coordinator"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None
        try:
            proc.stdin.write(request)
            proc.stdin.flush()
            try:
                returncode = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                self.fail("coordinator waited for stdin EOF after a complete request")
            stdout = proc.stdout.read()
            stderr = proc.stderr.read()
        finally:
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()

        self.assertEqual(returncode, 0, stderr)
        events = [json.loads(line) for line in stdout.splitlines()]
        self.assertEqual(events[0]["event"], "operation_started")
        self.assertEqual(events[-1]["event"], "operation_completed")
        self.assertEqual(stderr, "")

    def test_initial_request_reader_does_not_overread_cancel_line(self) -> None:
        request = {
            "protocol_version": 1,
            "operation_id": "pipe-request",
            "operation": "capabilities",
        }
        control = {
            "protocol_version": 1,
            "operation_id": "pipe-request",
            "control": "cancel",
        }
        read_fd, write_fd = os.pipe()
        stream = os.fdopen(read_fd, "r", encoding="utf-8")
        try:
            os.write(
                write_fd,
                (
                    json.dumps(request, ensure_ascii=False, indent=2)
                    + "\n"
                    + json.dumps(control)
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(
                _read_initial_json_document(stream),
                request,
            )
            self.assertEqual(stream.readline(), "\n")
            self.assertEqual(
                json.loads(stream.readline()),
                control,
            )
        finally:
            stream.close()
            os.close(write_fd)

    def test_invalid_operation_id_is_not_reflected_to_stdout(self) -> None:
        request = json.dumps(
            {
                "protocol_version": 1,
                "operation_id": "patient/name",
                "operation": "capabilities",
            }
        )
        proc = subprocess.run(
            [sys.executable, "-m", "totalsegmentator_wrapper_mac.coordinator"],
            input=request,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(proc.returncode, 2)
        events = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["operation_id"], "unknown")
        self.assertEqual(events[0]["error_code"], "operation_id_invalid")
        self.assertNotIn("patient/name", proc.stdout)

    def test_real_runner_fake_binary_and_real_preview_complete_vertical_slice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "private-input.nii.gz"
            source = np.zeros((12, 12, 12), dtype=np.float32)
            nib.save(nib.Nifti1Image(source, np.eye(4)), input_path)
            fake_bin = root / "fake_totalseg.py"
            fake_bin.write_text(
                f"#!{sys.executable}\n"
                "import argparse\n"
                "from pathlib import Path\n"
                "import nibabel as nib\n"
                "import numpy as np\n"
                "p=argparse.ArgumentParser(); p.add_argument('-i'); p.add_argument('-o'); "
                "p.add_argument('-ta'); p.add_argument('--device'); "
                "p.add_argument('--robust_crop', action='store_true'); "
                "p.add_argument('--higher_order_resampling', action='store_true'); a=p.parse_args()\n"
                "image=nib.load(a.i); data=np.zeros(image.shape, dtype=np.uint8); "
                "data[3:9,3:9,3:9]=1\n"
                "out=Path(a.o); out.mkdir(parents=True, exist_ok=True); "
                "nib.save(nib.Nifti1Image(data, image.affine), out/'mandible.nii.gz')\n"
                "print('Predicting...')\n"
                "print('100%|##########| 1/1 [00:01<00:00]', file=__import__('sys').stderr)\n",
                encoding="utf-8",
            )
            fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IXUSR)
            request = parse_coordinator_request(_run_request_payload(root))
            stream = io.StringIO()

            def real_runner(**kwargs: object) -> TotalSegRunResult:
                return run_totalsegmentator(
                    **kwargs,
                    totalseg_bin=str(fake_bin),
                )

            mirrored = io.StringIO()
            with redirect_stderr(mirrored):
                rc = run_coordinator_request(
                    request,
                    JsonlEventWriter(stream, operation_id=request.operation_id),
                    segmentation_runner=real_runner,
                )

            events = _events(stream)
            self.assertEqual(rc, 0, mirrored.getvalue())
            self.assertEqual(events[-1]["event"], "operation_completed")
            self.assertTrue(
                (root / "private-output" / "surface_preview" / "index.html").is_file()
            )
            self.assertTrue(
                (root / "private-output" / "artifact-manifest.json").is_file()
            )
            mask_stats_text = (
                root / "private-output" / "logs" / "mask_stats.json"
            ).read_text(encoding="utf-8")
            self.assertNotIn(".tswm-test-operation.staging", mask_stats_text)
            normalized_stats = json.loads(mask_stats_text)
            self.assertEqual(
                normalized_stats["mask_dir"],
                "segmentations/raw_totalseg",
            )
            self.assertTrue(
                all(
                    not Path(item["path"]).is_absolute()
                    for item in normalized_stats["masks"]
                )
            )
            self.assertIn("RUN_STAGE ", mirrored.getvalue())
            self.assertIn("RUN_PROGRESS ", mirrored.getvalue())
            self.assertNotIn(str(input_path), stream.getvalue())


if __name__ == "__main__":
    unittest.main()
