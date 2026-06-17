from __future__ import annotations

import json
import platform
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from totalsegmentator_wrapper_mac import __version__


UPDATE_SCHEMA = "totalsegmentator_wrapper_mac.update_manifest.v1"


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
        _require_https_url(manifest_url, field="manifest_url")
        request = urllib.request.Request(manifest_url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:  # noqa: S310
            body = response.read(1024 * 1024)
        manifest = json.loads(body.decode("utf-8"))
        _validate_manifest(manifest)
        latest_version = str(manifest["latest_version"])
        minimum_supported = str(manifest.get("minimum_supported_version") or "0")
        download_url = str(manifest.get("download_url") or "")
        release_notes_url = str(manifest.get("release_notes_url") or "")
        sha256 = str(manifest.get("sha256") or "")
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
        update_available = compare_versions(latest_version, current_version) > 0
        critical = compare_versions(minimum_supported, current_version) > 0
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


def compare_versions(left: str, right: str) -> int:
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    width = max(len(left_parts), len(right_parts))
    left_parts.extend([0] * (width - len(left_parts)))
    right_parts.extend([0] * (width - len(right_parts)))
    if left_parts > right_parts:
        return 1
    if left_parts < right_parts:
        return -1
    return 0


def _version_parts(value: str) -> list[int]:
    parts: list[int] = []
    for chunk in value.replace("-", ".").split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if digits:
            parts.append(int(digits))
    return parts or [0]


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("update manifest must be a JSON object")
    if manifest.get("schema") != UPDATE_SCHEMA:
        raise ValueError("unsupported update manifest schema")
    if manifest.get("channel") != "alpha":
        raise ValueError("unsupported update channel")
    for field in ("latest_version", "minimum_supported_version", "download_url", "sha256", "published_at"):
        if not manifest.get(field):
            raise ValueError(f"update manifest missing {field}")


def _require_https_url(url: str, *, field: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{field} must be an HTTPS URL")


def _require_allowed_update_link(
    url: str,
    *,
    field: str,
    manifest_url: str,
    allowed_link_hosts: set[str] | None,
) -> None:
    _require_https_url(url, field=field)
    parsed = urllib.parse.urlparse(url)
    manifest = urllib.parse.urlparse(manifest_url)
    allowed = {manifest.netloc.lower()}
    if allowed_link_hosts:
        allowed.update(host.lower() for host in allowed_link_hosts)
    if parsed.netloc.lower() not in allowed:
        raise ValueError(f"{field} must use the update manifest host")
