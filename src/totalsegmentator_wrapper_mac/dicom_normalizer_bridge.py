from __future__ import annotations

import os
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DICOM_NORMALIZER_ENV = "TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER"
DICOM_NORMALIZER_BINARY_NAME = "totalsegmentator-wrapper-dicom-normalizer"


@dataclass(frozen=True)
class DicomNormalizerResult:
    status: str
    returncode: int
    command: list[str]
    output_json: str
    stdout: str
    stderr: str
    binary: str | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "returncode": self.returncode,
            "command": self.command,
            "output_json": self.output_json,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "binary": self.binary,
            "error": self.error,
        }


def find_dicom_normalizer_binary(
    *,
    explicit: str | Path | None = None,
    project_root: Path | None = None,
) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())

    env_path = os.environ.get(DICOM_NORMALIZER_ENV)
    if env_path:
        candidates.append(Path(env_path).expanduser())

    root = (project_root or _infer_project_root()).resolve()
    candidates.extend(
        [
            Path(__file__).resolve().parent / "bin" / DICOM_NORMALIZER_BINARY_NAME,
            root / "build" / "dicom_normalizer" / DICOM_NORMALIZER_BINARY_NAME,
            root / "native" / "dicom_normalizer" / "build" / DICOM_NORMALIZER_BINARY_NAME,
        ]
    )

    path_candidate = shutil.which(DICOM_NORMALIZER_BINARY_NAME)
    if path_candidate:
        candidates.append(Path(path_candidate))

    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def build_dicom_normalizer_audit_command(
    *,
    dicom_dir: Path,
    output_json: Path,
    binary: str | Path | None = None,
    project_root: Path | None = None,
) -> list[str]:
    resolved_binary = find_dicom_normalizer_binary(
        explicit=binary,
        project_root=project_root,
    )
    if resolved_binary is None:
        raise FileNotFoundError(
            "totalsegmentator-wrapper-dicom-normalizer binary not found. Build it with "
            "`scripts/build_dicom_normalizer_mac.sh` or set TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER."
        )
    return [
        str(resolved_binary),
        "audit",
        "--dicom-dir",
        str(dicom_dir),
        "--output",
        str(output_json),
    ]


def build_dicom_normalizer_doctor_command(
    *,
    output_json: Path | None = None,
    binary: str | Path | None = None,
    project_root: Path | None = None,
) -> list[str]:
    resolved_binary = find_dicom_normalizer_binary(
        explicit=binary,
        project_root=project_root,
    )
    if resolved_binary is None:
        raise FileNotFoundError(
            "totalsegmentator-wrapper-dicom-normalizer binary not found. Build it with "
            "`scripts/build_dicom_normalizer_mac.sh` or set TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER."
        )
    command = [str(resolved_binary), "doctor"]
    if output_json is not None:
        command.extend(["--output", str(output_json)])
    return command


def build_dicom_normalizer_prepare_rescue_command(
    *,
    dicom_dir: Path,
    output_dir: Path,
    series_number: int | None = None,
    series_key: str | None = None,
    patched_spacing: str,
    binary: str | Path | None = None,
    project_root: Path | None = None,
    dcm2niix: str | Path | None = None,
) -> list[str]:
    resolved_binary = find_dicom_normalizer_binary(
        explicit=binary,
        project_root=project_root,
    )
    if resolved_binary is None:
        raise FileNotFoundError(
            "totalsegmentator-wrapper-dicom-normalizer binary not found. Build it with "
            "`scripts/build_dicom_normalizer_mac.sh` or set TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER."
        )
    if series_number is None and not series_key:
        raise ValueError("series_number or series_key is required")
    command = [
        str(resolved_binary),
        "prepare-rescue",
        "--dicom-dir",
        str(dicom_dir),
        "--patched-spacing",
        patched_spacing,
        "--output",
        str(output_dir),
    ]
    if series_number is not None:
        command.extend(["--series-number", str(series_number)])
    else:
        command.extend(["--series-key", str(series_key)])
    if dcm2niix is not None:
        command.extend(["--dcm2niix", str(dcm2niix)])
    return command


def build_dicom_normalizer_export_rescue_stack_command(
    *,
    dicom_dir: Path,
    output_dir: Path,
    series_number: int | None = None,
    series_key: str | None = None,
    binary: str | Path | None = None,
    project_root: Path | None = None,
) -> list[str]:
    resolved_binary = find_dicom_normalizer_binary(
        explicit=binary,
        project_root=project_root,
    )
    if resolved_binary is None:
        raise FileNotFoundError(
            "totalsegmentator-wrapper-dicom-normalizer binary not found. Build it with "
            "`scripts/build_dicom_normalizer_mac.sh` or set TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER."
        )
    if series_number is None and not series_key:
        raise ValueError("series_number or series_key is required")
    command = [
        str(resolved_binary),
        "export-rescue-stack",
        "--dicom-dir",
        str(dicom_dir),
        "--output",
        str(output_dir),
    ]
    if series_number is not None:
        command.extend(["--series-number", str(series_number)])
    else:
        command.extend(["--series-key", str(series_key)])
    return command


def build_dicom_normalizer_convert_clean_command(
    *,
    dicom_dir: Path,
    output_dir: Path,
    series_number: int | None = None,
    series_key: str | None = None,
    binary: str | Path | None = None,
    project_root: Path | None = None,
    dcm2niix: str | Path | None = None,
) -> list[str]:
    resolved_binary = find_dicom_normalizer_binary(
        explicit=binary,
        project_root=project_root,
    )
    if resolved_binary is None:
        raise FileNotFoundError(
            "totalsegmentator-wrapper-dicom-normalizer binary not found. Build it with "
            "`scripts/build_dicom_normalizer_mac.sh` or set TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER."
        )
    if series_number is None and not series_key:
        raise ValueError("series_number or series_key is required")
    command = [
        str(resolved_binary),
        "convert-clean",
        "--dicom-dir",
        str(dicom_dir),
        "--output",
        str(output_dir),
    ]
    if series_number is not None:
        command.extend(["--series-number", str(series_number)])
    else:
        command.extend(["--series-key", str(series_key)])
    if dcm2niix is not None:
        command.extend(["--dcm2niix", str(dcm2niix)])
    return command


def build_dicom_normalizer_prepare_viewer_export_command(
    *,
    dicom_dir: Path,
    output_dir: Path,
    group_id: str,
    series_number: int | None = None,
    series_key: str | None = None,
    binary: str | Path | None = None,
    project_root: Path | None = None,
    dcm2niix: str | Path | None = None,
) -> list[str]:
    resolved_binary = find_dicom_normalizer_binary(
        explicit=binary,
        project_root=project_root,
    )
    if resolved_binary is None:
        raise FileNotFoundError(
            "totalsegmentator-wrapper-dicom-normalizer binary not found. Build it with "
            "`scripts/build_dicom_normalizer_mac.sh` or set TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER."
        )
    if series_number is None and not series_key:
        raise ValueError("series_number or series_key is required")
    if not group_id:
        raise ValueError("group_id is required")
    command = [
        str(resolved_binary),
        "prepare-viewer-export",
        "--dicom-dir",
        str(dicom_dir),
        "--group-id",
        group_id,
        "--output",
        str(output_dir),
    ]
    if series_number is not None:
        command.extend(["--series-number", str(series_number)])
    else:
        command.extend(["--series-key", str(series_key)])
    if dcm2niix is not None:
        command.extend(["--dcm2niix", str(dcm2niix)])
    return command


def run_dicom_normalizer_audit(
    *,
    dicom_dir: Path,
    output_json: Path,
    binary: str | Path | None = None,
    project_root: Path | None = None,
    timeout_sec: int = 300,
) -> DicomNormalizerResult:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.unlink(missing_ok=True)
    try:
        command = build_dicom_normalizer_audit_command(
            dicom_dir=dicom_dir,
            output_json=output_json,
            binary=binary,
            project_root=project_root,
        )
    except Exception as exc:  # noqa: BLE001
        _write_audit_failure_json(
            output_json,
            dicom_dir=dicom_dir,
            status="failed",
            reason="normalizer_unavailable",
            error=str(exc),
            timeout_sec=timeout_sec,
            command=[],
            stdout="",
            stderr="",
        )
        return DicomNormalizerResult(
            status="failed",
            returncode=127,
            command=[],
            output_json=str(output_json),
            stdout="",
            stderr="",
            binary=None,
            error=str(exc),
        )

    try:
        proc = subprocess.run(  # noqa: S603
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_timeout_output(exc.stdout)
        stderr = _decode_timeout_output(exc.stderr)
        error = f"DICOM audit timed out after {timeout_sec} seconds."
        _write_audit_failure_json(
            output_json,
            dicom_dir=dicom_dir,
            status="failed",
            reason="timeout",
            error=error,
            timeout_sec=timeout_sec,
            command=command,
            stdout=stdout,
            stderr=stderr,
        )
        return DicomNormalizerResult(
            status="failed",
            returncode=124,
            command=command,
            output_json=str(output_json),
            stdout=stdout,
            stderr=stderr,
            binary=command[0],
            error=error,
        )
    if proc.returncode != 0 and not output_json.exists():
        _write_audit_failure_json(
            output_json,
            dicom_dir=dicom_dir,
            status="failed",
            reason="normalizer_failed",
            error="DICOM audit failed before writing a result JSON.",
            timeout_sec=timeout_sec,
            command=command,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    return DicomNormalizerResult(
        status="success" if proc.returncode == 0 else "failed",
        returncode=proc.returncode,
        command=command,
        output_json=str(output_json),
        stdout=proc.stdout,
        stderr=proc.stderr,
        binary=command[0],
    )


def _decode_timeout_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _write_audit_failure_json(
    output_json: Path,
    *,
    dicom_dir: Path,
    status: str,
    reason: str,
    error: str,
    timeout_sec: int,
    command: list[str],
    stdout: str,
    stderr: str,
) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(dicom_dir.expanduser())
    payload: dict[str, Any] = {
        "schema": "totalsegmentator_wrapper_mac.dicom_normalizer.audit_failure.v1",
        "status": status,
        "reason": reason,
        "error": error,
        "timeout_sec": timeout_sec,
        "dicom_dir": {
            "basename": dicom_dir.name,
            "path_hash": hashlib.sha256(resolved.encode("utf-8")).hexdigest(),
        },
        "series_count": 0,
        "series": [],
        "possible_causes": _audit_failure_possible_causes(reason),
        "next_actions": _audit_failure_next_actions(reason),
        "command_basename": _redact_command_for_audit_failure(command),
        "stdout_tail": _redact_audit_failure_text(stdout, dicom_dir=dicom_dir, output_json=output_json)[-4000:],
        "stderr_tail": _redact_audit_failure_text(stderr, dicom_dir=dicom_dir, output_json=output_json)[-4000:],
    }
    output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _redact_command_for_audit_failure(command: list[str]) -> list[str]:
    redacted: list[str] = []
    placeholder_for_next: str | None = None
    for index, part in enumerate(command):
        if index == 0:
            redacted.append(Path(part).name)
            continue
        if placeholder_for_next is not None:
            redacted.append(placeholder_for_next)
            placeholder_for_next = None
            continue
        redacted.append(part)
        if part == "--dicom-dir":
            placeholder_for_next = "<dicom_dir>"
        elif part == "--output":
            placeholder_for_next = "<output_json>"
    return redacted


def _redact_audit_failure_text(text: str, *, dicom_dir: Path, output_json: Path) -> str:
    redacted = text
    replacements = {
        str(dicom_dir.expanduser()): "<dicom_dir>",
        str(dicom_dir.expanduser().resolve()) if dicom_dir.exists() else str(dicom_dir.expanduser()): "<dicom_dir>",
        str(output_json.expanduser()): "<output_json>",
        str(output_json.expanduser().resolve()) if output_json.exists() else str(output_json.expanduser()): "<output_json>",
    }
    for raw, placeholder in replacements.items():
        if raw:
            redacted = redacted.replace(raw, placeholder)
    return redacted


def _audit_failure_possible_causes(reason: str) -> list[str]:
    if reason == "normalizer_unavailable":
        return [
            "normalizer_binary_missing_or_not_executable",
            "app_bundle_may_be_incomplete",
        ]
    if reason == "normalizer_failed":
        return [
            "normalizer_stopped_before_writing_json",
            "unsupported_or_malformed_dicom_metadata",
            "folder_contains_non_dicom_files",
        ]
    return [
        "folder_contains_many_or_non_dicom_files",
        "cloud_storage_files_may_not_be_fully_local",
        "dicomdir_or_nested_export_requires_manual_review",
        "normalizer_audit_took_too_long",
    ]


def _audit_failure_next_actions(reason: str) -> list[str]:
    if reason == "normalizer_unavailable":
        return [
            "Copy the app from the DMG again.",
            "Run setup again so the bundled DICOM normalizer can be checked.",
            "Open detailed log and share it only after checking local paths.",
        ]
    if reason == "normalizer_failed":
        return [
            "Choose the innermost folder that directly contains DICOM slices.",
            "If the folder contains reports, screenshots, or unrelated files, try the CT series folder only.",
            "Open detailed log and share it only after checking local paths.",
        ]
    return [
        "Copy the DICOM folder to a local disk and ensure files are fully downloaded.",
        "Choose the innermost folder that directly contains DICOM slices.",
        "If the folder contains reports, screenshots, or unrelated files, try the CT series folder only.",
        "Open detailed log and share it only after checking local paths.",
    ]


def run_dicom_normalizer_doctor(
    *,
    output_json: Path | None = None,
    binary: str | Path | None = None,
    project_root: Path | None = None,
    timeout_sec: int = 30,
) -> DicomNormalizerResult:
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
    try:
        command = build_dicom_normalizer_doctor_command(
            output_json=output_json,
            binary=binary,
            project_root=project_root,
        )
    except Exception as exc:  # noqa: BLE001
        return DicomNormalizerResult(
            status="failed",
            returncode=127,
            command=[],
            output_json=str(output_json or ""),
            stdout="",
            stderr="",
            binary=None,
            error=str(exc),
        )

    proc = subprocess.run(  # noqa: S603
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_sec,
        check=False,
    )
    return DicomNormalizerResult(
        status="success" if proc.returncode == 0 else "failed",
        returncode=proc.returncode,
        command=command,
        output_json=str(output_json or ""),
        stdout=proc.stdout,
        stderr=proc.stderr,
        binary=command[0],
    )


def inspect_dicom_normalizer(
    *,
    binary: str | Path | None = None,
    project_root: Path | None = None,
    timeout_sec: int = 30,
) -> dict[str, Any]:
    result = run_dicom_normalizer_doctor(
        binary=binary,
        project_root=project_root,
        timeout_sec=timeout_sec,
    )
    payload: dict[str, Any] | None = None
    if result.status == "success" and result.stdout.strip():
        try:
            import json

            payload = json.loads(result.stdout)
        except Exception as exc:  # noqa: BLE001
            payload = {"parse_error": repr(exc)}
    return {
        "status": result.status,
        "returncode": result.returncode,
        "binary": result.binary,
        "error": result.error,
        "stderr": result.stderr,
        "doctor": payload,
    }


def run_dicom_normalizer_convert_clean(
    *,
    dicom_dir: Path,
    output_dir: Path,
    series_number: int | None = None,
    series_key: str | None = None,
    binary: str | Path | None = None,
    project_root: Path | None = None,
    dcm2niix: str | Path | None = None,
    timeout_sec: int = 900,
) -> DicomNormalizerResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        command = build_dicom_normalizer_convert_clean_command(
            dicom_dir=dicom_dir,
            output_dir=output_dir,
            series_number=series_number,
            series_key=series_key,
            binary=binary,
            project_root=project_root,
            dcm2niix=dcm2niix,
        )
    except Exception as exc:  # noqa: BLE001
        return DicomNormalizerResult(
            status="failed",
            returncode=127,
            command=[],
            output_json=str(output_dir / "convert_clean_metadata.json"),
            stdout="",
            stderr="",
            binary=None,
            error=str(exc),
        )

    proc = subprocess.run(  # noqa: S603
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_sec,
        check=False,
    )
    return DicomNormalizerResult(
        status="success" if proc.returncode == 0 else "failed",
        returncode=proc.returncode,
        command=command,
        output_json=str(output_dir / "convert_clean_metadata.json"),
        stdout=proc.stdout,
        stderr=proc.stderr,
        binary=command[0],
    )


def run_dicom_normalizer_prepare_rescue(
    *,
    dicom_dir: Path,
    output_dir: Path,
    series_number: int | None = None,
    series_key: str | None = None,
    patched_spacing: str,
    binary: str | Path | None = None,
    project_root: Path | None = None,
    dcm2niix: str | Path | None = None,
    timeout_sec: int = 900,
) -> DicomNormalizerResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        command = build_dicom_normalizer_prepare_rescue_command(
            dicom_dir=dicom_dir,
            output_dir=output_dir,
            series_number=series_number,
            series_key=series_key,
            patched_spacing=patched_spacing,
            binary=binary,
            project_root=project_root,
            dcm2niix=dcm2niix,
        )
    except Exception as exc:  # noqa: BLE001
        return DicomNormalizerResult(
            status="failed",
            returncode=127,
            command=[],
            output_json=str(output_dir / "rescue_metadata.json"),
            stdout="",
            stderr="",
            binary=None,
            error=str(exc),
        )

    proc = subprocess.run(  # noqa: S603
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_sec,
        check=False,
    )
    return DicomNormalizerResult(
        status="success" if proc.returncode == 0 else "failed",
        returncode=proc.returncode,
        command=command,
        output_json=str(output_dir / "rescue_metadata.json"),
        stdout=proc.stdout,
        stderr=proc.stderr,
        binary=command[0],
    )


def run_dicom_normalizer_export_rescue_stack(
    *,
    dicom_dir: Path,
    output_dir: Path,
    series_number: int | None = None,
    series_key: str | None = None,
    binary: str | Path | None = None,
    project_root: Path | None = None,
    timeout_sec: int = 900,
) -> DicomNormalizerResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        command = build_dicom_normalizer_export_rescue_stack_command(
            dicom_dir=dicom_dir,
            output_dir=output_dir,
            series_number=series_number,
            series_key=series_key,
            binary=binary,
            project_root=project_root,
        )
    except Exception as exc:  # noqa: BLE001
        return DicomNormalizerResult(
            status="failed",
            returncode=127,
            command=[],
            output_json=str(output_dir / "source_manifest.json"),
            stdout="",
            stderr="",
            binary=None,
            error=str(exc),
        )

    proc = subprocess.run(  # noqa: S603
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_sec,
        check=False,
    )
    return DicomNormalizerResult(
        status="success" if proc.returncode == 0 else "failed",
        returncode=proc.returncode,
        command=command,
        output_json=str(output_dir / "source_manifest.json"),
        stdout=proc.stdout,
        stderr=proc.stderr,
        binary=command[0],
    )


def run_dicom_normalizer_prepare_viewer_export(
    *,
    dicom_dir: Path,
    output_dir: Path,
    group_id: str,
    series_number: int | None = None,
    series_key: str | None = None,
    binary: str | Path | None = None,
    project_root: Path | None = None,
    dcm2niix: str | Path | None = None,
    timeout_sec: int = 900,
) -> DicomNormalizerResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        command = build_dicom_normalizer_prepare_viewer_export_command(
            dicom_dir=dicom_dir,
            output_dir=output_dir,
            group_id=group_id,
            series_number=series_number,
            series_key=series_key,
            binary=binary,
            project_root=project_root,
            dcm2niix=dcm2niix,
        )
    except Exception as exc:  # noqa: BLE001
        return DicomNormalizerResult(
            status="failed",
            returncode=127,
            command=[],
            output_json=str(output_dir / "viewer_export_metadata.json"),
            stdout="",
            stderr="",
            binary=None,
            error=str(exc),
        )

    proc = subprocess.run(  # noqa: S603
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_sec,
        check=False,
    )
    return DicomNormalizerResult(
        status="success" if proc.returncode == 0 else "failed",
        returncode=proc.returncode,
        command=command,
        output_json=str(output_dir / "viewer_export_metadata.json"),
        stdout=proc.stdout,
        stderr=proc.stderr,
        binary=command[0],
    )


def _infer_project_root() -> Path:
    return Path(__file__).resolve().parents[2]
