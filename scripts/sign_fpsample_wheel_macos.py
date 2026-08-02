#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    for member in members:
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe wheel member: {member.filename}")
    return members


def _record_digest(path: Path) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest()).rstrip(b"=")
    return "sha256=" + encoded.decode("ascii")


def _update_record(root: Path, binary: Path) -> None:
    records = list(root.glob("fpsample-1.0.2.dist-info/RECORD"))
    if len(records) != 1:
        raise ValueError("fpsample wheel must contain exactly one RECORD")
    record = records[0]
    binary_name = binary.relative_to(root).as_posix()
    record_name = record.relative_to(root).as_posix()
    with record.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    found = False
    for row in rows:
        if row[0] == binary_name:
            row[1] = _record_digest(binary)
            row[2] = str(binary.stat().st_size)
            found = True
        elif row[0] == record_name:
            row[1:] = ["", ""]
    if not found:
        raise ValueError(f"binary is missing from wheel RECORD: {binary_name}")
    with record.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle, lineterminator="\n").writerows(rows)


def _repack(root: Path, output: Path) -> None:
    temporary = output.with_suffix(output.suffix + ".signed")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(2024, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    os.replace(temporary, output)


def sign_wheel(wheel: Path, identity: str) -> None:
    wheel = wheel.resolve()
    with tempfile.TemporaryDirectory(prefix="fpsample-wheel-sign.") as temp:
        root = Path(temp) / "wheel"
        root.mkdir()
        with zipfile.ZipFile(wheel) as archive:
            _safe_members(archive)
            archive.extractall(root)
        binaries = list(root.glob("fpsample/_fpsample.cpython-312-darwin.so"))
        if len(binaries) != 1:
            raise ValueError("fpsample wheel must contain exactly one CPython 3.12 extension")
        binary = binaries[0]
        binary.chmod(binary.stat().st_mode | 0o200)
        subprocess.run(
            [
                "codesign",
                "--force",
                "--timestamp",
                "--options",
                "runtime",
                "--sign",
                identity,
                str(binary),
            ],
            check=True,
        )
        subprocess.run(["codesign", "--verify", "--strict", "--verbose=2", str(binary)], check=True)
        _update_record(root, binary)
        _repack(root, wheel)

    with tempfile.TemporaryDirectory(prefix="fpsample-wheel-verify.") as temp:
        verify_root = Path(temp)
        with zipfile.ZipFile(wheel) as archive:
            _safe_members(archive)
            archive.extractall(verify_root)
        binary = verify_root / "fpsample" / "_fpsample.cpython-312-darwin.so"
        subprocess.run(["codesign", "--verify", "--strict", "--verbose=2", str(binary)], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Developer ID-sign the native extension inside an fpsample wheel.")
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--identity", required=True)
    args = parser.parse_args()
    sign_wheel(args.wheel, args.identity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
