#!/usr/bin/env python3
"""Create and verify the source-built static GDCM normalizer artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.collect_gdcm_source_licenses import (
        GDCM_SOURCE_SHA256,
        GDCM_SOURCE_URL,
        GDCM_VERSION,
        sha256_file,
        verify_gdcm_license_directory,
    )
    from scripts.verify_gdcm_source_artifact import (
        ENVIRONMENT_SCRUBBED,
        GDCMSourceArtifactError,
        RECEIPT_NAME as GDCM_RECEIPT_NAME,
        capture_toolchain_identity,
        toolchain_identity_from_json,
        validate_toolchain_identity,
        validate_receipt_payload as validate_gdcm_receipt_payload,
        verify_artifact as verify_gdcm_artifact,
    )
except ModuleNotFoundError:  # Direct execution from scripts/.
    from collect_gdcm_source_licenses import (  # type: ignore[no-redef]
        GDCM_SOURCE_SHA256,
        GDCM_SOURCE_URL,
        GDCM_VERSION,
        sha256_file,
        verify_gdcm_license_directory,
    )
    from verify_gdcm_source_artifact import (  # type: ignore[no-redef]
        ENVIRONMENT_SCRUBBED,
        GDCMSourceArtifactError,
        RECEIPT_NAME as GDCM_RECEIPT_NAME,
        capture_toolchain_identity,
        toolchain_identity_from_json,
        validate_toolchain_identity,
        validate_receipt_payload as validate_gdcm_receipt_payload,
        verify_artifact as verify_gdcm_artifact,
    )


SCHEMA = "totalsegmentator_wrapper_mac.dicom_normalizer_source_build.v2"
RECEIPT_NAME = "dicom-normalizer-build-provenance.json"
BINARY_NAME = "totalsegmentator-wrapper-dicom-normalizer"
MINIMUM_MACOS = "14.0"
ARCHITECTURE = "arm64"
SOURCE_DATE_EPOCH = 1_735_689_600
LINKAGE = "static-gdcm-3.2.7-system-only-no-rpath"
CMAKE_OPTIONS = (
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
MAX_RECEIPT_BYTES = 1024 * 1024


class DicomNormalizerArtifactError(RuntimeError):
    pass


def _owned_directory(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DicomNormalizerArtifactError(f"{label} is missing: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise DicomNormalizerArtifactError(f"{label} must be a non-symlink directory: {path}")
    if metadata.st_uid != os.getuid():
        raise DicomNormalizerArtifactError(f"{label} must be owned by the build user: {path}")
    return path.resolve(strict=True)


def _owned_regular(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DicomNormalizerArtifactError(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise DicomNormalizerArtifactError(f"{label} must be a regular non-symlink file: {path}")
    if metadata.st_uid != os.getuid():
        raise DicomNormalizerArtifactError(f"{label} must be owned by the build user: {path}")
    return path.resolve(strict=True)


def hash_source_tree(source_directory: Path) -> str:
    source_directory = _owned_directory(source_directory, "DICOM normalizer source directory")
    digest = hashlib.sha256()
    files: list[Path] = [source_directory / "CMakeLists.txt"]
    files.extend((source_directory / "src").rglob("*"))
    checked_files: list[Path] = []
    for path in files:
        if not path.exists() and not path.is_symlink():
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise DicomNormalizerArtifactError(f"normalizer source contains a symlink: {path}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise DicomNormalizerArtifactError(f"normalizer source contains a special entry: {path}")
        checked_files.append(path)
    if not checked_files:
        raise DicomNormalizerArtifactError("DICOM normalizer source directory is empty")
    for path in sorted(checked_files, key=lambda item: item.relative_to(source_directory).as_posix()):
        relative = path.relative_to(source_directory).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    path = _owned_regular(path, label)
    if path.stat().st_size > MAX_RECEIPT_BYTES:
        raise DicomNormalizerArtifactError(f"{label} exceeds the safe size limit")
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DicomNormalizerArtifactError(f"invalid {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DicomNormalizerArtifactError(f"{label} must be a JSON object")
    return payload


def _validate_gdcm_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return validate_gdcm_receipt_payload(payload)
    except GDCMSourceArtifactError as exc:
        raise DicomNormalizerArtifactError(f"copied GDCM build receipt is invalid: {exc}") from exc


def _validate_toolchain_identity(value: object, *, label: str) -> dict[str, Any]:
    try:
        return validate_toolchain_identity(value)
    except GDCMSourceArtifactError as exc:
        raise DicomNormalizerArtifactError(f"{label} toolchain is invalid: {exc}") from exc


def _assert_matching_toolchains(
    normalizer_toolchain: object,
    gdcm_toolchain: object,
) -> dict[str, Any]:
    normalizer = _validate_toolchain_identity(normalizer_toolchain, label="normalizer")
    gdcm = _validate_toolchain_identity(gdcm_toolchain, label="copied GDCM")
    if normalizer != gdcm:
        raise DicomNormalizerArtifactError(
            "DICOM normalizer and copied GDCM toolchain identities differ"
        )
    return normalizer


def _toolchain_from_json(value: str, *, label: str) -> dict[str, Any]:
    try:
        return toolchain_identity_from_json(value, label=label)
    except GDCMSourceArtifactError as exc:
        raise DicomNormalizerArtifactError(f"invalid {label}: {exc}") from exc


def source_manifest_from_packaged_receipts(
    receipt: dict[str, Any],
    *,
    receipt_bytes: bytes,
    gdcm_receipt_bytes: bytes,
) -> dict[str, object]:
    return {
        "kind": "source-built-static-gdcm",
        "release_eligible": True,
        "binary_sha256": receipt["binary_sha256"],
        "native_source_sha256": receipt["native_source_sha256"],
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "linkage": LINKAGE,
        "gdcm_version": GDCM_VERSION,
        "gdcm_source_url": GDCM_SOURCE_URL,
        "gdcm_source_archive_sha256": GDCM_SOURCE_SHA256,
        "build_receipt": f"licenses/{RECEIPT_NAME}",
        "build_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "gdcm_build_receipt": f"licenses/{GDCM_RECEIPT_NAME}",
        "gdcm_build_receipt_sha256": hashlib.sha256(gdcm_receipt_bytes).hexdigest(),
        "license_inventory": "licenses/GDCM-static-license-inventory.json",
        "license_inventory_sha256": receipt["license_inventory_sha256"],
    }


def validate_packaged_provenance(
    source: object,
    *,
    binary_input_sha256: str,
    receipt_bytes: bytes,
    gdcm_receipt_bytes: bytes,
    license_inventory_bytes: bytes,
) -> None:
    """Validate receipts copied into a signed app/wheel after binary signing."""

    try:
        receipt: Any = json.loads(receipt_bytes)
        gdcm_receipt: Any = json.loads(gdcm_receipt_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DicomNormalizerArtifactError(f"invalid packaged normalizer provenance JSON: {exc}") from exc
    if not isinstance(receipt, dict) or not isinstance(gdcm_receipt, dict):
        raise DicomNormalizerArtifactError("packaged normalizer provenance must contain JSON objects")
    gdcm_receipt = _validate_gdcm_receipt(gdcm_receipt)
    expected_keys = {
        "schema", "binary", "binary_sha256", "native_source_sha256",
        "minimum_macos", "architecture", "source_date_epoch", "linkage",
        "environment_scrubbed", "cmake_options", "license_inventory_sha256",
        "gdcm_build_receipt", "gdcm_build_receipt_sha256",
        "gdcm_prefix_tree_sha256", "gdcm_source_url",
        "gdcm_source_archive_sha256", "toolchain",
    }
    if set(receipt) != expected_keys:
        raise DicomNormalizerArtifactError("packaged normalizer build receipt field set mismatch")
    fixed = {
        "schema": SCHEMA,
        "binary": BINARY_NAME,
        "binary_sha256": binary_input_sha256,
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "linkage": LINKAGE,
        "environment_scrubbed": list(ENVIRONMENT_SCRUBBED),
        "cmake_options": list(CMAKE_OPTIONS),
        "license_inventory_sha256": hashlib.sha256(license_inventory_bytes).hexdigest(),
        "gdcm_build_receipt": GDCM_RECEIPT_NAME,
        "gdcm_build_receipt_sha256": hashlib.sha256(gdcm_receipt_bytes).hexdigest(),
        "gdcm_prefix_tree_sha256": gdcm_receipt.get("prefix_tree_sha256"),
        "gdcm_source_url": GDCM_SOURCE_URL,
        "gdcm_source_archive_sha256": GDCM_SOURCE_SHA256,
    }
    for key, value in fixed.items():
        if receipt.get(key) != value:
            raise DicomNormalizerArtifactError(f"packaged normalizer receipt {key} mismatch")
    native_source_sha = receipt.get("native_source_sha256")
    if not isinstance(native_source_sha, str) or len(native_source_sha) != 64:
        raise DicomNormalizerArtifactError("packaged normalizer native source SHA-256 is invalid")
    _assert_matching_toolchains(receipt.get("toolchain"), gdcm_receipt.get("toolchain"))
    expected_source = source_manifest_from_packaged_receipts(
        receipt,
        receipt_bytes=receipt_bytes,
        gdcm_receipt_bytes=gdcm_receipt_bytes,
    )
    if source != expected_source:
        raise DicomNormalizerArtifactError(
            "normalizer_source does not match the packaged source-build provenance"
        )


def create_receipt(
    artifact_directory: Path,
    *,
    source_directory: Path,
    gdcm_artifact_directory: Path,
    toolchain: object,
) -> Path:
    artifact_directory = _owned_directory(artifact_directory, "DICOM normalizer artifact")
    binary = _owned_regular(artifact_directory / BINARY_NAME, "DICOM normalizer binary")
    if not os.access(binary, os.X_OK):
        raise DicomNormalizerArtifactError("DICOM normalizer binary is not executable")
    licenses = _owned_directory(artifact_directory / "licenses", "DICOM normalizer licenses")
    inventory = verify_gdcm_license_directory(licenses)
    toolchain = _validate_toolchain_identity(toolchain, label="normalizer")
    gdcm_prefix = verify_gdcm_artifact(
        gdcm_artifact_directory, expected_toolchain=toolchain
    )
    gdcm_receipt_source = gdcm_prefix.parent / GDCM_RECEIPT_NAME
    copied_gdcm_receipt = artifact_directory / GDCM_RECEIPT_NAME
    if copied_gdcm_receipt.exists() or copied_gdcm_receipt.is_symlink():
        raise DicomNormalizerArtifactError("copied GDCM receipt must not exist before receipt creation")
    copied_gdcm_receipt.write_bytes(gdcm_receipt_source.read_bytes())
    gdcm_receipt = _read_json(copied_gdcm_receipt, "copied GDCM build receipt")
    gdcm_receipt = _validate_gdcm_receipt(gdcm_receipt)
    _assert_matching_toolchains(toolchain, gdcm_receipt.get("toolchain"))
    receipt_path = artifact_directory / RECEIPT_NAME
    if receipt_path.exists() or receipt_path.is_symlink():
        raise DicomNormalizerArtifactError("DICOM normalizer build receipt already exists")
    payload = {
        "schema": SCHEMA,
        "binary": BINARY_NAME,
        "binary_sha256": sha256_file(binary),
        "native_source_sha256": hash_source_tree(source_directory),
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "linkage": LINKAGE,
        "environment_scrubbed": list(ENVIRONMENT_SCRUBBED),
        "cmake_options": list(CMAKE_OPTIONS),
        "license_inventory_sha256": sha256_file(inventory),
        "gdcm_build_receipt": GDCM_RECEIPT_NAME,
        "gdcm_build_receipt_sha256": sha256_file(copied_gdcm_receipt),
        "gdcm_prefix_tree_sha256": gdcm_receipt["prefix_tree_sha256"],
        "gdcm_source_url": GDCM_SOURCE_URL,
        "gdcm_source_archive_sha256": GDCM_SOURCE_SHA256,
        "toolchain": toolchain,
    }
    receipt_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt_path


def verify_artifact(
    artifact_directory: Path,
    *,
    source_directory: Path,
    expected_toolchain: object | None = None,
) -> Path:
    artifact_directory = _owned_directory(artifact_directory, "DICOM normalizer artifact")
    expected_root_names = {
        BINARY_NAME,
        "licenses",
        GDCM_RECEIPT_NAME,
        RECEIPT_NAME,
    }
    if {path.name for path in artifact_directory.iterdir()} != expected_root_names:
        raise DicomNormalizerArtifactError("DICOM normalizer artifact file set mismatch")
    binary = _owned_regular(artifact_directory / BINARY_NAME, "DICOM normalizer binary")
    if not os.access(binary, os.X_OK):
        raise DicomNormalizerArtifactError("DICOM normalizer binary is not executable")
    inventory = verify_gdcm_license_directory(artifact_directory / "licenses")
    gdcm_receipt_path = artifact_directory / GDCM_RECEIPT_NAME
    gdcm_receipt = _read_json(gdcm_receipt_path, "copied GDCM build receipt")
    gdcm_receipt = _validate_gdcm_receipt(gdcm_receipt)
    receipt = _read_json(
        artifact_directory / RECEIPT_NAME, "DICOM normalizer build receipt"
    )
    expected_keys = {
        "schema", "binary", "binary_sha256", "native_source_sha256",
        "minimum_macos", "architecture", "source_date_epoch", "linkage",
        "environment_scrubbed", "cmake_options", "license_inventory_sha256",
        "gdcm_build_receipt", "gdcm_build_receipt_sha256",
        "gdcm_prefix_tree_sha256", "gdcm_source_url",
        "gdcm_source_archive_sha256", "toolchain",
    }
    if set(receipt) != expected_keys:
        raise DicomNormalizerArtifactError("DICOM normalizer build receipt field set mismatch")
    expected = {
        "schema": SCHEMA,
        "binary": BINARY_NAME,
        "binary_sha256": sha256_file(binary),
        "native_source_sha256": hash_source_tree(source_directory),
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "linkage": LINKAGE,
        "environment_scrubbed": list(ENVIRONMENT_SCRUBBED),
        "cmake_options": list(CMAKE_OPTIONS),
        "license_inventory_sha256": sha256_file(inventory),
        "gdcm_build_receipt": GDCM_RECEIPT_NAME,
        "gdcm_build_receipt_sha256": sha256_file(gdcm_receipt_path),
        "gdcm_prefix_tree_sha256": gdcm_receipt.get("prefix_tree_sha256"),
        "gdcm_source_url": GDCM_SOURCE_URL,
        "gdcm_source_archive_sha256": GDCM_SOURCE_SHA256,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise DicomNormalizerArtifactError(f"DICOM normalizer build receipt {key} mismatch")
    receipt_toolchain = _assert_matching_toolchains(
        receipt.get("toolchain"), gdcm_receipt.get("toolchain")
    )
    if expected_toolchain is not None and receipt_toolchain != _validate_toolchain_identity(
        expected_toolchain, label="expected normalizer"
    ):
        raise DicomNormalizerArtifactError(
            "DICOM normalizer receipt toolchain differs from the expected toolchain"
        )
    forbidden = [
        path for path in artifact_directory.rglob("*")
        if path.is_file() and (path.suffix == ".dylib" or path.name == "gdcmconv")
    ]
    if forbidden:
        raise DicomNormalizerArtifactError("DICOM normalizer artifact contains a forbidden runtime payload")
    return binary


def source_manifest(artifact_directory: Path, *, source_directory: Path) -> dict[str, object]:
    binary = verify_artifact(artifact_directory, source_directory=source_directory)
    artifact_directory = binary.parent
    receipt_path = artifact_directory / RECEIPT_NAME
    gdcm_receipt_path = artifact_directory / GDCM_RECEIPT_NAME
    receipt = _read_json(receipt_path, "DICOM normalizer build receipt")
    return source_manifest_from_packaged_receipts(
        receipt,
        receipt_bytes=receipt_path.read_bytes(),
        gdcm_receipt_bytes=gdcm_receipt_path.read_bytes(),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or verify the static GDCM normalizer artifact.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--capture-toolchain", action="store_true")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--gdcm-artifact-dir", type=Path)
    parser.add_argument("--toolchain-json")
    parser.add_argument("--expected-toolchain-json")
    parser.add_argument("--cmake-path", type=Path)
    parser.add_argument("--xcrun-path", type=Path)
    parser.add_argument("--compiler-path", type=Path)
    parser.add_argument("--cxx-compiler-path", type=Path)
    parser.add_argument("--sdk-root", type=Path)
    parser.add_argument("--json", action="store_true")
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
                raise DicomNormalizerArtifactError(
                    f"--{name.replace('_', '-')} is required with --capture-toolchain"
                )
        try:
            identity = capture_toolchain_identity(
                cmake_path=args.cmake_path,
                xcrun_path=args.xcrun_path,
                compiler_path=args.compiler_path,
                cxx_compiler_path=args.cxx_compiler_path,
                sdk_root=args.sdk_root,
            )
        except GDCMSourceArtifactError as exc:
            raise DicomNormalizerArtifactError(f"could not capture normalizer toolchain: {exc}") from exc
        print(json.dumps(identity, sort_keys=True, separators=(",", ":")))
        return 0
    if args.artifact_dir is None or args.source_dir is None:
        raise DicomNormalizerArtifactError(
            "--artifact-dir and --source-dir are required with --create or --verify"
        )
    if args.create and args.expected_toolchain_json:
        raise DicomNormalizerArtifactError("--expected-toolchain-json is valid only with --verify")
    if args.create:
        if args.gdcm_artifact_dir is None:
            raise DicomNormalizerArtifactError("--gdcm-artifact-dir is required with --create")
        if not args.toolchain_json:
            raise DicomNormalizerArtifactError("--toolchain-json is required with --create")
        create_receipt(
            args.artifact_dir,
            source_directory=args.source_dir,
            gdcm_artifact_directory=args.gdcm_artifact_dir,
            toolchain=_toolchain_from_json(
                args.toolchain_json, label="normalizer toolchain"
            ),
        )
    expected_toolchain = (
        _toolchain_from_json(
            args.expected_toolchain_json, label="expected normalizer toolchain"
        )
        if args.expected_toolchain_json
        else None
    )
    binary = verify_artifact(
        args.artifact_dir,
        source_directory=args.source_dir,
        expected_toolchain=expected_toolchain,
    )
    if args.json:
        print(json.dumps({"binary_path": str(binary), "source": source_manifest(args.artifact_dir, source_directory=args.source_dir)}, sort_keys=True))
    else:
        print(binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
