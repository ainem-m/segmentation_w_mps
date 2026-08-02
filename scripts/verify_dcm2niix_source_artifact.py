#!/usr/bin/env python3
"""Verify the pinned, source-built dcm2niix artifact used for Mac releases.

The source builder publishes an immutable content-addressed directory and only
then atomically replaces ``current-artifact.json``.  Packaging resolves that
pointer instead of selecting the newest file or trusting a caller-supplied
basename.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


CURRENT_POINTER_SCHEMA = (
    "totalsegmentator_wrapper_mac.dcm2niix_current_artifact.v1"
)
BUILD_RECEIPT_SCHEMA = "totalsegmentator_wrapper_mac.dcm2niix_source_build.v2"
RELEASE_TAG = "v1.0.20250506"
EXPECTED_CLI_VERSION = "v1.0.20250505"
SOURCE_URL = (
    "https://github.com/rordenlab/dcm2niix/archive/refs/tags/"
    "v1.0.20250506.tar.gz"
)
SOURCE_ARCHIVE_SHA256 = (
    "1b24658678b6c24141e58760dbea9fe2786ffdd736bcc37a36d9cdabc731bafa"
)
LICENSE_SHA256 = (
    "a423e1c074ff39d9c22843489dd81bbaf42d4fa243fd785f8e96ce084db2e503"
)
MINIMUM_MACOS = "14.0"
ARCHITECTURE = "arm64"
SOURCE_DATE_EPOCH = 1_746_489_600
CURRENT_POINTER_NAME = "current-artifact.json"
BINARY_NAME = "dcm2niix"
LICENSE_RELATIVE_PATH = PurePosixPath("licenses/dcm2niix-license.txt")
RECEIPT_NAME = "dcm2niix-build-provenance.json"
SHA256_LENGTH = 64
MAX_JSON_BYTES = 1024 * 1024
MAX_LICENSE_BYTES = 1024 * 1024


class Dcm2niixSourceArtifactError(RuntimeError):
    """The pinned release artifact or its provenance is not trustworthy."""


@dataclass(frozen=True)
class VerifiedDcm2niixArtifact:
    build_root: Path
    artifact_directory: Path
    binary: Path
    license: Path
    receipt: Path
    pointer: Path
    binary_sha256: str
    receipt_sha256: str
    pointer_sha256: str

    def source_manifest(self) -> dict[str, object]:
        return {
            "kind": "pinned-official-source-build",
            "release_eligible": True,
            "release_tag": RELEASE_TAG,
            "expected_cli_version": EXPECTED_CLI_VERSION,
            "source_url": SOURCE_URL,
            "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
            "source_date_epoch": SOURCE_DATE_EPOCH,
            "minimum_macos": MINIMUM_MACOS,
            "architecture": ARCHITECTURE,
            "binary_sha256": self.binary_sha256,
            "license": "licenses/dcm2niix-license.txt",
            "license_sha256": LICENSE_SHA256,
            "build_receipt": "licenses/dcm2niix-build-provenance.json",
            "build_receipt_sha256": self.receipt_sha256,
            "artifact_pointer": "licenses/dcm2niix-current-artifact.json",
            "artifact_pointer_sha256": self.pointer_sha256,
            "linkage": "system-only-no-rpath",
        }

    def cli_payload(self) -> dict[str, object]:
        return {
            "binary_path": str(self.binary),
            "license_path": str(self.license),
            "receipt_path": str(self.receipt),
            "pointer_path": str(self.pointer),
            "source": self.source_manifest(),
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_file(path: Path, label: str, maximum_size: int | None = None) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise Dcm2niixSourceArtifactError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise Dcm2niixSourceArtifactError(
            f"{label} must be a regular non-symlink file: {path}"
        )
    if metadata.st_uid != os.getuid():
        raise Dcm2niixSourceArtifactError(
            f"{label} must be owned by the packaging user: {path}"
        )
    if maximum_size is not None and metadata.st_size > maximum_size:
        raise Dcm2niixSourceArtifactError(
            f"{label} exceeds the safe size limit: {path}"
        )


def _require_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise Dcm2niixSourceArtifactError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise Dcm2niixSourceArtifactError(
            f"{label} must be a non-symlink directory: {path}"
        )
    if metadata.st_uid != os.getuid():
        raise Dcm2niixSourceArtifactError(
            f"{label} must be owned by the packaging user: {path}"
        )


def _read_json(path: Path, label: str) -> dict[str, Any]:
    _require_regular_file(path, label, MAX_JSON_BYTES)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Dcm2niixSourceArtifactError(f"{label} is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise Dcm2niixSourceArtifactError(f"{label} must contain a JSON object: {path}")
    return value


def _require_sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise Dcm2niixSourceArtifactError(f"{label} is not a lowercase SHA-256 digest")
    return value


def _require_exact(payload: dict[str, Any], key: str, expected: object, label: str) -> None:
    if payload.get(key) != expected:
        raise Dcm2niixSourceArtifactError(
            f"{label} {key} mismatch: expected {expected!r}, got {payload.get(key)!r}"
        )


def validate_build_receipt(payload: dict[str, Any], binary_sha256: str) -> None:
    expected_values = {
        "schema": BUILD_RECEIPT_SCHEMA,
        "release_tag": RELEASE_TAG,
        "expected_cli_version": EXPECTED_CLI_VERSION,
        "source_url": SOURCE_URL,
        "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
        "license_sha256": LICENSE_SHA256,
        "source_license_sha256": LICENSE_SHA256,
        "bundled_license_sha256": LICENSE_SHA256,
        "binary_sha256": binary_sha256,
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "artifact_directory": f"artifacts/{binary_sha256}",
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "binary": BINARY_NAME,
        "bundled_license": str(LICENSE_RELATIVE_PATH),
    }
    expected_keys = set(expected_values) | {"linkage"}
    if set(payload) != expected_keys:
        raise Dcm2niixSourceArtifactError(
            "dcm2niix build receipt field set mismatch: "
            f"expected {sorted(expected_keys)}, got {sorted(payload)}"
        )
    for key, expected in expected_values.items():
        _require_exact(payload, key, expected, "dcm2niix build receipt")
    expected_linkage = {
        "result": "system-only-no-rpath",
        "allowed_dependency_prefixes": ["/System/Library/", "/usr/lib/"],
        "rpaths": [],
    }
    _require_exact(payload, "linkage", expected_linkage, "dcm2niix build receipt")


def validate_artifact_pointer(payload: dict[str, Any], binary_sha256: str) -> None:
    """Validate the deterministic pointer copied into the release bundle."""

    for key, expected in {
        "schema": CURRENT_POINTER_SCHEMA,
        "release_tag": RELEASE_TAG,
        "source_url": SOURCE_URL,
        "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
        "license_sha256": LICENSE_SHA256,
        "binary_sha256": binary_sha256,
        "artifact_directory": f"artifacts/{binary_sha256}",
    }.items():
        _require_exact(payload, key, expected, "dcm2niix current artifact pointer")
    expected_keys = {
        "schema",
        "artifact_directory",
        "binary_sha256",
        "release_tag",
        "source_url",
        "source_archive_sha256",
        "license_sha256",
    }
    if set(payload) != expected_keys:
        raise Dcm2niixSourceArtifactError(
            "dcm2niix current artifact pointer field set mismatch: "
            f"expected {sorted(expected_keys)}, got {sorted(payload)}"
        )


def development_source_manifest() -> dict[str, object]:
    """Describe an explicitly supplied, non-release development executable."""

    return {
        "kind": "explicit-development-input-unpinned",
        "release_eligible": False,
        "expected_cli_version": EXPECTED_CLI_VERSION,
        "source_provenance": "not-verified",
        "license_status": "not-verified-for-custom-build-input",
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "linkage": "verified-system-only-no-rpath-at-packaging",
    }


def validate_source_manifest(
    source: object,
    *,
    binary_sha256: str,
    receipt_bytes: bytes,
    pointer_bytes: bytes,
) -> None:
    """Validate the canonical source object embedded in an app manifest."""

    if not isinstance(source, dict):
        raise Dcm2niixSourceArtifactError("dcm2niix_source must be a JSON object")
    expected = {
        "kind": "pinned-official-source-build",
        "release_eligible": True,
        "release_tag": RELEASE_TAG,
        "expected_cli_version": EXPECTED_CLI_VERSION,
        "source_url": SOURCE_URL,
        "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "minimum_macos": MINIMUM_MACOS,
        "architecture": ARCHITECTURE,
        "binary_sha256": binary_sha256,
        "license": "licenses/dcm2niix-license.txt",
        "license_sha256": LICENSE_SHA256,
        "build_receipt": "licenses/dcm2niix-build-provenance.json",
        "build_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "artifact_pointer": "licenses/dcm2niix-current-artifact.json",
        "artifact_pointer_sha256": hashlib.sha256(pointer_bytes).hexdigest(),
        "linkage": "system-only-no-rpath",
    }
    if source != expected:
        raise Dcm2niixSourceArtifactError(
            "dcm2niix_source does not match the pinned source-build provenance"
        )


def verify_build_root(
    build_root: Path,
    *,
    expected_license: Path,
) -> VerifiedDcm2niixArtifact:
    build_root = build_root.expanduser()
    expected_license = expected_license.expanduser()
    _require_directory(build_root, "dcm2niix build root")
    _require_regular_file(expected_license, "tracked dcm2niix license", MAX_LICENSE_BYTES)
    build_root = build_root.resolve(strict=True)
    expected_license = expected_license.resolve(strict=True)
    tracked_license_sha256 = _sha256_file(expected_license)
    if tracked_license_sha256 != LICENSE_SHA256:
        raise Dcm2niixSourceArtifactError(
            "tracked dcm2niix license does not match the pinned official source: "
            f"expected {LICENSE_SHA256}, got {tracked_license_sha256}"
        )

    pointer_path = build_root / CURRENT_POINTER_NAME
    pointer = _read_json(pointer_path, "dcm2niix current artifact pointer")
    pointer_binary_sha256 = _require_sha256(
        pointer.get("binary_sha256"), "dcm2niix pointer binary_sha256"
    )
    validate_artifact_pointer(pointer, pointer_binary_sha256)

    relative_text = pointer.get("artifact_directory")
    if not isinstance(relative_text, str):
        raise Dcm2niixSourceArtifactError(
            "dcm2niix pointer artifact_directory must be a relative path"
        )
    relative = PurePosixPath(relative_text)
    expected_relative = PurePosixPath("artifacts") / pointer_binary_sha256
    if relative != expected_relative:
        raise Dcm2niixSourceArtifactError(
            "dcm2niix pointer must identify its content-addressed artifacts/<binary-sha256> directory"
        )
    artifact_directory = build_root.joinpath(*relative.parts)
    _require_directory(build_root / "artifacts", "dcm2niix artifact container")
    _require_directory(artifact_directory, "dcm2niix content-addressed artifact")
    resolved_artifact = artifact_directory.resolve(strict=True)
    if not resolved_artifact.is_relative_to(build_root.resolve(strict=True)):
        raise Dcm2niixSourceArtifactError(
            "dcm2niix artifact pointer escapes the build root"
        )

    binary = resolved_artifact / BINARY_NAME
    license_path = resolved_artifact.joinpath(*LICENSE_RELATIVE_PATH.parts)
    receipt_path = resolved_artifact / RECEIPT_NAME
    _require_regular_file(binary, "dcm2niix binary")
    if not os.access(binary, os.X_OK):
        raise Dcm2niixSourceArtifactError(f"dcm2niix binary is not executable: {binary}")
    _require_regular_file(license_path, "dcm2niix artifact license", MAX_LICENSE_BYTES)
    _require_regular_file(receipt_path, "dcm2niix build receipt", MAX_JSON_BYTES)

    binary_sha256 = _sha256_file(binary)
    if binary_sha256 != pointer_binary_sha256:
        raise Dcm2niixSourceArtifactError(
            "dcm2niix binary SHA-256 does not match the current artifact pointer"
        )
    artifact_license_sha256 = _sha256_file(license_path)
    if artifact_license_sha256 != LICENSE_SHA256:
        raise Dcm2niixSourceArtifactError(
            "dcm2niix artifact license differs from the pinned official license"
        )
    if license_path.read_bytes() != expected_license.read_bytes():
        raise Dcm2niixSourceArtifactError(
            "dcm2niix artifact and tracked license bytes differ"
        )

    receipt = _read_json(receipt_path, "dcm2niix build receipt")
    validate_build_receipt(receipt, binary_sha256)
    return VerifiedDcm2niixArtifact(
        build_root=build_root,
        artifact_directory=resolved_artifact,
        binary=binary,
        license=license_path,
        receipt=receipt_path,
        pointer=pointer_path,
        binary_sha256=binary_sha256,
        receipt_sha256=_sha256_file(receipt_path),
        pointer_sha256=_sha256_file(pointer_path),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify and resolve the pinned dcm2niix macOS 14 source artifact."
    )
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--expected-license", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    verified = verify_build_root(
        args.build_root,
        expected_license=args.expected_license,
    )
    if args.json:
        print(json.dumps(verified.cli_payload(), sort_keys=True))
    else:
        print(f"PASS {verified.binary}: pinned dcm2niix source artifact verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
