#!/usr/bin/env python3
"""Fetch and safely extract a pinned source tar archive with resumable caching."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Sequence
from urllib.parse import urlparse


BUFFER_SIZE = 1024 * 1024
MAX_SOURCE_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_SOURCE_MEMBERS = 100_000
MAX_SOURCE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
CONTENT_RANGE = re.compile(r"^bytes ([0-9]+)-([0-9]+)/([0-9]+)$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
PROVENANCE_NAME = ".source-archive-provenance.json"
DEFAULT_ALLOWED_SOURCE_HOSTS = frozenset({"github.com", "codeload.github.com"})


class PinnedSourceError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(BUFFER_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _identity(url: str, sha256: str) -> dict[str, object]:
    return {
        "schema": "totalsegmentator_wrapper_mac.pinned_source_download.v1",
        "url": url,
        "sha256": sha256,
    }


def _validated_url(url: str, allowed_hosts: frozenset[str]) -> None:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise PinnedSourceError(f"source URL has an invalid port: {url}") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise PinnedSourceError(
            f"source URL must use HTTPS on an approved host: {url}"
        )


def _validate_owned_regular_or_missing(path: Path, label: str) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise PinnedSourceError(
            f"{label} must be an owner-controlled regular non-symlink file: {path}"
        )
    return True


def _validate_owned_directory(path: Path, label: str) -> None:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise PinnedSourceError(
            f"{label} must be an owner-controlled non-symlink directory: {path}"
        )


def _response_status(response: object) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()  # type: ignore[attr-defined]
    return int(status)


def _response_url(response: object) -> str:
    return str(response.geturl())  # type: ignore[attr-defined]


def _response_header(response: object, name: str) -> str | None:
    return response.headers.get(name)  # type: ignore[attr-defined]


def download_pinned_archive(
    *,
    url: str,
    expected_sha256: str,
    archive: Path,
    allowed_hosts: frozenset[str] = DEFAULT_ALLOWED_SOURCE_HOSTS,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> Path:
    if SHA256.fullmatch(expected_sha256) is None:
        raise PinnedSourceError("expected SHA-256 must be 64 lowercase hexadecimal characters")
    _validated_url(url, allowed_hosts)
    archive.parent.mkdir(parents=True, exist_ok=True)
    _validate_owned_directory(archive.parent, "source cache directory")
    part = archive.with_name(f"{archive.name}.part")
    sidecar = archive.with_name(f"{archive.name}.part.json")
    receipt = archive.with_name(f"{archive.name}.json")
    identity = _identity(url, expected_sha256)

    archive_exists = _validate_owned_regular_or_missing(archive, "cached source archive")
    part_exists = _validate_owned_regular_or_missing(part, "partial source archive")
    sidecar_exists = _validate_owned_regular_or_missing(sidecar, "partial source metadata")
    _validate_owned_regular_or_missing(receipt, "source archive receipt")
    if archive_exists:
        actual = sha256_file(archive)
        if actual != expected_sha256:
            raise PinnedSourceError(
                f"cached source archive checksum mismatch: expected {expected_sha256}, found {actual}"
            )
        _atomic_json(receipt, {**identity, "size_bytes": archive.stat().st_size})
        return archive

    if part_exists or sidecar_exists:
        sidecar_payload: object = None
        try:
            if sidecar_exists:
                sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            sidecar_payload = None
        if (
            sidecar_payload != identity
            or not part_exists
            or part.stat().st_size > MAX_SOURCE_ARCHIVE_BYTES
        ):
            if part_exists:
                part.unlink()
                part_exists = False
            if sidecar_exists:
                sidecar.unlink()
                sidecar_exists = False

    _atomic_json(sidecar, identity)
    resume_offset = part.stat().st_size if part.exists() else 0
    headers = {"Accept-Encoding": "identity", "User-Agent": "TotalSegmentatorWrapperMac-source-builder/0.4.1"}
    if resume_offset:
        headers["Range"] = f"bytes={resume_offset}-"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        response = opener(request, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and resume_offset and sha256_file(part) == expected_sha256:
            exc.close()
            os.replace(part, archive)
            if sidecar.exists():
                sidecar.unlink()
            _atomic_json(receipt, {**identity, "size_bytes": archive.stat().st_size})
            return archive
        if exc.code == 416 and resume_offset:
            # A stale or oversized partial can otherwise cause an endless 416
            # loop.  Remove only the already-validated cache pair and perform
            # one zero-byte request; a second 416 is a hard failure.
            exc.close()
            part.unlink()
            if sidecar.exists():
                sidecar.unlink()
            return download_pinned_archive(
                url=url,
                expected_sha256=expected_sha256,
                archive=archive,
                allowed_hosts=allowed_hosts,
                opener=opener,
            )
        code = exc.code
        exc.close()
        raise PinnedSourceError(f"source download failed: HTTP {code}") from exc
    except OSError as exc:
        raise PinnedSourceError(f"source download failed: {exc}") from exc

    with response:  # type: ignore[attr-defined]
        final_url = _response_url(response)
        _validated_url(final_url, allowed_hosts)
        encoding = (_response_header(response, "Content-Encoding") or "identity").lower()
        if encoding not in ("", "identity"):
            raise PinnedSourceError(
                f"source server returned unsupported Content-Encoding {encoding!r}"
            )
        status = _response_status(response)
        append = False
        expected_response_bytes: int | None = None
        if resume_offset and status == 206:
            match = CONTENT_RANGE.fullmatch(
                _response_header(response, "Content-Range") or ""
            )
            if match is None:
                raise PinnedSourceError("resume response has an invalid Content-Range")
            start, end, total = (int(value) for value in match.groups())
            if start != resume_offset or end < start or total != end + 1:
                raise PinnedSourceError(
                    "resume response Content-Range does not exactly cover the remaining archive"
                )
            expected_response_bytes = end - start + 1
            append = True
        elif resume_offset and status == 200:
            # The server ignored Range.  Never concatenate a full response to a
            # partial archive; restart this same complete response at byte zero.
            part.unlink()
            resume_offset = 0
        elif status != 200:
            raise PinnedSourceError(f"unexpected source download HTTP status {status}")

        content_length = _response_header(response, "Content-Length")
        if content_length is not None:
            if not content_length.isdigit():
                raise PinnedSourceError("source response has an invalid Content-Length")
            declared = int(content_length)
            if expected_response_bytes is not None and declared != expected_response_bytes:
                raise PinnedSourceError(
                    "resume response Content-Length disagrees with Content-Range"
                )
            expected_response_bytes = declared

        received = 0
        mode = "ab" if append else "wb"
        with part.open(mode) as handle:
            while chunk := response.read(BUFFER_SIZE):  # type: ignore[attr-defined]
                received += len(chunk)
                if resume_offset + received > MAX_SOURCE_ARCHIVE_BYTES:
                    raise PinnedSourceError("source archive exceeds the configured size limit")
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if expected_response_bytes is not None and received != expected_response_bytes:
            raise PinnedSourceError(
                f"source response ended early: expected {expected_response_bytes} bytes, received {received}"
            )

    actual = sha256_file(part)
    if actual != expected_sha256:
        raise PinnedSourceError(
            f"downloaded source archive checksum mismatch: expected {expected_sha256}, found {actual}"
        )
    os.replace(part, archive)
    if sidecar.exists():
        sidecar.unlink()
    _atomic_json(receipt, {**identity, "size_bytes": archive.stat().st_size})
    return archive


def _safe_member_path(name: str, expected_root: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise PinnedSourceError(f"unsafe source archive member path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise PinnedSourceError(f"unsafe source archive member path: {name!r}")
    if not path.parts or path.parts[0] != expected_root:
        raise PinnedSourceError(
            f"source archive member is outside expected root {expected_root!r}: {name!r}"
        )
    return path


def _source_tar_mode(archive: Path) -> str:
    """Accept only the two compression formats used by reviewed source policies."""

    name = archive.name
    if name.endswith((".tar.gz", ".tgz")):
        return "r:gz"
    if name.endswith(".tar.xz"):
        return "r:xz"
    raise PinnedSourceError(
        "source archive must use a supported .tar.gz, .tgz, or .tar.xz suffix: "
        f"{archive}"
    )


def extract_pinned_tar_archive(
    *,
    archive: Path,
    output_parent: Path,
    expected_root: str,
    url: str,
    expected_sha256: str,
) -> Path:
    expected_path = PurePosixPath(expected_root)
    if (
        expected_path.is_absolute()
        or len(expected_path.parts) != 1
        or expected_root in ("", ".", "..")
    ):
        raise PinnedSourceError(f"invalid expected source root: {expected_root!r}")
    if not _validate_owned_regular_or_missing(archive, "source archive"):
        raise PinnedSourceError(f"source archive is missing: {archive}")
    if sha256_file(archive) != expected_sha256:
        raise PinnedSourceError("refusing to extract source archive with an invalid checksum")
    target = output_parent / expected_root
    provenance = _identity(url, expected_sha256)
    if target.is_symlink():
        raise PinnedSourceError(f"existing source tree must not be a symlink: {target}")
    if target.exists():
        raise PinnedSourceError(
            "existing extracted source trees will not be reused because their "
            f"contents are mutable: {target}"
        )

    output_parent.mkdir(parents=True, exist_ok=True)
    _validate_owned_directory(output_parent, "source extraction directory")
    try:
        source = tarfile.open(archive, mode=_source_tar_mode(archive))
    except (OSError, tarfile.TarError) as exc:
        raise PinnedSourceError(f"invalid source tar archive: {exc}") from exc
    with source:
        members = source.getmembers()
        if not members or len(members) > MAX_SOURCE_MEMBERS:
            raise PinnedSourceError("source archive member count is invalid")
        total_size = 0
        validated: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
        seen: set[PurePosixPath] = set()
        for member in members:
            path = _safe_member_path(member.name.rstrip("/"), expected_root)
            if path in seen:
                raise PinnedSourceError(f"duplicate source archive member: {member.name}")
            seen.add(path)
            if not (member.isdir() or member.isfile()):
                raise PinnedSourceError(
                    f"unsupported source archive member type: {member.name}"
                )
            if member.isfile():
                total_size += member.size
                if total_size > MAX_SOURCE_UNCOMPRESSED_BYTES:
                    raise PinnedSourceError("source archive expands beyond the size limit")
            validated.append((member, path))

        staging = Path(
            tempfile.mkdtemp(prefix=f".{expected_root}.extracting.", dir=output_parent)
        )
        try:
            for member, relative in validated:
                destination = staging.joinpath(*relative.parts)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    destination.chmod(0o755)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                extracted: BinaryIO | None = source.extractfile(member)
                if extracted is None:
                    raise PinnedSourceError(f"could not read source member: {member.name}")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                descriptor = os.open(destination, flags, 0o755 if member.mode & 0o111 else 0o644)
                with extracted, os.fdopen(descriptor, "wb") as output:
                    remaining = member.size
                    while remaining:
                        chunk = extracted.read(min(BUFFER_SIZE, remaining))
                        if not chunk:
                            raise PinnedSourceError(f"source member ended early: {member.name}")
                        output.write(chunk)
                        remaining -= len(chunk)
                    if extracted.read(1):
                        raise PinnedSourceError(f"source member exceeds declared size: {member.name}")
            staged_source = staging / expected_root
            if not staged_source.is_dir():
                raise PinnedSourceError(f"source archive omitted expected root {expected_root!r}")
            _atomic_json(staged_source / PROVENANCE_NAME, provenance)
            os.replace(staged_source, target)
            staging.rmdir()
        except BaseException:
            if staging.exists() and staging.is_dir() and not staging.is_symlink():
                shutil.rmtree(staging)
            raise
    return target


def extract_pinned_tar_gz(
    *,
    archive: Path,
    output_parent: Path,
    expected_root: str,
    url: str,
    expected_sha256: str,
) -> Path:
    """Compatibility wrapper for existing gzip-only callers."""

    if _source_tar_mode(archive) != "r:gz":
        raise PinnedSourceError(
            f"gzip source extraction requires a .tar.gz or .tgz archive: {archive}"
        )
    return extract_pinned_tar_archive(
        archive=archive,
        output_parent=output_parent,
        expected_root=expected_root,
        url=url,
        expected_sha256=expected_sha256,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch a pinned source tar archive safely.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output-parent", required=True, type=Path)
    parser.add_argument("--expected-root", required=True)
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help=(
            "approved HTTPS source host (repeatable); omitting this preserves "
            "the default GitHub-only source policy"
        ),
    )
    return parser.parse_args(argv)


def _cli_allowed_hosts(values: Sequence[object]) -> frozenset[str]:
    if not values:
        return DEFAULT_ALLOWED_SOURCE_HOSTS
    hosts: list[str] = []
    for value in values:
        if (
            not isinstance(value, str)
            or HOSTNAME.fullmatch(value) is None
            or value != value.lower()
        ):
            raise PinnedSourceError(f"invalid approved source host: {value!r}")
        if value not in hosts:
            hosts.append(value)
    return frozenset(hosts)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    allowed_hosts = _cli_allowed_hosts(args.allowed_host)
    archive = download_pinned_archive(
        url=args.url,
        expected_sha256=args.sha256,
        archive=args.archive.expanduser(),
        allowed_hosts=allowed_hosts,
    )
    source = extract_pinned_tar_archive(
        archive=archive,
        output_parent=args.output_parent.expanduser(),
        expected_root=args.expected_root,
        url=args.url,
        expected_sha256=args.sha256,
    )
    print(source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
