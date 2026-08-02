from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_STATE = (
    ROOT / "native/macos/TotalSegmentatorWrapperForMac/AppState.swift"
).read_text(encoding="utf-8")
VIEWS = (
    ROOT / "native/macos/TotalSegmentatorWrapperForMac/Views.swift"
).read_text(encoding="utf-8")
MANUAL = (ROOT / "docs/USER_MANUAL_JA.md").read_text(encoding="utf-8")
FORM_URL = "https://forms.gle/QFPwF1Pi5C8bmSuw6"


def body(source: str, declaration: str) -> str:
    start = source.find(declaration)
    if start < 0:
        raise AssertionError(f"missing {declaration}")
    brace = source.find("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unclosed {declaration}")


class SetupErrorReportContractTests(unittest.TestCase):
    def test_setup_failure_exposes_two_explicit_actions_only_in_failure_ui(self) -> None:
        setup_view = body(VIEWS, "struct SetupView")
        self.assertIn("if !state.setupError.isEmpty", setup_view)
        self.assertIn("state.copySafeSetupErrorInfo()", setup_view)
        self.assertIn("state.openSetupErrorReportForm()", setup_view)
        self.assertEqual(setup_view.count('Label("エラー情報をコピー"'), 1)
        self.assertEqual(setup_view.count('Label("エラー報告フォームを開く"'), 1)
        self.assertGreaterEqual(setup_view.count(".disabled(state.setupRunning)"), 2)
        self.assertIn("フォームへは自動送信されません", setup_view)

    def test_setup_report_has_an_allowlisted_schema_and_no_raw_diagnostics(self) -> None:
        report = body(APP_STATE, "var safeSetupErrorCopyText")
        for field in (
            "report_schema=totalsegmentator_wrapper_mac.safe_setup_error_report.v1",
            "app_version=",
            "os_version=",
            "architecture=",
            "feature=setup",
            "setup_stage=",
            "reason_code=",
            "timestamp=",
            "setup_attempt_id=",
            "retryable=",
            "recovery_hint_code=",
            "diagnostic_log_kind=local_setup_log",
            "diagnostic_log_reference=",
            "return_code=",
            "python_version=",
            "dependency_set_id=",
            "build_id=",
        ):
            with self.subTest(field=field):
                self.assertIn(field, report)

        raw_error_fixture = (
            "stderr: failed to read /Users/patient/Example Patient/run.log"
        )
        self.assertNotIn(raw_error_fixture, report)
        for forbidden_reference in (
            "setupError",
            "logText",
            "currentLogURL",
            "launcherLog",
            "stdout",
            "stderr",
            "error=",
            "path=",
        ):
            with self.subTest(forbidden_reference=forbidden_reference):
                self.assertNotIn(forbidden_reference, report)
        self.assertIn('state["reason"]', body(APP_STATE, "private func safeSetupReasonCode"))
        self.assertIn("safeSetupString", body(APP_STATE, "private func safeSetupReasonCode"))
        self.assertIn('state["status"]', report)
        self.assertIn('== "failed"', report)
        attempt = body(APP_STATE, "private func safeSetupAttemptID")
        self.assertIn("UUID(uuidString: string)", attempt)
        reason = body(APP_STATE, "private func safeSetupReasonCode")
        self.assertIn("allowedReasonCodes", reason)
        self.assertIn('return "setup_failed"', reason)

    def test_setup_report_uses_its_attempt_id_as_a_non_path_local_log_reference(self) -> None:
        report = body(APP_STATE, "var safeSetupErrorCopyText")
        recovery = body(APP_STATE, "private func setupRecoveryHintCode")
        process = (
            Path(__file__).resolve().parents[1]
            / "native/macos/TotalSegmentatorWrapperForMac/ProcessSupport.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("diagnostic_log_kind=local_setup_log", report)
        self.assertIn("diagnostic_log_reference=\\(reportedAttemptID)", report)
        self.assertIn("copy_to_applications_then_retry", recovery)
        self.assertIn("review_local_setup_log_then_retry", recovery)
        self.assertIn("SETUP_ENGINEERING_DIAGNOSTIC setup_attempt_id=", process)
        self.assertIn("diagnostic_log_kind=local_setup_log", process)
        self.assertNotIn("paths.launcherLog.path", report)

    def test_setup_form_uses_the_existing_bare_url_and_never_interpolates_error(self) -> None:
        route = body(APP_STATE, "var errorReportFormURL")
        action = body(APP_STATE, "func openSetupErrorReportForm")
        copy = body(APP_STATE, "func copySafeSetupErrorInfo")
        self.assertIn(FORM_URL, route)
        self.assertNotIn("?entry", route)
        self.assertIn("copySafeSetupErrorInfo()", action)
        self.assertIn("openURLInWorkspace(errorReportFormURL)", action)
        self.assertNotIn("\\(setupError", action)
        self.assertNotIn("URL(string: setupError", action)
        self.assertNotIn("URLComponents", action)
        self.assertIn("safeSetupErrorCopyText", copy)
        for unsafe in ("logText", "currentLogURL", "\\(setupError"):
            self.assertNotIn(unsafe, copy)

    def test_manual_covers_setup_and_inference_privacy(self) -> None:
        for phrase in (
            "初回セットアップに失敗した場合",
            "推論に失敗した場合",
            "エラー情報をコピー",
            "エラー報告フォームを開く",
            "ログ、患者データは自動送信されません",
            FORM_URL,
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, MANUAL)

    def test_manual_keeps_raw_log_copy_out_of_the_support_form(self) -> None:
        for phrase in (
            "ログをコピー`はローカルでの原因確認用",
            "詳細ログにはローカルパスや入力ファイル名が含まれる場合がある",
            "相談フォームには貼り付けないでください",
            "エラー情報をコピー`を使用",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, MANUAL)

    def test_running_from_dmg_has_actionable_copy_instruction_without_volume_path(self) -> None:
        error = body(APP_STATE, "private func setupErrorText")
        recovery = body(APP_STATE, "var setupRecoveryText")
        self.assertIn("app_running_from_disk_image", error)
        self.assertIn("DMGや外部ボリューム内からアプリを直接実行", error)
        self.assertIn("app_running_from_disk_image", recovery)
        self.assertIn("DMGや外部ボリューム内から直接実行せず", recovery)
        self.assertIn("Applicationsまたは~/Applicationsへコピー", recovery)
        self.assertNotIn("/Volumes/", error + recovery)

    def test_busy_and_integrity_failures_are_marked_retryable(self) -> None:
        retryable = body(APP_STATE, "private func setupRetryableState")
        for reason in (
            "setup_busy",
            "weights_setup_busy",
            "weights_integrity_failed",
            "installed_package_missing_or_invalid",
            "setup_weights_manifest_sha256_changed",
            "dependency_set_id_changed",
            "constraints_sha256_changed",
            "fpsample_wheel_sha256_changed",
            "acvl_utils_wheel_sha256_changed",
        ):
            self.assertIn(f'"{reason}"', retryable)
        self.assertIn('return "true"', retryable)

    def test_setup_lock_failure_is_reportable_without_claiming_retryability(self) -> None:
        reason = body(APP_STATE, "private func safeSetupReasonCode")
        retryable = body(APP_STATE, "private func setupRetryableState")
        self.assertIn('"setup_lock_failed"', reason)
        self.assertNotIn('"setup_lock_failed"', retryable)

    def test_launch_state_prefers_the_current_detected_failure_reason(self) -> None:
        refresh = body(APP_STATE, "func refreshLaunchState")
        self.assertIn('status.reason == "setup_missing" && stateIsFailed', refresh)
        self.assertIn('(stateReason ?? status.reason)', refresh)
        self.assertIn(': status.reason', refresh)

    def test_dependency_identity_changes_have_actionable_setup_copy(self) -> None:
        reason = body(APP_STATE, "private func safeSetupReasonCode")
        retryable = body(APP_STATE, "private func setupRetryableState")
        command_builder = (
            Path(__file__).resolve().parents[1]
            / "native/macos/TotalSegmentatorWrapperForMac/CommandBuilder.swift"
        ).read_text(encoding="utf-8")
        for code in (
            "dependency_set_id_changed",
            "constraints_sha256_changed",
            "requirements_lock_sha256_changed",
            "dependency_lock_metadata_sha256_changed",
            "fpsample_wheel_sha256_changed",
            "acvl_utils_wheel_sha256_changed",
        ):
            self.assertIn(f'"{code}"', reason)
            self.assertIn(f'"{code}"', retryable)
            self.assertIn(f'"{code}"', command_builder)
        self.assertIn("アプリの依存構成が更新されました", command_builder)
        self.assertIn("セットアップをもう一度実行してください", command_builder)

    def test_bundled_wheel_failures_keep_specific_safe_codes_and_stages(self) -> None:
        reason = body(APP_STATE, "private func safeSetupReasonCode")
        retryable = body(APP_STATE, "private func setupRetryableState")
        stages = body(APP_STATE, "private func safeSetupStage")
        command_builder = (
            Path(__file__).resolve().parents[1]
            / "native/macos/TotalSegmentatorWrapperForMac/CommandBuilder.swift"
        ).read_text(encoding="utf-8")

        for code in (
            "bundled_wheel_invalid",
            "bundled_wheel_install_failed",
            "installed_bundled_dependency_missing_or_invalid",
        ):
            with self.subTest(code=code):
                self.assertIn(f'"{code}"', reason)
                self.assertIn(f'"{code}"', retryable)
                self.assertIn(f'"{code}"', command_builder)
        for stage in ("validate_bundled_wheels", "install_bundled_wheels"):
            self.assertIn(f'"{stage}"', stages)
        self.assertIn("同梱依存パッケージの完全性を確認できません", command_builder)
        self.assertIn("同梱依存パッケージの導入に失敗しました", command_builder)

    def test_dependency_consistency_failure_is_safe_retryable_and_stage_specific(self) -> None:
        reason = body(APP_STATE, "private func safeSetupReasonCode")
        retryable = body(APP_STATE, "private func setupRetryableState")
        stages = body(APP_STATE, "private func safeSetupStage")
        command_builder = (
            Path(__file__).resolve().parents[1]
            / "native/macos/TotalSegmentatorWrapperForMac/CommandBuilder.swift"
        ).read_text(encoding="utf-8")

        self.assertIn('"dependency_consistency_failed"', reason)
        self.assertIn('"dependency_consistency_failed"', retryable)
        self.assertIn('"verify_dependencies"', stages)
        self.assertIn(
            'case "dependency_consistency_failed": return "導入した依存パッケージの整合性を確認できませんでした。"',
            command_builder,
        )


if __name__ == "__main__":
    unittest.main()
