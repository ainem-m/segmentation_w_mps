"""Fail-closed release evidence checks runnable from an installed app wheel.

The test-account helper runs after Setup, when only the installed app and its
private virtual environment are available.  Keep this module self-contained:
it must not import a checkout-only ``scripts/`` module or require Xcode command
line tools on the clean test account.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


MINIMUM_MACOS = "14.0"
ARCHITECTURE = "arm64"
SOURCE_DATE_EPOCH = 1_735_689_600
MAX_JSON_BYTES = 1024 * 1024
MAX_WHEEL_NATIVE_MEMBER_BYTES = 1024 * 1024 * 1024
MAX_WHEEL_TOTAL_NATIVE_BYTES = 4 * 1024 * 1024 * 1024

CPU_TYPE_ARM64 = 0x0100000C
LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
PLATFORM_MACOS = 1
LC_LOAD_DYLIB = 0x0C
LC_ID_DYLIB = 0x0D
LC_LOAD_DYLINKER = 0x0E
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001F
LC_LAZY_LOAD_DYLIB = 0x20
LC_LOAD_UPWARD_DYLIB = 0x80000023
LC_RPATH = 0x8000001C
LC_DYLD_ENVIRONMENT = 0x27

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
_NATIVE_SUFFIXES = (".dylib", ".so")
_MAX_FAT_SLICES = 64

GDCM_VERSION = "3.2.7"
GDCM_SOURCE_URL = "https://github.com/malaterre/GDCM/archive/refs/tags/v3.2.7.tar.gz"
GDCM_SOURCE_ARCHIVE_SHA256 = (
    "b7b17b70c009677cf244cc7837b88386441e097f8861fdeee83aa27d1bc1b090"
)
GDCM_STATIC_LIBRARIES = (
    "lib/libgdcmCommon.a",
    "lib/libgdcmDICT.a",
    "lib/libgdcmDSED.a",
    "lib/libgdcmIOD.a",
    "lib/libgdcmMSFF.a",
    "lib/libgdcmjpeg8.a",
    "lib/libgdcmjpeg12.a",
    "lib/libgdcmjpeg16.a",
    "lib/libgdcmopenjp2.a",
    "lib/libgdcmcharls.a",
    "lib/libgdcmexpat.a",
    "lib/libgdcmzlib.a",
    "lib/libgdcmuuid.a",
)
GDCM_LICENSE_SPECS = (
    ("GDCM", "Copyright.txt", "GDCM-BSD-3-Clause.txt"),
    ("GDCM embedded IJG JPEG", "Utilities/gdcmjpeg/README", "GDCM-IJG-JPEG-README.txt"),
    ("GDCM embedded OpenJPEG", "Utilities/gdcmopenjpeg/LICENSE", "OpenJPEG-BSD-2-Clause.txt"),
    ("GDCM embedded CharLS", "Utilities/gdcmcharls/License.txt", "CharLS-BSD-3-Clause.txt"),
    ("GDCM embedded Expat", "Utilities/gdcmexpat/COPYING", "Expat-MIT.txt"),
    ("GDCM embedded zlib", "Utilities/gdcmzlib/LICENSE", "zlib-Zlib.txt"),
    ("GDCM embedded UUID", "Utilities/gdcmuuid/COPYING", "GDCM-UUID-BSD-3-Clause.txt"),
)
NORMALIZER_ENVIRONMENT_SCRUBBED = (
    "CMAKE_PREFIX_PATH",
    "CMAKE_LIBRARY_PATH",
    "CMAKE_INCLUDE_PATH",
    "PKG_CONFIG_PATH",
    "PKG_CONFIG_LIBDIR",
    "CPATH",
    "C_INCLUDE_PATH",
    "CPLUS_INCLUDE_PATH",
    "LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FALLBACK_LIBRARY_PATH",
)
GDCM_CMAKE_OPTIONS = (
    "CMAKE_POLICY_VERSION_MINIMUM=3.5",
    "BUILD_SHARED_LIBS=OFF",
    "GDCM_BUILD_SHARED_LIBS=OFF",
    "GDCM_BUILD_APPLICATIONS=OFF",
    "GDCM_BUILD_TESTING=OFF",
    "GDCM_BUILD_EXAMPLES=OFF",
    "GDCM_USE_VTK=OFF",
    "GDCM_WRAP_PYTHON=OFF",
    "GDCM_WRAP_JAVA=OFF",
    "GDCM_WRAP_CSHARP=OFF",
    "GDCM_WRAP_PERL=OFF",
    "GDCM_WRAP_PHP=OFF",
    "GDCM_USE_SYSTEM_ZLIB=OFF",
    "GDCM_USE_SYSTEM_OPENSSL=OFF",
    "GDCM_USE_SYSTEM_EXPAT=OFF",
    "GDCM_USE_SYSTEM_JSON=OFF",
    "GDCM_USE_SYSTEM_OPENJPEG=OFF",
    "GDCM_USE_SYSTEM_CHARLS=OFF",
    "GDCM_USE_SYSTEM_UUID=OFF",
    "GDCM_USE_SYSTEM_SOCKETXX=OFF",
    "GDCM_USE_SYSTEM_LJPEG=OFF",
    "GDCM_USE_SYSTEM_LIBXML2=OFF",
    "GDCM_USE_SYSTEM_POPPLER=OFF",
    "GDCM_USE_JPEGTURBO=OFF",
    "GDCM_USE_PVRG=OFF",
    "GDCM_USE_KAKADU=OFF",
    "CMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE",
    "CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE",
    "CMAKE_SKIP_RPATH=ON",
    "CMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local",
    "CMAKE_SYSTEM_IGNORE_PATH=/opt/homebrew;/usr/local",
)
GDCM_RECEIPT_SCHEMA = "totalsegmentator_wrapper_mac.gdcm_source_build.v2"
NORMALIZER_RECEIPT_SCHEMA = "totalsegmentator_wrapper_mac.dicom_normalizer_source_build.v2"
NATIVE_TOOLCHAIN_SCHEMA = "totalsegmentator_wrapper_mac.macos_native_toolchain.v1"
NATIVE_TOOLCHAIN_SELECTIONS = {
    "cmake": "command-v-cmake",
    "xcrun": "command-v-xcrun",
    "compiler": "xcrun--find-clang",
    "cxx_compiler": "xcrun--find-clang++",
    "sdk": "xcrun--sdk-macosx--show-sdk-path",
}
_NATIVE_TOOLCHAIN_SDK_VERSION = re.compile(
    r"[0-9]+(?:\.[0-9]+)*(?:[A-Za-z0-9._-]+)?$"
)
NORMALIZER_LINKAGE = "static-gdcm-3.2.7-system-only-no-rpath"
NORMALIZER_CMAKE_OPTIONS = (
    "CMAKE_BUILD_TYPE=Release",
    "CMAKE_OSX_ARCHITECTURES=arm64",
    "CMAKE_OSX_DEPLOYMENT_TARGET=14.0",
    "CMAKE_SKIP_RPATH=ON",
    "CMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local",
    "CMAKE_SYSTEM_IGNORE_PATH=/opt/homebrew;/usr/local",
    "CMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE",
    "CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE",
    "BUILD_TESTING=OFF",
    "GDCM_DIR=verified-static-artifact/prefix/lib/gdcm-3.2",
)

DCM2NIIX_RELEASE_TAG = "v1.0.20250506"
DCM2NIIX_EXPECTED_CLI_VERSION = "v1.0.20250505"
DCM2NIIX_SOURCE_URL = (
    "https://github.com/rordenlab/dcm2niix/archive/refs/tags/v1.0.20250506.tar.gz"
)
DCM2NIIX_SOURCE_ARCHIVE_SHA256 = (
    "1b24658678b6c24141e58760dbea9fe2786ffdd736bcc37a36d9cdabc731bafa"
)
DCM2NIIX_LICENSE_SHA256 = (
    "a423e1c074ff39d9c22843489dd81bbaf42d4fa243fd785f8e96ce084db2e503"
)
DCM2NIIX_SOURCE_DATE_EPOCH = 1_746_489_600
DCM2NIIX_BUILD_RECEIPT_SCHEMA = "totalsegmentator_wrapper_mac.dcm2niix_source_build.v2"
DCM2NIIX_POINTER_SCHEMA = "totalsegmentator_wrapper_mac.dcm2niix_current_artifact.v1"


class TestAccountBundleEvidenceError(RuntimeError):
    """The installed app cannot supply the required release evidence."""


@dataclass(frozen=True, order=True)
class _MacOSVersion:
    major: int
    minor: int = 0
    patch: int = 0

    @classmethod
    def from_packed(cls, value: int) -> "_MacOSVersion":
        return cls((value >> 16) & 0xFFFF, (value >> 8) & 0xFF, value & 0xFF)

    def __str__(self) -> str:
        if self.patch:
            return f"{self.major}.{self.minor}.{self.patch}"
        return f"{self.major}.{self.minor}"


@dataclass(frozen=True)
class _MachOSlice:
    cputype: int
    minimum_macos: _MacOSVersion
    deployment_command: str
    dependencies: tuple[str, ...]
    dylib_ids: tuple[str, ...]
    rpaths: tuple[str, ...]
    dylinkers: tuple[str, ...]
    dyld_environments: tuple[str, ...]

    @property
    def architecture(self) -> str:
        if self.cputype == CPU_TYPE_ARM64:
            return ARCHITECTURE
        if self.cputype == 0x01000007:
            return "x86_64"
        return f"cputype=0x{self.cputype & 0xFFFFFFFF:08x}"


def _need(data: bytes, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise TestAccountBundleEvidenceError(
            f"{label}: truncated or out-of-bounds Mach-O structure at byte {offset}"
        )


def _load_command_string(
    data: bytes,
    *,
    command_offset: int,
    command_size: int,
    endian: str,
    label: str,
    command_name: str,
) -> str:
    if command_size < 12:
        raise TestAccountBundleEvidenceError(
            f"{label}: truncated {command_name} command"
        )
    string_offset = struct.unpack_from(endian + "I", data, command_offset + 8)[0]
    if string_offset < 12 or string_offset >= command_size:
        raise TestAccountBundleEvidenceError(
            f"{label}: invalid {command_name} string offset"
        )
    start = command_offset + string_offset
    end = command_offset + command_size
    terminator = data.find(b"\0", start, end)
    if terminator == start or terminator < 0:
        raise TestAccountBundleEvidenceError(
            f"{label}: invalid {command_name} string"
        )
    try:
        value = data[start:terminator].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise TestAccountBundleEvidenceError(
            f"{label}: {command_name} string is not UTF-8"
        ) from exc
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise TestAccountBundleEvidenceError(
            f"{label}: {command_name} string contains a control character"
        )
    return value


def _parse_thin_macho(data: bytes, label: str) -> _MachOSlice:
    configuration = _THIN_MAGICS.get(data[:4])
    if configuration is None:
        raise TestAccountBundleEvidenceError(f"{label}: expected a thin Mach-O slice")
    endian, is_64 = configuration
    header_size = 32 if is_64 else 28
    _need(data, 0, header_size, label)
    header_format = endian + ("IiiIIIII" if is_64 else "IiiIIII")
    header = struct.unpack_from(header_format, data, 0)
    cputype = header[1] & 0xFFFFFFFF
    command_count = header[4]
    commands_size = header[5]
    if command_count > 100_000:
        raise TestAccountBundleEvidenceError(
            f"{label}: unreasonable load-command count {command_count}"
        )
    _need(data, header_size, commands_size, label)

    command_offset = header_size
    command_end = header_size + commands_size
    deployment_targets: list[tuple[_MacOSVersion, str]] = []
    dependencies: list[str] = []
    dylib_ids: list[str] = []
    rpaths: list[str] = []
    dylinkers: list[str] = []
    dyld_environments: list[str] = []
    dylib_commands = {
        LC_LOAD_DYLIB,
        LC_LOAD_WEAK_DYLIB,
        LC_REEXPORT_DYLIB,
        LC_LAZY_LOAD_DYLIB,
        LC_LOAD_UPWARD_DYLIB,
    }
    for index in range(command_count):
        _need(data, command_offset, 8, label)
        command, command_size = struct.unpack_from(endian + "II", data, command_offset)
        if command_size < 8 or command_size % 4 != 0:
            raise TestAccountBundleEvidenceError(
                f"{label}: invalid load command {index} size {command_size}"
            )
        if command_offset + command_size > command_end:
            raise TestAccountBundleEvidenceError(
                f"{label}: load command {index} extends beyond sizeofcmds"
            )
        if command == LC_BUILD_VERSION:
            if command_size < 24:
                raise TestAccountBundleEvidenceError(
                    f"{label}: truncated LC_BUILD_VERSION command"
                )
            _, _, platform, minimum, _, tool_count = struct.unpack_from(
                endian + "IIIIII", data, command_offset
            )
            if 24 + tool_count * 8 > command_size:
                raise TestAccountBundleEvidenceError(
                    f"{label}: LC_BUILD_VERSION tool records exceed cmdsize"
                )
            if platform == PLATFORM_MACOS:
                deployment_targets.append(
                    (_MacOSVersion.from_packed(minimum), "LC_BUILD_VERSION")
                )
        elif command == LC_VERSION_MIN_MACOSX:
            if command_size < 16:
                raise TestAccountBundleEvidenceError(
                    f"{label}: truncated LC_VERSION_MIN_MACOSX command"
                )
            _, _, minimum, _ = struct.unpack_from(endian + "IIII", data, command_offset)
            deployment_targets.append(
                (_MacOSVersion.from_packed(minimum), "LC_VERSION_MIN_MACOSX")
            )
        elif command in dylib_commands:
            if command_size < 24:
                raise TestAccountBundleEvidenceError(
                    f"{label}: truncated dylib load command"
                )
            dependencies.append(
                _load_command_string(
                    data,
                    command_offset=command_offset,
                    command_size=command_size,
                    endian=endian,
                    label=label,
                    command_name="dylib load",
                )
            )
        elif command == LC_ID_DYLIB:
            if command_size < 24:
                raise TestAccountBundleEvidenceError(
                    f"{label}: truncated LC_ID_DYLIB command"
                )
            dylib_ids.append(
                _load_command_string(
                    data,
                    command_offset=command_offset,
                    command_size=command_size,
                    endian=endian,
                    label=label,
                    command_name="LC_ID_DYLIB",
                )
            )
        elif command == LC_RPATH:
            rpaths.append(
                _load_command_string(
                    data,
                    command_offset=command_offset,
                    command_size=command_size,
                    endian=endian,
                    label=label,
                    command_name="LC_RPATH",
                )
            )
        elif command == LC_LOAD_DYLINKER:
            dylinkers.append(
                _load_command_string(
                    data,
                    command_offset=command_offset,
                    command_size=command_size,
                    endian=endian,
                    label=label,
                    command_name="LC_LOAD_DYLINKER",
                )
            )
        elif command == LC_DYLD_ENVIRONMENT:
            dyld_environments.append(
                _load_command_string(
                    data,
                    command_offset=command_offset,
                    command_size=command_size,
                    endian=endian,
                    label=label,
                    command_name="LC_DYLD_ENVIRONMENT",
                )
            )
        command_offset += command_size
    if command_offset != command_end:
        raise TestAccountBundleEvidenceError(
            f"{label}: load-command sizes do not equal Mach-O sizeofcmds"
        )
    if not deployment_targets:
        raise TestAccountBundleEvidenceError(
            f"{label}: Mach-O slice has no macOS minimum-version load command"
        )
    versions = {value for value, _ in deployment_targets}
    if len(versions) != 1:
        rendered = ", ".join(
            f"{command_name}={version}"
            for version, command_name in deployment_targets
        )
        raise TestAccountBundleEvidenceError(
            f"{label}: conflicting macOS deployment targets ({rendered})"
        )
    version, command_name = deployment_targets[0]
    return _MachOSlice(
        cputype=cputype,
        minimum_macos=version,
        deployment_command=command_name,
        dependencies=tuple(dependencies),
        dylib_ids=tuple(dylib_ids),
        rpaths=tuple(rpaths),
        dylinkers=tuple(dylinkers),
        dyld_environments=tuple(dyld_environments),
    )


def _parse_macho(data: bytes, label: str) -> tuple[_MachOSlice, ...]:
    if len(data) < 4 or data[:4] not in _RECOGNIZED_MAGICS:
        raise TestAccountBundleEvidenceError(f"{label}: not a recognized Mach-O file")
    if data[:4] in _THIN_MAGICS:
        return (_parse_thin_macho(data, label),)

    endian, is_64 = _FAT_MAGICS[data[:4]]
    _need(data, 0, 8, label)
    _, slice_count = struct.unpack_from(endian + "II", data, 0)
    if not 1 <= slice_count <= _MAX_FAT_SLICES:
        raise TestAccountBundleEvidenceError(
            f"{label}: invalid fat Mach-O slice count {slice_count}"
        )
    entry_size = 32 if is_64 else 20
    header_size = 8 + slice_count * entry_size
    _need(data, 0, header_size, label)
    ranges: list[tuple[int, int]] = []
    slices: list[_MachOSlice] = []
    for index in range(slice_count):
        offset = 8 + index * entry_size
        if is_64:
            cputype, _, slice_offset, slice_size, alignment, reserved = struct.unpack_from(
                endian + "iiQQII", data, offset
            )
            if reserved != 0:
                raise TestAccountBundleEvidenceError(
                    f"{label}: fat64 slice {index} has non-zero reserved field"
                )
        else:
            cputype, _, slice_offset, slice_size, alignment = struct.unpack_from(
                endian + "iiIII", data, offset
            )
        cputype &= 0xFFFFFFFF
        if alignment > 31:
            raise TestAccountBundleEvidenceError(
                f"{label}: fat slice {index} has unreasonable alignment exponent {alignment}"
            )
        if slice_size == 0 or slice_offset < header_size:
            raise TestAccountBundleEvidenceError(
                f"{label}: invalid fat slice {index} offset/size"
            )
        _need(data, slice_offset, slice_size, label)
        current_range = (slice_offset, slice_offset + slice_size)
        if any(current_range[0] < end and start < current_range[1] for start, end in ranges):
            raise TestAccountBundleEvidenceError(
                f"{label}: overlapping fat Mach-O slices"
            )
        ranges.append(current_range)
        payload = data[slice_offset : slice_offset + slice_size]
        if payload[:4] not in _THIN_MAGICS:
            raise TestAccountBundleEvidenceError(
                f"{label}: fat slice {index} is not a thin Mach-O"
            )
        parsed = _parse_thin_macho(payload, f"{label} [fat slice {index}]")
        if parsed.cputype != cputype:
            raise TestAccountBundleEvidenceError(
                f"{label}: fat slice {index} architecture disagrees with inner header"
            )
        slices.append(parsed)
    return tuple(slices)


def _verify_macos14_arm64(data: bytes, label: str) -> tuple[_MachOSlice, ...]:
    slices = _parse_macho(data, label)
    maximum = _MacOSVersion(14, 0)
    errors = [
        f"{item.architecture} minos {item.minimum_macos} exceeds supported macOS {maximum}"
        for item in slices
        if item.minimum_macos > maximum
    ]
    if not any(item.cputype == CPU_TYPE_ARM64 for item in slices):
        errors.append("arm64 slice is missing")
    if errors:
        raise TestAccountBundleEvidenceError(f"{label}: " + "; ".join(errors))
    return slices


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _dyld_token_suffix(value: str, token: str, *, label: str) -> str | None:
    if value == token:
        return ""
    prefix = token + "/"
    if not value.startswith(prefix):
        return None
    suffix = value[len(prefix) :]
    if (
        not suffix
        or "\\" in suffix
        or "\x00" in suffix
        or any(ord(character) < 32 or ord(character) == 127 for character in suffix)
        or any(part == "" for part in suffix.split("/"))
    ):
        raise TestAccountBundleEvidenceError(f"{label} contains an unsafe path: {value}")
    return suffix


def _validate_bundle_install_name(value: str, *, label: str) -> None:
    for token in ("@loader_path", "@executable_path", "@rpath"):
        suffix = _dyld_token_suffix(value, token, label="LC_ID_DYLIB")
        if suffix is None:
            continue
        if not suffix or any(part in (".", "..") for part in suffix.split("/")):
            raise TestAccountBundleEvidenceError(
                f"{label}: unsafe LC_ID_DYLIB: {value}"
            )
        return
    raise TestAccountBundleEvidenceError(
        f"{label}: LC_ID_DYLIB must use an app-relative dyld token: {value}"
    )


def _is_sealed_system_rpath(value: str) -> bool:
    return _is_sealed_system_path(value, allow_root=True)


def _validate_executable_relative_boundary(
    suffix: str,
    *,
    contents: Path | None,
    label: str,
    value: str,
) -> None:
    """Reject an ``@executable_path`` spelling that can leave ``Contents``.

    An app, helper, or private Python interpreter is always at least one
    directory below ``Contents``.  Resolving against the shallowest supported
    root (``Contents/MacOS``) proves that an executable-relative spelling does
    not escape the bundle, without pretending to know the eventual process
    root for every dynamically loaded image.
    """

    if contents is None:
        return
    candidate = (contents / "MacOS").joinpath(*suffix.split("/"))
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise TestAccountBundleEvidenceError(
            f"{label}: could not normalize {value}: {exc}"
        ) from exc
    if not _is_relative_to(resolved, contents):
        raise TestAccountBundleEvidenceError(
            f"{label}: @executable_path escapes app Contents: {value}"
        )


def _validate_app_relative_rpath(
    value: str,
    *,
    binary: Path | None,
    contents: Path | None,
    label: str,
) -> None:
    if value.startswith("/"):
        if not _is_sealed_system_rpath(value):
            raise TestAccountBundleEvidenceError(
                f"{label}: LC_RPATH must use sealed macOS or app-relative paths: {value}"
            )
        return
    loader_suffix = _dyld_token_suffix(value, "@loader_path", label="LC_RPATH")
    executable_suffix = _dyld_token_suffix(
        value, "@executable_path", label="LC_RPATH"
    )
    if loader_suffix is not None:
        if binary is None or contents is None:
            return
        candidate = binary.parent.joinpath(*loader_suffix.split("/"))
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise TestAccountBundleEvidenceError(
                f"{label}: could not normalize LC_RPATH {value}: {exc}"
            ) from exc
        if not _is_relative_to(resolved, contents):
            raise TestAccountBundleEvidenceError(
                f"{label}: LC_RPATH escapes app Contents: {value}"
            )
        if not resolved.is_dir():
            raise TestAccountBundleEvidenceError(
                f"{label}: LC_RPATH does not name an existing app directory: {value}"
            )
        return
    if executable_suffix is not None:
        _validate_executable_relative_boundary(
            executable_suffix,
            contents=contents,
            label=label,
            value=value,
        )
        return
    raise TestAccountBundleEvidenceError(
        f"{label}: LC_RPATH must use sealed macOS or app-relative paths: {value}"
    )


def _validate_dependency(
    value: str,
    *,
    binary: Path | None,
    contents: Path | None,
    label: str,
) -> None:
    if value.startswith("/"):
        if not _is_sealed_system_dependency(value):
            raise TestAccountBundleEvidenceError(
                f"{label}: non-system Mach-O dependency is not allowed: {value}"
            )
        return
    loader_suffix = _dyld_token_suffix(value, "@loader_path", label="Mach-O dependency")
    executable_suffix = _dyld_token_suffix(
        value, "@executable_path", label="Mach-O dependency"
    )
    rpath_suffix = _dyld_token_suffix(value, "@rpath", label="Mach-O dependency")
    if loader_suffix is not None:
        if binary is None or contents is None:
            return
        candidate = binary.parent.joinpath(*loader_suffix.split("/"))
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise TestAccountBundleEvidenceError(
                f"{label}: could not normalize dependency {value}: {exc}"
            ) from exc
        if not _is_relative_to(resolved, contents):
            raise TestAccountBundleEvidenceError(
                f"{label}: Mach-O dependency escapes app Contents: {value}"
            )
        return
    if executable_suffix is not None:
        _validate_executable_relative_boundary(
            executable_suffix,
            contents=contents,
            label=label,
            value=value,
        )
        return
    if rpath_suffix is not None:
        if not rpath_suffix or any(
            part in (".", "..") for part in rpath_suffix.split("/")
        ):
            raise TestAccountBundleEvidenceError(
                f"{label}: Mach-O @rpath dependency contains an unsafe path: {value}"
            )
        return
    raise TestAccountBundleEvidenceError(
        f"{label}: non-system Mach-O dependency is external or unsupported: {value}"
    )


def _verify_bundle_dyld_metadata(
    slices: tuple[_MachOSlice, ...],
    *,
    label: str,
    binary: Path | None,
    contents: Path | None,
) -> None:
    """Reject dyld metadata that can name an external or malformed image.

    The installed verifier has no developer-tool dependency.  It can validate
    absolute paths and local ``@loader_path`` boundaries directly; executable
    root and inherited ``@rpath`` resolution remain process-specific and are
    checked by the release linkage gate before packaging.
    """

    for index, item in enumerate(slices):
        slice_label = f"{label} [{item.architecture} slice {index}]"
        if len(item.dylib_ids) > 1:
            raise TestAccountBundleEvidenceError(
                f"{slice_label}: multiple LC_ID_DYLIB commands are not allowed"
            )
        for dylib_id in item.dylib_ids:
            _validate_bundle_install_name(dylib_id, label=slice_label)
        if len(item.dylinkers) > 1 or (
            item.dylinkers and item.dylinkers[0] != "/usr/lib/dyld"
        ):
            raise TestAccountBundleEvidenceError(
                f"{slice_label}: LC_LOAD_DYLINKER must be exactly /usr/lib/dyld"
            )
        if item.dyld_environments:
            raise TestAccountBundleEvidenceError(
                f"{slice_label}: LC_DYLD_ENVIRONMENT is not allowed"
            )
        for rpath in item.rpaths:
            _validate_app_relative_rpath(
                rpath,
                binary=binary,
                contents=contents,
                label=slice_label,
            )
        for dependency in item.dependencies:
            _validate_dependency(
                dependency,
                binary=binary,
                contents=contents,
                label=slice_label,
            )


def _regular_file_metadata_matches(
    before: os.stat_result,
    after: os.stat_result,
) -> bool:
    return (
        stat.S_ISREG(after.st_mode)
        and before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )


def _open_regular_readonly(
    path: Path,
    label: str,
    *,
    maximum_size: int | None = None,
) -> tuple[int, os.stat_result]:
    """Open one regular file without a path-following TOCTOU gap.

    This verifier reads files from an installed bundle that might have been
    altered after code signing.  ``lstat`` alone is not enough: reopening a
    pathname can follow a newly substituted symlink.  macOS provides
    ``O_NOFOLLOW``; treat its absence as an unsupported verification platform
    instead of silently weakening the check.
    """

    try:
        metadata = path.lstat()
    except OSError as exc:
        raise TestAccountBundleEvidenceError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise TestAccountBundleEvidenceError(
            f"{label} must be a regular non-symlink file: {path}"
        )
    if maximum_size is not None and metadata.st_size > maximum_size:
        raise TestAccountBundleEvidenceError(
            f"{label} exceeds the safe size limit: {path}"
        )
    try:
        nofollow = os.O_NOFOLLOW
    except AttributeError as exc:  # pragma: no cover - macOS always provides it
        raise TestAccountBundleEvidenceError(
            f"{label} cannot be verified safely because O_NOFOLLOW is unavailable"
        ) from exc
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise TestAccountBundleEvidenceError(
            f"could not safely open {label}: {path}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not _regular_file_metadata_matches(metadata, opened):
            raise TestAccountBundleEvidenceError(
                f"{label} changed while opening: {path}"
            )
        if maximum_size is not None and opened.st_size > maximum_size:
            raise TestAccountBundleEvidenceError(
                f"{label} exceeds the safe size limit: {path}"
            )
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def _verify_regular_file_unchanged(
    descriptor: int,
    metadata: os.stat_result,
    *,
    path: Path,
    label: str,
) -> None:
    try:
        after = os.fstat(descriptor)
    except OSError as exc:
        raise TestAccountBundleEvidenceError(
            f"could not re-stat {label}: {path}"
        ) from exc
    if not _regular_file_metadata_matches(metadata, after):
        raise TestAccountBundleEvidenceError(f"{label} changed while reading: {path}")


def _read_regular(path: Path, label: str, *, maximum_size: int | None = None) -> bytes:
    descriptor, metadata = _open_regular_readonly(
        path,
        label,
        maximum_size=maximum_size,
    )
    try:
        chunks: list[bytes] = []
        received = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            received += len(chunk)
            if maximum_size is not None and received > maximum_size:
                raise TestAccountBundleEvidenceError(
                    f"{label} exceeds the safe size limit: {path}"
                )
            chunks.append(chunk)
        _verify_regular_file_unchanged(
            descriptor,
            metadata,
            path=path,
            label=label,
        )
        if received != metadata.st_size:
            raise TestAccountBundleEvidenceError(f"{label} changed while reading: {path}")
        return b"".join(chunks)
    except OSError as exc:
        raise TestAccountBundleEvidenceError(f"could not read {label}: {path}") from exc
    finally:
        os.close(descriptor)


def _read_regular_prefix(path: Path, label: str, length: int) -> bytes:
    descriptor, metadata = _open_regular_readonly(path, label)
    try:
        prefix = os.read(descriptor, length)
        _verify_regular_file_unchanged(
            descriptor,
            metadata,
            path=path,
            label=label,
        )
        return prefix
    except OSError as exc:
        raise TestAccountBundleEvidenceError(f"could not read {label}: {path}") from exc
    finally:
        os.close(descriptor)


def _resource_file(resources: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise TestAccountBundleEvidenceError(f"{label} is missing from the app manifest")
    path_parts = PurePosixPath(relative)
    if (
        path_parts.is_absolute()
        or ".." in path_parts.parts
        or "." in path_parts.parts
        or "" in path_parts.parts
    ):
        raise TestAccountBundleEvidenceError(f"{label} is not a safe resource path: {relative!r}")
    try:
        resources_root = resources.resolve(strict=True)
        candidate = resources.joinpath(*path_parts.parts)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resources_root)
    except (OSError, ValueError) as exc:
        raise TestAccountBundleEvidenceError(
            f"{label} is missing or escapes app resources: {relative!r}"
        ) from exc
    _read_regular(candidate, label)
    return candidate


def _manifest_bundled(manifest: Mapping[str, object]) -> Mapping[str, object]:
    bundled = manifest.get("bundled")
    if not isinstance(bundled, dict):
        raise TestAccountBundleEvidenceError("setup manifest bundled section is missing")
    return bundled


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise TestAccountBundleEvidenceError(
            f"{label} is not a lowercase SHA-256 digest"
        )
    return value


def _read_json_resource(resources: Path, relative: object, label: str) -> tuple[dict[str, Any], bytes]:
    path = _resource_file(resources, relative, label)
    payload_bytes = _read_regular(path, label, maximum_size=MAX_JSON_BYTES)
    try:
        payload: Any = json.loads(payload_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TestAccountBundleEvidenceError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise TestAccountBundleEvidenceError(f"{label} must contain a JSON object")
    return payload, payload_bytes


def _require_exact_keys(payload: Mapping[str, object], expected: Iterable[str], label: str) -> None:
    expected_set = set(expected)
    if set(payload) != expected_set:
        raise TestAccountBundleEvidenceError(
            f"{label} field set mismatch: expected {sorted(expected_set)}, got {sorted(payload)}"
        )


def _require_exact(payload: Mapping[str, object], key: str, value: object, label: str) -> None:
    if payload.get(key) != value:
        raise TestAccountBundleEvidenceError(
            f"{label} {key} mismatch: expected {value!r}, got {payload.get(key)!r}"
        )


def _require_native_toolchain_text(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 256
        or any(token in value for token in ("/", "\\", "\n", "\r", "\x00", "~"))
    ):
        raise TestAccountBundleEvidenceError(f"{label} is invalid")
    return value


def _verify_native_toolchain_record(
    payload: object,
    *,
    name: str,
    label: str,
    digest_key: str = "binary_sha256",
) -> Mapping[str, object]:
    if not isinstance(payload, dict):
        raise TestAccountBundleEvidenceError(f"{label} {name} record is invalid")
    _require_exact_keys(payload, ("selection", "version", digest_key), f"{label} {name}")
    _require_exact(
        payload,
        "selection",
        NATIVE_TOOLCHAIN_SELECTIONS[name],
        f"{label} {name}",
    )
    _require_native_toolchain_text(payload.get("version"), f"{label} {name} version")
    _require_sha256(payload.get(digest_key), f"{label} {name} {digest_key}")
    return payload


def _verify_native_toolchain(payload: object, label: str) -> Mapping[str, object]:
    if not isinstance(payload, dict):
        raise TestAccountBundleEvidenceError(f"{label} toolchain is invalid")
    _require_exact_keys(
        payload,
        ("schema", "cmake", "xcrun", "compiler", "cxx_compiler", "sdk"),
        f"{label} toolchain",
    )
    _require_exact(payload, "schema", NATIVE_TOOLCHAIN_SCHEMA, f"{label} toolchain")
    for name in ("cmake", "xcrun", "compiler", "cxx_compiler"):
        _verify_native_toolchain_record(payload.get(name), name=name, label=label)
    sdk = _verify_native_toolchain_record(
        payload.get("sdk"),
        name="sdk",
        label=label,
        digest_key="settings_sha256",
    )
    sdk_version = _require_native_toolchain_text(sdk.get("version"), f"{label} SDK version")
    if not _NATIVE_TOOLCHAIN_SDK_VERSION.fullmatch(sdk_version):
        raise TestAccountBundleEvidenceError(f"{label} SDK version is invalid")
    return payload


def _verify_gdcm_license_inventory(
    resources: Path,
    payload: Mapping[str, object],
) -> None:
    _require_exact_keys(
        payload,
        (
            "schema",
            "gdcm_version",
            "source_url",
            "source_archive_sha256",
            "linkage",
            "gdcmconv_bundled",
            "components",
        ),
        "GDCM static license inventory",
    )
    expected_fixed = {
        "schema": "totalsegmentator_wrapper_mac.gdcm_static_license_inventory.v1",
        "gdcm_version": GDCM_VERSION,
        "source_url": GDCM_SOURCE_URL,
        "source_archive_sha256": GDCM_SOURCE_ARCHIVE_SHA256,
        "linkage": "static",
        "gdcmconv_bundled": False,
    }
    for key, value in expected_fixed.items():
        _require_exact(payload, key, value, "GDCM static license inventory")
    components = payload.get("components")
    if not isinstance(components, list):
        raise TestAccountBundleEvidenceError("GDCM static license inventory components are missing")
    by_path = {
        item.get("packaged_path"): item
        for item in components
        if isinstance(item, dict) and isinstance(item.get("packaged_path"), str)
    }
    expected_paths = {packaged for _, _, packaged in GDCM_LICENSE_SPECS}
    if set(by_path) != expected_paths or len(by_path) != len(components):
        raise TestAccountBundleEvidenceError("GDCM static license component set mismatch")
    for component, source_path, packaged_path in GDCM_LICENSE_SPECS:
        item = by_path[packaged_path]
        if item.get("component") != component or item.get("source_path") != source_path:
            raise TestAccountBundleEvidenceError(
                f"GDCM static license provenance mismatch: {packaged_path}"
            )
        license_path = _resource_file(
            resources, f"licenses/{packaged_path}", f"GDCM static license {packaged_path}"
        )
        license_bytes = _read_regular(license_path, f"GDCM static license {packaged_path}")
        if (
            item.get("sha256") != _sha256_bytes(license_bytes)
            or item.get("size_bytes") != len(license_bytes)
        ):
            raise TestAccountBundleEvidenceError(
                f"GDCM static license integrity mismatch: {packaged_path}"
            )


def _verify_gdcm_receipt(payload: Mapping[str, object]) -> Mapping[str, object]:
    _require_exact_keys(
        payload,
        (
            "schema",
            "gdcm_version",
            "source_url",
            "source_archive_sha256",
            "minimum_macos",
            "architecture",
            "source_date_epoch",
            "linkage",
            "gdcmconv_bundled",
            "environment_scrubbed",
            "cmake_options",
            "prefix_relpath",
            "prefix_tree_sha256",
            "license_inventory_sha256",
            "required_static_libraries",
            "toolchain",
        ),
        "GDCM build receipt",
    )
    expected_fixed = {
        "schema": GDCM_RECEIPT_SCHEMA,
        "gdcm_version": GDCM_VERSION,
        "source_url": GDCM_SOURCE_URL,
        "source_archive_sha256": GDCM_SOURCE_ARCHIVE_SHA256,
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "linkage": "static",
        "gdcmconv_bundled": False,
        "environment_scrubbed": list(NORMALIZER_ENVIRONMENT_SCRUBBED),
        "cmake_options": list(GDCM_CMAKE_OPTIONS),
        "prefix_relpath": "prefix",
    }
    for key, value in expected_fixed.items():
        _require_exact(payload, key, value, "GDCM build receipt")
    _require_sha256(payload.get("prefix_tree_sha256"), "GDCM prefix_tree_sha256")
    _require_sha256(payload.get("license_inventory_sha256"), "GDCM license_inventory_sha256")
    libraries = payload.get("required_static_libraries")
    if not isinstance(libraries, dict) or set(libraries) != set(GDCM_STATIC_LIBRARIES):
        raise TestAccountBundleEvidenceError("GDCM build receipt static library set mismatch")
    for relative, digest in libraries.items():
        _require_sha256(digest, f"GDCM static library digest {relative}")
    return _verify_native_toolchain(payload.get("toolchain"), "GDCM build receipt")


def _verify_normalizer_receipt(
    payload: Mapping[str, object],
    *,
    binary_input_sha256: str,
    gdcm_receipt: Mapping[str, object],
    gdcm_receipt_bytes: bytes,
    license_inventory_bytes: bytes,
) -> None:
    _require_exact_keys(
        payload,
        (
            "schema",
            "binary",
            "binary_sha256",
            "native_source_sha256",
            "minimum_macos",
            "architecture",
            "source_date_epoch",
            "linkage",
            "environment_scrubbed",
            "cmake_options",
            "license_inventory_sha256",
            "gdcm_build_receipt",
            "gdcm_build_receipt_sha256",
            "gdcm_prefix_tree_sha256",
            "gdcm_source_url",
            "gdcm_source_archive_sha256",
            "toolchain",
        ),
        "normalizer build receipt",
    )
    expected_fixed = {
        "schema": NORMALIZER_RECEIPT_SCHEMA,
        "binary": "totalsegmentator-wrapper-dicom-normalizer",
        "binary_sha256": binary_input_sha256,
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "linkage": NORMALIZER_LINKAGE,
        "environment_scrubbed": list(NORMALIZER_ENVIRONMENT_SCRUBBED),
        "cmake_options": list(NORMALIZER_CMAKE_OPTIONS),
        "license_inventory_sha256": _sha256_bytes(license_inventory_bytes),
        "gdcm_build_receipt": "gdcm-build-provenance.json",
        "gdcm_build_receipt_sha256": _sha256_bytes(gdcm_receipt_bytes),
        "gdcm_prefix_tree_sha256": gdcm_receipt.get("prefix_tree_sha256"),
        "gdcm_source_url": GDCM_SOURCE_URL,
        "gdcm_source_archive_sha256": GDCM_SOURCE_ARCHIVE_SHA256,
    }
    for key, value in expected_fixed.items():
        _require_exact(payload, key, value, "normalizer build receipt")
    _require_sha256(payload.get("native_source_sha256"), "normalizer native_source_sha256")
    normalizer_toolchain = _verify_native_toolchain(
        payload.get("toolchain"), "normalizer build receipt"
    )
    gdcm_toolchain = _verify_native_toolchain(
        gdcm_receipt.get("toolchain"), "GDCM build receipt"
    )
    if normalizer_toolchain != gdcm_toolchain:
        raise TestAccountBundleEvidenceError(
            "normalizer and GDCM build receipt toolchain identities differ"
        )


def verify_normalizer_source_provenance(
    resources: Path,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Bind a release manifest's normalizer source record to bundled receipts."""

    bundled = _manifest_bundled(manifest)
    expected_paths = {
        "dicom_normalizer_build_provenance": "licenses/dicom-normalizer-build-provenance.json",
        "gdcm_build_provenance": "licenses/gdcm-build-provenance.json",
        "gdcm_static_license_inventory": "licenses/GDCM-static-license-inventory.json",
    }
    for key, expected in expected_paths.items():
        _require_exact(bundled, key, expected, "setup manifest bundled")
    normalizer_input_sha256 = _require_sha256(
        manifest.get("normalizer_input_sha256"), "normalizer_input_sha256"
    )
    normalizer_receipt, normalizer_receipt_bytes = _read_json_resource(
        resources,
        expected_paths["dicom_normalizer_build_provenance"],
        "normalizer build receipt",
    )
    gdcm_receipt, gdcm_receipt_bytes = _read_json_resource(
        resources,
        expected_paths["gdcm_build_provenance"],
        "GDCM build receipt",
    )
    license_inventory, license_inventory_bytes = _read_json_resource(
        resources,
        expected_paths["gdcm_static_license_inventory"],
        "GDCM static license inventory",
    )
    _verify_gdcm_license_inventory(resources, license_inventory)
    _verify_gdcm_receipt(gdcm_receipt)
    _verify_normalizer_receipt(
        normalizer_receipt,
        binary_input_sha256=normalizer_input_sha256,
        gdcm_receipt=gdcm_receipt,
        gdcm_receipt_bytes=gdcm_receipt_bytes,
        license_inventory_bytes=license_inventory_bytes,
    )
    expected_source = {
        "kind": "source-built-static-gdcm",
        "release_eligible": True,
        "binary_sha256": normalizer_receipt["binary_sha256"],
        "native_source_sha256": normalizer_receipt["native_source_sha256"],
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "linkage": NORMALIZER_LINKAGE,
        "gdcm_version": GDCM_VERSION,
        "gdcm_source_url": GDCM_SOURCE_URL,
        "gdcm_source_archive_sha256": GDCM_SOURCE_ARCHIVE_SHA256,
        "build_receipt": expected_paths["dicom_normalizer_build_provenance"],
        "build_receipt_sha256": _sha256_bytes(normalizer_receipt_bytes),
        "gdcm_build_receipt": expected_paths["gdcm_build_provenance"],
        "gdcm_build_receipt_sha256": _sha256_bytes(gdcm_receipt_bytes),
        "license_inventory": expected_paths["gdcm_static_license_inventory"],
        "license_inventory_sha256": normalizer_receipt["license_inventory_sha256"],
    }
    if manifest.get("normalizer_source") != expected_source:
        raise TestAccountBundleEvidenceError(
            "normalizer_source does not match bundled normalizer/GDCM receipts"
        )
    return {
        "source_kind": expected_source["kind"],
        "normalizer_receipt_sha256": expected_source["build_receipt_sha256"],
        "gdcm_receipt_sha256": expected_source["gdcm_build_receipt_sha256"],
        "license_inventory_sha256": expected_source["license_inventory_sha256"],
    }


def _verify_dcm2niix_build_receipt(
    payload: Mapping[str, object], *, binary_input_sha256: str
) -> None:
    expected_fixed = {
        "schema": DCM2NIIX_BUILD_RECEIPT_SCHEMA,
        "release_tag": DCM2NIIX_RELEASE_TAG,
        "expected_cli_version": DCM2NIIX_EXPECTED_CLI_VERSION,
        "source_url": DCM2NIIX_SOURCE_URL,
        "source_archive_sha256": DCM2NIIX_SOURCE_ARCHIVE_SHA256,
        "license_sha256": DCM2NIIX_LICENSE_SHA256,
        "source_license_sha256": DCM2NIIX_LICENSE_SHA256,
        "bundled_license_sha256": DCM2NIIX_LICENSE_SHA256,
        "binary_sha256": binary_input_sha256,
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "artifact_directory": f"artifacts/{binary_input_sha256}",
        "source_date_epoch": DCM2NIIX_SOURCE_DATE_EPOCH,
        "binary": "dcm2niix",
        "bundled_license": "licenses/dcm2niix-license.txt",
    }
    _require_exact_keys(
        payload, (*expected_fixed, "linkage"), "dcm2niix build receipt"
    )
    for key, value in expected_fixed.items():
        _require_exact(payload, key, value, "dcm2niix build receipt")
    _require_exact(
        payload,
        "linkage",
        {
            "result": "system-only-no-rpath",
            "allowed_dependency_prefixes": ["/System/Library/", "/usr/lib/"],
            "rpaths": [],
        },
        "dcm2niix build receipt",
    )


def _verify_dcm2niix_pointer(
    payload: Mapping[str, object], *, binary_input_sha256: str
) -> None:
    expected_fixed = {
        "schema": DCM2NIIX_POINTER_SCHEMA,
        "release_tag": DCM2NIIX_RELEASE_TAG,
        "source_url": DCM2NIIX_SOURCE_URL,
        "source_archive_sha256": DCM2NIIX_SOURCE_ARCHIVE_SHA256,
        "license_sha256": DCM2NIIX_LICENSE_SHA256,
        "binary_sha256": binary_input_sha256,
        "artifact_directory": f"artifacts/{binary_input_sha256}",
    }
    _require_exact_keys(payload, expected_fixed, "dcm2niix current artifact pointer")
    for key, value in expected_fixed.items():
        _require_exact(payload, key, value, "dcm2niix current artifact pointer")


def verify_dcm2niix_source_provenance(
    resources: Path,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Bind dcm2niix manifest provenance to the bundled receipt and pointer."""

    bundled = _manifest_bundled(manifest)
    expected_paths = {
        "dcm2niix_build_provenance": "licenses/dcm2niix-build-provenance.json",
        "dcm2niix_artifact_pointer": "licenses/dcm2niix-current-artifact.json",
    }
    for key, expected in expected_paths.items():
        _require_exact(bundled, key, expected, "setup manifest bundled")
    binary_input_sha256 = _require_sha256(
        manifest.get("dcm2niix_input_sha256"), "dcm2niix_input_sha256"
    )
    receipt, receipt_bytes = _read_json_resource(
        resources,
        expected_paths["dcm2niix_build_provenance"],
        "dcm2niix build receipt",
    )
    pointer, pointer_bytes = _read_json_resource(
        resources,
        expected_paths["dcm2niix_artifact_pointer"],
        "dcm2niix current artifact pointer",
    )
    _verify_dcm2niix_build_receipt(receipt, binary_input_sha256=binary_input_sha256)
    _verify_dcm2niix_pointer(pointer, binary_input_sha256=binary_input_sha256)
    expected_source = {
        "kind": "pinned-official-source-build",
        "release_eligible": True,
        "release_tag": DCM2NIIX_RELEASE_TAG,
        "expected_cli_version": DCM2NIIX_EXPECTED_CLI_VERSION,
        "source_url": DCM2NIIX_SOURCE_URL,
        "source_archive_sha256": DCM2NIIX_SOURCE_ARCHIVE_SHA256,
        "source_date_epoch": DCM2NIIX_SOURCE_DATE_EPOCH,
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "binary_sha256": binary_input_sha256,
        "license": "licenses/dcm2niix-license.txt",
        "license_sha256": DCM2NIIX_LICENSE_SHA256,
        "build_receipt": expected_paths["dcm2niix_build_provenance"],
        "build_receipt_sha256": _sha256_bytes(receipt_bytes),
        "artifact_pointer": expected_paths["dcm2niix_artifact_pointer"],
        "artifact_pointer_sha256": _sha256_bytes(pointer_bytes),
        "linkage": "system-only-no-rpath",
    }
    if manifest.get("dcm2niix_source") != expected_source:
        raise TestAccountBundleEvidenceError(
            "dcm2niix_source does not match bundled receipt and artifact pointer"
        )
    return {
        "release_tag": DCM2NIIX_RELEASE_TAG,
        "receipt_sha256": expected_source["build_receipt_sha256"],
        "pointer_sha256": expected_source["artifact_pointer_sha256"],
    }


def _native_suffix(value: str) -> bool:
    return value.lower().endswith(_NATIVE_SUFFIXES)


def _safe_wheel_member_path(name: str) -> PurePosixPath:
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise TestAccountBundleEvidenceError(f"unsafe wheel member path: {name!r}")
    raw = name[:-1] if name.endswith("/") else name
    if (
        not raw
        or raw.endswith("/")
        or any(part in ("", ".", "..") for part in raw.split("/"))
    ):
        raise TestAccountBundleEvidenceError(f"unsafe wheel member path: {name!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts:
        raise TestAccountBundleEvidenceError(f"unsafe wheel member path: {name!r}")
    return path


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_IFMT(mode) == stat.S_IFLNK


def _macos_wheel_member_key(path: PurePosixPath) -> str:
    """Return the collision key used by default macOS volume semantics."""

    return unicodedata.normalize("NFC", str(path)).casefold()


def _verify_wheel_machos(wheel: Path) -> list[tuple[str, tuple[_MachOSlice, ...]]]:
    results: list[tuple[str, tuple[_MachOSlice, ...]]] = []
    total_native_bytes = 0
    descriptor: int | None = None
    try:
        descriptor, wheel_metadata = _open_regular_readonly(wheel, "bundled wheel")
        with os.fdopen(descriptor, "rb", closefd=True) as wheel_handle:
            descriptor = None
            with zipfile.ZipFile(wheel_handle) as archive:
                seen: set[PurePosixPath] = set()
                macos_seen: dict[str, PurePosixPath] = {}
                for info in archive.infolist():
                    relative = _safe_wheel_member_path(info.filename)
                    if relative in seen:
                        raise TestAccountBundleEvidenceError(
                            f"{wheel}: duplicate wheel member path: {info.filename}"
                        )
                    seen.add(relative)
                    macos_key = _macos_wheel_member_key(relative)
                    prior = macos_seen.get(macos_key)
                    if prior is not None:
                        raise TestAccountBundleEvidenceError(
                            f"{wheel}: case-insensitive wheel member collision: "
                            f"{prior} and {info.filename}"
                        )
                    macos_seen[macos_key] = relative
                    if _zip_member_is_symlink(info):
                        raise TestAccountBundleEvidenceError(
                            f"{wheel}: wheel contains a symlink member: {info.filename}"
                        )
                    if info.flag_bits & 0x1:
                        raise TestAccountBundleEvidenceError(
                            f"{wheel}: encrypted wheel member is not allowed: {info.filename}"
                        )
                    if info.is_dir():
                        continue
                    label = f"{wheel}!/{info.filename}"
                    with archive.open(info) as handle:
                        prefix = handle.read(4)
                        native = prefix in _RECOGNIZED_MAGICS
                        if not native and not _native_suffix(info.filename):
                            continue
                        if info.file_size > MAX_WHEEL_NATIVE_MEMBER_BYTES:
                            raise TestAccountBundleEvidenceError(
                                f"{label}: native wheel member exceeds safe size limit"
                            )
                        total_native_bytes += info.file_size
                        if total_native_bytes > MAX_WHEEL_TOTAL_NATIVE_BYTES:
                            raise TestAccountBundleEvidenceError(
                                f"{wheel}: native wheel members exceed safe total size limit"
                            )
                        data = prefix + handle.read()
                    if not native:
                        raise TestAccountBundleEvidenceError(
                            f"{label}: packaged native-library suffix is not a recognized Mach-O"
                        )
                    slices = _verify_macos14_arm64(data, label)
                    # Wheels are installed under App Support rather than inside
                    # this signed app bundle.  Their @loader_path and rpath
                    # targets cannot be proven from the archive alone, so do
                    # not grant them the app bundle's tokenized-dependency
                    # exception.  This matches the release linkage gate:
                    # native wheel members must use only sealed macOS dylibs.
                    _verify_system_linkage(slices, label)
                    results.append((label, slices))
            _verify_regular_file_unchanged(
                wheel_handle.fileno(),
                wheel_metadata,
                path=wheel,
                label="bundled wheel",
            )
    except TestAccountBundleEvidenceError:
        raise
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile) as exc:
        raise TestAccountBundleEvidenceError(f"{wheel}: invalid wheel ZIP") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return results


def _assert_safe_contents_symlink(path: Path, contents: Path) -> None:
    try:
        target = os.readlink(path)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise TestAccountBundleEvidenceError(
            f"app bundle contains a broken symlink: {path}: {exc}"
        ) from exc
    if target.startswith("/"):
        raise TestAccountBundleEvidenceError(
            f"app bundle contains an absolute symlink: {path} -> {target}"
        )
    if not _is_relative_to(resolved, contents):
        raise TestAccountBundleEvidenceError(
            f"app bundle symlink escapes app Contents: {path} -> {resolved}"
        )


def _collect_contents_machos(contents: Path) -> tuple[Path, ...]:
    """Enumerate regular Mach-O files while validating every Contents symlink."""

    pending = [contents]
    machos: list[Path] = []
    errors: list[str] = []
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            errors.append(f"could not enumerate app bundle directory {directory}: {exc}")
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_symlink():
                    _assert_safe_contents_symlink(path, contents)
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    errors.append(
                        f"app bundle contains a non-regular filesystem entry: {path}"
                    )
                    continue
                prefix = _read_regular_prefix(path, "app bundle file", 4)
            except (OSError, RuntimeError, TestAccountBundleEvidenceError) as exc:
                errors.append(str(exc))
                continue
            if prefix in _RECOGNIZED_MAGICS:
                machos.append(path)
            elif _native_suffix(path.name):
                errors.append(
                    f"{path}: packaged native-library suffix is not a recognized Mach-O"
                )
    if errors:
        raise TestAccountBundleEvidenceError("app bundle scan failed:\n" + "\n".join(errors))
    return tuple(sorted(machos))


def verify_macos14_arm64_app_and_wheels(app_path: Path) -> dict[str, object]:
    """Verify every app/wheel Mach-O slice without requiring host developer tools."""

    try:
        app_metadata = app_path.lstat()
    except OSError as exc:
        raise TestAccountBundleEvidenceError(f"app bundle is missing: {app_path}") from exc
    if stat.S_ISLNK(app_metadata.st_mode) or not stat.S_ISDIR(app_metadata.st_mode):
        raise TestAccountBundleEvidenceError(
            f"app bundle must be a directory, not a symlink: {app_path}"
        )
    contents = app_path / "Contents"
    resources = contents / "Resources"
    try:
        contents_metadata = contents.lstat()
        resources_metadata = resources.lstat()
    except OSError as exc:
        raise TestAccountBundleEvidenceError("app Contents or Resources directory is missing") from exc
    if (
        stat.S_ISLNK(contents_metadata.st_mode)
        or not stat.S_ISDIR(contents_metadata.st_mode)
        or stat.S_ISLNK(resources_metadata.st_mode)
        or not stat.S_ISDIR(resources_metadata.st_mode)
    ):
        raise TestAccountBundleEvidenceError("app Contents and Resources must be directories")
    required = (
        contents / "MacOS" / "TotalSegmentatorWrapperForMac",
        resources / "bin" / "totalsegmentator-wrapper-dicom-normalizer",
        resources / "bin" / "dcm2niix",
    )
    try:
        contents_root = contents.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise TestAccountBundleEvidenceError(
            f"could not resolve app Contents: {contents}"
        ) from exc
    results: list[tuple[str, tuple[_MachOSlice, ...]]] = []
    errors: list[str] = []
    required_resolved: set[Path] = set()
    for path in required:
        try:
            required_resolved.add(path.resolve(strict=True))
            slices = _verify_macos14_arm64(
                _read_regular(path, "required app Mach-O"), str(path)
            )
            _verify_bundle_dyld_metadata(
                slices,
                label=str(path),
                binary=path,
                contents=contents_root,
            )
            results.append((str(path), slices))
        except (OSError, TestAccountBundleEvidenceError) as exc:
            errors.append(str(exc))
    try:
        contents_machos = _collect_contents_machos(contents_root)
    except TestAccountBundleEvidenceError as exc:
        errors.append(str(exc))
        contents_machos = ()
    for path in contents_machos:
        try:
            if path.resolve() in required_resolved:
                continue
            slices = _verify_macos14_arm64(
                _read_regular(path, "app Mach-O"), str(path)
            )
            _verify_bundle_dyld_metadata(
                slices,
                label=str(path),
                binary=path,
                contents=contents_root,
            )
            results.append((str(path), slices))
        except (OSError, TestAccountBundleEvidenceError) as exc:
            errors.append(str(exc))
    wheels_directory = resources / "wheels"
    if not wheels_directory.is_dir() or wheels_directory.is_symlink():
        errors.append(f"bundled wheels directory is missing or unsafe: {wheels_directory}")
        wheel_paths: list[Path] = []
    else:
        wheel_paths = sorted(wheels_directory.glob("*.whl"))
        if not wheel_paths:
            errors.append(f"bundled wheels directory has no wheel: {wheels_directory}")
    for wheel in wheel_paths:
        try:
            results.extend(_verify_wheel_machos(wheel))
        except TestAccountBundleEvidenceError as exc:
            errors.append(str(exc))
    if errors:
        raise TestAccountBundleEvidenceError("\n".join(errors))
    if not results:
        raise TestAccountBundleEvidenceError("no Mach-O files were found in the app or wheels")
    return {
        "macho_file_count": len(results),
        "macho_slice_count": sum(len(slices) for _, slices in results),
        "wheel_count": len(wheel_paths),
        "maximum_macos": MINIMUM_MACOS,
        "required_architecture": ARCHITECTURE,
    }


def _is_sealed_system_path(value: str, *, allow_root: bool) -> bool:
    if (
        not value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    parts = value.split("/")
    if parts[0] != "" or any(part in ("", ".", "..") for part in parts[1:]):
        return False
    normalized = PurePosixPath(value)
    if str(normalized) != value:
        return False
    for root in ("/System/Library", "/usr/lib"):
        if allow_root and value == root:
            return True
        if value.startswith(root + "/"):
            return True
    return False


def _is_sealed_system_dependency(value: str) -> bool:
    return _is_sealed_system_path(value, allow_root=False)


def _verify_system_linkage(slices: tuple[_MachOSlice, ...], label: str) -> None:
    for index, item in enumerate(slices):
        slice_label = f"{label} [{item.architecture} slice {index}]"
        if item.dylib_ids:
            raise TestAccountBundleEvidenceError(
                f"{slice_label}: LC_ID_DYLIB is not allowed in a system-only helper"
            )
        if item.rpaths:
            raise TestAccountBundleEvidenceError(
                f"{slice_label}: LC_RPATH is not allowed: {', '.join(item.rpaths)}"
            )
        if len(item.dylinkers) > 1 or (
            item.dylinkers and item.dylinkers[0] != "/usr/lib/dyld"
        ):
            raise TestAccountBundleEvidenceError(
                f"{slice_label}: LC_LOAD_DYLINKER must be exactly /usr/lib/dyld"
            )
        if item.dyld_environments:
            raise TestAccountBundleEvidenceError(
                f"{slice_label}: LC_DYLD_ENVIRONMENT is not allowed"
            )
        unexpected = [
            dependency
            for dependency in item.dependencies
            if not _is_sealed_system_dependency(dependency)
        ]
        if unexpected:
            raise TestAccountBundleEvidenceError(
                f"{slice_label}: non-system Mach-O dependency is not allowed: "
                + ", ".join(unexpected)
            )


def verify_dicom_helpers_system_linkage(
    resources: Path,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Require the two bundled DICOM helpers to have sealed-system linkage only."""

    bundled = _manifest_bundled(manifest)
    expected_paths = {
        "dicom_normalizer": "bin/totalsegmentator-wrapper-dicom-normalizer",
        "dcm2niix": "bin/dcm2niix",
    }
    helper_slices = 0
    for manifest_key, relative in expected_paths.items():
        _require_exact(bundled, manifest_key, relative, "setup manifest bundled")
        path = _resource_file(resources, relative, f"bundled {manifest_key}")
        slices = _parse_macho(_read_regular(path, f"bundled {manifest_key}"), str(path))
        _verify_system_linkage(slices, str(path))
        helper_slices += len(slices)
    return {"helper_count": len(expected_paths), "helper_slice_count": helper_slices}
