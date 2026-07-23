from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from totalsegmentator_wrapper_mac.dicom_normalizer_bridge import inspect_dicom_normalizer


APP_SUPPORT_NAME = "TotalSegmentatorWrapperMac"
SETUP_STATE_FILENAME = "setup_state.json"
FORBIDDEN_COMMAND_PARTS = {
    "sudo",
    "brew",
    "port",
}
FORBIDDEN_WRITE_PREFIXES = (
    Path("/usr/local"),
    Path("/opt/homebrew"),
    Path("/Library"),
    Path("/System"),
)
DEFAULT_TOTALSEG_WEIGHT_TASK_IDS = (115, 297, 113)
DENTALSEGMENTATOR_DATASET_ID = "112"
DENTALSEGMENTATOR_DATASET_NAME = "Dataset112_DentalSegmentator_v100"
DENTALSEGMENTATOR_MODEL_FILENAME = "Dataset112_DentalSegmentator_v100.zip"
DENTALSEGMENTATOR_MODEL_MD5 = "b71cd5230168d28a4f71b078265b76be"
DENTALSEGMENTATOR_MODEL_URL = (
    "https://zenodo.org/api/records/10829675/files/"
    "Dataset112_DentalSegmentator_v100.zip/content"
)
DENTALSEGMENTATOR_ZENODO_DOI = "10.5281/zenodo.10829675"
TOOTHSEG_MODEL_FILENAME = "ToothSeg.zip"
TOOTHSEG_MODEL_MD5 = "5d8dd061cce9529943567aeba3271143"
TOOTHSEG_MODEL_URL = "https://zenodo.org/records/14893540/files/ToothSeg.zip?download=1"
TOOTHSEG_ZENODO_DOI = "10.5281/zenodo.14893540"


CommandRunner = Callable[[list[str], Path | None, dict[str, str] | None], subprocess.CompletedProcess[str]]
PythonInspector = Callable[[Path], dict[str, Any]]


@dataclass(frozen=True)
class SetupPaths:
    app_support: Path
    env_dir: Path
    wheels_dir: Path
    models_dir: Path
    cases_dir: Path
    logs_dir: Path
    cache_dir: Path
    state_json: Path

    def to_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


@dataclass
class SetupStep:
    name: str
    status: str
    command: list[str] = field(default_factory=list)
    elapsed_seconds: float | None = None
    returncode: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SetupResult:
    status: str
    reason: str | None
    paths: SetupPaths
    steps: list[SetupStep]
    python: str
    platform: str
    machine: str
    allow_network: bool
    dry_run: bool
    wheel: str | None
    doctor: dict[str, Any] | None = None
    dicom_normalizer: dict[str, Any] | None = None
    python_executable: str | None = None
    python_version: str | None = None
    venv_reused: bool = False
    wheel_install_mode: str | None = None
    constraints: str | None = None
    installed_bundle: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "totalsegmentator_wrapper_mac.setup_state.v1",
            "status": self.status,
            "reason": self.reason,
            "paths": self.paths.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "python": self.python,
            "platform": self.platform,
            "machine": self.machine,
            "allow_network": self.allow_network,
            "dry_run": self.dry_run,
            "wheel": self.wheel,
            "doctor": self.doctor,
            "dicom_normalizer": self.dicom_normalizer,
            "python_executable": self.python_executable,
            "python_version": self.python_version,
            "venv_reused": self.venv_reused,
            "wheel_install_mode": self.wheel_install_mode,
            "constraints": self.constraints,
            "installed_bundle": self.installed_bundle,
        }


def default_app_support_dir(home: Path | None = None) -> Path:
    root = home or Path.home()
    return root / "Library" / "Application Support" / APP_SUPPORT_NAME


def setup_paths(app_support_dir: Path | None = None, *, home: Path | None = None) -> SetupPaths:
    app_support = (app_support_dir or default_app_support_dir(home)).expanduser()
    return SetupPaths(
        app_support=app_support,
        env_dir=app_support / "env",
        wheels_dir=app_support / "wheels",
        models_dir=app_support / "models",
        cases_dir=app_support / "cases",
        logs_dir=app_support / "logs",
        cache_dir=app_support / "cache",
        state_json=app_support / SETUP_STATE_FILENAME,
    )


def validate_app_support_path(paths: SetupPaths, *, home: Path | None = None) -> None:
    expected = default_app_support_dir(home).resolve()
    actual = paths.app_support.resolve()
    if actual != expected:
        raise ValueError(f"app support directory must be {expected}; got {actual}")
    for path in paths.to_dict().values():
        resolved = Path(path).resolve()
        if not _is_relative_to(resolved, expected):
            raise ValueError(f"setup path escapes app support directory: {resolved}")
        for prefix in FORBIDDEN_WRITE_PREFIXES:
            if _is_relative_to(resolved, prefix):
                raise ValueError(f"setup path uses forbidden system prefix: {resolved}")


def create_setup_directories(paths: SetupPaths, *, dry_run: bool) -> SetupStep:
    step = SetupStep(name="create_app_support_dirs", status="skipped" if dry_run else "success")
    if dry_run:
        return step
    for path in (
        paths.app_support,
        paths.env_dir,
        paths.wheels_dir,
        paths.models_dir,
        dentalsegmentator_model_root(paths),
        dentalsegmentator_model_root(paths) / "nnUNet_raw",
        dentalsegmentator_model_root(paths) / "nnUNet_preprocessed",
        dentalsegmentator_model_root(paths) / "nnUNet_results",
        toothseg_model_root(paths),
        toothseg_model_root(paths) / "nnUNet_results",
        paths.cases_dir,
        paths.logs_dir,
        paths.cache_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return step


def build_venv_command(python_executable: Path, env_dir: Path) -> list[str]:
    command = [str(python_executable), "-m", "venv", str(env_dir)]
    validate_safe_command(command)
    return command


def build_wheel_install_command(
    venv_python: Path,
    wheel: Path,
    *,
    allow_network: bool,
    constraints: Path | None = None,
) -> list[str]:
    target = str(wheel)
    if allow_network:
        target = f"{target}[dicom,mps,dentalseg,toothseg]"
        command = [str(venv_python), "-m", "pip", "install"]
        if constraints is not None:
            command.extend(["-c", str(constraints)])
        command.append(target)
    else:
        command = [str(venv_python), "-m", "pip", "install", "--no-deps", str(wheel)]
    validate_safe_command(command)
    return command


def build_installed_doctor_command(venv_python: Path, output_json: Path) -> list[str]:
    command = [
        str(venv_python),
        "-m",
        "totalsegmentator_wrapper_mac",
        "doctor",
        "--json",
        str(output_json),
    ]
    validate_safe_command(command)
    return command


def build_totalseg_privacy_command(venv_python: Path) -> list[str]:
    command = [
        str(venv_python),
        "-c",
        (
            "from totalsegmentator.config import setup_totalseg, set_config_key; "
            "setup_totalseg(); "
            "set_config_key('send_usage_stats', False); "
            "set_config_key('statistics_disclaimer_shown', True)"
        ),
    ]
    validate_safe_command(command)
    return command


def build_totalseg_weights_command(
    venv_python: Path,
    task_ids: tuple[int, ...] = DEFAULT_TOTALSEG_WEIGHT_TASK_IDS,
) -> list[str]:
    downloads = "; ".join(f"download_pretrained_weights({task_id})" for task_id in task_ids)
    command = [
        str(venv_python),
        "-c",
        (
            "from totalsegmentator.config import setup_totalseg; "
            "from totalsegmentator.libs import download_pretrained_weights; "
            "setup_totalseg(); "
            f"{downloads}"
        ),
    ]
    validate_safe_command(command)
    return command


def dentalsegmentator_model_root(paths: SetupPaths) -> Path:
    return paths.models_dir / "dentalsegmentator"


def toothseg_model_root(paths: SetupPaths) -> Path:
    return paths.models_dir / "toothseg"


def build_dentalseg_weights_command(
    venv_python: Path,
    model_root: Path,
    *,
    model_url: str = DENTALSEGMENTATOR_MODEL_URL,
    expected_md5: str = DENTALSEGMENTATOR_MODEL_MD5,
    dataset_id: str = DENTALSEGMENTATOR_DATASET_ID,
    dataset_name: str = DENTALSEGMENTATOR_DATASET_NAME,
) -> list[str]:
    command = [
        str(venv_python),
        "-m",
        "totalsegmentator_wrapper_mac.dentalsegmentator_setup",
        "--model-url",
        model_url,
        "--model-zip",
        str(model_root / DENTALSEGMENTATOR_MODEL_FILENAME),
        "--expected-md5",
        expected_md5,
        "--nnunet-results",
        str(model_root / "nnUNet_results"),
        "--nnunet-raw",
        str(model_root / "nnUNet_raw"),
        "--nnunet-preprocessed",
        str(model_root / "nnUNet_preprocessed"),
        "--dataset-id",
        dataset_id,
        "--dataset-name",
        dataset_name,
    ]
    validate_safe_command(command)
    return command


def build_setup_environment(paths: SetupPaths, *, dicom_normalizer: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PIP_NO_INPUT"] = "1"
    env["PIP_CACHE_DIR"] = str(paths.cache_dir / "pip")
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONPYCACHEPREFIX"] = str(paths.cache_dir / "pycache")
    env["TOTALSEGMENTATOR_WRAPPER_MAC_APP_SUPPORT"] = str(paths.app_support)
    env["XDG_CACHE_HOME"] = str(paths.cache_dir)
    env["MPLCONFIGDIR"] = str(paths.cache_dir / "matplotlib")
    env["TOTALSEG_HOME_DIR"] = str(paths.models_dir / "totalsegmentator")
    env["TOTALSEG_WEIGHTS_PATH"] = str(paths.models_dir / "totalsegmentator" / "weights")
    dentalseg_root = dentalsegmentator_model_root(paths)
    env["nnUNet_raw"] = str(dentalseg_root / "nnUNet_raw")
    env["nnUNet_preprocessed"] = str(dentalseg_root / "nnUNet_preprocessed")
    env["nnUNet_results"] = str(dentalseg_root / "nnUNet_results")
    if dicom_normalizer is not None:
        env["TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER"] = str(dicom_normalizer)
    return env


def validate_safe_command(command: list[str]) -> None:
    if not command:
        raise ValueError("empty command")
    executable_name = Path(command[0]).name
    if executable_name in FORBIDDEN_COMMAND_PARTS:
        raise ValueError(f"forbidden setup command: {executable_name}")
    if any(part in FORBIDDEN_COMMAND_PARTS for part in command):
        raise ValueError(f"forbidden setup command part in: {command}")


def run_setup(
    *,
    app_support_dir: Path | None = None,
    python_executable: Path | None = None,
    wheel: Path | None = None,
    constraints: Path | None = None,
    bundle_manifest: Path | None = None,
    allow_network: bool = False,
    dry_run: bool = False,
    skip_install: bool = False,
    skip_mps_check: bool = False,
    use_existing_env: bool = False,
    skip_dentalseg_model: bool = False,
    progress_log: Path | None = None,
    home: Path | None = None,
    runner: CommandRunner | None = None,
    normalizer_inspector: Callable[[], dict[str, Any]] | None = None,
    python_inspector: PythonInspector | None = None,
) -> SetupResult:
    paths = setup_paths(app_support_dir, home=home)
    validate_app_support_path(paths, home=home)
    runner = runner or _run_command
    normalizer_inspector = normalizer_inspector or inspect_dicom_normalizer
    python_executable = python_executable.expanduser().resolve() if python_executable is not None else None
    wheel = wheel.expanduser().resolve() if wheel is not None else _find_latest_wheel()
    constraints = constraints.expanduser().resolve() if constraints is not None else None
    bundle_manifest = bundle_manifest.expanduser().resolve() if bundle_manifest is not None else None
    python_inspector = python_inspector or inspect_python_runtime

    steps: list[SetupStep] = []
    result = SetupResult(
        status="success",
        reason=None,
        paths=paths,
        steps=steps,
        python=sys.version,
        platform=platform.platform(),
        machine=platform.machine(),
        allow_network=allow_network,
        dry_run=dry_run,
        wheel=str(wheel) if wheel is not None else None,
        python_executable=str(python_executable) if python_executable is not None else None,
        constraints=str(constraints) if constraints is not None else None,
        installed_bundle=None,
    )

    try:
        if bundle_manifest is not None:
            try:
                result.installed_bundle = read_bundle_install_record(bundle_manifest)
            except Exception as exc:  # noqa: BLE001
                _write_progress(progress_log, "setup_exception", "failed", "アプリ同梱manifestを読めません。")
                steps.append(SetupStep(name="read_bundle_manifest", status="failed", error=repr(exc)))
                result.status = "failed"
                result.reason = "bundle_manifest_invalid"
                return _finalize_result(result, write_state=not dry_run)
        _write_progress(progress_log, "create_app_support_dirs", "running", "App Supportディレクトリを準備しています。")
        steps.append(create_setup_directories(paths, dry_run=dry_run))
        _write_progress(progress_log, "create_app_support_dirs", steps[-1].status, "App Supportディレクトリの準備が完了しました。")
        if python_executable is None:
            _write_progress(progress_log, "validate_python_312", "failed", "同梱Python 3.12が見つかりません。")
            steps.append(
                SetupStep(
                    name="validate_python_312",
                    status="failed",
                    error="Python 3.12 executable was not supplied.",
                )
            )
            result.status = "failed"
            result.reason = "python312_missing"
            return _finalize_result(result, write_state=not dry_run)

        _write_progress(progress_log, "validate_python_312", "running", "Python 3.12を確認しています。")
        python_info = python_inspector(python_executable)
        result.python_version = str(python_info.get("version")) if python_info.get("version") else None
        steps.append(
            SetupStep(
                name="validate_python_312",
                status="success" if python_info.get("status") == "success" else "failed",
                command=list(python_info.get("command", [])),
                error=str(python_info.get("error") or python_info.get("reason") or "") or None,
            )
        )
        if python_info.get("status") != "success":
            _write_progress(progress_log, "validate_python_312", "failed", "Python 3.12の確認に失敗しました。")
            result.status = "failed"
            result.reason = str(python_info.get("reason") or "python_version_unsupported")
            return _finalize_result(result, write_state=not dry_run)
        _write_progress(progress_log, "validate_python_312", "success", "Python 3.12を確認しました。")

        if wheel is None or not wheel.exists():
            _write_progress(progress_log, "install_wheel", "failed", "同梱wheelが見つかりません。")
            result.status = "failed"
            result.reason = "wheel_missing"
            return _finalize_result(result, write_state=not dry_run)

        if allow_network and constraints is None:
            _write_progress(progress_log, "install_wheel", "failed", "依存固定ファイルが見つかりません。")
            result.status = "failed"
            result.reason = "constraints_missing"
            return _finalize_result(result, write_state=not dry_run)
        if constraints is not None and not constraints.exists():
            _write_progress(progress_log, "install_wheel", "failed", "依存固定ファイルが見つかりません。")
            result.status = "failed"
            result.reason = "constraints_missing"
            return _finalize_result(result, write_state=not dry_run)

        venv_python = paths.env_dir / "bin" / "python"
        result.venv_reused = venv_python.exists()
        _write_progress(progress_log, "create_venv", "running", "専用Python環境を準備しています。")
        if result.venv_reused:
            venv_step = SetupStep(name="create_venv", status="skipped")
        elif use_existing_env:
            venv_step = SetupStep(
                name="create_venv",
                status="failed",
                error=f"--use-existing-env was requested but {venv_python} does not exist.",
            )
        else:
            venv_step = _execute_step(
                "create_venv",
                build_venv_command(python_executable, paths.env_dir),
                paths.logs_dir,
                runner,
                env=build_setup_environment(paths),
                dry_run=dry_run or skip_install,
            )
        steps.append(venv_step)
        if venv_step.status == "failed":
            _write_progress(progress_log, "create_venv", "failed", "専用Python環境の準備に失敗しました。")
            result.status = "failed"
            result.reason = "runtime_install_failed"
            return _finalize_result(result, write_state=not dry_run)
        _write_progress(progress_log, "create_venv", venv_step.status, "専用Python環境の準備が完了しました。")

        result.wheel_install_mode = "network_constraints" if allow_network else "no_deps"
        _write_progress(progress_log, "install_wheel", "running", "依存パッケージを取得中です。数分かかることがあります。")
        install_step = _execute_step(
            "install_wheel",
            build_wheel_install_command(
                venv_python,
                wheel,
                allow_network=allow_network,
                constraints=constraints,
            ),
            paths.logs_dir,
            runner,
            env=build_setup_environment(paths),
            dry_run=dry_run or skip_install,
        )
        steps.append(install_step)
        if install_step.status == "failed":
            _write_progress(progress_log, "install_wheel", "failed", "依存パッケージの導入に失敗しました。")
            result.status = "failed"
            result.reason = "runtime_install_failed"
            return _finalize_result(result, write_state=not dry_run)
        _write_progress(progress_log, "install_wheel", "success", "依存パッケージの導入が完了しました。")

        if not allow_network and not skip_mps_check:
            result.dicom_normalizer = _annotate_normalizer_source(normalizer_inspector())
            if result.dicom_normalizer.get("status") != "success":
                _write_progress(progress_log, "doctor", "failed", "CT確認用部品の確認に失敗しました。")
                result.status = "failed"
                result.reason = "normalizer_missing"
                return _finalize_result(result, write_state=not dry_run)
            _write_progress(progress_log, "install_wheel", "failed", "ネットワーク接続が必要です。")
            result.status = "failed"
            result.reason = "needs_network"
            return _finalize_result(result, write_state=not dry_run)

        _write_progress(progress_log, "configure_totalseg_privacy", "running", "プライバシー設定を適用しています。")
        privacy_step = _execute_step(
            "configure_totalseg_privacy",
            build_totalseg_privacy_command(venv_python),
            paths.logs_dir,
            runner,
            env=build_setup_environment(paths),
            dry_run=dry_run or skip_install,
        )
        steps.append(privacy_step)
        if privacy_step.status == "failed":
            _write_progress(progress_log, "configure_totalseg_privacy", "failed", "プライバシー設定に失敗しました。")
            result.status = "failed"
            result.reason = "totalseg_privacy_config_failed"
            return _finalize_result(result, write_state=not dry_run)
        _write_progress(progress_log, "configure_totalseg_privacy", privacy_step.status, "プライバシー設定を適用しました。")

        _write_progress(progress_log, "download_totalseg_weights", "running", "初回実行に必要なモデルを取得しています。数分かかることがあります。")
        weights_step = _execute_step(
            "download_totalseg_weights",
            build_totalseg_weights_command(venv_python),
            paths.logs_dir,
            runner,
            env=build_setup_environment(paths),
            dry_run=dry_run or skip_install,
        )
        steps.append(weights_step)
        if weights_step.status == "failed":
            _write_progress(progress_log, "download_totalseg_weights", "failed", "モデルの取得に失敗しました。")
            result.status = "failed"
            result.reason = "weights_download_failed"
            return _finalize_result(result, write_state=not dry_run)
        _write_progress(progress_log, "download_totalseg_weights", weights_step.status, "モデルの取得が完了しました。")

        if skip_dentalseg_model:
            dentalseg_weights_step = SetupStep(
                name="download_dentalseg_weights",
                status="skipped",
                error="DentalSegmentator model preparation was deferred by --skip-dentalseg-model.",
            )
            steps.append(dentalseg_weights_step)
            _write_progress(
                progress_log,
                "download_dentalseg_weights",
                "skipped",
                "DentalSegmentatorモデルの準備は後で行います。",
            )
        else:
            _write_progress(
                progress_log,
                "download_dentalseg_weights",
                "running",
                "DentalSegmentatorモデルを取得しています。数分かかることがあります。",
            )
            dentalseg_weights_step = _execute_step(
                "download_dentalseg_weights",
                build_dentalseg_weights_command(
                    venv_python,
                    dentalsegmentator_model_root(paths),
                ),
                paths.logs_dir,
                runner,
                env=build_setup_environment(paths),
                dry_run=dry_run or skip_install,
            )
            steps.append(dentalseg_weights_step)
            if dentalseg_weights_step.status == "failed":
                _write_progress(
                    progress_log,
                    "download_dentalseg_weights",
                    "failed",
                    "DentalSegmentatorモデルの取得に失敗しました。",
                )
                result.status = "failed"
                result.reason = "dentalseg_weights_download_failed"
                return _finalize_result(result, write_state=not dry_run)
            _write_progress(
                progress_log,
                "download_dentalseg_weights",
                dentalseg_weights_step.status,
                "DentalSegmentatorモデルの取得が完了しました。",
            )

        doctor_json = paths.logs_dir / "doctor.json"
        _write_progress(progress_log, "doctor", "running", "MPSとCT確認用部品を確認しています。")
        doctor_step = _execute_step(
            "doctor",
            build_installed_doctor_command(venv_python, doctor_json),
            paths.logs_dir,
            runner,
            env=build_setup_environment(paths),
            dry_run=dry_run or skip_mps_check,
        )
        steps.append(doctor_step)
        if doctor_json.exists():
            result.doctor = _read_json(doctor_json)
            result.dicom_normalizer = _annotate_normalizer_source(result.doctor.get("dicom_normalizer"))
        else:
            result.dicom_normalizer = _annotate_normalizer_source(normalizer_inspector())

        if not result.dicom_normalizer or result.dicom_normalizer.get("status") != "success":
            _write_progress(progress_log, "doctor", "failed", "CT確認用部品の確認に失敗しました。")
            result.status = "failed"
            result.reason = "normalizer_missing"
            return _finalize_result(result, write_state=not dry_run)

        if doctor_step.status == "failed":
            _write_progress(progress_log, "doctor", "failed", "MPS確認に失敗しました。")
            result.status = "failed"
            result.reason = "mps_unavailable"
            return _finalize_result(result, write_state=not dry_run)

        _write_progress(progress_log, "doctor", "success", "MPS確認が完了しました。")
        _write_progress(progress_log, "complete", "success", "起動準備が完了しました。")
        return _finalize_result(result, write_state=not dry_run)
    except Exception as exc:  # noqa: BLE001
        _write_progress(progress_log, "setup_exception", "failed", f"セットアップ中に例外が発生しました: {exc!r}")
        steps.append(SetupStep(name="setup_exception", status="failed", error=repr(exc)))
        result.status = "failed"
        result.reason = "setup_exception"
        return _finalize_result(result, write_state=not dry_run)


def write_setup_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_setup_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json(path)


def read_bundle_install_record(manifest_path: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    return bundle_install_record(manifest)


def bundle_install_record(manifest: dict[str, Any]) -> dict[str, Any]:
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


def _execute_step(
    name: str,
    command: list[str],
    cwd: Path | None,
    runner: CommandRunner,
    *,
    env: dict[str, str],
    dry_run: bool,
) -> SetupStep:
    if dry_run:
        return SetupStep(name=name, status="skipped", command=command)
    started = time.perf_counter()
    try:
        proc = runner(command, cwd, env)
    except Exception as exc:  # noqa: BLE001
        return SetupStep(name=name, status="failed", command=command, error=repr(exc))
    elapsed = time.perf_counter() - started
    return SetupStep(
        name=name,
        status="success" if proc.returncode == 0 else "failed",
        command=command,
        elapsed_seconds=elapsed,
        returncode=proc.returncode,
        error=proc.stderr.strip() if proc.returncode != 0 else None,
    )


def _write_progress(progress_log: Path | None, step: str, status: str, message: str) -> None:
    if progress_log is None:
        return
    progress_log.parent.mkdir(parents=True, exist_ok=True)
    with progress_log.open("a", encoding="utf-8") as log:
        log.write(f"SETUP_PROGRESS step={step} status={status} message={message}\n")
        log.flush()


def _run_command(
    command: list[str],
    cwd: Path | None,
    env: dict[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _setup_env() -> dict[str, str]:
    paths = setup_paths()
    return build_setup_environment(paths)


def inspect_python_runtime(python_executable: Path) -> dict[str, Any]:
    command = [
        str(python_executable),
        "-c",
        (
            "import json, sys; "
            "print(json.dumps({'version': sys.version.split()[0], "
            "'major': sys.version_info.major, 'minor': sys.version_info.minor}))"
        ),
    ]
    if not python_executable.exists():
        return {
            "status": "failed",
            "reason": "python312_missing",
            "command": command,
            "error": f"Python executable does not exist: {python_executable}",
        }
    proc = _run_command(command, None, build_setup_environment(setup_paths()))
    if proc.returncode != 0:
        return {
            "status": "failed",
            "reason": "python312_missing",
            "command": command,
            "error": proc.stderr.strip() or proc.stdout.strip(),
        }
    try:
        payload = json.loads(proc.stdout)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "reason": "python_version_unsupported",
            "command": command,
            "error": repr(exc),
        }
    if payload.get("major") != 3 or payload.get("minor") != 12:
        return {
            "status": "failed",
            "reason": "python_version_unsupported",
            "command": command,
            "version": payload.get("version"),
            "error": f"Expected Python 3.12, got {payload.get('version')}",
        }
    return {
        "status": "success",
        "reason": None,
        "command": command,
        "version": payload.get("version"),
    }


def _finalize_result(result: SetupResult, *, write_state: bool) -> SetupResult:
    if write_state:
        write_setup_state(result.paths.state_json, result.to_dict())
    return result


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_latest_wheel() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    wheels = sorted((root / "dist").glob("totalsegmentator_wrapper_mac-*.whl"))
    return wheels[-1] if wheels else None


def _annotate_normalizer_source(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    annotated = dict(payload)
    annotated["normalizer_source"] = _normalizer_source(payload.get("binary"))
    return annotated


def _normalizer_source(binary: Any) -> str:
    if not binary:
        return "missing"
    path = Path(str(binary))
    text = str(path)
    if ".app/Contents/Resources/bin/" in text:
        return "app_bundle"
    if path.parent.name == "bin" and path.parent.parent.name == "totalsegmentator_wrapper_mac":
        return "package"
    return "path"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
