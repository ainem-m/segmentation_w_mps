#!/bin/bash
set -euo pipefail

# This collector is intended to run on a clean test account.  Do not let a
# user-provided PATH substitute the core file/publish utilities used to record
# its release evidence.
PATH="/usr/bin:/bin:/usr/sbin:/sbin"
export PATH

APP_NAME="TotalSegmentator Wrapper for Mac.app"
APP_PATH="${1:-}"
APP_DISCOVERY_FAILURE=""

if [[ -z "${APP_PATH}" ]]; then
  if [[ -d "${HOME}/Applications/${APP_NAME}" ]]; then
    APP_PATH="${HOME}/Applications/${APP_NAME}"
  elif [[ -d "/Applications/${APP_NAME}" ]]; then
    APP_PATH="/Applications/${APP_NAME}"
  else
    APP_DISCOVERY_FAILURE="app_not_found_in_expected_location"
  fi
fi

SUPPORT_DIR="${HOME}/Library/Application Support/TotalSegmentatorWrapperMac"
STATE_JSON="${SUPPORT_DIR}/setup_state.json"
EVIDENCE_JSON="${SUPPORT_DIR}/logs/test_account_install_evidence.json"
VENV_PYTHON="${SUPPORT_DIR}/env/bin/python"
SHARED_EVIDENCE_DIR="${TOTALSEGMENTATOR_WRAPPER_MAC_SHARED_EVIDENCE_DIR:-/Users/Shared/TotalSegmentatorWrapperMac}"
SHARED_EVIDENCE_JSON="${SHARED_EVIDENCE_DIR}/test_account_install_evidence.json"
DMG_PATH="${TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH:-}"
EXPECTED_APP_VERSION="${TOTALSEGMENTATOR_WRAPPER_MAC_EXPECTED_APP_VERSION:-}"
PREFLIGHT_EVIDENCE_SCHEMA="totalsegmentator_wrapper_mac.test_account_install_preflight_failure.v1"

if ! mkdir -p "${SUPPORT_DIR}/logs"; then
  echo "evidence出力先を作成できません: ${SUPPORT_DIR}/logs" >&2
  exit 1
fi
RUN_ID="$(LC_ALL=C /usr/bin/od -An -N16 -tx1 /dev/urandom | /usr/bin/tr -d ' \n')"
if [[ ! "${RUN_ID}" =~ ^[0-9a-f]{32}$ ]]; then
  echo "証跡run IDを安全に生成できませんでした。" >&2
  exit 1
fi
COLLECTED_AT_UTC="$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
if [[ ! "${COLLECTED_AT_UTC}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
  echo "証跡timestampを安全に生成できませんでした。" >&2
  exit 1
fi

is_supported_preflight_failure_reason() {
  # These are collector-owned diagnostic codes, not user-provided text.  Keep
  # the preflight writer usable before the private Python runtime exists while
  # still making every interpolated JSON value grammar-safe.
  case "$1" in
    app_not_found_in_expected_location|app_path_is_not_a_directory|setup_state_missing|setup_runtime_python_missing|collector_failed_to_publish_current_evidence)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

write_preflight_failure_evidence() {
  local evidence_path="$1"
  local failure_reason="$2"
  local evidence_parent
  local evidence_name
  local temporary
  if ! is_supported_preflight_failure_reason "${failure_reason}"; then
    echo "未対応のpreflight failure reasonはevidenceへ書き出せません: ${failure_reason}" >&2
    return 1
  fi
  evidence_parent="$(dirname "${evidence_path}")"
  evidence_name="$(basename "${evidence_path}")"
  if ! mkdir -p "${evidence_parent}"; then
    return 1
  fi
  if ! temporary="$(/usr/bin/mktemp "${evidence_parent}/.${evidence_name}.${RUN_ID}.XXXXXX")"; then
    return 1
  fi
  if ! (
    umask 077
    # RUN_ID, COLLECTED_AT_UTC, and failure_reason are all validated fixed
    # grammar tokens above.  Never serialize an arbitrary shell argument into
    # this JSON because this path is used before Python is available.
    printf '{"schema":"%s","passed":false,"run_id":"%s","collected_at_utc":"%s","preflight_failure":"%s"}\n' \
      "${PREFLIGHT_EVIDENCE_SCHEMA}" \
      "${RUN_ID}" "${COLLECTED_AT_UTC}" "${failure_reason}" > "${temporary}"
  ); then
    /bin/rm -f "${temporary}"
    return 1
  fi
  if ! mv -f "${temporary}" "${evidence_path}"; then
    /bin/rm -f "${temporary}"
    return 1
  fi
}

publish_preflight_failure() {
  local failure_reason="$1"
  local local_status=0
  local shared_status=0
  if ! write_preflight_failure_evidence "${EVIDENCE_JSON}" "${failure_reason}"; then
    local_status=1
  fi
  if ! write_preflight_failure_evidence "${SHARED_EVIDENCE_JSON}" "${failure_reason}"; then
    shared_status=1
  fi
  if [[ "${local_status}" -ne 0 ]]; then
    echo "今回の失敗evidenceをlocalへ書き出せませんでした: ${EVIDENCE_JSON}" >&2
  fi
  if [[ "${shared_status}" -ne 0 ]]; then
    echo "今回の失敗evidenceをsharedへ書き出せませんでした: ${SHARED_EVIDENCE_JSON}" >&2
  fi
  [[ "${local_status}" -eq 0 && "${shared_status}" -eq 0 ]]
}

supersede_evidence_if_present() {
  local evidence_path="$1"
  if [[ -e "${evidence_path}" || -L "${evidence_path}" ]]; then
    local evidence_parent
    local evidence_name
    local superseded_path
    local temporary
    local archived_encoding="omitted"
    local archived_payload=""
    local archive_size=""
    local collision=0
    evidence_parent="$(dirname "${evidence_path}")"
    evidence_name="$(basename "${evidence_path}")"
    superseded_path="${evidence_path}.superseded-${RUN_ID}"
    while [[ -e "${superseded_path}" || -L "${superseded_path}" ]]; do
      collision=$((collision + 1))
      if [[ "${collision}" -gt 100 ]]; then
        echo "以前の証跡を失効化する退避先を確保できません: ${evidence_path}" >&2
        return 1
      fi
      superseded_path="${evidence_path}.superseded-${RUN_ID}-${collision}"
    done
    if [[ -f "${evidence_path}" && ! -L "${evidence_path}" ]]; then
      archive_size="$(/usr/bin/wc -c < "${evidence_path}" | /usr/bin/tr -d '[:space:]')"
      if [[ "${archive_size}" =~ ^[0-9]+$ && "${archive_size}" -le 2097152 ]]; then
        if archived_payload="$(/usr/bin/base64 < "${evidence_path}" | /usr/bin/tr -d '\n')"; then
          archived_encoding="base64"
        fi
      fi
    fi
    if ! temporary="$(/usr/bin/mktemp "${evidence_parent}/.${evidence_name}.${RUN_ID}.superseded.XXXXXX")"; then
      return 1
    fi
    if ! (
      umask 077
      printf '{"schema":"totalsegmentator_wrapper_mac.test_account_install_evidence.superseded.v1","passed":false,"superseded_by_run_id":"%s","superseded_at_utc":"%s","archived_evidence_encoding":"%s","archived_evidence_base64":"%s"}\n' \
        "${RUN_ID}" "${COLLECTED_AT_UTC}" "${archived_encoding}" "${archived_payload}" > "${temporary}"
    ); then
      /bin/rm -f "${temporary}"
      return 1
    fi
    # Replace the normal-path source with an invalid tombstone first.  If the
    # following archive rename is interrupted, no importer-compatible PASS is
    # left at either the normal path or the intended superseded path.
    if ! mv -f "${temporary}" "${evidence_path}"; then
      /bin/rm -f "${temporary}"
      return 1
    fi
    if ! mv -f "${evidence_path}" "${superseded_path}"; then
      echo "以前の証跡を失効化しましたが、診断用退避先へ移動できません: ${evidence_path}" >&2
      return 1
    fi
    echo "以前の証跡を失効化して退避しました: ${superseded_path}"
  fi
}

if ! supersede_evidence_if_present "${EVIDENCE_JSON}"; then
  echo "以前のlocal evidenceを安全に退避できないため、今回の検証を開始しません。" >&2
  exit 1
fi
if [[ -e "${SHARED_EVIDENCE_JSON}" || -L "${SHARED_EVIDENCE_JSON}" ]]; then
  if ! supersede_evidence_if_present "${SHARED_EVIDENCE_JSON}"; then
    echo "以前のshared evidenceを安全に退避できないため、今回の検証を開始しません。" >&2
    exit 1
  fi
fi

if [[ -n "${APP_DISCOVERY_FAILURE}" ]]; then
  echo "TotalSegmentator Wrapper for Mac.app が ~/Applications または /Applications に見つかりません。" >&2
  echo "必要ならアプリのパスを明示してください: $0 /path/to/TotalSegmentator\\ Wrapper\\ for\\ Mac.app" >&2
  if ! publish_preflight_failure "${APP_DISCOVERY_FAILURE}"; then
    echo "失敗evidenceのpublishも失敗しました。" >&2
  fi
  exit 2
fi
if [[ ! -d "${APP_PATH}" ]]; then
  echo "App bundle が見つかりません: ${APP_PATH}" >&2
  if ! publish_preflight_failure "app_path_is_not_a_directory"; then
    echo "失敗evidenceのpublishも失敗しました。" >&2
  fi
  exit 2
fi
if [[ ! -f "${STATE_JSON}" ]]; then
  echo "Setup状態ファイルが見つかりません: ${STATE_JSON}" >&2
  echo "このアカウントで TotalSegmentator Wrapper for Mac.app を開き、先にSetupを実行してください。" >&2
  if ! publish_preflight_failure "setup_state_missing"; then
    echo "失敗evidenceのpublishも失敗しました。" >&2
  fi
  exit 1
fi
if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "Setup済みruntime Pythonが見つかりません: ${VENV_PYTHON}" >&2
  if ! publish_preflight_failure "setup_runtime_python_missing"; then
    echo "失敗evidenceのpublishも失敗しました。" >&2
  fi
  exit 1
fi

set +e
"${VENV_PYTHON}" - "${APP_PATH}" "${SUPPORT_DIR}" "${STATE_JSON}" "${EVIDENCE_JSON}" "${SHARED_EVIDENCE_JSON}" "${DMG_PATH}" "${EXPECTED_APP_VERSION}" "${RUN_ID}" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path


EVIDENCE_SCHEMA = "totalsegmentator_wrapper_mac.test_account_install_evidence.v2"

app_input_path = Path(sys.argv[1]).expanduser()
support_dir = Path(sys.argv[2]).expanduser().resolve()
state_json = Path(sys.argv[3]).expanduser().resolve()
evidence_json = Path(sys.argv[4]).expanduser().absolute()
shared_evidence_json = Path(sys.argv[5]).expanduser().resolve()
dmg_path_arg = sys.argv[6]
expected_app_version = sys.argv[7].strip()
run_id = sys.argv[8]
home = Path.home().resolve()
try:
    app_path = app_input_path.resolve(strict=True)
except (OSError, RuntimeError):
    app_path = app_input_path.absolute()
resources = app_path / "Contents" / "Resources"
manifest_path = resources / "setup_manifest.json"
info_plist_path = app_path / "Contents" / "Info.plist"
runtime_dir = resources / "python" / "cpython-3.12"
runtime_python = runtime_dir / "bin" / "python3.12"
bundled_normalizer = resources / "bin" / "totalsegmentator-wrapper-dicom-normalizer"
bundled_dcm2niix = resources / "bin" / "dcm2niix"
license_inventory = resources / "licenses" / "third_party_license_inventory.json"
wrapper_license = resources / "LICENSE"
wrapper_notice = resources / "NOTICE"
totalsegmentator_license = resources / "licenses" / "TotalSegmentator-Apache-2.0.txt"
dentalsegmentator_notice = resources / "licenses" / "DentalSegmentator-NOTICE.txt"
toothseg_notice = resources / "licenses" / "ToothSeg-NOTICE.txt"
dcm2niix_license = resources / "licenses" / "dcm2niix-license.txt"
sample1_input = resources / "sample1" / "input" / "owner_cbct_jawcrop_0p5mm.nii.gz"
sample1_viewer = resources / "sample1" / "surface_preview" / "index.html"
sample1_manifest = resources / "sample1" / "sample_manifest.json"
sample1_notices = resources / "sample1" / "THIRD_PARTY_NOTICES.txt"


def regular_metadata_matches(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        stat.S_ISREG(after.st_mode)
        and before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )


def open_regular_readonly(path: Path, *, maximum_size: int | None = None) -> tuple[int, os.stat_result]:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"not a regular non-symlink file: {path}")
    if maximum_size is not None and metadata.st_size > maximum_size:
        raise OSError(f"file exceeds safe size limit: {path}")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:  # pragma: no cover - supported macOS always has it
        raise OSError("O_NOFOLLOW is unavailable")
    descriptor = os.open(path, os.O_RDONLY | nofollow)
    try:
        opened = os.fstat(descriptor)
        if not regular_metadata_matches(metadata, opened):
            raise OSError(f"file changed while opening: {path}")
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def ensure_regular_file_unchanged(
    descriptor: int,
    metadata: os.stat_result,
    *,
    path: Path,
) -> None:
    if not regular_metadata_matches(metadata, os.fstat(descriptor)):
        raise OSError(f"file changed while reading: {path}")


def read_regular_bytes(path: Path, *, maximum_size: int) -> bytes:
    descriptor, metadata = open_regular_readonly(path, maximum_size=maximum_size)
    try:
        chunks = []
        received = 0
        while chunk := os.read(descriptor, 64 * 1024):
            received += len(chunk)
            if received > maximum_size:
                raise OSError(f"file exceeds safe size limit: {path}")
            chunks.append(chunk)
        ensure_regular_file_unchanged(descriptor, metadata, path=path)
        if received != metadata.st_size:
            raise OSError(f"file changed while reading: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def load_json(path: Path) -> dict:
    try:
        payload = json.loads(read_regular_bytes(path, maximum_size=4 * 1024 * 1024))
        if isinstance(payload, dict):
            return payload
        return {"_load_error": "JSON root must be an object"}
    except Exception as exc:  # pragma: no cover - defensive for field script
        return {"_load_error": repr(exc)}


def load_plist(path: Path) -> dict:
    try:
        payload = plistlib.loads(read_regular_bytes(path, maximum_size=4 * 1024 * 1024))
        if isinstance(payload, dict):
            return payload
        return {"_load_error": "plist root must be a dictionary"}
    except Exception as exc:  # pragma: no cover - defensive for field script
        return {"_load_error": repr(exc)}


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def bundled_regular_file(relative: object) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    candidate = resources / relative
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resources.resolve(strict=True))
    except (OSError, ValueError):
        return None
    if candidate.is_symlink() or not candidate.is_file():
        return None
    return candidate


def sha256_file(path: Path) -> str:
    descriptor, metadata = open_regular_readonly(path)
    digest = hashlib.sha256()
    try:
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        ensure_regular_file_unchanged(descriptor, metadata, path=path)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def regular_file_sha256(path: Path) -> str | None:
    try:
        return sha256_file(path)
    except OSError:
        return None


def write_json_atomically(path: Path, payload: object) -> None:
    rendered = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{run_id}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(rendered):
            offset += os.write(descriptor, rendered[offset:])
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(descriptor)
    os.replace(temporary, path)
    try:
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


state = load_json(state_json)
manifest = load_json(manifest_path)
info_plist = load_plist(info_plist_path)
checks: list[dict] = []


def check(name: str, passed: bool, detail: object = None) -> None:
    if type(passed) is not bool:
        raise TypeError(f"collector check {name} must be an exact bool")
    checks.append({"name": name, "passed": passed, "detail": detail})


try:
    app_input_metadata = app_input_path.lstat()
    app_bundle_not_symlink = (
        not app_input_path.is_symlink() and app_input_path.is_dir()
    )
except OSError as exc:
    app_input_metadata = None
    app_bundle_not_symlink = False
    app_path_error = repr(exc)
else:
    app_path_error = None
check(
    "app_bundle_not_symlink",
    app_bundle_not_symlink,
    {
        "input_path": str(app_input_path),
        "resolved_path": str(app_path),
        "error": app_path_error,
    },
)
valid_app_parents = {Path("/Applications"), home / "Applications"}
check(
    "app_bundle_in_expected_install_location",
    app_bundle_not_symlink
    and app_path.name == "TotalSegmentator Wrapper for Mac.app"
    and app_path.parent in valid_app_parents,
    {"app_path": str(app_path), "allowed_parents": sorted(map(str, valid_app_parents))},
)


def successful_setup_step(name: str) -> bool:
    matching = [
        step
        for step in state.get("steps", [])
        if isinstance(step, dict) and step.get("name") == name
    ]
    return (
        len(matching) == 1
        and matching[0].get("status") == "success"
        and matching[0].get("returncode") == 0
    )


codesign = shutil.which("codesign")
if codesign:
    proc = subprocess.run(
        [codesign, "--verify", "--deep", "--strict", "--verbose=2", str(app_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    check("app_codesign_valid", proc.returncode == 0, (proc.stderr or proc.stdout).strip())
else:
    check("app_codesign_valid", False, "codesign not found")

spctl = shutil.which("spctl")
if spctl:
    proc = subprocess.run(
        [spctl, "--assess", "--type", "execute", "--verbose=4", str(app_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    check("spctl_app_accepted", proc.returncode == 0, (proc.stderr or proc.stdout).strip())
else:
    check("spctl_app_accepted", False, "spctl not found")

is_notarized_manifest = manifest.get("notarized") is True
check("manifest_notarized", is_notarized_manifest, manifest.get("notarized"))
stapler = shutil.which("xcrun")
dmg_path = Path(dmg_path_arg).expanduser() if dmg_path_arg else None
if dmg_path is not None:
    try:
        dmg_metadata = dmg_path.lstat()
        dmg_identity_path = dmg_path.resolve(strict=True)
    except (OSError, RuntimeError):
        dmg_identity_path = dmg_path.absolute()
        dmg_metadata = None
    dmg_identity_sha256 = (
        regular_file_sha256(dmg_path)
        if dmg_metadata is not None and not dmg_path.is_symlink()
        else None
    )
else:
    dmg_identity_path = None
    dmg_identity_sha256 = None
if stapler and dmg_path and dmg_path.exists():
    proc = subprocess.run(
        [stapler, "stapler", "validate", str(dmg_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    check("stapler_dmg_valid", proc.returncode == 0, (proc.stderr or proc.stdout).strip())
elif is_notarized_manifest:
    check("stapler_dmg_valid", False, "set TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH to the notarized DMG for this check")
else:
    check("stapler_dmg_valid", False, "notarized manifest is false")

check("setup_state_success", state.get("status") == "success", state.get("status"))
check("mps_actual_device", state.get("doctor", {}).get("actual_device") == "mps", state.get("doctor", {}).get("actual_device"))
check("mps_gate_pass", state.get("doctor", {}).get("convtranspose3d_fp32") == "pass", state.get("doctor", {}).get("convtranspose3d_fp32"))
check(
    "mps_no_fallback",
    state.get("doctor", {}).get("actual_device") == "mps"
    and state.get("doctor", {}).get("fallback_reason") is None,
    state.get("doctor", {}).get("fallback_reason"),
)
install_wheel_steps = [
    step
    for step in state.get("steps", [])
    if isinstance(step, dict) and step.get("name") == "install_wheel"
]
install_wheel_log = (
    Path(str(install_wheel_steps[0].get("diagnostic_log"))).expanduser()
    if len(install_wheel_steps) == 1 and install_wheel_steps[0].get("diagnostic_log")
    else None
)
check(
    "install_wheel_step_success",
    len(install_wheel_steps) == 1
    and install_wheel_steps[0].get("status") == "success"
    and install_wheel_steps[0].get("returncode") == 0
    and install_wheel_log is not None
    and install_wheel_log.is_file(),
    install_wheel_steps,
)
check(
    "normalizer_from_app_bundle",
    state.get("dicom_normalizer", {}).get("normalizer_source") == "app_bundle",
    state.get("dicom_normalizer", {}).get("normalizer_source"),
)
check("python_version_312", str(state.get("python_version", "")).startswith("3.12."), state.get("python_version"))

state_python = state.get("python_executable")
check(
    "python_executable_inside_app",
    bool(state_python) and is_relative_to(Path(state_python), app_path),
    state_python,
)

state_support = state.get("paths", {}).get("app_support")
check(
    "app_support_inside_current_home",
    bool(state_support) and is_relative_to(Path(state_support), home),
    state_support,
)
check("no_user_global_pip_cache", not (home / "Library" / "Caches" / "pip").exists(), str(home / "Library" / "Caches" / "pip"))
check("pip_cache_under_app_support", (support_dir / "cache" / "pip").is_dir(), str(support_dir / "cache" / "pip"))
check("pycache_under_app_support", (support_dir / "cache" / "pycache").is_dir(), str(support_dir / "cache" / "pycache"))

try:
    fpsample_version = metadata.version("fpsample")
    check("installed_fpsample_version", fpsample_version == "1.0.2", fpsample_version)
except Exception as exc:
    check("installed_fpsample_version", False, repr(exc))

try:
    import fpsample
    import numpy as np

    fpsample_points = np.column_stack(
        [np.arange(30), np.zeros(30), np.zeros(30)]
    ).astype(np.float32)
    fpsample_indices = fpsample.fps_sampling(
        fpsample_points, 5, start_idx=0
    ).tolist()
    check(
        "installed_fpsample_import_sample",
        fpsample_indices == [0, 29, 15, 22, 8],
        {"module": str(fpsample.__file__), "indices": fpsample_indices},
    )
except Exception as exc:
    check("installed_fpsample_import_sample", False, repr(exc))

try:
    acvl_utils_version = metadata.version("acvl-utils")
    check("installed_acvl_utils_version", acvl_utils_version == "0.2.6", acvl_utils_version)
except Exception as exc:
    check("installed_acvl_utils_version", False, repr(exc))

try:
    import acvl_utils

    check(
        "installed_acvl_utils_import",
        bool(getattr(acvl_utils, "__file__", None)),
        str(getattr(acvl_utils, "__file__", "")),
    )
except Exception as exc:
    check("installed_acvl_utils_import", False, repr(exc))

runtime = manifest.get("python_runtime", {})
check("manifest_ui_frontend_swiftui", manifest.get("ui_frontend") == "swiftui", manifest.get("ui_frontend"))
check(
    "app_minimum_macos_version_14",
    info_plist.get("LSMinimumSystemVersion") == "14.0"
    and manifest.get("minimum_macos_version") == "14.0",
    {
        "info_plist": info_plist.get("LSMinimumSystemVersion"),
        "setup_manifest": manifest.get("minimum_macos_version"),
    },
)
check("manifest_bundled_python312", runtime.get("strategy") == "bundled_python312", runtime.get("strategy"))
check("manifest_python_bundled", runtime.get("bundled") is True, runtime.get("bundled"))
check("bundled_python_exists", runtime_python.exists() and os.access(runtime_python, os.X_OK), str(runtime_python))

absolute_symlinks = []
if runtime_dir.exists():
    for candidate in runtime_dir.rglob("*"):
        if candidate.is_symlink():
            target = os.readlink(candidate)
            if os.path.isabs(target):
                absolute_symlinks.append(f"{candidate}: {target}")
check("bundled_python_has_no_absolute_symlinks", not absolute_symlinks, absolute_symlinks[:20])

bundled = manifest.get("bundled", {})
native_release_evidence_names = (
    "app_and_wheel_macho_macos14_arm64",
    "dicom_helpers_system_linkage_no_rpath",
    "normalizer_source_matches_bundled_receipts",
    "dcm2niix_source_matches_bundled_receipt_and_pointer",
)
try:
    from totalsegmentator_wrapper_mac.test_account_bundle_evidence import (
        verify_dcm2niix_source_provenance,
        verify_dicom_helpers_system_linkage,
        verify_macos14_arm64_app_and_wheels,
        verify_normalizer_source_provenance,
    )
except Exception as exc:  # The installed wrapper wheel must carry this verifier.
    for check_name in native_release_evidence_names:
        check(check_name, False, f"{type(exc).__name__}: {exc}")
else:
    native_release_evidence_checks = (
        ("app_and_wheel_macho_macos14_arm64", lambda: verify_macos14_arm64_app_and_wheels(app_path)),
        ("dicom_helpers_system_linkage_no_rpath", lambda: verify_dicom_helpers_system_linkage(resources, manifest)),
        ("normalizer_source_matches_bundled_receipts", lambda: verify_normalizer_source_provenance(resources, manifest)),
        ("dcm2niix_source_matches_bundled_receipt_and_pointer", lambda: verify_dcm2niix_source_provenance(resources, manifest)),
    )
    for check_name, verifier in native_release_evidence_checks:
        try:
            check(check_name, True, verifier())
        except Exception as exc:  # The evidence gate must fail closed on the clean account.
            check(check_name, False, f"{type(exc).__name__}: {exc}")

release_requires_hashed_lock = (
    manifest.get("signing_mode") == "developer-id"
    or manifest.get("notarized") is True
    or any(
        manifest.get(field) is not None
        for field in (
            "requirements_lock_sha256",
            "dependency_lock_metadata_sha256",
        )
    )
    or any(
        isinstance(bundled.get(field), str) and bundled.get(field)
        for field in (
            "requirements_lock",
            "dependency_lock_metadata",
        )
    )
)
license_inventory_payload = load_json(license_inventory)
sample1_manifest_payload = load_json(sample1_manifest)
check("manifest_includes_sample1", "sample1" in bundled, bundled.get("sample1"))
fpsample_manifest_sha256 = manifest.get("fpsample_wheel_sha256")
check(
    "manifest_has_fpsample_wheel_sha256",
    isinstance(fpsample_manifest_sha256, str)
    and len(fpsample_manifest_sha256) == 64,
    fpsample_manifest_sha256,
)
fpsample_wheel_relative = bundled.get("fpsample_wheel")
fpsample_wheel = (
    (resources / fpsample_wheel_relative).resolve()
    if isinstance(fpsample_wheel_relative, str) and fpsample_wheel_relative
    else None
)
fpsample_wheel_is_safe = (
    fpsample_wheel is not None
    and is_relative_to(fpsample_wheel, resources)
    and fpsample_wheel.is_file()
)
check(
    "bundled_fpsample_wheel_sha256_matches_manifest",
    fpsample_wheel_is_safe
    and sha256_file(fpsample_wheel) == fpsample_manifest_sha256,
    {
        "path": str(fpsample_wheel) if fpsample_wheel is not None else None,
        "manifest_sha256": fpsample_manifest_sha256,
        "actual_sha256": sha256_file(fpsample_wheel)
        if fpsample_wheel_is_safe
        else None,
    },
)
acvl_utils_manifest_sha256 = manifest.get("acvl_utils_wheel_sha256")
check(
    "manifest_has_acvl_utils_wheel_sha256",
    isinstance(acvl_utils_manifest_sha256, str)
    and len(acvl_utils_manifest_sha256) == 64,
    acvl_utils_manifest_sha256,
)
acvl_utils_wheel_relative = bundled.get("acvl_utils_wheel")
acvl_utils_wheel = (
    (resources / acvl_utils_wheel_relative).resolve()
    if isinstance(acvl_utils_wheel_relative, str) and acvl_utils_wheel_relative
    else None
)
acvl_utils_wheel_is_safe = (
    acvl_utils_wheel is not None
    and is_relative_to(acvl_utils_wheel, resources)
    and acvl_utils_wheel.is_file()
)
check(
    "bundled_acvl_utils_wheel_sha256_matches_manifest",
    acvl_utils_wheel_is_safe
    and sha256_file(acvl_utils_wheel) == acvl_utils_manifest_sha256,
    {
        "path": str(acvl_utils_wheel) if acvl_utils_wheel is not None else None,
        "manifest_sha256": acvl_utils_manifest_sha256,
        "actual_sha256": sha256_file(acvl_utils_wheel)
        if acvl_utils_wheel_is_safe
        else None,
    },
)
for manifest_field in (
    "app_version",
    "build_id",
    "dependency_set_id",
    "wheel_sha256",
    "constraints_sha256",
    "requirements_lock_sha256",
    "dependency_lock_metadata_sha256",
    "dependency_wheelhouse_manifest_sha256",
    "normalizer_input_sha256",
    "normalizer_sha256",
    "normalizer_sha256_scope",
    "dcm2niix_input_sha256",
    "dcm2niix_sha256",
    "dcm2niix_sha256_scope",
    "dcm2niix_version",
    "dcm2niix_source",
    "sample1_manifest_sha256",
    "setup_weights_manifest_sha256",
    "update_manifest_url",
    "update_allowed_hosts",
    "third_party_licenses",
  ):
    check(f"manifest_has_{manifest_field}", manifest_field in manifest, manifest.get(manifest_field))

requirements_lock_sha256 = manifest.get("requirements_lock_sha256")
dependency_lock_metadata_sha256 = manifest.get("dependency_lock_metadata_sha256")
dependency_wheelhouse_manifest_sha256 = manifest.get(
    "dependency_wheelhouse_manifest_sha256"
)
requirements_lock = bundled_regular_file(bundled.get("requirements_lock"))
dependency_lock_metadata = bundled_regular_file(
    bundled.get("dependency_lock_metadata")
)
dependency_wheelhouse_manifest = bundled_regular_file(
    bundled.get("dependency_wheelhouse_manifest")
)
if release_requires_hashed_lock:
    check(
        "wheel_install_hashed_lock",
        state.get("wheel_install_mode") == "network_require_hashes_lock",
        state.get("wheel_install_mode"),
    )
    check(
        "install_bundled_wheels_step_success",
        successful_setup_step("install_bundled_wheels"),
    )
    check(
        "install_locked_dependencies_step_success",
        successful_setup_step("install_locked_dependencies"),
    )
    check("pip_check_step_success", successful_setup_step("verify_dependencies"))
    check(
        "manifest_has_requirements_lock_sha256",
        isinstance(requirements_lock_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", requirements_lock_sha256) is not None,
        requirements_lock_sha256,
    )
    check(
        "manifest_has_dependency_lock_metadata_sha256",
        isinstance(dependency_lock_metadata_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", dependency_lock_metadata_sha256) is not None,
        dependency_lock_metadata_sha256,
    )
    check(
        "manifest_has_dependency_wheelhouse_manifest_sha256",
        isinstance(dependency_wheelhouse_manifest_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", dependency_wheelhouse_manifest_sha256)
        is not None,
        dependency_wheelhouse_manifest_sha256,
    )
    check(
        "bundled_requirements_lock_sha256_matches_manifest",
        requirements_lock is not None
        and isinstance(requirements_lock_sha256, str)
        and sha256_file(requirements_lock) == requirements_lock_sha256,
        {
            "path": str(requirements_lock) if requirements_lock is not None else None,
            "manifest_sha256": requirements_lock_sha256,
            "actual_sha256": sha256_file(requirements_lock)
            if requirements_lock is not None
            else None,
        },
    )
    check(
        "bundled_dependency_lock_metadata_sha256_matches_manifest",
        dependency_lock_metadata is not None
        and isinstance(dependency_lock_metadata_sha256, str)
        and sha256_file(dependency_lock_metadata) == dependency_lock_metadata_sha256,
        {
            "path": str(dependency_lock_metadata)
            if dependency_lock_metadata is not None
            else None,
            "manifest_sha256": dependency_lock_metadata_sha256,
            "actual_sha256": sha256_file(dependency_lock_metadata)
            if dependency_lock_metadata is not None
            else None,
        },
    )
    check(
        "bundled_dependency_wheelhouse_manifest_sha256_matches_manifest",
        dependency_wheelhouse_manifest is not None
        and isinstance(dependency_wheelhouse_manifest_sha256, str)
        and sha256_file(dependency_wheelhouse_manifest)
        == dependency_wheelhouse_manifest_sha256,
        {
            "path": str(dependency_wheelhouse_manifest)
            if dependency_wheelhouse_manifest is not None
            else None,
            "manifest_sha256": dependency_wheelhouse_manifest_sha256,
            "actual_sha256": sha256_file(dependency_wheelhouse_manifest)
            if dependency_wheelhouse_manifest is not None
            else None,
        },
    )
else:
    # The development-only no-lock route remains useful for local smoke tests,
    # but its evidence cannot satisfy the release importer contract below.
    check(
        "development_no_lock_wheel_install",
        state.get("wheel_install_mode") == "network_constraints_binary_only",
        state.get("wheel_install_mode"),
    )
native_input_scope = "build-input-before-copy-and-code-sign-v1"
check(
    "normalizer_input_digest_scope_explicit",
    manifest.get("normalizer_sha256") == manifest.get("normalizer_input_sha256")
    and manifest.get("normalizer_sha256_scope") == native_input_scope,
    {
        "legacy_alias": manifest.get("normalizer_sha256"),
        "input_sha256": manifest.get("normalizer_input_sha256"),
        "scope": manifest.get("normalizer_sha256_scope"),
    },
)
check(
    "dcm2niix_input_digest_scope_explicit",
    manifest.get("dcm2niix_sha256") == manifest.get("dcm2niix_input_sha256")
    and manifest.get("dcm2niix_sha256_scope") == native_input_scope,
    {
        "legacy_alias": manifest.get("dcm2niix_sha256"),
        "input_sha256": manifest.get("dcm2niix_input_sha256"),
        "scope": manifest.get("dcm2niix_sha256_scope"),
    },
)
for component, bundled_path in (
    ("normalizer", bundled_normalizer),
    ("dcm2niix", bundled_dcm2niix),
):
    bundled_field = f"{component}_bundled_sha256"
    if bundled_field in manifest:
        check(
            f"{bundled_field}_matches_bundled_bytes",
            bundled_path.is_file()
            and manifest.get(bundled_field) == sha256_file(bundled_path),
            {
                "manifest_sha256": manifest.get(bundled_field),
                "actual_sha256": sha256_file(bundled_path) if bundled_path.is_file() else None,
            },
        )
actual_app_version = manifest.get("app_version") or manifest.get("version")
effective_expected_app_version = expected_app_version or (
    actual_app_version if isinstance(actual_app_version, str) else ""
)
check(
    "manifest_app_version_matches_expected",
    isinstance(actual_app_version, str)
    and bool(effective_expected_app_version)
    and actual_app_version == effective_expected_app_version,
    {"expected": effective_expected_app_version or None, "actual": actual_app_version},
)
setup_manifest_sha256 = regular_file_sha256(manifest_path)
info_plist_sha256 = regular_file_sha256(info_plist_path)
release_identity_complete = (
    isinstance(actual_app_version, str)
    and bool(actual_app_version)
    and isinstance(manifest.get("build_id"), str)
    and bool(str(manifest.get("build_id")).strip())
    and isinstance(manifest.get("dependency_set_id"), str)
    and bool(str(manifest.get("dependency_set_id")).strip())
    and isinstance(setup_manifest_sha256, str)
    and isinstance(info_plist_sha256, str)
    and (dmg_identity_path is None or isinstance(dmg_identity_sha256, str))
)
check(
    "manifest_release_identity_complete",
    release_identity_complete,
    {
        "app_version": actual_app_version,
        "build_id": manifest.get("build_id"),
        "dependency_set_id": manifest.get("dependency_set_id"),
        "setup_manifest_sha256": setup_manifest_sha256,
        "info_plist_sha256": info_plist_sha256,
        "dmg_path": str(dmg_identity_path) if dmg_identity_path is not None else None,
        "dmg_sha256": dmg_identity_sha256,
    },
)
check("bundled_dcm2niix_exists", bundled_dcm2niix.exists() and os.access(bundled_dcm2niix, os.X_OK), str(bundled_dcm2niix))
check("manifest_license_apache_2_0", manifest.get("license", {}).get("expression") == "Apache-2.0", manifest.get("license"))
check("wrapper_license_exists", wrapper_license.exists(), str(wrapper_license))
check("wrapper_notice_exists", wrapper_notice.exists(), str(wrapper_notice))
check("totalsegmentator_license_exists", totalsegmentator_license.exists(), str(totalsegmentator_license))
check("dentalsegmentator_notice_exists", dentalsegmentator_notice.exists(), str(dentalsegmentator_notice))
check("toothseg_notice_exists", toothseg_notice.exists(), str(toothseg_notice))
check("dcm2niix_license_exists", dcm2niix_license.exists(), str(dcm2niix_license))
check("license_inventory_exists", license_inventory.exists(), str(license_inventory))
check(
    "license_inventory_unresolved_zero",
    license_inventory_payload.get("unresolved_count") == 0,
    license_inventory_payload.get("unresolved"),
)
license_surface_text = "\n".join(
    path.read_text(encoding="utf-8")
    for path in (
        wrapper_license,
        wrapper_notice,
        dentalsegmentator_notice,
        toothseg_notice,
        manifest_path,
        license_inventory,
    )
    if path.is_file()
)
check(
    "license_surfaces_no_old_first_party_markers",
    "LicenseRef-Proprietary" not in license_surface_text
    and "WrapperMac-Proprietary-License" not in license_surface_text,
)
check("sample1_input_exists", sample1_input.exists(), str(sample1_input))
check("sample1_surface_preview_exists", sample1_viewer.exists(), str(sample1_viewer))
check("sample1_manifest_exists", sample1_manifest.exists(), str(sample1_manifest))
check("sample1_notices_exists", sample1_notices.exists(), str(sample1_notices))
check(
    "sample1_manifest_non_clinical",
    sample1_manifest_payload.get("clinical_use") is False,
    sample1_manifest_payload.get("clinical_use"),
)

installed_bundle = state.get("installed_bundle", {})
current_bundle = {
    "schema": "totalsegmentator_wrapper_mac.installed_bundle.v1",
    "app_version": manifest.get("app_version") or manifest.get("version"),
    "build_id": manifest.get("build_id"),
    "dependency_set_id": manifest.get("dependency_set_id"),
    "wheel_sha256": manifest.get("wheel_sha256"),
    "fpsample_wheel_sha256": manifest.get("fpsample_wheel_sha256"),
    "acvl_utils_wheel_sha256": manifest.get("acvl_utils_wheel_sha256"),
    "constraints_sha256": manifest.get("constraints_sha256"),
    "requirements_lock_sha256": manifest.get("requirements_lock_sha256"),
    "dependency_lock_metadata_sha256": manifest.get("dependency_lock_metadata_sha256"),
    "dependency_wheelhouse_manifest_sha256": manifest.get(
        "dependency_wheelhouse_manifest_sha256"
    ),
    "normalizer_sha256": manifest.get("normalizer_sha256"),
    "dcm2niix_sha256": manifest.get("dcm2niix_sha256"),
    "sample1_manifest_sha256": manifest.get("sample1_manifest_sha256"),
    "setup_weights_manifest_sha256": manifest.get("setup_weights_manifest_sha256"),
    "update_manifest_url": manifest.get("update_manifest_url"),
}
check("setup_state_installed_bundle_current", installed_bundle == current_bundle, installed_bundle)
if release_requires_hashed_lock:
    check(
        "installed_requirements_lock_sha256_matches_manifest",
        installed_bundle.get("requirements_lock_sha256") == requirements_lock_sha256,
        {
            "installed": installed_bundle.get("requirements_lock_sha256"),
            "manifest": requirements_lock_sha256,
        },
    )
    check(
        "installed_dependency_lock_metadata_sha256_matches_manifest",
        installed_bundle.get("dependency_lock_metadata_sha256")
        == dependency_lock_metadata_sha256,
        {
            "installed": installed_bundle.get("dependency_lock_metadata_sha256"),
            "manifest": dependency_lock_metadata_sha256,
        },
    )
    check(
        "installed_dependency_wheelhouse_manifest_sha256_matches_manifest",
        installed_bundle.get("dependency_wheelhouse_manifest_sha256")
        == dependency_wheelhouse_manifest_sha256,
        {
            "installed": installed_bundle.get(
                "dependency_wheelhouse_manifest_sha256"
            ),
            "manifest": dependency_wheelhouse_manifest_sha256,
        },
    )

evidence = {
    "schema": EVIDENCE_SCHEMA,
    "passed": all(item["passed"] for item in checks),
    "run_id": run_id,
    "collected_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "home": str(home),
    "app_path": str(app_path),
    "support_dir": str(support_dir),
    "state_json": str(state_json),
    "manifest_path": str(manifest_path),
    "shared_copy_path": str(shared_evidence_json),
    "expected_app_version": effective_expected_app_version or None,
    "app_identity": {
        "app_version": actual_app_version,
        "build_id": manifest.get("build_id"),
        "dependency_set_id": manifest.get("dependency_set_id"),
        "setup_manifest_sha256": setup_manifest_sha256,
        "info_plist_sha256": info_plist_sha256,
        "dmg_path": str(dmg_identity_path) if dmg_identity_path is not None else None,
        "dmg_sha256": dmg_identity_sha256,
    },
    "checks": checks,
}
write_json_atomically(evidence_json, evidence)
print(json.dumps(evidence, indent=2, ensure_ascii=False))
raise SystemExit(0 if evidence["passed"] else 1)
PY
PYTHON_STATUS=$?
set -e

CURRENT_EVIDENCE=0
if [[ -f "${EVIDENCE_JSON}" ]]; then
  if "${VENV_PYTHON}" - "${EVIDENCE_JSON}" "${RUN_ID}" <<'PY'
import json
import os
import stat
import sys
from pathlib import Path


def reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


path = Path(sys.argv[1])
run_id = sys.argv[2]
metadata = path.lstat()
if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
    raise SystemExit(1)
nofollow = getattr(os, "O_NOFOLLOW", None)
if nofollow is None:
    raise SystemExit(1)
flags = os.O_RDONLY | nofollow
descriptor = os.open(path, flags)
try:
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_dev != metadata.st_dev
        or opened.st_ino != metadata.st_ino
        or opened.st_size != metadata.st_size
        or opened.st_mtime_ns != metadata.st_mtime_ns
        or opened.st_ctime_ns != metadata.st_ctime_ns
        or opened.st_size > 2 * 1024 * 1024
    ):
        raise SystemExit(1)
    chunks = []
    received = 0
    while chunk := os.read(descriptor, 64 * 1024):
        received += len(chunk)
        if received > 2 * 1024 * 1024:
            raise SystemExit(1)
        chunks.append(chunk)
    after = os.fstat(descriptor)
    if (
        received != opened.st_size
        or not stat.S_ISREG(after.st_mode)
        or after.st_dev != opened.st_dev
        or after.st_ino != opened.st_ino
        or after.st_size != opened.st_size
        or after.st_mtime_ns != opened.st_mtime_ns
        or after.st_ctime_ns != opened.st_ctime_ns
    ):
        raise SystemExit(1)
    payload = json.loads(
        b"".join(chunks).decode("utf-8"),
        object_pairs_hook=reject_duplicate_json_keys,
    )
finally:
    os.close(descriptor)
if (
    not isinstance(payload, dict)
    or payload.get("schema") != "totalsegmentator_wrapper_mac.test_account_install_evidence.v2"
    or payload.get("run_id") != run_id
    or type(payload.get("passed")) is not bool
):
    raise SystemExit(1)
PY
  then
    CURRENT_EVIDENCE=1
  fi
fi

SHARED_COPY_STATUS=not-attempted
if [[ "${CURRENT_EVIDENCE}" -ne 1 ]]; then
  if publish_preflight_failure "collector_failed_to_publish_current_evidence"; then
    CURRENT_EVIDENCE=1
    SHARED_COPY_STATUS=written
  else
    SHARED_COPY_STATUS=failed
  fi
fi
if [[ "${CURRENT_EVIDENCE}" -eq 1 && "${SHARED_COPY_STATUS}" != "written" ]]; then
  if mkdir -p "${SHARED_EVIDENCE_DIR}"; then
    if "${VENV_PYTHON}" - "${EVIDENCE_JSON}" "${SHARED_EVIDENCE_JSON}" "${RUN_ID}" <<'PY'
import json
import os
import secrets
import stat
import sys
from pathlib import Path


def reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


source = Path(sys.argv[1])
target = Path(sys.argv[2])
run_id = sys.argv[3]
source_metadata = source.lstat()
target_parent_metadata = target.parent.lstat()
if (
    stat.S_ISLNK(source_metadata.st_mode)
    or not stat.S_ISREG(source_metadata.st_mode)
    or stat.S_ISLNK(target_parent_metadata.st_mode)
    or not stat.S_ISDIR(target_parent_metadata.st_mode)
):
    raise SystemExit(1)
nofollow = getattr(os, "O_NOFOLLOW", None)
if nofollow is None:
    raise SystemExit(1)
source_descriptor = os.open(source, os.O_RDONLY | nofollow)
try:
    opened = os.fstat(source_descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_dev != source_metadata.st_dev
        or opened.st_ino != source_metadata.st_ino
        or opened.st_size != source_metadata.st_size
        or opened.st_mtime_ns != source_metadata.st_mtime_ns
        or opened.st_ctime_ns != source_metadata.st_ctime_ns
        or opened.st_size > 2 * 1024 * 1024
    ):
        raise SystemExit(1)
    chunks = []
    received = 0
    while chunk := os.read(source_descriptor, 64 * 1024):
        received += len(chunk)
        if received > 2 * 1024 * 1024:
            raise SystemExit(1)
        chunks.append(chunk)
    rendered = b"".join(chunks)
    after = os.fstat(source_descriptor)
    if (
        received != opened.st_size
        or not stat.S_ISREG(after.st_mode)
        or after.st_dev != opened.st_dev
        or after.st_ino != opened.st_ino
        or after.st_size != opened.st_size
        or after.st_mtime_ns != opened.st_mtime_ns
        or after.st_ctime_ns != opened.st_ctime_ns
    ):
        raise SystemExit(1)
finally:
    os.close(source_descriptor)
payload = json.loads(
    rendered.decode("utf-8"),
    object_pairs_hook=reject_duplicate_json_keys,
)
if (
    not isinstance(payload, dict)
    or payload.get("schema") != "totalsegmentator_wrapper_mac.test_account_install_evidence.v2"
    or payload.get("run_id") != run_id
    or type(payload.get("passed")) is not bool
):
    raise SystemExit(1)
temporary = target.with_name(f".{target.name}.{run_id}.{secrets.token_hex(8)}.tmp")
descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    offset = 0
    while offset < len(rendered):
        offset += os.write(descriptor, rendered[offset:])
    os.fsync(descriptor)
finally:
    os.close(descriptor)
os.replace(temporary, target)
PY
    then
      SHARED_COPY_STATUS=written
    else
      SHARED_COPY_STATUS=failed
    fi
  else
    SHARED_COPY_STATUS=failed
  fi
fi

if [[ "${CURRENT_EVIDENCE}" -eq 1 ]]; then
  echo "今回のテスト用アカウントinstall evidenceを書き出しました:"
  echo "${EVIDENCE_JSON}"
else
  echo "今回のrun IDに対応するfinal evidenceは書き出されませんでした。以前の証跡はsupersededとして退避済みです:" >&2
  echo "${EVIDENCE_JSON}.superseded-${RUN_ID}" >&2
fi
if [[ "${SHARED_COPY_STATUS}" == "written" ]]; then
  echo "今回の共有受け渡し用コピーを書き出しました:"
  echo "${SHARED_EVIDENCE_JSON}"
else
  echo "共有受け渡し用コピーは書き出されませんでした (${SHARED_COPY_STATUS}):" >&2
  echo "${SHARED_EVIDENCE_JSON}" >&2
fi
exit "${PYTHON_STATUS}"
