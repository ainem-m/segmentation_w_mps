#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import plistlib
import re
import stat
import subprocess
import tempfile
import zipfile
from email.parser import Parser
from pathlib import Path
from pathlib import PurePosixPath
from urllib.parse import urlparse

try:
    from scripts.verify_macos_deployment_target import (
        MachODeploymentTargetError,
        verify_app_machos,
        verify_wheel_machos,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from verify_macos_deployment_target import (  # type: ignore[no-redef]
        MachODeploymentTargetError,
        verify_app_machos,
        verify_wheel_machos,
    )

try:
    from scripts.verify_macos_binary_linkage import (
        verify_app_bundle_macos_linkage,
        verify_system_macos_linkage,
        verify_wheel_self_contained_macos_linkage,
        verify_wheel_system_macos_linkage,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from verify_macos_binary_linkage import (  # type: ignore[no-redef]
        verify_app_bundle_macos_linkage,
        verify_system_macos_linkage,
        verify_wheel_self_contained_macos_linkage,
        verify_wheel_system_macos_linkage,
    )

try:
    from scripts.verify_dcm2niix_source_artifact import (
        Dcm2niixSourceArtifactError,
        LICENSE_SHA256 as DCM2NIIX_PINNED_LICENSE_SHA256,
        development_source_manifest as dcm2niix_development_source_manifest,
        validate_artifact_pointer as validate_dcm2niix_artifact_pointer,
        validate_build_receipt as validate_dcm2niix_build_receipt,
        validate_source_manifest as validate_dcm2niix_source_manifest,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from verify_dcm2niix_source_artifact import (  # type: ignore[no-redef]
        Dcm2niixSourceArtifactError,
        LICENSE_SHA256 as DCM2NIIX_PINNED_LICENSE_SHA256,
        development_source_manifest as dcm2niix_development_source_manifest,
        validate_artifact_pointer as validate_dcm2niix_artifact_pointer,
        validate_build_receipt as validate_dcm2niix_build_receipt,
        validate_source_manifest as validate_dcm2niix_source_manifest,
    )

try:
    from scripts.verify_dicom_normalizer_artifact import (
        DicomNormalizerArtifactError,
        source_manifest_from_packaged_receipts as normalizer_source_from_receipts,
        validate_packaged_provenance as validate_normalizer_packaged_provenance,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from verify_dicom_normalizer_artifact import (  # type: ignore[no-redef]
        DicomNormalizerArtifactError,
        source_manifest_from_packaged_receipts as normalizer_source_from_receipts,
        validate_packaged_provenance as validate_normalizer_packaged_provenance,
    )

try:
    from scripts.verify_release_input_readiness import (
        BUNDLED_OVERRIDE_RELEASE_HASH_BINDING,
        BUNDLED_OVERRIDE_SPECS,
        ReleaseInputReadinessError,
        valid_revalidation_timestamp,
        verify_canonical_dependency_lock,
        verify_excluded_bundled_override_metadata,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from verify_release_input_readiness import (  # type: ignore[no-redef]
        BUNDLED_OVERRIDE_RELEASE_HASH_BINDING,
        BUNDLED_OVERRIDE_SPECS,
        ReleaseInputReadinessError,
        valid_revalidation_timestamp,
        verify_canonical_dependency_lock,
        verify_excluded_bundled_override_metadata,
    )

try:
    from scripts.release_build_toolchain import (
        ReleaseBuildToolchainError,
        verify_release_build_toolchain_receipt,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from release_build_toolchain import (  # type: ignore[no-redef]
        ReleaseBuildToolchainError,
        verify_release_build_toolchain_receipt,
    )


OLD_FIRST_PARTY_MARKERS = ("LicenseRef-Proprietary", "WrapperMac-Proprietary-License")
APACHE_HEADING = "Apache License"
APACHE_TERMS = "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION"
NATIVE_LICENSE_FILES = {
    "gdcm": "GDCM-BSD-3-Clause.txt",
    "openjp": "OpenJPEG-BSD-2-Clause.txt",
    "charls": "CharLS-BSD-3-Clause.txt",
    "json-c": "json-c-MIT.txt",
    "libssl": "OpenSSL-Apache-2.0.txt",
    "libcrypto": "OpenSSL-Apache-2.0.txt",
}
GDCM_STATIC_LICENSE_SOURCES = {
    "GDCM-BSD-3-Clause.txt": "Copyright.txt",
    "GDCM-IJG-JPEG-README.txt": "Utilities/gdcmjpeg/README",
    "OpenJPEG-BSD-2-Clause.txt": "Utilities/gdcmopenjpeg/LICENSE",
    "CharLS-BSD-3-Clause.txt": "Utilities/gdcmcharls/License.txt",
    "Expat-MIT.txt": "Utilities/gdcmexpat/COPYING",
    "zlib-Zlib.txt": "Utilities/gdcmzlib/LICENSE",
    "GDCM-UUID-BSD-3-Clause.txt": "Utilities/gdcmuuid/COPYING",
}
GDCM_STATIC_LICENSE_INVENTORY = "GDCM-static-license-inventory.json"
GDCM_STATIC_VERSION = "3.2.7"
GDCM_STATIC_SOURCE_URL = (
    "https://github.com/malaterre/GDCM/archive/refs/tags/v3.2.7.tar.gz"
)
GDCM_STATIC_SOURCE_SHA256 = (
    "b7b17b70c009677cf244cc7837b88386441e097f8861fdeee83aa27d1bc1b090"
)
MODEL_PAYLOAD_SUFFIXES = {
    ".bin",
    ".ckpt",
    ".h5",
    ".joblib",
    ".npy",
    ".npz",
    ".onnx",
    ".pb",
    ".pickle",
    ".pkl",
    ".pt",
    ".pth",
    ".safetensors",
    ".tar",
}
PRIVATE_MESH_SUFFIXES = {".3mf", ".glb", ".gltf", ".obj", ".off", ".ply", ".stl"}
PRIVATE_MEDICAL_SUFFIXES = {".dcm", ".dicom", ".ima", ".nii"}
FAIL_CLOSED_OPAQUE_ARCHIVE_ENDINGS = (
    ".7z",
    ".rar",
    ".tar.bz2",
    ".tar.gz",
    ".tar.xz",
    ".tbz",
    ".tbz2",
    ".tgz",
    ".txz",
)
AUTHORIZED_SAMPLE_NIFTI = {
    "sample1/input/owner_cbct_jawcrop_0p5mm.nii.gz": (
        "69fc10771a9677a3b5f1f597a5f938d8b889633044cd8da7e6221fd123607824"
    ),
    "sample1/teeth_result/toothseg_fdi_multilabel_0p5mm.nii.gz": (
        "57fa3cc887990b347cd13dc9a6ec1a43c88d89214eed1cd9ce553efda7465996"
    ),
}
ZIP_ARCHIVE_SUFFIXES = {".zip", ".whl", ".pyz"}
LARGE_MODEL_PAYLOAD_BYTES = 1024 * 1024
MAX_BENIGN_PYTHON_PTH_BYTES = 64 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_HASH_MEMBER_BYTES = 512 * 1024 * 1024
SUSPICIOUS_MODEL_ARCHIVE_NAMES = {
    "ckpts(new).zip",
    "ckpts.zip",
    "ckpts_challenge.zip",
    "tgnet.zip",
}
KNOWN_NON_BUNDLED_CHECKPOINT_FILENAMES = {
    "tgnet_fps.h5",
    "tgnet_bdl.h5",
    "model.tar",
}
KNOWN_NON_BUNDLED_CHECKPOINT_HASHES = {
    "54de398434f5d079838533b4b979bcd044fbbed3d4b10268b0ea201382f9b4f4": "legacy non-bundled checkpoint",
    "3d2e44db8865ff3968803e86dadcf73cf9c4b738ddc35bfb3bc42c02347d7a0c": "MeshSegNet Teeth3DS checkpoint",
    "024f585f20924c08eafced8fdc633015b0cc8bba04301d585b4cf7a0c02206b6": "TGNet FPS checkpoint",
    "5ec7780d7d645af522c6f2888093e5ca8e11c631d0e13798d208ba2a157554d1": "TGNet boundary checkpoint",
}
KNOWN_NON_BUNDLED_CHECKPOINTS = {
    64_037_327: frozenset(
        {
            "54de398434f5d079838533b4b979bcd044fbbed3d4b10268b0ea201382f9b4f4",
            "024f585f20924c08eafced8fdc633015b0cc8bba04301d585b4cf7a0c02206b6",
        }
    ),
    28_825_987: frozenset(
        {"3d2e44db8865ff3968803e86dadcf73cf9c4b738ddc35bfb3bc42c02347d7a0c"}
    ),
    511_103: frozenset(
        {"5ec7780d7d645af522c6f2888093e5ca8e11c631d0e13798d208ba2a157554d1"}
    ),
}
SETUP_WEIGHTS_SCHEMA = "totalsegmentator_wrapper_mac.setup_weights_manifest.v1"
SETUP_WEIGHTS_TOTALSEGMENTATOR_VERSION = "2.14.0"
SETUP_WEIGHTS_TASK_IDS = {113, 115, 297}
SETUP_WEIGHTS_MANIFEST_NAME = "totalseg_setup_weights_manifest.json"
DMG_ROOT_ALLOWLIST = {
    "Applications",
    "Collect TotalSegmentator Wrapper Logs.command",
    "LICENSE.txt",
    "NOTICE.txt",
    "README.txt",
    "TEST_ACCOUNT_INSTALL.txt",
    "TotalSegmentator Wrapper for Mac.app",
    "Verify Test Account Install.command",
}
SETUP_WEIGHTS_CHECKSUM_POLICY = (
    "Publisher-provided GitHub release digest where available; otherwise a locally "
    "observed SHA-256 value carried by this application for the pinned official "
    "GitHub release URL. Locally observed values are not publisher-provided digests. "
    "For assets without a publisher digest, observation date and source evidence are "
    "not preserved; revalidation by an approved official-asset download is required "
    "before release."
)
LOCAL_OBSERVATION_EVIDENCE = "not-preserved-unverified"
REVALIDATION_EVIDENCE_SCHEMA = (
    "totalsegmentator_wrapper_mac.official_asset_revalidation.v1"
)
REVALIDATION_CHECKS = [
    "complete-size",
    "sha256",
    "zip-crc",
    "expected-model-structure",
]
SETUP_WEIGHTS_EXPECTED_ASSETS = {
    113: {
        "release_tag": "v2.5.0-weights",
        "filename": "Dataset113_ToothFairy3.zip",
        "size_bytes": 232_066_830,
        "sha256": "cf28693eec49b7a8448e2ebf0a372da41855a099a26d52d1cffe15a3c7e4b740",
        "sha256_source": "github-release-digest",
        "publisher_digest_available": True,
        "dataset_dir": "Dataset113_ToothFairy3",
        "required_files": [
            "nnUNetTrainer_onlyMirror01__nnUNetPlans__3d_lowres_high/plans.json",
            "nnUNetTrainer_onlyMirror01__nnUNetPlans__3d_lowres_high/dataset.json",
            "nnUNetTrainer_onlyMirror01__nnUNetPlans__3d_lowres_high/fold_0/checkpoint_final.pth",
        ],
    },
    115: {
        "release_tag": "v2.5.0-weights",
        "filename": "Dataset115_mandible.zip",
        "size_bytes": 230_321_497,
        "sha256": "a9f4a7bd92e093fc0bb5a06450989429df2da1cc4e470d54373b2f3a3175eab9",
        "dataset_dir": "Dataset115_mandible",
        "required_files": [
            "nnUNetTrainer_DASegOrd0_NoMirroring__nnUNetPlans__3d_fullres/plans.json",
            "nnUNetTrainer_DASegOrd0_NoMirroring__nnUNetPlans__3d_fullres/dataset.json",
            "nnUNetTrainer_DASegOrd0_NoMirroring__nnUNetPlans__3d_fullres/fold_0/checkpoint_final.pth",
        ],
    },
    297: {
        "release_tag": "v2.0.0-weights",
        "filename": "Dataset297_TotalSegmentator_total_3mm_1559subj.zip",
        "size_bytes": 135_386_075,
        "sha256": "0baa2c8de2975600eb31801dd5c1825cd2b356f794498659cf3348714c073394",
        "dataset_dir": "Dataset297_TotalSegmentator_total_3mm_1559subj",
        "required_files": [
            "nnUNetTrainer_4000epochs_NoMirroring__nnUNetPlans__3d_fullres/plans.json",
            "nnUNetTrainer_4000epochs_NoMirroring__nnUNetPlans__3d_fullres/dataset.json",
            "nnUNetTrainer_4000epochs_NoMirroring__nnUNetPlans__3d_fullres/fold_0/checkpoint_final.pth",
        ],
    },
}
VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){2}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TEAM_IDENTIFIER_PATTERN = re.compile(r"^[A-Z0-9]{10}$")
SOURCE_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
CANONICAL_BUNDLE_IDENTIFIER = "jp.chino.totalsegmentator.wrapper.mac"
MINIMUM_SUPPORTED_MACOS_VERSION = "14.0"
MINIMUM_SUPPORTED_MACOS_VERSION_FROM = (0, 4, 1)
NATIVE_INPUT_DIGEST_SCOPE = "build-input-before-copy-and-code-sign-v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def safe_app_resource_relative_path(value: object, label: str) -> PurePosixPath:
    require(
        isinstance(value, str)
        and bool(value)
        and "\\" not in value
        and all(ord(character) >= 32 and ord(character) != 127 for character in value),
        f"{label} must be a safe relative path",
    )
    relative = PurePosixPath(value)
    require(
        not relative.is_absolute()
        and bool(relative.parts)
        and str(relative) == value
        and all(part not in ("", ".", "..") for part in relative.parts),
        f"{label} must be a safe relative path",
    )
    return relative


def validate_tgnet_policy_notice(value: str) -> None:
    for statement in (
        "source: user-provided",
        "license: not-verified",
        "not bundled",
        "not redistributed",
        "tgnet_fps.h5",
        "024f585f20924c08eafced8fdc633015b0cc8bba04301d585b4cf7a0c02206b6",
        "tgnet_bdl.h5",
        "5ec7780d7d645af522c6f2888093e5ca8e11c631d0e13798d208ba2a157554d1",
    ):
        require(
            statement in value,
            f"TGNet policy statement is missing: {statement}",
        )


def text(path: Path) -> str:
    require(path.is_file(), f"required file is missing: {path}")
    return path.read_text(encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_archive_member(archive: zipfile.ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with archive.open(name) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_safety_error(infos: list[zipfile.ZipInfo]) -> str | None:
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        return f"archive member count exceeds {MAX_ARCHIVE_MEMBERS}"
    total_size = 0
    for info in infos:
        if info.is_dir():
            continue
        if info.file_size < 0 or info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            return f"archive member exceeds {MAX_ARCHIVE_MEMBER_BYTES} bytes: {info.filename}"
        total_size += info.file_size
        if total_size > MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES:
            return (
                "archive total uncompressed size exceeds "
                f"{MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES} bytes"
            )
        member = PurePosixPath(info.filename.replace("\\", "/"))
        if member.is_absolute() or ".." in member.parts:
            return f"archive contains unsafe path: {info.filename}"
    return None


def read_wrapper_setup_manager_source(wheel: Path) -> str:
    """Read the setup lock consumer from the wheel that the app actually ships.

    This avoids validating a convenient checkout copy while a different wheel is
    packaged.  The source stays in memory; no archive path is extracted onto the
    host filesystem.
    """

    require(
        wheel.is_file() and not wheel.is_symlink(),
        f"wrapper wheel is missing or unsafe: {wheel}",
    )
    try:
        with zipfile.ZipFile(wheel) as archive:
            infos = archive.infolist()
            safety_error = _archive_safety_error(infos)
            require(
                safety_error is None,
                f"wrapper wheel archive is unsafe: {safety_error}",
            )
            candidates = [
                info
                for info in infos
                if not info.is_dir()
                and info.filename
                == "totalsegmentator_wrapper_mac/setup_manager.py"
            ]
            require(
                len(candidates) == 1,
                "wrapper wheel setup manager source is missing or ambiguous",
            )
            try:
                return archive.read(candidates[0]).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RuntimeError(
                    "wrapper wheel setup manager source is not UTF-8"
                ) from exc
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"wrapper wheel cannot be read safely: {exc}") from exc


def verify_bundled_override_release_hash_boundary(
    *,
    manifest: dict[str, object],
    lock_metadata: object,
    wheels: dict[str, Path],
) -> None:
    """Bind signed override wheel bytes at packaging, not in the pre-sign lock.

    The canonical lock records only the local wheel that participated in graph
    resolution.  A Developer ID signature can change the fpsample archive and
    its RECORD, so the app manifest is the only release-byte hash authority.
    """

    if not isinstance(lock_metadata, dict):
        raise RuntimeError("release app bundled override metadata is invalid")
    try:
        verify_excluded_bundled_override_metadata(
            lock_metadata.get("excluded_bundled_overrides")
        )
    except ReleaseInputReadinessError as exc:
        raise RuntimeError(
            f"release app bundled override metadata is invalid: {exc}"
        ) from exc
    manifest_hash_fields = {
        "acvl-utils": "acvl_utils_wheel_sha256",
        "fpsample": "fpsample_wheel_sha256",
    }
    overrides = lock_metadata["excluded_bundled_overrides"]
    assert isinstance(overrides, dict)
    for name, spec in BUNDLED_OVERRIDE_SPECS.items():
        wheel = wheels.get(name)
        require(
            wheel is not None and wheel.name == spec["filename"],
            f"release app bundled override wheel is missing or has the wrong filename: {name}",
        )
        assert wheel is not None
        entry = overrides[name]
        assert isinstance(entry, dict)
        require(
            entry.get("release_wheel_hash_binding")
            == BUNDLED_OVERRIDE_RELEASE_HASH_BINDING
            and "release_wheel_sha256" not in entry,
            f"release app bundled override metadata incorrectly embeds a signed wheel hash: {name}",
        )
        require(
            manifest.get(manifest_hash_fields[name]) == sha256_file(wheel),
            f"app {name} wheel SHA-256 does not match its setup manifest",
        )
        metadata_member = f"{spec['dist_info']}/METADATA"
        wheel_member = f"{spec['dist_info']}/WHEEL"
        try:
            with zipfile.ZipFile(wheel) as archive:
                infos = archive.infolist()
                for info in infos:
                    parts = PurePosixPath(info.filename).parts
                    require(
                        not info.filename.startswith("/") and ".." not in parts,
                        f"release app bundled override wheel has an unsafe path: {name}",
                    )
                require(
                    archive.testzip() is None,
                    f"release app bundled override wheel CRC validation failed: {name}",
                )
                metadata_members = [
                    info
                    for info in infos
                    if not info.is_dir() and info.filename == metadata_member
                ]
                wheel_members = [
                    info
                    for info in infos
                    if not info.is_dir() and info.filename == wheel_member
                ]
                require(
                    len(metadata_members) == 1 and len(wheel_members) == 1,
                    f"release app bundled override wheel metadata is missing or ambiguous: {name}",
                )
                final_metadata_sha256 = hashlib.sha256(
                    archive.read(metadata_members[0])
                ).hexdigest()
                final_wheel_metadata_sha256 = hashlib.sha256(
                    archive.read(wheel_members[0])
                ).hexdigest()
        except (OSError, zipfile.BadZipFile) as exc:
            raise RuntimeError(
                f"release app bundled override wheel is not a valid ZIP: {name}"
            ) from exc
        require(
            entry.get("resolution_input_metadata_sha256") == final_metadata_sha256
            and entry.get("resolution_input_wheel_metadata_sha256")
            == final_wheel_metadata_sha256,
            f"release app {name} wheel metadata differs from the resolver input",
        )
        if name == "acvl-utils":
            require(
                entry.get("resolution_input_sha256") == sha256_file(wheel),
                "release app acvl-utils wheel bytes differ from the resolver input",
            )
        else:
            require(
                manifest.get("fpsample_pre_sign_wheel_sha256")
                == entry.get("resolution_input_sha256"),
                "release app fpsample pre-sign wheel receipt differs from the resolver input",
            )


def _suspicious_archive_name(name: str) -> bool:
    lowered = Path(name).name.lower()
    return (
        lowered in SUSPICIOUS_MODEL_ARCHIVE_NAMES
        or "tgnet" in lowered
        or "checkpoint" in lowered
        or lowered.startswith("ckpts")
    )


def _opaque_archive_name(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(FAIL_CLOSED_OPAQUE_ARCHIVE_ENDINGS)


def _medical_image_name(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(".nii.gz") or Path(lowered).suffix in PRIVATE_MEDICAL_SUFFIXES


def _archive_member_has_dicom_magic(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> bool:
    if info.file_size < 132:
        return False
    with archive.open(info) as source:
        return source.read(132)[128:132] == b"DICM"


def _tree_file_has_dicom_magic(path: Path, size: int) -> bool:
    if size < 132:
        return False
    try:
        with path.open("rb") as source:
            source.seek(128)
            return source.read(4) == b"DICM"
    except OSError:
        return True


def verified_authorized_sample_nifti_paths(resources: Path) -> frozenset[Path]:
    """Validate and return the only medical images authorized for distribution."""

    manifest_path = resources / "sample1" / "sample_manifest.json"
    require(
        manifest_path.is_file() and not manifest_path.is_symlink(),
        "Sample 1 authorization manifest is missing or unsafe",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(
        manifest.get("schema") == "totalsegmentator_wrapper_mac.sample.v1",
        "Sample 1 authorization manifest schema mismatch",
    )
    source = manifest.get("source")
    require(
        isinstance(source, dict)
        and source.get("raw_dicom_included") is False
        and source.get("authorization_record")
        == "docs/43_OPEN_SOURCE_PUBLICATION_DECISIONS.md",
        "Sample 1 medical-image authorization is missing or invalid",
    )
    derived = manifest.get("derived_files")
    require(isinstance(derived, dict), "Sample 1 derived file inventory is invalid")
    observed_nifti = {name for name in derived if _medical_image_name(name)}
    expected_manifest_nifti = {
        relative.removeprefix("sample1/") for relative in AUTHORIZED_SAMPLE_NIFTI
    }
    require(
        observed_nifti == expected_manifest_nifti,
        "Sample 1 NIfTI path set differs from the authorized release set",
    )
    allowed: set[Path] = set()
    for relative, expected_sha256 in AUTHORIZED_SAMPLE_NIFTI.items():
        metadata = derived.get(relative.removeprefix("sample1/"))
        require(
            isinstance(metadata, dict)
            and metadata.get("sha256") == expected_sha256,
            f"Sample 1 authorized NIfTI manifest digest mismatch: {relative}",
        )
        candidate = resources / relative
        require(
            candidate.is_file()
            and not candidate.is_symlink()
            and sha256_file(candidate) == expected_sha256,
            f"Sample 1 authorized NIfTI bytes mismatch: {relative}",
        )
        allowed.add(candidate)
    return frozenset(allowed)


def _benign_python_path_configuration(payload: bytes) -> bool:
    """Distinguish small text ``.pth`` files from serialized PyTorch weights."""

    if len(payload) > MAX_BENIGN_PYTHON_PTH_BYTES or b"\x00" in payload:
        return False
    try:
        value = payload.decode("utf-8")
    except UnicodeDecodeError:
        return False
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("import "):
            continue
        if Path(line).is_absolute() or "\x00" in line:
            return False
    return True


def _archive_pth_is_model(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bool:
    if info.file_size > MAX_BENIGN_PYTHON_PTH_BYTES:
        return True
    with archive.open(info) as source:
        payload = source.read(MAX_BENIGN_PYTHON_PTH_BYTES + 1)
    return not _benign_python_path_configuration(payload)


def _tree_pth_is_model(path: Path, size: int) -> bool:
    if size > MAX_BENIGN_PYTHON_PTH_BYTES:
        return True
    try:
        payload = path.read_bytes()
    except OSError:
        return True
    return not _benign_python_path_configuration(payload)


def _exact_cpython_lib2to3_pickle(
    path: Path,
    verified_runtime_root: Path | None,
) -> bool:
    """Allow only CPython's versioned lib2to3 grammar caches.

    ``pickle`` remains a fail-closed checkpoint suffix everywhere else.  The
    caller may supply this root only after candidate-app validation has checked
    the bundled-runtime manifest, safe relative paths, and the runtime root.
    """

    if verified_runtime_root is None:
        return False
    try:
        relative = path.relative_to(verified_runtime_root)
    except ValueError:
        return False
    if relative.parent != Path("lib/python3.12/lib2to3"):
        return False
    return (
        re.fullmatch(
            r"(?:Grammar|PatternGrammar)3\.12\.\d+\.final\.0\.pickle",
            relative.name,
        )
        is not None
    )


def find_archive_model_payloads(
    archive: zipfile.ZipFile,
    *,
    reject_all_checkpoint_extensions: bool = False,
    reject_private_meshes: bool = False,
    reject_private_medical_images: bool = False,
    inspect_nested_zip: bool = True,
) -> list[str]:
    infos = archive.infolist()
    safety_error = _archive_safety_error(infos)
    if safety_error is not None:
        return [f"<unsafe archive: {safety_error}>"]
    found: list[str] = []
    for info in infos:
        if info.is_dir():
            continue
        member_path = Path(info.filename)
        member_name = member_path.name.lower()
        suffix = member_path.suffix.lower()
        opaque_archive_candidate = (
            reject_all_checkpoint_extensions and _opaque_archive_name(info.filename)
        )
        suffix_candidate = suffix in MODEL_PAYLOAD_SUFFIXES and (
            info.file_size >= LARGE_MODEL_PAYLOAD_BYTES
            or (
                reject_all_checkpoint_extensions
                and (suffix != ".pth" or _archive_pth_is_model(archive, info))
            )
        )
        private_mesh_candidate = reject_private_meshes and suffix in PRIVATE_MESH_SUFFIXES
        private_medical_candidate = reject_private_medical_images and (
            _medical_image_name(info.filename)
            or _archive_member_has_dicom_magic(archive, info)
        )
        filename_candidate = member_name in KNOWN_NON_BUNDLED_CHECKPOINT_FILENAMES
        known_digests = KNOWN_NON_BUNDLED_CHECKPOINTS.get(info.file_size)
        digest: str | None = None
        known_candidate = False
        if known_digests is not None:
            digest = _sha256_archive_member(archive, info.filename)
            known_candidate = digest in known_digests
        if not known_candidate and info.file_size <= MAX_ARCHIVE_HASH_MEMBER_BYTES:
            if digest is None:
                digest = _sha256_archive_member(archive, info.filename)
            known_candidate = digest in KNOWN_NON_BUNDLED_CHECKPOINT_HASHES
        if (
            opaque_archive_candidate
            or suffix_candidate
            or private_mesh_candidate
            or private_medical_candidate
            or filename_candidate
            or known_candidate
        ):
            found.append(info.filename)
            continue
        if member_path.suffix.lower() != ".zip":
            continue
        if _suspicious_archive_name(info.filename):
            found.append(info.filename)
            continue
        if not inspect_nested_zip:
            found.append(f"{info.filename}!<nested ZIP depth limit>")
            continue
        if info.file_size > MAX_ARCHIVE_HASH_MEMBER_BYTES:
            found.append(f"{info.filename}!<nested ZIP exceeds inspection limit>")
            continue
        try:
            with archive.open(info) as nested_source:
                nested_bytes = nested_source.read(MAX_ARCHIVE_HASH_MEMBER_BYTES + 1)
            if len(nested_bytes) > MAX_ARCHIVE_HASH_MEMBER_BYTES:
                found.append(f"{info.filename}!<nested ZIP exceeds inspection limit>")
                continue
            with zipfile.ZipFile(io.BytesIO(nested_bytes)) as nested:
                nested_payloads = find_archive_model_payloads(
                    nested,
                    reject_all_checkpoint_extensions=True,
                    reject_private_meshes=reject_private_meshes,
                    reject_private_medical_images=reject_private_medical_images,
                    inspect_nested_zip=False,
                )
            found.extend(f"{info.filename}!{payload}" for payload in nested_payloads)
        except (OSError, zipfile.BadZipFile, RuntimeError):
            found.append(f"{info.filename}!<invalid nested ZIP>")
    return sorted(found)


def _inspect_zip_path(path: Path) -> list[str]:
    if _suspicious_archive_name(path.name):
        return [path.name]
    try:
        with zipfile.ZipFile(path) as archive:
            return find_archive_model_payloads(
                archive,
                reject_all_checkpoint_extensions=True,
                reject_private_meshes=True,
                reject_private_medical_images=True,
            )
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return [f"{path.name}!<invalid ZIP>"]


def _safe_verified_runtime_symlink(
    path: Path,
    verified_runtime_root: Path | None,
) -> bool:
    if verified_runtime_root is None:
        return False
    try:
        path.relative_to(verified_runtime_root)
        target = os.readlink(path)
    except (OSError, ValueError):
        return False
    if Path(target).is_absolute():
        return False
    try:
        resolved_root = verified_runtime_root.resolve(strict=True)
        (path.parent / target).resolve(strict=True).relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def find_tree_model_payloads(
    root: Path,
    paths: list[Path],
    *,
    reject_all_checkpoint_extensions: bool = False,
    reject_private_meshes: bool = False,
    reject_private_medical_images: bool = False,
    verified_cpython_runtime_root: Path | None = None,
    authorized_medical_image_paths: frozenset[Path] = frozenset(),
) -> list[str]:
    found: list[str] = []
    for path in paths:
        if path.is_symlink():
            if not _safe_verified_runtime_symlink(
                path,
                verified_cpython_runtime_root,
            ):
                found.append(str(path.relative_to(root)))
            continue
        if not path.is_file():
            continue
        size = path.stat().st_size
        suffix = path.suffix.lower()
        opaque_archive_candidate = (
            reject_all_checkpoint_extensions and _opaque_archive_name(path.name)
        )
        exact_cpython_pickle = suffix == ".pickle" and _exact_cpython_lib2to3_pickle(
            path,
            verified_cpython_runtime_root,
        )
        suffix_candidate = not exact_cpython_pickle and suffix in MODEL_PAYLOAD_SUFFIXES and (
            size >= LARGE_MODEL_PAYLOAD_BYTES
            or (
                reject_all_checkpoint_extensions
                and (suffix != ".pth" or _tree_pth_is_model(path, size))
            )
        )
        private_mesh_candidate = reject_private_meshes and suffix in PRIVATE_MESH_SUFFIXES
        private_medical_candidate = reject_private_medical_images and (
            _medical_image_name(path.name) or _tree_file_has_dicom_magic(path, size)
        ) and path not in authorized_medical_image_paths
        filename_candidate = path.name.lower() in KNOWN_NON_BUNDLED_CHECKPOINT_FILENAMES
        known_digests = KNOWN_NON_BUNDLED_CHECKPOINTS.get(size)
        known_candidate = (
            known_digests is not None and sha256_file(path) in known_digests
        )
        if (
            opaque_archive_candidate
            or suffix_candidate
            or private_mesh_candidate
            or private_medical_candidate
            or filename_candidate
            or known_candidate
        ):
            found.append(str(path.relative_to(root)))
            continue
        if path.suffix.lower() in ZIP_ARCHIVE_SUFFIXES:
            relative = str(path.relative_to(root))
            archive_payloads = _inspect_zip_path(path)
            for payload in archive_payloads:
                if payload == path.name:
                    found.append(relative)
                else:
                    found.append(f"{relative}!{payload}")
    return sorted(found)


def canonical_setup_weights_manifest_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "src"
        / "totalsegmentator_wrapper_mac"
        / SETUP_WEIGHTS_MANIFEST_NAME
    )


def validate_setup_weights_manifest(manifest: object) -> None:
    require(isinstance(manifest, dict), "setup weights manifest must be a JSON object")
    require(manifest.get("schema") == SETUP_WEIGHTS_SCHEMA, "setup weights manifest schema mismatch")
    require(
        manifest.get("totalsegmentator_version") == SETUP_WEIGHTS_TOTALSEGMENTATOR_VERSION,
        "setup weights manifest TotalSegmentator version mismatch",
    )
    require(
        manifest.get("official_source")
        == "https://github.com/wasserth/TotalSegmentator",
        "setup weights manifest official source mismatch",
    )
    require(
        manifest.get("runtime_mapping_source")
        == "https://github.com/wasserth/TotalSegmentator/blob/v2.14.0/totalsegmentator/libs.py",
        "setup weights manifest runtime mapping source mismatch",
    )
    require(
        manifest.get("checksum_policy") == SETUP_WEIGHTS_CHECKSUM_POLICY,
        "setup weights manifest checksum provenance policy mismatch",
    )
    assets = manifest.get("assets")
    require(isinstance(assets, list), "setup weights manifest assets must be a list")
    require(len(assets) == len(SETUP_WEIGHTS_TASK_IDS), "setup weights manifest asset count mismatch")
    by_task: dict[int, dict] = {}
    for asset in assets:
        require(isinstance(asset, dict), "setup weights manifest asset must be an object")
        task_id = asset.get("task_id")
        require(
            isinstance(task_id, int) and not isinstance(task_id, bool),
            "setup weights manifest task_id must be an integer",
        )
        require(task_id not in by_task, f"duplicate setup weights task ID: {task_id}")
        by_task[task_id] = asset
        filename = asset.get("filename")
        require(
            isinstance(filename, str) and filename.endswith(".zip") and Path(filename).name == filename,
            f"setup weights task {task_id} filename is invalid",
        )
        parsed_url = urlparse(str(asset.get("url") or ""))
        require(
            parsed_url.scheme == "https"
            and parsed_url.netloc == "github.com"
            and parsed_url.path.startswith("/wasserth/TotalSegmentator/releases/download/")
            and parsed_url.path.endswith("/" + filename)
            and not parsed_url.params
            and not parsed_url.query
            and not parsed_url.fragment,
            f"setup weights task {task_id} must use its official HTTPS GitHub release URL",
        )
        size_bytes = asset.get("size_bytes")
        require(
            isinstance(size_bytes, int) and not isinstance(size_bytes, bool) and size_bytes > 0,
            f"setup weights task {task_id} size_bytes is invalid",
        )
        require(
            isinstance(asset.get("sha256"), str)
            and SHA256_PATTERN.fullmatch(asset["sha256"]) is not None,
            f"setup weights task {task_id} SHA-256 is invalid",
        )
        require(
            "sha256_observed_at" not in asset,
            f"setup weights task {task_id} checksum provenance must not claim an unpreserved observation date",
        )
        dataset_dir = asset.get("dataset_dir")
        require(
            isinstance(dataset_dir, str)
            and dataset_dir
            and Path(dataset_dir).name == dataset_dir,
            f"setup weights task {task_id} dataset_dir is invalid",
        )
        required_files = asset.get("required_files")
        require(
            isinstance(required_files, list) and len(required_files) >= 3,
            f"setup weights task {task_id} required_files is invalid",
        )
        for relative in required_files:
            member = PurePosixPath(str(relative))
            require(
                isinstance(relative, str)
                and relative
                and not member.is_absolute()
                and ".." not in member.parts,
                f"setup weights task {task_id} required layout contains an unsafe path",
            )
        required_names = {PurePosixPath(value).name for value in required_files}
        require(
            {"plans.json", "dataset.json", "checkpoint_final.pth"}.issubset(required_names),
            f"setup weights task {task_id} required layout is incomplete",
        )
        expected = SETUP_WEIGHTS_EXPECTED_ASSETS.get(task_id)
        require(expected is not None, f"unexpected setup weights task ID: {task_id}")
        _validate_setup_weight_asset_provenance(asset, task_id)
        expected_release_url = (
            "https://github.com/wasserth/TotalSegmentator/releases/tag/"
            + expected["release_tag"]
        )
        expected_download_url = (
            "https://github.com/wasserth/TotalSegmentator/releases/download/"
            f"{expected['release_tag']}/{expected['filename']}"
        )
        expected_fields = {
            **expected,
            "release_url": expected_release_url,
            "url": expected_download_url,
        }
        mismatched_fields = [
            key
            for key, expected_value in expected_fields.items()
            if asset.get(key) != expected_value
        ]
        require(
            not mismatched_fields,
            f"setup weights task {task_id} asset mapping mismatch: "
            + ", ".join(mismatched_fields),
        )
        if expected.get("publisher_digest_available"):
            require(
                "local_observation_evidence" not in asset
                and "revalidation_required_before_release" not in asset,
                f"setup weights task {task_id} checksum provenance incorrectly marks a publisher digest as local",
            )
    require(set(by_task) == SETUP_WEIGHTS_TASK_IDS, "setup weights manifest task IDs mismatch")


def _validate_setup_weight_asset_provenance(asset: dict, task_id: int) -> None:
    if task_id == 113:
        require(
            asset.get("sha256_source") == "github-release-digest"
            and asset.get("publisher_digest_available") is True
            and "local_observation_evidence" not in asset
            and "revalidation_required_before_release" not in asset
            and "revalidation_evidence" not in asset,
            "setup weights task 113 publisher checksum provenance is invalid",
        )
        return
    require(
        asset.get("publisher_digest_available") is False,
        f"setup weights task {task_id} publisher digest state is invalid",
    )
    state = asset.get("revalidation_required_before_release")
    if state is True:
        require(
            asset.get("sha256_source") == "locally-observed-official-asset"
            and asset.get("local_observation_evidence")
            == LOCAL_OBSERVATION_EVIDENCE
            and "revalidation_evidence" not in asset,
            f"setup weights task {task_id} pending revalidation provenance is invalid",
        )
        return
    evidence = asset.get("revalidation_evidence")
    require(
        state is False
        and asset.get("sha256_source")
        == "approved-official-asset-revalidation"
        and "local_observation_evidence" not in asset
        and isinstance(evidence, dict)
        and set(evidence)
        == {
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
        }
        and evidence.get("schema") == REVALIDATION_EVIDENCE_SCHEMA
        and evidence.get("official_url") == asset.get("url")
        and evidence.get("release_tag") == asset.get("release_tag")
        and evidence.get("filename") == asset.get("filename")
        and evidence.get("size_bytes") == asset.get("size_bytes")
        and evidence.get("sha256") == asset.get("sha256")
        and valid_revalidation_timestamp(evidence.get("verified_at_utc"))
        and evidence.get("transport")
        == "https-pinned-official-release-asset"
        and evidence.get("checks") == REVALIDATION_CHECKS
        and evidence.get("approval") == "approved-for-release",
        f"setup weights task {task_id} approved revalidation evidence is invalid",
    )


def canonical_setup_weights_manifest() -> dict:
    path = canonical_setup_weights_manifest_path()
    require(path.is_file(), f"canonical setup weights manifest is missing: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validate_setup_weights_manifest(manifest)
    return manifest


def _metadata_version(metadata_text: str) -> tuple[str, str]:
    metadata = Parser().parsestr(metadata_text)
    return str(metadata.get("Name") or ""), str(metadata.get("Version") or "")


def verify_wheel_release_identity(wheel: Path, expected_version: str | None) -> str:
    require(wheel.is_file(), f"wheel is missing: {wheel}")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        require(len(metadata_names) == 1, f"expected one wheel METADATA: {wheel}")
        package_name, metadata_version = _metadata_version(
            archive.read(metadata_names[0]).decode("utf-8")
        )
        require(
            package_name == "totalsegmentator-wrapper-mac",
            f"wrapper wheel package identity mismatch: {package_name or 'missing'}",
        )
        require(VERSION_PATTERN.fullmatch(metadata_version) is not None, "wrapper wheel version is invalid")
        if expected_version is not None:
            require(
                metadata_version == expected_version,
                f"wrapper wheel METADATA version mismatch: expected {expected_version}, found {metadata_version}",
            )
        init_names = [
            name
            for name in names
            if name.endswith("totalsegmentator_wrapper_mac/__init__.py")
        ]
        require(len(init_names) == 1, "wrapper wheel package __init__.py is missing or ambiguous")
        init_text = archive.read(init_names[0]).decode("utf-8")
        match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_text, re.MULTILINE)
        require(match is not None, "wrapper wheel package __version__ is missing")
        require(
            match.group(1) == metadata_version,
            "wrapper wheel package __version__ does not match METADATA",
        )
        setup_manifest_names = [
            name
            for name in names
            if name.endswith(f"totalsegmentator_wrapper_mac/{SETUP_WEIGHTS_MANIFEST_NAME}")
        ]
        require(
            len(setup_manifest_names) == 1,
            "wrapper wheel setup weights manifest is missing or ambiguous",
        )
        packaged_manifest = json.loads(archive.read(setup_manifest_names[0]))
        validate_setup_weights_manifest(packaged_manifest)
        require(
            packaged_manifest == canonical_setup_weights_manifest(),
            "wrapper wheel setup weights manifest does not match the canonical manifest",
        )
    return metadata_version


def verify_source(root: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    tracked = [
        root / value.decode("utf-8")
        for value in result.stdout.split(b"\0")
        if value
    ]
    sample_manifest = root / "resources" / "sample1" / "sample_manifest.json"
    authorized_medical_images = (
        verified_authorized_sample_nifti_paths(root / "resources")
        if sample_manifest.is_file()
        else frozenset()
    )
    payloads = find_tree_model_payloads(
        root,
        tracked,
        reject_all_checkpoint_extensions=True,
        reject_private_meshes=True,
        reject_private_medical_images=True,
        authorized_medical_image_paths=authorized_medical_images,
    )
    require(
        not payloads,
        "source tree tracks non-bundled model, private mesh, medical-image, or opaque archive payloads: "
        + ", ".join(payloads),
    )
    validate_setup_weights_manifest(
        json.loads(text(root / "src" / "totalsegmentator_wrapper_mac" / SETUP_WEIGHTS_MANIFEST_NAME))
    )


def verify_apache_license(path: Path) -> None:
    value = text(path)
    require(APACHE_HEADING in value and APACHE_TERMS in value, f"invalid Apache-2.0 text: {path}")


def verify_notice(path: Path) -> None:
    value = text(path)
    require("Copyright 2026 TotalSegmentator Wrapper for Mac contributors" in value, f"wrapper copyright missing: {path}")
    require("Third-party software" in value and "not relicensed" in value, f"scope boundary missing: {path}")


def native_license_for(name: str) -> str | None:
    lowered = name.lower()
    return next((license_name for marker, license_name in NATIVE_LICENSE_FILES.items() if marker in lowered), None)


def validate_gdcm_static_license_inventory(
    payload: object,
    license_payloads: dict[str, bytes],
) -> None:
    require(isinstance(payload, dict), "GDCM static license inventory is not an object")
    require(
        payload.get("schema")
        == "totalsegmentator_wrapper_mac.gdcm_static_license_inventory.v1",
        "GDCM static license inventory schema mismatch",
    )
    require(payload.get("gdcm_version") == GDCM_STATIC_VERSION, "GDCM static version mismatch")
    require(payload.get("source_url") == GDCM_STATIC_SOURCE_URL, "GDCM static source URL mismatch")
    require(
        payload.get("source_archive_sha256") == GDCM_STATIC_SOURCE_SHA256,
        "GDCM static source archive SHA-256 mismatch",
    )
    require(payload.get("linkage") == "static", "GDCM linkage is not static")
    require(payload.get("gdcmconv_bundled") is False, "GDCM inventory must exclude gdcmconv")
    components = payload.get("components")
    require(isinstance(components, list), "GDCM static license components are missing")
    by_output = {
        item.get("packaged_path"): item
        for item in components
        if isinstance(item, dict) and isinstance(item.get("packaged_path"), str)
    }
    require(
        set(by_output) == set(GDCM_STATIC_LICENSE_SOURCES),
        "GDCM static license component set mismatch",
    )
    for output_name, source_name in GDCM_STATIC_LICENSE_SOURCES.items():
        item = by_output[output_name]
        content = license_payloads.get(output_name)
        require(content is not None, f"GDCM static license is missing: {output_name}")
        require(item.get("source_path") == source_name, f"GDCM license source mismatch: {output_name}")
        require(
            item.get("sha256") == hashlib.sha256(content).hexdigest(),
            f"GDCM static license SHA-256 mismatch: {output_name}",
        )
        require(
            item.get("size_bytes") == len(content),
            f"GDCM static license size mismatch: {output_name}",
        )


def verify_wheel(wheel: Path, expected_version: str | None = None) -> None:
    verify_wheel_release_identity(wheel, expected_version)
    verify_wheel_machos(
        wheel,
        maximum_macos=MINIMUM_SUPPORTED_MACOS_VERSION,
        require_arm64=True,
    )
    verify_wheel_system_macos_linkage(wheel)
    with zipfile.ZipFile(wheel) as archive:
        payloads = find_archive_model_payloads(
            archive,
            reject_all_checkpoint_extensions=True,
            reject_private_meshes=True,
            reject_private_medical_images=True,
        )
        require(
            not payloads,
            "wheel contains non-bundled model, private mesh, medical-image, or opaque archive payloads: "
            + ", ".join(payloads),
        )
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        require(len(metadata_names) == 1, f"expected one wheel METADATA: {wheel}")
        metadata_text = archive.read(metadata_names[0]).decode("utf-8")
        require("License-Expression: Apache-2.0" in metadata_text, f"wheel SPDX metadata missing: {wheel}")
        license_names = [name for name in names if name.endswith(".dist-info/licenses/LICENSE")]
        notice_names = [name for name in names if name.endswith(".dist-info/licenses/NOTICE")]
        require(len(license_names) == 1, f"wheel LICENSE missing: {wheel}")
        require(len(notice_names) == 1, f"wheel NOTICE missing: {wheel}")
        license_text = archive.read(license_names[0]).decode("utf-8")
        notice_text = archive.read(notice_names[0]).decode("utf-8")
        require(APACHE_HEADING in license_text and APACHE_TERMS in license_text, f"wheel LICENSE invalid: {wheel}")
        require("not relicensed" in notice_text, f"wheel NOTICE scope missing: {wheel}")
        wheel_license_basenames = {Path(name).name for name in names if "/licenses/" in name}
        dylibs = [name for name in names if "/bin/lib/" in name and name.endswith(".dylib")]
        require(not dylibs, f"static GDCM wheel unexpectedly contains DICOM dylibs: {wheel}")
        gdcm_inventory_names = [
            name
            for name in names
            if Path(name).name == GDCM_STATIC_LICENSE_INVENTORY
            and "/totalsegmentator_wrapper_mac/licenses/" in f"/{name}"
        ]
        require(
            len(gdcm_inventory_names) == 1,
            f"wheel GDCM static license inventory is missing or ambiguous: {wheel}",
        )
        package_license_names = {
            Path(name).name: name
            for name in names
            if "/totalsegmentator_wrapper_mac/licenses/" in f"/{name}"
        }
        for obsolete_name in ("json-c-MIT.txt", "OpenSSL-Apache-2.0.txt"):
            require(
                obsolete_name not in package_license_names,
                f"static GDCM wheel contains an unproven legacy license: {obsolete_name}",
            )
        for required_name in GDCM_STATIC_LICENSE_SOURCES:
            require(
                required_name in package_license_names,
                f"wheel GDCM static license is missing: {required_name}",
            )
        validate_gdcm_static_license_inventory(
            json.loads(archive.read(gdcm_inventory_names[0])),
            {
                license_name: archive.read(package_license_names[license_name])
                for license_name in GDCM_STATIC_LICENSE_SOURCES
            },
        )
        for provenance_name in (
            "dicom-normalizer-build-provenance.json",
            "gdcm-build-provenance.json",
        ):
            require(
                provenance_name in package_license_names,
                f"wheel native source-build provenance is missing: {provenance_name}",
            )
        normalizer_receipt_bytes = archive.read(
            package_license_names["dicom-normalizer-build-provenance.json"]
        )
        gdcm_receipt_bytes = archive.read(
            package_license_names["gdcm-build-provenance.json"]
        )
        try:
            normalizer_receipt_payload = json.loads(normalizer_receipt_bytes)
            normalizer_source = normalizer_source_from_receipts(
                normalizer_receipt_payload,
                receipt_bytes=normalizer_receipt_bytes,
                gdcm_receipt_bytes=gdcm_receipt_bytes,
            )
            validate_normalizer_packaged_provenance(
                normalizer_source,
                binary_input_sha256=normalizer_receipt_payload["binary_sha256"],
                receipt_bytes=normalizer_receipt_bytes,
                gdcm_receipt_bytes=gdcm_receipt_bytes,
                license_inventory_bytes=archive.read(gdcm_inventory_names[0]),
            )
        except (
            DicomNormalizerArtifactError,
            KeyError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise RuntimeError(
                f"invalid wheel DICOM normalizer source-build provenance: {exc}"
            ) from exc
        for required_name in {
            "TotalSegmentator-Apache-2.0.txt",
            "DentalSegmentator-NOTICE.txt",
            "ToothSeg-NOTICE.txt",
            "MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt",
            "TGNet-User-Provided-Checkpoint-NOTICE.txt",
            "TotalSegmentator-task-inventory.json",
        }:
            require(required_name in package_license_names, f"wheel model notice missing: {required_name}")
        dental_text = archive.read(
            package_license_names["DentalSegmentator-NOTICE.txt"]
        ).decode("utf-8")
        require("10.5281/zenodo.10829675" in dental_text, "wheel DentalSegmentator DOI missing")
        require("Gauthier Dot" in dental_text, "wheel DentalSegmentator creator missing")
        require(
            "https://creativecommons.org/licenses/by/4.0/" in dental_text,
            "wheel DentalSegmentator license URL missing",
        )
        toothseg_text = archive.read(package_license_names["ToothSeg-NOTICE.txt"]).decode("utf-8")
        require("10.5281/zenodo.14893540" in toothseg_text, "wheel ToothSeg DOI missing")
        require(
            "https://creativecommons.org/licenses/by/4.0/" in toothseg_text,
            "wheel ToothSeg license URL missing",
        )
        meshsegnet_text = archive.read(
            package_license_names["MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt"]
        ).decode("utf-8")
        require(
            "Apache License 2.0" in meshsegnet_text,
            "wheel MeshSegNet checkpoint license missing",
        )
        require(
            "3d2e44db8865ff3968803e86dadcf73cf9c4b738ddc35bfb3bc42c02347d7a0c"
            in meshsegnet_text,
            "wheel MeshSegNet checkpoint SHA-256 missing",
        )
        tgnet_text = archive.read(
            package_license_names["TGNet-User-Provided-Checkpoint-NOTICE.txt"]
        ).decode("utf-8")
        validate_tgnet_policy_notice(tgnet_text)
        task_inventory = json.loads(
            archive.read(package_license_names["TotalSegmentator-task-inventory.json"])
        )
        require(task_inventory.get("upstream_version") == "2.14.0", "wheel task audit version mismatch")
        require(
            [item.get("name") for item in task_inventory.get("user_selectable_tasks", [])]
            == ["craniofacial_structures", "teeth"],
            "wheel TotalSegmentator task allowlist mismatch",
        )
        require(
            [item.get("task_id") for item in task_inventory.get("helper_weights", [])] == [297],
            "wheel TotalSegmentator helper allowlist mismatch",
        )
        combined = metadata_text + license_text + notice_text
        for marker in OLD_FIRST_PARTY_MARKERS:
            require(marker not in combined, f"old first-party marker {marker!r} remains in wheel")


def _signed_app_team_identifier(app: Path) -> str:
    try:
        completed = subprocess.run(
            ["codesign", "-dv", "--verbose=4", str(app)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RuntimeError(f"could not inspect signed app identity: {detail.strip()}") from exc
    details = completed.stdout + completed.stderr
    match = re.search(r"^TeamIdentifier=(.+)$", details, re.MULTILINE)
    require(match is not None, "signed app TeamIdentifier is missing")
    return match.group(1).strip()


def verify_app_version_identity(
    app: Path,
    expected_version: str | None = None,
    expected_source_commit: str | None = None,
) -> str:
    contents = app / "Contents"
    resources = contents / "Resources"
    info_path = contents / "Info.plist"
    require(info_path.is_file(), f"app Info.plist is missing: {info_path}")
    with info_path.open("rb") as handle:
        info = plistlib.load(handle)

    manifest = json.loads(text(resources / "setup_manifest.json"))
    signing_mode = manifest.get("signing_mode")
    notarized_asserted = manifest.get("notarized") is True
    manifest_version = manifest.get("app_version") or manifest.get("version")
    require(
        isinstance(manifest_version, str)
        and VERSION_PATTERN.fullmatch(manifest_version) is not None,
        "app setup manifest version is missing or invalid",
    )
    version = expected_version or manifest_version
    require(manifest_version == version, f"app setup manifest version mismatch: expected {version}, found {manifest_version}")
    require(
        manifest.get("version") == version and manifest.get("app_version") == version,
        "app setup manifest version and app_version must both match the release version",
    )
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        require(
            str(info.get(key) or "") == version,
            f"app {key} mismatch: expected {version}, found {info.get(key) or 'missing'}",
        )
    require(
        "notarization_profile_name" not in manifest,
        "app setup manifest must not contain a local notarization profile name",
    )
    notarization_credentials_configured = manifest.get(
        "notarization_credentials_configured"
    )
    require(
        isinstance(notarization_credentials_configured, bool),
        "app setup manifest notarization_credentials_configured must be a JSON boolean",
    )
    if notarized_asserted:
        require(
            notarization_credentials_configured is True,
            "notarized app must record notarization credentials as configured",
        )
    if tuple(int(part) for part in version.split(".")) >= MINIMUM_SUPPORTED_MACOS_VERSION_FROM:
        require(
            str(info.get("LSMinimumSystemVersion") or "")
            == MINIMUM_SUPPORTED_MACOS_VERSION,
            "app LSMinimumSystemVersion mismatch: expected "
            f"{MINIMUM_SUPPORTED_MACOS_VERSION}, found "
            f"{info.get('LSMinimumSystemVersion') or 'missing'}",
        )
        require(
            manifest.get("minimum_macos_version") == MINIMUM_SUPPORTED_MACOS_VERSION,
            "app setup manifest minimum_macos_version mismatch: expected "
            f"{MINIMUM_SUPPORTED_MACOS_VERSION}",
        )
        runtime_fingerprint = manifest.get("python_runtime_fingerprint")
        runtime = manifest.get("python_runtime")
        require(
            isinstance(runtime, dict),
            "app python_runtime section is missing or invalid",
        )
        require(
            isinstance(runtime.get("bundled"), bool),
            "app python_runtime bundled flag is missing or invalid",
        )
        if signing_mode == "developer-id" or notarized_asserted:
            require(
                runtime.get("bundled") is True,
                "Developer ID and notarized apps require a bundled Python runtime",
            )
        if runtime.get("bundled") is True:
            require(
                isinstance(runtime_fingerprint, str)
                and SHA256_PATTERN.fullmatch(runtime_fingerprint) is not None,
                "bundled app python_runtime_fingerprint is missing or invalid",
            )
            require(
                runtime.get("fingerprint") == runtime_fingerprint,
                "bundled app python_runtime fingerprint does not match python_runtime_fingerprint",
            )
            require(
                runtime.get("fingerprint_scope")
                == "copied-runtime-payload-pre-sign-v1",
                "bundled app Python runtime fingerprint scope is missing or invalid",
            )
            require(
                runtime.get("required_major") == 3
                and runtime.get("required_minor") == 12,
                "bundled app Python runtime must declare Python 3.12",
            )
            bundle_relative = safe_app_resource_relative_path(
                runtime.get("bundle_path"),
                "bundled app Python runtime bundle_path",
            )
            executable_relative = safe_app_resource_relative_path(
                runtime.get("python_executable"),
                "bundled app Python runtime python_executable",
            )
            require(
                executable_relative.parts[: len(bundle_relative.parts)]
                == bundle_relative.parts,
                "bundled app Python runtime executable must be inside bundle_path",
            )
            bundle_path = resources.joinpath(*bundle_relative.parts)
            executable_path = resources.joinpath(*executable_relative.parts)
            require(
                bundle_path.is_dir() and not bundle_path.is_symlink(),
                "bundled app Python runtime bundle_path is missing or invalid",
            )
            require(
                executable_path.is_file()
                and not executable_path.is_symlink()
                and stat.S_ISREG(executable_path.stat().st_mode)
                and bool(executable_path.stat().st_mode & 0o111),
                "bundled app Python 3.12 executable is missing, symlinked, or not executable",
            )
            try:
                executable_path.resolve(strict=True).relative_to(
                    bundle_path.resolve(strict=True)
                )
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    "bundled app Python runtime executable resolves outside bundle_path"
                ) from exc
        else:
            # An external-Python development build cannot assert a complete
            # bundled tree.  Keep this blank so Swift falls back to its
            # executable digest and detects an external interpreter change.
            require(
                runtime_fingerprint in (None, "")
                and runtime.get("fingerprint") in (None, "")
                and runtime.get("fingerprint_scope") in (None, ""),
                "external app Python runtime must not declare a full fingerprint",
            )

    bundle_identifier = manifest.get("bundle_identifier")
    require(
        isinstance(bundle_identifier, str)
        and bundle_identifier
        and info.get("CFBundleIdentifier") == bundle_identifier,
        "app bundle identifier does not match Info.plist",
    )
    team_identifier = manifest.get("team_identifier")
    identity_status = manifest.get("bundle_identity_status")
    source_commit = manifest.get("source_commit")
    source_tree_dirty = manifest.get("source_tree_dirty")
    require(
        isinstance(source_commit, str)
        and SOURCE_COMMIT_PATTERN.fullmatch(source_commit) is not None,
        "app source_commit is missing or invalid",
    )
    require(
        isinstance(source_tree_dirty, bool),
        "app source_tree_dirty must be a JSON boolean",
    )
    if expected_source_commit is not None:
        require(
            source_commit == expected_source_commit,
            "app source_commit does not match the release source HEAD",
        )
        require(
            source_tree_dirty is False,
            "release app records a dirty source tree",
        )
    if signing_mode == "developer-id":
        require(
            bundle_identifier == CANONICAL_BUNDLE_IDENTIFIER,
            "Developer ID app does not use the canonical bundle identifier",
        )
        require(
            isinstance(team_identifier, str)
            and TEAM_IDENTIFIER_PATTERN.fullmatch(team_identifier) is not None,
            "Developer ID app manifest team_identifier is missing or invalid",
        )
        require(
            identity_status == "verified-developer-id",
            "Developer ID app manifest bundle identity status is not verified",
        )
        require(
            source_tree_dirty is False,
            "Developer ID app must be built from a clean source tree",
        )
        require(
            _signed_app_team_identifier(app) == team_identifier,
            "signed app TeamIdentifier does not match setup manifest",
        )
    else:
        require(signing_mode == "ad-hoc", "app signing_mode must be ad-hoc or developer-id")
        require(
            team_identifier is None and identity_status == "degraded-ad-hoc",
            "ad-hoc app identity must be explicitly recorded as degraded",
        )

    wheels = sorted((resources / "wheels").glob("totalsegmentator_wrapper_mac-*.whl"))
    require(len(wheels) == 1, "app must contain exactly one wrapper wheel")
    verify_wheel_release_identity(wheels[0], version)

    setup_weights_path = resources / SETUP_WEIGHTS_MANIFEST_NAME
    packaged_setup_weights = json.loads(text(setup_weights_path))
    validate_setup_weights_manifest(packaged_setup_weights)
    require(
        packaged_setup_weights == canonical_setup_weights_manifest(),
        "app setup weights manifest does not match the canonical manifest",
    )
    require(
        manifest.get("setup_weights_manifest_sha256") == sha256_file(setup_weights_path),
        "app setup weights manifest SHA-256 does not match setup_manifest.json",
    )
    require(
        manifest.get("bundled", {}).get("totalseg_setup_weights_manifest")
        == SETUP_WEIGHTS_MANIFEST_NAME,
        "app setup manifest does not identify the bundled setup weights manifest",
    )

    normalizer = resources / "bin" / "totalsegmentator-wrapper-dicom-normalizer"
    require(normalizer.is_file(), "app DICOM normalizer is missing")
    try:
        completed = subprocess.run(
            [str(normalizer), "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RuntimeError(f"could not inspect bundled normalizer version: {detail.strip()}") from exc
    require(
        completed.stdout.strip() == version,
        f"bundled normalizer version mismatch: expected {version}, found {completed.stdout.strip() or 'missing'}",
    )
    return version


def verify_bundled_wheel_code_signing(
    wheel: Path,
    *,
    expected_native_members: tuple[str, ...],
    team_identifier: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="tswm-wheel-signature-") as tmp:
        extract_root = Path(tmp)
        with zipfile.ZipFile(wheel) as archive:
            by_name = {
                info.filename: info
                for info in archive.infolist()
                if not info.is_dir() and info.filename in expected_native_members
            }
            require(
                set(by_name) == set(expected_native_members),
                "bundled wheel Developer ID native inventory is incomplete or ambiguous",
            )
            for member_name in expected_native_members:
                member = by_name[member_name]
                target = (extract_root / member.filename).resolve()
                require(
                    target.is_relative_to(extract_root.resolve()),
                    "bundled wheel has an unsafe native extension path",
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as destination:
                    while chunk := source.read(1024 * 1024):
                        destination.write(chunk)

                verification = subprocess.run(
                    ["codesign", "--verify", "--strict", "--verbose=2", str(target)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                require(
                    verification.returncode == 0,
                    "bundled wheel native binary has an invalid code signature: "
                    + (verification.stderr.strip() or verification.stdout.strip()),
                )
                details = subprocess.run(
                    ["codesign", "-dv", "--verbose=4", str(target)],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                signature = details.stdout + details.stderr
                require(
                    "Authority=Developer ID Application:" in signature
                    and f"TeamIdentifier={team_identifier}" in signature,
                    "bundled wheel native binary has the wrong Developer ID identity",
                )
                require(
                    "Timestamp=" in signature and "flags=0x10000(runtime)" in signature,
                    "bundled wheel native binary lacks secure timestamp or hardened runtime",
                )


def verify_bundled_acvl_utils_wheel(wheel: Path) -> None:
    require(
        wheel.name == "acvl_utils-0.2.6-py3-none-any.whl",
        "bundled acvl-utils wheel filename is not the pinned pure wheel",
    )
    dist_info = "acvl_utils-0.2.6.dist-info"
    required_names = {
        "metadata": f"{dist_info}/METADATA",
        "wheel": f"{dist_info}/WHEEL",
        "license": f"{dist_info}/licenses/LICENCE",
        "record": f"{dist_info}/RECORD",
    }
    native_suffixes = {".so", ".dylib", ".a", ".o"}
    macho_magics = {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    }
    try:
        archive = zipfile.ZipFile(wheel)
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"bundled acvl-utils wheel is not a valid ZIP: {exc}") from exc
    with archive:
        infos = archive.infolist()
        require(len(infos) <= MAX_ARCHIVE_MEMBERS, "bundled acvl-utils wheel has too many members")
        total_size = 0
        file_names: list[str] = []
        for info in infos:
            relative = PurePosixPath(info.filename)
            require(
                not relative.is_absolute() and ".." not in relative.parts,
                f"bundled acvl-utils wheel has an unsafe path: {info.filename}",
            )
            total_size += info.file_size
            require(
                info.file_size <= MAX_ARCHIVE_MEMBER_BYTES
                and total_size <= MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES,
                "bundled acvl-utils wheel exceeds archive size limits",
            )
            if info.is_dir():
                continue
            file_names.append(info.filename)
            require(
                relative.suffix.lower() not in native_suffixes,
                f"bundled acvl-utils wheel unexpectedly contains native code: {info.filename}",
            )
            with archive.open(info) as handle:
                require(
                    handle.read(4) not in macho_magics,
                    f"bundled acvl-utils wheel unexpectedly contains Mach-O code: {info.filename}",
                )
        require(
            len(file_names) == len(set(file_names)),
            "bundled acvl-utils wheel has duplicate file members",
        )
        for label, name in required_names.items():
            require(
                file_names.count(name) == 1,
                f"bundled acvl-utils wheel {label} file is missing or duplicated",
            )

        metadata_text = archive.read(required_names["metadata"]).decode("utf-8")
        metadata = Parser().parsestr(metadata_text)
        normalized_name = re.sub(r"[-_.]+", "-", metadata.get("Name", "")).lower()
        require(normalized_name == "acvl-utils", "bundled acvl-utils wheel package name is invalid")
        require(metadata.get("Version") == "0.2.6", "bundled acvl-utils wheel version is invalid")
        require(
            metadata.get("License-Expression") == "Apache-2.0",
            "bundled acvl-utils wheel license expression is not Apache-2.0",
        )
        wheel_text = archive.read(required_names["wheel"]).decode("utf-8")
        require(
            "Root-Is-Purelib: true" in wheel_text and "Tag: py3-none-any" in wheel_text,
            "bundled acvl-utils wheel is not the expected pure py3-none-any wheel",
        )
        license_text = archive.read(required_names["license"]).decode("utf-8")
        require(
            APACHE_HEADING in license_text and APACHE_TERMS in license_text,
            "bundled acvl-utils wheel Apache-2.0 license text is missing",
        )

        record_text = archive.read(required_names["record"]).decode("utf-8")
        record_rows = list(csv.reader(io.StringIO(record_text)))
        require(
            all(len(row) == 3 for row in record_rows),
            "bundled acvl-utils wheel RECORD contains malformed rows",
        )
        recorded_names = [row[0] for row in record_rows]
        require(
            len(recorded_names) == len(set(recorded_names))
            and set(recorded_names) == set(file_names),
            "bundled acvl-utils wheel RECORD does not cover each file exactly once",
        )
        for name, hash_spec, size_text in record_rows:
            if name == required_names["record"]:
                require(
                    hash_spec == "" and size_text == "",
                    "bundled acvl-utils wheel RECORD self-entry must be unhashed",
                )
                continue
            require(
                hash_spec.startswith("sha256=") and size_text.isdigit(),
                f"bundled acvl-utils wheel RECORD entry is incomplete: {name}",
            )
            payload = archive.read(name)
            expected_digest = base64.urlsafe_b64encode(
                hashlib.sha256(payload).digest()
            ).rstrip(b"=").decode("ascii")
            require(
                hash_spec == f"sha256={expected_digest}"
                and int(size_text) == len(payload),
                f"bundled acvl-utils wheel RECORD integrity mismatch: {name}",
            )


def verified_bundled_cpython_runtime_root(resources: Path) -> Path | None:
    """Return a safely located bundled runtime root for narrow scan exceptions."""

    manifest_path = resources / "setup_manifest.json"
    require(manifest_path.is_file(), "app setup manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runtime = manifest.get("python_runtime")
    if not isinstance(runtime, dict) or runtime.get("bundled") is not True:
        return None
    bundle_relative = safe_app_resource_relative_path(
        runtime.get("bundle_path"),
        "bundled app Python runtime bundle_path",
    )
    runtime_root = resources.joinpath(*bundle_relative.parts)
    require(
        runtime_root.is_dir() and not runtime_root.is_symlink(),
        "app bundled Python runtime root is missing or unsafe",
    )
    return runtime_root


def verify_app(
    app: Path,
    expected_version: str | None = None,
    expected_source_commit: str | None = None,
) -> None:
    resources = app / "Contents" / "Resources"
    require(resources.is_dir(), f"app Resources directory is missing: {resources}")
    wheels = sorted((resources / "wheels").glob("totalsegmentator_wrapper_mac-*.whl"))
    require(len(wheels) == 1, "app must contain exactly one wrapper wheel")
    verified_version = verify_app_version_identity(
        app,
        expected_version,
        expected_source_commit,
    )
    verified_cpython_runtime_root = verified_bundled_cpython_runtime_root(resources)
    authorized_medical_images = verified_authorized_sample_nifti_paths(resources)
    verify_app_machos(
        app,
        maximum_macos=MINIMUM_SUPPORTED_MACOS_VERSION,
        require_arm64=True,
    )
    verify_app_bundle_macos_linkage(app)
    for bundled_wheel in sorted((resources / "wheels").glob("*.whl")):
        verify_wheel_self_contained_macos_linkage(bundled_wheel)
    payloads = find_tree_model_payloads(
        resources,
        [
            path
            for path in resources.rglob("*")
            if path.is_file() or path.is_symlink()
        ],
        reject_all_checkpoint_extensions=True,
        reject_private_meshes=True,
        reject_private_medical_images=True,
        verified_cpython_runtime_root=verified_cpython_runtime_root,
        authorized_medical_image_paths=authorized_medical_images,
    )
    require(
        not payloads,
        "app contains non-bundled model, private mesh, medical-image, or opaque archive payloads: "
        + ", ".join(payloads),
    )
    verify_apache_license(resources / "LICENSE")
    verify_notice(resources / "NOTICE")

    fpsample_wheels = list((resources / "wheels").glob("fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl"))
    require(len(fpsample_wheels) == 1, "app must contain exactly one macOS 13 arm64 fpsample wheel")
    verify_wheel_system_macos_linkage(fpsample_wheels[0])
    with zipfile.ZipFile(fpsample_wheels[0]) as archive:
        names = archive.namelist()
        require(
            any(name.endswith(".dist-info/licenses/LICENSE") for name in names),
            "bundled fpsample wheel MIT license is missing",
        )
    acvl_utils_wheels = list(
        (resources / "wheels").glob("acvl_utils-0.2.6-py3-none-any.whl")
    )
    require(
        len(acvl_utils_wheels) == 1,
        "app must contain exactly one pinned pure-Python acvl-utils wheel",
    )
    verify_bundled_acvl_utils_wheel(acvl_utils_wheels[0])
    open3d_wheels = list(
        (resources / "wheels").glob(
            "open3d-0.19.0-cp312-cp312-macosx_10_15_universal2.whl"
        )
    )
    require(len(open3d_wheels) == 1, "app must contain exactly one pinned Open3D wheel")

    dental = text(resources / "licenses" / "DentalSegmentator-NOTICE.txt")
    require("10.5281/zenodo.10829675" in dental, "DentalSegmentator DOI missing")
    require("Gauthier Dot" in dental, "DentalSegmentator creator missing")
    require("https://creativecommons.org/licenses/by/4.0/" in dental, "DentalSegmentator license URL missing")
    require("checkpoint parameters are not modified" in dental, "DentalSegmentator change status missing")

    toothseg = text(resources / "licenses" / "ToothSeg-NOTICE.txt")
    require("10.5281/zenodo.14893540" in toothseg, "ToothSeg DOI missing")
    require("https://creativecommons.org/licenses/by/4.0/" in toothseg, "ToothSeg license URL missing")
    require("Changes made by this project" in toothseg, "ToothSeg changes missing")

    verify_apache_license(resources / "licenses" / "TotalSegmentator-Apache-2.0.txt")
    task_inventory = json.loads(text(resources / "licenses" / "TotalSegmentator-task-inventory.json"))
    require(task_inventory.get("upstream_version") == "2.14.0", "TotalSegmentator audit version mismatch")
    task_names = [item.get("name") for item in task_inventory.get("user_selectable_tasks", [])]
    require(task_names == ["craniofacial_structures", "teeth"], "TotalSegmentator task allowlist mismatch")
    require(
        [item.get("task_id") for item in task_inventory.get("helper_weights", [])] == [297],
        "TotalSegmentator helper weight allowlist mismatch",
    )
    require(
        not any(
            item.get("requires_upstream_license_gate")
            for item in task_inventory.get("user_selectable_tasks", [])
            + task_inventory.get("helper_weights", [])
        ),
        "licensed TotalSegmentator task entered public inventory",
    )
    for required_license in {
        *GDCM_STATIC_LICENSE_SOURCES,
        GDCM_STATIC_LICENSE_INVENTORY,
        "dcm2niix-license.txt",
    }:
        require((resources / "licenses" / required_license).is_file(), f"native license missing: {required_license}")
    for obsolete_license in ("json-c-MIT.txt", "OpenSSL-Apache-2.0.txt"):
        require(
            not (resources / "licenses" / obsolete_license).exists(),
            f"static GDCM app contains an unproven legacy license: {obsolete_license}",
        )
    dylibs = sorted((resources / "bin" / "lib").glob("*.dylib"))
    require(not dylibs, "static GDCM app unexpectedly contains DICOM dylibs")
    validate_gdcm_static_license_inventory(
        json.loads(text(resources / "licenses" / GDCM_STATIC_LICENSE_INVENTORY)),
        {
            license_name: (resources / "licenses" / license_name).read_bytes()
            for license_name in GDCM_STATIC_LICENSE_SOURCES
        },
    )
    manifest = json.loads(text(resources / "setup_manifest.json"))
    for component, relative_path in (
        ("normalizer", "bin/totalsegmentator-wrapper-dicom-normalizer"),
        ("dcm2niix", "bin/dcm2niix"),
    ):
        input_field = f"{component}_input_sha256"
        legacy_field = f"{component}_sha256"
        scope_field = f"{component}_sha256_scope"
        input_digest = manifest.get(input_field)
        require(
            isinstance(input_digest, str)
            and SHA256_PATTERN.fullmatch(input_digest) is not None,
            f"app manifest {input_field} is missing or invalid",
        )
        require(
            manifest.get(legacy_field) == input_digest,
            f"app manifest legacy {legacy_field} must equal {input_field}",
        )
        require(
            manifest.get(scope_field) == NATIVE_INPUT_DIGEST_SCOPE,
            f"app manifest {scope_field} must identify the pre-sign build input scope",
        )
        bundled_field = f"{component}_bundled_sha256"
        if bundled_field in manifest:
            bundled_path = resources / relative_path
            require(
                manifest.get(bundled_field) == sha256_file(bundled_path),
                f"app manifest {bundled_field} does not match bundled bytes",
            )
    require(manifest.get("license", {}).get("expression") == "Apache-2.0", "app manifest SPDX license missing")
    bundled = manifest.get("bundled", {})
    for key in (
        "wrapper_license",
        "wrapper_notice",
        "totalsegmentator_license",
        "totalsegmentator_task_inventory",
        "dentalsegmentator_notice",
        "toothseg_notice",
        "meshsegnet_checkpoint_notice",
        "tgnet_checkpoint_policy_notice",
        "fpsample_wheel",
        "fpsample_license",
        "acvl_utils_wheel",
        "totalseg_setup_weights_manifest",
        "dcm2niix",
        "dcm2niix_license",
        "gdcm_static_license_inventory",
        "dicom_normalizer_build_provenance",
        "gdcm_build_provenance",
        "third_party_license_inventory",
        "requirements_lock",
        "dependency_lock_metadata",
        "project_file",
        "release_build_toolchain_lock",
        "release_build_toolchain_metadata",
        "release_build_toolchain_receipt",
    ):
        require(key in bundled, f"app manifest bundled.{key} missing")
    release_runtime_attestation_required = (
        manifest.get("signing_mode") == "developer-id"
        or manifest.get("notarized") is True
    )
    release_dependency_attestation_required = (
        bundled.get("requirements_lock") is not None
    )
    third_party_licenses = manifest.get("third_party_licenses")
    require(
        isinstance(third_party_licenses, dict),
        "app manifest third_party_licenses section is missing or invalid",
    )
    if release_dependency_attestation_required:
        require(
            third_party_licenses.get("inventory_mode") == "release_hashed_lock"
            and third_party_licenses.get("release_eligible") is True,
            "release app third-party license inventory is not derived from the canonical hashed lock",
        )
    else:
        require(
            third_party_licenses.get("inventory_mode")
            in {"development_constraints", "development_explicit_site_path"}
            and third_party_licenses.get("release_eligible") is False,
            "development app third-party license inventory must be explicitly marked non-release-eligible",
        )
    if release_dependency_attestation_required:
        require(
            bundled.get("requirements_lock")
            == "constraints/macos-arm64-py312.requirements.lock"
            and bundled.get("dependency_lock_metadata")
            == "constraints/macos-arm64-py312.lock.json"
            and bundled.get("project_file") == "constraints/pyproject.toml",
            "release app dependency lock paths are missing or invalid",
        )
        requirements_lock = resources / bundled["requirements_lock"]
        dependency_lock_metadata = resources / bundled["dependency_lock_metadata"]
        project_file = resources / bundled["project_file"]
        require(
            requirements_lock.is_file() and not requirements_lock.is_symlink(),
            "release app canonical requirements lock is missing",
        )
        require(
            dependency_lock_metadata.is_file()
            and not dependency_lock_metadata.is_symlink(),
            "release app dependency lock metadata is missing",
        )
        require(
            project_file.is_file() and not project_file.is_symlink(),
            "release app project dependency declarations are missing",
        )
        require(
            manifest.get("requirements_lock_sha256")
            == sha256_file(requirements_lock),
            "release app canonical requirements lock SHA-256 mismatch",
        )
        require(
            manifest.get("dependency_lock_metadata_sha256")
            == sha256_file(dependency_lock_metadata),
            "release app dependency lock metadata SHA-256 mismatch",
        )
        require(
            manifest.get("project_file_sha256") == sha256_file(project_file),
            "release app project dependency declarations SHA-256 mismatch",
        )
        try:
            verify_canonical_dependency_lock(
                constraints=resources / bundled["constraints"],
                requirements_lock=requirements_lock,
                lock_metadata=dependency_lock_metadata,
                project_file=project_file,
                setup_manager_source_text=read_wrapper_setup_manager_source(wheels[0]),
            )
        except (ReleaseInputReadinessError, RuntimeError) as exc:
            raise RuntimeError(
                f"release app dependency lock metadata binding is invalid: {exc}"
            ) from exc
    else:
        require(
            manifest.get("requirements_lock_sha256") is None
            and manifest.get("dependency_lock_metadata_sha256") is None
            and manifest.get("project_file_sha256") is None
            and bundled.get("requirements_lock") is None
            and bundled.get("dependency_lock_metadata") is None
            and bundled.get("project_file") is None,
            "development app must not claim release-attested dependency locks",
        )
    if release_runtime_attestation_required:
        require(
            bundled.get("release_build_toolchain_lock")
            == "build-toolchain/macos-arm64-py312.release-build-toolchain.lock"
            and bundled.get("release_build_toolchain_metadata")
            == "build-toolchain/macos-arm64-py312.release-build-toolchain.lock.json"
            and bundled.get("release_build_toolchain_receipt")
            == "build-toolchain/release-build-toolchain-receipt.json",
            "release app build-toolchain provenance paths are missing or invalid",
        )
        build_toolchain_lock = resources / bundled["release_build_toolchain_lock"]
        build_toolchain_metadata = resources / bundled[
            "release_build_toolchain_metadata"
        ]
        build_toolchain_receipt = resources / bundled[
            "release_build_toolchain_receipt"
        ]
        build_toolchain_root = resources / "build-toolchain"
        require(
            build_toolchain_root.is_dir() and not build_toolchain_root.is_symlink(),
            "release app build-toolchain provenance directory is missing or unsafe",
        )
        for path, label in (
            (build_toolchain_lock, "release app build-toolchain lock"),
            (build_toolchain_metadata, "release app build-toolchain metadata"),
            (build_toolchain_receipt, "release app build-toolchain receipt"),
        ):
            require(
                path.is_file() and not path.is_symlink(),
                f"{label} is missing",
            )
        require(
            manifest.get("release_build_toolchain_lock_sha256")
            == sha256_file(build_toolchain_lock),
            "release app build-toolchain lock SHA-256 mismatch",
        )
        require(
            manifest.get("release_build_toolchain_metadata_sha256")
            == sha256_file(build_toolchain_metadata),
            "release app build-toolchain metadata SHA-256 mismatch",
        )
        require(
            manifest.get("release_build_toolchain_receipt_sha256")
            == sha256_file(build_toolchain_receipt),
            "release app build-toolchain receipt SHA-256 mismatch",
        )
        try:
            build_toolchain_receipt_payload = verify_release_build_toolchain_receipt(
                receipt_path=build_toolchain_receipt,
                lock_path=build_toolchain_lock,
                metadata_path=build_toolchain_metadata,
            )
        except ReleaseBuildToolchainError as exc:
            raise RuntimeError(
                f"release app build-toolchain provenance is invalid: {exc}"
            ) from exc
        expected_build_toolchain_provenance = {
            "lock_sha256": build_toolchain_receipt_payload["lock_sha256"],
            "metadata_sha256": build_toolchain_receipt_payload["metadata_sha256"],
            "uv": build_toolchain_receipt_payload["toolchain"]["uv"],
            "python": build_toolchain_receipt_payload["toolchain"]["python"],
            "native_toolchain": build_toolchain_receipt_payload["toolchain"][
                "native_toolchain"
            ],
        }
        require(
            manifest.get("release_build_toolchain")
            == expected_build_toolchain_provenance,
            "release app build-toolchain manifest provenance does not match its receipt",
        )
    else:
        require(
            manifest.get("fpsample_pre_sign_wheel_sha256") is None
            and manifest.get("release_build_toolchain_lock_sha256") is None
            and manifest.get("release_build_toolchain_metadata_sha256") is None
            and manifest.get("release_build_toolchain_receipt_sha256") is None
            and manifest.get("release_build_toolchain") is None
            and bundled.get("release_build_toolchain_lock") is None
            and bundled.get("release_build_toolchain_metadata") is None
            and bundled.get("release_build_toolchain_receipt") is None,
            "development app must not claim release build-toolchain provenance",
        )
        require(
            not (resources / "build-toolchain").exists(),
            "development app must not bundle release build-toolchain provenance",
        )
    require(
        bundled.get("dicom_normalizer_build_provenance")
        == "licenses/dicom-normalizer-build-provenance.json",
        "app manifest normalizer build provenance path mismatch",
    )
    require(
        bundled.get("gdcm_build_provenance")
        == "licenses/gdcm-build-provenance.json",
        "app manifest GDCM build provenance path mismatch",
    )
    normalizer_receipt_path = (
        resources / "licenses" / "dicom-normalizer-build-provenance.json"
    )
    gdcm_receipt_path = resources / "licenses" / "gdcm-build-provenance.json"
    require(
        normalizer_receipt_path.is_file() and not normalizer_receipt_path.is_symlink(),
        "app normalizer build receipt is missing",
    )
    require(
        gdcm_receipt_path.is_file() and not gdcm_receipt_path.is_symlink(),
        "app GDCM build receipt is missing",
    )
    try:
        validate_normalizer_packaged_provenance(
            manifest.get("normalizer_source"),
            binary_input_sha256=manifest["normalizer_input_sha256"],
            receipt_bytes=normalizer_receipt_path.read_bytes(),
            gdcm_receipt_bytes=gdcm_receipt_path.read_bytes(),
            license_inventory_bytes=(
                resources / "licenses" / GDCM_STATIC_LICENSE_INVENTORY
            ).read_bytes(),
        )
    except DicomNormalizerArtifactError as exc:
        raise RuntimeError(f"invalid DICOM normalizer source-build provenance: {exc}") from exc
    dcm2niix_source = manifest.get("dcm2niix_source")
    dcm2niix_license_bytes = (
        resources / "licenses" / "dcm2niix-license.txt"
    ).read_bytes()
    require(
        hashlib.sha256(dcm2niix_license_bytes).hexdigest()
        == DCM2NIIX_PINNED_LICENSE_SHA256,
        "app dcm2niix license differs from the pinned official source license",
    )
    if isinstance(dcm2niix_source, dict) and dcm2niix_source.get("kind") == "pinned-official-source-build":
        require(
            bundled.get("dcm2niix_build_provenance")
            == "licenses/dcm2niix-build-provenance.json",
            "app manifest bundled.dcm2niix_build_provenance is missing",
        )
        require(
            bundled.get("dcm2niix_artifact_pointer")
            == "licenses/dcm2niix-current-artifact.json",
            "app manifest bundled.dcm2niix_artifact_pointer is missing",
        )
        receipt_path = resources / "licenses" / "dcm2niix-build-provenance.json"
        pointer_path = resources / "licenses" / "dcm2niix-current-artifact.json"
        require(receipt_path.is_file() and not receipt_path.is_symlink(), "app dcm2niix build receipt is missing")
        require(pointer_path.is_file() and not pointer_path.is_symlink(), "app dcm2niix artifact pointer is missing")
        receipt_bytes = receipt_path.read_bytes()
        pointer_bytes = pointer_path.read_bytes()
        try:
            receipt_payload = json.loads(receipt_bytes)
            pointer_payload = json.loads(pointer_bytes)
            validate_dcm2niix_build_receipt(
                receipt_payload,
                manifest["dcm2niix_input_sha256"],
            )
            validate_dcm2niix_artifact_pointer(
                pointer_payload,
                manifest["dcm2niix_input_sha256"],
            )
            validate_dcm2niix_source_manifest(
                dcm2niix_source,
                binary_sha256=manifest["dcm2niix_input_sha256"],
                receipt_bytes=receipt_bytes,
                pointer_bytes=pointer_bytes,
            )
        except (Dcm2niixSourceArtifactError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid pinned dcm2niix build provenance: {exc}") from exc
    else:
        require(
            dcm2niix_source == dcm2niix_development_source_manifest(),
            "app dcm2niix_source is neither pinned release provenance nor the explicit development marker",
        )
        require(
            manifest.get("signing_mode") != "developer-id"
            and manifest.get("notarized") is not True,
            "Developer ID and notarized apps cannot use an unpinned dcm2niix development input",
        )
        require(
            bundled.get("dcm2niix_build_provenance") is None
            and bundled.get("dcm2niix_artifact_pointer") is None,
            "development-only dcm2niix input must not claim pinned provenance files",
        )
    meshsegnet_notice = text(
        resources / "licenses" / "MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt"
    )
    require(
        "Apache License 2.0" in meshsegnet_notice,
        "app MeshSegNet checkpoint license missing",
    )
    require(
        "3d2e44db8865ff3968803e86dadcf73cf9c4b738ddc35bfb3bc42c02347d7a0c"
        in meshsegnet_notice,
        "app MeshSegNet checkpoint SHA-256 missing",
    )
    require(
        "huathedev/3D-Teeth-Scan-Semantic-Segmentation-with-MeshSegNet"
        in meshsegnet_notice,
        "app MeshSegNet canonical model source is missing",
    )
    tgnet_notice = text(
        resources / "licenses" / "TGNet-User-Provided-Checkpoint-NOTICE.txt"
    )
    validate_tgnet_policy_notice(tgnet_notice)
    combined_notices = text(resources / "THIRD_PARTY_NOTICES.txt")
    require(
        "MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt" in combined_notices,
        "app combined notices do not reference canonical MeshSegNet provenance",
    )
    require(
        "ibrahimhamamci/3DTeethSeg" not in combined_notices,
        "app combined notices contain stale MeshSegNet provenance",
    )
    require(
        "TGNet-User-Provided-Checkpoint-NOTICE.txt" in combined_notices,
        "app combined notices omit the TGNet user-provided checkpoint policy",
    )
    fpsample_notice = text(
        resources / "licenses" / "fpsample-1.0.2-MIT-and-nanoflann-BSD.txt"
    )
    require("MIT License" in fpsample_notice, "app fpsample MIT license missing")
    require(
        "Software License Agreement (BSD License)" in fpsample_notice,
        "app bundled nanoflann BSD notice missing",
    )
    require(
        manifest.get("fpsample_wheel_sha256") == sha256_file(fpsample_wheels[0]),
        "app fpsample wheel SHA-256 does not match manifest",
    )
    require(
        manifest.get("acvl_utils_wheel_sha256")
        == sha256_file(acvl_utils_wheels[0]),
        "app acvl-utils wheel SHA-256 does not match manifest",
    )
    if release_runtime_attestation_required:
        try:
            verify_bundled_override_release_hash_boundary(
                manifest=manifest,
                lock_metadata=json.loads(
                    dependency_lock_metadata.read_text(encoding="utf-8")
                ),
                wheels={
                    "acvl-utils": acvl_utils_wheels[0],
                    "fpsample": fpsample_wheels[0],
                },
            )
        except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError) as exc:
            raise RuntimeError(
                f"release app bundled override hash boundary is invalid: {exc}"
            ) from exc
    if manifest.get("signing_mode") == "developer-id":
        team_identifier = manifest.get("team_identifier")
        require(
            isinstance(team_identifier, str),
            "Developer ID app Team ID is missing before wheel signature verification",
        )
        assert isinstance(team_identifier, str)
        verify_bundled_wheel_code_signing(
            fpsample_wheels[0],
            expected_native_members=("fpsample/_fpsample.cpython-312-darwin.so",),
            team_identifier=team_identifier,
        )
        verify_bundled_wheel_code_signing(
            open3d_wheels[0],
            expected_native_members=(
                "open3d/cpu/pybind.cpython-312-darwin.so",
                "open3d/libomp.dylib",
                "open3d/libtbb.12.dylib",
            ),
            team_identifier=team_identifier,
        )

    inventory_text = text(resources / "licenses" / "third_party_license_inventory.json")
    inventory = json.loads(inventory_text)
    require(inventory.get("unresolved_count") == 0, "license inventory contains unresolved items")
    acvl_inventory = [
        item
        for item in inventory.get("packages", [])
        if re.sub(r"[-_.]+", "-", str(item.get("name", ""))).lower()
        == "acvl-utils"
    ]
    require(
        len(acvl_inventory) == 1
        and acvl_inventory[0].get("version") == "0.2.6"
        and acvl_inventory[0].get("license") == "Apache-2.0"
        and acvl_inventory[0].get("license_files"),
        "license inventory does not record bundled acvl-utils 0.2.6 as Apache-2.0 with license text",
    )
    first_party = [
        item
        for item in inventory.get("packages", [])
        if item.get("scope") == "first-party"
    ]
    require(len(first_party) == 1, "license inventory must contain one classified first-party package")
    require(first_party[0].get("license") == "Apache-2.0", "first-party package is not Apache-2.0")
    for marker in OLD_FIRST_PARTY_MARKERS:
        require(marker not in inventory_text, f"old first-party marker {marker!r} remains in inventory")

    sample_root = resources / "sample1"
    sample_manifest = json.loads(text(sample_root / "sample_manifest.json"))
    for relative, metadata in sample_manifest.get("derived_files", {}).items():
        require(
            sha256_file(sample_root / relative) == metadata.get("sha256"),
            f"Sample 1 hash mismatch: {relative}",
        )
    model_root = resources / "model_comparison"
    model_provenance = json.loads(text(model_root / "ASSET_PROVENANCE.json"))
    require(model_provenance.get("apache_2_0_relicensed") is False, "model images must remain outside wrapper Apache scope")
    for name, metadata in model_provenance.get("files", {}).items():
        require(
            sha256_file(model_root / name) == metadata.get("sha256"),
            f"model comparison image hash mismatch: {name}",
        )

    verify_wheel(wheels[0], verified_version)


def verify_dmg(
    dmg: Path,
    expected_version: str | None = None,
    expected_source_commit: str | None = None,
) -> None:
    require(dmg.is_file(), f"DMG is missing: {dmg}")
    with tempfile.TemporaryDirectory(prefix="tswm-license-dmg-") as tmp:
        mount = Path(tmp) / "mount"
        mount.mkdir()
        subprocess.run(
            ["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(dmg)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            root_entries = {path.name: path for path in mount.iterdir()}
            require(
                set(root_entries) == DMG_ROOT_ALLOWLIST,
                "DMG root entry set mismatch: expected "
                + ", ".join(sorted(DMG_ROOT_ALLOWLIST))
                + "; found "
                + ", ".join(sorted(root_entries)),
            )
            applications = root_entries["Applications"]
            require(
                applications.is_symlink()
                and os.readlink(applications) == "/Applications",
                "DMG Applications link must point exactly to /Applications",
            )
            app_resources = (
                root_entries["TotalSegmentator Wrapper for Mac.app"]
                / "Contents"
                / "Resources"
            )
            verified_cpython_runtime_root = verified_bundled_cpython_runtime_root(
                app_resources
            )
            authorized_medical_images = verified_authorized_sample_nifti_paths(
                app_resources
            )
            dmg_payloads = find_tree_model_payloads(
                mount,
                [
                    path
                    for path in mount.rglob("*")
                    if (path.is_file() or path.is_symlink()) and path != applications
                ],
                reject_all_checkpoint_extensions=True,
                reject_private_meshes=True,
                reject_private_medical_images=True,
                verified_cpython_runtime_root=verified_cpython_runtime_root,
                authorized_medical_image_paths=authorized_medical_images,
            )
            require(
                not dmg_payloads,
                "DMG contains non-bundled model, private mesh, or medical-image payloads: "
                + ", ".join(dmg_payloads),
            )
            verify_apache_license(mount / "LICENSE.txt")
            verify_notice(mount / "NOTICE.txt")
            verify_app(
                mount / "TotalSegmentator Wrapper for Mac.app",
                expected_version,
                expected_source_commit,
            )
            readme = text(mount / "README.txt")
            require("Apache License 2.0" in readme, "DMG README does not identify wrapper Apache-2.0")
            require("DentalSegmentator-NOTICE.txt" in readme, "DMG README omits DentalSegmentator notice")
            require("ToothSeg-NOTICE.txt" in readme, "DMG README omits ToothSeg notice")
            require("MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt" in readme, "DMG README omits MeshSegNet notice")
            require("TGNet-User-Provided-Checkpoint-NOTICE.txt" in readme, "DMG README omits TGNet policy notice")
            require("https://forms.gle/QFPwF1Pi5C8bmSuw6" in readme, "DMG README omits Google support form")
            require("github.com/ainem-m/segmentation_w_mps/issues へ報告" not in readme, "DMG README still directs end users to GitHub Issues")
            for marker in OLD_FIRST_PARTY_MARKERS:
                require(marker not in readme, f"old first-party marker {marker!r} remains in DMG README")
        finally:
            subprocess.run(
                ["hdiutil", "detach", str(mount)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify wrapper and third-party license surfaces.")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--dmg", type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-source-commit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require(
        any((args.source, args.wheel, args.app, args.dmg)),
        "at least one of --source, --wheel, --app, or --dmg is required",
    )
    if args.source:
        verify_source(args.source.expanduser().resolve())
    if args.wheel:
        verify_wheel(args.wheel.expanduser().resolve(), args.expected_version)
    if args.app:
        verify_app(
            args.app.expanduser().resolve(),
            args.expected_version,
            args.expected_source_commit,
        )
    if args.dmg:
        verify_dmg(
            args.dmg.expanduser().resolve(),
            args.expected_version,
            args.expected_source_commit,
        )
    print("License distribution verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
