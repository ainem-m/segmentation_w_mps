#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from totalsegmentator_wrapper_mac.disclaimers import NON_CLINICAL_NOTICE_EN


APP_NAME = "TotalSegmentator Wrapper for Mac"
APP_OBJECT_PREFIX = "totalsegmentator-wrapper-mac"
UPDATE_SCHEMA = "totalsegmentator_wrapper_mac.update_manifest.v1"
RELEASE_SCHEMA = "totalsegmentator_wrapper_mac.cloudflare_release.v1"


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    version = args.version
    if args.channel == "stable" and not args.minimum_supported_version:
        raise SystemExit("--minimum-supported-version is required for stable releases")
    if args.channel == "stable" and not args.notarized:
        raise SystemExit("stable releases require a notarized DMG")
    dmg = args.dmg
    if dmg is None:
        dmg = repo_root / "dist" / f"{APP_NAME}-{version}-20260722-gdcm-toothseg-arm64.dmg"
    dmg = dmg.expanduser()
    if not dmg.is_file():
        raise SystemExit(f"DMG not found: {dmg}")
    if args.channel == "stable":
        verify_stable_notarized_dmg(dmg)

    download_origin = normalize_https_origin(args.download_origin)
    object_prefix = normalize_object_prefix(args.object_prefix)
    published_at = args.published_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    minimum_supported = args.minimum_supported_version or default_minimum_supported(version, args.channel)
    sha256 = sha256_file(dmg)
    size = dmg.stat().st_size
    file_name = dmg.name

    release_key = object_key(object_prefix, f"releases/{version}/{file_name}")
    notes_key = object_key(object_prefix, f"releases/{version}/RELEASE_NOTES.txt")
    checksum_key = object_key(object_prefix, f"releases/{version}/SHA256SUMS.txt")
    release_json_key = object_key(object_prefix, f"releases/{version}/release.json")
    update_key = object_key(object_prefix, f"releases/{args.channel}/update.json")

    r2_root = (repo_root / args.r2_root).resolve()
    release_dir = r2_root / "releases" / version
    update_dir = r2_root / "releases" / args.channel
    release_dir.mkdir(parents=True, exist_ok=True)
    update_dir.mkdir(parents=True, exist_ok=True)

    download_url = object_url(download_origin, release_key)
    release_notes_url = object_url(download_origin, notes_key)
    update_manifest_url = object_url(download_origin, update_key)

    write_text(
        release_dir / "SHA256SUMS.txt",
        f"{sha256}  {file_name}\n",
    )
    write_text(
        release_dir / "RELEASE_NOTES.txt",
        release_notes(version, notarized=args.notarized),
    )
    write_json(
        release_dir / "release.json",
        {
            "schema": RELEASE_SCHEMA,
            "app_name": APP_NAME,
            "channel": args.channel,
            "version": version,
            "file_name": file_name,
            "file_size_bytes": size,
            "sha256": sha256,
            "download_url": download_url,
            "update_manifest_url": update_manifest_url,
            "release_notes_url": release_notes_url,
            "published_at": published_at,
            "notarized": args.notarized,
            "clinical_use": False,
        },
    )
    write_json(
        update_dir / "update.json",
        {
            "schema": UPDATE_SCHEMA,
            "channel": args.channel,
            "latest_version": version,
            "minimum_supported_version": minimum_supported,
            "download_url": download_url,
            "release_notes_url": release_notes_url,
            "sha256": sha256,
            "published_at": published_at,
        },
    )
    write_json(
        r2_root / "upload-plan.json",
        {
            "schema": "totalsegmentator_wrapper_mac.cloudflare_upload_plan.v1",
            "bucket": args.bucket,
            "download_origin": download_origin,
            "object_prefix": object_prefix,
            "objects": [
                upload_object(dmg, release_key, "application/x-apple-diskimage", repo_root=repo_root, immutable=True),
                upload_object(
                    release_dir / "SHA256SUMS.txt",
                    checksum_key,
                    "text/plain; charset=utf-8",
                    repo_root=repo_root,
                    immutable=True,
                ),
                upload_object(
                    release_dir / "RELEASE_NOTES.txt",
                    notes_key,
                    "text/plain; charset=utf-8",
                    repo_root=repo_root,
                    immutable=True,
                ),
                upload_object(
                    release_dir / "release.json",
                    release_json_key,
                    "application/json; charset=utf-8",
                    repo_root=repo_root,
                    immutable=True,
                ),
                upload_object(
                    update_dir / "update.json",
                    update_key,
                    "application/json; charset=utf-8",
                    repo_root=repo_root,
                    immutable=False,
                ),
            ],
        },
    )
    print(f"Wrote Cloudflare release metadata under {r2_root}")
    print(f"Update manifest: {update_manifest_url}")
    print(f"DMG SHA256: {sha256}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare Cloudflare R2 release metadata.")
    parser.add_argument("--version", default="0.2.1")
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--minimum-supported-version", default=None)
    parser.add_argument("--dmg", type=Path, default=None)
    parser.add_argument(
        "--download-origin",
        default=os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_DOWNLOAD_ORIGIN", "https://downloads.lacramy.com"),
        help="HTTPS origin for the R2 custom domain, for example https://downloads.lacramy.com",
    )
    parser.add_argument(
        "--object-prefix",
        default=os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_DOWNLOAD_PREFIX", APP_OBJECT_PREFIX),
        help="R2 key prefix under the shared downloads domain.",
    )
    parser.add_argument("--published-at", default=None)
    parser.add_argument("--r2-root", type=Path, default=Path("cloudflare/r2"))
    parser.add_argument("--bucket", default="lacramy-downloads")
    parser.add_argument("--notarized", action=argparse.BooleanOptionalAction, default=False)
    return parser.parse_args()


def verify_stable_notarized_dmg(dmg: Path) -> None:
    checks = (
        ("codesign", "--verify", "--verbose=2", str(dmg)),
        ("xcrun", "stapler", "validate", str(dmg)),
        (
            "spctl",
            "--assess",
            "--type",
            "open",
            "--context",
            "context:primary-signature",
            "--verbose=4",
            str(dmg),
        ),
    )
    for command in checks:
        try:
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise SystemExit(
                f"stable DMG failed notarization verification ({command[0]}): {detail.strip()}"
            ) from exc


def normalize_https_origin(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit("--download-origin must be an HTTPS origin")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise SystemExit("--download-origin must not include a path, query, or fragment")
    return f"https://{parsed.netloc}"


def object_url(origin: str, key: str) -> str:
    return f"{origin}/{quote(key, safe='/')}"


def normalize_object_prefix(value: str) -> str:
    prefix = value.strip().strip("/")
    if not prefix:
        return ""
    parts = prefix.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SystemExit("--object-prefix must be a simple slash-separated object key prefix")
    return prefix


def object_key(prefix: str, suffix: str) -> str:
    if prefix:
        return f"{prefix}/{suffix}"
    return suffix


def default_minimum_supported(version: str, channel: str) -> str:
    return version


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def upload_object(source: Path, key: str, content_type: str, *, repo_root: Path, immutable: bool) -> dict:
    cache_control = "public, max-age=31536000, immutable" if immutable else "no-cache"
    return {
        "source": display_path(source, repo_root),
        "key": key,
        "content_type": content_type,
        "cache_control": cache_control,
    }


def display_path(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(repo_root))
    except ValueError:
        return str(resolved)


def release_notes(version: str, *, notarized: bool) -> str:
    distribution_status = (
        "- Developer ID signed and Apple notarized DMG for Apple Silicon Macs."
        if notarized
        else "- Candidate metadata only: this DMG has not been verified as Developer ID signed and Apple notarized, and must not be published as the stable build."
    )
    return f"""TotalSegmentator Wrapper for Mac {version} alpha

{distribution_status}
- Bundled Python 3.12 runtime for first-run setup without sudo or Homebrew.
- Bundled dcm2niix and GDCM 3.2.7 DICOM normalizer for local CT intake.
- Native JPEG, JPEG-LS, JPEG 2000, and RLE DICOM decoding with lossless transcoding before dcm2niix conversion.
- Invalid compressed DICOM data now fails explicitly instead of silently falling back; Enhanced CT remains blocked until per-frame geometry can be verified.
- Bundled Sample 1 non-clinical preview data and offline 3D preview HTML.
- First setup now prepares the craniofacial, robust crop, and teeth model weights.
- Craniofacial app previews now use the robust crop path by default for local CBCT inputs.
- High-resolution ToothSeg refinement can be explicitly prepared and run after a successful TotalSegmentator result with detected teeth.
- The first ToothSeg preparation downloads approximately 920 MB; it never starts refinement automatically after the download.
- Model-specific stages show measured progress when available and an honest indeterminate range otherwise.
- Browser-ready 3D previews are produced before detailed STL export finishes.
- Binary STL serialization is chunked and vectorized while preserving mesh geometry and float-precision normals.
- Large browser mesh payloads are parsed as JSON to avoid Safari JavaScript parser stack overflow.
- On the tested M1 Mac with 16 GB unified memory, the bundled 12 mm ROI sample took 34 minutes 27 seconds; other scans may take longer or exceed available memory.
- Update checks run only after the user presses the update button.

Non-clinical limitation:
{NON_CLINICAL_NOTICE_EN}

Data and update endpoint:
DICOM, CT, generated outputs, local paths, logs, and user identifiers are not
sent to the update endpoint.
"""


if __name__ == "__main__":
    raise SystemExit(main())
