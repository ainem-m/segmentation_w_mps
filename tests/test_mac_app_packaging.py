from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_TEMPLATE = ROOT / "templates" / "mac_app_launcher.py"
ENTRYPOINT_TEMPLATE = ROOT / "templates" / "mac_app_entrypoint.c"
SWIFT_APP_DIR = ROOT / "native" / "macos" / "TotalSegmentatorWrapperForMac"
BUILD_SCRIPT = ROOT / "scripts" / "build_mac_app.sh"
WHEEL_BUILD_SCRIPT = ROOT / "scripts" / "build_mac_wheel.sh"
DMG_BUILD_SCRIPT = ROOT / "scripts" / "build_mac_dmg.sh"
NOTARIZE_SCRIPT = ROOT / "scripts" / "notarize_mac_dmg.sh"
DMG_VERIFY_SCRIPT = ROOT / "scripts" / "verify_zero_env_mac_dmg.sh"
EVIDENCE_SCRIPT = ROOT / "scripts" / "collect_test_account_install_evidence.sh"
EVIDENCE_IMPORT_SCRIPT = ROOT / "scripts" / "import_test_account_evidence.sh"
UI_TK = ROOT / "src" / "totalsegmentator_wrapper_mac" / "ui_tk.py"
SAMPLE1_ROOT = ROOT / "resources" / "sample1"
SAMPLE1_VIEWER_HTML = SAMPLE1_ROOT / "surface_preview" / "index.html"
SAMPLE1_MANIFEST = SAMPLE1_ROOT / "sample_manifest.json"
SAMPLE1_NOTICES = SAMPLE1_ROOT / "THIRD_PARTY_NOTICES.txt"


class MacAppPackagingTests(unittest.TestCase):
    def test_launcher_builds_argv_commands(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            support = root / "Library" / "Application Support" / "TotalSegmentatorWrapperMac"
            wheel = root / "totalsegmentator_wrapper_mac-0.1.0-cp312-cp312-macosx_11_0_arm64.whl"
            constraints = root / "constraints.txt"
            setup_json = support / "logs" / "setup_result.json"
            progress_log = support / "logs" / "launcher.log"
            bundle_manifest = root / "Resources" / "setup_manifest.json"
            python = support / "env" / "bin" / "python"
            python312 = root / "python3.12"

            commands = [
                launcher.build_create_venv_command(python312, support),
                launcher.build_bootstrap_install_command(python, wheel),
                launcher.build_setup_command(
                    python,
                    wheel,
                    setup_json,
                    python312=python312,
                    constraints=constraints,
                    allow_network=True,
                    skip_mps_check=True,
                    progress_log=progress_log,
                    bundle_manifest=bundle_manifest,
                ),
                launcher.build_ui_command(python),
            ]

            for command in commands:
                self.assertIsInstance(command, list)
                self.assertNotIn("sudo", command)
                self.assertNotIn("brew", command)
                self.assertNotIn(";", " ".join(command))

            self.assertIn("--force-reinstall", commands[1])
            self.assertIn("--no-deps", commands[1])
            self.assertIn("--allow-network", commands[2])
            self.assertIn("--skip-mps-check", commands[2])
            self.assertIn("--python", commands[2])
            self.assertIn(str(python312), commands[2])
            self.assertIn("--constraints", commands[2])
            self.assertIn(str(constraints), commands[2])
            self.assertIn("--use-existing-env", commands[2])
            self.assertIn("--progress-log", commands[2])
            self.assertIn(str(progress_log), commands[2])
            self.assertIn("--bundle-manifest", commands[2])
            self.assertIn(str(bundle_manifest), commands[2])

    def test_launcher_setup_window_is_japanese_and_progress_visible(self) -> None:
        text = LAUNCHER_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("TotalSegmentator Wrapper for Mac セットアップ", text)
        self.assertIn("セットアップ開始", text)
        self.assertIn("終了", text)
        self.assertIn("管理者権限は不要です", text)
        self.assertIn("App Support配下のみ書き込み", text)
        self.assertIn("DICOM/CT/処理結果は送信しません", text)
        self.assertIn("初回Setupまたは明示的な依存更新時のみ", text)
        self.assertIn("setup_context_message", text)
        self.assertIn("セットアップ開始を押すまで通信しません", text)
        self.assertIn("ttk.Progressbar", text)
        self.assertIn('mode="indeterminate"', text)
        self.assertIn("elapsed_var", text)
        self.assertIn("root.after(1000, poll_setup_log)", text)
        self.assertIn("read_new_log_text", text)
        self.assertIn("logs\" / \"launcher.log", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_SUPPRESS_STDOUT_JSON", text)
        self.assertIn("def exec_ui", text)
        self.assertIn("os.execve", text)
        self.assertIn("os.dup2", text)
        self.assertIn("UI exec failed", text)
        self.assertIn("3Dサンプルを開く", text)
        self.assertIn("セットアップ中も、Sample 1の3Dプレビュー", text)
        self.assertIn("def open_demo_viewer", text)
        self.assertIn('command = ["open", str(demo_html)]', text)
        self.assertIn("subprocess.Popen(command)", text)
        self.assertNotIn("shell=True", text)

    def test_launcher_translates_setup_reasons_and_progress_lines(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "launcher.log"

            self.assertEqual(launcher.setup_reason_to_japanese("needs_network"), "ネットワーク接続が必要です。")
            self.assertEqual(launcher.setup_reason_to_japanese("mps_unavailable"), "MPS確認に失敗しました。")
            self.assertEqual(launcher.setup_reason_to_japanese("python312_missing"), "同梱Python 3.12が見つかりません。")
            self.assertEqual(launcher.setup_reason_to_japanese("runtime_install_failed"), "依存パッケージの導入に失敗しました。")
            self.assertEqual(launcher.setup_reason_to_japanese("bundle_manifest_invalid"), "アプリ同梱manifestを読めません。")
            self.assertEqual(launcher.setup_step_to_japanese("install_wheel"), "依存パッケージ取得")
            self.assertEqual(launcher.setup_step_to_japanese("sync_bundle"), "アプリ更新反映")
            self.assertIn("数分かかる", launcher.setup_hint_for_step("install_wheel"))

            log_path.write_text(
                "old\nSETUP_PROGRESS step=doctor status=running message=MPS確認中\n",
                encoding="utf-8",
            )
            new_text, pos = launcher.read_new_log_text(log_path, 0)
            self.assertIn("SETUP_PROGRESS", new_text)
            self.assertEqual(pos, log_path.stat().st_size)
            self.assertEqual(
                launcher.progress_step_from_log_line("SETUP_PROGRESS step=doctor status=running message=MPS確認中"),
                "doctor",
            )
            self.assertEqual(
                launcher.display_log_line("SETUP_PROGRESS step=doctor status=running message=MPS確認中"),
                "MPS確認中",
            )

    def test_setup_state_success_check_is_conservative(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "setup_state.json"

            self.assertFalse(launcher.setup_state_is_successful(state))
            state.write_text('{"status": "failed"}', encoding="utf-8")
            self.assertFalse(launcher.setup_state_is_successful(state))
            state.write_text('{"status": "success"}', encoding="utf-8")
            self.assertTrue(launcher.setup_state_is_successful(state))

    def test_launcher_bundle_sync_detects_wheel_and_dependency_changes(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resources = root / "Resources"
            resources.mkdir()
            state = root / "setup_state.json"
            manifest_payload = {
                "app_version": "0.1.0",
                "build_id": "build-b",
                "dependency_set_id": "deps-a",
                "wheel_sha256": "wheel-b",
                "constraints_sha256": "constraints-a",
                "normalizer_sha256": "normalizer-a",
                "dcm2niix_sha256": "dcm-a",
                "sample1_manifest_sha256": "sample-a",
                "update_manifest_url": "",
            }
            (resources / "setup_manifest.json").write_text(json.dumps(manifest_payload), encoding="utf-8")

            state.write_text(
                json.dumps(
                    {
                        "status": "success",
                        "installed_bundle": {
                            "schema": "totalsegmentator_wrapper_mac.installed_bundle.v1",
                            "app_version": "0.1.0",
                            "build_id": "build-a",
                            "dependency_set_id": "deps-a",
                            "wheel_sha256": "wheel-a",
                            "constraints_sha256": "constraints-a",
                            "normalizer_sha256": "normalizer-a",
                            "dcm2niix_sha256": "dcm-a",
                            "sample1_manifest_sha256": "sample-a",
                            "update_manifest_url": "",
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(launcher.setup_state_is_current(state, resources))
            self.assertEqual(launcher.bundle_sync_status(state, resources)["action"], "resync_wheel")

            manifest_payload["dependency_set_id"] = "deps-b"
            (resources / "setup_manifest.json").write_text(json.dumps(manifest_payload), encoding="utf-8")
            self.assertEqual(launcher.bundle_sync_status(state, resources)["action"], "setup_required")

    def test_launcher_bundle_sync_requires_installed_wheel_marker(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resources = root / "Resources"
            resources.mkdir()
            state = root / "setup_state.json"
            manifest_payload = {
                "app_version": "0.1.0",
                "build_id": "build-a",
                "dependency_set_id": "deps-a",
                "wheel_sha256": "wheel-a",
                "constraints_sha256": "constraints-a",
                "normalizer_sha256": "normalizer-a",
                "dcm2niix_sha256": "dcm-a",
                "sample1_manifest_sha256": "sample-a",
                "update_manifest_url": "",
            }
            (resources / "setup_manifest.json").write_text(json.dumps(manifest_payload), encoding="utf-8")
            state.write_text(
                json.dumps(
                    {
                        "status": "success",
                        "installed_bundle": {
                            "schema": "totalsegmentator_wrapper_mac.installed_bundle.v1",
                            **manifest_payload,
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(launcher.bundle_sync_status(state, resources)["action"], "resync_wheel")
            launcher.installed_wheel_marker_path(root).write_text("wheel-a\n", encoding="utf-8")
            self.assertTrue(launcher.setup_state_is_current(state, resources))
            launcher.installed_wheel_marker_path(root).write_text("wheel-old\n", encoding="utf-8")
            self.assertEqual(launcher.bundle_sync_status(state, resources)["reason"], "wheel_marker_missing_or_stale")

    def test_launcher_resync_command_reinstalls_wheel_without_network_dependencies(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = launcher.build_resync_wheel_command(
                root / "env" / "bin" / "python",
                root / "totalsegmentator_wrapper_mac.whl",
            )

            self.assertIn("--force-reinstall", command)
            self.assertIn("--no-deps", command)
            self.assertNotIn("-c", command)
            self.assertNotIn("--allow-network", command)

    def test_launcher_resync_updates_installed_bundle_state(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            support = root / "Library" / "Application Support" / "TotalSegmentatorWrapperMac"
            resources = root / "Resources"
            fake_python = support / "env" / "bin" / "python"
            wheel = root / "totalsegmentator_wrapper_mac.whl"
            log_path = support / "logs" / "launcher.log"
            args_log = root / "pip_args.txt"
            resources.mkdir(parents=True)
            fake_python.parent.mkdir(parents=True)
            wheel.write_bytes(b"fake wheel")
            fake_python.write_text(f'#!/bin/sh\necho "$@" > "{args_log}"\nexit 0\n', encoding="utf-8")
            os.chmod(fake_python, 0o755)
            manifest_payload = {
                "app_version": "0.1.0",
                "build_id": "build-b",
                "dependency_set_id": "deps-a",
                "wheel_sha256": "wheel-b",
                "constraints_sha256": "constraints-a",
                "normalizer_sha256": "normalizer-a",
                "dcm2niix_sha256": "dcm-a",
                "sample1_manifest_sha256": "sample-a",
                "update_manifest_url": "",
            }
            (resources / "setup_manifest.json").write_text(json.dumps(manifest_payload), encoding="utf-8")
            (support / "setup_state.json").parent.mkdir(parents=True, exist_ok=True)
            (support / "setup_state.json").write_text(
                json.dumps(
                    {
                        "status": "success",
                        "installed_bundle": {
                            "schema": "totalsegmentator_wrapper_mac.installed_bundle.v1",
                            "app_version": "0.1.0",
                            "build_id": "build-a",
                            "dependency_set_id": "deps-a",
                            "wheel_sha256": "wheel-a",
                            "constraints_sha256": "constraints-a",
                            "normalizer_sha256": "normalizer-a",
                            "dcm2niix_sha256": "dcm-a",
                            "sample1_manifest_sha256": "sample-a",
                            "update_manifest_url": "",
                        },
                    }
                ),
                encoding="utf-8",
            )

            rc = launcher.resync_installed_bundle(
                support=support,
                wheel=wheel,
                resources=resources,
                log_path=log_path,
            )

            self.assertEqual(rc, 0)
            self.assertIn("--force-reinstall --no-deps", args_log.read_text(encoding="utf-8"))
            state = json.loads((support / "setup_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["installed_bundle"], launcher.current_bundle_record(resources))
            self.assertEqual(state["last_bundle_resync"]["reason"], "wheel_resync")
            self.assertEqual(
                launcher.installed_wheel_marker_path(support).read_text(encoding="utf-8").strip(),
                "wheel-b",
            )

    def test_launcher_opens_demo_viewer_with_argv_command(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resources = root / "Resources"
            demo_html = resources / "sample1" / "surface_preview" / "index.html"
            log_path = root / "Library" / "Application Support" / "TotalSegmentatorWrapperMac" / "logs" / "launcher.log"
            demo_html.parent.mkdir(parents=True)
            demo_html.write_text("<!doctype html><title>demo</title>", encoding="utf-8")
            calls = []
            original_popen = launcher.subprocess.Popen
            launcher.subprocess.Popen = lambda command: calls.append(command) or object()
            try:
                ok, message = launcher.open_demo_viewer(resources, log_path)
            finally:
                launcher.subprocess.Popen = original_popen

            self.assertTrue(ok)
            self.assertIn("ブラウザで開きました", message)
            self.assertEqual(calls, [["open", str(demo_html)]])
            self.assertIn("$ open ", log_path.read_text(encoding="utf-8"))

    def test_build_mac_app_script_has_expected_bundle_steps(self) -> None:
        text = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('APP_NAME="TotalSegmentator Wrapper for Mac"', text)
        self.assertIn('APP_DIR="${DIST_DIR}/${APP_NAME}.app"', text)
        self.assertIn('MACOS_DIR="${CONTENTS_DIR}/MacOS"', text)
        self.assertIn('RESOURCES_DIR="${CONTENTS_DIR}/Resources"', text)
        self.assertIn("SWIFT_APP_SOURCE_DIR", text)
        self.assertIn("native/macos/TotalSegmentatorWrapperForMac", text)
        self.assertIn("require_full_xcode", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_XCODE_DEVELOPER_DIR", text)
        self.assertIn("/Applications/Xcode.app/Contents/Developer", text)
        self.assertIn("export DEVELOPER_DIR", text)
        self.assertIn("xcodebuild -version", text)
        self.assertIn("Command Line Tools alone are not enough", text)
        self.assertIn("build_swiftui_frontend", text)
        self.assertIn("xcrun --sdk macosx swiftc", text)
        self.assertIn("SWIFT_MODULE_CACHE_PATH", text)
        self.assertIn("-module-cache-path", text)
        self.assertIn("-fmodules-cache-path", text)
        self.assertIn("-framework SwiftUI", text)
        self.assertIn("-target arm64-apple-macos13.0", text)
        self.assertIn("CommandBuilder.swift", text)
        self.assertIn("TotalSegmentatorWrapperForMacApp.swift", text)
        self.assertNotIn('cc "${ROOT}/templates/mac_app_entrypoint.c"', text)
        self.assertIn('${RESOURCES_DIR}/launcher', text)
        self.assertIn("launcher/mac_app_launcher.py", text)
        self.assertIn("resources/sample1", text)
        self.assertIn("sample1/surface_preview/index.html", text)
        self.assertIn("sample1/input/DZ-CBCT_jawcrop_0p5mm.nii.gz", text)
        self.assertIn("sample1/THIRD_PARTY_NOTICES.txt", text)
        self.assertIn("setup_manifest.json", text)
        self.assertIn('"ui_frontend": "swiftui"', text)
        self.assertIn('"legacy_tk_ui": true', text)
        self.assertIn("constraints/macos-arm64-py312.txt", text)
        self.assertIn("external_python312_required", text)
        self.assertIn("sys.base_prefix", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_EXTERNAL_PYTHON_RUNTIME", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_PYTHON_RUNTIME_DIR", text)
        self.assertIn("bundled_python312", text)
        self.assertIn("python/cpython-3.12/bin/python3.12", text)
        self.assertIn("xattr -cr", text)
        self.assertIn("find \"${RESOURCES_DIR}/python/cpython-3.12\" -type d", text)
        self.assertIn("find \"${RESOURCES_DIR}/python/cpython-3.12\" -type f -exec chmod a-w", text)
        self.assertIn("codesign --force --deep --sign -", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER", text)
        self.assertIn("require_developer_id_signing", text)
        self.assertIn("codesign_developer_id", text)
        self.assertIn("--timestamp", text)
        self.assertIn("--options runtime", text)
        self.assertIn("--entitlements", text)
        self.assertIn("resources/entitlements/app.entitlements", text)
        self.assertIn("resources/entitlements/python-runtime.entitlements", text)
        self.assertIn("security find-identity -v -p codesigning", text)
        self.assertIn('"signing_mode": "${SIGNING_MODE}"', text)
        self.assertIn('"bundle_identifier": ${BUNDLE_IDENTIFIER_JSON}', text)
        self.assertIn('"notarization_profile_name": ${NOTARY_PROFILE_JSON}', text)
        self.assertIn('"notarized": ${NOTARIZED_JSON}', text)
        self.assertIn('"sample1": {', text)
        self.assertIn("sha256_file", text)
        self.assertIn('BUILD_ID="${TOTALSEGMENTATOR_WRAPPER_MAC_BUILD_ID:-}"', text)
        self.assertIn('BUILD_ID="app-${APP_VERSION}-${WHEEL_SHA256:0:12}', text)
        self.assertIn("wheel_sha256", text)
        self.assertIn("constraints_sha256", text)
        self.assertIn("normalizer_sha256", text)
        self.assertIn("dcm2niix_sha256", text)
        self.assertIn("dcm2niix_version", text)
        self.assertIn("dcm2niix_source", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX", text)
        self.assertIn('cp "${DCM2NIIX_PATH}" "${RESOURCES_DIR}/bin/dcm2niix"', text)
        self.assertIn('"dcm2niix": "bin/dcm2niix"', text)
        self.assertIn("THIRD_PARTY_NOTICES.txt", text)
        self.assertIn("BSD license", text)
        self.assertIn("public domain or MIT licensed", text)
        self.assertIn("sample1_manifest_sha256", text)
        self.assertIn("dependency_set_id", text)
        self.assertIn("pydicom3", text)
        self.assertIn("update_manifest_url", text)
        self.assertIn("update_allowed_hosts", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_ALLOWED_HOSTS", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("brew install", text)

    def test_notarization_script_submits_staples_and_validates_dmg(self) -> None:
        text = NOTARIZE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_NOTARY_PROFILE", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_IDENTIFIER", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE=developer-id", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_NOTARIZED=1", text)
        self.assertIn("codesign --force --timestamp --sign", text)
        self.assertIn("notarytool submit", text)
        self.assertIn("--keychain-profile", text)
        self.assertIn("--wait", text)
        self.assertIn("--output-format json", text)
        self.assertIn("notary_submission.json", text)
        self.assertIn("notary_log.json", text)
        self.assertIn("stapler staple", text)
        self.assertIn("stapler validate", text)
        self.assertIn("spctl --assess --type open", text)
        self.assertIn("spctl --assess --type execute", text)
        self.assertIn("hdiutil attach", text)
        self.assertNotIn("AuthKey_", text)
        self.assertNotIn("--password", text)

    def test_swiftui_frontend_sources_cover_setup_main_and_safe_commands(self) -> None:
        texts = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(SWIFT_APP_DIR.glob("*.swift"))
        }
        combined = "\n".join(texts.values())

        for name in (
            "CommandBuilder.swift",
            "ProcessSupport.swift",
            "AppState.swift",
            "Views.swift",
            "TotalSegmentatorWrapperForMacApp.swift",
        ):
            self.assertIn(name, texts)

        self.assertIn("@main", texts["TotalSegmentatorWrapperForMacApp.swift"])
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_HEADLESS", texts["TotalSegmentatorWrapperForMacApp.swift"])
        self.assertIn("SetupCoordinator.runSetup", texts["TotalSegmentatorWrapperForMacApp.swift"])
        self.assertIn("NavigationSplitView", texts["Views.swift"])
        self.assertIn("Sampleで流れを体験する", texts["Views.swift"])
        self.assertIn("自分のCTを開く", texts["Views.swift"])
        self.assertIn("CTを選ぶ", texts["Views.swift"])
        self.assertIn("詳細ログを表示", texts["Views.swift"])
        self.assertIn("ログファイルを開く", texts["Views.swift"])
        self.assertIn("ログフォルダを開く", texts["Views.swift"])
        self.assertIn("logInfoText", texts["Views.swift"])
        self.assertIn("state.showDetailedLog()", texts["Views.swift"])
        self.assertIn("isExpanded: logExpanded", texts["Views.swift"])
        self.assertNotIn("TextEditor(text: $state.logText)", texts["Views.swift"])
        self.assertIn("100秒前後", texts["Views.swift"])
        self.assertIn("この撮影を使う", texts["Views.swift"])
        self.assertIn("3Dプレビューを再生成", texts["Views.swift"])
        self.assertIn("結果の要約", texts["Views.swift"])
        self.assertIn("詳細ログを表示", texts["Views.swift"])
        self.assertIn("confirmationDialog", texts["Views.swift"])
        self.assertIn("Process()", texts["ProcessSupport.swift"])
        self.assertIn("executableURL", texts["ProcessSupport.swift"])
        self.assertIn("arguments = Array(command.dropFirst())", texts["ProcessSupport.swift"])
        self.assertIn("SIGKILL", texts["ProcessSupport.swift"])
        self.assertIn("env/bin/TotalSegmentator", texts["CommandBuilder.swift"])
        self.assertIn('ProcessInfo.processInfo.environment["HOME"]', texts["CommandBuilder.swift"])
        self.assertIn('if python.hasPrefix("/")', texts["CommandBuilder.swift"])
        self.assertIn("inferBundleResourcesFromExecutable", texts["CommandBuilder.swift"])
        self.assertIn("Bundle.main.executableURL", texts["CommandBuilder.swift"])
        self.assertIn("resourcesURL(fromBundle", texts["CommandBuilder.swift"])
        self.assertIn("_NSGetExecutablePath", texts["CommandBuilder.swift"])
        self.assertIn("resourcesURL(fromExecutable", texts["CommandBuilder.swift"])
        self.assertIn('"Contents"', texts["CommandBuilder.swift"])
        self.assertIn('"Resources"', texts["CommandBuilder.swift"])
        self.assertIn('"Library/Application Support"', texts["CommandBuilder.swift"])
        self.assertIn('"--totalseg-bin"', texts["CommandBuilder.swift"])
        self.assertIn("dicom-normalizer-audit", texts["CommandBuilder.swift"])
        self.assertIn("dicom-normalizer-convert-clean", texts["CommandBuilder.swift"])
        self.assertIn("var dcm2niix: URL", texts["CommandBuilder.swift"])
        self.assertIn('"--dcm2niix"', texts["CommandBuilder.swift"])
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX", texts["CommandBuilder.swift"])
        self.assertNotIn("/opt/homebrew/bin", texts["CommandBuilder.swift"])
        self.assertNotIn("/usr/local/bin", texts["CommandBuilder.swift"])
        self.assertIn('"--timeout-sec"', texts["CommandBuilder.swift"])
        self.assertIn('"120"', texts["CommandBuilder.swift"])
        self.assertIn("surface-preview", texts["CommandBuilder.swift"])
        self.assertIn("surfacePreviewCommand", texts["CommandBuilder.swift"])
        self.assertIn("update-check", texts["CommandBuilder.swift"])
        self.assertIn("updateCheckRunning", texts["AppState.swift"])
        self.assertIn("updateInstallRunning", texts["AppState.swift"])
        self.assertIn("configure_totalseg_privacy", texts["CommandBuilder.swift"])
        self.assertIn("利用状況データ", texts["CommandBuilder.swift"])
        self.assertIn("totalseg_privacy_config_failed", texts["CommandBuilder.swift"])
        self.assertIn("setupRecoverySuggestion", texts["CommandBuilder.swift"])
        self.assertIn("let updateRunner = ProcessRunner()", texts["AppState.swift"])
        self.assertIn("downloadAndInstallPendingUpdate", texts["AppState.swift"])
        self.assertIn("pendingUpdateSHA256", texts["AppState.swift"])
        self.assertIn("sha256Hex", texts["AppState.swift"])
        self.assertIn("writeUpdateInstallerScript", texts["AppState.swift"])
        self.assertIn("spctl --assess --type execute", texts["AppState.swift"])
        self.assertIn("/usr/bin/ditto", texts["AppState.swift"])
        self.assertIn("更新をインストール", texts["Views.swift"])
        self.assertIn("enum InputSource", texts["AppState.swift"])
        self.assertIn("canStartSampleRun", texts["AppState.swift"])
        self.assertIn("canStartOwnDataRun", texts["AppState.swift"])
        self.assertIn("canUseSelectedDicomSeries", texts["AppState.swift"])
        self.assertIn("dicomCleanCandidates", texts["AppState.swift"])
        self.assertIn("convertDicomToNiftiFromAudit", texts["AppState.swift"])
        self.assertIn("cleanDicomSeriesCandidates", texts["AppState.swift"])
        self.assertIn("convertedNiftiURL", texts["AppState.swift"])
        self.assertIn("ownDataPrimaryButtonTitle", texts["AppState.swift"])
        self.assertIn("inputSource == .dicomFolder || isDirectory(inputURL)", texts["AppState.swift"])
        self.assertIn("guard inputSource == .sample || inputSource == .nifti", texts["AppState.swift"])
        self.assertIn("let output = nextCaseOutput()", texts["AppState.swift"])
        self.assertIn("regenerateSurfacePreview", texts["AppState.swift"])
        self.assertIn("CT解析は再実行せず", texts["AppState.swift"])
        self.assertIn("stopRequested", texts["AppState.swift"])
        self.assertIn("停止要求済み", texts["AppState.swift"])
        self.assertIn("showingUpdateConfirmation", texts["AppState.swift"])
        self.assertIn("confirmOpenPendingDownload", texts["AppState.swift"])
        self.assertIn("pendingDownloadURL = nil", texts["AppState.swift"])
        self.assertIn("stoppedBeforeSummary", texts["AppState.swift"])
        self.assertIn("runner.resetTerminationRequest()", texts["AppState.swift"])
        self.assertNotIn("rc == 0 && modeForRun == .individualTeeth", texts["AppState.swift"])
        self.assertIn("歯列・顎骨", texts["AppState.swift"])
        self.assertIn("CommandBuilder.surfacePreviewCommand", texts["AppState.swift"])
        self.assertIn("3Dプレビュー作成中", texts["AppState.swift"])
        self.assertIn("3Dプレビューを作成しました", texts["AppState.swift"])
        self.assertIn("3Dプレビュー生成に失敗しました", texts["AppState.swift"])
        self.assertIn("runProgressFromLog", texts["AppState.swift"])
        self.assertIn("runProgressFraction", texts["AppState.swift"])
        self.assertIn("RUN_PROGRESS ", texts["AppState.swift"])
        self.assertIn("LOG_TAIL_BYTES", texts["AppState.swift"])
        self.assertIn("readLogTail", texts["AppState.swift"])
        self.assertIn("openCurrentLogFile", texts["AppState.swift"])
        self.assertIn("openCurrentLogFolder", texts["AppState.swift"])
        self.assertIn("showDetailedLog", texts["AppState.swift"])
        self.assertIn("let target = url ?? currentLogURL", texts["AppState.swift"])
        self.assertIn("最後の一部だけ表示", texts["AppState.swift"])
        self.assertIn("stage: stringFromJSON", texts["AppState.swift"])
        self.assertIn("percent == 100", texts["AppState.swift"])
        self.assertIn("次の処理へ進んでいます", texts["AppState.swift"])
        self.assertIn("runHeartbeatText", texts["AppState.swift"])
        self.assertIn("lastRunProgressAt", texts["AppState.swift"])
        self.assertIn("lastRunProgressSignature", texts["AppState.swift"])
        self.assertIn("updateRunHeartbeat", texts["AppState.swift"])
        self.assertIn("最終更新:", texts["AppState.swift"])
        self.assertIn("大きなデータではこの待ち時間が発生します", texts["AppState.swift"])
        self.assertIn("ProgressView(value: fraction)", texts["Views.swift"])
        self.assertIn("Text(state.runHeartbeatText)", texts["Views.swift"])
        self.assertIn("CTを選び直す", texts["Views.swift"])
        self.assertNotIn("NIfTIへ変換して入力に使う", texts["Views.swift"])
        self.assertIn("retryButtonTitle", texts["Views.swift"])
        self.assertIn("もう一度実行", texts["AppState.swift"])
        self.assertIn("もう一度確認", texts["AppState.swift"])
        self.assertIn("canRetryFromResult", texts["AppState.swift"])
        self.assertIn("lastDicomDirURL != nil", texts["AppState.swift"])
        self.assertIn("最初に戻る", texts["Views.swift"])
        self.assertIn("goToInput", texts["AppState.swift"])
        self.assertIn("goToStart", texts["AppState.swift"])
        self.assertIn("goToSample", texts["AppState.swift"])
        self.assertIn("goToOwnData", texts["AppState.swift"])
        self.assertIn("retryRunFromResult", texts["AppState.swift"])
        self.assertIn("resultKind == .dicomAudit", texts["AppState.swift"])
        self.assertIn("runDicomAudit(dicomDir: lastDicomDirURL)", texts["AppState.swift"])
        self.assertIn("isDirectory(inputURL)", texts["AppState.swift"])
        self.assertIn("contentShape(Rectangle())", texts["Views.swift"])
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER", texts["CommandBuilder.swift"])
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX", texts["CommandBuilder.swift"])
        self.assertIn("DICOM/CT/結果は送信しません", texts["Views.swift"])
        self.assertIn("利用状況データの送信も止めます", texts["Views.swift"])
        self.assertIn("初回はモデル取得", texts["Views.swift"])
        self.assertIn("setupReasonToJapanese", texts["CommandBuilder.swift"])
        self.assertNotIn("shell=True", combined)
        self.assertNotIn("/usr/bin/env python3", combined)

    def test_bundled_sample1_viewer_is_offline_html(self) -> None:
        text = SAMPLE1_VIEWER_HTML.read_text(encoding="utf-8")

        self.assertIn("TotalSegmentator Wrapper 3Dプレビュー", text)
        self.assertIn("const DATA =", text)
        self.assertIn("modeTrackpad", text)
        self.assertIn("トラックパッド", text)
        self.assertIn("マウス", text)
        self.assertIn("全体表示", text)
        self.assertIn("なめらかさ", text)
        self.assertIn("面数", text)
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("cdn", text.lower())
        self.assertNotIn("<script src=", text.lower())

    def test_bundled_sample1_manifest_and_notices_document_license_and_purpose(self) -> None:
        manifest = json.loads(SAMPLE1_MANIFEST.read_text(encoding="utf-8"))
        notices = SAMPLE1_NOTICES.read_text(encoding="utf-8")

        self.assertEqual(manifest["sample_id"], "sample1_dz_cbct_jawcrop_0p5mm")
        self.assertEqual(manifest["default_input"], "input/DZ-CBCT_jawcrop_0p5mm.nii.gz")
        self.assertEqual(manifest["surface_preview"], "surface_preview/index.html")
        self.assertEqual(manifest["expected_runtime_seconds_approx"], 100)
        self.assertIn("100秒前後", manifest["expected_runtime_note_ja"])
        self.assertFalse(manifest["clinical_use"])
        self.assertIn("CBCT-MR Head", notices)
        self.assertIn("unrestricted", notices)
        self.assertIn("4ce7aa75278b5a7b757ed0c8d7a6b3caccfc3e2973b020532456dbc8f3def7db", notices)
        self.assertIn("TotalSegmentator", notices)
        self.assertIn("Apache License 2.0", notices)
        self.assertIn("not for diagnosis", notices)

    def test_bundled_sample1_metadata_does_not_expose_developer_local_paths(self) -> None:
        checked = [
            *sorted((SAMPLE1_ROOT / "logs").glob("*.json")),
            SAMPLE1_ROOT / "surface_preview" / "preview_summary.json",
        ]
        self.assertTrue(checked)
        for path in checked:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("/Users/ainem", text)
                self.assertNotIn("segmentation_w_mps", text)
                self.assertNotIn(".venv", text)

    def test_tk_ui_defaults_to_bundled_sample1_and_app_support_output(self) -> None:
        text = UI_TK.read_text(encoding="utf-8")

        self.assertIn("def bundled_sample1_input", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_RESOURCES_DIR", text)
        self.assertIn("sample1", text)
        self.assertIn("DZ-CBCT_jawcrop_0p5mm.nii.gz", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_APP_SUPPORT", text)
        self.assertIn('"runs"', text)
        self.assertIn("Sampleで流れを体験する", text)
        self.assertIn("自分のCT/NIfTIを開く", text)
        self.assertIn("Sample 1の3Dプレビューを開く", text)
        self.assertIn("Sample 1を入力に使う", text)
        self.assertIn("100秒前後", text)
        self.assertIn("NIfTIファイルを選ぶ", text)
        self.assertIn("DICOMフォルダを確認する", text)
        self.assertIn("詳細ログを表示", text)
        self.assertIn("実行開始", text)
        self.assertIn("結果フォルダを開く", text)
        self.assertIn("3Dプレビューを開く", text)
        self.assertIn("def _open_sample_viewer", text)
        self.assertIn("更新を確認", text)
        self.assertIn("def _check_updates", text)
        self.assertIn("update_manifest_url", text)
        self.assertIn("update_allowed_hosts", text)
        self.assertIn("DICOM/CT/path/logは送信しません", text)
        self.assertIn("messagebox.askyesno", text)
        self.assertIn("更新ページをブラウザで開きますか？", text)
        self.assertIn("配布ファイルSHA256", text)

    def test_mac_app_entrypoint_uses_bundled_python_without_system_python(self) -> None:
        text = ENTRYPOINT_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("_NSGetExecutablePath", text)
        self.assertIn("python/cpython-3.12/bin/python3.12", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_RESOURCES_DIR", text)
        self.assertIn("PYTHONPYCACHEPREFIX", text)
        self.assertIn('setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin", 1)', text)
        self.assertIn('setenv("TMPDIR", "/tmp", 1)', text)
        self.assertIn("execv(python, child_argv)", text)
        self.assertNotIn("/usr/bin/env python3", text)
        self.assertNotIn("brew", text)

    def test_build_mac_wheel_uses_pep517_frontend(self) -> None:
        text = WHEEL_BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("uv is required", text)
        self.assertIn("UV_CACHE_DIR", text)
        self.assertIn("SOURCE_DATE_EPOCH", text)
        self.assertIn("build --wheel --no-build-isolation", text)
        self.assertIn("--python \"${PYTHON_BIN}\"", text)
        self.assertIn("--no-build-isolation", text)
        self.assertIn("--config-setting=\"--build-option=--plat-name\"", text)
        self.assertIn("BinaryDistribution", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_SIGNING_MODE", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_CODESIGN_IDENTITY", text)
        self.assertIn("codesign \\", text)
        self.assertIn("--timestamp", text)
        self.assertIn("--options runtime", text)
        self.assertIn("src/totalsegmentator_wrapper_mac/bin/totalsegmentator-wrapper-dicom-normalizer", text)
        self.assertNotIn("setup.py bdist_wheel", text)
        self.assertNotIn("from wheel.bdist_wheel", text)

    def test_mac_constraints_use_pydicom3_for_dicom2nifti(self) -> None:
        constraints = (ROOT / "constraints" / "macos-arm64-py312.txt").read_text(encoding="utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("pydicom>=3,<4", constraints)
        self.assertIn('"pydicom>=3,<4"', pyproject)
        self.assertNotIn("pydicom>=2.4,<3", constraints)

    def test_dmg_scripts_support_user_local_install_validation(self) -> None:
        build_text = DMG_BUILD_SCRIPT.read_text(encoding="utf-8")
        verify_text = DMG_VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("hdiutil create", build_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_SKIP_APP_BUILD", build_text)
        self.assertIn('scripts/build_mac_app.sh', build_text)
        self.assertIn("chmod -R u+rwX", build_text)
        self.assertIn('ditto "${APP_PATH}" "${DMG_STAGING}/${APP_NAME}.app"', build_text)
        self.assertIn("README.txt", build_text)
        self.assertIn("TEST_ACCOUNT_INSTALL.txt", build_text)
        self.assertIn("ln -s /Applications", build_text)
        self.assertIn("Verify Test Account Install.command", build_text)
        self.assertIn("collect_test_account_install_evidence.sh", build_text)
        self.assertIn("Collect TotalSegmentator Wrapper Logs.command", build_text)
        self.assertIn("collect_launch_debug_logs.sh", build_text)
        self.assertIn("/Users/Shared/TotalSegmentatorWrapperMac", build_text)
        self.assertIn("セットアップ開始", build_text)
        self.assertIn("3Dサンプルを開く", build_text)
        self.assertIn("同梱Sample 1のオフライン3Dプレビュー", build_text)
        self.assertIn("同梱Sample 1のCT入力", build_text)
        self.assertIn("100秒前後", build_text)
        self.assertIn("モデル取得済みの場合", build_text)
        self.assertIn("利用状況データ", build_text)
        self.assertIn("セットアップ中もプレビュー作成中も送信しません", build_text)
        self.assertIn("表示用の断面画像", build_text)
        self.assertIn("CT画像そのものが壊れているとは限りません", build_text)
        self.assertIn("CTを書き出したソフト名", build_text)
        self.assertIn("ログにはローカルパス", build_text)
        self.assertIn("更新を確認", build_text)
        self.assertIn("起動時やSetup中に自動確認しません", build_text)
        self.assertIn("notarized DMGをダウンロード", build_text)
        self.assertIn("SHA256とGatekeeper確認", build_text)
        self.assertIn("アプリを置き換えて再起動", build_text)
        self.assertIn("THIRD_PARTY_NOTICES.txt", build_text)
        self.assertIn("Apache-2.0", build_text)
        self.assertIn("精度評価用データではありません", build_text)
        self.assertIn("管理者権限", build_text)
        self.assertIn("DICOM、CT", build_text)
        self.assertIn("Controlキー", build_text)
        self.assertIn("MANIFEST_NOTARIZED", build_text)
        self.assertIn("notarized済みDMG", build_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE", build_text)
        self.assertIn("hdiutil attach", verify_text)
        self.assertIn('ditto "${MOUNT_ROOT}/TotalSegmentator Wrapper for Mac.app"', verify_text)
        self.assertIn("README.txt", verify_text)
        self.assertIn("TEST_ACCOUNT_INSTALL.txt", verify_text)
        self.assertIn("Verify Test Account Install.command", verify_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE", verify_text)
        self.assertIn('${TEST_HOME}/Applications', verify_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_HEADLESS=1", verify_text)
        self.assertIn('"status": "success"', verify_text)
        self.assertIn('"actual_device": "mps"', verify_text)
        self.assertIn('"normalizer_source": "app_bundle"', verify_text)
        self.assertIn("Library/Caches/pip", verify_text)
        self.assertIn("cache/pycache", verify_text)
        self.assertIn("collect_test_account_install_evidence.sh", verify_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_SHARED_EVIDENCE_DIR", verify_text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH", verify_text)
        self.assertIn("SharedEvidence/test_account_install_evidence.json", verify_text)
        self.assertNotIn("sudo", build_text + verify_text)
        self.assertNotIn("brew install", build_text + verify_text)
        self.assertNotIn("/opt/homebrew", build_text + verify_text)

    def test_test_account_evidence_script_checks_distribution_invariants(self) -> None:
        text = EVIDENCE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("test_account_install_evidence.json", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_SHARED_EVIDENCE_DIR", text)
        self.assertIn("/Users/Shared/TotalSegmentatorWrapperMac", text)
        self.assertIn("shared_copy_path", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_DMG_PATH", text)
        self.assertIn("spctl_app_accepted", text)
        self.assertIn("stapler_dmg_valid", text)
        self.assertIn("manifest_notarized", text)
        self.assertIn('"--assess"', text)
        self.assertIn("stapler", text)
        self.assertIn("setup_state_success", text)
        self.assertIn("mps_actual_device", text)
        self.assertIn("mps_gate_pass", text)
        self.assertIn("normalizer_from_app_bundle", text)
        self.assertIn("python_executable_inside_app", text)
        self.assertIn("app_support_inside_current_home", text)
        self.assertIn("no_user_global_pip_cache", text)
        self.assertIn("pip_cache_under_app_support", text)
        self.assertIn("pycache_under_app_support", text)
        self.assertIn("manifest_ui_frontend_swiftui", text)
        self.assertIn("manifest_bundled_python312", text)
        self.assertIn("bundled_python_has_no_absolute_symlinks", text)
        self.assertIn("manifest_includes_sample1", text)
        self.assertIn("manifest_has_{manifest_field}", text)
        self.assertIn("wheel_sha256", text)
        self.assertIn("dcm2niix_sha256", text)
        self.assertIn("dcm2niix_version", text)
        self.assertIn("dcm2niix_source", text)
        self.assertIn("bundled_dcm2niix_exists", text)
        self.assertIn("update_allowed_hosts", text)
        self.assertIn("sample1_input_exists", text)
        self.assertIn("sample1_surface_preview_exists", text)
        self.assertIn("sample1_manifest_non_clinical", text)
        self.assertIn("setup_state_installed_bundle_current", text)
        self.assertIn("Setup状態ファイルが見つかりません", text)
        self.assertIn("共有受け渡し用コピーを書き出しました", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("brew", text)

    def test_test_account_evidence_import_script_requires_all_checks(self) -> None:
        text = EVIDENCE_IMPORT_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("test_account_install_verdict.json", text)
        self.assertIn("artifacts", text)
        self.assertIn("test_account_install", text)
        self.assertIn("missing_checks", text)
        self.assertIn("failed_checks", text)
        self.assertIn("home_failures", text)
        self.assertIn("evidence_home_is_temporary", text)
        self.assertIn("evidence_home_is_current_development_home", text)
        self.assertIn("TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_ZERO_ENV_EVIDENCE", text)
        self.assertIn("pycache_under_app_support", text)
        self.assertIn("manifest_ui_frontend_swiftui", text)
        self.assertIn("manifest_notarized", text)
        self.assertIn("app_codesign_valid", text)
        self.assertIn("spctl_app_accepted", text)
        self.assertIn("stapler_dmg_valid", text)
        self.assertIn("setup_state_installed_bundle_current", text)
        self.assertIn("manifest_has_update_allowed_hosts", text)
        self.assertIn("manifest_has_dcm2niix_sha256", text)
        self.assertIn("manifest_has_dcm2niix_version", text)
        self.assertIn("manifest_has_dcm2niix_source", text)
        self.assertIn("bundled_dcm2niix_exists", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("brew", text)

    def test_launcher_resolves_python312_from_env_or_manifest(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resources = root / "Resources"
            resources.mkdir()
            env_python = root / "python3.12"

            path, source = launcher.resolve_python312(resources, {"TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312": str(env_python)})
            self.assertEqual(path, env_python.resolve())
            self.assertEqual(source, "env")

            manifest_python = root / "manifest-python3.12"
            (resources / "setup_manifest.json").write_text(
                json.dumps({"python_runtime": {"python_executable": str(manifest_python)}}),
                encoding="utf-8",
            )
            path, source = launcher.resolve_python312(resources, {})
            self.assertEqual(path, manifest_python.resolve())
            self.assertEqual(source, "manifest")

            (resources / "setup_manifest.json").write_text(
                json.dumps({"python_runtime": {"python_executable": "python/cpython-3.12/bin/python3.12"}}),
                encoding="utf-8",
            )
            path, source = launcher.resolve_python312(resources, {})
            self.assertEqual(path, (resources / "python/cpython-3.12/bin/python3.12").resolve())
            self.assertEqual(source, "manifest")

    def test_launcher_can_resolve_resources_from_environment(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as tmp:
            resources = Path(tmp) / "Resources"

            old_value = launcher.os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_RESOURCES_DIR")
            launcher.os.environ["TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_RESOURCES_DIR"] = str(resources)
            try:
                self.assertEqual(launcher.bundle_resources_dir(), resources.resolve())
            finally:
                if old_value is None:
                    launcher.os.environ.pop("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_RESOURCES_DIR", None)
                else:
                    launcher.os.environ["TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_RESOURCES_DIR"] = old_value

    def test_launcher_environment_uses_bundled_normalizer_and_app_support_cache(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resources = root / "Resources"
            support = root / "Library" / "Application Support" / "TotalSegmentatorWrapperMac"
            normalizer = resources / "bin" / "totalsegmentator-wrapper-dicom-normalizer"
            dcm2niix = resources / "bin" / "dcm2niix"
            normalizer.parent.mkdir(parents=True)
            normalizer.write_text("# fake", encoding="utf-8")
            dcm2niix.write_text("# fake", encoding="utf-8")
            (resources / "python" / "cpython-3.12" / "lib" / "tcl8.6").mkdir(parents=True)
            (resources / "python" / "cpython-3.12" / "lib" / "tk8.6").mkdir(parents=True)

            env = launcher.build_launch_environment(resources, support)

            self.assertEqual(env["TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER"], str(normalizer))
            self.assertEqual(env["TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX"], str(dcm2niix))
            self.assertEqual(
                env["TCL_LIBRARY"],
                str(resources / "python" / "cpython-3.12" / "lib" / "tcl8.6"),
            )
            self.assertEqual(
                env["TK_LIBRARY"],
                str(resources / "python" / "cpython-3.12" / "lib" / "tk8.6"),
            )
            self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
            self.assertEqual(env["PYTHONPYCACHEPREFIX"], str(support / "cache" / "pycache"))
            self.assertEqual(env["PIP_CACHE_DIR"], str(support / "cache" / "pip"))
            self.assertEqual(env["PIP_DISABLE_PIP_VERSION_CHECK"], "1")
            self.assertTrue(env["XDG_CACHE_HOME"].startswith(str(support)))
            self.assertTrue(env["TOTALSEG_WEIGHTS_PATH"].startswith(str(support)))


def _load_launcher():
    spec = importlib.util.spec_from_file_location("mac_app_launcher_template", LAUNCHER_TEMPLATE)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
