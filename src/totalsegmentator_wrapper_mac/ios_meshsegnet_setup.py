from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4

from totalsegmentator_wrapper_mac.ios_meshsegnet_manifest import (
    EXPECTED_MODEL_SHA256,
    MODEL_DOWNLOAD_URL,
    MODEL_FILENAME,
    model_provenance,
)


STATUS_SCHEMA = "totalsegmentator_wrapper_mac.ios_meshsegnet_model_status.v1"
PARTIAL_SCHEMA = "totalsegmentator_wrapper_mac.ios_meshsegnet_model_partial.v1"
PROGRESS_PREFIX = "SETUP_DOWNLOAD_PROGRESS "
LOCK_FILENAME = ".ios-meshsegnet-model.lock"
DEFAULT_CHUNK_SIZE = 1024 * 1024
MAX_MODEL_BYTES = 128 * 1024 * 1024
_CONTENT_RANGE_PATTERN = re.compile(r"bytes (\d+)-(\d+)/(\d+)")


class MeshSegNetModelError(RuntimeError):
    error_code = "model_download_failed"


class MeshSegNetModelBusyError(MeshSegNetModelError):
    error_code = "model_prepare_busy"


class MeshSegNetModelIntegrityError(MeshSegNetModelError):
    error_code = "model_integrity_failed"


def _sha256(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise MeshSegNetModelIntegrityError(
                f"{path.name} must not be a symbolic link"
            ) from exc
        raise
    try:
        file_status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_status.st_mode)
            or file_status.st_uid != os.geteuid()
            or file_status.st_nlink != 1
        ):
            raise MeshSegNetModelIntegrityError(
                f"{path.name} must be a private regular file"
            )
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(DEFAULT_CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _safe_model_root(model_root: Path, *, create: bool) -> Path:
    root = Path(os.path.abspath(model_root.expanduser()))
    if root.is_symlink():
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet model directory must not be a symbolic link"
        )
    if create:
        root.mkdir(parents=True, exist_ok=True)
    try:
        root_status = root.lstat()
    except OSError:
        return root
    if not stat.S_ISDIR(root_status.st_mode) or stat.S_ISLNK(root_status.st_mode):
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet model path must be a directory"
        )
    return root


def _file_state(path: Path) -> str:
    try:
        file_status = path.lstat()
    except OSError:
        return "missing"
    if stat.S_ISLNK(file_status.st_mode):
        return "symlink"
    if not stat.S_ISREG(file_status.st_mode):
        return "not_regular"
    if file_status.st_uid != os.geteuid() or file_status.st_nlink != 1:
        return "not_private"
    return "regular"


@contextmanager
def _exclusive_model_lock(model_root: Path) -> Iterator[None]:
    lock_path = model_root / LOCK_FILENAME
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise MeshSegNetModelIntegrityError(
                "MeshSegNet model lock must not be a symbolic link"
            ) from exc
        raise
    try:
        lock_status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lock_status.st_mode)
            or lock_status.st_uid != os.geteuid()
            or lock_status.st_nlink != 1
            or lock_status.st_mode & 0o022
        ):
            raise MeshSegNetModelIntegrityError(
                "MeshSegNet model lock is not a private regular file"
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MeshSegNetModelBusyError(
                "MeshSegNet model preparation is already running"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _partial_payload(*, total_bytes: int | None) -> dict[str, Any]:
    return {
        "schema": PARTIAL_SCHEMA,
        "url": MODEL_DOWNLOAD_URL,
        "filename": MODEL_FILENAME,
        "sha256": EXPECTED_MODEL_SHA256,
        "max_size_bytes": MAX_MODEL_BYTES,
        "total_bytes": total_bytes,
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _write_partial_state(path: Path, *, total_bytes: int | None) -> None:
    _write_json_atomic(path, _partial_payload(total_bytes=total_bytes))


def _validated_partial_size(path: Path) -> int:
    state = _file_state(path)
    if state == "missing":
        return 0
    if state == "symlink":
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet partial download must not be a symbolic link"
        )
    if state != "regular":
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet partial download must be a private regular file"
        )
    size = path.stat().st_size
    if size > MAX_MODEL_BYTES:
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet partial download exceeds the operational safety limit"
        )
    return size


def _clear_partial(partial: Path, sidecar: Path) -> None:
    for path in (partial, sidecar):
        state = _file_state(path)
        if state == "missing":
            continue
        if state == "symlink":
            raise MeshSegNetModelIntegrityError(
                f"{path.name} must not be a symbolic link"
            )
        if state != "regular":
            raise MeshSegNetModelIntegrityError(
                f"{path.name} must be a private regular file"
            )
        path.unlink()


def _read_partial_state(path: Path) -> dict[str, Any] | None:
    state = _file_state(path)
    if state == "missing":
        return None
    if state == "symlink":
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet partial metadata must not be a symbolic link"
        )
    if state != "regular":
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet partial metadata must be a private regular file"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _sidecar_matches(path: Path) -> tuple[bool, int | None]:
    payload = _read_partial_state(path)
    if payload is None:
        return False, None
    expected = _partial_payload(total_bytes=payload.get("total_bytes"))
    if payload != expected:
        return False, None
    total_bytes = payload.get("total_bytes")
    if total_bytes is not None and (
        type(total_bytes) is not int
        or total_bytes <= 0
        or total_bytes > MAX_MODEL_BYTES
    ):
        return False, None
    return True, total_bytes


def _parse_content_length(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.isdigit():
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet response has an invalid Content-Length"
        )
    return int(value)


def _parse_content_range(value: Any) -> tuple[int, int, int]:
    match = _CONTENT_RANGE_PATTERN.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet response has an invalid Content-Range"
        )
    start, end, total = (int(part) for part in match.groups())
    if start > end or end >= total or total <= 0:
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet response has an impossible Content-Range"
        )
    return start, end, total


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()
    if type(status) is not int:
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet response has no valid HTTP status"
        )
    return status


def _validate_response_url(response: Any) -> None:
    geturl = getattr(response, "geturl", None)
    final_url = geturl() if callable(geturl) else None
    if not isinstance(final_url, str) or not final_url:
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet response did not expose its final HTTPS URL"
        )
    try:
        parsed = urllib.parse.urlsplit(final_url)
        port = parsed.port
    except ValueError as exc:
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet response has an invalid final URL"
        ) from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet response final URL must use HTTPS"
        )
    if parsed.username is not None or parsed.password is not None:
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet response final URL must not contain credentials"
        )
    if port not in (None, 443):
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet response final URL must use the standard HTTPS port"
        )


def _validate_content_encoding(headers: Any) -> None:
    content_encoding = headers.get("Content-Encoding")
    if content_encoding is None:
        return
    if not isinstance(content_encoding, str) or content_encoding.strip().lower() != "identity":
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet response must use identity Content-Encoding"
        )


def _write_progress(
    progress_log: Path | None,
    *,
    status: str,
    completed_bytes: int,
    total_bytes: int | None,
    rate_bps: float | None = None,
    eta_seconds: float | None = None,
    resumed: bool = False,
    resume_from_bytes: int = 0,
    restart_reason: str | None = None,
) -> None:
    if progress_log is None:
        return
    percent = (
        min(100, int(completed_bytes * 100 / total_bytes))
        if total_bytes is not None and total_bytes > 0
        else None
    )
    payload = {
        "source": "ios-meshsegnet",
        "status": status,
        "index": 1,
        "task_total": 1,
        "completed_bytes": completed_bytes,
        "total_bytes": total_bytes,
        "percent": percent,
        "rate_bps": rate_bps,
        "eta_seconds": eta_seconds,
        "resumed": resumed,
        "resume_from_bytes": resume_from_bytes,
        "restart_reason": restart_reason,
    }
    progress_log.parent.mkdir(parents=True, exist_ok=True)
    with progress_log.open("a", encoding="utf-8") as log:
        log.write(PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        log.flush()


def _open_partial_writer(path: Path, *, append: bool):
    flags = os.O_CREAT | os.O_WRONLY
    flags |= os.O_APPEND if append else os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o600)
    file_status = os.fstat(descriptor)
    if (
        not stat.S_ISREG(file_status.st_mode)
        or file_status.st_uid != os.geteuid()
        or file_status.st_nlink != 1
    ):
        os.close(descriptor)
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet partial download must be a private regular file"
        )
    return os.fdopen(descriptor, "ab" if append else "wb")


def _restart_partial(
    partial: Path,
    sidecar: Path,
    *,
    progress_log: Path | None,
    resume_from_bytes: int,
    reason: str,
) -> None:
    _clear_partial(partial, sidecar)
    _write_partial_state(sidecar, total_bytes=None)
    _write_progress(
        progress_log,
        status="restart",
        completed_bytes=0,
        total_bytes=None,
        resumed=resume_from_bytes > 0,
        resume_from_bytes=resume_from_bytes,
        restart_reason=reason,
    )


def _download_model(
    partial: Path,
    sidecar: Path,
    *,
    known_total: int | None,
    progress_log: Path | None,
    opener: Callable[..., Any],
    timeout_sec: int,
    chunk_size: int,
) -> None:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    initial_bytes = _validated_partial_size(partial)
    full_restart_used = False
    while True:
        offset = _validated_partial_size(partial)
        request = urllib.request.Request(
            MODEL_DOWNLOAD_URL,
            headers={
                "User-Agent": "TotalSegmentatorWrapperMac/0.4.1",
                "Accept-Encoding": "identity",
            },
        )
        if offset:
            request.add_header("Range", f"bytes={offset}-")
        try:
            response = opener(request, timeout=timeout_sec)
        except urllib.error.HTTPError as exc:
            _validate_response_url(exc)
            if offset and exc.code == 416 and not full_restart_used:
                exc.close()
                _restart_partial(
                    partial,
                    sidecar,
                    progress_log=progress_log,
                    resume_from_bytes=initial_bytes,
                    reason="range_not_satisfiable",
                )
                known_total = None
                full_restart_used = True
                continue
            raise

        restart_reason: str | None = None
        with response:
            _validate_response_url(response)
            status = _response_status(response)
            headers = response.headers or {}
            _validate_content_encoding(headers)
            content_length = _parse_content_length(headers.get("Content-Length"))
            expected_response_bytes: int | None = None
            response_total = known_total
            if offset:
                if status == 200:
                    restart_reason = "range_ignored"
                elif status != 206:
                    raise MeshSegNetModelIntegrityError(
                        f"MeshSegNet resume returned unexpected HTTP status {status}"
                    )
                else:
                    start, end, declared_total = _parse_content_range(
                        headers.get("Content-Range")
                    )
                    if start != offset:
                        raise MeshSegNetModelIntegrityError(
                            "MeshSegNet Content-Range did not start at the requested byte"
                        )
                    if known_total is not None and declared_total != known_total:
                        raise MeshSegNetModelIntegrityError(
                            "MeshSegNet response changed the declared total size"
                        )
                    response_total = declared_total
                    expected_response_bytes = end - start + 1
                    if (
                        content_length is not None
                        and content_length != expected_response_bytes
                    ):
                        raise MeshSegNetModelIntegrityError(
                            "MeshSegNet Content-Length disagrees with Content-Range"
                        )
            else:
                if status != 200:
                    raise MeshSegNetModelIntegrityError(
                        f"MeshSegNet full download returned unexpected HTTP status {status}"
                    )
                if content_length is not None:
                    response_total = content_length
                    expected_response_bytes = content_length

            if restart_reason is None:
                if response_total is not None and (
                    response_total <= 0 or response_total > MAX_MODEL_BYTES
                ):
                    raise MeshSegNetModelIntegrityError(
                        "MeshSegNet model exceeds the operational safety limit"
                    )
                if offset > MAX_MODEL_BYTES or (
                    response_total is not None and offset > response_total
                ):
                    raise MeshSegNetModelIntegrityError(
                        "MeshSegNet partial download exceeds its validated size"
                    )
                _write_partial_state(sidecar, total_bytes=response_total)
                completed = offset
                response_bytes = 0
                started = time.perf_counter()
                _write_progress(
                    progress_log,
                    status="downloading",
                    completed_bytes=completed,
                    total_bytes=response_total,
                    resumed=initial_bytes > 0,
                    resume_from_bytes=initial_bytes,
                )
                with _open_partial_writer(partial, append=offset > 0) as output:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        next_size = completed + len(chunk)
                        if next_size > MAX_MODEL_BYTES:
                            raise MeshSegNetModelIntegrityError(
                                "MeshSegNet model exceeds the operational safety limit"
                            )
                        if response_total is not None and next_size > response_total:
                            raise MeshSegNetModelIntegrityError(
                                "MeshSegNet response exceeded its declared total size"
                            )
                        output.write(chunk)
                        output.flush()
                        completed = next_size
                        response_bytes += len(chunk)
                        elapsed = max(time.perf_counter() - started, 1e-6)
                        rate_bps = response_bytes / elapsed
                        eta_seconds = (
                            max(0.0, (response_total - completed) / rate_bps)
                            if response_total is not None and rate_bps > 0
                            else None
                        )
                        _write_progress(
                            progress_log,
                            status="downloading",
                            completed_bytes=completed,
                            total_bytes=response_total,
                            rate_bps=rate_bps,
                            eta_seconds=eta_seconds,
                            resumed=initial_bytes > 0,
                            resume_from_bytes=initial_bytes,
                        )
                    os.fsync(output.fileno())
                if (
                    expected_response_bytes is not None
                    and response_bytes != expected_response_bytes
                ):
                    raise ConnectionError(
                        "MeshSegNet model response ended before its declared size; "
                        "partial data was preserved"
                    )
                final_size = _validated_partial_size(partial)
                if response_total is not None and final_size != response_total:
                    raise ConnectionError(
                        "MeshSegNet model download ended before its declared total; "
                        "partial data was preserved"
                    )
                _write_progress(
                    progress_log,
                    status="verifying",
                    completed_bytes=final_size,
                    total_bytes=response_total or final_size,
                    resumed=initial_bytes > 0,
                    resume_from_bytes=initial_bytes,
                )
                return

        if restart_reason is not None:
            if full_restart_used:
                raise MeshSegNetModelIntegrityError(
                    "MeshSegNet server ignored HTTP Range more than once"
                )
            _restart_partial(
                partial,
                sidecar,
                progress_log=progress_log,
                resume_from_bytes=initial_bytes,
                reason=restart_reason,
            )
            known_total = None
            full_restart_used = True


def _publish_verified_model(partial: Path, model_path: Path) -> None:
    actual_size = _validated_partial_size(partial)
    if actual_size <= 0:
        raise MeshSegNetModelIntegrityError("MeshSegNet model download is empty")
    actual_sha256 = _sha256(partial)
    if actual_sha256 != EXPECTED_MODEL_SHA256:
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet model SHA-256 mismatch: "
            f"expected {EXPECTED_MODEL_SHA256}, got {actual_sha256}"
        )
    target_state = _file_state(model_path)
    if target_state not in {"missing", "regular", "symlink"}:
        raise MeshSegNetModelIntegrityError(
            "MeshSegNet model target is not a replaceable file"
        )
    os.replace(partial, model_path)
    directory_descriptor = os.open(model_path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def model_status(model_root: Path) -> dict[str, Any]:
    model_root = _safe_model_root(model_root, create=False)
    model_path = model_root / MODEL_FILENAME
    model_file_state = _file_state(model_path)
    actual_sha256 = _sha256(model_path) if model_file_state == "regular" else None
    ready = actual_sha256 == EXPECTED_MODEL_SHA256
    return {
        "schema": STATUS_SCHEMA,
        "status": "ready" if ready else "not_installed",
        "model_state": "ready" if ready else "not_installed",
        "model_path": str(model_path),
        "actual_sha256": actual_sha256,
        "file_state": model_file_state,
        "provenance": model_provenance(),
    }


def install_model(
    model_root: Path,
    *,
    timeout_sec: int = 900,
    opener: Callable[..., Any] | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    progress_log: Path | None = None,
) -> dict[str, Any]:
    model_root = _safe_model_root(model_root, create=True)
    opener = opener or urllib.request.urlopen
    with _exclusive_model_lock(model_root):
        existing = model_status(model_root)
        if existing["model_state"] == "ready":
            return existing

        model_path = model_root / MODEL_FILENAME
        partial = model_root / f"{MODEL_FILENAME}.part"
        sidecar = model_root / f"{MODEL_FILENAME}.part.json"
        partial_size = _validated_partial_size(partial)
        sidecar_matches, known_total = _sidecar_matches(sidecar)
        if not sidecar_matches or (partial_size == 0 and known_total is not None):
            _clear_partial(partial, sidecar)
            _write_partial_state(sidecar, total_bytes=None)
            known_total = None
            partial_size = 0
        elif known_total is not None and partial_size > known_total:
            _clear_partial(partial, sidecar)
            _write_partial_state(sidecar, total_bytes=None)
            known_total = None
            partial_size = 0

        try:
            if partial_size and _sha256(partial) == EXPECTED_MODEL_SHA256:
                _write_progress(
                    progress_log,
                    status="verifying",
                    completed_bytes=partial_size,
                    total_bytes=partial_size,
                    resumed=True,
                    resume_from_bytes=partial_size,
                )
            else:
                _download_model(
                    partial,
                    sidecar,
                    known_total=known_total,
                    progress_log=progress_log,
                    opener=opener,
                    timeout_sec=timeout_sec,
                    chunk_size=chunk_size,
                )
            resume_from = partial_size
            _publish_verified_model(partial, model_path)
            sidecar.unlink(missing_ok=True)
            installed = model_status(model_root)
            _write_progress(
                progress_log,
                status="complete",
                completed_bytes=model_path.stat().st_size,
                total_bytes=model_path.stat().st_size,
                resumed=resume_from > 0,
                resume_from_bytes=resume_from,
            )
            return installed
        except MeshSegNetModelIntegrityError:
            _clear_partial(partial, sidecar)
            raise
        except Exception:
            preserved = _validated_partial_size(partial)
            _write_progress(
                progress_log,
                status="failed",
                completed_bytes=preserved,
                total_bytes=known_total,
                resumed=partial_size > 0,
                resume_from_bytes=partial_size,
            )
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "prepare"))
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--progress-log", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = (
            model_status(args.model_root)
            if args.command == "status"
            else install_model(args.model_root, progress_log=args.progress_log)
        )
        exit_code = 0
    except Exception as exc:
        error_code = getattr(exc, "error_code", "model_download_failed")
        payload = {
            "schema": STATUS_SCHEMA,
            "status": "failed",
            "model_state": "failed",
            "error_code": error_code,
            "safe_reason": "The MeshSegNet model preparation did not complete.",
            "error_type": type(exc).__name__,
            "provenance": model_provenance(),
        }
        exit_code = 1
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
