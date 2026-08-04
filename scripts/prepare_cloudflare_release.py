#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from totalsegmentator_wrapper_mac.disclaimers import NON_CLINICAL_NOTICE_EN


APP_NAME = "TotalSegmentator Wrapper for Mac"
APP_OBJECT_PREFIX = "totalsegmentator-wrapper-mac"
UPDATE_SCHEMA = "totalsegmentator_wrapper_mac.update_manifest.v1"
RELEASE_SCHEMA = "totalsegmentator_wrapper_mac.cloudflare_release.v1"
DMG_ARCHITECTURE = "arm64"
RELEASE_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){2}$")
PUBLISHED_AT_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
CANONICAL_DOWNLOAD_ORIGIN = "https://downloads.lacramy.com"
CANONICAL_OBJECT_PREFIX = APP_OBJECT_PREFIX
CANONICAL_BUCKET = "lacramy-downloads"
LEGACY_STABLE_CHANNEL = "stable"
ATOMIC_STABLE_CHANNEL = "stable-v2"
PRODUCTION_UPDATE_CHANNELS = {ATOMIC_STABLE_CHANNEL}
PAGES_PROMOTION_SCHEMA = "totalsegmentator_wrapper_mac.pages_promotion.v1"
PROMOTED_MINIMUM_MACOS_VERSION = "14.0"
PAGES_PUBLIC_HOST = "totalsegmentator.lacramy.com"
APP_HUB_PUBLIC_HOST = "app.lacramy.com"
PAGES_ASSET_PROVENANCE_SCHEMA = "totalsegmentator_wrapper_mac.asset_provenance.v2"
STABLE_V2_RELEASE_ELIGIBILITY_KEY = "stable_v2_release_eligibility"
IOS_DERIVATIVE_WEBP_PATH = "/assets/totalsegmentator-ios-tooth-segmentation.webp"
IOS_DERIVATIVE_WEBP_PUBLIC_DISPLAY_SCOPE = "single-named-derived-webp-public-display"
IOS_DERIVATIVE_WEBP_SHA256 = (
    "f63b7d9ecff780a35351cac023facc1b183abf9ed74b3c8c8800299be85400a2"
)
IOS_DERIVATIVE_WEBP_APPROVAL_DECISION_ID = (
    "owner-explicit-public-display-consent-2026-08-03"
)
IOS_DERIVATIVE_WEBP_APPROVAL_RECORDED_AT = "2026-08-02T21:08:23Z"
IOS_DERIVATIVE_WEBP_APPROVAL_SUBJECT_ATTESTATION = (
    "creator-self-scan-of-own-oral-cavity"
)
IOS_DERIVATIVE_WEBP_APPROVAL_RECORD = (
    "../../../docs/43_OPEN_SOURCE_PUBLICATION_DECISIONS.md#"
    "owner-explicit-public-display-consent-2026-08-03"
)
IOS_UPPER_PLY_FILENAME = "ios_upper.ply"
IOS_UPPER_PLY_EXCLUDED_FROM = (
    "git-repository",
    "app-bundle",
    "DMG",
    "R2",
    "Pages",
)
TGNET_CHECKPOINT_FILENAMES = frozenset(
    {
        "tgnet_fps.h5",
        "tgnet_bdl.h5",
        "ckpts(new).zip",
    }
)
TGNET_CHECKPOINT_SUFFIXES = frozenset(
    {".bin", ".h5", ".pt", ".pth", ".ckpt", ".safetensors"}
)
PUBLIC_REFERENCE_SOURCE_SUFFIXES = frozenset(
    {".css", ".html", ".htm", ".js", ".json", ".svg", ".webmanifest"}
)
PAGES_NON_ASSET_CONTROL_FILES = frozenset(
    {
        "index.html",
        "launch2.html",
        "_headers",
        "_redirects",
        "assets/ASSET_PROVENANCE.json",
        "preview/PROVENANCE.json",
    }
)
APP_HUB_NON_ASSET_CONTROL_FILES = frozenset(
    {"index.html", "_headers", "_redirects"}
)
QUOTED_PUBLIC_REFERENCE_PATTERN = re.compile(r'''"([^"\r\n]+)"|'([^'\r\n]+)' ''', re.VERBOSE)
STATIC_TEMPLATE_LITERAL_REFERENCE_PATTERN = re.compile(r"`([^`\r\n]+)`")
CSS_URL_REFERENCE_PATTERN = re.compile(
    r"""url\(\s*(?:[\"']([^\"']+)[\"']|([^\s)]+))\s*\)""",
    re.IGNORECASE,
)
HTML_SRCSET_REFERENCE_PATTERN = re.compile(
    r"""\bsrcset\s*=\s*(?:[\"]([^\"]*)[\"]|'([^']*)'|([^\s>]+))""",
    re.IGNORECASE,
)


def main(
    argv: list[str] | None = None,
    *,
    artifact_verifier=None,
    source_verifier=None,
) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    if args.promoted_pages_output is not None:
        _validate_promoted_output_path(repo_root, args.promoted_pages_output)
    source_version = canonical_project_version(repo_root)
    if args.version is not None and args.version != source_version:
        raise SystemExit(
            f"--version {args.version} does not match pyproject version {source_version}"
        )
    version = source_version
    release_id = normalize_release_id(args.release_id or version)
    if args.channel == LEGACY_STABLE_CHANNEL:
        raise SystemExit(
            "legacy stable channel is permanently frozen and read-only; use stable-v2 for a new verified release"
        )
    production_channel = args.channel in PRODUCTION_UPDATE_CHANNELS
    if args.promoted_pages_output is not None and not production_channel:
        raise SystemExit(
            "--promoted-pages-output is allowed only for a verified stable-v2 release"
        )
    if production_channel and release_id != version:
        raise SystemExit("stable-v2 release-id must exactly match the source version")
    if not production_channel and release_id == version:
        raise SystemExit(
            "non-production releases require an explicit distinct --release-id so they "
            "cannot occupy the immutable stable-v2 version path"
        )
    if production_channel and not args.minimum_supported_version:
        raise SystemExit("--minimum-supported-version is required for stable-v2 releases")
    if production_channel and not args.notarized:
        raise SystemExit("stable-v2 releases require a notarized DMG")
    download_origin = normalize_https_origin(args.download_origin)
    object_prefix = normalize_object_prefix(args.object_prefix)
    published_at_override = normalize_published_at(args.published_at)
    if production_channel and (
        download_origin != CANONICAL_DOWNLOAD_ORIGIN
        or object_prefix != CANONICAL_OBJECT_PREFIX
        or args.bucket != CANONICAL_BUCKET
    ):
        raise SystemExit(
            "stable-v2 releases require the canonical Cloudflare origin, object prefix, "
            "and bucket"
        )
    dmg = args.dmg
    if dmg is None:
        dmg = discover_default_dmg(repo_root, version)
    dmg = dmg.expanduser()
    if not dmg.is_file():
        raise SystemExit(f"DMG not found: {dmg}")
    validate_dmg_filename(dmg, version)
    if production_channel:
        source_commit = (source_verifier or verified_clean_source_commit)(repo_root)
        source_tree_dirty = False
        validate_source_provenance(source_commit, source_tree_dirty)
        verify_production_notarized_dmg(dmg, version, source_commit)
        validate_public_asset_provenance(
            pages_root=repo_root / "cloudflare" / "pages",
            app_hub_root=repo_root / "cloudflare" / "app-hub",
        )
        validate_public_asset_release_eligibility(
            pages_root=repo_root / "cloudflare" / "pages",
            app_hub_root=repo_root / "cloudflare" / "app-hub",
        )
    else:
        source_commit, source_tree_dirty = inspect_source_provenance(repo_root)
        verifier = artifact_verifier or verify_distribution_dmg
        verifier(dmg, version)

    minimum_supported = args.minimum_supported_version or default_minimum_supported(version, args.channel)
    if _version_tuple(minimum_supported) > _version_tuple(version):
        raise SystemExit(
            "minimum supported version cannot be newer than the release version"
        )
    sha256 = sha256_file(dmg)
    size = dmg.stat().st_size
    file_name = dmg.name

    release_key = object_key(object_prefix, f"releases/{release_id}/{file_name}")
    notes_key = object_key(object_prefix, f"releases/{release_id}/RELEASE_NOTES.txt")
    checksum_key = object_key(object_prefix, f"releases/{release_id}/SHA256SUMS.txt")
    release_json_key = object_key(object_prefix, f"releases/{release_id}/release.json")
    update_key = object_key(object_prefix, f"releases/{args.channel}/update.json")

    r2_root = (repo_root / args.r2_root).resolve()
    release_dir = r2_root / "releases" / release_id
    update_dir = r2_root / "releases" / args.channel
    download_url = object_url(download_origin, release_key)
    release_notes_url = object_url(download_origin, notes_key)
    update_manifest_url = object_url(download_origin, update_key)

    published_at = guard_existing_release(
        release_dir,
        version=version,
        release_id=release_id,
        file_name=file_name,
        file_size_bytes=size,
        sha256=sha256,
        channel=args.channel,
        notarized=args.notarized,
        published_at=published_at_override,
    )
    if published_at is None:
        published_at = (
            datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    checksum_text = f"{sha256}  {file_name}\n"
    notes_text = release_notes(
        version,
        release_id=release_id,
        channel=args.channel,
        notarized=args.notarized,
    )
    release_payload = {
        "schema": RELEASE_SCHEMA,
        "app_name": APP_NAME,
        "channel": args.channel,
        "version": version,
        "release_id": release_id,
        "file_name": file_name,
        "file_size_bytes": size,
        "sha256": sha256,
        "download_url": download_url,
        "update_manifest_url": update_manifest_url,
        "release_notes_url": release_notes_url,
        "published_at": published_at,
        "notarized": args.notarized,
        "source_commit": source_commit,
        "source_tree_dirty": source_tree_dirty,
        "clinical_use": False,
    }
    update_payload = {
        "schema": UPDATE_SCHEMA,
        "channel": args.channel,
        "latest_version": version,
        "minimum_supported_version": minimum_supported,
        "download_url": download_url,
        "release_notes_url": release_notes_url,
        "file_size_bytes": size,
        "sha256": sha256,
        "published_at": published_at,
        "source_commit": source_commit,
        "source_tree_dirty": source_tree_dirty,
    }
    guard_immutable_release_files(
        release_dir,
        checksum_text=checksum_text,
        notes_text=notes_text,
        release_payload=release_payload,
    )
    if production_channel:
        production_path = update_dir / "update.json"
        if production_path.is_file():
            existing_production = json.loads(production_path.read_text(encoding="utf-8"))
            same_release = guard_production_update(
                existing_production,
                version,
                sha256,
                file_size_bytes=size,
            )
            if same_release and existing_production != update_payload:
                raise SystemExit(
                    "stable-v2 channel already contains this version and SHA-256 "
                    "with different metadata"
                )

    write_text(release_dir / "SHA256SUMS.txt", checksum_text)
    write_text(release_dir / "RELEASE_NOTES.txt", notes_text)
    write_json(release_dir / "release.json", release_payload)
    write_json(update_dir / "update.json", update_payload)
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
    if args.promoted_pages_output is not None:
        materialize_promoted_pages(
            repo_root=repo_root,
            output_root=args.promoted_pages_output,
            release_json=release_dir / "release.json",
            update_json=update_dir / "update.json",
            dmg=dmg,
        )
    print(f"Wrote Cloudflare release metadata under {r2_root}")
    print(f"Update manifest: {update_manifest_url}")
    print(f"DMG SHA256: {sha256}")
    if args.promoted_pages_output is not None:
        print(f"Wrote promoted Pages staging under {args.promoted_pages_output}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare Cloudflare R2 release metadata.")
    parser.add_argument(
        "--version",
        default=None,
        help="Must match [project].version in pyproject.toml; defaults to that canonical value.",
    )
    parser.add_argument(
        "--release-id",
        default=None,
        help="Immutable release object path identifier; defaults to --version.",
    )
    parser.add_argument(
        "--channel",
        choices=(LEGACY_STABLE_CHANNEL, ATOMIC_STABLE_CHANNEL, "candidate", "alpha"),
        default=ATOMIC_STABLE_CHANNEL,
    )
    parser.add_argument("--minimum-supported-version", default=None)
    parser.add_argument("--dmg", type=Path, default=None)
    parser.add_argument(
        "--download-origin",
        default=os.environ.get(
            "TOTALSEGMENTATOR_WRAPPER_MAC_DOWNLOAD_ORIGIN",
            CANONICAL_DOWNLOAD_ORIGIN,
        ),
        help="HTTPS origin for the R2 custom domain, for example https://downloads.lacramy.com",
    )
    parser.add_argument(
        "--object-prefix",
        default=os.environ.get(
            "TOTALSEGMENTATOR_WRAPPER_MAC_DOWNLOAD_PREFIX",
            CANONICAL_OBJECT_PREFIX,
        ),
        help="R2 key prefix under the shared downloads domain.",
    )
    parser.add_argument("--published-at", default=None)
    parser.add_argument("--r2-root", type=Path, default=Path("cloudflare/r2"))
    parser.add_argument(
        "--promoted-pages-output",
        type=Path,
        default=None,
        help=(
            "Materialize stable-v2 Pages and app-hub deploy directories from the "
            "verified release metadata into a fresh directory outside the repository."
        ),
    )
    parser.add_argument("--bucket", default=CANONICAL_BUCKET)
    parser.add_argument("--notarized", action=argparse.BooleanOptionalAction, default=False)
    return parser.parse_args(argv)


def canonical_project_version(repo_root: Path) -> str:
    pyproject_path = repo_root / "pyproject.toml"
    try:
        document = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        project = document["project"]
        name = project["name"]
        version = project["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit(f"could not read canonical project version: {exc}") from exc
    if name != "totalsegmentator-wrapper-mac":
        raise SystemExit(f"unexpected project identity in pyproject.toml: {name}")
    if not isinstance(version, str) or RELEASE_VERSION_PATTERN.fullmatch(version) is None:
        raise SystemExit(f"invalid canonical project version: {version!r}")
    return version


def discover_default_dmg(repo_root: Path, version: str) -> Path:
    candidates = sorted(
        (repo_root / "dist").glob(
            f"{APP_NAME}-{version}-*-{DMG_ARCHITECTURE}.dmg"
        )
    )
    if not candidates:
        raise SystemExit(
            "no canonical-version DMG was found; pass --dmg with the exact candidate path"
        )
    if len(candidates) != 1:
        names = ", ".join(path.name for path in candidates)
        raise SystemExit(
            "multiple canonical-version DMGs were found; pass --dmg explicitly: " + names
        )
    return candidates[0]


def validate_dmg_filename(dmg: Path, version: str) -> None:
    pattern = re.compile(
        rf"^{re.escape(APP_NAME)}-{re.escape(version)}"
        rf"(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?-{DMG_ARCHITECTURE}\.dmg$"
    )
    if pattern.fullmatch(dmg.name) is None:
        raise SystemExit(
            "DMG filename does not match the canonical source version and architecture: "
            f"{dmg.name}"
        )


def _version_tuple(version: str) -> tuple[int, ...]:
    if RELEASE_VERSION_PATTERN.fullmatch(version) is None:
        raise SystemExit(f"release channel contains an invalid version: {version!r}")
    return tuple(int(part) for part in version.split("."))


def guard_existing_release(
    release_dir: Path,
    *,
    version: str,
    release_id: str,
    file_name: str,
    file_size_bytes: int,
    sha256: str,
    channel: str,
    notarized: bool,
    published_at: str | None,
) -> str | None:
    if not release_dir.exists() or not any(release_dir.iterdir()):
        return published_at
    release_path = release_dir / "release.json"
    if not release_path.is_file():
        raise SystemExit(
            f"immutable release collision: existing directory has no release.json: {release_dir}"
        )
    try:
        existing = json.loads(release_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"immutable release collision: invalid existing release.json: {exc}") from exc
    expected_identity = {
        "version": version,
        "release_id": release_id,
        "file_name": file_name,
        "file_size_bytes": file_size_bytes,
        "sha256": sha256,
        "channel": channel,
        "notarized": notarized,
    }
    mismatches = [
        key
        for key, expected in expected_identity.items()
        if existing.get(key) != expected
    ]
    if mismatches:
        raise SystemExit(
            "immutable release collision: existing release differs in "
            + ", ".join(mismatches)
        )
    existing_published_at = existing.get("published_at")
    if not isinstance(existing_published_at, str) or not existing_published_at:
        raise SystemExit("immutable release collision: existing published_at is invalid")
    try:
        normalize_published_at(existing_published_at)
    except SystemExit as exc:
        raise SystemExit(
            "immutable release collision: existing published_at is invalid"
        ) from exc
    if published_at is not None and published_at != existing_published_at:
        raise SystemExit(
            "immutable release collision: existing release has a different published_at"
        )
    return existing_published_at


def guard_immutable_release_files(
    release_dir: Path,
    *,
    checksum_text: str,
    notes_text: str,
    release_payload: dict,
) -> None:
    if not release_dir.exists() or not any(release_dir.iterdir()):
        return
    expected_text = {
        "SHA256SUMS.txt": checksum_text,
        "RELEASE_NOTES.txt": notes_text,
        "release.json": json.dumps(
            release_payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    }
    for name, expected in expected_text.items():
        path = release_dir / name
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            raise SystemExit(
                f"immutable release collision: existing {name} differs from this candidate"
            )


def guard_production_update(
    existing: dict,
    version: str,
    sha256: str,
    *,
    file_size_bytes: int | None = None,
) -> bool:
    existing_version = existing.get("latest_version")
    if not isinstance(existing_version, str):
        raise SystemExit("stable-v2 channel existing latest_version is missing")
    comparison = (_version_tuple(version) > _version_tuple(existing_version)) - (
        _version_tuple(version) < _version_tuple(existing_version)
    )
    if comparison < 0:
        raise SystemExit(
            f"stable-v2 channel downgrade is forbidden: {existing_version} -> {version}"
        )
    if comparison > 0:
        return False
    if existing.get("sha256") != sha256:
        raise SystemExit(
            "stable-v2 channel already contains this version with a different SHA-256"
        )
    if (
        file_size_bytes is not None
        and existing.get("file_size_bytes") != file_size_bytes
    ):
        raise SystemExit(
            "stable-v2 channel already contains this version with a different file_size_bytes"
        )
    return True


def verify_distribution_dmg(
    dmg: Path,
    expected_version: str,
    expected_source_commit: str | None = None,
) -> None:
    license_verifier = Path(__file__).with_name("verify_license_distribution.py")
    command = [
        sys.executable,
        str(license_verifier),
        "--dmg",
        str(dmg),
        "--expected-version",
        expected_version,
    ]
    if expected_source_commit is not None:
        command.extend(["--expected-source-commit", expected_source_commit])
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
        raise SystemExit(f"DMG failed distribution verification: {detail.strip()}") from exc


def verify_production_notarized_dmg(
    dmg: Path,
    expected_version: str,
    expected_source_commit: str,
) -> None:
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
                f"stable-v2 DMG failed notarization verification ({command[0]}): {detail.strip()}"
            ) from exc
    verify_distribution_dmg(dmg, expected_version, expected_source_commit)


def inspect_source_provenance(repo_root: Path) -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise SystemExit(f"could not inspect source provenance: {detail.strip()}") from exc
    validate_source_provenance(commit, bool(status))
    return commit, bool(status)


def validate_source_provenance(source_commit: str, source_tree_dirty: bool) -> None:
    if re.fullmatch(r"[0-9a-f]{40,64}", source_commit) is None:
        raise SystemExit("source HEAD is not a valid Git commit")
    if not isinstance(source_tree_dirty, bool):
        raise SystemExit("source_tree_dirty must be a JSON boolean")


def verified_clean_source_commit(repo_root: Path) -> str:
    commit, source_tree_dirty = inspect_source_provenance(repo_root)
    if source_tree_dirty:
        raise SystemExit(
            "stable-v2 release preparation requires a clean tracked and untracked source worktree"
        )
    return commit


def normalize_https_origin(value: str) -> str:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SystemExit("--download-origin must be an HTTPS origin") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise SystemExit("--download-origin must be an HTTPS origin")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise SystemExit("--download-origin must not include a path, query, or fragment")
    host = parsed.hostname.rstrip(".").lower()
    if not host:
        raise SystemExit("--download-origin must be an HTTPS origin")
    return f"https://{host}"


def normalize_published_at(value: str | None) -> str | None:
    if value is None:
        return None
    if PUBLISHED_AT_PATTERN.fullmatch(value) is None:
        raise SystemExit("--published-at must be UTC RFC 3339 seconds ending in Z")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise SystemExit("--published-at is not a valid UTC date/time") from exc
    return value


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


def normalize_release_id(value: str) -> str:
    release_id = value.strip()
    if (
        not release_id
        or release_id in (".", "..")
        or any(
            char
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
            for char in release_id
        )
    ):
        raise SystemExit(
            "--release-id must contain only ASCII letters, digits, dots, and hyphens"
        )
    return release_id


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


def validate_public_asset_provenance(*, pages_root: Path, app_hub_root: Path) -> None:
    """Fail closed when a local deployable Pages asset lacks provenance.

    The pages site has two explicit ledgers: the launch-page asset ledger and
    the interactive-preview ledger.  References are discovered from deployable
    HTML, CSS, JavaScript, and web manifests after they are copied to the
    promotion stage, so adding an image or other local static asset cannot
    bypass the ledger merely by using a new source format.
    """
    pages_root = pages_root.resolve()
    app_hub_root = app_hub_root.resolve()
    page_assets = _validate_pages_asset_provenance_inventory(pages_root)
    preview_assets = _validate_preview_asset_provenance_inventory(pages_root)
    _validate_app_hub_public_file_inventory(app_hub_root)

    for source_root in (pages_root, app_hub_root):
        for source_path in _iter_public_reference_sources(source_root):
            for value in _iter_local_reference_values(source_path):
                target = _resolve_local_public_reference(
                    value,
                    source_path=source_path,
                    source_root=source_root,
                    pages_root=pages_root,
                    app_hub_root=app_hub_root,
                )
                if target is None:
                    continue
                ledger_name, ledger_key = _public_asset_ledger_key(
                    target,
                    pages_root=pages_root,
                    app_hub_root=app_hub_root,
                )
                if ledger_name is None:
                    continue
                if ledger_name == "app-hub":
                    raise SystemExit(
                        "public asset provenance validation failed: local app-hub asset "
                        f"has no provenance ledger: {_display_public_path(target, app_hub_root)}"
                    )
                ledger = page_assets if ledger_name == "pages" else preview_assets
                if ledger_key not in ledger:
                    raise SystemExit(
                        "public asset provenance validation failed: local asset reference "
                        f"is missing from the {ledger_name} ledger: "
                        f"{_display_source_path(source_path, source_root)} -> {ledger_key}"
                    )


def validate_public_asset_release_eligibility(*, pages_root: Path, app_hub_root: Path) -> None:
    """Require explicit public-display approval for the sensitive stable-v2 asset.

    This is intentionally separate from hash, inventory, and reference
    validation.  The source tree can remain auditable while an asset awaits
    publication consent, but a stable-v2 release or promoted Pages stage must
    not be created until the required approval record is complete.
    """
    pages_root = pages_root.resolve()
    app_hub_root = app_hub_root.resolve()
    ledger_path = pages_root / "assets" / "ASSET_PROVENANCE.json"
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(
            "public asset release eligibility failed: Pages asset ledger is invalid: "
            f"{exc}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema") != PAGES_ASSET_PROVENANCE_SCHEMA:
        raise SystemExit(
            "public asset release eligibility failed: Pages asset ledger does not use "
            f"{PAGES_ASSET_PROVENANCE_SCHEMA}"
        )
    files = payload.get("files")
    if not isinstance(files, dict) or IOS_DERIVATIVE_WEBP_PATH not in files:
        raise SystemExit(
            "public asset release eligibility failed: required asset ledger entry is missing: "
            f"{IOS_DERIVATIVE_WEBP_PATH}"
        )
    webp_ledger = files[IOS_DERIVATIVE_WEBP_PATH]
    webp_path = (pages_root / IOS_DERIVATIVE_WEBP_PATH.lstrip("/")).resolve()
    if (
        not isinstance(webp_ledger, dict)
        or webp_ledger.get("sha256") != IOS_DERIVATIVE_WEBP_SHA256
        or not webp_path.is_relative_to(pages_root)
        or not webp_path.is_file()
        or sha256_file(webp_path) != IOS_DERIVATIVE_WEBP_SHA256
    ):
        raise SystemExit(
            "public asset release eligibility failed: approved WebP SHA-256 is invalid"
        )
    policy = payload.get(STABLE_V2_RELEASE_ELIGIBILITY_KEY)
    if not isinstance(policy, dict):
        raise SystemExit(
            "public asset release eligibility failed: stable-v2 release eligibility policy is missing"
        )
    required_assets = policy.get("required_assets")
    if not isinstance(required_assets, dict):
        raise SystemExit(
            "public asset release eligibility failed: stable-v2 required_assets policy is invalid"
        )
    ios_webp = required_assets.get(IOS_DERIVATIVE_WEBP_PATH)
    if not isinstance(ios_webp, dict):
        raise SystemExit(
            "public asset release eligibility failed: required public-display policy is missing for "
            f"{IOS_DERIVATIVE_WEBP_PATH}"
        )

    source_input = ios_webp.get("source_input")
    if not isinstance(source_input, dict):
        raise SystemExit(
            "public asset release eligibility failed: source-input exclusion policy is invalid for "
            f"{IOS_DERIVATIVE_WEBP_PATH}"
        )
    if (
        source_input.get("filename") != IOS_UPPER_PLY_FILENAME
        or source_input.get("distribution") != "excluded"
        or source_input.get("excluded_from") != list(IOS_UPPER_PLY_EXCLUDED_FROM)
    ):
        raise SystemExit(
            "public asset release eligibility failed: raw ios_upper.ply exclusion policy is invalid"
        )
    for public_root in (pages_root, app_hub_root):
        copied_raw_meshes = sorted(
            path.relative_to(public_root).as_posix()
            for path in public_root.rglob("*")
            if path.is_file() and path.suffix.lower() == ".ply"
        )
        if copied_raw_meshes:
            if IOS_UPPER_PLY_FILENAME in {
                Path(relative_path).name.lower() for relative_path in copied_raw_meshes
            }:
                detail = "raw ios_upper.ply"
            else:
                detail = "raw PLY"
            raise SystemExit(
                f"public asset release eligibility failed: {detail} is present in "
                f"the deployable {public_root.name} tree: {', '.join(copied_raw_meshes)}"
            )
        copied_tgnet_checkpoints = sorted(
            path.relative_to(public_root).as_posix()
            for path in public_root.rglob("*")
            if path.is_file() and _is_tgnet_checkpoint_payload(path)
        )
        if copied_tgnet_checkpoints:
            raise SystemExit(
                "public asset release eligibility failed: TGNet checkpoint payload is present in "
                f"the deployable {public_root.name} tree: "
                f"{', '.join(copied_tgnet_checkpoints)}"
            )

    status = ios_webp.get("public_display_status")
    if status != "approved":
        raise SystemExit(
            "public asset release eligibility failed: "
            f"{IOS_DERIVATIVE_WEBP_PATH} public display status is {status!r}; "
            "it is not explicitly approved for stable-v2"
        )
    if ios_webp.get("public_display_scope") != IOS_DERIVATIVE_WEBP_PUBLIC_DISPLAY_SCOPE:
        raise SystemExit(
            "public asset release eligibility failed: approved public-display scope is invalid for "
            f"{IOS_DERIVATIVE_WEBP_PATH}"
        )
    approval_evidence = ios_webp.get("approval_evidence")
    if not isinstance(approval_evidence, dict):
        raise SystemExit(
            "public asset release eligibility failed: approved public-display status requires "
            f"non-secret authorization evidence for {IOS_DERIVATIVE_WEBP_PATH}"
        )
    record_id = approval_evidence.get("record_id")
    recorded_at = approval_evidence.get("recorded_at")
    subject_attestation = approval_evidence.get("subject_attestation")
    decision_record = approval_evidence.get("decision_record")
    if record_id != IOS_DERIVATIVE_WEBP_APPROVAL_DECISION_ID:
        raise SystemExit(
            "public asset release eligibility failed: approved public-display "
            "decision identifier is invalid"
        )
    if recorded_at != IOS_DERIVATIVE_WEBP_APPROVAL_RECORDED_AT:
        raise SystemExit(
            "public asset release eligibility failed: approved public-display "
            "approval timestamp is invalid"
        )
    if subject_attestation != IOS_DERIVATIVE_WEBP_APPROVAL_SUBJECT_ATTESTATION:
        raise SystemExit(
            "public asset release eligibility failed: approved public-display "
            "approval subject attestation is invalid"
        )
    if decision_record != IOS_DERIVATIVE_WEBP_APPROVAL_RECORD:
        raise SystemExit(
            "public asset release eligibility failed: approved public-display "
            "decision record reference is invalid"
        )
    if not isinstance(recorded_at, str):
        raise SystemExit(
            "public asset release eligibility failed: approved public-display authorization "
            "evidence is incomplete"
        )
    try:
        normalize_published_at(recorded_at)
    except SystemExit as exc:
        raise SystemExit(
            "public asset release eligibility failed: approved public-display authorization "
            "evidence has an invalid recorded_at timestamp"
        ) from exc


def _is_tgnet_checkpoint_payload(path: Path) -> bool:
    """Reject the named TGNet set and any deployable model-weight payload."""

    name = path.name.lower()
    if name in TGNET_CHECKPOINT_FILENAMES or path.suffix.lower() in TGNET_CHECKPOINT_SUFFIXES:
        return True
    return False


def _validate_pages_asset_provenance_inventory(pages_root: Path) -> dict[str, dict]:
    ledger_path = pages_root / "assets" / "ASSET_PROVENANCE.json"
    entries = _read_public_asset_ledger(ledger_path, "Pages asset")
    expected: set[str] = set()
    for key, metadata in entries.items():
        normalized = _normalize_site_asset_key(key, label="Pages asset ledger key")
        target = (pages_root / normalized.lstrip("/")).resolve()
        if not target.is_relative_to(pages_root) or not _is_pages_main_asset(target, pages_root):
            raise SystemExit(
                "public asset provenance validation failed: Pages asset ledger key "
                f"is outside the supported public asset scope: {key}"
            )
        _validate_public_asset_metadata(
            metadata,
            target,
            label=f"Pages asset ledger entry {normalized}",
            require_scope=True,
        )
        expected.add(normalized)
    actual = _pages_main_asset_inventory(pages_root)
    _require_exact_asset_inventory("Pages asset", expected, actual)
    return entries


def _validate_preview_asset_provenance_inventory(pages_root: Path) -> dict[str, dict]:
    preview_root = pages_root / "preview"
    ledger_path = preview_root / "PROVENANCE.json"
    entries = _read_public_asset_ledger(ledger_path, "Pages preview")
    expected: set[str] = set()
    for key, metadata in entries.items():
        normalized = _normalize_preview_asset_key(key, label="Pages preview ledger key")
        target = (preview_root / normalized).resolve()
        if not target.is_relative_to(preview_root):
            raise SystemExit(
                "public asset provenance validation failed: Pages preview ledger key "
                f"escapes the preview directory: {key}"
            )
        _validate_public_asset_metadata(
            metadata,
            target,
            label=f"Pages preview ledger entry {normalized}",
            require_scope=False,
        )
        expected.add(normalized)
    actual = {
        path.relative_to(preview_root).as_posix()
        for path in preview_root.rglob("*")
        if path.is_file() and path.relative_to(preview_root).as_posix() != "PROVENANCE.json"
    }
    _require_exact_asset_inventory("Pages preview", expected, actual)
    return entries


def _read_public_asset_ledger(path: Path, label: str) -> dict[str, dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"public asset provenance validation failed: {label} ledger is invalid: {exc}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("apache_2_0_relicensed") is not False:
        raise SystemExit(
            f"public asset provenance validation failed: {label} ledger has an invalid license boundary"
        )
    entries = payload.get("files")
    if not isinstance(entries, dict) or not entries:
        raise SystemExit(
            f"public asset provenance validation failed: {label} ledger has no files mapping"
        )
    if any(not isinstance(key, str) or not isinstance(value, dict) for key, value in entries.items()):
        raise SystemExit(
            f"public asset provenance validation failed: {label} ledger files mapping is malformed"
        )
    return entries


def _normalize_site_asset_key(value: str, *, label: str) -> str:
    parsed = urlsplit(value)
    path = unquote(parsed.path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not path.startswith("/")
        or path.startswith("//")
    ):
        raise SystemExit(f"public asset provenance validation failed: {label} is not a site path: {value}")
    parts = path[1:].split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise SystemExit(f"public asset provenance validation failed: {label} is unsafe: {value}")
    return "/" + "/".join(parts)


def _normalize_preview_asset_key(value: str, *, label: str) -> str:
    parsed = urlsplit(value)
    path = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or path.startswith("/"):
        raise SystemExit(f"public asset provenance validation failed: {label} is not relative: {value}")
    parts = path.split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise SystemExit(f"public asset provenance validation failed: {label} is unsafe: {value}")
    return "/".join(parts)


def _validate_public_asset_metadata(
    metadata: dict,
    target: Path,
    *,
    label: str,
    require_scope: bool,
) -> None:
    expected_digest = metadata.get("sha256")
    if not isinstance(expected_digest, str) or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None:
        raise SystemExit(f"public asset provenance validation failed: {label} has an invalid SHA-256")
    if require_scope and (not isinstance(metadata.get("scope"), str) or not metadata["scope"].strip()):
        raise SystemExit(f"public asset provenance validation failed: {label} is missing scope")
    if not target.is_file():
        raise SystemExit(f"public asset provenance validation failed: {label} is missing: {target}")
    actual_digest = sha256_file(target)
    if actual_digest != expected_digest:
        raise SystemExit(
            f"public asset provenance validation failed: {label} SHA-256 differs from its ledger"
        )


def _is_pages_main_asset(path: Path, pages_root: Path) -> bool:
    relative = path.relative_to(pages_root)
    if not relative.parts or relative.parts[0] == "preview":
        return False
    return relative.as_posix() not in PAGES_NON_ASSET_CONTROL_FILES


def _pages_main_asset_inventory(pages_root: Path) -> set[str]:
    return {
        _display_public_path(path, pages_root)
        for path in pages_root.rglob("*")
        if path.is_file() and _is_pages_main_asset(path, pages_root)
    }


def _is_app_hub_control_file(path: Path, app_hub_root: Path) -> bool:
    return path.relative_to(app_hub_root).as_posix() in APP_HUB_NON_ASSET_CONTROL_FILES


def _validate_app_hub_public_file_inventory(app_hub_root: Path) -> None:
    assets = sorted(
        _display_public_path(path, app_hub_root)
        for path in app_hub_root.rglob("*")
        if path.is_file() and not _is_app_hub_control_file(path, app_hub_root)
    )
    if assets:
        raise SystemExit(
            "public asset provenance validation failed: app-hub deployable file inventory differs "
            "(no app-hub provenance ledger exists: "
            + ", ".join(assets)
            + ")"
        )


def _require_exact_asset_inventory(label: str, expected: set[str], actual: set[str]) -> None:
    missing = sorted(actual - expected)
    stale = sorted(expected - actual)
    if missing or stale:
        details: list[str] = []
        if missing:
            details.append("missing from ledger: " + ", ".join(missing))
        if stale:
            details.append("ledger entries without an asset: " + ", ".join(stale))
        raise SystemExit(
            "public asset provenance validation failed: " + label + " inventory differs (" + "; ".join(details) + ")"
        )


def _iter_public_reference_sources(site_root: Path):
    for path in site_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in PUBLIC_REFERENCE_SOURCE_SUFFIXES:
            continue
        relative = path.relative_to(site_root).as_posix()
        if relative in {"assets/ASSET_PROVENANCE.json", "preview/PROVENANCE.json"}:
            continue
        yield path


def _iter_local_reference_values(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SystemExit(
            f"public asset provenance validation failed: could not read reference source {path}: {exc}"
        ) from exc
    for match in QUOTED_PUBLIC_REFERENCE_PATTERN.finditer(text):
        value = match.group(1) or match.group(2)
        if value:
            yield value
    for match in STATIC_TEMPLATE_LITERAL_REFERENCE_PATTERN.finditer(text):
        value = match.group(1)
        if value and "${" not in value:
            yield value
    for match in CSS_URL_REFERENCE_PATTERN.finditer(text):
        value = match.group(1) or match.group(2)
        if value:
            yield value
    for match in HTML_SRCSET_REFERENCE_PATTERN.finditer(text):
        srcset = match.group(1) or match.group(2) or match.group(3)
        if not srcset:
            continue
        for candidate in srcset.split(","):
            url = candidate.strip().split(maxsplit=1)[0] if candidate.strip() else ""
            if url:
                yield url


def _resolve_local_public_reference(
    value: str,
    *,
    source_path: Path,
    source_root: Path,
    pages_root: Path,
    app_hub_root: Path,
) -> Path | None:
    value = value.strip().replace(r"\/", "/")
    # A quoted runtime payload can be megabytes long.  It cannot be a usable
    # local URL path, and resolving it through pathlib would make the release
    # gate needlessly quadratic on preview geometry data.
    if not value or len(value) > 4096 or value.startswith(("#", "?")):
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        host = (parsed.hostname or "").rstrip(".").lower()
        if host == PAGES_PUBLIC_HOST:
            target_root = pages_root
        elif host == APP_HUB_PUBLIC_HOST:
            target_root = app_hub_root
        else:
            return None
    else:
        target_root = source_root
    raw_path = unquote(parsed.path)
    if not raw_path:
        return None
    candidate = (
        target_root / raw_path.lstrip("/")
        if raw_path.startswith("/")
        else source_path.parent / raw_path
    )
    target = candidate.resolve()
    if not target.is_relative_to(target_root):
        if candidate.exists():
            raise SystemExit(
                "public asset provenance validation failed: local public reference escapes its site root: "
                f"{_display_source_path(source_path, source_root)} -> {value}"
            )
        return None
    if not target.is_file():
        return None
    return target


def _public_asset_ledger_key(
    target: Path,
    *,
    pages_root: Path,
    app_hub_root: Path,
) -> tuple[str | None, str | None]:
    if target.is_relative_to(pages_root):
        relative = target.relative_to(pages_root)
        if relative.parts and relative.parts[0] == "preview":
            return "preview", relative.relative_to("preview").as_posix()
        if _is_pages_main_asset(target, pages_root):
            return "pages", _display_public_path(target, pages_root)
        return None, None
    if target.is_relative_to(app_hub_root) and not _is_app_hub_control_file(target, app_hub_root):
        return "app-hub", _display_public_path(target, app_hub_root)
    return None, None


def _display_public_path(path: Path, site_root: Path) -> str:
    return "/" + path.relative_to(site_root).as_posix()


def _display_source_path(path: Path, site_root: Path) -> str:
    return path.relative_to(site_root).as_posix()


def materialize_promoted_pages(
    *,
    repo_root: Path,
    output_root: Path,
    release_json: Path,
    update_json: Path,
    dmg: Path,
) -> None:
    """Create deployable stable-v2 Pages trees without mutating pre-release templates."""
    repo_root = repo_root.resolve()
    output_root = _validate_promoted_output_path(repo_root, output_root)

    release_path = release_json.expanduser().resolve()
    update_path = update_json.expanduser().resolve()
    dmg_path = dmg.expanduser().resolve()
    release = _read_json_object(release_path, "release.json")
    update = _read_json_object(update_path, "update.json")
    identity = _validate_pages_promotion_identity(release, update, dmg_path)

    source_pages = repo_root / "cloudflare" / "pages"
    source_hub = repo_root / "cloudflare" / "app-hub"
    source_index = source_pages / "index.html"
    source_launch = source_pages / "launch2.html"
    for required in (source_index, source_launch, source_pages / "_redirects", source_hub / "index.html", source_hub / "_redirects"):
        if not required.is_file():
            raise SystemExit(f"Pages promotion template is missing: {required}")
    if source_index.read_bytes() != source_launch.read_bytes():
        raise SystemExit("Pages promotion templates index.html and launch2.html differ")

    staging_root = output_root.with_name(f".{output_root.name}.{os.getpid()}.tmp")
    if staging_root.exists():
        raise SystemExit(f"promoted Pages staging path already exists: {staging_root}")
    try:
        shutil.copytree(source_pages, staging_root / "pages")
        shutil.copytree(source_hub, staging_root / "app-hub")

        page = _promote_app_page(
            (staging_root / "pages" / "index.html").read_text(encoding="utf-8"),
            version=identity["version"],
        )
        write_text(staging_root / "pages" / "index.html", page)
        write_text(staging_root / "pages" / "launch2.html", page)
        hub = _promote_app_hub(
            (staging_root / "app-hub" / "index.html").read_text(encoding="utf-8"),
            version=identity["version"],
        )
        write_text(staging_root / "app-hub" / "index.html", hub)
        for directory in (staging_root / "pages", staging_root / "app-hub"):
            redirects_path = directory / "_redirects"
            redirects = _promote_redirects(
                redirects_path.read_text(encoding="utf-8"),
                download_url=identity["download_url"],
                release_notes_url=identity["release_notes_url"],
            )
            write_text(redirects_path, redirects)

        _validate_promoted_pages(staging_root, identity)
        receipt = {
            "schema": PAGES_PROMOTION_SCHEMA,
            "verification_scope": "local-release-update-dmg-identity-and-public-asset-provenance",
            "live_r2_verified_by_materializer": False,
            "public_asset_provenance_verified": True,
            "public_asset_release_eligibility_verified": True,
            "version": identity["version"],
            "minimum_macos_version": PROMOTED_MINIMUM_MACOS_VERSION,
            "dmg_file_name": identity["file_name"],
            "dmg_size_bytes": identity["file_size_bytes"],
            "dmg_sha256": identity["sha256"],
            "download_url": identity["download_url"],
            "release_notes_url": identity["release_notes_url"],
            "update_manifest_url": identity["update_manifest_url"],
            "published_at": identity["published_at"],
            "source_commit": identity["source_commit"],
            "release_json_sha256": sha256_file(release_path),
            "update_json_sha256": sha256_file(update_path),
        }
        write_json(staging_root / "PROMOTION_RECEIPT.json", receipt)
        staging_root.replace(output_root)
    except BaseException:
        if staging_root.exists():
            shutil.rmtree(staging_root)
        raise


def _read_json_object(path: Path, label: str) -> dict:
    if not path.is_file():
        raise SystemExit(f"Pages promotion {label} not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Pages promotion {label} is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Pages promotion {label} must contain a JSON object")
    return payload


def _validate_promoted_output_path(repo_root: Path, output_root: Path) -> Path:
    repo_root = repo_root.resolve()
    resolved = output_root.expanduser().resolve()
    if resolved == repo_root or resolved.is_relative_to(repo_root):
        raise SystemExit(
            "promoted Pages output must be outside the repository so tracked pre-release templates remain unchanged"
        )
    if resolved.exists():
        raise SystemExit(f"promoted Pages output already exists: {resolved}")
    if not resolved.parent.is_dir():
        raise SystemExit(f"promoted Pages output parent directory does not exist: {resolved.parent}")
    return resolved


def _validate_pages_promotion_identity(release: dict, update: dict, dmg: Path) -> dict:
    if release.get("schema") != RELEASE_SCHEMA:
        raise SystemExit("Pages promotion release.json schema is not supported")
    if update.get("schema") != UPDATE_SCHEMA:
        raise SystemExit("Pages promotion update.json schema is not supported")
    if release.get("app_name") != APP_NAME:
        raise SystemExit("Pages promotion release.json app identity does not match")
    if release.get("channel") != ATOMIC_STABLE_CHANNEL or update.get("channel") != ATOMIC_STABLE_CHANNEL:
        raise SystemExit("Pages promotion requires stable-v2 release and update metadata")

    version = release.get("version")
    if not isinstance(version, str) or RELEASE_VERSION_PATTERN.fullmatch(version) is None:
        raise SystemExit("Pages promotion release version is invalid")
    if _version_tuple(version) < (0, 4, 1):
        raise SystemExit("Pages promotion requires version 0.4.1 or later")
    if release.get("release_id") != version or update.get("latest_version") != version:
        raise SystemExit("Pages promotion release_id/latest_version do not match the release version")
    minimum_supported = update.get("minimum_supported_version")
    if not isinstance(minimum_supported, str) or _version_tuple(minimum_supported) > _version_tuple(version):
        raise SystemExit("Pages promotion minimum_supported_version is invalid")

    source_commit = release.get("source_commit")
    if source_commit != update.get("source_commit"):
        raise SystemExit("Pages promotion source_commit differs between release and update metadata")
    validate_source_provenance(source_commit, release.get("source_tree_dirty"))
    if release.get("source_tree_dirty") is not False or update.get("source_tree_dirty") is not False:
        raise SystemExit("Pages promotion requires source_tree_dirty=false")
    if release.get("notarized") is not True:
        raise SystemExit("Pages promotion requires notarized=true")
    if release.get("clinical_use") is not False:
        raise SystemExit("Pages promotion requires clinical_use=false")

    file_name = release.get("file_name")
    file_size_bytes = release.get("file_size_bytes")
    sha256 = release.get("sha256")
    if not isinstance(file_name, str) or not file_name:
        raise SystemExit("Pages promotion release file_name is invalid")
    if not isinstance(file_size_bytes, int) or isinstance(file_size_bytes, bool) or file_size_bytes <= 0:
        raise SystemExit("Pages promotion release file_size_bytes is invalid")
    if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise SystemExit("Pages promotion release SHA-256 is invalid")
    if update.get("file_size_bytes") != file_size_bytes or update.get("sha256") != sha256:
        raise SystemExit("Pages promotion release/update DMG size or SHA-256 differs")

    expected_download_url = object_url(
        CANONICAL_DOWNLOAD_ORIGIN,
        object_key(CANONICAL_OBJECT_PREFIX, f"releases/{version}/{file_name}"),
    )
    expected_release_notes_url = object_url(
        CANONICAL_DOWNLOAD_ORIGIN,
        object_key(CANONICAL_OBJECT_PREFIX, f"releases/{version}/RELEASE_NOTES.txt"),
    )
    expected_update_manifest_url = object_url(
        CANONICAL_DOWNLOAD_ORIGIN,
        object_key(CANONICAL_OBJECT_PREFIX, f"releases/{ATOMIC_STABLE_CHANNEL}/update.json"),
    )
    if release.get("download_url") != expected_download_url or update.get("download_url") != expected_download_url:
        raise SystemExit("Pages promotion download URL is not the canonical immutable release URL")
    if release.get("release_notes_url") != expected_release_notes_url or update.get("release_notes_url") != expected_release_notes_url:
        raise SystemExit("Pages promotion release notes URL is not the canonical immutable release URL")
    if release.get("update_manifest_url") != expected_update_manifest_url:
        raise SystemExit("Pages promotion update manifest URL is not canonical stable-v2")
    published_at = release.get("published_at")
    if published_at != update.get("published_at"):
        raise SystemExit("Pages promotion published_at differs between release and update metadata")
    if not isinstance(published_at, str):
        raise SystemExit("Pages promotion published_at is invalid")
    normalize_published_at(published_at)

    if not dmg.is_file():
        raise SystemExit(f"Pages promotion DMG not found: {dmg}")
    if dmg.name != file_name:
        raise SystemExit("Pages promotion DMG filename does not match release.json")
    if dmg.stat().st_size != file_size_bytes:
        raise SystemExit("Pages promotion DMG size does not match release.json")
    if sha256_file(dmg) != sha256:
        raise SystemExit("Pages promotion DMG SHA-256 does not match release.json")

    return {
        "version": version,
        "file_name": file_name,
        "file_size_bytes": file_size_bytes,
        "sha256": sha256,
        "download_url": expected_download_url,
        "release_notes_url": expected_release_notes_url,
        "update_manifest_url": expected_update_manifest_url,
        "published_at": published_at,
        "source_commit": source_commit,
    }


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Pages promotion template mismatch for {label}: expected exactly one occurrence, found {count}"
        )
    return text.replace(old, new, 1)


def _promote_app_page(text: str, *, version: str) -> str:
    replacements = (
        (
            f"{version} beta・Apple Silicon搭載のMac・macOS 14以降",
            f"バージョン{version}・Apple Silicon搭載のMac・macOS 14以降",
            "hero version and platform",
        ),
        ("<strong>ベータ版</strong>", "<strong>最新バージョン</strong>", "release status"),
        (
            f"              Version {version} beta\n",
            f"              Version {version}\n",
            "release version",
        ),
        (
            f"""              Version {version} betaでは、CT/CBCTの3Dプレビューに加え、
              口腔内スキャンからの歯別STL作成、詳細なエラー報告、単一.dcm対応を試せます。
              安定版0.3.0も引き続きダウンロードできます。""",
            f"""              Version {version}では、CT/CBCTの3Dプレビューに加え、
              口腔内スキャンからの歯別STL作成、詳細なエラー報告、単一.dcm対応を提供します。""",
            "release lead",
        ),
        (
            f"""              <a class="button primary" href="/download-beta">{version} betaをダウンロード</a>
              <a class="button subtle" href="/download">安定版0.3.0</a>""",
            f'              <a class="button primary" href="/download">Version {version}をダウンロード</a>',
            "hero download call to action",
        ),
        (
            f"""              <a class="button primary" href="/download-beta">{version} betaをダウンロード</a>
              <a class="button" href="/download">安定版0.3.0をダウンロード</a>
              <a class="button" href="#update-details">更新内容を見る</a>""",
            f"""              <a class="button primary" href="/download">Version {version}をダウンロード</a>
              <a class="button" href="#update-details">更新内容を見る</a>""",
            "release download call to action",
        ),
        (
            f"""            <a class="button primary" href="/download-beta">{version} betaをダウンロード</a>
            <a class="button" href="/release-notes-beta">{version} betaのリリースノート</a>
            <a class="button" href="/download">安定版0.3.0をダウンロード</a>
            <a class="button" href="/release-notes">安定版0.3.0のリリースノート</a>""",
            f"""            <a class="button primary" href="/download">Version {version}をダウンロード</a>
            <a class="button" href="/release-notes">リリースノート</a>""",
            "footer download call to action",
        ),
        (
            f"{version} beta／macOS 14以降、0.3.0安定版／macOS 13以降。どちらもDeveloper ID署名・Apple公証済みです。",
            f"TotalSegmentator Wrapper for Mac {version}／Developer ID署名・Apple公証済み",
            "release footer",
        ),
    )
    for old, new, label in replacements:
        text = _replace_once(text, old, new, label)
    return text


def _promote_app_hub(text: str, *, version: str) -> str:
    text = _replace_once(text, "<dd>0.3.0</dd>", f"<dd>{version}</dd>", "app hub version")
    return _replace_once(
        text,
        "Apple Silicon Mac / macOS 13+",
        "Apple Silicon Mac / macOS 14+",
        "app hub platform",
    )


def _promote_redirects(text: str, *, download_url: str, release_notes_url: str) -> str:
    replacements = {
        "/download": download_url,
        "/release-notes": release_notes_url,
    }
    found = {route: 0 for route in replacements}
    output: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            output.append(line)
            continue
        parts = stripped.split()
        route = parts[0]
        if route not in replacements:
            output.append(line)
            continue
        if len(parts) != 3 or parts[2] != "302":
            raise SystemExit(f"Pages promotion redirect has an unexpected format: {line}")
        found[route] += 1
        output.append(f"{route} {replacements[route]} 302")
    invalid = [route for route, count in found.items() if count != 1]
    if invalid:
        raise SystemExit(
            "Pages promotion redirects require exactly one route for " + ", ".join(invalid)
        )
    return "\n".join(output) + "\n"


def _validate_promoted_pages(staging_root: Path, identity: dict) -> None:
    page = (staging_root / "pages" / "index.html").read_text(encoding="utf-8")
    launch = (staging_root / "pages" / "launch2.html").read_text(encoding="utf-8")
    hub = (staging_root / "app-hub" / "index.html").read_text(encoding="utf-8")
    if page != launch:
        raise SystemExit("promoted Pages index.html and launch2.html differ")
    required_page = (
        f"バージョン{identity['version']}・Apple Silicon搭載のMac・macOS 14以降",
        f"Version {identity['version']}",
        "<strong>最新バージョン</strong>",
        f"Version {identity['version']}をダウンロード",
    )
    forbidden_page = (
        "公開前",
        "開発中",
        "/download-beta",
        "/release-notes-beta",
        "ベータ版",
        "public-0-3-0",
        "現在の公開版 0.3.0",
        "0.4.0は公開停止済み",
        "macOS 13",
    )
    if any(value not in page for value in required_page) or any(value in page for value in forbidden_page):
        raise SystemExit("promoted Pages content did not pass release-state validation")
    if f"<dd>{identity['version']}</dd>" not in hub or "Apple Silicon Mac / macOS 14+" not in hub:
        raise SystemExit("promoted app hub does not show the verified release and macOS 14+")
    if "<dd>0.3.0</dd>" in hub or "macOS 13" in hub:
        raise SystemExit("promoted app hub still contains the legacy public release state")
    for directory in (staging_root / "pages", staging_root / "app-hub"):
        redirects = (directory / "_redirects").read_text(encoding="utf-8")
        if f"/download {identity['download_url']} 302" not in redirects:
            raise SystemExit(f"promoted {directory.name} download redirect is not verified")
        if f"/release-notes {identity['release_notes_url']} 302" not in redirects:
            raise SystemExit(f"promoted {directory.name} release-notes redirect is not verified")
        if "/releases/0.3.0/" in redirects:
            raise SystemExit(f"promoted {directory.name} redirects still target 0.3.0")
    validate_public_asset_provenance(
        pages_root=staging_root / "pages",
        app_hub_root=staging_root / "app-hub",
    )
    validate_public_asset_release_eligibility(
        pages_root=staging_root / "pages",
        app_hub_root=staging_root / "app-hub",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_json(path: Path, payload: dict) -> None:
    write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def upload_object(source: Path, key: str, content_type: str, *, repo_root: Path, immutable: bool) -> dict:
    cache_control = "public, max-age=31536000, immutable" if immutable else "no-cache"
    return {
        "source": display_path(source, repo_root),
        "key": key,
        "content_type": content_type,
        "cache_control": cache_control,
        "immutable": immutable,
    }


def display_path(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(repo_root))
    except ValueError:
        return str(resolved)


def release_notes(
    version: str,
    *,
    release_id: str | None = None,
    channel: str,
    notarized: bool,
) -> str:
    if notarized:
        distribution_status = (
            "- Developer ID signed and Apple notarized DMG for Apple Silicon Macs."
        )
    elif channel == "alpha":
        distribution_status = (
            "- Public alpha DMG: this build is not Developer ID signed or Apple "
            "notarized, so macOS may show an initial security warning. It must "
            "not be promoted to stable."
        )
    else:
        distribution_status = (
            "- Candidate metadata only: this DMG has not been verified as "
            "Developer ID signed and Apple notarized, and must not be published "
            "as the stable build."
        )
    title_version = release_id or version
    title_suffix = " public alpha" if channel == "alpha" else ""
    platform_requirement = ""
    target_compatibility_note = ""
    if _version_tuple(version) >= (0, 4, 1):
        platform_requirement = "- Requires macOS 14 or later on Apple Silicon Macs.\n"
        target_compatibility_note = (
            "- Dependency lock resolution host: macOS 26.6 / Apple Silicon / "
            "CPython 3.12. macOS 14 compatibility is enforced through pip's explicit "
            "macosx_14_0_arm64 target options and an audited wheelhouse tag manifest.\n"
            "- macOS 14 runtime E2E is unverified. The release evidence records the "
            "actual test-account operating systems; macOS 15.7.3 and macOS 26 are "
            "required verification targets.\n"
        )
    if channel == ATOMIC_STABLE_CHANNEL:
        update_channel_status = (
            "- The legacy stable update manifest is permanently frozen and read-only "
            "at the live 0.3.0 payload. The local 0.4.0 record is withdrawn and must "
            "not be uploaded or treated as an update target. This release is "
            "distributed through the verified stable-v2 update manifest."
        )
    else:
        update_channel_status = (
            "- The legacy stable update manifest is permanently frozen and read-only "
            "at the live 0.3.0 payload. The local 0.4.0 record is withdrawn and must "
            "not be uploaded or treated as an update target. A future 0.4.1+ release "
            "is installed from a manually downloaded DMG until its verified stable-v2 "
            "manifest is published; stable-v2 currently returns HTTP 404."
        )
    return f"""TotalSegmentator Wrapper for Mac {title_version}{title_suffix}

{distribution_status}
{platform_requirement}{target_compatibility_note}- Bundled Python 3.12 runtime for first-run setup without sudo or Homebrew.
- Bundled dcm2niix and GDCM 3.2.7 DICOM normalizer for local CT intake.
- Native JPEG, JPEG-LS, JPEG 2000, and RLE DICOM decoding with lossless transcoding before dcm2niix conversion.
- Invalid compressed DICOM data now fails explicitly instead of silently falling back. Multi-frame Enhanced CT is rejected by the clean-conversion path and can proceed only through the explicit shape-confirmation rescue path.
- Bundled Sample 1 non-clinical preview data and offline 3D preview HTML.
- First setup now prepares the craniofacial, robust crop, and teeth model weights.
- Interrupted downloads of those three TotalSegmentator archives resume with strictly validated HTTP Range responses. If a server ignores Range, the partial bytes are never concatenated and the archive is safely fetched again from byte zero.
- Completed model archives are accepted only after pinned SHA-256, ZIP CRC/path-safety, and exact expected model-structure validation.
- Setup is refused when the app is run directly from a DMG or App Translocation. A stale or broken environment that points to an older app bundle is detected and rebuilt from the currently installed app.
- Setup shows measured download progress, including resumed byte position, whenever the server supplies the required size metadata.
- Craniofacial app previews now use the robust crop path by default for local CBCT inputs.
- High-resolution ToothSeg refinement can be explicitly prepared and run after a successful TotalSegmentator result with detected teeth.
- The first ToothSeg preparation downloads approximately 920 MB; it never starts refinement automatically after the download.
- Model-specific stages show measured progress when available and an honest indeterminate range otherwise.
- Browser-ready 3D previews are produced before detailed STL export finishes.
- Binary STL serialization is chunked and vectorized while preserving mesh geometry and float-precision normals.
- Large browser mesh payloads are parsed as JSON to avoid Safari JavaScript parser stack overflow.
- On the tested M1 Mac with 16 GB unified memory, the bundled 12 mm ROI sample took 34 minutes 27 seconds; other scans may take longer or exceed available memory.
- Update checks run only after the user presses the update button.
{update_channel_status}
- Failure screens can copy an allowlisted structured error report and open the existing Google support form without automatically uploading files or logs. Google account sign-in is not required.
- Research-only intra-oral PLY/STL scans can produce separate tooth STL files with the built-in MeshSegNet implementation or the independently implemented TGNet path. MeshSegNet weights are downloaded separately from a pinned source on first use of the intra-oral scan feature, SHA-256 verified, and not bundled in the app or DMG.
- TGNet weights are not bundled or automatically downloaded. To use TGNet, select the specified ckpts(new).zip or its expanded directory after obtaining it from the distribution page linked by the application.
- TGNet weight license terms are not verified by this application. Review the terms shown by the distributor before use.
- The application accepts the TGNet selection only after validating the required filenames and pinned SHA-256 values; strict role, tensor-shape, and class validation remains active at inference.
- Intra-oral scan output now adds gingiva.stl at the result root when a gingiva/background candidate exists. TGNet uses its gingiva prediction; MeshSegNet uses label 0 as a gingiva-or-background candidate that requires visual review. No gingiva STL is emitted when label 0 is absent.

Non-clinical limitation:
{NON_CLINICAL_NOTICE_EN}

Data and update endpoint:
DICOM, CT, generated outputs, local paths, logs, and user identifiers are not
sent to the update endpoint.
"""


if __name__ == "__main__":
    raise SystemExit(main())
