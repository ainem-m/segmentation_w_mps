#!/usr/bin/env python3
"""Collect the license inventory for the pinned, statically linked GDCM build."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence


GDCM_VERSION = "3.2.7"
GDCM_SOURCE_URL = "https://github.com/malaterre/GDCM/archive/refs/tags/v3.2.7.tar.gz"
GDCM_SOURCE_SHA256 = "b7b17b70c009677cf244cc7837b88386441e097f8861fdeee83aa27d1bc1b090"


class GDCMLicenseError(RuntimeError):
    pass


@dataclass(frozen=True)
class LicenseSpec:
    component: str
    source: str
    output: str
    marker: str


LICENSE_SPECS = (
    LicenseSpec(
        "GDCM",
        "Copyright.txt",
        "GDCM-BSD-3-Clause.txt",
        "Redistribution and use in source and binary forms",
    ),
    LicenseSpec(
        "GDCM embedded IJG JPEG",
        "Utilities/gdcmjpeg/README",
        "GDCM-IJG-JPEG-README.txt",
        "Independent JPEG Group",
    ),
    LicenseSpec(
        "GDCM embedded OpenJPEG",
        "Utilities/gdcmopenjpeg/LICENSE",
        "OpenJPEG-BSD-2-Clause.txt",
        "2-clauses BSD License",
    ),
    LicenseSpec(
        "GDCM embedded CharLS",
        "Utilities/gdcmcharls/License.txt",
        "CharLS-BSD-3-Clause.txt",
        "Redistribution and use in source and binary forms",
    ),
    LicenseSpec(
        "GDCM embedded Expat",
        "Utilities/gdcmexpat/COPYING",
        "Expat-MIT.txt",
        "Permission is hereby granted, free of charge",
    ),
    LicenseSpec(
        "GDCM embedded zlib",
        "Utilities/gdcmzlib/LICENSE",
        "zlib-Zlib.txt",
        "Permission is granted to anyone to use this software",
    ),
    LicenseSpec(
        "GDCM embedded UUID",
        "Utilities/gdcmuuid/COPYING",
        "GDCM-UUID-BSD-3-Clause.txt",
        "Redistribution and use in source and binary forms",
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def collect_gdcm_source_licenses(source_root: Path, output_dir: Path) -> Path:
    if not source_root.is_dir() or source_root.is_symlink():
        raise GDCMLicenseError(f"invalid GDCM source root: {source_root}")
    if output_dir.exists():
        if not output_dir.is_dir() or output_dir.is_symlink() or any(output_dir.iterdir()):
            raise GDCMLicenseError(
                f"GDCM license output must be a new or empty directory: {output_dir}"
            )
    else:
        output_dir.mkdir(parents=True)

    inventory: list[dict[str, object]] = []
    for spec in LICENSE_SPECS:
        relative = PurePosixPath(spec.source)
        source = source_root.joinpath(*relative.parts)
        if not source.is_file() or source.is_symlink():
            raise GDCMLicenseError(
                f"GDCM {GDCM_VERSION} license source is missing: {spec.source}"
            )
        value = source.read_text(encoding="utf-8")
        if spec.marker not in value:
            raise GDCMLicenseError(
                f"GDCM license source marker is missing: {spec.source}"
            )
        destination = output_dir / spec.output
        shutil.copyfile(source, destination)
        inventory.append(
            {
                "component": spec.component,
                "source_path": spec.source,
                "packaged_path": spec.output,
                "sha256": sha256_file(destination),
                "size_bytes": destination.stat().st_size,
            }
        )

    manifest = {
        "schema": "totalsegmentator_wrapper_mac.gdcm_static_license_inventory.v1",
        "gdcm_version": GDCM_VERSION,
        "source_url": GDCM_SOURCE_URL,
        "source_archive_sha256": GDCM_SOURCE_SHA256,
        "linkage": "static",
        "gdcmconv_bundled": False,
        "components": inventory,
    }
    manifest_path = output_dir / "GDCM-static-license-inventory.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def verify_gdcm_license_directory(output_dir: Path) -> Path:
    if not output_dir.is_dir() or output_dir.is_symlink():
        raise GDCMLicenseError(f"invalid GDCM license directory: {output_dir}")
    manifest_path = output_dir / "GDCM-static-license-inventory.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GDCMLicenseError(f"invalid GDCM static license inventory: {exc}") from exc
    if manifest.get("schema") != "totalsegmentator_wrapper_mac.gdcm_static_license_inventory.v1":
        raise GDCMLicenseError("GDCM static license inventory schema mismatch")
    if (
        manifest.get("gdcm_version") != GDCM_VERSION
        or manifest.get("source_url") != GDCM_SOURCE_URL
        or manifest.get("source_archive_sha256") != GDCM_SOURCE_SHA256
        or manifest.get("linkage") != "static"
        or manifest.get("gdcmconv_bundled") is not False
    ):
        raise GDCMLicenseError("GDCM static license inventory provenance mismatch")
    components = manifest.get("components")
    if not isinstance(components, list):
        raise GDCMLicenseError("GDCM static license components are missing")
    by_output = {
        item.get("packaged_path"): item
        for item in components
        if isinstance(item, dict) and isinstance(item.get("packaged_path"), str)
    }
    if set(by_output) != {spec.output for spec in LICENSE_SPECS}:
        raise GDCMLicenseError("GDCM static license component set mismatch")
    expected_files = {spec.output for spec in LICENSE_SPECS} | {manifest_path.name}
    actual_files = {path.name for path in output_dir.iterdir() if path.is_file()}
    if actual_files != expected_files:
        raise GDCMLicenseError("GDCM static license directory contains an unexpected file set")
    for spec in LICENSE_SPECS:
        item = by_output[spec.output]
        path = output_dir / spec.output
        if not path.is_file() or path.is_symlink():
            raise GDCMLicenseError(f"GDCM static license is missing: {spec.output}")
        if item.get("source_path") != spec.source:
            raise GDCMLicenseError(f"GDCM static license source mismatch: {spec.output}")
        if item.get("sha256") != sha256_file(path) or item.get("size_bytes") != path.stat().st_size:
            raise GDCMLicenseError(f"GDCM static license integrity mismatch: {spec.output}")
    return manifest_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect pinned GDCM source licenses.")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify-output-dir", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.verify_output_dir is not None:
        if args.source_root is not None or args.output_dir is not None:
            raise GDCMLicenseError("--verify-output-dir cannot be combined with collection arguments")
        manifest = verify_gdcm_license_directory(
            args.verify_output_dir.expanduser().resolve()
        )
    else:
        if args.source_root is None or args.output_dir is None:
            raise GDCMLicenseError("--source-root and --output-dir are required for collection")
        manifest = collect_gdcm_source_licenses(
            args.source_root.expanduser().resolve(),
            args.output_dir.expanduser().resolve(),
        )
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
