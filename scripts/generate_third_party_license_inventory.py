#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "totalsegmentator_wrapper_mac.third_party_license_inventory.v1"
OVERRIDE_SCHEMA = "totalsegmentator_wrapper_mac.manual_license_overrides.v1"
LICENSE_NAME_RE = re.compile(r"(license|licence|copying|notice|copyright)", re.IGNORECASE)
UNKNOWN_VALUES = {"", "unknown", "none", "n/a", "not specified"}
ATTENTION_RE = re.compile(
    r"\b(AGPL|GPL|LGPL|SSPL)\b|NON[- ]?COMMERCIAL|CC[- ]BY[- ]NC|PROPRIETARY",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ManualOverride:
    package: str
    version: str
    license: str
    source_url: str
    reviewed_at: str
    decision: str
    reason: str
    license_file: str | None = None

    @property
    def accepted(self) -> bool:
        return self.decision == "accepted"


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "license"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manual_overrides(path: Path) -> dict[tuple[str, str], ManualOverride]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != OVERRIDE_SCHEMA:
        raise ValueError(f"unexpected manual override schema in {path}")
    overrides: dict[tuple[str, str], ManualOverride] = {}
    for item in payload.get("overrides", []):
        override = ManualOverride(
            package=item["package"],
            version=item.get("version", "*"),
            license=item["license"],
            source_url=item["source_url"],
            reviewed_at=item["reviewed_at"],
            decision=item["decision"],
            reason=item["reason"],
            license_file=item.get("license_file"),
        )
        if override.decision not in {"accepted", "rejected"}:
            raise ValueError(f"manual override for {override.package} has unsupported decision")
        key = (normalize_name(override.package), override.version)
        overrides[key] = override
    return overrides


def find_override(
    overrides: dict[tuple[str, str], ManualOverride],
    package: str,
    version: str,
) -> ManualOverride | None:
    normalized = normalize_name(package)
    return overrides.get((normalized, version)) or overrides.get((normalized, "*"))


def dist_info_path(dist: metadata.Distribution) -> Path | None:
    raw_path = getattr(dist, "_path", None)
    if raw_path is None:
        return None
    return Path(raw_path)


def license_metadata(dist: metadata.Distribution) -> tuple[str, str, list[str]]:
    meta = dist.metadata
    classifiers = meta.get_all("Classifier") or []
    license_expression = meta.get("License-Expression", "").strip()
    if license_expression:
        return license_expression, "License-Expression", classifiers
    license_field = meta.get("License", "").strip()
    if license_field and license_field.lower() not in UNKNOWN_VALUES:
        return " ".join(license_field.split()), "License", classifiers
    license_classifiers = [c for c in classifiers if c.startswith("License ::")]
    if license_classifiers:
        return "; ".join(license_classifiers), "Classifier", classifiers
    return "", "missing", classifiers


def candidate_license_files(dist: metadata.Distribution) -> list[Path]:
    candidates: set[Path] = set()
    root = dist_info_path(dist)
    if root is not None and root.exists():
        for child in root.iterdir():
            if child.is_file() and LICENSE_NAME_RE.search(child.name):
                candidates.add(child)
    for package_file in dist.files or ():
        name = package_file.name
        if not LICENSE_NAME_RE.search(name):
            continue
        try:
            located = Path(dist.locate_file(package_file))
        except Exception:
            continue
        if located.is_file():
            candidates.add(located)
    return sorted(candidates, key=lambda p: str(p).lower())


def copy_license_file(source: Path, destination_dir: Path, output_dir: Path, prefix: str = "") -> dict[str, Any]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination_name = safe_name(prefix + source.name)
    destination = destination_dir / destination_name
    counter = 2
    while destination.exists() and destination.resolve() != source.resolve():
        destination = destination_dir / f"{destination_name}.{counter}"
        counter += 1
    shutil.copy2(source, destination)
    return {
        "source": str(source),
        "path": str(destination.relative_to(output_dir)),
        "sha256": sha256_file(destination),
    }


def attention_found(text: str) -> bool:
    return bool(ATTENTION_RE.search(text))


def collect_package_inventory(
    *,
    output_dir: Path,
    site_paths: list[Path],
    manual_overrides_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    overrides = load_manual_overrides(manual_overrides_path)
    if site_paths:
        distributions = list(metadata.distributions(path=[str(p) for p in site_paths]))
    else:
        distributions = list(metadata.distributions())
    packages: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    packages_dir = output_dir / "python-packages"

    for dist in sorted(distributions, key=lambda d: (d.metadata.get("Name") or "").lower()):
        name = dist.metadata.get("Name") or "UNKNOWN"
        version = dist.version
        license_summary, license_source, classifiers = license_metadata(dist)
        override = find_override(overrides, name, version)
        package_dir = packages_dir / f"{normalize_name(name)}-{safe_name(version)}"
        copied_files = [
            copy_license_file(path, package_dir, output_dir)
            for path in candidate_license_files(dist)
        ]

        if override and override.license_file:
            manual_file = manual_overrides_path.parent / override.license_file
            if manual_file.exists():
                copied_files.append(copy_license_file(manual_file, package_dir, output_dir, prefix="manual-"))
            else:
                unresolved.append(
                    {
                        "package": name,
                        "version": version,
                        "code": "manual_license_file_missing",
                        "detail": str(manual_file),
                    }
                )

        effective_license = override.license if override and override.accepted else license_summary
        if not effective_license or effective_license.lower() in UNKNOWN_VALUES:
            unresolved.append(
                {
                    "package": name,
                    "version": version,
                    "code": "license_metadata_unknown",
                    "detail": "license metadata is missing or UNKNOWN",
                }
            )
        if not copied_files and not (override and override.accepted and override.license_file):
            unresolved.append(
                {
                    "package": name,
                    "version": version,
                    "code": "license_text_missing",
                    "detail": "no LICENSE/NOTICE/COPYING/COPYRIGHT file found in distribution metadata",
                }
            )
        attention_text = "\n".join([effective_license, *classifiers])
        if attention_found(attention_text) and not (override and override.accepted):
            unresolved.append(
                {
                    "package": name,
                    "version": version,
                    "code": "attention_license_requires_review",
                    "detail": effective_license,
                }
            )

        packages.append(
            {
                "name": name,
                "version": version,
                "license": effective_license,
                "license_source": "manual_override" if override and override.accepted else license_source,
                "classifiers": classifiers,
                "license_files": copied_files,
                "manual_override": (
                    {
                        "source_url": override.source_url,
                        "reviewed_at": override.reviewed_at,
                        "decision": override.decision,
                        "reason": override.reason,
                    }
                    if override
                    else None
                ),
            }
        )
    return packages, unresolved


def collect_runtime_licenses(output_dir: Path, runtime_root: Path | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if runtime_root is None:
        return [], []
    if not runtime_root.exists():
        return [], [{"code": "python_runtime_missing", "detail": str(runtime_root)}]
    runtime_dir = output_dir / "python-runtime"
    copied: list[dict[str, Any]] = []
    for path in sorted(runtime_root.rglob("*"), key=lambda p: str(p).lower()):
        if not path.is_file() or not LICENSE_NAME_RE.search(path.name):
            continue
        if "site-packages" in path.parts:
            continue
        relative_parent = path.relative_to(runtime_root).parent
        relative_prefix = "" if str(relative_parent) == "." else f"{safe_name(str(relative_parent)).replace('-', '_')}-"
        copied.append(copy_license_file(path, runtime_dir, output_dir, prefix=relative_prefix))
    if not copied:
        return [], [{"code": "python_runtime_license_missing", "detail": str(runtime_root)}]
    return copied, []


def write_summary(output_dir: Path, inventory: dict[str, Any]) -> None:
    lines = [
        "TotalSegmentator Wrapper for Mac third-party license summary",
        "",
        f"Generated at: {inventory['generated_at']}",
        f"Dependency set: {inventory['dependency_set_id']}",
        f"Unresolved items: {inventory['unresolved_count']}",
        "",
        "Python packages:",
    ]
    for package in inventory["packages"]:
        lines.append(f"- {package['name']} {package['version']}: {package['license'] or 'UNKNOWN'}")
    if inventory["python_runtime_license_files"]:
        lines.extend(["", "Python runtime license files:"])
        for item in inventory["python_runtime_license_files"]:
            lines.append(f"- {item['path']}")
    if inventory["unresolved"]:
        lines.extend(["", "Unresolved items:"])
        for item in inventory["unresolved"]:
            subject = item.get("package", "python_runtime")
            lines.append(f"- {subject}: {item['code']} ({item['detail']})")
    (output_dir / "THIRD_PARTY_LICENSES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dependency-set-id", default="")
    parser.add_argument("--site-path", type=Path, action="append", default=[])
    parser.add_argument("--python-runtime-root", type=Path, default=None)
    parser.add_argument(
        "--manual-overrides",
        type=Path,
        default=Path("resources/third_party/licenses/manual-overrides.json"),
    )
    parser.add_argument("--fail-on-unresolved", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if not args.site_path:
        raise SystemExit("--site-path is required; ambient Python packages are not release inventory input")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    packages, unresolved = collect_package_inventory(
        output_dir=output_dir,
        site_paths=args.site_path,
        manual_overrides_path=args.manual_overrides,
    )
    runtime_files, runtime_unresolved = collect_runtime_licenses(output_dir, args.python_runtime_root)
    unresolved.extend(runtime_unresolved)

    inventory = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "dependency_set_id": args.dependency_set_id,
        "python_executable": sys.executable,
        "site_paths": [str(path) for path in args.site_path],
        "python_runtime_root": str(args.python_runtime_root) if args.python_runtime_root else None,
        "manual_overrides": str(args.manual_overrides),
        "packages": packages,
        "python_runtime_license_files": runtime_files,
        "unresolved": unresolved,
        "unresolved_count": len(unresolved),
    }
    (output_dir / "third_party_license_inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_summary(output_dir, inventory)
    print(json.dumps({"unresolved_count": len(unresolved), "output_dir": str(output_dir)}, sort_keys=True))
    if args.fail_on_unresolved and unresolved:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
