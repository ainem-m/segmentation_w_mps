import Foundation
import Darwin

@main
struct SwiftProgressContractTests {
    static func main() {
        require(isLowercaseSHA256(String(repeating: "a", count: 64)), "ASCII SHA-256 was rejected")
        require(
            !isLowercaseSHA256(String(repeating: "١", count: 64)),
            "non-ASCII digits must not satisfy the registry SHA-256 contract"
        )
        require(
            setupReasonToJapanese("dependency_consistency_failed")
                == "導入した依存パッケージの整合性を確認できませんでした。",
            "dependency consistency failures must use fixed Japanese copy rather than raw diagnostics"
        )
        require(
            setupReasonToJapanese("app_running_from_disk_image").contains("外部ボリューム"),
            "the setup reason must cover removable /Volumes locations as well as DMGs"
        )
        require(
            setupRecoverySuggestion("app_running_from_disk_image").contains("開き直して"),
            "the installed-location recovery must tell the user to reopen the copied app"
        )
        let setupLog = """
        SETUP_PROGRESS step=download_totalseg_weights status=running message=初回実行に必要なモデルを取得しています。
        SETUP_DOWNLOAD_PROGRESS {"source":"totalsegmentator","status":"downloading","task_id":297,"index":2,"task_total":3,"completed_bytes":227000000,"total_bytes":232000000,"percent":98,"rate_bps":388000,"eta_seconds":13}
        """
        let setupState = setupExecutionStateFromLog(setupLog)
        require(setupState?.event.step == "download_totalseg_weights", "setup step was not parsed")
        require(setupState?.downloadProgress?.index == 2, "setup task index was not parsed")
        require(setupState?.downloadProgress?.percent == 98, "setup percent was not parsed")
        require(abs((setupState?.downloadProgress?.fraction ?? -1) - 0.98) < 0.000_001, "setup fraction mismatch")
        require(setupState?.downloadProgress?.displayText.contains("227 MB / 232 MB") == true, "setup bytes are missing")
        let completedSetup = setupExecutionStateFromLog(
            setupLog + "\nSETUP_PROGRESS step=download_totalseg_weights status=success message=モデルの取得が完了しました。"
        )
        require(completedSetup?.downloadProgress == nil, "completed setup must clear stale download progress")
        let lockedDependencySetup = setupExecutionStateFromLog(
            "SETUP_PROGRESS step=install_locked_dependencies status=running message=SHA-256固定済みの依存パッケージを取得しています。"
        )
        require(
            lockedDependencySetup?.event.step == SetupStep.installLockedDependencies.rawValue,
            "hashed dependency installation must map to its dedicated setup step"
        )
        require(
            SetupStep(rawValue: lockedDependencySetup?.event.step ?? "") == .installLockedDependencies,
            "hashed dependency progress must not leave the UI on a stale setup step"
        )
        require(
            SetupStep.installLockedDependencies.label.contains("固定済み依存"),
            "hashed dependency setup label must accurately identify the work"
        )
        require(
            SetupStep.installLockedDependencies.hint.contains("SHA-256")
                && SetupStep.installLockedDependencies.hint.contains("数分"),
            "hashed dependency setup hint must explain verification and expected wait"
        )
        let taillessSetup = setupExecutionStateFromLog(
            "SETUP_DOWNLOAD_PROGRESS {\"source\":\"totalsegmentator\",\"status\":\"downloading\",\"index\":3,\"task_total\":3,\"percent\":40}"
        )
        require(taillessSetup?.event.step == "download_totalseg_weights", "download event must recover a truncated setup marker")
        require(taillessSetup?.downloadProgress?.percent == 40, "tail-only setup progress was not preserved")
        let dentalSetup = setupExecutionStateFromLog(
            "SETUP_DOWNLOAD_PROGRESS {\"source\":\"dentalsegmentator\",\"status\":\"downloading\",\"index\":1,\"task_total\":1,\"completed_bytes\":400,\"total_bytes\":1000,\"percent\":40,\"resumed\":true,\"resume_from_bytes\":250}"
        )
        require(dentalSetup?.event.step == "download_dentalseg_weights", "DentalSegmentator setup step mismatch")
        require(dentalSetup?.downloadProgress?.resumed == true, "DentalSegmentator resume state was lost")
        require(dentalSetup?.downloadProgress?.resumeFromBytes == 250, "resume byte offset was lost")
        let dentalLazy = dentalSegmentatorPreparationProgressFromLog(
            "SETUP_DOWNLOAD_PROGRESS {\"source\":\"dentalsegmentator\",\"status\":\"downloading\",\"index\":1,\"task_total\":1,\"completed_bytes\":400,\"total_bytes\":1000,\"percent\":40,\"rate_bps\":200,\"eta_seconds\":3,\"resumed\":true,\"resume_from_bytes\":250}"
        )
        require(abs((dentalLazy?.fraction ?? -1) - 0.4) < 0.000_001, "lazy DentalSegmentator fraction mismatch")
        require(dentalLazy?.message.contains("DentalSegmentator") == true, "lazy DentalSegmentator message mismatch")
        require(dentalLazy?.detailText.contains("40%") == true, "lazy DentalSegmentator percent is missing")
        require(dentalLazy?.detailText.contains("/秒") == true, "lazy DentalSegmentator rate is missing")
        require(dentalLazy?.detailText.contains("残り約") == true, "lazy DentalSegmentator ETA is missing")
        require(dentalLazy?.detailText.contains("の中断位置から再開") == true, "lazy DentalSegmentator resume detail is missing")

        func setupDownloadCopy(
            status: String,
            resumed: Bool = false,
            percent: Int? = 100,
            rateBPS: Double? = 512,
            etaSeconds: Int? = 12
        ) -> String {
            SetupDownloadProgress(
                source: "totalsegmentator",
                status: status,
                taskID: 297,
                index: 2,
                taskTotal: 3,
                completedBytes: 1_000,
                totalBytes: 1_000,
                percent: percent,
                rateBPS: rateBPS,
                etaSeconds: etaSeconds,
                resumed: resumed,
                resumeFromBytes: 500
            ).displayText
        }
        let downloadingCopy = setupDownloadCopy(status: "downloading", resumed: true, percent: 50)
        require(downloadingCopy.contains("取得中"), "downloading progress must say that it is downloading")
        require(downloadingCopy.contains("中断位置から再開"), "only downloading progress may show the resume position")
        require(downloadingCopy.contains("/秒"), "downloading progress must retain transfer-rate detail")
        require(downloadingCopy.contains("残り約"), "downloading progress must retain ETA detail")

        let verifyingCopy = setupDownloadCopy(status: "verifying", resumed: true)
        require(verifyingCopy.contains("完全性を確認中"), "verifying progress must say that integrity is being checked")
        require(!verifyingCopy.contains("取得中"), "100% verification must not be presented as an active download")
        require(!verifyingCopy.contains("中断位置から再開"), "verification must not show resume wording")
        require(!verifyingCopy.contains("/秒"), "verification must not show transfer-rate detail")
        require(!verifyingCopy.contains("残り約"), "verification must not show download ETA detail")

        let restartCopy = setupDownloadCopy(status: "restart", resumed: true, percent: 0, rateBPS: nil, etaSeconds: nil)
        require(
            restartCopy.contains("再開条件を確認できなかったため先頭から再取得"),
            "restart progress must explain the safe restart"
        )
        require(!restartCopy.contains("中断位置から再開"), "restart must never claim that it resumed")

        let completeCopy = setupDownloadCopy(status: "complete", resumed: true)
        require(completeCopy.contains("準備完了"), "complete progress must say that preparation is complete")
        require(!completeCopy.contains("中断位置から再開"), "complete progress must not show resume wording")
        require(!completeCopy.contains("/秒"), "complete progress must not show transfer-rate detail")

        let failedCopy = setupDownloadCopy(status: "failed", resumed: true)
        require(failedCopy.contains("取得失敗"), "failed progress must say that the download failed")
        require(!failedCopy.contains("中断位置から再開"), "failed progress must not show resume wording")
        require(!failedCopy.contains("/秒"), "failed progress must not show transfer-rate detail")

        let unknownCopy = setupDownloadCopy(status: "future_status", resumed: true)
        require(unknownCopy.contains("状態を確認中"), "unknown progress status must use safe generic wording")
        require(!unknownCopy.contains("取得中"), "unknown progress status must not falsely claim a download")
        require(!unknownCopy.contains("中断位置から再開"), "unknown progress status must not show resume wording")

        func dentalPreparationCopy(status: String, resumed: Bool = true) -> ToothSegPreparationProgress? {
            dentalSegmentatorPreparationProgressFromLog(
                "SETUP_DOWNLOAD_PROGRESS {\"source\":\"dentalsegmentator\",\"status\":\"\(status)\",\"index\":1,\"task_total\":1,\"completed_bytes\":1000,\"total_bytes\":1000,\"percent\":100,\"rate_bps\":512,\"eta_seconds\":12,\"resumed\":\(resumed),\"resume_from_bytes\":500}"
            )
        }
        let dentalVerifying = dentalPreparationCopy(status: "verifying")
        require(dentalVerifying?.message.contains("完全性を確認中") == true, "DentalSegmentator verification must retain its status")
        require(dentalVerifying?.stage == "verifying", "DentalSegmentator verification must not be a download stage")
        require(dentalVerifying?.detailText.isEmpty == true, "DentalSegmentator verification must not show transfer details")

        let dentalRestart = dentalPreparationCopy(status: "restart")
        require(
            dentalRestart?.message.contains("再開条件を確認できなかったため先頭から再取得") == true,
            "DentalSegmentator restart must explain the safe restart"
        )
        require(dentalRestart?.message.contains("中断位置から再開") == false, "DentalSegmentator restart must not claim a resume")
        require(dentalRestart?.detailText.isEmpty == true, "DentalSegmentator restart must not show stale transfer details")

        let dentalComplete = dentalPreparationCopy(status: "complete")
        require(dentalComplete?.message.contains("準備完了") == true, "DentalSegmentator completion must say preparation is complete")
        require(dentalComplete?.stage == "complete", "DentalSegmentator completion stage mismatch")

        let dentalFailed = dentalPreparationCopy(status: "failed")
        require(dentalFailed?.message.contains("取得失敗") == true, "DentalSegmentator failure must retain its status")
        require(dentalFailed?.stage == "failed", "DentalSegmentator failure must not be a download stage")
        require(dentalFailed?.detailText.isEmpty == true, "DentalSegmentator failure must not show stale transfer details")

        let preparationResetRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("swift-preparation-reset-\(UUID().uuidString)", isDirectory: true)
        let preparationResetPaths = AppPaths(
            resources: preparationResetRoot.appendingPathComponent("Resources", isDirectory: true),
            support: preparationResetRoot.appendingPathComponent("Support", isDirectory: true)
        )
        let staleProgress = "SETUP_DOWNLOAD_PROGRESS {\"source\":\"dentalsegmentator\",\"status\":\"complete\",\"percent\":100}\n"
        try! FileManager.default.createDirectory(
            at: preparationResetPaths.logs,
            withIntermediateDirectories: true
        )
        try! staleProgress.write(
            to: preparationResetPaths.dentalsegPrepareLog,
            atomically: true,
            encoding: .utf8
        )
        writeJSON(["model_state": "ready"], to: preparationResetPaths.dentalsegPrepareResultJSON)
        require(
            clearModelPreparationAttemptArtifacts(
                logURL: preparationResetPaths.dentalsegPrepareLog,
                resultURL: preparationResetPaths.dentalsegPrepareResultJSON,
                logsRoot: preparationResetPaths.logs
            ),
            "DentalSegmentator attempt artifacts must reset safely"
        )
        require(
            !FileManager.default.fileExists(atPath: preparationResetPaths.dentalsegPrepareLog.path)
                && !FileManager.default.fileExists(atPath: preparationResetPaths.dentalsegPrepareResultJSON.path),
            "new DentalSegmentator attempt must not retain stale completion"
        )

        try! staleProgress.replacingOccurrences(of: "dentalsegmentator", with: "toothseg").write(
            to: preparationResetPaths.toothsegPrepareLog,
            atomically: true,
            encoding: .utf8
        )
        writeJSON(["model_state": "ready"], to: preparationResetPaths.toothsegPrepareResultJSON)
        require(
            clearModelPreparationAttemptArtifacts(
                logURL: preparationResetPaths.toothsegPrepareLog,
                resultURL: preparationResetPaths.toothsegPrepareResultJSON,
                logsRoot: preparationResetPaths.logs
            ),
            "ToothSeg attempt artifacts must reset safely"
        )
        require(
            readLogTail(preparationResetPaths.toothsegPrepareLog, maxBytes: LOG_TAIL_BYTES) == nil,
            "an immediate ToothSeg failure must not expose a previous 100% event"
        )

        let preservedPreparationTarget = preparationResetRoot.appendingPathComponent("preserve-preparation-target.txt")
        try! Data("preserve".utf8).write(to: preservedPreparationTarget)
        try! FileManager.default.createSymbolicLink(
            at: preparationResetPaths.dentalsegPrepareLog,
            withDestinationURL: preservedPreparationTarget
        )
        require(
            clearModelPreparationAttemptArtifacts(
                logURL: preparationResetPaths.dentalsegPrepareLog,
                resultURL: preparationResetPaths.dentalsegPrepareResultJSON,
                logsRoot: preparationResetPaths.logs
            ),
            "a stale preparation-log symlink must be unlinked without following it"
        )
        require(
            String(data: try! Data(contentsOf: preservedPreparationTarget), encoding: .utf8) == "preserve",
            "preparation reset must never modify a symlink target"
        )
        try? FileManager.default.removeItem(at: preparationResetRoot)

        let knownLog = """
        RUN_STAGE {"route":"toothseg_refine","stage_id":"instance","index":3,"total":5,"label":"ToothSeg instance枝"}
        RUN_PROGRESS {"route":"toothseg_refine","stage_id":"instance","scope":"stage","step":40,"total":80,"percent":50,"eta_seconds":613}
        """
        let known = runExecutionStateFromLog(knownLog)
        require(known.stage?.stageID == "instance", "structured stage was not parsed")
        require(known.progress?.scope == "stage", "stage scope was not parsed")
        let weighted = known.stage.flatMap {
            RunWeightedProgress.calculate(stage: $0, progress: known.progress)
        }
        require(abs((weighted?.estimate ?? -1) - 0.595) < 0.000_001, "ToothSeg 50% must map to 59.5% overall")
        require(known.progress?.etaSeconds == 613, "actual ETA was not preserved")

        let unknownLog = """
        RUN_STAGE {"route":"totalsegmentator","stage_id":"segment","index":2,"total":4,"label":"顎顔面を抽出中"}
        """
        let unknown = runExecutionStateFromLog(unknownLog)
        let unknownWeighted = unknown.stage.flatMap {
            RunWeightedProgress.calculate(stage: $0, progress: unknown.progress)
        }
        require(unknownWeighted?.estimate == nil, "unknown stage must not invent a point estimate")
        require(abs((unknownWeighted?.lowerBound ?? -1) - 0.01) < 0.000_001, "unknown lower bound mismatch")
        require(abs((unknownWeighted?.upperBound ?? -1) - 0.69) < 0.000_001, "unknown upper bound mismatch")

        let subtaskLog = unknownLog + "\n" + """
        RUN_PROGRESS {"route":"totalsegmentator","stage_id":"segment","scope":"subtask","step":100,"total":100,"percent":100,"eta_seconds":0}
        """
        let subtask = runExecutionStateFromLog(subtaskLog)
        let subtaskWeighted = subtask.stage.flatMap {
            RunWeightedProgress.calculate(stage: $0, progress: subtask.progress)
        }
        require(subtaskWeighted?.estimate == nil, "subtask completion must not advance overall estimate")

        let transitionLog = knownLog + "\n" + """
        RUN_STAGE {"route":"toothseg_refine","stage_id":"restore","index":4,"total":5,"label":"FDI番号付与・元画像へ復元中"}
        """
        let transition = runExecutionStateFromLog(transitionLog)
        require(transition.stage?.stageID == "restore", "new stage was not selected")
        require(transition.progress == nil, "old percent and ETA must be discarded on stage change")

        let legacy = runExecutionStateFromLog(
            "RUN_PROGRESS {\"step\":2,\"total\":4,\"percent\":50,\"stage\":\"Predicting\"}"
        )
        require(legacy.stage == nil, "legacy log must not fabricate a structured stage")
        require(legacy.progress?.scope == "subtask", "legacy progress must default safely to subtask")

        let longLogURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("swift-progress-tail-\(UUID().uuidString).log")
        let longLog = """
        RUN_STAGE {"route":"toothseg_refine","stage_id":"semantic","index":2,"total":5,"label":"ToothSeg semantic枝"}
        \(String(repeating: "filler line without events\n", count: 4_000))
        RUN_PROGRESS {"route":"toothseg_refine","stage_id":"semantic","scope":"stage","step":20,"total":80,"percent":25,"eta_seconds":300}
        """
        try! longLog.write(to: longLogURL, atomically: true, encoding: .utf8)
        let tail = readLogTail(longLogURL, maxBytes: LOG_TAIL_BYTES)!
        try? FileManager.default.removeItem(at: longLogURL)
        let tailState = runExecutionStateFromLog(tail.text)
        require(tail.truncated, "long-log fixture must exercise the 64 KiB tail")
        require(tailState.stage == nil, "stage marker should be outside the retained tail")
        let inferred = tailState.progress.flatMap(inferredRunStage(from:))
        require(inferred?.stageID == "semantic", "structured progress must recover its stage after tail truncation")
        let tailWeighted = inferred.flatMap {
            RunWeightedProgress.calculate(stage: $0, progress: tailState.progress)
        }
        require(abs((tailWeighted?.estimate ?? -1) - 0.0575) < 0.000_001, "tail recovery must keep trusted stage progress live")

        let preview = RunStageEvent(
            route: "toothseg_refine", stageID: "preview", index: 5,
            total: 5, label: "3D表示・結果情報を作成中"
        )
        let previewState = runExecutionStateFromLog(runStageLogLine(preview))
        require(previewState.stage == preview, "Swift preview stage must use the persisted RUN_STAGE contract")

        let upgradeRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("swift-upgrade-contract-\(UUID().uuidString)", isDirectory: true)
        let resources = upgradeRoot.appendingPathComponent("Resources", isDirectory: true)
        let support = upgradeRoot.appendingPathComponent("Support", isDirectory: true)
        try! FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
        try! FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        let upgradePaths = AppPaths(resources: resources, support: support)
        let bundlePython = resources.appendingPathComponent("python/bin/python3.12")
        try! FileManager.default.createDirectory(
            at: bundlePython.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try! Data("fixture bundled python".utf8).write(to: bundlePython)
        try! FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: bundlePython.path
        )
        try! FileManager.default.createDirectory(
            at: upgradePaths.venvPython.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try! FileManager.default.createSymbolicLink(
            at: upgradePaths.venvPython,
            withDestinationURL: bundlePython
        )
        let pyvenvConfig = upgradePaths.support.appendingPathComponent("env/pyvenv.cfg")
        let validPyvenvConfig = """
        home = \(bundlePython.deletingLastPathComponent().path)
        executable = \(bundlePython.path)
        """
        try! validPyvenvConfig.write(to: pyvenvConfig, atomically: true, encoding: .utf8)

        let danglingSupport = upgradeRoot.appendingPathComponent("DanglingSupport", isDirectory: true)
        let danglingPaths = AppPaths(resources: resources, support: danglingSupport)
        try! FileManager.default.createDirectory(
            at: danglingPaths.venvPython.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try! FileManager.default.createSymbolicLink(
            atPath: danglingPaths.venvPython.path,
            withDestinationPath: "/Volumes/Removed Wrapper/Wrapper.app/Contents/Resources/python/bin/python3.12"
        )
        let danglingPyvenvConfig = danglingPaths.support.appendingPathComponent("env/pyvenv.cfg")
        try! """
        home = /Volumes/TotalSegmentator Wrapper for Mac 4/TotalSegmentator Wrapper for Mac.app/Contents/Resources/python/cpython-3.12/bin
        executable = /Volumes/TotalSegmentator Wrapper for Mac 4/TotalSegmentator Wrapper for Mac.app/Contents/Resources/python/cpython-3.12/bin/python3.12
        """.write(to: danglingPyvenvConfig, atomically: true, encoding: .utf8)
        require(
            venvPythonEntryExists(paths: danglingPaths),
            "a dangling venv Python symlink must be detected as an existing directory entry"
        )
        require(
            venvRequiresRecreation(paths: danglingPaths, python312: bundlePython),
            "a dangling /Volumes venv Python symlink must force environment recreation"
        )
        require(
            managedVenvRefreshDecision(
                paths: danglingPaths,
                python312: bundlePython,
                setupStatus: SetupStatus(
                    state: nil,
                    action: "setup_required",
                    reason: "venv_python_changed"
                )
            ) == .recreate(reason: "venv_python_changed"),
            "the real dangling /Volumes venv shape must choose managed environment recreation"
        )
        require(
            safelyRemoveManagedVenv(paths: danglingPaths) == .removed,
            "recreating the dangling /Volumes environment must remove only its managed env directory"
        )
        let oldBundlePython = upgradeRoot
            .appendingPathComponent("OldWrapper.app/Contents/Resources/python/bin/python3.12")
        try! FileManager.default.createDirectory(
            at: oldBundlePython.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try! Data("old bundled python".utf8).write(to: oldBundlePython)
        try! FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: oldBundlePython.path
        )
        let oldRuntimePaths = AppPaths(
            resources: resources,
            support: upgradeRoot.appendingPathComponent("OldRuntimeSupport", isDirectory: true)
        )
        try! FileManager.default.createDirectory(
            at: oldRuntimePaths.venvPython.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try! FileManager.default.createSymbolicLink(
            at: oldRuntimePaths.venvPython,
            withDestinationURL: oldBundlePython
        )
        try! """
        home = \(oldBundlePython.deletingLastPathComponent().path)
        executable = \(oldBundlePython.path)
        """.write(
            to: oldRuntimePaths.support.appendingPathComponent("env/pyvenv.cfg"),
            atomically: true,
            encoding: .utf8
        )
        require(
            venvRequiresRecreation(paths: oldRuntimePaths, python312: bundlePython),
            "a venv linked to another app bundle must be recreated"
        )
        require(
            managedVenvRefreshDecision(
                paths: oldRuntimePaths,
                python312: bundlePython,
                setupStatus: SetupStatus(
                    state: nil,
                    action: "setup_required",
                    reason: "venv_python_changed"
                )
            ) == .recreate(reason: "venv_python_changed"),
            "another-bundle runtime linkage must choose managed environment recreation"
        )
        require(
            safelyRemoveManagedVenv(paths: oldRuntimePaths) == .removed,
            "another-bundle runtime recovery must remove only its managed env directory"
        )
        let staleBasePaths = AppPaths(
            resources: resources,
            support: upgradeRoot.appendingPathComponent("StaleBaseSupport", isDirectory: true)
        )
        try! FileManager.default.createDirectory(
            at: staleBasePaths.venvPython.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try! FileManager.default.createSymbolicLink(
            at: staleBasePaths.venvPython,
            withDestinationURL: bundlePython
        )
        try! """
        home = \(oldBundlePython.deletingLastPathComponent().path)
        executable = \(bundlePython.path)
        """.write(
            to: staleBasePaths.support.appendingPathComponent("env/pyvenv.cfg"),
            atomically: true,
            encoding: .utf8
        )
        require(
            venvRequiresRecreation(paths: staleBasePaths, python312: bundlePython),
            "a venv whose base runtime belongs to another app bundle must be recreated"
        )
        require(
            managedVenvRefreshDecision(
                paths: staleBasePaths,
                python312: bundlePython,
                setupStatus: SetupStatus(
                    state: nil,
                    action: "setup_required",
                    reason: "venv_python_changed"
                )
            ) == .recreate(reason: "venv_python_changed"),
            "another-bundle base runtime must choose managed environment recreation"
        )
        require(
            safelyRemoveManagedVenv(paths: staleBasePaths) == .removed,
            "stale base-runtime recovery must remove only its managed env directory"
        )
        require(
            !venvRequiresRecreation(paths: upgradePaths, python312: bundlePython),
            "a normal current venv Python symlink must remain reusable"
        )
        let installedPackage = upgradePaths.venvSitePackages
            .appendingPathComponent("totalsegmentator_wrapper_mac", isDirectory: true)
        let installedDistribution = upgradePaths.venvSitePackages
            .appendingPathComponent("totalsegmentator_wrapper_mac-0.2.0.dist-info", isDirectory: true)
        try! FileManager.default.createDirectory(at: installedPackage, withIntermediateDirectories: true)
        try! FileManager.default.createDirectory(at: installedDistribution, withIntermediateDirectories: true)
        try! Data("__version__ = \"0.2.0\"".utf8).write(
            to: installedPackage.appendingPathComponent("__init__.py")
        )
        let installedMetadata = installedDistribution.appendingPathComponent("METADATA")
        try! "Name: totalsegmentator-wrapper-mac\nVersion: 0.2.0\n".write(
            to: installedMetadata,
            atomically: true,
            encoding: .utf8
        )
        let acvlPackage = upgradePaths.venvSitePackages
            .appendingPathComponent("acvl_utils", isDirectory: true)
        let acvlModule = acvlPackage
            .appendingPathComponent("instance_segmentation", isDirectory: true)
            .appendingPathComponent("instance_as_semantic_seg.py")
        let acvlDistribution = upgradePaths.venvSitePackages
            .appendingPathComponent("acvl_utils-0.2.6.dist-info", isDirectory: true)
        try! FileManager.default.createDirectory(
            at: acvlModule.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try! Data("def convert_semantic_to_instanceseg(): pass".utf8).write(to: acvlModule)
        try! FileManager.default.createDirectory(at: acvlDistribution, withIntermediateDirectories: true)
        try! "Name: acvl_utils\nVersion: 0.2.6\n".write(
            to: acvlDistribution.appendingPathComponent("METADATA"),
            atomically: true,
            encoding: .utf8
        )
        let fpsamplePackage = upgradePaths.venvSitePackages
            .appendingPathComponent("fpsample", isDirectory: true)
        let fpsampleDistribution = upgradePaths.venvSitePackages
            .appendingPathComponent("fpsample-1.0.2.dist-info", isDirectory: true)
        try! FileManager.default.createDirectory(at: fpsamplePackage, withIntermediateDirectories: true)
        try! Data("from ._fpsample import fps_sampling".utf8).write(
            to: fpsamplePackage.appendingPathComponent("__init__.py")
        )
        try! Data("fixture native extension".utf8).write(
            to: fpsamplePackage.appendingPathComponent("_fpsample.cpython-312-darwin.so")
        )
        try! FileManager.default.createDirectory(at: fpsampleDistribution, withIntermediateDirectories: true)
        try! "Name: fpsample\nVersion: 1.0.2\n".write(
            to: fpsampleDistribution.appendingPathComponent("METADATA"),
            atomically: true,
            encoding: .utf8
        )
        let weightsManifestSHA = String(repeating: "a", count: 64)
        let currentManifest: [String: Any] = [
            "app_version": "0.2.0",
            "build_id": "app-0.2.0-test",
            "dependency_set_id": "deps-toothseg",
            "wheel_sha256": "new-wheel",
            "requirements_lock_sha256": String(repeating: "b", count: 64),
            "dependency_lock_metadata_sha256": String(repeating: "c", count: 64),
            "fpsample_wheel_sha256": "fpsample-wheel",
            "acvl_utils_wheel_sha256": "acvl-wheel",
            "constraints_sha256": "new-constraints",
            "normalizer_sha256": "normalizer",
            "dcm2niix_sha256": "dcm2niix",
            "sample1_manifest_sha256": "sample",
            "setup_weights_manifest_sha256": weightsManifestSHA,
            "python_runtime_fingerprint": "runtime-fixture-v1",
            "python_runtime": ["python_executable": "python/bin/python3.12", "bundled": true],
            "update_manifest_url": "https://updates.example.test/update.json",
        ]
        do {
        let explicitExternalPython = upgradeRoot.appendingPathComponent("explicit-external-python")
        try! "#!/bin/sh\nprintf '3.12\\n'\n# external-runtime-v1\n".write(
            to: explicitExternalPython,
            atomically: true,
            encoding: .utf8
        )
        try! FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: explicitExternalPython.path
        )
        let previousExternalPython = ProcessInfo.processInfo.environment[
            "TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312"
        ]
        setenv(
            "TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312",
            explicitExternalPython.path,
            1
        )
        defer {
            if let previousExternalPython {
                setenv("TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312", previousExternalPython, 1)
            } else {
                unsetenv("TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312")
            }
        }
        let externalRuntimeManifest: [String: Any] = [
            "python_runtime": ["bundled": false],
        ]
        let externalDigestBefore = pythonRuntimeExecutableSHA256(
            paths: upgradePaths,
            manifest: externalRuntimeManifest
        )
        require(
            !externalDigestBefore.isEmpty,
            "an explicitly configured external Python executable must retain a digest fallback"
        )
        try! "#!/bin/sh\nprintf '3.12\\n'\n# external-runtime-v2\n".write(
            to: explicitExternalPython,
            atomically: true,
            encoding: .utf8
        )
        try! FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: explicitExternalPython.path
        )
        let externalDigestAfter = pythonRuntimeExecutableSHA256(
            paths: upgradePaths,
            manifest: externalRuntimeManifest
        )
        require(
            externalDigestBefore != externalDigestAfter,
            "an external Python executable byte change must change its fallback digest"
        )
        require(
            pythonRuntimeFingerprintRequiresEnvironmentRefresh(
                installed: [
                    "python_runtime_fingerprint": "",
                    "python_runtime_executable_sha256": externalDigestBefore,
                ],
                current: [
                    "python_runtime_fingerprint": "",
                    "python_runtime_executable_sha256": externalDigestAfter,
                ]
            ),
            "an external Python executable digest change must recreate the managed venv"
        )
        }
        writeJSON(currentManifest, to: upgradePaths.manifest)
        var installed = currentBundleRecord(paths: upgradePaths)
        installed["app_version"] = "0.1.2"
        installed["wheel_sha256"] = "old-wheel"
        installed["constraints_sha256"] = "old-constraints"
        writeJSON(
            [
                "schema": "totalsegmentator_wrapper_mac.setup_state.v1",
                "status": "success",
                "installed_bundle": installed,
            ],
            to: upgradePaths.stateJSON
        )
        let upgradeStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(upgradeStatus.action == "setup_required", "0.1.2 constraints change must trigger full setup")
        require(upgradeStatus.reason == "constraints_sha256_changed", "upgrade reason must identify constraints change")
        require(
            managedVenvRefreshDecision(
                paths: upgradePaths,
                python312: bundlePython,
                setupStatus: upgradeStatus
            ) == .recreate(reason: "constraints_sha256_changed"),
            "a dependency constraints change must recreate the venv before setup reuses it"
        )

        let currentRecord = currentBundleRecord(paths: upgradePaths)
        writeJSON(
            [
                "schema": "totalsegmentator_wrapper_mac.setup_state.v1",
                "status": "success",
                "installed_bundle": currentRecord,
            ],
            to: upgradePaths.stateJSON
        )
        writeInstalledWheelMarker(paths: upgradePaths)
        let missingWeightsStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            missingWeightsStatus.action == "setup_required",
            "a current setup state must not hide missing TotalSegmentator models"
        )
        require(
            missingWeightsStatus.reason == "setup_weights_missing_or_invalid",
            "missing model registry must have a specific setup reason"
        )

        let setupWeightsRoot = upgradePaths.support
            .appendingPathComponent("models/totalsegmentator/weights", isDirectory: true)
        var registryAssets: [[String: Any]] = []
        for taskID in [113, 115, 297] {
            let dataset = "Dataset\(taskID)_fixture"
            let relative = "trainer/fold_0/checkpoint_final.pth"
            let model = setupWeightsRoot
                .appendingPathComponent(dataset, isDirectory: true)
                .appendingPathComponent(relative)
            try! FileManager.default.createDirectory(
                at: model.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try! Data("verified model \(taskID)".utf8).write(to: model)
            let modelData = try! Data(contentsOf: model)
            registryAssets.append(
                [
                    "task_id": taskID,
                    "dataset_dir": dataset,
                    "integrity_source": "legacy-deep-validation",
                    "archive_verified": false,
                    "archive_sha256": NSNull(),
                    "required_files": [
                        [
                            "path": relative,
                            "size_bytes": Int(modelData.count),
                            "sha256": sha256Hex(modelData),
                        ],
                    ],
                ]
            )
        }
        writeJSON(
            [
                "schema": "totalsegmentator_wrapper_mac.setup_weights_registry.v2",
                "totalsegmentator_version": "2.14.0",
                "setup_weights_manifest_sha256": weightsManifestSHA,
                "integrity_source": "legacy-deep-validation",
                "archive_verified": false,
                "legacy_migration": true,
                "assets": registryAssets,
            ],
            to: setupWeightsRoot.appendingPathComponent(".totalsegmentator-wrapper-setup-weights.json")
        )
        let validWeightsStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            validWeightsStatus.action == "current",
            "valid setup model registry must keep setup current (\(validWeightsStatus.action): \(validWeightsStatus.reason))"
        )
        require(
            pythonRuntimeCanSafelyCreateManagedVenv(paths: upgradePaths, python312: bundlePython),
            "the declared bundled Python must be verified before an environment refresh"
        )

        for (key, changedValue) in [
            ("requirements_lock_sha256", String(repeating: "d", count: 64)),
            ("dependency_lock_metadata_sha256", String(repeating: "e", count: 64)),
        ] {
            var changedLockManifest = currentManifest
            changedLockManifest[key] = changedValue
            writeJSON(changedLockManifest, to: upgradePaths.manifest)
            let changedLockStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
            require(
                changedLockStatus.action == "setup_required"
                    && changedLockStatus.reason == "\(key)_changed",
                "a changed \(key) must require a full setup"
            )
            require(
                managedVenvRefreshDecision(
                    paths: upgradePaths,
                    python312: bundlePython,
                    setupStatus: changedLockStatus
                ) == .recreate(reason: "\(key)_changed"),
                "a changed \(key) must recreate the managed dependency environment"
            )
        }

        var developmentManifestWithoutLocks = currentManifest
        developmentManifestWithoutLocks.removeValue(forKey: "requirements_lock_sha256")
        developmentManifestWithoutLocks.removeValue(forKey: "dependency_lock_metadata_sha256")
        writeJSON(developmentManifestWithoutLocks, to: upgradePaths.manifest)
        let missingDevelopmentLockStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            missingDevelopmentLockStatus.action == "mark_current",
            "a development manifest without lock fingerprints must remain compatible"
        )
        require(
            managedVenvRefreshDecision(
                paths: upgradePaths,
                python312: bundlePython,
                setupStatus: missingDevelopmentLockStatus
            ) == .reuse,
            "missing optional development lock fingerprints must not recreate the venv"
        )

        var developmentManifestWithNullLocks = currentManifest
        developmentManifestWithNullLocks["requirements_lock_sha256"] = NSNull()
        developmentManifestWithNullLocks["dependency_lock_metadata_sha256"] = NSNull()
        writeJSON(developmentManifestWithNullLocks, to: upgradePaths.manifest)
        let nullDevelopmentRecord = currentBundleRecord(paths: upgradePaths)
        require(
            nullDevelopmentRecord["requirements_lock_sha256"] == nil
                && nullDevelopmentRecord["dependency_lock_metadata_sha256"] == nil,
            "null development lock fingerprints must remain absent from bundle identity"
        )
        let nullDevelopmentLockStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            nullDevelopmentLockStatus.action == "mark_current",
            "a development manifest with null lock fingerprints must remain compatible"
        )
        writeJSON(currentManifest, to: upgradePaths.manifest)

        var unsafeRuntimeManifest = currentManifest
        unsafeRuntimeManifest["python_runtime"] = [
            "python_executable": "../outside-python",
            "bundled": true,
        ]
        writeJSON(unsafeRuntimeManifest, to: upgradePaths.manifest)
        require(
            !pythonRuntimeCanSafelyCreateManagedVenv(paths: upgradePaths, python312: bundlePython),
            "an unsafe bundled Python manifest path must block environment refresh"
        )
        writeJSON(currentManifest, to: upgradePaths.manifest)

        require(
            managedVenvRefreshDecision(
                paths: upgradePaths,
                python312: bundlePython,
                setupStatus: validWeightsStatus
            ) == .reuse,
            "a clean current venv must remain reusable"
        )
        for reason in [
            "legacy_setup_state",
            "dependency_set_id_changed",
            "constraints_sha256_changed",
            "fpsample_wheel_sha256_changed",
            "acvl_utils_wheel_sha256_changed",
            "installed_bundled_dependency_missing_or_invalid",
        ] {
            require(
                managedVenvRefreshDecision(
                    paths: upgradePaths,
                    python312: bundlePython,
                    setupStatus: SetupStatus(state: nil, action: "setup_required", reason: reason)
                ) == .recreate(reason: reason),
                "\(reason) must replace the stale dependency environment"
            )
        }
        for status in [
            SetupStatus(state: nil, action: "mark_current", reason: "resource_only_change"),
            SetupStatus(state: nil, action: "resync_wheel", reason: "wheel_changed"),
            SetupStatus(state: nil, action: "resync_wheel", reason: "wheel_marker_missing_or_stale"),
            SetupStatus(state: nil, action: "setup_required", reason: "setup_weights_manifest_sha256_changed"),
            SetupStatus(state: nil, action: "setup_required", reason: "setup_weights_missing_or_invalid"),
        ] {
            require(
                managedVenvRefreshDecision(
                    paths: upgradePaths,
                    python312: bundlePython,
                    setupStatus: status
                ) == .reuse,
                "\(status.reason) must not delete a reusable dependency environment"
            )
        }
        require(
            managedVenvRefreshDecision(
                paths: upgradePaths,
                python312: bundlePython,
                setupStatus: SetupStatus(
                    state: ["status": "success"],
                    action: "setup_required",
                    reason: "unrecognized_setup_required_reason"
                )
            ) == .recreate(reason: "previous_setup_failed_or_indeterminate"),
            "an unrecognized setup-required reason must not reuse a dependency environment"
        )

        var changedRuntimeFingerprintManifest = currentManifest
        changedRuntimeFingerprintManifest["python_runtime_fingerprint"] = "runtime-fixture-v2"
        writeJSON(changedRuntimeFingerprintManifest, to: upgradePaths.manifest)
        let changedRuntimeFingerprintStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            changedRuntimeFingerprintStatus.reason == "venv_python_changed",
            "a declared bundled Python runtime fingerprint change must recreate the venv"
        )
        writeJSON(currentManifest, to: upgradePaths.manifest)

        let originalBundledPython = try! Data(contentsOf: bundlePython)
        try! Data("fixture bundled python replacement".utf8).write(to: bundlePython)
        try! FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: bundlePython.path
        )
        let changedBundledPythonStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            managedVenvRefreshDecision(
                paths: upgradePaths,
                python312: bundlePython,
                setupStatus: changedBundledPythonStatus
            ) == .reuse,
            "a matching complete runtime fingerprint must ignore a timestamp-only executable digest change"
        )
        try! originalBundledPython.write(to: bundlePython)
        try! FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: bundlePython.path
        )
        require(
            SetupCoordinator.setupStatus(paths: upgradePaths).action == "current",
            "restoring the bundled Python must restore the current environment status"
        )
        var legacyRuntimeInstalled = currentRecord
        var legacyRuntimeCurrent = currentRecord
        legacyRuntimeInstalled.removeValue(forKey: "python_runtime_fingerprint")
        legacyRuntimeCurrent.removeValue(forKey: "python_runtime_fingerprint")
        legacyRuntimeCurrent["python_runtime_executable_sha256"] = String(repeating: "b", count: 64)
        require(
            pythonRuntimeFingerprintRequiresEnvironmentRefresh(
                installed: legacyRuntimeInstalled,
                current: legacyRuntimeCurrent
            ),
            "without a full runtime fingerprint, an executable digest change remains a conservative fallback"
        )
        var externalRuntimeInstalled = currentRecord
        var externalRuntimeCurrent = currentRecord
        externalRuntimeInstalled["python_runtime_fingerprint"] = ""
        externalRuntimeCurrent["python_runtime_fingerprint"] = ""
        externalRuntimeCurrent["python_runtime_executable_sha256"] = String(repeating: "c", count: 64)
        require(
            pythonRuntimeFingerprintRequiresEnvironmentRefresh(
                installed: externalRuntimeInstalled,
                current: externalRuntimeCurrent
            ),
            "an external Python build with no complete runtime fingerprint must fall back to its executable digest"
        )
        let fullRuntimeInstalled = currentRecord
        var fullRuntimeCurrent = currentRecord
        fullRuntimeCurrent["python_runtime_executable_sha256"] = String(repeating: "b", count: 64)
        require(
            !pythonRuntimeFingerprintRequiresEnvironmentRefresh(
                installed: fullRuntimeInstalled,
                current: fullRuntimeCurrent
            ),
            "matching complete runtime fingerprints must be canonical over executable digests"
        )

        var pythonSetupRecord = currentRecord
        pythonSetupRecord.removeValue(forKey: "python_runtime_fingerprint")
        pythonSetupRecord.removeValue(forKey: "python_runtime_executable_sha256")
        writeJSON(
            [
                "schema": "totalsegmentator_wrapper_mac.setup_state.v1",
                "status": "success",
                "installed_bundle": pythonSetupRecord,
            ],
            to: upgradePaths.stateJSON
        )
        require(
            SetupCoordinator.setupStatus(paths: upgradePaths).reason == "venv_python_changed",
            "a Python-only setup record must expose its missing runtime fingerprint"
        )
        markBundleCurrent(paths: upgradePaths, reason: "setup_completed")
        let successfulSetupStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            successfulSetupStatus.action == "current",
            "a successful setup must persist Swift's complete installed bundle record"
        )
        let secondRunSentinel = upgradePaths.venvSitePackages
            .appendingPathComponent("second-run-must-survive.txt")
        try! Data("reuse me".utf8).write(to: secondRunSentinel)
        require(
            managedVenvRefreshDecision(
                paths: upgradePaths,
                python312: bundlePython,
                setupStatus: successfulSetupStatus
            ) == .reuse,
            "the second setup run after success must reuse its current environment"
        )
        require(
            FileManager.default.fileExists(atPath: secondRunSentinel.path),
            "a current second setup run must not remove its environment sentinel"
        )
        var wrapperOnlyChangedRecord = currentRecord
        wrapperOnlyChangedRecord["wheel_sha256"] = "old-wrapper-wheel"
        writeJSON(
            [
                "schema": "totalsegmentator_wrapper_mac.setup_state.v1",
                "status": "success",
                "installed_bundle": wrapperOnlyChangedRecord,
            ],
            to: upgradePaths.stateJSON
        )
        try! FileManager.default.removeItem(at: acvlModule)
        let wrapperAndDependencyBrokenStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            wrapperAndDependencyBrokenStatus.action == "setup_required"
                && wrapperAndDependencyBrokenStatus.reason
                    == "installed_bundled_dependency_missing_or_invalid",
            "a wrapper resync must not hide a separately bundled dependency failure"
        )
        try! Data("def convert_semantic_to_instanceseg(): pass".utf8).write(to: acvlModule)
        markBundleCurrent(paths: upgradePaths, reason: "test_restore")

        let refreshPaths = AppPaths(
            resources: resources,
            support: upgradeRoot.appendingPathComponent("RefreshSupport", isDirectory: true)
        )
        let hostileSetupEnvironment = [
            "PIP_TARGET": "/private/hostile-target",
            "PIP_PREFIX": "/private/hostile-prefix",
            "PIP_INDEX_URL": "https://hostile.invalid/simple",
            "PIP_CONFIG_FILE": "/private/hostile-pip.conf",
            "PYTHONPATH": "/private/hostile-pythonpath",
            "PYTHONHOME": "/private/hostile-pythonhome",
            "PYTHONUSERBASE": "/private/hostile-userbase",
        ]
        let isolatedSetupEnvironment = CommandBuilder.launchEnvironment(
            paths: refreshPaths,
            baseEnvironment: hostileSetupEnvironment
        )
        for key in hostileSetupEnvironment.keys where key != "PIP_CONFIG_FILE" {
            require(
                isolatedSetupEnvironment[key] == nil,
                "setup launcher must not inherit hostile \(key)"
            )
        }
        require(
            isolatedSetupEnvironment["PIP_CONFIG_FILE"] == "/dev/null"
                && isolatedSetupEnvironment["PYTHONNOUSERSITE"] == "1",
            "setup launcher must force isolated pip configuration"
        )
        let bootstrapInstall = CommandBuilder.bootstrapInstallCommand(
            python: refreshPaths.venvPython,
            wheel: refreshPaths.resources.appendingPathComponent("fixture.whl")
        )
        require(
            bootstrapInstall.contains("--isolated")
                && bootstrapInstall.contains("--no-deps"),
            "bootstrap pip install must be isolated and dependency-free"
        )
        try! FileManager.default.createDirectory(
            at: refreshPaths.venvPython.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try! FileManager.default.createSymbolicLink(
            at: refreshPaths.venvPython,
            withDestinationURL: bundlePython
        )
        let refreshPyvenv = refreshPaths.support.appendingPathComponent("env/pyvenv.cfg")
        try! validPyvenvConfig.write(to: refreshPyvenv, atomically: true, encoding: .utf8)
        let staleTransitiveSentinel = refreshPaths.venvSitePackages
            .appendingPathComponent("old_transitive_sentinel.txt")
        try! FileManager.default.createDirectory(
            at: staleTransitiveSentinel.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try! Data("stale dependency".utf8).write(to: staleTransitiveSentinel)
        let preservedModelSentinel = refreshPaths.support
            .appendingPathComponent("models/totalsegmentator/weights/keep-model.txt")
        let preservedCaseSentinel = refreshPaths.runs.appendingPathComponent("keep-case.txt")
        let preservedCacheSentinel = refreshPaths.cache.appendingPathComponent("keep-cache.txt")
        for sentinel in [preservedModelSentinel, preservedCaseSentinel, preservedCacheSentinel] {
            try! FileManager.default.createDirectory(
                at: sentinel.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try! Data("preserve".utf8).write(to: sentinel)
        }
        let priorRefreshState: [String: Any] = [
            "status": "success",
            "installed_bundle": ["constraints_sha256": "old-constraints"],
        ]
        writeJSON(priorRefreshState, to: refreshPaths.stateJSON)
        let failedPriorSetup = SetupStatus(
            state: ["status": "failed"],
            action: "setup_required",
            reason: "setup_missing"
        )
        require(
            managedVenvRefreshDecision(
                paths: refreshPaths,
                python312: bundlePython,
                setupStatus: failedPriorSetup
            ) == .recreate(reason: "previous_setup_failed_or_indeterminate"),
            "a failed setup state must never reuse its dependency environment"
        )
        require(
            managedVenvRefreshDecision(
                paths: refreshPaths,
                python312: bundlePython,
                setupStatus: SetupStatus(
                    state: nil,
                    action: "setup_required",
                    reason: "setup_missing"
                )
            ) == .recreate(reason: "previous_setup_failed_or_indeterminate"),
            "an indeterminate setup state with an existing venv must not be reused"
        )
        let refreshStateBefore = try! Data(contentsOf: refreshPaths.stateJSON)
        require(
            managedVenvRefreshDecision(
                paths: refreshPaths,
                python312: bundlePython,
                setupStatus: SetupStatus(
                    state: priorRefreshState,
                    action: "setup_required",
                    reason: "dependency_set_id_changed"
                )
            ) == .recreate(reason: "dependency_set_id_changed"),
            "a dependency-set refresh fixture must choose venv recreation"
        )
        require(
            safelyRemoveManagedVenv(paths: refreshPaths) == .removed,
            "a verified App Support env directory must be removable"
        )
        require(
            !FileManager.default.fileExists(atPath: staleTransitiveSentinel.path),
            "environment recreation must remove a stale transitive dependency"
        )
        for sentinel in [preservedModelSentinel, preservedCaseSentinel, preservedCacheSentinel] {
            require(
                FileManager.default.fileExists(atPath: sentinel.path),
                "environment recreation must preserve models, cases, and cache"
            )
        }
        require(
            try! Data(contentsOf: refreshPaths.stateJSON) == refreshStateBefore,
            "environment removal alone must not record a new bundle state"
        )

        let externalNestedTarget = upgradeRoot.appendingPathComponent("outside-env-symlink-target.txt")
        try! Data("outside".utf8).write(to: externalNestedTarget)
        let replacementEnvLink = refreshPaths.support.appendingPathComponent("env/bin/outside-link")
        try! FileManager.default.createDirectory(
            at: replacementEnvLink.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try! FileManager.default.createSymbolicLink(
            at: replacementEnvLink,
            withDestinationURL: externalNestedTarget
        )
        require(
            safelyRemoveManagedVenv(paths: refreshPaths) == .removed,
            "a managed venv containing symlinks must be removed as links"
        )
        require(
            String(data: try! Data(contentsOf: externalNestedTarget), encoding: .utf8) == "outside",
            "environment removal must not follow a nested venv symlink outside App Support"
        )

        let unsafeRootPaths = AppPaths(
            resources: resources,
            support: upgradeRoot.appendingPathComponent("UnsafeEnvRootSupport", isDirectory: true)
        )
        try! FileManager.default.createDirectory(at: unsafeRootPaths.support, withIntermediateDirectories: true)
        let externalEnv = upgradeRoot.appendingPathComponent("ExternalEnvironment", isDirectory: true)
        let externalEnvSentinel = externalEnv.appendingPathComponent("must-survive.txt")
        try! FileManager.default.createDirectory(at: externalEnv, withIntermediateDirectories: true)
        try! Data("external environment".utf8).write(to: externalEnvSentinel)
        try! FileManager.default.createSymbolicLink(
            at: unsafeRootPaths.support.appendingPathComponent("env"),
            withDestinationURL: externalEnv
        )
        require(
            safelyRemoveManagedVenv(paths: unsafeRootPaths) == .unsafeTarget,
            "a symlink in place of the managed env root must be rejected"
        )
        require(
            String(data: try! Data(contentsOf: externalEnvSentinel), encoding: .utf8) == "external environment",
            "rejected env-root symlink must not delete its external target"
        )

        let nonDirectoryPaths = AppPaths(
            resources: resources,
            support: upgradeRoot.appendingPathComponent("NonDirectoryEnvSupport", isDirectory: true)
        )
        try! FileManager.default.createDirectory(at: nonDirectoryPaths.support, withIntermediateDirectories: true)
        let nonDirectoryEnv = nonDirectoryPaths.support.appendingPathComponent("env")
        try! Data("not a directory".utf8).write(to: nonDirectoryEnv)
        require(
            safelyRemoveManagedVenv(paths: nonDirectoryPaths) == .unsafeTarget,
            "a non-directory managed env target must be rejected"
        )
        require(
            String(data: try! Data(contentsOf: nonDirectoryEnv), encoding: .utf8) == "not a directory",
            "rejecting a non-directory env target must leave it untouched"
        )

        let externalSupport = upgradeRoot.appendingPathComponent("ExternalSupport", isDirectory: true)
        let externalSupportEnvSentinel = externalSupport.appendingPathComponent("env/keep-external.txt")
        try! FileManager.default.createDirectory(
            at: externalSupportEnvSentinel.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try! Data("external support".utf8).write(to: externalSupportEnvSentinel)
        let linkedSupport = upgradeRoot.appendingPathComponent("LinkedSupport", isDirectory: true)
        try! FileManager.default.createSymbolicLink(at: linkedSupport, withDestinationURL: externalSupport)
        let linkedSupportPaths = AppPaths(resources: resources, support: linkedSupport)
        require(
            safelyRemoveManagedVenv(paths: linkedSupportPaths) == .unsafeTarget,
            "a symlinked App Support root must be rejected before env deletion"
        )
        require(
            String(data: try! Data(contentsOf: externalSupportEnvSentinel), encoding: .utf8) == "external support",
            "rejecting a symlinked support root must preserve the external environment"
        )

        try! FileManager.default.removeItem(at: acvlModule)
        let missingBundledDependencyStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            missingBundledDependencyStatus.action == "setup_required"
                && missingBundledDependencyStatus.reason == "installed_bundled_dependency_missing_or_invalid",
            "missing bundled dependency files must never be treated as current"
        )
        try! Data("def convert_semantic_to_instanceseg(): pass".utf8).write(to: acvlModule)

        var staleACVLRecord = currentRecord
        staleACVLRecord["acvl_utils_wheel_sha256"] = "old-acvl-wheel"
        writeJSON(
            [
                "schema": "totalsegmentator_wrapper_mac.setup_state.v1",
                "status": "success",
                "installed_bundle": staleACVLRecord,
            ],
            to: upgradePaths.stateJSON
        )
        let staleACVLStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            staleACVLStatus.reason == "acvl_utils_wheel_sha256_changed",
            "an acvl-utils wheel identity change must require full setup"
        )
        writeJSON(
            [
                "schema": "totalsegmentator_wrapper_mac.setup_state.v1",
                "status": "success",
                "installed_bundle": currentRecord,
            ],
            to: upgradePaths.stateJSON
        )

        var missingBundledPythonManifest = currentManifest
        missingBundledPythonManifest.removeValue(forKey: "python_runtime")
        writeJSON(missingBundledPythonManifest, to: upgradePaths.manifest)
        let unresolvedBundledPythonStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            unresolvedBundledPythonStatus.reason == "python312_missing",
            "an unresolved bundled Python must never be treated as current"
        )
        writeJSON(currentManifest, to: upgradePaths.manifest)

        try! FileManager.default.removeItem(at: pyvenvConfig)
        let missingPyvenvStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            missingPyvenvStatus.reason == "venv_python_changed",
            "missing pyvenv.cfg must never be treated as current"
        )
        try! "home = \(bundlePython.deletingLastPathComponent().path)\n".write(
            to: pyvenvConfig,
            atomically: true,
            encoding: .utf8
        )
        let missingExecutableStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            missingExecutableStatus.reason == "venv_python_changed",
            "pyvenv.cfg without executable must never be treated as current"
        )
        try! validPyvenvConfig.write(to: pyvenvConfig, atomically: true, encoding: .utf8)

        try! FileManager.default.removeItem(at: installedMetadata)
        let missingPackageStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            missingPackageStatus.action == "resync_wheel"
                && missingPackageStatus.reason == "installed_package_missing_or_invalid",
            "missing installed package metadata must trigger wheel resync"
        )
        try! "Name: totalsegmentator-wrapper-mac\nVersion: 0.1.0\n".write(
            to: installedMetadata,
            atomically: true,
            encoding: .utf8
        )
        let wrongPackageVersionStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            wrongPackageVersionStatus.reason == "installed_package_missing_or_invalid",
            "an installed package version mismatch must not be current"
        )
        try! "Name: totalsegmentator-wrapper-mac\nVersion: 0.2.0\n".write(
            to: installedMetadata,
            atomically: true,
            encoding: .utf8
        )

        try! FileManager.default.removeItem(at: upgradePaths.venvPython)
        let missingVenvStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(missingVenvStatus.action == "setup_required", "missing venv Python must require setup")
        require(missingVenvStatus.reason == "venv_missing", "missing venv must have a stable reason")
        try! FileManager.default.createSymbolicLink(
            at: upgradePaths.venvPython,
            withDestinationURL: bundlePython
        )

        let firstModel = setupWeightsRoot
            .appendingPathComponent("Dataset113_fixture", isDirectory: true)
            .appendingPathComponent("trainer/fold_0/checkpoint_final.pth")
        try! Data("short".utf8).write(to: firstModel)
        let truncatedWeightsStatus = SetupCoordinator.setupStatus(paths: upgradePaths)
        require(
            truncatedWeightsStatus.action == "setup_required",
            "a truncated required model must return to setup"
        )
        require(
            truncatedWeightsStatus.reason == "setup_weights_missing_or_invalid",
            "truncated model must have a specific setup reason"
        )

        let diskSupport = upgradeRoot.appendingPathComponent("DiskSupport", isDirectory: true)
        let diskImagePaths = AppPaths(
            resources: URL(fileURLWithPath: "/Volumes/Test/Wrapper.app/Contents/Resources"),
            support: diskSupport
        )
        require(appIsRunningFromDiskImage(paths: diskImagePaths), "a /Volumes app fixture must be rejected")
        let writableExternalVolumeResources =
            "/Volumes/Writable External/Wrapper.app/Contents/Resources"
        require(
            appResourcePathRequiresInstalledLocation(writableExternalVolumeResources),
            "a writable /Volumes app fixture must still be rejected before setup"
        )
        let diskImageStatus = SetupCoordinator.setupStatus(paths: diskImagePaths)
        require(
            diskImageStatus.reason == "app_running_from_disk_image",
            "disk-image setup status must expose the copy-first recovery reason"
        )
        let diskAttemptID = UUID().uuidString.lowercased()
        var diskImageSetupMessage = ""
        let diskImageSetupRC = SetupCoordinator.runSetup(
            paths: diskImagePaths,
            setupAttemptID: diskAttemptID
        ) { _, message in
            diskImageSetupMessage = message
        }
        require(diskImageSetupRC == 2, "disk-image setup must stop before launching a mutation process")
        require(
            diskImageSetupMessage.contains("Applicationsまたは~/Applications")
                && diskImageSetupMessage.contains("開き直して"),
            "disk-image setup must tell the user where to copy the app and to reopen it"
        )
        require(
            stringValue(readJSON(diskImagePaths.stateJSON)?["reason"]) == "app_running_from_disk_image",
            "disk-image setup must save the stable recovery reason"
        )
        require(
            stringValue(readJSON(diskImagePaths.stateJSON)?["setup_attempt_id"]) == diskAttemptID,
            "disk-image failure state must retain the caller's setup attempt"
        )
        require(
            !FileManager.default.fileExists(atPath: diskSupport.appendingPathComponent("env").path),
            "disk-image setup must not create a runtime environment"
        )
        writeJSON(
            ["status": "success", "reason": "stale_previous_reason"],
            to: diskImagePaths.stateJSON
        )
        writeJSON(
            [
                "schema": "totalsegmentator_wrapper_mac.update_install_status.v1",
                "status": "failed",
                "reason": "update_install_failed_rolled_back",
                "stage": "copy",
                "return_code": 1,
            ],
            to: diskImagePaths.updateInstallStatusJSON
        )
        let diskImageAppState = AppState(paths: diskImagePaths)
        diskImageAppState.refreshLaunchState()
        require(
            diskImageAppState.setupError.contains("DMGや外部ボリューム内"),
            "launch refresh must prioritize the current disk-image gate over stale state"
        )
        require(
            diskImageAppState.updateMessage.contains("以前のアプリへ戻して"),
            "a persisted update rollback must be explained after the previous app reopens"
        )
        let translocatedPaths = AppPaths(
            resources: URL(
                fileURLWithPath: "/private/var/folders/test/AppTranslocation/fixture/d/Wrapper.app/Contents/Resources"
            ),
            support: upgradeRoot.appendingPathComponent("TranslocatedSupport", isDirectory: true)
        )
        require(
            appIsRunningFromDiskImage(paths: translocatedPaths),
            "a Gatekeeper-translocated app must require copying before setup"
        )
        let applicationsPaths = AppPaths(
            resources: upgradeRoot.appendingPathComponent("Applications/Wrapper.app/Contents/Resources"),
            support: upgradeRoot.appendingPathComponent("ApplicationsSupport", isDirectory: true)
        )
        require(!appIsRunningFromDiskImage(paths: applicationsPaths), "an Applications copy must be allowed")
        let heldSetupLock: NativeSetupFileLock? = {
            if case .acquired(let lock) = NativeSetupFileLock.acquire(
                paths: applicationsPaths,
                token: "held-fixture"
            ) {
                return lock
            }
            return nil
        }()
        require(heldSetupLock != nil, "the first native setup lock must be acquired")
        let busyAttemptID = UUID().uuidString.lowercased()
        let busySetupRC = SetupCoordinator.runSetup(
            paths: applicationsPaths,
            setupAttemptID: busyAttemptID
        ) { _, _ in }
        require(busySetupRC == 75, "a second native setup must fail immediately as busy")
        require(
            stringValue(readJSON(applicationsPaths.stateJSON)?["reason"]) == "setup_busy",
            "native setup lock contention must persist the stable busy reason"
        )
        let busyState = readJSON(applicationsPaths.stateJSON) ?? [:]
        require(
            stringValue(busyState["setup_attempt_id"]) == busyAttemptID
                && stringValue(busyState["return_code"]) == "75",
            "busy state and return code must share the caller's setup attempt"
        )
        let busySteps = busyState["steps"] as? [[String: Any]]
        require(
            stringValue(busySteps?.last?["name"]) == "acquire_setup_lock",
            "busy failure must report the lock stage instead of Python validation"
        )
        require(
            !FileManager.default.fileExists(
                atPath: applicationsPaths.support.appendingPathComponent("env").path
            ),
            "busy setup must not mutate the runtime environment"
        )
        let busyResyncAttemptID = UUID().uuidString.lowercased()
        let busyResyncRC = SetupCoordinator.resyncWheel(
            paths: applicationsPaths,
            setupAttemptID: busyResyncAttemptID
        )
        require(busyResyncRC == 75, "wheel resync must share the setup lock")
        let busyResyncState = readJSON(applicationsPaths.stateJSON) ?? [:]
        require(
            stringValue(busyResyncState["reason"]) == "setup_busy"
                && stringValue(busyResyncState["setup_attempt_id"]) == busyResyncAttemptID,
            "busy wheel resync must retain its attempt and stable reason"
        )
        heldSetupLock?.release()
        let reacquiredSetupLock: NativeSetupFileLock? = {
            if case .acquired(let lock) = NativeSetupFileLock.acquire(
                paths: applicationsPaths,
                token: "again"
            ) {
                return lock
            }
            return nil
        }()
        require(reacquiredSetupLock != nil, "released native setup lock must be reusable")
        reacquiredSetupLock?.release()
        let resyncAttemptID = UUID().uuidString.lowercased()
        let failedResyncRC = SetupCoordinator.resyncWheel(
            paths: applicationsPaths,
            setupAttemptID: resyncAttemptID
        )
        let failedResyncState = readJSON(applicationsPaths.stateJSON) ?? [:]
        require(failedResyncRC == 2, "missing wheel resync fixture must fail")
        require(
            stringValue(failedResyncState["setup_attempt_id"]) == resyncAttemptID
                && stringValue(failedResyncState["return_code"]) == "2",
            "resync failure state must retain the caller's setup attempt and return code"
        )
        let resyncSteps = failedResyncState["steps"] as? [[String: Any]]
        require(
            stringValue(resyncSteps?.last?["name"]) == "sync_bundle",
            "resync failure must report the sync stage"
        )
        let unsafeSupport = upgradeRoot.appendingPathComponent("UnsafeLockSupport", isDirectory: true)
        try! FileManager.default.createDirectory(at: unsafeSupport, withIntermediateDirectories: true)
        let unrelatedTarget = upgradeRoot.appendingPathComponent("must-not-be-truncated.txt")
        try! Data("preserve me".utf8).write(to: unrelatedTarget)
        try! FileManager.default.createSymbolicLink(
            at: unsafeSupport.appendingPathComponent(".totalsegmentator-wrapper-setup.lock"),
            withDestinationURL: unrelatedTarget
        )
        let unsafeLockPaths = AppPaths(resources: resources, support: unsafeSupport)
        let unsafeLockRejected: Bool
        switch NativeSetupFileLock.acquire(paths: unsafeLockPaths, token: "unsafe") {
        case .failed:
            unsafeLockRejected = true
        case .acquired(let lock):
            lock.release()
            unsafeLockRejected = false
        default:
            unsafeLockRejected = false
        }
        require(unsafeLockRejected, "a symlink setup lock must be rejected")
        require(
            String(data: try! Data(contentsOf: unrelatedTarget), encoding: .utf8) == "preserve me",
            "setup lock acquisition must never truncate a symlink target"
        )
        let hardlinkSupport = upgradeRoot.appendingPathComponent(
            "HardlinkLockSupport",
            isDirectory: true
        )
        try! FileManager.default.createDirectory(
            at: hardlinkSupport,
            withIntermediateDirectories: true
        )
        let hardlinkTarget = upgradeRoot.appendingPathComponent("must-not-be-hardlink-truncated.txt")
        try! Data("preserve hardlink target".utf8).write(to: hardlinkTarget)
        try! FileManager.default.linkItem(
            at: hardlinkTarget,
            to: hardlinkSupport.appendingPathComponent(".totalsegmentator-wrapper-setup.lock")
        )
        let hardlinkPaths = AppPaths(resources: resources, support: hardlinkSupport)
        let hardlinkRejected: Bool
        switch NativeSetupFileLock.acquire(paths: hardlinkPaths, token: "unsafe-hardlink") {
        case .failed:
            hardlinkRejected = true
        case .acquired(let lock):
            lock.release()
            hardlinkRejected = false
        default:
            hardlinkRejected = false
        }
        require(hardlinkRejected, "a hardlinked setup lock must be rejected")
        require(
            String(data: try! Data(contentsOf: hardlinkTarget), encoding: .utf8)
                == "preserve hardlink target",
            "setup lock acquisition must never truncate a hardlink target"
        )

        let modelStatusRoot = upgradeRoot.appendingPathComponent(
            "ModelStatusSupport",
            isDirectory: true
        )
        let modelStatusPaths = AppPaths(resources: resources, support: modelStatusRoot)
        let dentalRuntimePaths = [
            "nnUNetTrainer__nnUNetPlans__3d_fullres/dataset.json",
            "nnUNetTrainer__nnUNetPlans__3d_fullres/plans.json",
            "nnUNetTrainer__nnUNetPlans__3d_fullres/fold_0/checkpoint_final.pth",
        ]
        let dentalDatasetRoot = modelStatusPaths.dentalsegReadyMarker.deletingLastPathComponent()
        let dentalManifest = writeRuntimeManifestFixture(
            root: dentalDatasetRoot,
            relativePaths: dentalRuntimePaths
        )
        let dentalReadyMarker: [String: Any] = [
            "schema": "totalsegmentator_wrapper_mac.dentalsegmentator_model_status.v1",
            "model_state": "ready",
            "expected_md5": dentalsegExpectedMD5,
            "dataset_id": "112",
            "dataset_name": "Dataset112_DentalSegmentator_v100",
            "runtime_files": dentalManifest,
            "archive_md5_verified": true,
            "archive_sha256": "bc5510cc93bc2100ab1faccb63512e09c1ca326c738b0a9939c074d82b38a4ac",
            "archive_sha256_verified": true,
            "sha256_provenance": "locally-observed official asset verified against publisher MD5",
            "legacy_marker_migrated": false,
        ]
        writeJSON(dentalReadyMarker, to: modelStatusPaths.dentalsegReadyMarker)
        let modelStatusState = AppState(paths: modelStatusPaths)
        require(modelStatusState.isDentalSegmentatorModelReady, "current DentalSegmentator marker must remain ready")

        var migratedDentalMarker = dentalReadyMarker
        migratedDentalMarker["legacy_marker_migrated"] = true
        migratedDentalMarker["archive_md5_verified"] = false
        migratedDentalMarker["archive_sha256"] = NSNull()
        migratedDentalMarker["archive_sha256_verified"] = false
        migratedDentalMarker["sha256_provenance"] = NSNull()
        writeJSON(migratedDentalMarker, to: modelStatusPaths.dentalsegReadyMarker)
        require(
            modelStatusState.isDentalSegmentatorModelReady,
            "deep-validated migrated DentalSegmentator marker must remain ready"
        )

        var legacyDentalMarker = dentalReadyMarker
        legacyDentalMarker.removeValue(forKey: "runtime_files")
        writeJSON(legacyDentalMarker, to: modelStatusPaths.dentalsegReadyMarker)
        require(!modelStatusState.isDentalSegmentatorModelReady, "legacy DentalSegmentator marker must require migration")
        modelStatusState.creationChoice = .dentalSegmentatorExperimental
        require(modelStatusState.creationChoiceNeedsPreparation, "legacy DentalSegmentator marker must return to lazy preparation")

        var unsafeDentalMarker = dentalReadyMarker
        var unsafeDentalManifest = dentalManifest
        unsafeDentalManifest[0]["path"] = "../checkpoint_final.pth"
        unsafeDentalMarker["runtime_files"] = unsafeDentalManifest
        writeJSON(unsafeDentalMarker, to: modelStatusPaths.dentalsegReadyMarker)
        require(!modelStatusState.isDentalSegmentatorModelReady, "unsafe DentalSegmentator manifest path must be rejected")

        var duplicateDentalMarker = dentalReadyMarker
        var duplicateDentalManifest = dentalManifest
        duplicateDentalManifest[1]["path"] = duplicateDentalManifest[0]["path"]
        duplicateDentalMarker["runtime_files"] = duplicateDentalManifest
        writeJSON(duplicateDentalMarker, to: modelStatusPaths.dentalsegReadyMarker)
        require(!modelStatusState.isDentalSegmentatorModelReady, "duplicate DentalSegmentator manifest path must be rejected")

        var wrongSizeDentalMarker = dentalReadyMarker
        var wrongSizeDentalManifest = dentalManifest
        wrongSizeDentalManifest[0]["size_bytes"] = 99_999
        wrongSizeDentalMarker["runtime_files"] = wrongSizeDentalManifest
        writeJSON(wrongSizeDentalMarker, to: modelStatusPaths.dentalsegReadyMarker)
        require(!modelStatusState.isDentalSegmentatorModelReady, "DentalSegmentator size mismatch must be rejected")

        var invalidHashDentalMarker = dentalReadyMarker
        var invalidHashDentalManifest = dentalManifest
        invalidHashDentalManifest[0]["sha256"] = String(repeating: "A", count: 64)
        invalidHashDentalMarker["runtime_files"] = invalidHashDentalManifest
        writeJSON(invalidHashDentalMarker, to: modelStatusPaths.dentalsegReadyMarker)
        require(!modelStatusState.isDentalSegmentatorModelReady, "non-canonical DentalSegmentator SHA-256 must be rejected")

        let dentalSymlinkFile = dentalDatasetRoot.appendingPathComponent(dentalRuntimePaths[0])
        let dentalSymlinkTarget = modelStatusRoot.appendingPathComponent("external-dental-runtime.json")
        try! Data("runtime fixture 0".utf8).write(to: dentalSymlinkTarget)
        try! FileManager.default.removeItem(at: dentalSymlinkFile)
        try! FileManager.default.createSymbolicLink(
            at: dentalSymlinkFile,
            withDestinationURL: dentalSymlinkTarget
        )
        writeJSON(dentalReadyMarker, to: modelStatusPaths.dentalsegReadyMarker)
        require(!modelStatusState.isDentalSegmentatorModelReady, "DentalSegmentator runtime symlink must be rejected")
        try! FileManager.default.removeItem(at: dentalSymlinkFile)
        try! Data("runtime fixture 0".utf8).write(to: dentalSymlinkFile)

        writeJSON(dentalReadyMarker, to: modelStatusPaths.dentalsegReadyMarker)
        require(modelStatusState.isDentalSegmentatorModelReady, "restored DentalSegmentator marker must be ready")

        let toothRuntimePaths = [
            "Dataset121_ToothFairy2_Teeth/nnUNetTrainer_onlyMirror01_DASegOrd0__nnUNetPlans__3d_fullres_resample_torch_256_bs8_ctnorm/dataset.json",
            "Dataset121_ToothFairy2_Teeth/nnUNetTrainer_onlyMirror01_DASegOrd0__nnUNetPlans__3d_fullres_resample_torch_256_bs8_ctnorm/plans.json",
            "Dataset121_ToothFairy2_Teeth/nnUNetTrainer_onlyMirror01_DASegOrd0__nnUNetPlans__3d_fullres_resample_torch_256_bs8_ctnorm/fold_5/checkpoint_final.pth",
            "Dataset123_ToothFairy2fixed_teeth_spacing02_brd3px/nnUNetTrainer__nnUNetPlans__3d_fullres_resample_torch_192_bs8_ctnorm/dataset.json",
            "Dataset123_ToothFairy2fixed_teeth_spacing02_brd3px/nnUNetTrainer__nnUNetPlans__3d_fullres_resample_torch_192_bs8_ctnorm/plans.json",
            "Dataset123_ToothFairy2fixed_teeth_spacing02_brd3px/nnUNetTrainer__nnUNetPlans__3d_fullres_resample_torch_192_bs8_ctnorm/fold_5/checkpoint_final.pth",
        ]
        let toothManifest = writeRuntimeManifestFixture(
            root: modelStatusPaths.toothsegResults,
            relativePaths: toothRuntimePaths
        )
        try! FileManager.default.createDirectory(
            at: modelStatusPaths.toothsegPairDistributions.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try! Data("pair distributions".utf8).write(to: modelStatusPaths.toothsegPairDistributions)
        let toothReadyMarker: [String: Any] = [
            "schema": "totalsegmentator_wrapper_mac.toothseg_model_status.v1",
            "model_state": "ready",
            "expected_md5": toothsegExpectedMD5,
            "pair_distributions_sha256": toothsegPairDistributionsSHA256,
            "semantic_mps_patch_size": toothsegSemanticMPSPatchSize,
            "dataset_ids": ["121", "123"],
            "dataset_names": ["Dataset121_ToothFairy2_Teeth", "Dataset123_ToothFairy2fixed_teeth_spacing02_brd3px"],
            "runtime_files": toothManifest,
        ]
        writeJSON(toothReadyMarker, to: modelStatusPaths.toothsegReadyMarker)
        require(modelStatusState.isToothSegModelReady, "current ToothSeg marker must remain ready")

        var legacyToothMarker = toothReadyMarker
        legacyToothMarker.removeValue(forKey: "runtime_files")
        writeJSON(legacyToothMarker, to: modelStatusPaths.toothsegReadyMarker)
        require(!modelStatusState.isToothSegModelReady, "legacy ToothSeg marker must require migration")
        modelStatusState.creationChoice = .toothSegExperimental
        require(modelStatusState.creationChoiceNeedsPreparation, "legacy ToothSeg marker must return to lazy preparation")

        var missingToothMarker = toothReadyMarker
        missingToothMarker["runtime_files"] = Array(toothManifest.dropLast())
        writeJSON(missingToothMarker, to: modelStatusPaths.toothsegReadyMarker)
        require(!modelStatusState.isToothSegModelReady, "incomplete ToothSeg manifest must be rejected")

        var duplicateToothMarker = toothReadyMarker
        var duplicateToothManifest = toothManifest
        duplicateToothManifest[5]["path"] = duplicateToothManifest[0]["path"]
        duplicateToothMarker["runtime_files"] = duplicateToothManifest
        writeJSON(duplicateToothMarker, to: modelStatusPaths.toothsegReadyMarker)
        require(!modelStatusState.isToothSegModelReady, "duplicate ToothSeg manifest path must be rejected")

        var wrongSizeToothMarker = toothReadyMarker
        var wrongSizeToothManifest = toothManifest
        wrongSizeToothManifest[5]["size_bytes"] = 99_999
        wrongSizeToothMarker["runtime_files"] = wrongSizeToothManifest
        writeJSON(wrongSizeToothMarker, to: modelStatusPaths.toothsegReadyMarker)
        require(!modelStatusState.isToothSegModelReady, "ToothSeg size mismatch must be rejected")

        var invalidHashToothMarker = toothReadyMarker
        var invalidHashToothManifest = toothManifest
        invalidHashToothManifest[5]["sha256"] = "../../invalid"
        invalidHashToothMarker["runtime_files"] = invalidHashToothManifest
        writeJSON(invalidHashToothMarker, to: modelStatusPaths.toothsegReadyMarker)
        require(!modelStatusState.isToothSegModelReady, "invalid ToothSeg SHA-256 must be rejected")

        let toothPairSymlinkTarget = modelStatusRoot.appendingPathComponent("external-pair-distributions.json")
        try! Data("pair distributions".utf8).write(to: toothPairSymlinkTarget)
        try! FileManager.default.removeItem(at: modelStatusPaths.toothsegPairDistributions)
        try! FileManager.default.createSymbolicLink(
            at: modelStatusPaths.toothsegPairDistributions,
            withDestinationURL: toothPairSymlinkTarget
        )
        writeJSON(toothReadyMarker, to: modelStatusPaths.toothsegReadyMarker)
        require(!modelStatusState.isToothSegModelReady, "ToothSeg pair-distribution symlink must be rejected")
        try! FileManager.default.removeItem(at: modelStatusPaths.toothsegPairDistributions)
        try! Data("pair distributions".utf8).write(to: modelStatusPaths.toothsegPairDistributions)

        writeJSON(toothReadyMarker, to: modelStatusPaths.toothsegReadyMarker)
        require(modelStatusState.isToothSegModelReady, "restored ToothSeg marker must be ready")

        let state = AppState(paths: upgradePaths)
        state.creationChoice = .standardArchJaw
        state.safeErrorCode = "toothseg_input_invalid"
        state.safeErrorReason = "ToothSeg input failed."
        state.toothSegRefineFailed = true
        require(
            state.safeErrorCopyText.contains("feature=ToothSeg高精細化"),
            "ToothSeg refine failure copy must not identify the primary creation choice"
        )
        let caseDir = upgradeRoot.appendingPathComponent("case", isDirectory: true)
        state.outputURL = caseDir
        state.resultKind = .inference
        state.resultOutcome = .success
        state.primaryRunBackend = .totalSegmentator
        state.primaryRunTeethDetected = true
        state.toothSegRefineFailed = false
        require(state.canShowToothSegRefine, "successful primary teeth result must offer ToothSeg")
        let toothSegLabelmap = caseDir
            .appendingPathComponent("segmentations", isDirectory: true)
            .appendingPathComponent("toothseg", isDirectory: true)
            .appendingPathComponent("toothseg_fdi_multilabel.nii.gz")
        try! FileManager.default.createDirectory(
            at: toothSegLabelmap.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try! Data("completed ToothSeg".utf8).write(to: toothSegLabelmap)
        require(!state.canShowToothSegRefine, "completed ToothSeg must hide the run button")
        try! FileManager.default.removeItem(at: toothSegLabelmap)
        state.toothSegRefineFailed = true
        require(state.canRetryToothSegRefine, "failed ToothSeg must retain a retry action")
        require(!state.showsDentalPreparationFailureActions, "preparation actions must be hidden by default")
        state.dentalPreparationFailed = true
        require(state.showsDentalPreparationFailureActions, "failed preparation must expose support actions")
        state.dentalPreparationRunning = true
        require(!state.showsDentalPreparationFailureActions, "running preparation must hide failure actions")

        let hostileRunResult: [String: Any] = [
            "error_code": "backend_failed\npath=/Users/patient/private.nii.gz",
            "safe_reason": "failed at /Users/patient/private.nii.gz\nurl=https://example.invalid/secret",
            "mps_state": "validated\npath=/Users/patient",
            "occurred_at": "2026-08-01T01:02:03Z\npath=/Users/patient",
        ]
        let hostileFields = safeRunResultFields(from: hostileRunResult)
        require(hostileFields.errorCode == "operation_failed", "hostile error code must collapse to generic")
        require(
            hostileFields.reason == "The requested operation did not complete.",
            "hostile safe_reason must never be copied from run_result.json"
        )
        require(hostileFields.mpsState == "unknown", "hostile MPS state must collapse to unknown")
        require(hostileFields.occurredAt.isEmpty, "hostile timestamp must be discarded")
        let offsetTimestampFields = safeRunResultFields(from: [
            "error_code": "backend_failed",
            "occurred_at": "2026-08-01T10:02:03+09:00",
        ])
        require(
            offsetTimestampFields.occurredAt == "2026-08-01T01:02:03Z",
            "safe timestamps must be canonical UTC RFC3339 values"
        )
        let expectedRunAttemptID = "8f4c5382-27c5-4de1-8d24-d5aa78cf04d3"
        let validSafeRunResult: [String: Any] = [
            "schema": "totalsegmentator_wrapper_mac.safe_run_result.v1",
            "status": "failed",
            "run_attempt_id": expectedRunAttemptID,
        ]
        require(
            isCurrentSafeRunResultPayload(
                validSafeRunResult,
                expectedRunAttemptID: expectedRunAttemptID
            ),
            "a current safe run result was rejected"
        )
        let invalidSafeRunResults: [(String, [String: Any])] = [
            (
                "missing UUID",
                [
                    "schema": "totalsegmentator_wrapper_mac.safe_run_result.v1",
                    "status": "failed",
                ]
            ),
            (
                "wrong schema",
                [
                    "schema": "totalsegmentator_wrapper_mac.safe_run_result.v0",
                    "status": "failed",
                    "run_attempt_id": expectedRunAttemptID,
                ]
            ),
            (
                "mismatched UUID",
                [
                    "schema": "totalsegmentator_wrapper_mac.safe_run_result.v1",
                    "status": "failed",
                    "run_attempt_id": "00000000-0000-0000-0000-000000000000",
                ]
            ),
            (
                "unallowlisted status",
                [
                    "schema": "totalsegmentator_wrapper_mac.safe_run_result.v1",
                    "status": "partial",
                    "run_attempt_id": expectedRunAttemptID,
                ]
            ),
        ]
        for (name, payload) in invalidSafeRunResults {
            require(
                !isCurrentSafeRunResultPayload(
                    payload,
                    expectedRunAttemptID: expectedRunAttemptID
                ),
                "\(name) must not be accepted as a current safe run result"
            )
        }
        let invalidRunResultRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("swift-invalid-run-result-\(UUID().uuidString)", isDirectory: true)
        let invalidRunResultPaths = AppPaths(
            resources: invalidRunResultRoot.appendingPathComponent("Resources", isDirectory: true),
            support: invalidRunResultRoot.appendingPathComponent("Support", isDirectory: true)
        )
        for (name, payload) in invalidSafeRunResults.prefix(3) {
            let invalidRunResultState = AppState(paths: invalidRunResultPaths)
            invalidRunResultState.safeErrorCode = "backend_failed"
            invalidRunResultState.safeErrorReason = "stale failure"
            invalidRunResultState.teethDetected = true
            invalidRunResultState.refineAvailable = true
            invalidRunResultState.primaryRunTeethDetected = true
            invalidRunResultState.canRunToothSegRefine = true
            require(
                invalidRunResultState.safeErrorCode == "backend_failed"
                    && invalidRunResultState.teethDetected
                    && invalidRunResultState.primaryRunTeethDetected,
                "\(name) fixture must begin with stale run state"
            )
            var hostilePayload = payload
            hostilePayload["error_code"] = "totalseg_backend_nonzero_exit"
            hostilePayload["teeth_detected"] = true
            hostilePayload["refine_available"] = true
            writeJSON(hostilePayload, to: invalidRunResultPaths.runResultJSON)
            invalidRunResultState.loadSafeRunResult()
            require(
                invalidRunResultState.safeErrorCode.isEmpty
                    && invalidRunResultState.safeErrorReason.isEmpty,
                "\(name) must leave safe error fields reset"
            )
            require(
                !invalidRunResultState.teethDetected
                    && !invalidRunResultState.refineAvailable
                    && !invalidRunResultState.primaryRunTeethDetected,
                "\(name) must leave teeth fields reset"
            )
        }
        let secondaryInvalidRunResultState = AppState(paths: invalidRunResultPaths)
        secondaryInvalidRunResultState.safeErrorCode = "backend_failed"
        secondaryInvalidRunResultState.safeErrorReason = "primary failure"
        secondaryInvalidRunResultState.teethDetected = true
        secondaryInvalidRunResultState.refineAvailable = true
        secondaryInvalidRunResultState.primaryRunTeethDetected = true
        secondaryInvalidRunResultState.canRunToothSegRefine = true
        secondaryInvalidRunResultState.loadSafeRunResult(treatAsPrimaryResult: false)
        require(
            secondaryInvalidRunResultState.safeErrorCode == "backend_failed"
                && secondaryInvalidRunResultState.safeErrorReason == "primary failure"
                && secondaryInvalidRunResultState.teethDetected
                && secondaryInvalidRunResultState.refineAvailable
                && secondaryInvalidRunResultState.primaryRunTeethDetected
                && secondaryInvalidRunResultState.canRunToothSegRefine,
            "an invalid secondary result must preserve the primary result state"
        )
        try? FileManager.default.removeItem(at: invalidRunResultRoot)
        let diagnosticAttemptID = UUID().uuidString.lowercased()
        let hostileDiagnosticFields = safeRunResultFields(from: [
            "error_code": "totalseg_backend_nonzero_exit",
            "run_attempt_id": diagnosticAttemptID,
            "failed_stage": "backend_inference\npath=/Users/patient/private.nii.gz",
            "specific_cause": "1.2.840.113619.2.55.3.604688435",
            "retryable": true,
            "recovery_hint_code": "review_local_log_then_retry\nurl=https://example.invalid/private",
            "diagnostic_log_kind": "local_engineering_diagnostic",
            "diagnostic_log_reference": diagnosticAttemptID,
            "backend_version": "1.2.840.113619.2.55.3.604688435",
            "model_version": "2.14.0",
            "runtime_python_version": "3.12",
            "runtime_torch_version": "2.12.0",
            "input_kind": "PatientName=Alice Example",
            "input_size_bucket": "100_to_500_mib",
            "actual_device": "mps",
            "fallback_used": false,
        ])
        require(hostileDiagnosticFields.runAttemptID == diagnosticAttemptID, "run attempt ID was not retained")
        require(hostileDiagnosticFields.failedStage == "unknown", "hostile stage must collapse")
        require(hostileDiagnosticFields.specificCause == "unknown", "DICOM UID must not be a report cause")
        require(hostileDiagnosticFields.recoveryHintCode == "unknown", "hostile recovery hint must collapse")
        require(hostileDiagnosticFields.diagnosticLogKind == "local_engineering_diagnostic", "safe local log kind was lost")
        require(hostileDiagnosticFields.diagnosticLogReference == diagnosticAttemptID, "local log reference must equal attempt ID")
        require(hostileDiagnosticFields.backendVersion == "unknown", "UID-shaped backend version must collapse")
        require(hostileDiagnosticFields.modelVersion == "2.14.0", "allowlisted model version was lost")
        require(hostileDiagnosticFields.inputKind == "unknown", "patient-like input kind must collapse")
        require(hostileDiagnosticFields.inputSizeBucket == "100_to_500_mib", "safe size bucket was lost")
        require(hostileDiagnosticFields.actualDevice == "mps", "safe actual device was lost")
        require(hostileDiagnosticFields.fallbackUsed == "false", "safe no-fallback flag was lost")
        let danglingDiagnosticReference = safeRunResultFields(from: [
            "error_code": "totalseg_backend_nonzero_exit",
            "run_attempt_id": diagnosticAttemptID,
            "diagnostic_log_kind": "unsafe_log_kind",
            "diagnostic_log_reference": diagnosticAttemptID,
        ])
        require(danglingDiagnosticReference.diagnosticLogKind == "none", "unknown log kinds must collapse")
        require(danglingDiagnosticReference.diagnosticLogReference.isEmpty, "unknown log kinds must not retain a dangling reference")
        let hostileDiagnosticReport = safeRunErrorReportText(
            fields: hostileDiagnosticFields,
            appVersion: "0.4.1",
            osVersion: "Version 26.0.0",
            architecture: "arm64",
            feature: "DentalSegmentator（実験的）",
            fallbackInputKind: "nifti",
            timestamp: "2026-08-01T01:02:03Z"
        )
        require(
            hostileDiagnosticReport.contains("run_attempt_id=\(diagnosticAttemptID)"),
            "safe copied report must retain its UUID correlation"
        )
        require(
            hostileDiagnosticReport.contains("diagnostic_log_reference=\(diagnosticAttemptID)"),
            "safe copied report must retain only the matching local-log reference"
        )
        for forbidden in [
            "/Users/patient/private.nii.gz",
            "1.2.840.113619.2.55.3.604688435",
            "https://example.invalid/private",
            "PatientName=Alice Example",
        ] {
            require(
                !hostileDiagnosticReport.contains(forbidden),
                "safe copied report must not contain \(forbidden)"
            )
        }
        let diskFullFields = safeRunResultFields(from: [
            "error_code": "insufficient_disk_space",
            "safe_reason": "hostile override",
            "mps_state": "not_applicable",
        ])
        require(
            diskFullFields.errorCode == "insufficient_disk_space",
            "disk-full preparation failure must retain its stable safe code"
        )
        require(
            diskFullFields.reason.contains("available disk space is insufficient"),
            "disk-full reason must be re-derived from the safe code"
        )

        state.safeErrorCode = "backend_failed"
        state.safeErrorReason = "leak /Users/patient/private.nii.gz\nurl=https://example.invalid/secret"
        state.safeMPSState = "validated"
        state.safeErrorOccurredAt = "2026-08-01T01:02:03Z"
        let knownSafeReport = state.safeErrorCopyText
        require(
            knownSafeReport.contains("reason=The segmentation backend did not complete."),
            "known error reason must be re-derived from the allowlisted code"
        )
        require(!knownSafeReport.contains("/Users/"), "safe report must not contain a local path")
        require(!knownSafeReport.contains("https://"), "safe report must not contain a URL")

        state.safeErrorCode = "backend_failed\npath=/Users/patient/private.nii.gz"
        state.safeErrorReason = "reason\npath=/Users/patient/private.nii.gz"
        state.safeMPSState = "validated\npath=/Users/patient"
        state.safeErrorOccurredAt = "2026-08-01T01:02:03Z\npath=/Users/patient"
        let hostileSafeReport = state.safeErrorCopyText
        require(hostileSafeReport.contains("error_code=operation_failed"), "unknown report code must be generic")
        require(hostileSafeReport.contains("mps_state=unknown"), "unknown report MPS state must be generic")
        require(!hostileSafeReport.contains("/Users/"), "hostile report must not contain a local path")
        require(!hostileSafeReport.contains("https://"), "hostile report must not contain a URL")
        require(!hostileSafeReport.contains("\npath="), "hostile report must not inject fields")

        let hostileTGNetDetail = safeTGNetValidationDetail(from: [
            "error_code": "tgnet_checkpoint_set_incomplete\npath=/Users/patient",
            "details": "missing /Users/patient/checkpoint.h5\nhttps://example.invalid/secret",
            "safe_detail": "unsafe override /Users/patient",
        ])
        require(!hostileTGNetDetail.contains("/Users/"), "TGNet UI detail must not expose a local path")
        require(!hostileTGNetDetail.contains("https://"), "TGNet UI detail must not expose a URL")
        let knownTGNetDetail = safeTGNetValidationDetail(from: [
            "error_code": "tgnet_checkpoint_set_incomplete",
            "details": "missing /Users/patient/checkpoint.h5",
        ])
        require(
            knownTGNetDetail == "必要な2つのcheckpointが揃っていないか、配置が異なります。",
            "TGNet UI detail must be re-derived from the allowlisted code"
        )

        let updateAtomicRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("swift-update-atomic-\(UUID().uuidString)", isDirectory: true)
        let updateApp = updateAtomicRoot.appendingPathComponent(
            "TotalSegmentator Wrapper for Mac.app",
            isDirectory: true
        )
        let updateToken = "11111111-1111-4111-8111-111111111111"
        let updateStage = updateStagingURL(appURL: updateApp, token: updateToken)
        try! FileManager.default.createDirectory(at: updateApp, withIntermediateDirectories: true)
        try! FileManager.default.createDirectory(at: updateStage, withIntermediateDirectories: true)
        try! Data("previous-app".utf8).write(to: updateApp.appendingPathComponent("marker.txt"))
        try! Data("target-app".utf8).write(to: updateStage.appendingPathComponent("marker.txt"))
        require(
            canUseAtomicUpdateSwap(appURL: updateApp),
            "Swift update fixture requires a same-volume directory that supports atomic rename swap"
        )
        try! performAtomicUpdateSwap(appURL: updateApp, stageURL: updateStage)
        require(
            String(data: try! Data(contentsOf: updateApp.appendingPathComponent("marker.txt")), encoding: .utf8) == "target-app",
            "atomic swap must leave the launchable app path on the target bundle"
        )
        require(
            String(data: try! Data(contentsOf: updateStage.appendingPathComponent("marker.txt")), encoding: .utf8) == "previous-app",
            "atomic swap must retain the previous bundle at the controlled stage path"
        )
        do {
            try performAtomicUpdateSwap(
                appURL: updateApp,
                stageURL: updateAtomicRoot.appendingPathComponent("untrusted-stage.app", isDirectory: true)
            )
            fatalError("atomic swap must reject an untrusted stage path")
        } catch {
            // Expected: only the generated same-parent stage name is accepted.
        }
        try! FileManager.default.removeItem(at: updateStage)
        let symlinkTarget = updateAtomicRoot.appendingPathComponent("symlink-target", isDirectory: true)
        try! FileManager.default.createDirectory(at: symlinkTarget, withIntermediateDirectories: true)
        try! FileManager.default.createSymbolicLink(at: updateStage, withDestinationURL: symlinkTarget)
        do {
            try performAtomicUpdateSwap(appURL: updateApp, stageURL: updateStage)
            fatalError("atomic swap must reject a symlink stage")
        } catch {
            // Expected: a symlink must never be renamed into the app path.
        }
        require(
            hasAnyUpdateStageArtifact(appURL: updateApp),
            "a symlink with the controlled staging prefix must block a new automatic update"
        )
        try! FileManager.default.removeItem(at: updateStage)
        let orphanStage = updateAtomicRoot.appendingPathComponent(
            ".TotalSegmentator Wrapper for Mac.app.update-stage-orphan-file"
        )
        try! Data("partial-copy".utf8).write(to: orphanStage)
        require(
            hasAnyUpdateStageArtifact(appURL: updateApp)
                && hasPendingUpdateArtifacts(appURL: updateApp),
            "a non-directory staging-prefix orphan must block a new automatic update"
        )
        try! FileManager.default.removeItem(at: orphanStage)
        let orphanDirectory = updateAtomicRoot.appendingPathComponent(
            ".TotalSegmentator Wrapper for Mac.app.update-stage-orphan-directory",
            isDirectory: true
        )
        try! FileManager.default.createDirectory(at: orphanDirectory, withIntermediateDirectories: true)
        require(
            hasAnyUpdateStageArtifact(appURL: updateApp),
            "a partial staging directory must block a new automatic update"
        )
        try! FileManager.default.removeItem(at: orphanDirectory)
        let orphanOnlyStage = updateAtomicRoot.appendingPathComponent(
            ".TotalSegmentator Wrapper for Mac.app.update-stage-orphan-only",
            isDirectory: true
        )
        let orphanOnlyStatus = updateAtomicRoot.appendingPathComponent("orphan-only-status.json")
        try! FileManager.default.createDirectory(at: orphanOnlyStage, withIntermediateDirectories: true)
        require(
            recoverInterruptedUpdateTransaction(
                appURL: updateApp,
                statusURL: orphanOnlyStatus
            ) == .manualRecoveryRequired,
            "a transaction-less partial stage must stop automatic updates on the next launch"
        )
        require(
            FileManager.default.fileExists(atPath: orphanOnlyStage.path),
            "launch recovery must preserve a transaction-less partial stage for manual recovery"
        )
        let orphanOnlyPayload = try! JSONSerialization.jsonObject(
            with: Data(contentsOf: orphanOnlyStatus)
        ) as! [String: Any]
        require(
            orphanOnlyPayload["reason"] as? String == "update_recovery_required",
            "a transaction-less partial stage must persist the manual recovery reason"
        )
        try! FileManager.default.removeItem(at: orphanOnlyStage)
        for linkKind in ["symlink", "hardlink"] {
            let linkedStage = updateAtomicRoot.appendingPathComponent(
                ".TotalSegmentator Wrapper for Mac.app.update-stage-status-\(linkKind)",
                isDirectory: true
            )
            let statusVictim = updateAtomicRoot.appendingPathComponent("status-victim-\(linkKind).json")
            let linkedStatus = updateAtomicRoot.appendingPathComponent("linked-status-\(linkKind).json")
            try! FileManager.default.createDirectory(at: linkedStage, withIntermediateDirectories: true)
            try! Data("preserve-status-victim".utf8).write(to: statusVictim)
            if linkKind == "symlink" {
                try! FileManager.default.createSymbolicLink(
                    at: linkedStatus,
                    withDestinationURL: statusVictim
                )
            } else {
                try! FileManager.default.linkItem(at: statusVictim, to: linkedStatus)
            }
            require(
                recoverInterruptedUpdateTransaction(
                    appURL: updateApp,
                    statusURL: linkedStatus
                ) == .manualRecoveryRequired,
                "an orphan stage with an unsafe status entry must remain manual recovery"
            )
            require(
                String(data: try! Data(contentsOf: statusVictim), encoding: .utf8)
                    == "preserve-status-victim",
                "update recovery must not change a linked status target"
            )
            let linkedStatusValues = try! linkedStatus.resourceValues(
                forKeys: [.isSymbolicLinkKey]
            )
            if linkKind == "symlink" {
                require(
                    linkedStatusValues.isSymbolicLink == true,
                    "update recovery must preserve an existing status symlink"
                )
            } else {
                var victimInfo = stat()
                var linkedInfo = stat()
                require(
                    lstat(statusVictim.path, &victimInfo) == 0
                        && lstat(linkedStatus.path, &linkedInfo) == 0
                        && victimInfo.st_ino == linkedInfo.st_ino,
                    "update recovery must preserve an existing status hardlink"
                )
            }
            try! FileManager.default.removeItem(at: linkedStage)
            try! FileManager.default.removeItem(at: linkedStatus)
            try! FileManager.default.removeItem(at: statusVictim)
        }
        let previousIdentity = UpdateBundleIdentity(
            bundleID: "jp.chino.totalsegmentator.wrapper.mac",
            teamID: "team123456",
            version: "0.4.0"
        )!
        let targetIdentity = UpdateBundleIdentity(
            bundleID: "jp.chino.totalsegmentator.wrapper.mac",
            teamID: "team123456",
            version: "0.4.1"
        )!
        require(
            compareSemanticVersionTriplets("0.4.1", "0.4.0") == 1,
            "strict semantic version comparison must accept a newer triplet"
        )
        require(
            compareSemanticVersionTriplets("0.4.1", "0.4.1") == 0,
            "strict semantic version comparison must identify equal triplets"
        )
        require(
            compareSemanticVersionTriplets("0.4.1", "0.4.2") == -1,
            "strict semantic version comparison must identify a downgrade"
        )
        require(
            compareSemanticVersionTriplets("0.4", "0.4.1") == nil
                && compareSemanticVersionTriplets("0.4.1-alpha", "0.4.0") == nil,
            "malformed or suffixed versions must fail strict comparison"
        )
        let validUpdateCheckResult: [String: Any] = [
            "schema": "totalsegmentator_wrapper_mac.update_check_result.v1",
            "status": "update_available",
            "manifest_url": "https://updates.example.test/stable-v2/update.json",
            "current_version": "0.4.1",
            "latest_version": "0.4.2",
            "update_available": true,
            "critical": false,
        ]
        require(
            validatedUpdateCheckStatus(
                validUpdateCheckResult,
                expectedManifestURL: "https://updates.example.test/stable-v2/update.json",
                expectedCurrentVersion: "0.4.1"
            ) == "update_available",
            "a current, provenance-bound update result must be accepted"
        )
        for invalidUpdateResult in [
            validUpdateCheckResult.merging(["schema": "unknown"]) { _, new in new },
            validUpdateCheckResult.merging(["manifest_url": "https://updates.example.test/stale.json"]) { _, new in new },
            validUpdateCheckResult.merging(["current_version": "0.4.0"]) { _, new in new },
            validUpdateCheckResult.merging(["latest_version": "0.4.1"]) { _, new in new },
            validUpdateCheckResult.merging(["update_available": false]) { _, new in new },
            validUpdateCheckResult.merging(["critical": true]) { _, new in new },
        ] {
            require(
                validatedUpdateCheckStatus(
                    invalidUpdateResult,
                    expectedManifestURL: "https://updates.example.test/stable-v2/update.json",
                    expectedCurrentVersion: "0.4.1"
                ) == nil,
                "stale or internally inconsistent update results must be rejected"
            )
        }
        for rejectedArguments in [
            [
                "fixture", "--update-atomic-swap", "/tmp/current.app", "/tmp/stage.app",
                "jp.chino.totalsegmentator.wrapper.mac", "team123456",
                "0.4.2", "0.4.1", "0.4.1",
            ],
            [
                "fixture", "--update-atomic-swap", "/tmp/current.app", "/tmp/stage.app",
                "jp.chino.totalsegmentator.wrapper.mac", "team123456",
                "0.4.1", "0.4.1", "0.4.1",
            ],
        ] {
            require(
                runAtomicUpdateSwapIfRequested(arguments: rejectedArguments) == 64,
                "the atomic helper must reject equal-version and downgrade install arguments"
            )
        }
        require(
            updateTransactionRecoveryDecision(
                active: previousIdentity,
                staged: targetIdentity,
                stageExists: true,
                transactionStage: "swap",
                previous: previousIdentity,
                target: targetIdentity
            ) == .discardStagedUpdate,
            "pre-swap interruption must preserve the launchable previous app"
        )
        require(
            updateTransactionRecoveryDecision(
                active: targetIdentity,
                staged: previousIdentity,
                stageExists: true,
                transactionStage: "swap",
                previous: previousIdentity,
                target: targetIdentity
            ) == .finalizeInstalledUpdate,
            "post-swap interruption must finalize only when both bundle identities match"
        )
        require(
            updateTransactionRecoveryDecision(
                active: targetIdentity,
                staged: nil,
                stageExists: false,
                transactionStage: "swap",
                previous: previousIdentity,
                target: targetIdentity
            ) == .finalizeInstalledUpdate,
            "post-cleanup interruption must retain the verified target app"
        )
        require(
            updateTransactionRecoveryDecision(
                active: nil,
                staged: targetIdentity,
                stageExists: true,
                transactionStage: "swap",
                previous: previousIdentity,
                target: targetIdentity
            ) == .manualRecoveryRequired,
            "unknown update state must never be auto-repaired"
        )
        require(
            updateTransactionRecoveryDecision(
                active: previousIdentity,
                staged: nil,
                stageExists: true,
                transactionStage: "swap",
                previous: previousIdentity,
                target: targetIdentity
            ) == .manualRecoveryRequired,
            "a partial stage must never be discarded while the previous app is active"
        )
        require(
            updateTransactionRecoveryDecision(
                active: previousIdentity,
                staged: nil,
                stageExists: false,
                transactionStage: "swap",
                previous: previousIdentity,
                target: targetIdentity
            ) == .manualRecoveryRequired,
            "a missing stage must never be discarded while the previous app is active"
        )
        require(
            updateTransactionRecoveryDecision(
                active: previousIdentity,
                staged: targetIdentity,
                stageExists: true,
                transactionStage: "swapped",
                previous: previousIdentity,
                target: targetIdentity
            ) == .manualRecoveryRequired,
            "a legacy swapped journal cannot discard a target stage while the previous app is active"
        )
        require(
            updateTransactionRecoveryDecision(
                active: targetIdentity,
                staged: nil,
                stageExists: true,
                transactionStage: "swap",
                previous: previousIdentity,
                target: targetIdentity
            ) == .manualRecoveryRequired,
            "a present but invalid post-swap stage requires manual recovery"
        )
        require(
            updateTransactionRecoveryDecision(
                active: targetIdentity,
                staged: previousIdentity,
                stageExists: true,
                transactionStage: "swapped",
                previous: previousIdentity,
                target: targetIdentity
            ) == .finalizeInstalledUpdate,
            "a legacy swapped journal may finalize only a verified previous stage"
        )
        require(
            updateTransactionRecoveryDecision(
                active: targetIdentity,
                staged: nil,
                stageExists: false,
                transactionStage: "stage_copy",
                previous: previousIdentity,
                target: targetIdentity
            ) == .manualRecoveryRequired,
            "pre-swap journal stages must never finalize an update"
        )
        let transactionURL = updateTransactionURL(appURL: updateApp)
        try! Data("{}".utf8).write(to: transactionURL)
        require(
            updateTransactionFileURLIsSafe(transactionURL, appURL: updateApp),
            "a current-user unlinked transaction at the fixed parent path must be readable"
        )
        let transactionHardlink = updateAtomicRoot.appendingPathComponent("transaction-hardlink.json")
        try! FileManager.default.linkItem(at: transactionURL, to: transactionHardlink)
        require(
            !updateTransactionFileURLIsSafe(transactionURL, appURL: updateApp),
            "a hardlinked transaction must be preserved for manual recovery"
        )
        try! FileManager.default.removeItem(at: transactionHardlink)
        require(
            updateTransactionFileURLIsSafe(transactionURL, appURL: updateApp),
            "removing the external hardlink restores the owned transaction contract"
        )
        try! FileManager.default.removeItem(at: transactionURL)
        let updatesWorkspace = updateAtomicRoot.appendingPathComponent("updates", isDirectory: true)
        require(
            prepareOwnedUpdateDirectory(updatesWorkspace),
            "a fresh current-user update directory must be created"
        )
        require(
            prepareOwnedUpdateDirectory(updatesWorkspace),
            "an existing safe update directory must remain reusable"
        )
        let freshHelper = updatesWorkspace.appendingPathComponent("install_fixture", isDirectory: true)
        require(
            createFreshOwnedUpdateDirectory(freshHelper),
            "a fresh helper directory must be created under the safe update root"
        )
        require(
            !createFreshOwnedUpdateDirectory(freshHelper),
            "an existing helper path must never be reused"
        )
        let symlinkWorkspace = updateAtomicRoot.appendingPathComponent("updates-link", isDirectory: true)
        try! FileManager.default.createSymbolicLink(
            at: symlinkWorkspace,
            withDestinationURL: updatesWorkspace
        )
        require(
            !prepareOwnedUpdateDirectory(symlinkWorkspace),
            "a symlink update directory must be rejected"
        )
        let writableWorkspace = updateAtomicRoot.appendingPathComponent("updates-writable", isDirectory: true)
        try! FileManager.default.createDirectory(at: writableWorkspace, withIntermediateDirectories: false)
        try! FileManager.default.setAttributes(
            [.posixPermissions: 0o777],
            ofItemAtPath: writableWorkspace.path
        )
        require(
            !prepareOwnedUpdateDirectory(writableWorkspace),
            "a group/world-writable update directory must be rejected"
        )
        try? FileManager.default.removeItem(at: updateAtomicRoot)

        let bundledResourceRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("swift-bundle-resources-\(UUID().uuidString)", isDirectory: true)
        let bundledResources = bundledResourceRoot.appendingPathComponent("Resources", isDirectory: true)
        let bundledWheels = bundledResources.appendingPathComponent("wheels", isDirectory: true)
        let bundledConstraintsDirectory = bundledResources.appendingPathComponent("constraints", isDirectory: true)
        try! FileManager.default.createDirectory(at: bundledWheels, withIntermediateDirectories: true)
        try! FileManager.default.createDirectory(at: bundledConstraintsDirectory, withIntermediateDirectories: true)
        let bundledWheel = bundledWheels.appendingPathComponent(
            "totalsegmentator_wrapper_mac-0.4.1-cp312-cp312-macosx_11_0_arm64.whl"
        )
        let bundledConstraints = bundledConstraintsDirectory.appendingPathComponent("macos-arm64-py312.txt")
        let bundledWheelData = Data("fixture wrapper wheel".utf8)
        let bundledConstraintsData = Data("fixture constraints".utf8)
        try! bundledWheelData.write(to: bundledWheel)
        try! bundledConstraintsData.write(to: bundledConstraints)
        var bundledManifest: [String: Any] = [
            "wheel_sha256": sha256Hex(bundledWheelData),
            "constraints_sha256": sha256Hex(bundledConstraintsData),
            "bundled": [
                "wheel": bundledWheel.lastPathComponent,
                "constraints": "constraints/macos-arm64-py312.txt",
            ],
        ]
        let bundledManifestURL = bundledResources.appendingPathComponent("setup_manifest.json")
        writeJSON(bundledManifest, to: bundledManifestURL)
        let resolvedBundleResources = CommandBuilder.bundledSetupResources(resources: bundledResources)
        require(
            resolvedBundleResources?.wheel == bundledWheel
                && resolvedBundleResources?.constraints == bundledConstraints,
            "bootstrap resources must resolve only manifest-bound verified files"
        )
        require(
            CommandBuilder.latestWheel(resources: bundledResources) == bundledWheel,
            "latestWheel compatibility API must use the manifest-bound wrapper wheel"
        )

        bundledManifest["wheel_sha256"] = String(repeating: "0", count: 64)
        writeJSON(bundledManifest, to: bundledManifestURL)
        require(
            CommandBuilder.bundledSetupResources(resources: bundledResources) == nil,
            "wrapper wheel hash mismatch must stop bootstrap before pip"
        )
        bundledManifest["wheel_sha256"] = sha256Hex(bundledWheelData)
        let duplicateWrapper = bundledWheels.appendingPathComponent(
            "totalsegmentator_wrapper_mac-0.4.0-cp312-cp312-macosx_11_0_arm64.whl"
        )
        try! Data("unexpected wrapper wheel".utf8).write(to: duplicateWrapper)
        writeJSON(bundledManifest, to: bundledManifestURL)
        require(
            CommandBuilder.bundledSetupResources(resources: bundledResources) == nil,
            "multiple matching wrapper wheels must never select one by sort order"
        )
        try! FileManager.default.removeItem(at: duplicateWrapper)
        bundledManifest["constraints_sha256"] = String(repeating: "f", count: 64)
        writeJSON(bundledManifest, to: bundledManifestURL)
        require(
            CommandBuilder.bundledSetupResources(resources: bundledResources) == nil,
            "constraints hash mismatch must stop bootstrap before pip"
        )
        bundledManifest["constraints_sha256"] = sha256Hex(bundledConstraintsData)
        let wrapperSymlinkTarget = bundledResourceRoot
            .appendingPathComponent("outside-wrapper.whl")
        try! bundledWheelData.write(to: wrapperSymlinkTarget)
        try! FileManager.default.removeItem(at: bundledWheel)
        try! FileManager.default.createSymbolicLink(
            at: bundledWheel,
            withDestinationURL: wrapperSymlinkTarget
        )
        writeJSON(bundledManifest, to: bundledManifestURL)
        require(
            CommandBuilder.bundledSetupResources(resources: bundledResources) == nil,
            "a wrapper-wheel symlink must not be followed during bootstrap"
        )
        try! FileManager.default.removeItem(at: bundledWheel)
        try! bundledWheelData.write(to: bundledWheel)
        var unsafeBundled = bundledManifest["bundled"] as! [String: Any]
        unsafeBundled["wheel"] = "../untrusted-wrapper.whl"
        bundledManifest["bundled"] = unsafeBundled
        writeJSON(bundledManifest, to: bundledManifestURL)
        require(
            CommandBuilder.bundledSetupResources(resources: bundledResources) == nil,
            "unsafe wrapper relative path must not escape Resources/wheels"
        )
        try? FileManager.default.removeItem(at: bundledResourceRoot)

        let smoothPreview = CommandBuilder.surfacePreviewCommand(
            python: upgradePaths.venvPython,
            caseDir: caseDir,
            smoothSurfaces: true
        )
        let rawPreview = CommandBuilder.surfacePreviewCommand(
            python: upgradePaths.venvPython,
            caseDir: caseDir,
            smoothSurfaces: false
        )
        require(
            commandContainsPair(smoothPreview, flag: "--smooth-preset", value: "slicer_like"),
            "smoothing ON must select slicer_like"
        )
        require(
            commandContainsPair(rawPreview, flag: "--smooth-preset", value: "none"),
            "smoothing OFF must select none"
        )
        require(smoothPreview.contains("--defer-stl"), "surface preview must defer STL export")
        require(rawPreview.contains("--defer-stl"), "raw surface preview must defer STL export")
        try? FileManager.default.removeItem(at: upgradeRoot)
    }

    private static func require(_ condition: @autoclosure () -> Bool, _ message: String) {
        if !condition() {
            fatalError(message)
        }
    }

    private static func writeRuntimeManifestFixture(
        root: URL,
        relativePaths: [String]
    ) -> [[String: Any]] {
        relativePaths.enumerated().map { index, relativePath in
            let file = root.appendingPathComponent(relativePath)
            try! FileManager.default.createDirectory(
                at: file.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            let payload = Data("runtime fixture \(index)".utf8)
            try! payload.write(to: file)
            return [
                "path": relativePath,
                "size_bytes": payload.count,
                "sha256": String(repeating: String(format: "%x", (index % 15) + 1), count: 64),
            ]
        }
    }

    private static func commandContainsPair(
        _ command: [String],
        flag: String,
        value: String
    ) -> Bool {
        command.indices.dropLast().contains { index in
            command[index] == flag && command[index + 1] == value
        }
    }
}
