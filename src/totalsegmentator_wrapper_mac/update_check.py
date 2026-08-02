from __future__ import annotations

import json
import platform
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from totalsegmentator_wrapper_mac import __version__


UPDATE_SCHEMA = "totalsegmentator_wrapper_mac.update_manifest.v1"
SUPPORTED_UPDATE_CHANNELS = {"alpha", "stable", "stable-v2"}
MAX_UPDATE_MANIFEST_BYTES = 1024 * 1024
MAX_UPDATE_DMG_BYTES = 4 * 1024 * 1024 * 1024
SEMANTIC_VERSION_TRIPLET = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
)
CANONICAL_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class UpdateCheckResult:
    status: str
    current_version: str
    latest_version: str | None
    update_available: bool
    critical: bool
    manifest_url: str
    download_url: str | None = None
    release_notes_url: str | None = None
    sha256: str | None = None
    file_size_bytes: int | None = None
    published_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "totalsegmentator_wrapper_mac.update_check_result.v1",
            "status": self.status,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "critical": self.critical,
            "manifest_url": self.manifest_url,
            "download_url": self.download_url,
            "release_notes_url": self.release_notes_url,
            "sha256": self.sha256,
            "file_size_bytes": self.file_size_bytes,
            "published_at": self.published_at,
            "error": self.error,
        }


def check_for_update(
    *,
    manifest_url: str,
    current_version: str = __version__,
    timeout_sec: float = 5.0,
    allowed_link_hosts: set[str] | None = None,
) -> UpdateCheckResult:
    try:
        _version_parts(current_version, field="current_version")
        _require_https_url(manifest_url, field="manifest_url")
        request = urllib.request.Request(manifest_url, method="GET")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", f"TotalSegmentatorWrapperMac/{_user_agent_version(current_version)}")
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:  # noqa: S310
            response_url = str(response.geturl())
            _require_allowed_update_link(
                response_url,
                field="manifest response URL",
                manifest_url=manifest_url,
                allowed_link_hosts=allowed_link_hosts,
            )
            body = _read_bounded_manifest(response)
        manifest = json.loads(body.decode("utf-8"))
        _validate_manifest(manifest)
        latest_version = str(manifest["latest_version"])
        minimum_supported = str(manifest.get("minimum_supported_version") or "0")
        download_url = str(manifest.get("download_url") or "")
        release_notes_url = str(manifest.get("release_notes_url") or "")
        sha256 = str(manifest.get("sha256") or "")
        file_size_value = manifest.get("file_size_bytes")
        file_size_bytes = (
            int(file_size_value) if file_size_value is not None else None
        )
        if download_url:
            _require_allowed_update_link(
                download_url,
                field="download_url",
                manifest_url=manifest_url,
                allowed_link_hosts=allowed_link_hosts,
            )
        if release_notes_url:
            _require_allowed_update_link(
                release_notes_url,
                field="release_notes_url",
                manifest_url=manifest_url,
                allowed_link_hosts=allowed_link_hosts,
            )
        latest_order = compare_versions(latest_version, current_version)
        if latest_order < 0:
            raise ValueError(
                "update manifest latest_version is older than current_version"
            )
        update_available = latest_order > 0
        critical = update_available and compare_versions(
            minimum_supported,
            current_version,
        ) > 0
        status = "update_available" if update_available else "current"
        if critical:
            status = "critical_update_available"
        return UpdateCheckResult(
            status=status,
            current_version=current_version,
            latest_version=latest_version,
            update_available=update_available,
            critical=critical,
            manifest_url=manifest_url,
            download_url=download_url or None,
            release_notes_url=release_notes_url or None,
            sha256=sha256 or None,
            file_size_bytes=file_size_bytes,
            published_at=manifest.get("published_at"),
        )
    except (OSError, urllib.error.URLError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        return UpdateCheckResult(
            status="failed",
            current_version=current_version,
            latest_version=None,
            update_available=False,
            critical=False,
            manifest_url=manifest_url,
            error=str(exc),
        )


def update_request_metadata() -> dict[str, str]:
    return {
        "app_version": __version__,
        "architecture": platform.machine(),
        "macos": platform.mac_ver()[0],
    }


def _user_agent_version(version: str) -> str:
    value = "".join(char for char in version if char.isalnum() or char in ".-_")
    return value or __version__


def _read_bounded_manifest(response: Any) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= MAX_UPDATE_MANIFEST_BYTES:
        chunk = response.read(min(64 * 1024, MAX_UPDATE_MANIFEST_BYTES + 1 - total))
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise ValueError("update manifest response must contain bytes")
        chunks.append(chunk)
        total += len(chunk)
    if total > MAX_UPDATE_MANIFEST_BYTES:
        raise ValueError("update manifest is too large")
    return b"".join(chunks)


def compare_versions(left: str, right: str) -> int:
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    for left_part, right_part in zip(left_parts, right_parts, strict=True):
        if len(left_part) != len(right_part):
            return 1 if len(left_part) > len(right_part) else -1
        if left_part != right_part:
            return 1 if left_part > right_part else -1
    return 0


def _version_parts(value: str, *, field: str = "version") -> list[str]:
    if not isinstance(value, str) or SEMANTIC_VERSION_TRIPLET.fullmatch(value) is None:
        raise ValueError(f"{field} must be a semantic version triplet")
    return value.split(".")


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("update manifest must be a JSON object")
    if manifest.get("schema") != UPDATE_SCHEMA:
        raise ValueError("unsupported update manifest schema")
    if manifest.get("channel") not in SUPPORTED_UPDATE_CHANNELS:
        raise ValueError("unsupported update channel")
    for field in ("latest_version", "minimum_supported_version", "download_url", "sha256", "published_at"):
        if not manifest.get(field):
            raise ValueError(f"update manifest missing {field}")
    for field in ("latest_version", "minimum_supported_version"):
        _version_parts(
            manifest[field],
            field=f"update manifest {field}",
        )
    sha256 = manifest["sha256"]
    if not isinstance(sha256, str) or CANONICAL_SHA256.fullmatch(sha256) is None:
        raise ValueError("update manifest sha256 must be 64 lowercase hexadecimal characters")
    if compare_versions(
        manifest["latest_version"],
        manifest["minimum_supported_version"],
    ) < 0:
        raise ValueError(
            "update manifest latest_version must not be older than "
            "minimum_supported_version"
        )
    file_size = manifest.get("file_size_bytes")
    if manifest["channel"] == "stable-v2" and file_size is None:
        raise ValueError("stable-v2 update manifest missing file_size_bytes")
    if file_size is not None and (
        isinstance(file_size, bool)
        or not isinstance(file_size, int)
        or file_size <= 0
        or file_size > MAX_UPDATE_DMG_BYTES
    ):
        raise ValueError(
            "update manifest file_size_bytes must be a positive integer "
            f"no greater than {MAX_UPDATE_DMG_BYTES}"
        )


def _require_https_url(url: str, *, field: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            f"{field} must be an HTTPS URL without userinfo and with port 443"
        ) from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not _normalize_hostname(parsed.hostname, field=field)
    ):
        raise ValueError(
            f"{field} must be an HTTPS URL without userinfo and with port 443"
        )


def _normalize_hostname(hostname: str | None, *, field: str) -> str:
    if not hostname:
        raise ValueError(f"{field} must be an HTTPS URL")
    try:
        normalized = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError(f"{field} contains an invalid hostname") from exc
    if not normalized:
        raise ValueError(f"{field} contains an invalid hostname")
    return normalized


def _normalize_allowed_hostname(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError("allowed update host must not be empty")
    parsed = urllib.parse.urlsplit(f"https://{candidate}")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("allowed update host must be a hostname")
    _require_https_url(parsed.geturl(), field="allowed update host")
    return _normalize_hostname(parsed.hostname, field="allowed update host")


def _require_allowed_update_link(
    url: str,
    *,
    field: str,
    manifest_url: str,
    allowed_link_hosts: set[str] | None,
) -> None:
    _require_https_url(url, field=field)
    parsed = urllib.parse.urlsplit(url)
    manifest = urllib.parse.urlsplit(manifest_url)
    allowed = {
        _normalize_hostname(manifest.hostname, field="manifest_url")
    }
    if allowed_link_hosts:
        allowed.update(
            _normalize_allowed_hostname(host) for host in allowed_link_hosts
        )
    if _normalize_hostname(parsed.hostname, field=field) not in allowed:
        raise ValueError(f"{field} must use the update manifest host")
