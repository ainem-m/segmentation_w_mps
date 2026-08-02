from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata, resources
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit
from uuid import uuid4


PROGRESS_PREFIX = "SETUP_DOWNLOAD_PROGRESS "
MANIFEST_RESOURCE = "totalseg_setup_weights_manifest.json"
REGISTRY_FILENAME = ".totalsegmentator-wrapper-setup-weights.json"
REGISTRY_SCHEMA = "totalsegmentator_wrapper_mac.setup_weights_registry.v2"
LEGACY_REGISTRY_SCHEMA = "totalsegmentator_wrapper_mac.setup_weights_registry.v1"
OFFICIAL_ARCHIVE_INTEGRITY_SOURCE = "official-archive-sha256"
LEGACY_MIGRATION_INTEGRITY_SOURCE = "legacy-deep-validation"
PARTIAL_SCHEMA = "totalsegmentator_wrapper_mac.setup_weight_partial.v1"
TASK_RECEIPT_SCHEMA = "totalsegmentator_wrapper_mac.setup_weight_receipt.v1"
TASK_RECEIPT_SOURCE = "official-release-asset"
RECOVERY_MARKER_SCHEMA = "totalsegmentator_wrapper_mac.setup_weight_recovery_marker.v1"
SETUP_LOCK_FILENAME = ".totalsegmentator-wrapper-weights-setup.lock"
PINNED_TOTALSEGMENTATOR_VERSION = "2.14.0"
SETUP_TASK_IDS = (113, 115, 297)
CHECKSUM_POLICY = (
    "Publisher-provided GitHub release digest where available; otherwise a locally "
    "observed SHA-256 value carried by this application for the pinned official "
    "GitHub release URL. Locally observed values are not publisher-provided digests. "
    "For assets without a publisher digest, observation date and source evidence are "
    "not preserved; revalidation by an approved official-asset download is required "
    "before release."
)
PUBLISHER_DIGEST_SOURCE = "github-release-digest"
LOCAL_OBSERVED_DIGEST_SOURCE = "locally-observed-official-asset"
REVALIDATED_DIGEST_SOURCE = "approved-official-asset-revalidation"
LOCAL_OBSERVATION_EVIDENCE = "not-preserved-unverified"
LOCAL_REVALIDATION_TASK_IDS = frozenset((115, 297))
REVALIDATION_EVIDENCE_SCHEMA = (
    "totalsegmentator_wrapper_mac.official_asset_revalidation.v1"
)
REVALIDATION_TRANSPORT = "https-pinned-official-release-asset"
REVALIDATION_CHECKS = (
    "complete-size",
    "sha256",
    "zip-crc",
    "expected-model-structure",
)
DEFAULT_CHUNK_SIZE = 1024 * 1024
# urllib applies this to connect and each blocking socket read, so an active
# long download may run for much longer while a silent connection is retried
# through the preserved .part file after two minutes.
DEFAULT_DOWNLOAD_INACTIVITY_TIMEOUT_SEC = 120
_TQDM_DOWNLOAD_RE = re.compile(
    r"Downloading:\s*(?P<percent>\d{1,3})%.*?\|\s*"
    r"(?P<completed>[\d.]+)(?P<completed_unit>[kMGT]?)\s*/\s*"
    r"(?P<total>[\d.]+)(?P<total_unit>[kMGT]?)\s*"
    r"\[(?P<elapsed>[\d:]+)<(?P<eta>[\d:]+),\s*"
    r"(?P<rate>[\d.]+)(?P<rate_unit>[kMGT]?)B/s\]"
)
_UNIT_FACTORS = {"": 1, "k": 1_000, "M": 1_000_000, "G": 1_000_000_000, "T": 1_000_000_000_000}
_STAGING_ARTIFACT_RE = re.compile(
    r"\.totalseg-staging-(?P<task_id>113|115|297)-(?P<nonce>[0-9a-f]{32})\Z"
)
_STAGING_MARKER_RE = re.compile(
    r"\.totalseg-staging-owner-(?P<task_id>113|115|297)-(?P<nonce>[0-9a-f]{32})\.json\Z"
)
_BACKUP_MARKER_RE = re.compile(
    r"\.totalseg-backup-owner-(?P<task_id>113|115|297)-(?P<nonce>[0-9a-f]{32})\.json\Z"
)
_REGISTRY_TEMP_RE = re.compile(
    rf"\.{re.escape(REGISTRY_FILENAME)}\.tmp-(?P<nonce>[0-9a-f]{{32}})\Z"
)


class SetupWeightsError(RuntimeError):
    reason = "weights_download_failed"


class SetupWeightsBusyError(SetupWeightsError):
    reason = "weights_setup_busy"


class SetupWeightsManifestError(ValueError, SetupWeightsError):
    reason = "weights_manifest_incompatible"


class SetupWeightsIntegrityError(ValueError, SetupWeightsError):
    reason = "weights_integrity_failed"


_OPEN_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_OPEN_FILE_FLAGS = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
_DISK_METADATA_RESERVE_BYTES = 4 * 1024 * 1024
_MACOS_TOP_LEVEL_SYSTEM_ALIASES = {
    "var": "private/var",
    "tmp": "private/tmp",
}


@dataclass
class _RootDirectory:
    """A root directory pinned by an open descriptor.

    Path names are deliberately retained only for diagnostics and progress
    payloads.  All mutable children are opened relative to ``fd`` so a
    symlink/rename after root validation cannot redirect a write outside the
    managed root.
    """

    path: Path
    fd: int
    identity: tuple[int, int]

    def close(self) -> None:
        os.close(self.fd)


def _absolute_path_without_resolving(path: Path) -> Path:
    """Make a lexical absolute path without following a leaf symlink."""

    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _path_components(path: Path) -> tuple[str, ...]:
    if not path.is_absolute():
        raise SetupWeightsIntegrityError("TotalSegmentator root path must be absolute")
    parts = tuple(component for component in path.parts if component not in {path.anchor, ""})
    if any(component in {".", ".."} or "\x00" in component for component in parts):
        raise SetupWeightsIntegrityError("TotalSegmentator root path is invalid")
    return parts


def _root_components_with_verified_macos_alias(
    requested: Path,
) -> tuple[Path, tuple[str, ...]]:
    """Return lexical components, allowing only known macOS root aliases.

    ``/var`` and ``/tmp`` are root-owned symbolic links on macOS.  A generic
    ``realpath`` would also follow user-controlled ancestors and introduces a
    lstat-to-resolution race before a directory descriptor is pinned.  This
    narrowly verifies an exact top-level system alias, then substitutes its
    known physical spelling; every other component is opened with O_NOFOLLOW.
    """

    components = _path_components(requested)
    if not components or components[0] not in _MACOS_TOP_LEVEL_SYSTEM_ALIASES:
        return requested, components

    alias = components[0]
    expected_target = _MACOS_TOP_LEVEL_SYSTEM_ALIASES[alias]
    try:
        root_fd = os.open(requested.anchor, _OPEN_DIRECTORY_FLAGS)
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            "TotalSegmentator filesystem root cannot be opened safely"
        ) from exc
    try:
        try:
            before = os.stat(alias, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            # Non-macOS hosts can legitimately provide a real /var or /tmp
            # later in the normal descriptor traversal.
            return requested, components
        if not stat.S_ISLNK(before.st_mode):
            return requested, components
        if before.st_uid != 0:
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator top-level /{alias} alias is not system-owned"
            )
        try:
            target = os.readlink(alias, dir_fd=root_fd)
            after = os.stat(alias, dir_fd=root_fd, follow_symlinks=False)
        except OSError as exc:
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator top-level /{alias} alias cannot be verified safely"
            ) from exc
        if _identity(before) != _identity(after) or target != expected_target:
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator top-level /{alias} alias is not the expected system alias"
            )
    finally:
        os.close(root_fd)

    canonical_components = tuple(expected_target.split("/")) + components[1:]
    return Path(requested.anchor).joinpath(*canonical_components), canonical_components


def _open_root_directory(
    path: Path,
    *,
    create: bool,
    label: str,
) -> _RootDirectory:
    """Open/create a root through no-follow directory descriptors.

    No whole-path resolution is performed.  The requested path is traversed
    component-by-component from a pinned filesystem-root descriptor, so an
    arbitrary leaf or ancestor symlink is rejected rather than followed.  The
    only exception is the explicitly verified macOS ``/var`` or ``/tmp``
    system alias handled by :func:`_root_components_with_verified_macos_alias`.
    """

    requested = _absolute_path_without_resolving(path)
    absolute, components = _root_components_with_verified_macos_alias(requested)
    try:
        current_fd = os.open(absolute.anchor, _OPEN_DIRECTORY_FLAGS)
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be opened safely"
        ) from exc
    try:
        for component in components:
            try:
                next_fd = os.open(component, _OPEN_DIRECTORY_FLAGS, dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise SetupWeightsIntegrityError(
                        f"TotalSegmentator {label} is missing"
                    ) from None
                try:
                    os.mkdir(component, 0o700, dir_fd=current_fd)
                except FileExistsError:
                    # A racing writer created the entry.  The no-follow open
                    # below decides whether it is a safe directory.
                    pass
                try:
                    next_fd = os.open(component, _OPEN_DIRECTORY_FLAGS, dir_fd=current_fd)
                except OSError as exc:
                    raise SetupWeightsIntegrityError(
                        f"TotalSegmentator {label} cannot be opened safely"
                    ) from exc
            except OSError as exc:
                raise SetupWeightsIntegrityError(
                    f"TotalSegmentator {label} cannot be opened safely"
                ) from exc
            os.close(current_fd)
            current_fd = next_fd
        status = os.fstat(current_fd)
        if not _is_private_directory_status(status):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} must be a private directory"
            )
        return _RootDirectory(
            path=absolute,
            fd=current_fd,
            identity=(status.st_dev, status.st_ino),
        )
    except BaseException:
        os.close(current_fd)
        raise


@contextmanager
def _opened_roots(
    cache_root: Path,
    *,
    weights_root: Path | None,
    label: str,
) -> Iterator[tuple[_RootDirectory, _RootDirectory]]:
    """Open distinct mutable roots and deduplicate by filesystem identity."""

    candidates = [("cache", cache_root)]
    if weights_root is not None:
        candidates.append(("weights", weights_root))
    opened: dict[tuple[int, int], _RootDirectory] = {}
    selected: dict[str, _RootDirectory] = {}
    try:
        for role, candidate in candidates:
            root = _open_root_directory(candidate, create=True, label=f"{label} {role} root")
            existing = opened.get(root.identity)
            if existing is not None:
                root.close()
                selected[role] = existing
            else:
                opened[root.identity] = root
                selected[role] = root
        cache = selected["cache"]
        weights = selected.get("weights", cache)
        yield cache, weights
    finally:
        for root in opened.values():
            try:
                root.close()
            except OSError:
                pass


def _private_regular_status(status: os.stat_result) -> bool:
    return (
        stat.S_ISREG(status.st_mode)
        and status.st_uid == os.geteuid()
        and status.st_nlink == 1
        and not status.st_mode & 0o022
    )


def _entry_lstat(root: _RootDirectory, name: str) -> os.stat_result | None:
    if not _safe_single_component(name):
        raise SetupWeightsIntegrityError("TotalSegmentator managed entry name is invalid")
    try:
        return os.stat(name, dir_fd=root.fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            "TotalSegmentator managed entry cannot be inspected safely"
        ) from exc


def _identity(status: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        status.st_dev,
        status.st_ino,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _open_private_regular_at(
    root: _RootDirectory,
    name: str,
    *,
    flags: int,
    mode: int = 0o600,
    expected: os.stat_result | None = None,
    create_exclusive: bool = False,
    label: str,
) -> tuple[int, os.stat_result]:
    """Open a regular child without following a symlink, then verify its inode."""

    if not _safe_single_component(name):
        raise SetupWeightsIntegrityError(f"TotalSegmentator {label} name is invalid")
    open_flags = flags | _OPEN_FILE_FLAGS
    if create_exclusive:
        open_flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(name, open_flags, mode, dir_fd=root.fd)
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be opened safely"
        ) from exc
    try:
        status = os.fstat(descriptor)
        if not _private_regular_status(status):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} must be a private regular file"
            )
        if expected is not None and _identity(status) != _identity(expected):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} changed while it was being opened"
            )
        return descriptor, status
    except BaseException:
        os.close(descriptor)
        raise


def _open_private_regular_fd(
    parent_fd: int,
    name: str,
    *,
    flags: int,
    mode: int = 0o600,
    expected: os.stat_result | None = None,
    create_exclusive: bool = False,
    label: str,
) -> tuple[int, os.stat_result]:
    """Descriptor-relative equivalent of :func:`_open_private_regular_at`.

    Extraction and required-file validation work beneath a pinned directory,
    rather than directly beneath a managed root.  Keeping this small primitive
    separate makes every leaf open use the same no-follow, ownership, mode,
    link-count, and identity checks.
    """

    if not _safe_single_component(name):
        raise SetupWeightsIntegrityError(f"TotalSegmentator {label} name is invalid")
    open_flags = flags | _OPEN_FILE_FLAGS
    if create_exclusive:
        open_flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(name, open_flags, mode, dir_fd=parent_fd)
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be opened safely"
        ) from exc
    try:
        status = os.fstat(descriptor)
        if not _private_regular_status(status):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} must be a private regular file"
            )
        if expected is not None and _identity(status) != _identity(expected):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} changed while it was being opened"
            )
        return descriptor, status
    except BaseException:
        os.close(descriptor)
        raise


def _entry_lstat_fd(parent_fd: int, name: str, *, label: str) -> os.stat_result | None:
    if not _safe_single_component(name):
        raise SetupWeightsIntegrityError(f"TotalSegmentator {label} name is invalid")
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be inspected safely"
        ) from exc


def _current_entry_matches_fd(
    parent_fd: int,
    name: str,
    expected: os.stat_result,
    *,
    label: str,
) -> bool:
    current = _entry_lstat_fd(parent_fd, name, label=label)
    return current is not None and _identity(current) == _identity(expected)


def _private_directory_status(status: os.stat_result) -> bool:
    """Return whether a directory is safe to traverse as an app-owned tree."""

    return _is_private_directory_status(status)


def _open_private_directory_at(
    parent_fd: int,
    name: str,
    *,
    create: bool,
    label: str,
) -> tuple[int, os.stat_result]:
    if not _safe_single_component(name):
        raise SetupWeightsIntegrityError(f"TotalSegmentator {label} name is invalid")
    try:
        descriptor = os.open(name, _OPEN_DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise SetupWeightsIntegrityError(f"TotalSegmentator {label} is missing") from None
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        descriptor = os.open(name, _OPEN_DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be opened safely"
        ) from exc
    try:
        status = os.fstat(descriptor)
        if not _private_directory_status(status):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} must be a private directory"
            )
        # Explicit modes make setup independent from an unusual inherited umask.
        os.fchmod(descriptor, 0o700)
        return descriptor, status
    except BaseException:
        os.close(descriptor)
        raise


def _require_private_regular_or_missing_at(
    root: _RootDirectory,
    name: str,
    *,
    label: str,
) -> os.stat_result | None:
    status = _entry_lstat(root, name)
    if status is not None and not _private_regular_status(status):
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} must be a private regular file"
        )
    return status


def _read_private_file_at(
    root: _RootDirectory,
    name: str,
    *,
    label: str,
    expected: os.stat_result | None = None,
) -> tuple[bytes, os.stat_result]:
    before = expected if expected is not None else _require_private_regular_or_missing_at(
        root, name, label=label
    )
    if before is None:
        raise FileNotFoundError(name)
    descriptor, opened = _open_private_regular_at(
        root,
        name,
        flags=os.O_RDONLY,
        expected=before,
        label=label,
    )
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, DEFAULT_CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if _identity(after) != _identity(opened):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} changed while it was being read"
            )
        # A descriptor pins the data even if the path is renamed.  Re-check the
        # directory entry before accepting it as a current receipt/sidecar.
        current = _entry_lstat(root, name)
        if current is None or _identity(current) != _identity(opened):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} changed while it was being read"
            )
        return b"".join(chunks), opened
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "short write")
        view = view[written:]


def _write_json_atomic_at(
    root: _RootDirectory,
    name: str,
    payload: dict[str, Any],
    *,
    label: str,
) -> None:
    """Atomically replace one regular root child without path-following writes."""

    if not _safe_single_component(name):
        raise SetupWeightsIntegrityError(f"TotalSegmentator {label} name is invalid")
    destination_before = _entry_lstat(root, name)
    if destination_before is not None and not _private_regular_status(destination_before):
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} must be a private regular file"
        )
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    temporary: str | None = None
    temporary_status: os.stat_result | None = None
    descriptor: int | None = None
    try:
        for _ in range(4):
            candidate = f".{name}.tmp-{uuid4().hex}"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _OPEN_FILE_FLAGS,
                    0o600,
                    dir_fd=root.fd,
                )
            except FileExistsError:
                continue
            except OSError as exc:
                raise SetupWeightsIntegrityError(
                    f"TotalSegmentator {label} temporary file could not be created safely"
                ) from exc
            temporary_status = os.fstat(descriptor)
            if not _private_regular_status(temporary_status):
                os.close(descriptor)
                descriptor = None
                raise SetupWeightsIntegrityError(
                    f"TotalSegmentator {label} temporary file must be a private regular file"
                )
            temporary = candidate
            break
        if descriptor is None or temporary is None:
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} temporary file could not be created safely"
            )
        os.fchmod(descriptor, 0o600)
        temporary_status = os.fstat(descriptor)
        _write_all(descriptor, data)
        os.fsync(descriptor)
        current = _entry_lstat(root, temporary)
        if current is None:
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} temporary file disappeared before publish"
            )
        opened = os.fstat(descriptor)
        temporary_status = opened
        if _identity(current) != _identity(opened):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} temporary file changed before publish"
            )
        destination_now = _entry_lstat(root, name)
        if (
            (destination_before is None and destination_now is not None)
            or (
                destination_before is not None
                and (
                    destination_now is None
                    or _identity(destination_before) != _identity(destination_now)
                )
            )
        ):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} changed before publish"
            )
        os.close(descriptor)
        descriptor = None
        # renameat replaces the root entry itself; it never follows a symlink
        # used as the destination.
        os.replace(temporary, name, src_dir_fd=root.fd, dst_dir_fd=root.fd)
        # Do not run the failure cleanup below after a successful rename: a
        # same-user process could otherwise create this random-looking name in
        # the tiny post-rename window and have its new in-root entry removed.
        temporary = None
        _fsync_directory(root.fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None and temporary_status is not None:
            # Only remove the exact temporary inode we created.  unlinkat does
            # not follow symlinks, and refusing a swapped entry avoids deleting
            # an unrelated new file in the post-error cleanup window.
            try:
                current = _entry_lstat(root, temporary)
                if current is not None and _identity(current) == _identity(temporary_status):
                    os.unlink(temporary, dir_fd=root.fd)
            except (FileNotFoundError, SetupWeightsIntegrityError):
                pass


def _fsync_directory(descriptor: int) -> None:
    """Best-effort directory durability; APFS may reject directory fsync."""

    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EBADF}:
            raise


def _unlink_private_entry_at(
    root: _RootDirectory,
    name: str,
    *,
    label: str,
    expected: os.stat_result | None = None,
    fail_on_nonregular: bool = True,
) -> None:
    status = _entry_lstat(root, name)
    if status is None:
        return
    if not _private_regular_status(status):
        if fail_on_nonregular:
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} must be a private regular file"
            )
        return
    expected_status = expected if expected is not None else status
    if _identity(status) != _identity(expected_status):
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} changed before cleanup"
        )
    current = _entry_lstat(root, name)
    if current is None:
        return
    if _identity(current) != _identity(expected_status):
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} changed before cleanup"
        )
    # unlinkat does not follow a symlink. The identity re-check also avoids
    # removing an unrelated in-root replacement in the lstat/unlink window.
    os.unlink(name, dir_fd=root.fd)


def _discard_download_files_at(
    cache_root: _RootDirectory,
    partial_name: str,
    sidecar_name: str,
    *,
    fail_on_nonregular: bool = True,
) -> None:
    _unlink_private_entry_at(
        cache_root,
        partial_name,
        label="partial archive",
        fail_on_nonregular=fail_on_nonregular,
    )
    _unlink_private_entry_at(
        cache_root,
        sidecar_name,
        label="partial metadata",
        fail_on_nonregular=fail_on_nonregular,
    )


@contextmanager
def _opened_relative_directory(
    parent_fd: int,
    parts: tuple[str, ...],
    *,
    create: bool,
    label: str,
) -> Iterator[int]:
    """Open a relative directory chain with no-follow checks at every step."""

    current_fd = os.dup(parent_fd)
    try:
        for part in parts:
            next_fd, _ = _open_private_directory_at(
                current_fd,
                part,
                create=create,
                label=label,
            )
            os.close(current_fd)
            current_fd = next_fd
        yield current_fd
    finally:
        os.close(current_fd)


@contextmanager
def exclusive_setup_lock(
    cache_root: Path,
    *,
    weights_root: Path | None = None,
) -> Iterator[tuple[_RootDirectory, _RootDirectory]]:
    """Lock every mutable root in canonical order without waiting.

    A partial archive belongs to ``cache_root`` while staging, backups, receipts,
    and the registry belong to ``weights_root``.  Taking a lock in each distinct
    root prevents two callers from racing if they configure different cache
    locations for the same installed models (or vice versa).
    """

    with _opened_roots(
        cache_root,
        weights_root=weights_root,
        label="weight setup lock",
    ) as (cache, weights):
        # ``Path.resolve`` keeps user-provided case spelling on the default
        # case-insensitive APFS volume.  Descriptor identity is therefore the
        # only reliable dedupe key.  Ordering distinct pinned roots by
        # (device, inode) is stable for the duration of this lock lease.
        roots = sorted(
            {root.identity: root for root in (cache, weights)}.values(),
            key=lambda root: root.identity,
        )
        handles: list[Any] = []
        try:
            for root in roots:
                handles.append(_acquire_private_setup_lock(root))
            yield cache, weights
        finally:
            for handle in reversed(handles):
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()


def _acquire_private_setup_lock(root: _RootDirectory) -> Any:
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(SETUP_LOCK_FILENAME, flags, 0o600, dir_fd=root.fd)
    try:
        file_status = os.fstat(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    if (
        not stat.S_ISREG(file_status.st_mode)
        or file_status.st_uid != os.geteuid()
        or file_status.st_nlink != 1
        or file_status.st_mode & 0o022
    ):
        os.close(descriptor)
        raise SetupWeightsError("TotalSegmentator weight setup lock is not a private regular file")
    try:
        handle = os.fdopen(descriptor, "a+b")
    except BaseException:
        os.close(descriptor)
        raise
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise SetupWeightsBusyError(
            "TotalSegmentator weight setup is already running."
        ) from exc
    except BaseException:
        handle.close()
        raise
    return handle


@dataclass(frozen=True)
class WeightAsset:
    task_id: int
    totalsegmentator_version: str
    release_tag: str
    filename: str
    url: str
    size_bytes: int
    sha256: str
    sha256_source: str
    dataset_dir: str
    required_files: tuple[str, ...]

    def sidecar_payload(self) -> dict[str, Any]:
        return {
            "schema": PARTIAL_SCHEMA,
            "task_id": self.task_id,
            "totalsegmentator_version": self.totalsegmentator_version,
            "release_tag": self.release_tag,
            "filename": self.filename,
            "url": self.url,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


def load_setup_weight_manifest(path: Path | None = None) -> tuple[WeightAsset, ...]:
    try:
        manifest_bytes = _manifest_bytes(path)
        payload = json.loads(manifest_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            raise SetupWeightsManifestError("TotalSegmentator setup weights manifest must be an object")
        if payload.get("schema") != "totalsegmentator_wrapper_mac.setup_weights_manifest.v1":
            raise SetupWeightsManifestError("unsupported TotalSegmentator setup weights manifest schema")
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            raise SetupWeightsManifestError("TotalSegmentator setup weights manifest assets must be a list")
        _validate_checksum_provenance(payload, raw_assets)
        assets = tuple(
            WeightAsset(
                task_id=int(item["task_id"]),
                totalsegmentator_version=str(payload["totalsegmentator_version"]),
                release_tag=str(item["release_tag"]),
                filename=str(item["filename"]),
                url=str(item["url"]),
                size_bytes=int(item["size_bytes"]),
                sha256=str(item["sha256"]),
                sha256_source=str(item["sha256_source"]),
                dataset_dir=str(item["dataset_dir"]),
                required_files=tuple(str(value) for value in item["required_files"]),
            )
            for item in raw_assets
        )
        _validate_manifest(assets)
        return assets
    except SetupWeightsManifestError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        raise SetupWeightsManifestError(
            "invalid TotalSegmentator setup weights manifest"
        ) from exc


def setup_weight_manifest_sha256(path: Path | None = None) -> str:
    return hashlib.sha256(_manifest_bytes(path)).hexdigest()


def _manifest_bytes(path: Path | None) -> bytes:
    if path is None:
        return resources.files("totalsegmentator_wrapper_mac").joinpath(MANIFEST_RESOURCE).read_bytes()
    return path.read_bytes()


def _validate_checksum_provenance(
    payload: dict[str, Any],
    raw_assets: list[Any],
) -> None:
    if payload.get("checksum_policy") != CHECKSUM_POLICY:
        raise SetupWeightsManifestError(
            "TotalSegmentator setup weights checksum provenance policy is invalid"
        )

    assets_by_task: dict[int, dict[str, Any]] = {}
    for item in raw_assets:
        if not isinstance(item, dict) or type(item.get("task_id")) is not int:
            raise SetupWeightsManifestError(
                "TotalSegmentator setup weights checksum provenance entry is invalid"
            )
        task_id = item["task_id"]
        if task_id in assets_by_task:
            raise SetupWeightsManifestError(
                "TotalSegmentator setup weights checksum provenance has duplicate task IDs"
            )
        assets_by_task[task_id] = item

    if set(assets_by_task) != set(SETUP_TASK_IDS):
        raise SetupWeightsManifestError(
            "TotalSegmentator setup weights checksum provenance task set is invalid"
        )

    for task_id, item in assets_by_task.items():
        expected_local = task_id in LOCAL_REVALIDATION_TASK_IDS
        if "sha256_observed_at" in item:
            raise SetupWeightsManifestError(
                f"TotalSegmentator setup weights checksum provenance is invalid for task {task_id}"
            )
        if expected_local:
            if item.get("publisher_digest_available") is not False:
                raise SetupWeightsManifestError(
                    f"TotalSegmentator setup weights checksum provenance is invalid for task {task_id}"
                )
            if item.get("revalidation_required_before_release") is True:
                if (
                    item.get("sha256_source") != LOCAL_OBSERVED_DIGEST_SOURCE
                    or item.get("local_observation_evidence")
                    != LOCAL_OBSERVATION_EVIDENCE
                    or "revalidation_evidence" in item
                ):
                    raise SetupWeightsManifestError(
                        f"TotalSegmentator setup weights checksum provenance is invalid for task {task_id}"
                    )
            elif item.get("revalidation_required_before_release") is False:
                if (
                    item.get("sha256_source") != REVALIDATED_DIGEST_SOURCE
                    or "local_observation_evidence" in item
                    or not _valid_revalidation_evidence(item)
                ):
                    raise SetupWeightsManifestError(
                        f"TotalSegmentator setup weights checksum provenance is invalid for task {task_id}"
                    )
            else:
                raise SetupWeightsManifestError(
                    f"TotalSegmentator setup weights checksum provenance is invalid for task {task_id}"
                )
        elif (
            item.get("sha256_source") != PUBLISHER_DIGEST_SOURCE
            or item.get("publisher_digest_available") is not True
            or "local_observation_evidence" in item
            or "revalidation_required_before_release" in item
            or "revalidation_evidence" in item
        ):
            raise SetupWeightsManifestError(
                f"TotalSegmentator setup weights checksum provenance is invalid for task {task_id}"
            )


def _valid_revalidation_evidence(item: dict[str, Any]) -> bool:
    evidence = item.get("revalidation_evidence")
    if not isinstance(evidence, dict) or set(evidence) != {
        "schema",
        "official_url",
        "release_tag",
        "filename",
        "size_bytes",
        "sha256",
        "verified_at_utc",
        "transport",
        "checks",
        "approval",
    }:
        return False
    verified_at = evidence.get("verified_at_utc")
    return (
        evidence.get("schema") == REVALIDATION_EVIDENCE_SCHEMA
        and evidence.get("official_url") == item.get("url")
        and evidence.get("release_tag") == item.get("release_tag")
        and evidence.get("filename") == item.get("filename")
        and evidence.get("size_bytes") == item.get("size_bytes")
        and evidence.get("sha256") == item.get("sha256")
        and _valid_revalidation_timestamp(verified_at)
        and evidence.get("transport") == REVALIDATION_TRANSPORT
        and evidence.get("checks") == list(REVALIDATION_CHECKS)
        and evidence.get("approval") == "approved-for-release"
    )


def _valid_revalidation_timestamp(value: object) -> bool:
    if (
        not isinstance(value, str)
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
            value,
        )
        is None
    ):
        return False
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == value


def prepare_weight_asset(
    asset: WeightAsset,
    *,
    weights_root: Path,
    cache_root: Path,
    progress_log: Path | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout_sec: int = DEFAULT_DOWNLOAD_INACTIVITY_TIMEOUT_SEC,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    progress_index: int = 1,
    progress_total: int = 1,
    manifest_sha256: str | None = None,
    allow_structure_only_skip: bool = False,
    _roots: tuple[_RootDirectory, _RootDirectory] | None = None,
) -> dict[str, Any]:
    """Prepare one asset.

    ``_roots`` is intentionally private: the multi-task entrypoint keeps its
    lock-pinned descriptors for the whole transaction, while direct callers
    still receive a safe short-lived descriptor lease.
    """

    if _roots is None:
        with _opened_roots(
            cache_root,
            weights_root=weights_root,
            label="weight setup",
        ) as roots:
            return _prepare_weight_asset_locked(
                asset,
                weights_root=roots[1],
                cache_root=roots[0],
                progress_log=progress_log,
                opener=opener,
                timeout_sec=timeout_sec,
                chunk_size=chunk_size,
                progress_index=progress_index,
                progress_total=progress_total,
                manifest_sha256=manifest_sha256,
                allow_structure_only_skip=allow_structure_only_skip,
            )
    return _prepare_weight_asset_locked(
        asset,
        weights_root=_roots[1],
        cache_root=_roots[0],
        progress_log=progress_log,
        opener=opener,
        timeout_sec=timeout_sec,
        chunk_size=chunk_size,
        progress_index=progress_index,
        progress_total=progress_total,
        manifest_sha256=manifest_sha256,
        allow_structure_only_skip=allow_structure_only_skip,
    )


def _prepare_weight_asset_locked(
    asset: WeightAsset,
    *,
    weights_root: _RootDirectory,
    cache_root: _RootDirectory,
    progress_log: Path | None,
    opener: Callable[..., Any],
    timeout_sec: int,
    chunk_size: int,
    progress_index: int,
    progress_total: int,
    manifest_sha256: str | None,
    allow_structure_only_skip: bool,
) -> dict[str, Any]:
    if isinstance(timeout_sec, bool) or not isinstance(timeout_sec, int) or timeout_sec <= 0:
        raise ValueError("TotalSegmentator download inactivity timeout must be a positive integer")
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("TotalSegmentator download chunk size must be a positive integer")
    manifest_sha256 = manifest_sha256 or setup_weight_manifest_sha256()
    if re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None:
        raise SetupWeightsManifestError(
            "TotalSegmentator setup weights manifest SHA-256 is invalid"
        )
    target = weights_root.path / asset.dataset_dir
    partial_name = f"{asset.filename}.part"
    sidecar_name = f"{asset.filename}.part.json"
    receipt_name = _task_receipt_filename(asset)
    _require_private_regular_or_missing_at(cache_root, partial_name, label="partial archive")
    _require_private_regular_or_missing_at(cache_root, sidecar_name, label="partial metadata")
    _require_private_regular_or_missing_at(weights_root, receipt_name, label="task receipt")
    if _task_receipt_is_valid_at(
        weights_root,
        receipt_name,
        asset,
        manifest_sha256=manifest_sha256,
    ):
        _discard_download_files_at(cache_root, partial_name, sidecar_name)
        _write_progress(
            progress_log, asset, status="complete", completed_bytes=asset.size_bytes,
            index=progress_index, task_total=progress_total,
        )
        return {
            "status": "verified_receipt_skipped",
            "task_id": asset.task_id,
            "target": str(target),
            "archive_verified": True,
            "integrity_source": OFFICIAL_ARCHIVE_INTEGRITY_SOURCE,
            "receipt": str(weights_root.path / receipt_name),
        }
    if allow_structure_only_skip and _has_expected_structure_at(
        weights_root,
        asset.dataset_dir,
        asset.required_files,
        deep=True,
    ):
        _discard_download_files_at(cache_root, partial_name, sidecar_name)
        _write_progress(
            progress_log, asset, status="complete", completed_bytes=asset.size_bytes,
            index=progress_index, task_total=progress_total,
        )
        return {"status": "skipped", "task_id": asset.task_id, "target": str(target)}

    if not _sidecar_matches_at(cache_root, sidecar_name, asset):
        _discard_download_files_at(cache_root, partial_name, sidecar_name)
    _write_json_atomic_at(
        cache_root,
        sidecar_name,
        asset.sidecar_payload(),
        label="partial metadata",
    )
    partial_status = _require_private_regular_or_missing_at(
        cache_root, partial_name, label="partial archive"
    )
    existing_bytes = partial_status.st_size if partial_status is not None else 0
    _require_free_space_fd(
        cache_root,
        max(0, asset.size_bytes - existing_bytes),
        metadata_entries=2,
    )

    try:
        _download_asset_at(
            asset,
            cache_root=cache_root,
            partial_name=partial_name,
            sidecar_name=sidecar_name,
            progress_log=progress_log,
            opener=opener,
            timeout_sec=timeout_sec,
            chunk_size=chunk_size,
            progress_index=progress_index,
            progress_total=progress_total,
        )
        archive_uncompressed_size = _verify_archive_at(cache_root, partial_name, asset)
        # The archive remains in ``cache_root`` for resume, while extraction is
        # staged beside the final dataset in ``weights_root``.  Checking the
        # latter is what guarantees the subsequent rename remains atomic even
        # when the download cache was placed on another volume.
        _require_free_space_fd(
            weights_root,
            archive_uncompressed_size,
            metadata_entries=len(asset.required_files) + 8,
        )
        # This returns only after deep JSON/checkpoint validation and the
        # staged dataset has atomically replaced the managed target.
        _publish_archive_at(cache_root, partial_name, asset, weights_root=weights_root)
        _write_task_receipt_at(
            weights_root,
            receipt_name,
            asset,
            manifest_sha256=manifest_sha256,
        )
    except (ValueError, zipfile.BadZipFile):
        _discard_download_files_at(
            cache_root,
            partial_name,
            sidecar_name,
            fail_on_nonregular=False,
        )
        _write_progress(
            progress_log, asset, status="failed", completed_bytes=0,
            index=progress_index, task_total=progress_total,
        )
        raise
    except Exception:
        _write_progress(
            progress_log,
            asset,
            status="failed",
            completed_bytes=_regular_file_size_at(cache_root, partial_name),
            index=progress_index, task_total=progress_total,
        )
        raise

    _discard_download_files_at(cache_root, partial_name, sidecar_name)
    _write_progress(
        progress_log, asset, status="complete", completed_bytes=asset.size_bytes,
        index=progress_index, task_total=progress_total,
    )
    return {
        "status": "installed",
        "task_id": asset.task_id,
        "target": str(target),
        "archive_verified": True,
        "integrity_source": OFFICIAL_ARCHIVE_INTEGRITY_SOURCE,
        "receipt": str(weights_root.path / receipt_name),
    }


def prepare_setup_weights(
    task_ids: tuple[int, ...],
    *,
    weights_root: Path,
    cache_root: Path,
    progress_log: Path | None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout_sec: int = DEFAULT_DOWNLOAD_INACTIVITY_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    assets = load_setup_weight_manifest()
    pinned_version = assets[0].totalsegmentator_version
    runtime_version = metadata.version("TotalSegmentator")
    if runtime_version != pinned_version:
        raise SetupWeightsManifestError(
            f"TotalSegmentator setup weights require version {pinned_version}, got {runtime_version}"
        )
    manifest = {asset.task_id: asset for asset in assets}
    unknown = [task_id for task_id in task_ids if task_id not in manifest]
    if unknown:
        raise SetupWeightsManifestError(f"unsupported setup TotalSegmentator task IDs: {unknown}")
    if len(task_ids) != len(SETUP_TASK_IDS) or set(task_ids) != set(SETUP_TASK_IDS):
        raise SetupWeightsManifestError(
            "TotalSegmentator setup requires exactly tasks 113, 115, and 297"
        )

    manifest_sha256 = setup_weight_manifest_sha256()
    with exclusive_setup_lock(cache_root, weights_root=weights_root) as (cache, weights):
        _recover_interrupted_publications_at(assets, weights_root=weights, cache_root=cache)
        registry_was_valid = False
        legacy_migration = False
        allow_unreceipted_structure_skip = False
        if _entry_lstat(weights, REGISTRY_FILENAME) is not None:
            try:
                _validate_setup_weights_registry_at(weights, manifest_path=None)
            except SetupWeightsIntegrityError:
                # A registry from a different manifest (or one whose recorded files
                # no longer validate) must not be rewritten around unverified old
                # model files. Keep both the invalid registry and each current target
                # until every verified replacement is published. If this run stops,
                # the stale registry continues to force refresh on the next attempt
                # instead of allowing structurally valid old files to be adopted.
                allow_unreceipted_structure_skip = False
            else:
                registry_was_valid = True
                allow_unreceipted_structure_skip = True
        elif _all_assets_deep_valid_at(assets, weights):
            # Public v0.4.0 installed the same pinned task set before a registry
            # existed. Preserve that one upgrade without pretending the original
            # release archives were observed here: deeply validate every JSON and
            # PyTorch ZIP/CRC first, then baseline the current required-file hashes.
            # A crash after the new receipt protocol completed every task but
            # before publishing the registry is not a legacy migration.  Keep
            # the official archive provenance already proven by those receipts.
            all_current_receipts = all(
                _task_receipt_is_valid_at(
                    weights,
                    _task_receipt_filename(asset),
                    asset,
                    manifest_sha256=manifest_sha256,
                )
                for asset in assets
            )
            legacy_migration = not all_current_receipts
            allow_unreceipted_structure_skip = True
        elif _any_asset_target_exists_at(assets, weights):
            # A partial legacy tree is not eligible for migration. Reacquire every
            # task so a single official-archive provenance applies to the new v2
            # registry instead of mixing verified and unverified origins.
            allow_unreceipted_structure_skip = False

        results: list[dict[str, Any]] = []
        for index, task_id in enumerate(task_ids, start=1):
            results.append(
                prepare_weight_asset(
                    manifest[task_id],
                    weights_root=weights.path,
                    cache_root=cache.path,
                    progress_log=progress_log,
                    progress_index=index,
                    progress_total=len(task_ids),
                    manifest_sha256=manifest_sha256,
                    allow_structure_only_skip=allow_unreceipted_structure_skip,
                    opener=opener,
                    timeout_sec=timeout_sec,
                    _roots=(cache, weights),
                )
            )
        if not registry_was_valid:
            if legacy_migration:
                integrity_source = LEGACY_MIGRATION_INTEGRITY_SOURCE
                archive_verified = False
            else:
                if not all(
                    item.get("status") in {"installed", "verified_receipt_skipped"}
                    and item.get("archive_verified") is True
                    and item.get("integrity_source") == OFFICIAL_ARCHIVE_INTEGRITY_SOURCE
                    for item in results
                ):
                    raise SetupWeightsIntegrityError(
                        "refusing to publish an official TotalSegmentator registry "
                        "without verified archive installation results"
                    )
                integrity_source = OFFICIAL_ARCHIVE_INTEGRITY_SOURCE
                archive_verified = True
            _write_ready_registry_at(
                weights,
                assets,
                manifest_sha256=manifest_sha256,
                integrity_source=integrity_source,
                archive_verified=archive_verified,
            )
        return results


def parse_tqdm_download_progress(text: str) -> dict[str, int] | None:
    match = _TQDM_DOWNLOAD_RE.search(text)
    if match is None:
        return None
    return {
        "percent": max(0, min(100, int(match.group("percent")))),
        "completed_bytes": _scaled_integer(match.group("completed"), match.group("completed_unit")),
        "total_bytes": _scaled_integer(match.group("total"), match.group("total_unit")),
        "eta_seconds": _duration_seconds(match.group("eta")),
        "rate_bps": _scaled_integer(match.group("rate"), match.group("rate_unit")),
    }


class DownloadProgressWriter:
    """Compatibility writer for parsing upstream-style tqdm output in tests and diagnostics."""

    def __init__(self, progress_log: Path | None, *, task_ids: tuple[int, ...]) -> None:
        self.progress_log = progress_log
        self.task_ids = task_ids
        self.current_task_id: int | None = None
        self.current_index: int | None = None
        self._last_signature: tuple[int, int, int] | None = None

    def start_task(self, task_id: int, *, index: int) -> None:
        self.current_task_id = task_id
        self.current_index = index
        self._last_signature = None
        self._write({"status": "starting", "task_id": task_id, "index": index})

    def consume(self, text: str) -> None:
        progress = parse_tqdm_download_progress(text)
        if progress is None or self.current_task_id is None or self.current_index is None:
            return
        signature = (progress["percent"], progress["completed_bytes"], progress["eta_seconds"])
        if signature == self._last_signature:
            return
        self._last_signature = signature
        self._write(
            {
                "status": "downloading",
                "task_id": self.current_task_id,
                "index": self.current_index,
                **progress,
            }
        )

    def complete_task(self, task_id: int, *, index: int) -> None:
        self._write({"status": "complete", "task_id": task_id, "index": index})

    def fail_task(self, task_id: int, *, index: int) -> None:
        self._write({"status": "failed", "task_id": task_id, "index": index})

    def _write(self, payload: dict[str, Any]) -> None:
        _append_progress(
            self.progress_log,
            {"source": "totalsegmentator", "task_total": len(self.task_ids), **payload},
        )


def _regular_file_size_at(root: _RootDirectory, name: str) -> int:
    status = _require_private_regular_or_missing_at(root, name, label="partial archive")
    return status.st_size if status is not None else 0


def _sidecar_matches_at(root: _RootDirectory, name: str, asset: WeightAsset) -> bool:
    try:
        payload_bytes, _ = _read_private_file_at(root, name, label="partial metadata")
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError, SetupWeightsIntegrityError):
        return False
    return payload == asset.sidecar_payload()


def _restart_partial_at(
    cache_root: _RootDirectory,
    partial_name: str,
    sidecar_name: str,
    asset: WeightAsset,
    *,
    progress_log: Path | None,
    progress_index: int,
    progress_total: int,
    reason: str,
) -> None:
    previous_size = _regular_file_size_at(cache_root, partial_name)
    _discard_download_files_at(cache_root, partial_name, sidecar_name)
    _write_json_atomic_at(
        cache_root,
        sidecar_name,
        asset.sidecar_payload(),
        label="partial metadata",
    )
    _write_progress(
        progress_log,
        asset,
        status="restart",
        completed_bytes=0,
        resumed=previous_size > 0,
        resume_from_bytes=previous_size,
        index=progress_index,
        task_total=progress_total,
        restart_reason=reason,
    )


def _open_partial_for_stream_at(
    cache_root: _RootDirectory,
    partial_name: str,
    *,
    expected: os.stat_result | None,
    append: bool,
) -> tuple[int, os.stat_result]:
    """Open one partial archive after pinning the exact inode to be updated.

    A path check followed by ``Path.open`` is vulnerable to a same-user symlink
    replacement.  Here the entry is lstat'd immediately before a no-follow
    descriptor open, then the descriptor's identity is checked against that
    lstat result.  New partials use O_EXCL, so a racing replacement is an
    explicit failure rather than an overwrite of an unknown file.
    """

    if expected is None:
        descriptor, status = _open_private_regular_at(
            cache_root,
            partial_name,
            flags=os.O_WRONLY,
            mode=0o600,
            create_exclusive=True,
            label="partial archive",
        )
    else:
        descriptor, status = _open_private_regular_at(
            cache_root,
            partial_name,
            flags=os.O_WRONLY | (os.O_APPEND if append else 0),
            expected=expected,
            label="partial archive",
        )
    try:
        os.fchmod(descriptor, 0o600)
        if not append:
            os.ftruncate(descriptor, 0)
        current = _entry_lstat(cache_root, partial_name)
        opened = os.fstat(descriptor)
        if current is None or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise SetupWeightsIntegrityError(
                "TotalSegmentator partial archive changed while it was being opened"
            )
        if not _private_regular_status(opened):
            raise SetupWeightsIntegrityError(
                "TotalSegmentator partial archive must be a private regular file"
            )
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def _close_written_partial_at(
    cache_root: _RootDirectory,
    partial_name: str,
    descriptor: int,
) -> None:
    """Durably close a partial only if its current root entry is the opened inode."""

    try:
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        if not _private_regular_status(opened):
            raise SetupWeightsIntegrityError(
                "TotalSegmentator partial archive became unsafe while downloading"
            )
        current = _entry_lstat(cache_root, partial_name)
        if current is None or _identity(current) != _identity(opened):
            raise SetupWeightsIntegrityError(
                "TotalSegmentator partial archive changed while downloading"
            )
    finally:
        os.close(descriptor)


def _download_asset_at(
    asset: WeightAsset,
    *,
    cache_root: _RootDirectory,
    partial_name: str,
    sidecar_name: str,
    progress_log: Path | None,
    opener: Callable[..., Any],
    timeout_sec: int,
    chunk_size: int,
    progress_index: int,
    progress_total: int,
) -> None:
    partial_status = _require_private_regular_or_missing_at(
        cache_root, partial_name, label="partial archive"
    )
    _require_private_regular_or_missing_at(cache_root, sidecar_name, label="partial metadata")
    if partial_status is not None and partial_status.st_size > asset.size_bytes:
        _restart_partial_at(
            cache_root,
            partial_name,
            sidecar_name,
            asset,
            progress_log=progress_log,
            progress_index=progress_index,
            progress_total=progress_total,
            reason="partial_larger_than_expected",
        )

    initial_resume_from = _regular_file_size_at(cache_root, partial_name)
    if initial_resume_from == asset.size_bytes:
        _write_progress(
            progress_log,
            asset,
            status="verifying",
            completed_bytes=initial_resume_from,
            resumed=initial_resume_from > 0,
            resume_from_bytes=initial_resume_from,
            index=progress_index,
            task_total=progress_total,
        )
        return

    full_restart_used = False
    while True:
        resume_from = _regular_file_size_at(cache_root, partial_name)
        if resume_from == asset.size_bytes:
            break
        request_headers = {"Accept-Encoding": "identity"}
        if resume_from:
            request_headers["Range"] = f"bytes={resume_from}-"
        request = urllib.request.Request(asset.url, headers=request_headers)
        try:
            response = opener(request, timeout=timeout_sec)
        except urllib.error.HTTPError as exc:
            if resume_from and exc.code == 416 and not full_restart_used:
                exc.close()
                _restart_partial_at(
                    cache_root,
                    partial_name,
                    sidecar_name,
                    asset,
                    progress_log=progress_log,
                    progress_index=progress_index,
                    progress_total=progress_total,
                    reason="range_not_satisfiable",
                )
                full_restart_used = True
                continue
            raise

        restart_reason: str | None = None
        with response:
            _validate_download_response_transport(
                response,
                label=f"TotalSegmentator task {asset.task_id}",
                requested_url=asset.url,
            )
            status = getattr(response, "status", None) or response.getcode()
            segment_end = asset.size_bytes - 1
            if resume_from:
                if status == 200:
                    restart_reason = "range_ignored"
                else:
                    parsed_range = _parse_content_range(response)
                    if status != 206 or parsed_range is None:
                        raise SetupWeightsIntegrityError(
                            f"invalid HTTP Range response for task {asset.task_id}"
                        )
                    start, segment_end, response_total = parsed_range
                    if (
                        start != resume_from
                        or response_total != asset.size_bytes
                        or segment_end < start
                        or segment_end >= asset.size_bytes
                    ):
                        raise SetupWeightsIntegrityError(
                            f"invalid Content-Range for task {asset.task_id}"
                        )
            elif status != 200:
                raise SetupWeightsIntegrityError(
                    f"unexpected HTTP status for task {asset.task_id}: {status}"
                )

            if restart_reason is None:
                expected_response_bytes = segment_end - resume_from + 1
                raw_content_length = response.headers.get("Content-Length") if response.headers else None
                content_length = _header_int(response, "Content-Length")
                if raw_content_length is not None and content_length is None:
                    raise SetupWeightsIntegrityError(
                        f"invalid Content-Length for task {asset.task_id}"
                    )
                if content_length is not None and content_length != expected_response_bytes:
                    raise SetupWeightsIntegrityError(
                        f"TotalSegmentator asset size header mismatch for task {asset.task_id}: "
                        f"expected {expected_response_bytes}, got {content_length}"
                    )

                # Capture the current partial immediately before opening it.  A
                # swap performed by the opener/response hook is rejected by the
                # no-follow descriptor open below instead of becoming a write to
                # the symlink target.
                expected_partial = _require_private_regular_or_missing_at(
                    cache_root,
                    partial_name,
                    label="partial archive",
                )
                actual_resume = expected_partial.st_size if expected_partial is not None else 0
                if actual_resume != resume_from:
                    raise SetupWeightsIntegrityError(
                        "TotalSegmentator partial archive changed before download write"
                    )
                output, _ = _open_partial_for_stream_at(
                    cache_root,
                    partial_name,
                    expected=expected_partial,
                    append=resume_from > 0,
                )
                downloaded = resume_from
                started = time.perf_counter()
                _write_progress(
                    progress_log,
                    asset,
                    status="downloading",
                    completed_bytes=downloaded,
                    resumed=resume_from > 0,
                    resume_from_bytes=resume_from,
                    index=progress_index,
                    task_total=progress_total,
                )
                try:
                    while True:
                        remaining_in_response = segment_end + 1 - downloaded
                        # Without Content-Length, read at most one byte beyond
                        # the declared total before reporting an oversized body.
                        read_size = min(
                            chunk_size,
                            max(1, remaining_in_response + (content_length is None)),
                        )
                        chunk = response.read(read_size)
                        if not chunk:
                            break
                        if downloaded + len(chunk) > segment_end + 1:
                            raise SetupWeightsIntegrityError(
                                f"TotalSegmentator asset response exceeded expected size for task {asset.task_id}"
                            )
                        _write_all(output, chunk)
                        downloaded += len(chunk)
                        elapsed = max(time.perf_counter() - started, 1e-6)
                        rate = (downloaded - resume_from) / elapsed
                        eta = max(0.0, (asset.size_bytes - downloaded) / rate) if rate > 0 else None
                        _write_progress(
                            progress_log,
                            asset,
                            status="downloading",
                            completed_bytes=downloaded,
                            rate_bps=rate,
                            eta_seconds=eta,
                            resumed=resume_from > 0,
                            resume_from_bytes=resume_from,
                            index=progress_index,
                            task_total=progress_total,
                        )
                finally:
                    _close_written_partial_at(cache_root, partial_name, output)
                received = downloaded - resume_from
                if received != expected_response_bytes:
                    raise ConnectionError(
                        f"TotalSegmentator asset download incomplete for task {asset.task_id}: "
                        f"expected response bytes {expected_response_bytes}, got {received}"
                    )

        if restart_reason is not None:
            if full_restart_used:
                raise SetupWeightsIntegrityError(
                    f"HTTP Range restart repeated for task {asset.task_id}"
                )
            _restart_partial_at(
                cache_root,
                partial_name,
                sidecar_name,
                asset,
                progress_log=progress_log,
                progress_index=progress_index,
                progress_total=progress_total,
                reason=restart_reason,
            )
            full_restart_used = True
            continue

    actual_size = _regular_file_size_at(cache_root, partial_name)
    if actual_size != asset.size_bytes:
        raise ConnectionError(
            f"TotalSegmentator asset download incomplete for task {asset.task_id}: "
            f"expected {asset.size_bytes}, got {actual_size}"
        )
    _write_progress(
        progress_log,
        asset,
        status="verifying",
        completed_bytes=asset.size_bytes,
        resumed=initial_resume_from > 0,
        resume_from_bytes=initial_resume_from,
        index=progress_index,
        task_total=progress_total,
    )


def _download_asset(
    asset: WeightAsset,
    *,
    partial: Path,
    sidecar: Path,
    progress_log: Path | None,
    opener: Callable[..., Any],
    timeout_sec: int,
    chunk_size: int,
    progress_index: int,
    progress_total: int,
) -> None:
    _require_regular_download_file_or_missing(partial, label="partial archive")
    _require_regular_download_file_or_missing(sidecar, label="partial metadata")
    if _path_exists(partial) and partial.stat().st_size > asset.size_bytes:
        _restart_partial(
            partial,
            sidecar,
            asset,
            progress_log=progress_log,
            progress_index=progress_index,
            progress_total=progress_total,
            reason="partial_larger_than_expected",
        )

    initial_resume_from = _regular_file_size(partial)
    if initial_resume_from == asset.size_bytes:
        _write_progress(
            progress_log,
            asset,
            status="verifying",
            completed_bytes=initial_resume_from,
            resumed=initial_resume_from > 0,
            resume_from_bytes=initial_resume_from,
            index=progress_index,
            task_total=progress_total,
        )
        return

    full_restart_used = False
    while True:
        resume_from = _regular_file_size(partial)
        if resume_from == asset.size_bytes:
            break
        request_headers = {"Accept-Encoding": "identity"}
        if resume_from:
            request_headers["Range"] = f"bytes={resume_from}-"
        request = urllib.request.Request(asset.url, headers=request_headers)
        try:
            response = opener(request, timeout=timeout_sec)
        except urllib.error.HTTPError as exc:
            if resume_from and exc.code == 416 and not full_restart_used:
                exc.close()
                _restart_partial(
                    partial,
                    sidecar,
                    asset,
                    progress_log=progress_log,
                    progress_index=progress_index,
                    progress_total=progress_total,
                    reason="range_not_satisfiable",
                )
                full_restart_used = True
                continue
            raise

        restart_reason: str | None = None
        with response:
            _validate_download_response_transport(
                response,
                label=f"TotalSegmentator task {asset.task_id}",
                requested_url=asset.url,
            )
            status = getattr(response, "status", None) or response.getcode()
            segment_end = asset.size_bytes - 1
            if resume_from:
                if status == 200:
                    restart_reason = "range_ignored"
                else:
                    parsed_range = _parse_content_range(response)
                    if status != 206 or parsed_range is None:
                        raise SetupWeightsIntegrityError(
                            f"invalid HTTP Range response for task {asset.task_id}"
                        )
                    start, segment_end, response_total = parsed_range
                    if (
                        start != resume_from
                        or response_total != asset.size_bytes
                        or segment_end < start
                        or segment_end >= asset.size_bytes
                    ):
                        raise SetupWeightsIntegrityError(
                            f"invalid Content-Range for task {asset.task_id}"
                        )
            elif status != 200:
                raise SetupWeightsIntegrityError(
                    f"unexpected HTTP status for task {asset.task_id}: {status}"
                )

            if restart_reason is None:
                expected_response_bytes = segment_end - resume_from + 1
                raw_content_length = response.headers.get("Content-Length") if response.headers else None
                content_length = _header_int(response, "Content-Length")
                if raw_content_length is not None and content_length is None:
                    raise SetupWeightsIntegrityError(
                        f"invalid Content-Length for task {asset.task_id}"
                    )
                if content_length is not None and content_length != expected_response_bytes:
                    raise SetupWeightsIntegrityError(
                        f"TotalSegmentator asset size header mismatch for task {asset.task_id}: "
                        f"expected {expected_response_bytes}, got {content_length}"
                    )
                downloaded = resume_from
                started = time.perf_counter()
                _write_progress(
                    progress_log,
                    asset,
                    status="downloading",
                    completed_bytes=downloaded,
                    resumed=resume_from > 0,
                    resume_from_bytes=resume_from,
                    index=progress_index,
                    task_total=progress_total,
                )
                with partial.open("ab" if resume_from else "wb") as output:
                    while True:
                        remaining_in_response = segment_end + 1 - downloaded
                        # Without Content-Length, read at most one byte beyond the declared total.
                        read_size = min(chunk_size, max(1, remaining_in_response + (content_length is None)))
                        chunk = response.read(read_size)
                        if not chunk:
                            break
                        if downloaded + len(chunk) > segment_end + 1:
                            raise SetupWeightsIntegrityError(
                                f"TotalSegmentator asset response exceeded expected size for task {asset.task_id}"
                            )
                        output.write(chunk)
                        output.flush()
                        downloaded += len(chunk)
                        elapsed = max(time.perf_counter() - started, 1e-6)
                        rate = (downloaded - resume_from) / elapsed
                        eta = max(0.0, (asset.size_bytes - downloaded) / rate) if rate > 0 else None
                        _write_progress(
                            progress_log,
                            asset,
                            status="downloading",
                            completed_bytes=downloaded,
                            rate_bps=rate,
                            eta_seconds=eta,
                            resumed=resume_from > 0,
                            resume_from_bytes=resume_from,
                            index=progress_index,
                            task_total=progress_total,
                        )
                received = downloaded - resume_from
                if received != expected_response_bytes:
                    raise ConnectionError(
                        f"TotalSegmentator asset download incomplete for task {asset.task_id}: "
                        f"expected response bytes {expected_response_bytes}, got {received}"
                    )

        if restart_reason is not None:
            if full_restart_used:
                raise SetupWeightsIntegrityError(
                    f"HTTP Range restart repeated for task {asset.task_id}"
                )
            _restart_partial(
                partial,
                sidecar,
                asset,
                progress_log=progress_log,
                progress_index=progress_index,
                progress_total=progress_total,
                reason=restart_reason,
            )
            full_restart_used = True
            continue

    if not _path_exists(partial) or partial.stat().st_size != asset.size_bytes:
        actual_size = _regular_file_size(partial)
        raise ConnectionError(
            f"TotalSegmentator asset download incomplete for task {asset.task_id}: "
            f"expected {asset.size_bytes}, got {actual_size}"
        )
    _write_progress(
        progress_log,
        asset,
        status="verifying",
        completed_bytes=asset.size_bytes,
        resumed=initial_resume_from > 0,
        resume_from_bytes=initial_resume_from,
        index=progress_index,
        task_total=progress_total,
    )


def _sha256_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, DEFAULT_CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _file_sha256_at(root: _RootDirectory, name: str, *, label: str) -> tuple[str, os.stat_result]:
    before = _require_private_regular_or_missing_at(root, name, label=label)
    if before is None:
        raise SetupWeightsIntegrityError(f"TotalSegmentator {label} is missing")
    descriptor, opened = _open_private_regular_at(
        root,
        name,
        flags=os.O_RDONLY,
        expected=before,
        label=label,
    )
    try:
        digest = _sha256_descriptor(descriptor)
        after = os.fstat(descriptor)
        current = _entry_lstat(root, name)
        if _identity(after) != _identity(opened) or current is None or _identity(current) != _identity(opened):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} changed while it was being hashed"
            )
        return digest, opened
    finally:
        os.close(descriptor)


@contextmanager
def _open_zip_from_regular_at(
    root: _RootDirectory,
    name: str,
    *,
    label: str,
) -> Iterator[tuple[zipfile.ZipFile, os.stat_result]]:
    """Open a ZIP from a no-follow descriptor and verify the entry afterwards."""

    before = _require_private_regular_or_missing_at(root, name, label=label)
    if before is None:
        raise SetupWeightsIntegrityError(f"TotalSegmentator {label} is missing")
    descriptor, opened = _open_private_regular_at(
        root,
        name,
        flags=os.O_RDONLY,
        expected=before,
        label=label,
    )
    stream = os.fdopen(descriptor, "rb", closefd=True)
    archive: zipfile.ZipFile | None = None
    try:
        archive = zipfile.ZipFile(stream)
        yield archive, opened
    finally:
        if archive is not None:
            archive.close()
        # ZipFile does not own a file object supplied by the caller.
        if not stream.closed:
            after = os.fstat(stream.fileno())
            stream.close()
            current = _entry_lstat(root, name)
            if _identity(after) != _identity(opened) or current is None or _identity(current) != _identity(opened):
                raise SetupWeightsIntegrityError(
                    f"TotalSegmentator {label} changed while it was being read"
                )


def _verify_archive_at(
    cache_root: _RootDirectory,
    partial_name: str,
    asset: WeightAsset,
) -> int:
    actual_sha256, _ = _file_sha256_at(
        cache_root,
        partial_name,
        label="partial archive",
    )
    if actual_sha256 != asset.sha256:
        raise ValueError(
            f"TotalSegmentator asset SHA-256 mismatch for task {asset.task_id}: "
            f"expected {asset.sha256}, got {actual_sha256}"
        )
    with _open_zip_from_regular_at(
        cache_root,
        partial_name,
        label="partial archive",
    ) as (archive, _):
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(f"TotalSegmentator ZIP CRC failed for task {asset.task_id}: {bad_member}")
        names: set[str] = set()
        uncompressed_size = 0
        for info in archive.infolist():
            _validate_zip_member(info)
            normalized = info.filename.rstrip("/")
            if normalized in names:
                raise ValueError(
                    f"TotalSegmentator ZIP contains duplicate member for task {asset.task_id}: {info.filename}"
                )
            names.add(normalized)
            if not info.is_dir():
                uncompressed_size += info.file_size
        expected = {f"{asset.dataset_dir}/{relative}" for relative in asset.required_files}
        if not expected.issubset(names):
            missing = sorted(expected - names)
            raise ValueError(
                f"TotalSegmentator ZIP missing expected model structure for task {asset.task_id}: {missing}"
            )
    return uncompressed_size


def _create_private_directory_at(
    parent_fd: int,
    name: str,
    *,
    label: str,
) -> tuple[int, os.stat_result]:
    if not _safe_single_component(name):
        raise SetupWeightsIntegrityError(f"TotalSegmentator {label} name is invalid")
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} already exists"
        ) from exc
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be created safely"
        ) from exc
    try:
        descriptor = os.open(name, _OPEN_DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be opened safely"
        ) from exc
    try:
        status = os.fstat(descriptor)
        current = _entry_lstat_fd(parent_fd, name, label=label)
        if (
            not _private_directory_status(status)
            or current is None
            or _identity(current) != _identity(status)
        ):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} changed while it was being created"
            )
        os.fchmod(descriptor, 0o700)
        return descriptor, os.fstat(descriptor)
    except BaseException:
        os.close(descriptor)
        raise


def _validate_current_regular_at(
    parent_fd: int,
    name: str,
    descriptor: int,
    *,
    label: str,
) -> os.stat_result:
    opened = os.fstat(descriptor)
    current = _entry_lstat_fd(parent_fd, name, label=label)
    if (
        not _private_regular_status(opened)
        or current is None
        or _identity(current) != _identity(opened)
    ):
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} changed while it was being written"
        )
    return opened


def _extract_member_at(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    staging_fd: int,
) -> None:
    """Extract one validated member using only fd-relative no-follow opens."""

    _validate_zip_member(info)
    relative = PurePosixPath(info.filename)
    parts = tuple(relative.parts)
    if info.is_dir():
        with _opened_relative_directory(
            staging_fd,
            parts,
            create=True,
            label="weights staging directory",
        ):
            return

    parent_parts = parts[:-1]
    leaf = parts[-1]
    with _opened_relative_directory(
        staging_fd,
        parent_parts,
        create=True,
        label="weights staging directory",
    ) as parent_fd:
        if _entry_lstat_fd(parent_fd, leaf, label="weights staging file") is not None:
            raise ValueError(f"duplicate or conflicting ZIP member: {info.filename!r}")
        descriptor, _ = _open_private_regular_fd(
            parent_fd,
            leaf,
            flags=os.O_WRONLY,
            mode=0o600,
            create_exclusive=True,
            label="weights staging file",
        )
        try:
            os.fchmod(descriptor, 0o600)
            written = 0
            with archive.open(info) as source:
                while True:
                    chunk = source.read(DEFAULT_CHUNK_SIZE)
                    if not chunk:
                        break
                    _write_all(descriptor, chunk)
                    written += len(chunk)
            if written != info.file_size:
                raise ValueError(f"ZIP member size mismatch: {info.filename!r}")
            os.fsync(descriptor)
            _validate_current_regular_at(
                parent_fd,
                leaf,
                descriptor,
                label="weights staging file",
            )
        finally:
            os.close(descriptor)


def _remove_private_tree_contents_fd(parent_fd: int, *, label: str) -> None:
    try:
        entries = sorted(os.listdir(parent_fd))
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be inspected safely"
        ) from exc
    for name in entries:
        if not _safe_single_component(name):
            raise SetupWeightsIntegrityError(f"TotalSegmentator {label} contains an invalid entry")
        status = _entry_lstat_fd(parent_fd, name, label=label)
        if status is None:
            continue
        if _private_directory_status(status):
            try:
                child_fd = os.open(name, _OPEN_DIRECTORY_FLAGS, dir_fd=parent_fd)
            except OSError as exc:
                raise SetupWeightsIntegrityError(
                    f"TotalSegmentator {label} directory cannot be opened safely"
                ) from exc
            try:
                opened = os.fstat(child_fd)
                if _identity(opened) != _identity(status):
                    raise SetupWeightsIntegrityError(
                        f"TotalSegmentator {label} directory changed while being removed"
                    )
                _remove_private_tree_contents_fd(child_fd, label=label)
                current = _entry_lstat_fd(parent_fd, name, label=label)
                after = os.fstat(child_fd)
                if current is None or _identity(current) != _identity(after):
                    raise SetupWeightsIntegrityError(
                        f"TotalSegmentator {label} directory changed before removal"
                    )
            finally:
                os.close(child_fd)
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except OSError as exc:
                raise SetupWeightsIntegrityError(
                    f"TotalSegmentator {label} directory cannot be removed safely"
                ) from exc
            continue
        if not _private_regular_status(status):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} contains an unsafe entry"
            )
        # Re-check the direct entry immediately before unlinking.  unlinkat
        # never follows a symlink; an unexpected replacement therefore cannot
        # delete outside the pinned root, and is reported instead of ignored.
        current = _entry_lstat_fd(parent_fd, name, label=label)
        if current is None or _identity(current) != _identity(status):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} file changed before removal"
            )
        try:
            os.unlink(name, dir_fd=parent_fd)
        except OSError as exc:
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} file cannot be removed safely"
            ) from exc


def _remove_owned_tree_at(root: _RootDirectory, name: str, *, label: str) -> None:
    before = _entry_lstat(root, name)
    if before is None:
        return
    if not _private_directory_status(before):
        raise SetupWeightsIntegrityError(f"TotalSegmentator {label} must be a private directory")
    try:
        descriptor = os.open(name, _OPEN_DIRECTORY_FLAGS, dir_fd=root.fd)
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be opened safely"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} changed while it was being opened"
            )
        _remove_private_tree_contents_fd(descriptor, label=label)
        current = _entry_lstat(root, name)
        after = os.fstat(descriptor)
        if current is None or _identity(current) != _identity(after):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} changed before removal"
            )
    finally:
        os.close(descriptor)
    try:
        os.rmdir(name, dir_fd=root.fd)
        _fsync_directory(root.fd)
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be removed safely"
        ) from exc


def _remove_owned_managed_entry_at(root: _RootDirectory, name: str, *, label: str) -> None:
    status = _entry_lstat(root, name)
    if status is None:
        return
    if _private_directory_status(status):
        _remove_owned_tree_at(root, name, label=label)
        return
    if _private_regular_status(status):
        _unlink_private_entry_at(root, name, label=label, expected=status)
        _fsync_directory(root.fd)
        return
    raise SetupWeightsIntegrityError(
        f"TotalSegmentator {label} must be a private regular file or directory"
    )


def _staging_marker_name(asset: WeightAsset, nonce: str) -> str:
    _require_transaction_nonce(nonce)
    return f".totalseg-staging-owner-{asset.task_id}-{nonce}.json"


def _backup_marker_name(asset: WeightAsset, nonce: str) -> str:
    _require_transaction_nonce(nonce)
    return f".totalseg-backup-owner-{asset.task_id}-{nonce}.json"


def _write_recovery_marker_at(
    root: _RootDirectory,
    name: str,
    *,
    kind: str,
    asset: WeightAsset,
    nonce: str,
    artifact_name: str,
) -> None:
    if _entry_lstat(root, name) is not None:
        raise SetupWeightsIntegrityError("TotalSegmentator recovery marker path already exists")
    _write_json_atomic_at(
        root,
        name,
        _recovery_marker_payload(
            kind=kind,
            asset=asset,
            nonce=nonce,
            artifact_name=artifact_name,
        ),
        label="recovery marker",
    )


def _recovery_marker_is_valid_at(
    root: _RootDirectory,
    name: str,
    *,
    kind: str,
    asset: WeightAsset,
    nonce: str,
    artifact_name: str,
) -> bool:
    try:
        raw, _ = _read_private_file_at(root, name, label="recovery marker")
        payload = json.loads(raw.decode("utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError, SetupWeightsIntegrityError):
        return False
    return payload == _recovery_marker_payload(
        kind=kind,
        asset=asset,
        nonce=nonce,
        artifact_name=artifact_name,
    )


def _remove_owned_staging_at(
    root: _RootDirectory,
    staging_name: str,
    marker_name: str,
    *,
    asset: WeightAsset,
    nonce: str,
) -> None:
    if not _recovery_marker_is_valid_at(
        root,
        marker_name,
        kind="staging",
        asset=asset,
        nonce=nonce,
        artifact_name=staging_name,
    ):
        raise SetupWeightsIntegrityError("TotalSegmentator staging recovery marker is invalid")
    _remove_owned_tree_at(root, staging_name, label="weights staging directory")
    _unlink_private_entry_at(root, marker_name, label="recovery marker")
    _fsync_directory(root.fd)


def _remove_owned_backup_at(
    root: _RootDirectory,
    backup_name: str,
    marker_name: str,
    *,
    asset: WeightAsset,
    nonce: str,
) -> None:
    if not _recovery_marker_is_valid_at(
        root,
        marker_name,
        kind="backup",
        asset=asset,
        nonce=nonce,
        artifact_name=backup_name,
    ):
        raise SetupWeightsIntegrityError("TotalSegmentator backup recovery marker is invalid")
    _remove_owned_managed_entry_at(root, backup_name, label="model backup")
    _unlink_private_entry_at(root, marker_name, label="recovery marker")
    _fsync_directory(root.fd)


def _restore_owned_backup_at(
    root: _RootDirectory,
    backup_name: str,
    marker_name: str,
    *,
    asset: WeightAsset,
    nonce: str,
) -> None:
    if not _recovery_marker_is_valid_at(
        root,
        marker_name,
        kind="backup",
        asset=asset,
        nonce=nonce,
        artifact_name=backup_name,
    ):
        raise SetupWeightsIntegrityError("TotalSegmentator backup recovery marker is invalid")
    backup = _entry_lstat(root, backup_name)
    if backup is None or not _private_directory_status(backup):
        raise SetupWeightsIntegrityError("TotalSegmentator model backup is missing or unsafe")
    _remove_owned_managed_entry_at(root, asset.dataset_dir, label="managed model target")
    current_backup = _entry_lstat(root, backup_name)
    current_target = _entry_lstat(root, asset.dataset_dir)
    if current_target is not None or current_backup is None or _identity(current_backup) != _identity(backup):
        raise SetupWeightsIntegrityError("TotalSegmentator model backup changed before restore")
    os.replace(
        backup_name,
        asset.dataset_dir,
        src_dir_fd=root.fd,
        dst_dir_fd=root.fd,
    )
    _fsync_directory(root.fd)
    _unlink_private_entry_at(root, marker_name, label="recovery marker")
    _fsync_directory(root.fd)


def _publish_archive_at(
    cache_root: _RootDirectory,
    partial_name: str,
    asset: WeightAsset,
    *,
    weights_root: _RootDirectory,
) -> None:
    """Extract beneath a pinned staging fd, then rename only inside weights_root."""

    nonce = uuid4().hex
    staging_name = _staging_artifact_name(asset, nonce)
    staging_marker_name = _staging_marker_name(asset, nonce)
    _write_recovery_marker_at(
        weights_root,
        staging_marker_name,
        kind="staging",
        asset=asset,
        nonce=nonce,
        artifact_name=staging_name,
    )
    staging_fd: int | None = None
    backup_name: str | None = None
    backup_marker_name: str | None = None
    replaced_existing = False
    try:
        staging_fd, _ = _create_private_directory_at(
            weights_root.fd,
            staging_name,
            label="weights staging directory",
        )
        with _open_zip_from_regular_at(
            cache_root,
            partial_name,
            label="partial archive",
        ) as (archive, _):
            for info in archive.infolist():
                _extract_member_at(archive, info, staging_fd)

        staging_root = _RootDirectory(
            path=weights_root.path / staging_name,
            fd=os.dup(staging_fd),
            identity=(os.fstat(staging_fd).st_dev, os.fstat(staging_fd).st_ino),
        )
        try:
            if not _has_expected_structure_at(
                staging_root,
                asset.dataset_dir,
                asset.required_files,
                deep=True,
            ):
                raise ValueError(
                    f"TotalSegmentator ZIP did not create expected model structure for task {asset.task_id}"
                )
        finally:
            staging_root.close()

        target = _entry_lstat(weights_root, asset.dataset_dir)
        if target is not None:
            if not (_private_directory_status(target) or _private_regular_status(target)):
                raise SetupWeightsIntegrityError(
                    "TotalSegmentator existing model target is unsafe"
                )
            backup_name = _backup_artifact_name(asset, nonce)
            backup_marker_name = _backup_marker_name(asset, nonce)
            _write_recovery_marker_at(
                weights_root,
                backup_marker_name,
                kind="backup",
                asset=asset,
                nonce=nonce,
                artifact_name=backup_name,
            )
            current_target = _entry_lstat(weights_root, asset.dataset_dir)
            if current_target is None or _identity(current_target) != _identity(target):
                raise SetupWeightsIntegrityError(
                    "TotalSegmentator existing model target changed before backup"
                )
            os.replace(
                asset.dataset_dir,
                backup_name,
                src_dir_fd=weights_root.fd,
                dst_dir_fd=weights_root.fd,
            )
            _fsync_directory(weights_root.fd)
            replaced_existing = True

        staged_dataset = _entry_lstat_fd(staging_fd, asset.dataset_dir, label="staged model target")
        current_target = _entry_lstat(weights_root, asset.dataset_dir)
        if (
            staged_dataset is None
            or not _private_directory_status(staged_dataset)
            or current_target is not None
        ):
            raise SetupWeightsIntegrityError(
                "TotalSegmentator staged model target changed before publish"
            )
        os.replace(
            asset.dataset_dir,
            asset.dataset_dir,
            src_dir_fd=staging_fd,
            dst_dir_fd=weights_root.fd,
        )
        _fsync_directory(weights_root.fd)
    except Exception:
        if (
            replaced_existing
            and backup_name is not None
            and backup_marker_name is not None
            and _entry_lstat(weights_root, backup_name) is not None
            and _entry_lstat(weights_root, asset.dataset_dir) is None
        ):
            _restore_owned_backup_at(
                weights_root,
                backup_name,
                backup_marker_name,
                asset=asset,
                nonce=nonce,
            )
        raise
    finally:
        if staging_fd is not None:
            os.close(staging_fd)
        _remove_owned_staging_at(
            weights_root,
            staging_name,
            staging_marker_name,
            asset=asset,
            nonce=nonce,
        )
    if backup_name is not None and backup_marker_name is not None:
        _remove_owned_backup_at(
            weights_root,
            backup_name,
            backup_marker_name,
            asset=asset,
            nonce=nonce,
        )


def _verify_archive(path: Path, asset: WeightAsset) -> None:
    actual_sha256 = _file_sha256(path)
    if actual_sha256 != asset.sha256:
        raise ValueError(
            f"TotalSegmentator asset SHA-256 mismatch for task {asset.task_id}: "
            f"expected {asset.sha256}, got {actual_sha256}"
        )
    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(f"TotalSegmentator ZIP CRC failed for task {asset.task_id}: {bad_member}")
        for info in archive.infolist():
            _validate_zip_member(info)
        names = {info.filename.rstrip("/") for info in archive.infolist()}
        expected = {f"{asset.dataset_dir}/{relative}" for relative in asset.required_files}
        if not expected.issubset(names):
            missing = sorted(expected - names)
            raise ValueError(
                f"TotalSegmentator ZIP missing expected model structure for task {asset.task_id}: {missing}"
            )


def _publish_archive(path: Path, asset: WeightAsset, *, weights_root: Path) -> None:
    """Extract beside the final dataset and atomically publish on that filesystem."""

    _require_private_directory(weights_root, label="weights root")
    target = weights_root / asset.dataset_dir
    nonce = uuid4().hex
    staging = weights_root / _staging_artifact_name(asset, nonce)
    staging_marker = _staging_marker_path(weights_root, asset, nonce)
    _write_recovery_marker(
        staging_marker,
        kind="staging",
        asset=asset,
        nonce=nonce,
        artifact_name=staging.name,
    )
    staging.mkdir(mode=0o700)
    os.chmod(staging, 0o700)
    _require_private_directory(staging, label="weights staging directory")

    backup: Path | None = None
    backup_marker: Path | None = None
    replaced_existing = False
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                _extract_member(archive, info, staging)
        staged_dataset = staging / asset.dataset_dir
        if not _has_expected_structure(staged_dataset, asset.required_files, deep=True):
            raise ValueError(
                f"TotalSegmentator ZIP did not create expected model structure for task {asset.task_id}"
            )
        if _path_exists(target):
            _require_private_managed_path(target, label="existing model target")
            backup = weights_root / _backup_artifact_name(asset, nonce)
            backup_marker = _backup_marker_path(weights_root, asset, nonce)
            _write_recovery_marker(
                backup_marker,
                kind="backup",
                asset=asset,
                nonce=nonce,
                artifact_name=backup.name,
            )
            os.replace(target, backup)
            _require_private_managed_path(backup, label="model backup")
            replaced_existing = True
        # Both paths are direct children of weights_root, so this is an atomic
        # rename even if the download cache lives on a different filesystem.
        os.replace(staged_dataset, target)
    except Exception:
        if (
            replaced_existing
            and backup is not None
            and backup_marker is not None
            and _path_exists(backup)
            and not _path_exists(target)
        ):
            _restore_owned_backup(
                backup,
                backup_marker,
                asset=asset,
                nonce=nonce,
                target=target,
            )
        raise
    finally:
        _remove_owned_staging(
            staging,
            staging_marker,
            asset=asset,
            nonce=nonce,
        )
    if backup is not None and backup_marker is not None:
        _remove_owned_backup(
            backup,
            backup_marker,
            asset=asset,
            nonce=nonce,
        )


def _staging_artifact_name(asset: WeightAsset, nonce: str) -> str:
    _require_transaction_nonce(nonce)
    return f".totalseg-staging-{asset.task_id}-{nonce}"


def _backup_artifact_name(asset: WeightAsset, nonce: str) -> str:
    _require_transaction_nonce(nonce)
    return f".{asset.dataset_dir}.previous-{nonce}"


def _staging_marker_path(weights_root: Path, asset: WeightAsset, nonce: str) -> Path:
    _require_transaction_nonce(nonce)
    return weights_root / f".totalseg-staging-owner-{asset.task_id}-{nonce}.json"


def _backup_marker_path(weights_root: Path, asset: WeightAsset, nonce: str) -> Path:
    _require_transaction_nonce(nonce)
    return weights_root / f".totalseg-backup-owner-{asset.task_id}-{nonce}.json"


def _require_transaction_nonce(nonce: str) -> None:
    if re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise SetupWeightsIntegrityError("TotalSegmentator recovery transaction identity is invalid")


def _recovery_marker_payload(
    *,
    kind: str,
    asset: WeightAsset,
    nonce: str,
    artifact_name: str,
) -> dict[str, Any]:
    if kind not in {"staging", "backup"}:
        raise SetupWeightsIntegrityError("TotalSegmentator recovery marker kind is invalid")
    _require_transaction_nonce(nonce)
    expected_artifact = (
        _staging_artifact_name(asset, nonce)
        if kind == "staging"
        else _backup_artifact_name(asset, nonce)
    )
    if artifact_name != expected_artifact:
        raise SetupWeightsIntegrityError("TotalSegmentator recovery marker path is invalid")
    return {
        "schema": RECOVERY_MARKER_SCHEMA,
        "kind": kind,
        "task_id": asset.task_id,
        "dataset_dir": asset.dataset_dir,
        "nonce": nonce,
        "artifact_name": artifact_name,
    }


def _write_recovery_marker(
    path: Path,
    *,
    kind: str,
    asset: WeightAsset,
    nonce: str,
    artifact_name: str,
) -> None:
    """Compatibility helper used by fixtures; the marker write is fd-safe."""

    with _opened_roots(
        path.parent,
        weights_root=None,
        label="recovery marker",
    ) as (root, _):
        _write_recovery_marker_at(
            root,
            path.name,
            kind=kind,
            asset=asset,
            nonce=nonce,
            artifact_name=artifact_name,
        )


def _recovery_marker_is_valid(
    path: Path,
    *,
    kind: str,
    asset: WeightAsset,
    nonce: str,
    artifact_name: str,
) -> bool:
    try:
        before = path.lstat()
    except OSError:
        return False
    if not _is_private_regular_file_status(before):
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        after = path.lstat()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    return (
        before_identity == after_identity
        and _is_private_regular_file_status(after)
        and payload
        == _recovery_marker_payload(
            kind=kind,
            asset=asset,
            nonce=nonce,
            artifact_name=artifact_name,
        )
    )


def _remove_owned_staging(
    staging: Path,
    marker: Path,
    *,
    asset: WeightAsset,
    nonce: str,
) -> None:
    if not _recovery_marker_is_valid(
        marker,
        kind="staging",
        asset=asset,
        nonce=nonce,
        artifact_name=staging.name,
    ):
        raise SetupWeightsIntegrityError("TotalSegmentator staging recovery marker is invalid")
    if _path_exists(staging):
        _require_private_directory(staging, label="weights staging directory")
        _remove_path(staging)
    _remove_owned_recovery_marker(
        marker,
        kind="staging",
        asset=asset,
        nonce=nonce,
        artifact_name=staging.name,
    )


def _remove_owned_backup(
    backup: Path,
    marker: Path,
    *,
    asset: WeightAsset,
    nonce: str,
) -> None:
    if not _recovery_marker_is_valid(
        marker,
        kind="backup",
        asset=asset,
        nonce=nonce,
        artifact_name=backup.name,
    ):
        raise SetupWeightsIntegrityError("TotalSegmentator backup recovery marker is invalid")
    if _path_exists(backup):
        _require_private_managed_path(backup, label="model backup")
        _remove_path(backup)
    _remove_owned_recovery_marker(
        marker,
        kind="backup",
        asset=asset,
        nonce=nonce,
        artifact_name=backup.name,
    )


def _restore_owned_backup(
    backup: Path,
    marker: Path,
    *,
    asset: WeightAsset,
    nonce: str,
    target: Path,
) -> None:
    if not _recovery_marker_is_valid(
        marker,
        kind="backup",
        asset=asset,
        nonce=nonce,
        artifact_name=backup.name,
    ):
        raise SetupWeightsIntegrityError("TotalSegmentator backup recovery marker is invalid")
    _require_private_managed_path(backup, label="model backup")
    if _path_exists(target):
        _require_private_managed_path(target, label="managed model target")
        _remove_path(target)
    os.replace(backup, target)
    _remove_owned_recovery_marker(
        marker,
        kind="backup",
        asset=asset,
        nonce=nonce,
        artifact_name=backup.name,
    )


def _remove_owned_recovery_marker(
    marker: Path,
    *,
    kind: str,
    asset: WeightAsset,
    nonce: str,
    artifact_name: str,
) -> None:
    if not _recovery_marker_is_valid(
        marker,
        kind=kind,
        asset=asset,
        nonce=nonce,
        artifact_name=artifact_name,
    ):
        raise SetupWeightsIntegrityError("TotalSegmentator recovery marker is invalid")
    marker.unlink()


def _task_receipt_path(weights_root: Path, asset: WeightAsset) -> Path:
    return weights_root / _task_receipt_filename(asset)


def _task_receipt_filename(asset: WeightAsset) -> str:
    return f".totalsegmentator-wrapper-task-{asset.task_id}-receipt.json"


@contextmanager
def _open_dataset_directory_at(
    root: _RootDirectory,
    dataset_dir: str,
    *,
    label: str,
) -> Iterator[tuple[int, os.stat_result]]:
    descriptor, status = _open_private_directory_at(
        root.fd,
        dataset_dir,
        create=False,
        label=label,
    )
    try:
        current = _entry_lstat(root, dataset_dir)
        opened = os.fstat(descriptor)
        if current is None or _identity(current) != _identity(opened):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator {label} changed while it was being opened"
            )
        yield descriptor, opened
    finally:
        os.close(descriptor)


@contextmanager
def _open_required_file_at(
    root: _RootDirectory,
    dataset_dir: str,
    relative: str,
    *,
    label: str,
) -> Iterator[tuple[int, os.stat_result]]:
    if not _safe_relative_path(relative):
        raise SetupWeightsIntegrityError(f"TotalSegmentator {label} path is invalid")
    parts = tuple(PurePosixPath(relative).parts)
    with _open_dataset_directory_at(root, dataset_dir, label="managed model directory") as (dataset_fd, _):
        with _opened_relative_directory(
            dataset_fd,
            parts[:-1],
            create=False,
            label="managed model directory",
        ) as parent_fd:
            before = _entry_lstat_fd(parent_fd, parts[-1], label=label)
            if before is None or not _private_regular_status(before) or before.st_size <= 0:
                raise SetupWeightsIntegrityError(
                    f"TotalSegmentator {label} is missing or unsafe"
                )
            descriptor, opened = _open_private_regular_fd(
                parent_fd,
                parts[-1],
                flags=os.O_RDONLY,
                expected=before,
                label=label,
            )
            try:
                yield descriptor, opened
                after = os.fstat(descriptor)
                current = _entry_lstat_fd(parent_fd, parts[-1], label=label)
                if _identity(after) != _identity(opened) or current is None or _identity(current) != _identity(opened):
                    raise SetupWeightsIntegrityError(
                        f"TotalSegmentator {label} changed while it was being read"
                    )
            finally:
                os.close(descriptor)


def _read_descriptor_bytes(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, DEFAULT_CHUNK_SIZE)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _valid_pytorch_checkpoint_fd(descriptor: int) -> bool:
    duplicate = os.dup(descriptor)
    try:
        os.lseek(duplicate, 0, os.SEEK_SET)
        stream = os.fdopen(duplicate, "rb", closefd=True)
        duplicate = -1
        with stream, zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            if not infos or archive.testzip() is not None:
                return False
            for info in infos:
                _validate_zip_member(info)
            names = [info.filename.rstrip("/") for info in infos if not info.is_dir()]
            has_pickle = any(PurePosixPath(name).name == "data.pkl" for name in names)
            has_tensor_data = any("/data/" in f"/{name}" for name in names)
            return has_pickle and has_tensor_data
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
        return False
    finally:
        if duplicate >= 0:
            os.close(duplicate)


def _has_expected_structure_at(
    root: _RootDirectory,
    dataset_dir: str,
    required_files: tuple[str, ...],
    *,
    deep: bool = False,
) -> bool:
    try:
        for relative in required_files:
            with _open_required_file_at(
                root,
                dataset_dir,
                relative,
                label="required model file",
            ) as (descriptor, _):
                if not deep:
                    continue
                if relative.endswith(".json"):
                    content = _read_descriptor_bytes(descriptor)
                    if not content.strip():
                        return False
                    json.loads(content.decode("utf-8"))
                elif relative.endswith(".pth") and not _valid_pytorch_checkpoint_fd(descriptor):
                    return False
        return True
    except (
        OSError,
        SetupWeightsIntegrityError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ):
        return False


def _fingerprint_required_file_at(
    root: _RootDirectory,
    dataset_dir: str,
    relative: str,
) -> tuple[int, str]:
    with _open_required_file_at(
        root,
        dataset_dir,
        relative,
        label="required model file",
    ) as (descriptor, status):
        return status.st_size, _sha256_descriptor(descriptor)


def _task_receipt_payload_at(
    root: _RootDirectory,
    asset: WeightAsset,
    *,
    manifest_sha256: str,
) -> dict[str, Any]:
    if not _has_expected_structure_at(root, asset.dataset_dir, asset.required_files, deep=False):
        raise SetupWeightsIntegrityError(
            f"refusing to record task receipt before model files validate for task {asset.task_id}"
        )
    payload = _task_receipt_static_payload(asset, manifest_sha256=manifest_sha256)
    payload["required_files"] = [
        {
            "path": relative,
            "size_bytes": fingerprint[0],
            "sha256": fingerprint[1],
        }
        for relative in asset.required_files
        for fingerprint in [_fingerprint_required_file_at(root, asset.dataset_dir, relative)]
    ]
    return payload


def _write_task_receipt_at(
    root: _RootDirectory,
    name: str,
    asset: WeightAsset,
    *,
    manifest_sha256: str,
) -> None:
    _write_json_atomic_at(
        root,
        name,
        _task_receipt_payload_at(root, asset, manifest_sha256=manifest_sha256),
        label="task receipt",
    )


def _task_receipt_is_valid_at(
    root: _RootDirectory,
    name: str,
    asset: WeightAsset,
    *,
    manifest_sha256: str,
) -> bool:
    try:
        raw, _ = _read_private_file_at(root, name, label="task receipt")
        payload = json.loads(raw.decode("utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError, SetupWeightsIntegrityError):
        return False
    if not isinstance(payload, dict):
        return False
    expected_static = _task_receipt_static_payload(asset, manifest_sha256=manifest_sha256)
    if (
        set(payload) != set(expected_static) | {"required_files"}
        or any(payload.get(key) != value for key, value in expected_static.items())
    ):
        return False
    try:
        return payload == _task_receipt_payload_at(
            root,
            asset,
            manifest_sha256=manifest_sha256,
        )
    except (OSError, SetupWeightsIntegrityError):
        return False


def _task_receipt_static_payload(
    asset: WeightAsset,
    *,
    manifest_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": TASK_RECEIPT_SCHEMA,
        "setup_weights_manifest_sha256": manifest_sha256,
        "task_id": asset.task_id,
        "totalsegmentator_version": asset.totalsegmentator_version,
        "release_tag": asset.release_tag,
        "filename": asset.filename,
        "url": asset.url,
        "archive_size_bytes": asset.size_bytes,
        "archive_sha256": asset.sha256,
        "archive_sha256_source": asset.sha256_source,
        "source": TASK_RECEIPT_SOURCE,
        "dataset_dir": asset.dataset_dir,
        "integrity_source": OFFICIAL_ARCHIVE_INTEGRITY_SOURCE,
        "archive_verified": True,
    }


def _task_receipt_payload(
    asset: WeightAsset,
    *,
    target: Path,
    manifest_sha256: str,
) -> dict[str, Any]:
    if not _has_expected_structure(target, asset.required_files, deep=False):
        raise SetupWeightsIntegrityError(
            f"refusing to record task receipt before model files validate for task {asset.task_id}"
        )
    payload = _task_receipt_static_payload(asset, manifest_sha256=manifest_sha256)
    payload["required_files"] = [
        {
            "path": relative,
            "size_bytes": fingerprint[0],
            "sha256": fingerprint[1],
        }
        for relative in asset.required_files
        for fingerprint in [_fingerprint_required_file(target / relative)]
    ]
    return payload


def _write_task_receipt(
    path: Path,
    asset: WeightAsset,
    *,
    target: Path,
    manifest_sha256: str,
) -> None:
    _require_regular_download_file_or_missing(path, label="task receipt")
    _write_json_atomic(
        path,
        _task_receipt_payload(
            asset,
            target=target,
            manifest_sha256=manifest_sha256,
        ),
    )


def _task_receipt_is_valid(
    path: Path,
    asset: WeightAsset,
    *,
    target: Path,
    manifest_sha256: str,
) -> bool:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or before.st_mode & 0o022
    ):
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        after = path.lstat()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if (
        before_identity != after_identity
        or not stat.S_ISREG(after.st_mode)
        or stat.S_ISLNK(after.st_mode)
        or after.st_uid != os.geteuid()
        or after.st_nlink != 1
        or after.st_mode & 0o022
        or not isinstance(payload, dict)
    ):
        return False
    expected_static = _task_receipt_static_payload(
        asset,
        manifest_sha256=manifest_sha256,
    )
    if (
        set(payload) != set(expected_static) | {"required_files"}
        or any(payload.get(key) != value for key, value in expected_static.items())
    ):
        return False
    try:
        expected = _task_receipt_payload(
            asset,
            target=target,
            manifest_sha256=manifest_sha256,
        )
    except (OSError, SetupWeightsIntegrityError):
        return False
    return payload == expected


def _extract_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, destination: Path) -> None:
    _validate_zip_member(info)
    relative = PurePosixPath(info.filename)
    target = destination.joinpath(*relative.parts)
    if info.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info) as source, target.open("wb") as output:
        shutil.copyfileobj(source, output)


def _validate_zip_member(info: zipfile.ZipInfo) -> None:
    relative = PurePosixPath(info.filename)
    mode = (info.external_attr >> 16) & 0xFFFF
    member_type = stat.S_IFMT(mode)
    if (
        not info.filename
        or relative.is_absolute()
        or ".." in relative.parts
        or "\\" in info.filename
        or stat.S_ISLNK(mode)
        or (
            member_type not in {0, stat.S_IFREG, stat.S_IFDIR}
            or (member_type == stat.S_IFDIR and not info.is_dir())
        )
    ):
        raise ValueError(f"unsafe ZIP member: {info.filename!r}")


def _parse_content_range(response: Any) -> tuple[int, int, int] | None:
    content_range = response.headers.get("Content-Range") if response.headers else None
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range or "")
    if match is None:
        return None
    return tuple(int(value) for value in match.groups())


def _validate_download_response_transport(
    response: Any,
    *,
    label: str,
    requested_url: str,
) -> None:
    try:
        final_url = response.geturl()
        parsed = urlsplit(final_url)
        port = parsed.port
        requested = urlsplit(requested_url)
        requested_port = requested.port
    except (AttributeError, TypeError, ValueError) as exc:
        raise SetupWeightsIntegrityError(f"{label} response URL is invalid") from exc
    if (
        not isinstance(final_url, str)
        or parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise SetupWeightsIntegrityError(
            f"{label} final response URL must use HTTPS on port 443 without credentials"
        )
    if (
        requested.scheme.lower() != "https"
        or not requested.hostname
        or requested.username is not None
        or requested.password is not None
        or requested_port not in (None, 443)
    ):
        raise SetupWeightsIntegrityError(
            f"{label} requested URL must use HTTPS on port 443 without credentials"
        )
    requested_host = requested.hostname.lower()
    final_host = parsed.hostname.lower()
    allowed_response_hosts = {requested_host}
    if requested_host == "github.com":
        allowed_response_hosts.add("release-assets.githubusercontent.com")
    if final_host not in allowed_response_hosts:
        raise SetupWeightsIntegrityError(
            f"{label} response host is not approved for the requested asset"
        )
    headers = getattr(response, "headers", None) or {}
    content_encoding = headers.get("Content-Encoding")
    if content_encoding is not None and (
        not isinstance(content_encoding, str)
        or content_encoding.strip().lower() != "identity"
    ):
        raise SetupWeightsIntegrityError(
            f"{label} response used unsupported Content-Encoding"
        )


def _write_progress(
    progress_log: Path | None,
    asset: WeightAsset,
    *,
    status: str,
    completed_bytes: int,
    rate_bps: float | None = None,
    eta_seconds: float | None = None,
    resumed: bool = False,
    resume_from_bytes: int = 0,
    index: int = 1,
    task_total: int = 1,
    restart_reason: str | None = None,
) -> None:
    percent = min(100, int(completed_bytes * 100 / asset.size_bytes)) if asset.size_bytes else None
    _append_progress(
        progress_log,
        {
            "source": "totalsegmentator",
            "status": status,
            "task_id": asset.task_id,
            "index": index,
            "task_total": task_total,
            "completed_bytes": completed_bytes,
            "total_bytes": asset.size_bytes,
            "percent": percent,
            "rate_bps": rate_bps,
            "eta_seconds": eta_seconds,
            "resumed": resumed,
            "resume_from_bytes": resume_from_bytes,
            "restart_reason": restart_reason,
        },
    )


def _append_progress(progress_log: Path | None, payload: dict[str, Any]) -> None:
    if progress_log is None:
        return
    progress_log.parent.mkdir(parents=True, exist_ok=True)
    with progress_log.open("a", encoding="utf-8") as log:
        log.write(PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        log.flush()


def _validate_manifest(assets: tuple[WeightAsset, ...]) -> None:
    if len(assets) != len(SETUP_TASK_IDS) or {asset.task_id for asset in assets} != set(SETUP_TASK_IDS):
        raise ValueError("TotalSegmentator setup manifest must contain exactly tasks 113, 115, and 297")
    for asset in assets:
        if asset.totalsegmentator_version != PINNED_TOTALSEGMENTATOR_VERSION:
            raise ValueError("TotalSegmentator setup manifest version does not match the pinned runtime")
        if not re.fullmatch(r"[0-9a-f]{64}", asset.sha256):
            raise ValueError(f"invalid SHA-256 for TotalSegmentator task {asset.task_id}")
        if not _safe_single_component(asset.filename, suffix=".zip"):
            raise ValueError(f"invalid filename in TotalSegmentator setup manifest for task {asset.task_id}")
        if not _safe_single_component(asset.dataset_dir):
            raise ValueError(f"invalid dataset directory in TotalSegmentator setup manifest for task {asset.task_id}")
        if not _safe_single_component(asset.release_tag):
            raise ValueError(f"invalid release tag in TotalSegmentator setup manifest for task {asset.task_id}")
        expected_url = (
            "https://github.com/wasserth/TotalSegmentator/releases/download/"
            f"{asset.release_tag}/{asset.filename}"
        )
        if asset.url != expected_url:
            raise ValueError(f"untrusted TotalSegmentator asset URL for task {asset.task_id}")
        dataset_path = PurePosixPath(asset.dataset_dir)
        required_paths = tuple(PurePosixPath(value) for value in asset.required_files)
        if (
            asset.size_bytes <= 0
            or not asset.required_files
            or dataset_path.is_absolute()
            or len(dataset_path.parts) != 1
            or len(set(asset.required_files)) != len(asset.required_files)
            or any(
                path.is_absolute()
                or not path.parts
                or any(part in {"", ".", ".."} for part in path.parts)
                or "\\" in str(path)
                or _has_control_character(str(path))
                for path in required_paths
            )
        ):
            raise ValueError(f"incomplete TotalSegmentator manifest entry for task {asset.task_id}")


def validate_setup_weights_registry(
    weights_root: Path,
    *,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Strictly validate registry provenance and every managed model file hash."""
    with _opened_roots(
        weights_root,
        weights_root=None,
        label="setup weights registry",
    ) as (root, _):
        return _validate_setup_weights_registry_at(root, manifest_path=manifest_path)


def _validate_setup_weights_registry_at(
    weights_root: _RootDirectory,
    *,
    manifest_path: Path | None,
) -> dict[str, Any]:
    try:
        raw, _ = _read_private_file_at(
            weights_root,
            REGISTRY_FILENAME,
            label="setup weights registry",
        )
        payload = json.loads(raw.decode("utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError, SetupWeightsIntegrityError) as exc:
        raise SetupWeightsIntegrityError("TotalSegmentator setup weights registry is missing or invalid") from exc
    if isinstance(payload, dict) and payload.get("schema") == LEGACY_REGISTRY_SCHEMA:
        raise SetupWeightsIntegrityError(
            "TotalSegmentator v1 setup weights registry requires verified archive refresh"
        )
    if not isinstance(payload, dict) or payload.get("schema") != REGISTRY_SCHEMA:
        raise SetupWeightsIntegrityError("unsupported TotalSegmentator setup weights registry schema")
    assets = load_setup_weight_manifest(manifest_path)
    expected_manifest_sha = setup_weight_manifest_sha256(manifest_path)
    if payload.get("totalsegmentator_version") != PINNED_TOTALSEGMENTATOR_VERSION:
        raise SetupWeightsIntegrityError("TotalSegmentator setup weights registry version mismatch")
    if payload.get("setup_weights_manifest_sha256") != expected_manifest_sha:
        raise SetupWeightsIntegrityError("TotalSegmentator setup weights registry manifest mismatch")
    integrity_source = payload.get("integrity_source")
    archive_verified = payload.get("archive_verified")
    legacy_migration = payload.get("legacy_migration")
    if type(archive_verified) is not bool or type(legacy_migration) is not bool:
        raise SetupWeightsIntegrityError("TotalSegmentator setup weights registry provenance is invalid")
    if archive_verified:
        if integrity_source != OFFICIAL_ARCHIVE_INTEGRITY_SOURCE or legacy_migration:
            raise SetupWeightsIntegrityError("TotalSegmentator official archive provenance is invalid")
    elif integrity_source != LEGACY_MIGRATION_INTEGRITY_SOURCE or not legacy_migration:
        raise SetupWeightsIntegrityError("TotalSegmentator legacy migration provenance is invalid")

    raw_entries = payload.get("assets")
    if not isinstance(raw_entries, list) or len(raw_entries) != len(SETUP_TASK_IDS):
        raise SetupWeightsIntegrityError("TotalSegmentator setup weights registry task set is invalid")
    entries: dict[int, dict[str, Any]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict) or type(entry.get("task_id")) is not int:
            raise SetupWeightsIntegrityError("TotalSegmentator setup weights registry entry is invalid")
        task_id = entry["task_id"]
        if task_id in entries:
            raise SetupWeightsIntegrityError("TotalSegmentator setup weights registry contains duplicate tasks")
        entries[task_id] = entry
    if set(entries) != set(SETUP_TASK_IDS):
        raise SetupWeightsIntegrityError("TotalSegmentator setup weights registry task set is invalid")

    for asset in assets:
        entry = entries[asset.task_id]
        if entry.get("dataset_dir") != asset.dataset_dir:
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator setup weights registry dataset mismatch for task {asset.task_id}"
            )
        if (
            entry.get("integrity_source") != integrity_source
            or type(entry.get("archive_verified")) is not bool
            or entry.get("archive_verified") is not archive_verified
        ):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator setup weights registry provenance mismatch for task {asset.task_id}"
            )
        if archive_verified:
            if (
                entry.get("archive_sha256") != asset.sha256
                or entry.get("archive_sha256_source") != asset.sha256_source
            ):
                raise SetupWeightsIntegrityError(
                    f"TotalSegmentator archive provenance mismatch for task {asset.task_id}"
                )
        elif entry.get("archive_sha256") is not None or entry.get("archive_sha256_source") is not None:
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator legacy migration falsely records archive verification for task {asset.task_id}"
            )
        raw_files = entry.get("required_files")
        if not isinstance(raw_files, list) or len(raw_files) != len(asset.required_files):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator setup weights registry file set mismatch for task {asset.task_id}"
            )
        registered_files: dict[str, tuple[int, str]] = {}
        for item in raw_files:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
                or type(item.get("size_bytes")) is not int
                or item["size_bytes"] <= 0
                or not isinstance(item.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
                or not _safe_relative_path(item["path"])
            ):
                raise SetupWeightsIntegrityError(
                    f"TotalSegmentator setup weights registry file entry is invalid for task {asset.task_id}"
                )
            if item["path"] in registered_files:
                raise SetupWeightsIntegrityError(
                    f"TotalSegmentator setup weights registry contains duplicate files for task {asset.task_id}"
                )
            registered_files[item["path"]] = (item["size_bytes"], item["sha256"])
        if set(registered_files) != set(asset.required_files):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator setup weights registry file set mismatch for task {asset.task_id}"
            )
        # The v2 registry hashes were created only after deep JSON/checkpoint ZIP
        # validation. Runtime preflight rechecks every byte by SHA-256; repeating
        # checkpoint ZIP decompression here would double large-file I/O without
        # adding an independent integrity property.
        if not _has_expected_structure_at(
            weights_root,
            asset.dataset_dir,
            asset.required_files,
            deep=False,
        ):
            raise SetupWeightsIntegrityError(
                f"TotalSegmentator installed model is missing or invalid for task {asset.task_id}"
            )
        for relative, (expected_size, expected_sha256) in registered_files.items():
            actual_size, actual_sha256 = _fingerprint_required_file_at(
                weights_root,
                asset.dataset_dir,
                relative,
            )
            if actual_size != expected_size:
                raise SetupWeightsIntegrityError(
                    f"TotalSegmentator installed model size mismatch for task {asset.task_id}"
                )
            if actual_sha256 != expected_sha256:
                raise SetupWeightsIntegrityError(
                    f"TotalSegmentator installed model SHA-256 mismatch for task {asset.task_id}"
                )
    return payload


def _write_ready_registry(
    weights_root: Path,
    assets: tuple[WeightAsset, ...],
    *,
    manifest_sha256: str,
    integrity_source: str,
    archive_verified: bool,
) -> None:
    """Compatibility helper used by fixtures; the implementation is fd-safe."""

    with _opened_roots(
        weights_root,
        weights_root=None,
        label="setup weights registry",
    ) as (root, _):
        _write_ready_registry_at(
            root,
            assets,
            manifest_sha256=manifest_sha256,
            integrity_source=integrity_source,
            archive_verified=archive_verified,
        )


def _registry_payload_at(
    weights_root: _RootDirectory,
    assets: tuple[WeightAsset, ...],
    *,
    manifest_sha256: str,
    integrity_source: str,
    archive_verified: bool,
) -> dict[str, Any]:
    if (
        type(archive_verified) is not bool
        or (
            archive_verified
            and integrity_source != OFFICIAL_ARCHIVE_INTEGRITY_SOURCE
        )
        or (
            not archive_verified
            and integrity_source != LEGACY_MIGRATION_INTEGRITY_SOURCE
        )
    ):
        raise SetupWeightsIntegrityError("invalid TotalSegmentator registry provenance request")
    if not _all_assets_deep_valid_at(assets, weights_root):
        raise SetupWeightsIntegrityError(
            "refusing to publish TotalSegmentator setup weights registry before all models validate"
        )
    return {
        "schema": REGISTRY_SCHEMA,
        "totalsegmentator_version": PINNED_TOTALSEGMENTATOR_VERSION,
        "setup_weights_manifest_sha256": manifest_sha256,
        "integrity_source": integrity_source,
        "archive_verified": archive_verified,
        "legacy_migration": not archive_verified,
        "assets": [
            {
                "task_id": asset.task_id,
                "dataset_dir": asset.dataset_dir,
                "integrity_source": integrity_source,
                "archive_verified": archive_verified,
                "archive_sha256": asset.sha256 if archive_verified else None,
                "archive_sha256_source": asset.sha256_source if archive_verified else None,
                "required_files": [
                    {
                        "path": relative,
                        "size_bytes": fingerprint[0],
                        "sha256": fingerprint[1],
                    }
                    for relative in asset.required_files
                    for fingerprint in [
                        _fingerprint_required_file_at(
                            weights_root,
                            asset.dataset_dir,
                            relative,
                        )
                    ]
                ],
            }
            for asset in sorted(assets, key=lambda value: value.task_id)
        ],
    }


def _write_ready_registry_at(
    weights_root: _RootDirectory,
    assets: tuple[WeightAsset, ...],
    *,
    manifest_sha256: str,
    integrity_source: str,
    archive_verified: bool,
) -> None:
    _write_json_atomic_at(
        weights_root,
        REGISTRY_FILENAME,
        _registry_payload_at(
            weights_root,
            assets,
            manifest_sha256=manifest_sha256,
            integrity_source=integrity_source,
            archive_verified=archive_verified,
        ),
        label="setup weights registry",
    )


def _all_assets_deep_valid_at(
    assets: tuple[WeightAsset, ...],
    weights_root: _RootDirectory,
) -> bool:
    return all(
        _has_expected_structure_at(
            weights_root,
            asset.dataset_dir,
            asset.required_files,
            deep=True,
        )
        for asset in assets
    )


def _any_asset_target_exists_at(
    assets: tuple[WeightAsset, ...],
    weights_root: _RootDirectory,
) -> bool:
    return any(_entry_lstat(weights_root, asset.dataset_dir) is not None for asset in assets)


def _directory_entry_names_at(root: _RootDirectory, *, label: str) -> list[str]:
    try:
        entries = os.listdir(root.fd)
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be inspected safely"
        ) from exc
    names = sorted(str(name) for name in entries)
    if any(not _safe_single_component(name) for name in names):
        raise SetupWeightsIntegrityError(f"TotalSegmentator {label} contains an invalid entry")
    return names


def _recover_interrupted_publications_at(
    assets: tuple[WeightAsset, ...],
    *,
    weights_root: _RootDirectory,
    cache_root: _RootDirectory,
) -> None:
    """Recover only marker-proven transaction artifacts beneath pinned roots.

    Pre-marker cache staging directories are intentionally retained.  Their
    name alone cannot prove ownership, so deleting them would violate the same
    safety rule used for recovery markers and user-provided files.
    """

    # ``cache_root`` is intentionally retained in the signature: taking both
    # root locks is part of the transaction contract, even though markerless
    # legacy cache staging is now preserved rather than deleted.
    _ = cache_root
    assets_by_task = {asset.task_id: asset for asset in assets}
    owned_backups: dict[int, list[tuple[str, str, str]]] = {
        asset.task_id: [] for asset in assets
    }

    for name in _directory_entry_names_at(weights_root, label="weights root"):
        staging_match = _STAGING_MARKER_RE.fullmatch(name)
        if staging_match is not None:
            task_id = int(staging_match.group("task_id"))
            asset = assets_by_task.get(task_id)
            nonce = staging_match.group("nonce")
            if asset is not None:
                staging_name = _staging_artifact_name(asset, nonce)
                if _recovery_marker_is_valid_at(
                    weights_root,
                    name,
                    kind="staging",
                    asset=asset,
                    nonce=nonce,
                    artifact_name=staging_name,
                ):
                    _remove_owned_staging_at(
                        weights_root,
                        staging_name,
                        name,
                        asset=asset,
                        nonce=nonce,
                    )
            continue

        backup_match = _BACKUP_MARKER_RE.fullmatch(name)
        if backup_match is None:
            continue
        task_id = int(backup_match.group("task_id"))
        asset = assets_by_task.get(task_id)
        nonce = backup_match.group("nonce")
        if asset is None:
            continue
        backup_name = _backup_artifact_name(asset, nonce)
        if _recovery_marker_is_valid_at(
            weights_root,
            name,
            kind="backup",
            asset=asset,
            nonce=nonce,
            artifact_name=backup_name,
        ):
            owned_backups[task_id].append((backup_name, name, nonce))

    for asset in assets:
        backups = sorted(owned_backups[asset.task_id], key=lambda item: item[0])
        if _has_expected_structure_at(
            weights_root,
            asset.dataset_dir,
            asset.required_files,
            deep=True,
        ):
            for backup_name, marker_name, nonce in backups:
                _remove_owned_backup_at(
                    weights_root,
                    backup_name,
                    marker_name,
                    asset=asset,
                    nonce=nonce,
                )
            continue

        valid_backups = [
            item
            for item in backups
            if _has_expected_structure_at(
                weights_root,
                item[0],
                asset.required_files,
                deep=True,
            )
        ]
        if valid_backups:
            backup_name, marker_name, nonce = valid_backups[-1]
            _restore_owned_backup_at(
                weights_root,
                backup_name,
                marker_name,
                asset=asset,
                nonce=nonce,
            )
        for backup_name, marker_name, nonce in backups:
            if _entry_lstat(weights_root, marker_name) is not None:
                _remove_owned_backup_at(
                    weights_root,
                    backup_name,
                    marker_name,
                    asset=asset,
                    nonce=nonce,
                )

    # _write_json_atomic_at() emits precisely this private uuid4().hex
    # filename.  A prefix match would make unrelated user files cleanup targets.
    for name in _directory_entry_names_at(weights_root, label="weights root"):
        if _REGISTRY_TEMP_RE.fullmatch(name) is None:
            continue
        status = _entry_lstat(weights_root, name)
        if status is not None and _private_regular_status(status):
            _unlink_private_entry_at(
                weights_root,
                name,
                label="setup weights registry temporary",
                expected=status,
            )
    _fsync_directory(weights_root.fd)


def _all_assets_deep_valid(assets: tuple[WeightAsset, ...], weights_root: Path) -> bool:
    return all(
        _has_expected_structure(weights_root / asset.dataset_dir, asset.required_files, deep=True)
        for asset in assets
    )


def _any_asset_target_exists(assets: tuple[WeightAsset, ...], weights_root: Path) -> bool:
    return any(_path_exists(weights_root / asset.dataset_dir) for asset in assets)


def _fingerprint_required_file(path: Path) -> tuple[int, str]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator required model file is missing: {path.name}"
        ) from exc
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or before.st_size <= 0:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator required model path is not a regular file: {path.name}"
        )
    digest = _file_sha256(path)
    try:
        after = path.lstat()
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator required model file changed while hashing: {path.name}"
        ) from exc
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or not stat.S_ISREG(after.st_mode):
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator required model file changed while hashing: {path.name}"
        )
    return before.st_size, digest


def _recover_interrupted_publications(
    assets: tuple[WeightAsset, ...],
    *,
    weights_root: Path,
    cache_root: Path,
) -> None:
    """Recover only artifacts with an app-owned, strict identity.

    This code runs before a fresh setup while both the cache and weights roots
    are locked.  It must not turn an incidental dot-file into an application
    deletion target: new publication artifacts require an exact private marker;
    the one pre-marker cache staging format is accepted only with the former
    task-id plus 32-hex-UUID name.
    """

    _require_private_directory(weights_root, label="weights root")
    _require_private_directory(cache_root, label="download cache root")
    assets_by_task = {asset.task_id: asset for asset in assets}

    # Earlier development builds staged only in the cache.  Their naming was a
    # task id and uuid4().hex, so preserve recovery for that exact, private
    # format without broad matching every .totalseg-staging-* directory.
    for staging in _private_legacy_cache_staging(cache_root, assets_by_task):
        _remove_path(staging)

    owned_backups: dict[int, list[tuple[Path, Path, str]]] = {
        asset.task_id: [] for asset in assets
    }
    for candidate in _directory_entries(weights_root, label="weights root"):
        staging_match = _STAGING_MARKER_RE.fullmatch(candidate.name)
        if staging_match is not None:
            task_id = int(staging_match.group("task_id"))
            asset = assets_by_task.get(task_id)
            nonce = staging_match.group("nonce")
            if asset is not None:
                staging = weights_root / _staging_artifact_name(asset, nonce)
                if _recovery_marker_is_valid(
                    candidate,
                    kind="staging",
                    asset=asset,
                    nonce=nonce,
                    artifact_name=staging.name,
                ):
                    _remove_owned_staging(
                        staging,
                        candidate,
                        asset=asset,
                        nonce=nonce,
                    )
            continue

        backup_match = _BACKUP_MARKER_RE.fullmatch(candidate.name)
        if backup_match is None:
            continue
        task_id = int(backup_match.group("task_id"))
        asset = assets_by_task.get(task_id)
        nonce = backup_match.group("nonce")
        if asset is None:
            continue
        backup = weights_root / _backup_artifact_name(asset, nonce)
        if _recovery_marker_is_valid(
            candidate,
            kind="backup",
            asset=asset,
            nonce=nonce,
            artifact_name=backup.name,
        ):
            owned_backups[task_id].append((backup, candidate, nonce))

    for asset in assets:
        target = weights_root / asset.dataset_dir
        backups = sorted(owned_backups[asset.task_id], key=lambda item: item[0].name)
        if _has_expected_structure(target, asset.required_files, deep=True):
            _require_private_directory(target, label="managed model target")
            for backup, marker, nonce in backups:
                _remove_owned_backup(backup, marker, asset=asset, nonce=nonce)
            continue

        valid_backups = [
            item
            for item in backups
            if _has_expected_structure(item[0], asset.required_files, deep=True)
        ]
        if valid_backups:
            backup, marker, nonce = valid_backups[-1]
            _restore_owned_backup(
                backup,
                marker,
                asset=asset,
                nonce=nonce,
                target=target,
            )
        for backup, marker, nonce in backups:
            if _path_exists(marker):
                _remove_owned_backup(backup, marker, asset=asset, nonce=nonce)

    # _write_json_atomic() emits precisely this private uuid4().hex filename.
    # A prefix glob would also erase user files after an interrupted setup.
    for temporary_registry in _directory_entries(weights_root, label="weights root"):
        if _REGISTRY_TEMP_RE.fullmatch(temporary_registry.name) is None:
            continue
        try:
            file_status = temporary_registry.lstat()
        except OSError:
            continue
        if _is_private_regular_file_status(file_status):
            temporary_registry.unlink()


def _directory_entries(root: Path, *, label: str) -> list[Path]:
    try:
        return sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be inspected safely"
        ) from exc


def _private_legacy_cache_staging(
    cache_root: Path,
    assets_by_task: dict[int, WeightAsset],
) -> list[Path]:
    owned: list[Path] = []
    for candidate in _directory_entries(cache_root, label="download cache root"):
        match = _STAGING_ARTIFACT_RE.fullmatch(candidate.name)
        if match is None or int(match.group("task_id")) not in assets_by_task:
            continue
        try:
            file_status = candidate.lstat()
        except OSError:
            continue
        if _is_private_directory_status(file_status):
            owned.append(candidate)
    return owned


def _safe_single_component(value: str, *, suffix: str | None = None) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and not _has_control_character(value)
        and "\\" not in value
        and not path.is_absolute()
        and len(path.parts) == 1
        and path.name == value
        and value not in {".", ".."}
        and (suffix is None or value.endswith(suffix))
    )


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and not path.is_absolute()
        and "\\" not in value
        and not _has_control_character(value)
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _sidecar_matches(path: Path, asset: WeightAsset) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload == asset.sidecar_payload()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _has_expected_structure(
    root: Path,
    required_files: tuple[str, ...],
    *,
    deep: bool = False,
) -> bool:
    try:
        root_stat = root.lstat()
    except OSError:
        return False
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        return False
    for relative in required_files:
        if not _safe_relative_path(relative):
            return False
        path = root.joinpath(*PurePosixPath(relative).parts)
        if _contains_symlink(root, path):
            return False
        try:
            file_stat = path.lstat()
        except OSError:
            return False
        if not stat.S_ISREG(file_stat.st_mode) or stat.S_ISLNK(file_stat.st_mode) or file_stat.st_size <= 0:
            return False
        if not deep:
            continue
        try:
            if relative.endswith(".json"):
                content = path.read_bytes()
                if not content.strip():
                    return False
                json.loads(content.decode("utf-8"))
            elif relative.endswith(".pth") and not _valid_pytorch_checkpoint(path):
                return False
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile):
            return False
    return True


def _valid_pytorch_checkpoint(path: Path) -> bool:
    if not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if not infos or archive.testzip() is not None:
            return False
        for info in infos:
            _validate_zip_member(info)
        names = [info.filename.rstrip("/") for info in infos if not info.is_dir()]
        has_pickle = any(PurePosixPath(name).name == "data.pkl" for name in names)
        has_tensor_data = any("/data/" in f"/{name}" for name in names)
        return has_pickle and has_tensor_data


def _contains_symlink(root: Path, path: Path) -> bool:
    current = root
    relative = path.relative_to(root)
    for part in relative.parts[:-1]:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except OSError:
            return False
    return False


def _archive_uncompressed_size(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        return sum(info.file_size for info in archive.infolist() if not info.is_dir())


def _require_free_space(path: Path, required_bytes: int) -> None:
    if required_bytes <= 0:
        return
    free_bytes = shutil.disk_usage(path).free
    if free_bytes < required_bytes:
        raise OSError(
            errno.ENOSPC,
            f"insufficient disk space: need {required_bytes} bytes, have {free_bytes} bytes",
        )


def _require_free_space_fd(
    root: _RootDirectory,
    required_bytes: int,
    *,
    metadata_entries: int,
) -> None:
    """Check space as filesystem blocks plus a bounded metadata reserve.

    Archive member sizes alone understate actual allocation: every partial and
    extracted entry consumes at least one filesystem block, and directory / ZIP
    bookkeeping requires a small amount of metadata space.  Four MiB is a
    deliberately bounded reserve (rather than an arbitrary second copy of a
    hundreds-of-MiB model) and the per-entry component tracks the filesystem's
    actual allocation unit.
    """

    if required_bytes < 0 or metadata_entries < 0:
        raise ValueError("TotalSegmentator disk-space request is invalid")
    try:
        vfs = os.fstatvfs(root.fd)
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            "TotalSegmentator disk space cannot be inspected safely"
        ) from exc
    block_size = max(int(vfs.f_frsize or vfs.f_bsize or 1), 1)
    data_blocks = (required_bytes + block_size - 1) // block_size
    # A directory plus each newly written entry can consume one allocation
    # block.  Keep one base block even when the remaining payload is zero.
    metadata_blocks = max(1, metadata_entries)
    required = (
        (data_blocks + metadata_blocks) * block_size
        + _DISK_METADATA_RESERVE_BYTES
    )
    free_bytes = int(vfs.f_bavail) * block_size
    if free_bytes < required:
        raise OSError(
            errno.ENOSPC,
            f"insufficient disk space: need {required} bytes, have {free_bytes} bytes",
        )


def _restart_partial(
    partial: Path,
    sidecar: Path,
    asset: WeightAsset,
    *,
    progress_log: Path | None,
    progress_index: int,
    progress_total: int,
    reason: str,
) -> None:
    previous_size = _regular_file_size(partial)
    _discard_download_files(partial, sidecar)
    _write_json_atomic(sidecar, asset.sidecar_payload())
    _write_progress(
        progress_log,
        asset,
        status="restart",
        completed_bytes=0,
        resumed=previous_size > 0,
        resume_from_bytes=previous_size,
        index=progress_index,
        task_total=progress_total,
        restart_reason=reason,
    )


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except OSError:
        return False


def _require_regular_download_file_or_missing(path: Path, *, label: str) -> None:
    try:
        file_status = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be inspected safely"
        ) from exc
    if not _is_private_regular_file_status(file_status):
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} must be a private regular file"
        )


def _regular_file_size(path: Path) -> int:
    try:
        file_status = path.lstat()
    except FileNotFoundError:
        return 0
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            "TotalSegmentator partial download cannot be inspected safely"
        ) from exc
    if not _is_private_regular_file_status(file_status):
        raise SetupWeightsIntegrityError(
            "TotalSegmentator partial download must be a private regular file"
        )
    return file_status.st_size


def _is_private_regular_file_status(file_status: os.stat_result) -> bool:
    return (
        stat.S_ISREG(file_status.st_mode)
        and not stat.S_ISLNK(file_status.st_mode)
        and file_status.st_uid == os.geteuid()
        and file_status.st_nlink == 1
        and not file_status.st_mode & 0o022
    )


def _is_private_directory_status(file_status: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(file_status.st_mode)
        and not stat.S_ISLNK(file_status.st_mode)
        and file_status.st_uid == os.geteuid()
        and not file_status.st_mode & 0o022
    )


def _require_private_directory(path: Path, *, label: str) -> None:
    try:
        file_status = path.lstat()
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be inspected safely"
        ) from exc
    if not _is_private_directory_status(file_status):
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} must be a private directory"
        )


def _require_private_managed_path(path: Path, *, label: str) -> None:
    try:
        file_status = path.lstat()
    except OSError as exc:
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} cannot be inspected safely"
        ) from exc
    if not (
        _is_private_directory_status(file_status)
        or _is_private_regular_file_status(file_status)
    ):
        raise SetupWeightsIntegrityError(
            f"TotalSegmentator {label} must be a private regular file or directory"
        )


def _discard_download_files(
    partial: Path,
    sidecar: Path,
    *,
    fail_on_nonregular: bool = True,
) -> None:
    for path in (partial, sidecar):
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError:
            if fail_on_nonregular:
                raise SetupWeightsIntegrityError(
                    "TotalSegmentator partial download cannot be inspected safely"
                )
            continue
        if not stat.S_ISREG(mode):
            if fail_on_nonregular:
                raise SetupWeightsIntegrityError(
                    "TotalSegmentator partial download path must be a regular file"
                )
            continue
        path.unlink()


def _remove_path(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return
    if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(DEFAULT_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _header_int(response: Any, name: str) -> int | None:
    value = response.headers.get(name) if response.headers else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _scaled_integer(value: str, unit: str) -> int:
    return int(float(value) * _UNIT_FACTORS[unit])


def _duration_seconds(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return parts[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare pinned TotalSegmentator setup weights with resume.")
    parser.add_argument("--progress-log", type=Path)
    parser.add_argument("--task-ids", type=int, nargs="+", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    weights_path = os.environ.get("TOTALSEG_WEIGHTS_PATH")
    home_path = os.environ.get("TOTALSEG_HOME_DIR")
    if not weights_path or not home_path:
        raise RuntimeError("TOTALSEG_WEIGHTS_PATH and TOTALSEG_HOME_DIR are required")
    try:
        prepare_setup_weights(
            tuple(args.task_ids),
            weights_root=Path(weights_path),
            cache_root=Path(home_path) / "downloads",
            progress_log=args.progress_log,
        )
        return 0
    except SetupWeightsError as exc:
        print(f"SETUP_ERROR reason={exc.reason} message={exc}", file=sys.stderr)
        return 75 if isinstance(exc, SetupWeightsBusyError) else 2
    except OSError as exc:
        reason = "insufficient_disk_space" if exc.errno == errno.ENOSPC else "weights_download_failed"
        print(f"SETUP_ERROR reason={reason} message={exc.strerror or type(exc).__name__}", file=sys.stderr)
        return 2
    except (ValueError, zipfile.BadZipFile) as exc:
        print(f"SETUP_ERROR reason=weights_integrity_failed message={type(exc).__name__}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(
            f"SETUP_ERROR reason=weights_download_failed message={type(exc).__name__}",
            file=sys.stderr,
        )
        print(repr(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
