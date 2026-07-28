#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path


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


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def text(path: Path) -> str:
    require(path.is_file(), f"required file is missing: {path}")
    return path.read_text(encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def verify_wheel(wheel: Path) -> None:
    require(wheel.is_file(), f"wheel is missing: {wheel}")
    with zipfile.ZipFile(wheel) as archive:
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
        require(dylibs, f"wheel contains no native DICOM runtime libraries: {wheel}")
        for dylib in dylibs:
            required_license = native_license_for(Path(dylib).name)
            require(required_license is not None, f"unmapped native library in wheel: {dylib}")
            require(
                required_license in wheel_license_basenames,
                f"{dylib} is missing required packaged license {required_license}",
            )
        package_license_names = {
            Path(name).name: name
            for name in names
            if "/totalsegmentator_wrapper_mac/licenses/" in f"/{name}"
        }
        for required_name in {
            "TotalSegmentator-Apache-2.0.txt",
            "DentalSegmentator-NOTICE.txt",
            "ToothSeg-NOTICE.txt",
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


def verify_app(app: Path) -> None:
    resources = app / "Contents" / "Resources"
    require(resources.is_dir(), f"app Resources directory is missing: {resources}")
    verify_apache_license(resources / "LICENSE")
    verify_notice(resources / "NOTICE")

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
        *NATIVE_LICENSE_FILES.values(),
        "GDCM-IJG-JPEG-README.txt",
        "dcm2niix-license.txt",
    }:
        require((resources / "licenses" / required_license).is_file(), f"native license missing: {required_license}")
    dylibs = sorted((resources / "bin" / "lib").glob("*.dylib"))
    require(dylibs, "app contains no native DICOM runtime libraries")
    for dylib in dylibs:
        required_license = native_license_for(dylib.name)
        require(required_license is not None, f"unmapped native library in app: {dylib.name}")
    manifest = json.loads(text(resources / "setup_manifest.json"))
    require(manifest.get("license", {}).get("expression") == "Apache-2.0", "app manifest SPDX license missing")
    bundled = manifest.get("bundled", {})
    for key in (
        "wrapper_license",
        "wrapper_notice",
        "totalsegmentator_license",
        "totalsegmentator_task_inventory",
        "dentalsegmentator_notice",
        "toothseg_notice",
        "third_party_license_inventory",
    ):
        require(key in bundled, f"app manifest bundled.{key} missing")

    inventory_text = text(resources / "licenses" / "third_party_license_inventory.json")
    inventory = json.loads(inventory_text)
    require(inventory.get("unresolved_count") == 0, "license inventory contains unresolved items")
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

    wheels = sorted((resources / "wheels").glob("totalsegmentator_wrapper_mac-*.whl"))
    require(len(wheels) == 1, "app must contain exactly one wrapper wheel")
    verify_wheel(wheels[0])


def verify_dmg(dmg: Path) -> None:
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
            verify_apache_license(mount / "LICENSE.txt")
            verify_notice(mount / "NOTICE.txt")
            verify_app(mount / "TotalSegmentator Wrapper for Mac.app")
            readme = text(mount / "README.txt")
            require("Apache License 2.0" in readme, "DMG README does not identify wrapper Apache-2.0")
            require("DentalSegmentator-NOTICE.txt" in readme, "DMG README omits DentalSegmentator notice")
            require("ToothSeg-NOTICE.txt" in readme, "DMG README omits ToothSeg notice")
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
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--dmg", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require(any((args.wheel, args.app, args.dmg)), "at least one of --wheel, --app, or --dmg is required")
    if args.wheel:
        verify_wheel(args.wheel.expanduser().resolve())
    if args.app:
        verify_app(args.app.expanduser().resolve())
    if args.dmg:
        verify_dmg(args.dmg.expanduser().resolve())
    print("License distribution verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
