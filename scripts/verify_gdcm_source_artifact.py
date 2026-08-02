#!/usr/bin/env python3
"""Create and verify the immutable pinned GDCM static build receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

try:
    from scripts.collect_gdcm_source_licenses import (
        GDCM_SOURCE_SHA256,
        GDCM_SOURCE_URL,
        GDCM_VERSION,
        sha256_file,
        verify_gdcm_license_directory,
    )
except ModuleNotFoundError:  # Direct execution from scripts/.
    from collect_gdcm_source_licenses import (  # type: ignore[no-redef]
        GDCM_SOURCE_SHA256,
        GDCM_SOURCE_URL,
        GDCM_VERSION,
        sha256_file,
        verify_gdcm_license_directory,
    )


SCHEMA = "totalsegmentator_wrapper_mac.gdcm_source_build.v2"
RECEIPT_NAME = "gdcm-build-provenance.json"
PREFIX_NAME = "prefix"
LICENSES_NAME = "licenses"
MINIMUM_MACOS = "14.0"
ARCHITECTURE = "arm64"
SOURCE_DATE_EPOCH = 1_735_689_600
MAX_RECEIPT_BYTES = 1024 * 1024
TOOLCHAIN_SCHEMA = "totalsegmentator_wrapper_mac.macos_native_toolchain.v1"
TOOLCHAIN_SELECTIONS = {
    "cmake": "command-v-cmake",
    "xcrun": "command-v-xcrun",
    "compiler": "xcrun--find-clang",
    "cxx_compiler": "xcrun--find-clang++",
    "sdk": "xcrun--sdk-macosx--show-sdk-path",
}
_SDK_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*(?:[A-Za-z0-9._-]+)?$")
REQUIRED_STATIC_LIBRARIES = (
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
ENVIRONMENT_SCRUBBED = (
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
CMAKE_OPTIONS = (
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


class GDCMSourceArtifactError(RuntimeError):
    pass


def _owned_directory(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise GDCMSourceArtifactError(f"{label} is missing: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise GDCMSourceArtifactError(f"{label} must be a non-symlink directory: {path}")
    if metadata.st_uid != os.getuid():
        raise GDCMSourceArtifactError(f"{label} must be owned by the build user: {path}")
    return path.resolve(strict=True)


def _owned_regular(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise GDCMSourceArtifactError(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise GDCMSourceArtifactError(f"{label} must be a regular non-symlink file: {path}")
    if metadata.st_uid != os.getuid():
        raise GDCMSourceArtifactError(f"{label} must be owned by the build user: {path}")
    return path.resolve(strict=True)


def _resolved_executable(path: Path, label: str) -> Path:
    """Resolve a tool selected by the builder without recording its local path."""

    try:
        resolved = path.expanduser().resolve(strict=True)
        metadata = resolved.lstat()
    except OSError as exc:
        raise GDCMSourceArtifactError(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise GDCMSourceArtifactError(f"{label} must resolve to a regular file: {path}")
    if not os.access(resolved, os.X_OK):
        raise GDCMSourceArtifactError(f"{label} is not executable: {path}")
    return resolved


def _resolved_regular(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
        metadata = resolved.lstat()
    except OSError as exc:
        raise GDCMSourceArtifactError(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise GDCMSourceArtifactError(f"{label} must resolve to a regular file: {path}")
    return resolved


def _resolved_directory(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
        metadata = resolved.lstat()
    except OSError as exc:
        raise GDCMSourceArtifactError(f"{label} is missing: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise GDCMSourceArtifactError(f"{label} must resolve to a directory: {path}")
    return resolved


def _run_tool_identity(executable: Path, arguments: Sequence[str], label: str) -> str:
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GDCMSourceArtifactError(f"could not inspect {label}") from exc
    output = completed.stdout.strip()
    if not output:
        raise GDCMSourceArtifactError(f"{label} produced no output")
    return output


def _identity_version(executable: Path, arguments: Sequence[str], label: str) -> str:
    return _run_tool_identity(executable, arguments, label).splitlines()[0].strip()


def _validated_toolchain_text(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 256
        or any(token in value for token in ("/", "\\", "\n", "\r", "\x00", "~"))
    ):
        raise GDCMSourceArtifactError(f"GDCM toolchain {label} is invalid")
    return value


def _toolchain_record(
    value: object,
    *,
    name: str,
    digest_key: str = "binary_sha256",
) -> dict[str, Any]:
    expected_selection = TOOLCHAIN_SELECTIONS[name]
    if not isinstance(value, dict) or set(value) != {
        "selection", "version", digest_key
    }:
        raise GDCMSourceArtifactError(f"GDCM toolchain {name} record is invalid")
    if value.get("selection") != expected_selection:
        raise GDCMSourceArtifactError(f"GDCM toolchain {name} selection mismatch")
    version = _validated_toolchain_text(value.get("version"), f"{name} version")
    digest = value.get(digest_key)
    if not _is_sha256(digest):
        raise GDCMSourceArtifactError(f"GDCM toolchain {name} digest is invalid")
    return {
        "selection": expected_selection,
        "version": version,
        digest_key: digest,
    }


def validate_toolchain_identity(value: object) -> dict[str, Any]:
    """Validate the portable, path-free identity of a macOS native toolchain."""

    if not isinstance(value, dict) or set(value) != {
        "schema", "cmake", "xcrun", "compiler", "cxx_compiler", "sdk"
    }:
        raise GDCMSourceArtifactError("GDCM build receipt toolchain is invalid")
    if value.get("schema") != TOOLCHAIN_SCHEMA:
        raise GDCMSourceArtifactError("GDCM toolchain schema mismatch")
    sdk = _toolchain_record(
        value.get("sdk"), name="sdk", digest_key="settings_sha256"
    )
    if not _SDK_VERSION.fullmatch(sdk["version"]):
        raise GDCMSourceArtifactError("GDCM toolchain SDK version is invalid")
    return {
        "schema": TOOLCHAIN_SCHEMA,
        "cmake": _toolchain_record(value.get("cmake"), name="cmake"),
        "xcrun": _toolchain_record(value.get("xcrun"), name="xcrun"),
        "compiler": _toolchain_record(value.get("compiler"), name="compiler"),
        "cxx_compiler": _toolchain_record(
            value.get("cxx_compiler"), name="cxx_compiler"
        ),
        "sdk": sdk,
    }


def toolchain_identity_from_json(value: str, *, label: str) -> dict[str, Any]:
    try:
        parsed: Any = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise GDCMSourceArtifactError(f"invalid {label} JSON") from exc
    return validate_toolchain_identity(parsed)


def capture_toolchain_identity(
    *,
    cmake_path: Path,
    xcrun_path: Path,
    compiler_path: Path,
    cxx_compiler_path: Path,
    sdk_root: Path,
) -> dict[str, Any]:
    """Capture the exact non-hermetic macOS tools used by a native build.

    Paths are only used while capturing and checking selection.  The receipt
    stores versions, fixed selection rules, and content hashes so it remains
    portable and does not leak a local Xcode installation path.
    """

    cmake = _resolved_executable(cmake_path, "CMake executable")
    xcrun = _resolved_executable(xcrun_path, "xcrun executable")
    compiler = _resolved_executable(compiler_path, "C compiler")
    cxx_compiler = _resolved_executable(cxx_compiler_path, "C++ compiler")
    selected_sdk = _resolved_directory(
        Path(
            _run_tool_identity(
                xcrun,
                ("--sdk", "macosx", "--show-sdk-path"),
                "xcrun macOS SDK path",
            )
        ),
        "xcrun macOS SDK root",
    )
    supplied_sdk = _resolved_directory(sdk_root, "supplied macOS SDK root")
    if selected_sdk != supplied_sdk:
        raise GDCMSourceArtifactError("xcrun macOS SDK selection differs from the build SDK")
    selected_compiler = _resolved_executable(
        Path(_run_tool_identity(xcrun, ("--find", "clang"), "xcrun clang path")),
        "xcrun clang",
    )
    selected_cxx_compiler = _resolved_executable(
        Path(
            _run_tool_identity(xcrun, ("--find", "clang++"), "xcrun clang++ path")
        ),
        "xcrun clang++",
    )
    if selected_compiler != compiler or selected_cxx_compiler != cxx_compiler:
        raise GDCMSourceArtifactError(
            "xcrun compiler selection differs from the build compiler"
        )
    sdk_settings = selected_sdk / "SDKSettings.json"
    sdk_settings = _resolved_regular(sdk_settings, "macOS SDKSettings.json")
    try:
        sdk_settings.relative_to(selected_sdk)
    except ValueError as exc:
        raise GDCMSourceArtifactError("macOS SDKSettings.json escapes the selected SDK") from exc

    return validate_toolchain_identity(
        {
            "schema": TOOLCHAIN_SCHEMA,
            "cmake": {
                "selection": TOOLCHAIN_SELECTIONS["cmake"],
                "version": _identity_version(cmake, ("--version",), "CMake version"),
                "binary_sha256": sha256_file(cmake),
            },
            "xcrun": {
                "selection": TOOLCHAIN_SELECTIONS["xcrun"],
                "version": _identity_version(xcrun, ("--version",), "xcrun version"),
                "binary_sha256": sha256_file(xcrun),
            },
            "compiler": {
                "selection": TOOLCHAIN_SELECTIONS["compiler"],
                "version": _identity_version(
                    compiler, ("--version",), "C compiler version"
                ),
                "binary_sha256": sha256_file(compiler),
            },
            "cxx_compiler": {
                "selection": TOOLCHAIN_SELECTIONS["cxx_compiler"],
                "version": _identity_version(
                    cxx_compiler, ("--version",), "C++ compiler version"
                ),
                "binary_sha256": sha256_file(cxx_compiler),
            },
            "sdk": {
                "selection": TOOLCHAIN_SELECTIONS["sdk"],
                "version": _run_tool_identity(
                    xcrun,
                    ("--sdk", "macosx", "--show-sdk-version"),
                    "xcrun macOS SDK version",
                ).strip(),
                "settings_sha256": sha256_file(sdk_settings),
            },
        }
    )


def hash_regular_tree(root: Path) -> str:
    """Hash all regular files and their relative names; reject ambiguous entries."""

    root = _owned_directory(root, "GDCM installed prefix")
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in root.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise GDCMSourceArtifactError(f"GDCM installed prefix contains a symlink: {path}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise GDCMSourceArtifactError(
                f"GDCM installed prefix contains a special filesystem entry: {path}"
            )
        if metadata.st_uid != os.getuid():
            raise GDCMSourceArtifactError(
                f"GDCM installed file must be owned by the build user: {path}"
            )
        files.append(path)
    if not files:
        raise GDCMSourceArtifactError("GDCM installed prefix is empty")
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content_digest = bytes.fromhex(sha256_file(path))
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        digest.update(content_digest)
    return digest.hexdigest()


def _required_library_hashes(prefix: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative_text in REQUIRED_STATIC_LIBRARIES:
        relative = PurePosixPath(relative_text)
        path = prefix.joinpath(*relative.parts)
        _owned_regular(path, f"required GDCM static library {relative_text}")
        hashes[relative_text] = sha256_file(path)
    return hashes


def _assert_no_forbidden_payloads(prefix: Path) -> None:
    dylibs = sorted(path for path in prefix.rglob("*.dylib") if path.is_file())
    gdcmconv = sorted(path for path in prefix.rglob("gdcmconv") if path.is_file())
    if dylibs:
        raise GDCMSourceArtifactError(
            "GDCM static artifact contains dynamic libraries: "
            + ", ".join(str(path) for path in dylibs)
        )
    if gdcmconv:
        raise GDCMSourceArtifactError("GDCM static artifact unexpectedly contains gdcmconv")


def create_receipt(
    artifact_directory: Path,
    *,
    toolchain: object,
) -> Path:
    artifact_directory = _owned_directory(artifact_directory, "GDCM artifact directory")
    prefix = _owned_directory(artifact_directory / PREFIX_NAME, "GDCM installed prefix")
    licenses = _owned_directory(artifact_directory / LICENSES_NAME, "GDCM license directory")
    receipt = artifact_directory / RECEIPT_NAME
    if receipt.exists() or receipt.is_symlink():
        raise GDCMSourceArtifactError(f"GDCM build receipt already exists: {receipt}")
    inventory = verify_gdcm_license_directory(licenses)
    _assert_no_forbidden_payloads(prefix)
    toolchain = validate_toolchain_identity(toolchain)
    payload = {
        "schema": SCHEMA,
        "gdcm_version": GDCM_VERSION,
        "source_url": GDCM_SOURCE_URL,
        "source_archive_sha256": GDCM_SOURCE_SHA256,
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "linkage": "static",
        "gdcmconv_bundled": False,
        "environment_scrubbed": list(ENVIRONMENT_SCRUBBED),
        "cmake_options": list(CMAKE_OPTIONS),
        "prefix_relpath": PREFIX_NAME,
        "prefix_tree_sha256": hash_regular_tree(prefix),
        "license_inventory_sha256": sha256_file(inventory),
        "required_static_libraries": _required_library_hashes(prefix),
        "toolchain": toolchain,
    }
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_receipt_payload(payload: object) -> dict[str, Any]:
    """Strictly validate portable GDCM receipt fields without the artifact tree."""

    if not isinstance(payload, dict):
        raise GDCMSourceArtifactError("GDCM build receipt must be a JSON object")
    expected_keys = {
        "schema", "gdcm_version", "source_url", "source_archive_sha256",
        "minimum_macos", "architecture", "source_date_epoch", "linkage",
        "gdcmconv_bundled", "environment_scrubbed", "cmake_options",
        "prefix_relpath", "prefix_tree_sha256", "license_inventory_sha256",
        "required_static_libraries", "toolchain",
    }
    if set(payload) != expected_keys:
        raise GDCMSourceArtifactError("GDCM build receipt field set mismatch")
    expected_values = {
        "schema": SCHEMA,
        "gdcm_version": GDCM_VERSION,
        "source_url": GDCM_SOURCE_URL,
        "source_archive_sha256": GDCM_SOURCE_SHA256,
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "linkage": "static",
        "gdcmconv_bundled": False,
        "environment_scrubbed": list(ENVIRONMENT_SCRUBBED),
        "cmake_options": list(CMAKE_OPTIONS),
        "prefix_relpath": PREFIX_NAME,
    }
    for key, expected in expected_values.items():
        if payload.get(key) != expected:
            raise GDCMSourceArtifactError(f"GDCM build receipt {key} mismatch")
    for key in ("prefix_tree_sha256", "license_inventory_sha256"):
        if not _is_sha256(payload.get(key)):
            raise GDCMSourceArtifactError(f"GDCM build receipt {key} is invalid")
    libraries = payload.get("required_static_libraries")
    if not isinstance(libraries, dict) or set(libraries) != set(REQUIRED_STATIC_LIBRARIES):
        raise GDCMSourceArtifactError("GDCM build receipt static library set mismatch")
    if any(not _is_sha256(value) for value in libraries.values()):
        raise GDCMSourceArtifactError("GDCM build receipt static library digest is invalid")
    validate_toolchain_identity(payload.get("toolchain"))
    return payload


def verify_artifact(
    artifact_directory: Path,
    *,
    expected_toolchain: object | None = None,
) -> Path:
    artifact_directory = _owned_directory(artifact_directory, "GDCM artifact directory")
    receipt_path = _owned_regular(
        artifact_directory / RECEIPT_NAME, "GDCM build receipt"
    )
    if receipt_path.stat().st_size > MAX_RECEIPT_BYTES:
        raise GDCMSourceArtifactError("GDCM build receipt exceeds the safe size limit")
    try:
        payload: Any = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GDCMSourceArtifactError(f"invalid GDCM build receipt: {exc}") from exc
    payload = validate_receipt_payload(payload)
    prefix = _owned_directory(
        artifact_directory / str(payload["prefix_relpath"]), "GDCM installed prefix"
    )
    licenses = _owned_directory(artifact_directory / LICENSES_NAME, "GDCM license directory")
    receipt_toolchain = validate_toolchain_identity(payload.get("toolchain"))
    if expected_toolchain is not None and receipt_toolchain != validate_toolchain_identity(
        expected_toolchain
    ):
        raise GDCMSourceArtifactError(
            "GDCM build receipt toolchain differs from the expected toolchain"
        )
    _assert_no_forbidden_payloads(prefix)
    if payload.get("prefix_tree_sha256") != hash_regular_tree(prefix):
        raise GDCMSourceArtifactError("GDCM installed prefix integrity mismatch")
    if payload.get("required_static_libraries") != _required_library_hashes(prefix):
        raise GDCMSourceArtifactError("GDCM required static library integrity mismatch")
    inventory = verify_gdcm_license_directory(licenses)
    if payload.get("license_inventory_sha256") != sha256_file(inventory):
        raise GDCMSourceArtifactError("GDCM license inventory integrity mismatch")
    return prefix


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or verify a pinned GDCM source artifact receipt.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--capture-toolchain", action="store_true")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--toolchain-json")
    parser.add_argument("--expected-toolchain-json")
    parser.add_argument("--cmake-path", type=Path)
    parser.add_argument("--xcrun-path", type=Path)
    parser.add_argument("--compiler-path", type=Path)
    parser.add_argument("--cxx-compiler-path", type=Path)
    parser.add_argument("--sdk-root", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.capture_toolchain:
        fields = (
            "cmake_path",
            "xcrun_path",
            "compiler_path",
            "cxx_compiler_path",
            "sdk_root",
        )
        for name in fields:
            if getattr(args, name) is None:
                raise GDCMSourceArtifactError(
                    f"--{name.replace('_', '-')} is required with --capture-toolchain"
                )
        identity = capture_toolchain_identity(
            cmake_path=args.cmake_path,
            xcrun_path=args.xcrun_path,
            compiler_path=args.compiler_path,
            cxx_compiler_path=args.cxx_compiler_path,
            sdk_root=args.sdk_root,
        )
        print(json.dumps(identity, sort_keys=True, separators=(",", ":")))
        return 0
    if args.artifact_dir is None:
        raise GDCMSourceArtifactError("--artifact-dir is required with --create or --verify")
    artifact = args.artifact_dir.expanduser()
    if args.create and args.expected_toolchain_json:
        raise GDCMSourceArtifactError("--expected-toolchain-json is valid only with --verify")
    if args.create:
        if not args.toolchain_json:
            raise GDCMSourceArtifactError("--toolchain-json is required with --create")
        create_receipt(
            artifact,
            toolchain=toolchain_identity_from_json(
                args.toolchain_json, label="GDCM toolchain"
            ),
        )
    expected_toolchain = (
        toolchain_identity_from_json(
            args.expected_toolchain_json, label="expected GDCM toolchain"
        )
        if args.expected_toolchain_json
        else None
    )
    prefix = verify_artifact(artifact, expected_toolchain=expected_toolchain)
    print(prefix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
