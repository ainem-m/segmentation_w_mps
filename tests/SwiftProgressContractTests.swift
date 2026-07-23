import Foundation

@main
struct SwiftProgressContractTests {
    static func main() {
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
        let currentManifest: [String: Any] = [
            "app_version": "0.2.0",
            "build_id": "app-0.2.0-test",
            "dependency_set_id": "deps-toothseg",
            "wheel_sha256": "new-wheel",
            "constraints_sha256": "new-constraints",
            "normalizer_sha256": "normalizer",
            "dcm2niix_sha256": "dcm2niix",
            "sample1_manifest_sha256": "sample",
            "update_manifest_url": "https://updates.example.test/update.json",
        ]
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
        require(smoothPreview.suffix(2) == ["--smooth-preset", "slicer_like"], "smoothing ON must select slicer_like")
        require(rawPreview.suffix(2) == ["--smooth-preset", "none"], "smoothing OFF must select none")
        try? FileManager.default.removeItem(at: upgradeRoot)
    }

    private static func require(_ condition: @autoclosure () -> Bool, _ message: String) {
        if !condition() {
            fatalError(message)
        }
    }
}
