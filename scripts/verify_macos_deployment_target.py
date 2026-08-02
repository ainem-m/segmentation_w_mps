#!/usr/bin/env python3
"""Verify packaged Mach-O architecture and macOS deployment targets.

This parser intentionally uses only the Python standard library.  Release
verification must not depend on the host's ``otool`` output format, nor may it
silently accept a recognized but malformed Mach-O file.
"""

from __future__ import annotations

import argparse
import struct
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


CPU_TYPE_ARM64 = 0x0100000C
LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
PLATFORM_MACOS = 1
MAX_FAT_SLICES = 64
NATIVE_SUFFIXES = (".dylib", ".so")

_THIN_MAGICS: dict[bytes, tuple[str, bool]] = {
    b"\xce\xfa\xed\xfe": ("<", False),
    b"\xcf\xfa\xed\xfe": ("<", True),
    b"\xfe\xed\xfa\xce": (">", False),
    b"\xfe\xed\xfa\xcf": (">", True),
}
_FAT_MAGICS: dict[bytes, tuple[str, bool]] = {
    b"\xca\xfe\xba\xbe": (">", False),
    b"\xbe\xba\xfe\xca": ("<", False),
    b"\xca\xfe\xba\xbf": (">", True),
    b"\xbf\xba\xfe\xca": ("<", True),
}
_RECOGNIZED_MAGICS = frozenset((*_THIN_MAGICS, *_FAT_MAGICS))


class MachODeploymentTargetError(RuntimeError):
    """Raised when a packaged native binary violates the release contract."""


@dataclass(frozen=True, order=True)
class MacOSVersion:
    major: int
    minor: int = 0
    patch: int = 0

    @classmethod
    def parse(cls, value: str) -> "MacOSVersion":
        parts = value.split(".")
        if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
            raise ValueError(f"invalid macOS version: {value!r}")
        numbers = [int(part) for part in parts]
        numbers.extend([0] * (3 - len(numbers)))
        if any(number < 0 or number > 0xFFFF for number in numbers):
            raise ValueError(f"invalid macOS version: {value!r}")
        return cls(*numbers)

    @classmethod
    def from_packed(cls, value: int) -> "MacOSVersion":
        return cls((value >> 16) & 0xFFFF, (value >> 8) & 0xFF, value & 0xFF)

    def __str__(self) -> str:
        if self.patch:
            return f"{self.major}.{self.minor}.{self.patch}"
        return f"{self.major}.{self.minor}"


@dataclass(frozen=True)
class MachOSlice:
    cputype: int
    minimum_macos: MacOSVersion
    command: str

    @property
    def architecture(self) -> str:
        if self.cputype == CPU_TYPE_ARM64:
            return "arm64"
        if self.cputype == 0x01000007:
            return "x86_64"
        return f"cputype=0x{self.cputype & 0xFFFFFFFF:08x}"


@dataclass(frozen=True)
class VerifiedMachO:
    label: str
    slices: tuple[MachOSlice, ...]


def _need(data: bytes, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise MachODeploymentTargetError(
            f"{label}: truncated or out-of-bounds Mach-O structure at byte {offset}"
        )


def is_macho_bytes(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] in _RECOGNIZED_MAGICS


def _parse_thin(data: bytes, label: str) -> MachOSlice:
    configuration = _THIN_MAGICS.get(data[:4])
    if configuration is None:
        raise MachODeploymentTargetError(f"{label}: expected a thin Mach-O slice")
    endian, is_64 = configuration
    header_size = 32 if is_64 else 28
    _need(data, 0, header_size, label)
    header_format = endian + ("IiiIIIII" if is_64 else "IiiIIII")
    header = struct.unpack_from(header_format, data, 0)
    cputype = header[1] & 0xFFFFFFFF
    ncmds = header[4]
    sizeofcmds = header[5]
    if ncmds > 100_000:
        raise MachODeploymentTargetError(f"{label}: unreasonable load-command count {ncmds}")
    _need(data, header_size, sizeofcmds, label)

    command_offset = header_size
    command_end = header_size + sizeofcmds
    targets: list[tuple[MacOSVersion, str]] = []
    for index in range(ncmds):
        _need(data, command_offset, 8, label)
        cmd, cmdsize = struct.unpack_from(endian + "II", data, command_offset)
        if cmdsize < 8 or cmdsize % 4 != 0:
            raise MachODeploymentTargetError(
                f"{label}: invalid load command {index} size {cmdsize}"
            )
        if command_offset + cmdsize > command_end:
            raise MachODeploymentTargetError(
                f"{label}: load command {index} extends beyond sizeofcmds"
            )
        if cmd == LC_BUILD_VERSION:
            if cmdsize < 24:
                raise MachODeploymentTargetError(
                    f"{label}: truncated LC_BUILD_VERSION command"
                )
            _, _, platform, minos, _, ntools = struct.unpack_from(
                endian + "IIIIII", data, command_offset
            )
            required_size = 24 + ntools * 8
            if required_size > cmdsize:
                raise MachODeploymentTargetError(
                    f"{label}: LC_BUILD_VERSION tool records exceed cmdsize"
                )
            if platform == PLATFORM_MACOS:
                targets.append((MacOSVersion.from_packed(minos), "LC_BUILD_VERSION"))
        elif cmd == LC_VERSION_MIN_MACOSX:
            if cmdsize < 16:
                raise MachODeploymentTargetError(
                    f"{label}: truncated LC_VERSION_MIN_MACOSX command"
                )
            _, _, version, _ = struct.unpack_from(endian + "IIII", data, command_offset)
            targets.append(
                (MacOSVersion.from_packed(version), "LC_VERSION_MIN_MACOSX")
            )
        command_offset += cmdsize

    if command_offset != command_end:
        raise MachODeploymentTargetError(
            f"{label}: load-command sizes do not equal Mach-O sizeofcmds"
        )
    if not targets:
        raise MachODeploymentTargetError(
            f"{label}: Mach-O slice has no macOS minimum-version load command"
        )
    versions = {item[0] for item in targets}
    if len(versions) != 1:
        rendered = ", ".join(f"{command}={version}" for version, command in targets)
        raise MachODeploymentTargetError(
            f"{label}: conflicting macOS deployment targets ({rendered})"
        )
    version, command = targets[0]
    return MachOSlice(cputype=cputype, minimum_macos=version, command=command)


def parse_macho(data: bytes, label: str = "Mach-O") -> tuple[MachOSlice, ...]:
    if len(data) < 4 or data[:4] not in _RECOGNIZED_MAGICS:
        raise MachODeploymentTargetError(f"{label}: not a recognized Mach-O file")
    if data[:4] in _THIN_MAGICS:
        return (_parse_thin(data, label),)

    endian, is_64 = _FAT_MAGICS[data[:4]]
    _need(data, 0, 8, label)
    _, slice_count = struct.unpack_from(endian + "II", data, 0)
    if not 1 <= slice_count <= MAX_FAT_SLICES:
        raise MachODeploymentTargetError(
            f"{label}: invalid fat Mach-O slice count {slice_count}"
        )
    entry_size = 32 if is_64 else 20
    header_size = 8 + slice_count * entry_size
    _need(data, 0, header_size, label)
    ranges: list[tuple[int, int]] = []
    slices: list[MachOSlice] = []
    for index in range(slice_count):
        offset = 8 + index * entry_size
        if is_64:
            cputype, _, slice_offset, slice_size, align, reserved = struct.unpack_from(
                endian + "iiQQII", data, offset
            )
            if reserved != 0:
                raise MachODeploymentTargetError(
                    f"{label}: fat64 slice {index} has non-zero reserved field"
                )
        else:
            cputype, _, slice_offset, slice_size, align = struct.unpack_from(
                endian + "iiIII", data, offset
            )
        cputype &= 0xFFFFFFFF
        if align > 31:
            raise MachODeploymentTargetError(
                f"{label}: fat slice {index} has unreasonable alignment exponent {align}"
            )
        if slice_size == 0 or slice_offset < header_size:
            raise MachODeploymentTargetError(
                f"{label}: invalid fat slice {index} offset/size"
            )
        _need(data, slice_offset, slice_size, label)
        slice_range = (slice_offset, slice_offset + slice_size)
        if any(slice_range[0] < end and start < slice_range[1] for start, end in ranges):
            raise MachODeploymentTargetError(f"{label}: overlapping fat Mach-O slices")
        ranges.append(slice_range)
        payload = data[slice_offset : slice_offset + slice_size]
        if payload[:4] not in _THIN_MAGICS:
            raise MachODeploymentTargetError(
                f"{label}: fat slice {index} is not a thin Mach-O"
            )
        parsed = _parse_thin(payload, f"{label} [fat slice {index}]")
        if parsed.cputype != cputype:
            raise MachODeploymentTargetError(
                f"{label}: fat slice {index} architecture disagrees with inner header"
            )
        slices.append(parsed)
    return tuple(slices)


def verify_macho_bytes(
    data: bytes,
    label: str,
    *,
    maximum_macos: MacOSVersion | str = MacOSVersion(14, 0),
    require_arm64: bool = True,
) -> VerifiedMachO:
    maximum = (
        MacOSVersion.parse(maximum_macos)
        if isinstance(maximum_macos, str)
        else maximum_macos
    )
    slices = parse_macho(data, label)
    errors = [
        f"{item.architecture} minos {item.minimum_macos} exceeds supported macOS {maximum}"
        for item in slices
        if item.minimum_macos > maximum
    ]
    if require_arm64 and not any(item.cputype == CPU_TYPE_ARM64 for item in slices):
        errors.append("arm64 slice is missing")
    if errors:
        raise MachODeploymentTargetError(f"{label}: " + "; ".join(errors))
    return VerifiedMachO(label=label, slices=slices)


def verify_macho_file(
    path: Path,
    *,
    maximum_macos: MacOSVersion | str = MacOSVersion(14, 0),
    require_arm64: bool = True,
) -> VerifiedMachO:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise MachODeploymentTargetError(f"{path}: could not read file: {exc}") from exc
    return verify_macho_bytes(
        data,
        str(path),
        maximum_macos=maximum_macos,
        require_arm64=require_arm64,
    )


def _native_suffix(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(NATIVE_SUFFIXES)


def scan_directory(
    root: Path,
    *,
    maximum_macos: MacOSVersion | str = MacOSVersion(14, 0),
    require_arm64: bool = True,
    exclude_paths: Iterable[Path] = (),
) -> list[VerifiedMachO]:
    if not root.is_dir():
        raise MachODeploymentTargetError(f"{root}: directory is missing")
    results: list[VerifiedMachO] = []
    errors: list[str] = []
    excluded = {path.resolve() for path in exclude_paths}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if path.resolve() in excluded:
                continue
        except OSError:
            pass
        try:
            with path.open("rb") as handle:
                prefix = handle.read(4)
        except OSError as exc:
            errors.append(f"{path}: could not read file: {exc}")
            continue
        if prefix in _RECOGNIZED_MAGICS:
            try:
                results.append(
                    verify_macho_file(
                        path,
                        maximum_macos=maximum_macos,
                        require_arm64=require_arm64,
                    )
                )
            except MachODeploymentTargetError as exc:
                errors.append(str(exc))
        elif _native_suffix(path.name):
            errors.append(
                f"{path}: packaged native-library suffix is not a recognized Mach-O"
            )
    if errors:
        raise MachODeploymentTargetError("\n".join(errors))
    return results


def verify_wheel_machos(
    wheel: Path,
    *,
    maximum_macos: MacOSVersion | str = MacOSVersion(14, 0),
    require_arm64: bool = True,
) -> list[VerifiedMachO]:
    if not wheel.is_file():
        raise MachODeploymentTargetError(f"{wheel}: wheel is missing")
    results: list[VerifiedMachO] = []
    try:
        with zipfile.ZipFile(wheel) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                label = f"{wheel}!/{info.filename}"
                with archive.open(info) as handle:
                    prefix = handle.read(4)
                    native = prefix in _RECOGNIZED_MAGICS
                    if not native and not _native_suffix(info.filename):
                        continue
                    remainder = handle.read()
                data = prefix + remainder
                if not native:
                    raise MachODeploymentTargetError(
                        f"{label}: packaged native-library suffix is not a recognized Mach-O"
                    )
                results.append(
                    verify_macho_bytes(
                        data,
                        label,
                        maximum_macos=maximum_macos,
                        require_arm64=require_arm64,
                    )
                )
    except MachODeploymentTargetError:
        raise
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile) as exc:
        raise MachODeploymentTargetError(f"{wheel}: invalid wheel ZIP: {exc}") from exc
    return results


def verify_app_machos(
    app: Path,
    *,
    maximum_macos: MacOSVersion | str = MacOSVersion(14, 0),
    require_arm64: bool = True,
) -> list[VerifiedMachO]:
    contents = app / "Contents"
    resources = contents / "Resources"
    required = (
        contents / "MacOS" / "TotalSegmentatorWrapperForMac",
        resources / "bin" / "totalsegmentator-wrapper-dicom-normalizer",
        resources / "bin" / "dcm2niix",
    )
    results: list[VerifiedMachO] = []
    errors: list[str] = []
    for path in required:
        if not path.is_file() or path.is_symlink():
            errors.append(f"{path}: required native executable is missing")
            continue
        try:
            results.append(
                verify_macho_file(
                    path,
                    maximum_macos=maximum_macos,
                    require_arm64=require_arm64,
                )
            )
        except MachODeploymentTargetError as exc:
            errors.append(str(exc))

    try:
        results.extend(
            scan_directory(
                app,
                maximum_macos=maximum_macos,
                require_arm64=require_arm64,
                exclude_paths=required,
            )
        )
    except MachODeploymentTargetError as exc:
        errors.append(str(exc))
    for wheel in sorted((resources / "wheels").glob("*.whl")):
        try:
            results.extend(
                verify_wheel_machos(
                    wheel,
                    maximum_macos=maximum_macos,
                    require_arm64=require_arm64,
                )
            )
        except MachODeploymentTargetError as exc:
            errors.append(str(exc))
    if errors:
        raise MachODeploymentTargetError("\n".join(errors))
    return results


def _render(result: VerifiedMachO) -> str:
    details = ", ".join(
        f"{item.architecture} minos={item.minimum_macos} ({item.command})"
        for item in result.slices
    )
    return f"PASS {result.label}: {details}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify packaged Mach-O files support the declared macOS floor."
    )
    parser.add_argument("--path", action="append", type=Path, default=[])
    parser.add_argument("--directory", action="append", type=Path, default=[])
    parser.add_argument("--wheel", action="append", type=Path, default=[])
    parser.add_argument("--app", action="append", type=Path, default=[])
    parser.add_argument("--max-macos", default="14.0")
    parser.add_argument("--require-arm64", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not any((args.path, args.directory, args.wheel, args.app)):
        raise MachODeploymentTargetError(
            "at least one of --path, --directory, --wheel, or --app is required"
        )
    maximum = MacOSVersion.parse(args.max_macos)
    results: list[VerifiedMachO] = []
    errors: list[str] = []

    def collect(operation: object) -> None:
        try:
            value = operation()  # type: ignore[operator]
            if isinstance(value, list):
                results.extend(value)
            else:
                results.append(value)
        except MachODeploymentTargetError as exc:
            errors.append(str(exc))

    for path in args.path:
        collect(
            lambda path=path: verify_macho_file(
                path.expanduser().resolve(),
                maximum_macos=maximum,
                require_arm64=args.require_arm64,
            )
        )
    for directory in args.directory:
        collect(
            lambda directory=directory: scan_directory(
                directory.expanduser().resolve(),
                maximum_macos=maximum,
                require_arm64=args.require_arm64,
            )
        )
    for wheel in args.wheel:
        collect(
            lambda wheel=wheel: verify_wheel_machos(
                wheel.expanduser().resolve(),
                maximum_macos=maximum,
                require_arm64=args.require_arm64,
            )
        )
    for app in args.app:
        collect(
            lambda app=app: verify_app_machos(
                app.expanduser().resolve(),
                maximum_macos=maximum,
                require_arm64=args.require_arm64,
            )
        )
    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        return 1
    if not results:
        print("FAIL no Mach-O files were found", file=sys.stderr)
        return 1
    for result in results:
        print(_render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
