#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


APP_SUPPORT_NAME = "TotalSegmentatorWrapperMac"
SETUP_STEP_LABELS = {
    "idle": "待機中",
    "create_app_support_dirs": "保存先準備",
    "validate_python_312": "Python確認",
    "create_venv": "専用環境作成",
    "bootstrap_install": "アプリ本体導入",
    "sync_bundle": "アプリ更新反映",
    "install_wheel": "依存パッケージ取得",
    "configure_totalseg_privacy": "プライバシー設定",
    "doctor": "MPS確認",
    "complete": "起動準備完了",
    "setup_exception": "エラー",
}
SETUP_STEP_HINTS = {
    "idle": "セットアップ開始を押してください。",
    "create_app_support_dirs": "App Support配下に専用ディレクトリを準備しています。",
    "validate_python_312": "同梱Python 3.12を確認しています。",
    "create_venv": "このアプリ専用のPython環境を作成しています。",
    "bootstrap_install": "セットアップ管理用のアプリ本体を専用環境へ導入しています。",
    "sync_bundle": "同梱アプリ更新を専用環境へ反映しています。",
    "install_wheel": "依存パッケージを取得中です。数分かかることがあります。",
    "configure_totalseg_privacy": "利用状況データの送信を止めています。",
    "doctor": "PyTorch MPSとCT確認用部品を確認しています。",
    "complete": "起動準備が完了しました。",
    "setup_exception": "セットアップ中にエラーが発生しました。",
}
SETUP_REASON_MESSAGES = {
    "python312_missing": "同梱Python 3.12が見つかりません。",
    "python_version_unsupported": "Python 3.12以外ではセットアップできません。",
    "constraints_missing": "依存固定ファイルが見つかりません。",
    "wheel_missing": "同梱アプリパッケージが見つかりません。",
    "needs_network": "ネットワーク接続が必要です。",
    "runtime_install_failed": "依存パッケージの導入に失敗しました。",
    "mps_unavailable": "MPS確認に失敗しました。",
    "normalizer_missing": "CT確認用部品の確認に失敗しました。",
    "bundle_manifest_invalid": "アプリ同梱manifestを読めません。",
    "setup_exception": "セットアップ中にエラーが発生しました。",
}


def app_support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / APP_SUPPORT_NAME


def bundle_resources_dir() -> Path:
    configured = os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_RESOURCES_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    executable = Path(sys.argv[0]).resolve()
    return executable.parents[1] / "Resources"


def latest_wheel(resources: Path) -> Path:
    wheels = sorted((resources / "wheels").glob("totalsegmentator_wrapper_mac-*.whl"))
    if not wheels:
        raise FileNotFoundError("bundled totalsegmentator_wrapper_mac wheel not found")
    return wheels[-1]


def bundled_constraints(resources: Path) -> Path:
    constraints = resources / "constraints" / "macos-arm64-py312.txt"
    if not constraints.exists():
        raise FileNotFoundError("bundled macOS Python 3.12 constraints file not found")
    return constraints


def bundled_normalizer(resources: Path) -> Path:
    return resources / "bin" / "totalsegmentator-wrapper-dicom-normalizer"


def bundled_dcm2niix(resources: Path) -> Path:
    return resources / "bin" / "dcm2niix"


def bundled_demo_viewer(resources: Path) -> Path:
    return resources / "sample1" / "surface_preview" / "index.html"


def read_setup_manifest(resources: Path) -> dict:
    manifest_path = setup_manifest_path(resources)
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def setup_manifest_path(resources: Path) -> Path:
    return resources / "setup_manifest.json"


def venv_python(app_support: Path) -> Path:
    return app_support / "env" / "bin" / "python"


def build_create_venv_command(host_python: Path, app_support: Path) -> list[str]:
    return [str(host_python), "-m", "venv", str(app_support / "env")]


def build_bootstrap_install_command(python: Path, wheel: Path) -> list[str]:
    return [str(python), "-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel)]


def build_resync_wheel_command(python: Path, wheel: Path) -> list[str]:
    return [str(python), "-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel)]


def build_setup_command(
    python: Path,
    wheel: Path,
    setup_json: Path,
    *,
    python312: Path,
    constraints: Path,
    allow_network: bool,
    skip_mps_check: bool = False,
    progress_log: Path | None = None,
    bundle_manifest: Path | None = None,
) -> list[str]:
    command = [
        str(python),
        "-m",
        "totalsegmentator_wrapper_mac",
        "setup",
        "--python",
        str(python312),
        "--wheel",
        str(wheel),
        "--constraints",
        str(constraints),
        "--json",
        str(setup_json),
        "--use-existing-env",
    ]
    if bundle_manifest is not None:
        command.extend(["--bundle-manifest", str(bundle_manifest)])
    if progress_log is not None:
        command.extend(["--progress-log", str(progress_log)])
    if allow_network:
        command.append("--allow-network")
    if skip_mps_check:
        command.append("--skip-mps-check")
    return command


def build_ui_command(python: Path) -> list[str]:
    return [str(python), "-m", "totalsegmentator_wrapper_mac.ui_tk"]


def setup_state_is_successful(state_json: Path) -> bool:
    payload = read_setup_state(state_json)
    if payload is None:
        return False
    return payload.get("status") == "success"


def read_setup_state(state_json: Path) -> dict | None:
    if not state_json.exists():
        return None
    try:
        return json.loads(state_json.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_setup_state(state_json: Path, payload: dict) -> None:
    state_json.parent.mkdir(parents=True, exist_ok=True)
    state_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_setup_failure_reason(state_json: Path) -> str | None:
    payload = read_setup_state(state_json)
    if payload is None:
        return None
    return payload.get("reason")


def current_bundle_record(resources: Path) -> dict:
    manifest = read_setup_manifest(resources)
    return {
        "schema": "totalsegmentator_wrapper_mac.installed_bundle.v1",
        "app_version": manifest.get("app_version") or manifest.get("version"),
        "build_id": manifest.get("build_id"),
        "dependency_set_id": manifest.get("dependency_set_id"),
        "wheel_sha256": manifest.get("wheel_sha256"),
        "constraints_sha256": manifest.get("constraints_sha256"),
        "normalizer_sha256": manifest.get("normalizer_sha256"),
        "dcm2niix_sha256": manifest.get("dcm2niix_sha256"),
        "sample1_manifest_sha256": manifest.get("sample1_manifest_sha256"),
        "update_manifest_url": manifest.get("update_manifest_url"),
    }


def installed_wheel_marker_path(support: Path) -> Path:
    return support / "installed_wheel_sha256.txt"


def installed_wheel_marker_matches(support: Path, resources: Path) -> bool:
    expected = current_bundle_record(resources).get("wheel_sha256")
    if not expected:
        return False
    marker = installed_wheel_marker_path(support)
    if not marker.exists():
        return False
    return marker.read_text(encoding="utf-8").strip() == expected


def bundle_sync_status(state_json: Path, resources: Path) -> dict:
    state = read_setup_state(state_json)
    if state is None or state.get("status") != "success":
        return {"action": "setup_required", "reason": "setup_missing"}
    support = state_json.parent
    installed = state.get("installed_bundle")
    current = current_bundle_record(resources)
    if not installed:
        return {"action": "resync_wheel", "reason": "legacy_setup_state", "current": current}
    if installed == current and installed_wheel_marker_matches(support, resources):
        return {"action": "current", "reason": "current", "current": current}
    if installed == current:
        return {"action": "resync_wheel", "reason": "wheel_marker_missing_or_stale", "current": current}
    for key in ("dependency_set_id", "constraints_sha256"):
        if installed.get(key) != current.get(key):
            return {"action": "setup_required", "reason": f"{key}_changed", "current": current}
    if installed.get("wheel_sha256") != current.get("wheel_sha256"):
        return {"action": "resync_wheel", "reason": "wheel_changed", "current": current}
    return {"action": "mark_current", "reason": "resource_only_change", "current": current}


def setup_state_is_current(state_json: Path, resources: Path) -> bool:
    return bundle_sync_status(state_json, resources).get("action") == "current"


def mark_bundle_current(state_json: Path, resources: Path, *, reason: str) -> None:
    state = read_setup_state(state_json) or {}
    state["installed_bundle"] = current_bundle_record(resources)
    state["last_bundle_resync"] = {
        "reason": reason,
        "status": "state_updated",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_setup_state(state_json, state)
    wheel_sha256 = state["installed_bundle"].get("wheel_sha256")
    if wheel_sha256:
        marker = installed_wheel_marker_path(state_json.parent)
        marker.write_text(str(wheel_sha256) + "\n", encoding="utf-8")


def build_launch_environment(resources: Path, support: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    bundled_python_root = resources / "python" / "cpython-3.12"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TOTALSEGMENTATOR_WRAPPER_MAC_APP_SUPPORT"] = str(support)
    env["XDG_CACHE_HOME"] = str(support / "cache")
    env["PYTHONPYCACHEPREFIX"] = str(support / "cache" / "pycache")
    env["PIP_CACHE_DIR"] = str(support / "cache" / "pip")
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["MPLCONFIGDIR"] = str(support / "cache" / "matplotlib")
    env["TOTALSEG_HOME_DIR"] = str(support / "models" / "totalsegmentator")
    env["TOTALSEG_WEIGHTS_PATH"] = str(support / "models" / "totalsegmentator" / "weights")
    tcl_library = bundled_python_root / "lib" / "tcl8.6"
    tk_library = bundled_python_root / "lib" / "tk8.6"
    if tcl_library.exists():
        env["TCL_LIBRARY"] = str(tcl_library)
    if tk_library.exists():
        env["TK_LIBRARY"] = str(tk_library)
    normalizer = bundled_normalizer(resources)
    if normalizer.exists():
        env["TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER"] = str(normalizer)
    dcm2niix = bundled_dcm2niix(resources)
    if dcm2niix.exists():
        env["TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX"] = str(dcm2niix)
    return env


def setup_reason_to_japanese(reason: str | None) -> str:
    if not reason:
        return "原因は記録されていません。"
    return SETUP_REASON_MESSAGES.get(reason, f"未対応のエラーです: {reason}")


def setup_step_to_japanese(step: str | None) -> str:
    if not step:
        return SETUP_STEP_LABELS["idle"]
    return SETUP_STEP_LABELS.get(step, step)


def setup_hint_for_step(step: str | None) -> str:
    if not step:
        return SETUP_STEP_HINTS["idle"]
    return SETUP_STEP_HINTS.get(step, "セットアップを実行しています。")


def format_elapsed(elapsed_seconds: float) -> str:
    seconds = max(0, int(elapsed_seconds))
    minutes, remaining = divmod(seconds, 60)
    if minutes:
        return f"経過時間: {minutes}分{remaining:02d}秒"
    return f"経過時間: {remaining}秒"


def read_new_log_text(path: Path, position: int) -> tuple[str, int]:
    if not path.exists():
        return "", position
    size = path.stat().st_size
    if size < position:
        position = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(position)
        text = handle.read()
        return text, handle.tell()


def progress_step_from_log_line(line: str) -> str | None:
    if not line.startswith("SETUP_PROGRESS "):
        return None
    for part in line.split():
        if part.startswith("step="):
            return part.removeprefix("step=")
    return None


def display_log_line(line: str) -> str:
    if line.startswith("SETUP_PROGRESS ") and " message=" in line:
        return line.split(" message=", 1)[1]
    return line


def resolve_python312(resources: Path, env: dict[str, str] | None = None) -> tuple[Path | None, str]:
    source_env = env or os.environ
    configured = source_env.get("TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312")
    if configured:
        return Path(configured).expanduser().resolve(), "env"
    manifest = read_setup_manifest(resources)
    runtime = manifest.get("python_runtime", {})
    manifest_python = runtime.get("python_executable") or manifest.get("python_executable")
    if manifest_python:
        candidate = Path(manifest_python).expanduser()
        if not candidate.is_absolute():
            candidate = resources / candidate
        return candidate.resolve(), "manifest"
    return None, "missing"


def inspect_python312(python: Path) -> dict:
    command = [
        str(python),
        "-c",
        (
            "import json, sys; "
            "print(json.dumps({'version': sys.version.split()[0], "
            "'major': sys.version_info.major, 'minor': sys.version_info.minor}))"
        ),
    ]
    if not python.exists():
        return {"status": "failed", "reason": "python312_missing", "command": command}
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(  # noqa: S603
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        return {"status": "failed", "reason": "python312_missing", "command": command, "stderr": proc.stderr}
    try:
        payload = json.loads(proc.stdout)
    except Exception as exc:
        return {"status": "failed", "reason": "python_version_unsupported", "command": command, "error": repr(exc)}
    if payload.get("major") != 3 or payload.get("minor") != 12:
        return {
            "status": "failed",
            "reason": "python_version_unsupported",
            "command": command,
            "version": payload.get("version"),
        }
    payload["status"] = "success"
    payload["reason"] = None
    payload["command"] = command
    return payload


def write_setup_failure_state(
    *,
    support: Path,
    reason: str,
    details: dict,
    allow_network: bool,
) -> None:
    support.mkdir(parents=True, exist_ok=True)
    (support / "logs").mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "totalsegmentator_wrapper_mac.setup_state.v1",
        "status": "failed",
        "reason": reason,
        "paths": {
            "app_support": str(support),
            "env_dir": str(support / "env"),
            "logs_dir": str(support / "logs"),
            "cache_dir": str(support / "cache"),
        },
        "allow_network": allow_network,
        "python_executable": str(details.get("python_executable")) if details.get("python_executable") else None,
        "python_version": details.get("version"),
        "steps": [
            {
                "name": "validate_python_312",
                "status": "failed",
                "command": details.get("command", []),
                "error": details.get("reason"),
            }
        ],
    }
    (support / "setup_state.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def tail_file(path: Path, *, lines: int = 24) -> str:
    if not path.exists():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def run_logged(command: list[str], log_path: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("$ " + " ".join(command) + "\n")
        log.flush()
        proc = subprocess.run(  # noqa: S603
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            check=False,
        )
        log.write(f"returncode={proc.returncode}\n")
    return proc


def write_launcher_progress(log_path: Path, step: str, status: str, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"SETUP_PROGRESS step={step} status={status} message={message}\n")
        log.flush()


def run_setup_sequence(
    *,
    support: Path,
    wheel: Path,
    python312: Path | None,
    constraints: Path,
    resources: Path,
    allow_network: bool,
    skip_mps_check: bool,
    log_path: Path,
) -> int:
    python = venv_python(support)
    setup_json = support / "logs" / "setup_result.json"
    support.mkdir(parents=True, exist_ok=True)
    (support / "cache" / "pip").mkdir(parents=True, exist_ok=True)
    (support / "cache" / "pycache").mkdir(parents=True, exist_ok=True)
    env = build_launch_environment(resources, support)
    if python312 is None:
        write_launcher_progress(log_path, "validate_python_312", "failed", "同梱Python 3.12が見つかりません。")
        write_setup_failure_state(
            support=support,
            reason="python312_missing",
            details={"reason": "TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312 or manifest python_executable is required."},
            allow_network=allow_network,
        )
        return 2
    write_launcher_progress(log_path, "validate_python_312", "running", "Python 3.12を確認しています。")
    py_info = inspect_python312(python312)
    py_info["python_executable"] = str(python312)
    if py_info.get("status") != "success":
        write_setup_failure_state(
            support=support,
            reason=str(py_info.get("reason") or "python_version_unsupported"),
            details=py_info,
            allow_network=allow_network,
        )
        return 2
    write_launcher_progress(log_path, "validate_python_312", "success", "Python 3.12を確認しました。")
    if not python.exists():
        write_launcher_progress(log_path, "create_venv", "running", "専用Python環境を作成しています。")
        proc = run_logged(build_create_venv_command(python312, support), log_path, env=env)
        if proc.returncode != 0:
            write_launcher_progress(log_path, "create_venv", "failed", "専用Python環境の作成に失敗しました。")
            return proc.returncode
        write_launcher_progress(log_path, "create_venv", "success", "専用Python環境を作成しました。")
    else:
        write_launcher_progress(log_path, "create_venv", "skipped", "既存の専用Python環境を再利用します。")
    write_launcher_progress(log_path, "bootstrap_install", "running", "アプリ本体を専用環境へ導入しています。")
    proc = run_logged(build_bootstrap_install_command(python, wheel), log_path, env=env)
    if proc.returncode != 0:
        write_launcher_progress(log_path, "bootstrap_install", "failed", "アプリ本体の導入に失敗しました。")
        return proc.returncode
    write_launcher_progress(log_path, "bootstrap_install", "success", "アプリ本体の導入が完了しました。")
    write_launcher_progress(log_path, "install_wheel", "running", "依存パッケージを取得中です。数分かかることがあります。")
    setup_env = dict(env)
    setup_env["TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_SUPPRESS_STDOUT_JSON"] = "1"
    proc = run_logged(
        build_setup_command(
            python,
            wheel,
            setup_json,
            python312=python312,
            constraints=constraints,
            allow_network=allow_network,
            skip_mps_check=skip_mps_check,
            progress_log=log_path,
            bundle_manifest=setup_manifest_path(resources),
        ),
        log_path,
        env=setup_env,
    )
    if proc.returncode == 0:
        mark_bundle_current(support / "setup_state.json", resources, reason="setup_success")
        write_launcher_progress(log_path, "complete", "success", "起動準備が完了しました。")
    else:
        write_launcher_progress(log_path, "setup_exception", "failed", "セットアップが停止しました。")
    return proc.returncode


def resync_installed_bundle(*, support: Path, wheel: Path, resources: Path, log_path: Path) -> int:
    python = venv_python(support)
    if not python.exists():
        return 2
    write_launcher_progress(log_path, "sync_bundle", "running", "同梱アプリ更新を専用環境へ反映しています。")
    proc = run_logged(
        build_resync_wheel_command(python, wheel),
        log_path,
        env=build_launch_environment(resources, support),
    )
    if proc.returncode != 0:
        write_launcher_progress(log_path, "sync_bundle", "failed", "同梱アプリ更新の反映に失敗しました。")
        return proc.returncode
    mark_bundle_current(support / "setup_state.json", resources, reason="wheel_resync")
    write_launcher_progress(log_path, "sync_bundle", "success", "同梱アプリ更新を反映しました。")
    return 0


def launch_ui(python: Path, *, resources: Path, support: Path) -> None:
    subprocess.Popen(build_ui_command(python), env=build_launch_environment(resources, support))  # noqa: S603


def open_demo_viewer(resources: Path, log_path: Path) -> tuple[bool, str]:
    demo_html = bundled_demo_viewer(resources)
    if not demo_html.exists():
        return False, "3Dサンプルviewerが見つかりません。"
    command = ["open", str(demo_html)]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("$ " + " ".join(command) + "\n")
        log.flush()
    try:
        subprocess.Popen(command)  # noqa: S603
    except OSError as exc:
        return False, f"3Dサンプルviewerを開けませんでした: {exc}"
    return True, "Sample 1の3Dサンプルviewerをブラウザで開きました。"


def exec_ui(python: Path, *, resources: Path, support: Path, log_path: Path) -> int:
    command = build_ui_command(python)
    env = build_launch_environment(resources, support)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("$ " + " ".join(command) + "\n")
        log.flush()
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        try:
            os.execve(str(python), command, env)
        except OSError as exc:
            log.write(f"UI exec failed: {exc!r}\n")
            log.flush()
            return 127
    return 127


def show_setup_window(
    *,
    support: Path,
    wheel: Path,
    python312: Path | None,
    python_source: str,
    constraints: Path,
    resources: Path,
    allow_network: bool,
    skip_mps_check: bool,
    setup_context_message: str | None = None,
) -> int:
    import tkinter as tk
    from tkinter import ttk

    log_path = support / "logs" / "launcher.log"
    python = venv_python(support)
    should_launch_ui = {"value": False}
    root = tk.Tk()
    root.title("TotalSegmentator Wrapper for Mac セットアップ")
    root.minsize(680, 540)

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="TotalSegmentator Wrapper for Mac セットアップ").pack(anchor="w")
    ttk.Label(
        frame,
        text=(
            "管理者権限は不要です。セットアップは ~/Library/Application Support/TotalSegmentatorWrapperMac/ "
            "配下にだけ専用環境を書き込み、system Python や Homebrew は変更しません。"
        ),
        wraplength=620,
    ).pack(anchor="w", pady=(6, 4))
    ttk.Label(
        frame,
        text=(
            "DICOM/CT/処理結果は送信しません。初回Setupまたは明示的な依存更新時のみ、"
            "依存パッケージやモデル準備のためにネットワークを使用します。"
        ),
        wraplength=620,
    ).pack(anchor="w", pady=(0, 12))
    if setup_context_message:
        ttk.Label(frame, text=setup_context_message, wraplength=620).pack(anchor="w", pady=(0, 12))
    ttk.Label(
        frame,
        text="セットアップ中も、Sample 1の3Dプレビューをブラウザで操作できます。",
        wraplength=620,
    ).pack(anchor="w", pady=(0, 12))

    current_step_var = tk.StringVar(value=setup_step_to_japanese("idle"))
    elapsed_var = tk.StringVar(value=format_elapsed(0))
    hint_var = tk.StringVar(value=setup_hint_for_step("idle"))

    status_row = ttk.Frame(frame)
    status_row.pack(fill="x", pady=(0, 6))
    ttk.Label(status_row, text="現在の処理:").pack(side="left")
    ttk.Label(status_row, textvariable=current_step_var).pack(side="left", padx=(6, 16))
    ttk.Label(status_row, textvariable=elapsed_var).pack(side="right")

    progress = ttk.Progressbar(frame, mode="indeterminate")
    progress.pack(fill="x", pady=(0, 6))
    ttk.Label(frame, textvariable=hint_var, wraplength=620).pack(anchor="w", pady=(0, 10))

    text = tk.Text(frame, height=12, wrap="word")
    text.pack(fill="both", expand=True)
    text.configure(state="disabled")
    button_row = ttk.Frame(frame)
    button_row.pack(fill="x", pady=(10, 0))
    setup_button = ttk.Button(button_row, text="セットアップ開始")
    setup_button.pack(side="left")
    demo_button = ttk.Button(button_row, text="3Dサンプルを開く")
    demo_button.pack(side="left", padx=(8, 0))
    quit_button = ttk.Button(button_row, text="終了", command=root.destroy)
    quit_button.pack(side="right")

    ui_state = {
        "running": False,
        "started_at": 0.0,
        "log_position": log_path.stat().st_size if log_path.exists() else 0,
        "shown_lines": set(),
    }

    def append(message: str) -> None:
        if not message:
            return
        text.configure(state="normal")
        text.insert("end", message + "\n")
        text.see("end")
        text.configure(state="disabled")

    def set_step(step: str | None) -> None:
        current_step_var.set(setup_step_to_japanese(step))
        hint_var.set(setup_hint_for_step(step))

    def append_log_line(line: str) -> None:
        if not line or line in ui_state["shown_lines"]:
            return
        ui_state["shown_lines"].add(line)
        step = progress_step_from_log_line(line)
        if step:
            set_step(step)
        append(display_log_line(line))

    def poll_setup_log() -> None:
        if ui_state["running"]:
            elapsed = time.monotonic() - float(ui_state["started_at"])
            elapsed_var.set(format_elapsed(elapsed))
        new_text, new_position = read_new_log_text(log_path, int(ui_state["log_position"]))
        ui_state["log_position"] = new_position
        for line in new_text.splitlines():
            append_log_line(line)
        if ui_state["running"]:
            root.after(1000, poll_setup_log)

    def finish_success() -> None:
        ui_state["running"] = False
        progress.stop()
        set_step("complete")
        elapsed = time.monotonic() - float(ui_state["started_at"])
        elapsed_var.set(format_elapsed(elapsed))
        append("セットアップが完了しました。アプリを起動します。")
        should_launch_ui["value"] = True
        root.after(700, root.destroy)

    def finish_failure(rc: int) -> None:
        ui_state["running"] = False
        progress.stop()
        setup_button.configure(state="normal")
        reason = read_setup_failure_reason(support / "setup_state.json")
        message = setup_reason_to_japanese(reason)
        set_step("setup_exception")
        elapsed = time.monotonic() - float(ui_state["started_at"])
        elapsed_var.set(format_elapsed(elapsed))
        append(f"セットアップが停止しました: {message}")
        append(f"終了コード: {rc}")
        append(f"ログ: {log_path}")
        tail = tail_file(log_path)
        if tail:
            append("直近のログ:")
            for line in tail.splitlines():
                append_log_line(line)

    def worker() -> None:
        rc = run_setup_sequence(
            support=support,
            wheel=wheel,
            python312=python312,
            constraints=constraints,
            resources=resources,
            allow_network=allow_network,
            skip_mps_check=skip_mps_check,
            log_path=log_path,
        )
        if rc == 0:
            root.after(0, finish_success)
        else:
            root.after(0, finish_failure, rc)

    def start_setup() -> None:
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.configure(state="disabled")
        ui_state["shown_lines"] = set()
        ui_state["running"] = True
        ui_state["started_at"] = time.monotonic()
        ui_state["log_position"] = log_path.stat().st_size if log_path.exists() else 0
        setup_button.configure(state="disabled")
        elapsed_var.set(format_elapsed(0))
        set_step("validate_python_312")
        progress.start(12)
        append("セットアップを開始します。")
        append(f"Python 3.12: {python312 if python312 else '未設定'} ({python_source})")
        poll_setup_log()
        threading.Thread(target=worker, daemon=True).start()

    def open_demo_from_setup() -> None:
        ok, message = open_demo_viewer(resources, log_path)
        append(message)
        if not ok:
            append(f"ログ: {log_path}")

    setup_button.configure(command=start_setup)
    demo_button.configure(command=open_demo_from_setup)
    if not bundled_demo_viewer(resources).exists():
        demo_button.configure(state="disabled")
    append(f"専用環境の保存先: {support}")
    append("管理者権限は不要です。App Support配下のみ書き込みます。")
    append("DICOM/CT/処理結果は送信しません。")
    append(
        "初回Setupまたは明示的な依存更新時のみ、依存パッケージやモデル準備の取得にネットワークを使用します。"
        if allow_network
        else "オフラインモードです。必要な依存が未取得の場合は停止します。"
    )
    if setup_context_message:
        append(setup_context_message)
    if skip_mps_check:
        append("検証用設定のため、MPS確認をスキップします。")
    if python_source == "missing":
        append("このalphaでは同梱Python 3.12が必要です。")
    elif python_source == "manifest":
        append("Python 3.12はアプリのmanifestで指定されています。")
    else:
        append("Python 3.12はTOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312で指定されています。")
    root.mainloop()
    if should_launch_ui["value"]:
        return exec_ui(python, resources=resources, support=support, log_path=log_path)
    return 0


def main() -> int:
    resources = bundle_resources_dir()
    support = app_support_dir()
    state_json = support / "setup_state.json"
    log_path = support / "logs" / "launcher.log"
    wheel = latest_wheel(resources)
    constraints = bundled_constraints(resources)
    python312, python_source = resolve_python312(resources)
    python = venv_python(support)
    allow_network = os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_OFFLINE") != "1"
    skip_mps_check = os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_SKIP_MPS_CHECK") == "1"

    if not setup_state_is_successful(state_json):
        if os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_HEADLESS") == "1":
            return run_setup_sequence(
                support=support,
                wheel=wheel,
                python312=python312,
                constraints=constraints,
                resources=resources,
                allow_network=allow_network,
                skip_mps_check=skip_mps_check,
                log_path=log_path,
            )
        return show_setup_window(
            support=support,
            wheel=wheel,
            python312=python312,
            python_source=python_source,
            constraints=constraints,
            resources=resources,
            allow_network=allow_network,
            skip_mps_check=skip_mps_check,
        )

    sync = bundle_sync_status(state_json, resources)
    if sync["action"] == "resync_wheel":
        rc = resync_installed_bundle(support=support, wheel=wheel, resources=resources, log_path=log_path)
        if rc != 0:
            if os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_HEADLESS") == "1":
                return rc
            return show_setup_window(
                support=support,
                wheel=wheel,
                python312=python312,
                python_source=python_source,
                constraints=constraints,
                resources=resources,
                allow_network=allow_network,
                skip_mps_check=skip_mps_check,
                setup_context_message=(
                    "同梱アプリ更新の反映に失敗しました。セットアップ開始を押すと、"
                    "専用環境を確認して復旧を試みます。"
                ),
            )
    elif sync["action"] == "mark_current":
        mark_bundle_current(state_json, resources, reason=str(sync.get("reason") or "resource_only_change"))
    elif sync["action"] == "setup_required":
        write_launcher_progress(log_path, "sync_bundle", "running", f"依存更新が必要です: {sync.get('reason')}")
        if os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_HEADLESS") == "1":
            return run_setup_sequence(
                support=support,
                wheel=wheel,
                python312=python312,
                constraints=constraints,
                resources=resources,
                allow_network=allow_network,
                skip_mps_check=skip_mps_check,
                log_path=log_path,
            )
        return show_setup_window(
            support=support,
            wheel=wheel,
            python312=python312,
            python_source=python_source,
            constraints=constraints,
            resources=resources,
            allow_network=allow_network,
            skip_mps_check=skip_mps_check,
            setup_context_message=(
                f"アプリ更新により依存更新が必要です（{sync.get('reason')}）。"
                "セットアップ開始を押すまで通信しません。"
            ),
        )

    return exec_ui(python, resources=resources, support=support, log_path=log_path)


if __name__ == "__main__":
    raise SystemExit(main())
