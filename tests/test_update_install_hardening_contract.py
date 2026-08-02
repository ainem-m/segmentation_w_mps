from __future__ import annotations

import json
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_STATE = (
    ROOT / "native/macos/TotalSegmentatorWrapperForMac/AppState.swift"
).read_text(encoding="utf-8")
APP_ENTRY = (
    ROOT / "native/macos/TotalSegmentatorWrapperForMac/TotalSegmentatorWrapperForMacApp.swift"
).read_text(encoding="utf-8")
VIEWS = (
    ROOT / "native/macos/TotalSegmentatorWrapperForMac/Views.swift"
).read_text(encoding="utf-8")


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


class UpdateInstallHardeningContractTests(unittest.TestCase):
    def test_interrupted_install_uses_atomic_same_volume_swap_and_recovery(self) -> None:
        """A killed helper must never leave the launchable app path absent.

        The prior backup-then-copy sequence had a fatal interval after moving the
        old bundle away.  Keep this contract deliberately structural: the real
        filesystem state-machine fixtures live in the Swift contract test, while
        this one makes the release path reject a return to non-atomic `mv`.
        """
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        refresh = body(APP_STATE, "func refreshLaunchState")
        for token in (
            "STAGED_NEW",
            "UPDATE_TRANSACTION",
            "stage_copy",
            "verify_stage",
            "--update-atomic-swap",
            "--update-atomic-rollback",
            "recoverInterruptedUpdateTransaction",
        ):
            with self.subTest(token=token):
                self.assertIn(token, installer + refresh)
        self.assertIn("runAtomicUpdateSwapIfRequested", APP_ENTRY)
        self.assertIn("renameatx_np", APP_STATE)
        self.assertIn("RENAME_SWAP", APP_STATE)
        self.assertIn("volumeSupportsSwapRenamingKey", APP_STATE)
        self.assertIn("UpdateAtomicSwapMode.rollback", APP_STATE)
        self.assertIn("verifySignedUpdateBundle(stageURL, expected: expectedStage)", APP_STATE)
        self.assertIn("appIdentity?.bundleID == expectedApp.bundleID", APP_STATE)
        self.assertNotIn("update-backup.$$", installer)
        self.assertNotIn('/bin/mv "$APP" "$BACKUP"', installer)
        self.assertNotIn('/usr/bin/ditto "$NEW_APP" "$APP"', installer)

    def test_transaction_journal_is_created_only_for_a_verified_swap_candidate(self) -> None:
        """A kill during copy/verification must leave an opaque orphan, not a recoverable journal.

        A transaction means that its controlled stage is a fully verified target
        and the next operation is the atomic swap.  Recording earlier states
        lets recovery mistake a partial copy for an owned bundle and delete it.
        """
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        install = body(APP_STATE, "private func downloadAndInstallPendingUpdate")
        recovery = body(APP_STATE, "func recoverInterruptedUpdateTransaction")
        decision = body(APP_STATE, "func updateTransactionRecoveryDecision")
        transaction_safety = body(APP_STATE, "func updateTransactionFileURLIsSafe")

        self.assertNotIn('write_update_transaction "stage_copy"', installer)
        self.assertNotIn('write_update_transaction "verify_stage"', installer)
        self.assertNotIn('write_update_transaction "swapped"', installer)
        self.assertEqual(installer.count('write_update_transaction "swap"'), 1)
        self.assertLess(
            installer.index('STAGED_VERSION="$(/usr/libexec/PlistBuddy'),
            installer.index('write_update_transaction "swap"'),
        )
        self.assertLess(
            installer.index('write_update_transaction "swap"'),
            installer.index('/usr/bin/arch -arm64 "$UPDATE_SWAP_EXECUTABLE" --update-atomic-swap'),
        )
        self.assertIn("hasPendingUpdateArtifacts", install)
        self.assertIn("hasPendingUpdateArtifacts", installer)
        self.assertIn("hasAnyUpdateStageArtifact", APP_STATE)
        self.assertIn("transactionStage == \"swap\"", decision)
        self.assertIn("transactionStage: transaction.stage", recovery)
        for token in ("lstat", "S_IFREG", "st_nlink", "st_uid", "getuid()"):
            with self.subTest(token=token):
                self.assertIn(token, transaction_safety)

    def test_kill_during_stage_copy_leaves_an_orphan_without_a_transaction(self) -> None:
        """The next launch must stop for manual recovery instead of removing a partial stage."""
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        match = re.search(r'let script = """\n(.*?)\n"""', installer, re.DOTALL)
        self.assertIsNotNone(match)
        assert match is not None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "TotalSegmentator Wrapper for Mac.app"
            app.mkdir()
            (app / "old-version.txt").write_text("old", encoding="utf-8")
            dmg = root / "update.dmg"
            dmg.write_bytes(b"fixture")
            mount = root / "mount"
            status = root / "updates" / "update_install_status.json"
            log = root / "updates" / "update_install.log"
            status.parent.mkdir()
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            stage = root / ".TotalSegmentator Wrapper for Mac.app.update-stage-fixture"
            transaction = root / ".totalsegmentator-wrapper-update-transaction.json"

            def executable(name: str, text: str) -> Path:
                path = fake_bin / name
                path.write_text("#!/bin/zsh\n" + text, encoding="utf-8")
                path.chmod(0o755)
                return path

            hdiutil = executable(
                "hdiutil",
                '''if [[ "$1" == "attach" ]]; then
  NEW_APP="${@: -1}/TotalSegmentator Wrapper for Mac.app"
  /bin/mkdir -p "$NEW_APP/Contents/MacOS"
  /usr/bin/touch "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
  /bin/chmod +x "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
fi
exit 0
''',
            )
            codesign = executable(
                "codesign",
                'if [[ "$1" == "-dv" ]]; then print -u2 "TeamIdentifier=TEAM"; fi\nexit 0\n',
            )
            spctl = executable("spctl", "exit 0\n")
            plist = executable(
                "PlistBuddy",
                '''if [[ "$2" == *CFBundleIdentifier* ]]; then
  print "jp.chino.TotalSegmentatorWrapperForMac"
elif [[ "$3" == *"/mount/"* ]]; then
  print "0.4.1"
else
  print "0.4.0"
fi
''',
            )
            ditto = executable(
                "ditto",
                '''/bin/mkdir -p "$2"
/usr/bin/touch "$2/partial-copy"
/bin/kill -KILL "$PPID"
exit 0
''',
            )
            open_command = executable("open", "exit 0\n")
            sleep_command = executable("sleep", "exit 0\n")

            shell = match.group(1)
            replacements = {
                r"\(dmgPath)": shlex.quote(str(dmg)),
                r"\(appPath)": shlex.quote(str(app)),
                r"\(mountPath)": shlex.quote(str(mount)),
                r"\(version)": shlex.quote("0.4.1"),
                r"\(stagedPath)": shlex.quote(str(stage)),
                r"\(transactionPath)": shlex.quote(str(transaction)),
                r"\(updateTokenPath)": shlex.quote("11111111-1111-4111-8111-111111111111"),
                r"\(statusPath)": shlex.quote(str(status)),
                r"\(logPath)": shlex.quote(str(log)),
                "/usr/bin/hdiutil": shlex.quote(str(hdiutil)),
                "/usr/bin/codesign": shlex.quote(str(codesign)),
                "/usr/sbin/spctl": shlex.quote(str(spctl)),
                "/usr/libexec/PlistBuddy": shlex.quote(str(plist)),
                "/usr/bin/ditto": shlex.quote(str(ditto)),
                "/usr/bin/open": shlex.quote(str(open_command)),
                "/bin/sleep": shlex.quote(str(sleep_command)),
            }
            for source, destination in replacements.items():
                shell = shell.replace(source, destination)
            shell = shell.replace("\\\\n", "\\n")

            result = subprocess.run(
                ["/bin/zsh"],
                input=shell,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )

            self.assertLess(result.returncode, 0, result.stderr)
            self.assertEqual((app / "old-version.txt").read_text(encoding="utf-8"), "old")
            self.assertTrue((stage / "partial-copy").is_file())
            self.assertFalse(transaction.exists())

    def test_existing_prefix_orphan_stops_the_shell_without_deleting_it(self) -> None:
        """A retry must not treat another partial stage as this helper's cleanup target."""
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        match = re.search(r'let script = """\n(.*?)\n"""', installer, re.DOTALL)
        self.assertIsNotNone(match)
        assert match is not None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "TotalSegmentator Wrapper for Mac.app"
            app.mkdir()
            (app / "old-version.txt").write_text("old", encoding="utf-8")
            dmg = root / "update.dmg"
            dmg.write_bytes(b"fixture")
            mount = root / "mount"
            status = root / "updates" / "update_install_status.json"
            log = root / "updates" / "update_install.log"
            status.parent.mkdir()
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            orphan = root / ".TotalSegmentator Wrapper for Mac.app.update-stage-left-behind"
            orphan.write_text("do not delete", encoding="utf-8")
            ditto_called = root / "ditto-called.txt"

            def executable(name: str, text: str) -> Path:
                path = fake_bin / name
                path.write_text("#!/bin/zsh\n" + text, encoding="utf-8")
                path.chmod(0o755)
                return path

            hdiutil = executable(
                "hdiutil",
                '''if [[ "$1" == "attach" ]]; then
  NEW_APP="${@: -1}/TotalSegmentator Wrapper for Mac.app"
  /bin/mkdir -p "$NEW_APP/Contents/MacOS"
  /usr/bin/touch "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
  /bin/chmod +x "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
fi
exit 0
''',
            )
            codesign = executable(
                "codesign",
                'if [[ "$1" == "-dv" ]]; then print -u2 "TeamIdentifier=TEAM"; fi\nexit 0\n',
            )
            spctl = executable("spctl", "exit 0\n")
            plist = executable(
                "PlistBuddy",
                '''if [[ "$2" == *CFBundleIdentifier* ]]; then
  print "jp.chino.TotalSegmentatorWrapperForMac"
elif [[ "$3" == *"/mount/"* ]]; then
  print "0.4.1"
else
  print "0.4.0"
fi
''',
            )
            ditto = executable(
                "ditto",
                f'/usr/bin/touch {shlex.quote(str(ditto_called))}\nexit 0\n',
            )
            open_command = executable("open", "exit 0\n")
            sleep_command = executable("sleep", "exit 0\n")

            shell = match.group(1)
            replacements = {
                r"\(dmgPath)": shlex.quote(str(dmg)),
                r"\(appPath)": shlex.quote(str(app)),
                r"\(mountPath)": shlex.quote(str(mount)),
                r"\(version)": shlex.quote("0.4.1"),
                r"\(stagedPath)": shlex.quote(
                    str(root / ".TotalSegmentator Wrapper for Mac.app.update-stage-fixture")
                ),
                r"\(transactionPath)": shlex.quote(
                    str(root / ".totalsegmentator-wrapper-update-transaction.json")
                ),
                r"\(updateTokenPath)": shlex.quote("11111111-1111-4111-8111-111111111111"),
                r"\(statusPath)": shlex.quote(str(status)),
                r"\(logPath)": shlex.quote(str(log)),
                "/usr/bin/hdiutil": shlex.quote(str(hdiutil)),
                "/usr/bin/codesign": shlex.quote(str(codesign)),
                "/usr/sbin/spctl": shlex.quote(str(spctl)),
                "/usr/libexec/PlistBuddy": shlex.quote(str(plist)),
                "/usr/bin/ditto": shlex.quote(str(ditto)),
                "/usr/bin/open": shlex.quote(str(open_command)),
                "/bin/sleep": shlex.quote(str(sleep_command)),
            }
            for source, destination in replacements.items():
                shell = shell.replace(source, destination)
            shell = shell.replace("\\\\n", "\\n")

            result = subprocess.run(
                ["/bin/zsh"],
                input=shell,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )

            self.assertEqual(result.returncode, 11, result.stderr)
            self.assertEqual(orphan.read_text(encoding="utf-8"), "do not delete")
            self.assertFalse(ditto_called.exists())
            payload = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(payload["reason"], "update_recovery_required")
            self.assertEqual(payload["stage"], "recovery")

    def test_update_check_bootstraps_from_bundled_python_and_wheel(self) -> None:
        command = body(APP_STATE, "private func updateCheckCommand")
        self.assertIn("paths.venvPython", command)
        self.assertIn("CommandBuilder.resolvePython312(paths: paths)", command)
        self.assertIn("CommandBuilder.latestWheel(resources: paths.resources)", command)
        self.assertIn('"-I"', command)
        self.assertIn('"-c"', command)
        self.assertIn("sys.path.insert(0,wheel)", command)
        self.assertIn("runpy.run_module", command)
        self.assertIn("wheel.path", command)
        self.assertNotIn("PYTHONPATH", command)
        self.assertNotIn("/bin/zsh", command)
        self.assertLess(
            command.index("CommandBuilder.resolvePython312(paths: paths)"),
            command.index("paths.venvPython"),
        )

    def test_update_check_does_not_accept_stale_or_unvalidated_results(self) -> None:
        check = body(APP_STATE, "func checkUpdates")
        self.assertIn("removeItem(at: updateJSON)", check)
        self.assertIn("rc == 0", check)
        self.assertIn("validatedUpdateCheckStatus", check)
        self.assertIn("isAllowedUpdateURL", check)
        self.assertIn("isSHA256Hex", check)
        self.assertIn('result["file_size_bytes"]', check)
        self.assertIn("MAX_UPDATE_DMG_BYTES", check)

        validation = body(APP_STATE, "func validatedUpdateCheckStatus")
        for token in (
            "totalsegmentator_wrapper_mac.update_check_result.v1",
            'result["manifest_url"] as? String == expectedManifestURL',
            'result["current_version"] as? String == expectedCurrentVersion',
            'result["update_available"] as? Bool',
            'result["critical"] as? Bool',
            "compareSemanticVersionTriplets",
        ):
            with self.subTest(token=token):
                self.assertIn(token, validation)

    def test_update_check_requires_a_strict_embedded_current_version(self) -> None:
        check = body(APP_STATE, "func checkUpdates")
        self.assertIn("semanticVersionTripletParts(version) != nil", check)
        self.assertNotIn('?? "0.4.1"', check)

    def test_update_check_and_install_are_mutually_exclusive(self) -> None:
        check = body(APP_STATE, "func checkUpdates")
        install = body(APP_STATE, "private func downloadAndInstallPendingUpdate")
        sidebar = body(VIEWS, "struct SidebarView")
        self.assertIn("guard !updateCheckRunning && !updateInstallRunning", check)
        self.assertIn("guard !updateInstallRunning && !updateCheckRunning", install)
        self.assertGreaterEqual(
            sidebar.count("state.updateCheckRunning || state.updateInstallRunning"),
            2,
        )

    def test_dmg_download_streams_to_disk_and_validates_final_response(self) -> None:
        install = body(APP_STATE, "private func downloadAndInstallPendingUpdate")
        self.assertIn("URLSession.shared.downloadTask", install)
        self.assertIn("HTTPURLResponse", install)
        self.assertIn("http.url", install)
        self.assertIn("isAllowedUpdateURL", install)
        self.assertIn("expectedFileSizeBytes", install)
        self.assertIn("pendingUpdateFileSizeBytes > 0", install)
        self.assertIn("MAX_UPDATE_DMG_BYTES", install)
        self.assertIn("http.expectedContentLength", install)
        self.assertIn("sha256HexFile", install)
        self.assertIn("task.progress.observe", install)
        self.assertIn("task.cancel()", install)
        self.assertIn("moveItem(at: temporaryURL", install)
        self.assertNotIn("Data(contentsOf: downloadURL)", install)
        sidebar = body(VIEWS, "struct SidebarView")
        self.assertIn("state.updateInstallProgressFraction", sidebar)
        self.assertIn("state.updateInstallProgressText", sidebar)

    def test_update_workspace_and_fixed_status_log_paths_fail_closed(self) -> None:
        install = body(APP_STATE, "private func downloadAndInstallPendingUpdate")
        installer = body(APP_STATE, "func writeUpdateInstallerScript")

        self.assertIn("prepareOwnedUpdateDirectory(updatesDir)", install)
        self.assertIn("createFreshOwnedUpdateDirectory(helperRoot)", installer)
        self.assertIn(
            'helperRoot.appendingPathComponent("update_install.log")',
            installer,
        )
        self.assertIn("status_destination_is_safe_for_replace", installer)
        self.assertIn("unsetopt CLOBBER", installer)
        self.assertIn('exec {UPDATE_LOG_FD}> "$UPDATE_INSTALL_LOG"', installer)
        self.assertLess(
            installer.index("trap cleanup EXIT"),
            installer.index('exec {UPDATE_LOG_FD}> "$UPDATE_INSTALL_LOG"'),
        )
        recovery_status = body(APP_STATE, "private func writeUpdateInstallerStatus")
        self.assertIn("isOwnedNormalUpdateDirectory", recovery_status)
        self.assertIn("updateStatusDestinationIsSafeForReplace", recovery_status)

    def test_installer_does_not_follow_log_or_status_links(self) -> None:
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        match = re.search(r'let script = """\n(.*?)\n"""', installer, re.DOTALL)
        self.assertIsNotNone(match)
        assert match is not None

        for unsafe_path_name, link_kind in (
            ("log", "symlink"),
            ("log", "hardlink"),
            ("status", "symlink"),
            ("status", "hardlink"),
        ):
            with self.subTest(
                unsafe_path_name=unsafe_path_name,
                link_kind=link_kind,
            ), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                app = root / "TotalSegmentator Wrapper for Mac.app"
                app.mkdir()
                dmg = root / "update.dmg"
                dmg.write_bytes(b"fixture")
                helper = root / "updates" / "install_fixture"
                helper.mkdir(parents=True)
                mount = helper / "mount"
                log = helper / "update_install.log"
                status = root / "updates" / "update_install_status.json"
                victim = root / "must-not-change.txt"
                victim.write_text("preserve me", encoding="utf-8")
                unsafe_path = log if unsafe_path_name == "log" else status
                if link_kind == "symlink":
                    unsafe_path.symlink_to(victim)
                else:
                    unsafe_path.hardlink_to(victim)

                shell = match.group(1)
                replacements = {
                    r"\(dmgPath)": shlex.quote(str(dmg)),
                    r"\(appPath)": shlex.quote(str(app)),
                    r"\(mountPath)": shlex.quote(str(mount)),
                    r"\(version)": shlex.quote("0.4.1"),
                    r"\(stagedPath)": shlex.quote(
                        str(root / ".TotalSegmentator Wrapper for Mac.app.update-stage-fixture")
                    ),
                    r"\(transactionPath)": shlex.quote(
                        str(root / ".totalsegmentator-wrapper-update-transaction.json")
                    ),
                    r"\(updateTokenPath)": shlex.quote(
                        "11111111-1111-4111-8111-111111111111"
                    ),
                    r"\(statusPath)": shlex.quote(str(status)),
                    r"\(logPath)": shlex.quote(str(log)),
                    "/usr/bin/hdiutil": "/usr/bin/true",
                    "/usr/bin/open": "/usr/bin/true",
                    "/bin/sleep": "/usr/bin/true",
                }
                for source, destination in replacements.items():
                    shell = shell.replace(source, destination)
                shell = shell.replace("\\\\n", "\\n")

                result = subprocess.run(
                    ["/bin/zsh"],
                    input=shell,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=10,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(victim.read_text(encoding="utf-8"), "preserve me")
                self.assertTrue(unsafe_path.exists() or unsafe_path.is_symlink())
                self.assertTrue(app.is_dir())

    def test_installer_requires_an_arm64_candidate_and_staged_executable(self) -> None:
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        self.assertGreaterEqual(
            installer.count('/usr/bin/arch -arm64 "$UPDATE_SWAP_EXECUTABLE"'),
            2,
        )

    def test_installer_requires_same_identity_and_exact_pending_version(self) -> None:
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        for token in (
            "EXPECTED_VERSION",
            "CFBundleIdentifier",
            "CFBundleShortVersionString",
            "TeamIdentifier",
            "CURRENT_BUNDLE_ID",
            "NEW_BUNDLE_ID",
            "CURRENT_TEAM_ID",
            "NEW_TEAM_ID",
            "INSTALLED_BUNDLE_ID",
            "INSTALLED_TEAM_ID",
            "INSTALLED_VERSION",
            "codesign --verify --deep --strict",
            "spctl --assess --type execute",
            "STAGED_NEW",
            "UPDATE_SWAP_EXECUTABLE",
            "--update-atomic-swap",
            "write_update_transaction",
        ):
            with self.subTest(token=token):
                self.assertIn(token, installer)
        self.assertGreaterEqual(installer.count("codesign --verify"), 3)
        self.assertGreaterEqual(installer.count("spctl --assess"), 2)
        self.assertNotIn('/bin/mv "$APP" "$BACKUP"', installer)
        self.assertNotIn('/usr/bin/ditto "$NEW_APP" "$APP"', installer)

    def test_installer_rejects_equal_downgrade_and_malformed_versions_before_copy(self) -> None:
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        match = re.search(r'let script = """\n(.*?)\n"""', installer, re.DOTALL)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertIn("compare_semantic_version_triplets", installer)
        self.assertIn('VERSION_ORDER="$(compare_semantic_version_triplets', installer)
        self.assertIn('[[ "$VERSION_ORDER" != "1" ]]', installer)

        for current_version, candidate_version in (
            ("0.4.2", "0.4.1"),
            ("0.4.1", "0.4.1"),
            ("0.4", "0.4.1"),
            ("0.4.0", "0.4.1-alpha"),
        ):
            with self.subTest(
                current_version=current_version,
                candidate_version=candidate_version,
            ), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                app = root / "TotalSegmentator Wrapper for Mac.app"
                app.mkdir()
                dmg = root / "update.dmg"
                dmg.write_bytes(b"fixture")
                mount = root / "mount"
                status = root / "updates" / "update_install_status.json"
                log = root / "updates" / "update_install.log"
                status.parent.mkdir()
                copied = root / "ditto-called.txt"
                fake_bin = root / "fake-bin"
                fake_bin.mkdir()

                def executable(name: str, text: str) -> Path:
                    path = fake_bin / name
                    path.write_text("#!/bin/zsh\n" + text, encoding="utf-8")
                    path.chmod(0o755)
                    return path

                hdiutil = executable(
                    "hdiutil",
                    '''if [[ "$1" == "attach" ]]; then
  NEW_APP="${@: -1}/TotalSegmentator Wrapper for Mac.app"
  /bin/mkdir -p "$NEW_APP/Contents/MacOS"
  /usr/bin/touch "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
  /bin/chmod +x "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
fi
exit 0
''',
                )
                codesign = executable(
                    "codesign",
                    'if [[ "$1" == "-dv" ]]; then print -u2 "TeamIdentifier=TEAM"; fi\nexit 0\n',
                )
                spctl = executable("spctl", "exit 0\n")
                plist = executable(
                    "PlistBuddy",
                    f'''if [[ "$2" == *CFBundleIdentifier* ]]; then
  print "jp.chino.TotalSegmentatorWrapperForMac"
elif [[ "$3" == *"/mount/"* ]]; then
  print {shlex.quote(candidate_version)}
else
  print {shlex.quote(current_version)}
fi
''',
                )
                ditto = executable(
                    "ditto",
                    f'/usr/bin/touch {shlex.quote(str(copied))}\nexit 0\n',
                )
                open_command = executable("open", "exit 0\n")
                sleep_command = executable("sleep", "exit 0\n")

                shell = match.group(1)
                replacements = {
                    r"\(dmgPath)": shlex.quote(str(dmg)),
                    r"\(appPath)": shlex.quote(str(app)),
                    r"\(mountPath)": shlex.quote(str(mount)),
                    r"\(version)": shlex.quote(candidate_version),
                    r"\(stagedPath)": shlex.quote(
                        str(root / ".TotalSegmentator Wrapper for Mac.app.update-stage-fixture")
                    ),
                    r"\(transactionPath)": shlex.quote(
                        str(root / ".totalsegmentator-wrapper-update-transaction.json")
                    ),
                    r"\(updateTokenPath)": shlex.quote("11111111-1111-4111-8111-111111111111"),
                    r"\(statusPath)": shlex.quote(str(status)),
                    r"\(logPath)": shlex.quote(str(log)),
                    "/usr/bin/hdiutil": shlex.quote(str(hdiutil)),
                    "/usr/bin/codesign": shlex.quote(str(codesign)),
                    "/usr/sbin/spctl": shlex.quote(str(spctl)),
                    "/usr/libexec/PlistBuddy": shlex.quote(str(plist)),
                    "/usr/bin/ditto": shlex.quote(str(ditto)),
                    "/usr/bin/open": shlex.quote(str(open_command)),
                    "/bin/sleep": shlex.quote(str(sleep_command)),
                }
                for source, destination in replacements.items():
                    shell = shell.replace(source, destination)
                shell = shell.replace("\\\\n", "\\n")

                result = subprocess.run(
                    ["/bin/zsh"],
                    input=shell,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=10,
                )

                self.assertEqual(result.returncode, 9, result.stderr)
                self.assertFalse(copied.exists())

    def test_installer_failure_rolls_back_reopens_and_persists_safe_status(self) -> None:
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        refresh = body(APP_STATE, "private func refreshUpdateInstallerStatus")
        for token in (
            "UPDATE_STATUS_JSON",
            "UPDATE_INSTALL_LOG",
            "write_update_status",
            "update_install_failed_before_replace",
            "update_install_failed_rolled_back",
            "update_recovery_required",
            "verify_rolled_back_app",
            "verify_installed_target_app",
            'trap cleanup EXIT',
            '/usr/bin/open "$APP"',
        ):
            with self.subTest(token=token):
                self.assertIn(token, installer)
        self.assertIn('"schema":"totalsegmentator_wrapper_mac.update_install_status.v1"', installer)
        self.assertIn("refreshUpdateInstallerStatus()", body(APP_STATE, "func refreshLaunchState"))
        self.assertIn("update_install_interrupted_before_swap", APP_STATE)
        self.assertIn("updateInstallStatusJSON", refresh)
        self.assertIn("allowedReasons", refresh)
        self.assertNotIn("safeErrorReason", refresh)
        self.assertLess(
            installer.index("rollback_after_failed_postcheck && verify_rolled_back_app"),
            installer.index('/usr/bin/hdiutil detach "$MOUNT"'),
        )
        self.assertIn(
            '/usr/bin/arch -arm64 "$UPDATE_SWAP_EXECUTABLE" --update-atomic-rollback',
            installer,
        )

    def test_generated_installer_shell_template_has_valid_zsh_syntax(self) -> None:
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        match = re.search(r'let script = """\n(.*?)\n"""', installer, re.DOTALL)
        self.assertIsNotNone(match)
        assert match is not None
        shell = re.sub(r"\\\([A-Za-z][A-Za-z0-9]*\)", "'fixture'", match.group(1))
        result = subprocess.run(
            ["/bin/zsh", "-n"],
            input=shell,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stage_copy_failure_preserves_and_reopens_previous_app(self) -> None:
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        match = re.search(r'let script = """\n(.*?)\n"""', installer, re.DOTALL)
        self.assertIsNotNone(match)
        assert match is not None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "TotalSegmentator Wrapper for Mac.app"
            app.mkdir()
            (app / "old-version.txt").write_text("old", encoding="utf-8")
            dmg = root / "update.dmg"
            dmg.write_bytes(b"fixture")
            mount = root / "mount"
            status = root / "updates" / "update_install_status.json"
            log = root / "updates" / "update_install.log"
            status.parent.mkdir()
            opened = root / "opened.txt"
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()

            def executable(name: str, text: str) -> Path:
                path = fake_bin / name
                path.write_text("#!/bin/zsh\n" + text, encoding="utf-8")
                path.chmod(0o755)
                return path

            hdiutil = executable(
                "hdiutil",
                '''if [[ "$1" == "attach" ]]; then
  NEW_APP="${@: -1}/TotalSegmentator Wrapper for Mac.app"
  /bin/mkdir -p "$NEW_APP/Contents/MacOS"
  /usr/bin/touch "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
  /bin/chmod +x "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
fi
exit 0
''',
            )
            codesign = executable(
                "codesign",
                'if [[ "$1" == "-dv" ]]; then print -u2 "TeamIdentifier=TEAM"; fi\nexit 0\n',
            )
            spctl = executable("spctl", "exit 0\n")
            plist = executable(
                "PlistBuddy",
                '''if [[ "$2" == *CFBundleIdentifier* ]]; then
  print "jp.chino.TotalSegmentatorWrapperForMac"
elif [[ "$3" == *"/mount/"* ]]; then
  print "0.4.1"
else
  print "0.4.0"
fi
''',
            )
            ditto = executable("ditto", "exit 9\n")
            open_command = executable(
                "open",
                f'/usr/bin/printf "%s" "$1" > {shlex.quote(str(opened))}\n',
            )
            sleep_command = executable("sleep", "exit 0\n")

            shell = match.group(1)
            replacements = {
                r"\(dmgPath)": shlex.quote(str(dmg)),
                r"\(appPath)": shlex.quote(str(app)),
                r"\(mountPath)": shlex.quote(str(mount)),
                r"\(version)": shlex.quote("0.4.1"),
                r"\(stagedPath)": shlex.quote(
                    str(root / ".TotalSegmentator Wrapper for Mac.app.update-stage-fixture")
                ),
                r"\(transactionPath)": shlex.quote(
                    str(root / ".totalsegmentator-wrapper-update-transaction.json")
                ),
                r"\(updateTokenPath)": shlex.quote("11111111-1111-4111-8111-111111111111"),
                r"\(statusPath)": shlex.quote(str(status)),
                r"\(logPath)": shlex.quote(str(log)),
                "/usr/bin/hdiutil": shlex.quote(str(hdiutil)),
                "/usr/bin/codesign": shlex.quote(str(codesign)),
                "/usr/sbin/spctl": shlex.quote(str(spctl)),
                "/usr/libexec/PlistBuddy": shlex.quote(str(plist)),
                "/usr/bin/ditto": shlex.quote(str(ditto)),
                "/usr/bin/open": shlex.quote(str(open_command)),
                "/bin/sleep": shlex.quote(str(sleep_command)),
            }
            for source, destination in replacements.items():
                shell = shell.replace(source, destination)
            # Swift reduces the doubled backslash in the multiline literal before
            # zsh receives the printf format.
            shell = shell.replace("\\\\n", "\\n")

            result = subprocess.run(
                ["/bin/zsh"],
                input=shell,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )

            self.assertEqual(result.returncode, 9, result.stderr)
            self.assertEqual((app / "old-version.txt").read_text(encoding="utf-8"), "old")
            self.assertEqual(opened.read_text(encoding="utf-8"), str(app))
            payload = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["reason"], "update_install_failed_before_replace")
            self.assertEqual(payload["stage"], "stage_copy")
            self.assertEqual(payload["return_code"], 9)
            self.assertTrue(log.is_file())

    def test_reraced_stage_is_preserved_for_manual_recovery(self) -> None:
        """Cleanup must not remove a same-name stage replaced after ditto.

        The helper owns the directory it copied, not a later directory that
        happens to reuse the generated pathname.  This simulates a filesystem
        race during staged verification; the replacement must remain visible
        for manual recovery instead of being recursively removed by cleanup.
        """
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        match = re.search(r'let script = """\n(.*?)\n"""', installer, re.DOTALL)
        self.assertIsNotNone(match)
        assert match is not None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "TotalSegmentator Wrapper for Mac.app"
            app.mkdir()
            (app / "old-version.txt").write_text("old", encoding="utf-8")
            dmg = root / "update.dmg"
            dmg.write_bytes(b"fixture")
            mount = root / "mount"
            status = root / "updates" / "update_install_status.json"
            log = root / "updates" / "update_install.log"
            status.parent.mkdir()
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            stage = root / ".TotalSegmentator Wrapper for Mac.app.update-stage-fixture"

            def executable(name: str, text: str) -> Path:
                path = fake_bin / name
                path.write_text("#!/bin/zsh\n" + text, encoding="utf-8")
                path.chmod(0o755)
                return path

            hdiutil = executable(
                "hdiutil",
                '''if [[ "$1" == "attach" ]]; then
  NEW_APP="${@: -1}/TotalSegmentator Wrapper for Mac.app"
  /bin/mkdir -p "$NEW_APP/Contents/MacOS"
  /usr/bin/touch "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
  /bin/chmod +x "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
fi
exit 0
''',
            )
            codesign = executable(
                "codesign",
                f'''if [[ "$1" == "-dv" ]]; then print -u2 "TeamIdentifier=TEAM"; exit 0; fi
if [[ "$1" == "--verify" && "$5" == {shlex.quote(str(stage))} ]]; then
  /bin/rm -rf "$5"
  /bin/mkdir -p "$5"
  /usr/bin/touch "$5/re-raced-marker"
  exit 15
fi
exit 0
''',
            )
            spctl = executable("spctl", "exit 0\n")
            plist = executable(
                "PlistBuddy",
                '''if [[ "$2" == *CFBundleIdentifier* ]]; then
  print "jp.chino.TotalSegmentatorWrapperForMac"
elif [[ "$3" == *"/mount/"* ]]; then
  print "0.4.1"
else
  print "0.4.0"
fi
''',
            )
            ditto = executable("ditto", '/bin/cp -R "$1" "$2"\n')
            open_command = executable("open", "exit 0\n")
            sleep_command = executable("sleep", "exit 0\n")

            shell = match.group(1)
            replacements = {
                r"\(dmgPath)": shlex.quote(str(dmg)),
                r"\(appPath)": shlex.quote(str(app)),
                r"\(mountPath)": shlex.quote(str(mount)),
                r"\(version)": shlex.quote("0.4.1"),
                r"\(stagedPath)": shlex.quote(str(stage)),
                r"\(transactionPath)": shlex.quote(
                    str(root / ".totalsegmentator-wrapper-update-transaction.json")
                ),
                r"\(updateTokenPath)": shlex.quote("11111111-1111-4111-8111-111111111111"),
                r"\(statusPath)": shlex.quote(str(status)),
                r"\(logPath)": shlex.quote(str(log)),
                "/usr/bin/hdiutil": shlex.quote(str(hdiutil)),
                "/usr/bin/codesign": shlex.quote(str(codesign)),
                "/usr/sbin/spctl": shlex.quote(str(spctl)),
                "/usr/libexec/PlistBuddy": shlex.quote(str(plist)),
                "/usr/bin/ditto": shlex.quote(str(ditto)),
                "/usr/bin/open": shlex.quote(str(open_command)),
                "/bin/sleep": shlex.quote(str(sleep_command)),
            }
            for source, destination in replacements.items():
                shell = shell.replace(source, destination)
            shell = shell.replace("\\\\n", "\\n")

            result = subprocess.run(
                ["/bin/zsh"],
                input=shell,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )

            self.assertEqual(result.returncode, 15, result.stderr)
            self.assertTrue((stage / "re-raced-marker").is_file())
            payload = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(payload["reason"], "update_recovery_required")
            self.assertEqual(payload["stage"], "recovery")

    def test_reraced_transaction_is_preserved_for_manual_recovery(self) -> None:
        """Cleanup must not remove a replacement transaction after it journals swap."""
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        match = re.search(r'let script = """\n(.*?)\n"""', installer, re.DOTALL)
        self.assertIsNotNone(match)
        assert match is not None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "TotalSegmentator Wrapper for Mac.app"
            app.mkdir()
            (app / "old-version.txt").write_text("old", encoding="utf-8")
            dmg = root / "update.dmg"
            dmg.write_bytes(b"fixture")
            mount = root / "mount"
            status = root / "updates" / "update_install_status.json"
            log = root / "updates" / "update_install.log"
            status.parent.mkdir()
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            stage = root / ".TotalSegmentator Wrapper for Mac.app.update-stage-fixture"
            transaction = root / ".totalsegmentator-wrapper-update-transaction.json"

            def executable(name: str, text: str) -> Path:
                path = fake_bin / name
                path.write_text("#!/bin/zsh\n" + text, encoding="utf-8")
                path.chmod(0o755)
                return path

            helper_program = "\n".join(
                [
                    "#!/bin/zsh",
                    f"TRANSACTION={shlex.quote(str(transaction))}",
                    '/bin/rm -f "$TRANSACTION"',
                    '/usr/bin/printf %s foreign-transaction > "$TRANSACTION"',
                    "exit 16",
                ]
            ) + "\n"
            hdiutil = executable(
                "hdiutil",
                f'''if [[ "$1" == "attach" ]]; then
  NEW_APP="${{@: -1}}/TotalSegmentator Wrapper for Mac.app"
  /bin/mkdir -p "$NEW_APP/Contents/MacOS"
  /usr/bin/printf '%s' {shlex.quote(helper_program)} > "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
  /bin/chmod +x "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
fi
exit 0
''',
            )
            codesign = executable(
                "codesign",
                'if [[ "$1" == "-dv" ]]; then print -u2 "TeamIdentifier=TEAM"; fi\nexit 0\n',
            )
            spctl = executable("spctl", "exit 0\n")
            plist = executable(
                "PlistBuddy",
                '''if [[ "$2" == *CFBundleIdentifier* ]]; then
  print "jp.chino.TotalSegmentatorWrapperForMac"
elif [[ "$3" == *"/mount/"* || "$3" == *"update-stage-"* ]]; then
  print "0.4.1"
else
  print "0.4.0"
fi
''',
            )
            ditto = executable("ditto", '/bin/cp -R "$1" "$2"\n')
            open_command = executable("open", "exit 0\n")
            sleep_command = executable("sleep", "exit 0\n")

            shell = match.group(1)
            replacements = {
                r"\(dmgPath)": shlex.quote(str(dmg)),
                r"\(appPath)": shlex.quote(str(app)),
                r"\(mountPath)": shlex.quote(str(mount)),
                r"\(version)": shlex.quote("0.4.1"),
                r"\(stagedPath)": shlex.quote(str(stage)),
                r"\(transactionPath)": shlex.quote(str(transaction)),
                r"\(updateTokenPath)": shlex.quote("11111111-1111-4111-8111-111111111111"),
                r"\(statusPath)": shlex.quote(str(status)),
                r"\(logPath)": shlex.quote(str(log)),
                "/usr/bin/hdiutil": shlex.quote(str(hdiutil)),
                "/usr/bin/codesign": shlex.quote(str(codesign)),
                "/usr/sbin/spctl": shlex.quote(str(spctl)),
                "/usr/libexec/PlistBuddy": shlex.quote(str(plist)),
                "/usr/bin/ditto": shlex.quote(str(ditto)),
                "/usr/bin/open": shlex.quote(str(open_command)),
                "/bin/sleep": shlex.quote(str(sleep_command)),
            }
            for source, destination in replacements.items():
                shell = shell.replace(source, destination)
            shell = shell.replace("\\\\n", "\\n")

            result = subprocess.run(
                ["/bin/zsh"],
                input=shell,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )

            self.assertEqual(result.returncode, 16, result.stderr)
            self.assertTrue(transaction.is_file(), "cleanup must preserve the replacement transaction")
            self.assertEqual(transaction.read_text(encoding="utf-8"), "foreign-transaction")
            payload = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(payload["reason"], "update_recovery_required")
            self.assertEqual(payload["stage"], "recovery")

    def test_post_swap_verification_failure_rolls_back_before_detach(self) -> None:
        installer = body(APP_STATE, "func writeUpdateInstallerScript")
        match = re.search(r'let script = """\n(.*?)\n"""', installer, re.DOTALL)
        self.assertIsNotNone(match)
        assert match is not None

        for failed_verifier, expected_return_code in (("codesign", 23), ("spctl", 24)):
            with self.subTest(failed_verifier=failed_verifier), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                app = root / "TotalSegmentator Wrapper for Mac.app"
                app.mkdir()
                (app / "old-version.txt").write_text("old", encoding="utf-8")
                dmg = root / "update.dmg"
                dmg.write_bytes(b"fixture")
                mount = root / "mount"
                status = root / "updates" / "update_install_status.json"
                log = root / "updates" / "update_install.log"
                status.parent.mkdir()
                opened = root / "opened.txt"
                events = root / "events.txt"
                fake_bin = root / "fake-bin"
                fake_bin.mkdir()

                def executable(name: str, text: str) -> Path:
                    path = fake_bin / name
                    path.write_text("#!/bin/zsh\n" + text, encoding="utf-8")
                    path.chmod(0o755)
                    return path

                helper_program = "\n".join(
                    [
                        "#!/bin/zsh",
                        f"EVENTS={shlex.quote(str(events))}",
                        "/usr/bin/printf 'swap\\n' >> \"$EVENTS\"",
                        'APP="$2"',
                        'STAGE="$3"',
                        'TEMP="${APP}.fixture-swap"',
                        '/bin/mv "$APP" "$TEMP"',
                        '/bin/mv "$STAGE" "$APP"',
                        '/bin/mv "$TEMP" "$STAGE"',
                    ]
                ) + "\n"
                hdiutil = executable(
                    "hdiutil",
                    f'''if [[ "$1" == "attach" ]]; then
  /usr/bin/printf 'attach\\n' >> {shlex.quote(str(events))}
  NEW_APP="$6/TotalSegmentator Wrapper for Mac.app"
  /bin/mkdir -p "$NEW_APP/Contents/MacOS"
  /usr/bin/touch "$NEW_APP/target-marker"
  /usr/bin/printf '%s' {shlex.quote(helper_program)} > "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
  /bin/chmod +x "$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
elif [[ "$1" == "detach" ]]; then
  /usr/bin/printf 'detach\\n' >> {shlex.quote(str(events))}
fi
exit 0
''',
                )
                app_path = shlex.quote(str(app))
                codesign_failure = (
                    f'''if [[ "$1" == "--verify" && "$5" == {app_path} && -f {app_path}/target-marker ]]; then
  exit 23
fi
'''
                    if failed_verifier == "codesign"
                    else ""
                )
                codesign = executable(
                    "codesign",
                    f'''if [[ "$1" == "-dv" ]]; then print -u2 "TeamIdentifier=TEAM"; exit 0; fi
{codesign_failure}exit 0
''',
                )
                spctl_failure = (
                    f'''if [[ "$5" == {app_path} && -f {app_path}/target-marker ]]; then
  exit 24
fi
'''
                    if failed_verifier == "spctl"
                    else ""
                )
                spctl = executable("spctl", spctl_failure + "exit 0\n")
                plist = executable(
                    "PlistBuddy",
                    '''if [[ "$2" == *CFBundleIdentifier* ]]; then
  print "jp.chino.TotalSegmentatorWrapperForMac"
else
  BUNDLE="${3%/Contents/Info.plist}"
  if [[ -f "$BUNDLE/target-marker" ]]; then print "0.4.1"; else print "0.4.0"; fi
fi
''',
                )
                ditto = executable("ditto", '/bin/cp -R "$1" "$2"\n')
                open_command = executable(
                    "open",
                    f'/usr/bin/printf "%s" "$1" > {shlex.quote(str(opened))}\n',
                )
                sleep_command = executable("sleep", "exit 0\n")

                shell = match.group(1)
                replacements = {
                    r"\(dmgPath)": shlex.quote(str(dmg)),
                    r"\(appPath)": shlex.quote(str(app)),
                    r"\(mountPath)": shlex.quote(str(mount)),
                    r"\(version)": shlex.quote("0.4.1"),
                    r"\(stagedPath)": shlex.quote(
                        str(root / ".TotalSegmentator Wrapper for Mac.app.update-stage-fixture")
                    ),
                    r"\(transactionPath)": shlex.quote(
                        str(root / ".totalsegmentator-wrapper-update-transaction.json")
                    ),
                    r"\(updateTokenPath)": shlex.quote("11111111-1111-4111-8111-111111111111"),
                    r"\(statusPath)": shlex.quote(str(status)),
                    r"\(logPath)": shlex.quote(str(log)),
                    "/usr/bin/hdiutil": shlex.quote(str(hdiutil)),
                    "/usr/bin/codesign": shlex.quote(str(codesign)),
                    "/usr/sbin/spctl": shlex.quote(str(spctl)),
                    "/usr/libexec/PlistBuddy": shlex.quote(str(plist)),
                    "/usr/bin/ditto": shlex.quote(str(ditto)),
                    "/usr/bin/open": shlex.quote(str(open_command)),
                    "/bin/sleep": shlex.quote(str(sleep_command)),
                }
                for source, destination in replacements.items():
                    shell = shell.replace(source, destination)
                shell = shell.replace("\\\\n", "\\n")

                result = subprocess.run(
                    ["/bin/zsh"],
                    input=shell,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=10,
                )

                self.assertEqual(result.returncode, 72, result.stderr)
                self.assertEqual((app / "old-version.txt").read_text(encoding="utf-8"), "old")
                self.assertFalse((app / "target-marker").exists())
                self.assertEqual(opened.read_text(encoding="utf-8"), str(app))
                payload = json.loads(status.read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "failed")
                self.assertEqual(payload["reason"], "update_install_failed_rolled_back")
                self.assertEqual(payload["stage"], "verify_installed")
                self.assertEqual(payload["return_code"], 72)
                self.assertEqual(events.read_text(encoding="utf-8").splitlines(), [
                    "attach",
                    "swap",
                    "swap",
                    "detach",
                ])
                self.assertFalse(dmg.exists())

    def test_update_urls_reject_nonstandard_https_ports(self) -> None:
        https = body(APP_STATE, "func isHTTPSURL")
        allowed = body(APP_STATE, "func isAllowedUpdateURL")
        self.assertIn("url.port == nil || url.port == 443", https)
        self.assertIn("isHTTPSURL(url)", allowed)
        self.assertIn("isHTTPSURL(manifestURL)", allowed)


if __name__ == "__main__":
    unittest.main()
