from __future__ import annotations

import json
import os
import queue
import signal
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from totalsegmentator_wrapper_mac.case_summary import format_case_summary_text
from totalsegmentator_wrapper_mac.cli import DEVICES
from totalsegmentator_wrapper_mac.dicom_normalizer_bridge import build_dicom_normalizer_audit_command


APP_TITLE = "TotalSegmentator Wrapper for Mac"
WIZARD_STAGES = ("1 目的", "2 入力", "3 実行", "4 結果")
GUI_EXPERIMENTAL_TEETH_MARGIN_MM = 5.0
RUN_MODE_ARCH_PREVIEW = "歯列と顎骨をまとめて表示"
RUN_MODE_INDIVIDUAL_TEETH = "歯を1本ずつ分けて表示（ベータ）"
RUN_MODES = (RUN_MODE_ARCH_PREVIEW, RUN_MODE_INDIVIDUAL_TEETH)
RUN_MODE_DESCRIPTIONS = {
    RUN_MODE_ARCH_PREVIEW: "歯列と顎骨をまとめて3D確認用に分けます。",
    RUN_MODE_INDIVIDUAL_TEETH: "歯を1本ずつ分けます。ベータ機能のため時間がかかります。",
}
RUN_PROGRESS_PREFIX = "RUN_PROGRESS "


def run_mode_to_task(run_mode: str) -> str:
    if run_mode == RUN_MODE_INDIVIDUAL_TEETH:
        return "teeth"
    return "craniofacial_structures"


def run_mode_uses_experimental_teeth(run_mode: str) -> bool:
    return run_mode == RUN_MODE_INDIVIDUAL_TEETH


def run_progress_from_log(text: str) -> dict[str, object] | None:
    last: dict[str, object] | None = None
    for line in text.splitlines():
        if not line.startswith(RUN_PROGRESS_PREFIX):
            continue
        try:
            payload = json.loads(line[len(RUN_PROGRESS_PREFIX) :])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            last = payload
    return last


def format_run_progress(progress: dict[str, object]) -> str:
    step = progress.get("step")
    total = progress.get("total")
    percent = progress.get("percent")
    stage = progress.get("stage")
    stage_prefix = f"{stage} " if isinstance(stage, str) and stage else ""
    if percent == 100:
        return f"プレビュー作成中: {stage_prefix}完了。次の処理へ進んでいます..."
    if isinstance(step, int) and isinstance(total, int) and total > 0:
        if isinstance(percent, int):
            return f"プレビュー作成中: {stage_prefix}{step}/{total} ({percent}%)"
        return f"プレビュー作成中: {stage_prefix}{step}/{total}"
    if isinstance(percent, int):
        return f"プレビュー作成中: {stage_prefix}{percent}%"
    return f"プレビュー作成中: {stage_prefix}進行中"


def default_totalseg_bin() -> str:
    candidate = Path(sys.executable).parent / "TotalSegmentator"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("TotalSegmentator")
    return found or "TotalSegmentator"


def default_output_dir() -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    app_support = os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_APP_SUPPORT")
    if app_support:
        return str((Path(app_support).expanduser() / "runs" / f"case_{stamp}").resolve())
    return str((Path.cwd() / "runs" / f"case_{stamp}").resolve())


def bundled_resources_dir() -> Path | None:
    configured = os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_RESOURCES_DIR")
    if not configured:
        return None
    return Path(configured).expanduser().resolve()


def bundled_setup_manifest() -> dict:
    resources = bundled_resources_dir()
    if resources is None:
        return {}
    manifest = resources / "setup_manifest.json"
    if not manifest.exists():
        return {}
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return {}


def update_manifest_url() -> str | None:
    url = bundled_setup_manifest().get("update_manifest_url")
    return str(url) if url else None


def update_allowed_hosts() -> set[str] | None:
    raw_hosts = bundled_setup_manifest().get("update_allowed_hosts")
    if not raw_hosts:
        return None
    if isinstance(raw_hosts, list):
        hosts = {str(host).strip() for host in raw_hosts if str(host).strip()}
        return hosts or None
    return {str(raw_hosts).strip()}


def bundled_sample1_input() -> Path | None:
    resources = bundled_resources_dir()
    if resources is None:
        return None
    candidate = resources / "sample1" / "input" / "DZ-CBCT_jawcrop_0p5mm.nii.gz"
    return candidate if candidate.exists() else None


def bundled_sample1_viewer() -> Path | None:
    resources = bundled_resources_dir()
    if resources is None:
        return None
    candidate = resources / "sample1" / "surface_preview" / "index.html"
    return candidate if candidate.exists() else None


def case_surface_preview(case_dir: Path) -> Path | None:
    candidate = case_dir / "surface_preview" / "index.html"
    return candidate if candidate.exists() else None


def path_display_name(raw_path: str) -> str:
    if not raw_path:
        return "未選択"
    path = Path(raw_path)
    name = path.name or str(path)
    if path.is_dir():
        return f"{name}/"
    return name


def _classification_label(value: object) -> str:
    labels = {
        "original_ct_geometry_ok": "通常CT候補",
        "secondary_capture_rescue_candidate": "救済候補",
        "needs_dicom_library": "DICOMライブラリ確認が必要",
        "compressed_pixel_data": "圧縮DICOM",
        "dicomdir_only": "DICOMDIRのみ",
        "reject": "対象外",
    }
    return labels.get(str(value), str(value))


def _next_action_label(value: object) -> str:
    labels = {
        "convert_clean": "clean CT変換候補",
        "prepare_rescue": "救済変換候補",
        "use_external_transcoder": "外部transcoderが必要",
        "inspect_manually": "手動確認",
        "reject": "処理しない",
        "none": "なし",
    }
    return labels.get(str(value), str(value))


def build_run_command(
    *,
    python_executable: str,
    input_path: str,
    output_dir: str,
    run_mode: str,
    device: str,
    totalseg_bin: str,
    no_copy_input: bool = False,
    experimental_teeth_margin_mm: float = GUI_EXPERIMENTAL_TEETH_MARGIN_MM,
) -> list[str]:
    task = run_mode_to_task(run_mode)
    command = [
        python_executable,
        "-m",
        "totalsegmentator_wrapper_mac",
        "run",
        "--input",
        input_path,
        "--output",
        output_dir,
        "--task",
        task,
        "--device",
        device,
        "--totalseg-bin",
        totalseg_bin,
    ]
    if no_copy_input:
        command.append("--no-copy-input")
    if run_mode_uses_experimental_teeth(run_mode):
        command.append("--experimental-teeth")
        command.extend(["--teeth-crop-margin-mm", f"{experimental_teeth_margin_mm:.1f}"])
    return command


def build_backend_env(project_root: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    executable_dir = str(Path(sys.executable).parent)
    path_parts = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    if executable_dir not in path_parts:
        env["PATH"] = os.pathsep.join([executable_dir, *path_parts])
    root = (project_root or Path.cwd()).resolve()
    src = root / "src"
    if src.exists():
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(src) if not existing else f"{src}{os.pathsep}{existing}"

    artifacts = root / "artifacts"
    totalseg_home = artifacts / "totalseg_home"
    totalseg_weights = artifacts / "totalseg_weights"
    if totalseg_home.exists():
        env.setdefault("TOTALSEG_HOME_DIR", str(totalseg_home))
    if totalseg_weights.exists():
        env.setdefault("TOTALSEG_WEIGHTS_PATH", str(totalseg_weights))
    env.setdefault("MPLCONFIGDIR", str(artifacts / "matplotlib_cache"))
    env.setdefault("XDG_CACHE_HOME", str(artifacts / "cache"))
    return env


class PreviewApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(880, 620)

        sample_input = bundled_sample1_input()
        self.input_var = tk.StringVar(value=str(sample_input) if sample_input else "")
        self.output_var = tk.StringVar(value=default_output_dir())
        self.run_mode_var = tk.StringVar(value=RUN_MODE_ARCH_PREVIEW)
        self.run_mode_description_var = tk.StringVar(value=RUN_MODE_DESCRIPTIONS[RUN_MODE_ARCH_PREVIEW])
        self.device_var = tk.StringVar(value="mps")
        self.totalseg_var = tk.StringVar(value=default_totalseg_bin())
        self.show_advanced_var = tk.BooleanVar(value=False)
        self.stage_var = tk.StringVar(value=WIZARD_STAGES[0])
        self.status_var = tk.StringVar(value="待機中")
        self.elapsed_var = tk.StringVar(value="")
        self.result_var = tk.StringVar(value="Sample 1から試すか、自分のNIfTI/DICOMフォルダを選んでください。")

        self.process: subprocess.Popen[str] | None = None
        self.worker: threading.Thread | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.log_offset = 0
        self.log_path: Path | None = None
        self.started_at: float | None = None
        self.current_mode = "run"
        self.advanced_frame: ttk.Frame | None = None
        self.main_panel: ttk.Frame | None = None
        self.log_frame: ttk.Frame | None = None
        self.log_toggle_button: ttk.Button | None = None
        self.run_button: ttk.Button | None = None
        self.stop_button: ttk.Button | None = None
        self.dicom_audit_button: ttk.Button | None = None
        self.update_button: ttk.Button | None = None
        self.open_output_button: ttk.Button | None = None
        self.open_case_preview_button: ttk.Button | None = None
        self.show_summary_button: ttk.Button | None = None
        self.open_slicer_button: ttk.Button | None = None
        self.result_actions_frame: ttk.LabelFrame | None = None
        self.run_progress_bar: ttk.Progressbar | None = None
        self.run_progress_screen_bar: ttk.Progressbar | None = None
        self.stage_labels: list[ttk.Label] = []
        self.current_screen = "start"
        self.log_visible_var = tk.BooleanVar(value=False)
        self.progress_step_var = tk.StringVar(value="目的を選んでください")

        self._build_ui()
        self.run_mode_var.trace_add("write", lambda *_: self._refresh_run_mode_description())
        self.root.after(250, self._poll_events)
        self.root.after(1000, self._poll_run_log)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        shell = ttk.Frame(self.root, padding=12)
        shell.grid(row=0, column=0, sticky="ew")
        shell.columnconfigure(0, weight=1)

        ttk.Label(shell, text="TotalSegmentator Wrapper for Mac", font=("", 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            shell,
            text="非臨床preview用です。データは送信しません。",
        ).grid(row=1, column=0, sticky="w", pady=(2, 8))

        body = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(body, width=250)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        sidebar.grid_propagate(False)
        ttk.Label(sidebar, text="流れ", font=("", 13, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        for index, label in enumerate(WIZARD_STAGES, start=1):
            stage_label = ttk.Label(sidebar, text=label, wraplength=220)
            stage_label.grid(row=index, column=0, sticky="w", pady=(0, 6))
            self.stage_labels.append(stage_label)

        status_frame = ttk.LabelFrame(sidebar, text="状態")
        status_frame.grid(row=5, column=0, sticky="ew", pady=(14, 8))
        status_frame.columnconfigure(0, weight=1)
        ttk.Label(status_frame, textvariable=self.status_var).grid(row=0, column=0, sticky="w", padx=8, pady=(6, 2))
        ttk.Label(status_frame, textvariable=self.progress_step_var).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 2))
        self.run_progress_bar = ttk.Progressbar(status_frame, mode="determinate", maximum=100)
        self.run_progress_bar.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))
        ttk.Label(status_frame, textvariable=self.elapsed_var).grid(row=3, column=0, sticky="w", padx=8, pady=(0, 8))

        self.result_actions_frame = ttk.LabelFrame(sidebar, text="結果")
        self.result_actions_frame.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        self.result_actions_frame.columnconfigure(0, weight=1)
        self.open_output_button = ttk.Button(self.result_actions_frame, text="結果フォルダを開く", command=self._open_output)
        self.open_output_button.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 4))
        self.open_case_preview_button = ttk.Button(
            self.result_actions_frame, text="3Dプレビューを開く", command=self._open_case_preview
        )
        self.open_case_preview_button.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.show_summary_button = ttk.Button(self.result_actions_frame, text="結果の要約を表示", command=self._show_summary)
        self.show_summary_button.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.open_slicer_button = ttk.Button(
            self.result_actions_frame, text="Slicer用スクリプトを開く", command=self._open_slicer_script
        )
        self.open_slicer_button.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))

        utilities = ttk.LabelFrame(sidebar, text="詳細")
        utilities.grid(row=7, column=0, sticky="ew")
        utilities.columnconfigure(0, weight=1)
        self.log_toggle_button = ttk.Button(utilities, text="詳細ログを表示", command=self._toggle_log_visibility)
        self.log_toggle_button.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 4))
        self.update_button = ttk.Button(utilities, text="更新を確認", command=self._check_updates)
        self.update_button.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        if update_manifest_url() is None:
            self.update_button.state(["disabled"])

        self.main_panel = ttk.Frame(body)
        self.main_panel.grid(row=0, column=1, sticky="nsew")
        self.main_panel.columnconfigure(0, weight=1)

        self.log_frame = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        self.log_frame.grid(row=2, column=0, sticky="nsew")
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(self.log_frame, wrap="word", height=12)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self.log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_frame.grid_remove()

        sample_input = bundled_sample1_input()
        self._refresh_result_actions()
        self._set_stage(0)
        self._show_start_screen()
        if sample_input:
            self._append_log(f"Sample 1を同梱しています: {sample_input}\n")

    def _set_stage(self, index: int) -> None:
        bounded = min(max(index, 0), len(WIZARD_STAGES) - 1)
        self.stage_var.set(WIZARD_STAGES[bounded])
        for label_index, label in enumerate(self.stage_labels):
            marker = "▶ " if label_index == bounded else "  "
            label.configure(text=f"{marker}{WIZARD_STAGES[label_index]}")

    def _clear_main_panel(self) -> ttk.Frame:
        if self.main_panel is None:
            raise RuntimeError("main panel not initialized")
        for child in self.main_panel.winfo_children():
            child.destroy()
        self.run_button = None
        self.stop_button = None
        self.dicom_audit_button = None
        self.advanced_frame = None
        self.main_panel.columnconfigure(0, weight=1)
        return self.main_panel

    def _section_title(self, parent: ttk.Frame, title: str, description: str) -> None:
        ttk.Label(parent, text=title, font=("", 18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(parent, text=description, wraplength=620).grid(row=1, column=0, sticky="w", pady=(6, 18))

    def _show_start_screen(self) -> None:
        self.current_screen = "start"
        self._set_stage(0)
        panel = self._clear_main_panel()
        self._section_title(
            panel,
            "まず、どちらから始めますか？",
            "Sampleで流れを体験するか、自分のCT/NIfTIを選んで進めます。",
        )
        cards = ttk.Frame(panel)
        cards.grid(row=2, column=0, sticky="ew")
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)

        sample_card = ttk.LabelFrame(cards, text="おすすめ")
        sample_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        sample_card.columnconfigure(0, weight=1)
        ttk.Label(
            sample_card,
            text="Sampleで流れを体験する",
            font=("", 14, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        ttk.Label(
            sample_card,
            text="完成3Dを見る → Sampleを入力にする → 3Dプレビュー作成（segmentation） → 結果確認、の順に試せます。",
            wraplength=310,
        ).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 12))
        ttk.Button(sample_card, text="Sampleで流れを体験する", command=self._show_sample_tutorial).grid(
            row=2, column=0, sticky="ew", padx=12, pady=(0, 12)
        )

        own_card = ttk.LabelFrame(cards, text="自分のデータ")
        own_card.grid(row=0, column=1, sticky="nsew")
        own_card.columnconfigure(0, weight=1)
        ttk.Label(
            own_card,
            text="自分のCT/NIfTIを開く",
            font=("", 14, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        ttk.Label(
            own_card,
            text="NIfTIファイルを選ぶか、DICOMフォルダを先に確認します。DICOMは直接処理せず、まず安全に内容確認します。",
            wraplength=310,
        ).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 12))
        ttk.Button(own_card, text="自分のCT/NIfTIを開く", command=self._show_own_data_screen).grid(
            row=2, column=0, sticky="ew", padx=12, pady=(0, 12)
        )

    def _show_sample_tutorial(self) -> None:
        self.current_screen = "sample"
        self._set_stage(0)
        panel = self._clear_main_panel()
        self._section_title(
            panel,
            "Sampleで流れを体験する",
            "ボタンを上から順に押すだけで、完成イメージから結果確認まで試せます。",
        )
        steps = ttk.Frame(panel)
        steps.grid(row=2, column=0, sticky="ew")
        steps.columnconfigure(0, weight=1)
        self._sample_step(
            steps,
            0,
            "1. 完成イメージを見る",
            "同梱Sample 1の3Dプレビューをブラウザで開きます。",
            "Sample 1の3Dプレビューを開く",
            self._open_sample_viewer,
        )
        self._sample_step(
            steps,
            1,
            "2. 入力を準備する",
            f"入力: {path_display_name(str(bundled_sample1_input() or ''))}",
            "Sample 1を入力に使う",
            self._use_sample_input,
        )
        run_frame = ttk.LabelFrame(steps, text="3. 3Dプレビュー作成（segmentation）")
        run_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        run_frame.columnconfigure(1, weight=1)
        ttk.Label(run_frame, text="実行モード").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        ttk.OptionMenu(run_frame, self.run_mode_var, self.run_mode_var.get(), *RUN_MODES).grid(
            row=0, column=1, sticky="w", pady=(10, 4)
        )
        ttk.Label(run_frame, text="デバイス: mps").grid(row=0, column=2, sticky="e", padx=10, pady=(10, 4))
        ttk.Label(run_frame, textvariable=self.run_mode_description_var, wraplength=600).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 8)
        )
        ttk.Label(
            run_frame,
            text="Sample 1の3Dプレビュー作成は、このMacでおおむね100秒前後かかります。",
            wraplength=600,
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 8))
        self._build_advanced_settings(run_frame, row=3)
        self.run_button = ttk.Button(run_frame, text="このSampleで実行開始", command=self._run_sample)
        self.run_button.grid(row=5, column=0, sticky="w", padx=10, pady=(0, 10))
        self.stop_button = ttk.Button(run_frame, text="停止", command=self._stop)
        self.stop_button.grid(row=5, column=1, sticky="w", pady=(0, 10))
        self.stop_button.state(["disabled"])
        ttk.Button(steps, text="自分のデータを開く", command=self._show_own_data_screen).grid(
            row=3, column=0, sticky="w"
        )

    def _sample_step(
        self,
        parent: ttk.Frame,
        row: int,
        title: str,
        description: str,
        button_text: str,
        command: object,
    ) -> None:
        frame = ttk.LabelFrame(parent, text=title)
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=description, wraplength=620).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 6))
        ttk.Button(frame, text=button_text, command=command).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 10))

    def _show_own_data_screen(self) -> None:
        self.current_screen = "own_data"
        self._set_stage(1)
        panel = self._clear_main_panel()
        self._section_title(
            panel,
            "自分のデータを開く",
            "NIfTIはそのまま実行設定へ進みます。DICOMフォルダはまず内容確認だけを行います。",
        )
        cards = ttk.Frame(panel)
        cards.grid(row=2, column=0, sticky="ew")
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)
        nifti = ttk.LabelFrame(cards, text="NIfTI")
        nifti.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        nifti.columnconfigure(0, weight=1)
        ttk.Label(nifti, text="NIfTIファイルを選ぶ", font=("", 14, "bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 4)
        )
        ttk.Label(nifti, text="変換済みCTやSample同等のNIfTIを使います。", wraplength=300).grid(
            row=1, column=0, sticky="w", padx=12, pady=(0, 12)
        )
        ttk.Button(nifti, text="NIfTIファイルを選ぶ", command=self._choose_input).grid(
            row=2, column=0, sticky="ew", padx=12, pady=(0, 12)
        )

        dicom = ttk.LabelFrame(cards, text="DICOM")
        dicom.grid(row=0, column=1, sticky="nsew")
        dicom.columnconfigure(0, weight=1)
        ttk.Label(dicom, text="DICOMフォルダを確認する", font=("", 14, "bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 4)
        )
        ttk.Label(dicom, text="segmentation前にseriesや形式を確認します。結果はまだ作りません。", wraplength=300).grid(
            row=1, column=0, sticky="w", padx=12, pady=(0, 12)
        )
        ttk.Button(dicom, text="DICOMフォルダを確認する", command=self._choose_dicom_dir_and_audit).grid(
            row=2, column=0, sticky="ew", padx=12, pady=(0, 12)
        )

    def _show_run_screen(self) -> None:
        self.current_screen = "run_settings"
        self._set_stage(2)
        panel = self._clear_main_panel()
        input_path = self.input_var.get().strip()
        title = "実行前の確認"
        description = "入力と保存先を確認して、3Dプレビュー作成（segmentation）を開始します。"
        if input_path and Path(input_path).is_dir():
            title = "DICOM確認"
            description = "このフォルダは直接segmentationせず、まずDICOMの内容確認を行います。"
        self._section_title(panel, title, description)

        summary = ttk.LabelFrame(panel, text="選択中")
        summary.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        summary.columnconfigure(1, weight=1)
        ttk.Label(summary, text="入力").grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))
        ttk.Label(summary, text=path_display_name(input_path)).grid(row=0, column=1, sticky="w", pady=(8, 4))
        ttk.Button(summary, text="変更", command=self._show_own_data_screen).grid(
            row=0, column=2, sticky="e", padx=10, pady=(8, 4)
        )
        ttk.Label(summary, text="保存先").grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))
        ttk.Label(summary, text=path_display_name(self.output_var.get().strip())).grid(
            row=1, column=1, sticky="w", pady=(0, 8)
        )
        ttk.Button(summary, text="保存先を選ぶ", command=self._choose_output).grid(
            row=1, column=2, sticky="e", padx=10, pady=(0, 8)
        )

        run_frame = ttk.LabelFrame(panel, text="実行設定")
        run_frame.grid(row=3, column=0, sticky="ew")
        run_frame.columnconfigure(1, weight=1)
        ttk.Label(run_frame, text="実行モード").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        ttk.OptionMenu(run_frame, self.run_mode_var, self.run_mode_var.get(), *RUN_MODES).grid(
            row=0, column=1, sticky="w", pady=(10, 4)
        )
        ttk.Label(run_frame, text="デバイス: mps").grid(row=0, column=2, sticky="e", padx=10, pady=(10, 4))
        ttk.Label(run_frame, textvariable=self.run_mode_description_var, wraplength=620).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 8)
        )
        self._build_advanced_settings(run_frame, row=2)
        actions = ttk.Frame(run_frame)
        actions.grid(row=4, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 10))
        if input_path and Path(input_path).is_dir():
            self.dicom_audit_button = ttk.Button(actions, text="DICOM確認を実行", command=self._run_dicom_audit)
            self.dicom_audit_button.grid(row=0, column=0, sticky="w", padx=(0, 8))
        else:
            self.run_button = ttk.Button(actions, text="実行開始", command=self._run)
            self.run_button.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.stop_button = ttk.Button(actions, text="停止", command=self._stop)
        self.stop_button.grid(row=0, column=1, sticky="w")
        self.stop_button.state(["disabled"])

    def _build_advanced_settings(self, parent: ttk.Frame, *, row: int) -> None:
        ttk.Checkbutton(
            parent,
            text="詳細設定を表示",
            variable=self.show_advanced_var,
            command=self._toggle_advanced_settings,
        ).grid(row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 4))
        self.advanced_frame = ttk.Frame(parent)
        self.advanced_frame.grid(row=row + 1, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 8))
        self.advanced_frame.columnconfigure(1, weight=1)
        ttk.Label(self.advanced_frame, text="デバイス").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.OptionMenu(self.advanced_frame, self.device_var, self.device_var.get(), *DEVICES).grid(
            row=0, column=1, sticky="w", pady=(0, 4)
        )
        ttk.Label(self.advanced_frame, text="TotalSegmentator実行ファイル").grid(row=1, column=0, sticky="w")
        ttk.Entry(self.advanced_frame, textvariable=self.totalseg_var).grid(
            row=1, column=1, sticky="ew", padx=(8, 8)
        )
        ttk.Button(self.advanced_frame, text="実行ファイルを選ぶ", command=self._choose_runner).grid(
            row=1, column=2, sticky="e"
        )
        self._toggle_advanced_settings()

    def _show_progress_screen(self, message: str) -> None:
        self.current_screen = "progress"
        panel = self._clear_main_panel()
        self._section_title(panel, "処理中です", message)
        progress = ttk.LabelFrame(panel, text="進捗")
        progress.grid(row=2, column=0, sticky="ew")
        progress.columnconfigure(0, weight=1)
        ttk.Label(progress, text="準備中 → プレビュー作成中 → 結果保存中 → 完了", wraplength=620).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 4)
        )
        self.run_progress_screen_bar = ttk.Progressbar(progress, mode="indeterminate", maximum=100)
        self.run_progress_screen_bar.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        self.run_progress_screen_bar.start(12)
        ttk.Label(progress, textvariable=self.elapsed_var).grid(row=2, column=0, sticky="w", padx=10, pady=(0, 8))
        self.stop_button = ttk.Button(progress, text="停止", command=self._stop)
        self.stop_button.grid(row=3, column=0, sticky="w", padx=10, pady=(0, 10))

    def _show_result_screen(self, *, success: bool) -> None:
        self.current_screen = "results"
        self._set_stage(3)
        panel = self._clear_main_panel()
        title = "完了しました" if success else "確認が必要です"
        self._section_title(panel, title, self.result_var.get())
        actions = ttk.LabelFrame(panel, text="次にできること")
        actions.grid(row=2, column=0, sticky="ew")
        for column in range(4):
            actions.columnconfigure(column, weight=1)
        output_button = ttk.Button(actions, text="結果フォルダを開く", command=self._open_output)
        output_button.grid(
            row=0, column=0, sticky="ew", padx=10, pady=10
        )
        preview_button = ttk.Button(actions, text="3Dプレビューを開く", command=self._open_case_preview)
        preview_button.grid(
            row=0, column=1, sticky="ew", padx=(0, 10), pady=10
        )
        summary_button = ttk.Button(actions, text="結果の要約を表示", command=self._show_summary)
        summary_button.grid(
            row=0, column=2, sticky="ew", padx=(0, 10), pady=10
        )
        ttk.Button(actions, text="詳細ログを表示", command=lambda: self._set_log_visibility(True)).grid(
            row=0, column=3, sticky="ew", padx=(0, 10), pady=10
        )
        output_dir = Path(self.output_var.get().strip()) if self.output_var.get().strip() else None
        output_exists = bool(output_dir and output_dir.exists())
        preview_exists = bool(output_dir and case_surface_preview(output_dir) is not None)
        self._button_state(output_button, output_exists)
        self._button_state(summary_button, output_exists)
        self._button_state(preview_button, preview_exists)
        ttk.Button(panel, text="別のデータを開く", command=self._show_start_screen).grid(row=3, column=0, sticky="w")

    def _refresh_run_mode_description(self) -> None:
        mode = self.run_mode_var.get()
        description = RUN_MODE_DESCRIPTIONS.get(mode, "")
        if run_mode_uses_experimental_teeth(mode):
            description = f"{description} ROI margin {GUI_EXPERIMENTAL_TEETH_MARGIN_MM:.1f}mmで実行します。"
        self.run_mode_description_var.set(description)

    def _toggle_advanced_settings(self) -> None:
        if self.advanced_frame is None:
            return
        if self.show_advanced_var.get():
            self.advanced_frame.grid()
        else:
            self.advanced_frame.grid_remove()

    def _button_state(self, button: ttk.Button | None, enabled: bool) -> None:
        if button is None:
            return
        try:
            if enabled:
                button.state(["!disabled"])
            else:
                button.state(["disabled"])
        except tk.TclError:
            return

    def _toggle_log_visibility(self) -> None:
        self._set_log_visibility(not self.log_visible_var.get())

    def _set_log_visibility(self, visible: bool) -> None:
        self.log_visible_var.set(visible)
        if self.log_frame is not None:
            if visible:
                self.log_frame.grid()
            else:
                self.log_frame.grid_remove()
        if self.log_toggle_button is not None:
            self.log_toggle_button.configure(text="詳細ログを隠す" if visible else "詳細ログを表示")

    def _use_sample_input(self) -> bool:
        sample_input = bundled_sample1_input()
        if sample_input is None:
            messagebox.showerror(APP_TITLE, "Sample 1の入力ファイルが見つかりません。")
            return False
        self.input_var.set(str(sample_input))
        self._set_stage(1)
        self.result_var.set("Sample 1を入力に設定しました。次に実行開始を押してください。")
        self._append_log(f"Sample 1を入力に設定しました: {sample_input}\n")
        return True

    def _run_sample(self) -> None:
        if self._use_sample_input():
            self._run()

    def _refresh_result_actions(self) -> None:
        output_dir = Path(self.output_var.get().strip()) if self.output_var.get().strip() else None
        output_exists = bool(output_dir and output_dir.exists())
        preview_exists = bool(output_dir and case_surface_preview(output_dir) is not None)
        if self.result_actions_frame is not None:
            if output_exists or self.current_screen == "results":
                self.result_actions_frame.grid()
            else:
                self.result_actions_frame.grid_remove()
        for button, enabled in (
            (self.open_output_button, output_exists),
            (self.show_summary_button, output_exists),
            (self.open_case_preview_button, preview_exists),
        ):
            self._button_state(button, enabled)

    def _choose_input(self) -> None:
        # macOS Tk can crash in the native open panel when bundled Tcl/Tk receives
        # multi-pattern file type filters.
        path = filedialog.askopenfilename(title="NIfTI入力を選択")
        if path:
            self.input_var.set(path)
            self._set_stage(1)
            self.result_var.set("NIfTI入力を選択しました。設定を確認して実行開始を押してください。")
            self._show_run_screen()

    def _choose_dicom_dir(self) -> bool:
        path = filedialog.askdirectory(title="DICOMフォルダを選択")
        if path:
            self.input_var.set(path)
            self._set_stage(1)
            self.result_var.set("DICOMフォルダを選択しました。実行前にDICOM確認を行います。")
            return True
        return False

    def _choose_dicom_dir_and_audit(self) -> None:
        if self._choose_dicom_dir():
            self._run_dicom_audit()

    def _choose_output(self) -> None:
        path = filedialog.askdirectory(title="保存先フォルダを選択")
        if path:
            self.output_var.set(path)
            self._refresh_result_actions()
            if self.current_screen == "run_settings":
                self._show_run_screen()

    def _choose_runner(self) -> None:
        path = filedialog.askopenfilename(title="TotalSegmentator実行ファイルを選択")
        if path:
            self.totalseg_var.set(path)

    def _run(self) -> None:
        if self.process is not None:
            return
        input_path = self.input_var.get().strip()
        output_dir = self.output_var.get().strip()
        totalseg_bin = self.totalseg_var.get().strip()
        if not input_path or not Path(input_path).exists():
            messagebox.showerror(APP_TITLE, "入力ファイルまたはDICOMフォルダが見つかりません。")
            return
        if Path(input_path).is_dir():
            self._append_log("DICOMフォルダが選択されています。プレビュー作成ではなく撮影データの確認を実行します。\n")
            self._run_dicom_audit()
            return
        if not output_dir:
            messagebox.showerror(APP_TITLE, "保存先フォルダが必要です。")
            return
        if not totalseg_bin:
            messagebox.showerror(APP_TITLE, "TotalSegmentator実行ファイルが必要です。")
            return

        self.log_text.delete("1.0", "end")
        self.log_offset = 0
        self.log_path = Path(output_dir) / "logs" / "run.log"
        self._reset_run_progress()
        self.started_at = time.perf_counter()
        self._set_stage(2)
        self.status_var.set("実行中")
        self.progress_step_var.set("プレビュー作成中")
        self.result_var.set("3Dプレビュー作成（segmentation）を実行中です。")
        self.elapsed_var.set("")
        self._button_state(self.run_button, False)
        self._button_state(self.stop_button, True)
        self._button_state(self.dicom_audit_button, False)
        self.current_mode = "run"
        command = build_run_command(
            python_executable=sys.executable,
            input_path=input_path,
            output_dir=output_dir,
            run_mode=self.run_mode_var.get(),
            device=self.device_var.get(),
            totalseg_bin=totalseg_bin,
            no_copy_input=False,
        )
        self._append_log("$ " + " ".join(command) + "\n\n")
        self._show_progress_screen("3Dプレビュー作成（segmentation）を実行中です。数分かかることがあります。")
        self.worker = threading.Thread(target=self._worker_run, args=(command,), daemon=True)
        self.worker.start()

    def _run_dicom_audit(self) -> None:
        if self.process is not None:
            return
        input_path = self.input_var.get().strip()
        output_dir = self.output_var.get().strip()
        if not input_path or not Path(input_path).exists():
            messagebox.showerror(APP_TITLE, "DICOMフォルダが見つかりません。")
            return
        if not Path(input_path).is_dir():
            messagebox.showerror(APP_TITLE, "DICOM確認にはフォルダを選択してください。")
            return
        if not output_dir:
            messagebox.showerror(APP_TITLE, "保存先フォルダが必要です。")
            return

        output_root = Path(output_dir)
        audit_json = output_root / "logs" / "dicom_normalizer_audit.json"
        audit_json.parent.mkdir(parents=True, exist_ok=True)
        try:
            command = build_dicom_normalizer_audit_command(
                dicom_dir=Path(input_path),
                output_json=audit_json,
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return

        self.log_text.delete("1.0", "end")
        self.log_offset = 0
        self.log_path = None
        self._reset_run_progress()
        self.started_at = time.perf_counter()
        self._set_stage(2)
        self.status_var.set("DICOM確認中")
        self.progress_step_var.set("DICOM確認中")
        self.result_var.set("DICOMフォルダを確認中です。segmentationはまだ実行しません。")
        self.elapsed_var.set("")
        self._button_state(self.run_button, False)
        self._button_state(self.stop_button, True)
        self._button_state(self.dicom_audit_button, False)
        self.current_mode = "dicom_audit"
        self._append_log(
            "DICOMフォルダが選択されました。まずC++ DICOM normalizer auditを実行します。\n\n"
        )
        self._append_log("$ " + " ".join(command) + "\n\n")
        self._show_progress_screen("DICOMフォルダを確認中です。segmentationはまだ実行しません。")
        self.worker = threading.Thread(target=self._worker_run, args=(command,), daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        if self.process is None:
            return
        self.status_var.set("停止中")
        self.progress_step_var.set("停止中")
        self._button_state(self.stop_button, False)
        self._append_log("\n停止を要求しました...\n")
        try:
            self.process.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass

    def _worker_run(self, command: list[str]) -> None:
        try:
            self.process = subprocess.Popen(  # noqa: S603
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=build_backend_env(),
            )
            assert self.process.stdout is not None
            output_chunks = []
            for line in self.process.stdout:
                output_chunks.append(line)
                self.events.put(("stdout", line))
            returncode = self.process.wait()
            self.events.put(("finished", (returncode, "".join(output_chunks))))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("failed", repr(exc)))

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "stdout":
                self._append_log(str(payload))
            elif kind == "finished":
                returncode, _output = payload  # type: ignore[misc]
                self._finish_run(int(returncode))
            elif kind == "failed":
                self._append_log(f"\nUI runnerが失敗しました: {payload}\n")
                self._finish_run(1)
            elif kind == "update_result":
                self._finish_update_check(payload)  # type: ignore[arg-type]
            elif kind == "update_failed":
                self._append_log(f"\n更新確認に失敗しました: {payload}\n")
                self.status_var.set("待機中")
                self._button_state(self.update_button, True)
        self.root.after(250, self._poll_events)

    def _poll_run_log(self) -> None:
        if self.process is not None:
            self._flush_run_log()
        if self.process is not None and self.started_at is not None:
            self.elapsed_var.set(f"{time.perf_counter() - self.started_at:.1f}s")
        self.root.after(1000, self._poll_run_log)

    def _finish_run(self, returncode: int) -> None:
        self._flush_run_log()
        if self.current_mode == "dicom_audit":
            audit_json = Path(self.output_var.get().strip()) / "logs" / "dicom_normalizer_audit.json"
            if audit_json.exists():
                self._append_log(f"\nDICOM確認JSON: {audit_json}\n")
                self._append_dicom_audit_summary(audit_json)
            else:
                self._append_log("\nDICOM確認JSONは作成されませんでした。\n")
        else:
            self._append_log(format_case_summary_text(Path(self.output_var.get().strip())))
        if self.started_at is not None:
            self.elapsed_var.set(f"{time.perf_counter() - self.started_at:.1f}s")
        self._set_stage(3)
        if returncode == 0:
            self.status_var.set("成功")
            self.progress_step_var.set("完了")
            self._set_run_progress_value(100)
            if self.current_mode == "dicom_audit":
                self.result_var.set("DICOM確認が完了しました。プレビュー作成はまだ開始していません。次に進む場合は変換済みNIfTIを選んでください。")
            else:
                self.result_var.set("実行が完了しました。結果フォルダや3Dプレビューを確認できます。")
        else:
            self.status_var.set(f"失敗 ({returncode})")
            self.progress_step_var.set("確認が必要です")
            self._stop_run_progress_bar()
            if self.log_path is not None:
                self.result_var.set(f"失敗しました。ログを確認してください: {self.log_path}")
            else:
                self.result_var.set("失敗しました。ログ欄とDICOM確認JSONを確認してください。")
        self.process = None
        self._button_state(self.run_button, True)
        self._button_state(self.stop_button, False)
        self._button_state(self.dicom_audit_button, True)
        self._refresh_result_actions()
        self._show_result_screen(success=returncode == 0)

    def _flush_run_log(self) -> None:
        if self.log_path is None or not self.log_path.exists():
            return
        text = self.log_path.read_text(encoding="utf-8", errors="replace")
        if len(text) > self.log_offset:
            self._append_log(text[self.log_offset :])
            self.log_offset = len(text)
        progress = run_progress_from_log(text)
        if progress is not None:
            self.progress_step_var.set(format_run_progress(progress))
            percent = progress.get("percent")
            if percent == 100 and self.process is not None:
                self._set_run_progress_indeterminate()
            elif isinstance(percent, int):
                self._set_run_progress_value(percent)

    def _reset_run_progress(self) -> None:
        for bar in self._progress_bars():
            try:
                bar.stop()
                bar.configure(mode="indeterminate")
                bar["value"] = 0
            except tk.TclError:
                pass

    def _set_run_progress_indeterminate(self) -> None:
        for bar in self._progress_bars():
            try:
                bar.stop()
                bar.configure(mode="indeterminate")
                bar["value"] = 0
                bar.start(12)
            except tk.TclError:
                pass

    def _stop_run_progress_bar(self) -> None:
        for bar in self._progress_bars():
            try:
                bar.stop()
            except tk.TclError:
                pass

    def _set_run_progress_value(self, percent: int) -> None:
        for bar in self._progress_bars():
            try:
                bar.stop()
                bar.configure(mode="determinate", maximum=100)
                bar["value"] = max(0, min(100, percent))
            except tk.TclError:
                pass

    def _progress_bars(self) -> list[ttk.Progressbar]:
        return [bar for bar in (self.run_progress_bar, self.run_progress_screen_bar) if bar is not None]

    def _append_log(self, text: str) -> None:
        self.log_text.insert("end", text)
        self.log_text.see("end")

    def _append_dicom_audit_summary(self, audit_json: Path) -> None:
        try:
            payload = json.loads(audit_json.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"DICOM確認サマリーを読めませんでした: {exc}\n")
            return

        tools = payload.get("optional_tools", {})
        any_transcoder = tools.get("any_transcoder")
        dicomdir = payload.get("dicomdir", {})
        self._append_log("\nDICOM確認サマリー:\n")
        self._append_log(f"  series数: {payload.get('series_count', 0)}\n")
        self._append_log(f"  optional transcoder利用可: {any_transcoder}\n")
        if dicomdir.get("dicomdir_file_count", 0):
            self._append_log(
                "  DICOMDIR: "
                f"解決 {dicomdir.get('resolved_reference_count', 0)}, "
                f"不足 {dicomdir.get('missing_reference_count', 0)}\n"
            )

        for index, series in enumerate(payload.get("series", [])[:12], start=1):
            classification = series.get("classification", {})
            series_number = series.get("series_number")
            description = series.get("series_description") or "(no description)"
            status = classification.get("status")
            next_action = classification.get("next_action")
            requires_tool = classification.get("requires_external_tool")
            self._append_log(
                f"  [{index}] series={series_number} {description}: "
                f"分類={_classification_label(status)}; "
                f"次の操作={_next_action_label(next_action)}; "
                f"外部ツール必要={requires_tool}\n"
            )
        if len(payload.get("series", [])) > 12:
            self._append_log("  ...追加seriesはUIログでは省略しました。JSONを確認してください。\n")
        self._append_log("  プレビュー作成はまだ開始していません。SwiftUI版では通常CTなら自動で取り込み準備へ進みます。\n")

    def _open_output(self) -> None:
        path = Path(self.output_var.get().strip())
        if not path.exists():
            messagebox.showerror(APP_TITLE, "結果フォルダが見つかりません。")
            return
        _open_path(path)

    def _open_case_preview(self) -> None:
        output_dir = Path(self.output_var.get().strip())
        preview = case_surface_preview(output_dir)
        if preview is None:
            messagebox.showerror(APP_TITLE, "3DプレビューHTMLが見つかりません。")
            return
        _open_path(preview)

    def _show_summary(self) -> None:
        output_dir = Path(self.output_var.get().strip())
        if not output_dir.exists():
            messagebox.showerror(APP_TITLE, "結果フォルダが見つかりません。")
            return
        self._set_log_visibility(True)
        self._append_log("\n" + format_case_summary_text(output_dir))

    def _open_slicer_script(self) -> None:
        script = Path(self.output_var.get().strip()) / "slicer" / "open_in_slicer.py"
        if not script.exists():
            messagebox.showerror(APP_TITLE, "Slicer用スクリプトが見つかりません。")
            return
        _open_path(script)

    def _open_sample_viewer(self) -> None:
        viewer = bundled_sample1_viewer()
        if viewer is None:
            messagebox.showerror(APP_TITLE, "Sample 1の3Dプレビューが見つかりません。")
            return
        self._set_stage(0)
        _open_path(viewer)

    def _check_updates(self) -> None:
        url = update_manifest_url()
        if not url:
            messagebox.showinfo(APP_TITLE, "更新確認URLは設定されていません。")
            return
        self.status_var.set("更新確認中")
        self._button_state(self.update_button, False)
        self._append_log("\nユーザー操作により更新確認を実行します。DICOM/CT/path/logは送信しません。\n")
        threading.Thread(target=self._worker_update_check, args=(url, update_allowed_hosts()), daemon=True).start()

    def _worker_update_check(self, manifest_url: str, allowed_hosts: set[str] | None) -> None:
        try:
            from totalsegmentator_wrapper_mac.update_check import check_for_update

            result = check_for_update(manifest_url=manifest_url, allowed_link_hosts=allowed_hosts).to_dict()
            self.events.put(("update_result", result))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("update_failed", repr(exc)))

    def _finish_update_check(self, result: dict[str, object]) -> None:
        self._button_state(self.update_button, True)
        status = str(result.get("status"))
        self.status_var.set("待機中")
        if status == "failed":
            self._append_log(f"更新確認に失敗しました: {result.get('error')}\n")
            return
        latest = result.get("latest_version")
        current = result.get("current_version")
        if result.get("update_available"):
            prefix = "重要な更新があります" if result.get("critical") else "更新があります"
            self._append_log(f"{prefix}: {current} -> {latest}\n")
            if result.get("sha256"):
                self._append_log(f"配布ファイルSHA256: {result.get('sha256')}\n")
            url = result.get("download_url") or result.get("release_notes_url")
            if url:
                should_open = messagebox.askyesno(
                    APP_TITLE,
                    f"{prefix}: {current} -> {latest}\n\n更新ページをブラウザで開きますか？",
                )
                if should_open:
                    _open_url(str(url))
                    self._append_log(f"更新ページをブラウザで開きました: {url}\n")
                else:
                    self._append_log("更新ページは開きませんでした。\n")
        else:
            self._append_log(f"現在のバージョンは最新です: {current}\n")


def _open_path(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607
    elif os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607


def _open_url(url: str) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", url])  # noqa: S603, S607
    elif os.name == "nt":
        os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", url])  # noqa: S603, S607


def main() -> None:
    root = tk.Tk()
    PreviewApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
