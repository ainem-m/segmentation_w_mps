#!/usr/bin/env python3
"""Fingerprint a copied bundled Python runtime without following its symlinks.

The fingerprint deliberately records path names, node types, POSIX permission
bits, safe relative symlink targets, and a SHA-256 digest of every regular
file's contents.  It deliberately excludes mtimes, ownership, and absolute
source paths so the result identifies the runtime payload rather than the
machine that assembled it.  Including mode bits is important: executable and
directory traversal permissions are part of whether a bundled interpreter can
start and import its standard library.

This script is stdlib-only because it runs before any packaged environment is
created.  It never follows an in-tree symlink while enumerating the runtime;
absolute or escaping link targets are rejected instead of being fingerprinted
as if they were bundled content.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path


FINGERPRINT_FORMAT = b"totalsegmentator-wrapper-mac.python-runtime.v1\0"
CHUNK_SIZE = 1024 * 1024


class RuntimeFingerprintError(RuntimeError):
    """The runtime tree cannot safely be represented by this fingerprint."""


def _as_bytes(value: str | bytes) -> bytes:
    return value if isinstance(value, bytes) else os.fsencode(value)


def _relative_display(relative: bytes) -> str:
    return os.fsdecode(relative) if relative else "."


def _update_field(digest: "hashlib._Hash", value: bytes) -> None:
    digest.update(len(value).to_bytes(8, byteorder="big", signed=False))
    digest.update(value)


def _safe_relative_symlink_target(relative: bytes, target: bytes) -> None:
    if os.path.isabs(target):
        raise RuntimeFingerprintError(
            "runtime symlink has an absolute target: "
            f"{_relative_display(relative)} -> {os.fsdecode(target)}"
        )

    resolved_relative = os.path.normpath(
        os.path.join(os.path.dirname(relative), target)
    )
    parent_reference = os.pardir.encode()
    if resolved_relative == parent_reference or resolved_relative.startswith(
        parent_reference + os.sep.encode()
    ):
        raise RuntimeFingerprintError(
            "runtime symlink escapes the runtime root: "
            f"{_relative_display(relative)} -> {os.fsdecode(target)}"
        )


def _runtime_root(path: os.PathLike[str] | str) -> str:
    root = os.fspath(path)
    try:
        entry = os.lstat(root)
    except FileNotFoundError as error:
        raise RuntimeFingerprintError(f"runtime root does not exist: {root}") from error
    if stat.S_ISLNK(entry.st_mode):
        raise RuntimeFingerprintError(f"runtime root must not be a symlink: {root}")
    if not stat.S_ISDIR(entry.st_mode):
        raise RuntimeFingerprintError(f"runtime root is not a directory: {root}")
    return os.path.realpath(root)


def _iter_runtime_entries(root: str) -> Iterator[tuple[bytes, str, os.stat_result, str]]:
    """Yield `relative, kind, lstat, path` in deterministic byte-path order."""

    def walk(directory: str, relative_directory: bytes) -> Iterator[tuple[bytes, str, os.stat_result, str]]:
        try:
            with os.scandir(directory) as scanner:
                entries = sorted(scanner, key=lambda entry: _as_bytes(entry.name))
        except OSError as error:
            raise RuntimeFingerprintError(
                f"cannot enumerate runtime directory {_relative_display(relative_directory)}: {error}"
            ) from error

        for entry in entries:
            relative = (
                _as_bytes(entry.name)
                if not relative_directory
                else relative_directory + os.sep.encode() + _as_bytes(entry.name)
            )
            try:
                entry_stat = os.lstat(entry.path)
            except OSError as error:
                raise RuntimeFingerprintError(
                    f"cannot lstat runtime entry {_relative_display(relative)}: {error}"
                ) from error
            mode = entry_stat.st_mode
            if stat.S_ISDIR(mode):
                yield relative, "directory", entry_stat, entry.path
                yield from walk(entry.path, relative)
            elif stat.S_ISREG(mode):
                yield relative, "regular", entry_stat, entry.path
            elif stat.S_ISLNK(mode):
                yield relative, "symlink", entry_stat, entry.path
            else:
                raise RuntimeFingerprintError(
                    "unsupported filesystem entry "
                    f"at {_relative_display(relative)} (mode {mode:o})"
                )

    root_stat = os.lstat(root)
    yield b"", "directory", root_stat, root
    yield from walk(root, b"")


def _regular_file_digest(path: str, expected_stat: os.stat_result) -> bytes:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeFingerprintError(f"cannot read runtime file {path}: {error}") from error
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise RuntimeFingerprintError(f"runtime file changed type while hashing: {path}")
        if (opened_stat.st_dev, opened_stat.st_ino) != (
            expected_stat.st_dev,
            expected_stat.st_ino,
        ):
            raise RuntimeFingerprintError(f"runtime file changed while hashing: {path}")
        if (
            stat.S_IMODE(opened_stat.st_mode) != stat.S_IMODE(expected_stat.st_mode)
            or opened_stat.st_mtime_ns != expected_stat.st_mtime_ns
            or opened_stat.st_size != expected_stat.st_size
        ):
            raise RuntimeFingerprintError(
                f"runtime file metadata changed before hashing: {path}"
            )
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, CHUNK_SIZE)
            if not block:
                break
            digest.update(block)
        final_stat = os.fstat(descriptor)
        if (
            stat.S_IMODE(final_stat.st_mode) != stat.S_IMODE(expected_stat.st_mode)
            or final_stat.st_mtime_ns != expected_stat.st_mtime_ns
            or final_stat.st_size != expected_stat.st_size
        ):
            raise RuntimeFingerprintError(
                f"runtime file metadata changed while hashing: {path}"
            )
        return digest.digest()
    finally:
        os.close(descriptor)


def fingerprint_runtime_tree(path: os.PathLike[str] | str) -> str:
    """Return the deterministic SHA-256 fingerprint for a runtime tree.

    The root itself is an explicit directory record.  Every record is length
    prefixed, so path names and file contents cannot create ambiguous stream
    boundaries.
    """

    root = _runtime_root(path)
    digest = hashlib.sha256()
    digest.update(FINGERPRINT_FORMAT)
    for relative, kind, entry_stat, entry_path in _iter_runtime_entries(root):
        _update_field(digest, b"entry")
        _update_field(digest, relative)
        _update_field(digest, kind.encode("ascii"))
        _update_field(
            digest,
            f"{stat.S_IMODE(entry_stat.st_mode):04o}".encode("ascii"),
        )
        if kind == "regular":
            _update_field(digest, str(entry_stat.st_size).encode("ascii"))
            _update_field(digest, _regular_file_digest(entry_path, entry_stat))
        elif kind == "symlink":
            try:
                target = _as_bytes(os.readlink(entry_path))
            except OSError as error:
                raise RuntimeFingerprintError(
                    f"cannot read runtime symlink {_relative_display(relative)}: {error}"
                ) from error
            _safe_relative_symlink_target(relative, target)
            _update_field(digest, target)
    return digest.hexdigest()


def _resolved_inside(root: Path, value: str, *, label: str) -> None:
    if not value:
        raise RuntimeFingerprintError(f"copied runtime has an empty {label}")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise RuntimeFingerprintError(
            f"copied runtime has a non-absolute {label}: {value}"
        )
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise RuntimeFingerprintError(
            f"copied runtime leaks {label} outside the bundle: {value}"
        ) from error


def assert_self_contained_runtime(path: os.PathLike[str] | str) -> None:
    """Confirm a `-I` copied-runtime process has no host Python path entries."""

    root = Path(_runtime_root(path)).resolve(strict=True)
    for label, value in (
        ("sys.executable", sys.executable),
        ("sys.prefix", sys.prefix),
        ("sys.base_prefix", sys.base_prefix),
        ("sys.exec_prefix", sys.exec_prefix),
        ("sys.base_exec_prefix", sys.base_exec_prefix),
    ):
        _resolved_inside(root, value, label=label)
    for value in sys.path:
        _resolved_inside(root, value, label="sys.path entry")


def assert_venv_uses_copied_runtime(
    runtime_root: os.PathLike[str] | str, venv_root: os.PathLike[str] | str
) -> None:
    """Confirm a smoke-test venv retains the copied runtime as its base."""

    runtime = Path(_runtime_root(runtime_root)).resolve(strict=True)
    venv = Path(venv_root).resolve(strict=True)
    for label, value in (
        ("sys.executable", sys.executable),
        ("sys.prefix", sys.prefix),
        ("sys.base_prefix", sys.base_prefix),
        ("sys.exec_prefix", sys.exec_prefix),
        ("sys.base_exec_prefix", sys.base_exec_prefix),
    ):
        candidate = Path(value)
        if not candidate.is_absolute():
            raise RuntimeFingerprintError(f"venv has a non-absolute {label}: {value}")
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(runtime)
        except ValueError:
            try:
                resolved.relative_to(venv)
            except ValueError as error:
                raise RuntimeFingerprintError(
                    f"venv leaks {label} outside the copied runtime: {value}"
                ) from error
    for value in sys.path:
        candidate = Path(value)
        if not candidate.is_absolute():
            raise RuntimeFingerprintError(f"venv has a non-absolute sys.path entry: {value}")
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(runtime)
        except ValueError:
            try:
                resolved.relative_to(venv)
            except ValueError as error:
                raise RuntimeFingerprintError(
                    f"venv leaks sys.path outside the copied runtime: {value}"
                ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--fingerprint", action="store_true")
    parser.add_argument("--check-self-contained", action="store_true")
    parser.add_argument("--check-venv-base", action="store_true")
    parser.add_argument("--venv-root", type=Path)
    args = parser.parse_args(argv)
    if not (args.fingerprint or args.check_self_contained or args.check_venv_base):
        parser.error("select --fingerprint, --check-self-contained, or --check-venv-base")
    if args.check_venv_base and args.venv_root is None:
        parser.error("--check-venv-base requires --venv-root")
    try:
        if args.check_self_contained:
            assert_self_contained_runtime(args.runtime_root)
        if args.check_venv_base:
            assert args.venv_root is not None
            assert_venv_uses_copied_runtime(args.runtime_root, args.venv_root)
        if args.fingerprint:
            print(fingerprint_runtime_tree(args.runtime_root))
    except RuntimeFingerprintError as error:
        print(f"Python runtime fingerprint validation failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
