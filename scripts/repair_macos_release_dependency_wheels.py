#!/usr/bin/env python3
"""Rewrite the pinned Open3D macOS wheel for the 0.4.1 release.

This is intentionally not a general wheel relocation tool. It accepts only the
reviewed Open3D 0.19.0 wheel bytes declared below, removes the optional ML ops
that MeshSegNet does not use, makes the retained runtime self-contained,
regenerates RECORD, and applies the release linkage/deployment validators.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

try:
    from scripts.verify_macos_binary_linkage import (
        MACHO_MAGICS,
        MacOSBinaryLinkageError,
        _run_otool,
        parse_otool_dyld_load_commands,
        parse_otool_install_name,
        parse_otool_libraries,
        verify_wheel_self_contained_macos_linkage,
    )
    from scripts.verify_macos_deployment_target import verify_wheel_machos
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from verify_macos_binary_linkage import (  # type: ignore[no-redef]
        MACHO_MAGICS,
        MacOSBinaryLinkageError,
        _run_otool,
        parse_otool_dyld_load_commands,
        parse_otool_install_name,
        parse_otool_libraries,
        verify_wheel_self_contained_macos_linkage,
    )
    from verify_macos_deployment_target import (  # type: ignore[no-redef]
        verify_wheel_machos,
    )


ROOT = Path(__file__).resolve().parents[1]
REPAIR_SCHEMA = "totalsegmentator_wrapper_mac.open3d_wheel_rewrite.v1"
REPAIR_POLICY = "0.4.1-exact-open3d-0.19.0-v1"
SIGNED_REPAIR_SCHEMA = "totalsegmentator_wrapper_mac.open3d_wheel_developer_id.v1"
SIGNED_REPAIR_POLICY = "0.4.1-exact-open3d-0.19.0-developer-id-v1"
RELEASE_TEAM_IDENTIFIER = "8632JF4773"
FIXED_ZIP_TIMESTAMP = (2024, 1, 1, 0, 0, 0)
INSTALL_NAME_TOOL = Path("/usr/bin/install_name_tool")
CODESIGN = Path("/usr/bin/codesign")
MAX_ARCHIVE_MEMBERS = 50_000
MAX_ARCHIVE_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 4 * 1024 * 1024 * 1024

OPEN3D_FILENAME = "open3d-0.19.0-cp312-cp312-macosx_10_15_universal2.whl"
OPEN3D_SHA256 = (
    "9e4a8d29443ba4c83010d199d56c96bf553dd970d3351692ab271759cbe2d7ac"
)
OPEN3D_REPAIRED_SHA256 = (
    "b71b3ffd13427a01a6d1caab8af98d6dc9d1eb3c60ce2b32cbe4ce602168153d"
)
OPEN3D_DIST_INFO = "open3d-0.19.0.dist-info"
OPEN3D_REMOVED_MEMBERS = (
    "open3d/cpu/open3d_tf_ops.dylib",
    "open3d/cpu/open3d_torch_ops.dylib",
)
OPEN3D_RETAINED_MACHOS = (
    "open3d/cpu/pybind.cpython-312-darwin.so",
    "open3d/libomp.dylib",
    "open3d/libtbb.12.dylib",
)
OPEN3D_BUILD_CONFIG_REPLACEMENTS: Mapping[str, str] = {
    '"BUILD_TENSORFLOW_OPS" : True': '"BUILD_TENSORFLOW_OPS" : False',
    '"BUILD_PYTORCH_OPS" : True': '"BUILD_PYTORCH_OPS" : False',
    '"Tensorflow_VERSION" : "2.16.2"': '"Tensorflow_VERSION" : ""',
    '"Pytorch_VERSION" : "2.2.2"': '"Pytorch_VERSION" : ""',
}


class WheelRepairError(RuntimeError):
    """The exact reviewed wheel repair could not be reproduced safely."""


@dataclass(frozen=True)
class WheelSpec:
    distribution: str
    filename: str
    sha256: str
    repaired_sha256: str
    dist_info: str


@dataclass(frozen=True)
class MachOMetadata:
    install_name: str | None
    non_system_dependencies: tuple[str, ...]
    rpaths: tuple[str, ...]


@dataclass(frozen=True)
class RepairResult:
    distribution: str
    input_filename: str
    input_sha256: str
    output_filename: str
    output_sha256: str
    output_size_bytes: int
    macho_count: int
    operations: Mapping[str, object]


Runner = Callable[..., subprocess.CompletedProcess[str]]

OPEN3D_SPEC = WheelSpec(
    "open3d",
    OPEN3D_FILENAME,
    OPEN3D_SHA256,
    OPEN3D_REPAIRED_SHA256,
    OPEN3D_DIST_INFO,
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_non_symlink(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise WheelRepairError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise WheelRepairError(
            f"{label} must be a regular non-symlink file: {path}"
        )


def _safe_member_path(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise WheelRepairError(f"wheel contains an unsafe member name: {name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or str(path) != name.rstrip("/")
        or any(part in ("", ".", "..") or ":" in part for part in path.parts)
    ):
        raise WheelRepairError(f"wheel contains an unsafe member path: {name}")
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode) or file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise WheelRepairError(f"wheel contains a non-regular member: {name}")
    if info.flag_bits & 0x1:
        raise WheelRepairError(f"wheel contains an encrypted member: {name}")
    if info.file_size < 0 or info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
        raise WheelRepairError(f"wheel member exceeds the size limit: {name}")
    return path


def _extract_wheel(wheel: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise WheelRepairError(f"wheel extraction destination must be absent: {destination}")
    destination.mkdir(parents=True, mode=0o700)
    try:
        archive_context = zipfile.ZipFile(wheel)
    except (OSError, zipfile.BadZipFile) as exc:
        raise WheelRepairError(f"wheel is not a valid ZIP archive: {wheel}") from exc
    with archive_context as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise WheelRepairError("wheel contains too many archive members")
        seen: set[PurePosixPath] = set()
        total_size = 0
        validated: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        for info in infos:
            relative = _safe_member_path(info)
            if relative in seen:
                raise WheelRepairError(
                    f"wheel contains a duplicate member path: {info.filename}"
                )
            seen.add(relative)
            total_size += info.file_size
            if total_size > MAX_ARCHIVE_TOTAL_BYTES:
                raise WheelRepairError("wheel members exceed the total size limit")
            validated.append((info, relative))
        bad_member = archive.testzip()
        if bad_member is not None:
            raise WheelRepairError(f"wheel member failed CRC validation: {bad_member}")
        for info, relative in validated:
            target = destination.joinpath(*relative.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=False, mode=0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            mode = (info.external_attr >> 16) & 0o777
            output_mode = 0o755 if mode & 0o111 else 0o644
            try:
                with archive.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                target.chmod(output_mode)
            except OSError as exc:
                raise WheelRepairError(
                    f"could not safely extract wheel member: {info.filename}"
                ) from exc


def _macho_paths(root: Path) -> tuple[Path, ...]:
    machos: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            with path.open("rb") as handle:
                prefix = handle.read(4)
        except OSError as exc:
            raise WheelRepairError(f"could not inspect extracted member: {path}") from exc
        if prefix in MACHO_MAGICS:
            machos.append(path)
    return tuple(machos)


def _is_system_dependency(value: str) -> bool:
    return value.startswith("/System/Library/") or value.startswith("/usr/lib/")


def _macho_metadata(path: Path, *, runner: Runner = subprocess.run) -> MachOMetadata:
    try:
        dependencies = list(
            parse_otool_libraries(_run_otool(path, ["-L"], runner=runner))
        )
        install_name = parse_otool_install_name(
            _run_otool(path, ["-D"], runner=runner)
        )
        if install_name is not None:
            if dependencies.count(install_name) != 1:
                raise WheelRepairError(
                    f"LC_ID_DYLIB is not represented exactly once: {path}"
                )
            dependencies.remove(install_name)
        commands = parse_otool_dyld_load_commands(
            _run_otool(path, ["-l"], runner=runner)
        )
    except (MacOSBinaryLinkageError, OSError, RuntimeError) as exc:
        raise WheelRepairError(f"could not inspect Mach-O metadata: {path}: {exc}") from exc
    return MachOMetadata(
        install_name=install_name,
        non_system_dependencies=tuple(
            dependency
            for dependency in dependencies
            if not _is_system_dependency(dependency)
        ),
        rpaths=commands.rpaths,
    )


def _run_install_name_tool(
    arguments: Sequence[str],
    path: Path,
    *,
    runner: Runner = subprocess.run,
) -> None:
    _require_regular_non_symlink(INSTALL_NAME_TOOL, "install_name_tool")
    try:
        completed = runner(
            [str(INSTALL_NAME_TOOL), *arguments, str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise WheelRepairError(f"could not execute install_name_tool: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise WheelRepairError(
            f"install_name_tool {' '.join(arguments)} failed for {path}: {detail}"
        )


def _verify_ad_hoc_signature(
    path: Path,
    *,
    runner: Runner = subprocess.run,
) -> None:
    _require_regular_non_symlink(CODESIGN, "codesign")
    try:
        completed = runner(
            [str(CODESIGN), "--verify", "--strict", "--verbose=2", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise WheelRepairError(f"could not execute codesign verification: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise WheelRepairError(f"Mach-O signature verification failed: {path}: {detail}")


def _ad_hoc_sign(
    path: Path,
    *,
    runner: Runner = subprocess.run,
) -> None:
    _require_regular_non_symlink(CODESIGN, "codesign")
    try:
        completed = runner(
            [
                str(CODESIGN),
                "--force",
                "--sign",
                "-",
                "--timestamp=none",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise WheelRepairError(f"could not execute ad-hoc codesign: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise WheelRepairError(f"ad-hoc codesign failed: {path}: {detail}")
    _verify_ad_hoc_signature(path, runner=runner)


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256_file(path)
        for path in sorted(item for item in root.rglob("*") if item.is_file())
        if not path.is_symlink()
    }


def _assert_exact_tree_changes(
    before: Mapping[str, str],
    after: Mapping[str, str],
    *,
    changed: set[str],
    removed: set[str] | frozenset[str] = frozenset(),
) -> None:
    expected_after = set(before) - removed
    if set(after) != expected_after:
        raise WheelRepairError(
            "wheel member inventory changed outside the exact repair allowlist: "
            f"missing={sorted(set(before) - set(after))!r}, "
            f"added={sorted(set(after) - set(before))!r}"
        )
    observed_changed = {
        name for name in expected_after if before[name] != after[name]
    }
    if observed_changed != changed:
        raise WheelRepairError(
            "wheel bytes changed outside the exact repair allowlist: "
            f"expected {sorted(changed)!r}, found {sorted(observed_changed)!r}"
        )


def _assert_open3d_preconditions(
    root: Path,
    *,
    runner: Runner = subprocess.run,
) -> None:
    expected: Mapping[str, MachOMetadata] = {
        "open3d/cpu/open3d_tf_ops.dylib": MachOMetadata(
            "@rpath/open3d_tf_ops.dylib",
            (
                "@rpath/libtensorflow_framework.2.dylib",
                "@rpath/libtbb.12.dylib",
                "/opt/homebrew/opt/libomp/lib/libomp.dylib",
            ),
            (
                "@loader_path/..",
                "/Library/Frameworks/Python.framework/Versions/3.12/"
                "lib/python3.12/site-packages/tensorflow",
                "/Users/runner/work/Open3D/Open3D/"
                "build/appleclang_15.0_cxx17_64_release",
            ),
        ),
        "open3d/cpu/open3d_torch_ops.dylib": MachOMetadata(
            "@rpath/open3d_torch_ops.dylib",
            (
                "@rpath/libtorch_cpu.dylib",
                "@rpath/libtbb.12.dylib",
                "@rpath/libc10.dylib",
                "/opt/homebrew/opt/libomp/lib/libomp.dylib",
            ),
            (
                "@loader_path/..",
                "/Library/Frameworks/Python.framework/Versions/3.12/"
                "lib/python3.12/site-packages/torch/lib",
                "/Users/runner/work/Open3D/Open3D/"
                "build/appleclang_15.0_cxx17_64_release",
            ),
        ),
        "open3d/cpu/pybind.cpython-312-darwin.so": MachOMetadata(
            None,
            ("@rpath/libtbb.12.dylib", "@rpath/libomp.dylib"),
            (
                "@loader_path",
                "@loader_path/..",
                "/Users/runner/work/Open3D/Open3D/"
                "build/appleclang_15.0_cxx17_64_release",
            ),
        ),
        "open3d/libomp.dylib": MachOMetadata(
            "/opt/homebrew/opt/libomp/lib/libomp.dylib",
            (),
            (),
        ),
        "open3d/libtbb.12.dylib": MachOMetadata(
            "@rpath/libtbb.12.dylib",
            (),
            ("@loader_path/../",),
        ),
    }
    machos = _macho_paths(root)
    observed_paths = {path.relative_to(root).as_posix() for path in machos}
    if observed_paths != set(expected):
        raise WheelRepairError(
            "Open3D Mach-O inventory changed: "
            f"expected {sorted(expected)!r}, found {sorted(observed_paths)!r}"
        )
    for relative, expected_metadata in expected.items():
        path = root.joinpath(*PurePosixPath(relative).parts)
        observed = _macho_metadata(path, runner=runner)
        if observed != expected_metadata:
            raise WheelRepairError(
                f"Open3D Mach-O metadata changed: {relative}: "
                f"expected {expected_metadata!r}, found {observed!r}"
            )


def _patch_open3d_build_config(root: Path) -> str:
    config = root / "open3d" / "_build_config.py"
    _require_regular_non_symlink(config, "Open3D build configuration")
    try:
        text = config.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise WheelRepairError("could not read Open3D build configuration") from exc
    for before, after in OPEN3D_BUILD_CONFIG_REPLACEMENTS.items():
        if text.count(before) != 1 or after in text:
            raise WheelRepairError(
                f"Open3D build configuration precondition changed: {before}"
            )
        text = text.replace(before, after, 1)
    try:
        config.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise WheelRepairError("could not update Open3D build configuration") from exc
    return config.relative_to(root).as_posix()


def _repair_open3d(
    root: Path,
    removed_root: Path,
    *,
    runner: Runner = subprocess.run,
) -> Mapping[str, object]:
    _assert_open3d_preconditions(root, runner=runner)
    original_machos = _macho_paths(root)
    for macho in original_machos:
        _verify_ad_hoc_signature(macho, runner=runner)
    before = _file_hashes(root)
    config_member = _patch_open3d_build_config(root)
    removed_root.mkdir(parents=True, mode=0o700)
    for relative in OPEN3D_REMOVED_MEMBERS:
        source = root.joinpath(*PurePosixPath(relative).parts)
        _require_regular_non_symlink(source, "Open3D optional op")
        target = removed_root.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.exists() or target.is_symlink():
            raise WheelRepairError(f"Open3D removed-member target already exists: {target}")
        source.rename(target)

    pybind = root / "open3d" / "cpu" / "pybind.cpython-312-darwin.so"
    _run_install_name_tool(
        [
            "-delete_rpath",
            "/Users/runner/work/Open3D/Open3D/"
            "build/appleclang_15.0_cxx17_64_release",
        ],
        pybind,
        runner=runner,
    )

    libomp = root / "open3d" / "libomp.dylib"
    _run_install_name_tool(
        ["-id", "@rpath/libomp.dylib"],
        libomp,
        runner=runner,
    )
    libtbb = root / "open3d" / "libtbb.12.dylib"
    _run_install_name_tool(
        ["-delete_rpath", "@loader_path/../"],
        libtbb,
        runner=runner,
    )
    for macho in (pybind, libomp, libtbb):
        _ad_hoc_sign(macho, runner=runner)
    retained = tuple(path.relative_to(root).as_posix() for path in _macho_paths(root))
    if retained != OPEN3D_RETAINED_MACHOS:
        raise WheelRepairError(
            "Open3D retained Mach-O inventory differs after repair: "
            f"expected {OPEN3D_RETAINED_MACHOS!r}, found {retained!r}"
        )
    for macho in _macho_paths(root):
        _verify_ad_hoc_signature(macho, runner=runner)
    changed = {config_member, *OPEN3D_RETAINED_MACHOS}
    _assert_exact_tree_changes(
        before,
        _file_hashes(root),
        changed=changed,
        removed=set(OPEN3D_REMOVED_MEMBERS),
    )
    return {
        "lc_id_rewrites": 1,
        "dependency_rewrites": 0,
        "rpaths_deleted": 2,
        "ad_hoc_signatures": 3,
        "codesign_identity": "-",
        "codesign_timestamp": None,
        "removed_members": list(OPEN3D_REMOVED_MEMBERS),
        "metadata_edits": [config_member],
    }


def _record_digest(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
    return "sha256=" + encoded.decode("ascii")


def _regenerate_record(root: Path, expected_dist_info: str) -> str:
    dist_infos = sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and path.name.endswith(".dist-info")
    )
    if dist_infos != [expected_dist_info]:
        raise WheelRepairError(
            f"wheel dist-info inventory changed: expected {[expected_dist_info]!r}, "
            f"found {dist_infos!r}"
        )
    record = root / expected_dist_info / "RECORD"
    _require_regular_non_symlink(record, "wheel RECORD")
    for signature_name in ("RECORD.jws", "RECORD.p7s"):
        signature = record.with_name(signature_name)
        if signature.exists() or signature.is_symlink():
            raise WheelRepairError(
                f"wheel contains a RECORD signature that would become invalid: {signature_name}"
            )
    record_relative = record.relative_to(root).as_posix()
    rows: list[tuple[str, str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise WheelRepairError(f"extracted wheel contains a symlink: {path}")
        relative = path.relative_to(root).as_posix()
        if relative == record_relative:
            continue
        payload = path.read_bytes()
        rows.append((relative, _record_digest(payload), str(len(payload))))
    rows.append((record_relative, "", ""))
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    record.write_text(output.getvalue(), encoding="utf-8", newline="")
    return record_relative


def _repack_wheel(root: Path, output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise WheelRepairError(f"repaired wheel output must be absent: {output}")
    partial = output.with_suffix(output.suffix + ".partial")
    if partial.exists() or partial.is_symlink():
        raise WheelRepairError(f"repaired wheel partial output already exists: {partial}")
    try:
        with zipfile.ZipFile(
            partial,
            "x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                if path.is_symlink():
                    raise WheelRepairError(f"wheel contains a symlink before repack: {path}")
                relative = path.relative_to(root).as_posix()
                mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                info = zipfile.ZipInfo(relative, date_time=FIXED_ZIP_TIMESTAMP)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(
                    info,
                    path.read_bytes(),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
        partial.rename(output)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise WheelRepairError(f"could not deterministically repack wheel: {output}") from exc


def _verify_record(wheel: Path, expected_dist_info: str) -> None:
    try:
        archive_context = zipfile.ZipFile(wheel)
    except (OSError, zipfile.BadZipFile) as exc:
        raise WheelRepairError(f"repaired wheel is not a valid ZIP: {wheel}") from exc
    with archive_context as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise WheelRepairError("repaired wheel contains duplicate member paths")
        record_name = f"{expected_dist_info}/RECORD"
        if names.count(record_name) != 1:
            raise WheelRepairError("repaired wheel RECORD is missing or ambiguous")
        try:
            rows = list(
                csv.reader(
                    io.StringIO(archive.read(record_name).decode("utf-8"), newline="")
                )
            )
        except (UnicodeError, csv.Error, KeyError) as exc:
            raise WheelRepairError("repaired wheel RECORD is invalid") from exc
        by_name: dict[str, tuple[str, str]] = {}
        for row in rows:
            if len(row) != 3 or row[0] in by_name:
                raise WheelRepairError("repaired wheel RECORD has malformed rows")
            by_name[row[0]] = (row[1], row[2])
        if set(by_name) != set(names):
            raise WheelRepairError("repaired wheel RECORD does not cover every member")
        for info in infos:
            digest, size = by_name[info.filename]
            if info.filename == record_name:
                if digest or size:
                    raise WheelRepairError("repaired wheel RECORD self-entry must be empty")
                continue
            payload = archive.read(info)
            if digest != _record_digest(payload) or size != str(len(payload)):
                raise WheelRepairError(
                    f"repaired wheel RECORD integrity mismatch: {info.filename}"
                )


def _validate_input_wheel(path: Path, spec: WheelSpec) -> None:
    _require_regular_non_symlink(path, f"{spec.distribution} input wheel")
    if path.name != spec.filename:
        raise WheelRepairError(
            f"{spec.distribution} input filename mismatch: "
            f"expected {spec.filename}, found {path.name}"
        )
    observed = _sha256_file(path)
    if observed != spec.sha256:
        raise WheelRepairError(
            f"{spec.distribution} input SHA-256 mismatch: "
            f"expected {spec.sha256}, found {observed}"
        )


def _repair_one(
    *,
    input_wheel: Path,
    spec: WheelSpec,
    output_directory: Path,
    work_directory: Path,
    repair: Callable[..., Mapping[str, object]],
    repair_arguments: Mapping[str, object] | None = None,
) -> RepairResult:
    _validate_input_wheel(input_wheel, spec)
    work_root = work_directory / spec.distribution
    _extract_wheel(input_wheel, work_root)
    arguments = dict(repair_arguments or {})
    operations = repair(work_root, **arguments)
    _regenerate_record(work_root, spec.dist_info)
    output_wheel = output_directory / "wheels" / spec.filename
    _repack_wheel(work_root, output_wheel)
    _verify_record(output_wheel, spec.dist_info)
    output_sha256 = _sha256_file(output_wheel)
    if output_sha256 != spec.repaired_sha256:
        raise WheelRepairError(
            f"repaired {spec.distribution} output SHA-256 mismatch: "
            f"expected {spec.repaired_sha256}, found {output_sha256}"
        )
    try:
        linked = verify_wheel_self_contained_macos_linkage(output_wheel)
        deployed = verify_wheel_machos(
            output_wheel,
            maximum_macos="14.0",
            require_arm64=True,
        )
    except (MacOSBinaryLinkageError, OSError, RuntimeError, ValueError) as exc:
        raise WheelRepairError(
            f"repaired {spec.distribution} wheel failed release validation: {exc}"
        ) from exc
    if len(linked) != len(deployed):
        raise WheelRepairError(
            f"repaired {spec.distribution} validator inventories differ: "
            f"linkage={len(linked)}, deployment={len(deployed)}"
        )
    return RepairResult(
        distribution=spec.distribution,
        input_filename=spec.filename,
        input_sha256=spec.sha256,
        output_filename=spec.filename,
        output_sha256=output_sha256,
        output_size_bytes=output_wheel.stat().st_size,
        macho_count=len(linked),
        operations=operations,
    )


def rewrite_open3d_release_wheel(
    *,
    open3d_wheel: Path,
    output_directory: Path,
) -> dict[str, object]:
    output_directory = output_directory.expanduser().absolute()
    if output_directory.exists() or output_directory.is_symlink():
        raise WheelRepairError(
            f"repair output directory must be absent: {output_directory}"
        )
    parent = output_directory.parent
    if not parent.is_dir() or parent.is_symlink():
        raise WheelRepairError(
            f"repair output parent must be a regular directory: {parent}"
        )
    _require_regular_non_symlink(INSTALL_NAME_TOOL, "install_name_tool")
    _require_regular_non_symlink(CODESIGN, "codesign")
    output_directory.mkdir(mode=0o700)
    (output_directory / "wheels").mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(
        prefix=".open3d-wheel-rewrite-", dir=parent
    ) as temporary:
        work_directory = Path(temporary)
        open3d_result = _repair_one(
            input_wheel=open3d_wheel.expanduser().absolute(),
            spec=OPEN3D_SPEC,
            output_directory=output_directory,
            work_directory=work_directory,
            repair=_repair_open3d,
            repair_arguments={
                "removed_root": work_directory / "removed" / "open3d",
            },
        )
    manifest: dict[str, object] = {
        "schema": REPAIR_SCHEMA,
        "policy": REPAIR_POLICY,
        "target": {
            "architecture": "arm64",
            "maximum_macos": "14.0",
            "python": "3.12",
        },
        "wheel": {
            "distribution": open3d_result.distribution,
            "input": {
                "filename": open3d_result.input_filename,
                "sha256": open3d_result.input_sha256,
            },
            "output": {
                "filename": open3d_result.output_filename,
                "sha256": open3d_result.output_sha256,
                "size_bytes": open3d_result.output_size_bytes,
                "macho_count": open3d_result.macho_count,
            },
            "operations": open3d_result.operations,
        },
    }
    manifest_path = output_directory / "repair-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"PASS rewritten Open3D wheel: {manifest_path}")
    print(
        f"PASS {open3d_result.distribution}: {open3d_result.output_filename} "
        f"sha256={open3d_result.output_sha256} "
        f"Mach-O={open3d_result.macho_count}"
    )
    return manifest


def _verify_ad_hoc_rewritten_open3d_wheel(
    output_directory: Path,
) -> dict[str, object]:
    """Read-only verification of a completed Open3D rewrite artifact."""

    output_directory = output_directory.expanduser().absolute()
    if not output_directory.is_dir() or output_directory.is_symlink():
        raise WheelRepairError(
            f"rewrite output must be a regular directory: {output_directory}"
        )
    manifest_path = output_directory / "repair-manifest.json"
    wheel_directory = output_directory / "wheels"
    _require_regular_non_symlink(manifest_path, "Open3D rewrite manifest")
    if not wheel_directory.is_dir() or wheel_directory.is_symlink():
        raise WheelRepairError("Open3D rewrite wheel directory is invalid")
    if {path.name for path in output_directory.iterdir()} != {
        "repair-manifest.json",
        "wheels",
    }:
        raise WheelRepairError("Open3D rewrite output contains unexpected entries")
    wheel = wheel_directory / OPEN3D_FILENAME
    if {path.name for path in wheel_directory.iterdir()} != {OPEN3D_FILENAME}:
        raise WheelRepairError("Open3D rewrite wheel inventory is invalid")
    _require_regular_non_symlink(wheel, "rewritten Open3D wheel")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WheelRepairError("Open3D rewrite manifest is invalid") from exc
    expected_operations = {
        "lc_id_rewrites": 1,
        "dependency_rewrites": 0,
        "rpaths_deleted": 2,
        "ad_hoc_signatures": 3,
        "codesign_identity": "-",
        "codesign_timestamp": None,
        "removed_members": list(OPEN3D_REMOVED_MEMBERS),
        "metadata_edits": ["open3d/_build_config.py"],
    }
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema",
        "policy",
        "target",
        "wheel",
    }:
        raise WheelRepairError("Open3D rewrite manifest field set mismatch")
    entry = manifest.get("wheel")
    if (
        manifest.get("schema") != REPAIR_SCHEMA
        or manifest.get("policy") != REPAIR_POLICY
        or manifest.get("target")
        != {"architecture": "arm64", "maximum_macos": "14.0", "python": "3.12"}
        or not isinstance(entry, dict)
        or entry.get("distribution") != "open3d"
        or entry.get("input")
        != {"filename": OPEN3D_FILENAME, "sha256": OPEN3D_SHA256}
        or entry.get("operations") != expected_operations
    ):
        raise WheelRepairError("Open3D rewrite manifest identity mismatch")
    output = entry.get("output")
    if (
        not isinstance(output, dict)
        or output.get("filename") != OPEN3D_FILENAME
        or output.get("sha256") != OPEN3D_REPAIRED_SHA256
        or output.get("macho_count") != 3
        or output.get("size_bytes") != wheel.stat().st_size
        or _sha256_file(wheel) != OPEN3D_REPAIRED_SHA256
    ):
        raise WheelRepairError("rewritten Open3D wheel identity mismatch")
    _verify_record(wheel, OPEN3D_DIST_INFO)
    linked = verify_wheel_self_contained_macos_linkage(wheel)
    deployed = verify_wheel_machos(
        wheel,
        maximum_macos="14.0",
        require_arm64=True,
    )
    if len(linked) != 3 or len(deployed) != 3:
        raise WheelRepairError("rewritten Open3D Mach-O inventory mismatch")
    return manifest


def _developer_id_signature_details(path: Path, *, team_identifier: str) -> None:
    verification = subprocess.run(
        [str(CODESIGN), "--verify", "--strict", "--verbose=2", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if verification.returncode != 0:
        raise WheelRepairError(
            f"Developer ID signature verification failed: {path}: "
            + (verification.stderr.strip() or verification.stdout.strip())
        )
    details = subprocess.run(
        [str(CODESIGN), "-dv", "--verbose=4", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    signature = details.stdout + details.stderr
    if (
        "Authority=Developer ID Application:" not in signature
        or f"TeamIdentifier={team_identifier}" not in signature
        or "Timestamp=" not in signature
        or "flags=0x10000(runtime)" not in signature
    ):
        raise WheelRepairError(
            f"Open3D Mach-O lacks the required Developer ID/timestamp/runtime identity: {path}"
        )


def _verify_signed_open3d_wheel_payload(
    wheel: Path, *, team_identifier: str
) -> None:
    with tempfile.TemporaryDirectory(prefix=".open3d-wheel-signature-verify-") as temporary:
        root = Path(temporary) / "wheel"
        _extract_wheel(wheel, root)
        machos = tuple(path.relative_to(root).as_posix() for path in _macho_paths(root))
        if machos != OPEN3D_RETAINED_MACHOS:
            raise WheelRepairError(
                "signed Open3D Mach-O inventory differs: "
                f"expected {OPEN3D_RETAINED_MACHOS!r}, found {machos!r}"
            )
        for relative in OPEN3D_RETAINED_MACHOS:
            _developer_id_signature_details(
                root.joinpath(*PurePosixPath(relative).parts),
                team_identifier=team_identifier,
            )


def sign_rewritten_open3d_wheel(
    *,
    rewritten_input_directory: Path,
    output_directory: Path,
    identity: str,
    team_identifier: str,
) -> dict[str, object]:
    if team_identifier != RELEASE_TEAM_IDENTIFIER:
        raise WheelRepairError(
            f"Open3D release signing requires Team ID {RELEASE_TEAM_IDENTIFIER}"
        )
    if not identity.startswith("Developer ID Application:"):
        raise WheelRepairError("Open3D release signing requires a Developer ID Application identity")
    rewritten_input_directory = rewritten_input_directory.expanduser().absolute()
    base_manifest = _verify_ad_hoc_rewritten_open3d_wheel(rewritten_input_directory)
    base_manifest_path = rewritten_input_directory / "repair-manifest.json"
    base_wheel = rewritten_input_directory / "wheels" / OPEN3D_FILENAME
    output_directory = output_directory.expanduser().absolute()
    if output_directory.exists() or output_directory.is_symlink():
        raise WheelRepairError(
            f"signed Open3D output directory must be absent: {output_directory}"
        )
    parent = output_directory.parent
    if not parent.is_dir() or parent.is_symlink():
        raise WheelRepairError(
            f"signed Open3D output parent must be a regular directory: {parent}"
        )
    output_directory.mkdir(mode=0o700)
    (output_directory / "wheels").mkdir(mode=0o700)
    output_wheel = output_directory / "wheels" / OPEN3D_FILENAME
    with tempfile.TemporaryDirectory(
        prefix=".open3d-wheel-developer-id-", dir=parent
    ) as temporary:
        root = Path(temporary) / "wheel"
        _extract_wheel(base_wheel, root)
        machos = tuple(path.relative_to(root).as_posix() for path in _macho_paths(root))
        if machos != OPEN3D_RETAINED_MACHOS:
            raise WheelRepairError("rewritten Open3D Mach-O inventory changed before signing")
        for relative in OPEN3D_RETAINED_MACHOS:
            macho = root.joinpath(*PurePosixPath(relative).parts)
            subprocess.run(
                [
                    str(CODESIGN),
                    "--force",
                    "--timestamp",
                    "--options",
                    "runtime",
                    "--sign",
                    identity,
                    str(macho),
                ],
                check=True,
            )
            _developer_id_signature_details(macho, team_identifier=team_identifier)
        _regenerate_record(root, OPEN3D_DIST_INFO)
        _repack_wheel(root, output_wheel)
    _verify_record(output_wheel, OPEN3D_DIST_INFO)
    linked = verify_wheel_self_contained_macos_linkage(output_wheel)
    deployed = verify_wheel_machos(
        output_wheel,
        maximum_macos="14.0",
        require_arm64=True,
    )
    if len(linked) != 3 or len(deployed) != 3:
        raise WheelRepairError("signed Open3D validator inventories differ")
    _verify_signed_open3d_wheel_payload(
        output_wheel, team_identifier=team_identifier
    )
    output_sha256 = _sha256_file(output_wheel)
    manifest: dict[str, object] = {
        "schema": SIGNED_REPAIR_SCHEMA,
        "policy": SIGNED_REPAIR_POLICY,
        "target": base_manifest["target"],
        "wheel": {
            "distribution": "open3d",
            "input": {
                "filename": OPEN3D_FILENAME,
                "sha256": OPEN3D_REPAIRED_SHA256,
                "repair_manifest_sha256": _sha256_file(base_manifest_path),
            },
            "output": {
                "filename": OPEN3D_FILENAME,
                "sha256": output_sha256,
                "size_bytes": output_wheel.stat().st_size,
                "macho_count": 3,
            },
            "operations": {
                "developer_id_signatures": 3,
                "codesign_identity": "Developer ID Application",
                "codesign_team_identifier": team_identifier,
                "codesign_timestamp": "secure",
                "codesign_options": "runtime",
            },
        },
    }
    manifest_path = output_directory / "repair-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def _verify_developer_id_signed_open3d_wheel(
    output_directory: Path, *, team_identifier: str
) -> dict[str, object]:
    output_directory = output_directory.expanduser().absolute()
    manifest_path = output_directory / "repair-manifest.json"
    wheel_directory = output_directory / "wheels"
    _require_regular_non_symlink(manifest_path, "signed Open3D manifest")
    if not wheel_directory.is_dir() or wheel_directory.is_symlink():
        raise WheelRepairError("signed Open3D wheel directory is invalid")
    if {path.name for path in output_directory.iterdir()} != {
        "repair-manifest.json",
        "wheels",
    } or {path.name for path in wheel_directory.iterdir()} != {OPEN3D_FILENAME}:
        raise WheelRepairError("signed Open3D output inventory is invalid")
    wheel = wheel_directory / OPEN3D_FILENAME
    _require_regular_non_symlink(wheel, "signed Open3D wheel")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WheelRepairError("signed Open3D manifest is invalid") from exc
    entry = manifest.get("wheel") if isinstance(manifest, dict) else None
    output = entry.get("output") if isinstance(entry, dict) else None
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "policy", "target", "wheel"}
        or manifest.get("schema") != SIGNED_REPAIR_SCHEMA
        or manifest.get("policy") != SIGNED_REPAIR_POLICY
        or manifest.get("target")
        != {"architecture": "arm64", "maximum_macos": "14.0", "python": "3.12"}
        or not isinstance(entry, dict)
        or set(entry) != {"distribution", "input", "operations", "output"}
        or entry.get("distribution") != "open3d"
        or entry.get("operations")
        != {
            "developer_id_signatures": 3,
            "codesign_identity": "Developer ID Application",
            "codesign_team_identifier": team_identifier,
            "codesign_timestamp": "secure",
            "codesign_options": "runtime",
        }
        or not isinstance(entry.get("input"), dict)
        or entry["input"].get("filename") != OPEN3D_FILENAME
        or entry["input"].get("sha256") != OPEN3D_REPAIRED_SHA256
        or not isinstance(entry["input"].get("repair_manifest_sha256"), str)
        or not isinstance(output, dict)
        or set(output) != {"filename", "sha256", "size_bytes", "macho_count"}
        or output.get("filename") != OPEN3D_FILENAME
        or output.get("macho_count") != 3
        or type(output.get("size_bytes")) is not int
        or output.get("size_bytes") != wheel.stat().st_size
        or not isinstance(output.get("sha256"), str)
        or output.get("sha256") != _sha256_file(wheel)
    ):
        raise WheelRepairError("signed Open3D manifest identity mismatch")
    _verify_record(wheel, OPEN3D_DIST_INFO)
    linked = verify_wheel_self_contained_macos_linkage(wheel)
    deployed = verify_wheel_machos(
        wheel, maximum_macos="14.0", require_arm64=True
    )
    if len(linked) != 3 or len(deployed) != 3:
        raise WheelRepairError("signed Open3D Mach-O inventory mismatch")
    _verify_signed_open3d_wheel_payload(wheel, team_identifier=team_identifier)
    return manifest


def verify_rewritten_open3d_wheel(
    output_directory: Path,
    *,
    require_developer_id: bool = False,
    team_identifier: str = RELEASE_TEAM_IDENTIFIER,
) -> dict[str, object]:
    manifest_path = output_directory.expanduser().absolute() / "repair-manifest.json"
    try:
        schema = json.loads(manifest_path.read_text(encoding="utf-8")).get("schema")
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
        raise WheelRepairError("Open3D release manifest is invalid") from exc
    if schema == SIGNED_REPAIR_SCHEMA:
        return _verify_developer_id_signed_open3d_wheel(
            output_directory, team_identifier=team_identifier
        )
    if require_developer_id:
        raise WheelRepairError("Developer ID build requires the signed Open3D wheel artifact")
    return _verify_ad_hoc_rewritten_open3d_wheel(output_directory)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rewrite only the SHA-pinned Open3D 0.19.0 macOS wheel approved "
            "for release 0.4.1."
        )
    )
    parser.add_argument("--open3d-wheel", type=Path)
    parser.add_argument("--sign-rewritten-input-directory", type=Path)
    parser.add_argument("--identity")
    parser.add_argument("--team-identifier", default=RELEASE_TEAM_IDENTIFIER)
    parser.add_argument("--require-developer-id", action="store_true")
    parser.add_argument("--verify-existing", action="store_true")
    parser.add_argument("--output-directory", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.verify_existing:
            if args.open3d_wheel is not None or args.sign_rewritten_input_directory is not None:
                raise WheelRepairError(
                    "--verify-existing does not accept wheel production inputs"
                )
            verify_rewritten_open3d_wheel(
                args.output_directory,
                require_developer_id=args.require_developer_id,
                team_identifier=args.team_identifier,
            )
        elif args.sign_rewritten_input_directory is not None:
            if args.open3d_wheel is not None or not args.identity:
                raise WheelRepairError(
                    "Developer ID signing requires --sign-rewritten-input-directory and --identity only"
                )
            sign_rewritten_open3d_wheel(
                rewritten_input_directory=args.sign_rewritten_input_directory,
                output_directory=args.output_directory,
                identity=args.identity,
                team_identifier=args.team_identifier,
            )
        else:
            if args.open3d_wheel is None or args.identity is not None:
                raise WheelRepairError("--open3d-wheel is required for a rewrite")
            rewrite_open3d_release_wheel(
                open3d_wheel=args.open3d_wheel,
                output_directory=args.output_directory,
            )
    except WheelRepairError as exc:
        print(f"dependency wheel repair failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
