import Foundation
import AppKit
import CryptoKit
import Darwin

@_silgen_name("flock")
private func systemFlock(_ descriptor: Int32, _ operation: Int32) -> Int32

final class ProcessRunner {
    private(set) var process: Process?
    private let lock = NSLock()
    private var terminationRequested = false

    var isTerminationRequested: Bool {
        lock.lock()
        let requested = terminationRequested
        lock.unlock()
        return requested
    }

    func resetTerminationRequest() {
        lock.lock()
        terminationRequested = false
        lock.unlock()
    }

    func terminate(graceSeconds: TimeInterval = 10.0, onForceKill: (() -> Void)? = nil) {
        lock.lock()
        terminationRequested = true
        let current = process
        lock.unlock()
        if current?.isRunning == true {
            current?.terminate()
        }
        guard let current else { return }
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + graceSeconds) { [weak self, weak current] in
            guard let self, let current else { return }
            self.lock.lock()
            let stillCurrent = self.process === current
            self.lock.unlock()
            guard stillCurrent, current.isRunning else { return }
            kill(current.processIdentifier, SIGKILL)
            onForceKill?()
        }
    }

    func run(_ command: [String], environment: [String: String], logURL: URL? = nil) -> Int32 {
        guard !command.isEmpty else {
            return 127
        }
        appendLog("$ " + command.map(quoteForDisplay).joined(separator: " ") + "\n", to: logURL)
        guard !isTerminationRequested else {
            appendLog("Process skipped: stop requested\nreturncode=143\n", to: logURL)
            return 143
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: command[0])
        proc.arguments = Array(command.dropFirst())
        proc.environment = environment

        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe

        lock.lock()
        process = proc
        lock.unlock()

        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            if let text = String(data: data, encoding: .utf8) {
                appendLog(text, to: logURL)
            }
        }

        do {
            try proc.run()
            if isTerminationRequested {
                proc.terminate()
            }
            proc.waitUntilExit()
        } catch {
            appendLog("Process failed: \(error)\n", to: logURL)
            pipe.fileHandleForReading.readabilityHandler = nil
            lock.lock()
            process = nil
            lock.unlock()
            return 127
        }

        pipe.fileHandleForReading.readabilityHandler = nil
        let remaining = pipe.fileHandleForReading.readDataToEndOfFile()
        if !remaining.isEmpty, let text = String(data: remaining, encoding: .utf8) {
            appendLog(text, to: logURL)
        }
        appendLog("returncode=\(proc.terminationStatus)\n", to: logURL)

        lock.lock()
        process = nil
        lock.unlock()
        return proc.terminationStatus
    }

    func runCapturing(_ command: [String], environment: [String: String], logURL: URL? = nil) -> (Int32, String) {
        guard !command.isEmpty else {
            return (127, "")
        }
        appendLog("$ " + command.map(quoteForDisplay).joined(separator: " ") + "\n", to: logURL)
        guard !isTerminationRequested else {
            appendLog("Process skipped: stop requested\nreturncode=143\n", to: logURL)
            return (143, "")
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: command[0])
        proc.arguments = Array(command.dropFirst())
        proc.environment = environment
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe

        lock.lock()
        process = proc
        lock.unlock()

        do {
            try proc.run()
            if isTerminationRequested {
                proc.terminate()
            }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            proc.waitUntilExit()
            let text = String(data: data, encoding: .utf8) ?? ""
            appendLog(text, to: logURL)
            appendLog("returncode=\(proc.terminationStatus)\n", to: logURL)
            lock.lock()
            process = nil
            lock.unlock()
            return (proc.terminationStatus, text)
        } catch {
            appendLog("Process failed: \(error)\n", to: logURL)
            lock.lock()
            process = nil
            lock.unlock()
            return (127, "")
        }
    }
}

struct SetupStatus {
    let state: [String: Any]?
    let action: String
    let reason: String
}

enum NativeSetupLockAcquisition {
    case acquired(NativeSetupFileLock)
    case busy
    case insufficientDiskSpace
    case failed
}

final class NativeSetupFileLock {
    private var descriptor: Int32

    private init(descriptor: Int32) {
        self.descriptor = descriptor
    }

    static func acquire(paths: AppPaths, token: String) -> NativeSetupLockAcquisition {
        do {
            try FileManager.default.createDirectory(at: paths.support, withIntermediateDirectories: true)
        } catch {
            return cocoaErrorIsDiskFull(error) ? .insufficientDiskSpace : .failed
        }
        let lockURL = paths.support.appendingPathComponent(".totalsegmentator-wrapper-setup.lock")
        let descriptor = lockURL.path.withCString {
            Darwin.open($0, O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, mode_t(S_IRUSR | S_IWUSR))
        }
        guard descriptor >= 0 else {
            return errno == ENOSPC ? .insufficientDiskSpace : .failed
        }
        var fileStatus = stat()
        guard Darwin.fstat(descriptor, &fileStatus) == 0,
              (fileStatus.st_mode & S_IFMT) == S_IFREG,
              fileStatus.st_uid == Darwin.geteuid(),
              fileStatus.st_nlink == 1,
              (fileStatus.st_mode & (S_IWGRP | S_IWOTH)) == 0
        else {
            Darwin.close(descriptor)
            return .failed
        }
        guard systemFlock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            let lockError = errno
            Darwin.close(descriptor)
            return lockError == EWOULDBLOCK || lockError == EAGAIN ? .busy : .failed
        }
        let record: [String: Any] = [
            "schema": "totalsegmentator_wrapper_mac.parent_setup_lock.v1",
            "token": token,
            "pid": Int(Darwin.getpid()),
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]),
              writeLockRecord(data, descriptor: descriptor)
        else {
            let writeError = errno
            _ = systemFlock(descriptor, LOCK_UN)
            Darwin.close(descriptor)
            return writeError == ENOSPC ? .insufficientDiskSpace : .failed
        }
        return .acquired(NativeSetupFileLock(descriptor: descriptor))
    }

    func release() {
        guard descriptor >= 0 else { return }
        _ = Darwin.ftruncate(descriptor, 0)
        _ = Darwin.fsync(descriptor)
        _ = systemFlock(descriptor, LOCK_UN)
        _ = Darwin.close(descriptor)
        descriptor = -1
    }

    deinit {
        release()
    }

    private static func writeLockRecord(_ data: Data, descriptor: Int32) -> Bool {
        guard Darwin.ftruncate(descriptor, 0) == 0,
              Darwin.lseek(descriptor, 0, SEEK_SET) == 0
        else {
            return false
        }
        let succeeded = data.withUnsafeBytes { buffer -> Bool in
            guard let baseAddress = buffer.baseAddress else { return data.isEmpty }
            var written = 0
            while written < buffer.count {
                let result = Darwin.write(
                    descriptor,
                    baseAddress.advanced(by: written),
                    buffer.count - written
                )
                if result <= 0 {
                    return false
                }
                written += result
            }
            return true
        }
        return succeeded && Darwin.fsync(descriptor) == 0
    }
}

func cocoaErrorIsDiskFull(_ error: Error) -> Bool {
    let nsError = error as NSError
    return nsError.domain == NSCocoaErrorDomain
        && nsError.code == NSFileWriteOutOfSpaceError
}

struct SetupCoordinator {
    static func setupStatus(paths: AppPaths) -> SetupStatus {
        if appIsRunningFromDiskImage(paths: paths) {
            return SetupStatus(
                state: readJSON(paths.stateJSON),
                action: "setup_required",
                reason: "app_running_from_disk_image"
            )
        }
        guard let state = readJSON(paths.stateJSON), state["status"] as? String == "success" else {
            return SetupStatus(state: readJSON(paths.stateJSON), action: "setup_required", reason: "setup_missing")
        }
        guard FileManager.default.fileExists(atPath: paths.venvPython.path) else {
            let reason = venvPythonEntryExists(paths: paths) ? "venv_python_changed" : "venv_missing"
            return SetupStatus(state: state, action: "setup_required", reason: reason)
        }
        guard let python312 = CommandBuilder.resolvePython312(paths: paths) else {
            return SetupStatus(state: state, action: "setup_required", reason: "python312_missing")
        }
        if !venvPythonMatchesBundle(paths: paths, python312: python312) {
            return SetupStatus(state: state, action: "setup_required", reason: "venv_python_changed")
        }
        let current = currentBundleRecord(paths: paths)
        guard let installed = state["installed_bundle"] as? [String: Any] else {
            return SetupStatus(state: state, action: "setup_required", reason: "legacy_setup_state")
        }
        if dictionariesEqual(installed, current), installedWheelMarkerMatches(paths: paths, current: current) {
            guard installedAppPackageMatchesBundle(paths: paths, current: current) else {
                return SetupStatus(
                    state: state,
                    action: "resync_wheel",
                    reason: "installed_package_missing_or_invalid"
                )
            }
            guard installedBundledDependenciesMatchBundle(paths: paths) else {
                return SetupStatus(
                    state: state,
                    action: "setup_required",
                    reason: "installed_bundled_dependency_missing_or_invalid"
                )
            }
            guard setupWeightsRegistryIsValid(paths: paths, current: current) else {
                return SetupStatus(state: state, action: "setup_required", reason: "setup_weights_missing_or_invalid")
            }
            return SetupStatus(state: state, action: "current", reason: "current")
        }
        for key in [
            "dependency_set_id",
            "constraints_sha256",
            "fpsample_wheel_sha256",
            "acvl_utils_wheel_sha256",
            "setup_weights_manifest_sha256",
        ] {
            if stringValue(installed[key]) != stringValue(current[key]) {
                return SetupStatus(state: state, action: "setup_required", reason: "\(key)_changed")
            }
        }
        // Release manifests bind the complete hashed dependency lock. Development
        // manifests intentionally remain usable when these optional fields are absent
        // or null, but once the current bundle declares a fingerprint it is part of the
        // managed-environment identity and cannot be repaired by a wrapper-only resync.
        for key in [
            "requirements_lock_sha256",
            "dependency_lock_metadata_sha256",
            "dependency_wheelhouse_manifest_sha256",
        ] {
            guard let currentFingerprint = optionalBundleFingerprint(current[key]) else {
                continue
            }
            if optionalBundleFingerprint(installed[key]) != currentFingerprint {
                return SetupStatus(state: state, action: "setup_required", reason: "\(key)_changed")
            }
        }
        if pythonRuntimeFingerprintRequiresEnvironmentRefresh(installed: installed, current: current) {
            // A bundled Python update may keep its path while changing the runtime beneath
            // the venv.  The existing recovery code is deliberately reused so old and new
            // setup states do not need a second user-facing error vocabulary.
            return SetupStatus(state: state, action: "setup_required", reason: "venv_python_changed")
        }
        // A wrapper-wheel resync cannot repair the separately bundled runtime
        // dependencies.  Prefer a single clean setup over a resync followed by
        // a second setup attempt when both conditions are present.
        guard installedBundledDependenciesMatchBundle(paths: paths) else {
            return SetupStatus(
                state: state,
                action: "setup_required",
                reason: "installed_bundled_dependency_missing_or_invalid"
            )
        }
        if stringValue(installed["wheel_sha256"]) != stringValue(current["wheel_sha256"]) {
            return SetupStatus(state: state, action: "resync_wheel", reason: "wheel_changed")
        }
        if !installedWheelMarkerMatches(paths: paths, current: current) {
            return SetupStatus(state: state, action: "resync_wheel", reason: "wheel_marker_missing_or_stale")
        }
        guard installedAppPackageMatchesBundle(paths: paths, current: current) else {
            return SetupStatus(
                state: state,
                action: "resync_wheel",
                reason: "installed_package_missing_or_invalid"
            )
        }
        guard setupWeightsRegistryIsValid(paths: paths, current: current) else {
            return SetupStatus(state: state, action: "setup_required", reason: "setup_weights_missing_or_invalid")
        }
        return SetupStatus(state: state, action: "mark_current", reason: "resource_only_change")
    }

    static func runSetup(
        paths: AppPaths,
        setupAttemptID suppliedAttemptID: String? = nil,
        onProgress: @escaping (SetupStep, String) -> Void
    ) -> Int32 {
        let setupAttemptID = suppliedAttemptID.flatMap {
            UUID(uuidString: $0)?.uuidString.lowercased()
        } ?? UUID().uuidString.lowercased()
        var environment = CommandBuilder.launchEnvironment(paths: paths)
        environment["TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_ATTEMPT_ID"] = setupAttemptID
        appendLog(
            "SETUP_ENGINEERING_DIAGNOSTIC setup_attempt_id=\(setupAttemptID) diagnostic_log_kind=local_setup_log\n",
            to: paths.launcherLog
        )
        let runner = ProcessRunner()
        let allowNetwork = ProcessInfo.processInfo.environment["TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_OFFLINE"] != "1"
        let skipMPSCheck = ProcessInfo.processInfo.environment["TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_SKIP_MPS_CHECK"] == "1"

        if appIsRunningFromDiskImage(paths: paths) {
            writeSwiftFailureState(
                paths: paths,
                reason: "app_running_from_disk_image",
                allowNetwork: allowNetwork,
                setupAttemptID: setupAttemptID,
                step: .setupException
            )
            onProgress(
                .setupException,
                "アプリをApplicationsまたは~/Applicationsへコピーし、コピー先から開き直してセットアップしてください。"
            )
            return 2
        }

        let setupFileLock: NativeSetupFileLock
        switch NativeSetupFileLock.acquire(paths: paths, token: setupAttemptID) {
        case .acquired(let lock):
            setupFileLock = lock
        case .busy:
            writeSwiftFailureState(
                paths: paths,
                reason: "setup_busy",
                allowNetwork: allowNetwork,
                setupAttemptID: setupAttemptID,
                step: .acquireSetupLock,
                returnCode: 75
            )
            onProgress(.setupException, "別のセットアップが実行中です。完了後にもう一度お試しください。")
            return 75
        case .insufficientDiskSpace:
            writeSwiftFailureState(
                paths: paths,
                reason: "insufficient_disk_space",
                allowNetwork: allowNetwork,
                setupAttemptID: setupAttemptID,
                step: .acquireSetupLock
            )
            onProgress(.setupException, "セットアップに必要な空き容量が不足しています。")
            return 2
        case .failed:
            writeSwiftFailureState(
                paths: paths,
                reason: "setup_lock_failed",
                allowNetwork: allowNetwork,
                setupAttemptID: setupAttemptID,
                step: .acquireSetupLock
            )
            onProgress(.setupException, "セットアップの排他制御を開始できませんでした。")
            return 2
        }
        defer { setupFileLock.release() }
        environment["TOTALSEGMENTATOR_WRAPPER_MAC_PARENT_SETUP_LOCK_TOKEN"] = setupAttemptID
        environment["TOTALSEGMENTATOR_WRAPPER_MAC_PARENT_SETUP_LOCK_PID"] = String(Darwin.getpid())

        createRuntimeDirectories(paths: paths)
        writeProgress(paths.launcherLog, step: .createAppSupportDirs, status: "running", message: "Resources: \(paths.resources.path)")

        guard let python312 = CommandBuilder.resolvePython312(paths: paths) else {
            writeProgress(paths.launcherLog, step: .validatePython312, status: "failed", message: "同梱Python 3.12が見つかりません。")
            writeSwiftFailureState(paths: paths, reason: "python312_missing", allowNetwork: allowNetwork, setupAttemptID: setupAttemptID, step: .validatePython312)
            onProgress(.setupException, "同梱Python 3.12が見つかりません。")
            return 2
        }
        onProgress(.validateBundledWheels, "同梱アプリパッケージを確認しています。")
        guard let bundledSetupResources = CommandBuilder.bundledSetupResources(resources: paths.resources),
              bundledSetupResources.constraints.standardizedFileURL.path
                == paths.constraints.standardizedFileURL.path
        else {
            writeProgress(
                paths.launcherLog,
                step: .validateBundledWheels,
                status: "failed",
                message: "同梱wheelまたは依存固定ファイルを確認できません。"
            )
            writeSwiftFailureState(
                paths: paths,
                reason: "bundle_manifest_invalid",
                allowNetwork: allowNetwork,
                setupAttemptID: setupAttemptID,
                step: .validateBundledWheels
            )
            onProgress(.setupException, "同梱アプリパッケージを確認できません。")
            return 2
        }
        let wheel = bundledSetupResources.wheel
        writeProgress(
            paths.launcherLog,
            step: .validateBundledWheels,
            status: "success",
            message: "同梱wheelと依存固定ファイルを確認しました。"
        )

        guard pythonRuntimeCanSafelyCreateManagedVenv(paths: paths, python312: python312) else {
            writeProgress(
                paths.launcherLog,
                step: .validatePython312,
                status: "failed",
                message: "同梱Python 3.12の配置を確認できません。"
            )
            writeSwiftFailureState(
                paths: paths,
                reason: "python312_missing",
                allowNetwork: allowNetwork,
                setupAttemptID: setupAttemptID,
                step: .validatePython312
            )
            onProgress(.setupException, "同梱Python 3.12を確認できません。")
            return 2
        }

        onProgress(.validatePython312, "Python 3.12を確認しています。")
        let pythonCheck = [
            python312.path,
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 2)",
        ]
        let pythonRC = runner.run(pythonCheck, environment: environment, logURL: paths.launcherLog)
        if pythonRC != 0 {
            writeSwiftFailureState(paths: paths, reason: "python_version_unsupported", allowNetwork: allowNetwork, setupAttemptID: setupAttemptID, step: .validatePython312, returnCode: pythonRC)
            onProgress(.setupException, "Python 3.12の確認に失敗しました。")
            return pythonRC
        }

        let initialSetupStatus = setupStatus(paths: paths)
        switch managedVenvRefreshDecision(
            paths: paths,
            python312: python312,
            setupStatus: initialSetupStatus
        ) {
        case .reuse:
            break
        case let .recreate(reason):
            onProgress(.createVenv, "専用Python環境を作り直しています。")
            writeProgress(
                paths.launcherLog,
                step: .createVenv,
                status: "running",
                message: "専用Python環境を更新しています（理由: \(reason)）。"
            )
            switch safelyRemoveManagedVenv(paths: paths) {
            case .removed, .notPresent:
                break
            case .unsafeTarget, .failed:
                writeSwiftFailureState(paths: paths, reason: "runtime_refresh_failed", allowNetwork: allowNetwork, setupAttemptID: setupAttemptID, step: .createVenv)
                onProgress(.setupException, "専用Python環境の更新に失敗しました。")
                appendLog(
                    "Refused or failed to remove managed venv (reason=\(reason)).\n",
                    to: paths.launcherLog
                )
                return 2
            }
        }

        if !FileManager.default.fileExists(atPath: paths.venvPython.path) {
            onProgress(.createVenv, "専用Python環境を作成しています。")
            writeProgress(paths.launcherLog, step: .createVenv, status: "running", message: "専用Python環境を作成しています。")
            let rc = runner.run(CommandBuilder.createVenvCommand(python312: python312, paths: paths), environment: environment, logURL: paths.launcherLog)
            if rc != 0 {
                writeSwiftFailureState(paths: paths, reason: "runtime_install_failed", allowNetwork: allowNetwork, setupAttemptID: setupAttemptID, step: .createVenv, returnCode: rc)
                onProgress(.setupException, "専用Python環境の作成に失敗しました。")
                return rc
            }
        } else {
            writeProgress(paths.launcherLog, step: .createVenv, status: "skipped", message: "既存の専用Python環境を再利用します。")
        }

        onProgress(.bootstrapInstall, "アプリ本体を専用環境へ導入しています。")
        let bootstrapRC = runner.run(CommandBuilder.bootstrapInstallCommand(python: paths.venvPython, wheel: wheel), environment: environment, logURL: paths.launcherLog)
        if bootstrapRC != 0 {
            writeSwiftFailureState(paths: paths, reason: "runtime_install_failed", allowNetwork: allowNetwork, setupAttemptID: setupAttemptID, step: .bootstrapInstall, returnCode: bootstrapRC)
            onProgress(.setupException, "アプリ本体の導入に失敗しました。")
            return bootstrapRC
        }

        onProgress(.installWheel, "同梱アプリ本体を導入しています。")
        var setupEnvironment = environment
        setupEnvironment["TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_SUPPRESS_STDOUT_JSON"] = "1"
        let setupRC = runner.run(
            CommandBuilder.setupCommand(
                python: paths.venvPython,
                python312: python312,
                wheel: wheel,
                paths: paths,
                allowNetwork: allowNetwork,
                skipMPSCheck: skipMPSCheck
            ),
            environment: setupEnvironment,
            logURL: paths.launcherLog
        )
        if setupRC == 0 {
            // Python records its own setup result.  Replace only its bundle record with the
            // Swift-side canonical record so newly added runtime fingerprints cannot make the
            // next launch mistake a successful setup for a stale environment.
            markBundleCurrent(paths: paths, reason: "setup_completed")
            onProgress(.complete, "起動準備が完了しました。")
        } else {
            onProgress(.setupException, "セットアップが停止しました。")
        }
        return setupRC
    }

    static func resyncWheel(paths: AppPaths, setupAttemptID suppliedAttemptID: String? = nil) -> Int32 {
        let setupAttemptID = suppliedAttemptID.flatMap {
            UUID(uuidString: $0)?.uuidString.lowercased()
        } ?? UUID().uuidString.lowercased()
        let setupFileLock: NativeSetupFileLock
        switch NativeSetupFileLock.acquire(paths: paths, token: setupAttemptID) {
        case .acquired(let lock):
            setupFileLock = lock
        case .busy:
            writeSwiftFailureState(
                paths: paths,
                reason: "setup_busy",
                allowNetwork: false,
                setupAttemptID: setupAttemptID,
                step: .acquireSetupLock,
                returnCode: 75
            )
            return 75
        case .insufficientDiskSpace:
            writeSwiftFailureState(
                paths: paths,
                reason: "insufficient_disk_space",
                allowNetwork: false,
                setupAttemptID: setupAttemptID,
                step: .acquireSetupLock,
                returnCode: 2
            )
            return 2
        case .failed:
            writeSwiftFailureState(
                paths: paths,
                reason: "setup_lock_failed",
                allowNetwork: false,
                setupAttemptID: setupAttemptID,
                step: .acquireSetupLock,
                returnCode: 2
            )
            return 2
        }
        defer { setupFileLock.release() }
        guard let wheel = CommandBuilder.latestWheel(resources: paths.resources) else {
            writeSwiftFailureState(
                paths: paths,
                reason: "wheel_missing",
                allowNetwork: false,
                setupAttemptID: setupAttemptID,
                step: .syncBundle,
                returnCode: 2
            )
            return 2
        }
        let runner = ProcessRunner()
        var environment = CommandBuilder.launchEnvironment(paths: paths)
        environment["TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_ATTEMPT_ID"] = setupAttemptID
        appendLog(
            "SETUP_ENGINEERING_DIAGNOSTIC setup_attempt_id=\(setupAttemptID) diagnostic_log_kind=local_setup_log\n",
            to: paths.launcherLog
        )
        let rc = runner.run(
            CommandBuilder.bootstrapInstallCommand(python: paths.venvPython, wheel: wheel),
            environment: environment,
            logURL: paths.launcherLog
        )
        if rc == 0 {
            markBundleCurrent(paths: paths, reason: "wheel_resync")
        } else {
            writeSwiftFailureState(
                paths: paths,
                reason: "runtime_install_failed",
                allowNetwork: false,
                setupAttemptID: setupAttemptID,
                step: .syncBundle,
                returnCode: rc
            )
        }
        return rc
    }
}

func appIsRunningFromDiskImage(paths: AppPaths) -> Bool {
    appResourcePathRequiresInstalledLocation(paths.resources.standardizedFileURL.path)
}

func appResourcePathRequiresInstalledLocation(_ path: String) -> Bool {
    if path.contains("/AppTranslocation/") && path.contains(".app/Contents/Resources") {
        return true
    }
    // A venv records the absolute bundled-runtime path. Any app launched from
    // /Volumes can later disappear or remount elsewhere, including a writable
    // external volume, so setup is allowed only from an installed stable path.
    return path.hasPrefix("/Volumes/") && path.contains(".app/Contents/Resources")
}

func setupWeightsRegistryIsValid(paths: AppPaths, current: [String: Any]) -> Bool {
    guard let registry = readJSON(paths.totalsegWeightsRegistry),
          stringValue(registry["schema"]) == "totalsegmentator_wrapper_mac.setup_weights_registry.v2",
          stringValue(registry["totalsegmentator_version"]) == "2.14.0",
          let expectedManifestSHA = current["setup_weights_manifest_sha256"] as? String,
          isLowercaseSHA256(expectedManifestSHA),
          stringValue(registry["setup_weights_manifest_sha256"]) == expectedManifestSHA,
          let integritySource = registry["integrity_source"] as? String,
          let archiveVerified = registry["archive_verified"] as? Bool,
          let legacyMigration = registry["legacy_migration"] as? Bool,
          let assets = registry["assets"] as? [[String: Any]],
          assets.count == 3
    else {
        return false
    }
    if archiveVerified {
        guard integritySource == "official-archive-sha256", !legacyMigration else {
            return false
        }
    } else {
        guard integritySource == "legacy-deep-validation", legacyMigration else {
            return false
        }
    }

    let fileManager = FileManager.default
    var taskIDs = Set<Int>()
    for asset in assets {
        guard let taskID = asset["task_id"] as? Int,
              [113, 115, 297].contains(taskID),
              taskIDs.insert(taskID).inserted,
              let dataset = asset["dataset_dir"] as? String,
              safeSetupPathComponent(dataset),
              asset["integrity_source"] as? String == integritySource,
              let assetArchiveVerified = asset["archive_verified"] as? Bool,
              assetArchiveVerified == archiveVerified,
              let requiredFiles = asset["required_files"] as? [[String: Any]],
              !requiredFiles.isEmpty
        else {
            return false
        }
        if archiveVerified {
            guard let archiveSHA256 = asset["archive_sha256"] as? String,
                  isLowercaseSHA256(archiveSHA256),
                  let archiveSHA256Source = asset["archive_sha256_source"] as? String,
                  !archiveSHA256Source.isEmpty
            else {
                return false
            }
        } else if !(asset["archive_sha256"] == nil || asset["archive_sha256"] is NSNull)
            || !(asset["archive_sha256_source"] == nil || asset["archive_sha256_source"] is NSNull) {
            return false
        }
        let datasetRoot = paths.totalsegWeightsRoot.appendingPathComponent(dataset, isDirectory: true)
        guard setupPathIsDirectoryWithoutSymlink(datasetRoot) else {
            return false
        }
        var relativePaths = Set<String>()
        for required in requiredFiles {
            guard let relative = required["path"] as? String,
                  safeSetupRelativePath(relative),
                  relativePaths.insert(relative).inserted,
                  let expectedSize = required["size_bytes"] as? Int,
                  expectedSize > 0,
                  let expectedSHA256 = required["sha256"] as? String,
                  isLowercaseSHA256(expectedSHA256)
            else {
                return false
            }
            let file = relative.split(separator: "/").reduce(datasetRoot) {
                $0.appendingPathComponent(String($1))
            }
            guard setupPathIsRegularFileWithoutSymlink(
                file,
                beneath: datasetRoot,
                expectedSize: expectedSize,
                fileManager: fileManager
            ) else {
                return false
            }
        }
    }
    return taskIDs == Set([113, 115, 297])
}

func isLowercaseSHA256(_ value: String) -> Bool {
    value.count == 64 && value.allSatisfy { character in
        ("0"..."9").contains(String(character))
            || ("a"..."f").contains(String(character))
    }
}

func safeSetupPathComponent(_ value: String) -> Bool {
    !value.isEmpty
        && value != "."
        && value != ".."
        && !value.contains("/")
        && !value.contains("\\")
        && !value.unicodeScalars.contains { $0.value < 32 || $0.value == 127 }
}

func safeSetupRelativePath(_ value: String) -> Bool {
    let parts = value.split(separator: "/", omittingEmptySubsequences: false)
    return !value.hasPrefix("/")
        && !value.contains("\\")
        && !value.unicodeScalars.contains { $0.value < 32 || $0.value == 127 }
        && !parts.isEmpty
        && parts.allSatisfy { !$0.isEmpty && $0 != "." && $0 != ".." }
}

func setupPathIsDirectoryWithoutSymlink(_ url: URL) -> Bool {
    guard let values = try? url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey]) else {
        return false
    }
    return values.isDirectory == true && values.isSymbolicLink != true
}

func setupPathIsRegularFileWithoutSymlink(
    _ url: URL,
    beneath root: URL,
    expectedSize: Int,
    fileManager: FileManager
) -> Bool {
    let rootPath = root.standardizedFileURL.path
    let filePath = url.standardizedFileURL.path
    guard filePath.hasPrefix(rootPath + "/") else {
        return false
    }
    var current = root
    let relativeComponents = String(filePath.dropFirst(rootPath.count + 1)).split(separator: "/")
    for component in relativeComponents.dropLast() {
        current.appendPathComponent(String(component), isDirectory: true)
        guard setupPathIsDirectoryWithoutSymlink(current) else {
            return false
        }
    }
    guard let attributes = try? fileManager.attributesOfItem(atPath: filePath),
          attributes[.type] as? FileAttributeType == .typeRegular,
          let actualSize = (attributes[.size] as? NSNumber)?.intValue
    else {
        return false
    }
    return actualSize == expectedSize
}

func createRuntimeDirectories(paths: AppPaths) {
    let fm = FileManager.default
    for url in [
        paths.support,
        paths.logs,
        paths.cache,
        paths.cache.appendingPathComponent("pip", isDirectory: true),
        paths.cache.appendingPathComponent("pycache", isDirectory: true),
        paths.runs,
        paths.support.appendingPathComponent("models", isDirectory: true),
        paths.dentalsegRaw,
        paths.dentalsegPreprocessed,
        paths.dentalsegResults,
        paths.toothsegRoot,
        paths.toothsegResults,
        paths.iosMeshSegNetRoot,
    ] {
        try? fm.createDirectory(at: url, withIntermediateDirectories: true)
    }
}

func appendLog(_ text: String, to url: URL?) {
    guard let url else { return }
    do {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: url.path) {
            try Data().write(to: url)
        }
        let handle = try FileHandle(forWritingTo: url)
        try handle.seekToEnd()
        if let data = text.data(using: .utf8) {
            try handle.write(contentsOf: data)
        }
        try handle.close()
    } catch {
        NSLog("Failed to append log \(url.path): \(error)")
    }
}

func clearModelPreparationAttemptArtifacts(
    logURL: URL,
    resultURL: URL,
    logsRoot: URL
) -> Bool {
    do {
        try FileManager.default.createDirectory(at: logsRoot, withIntermediateDirectories: true)
    } catch {
        return false
    }
    guard setupPathIsDirectoryWithoutSymlink(logsRoot) else {
        return false
    }
    return unlinkRegularFileOrSymlinkIfPresent(logURL, directlyBeneath: logsRoot)
        && unlinkRegularFileOrSymlinkIfPresent(resultURL, directlyBeneath: logsRoot)
}

private func unlinkRegularFileOrSymlinkIfPresent(_ url: URL, directlyBeneath root: URL) -> Bool {
    let standardizedRoot = root.standardizedFileURL
    let standardizedURL = url.standardizedFileURL
    guard standardizedURL.deletingLastPathComponent() == standardizedRoot else {
        return false
    }
    var status = stat()
    let lstatResult = standardizedURL.path.withCString { Darwin.lstat($0, &status) }
    if lstatResult != 0 {
        return errno == ENOENT
    }
    let fileType = status.st_mode & S_IFMT
    guard fileType == S_IFREG || fileType == S_IFLNK else {
        return false
    }
    let unlinkResult = standardizedURL.path.withCString { Darwin.unlink($0) }
    return unlinkResult == 0 || errno == ENOENT
}

func writeProgress(_ logURL: URL, step: SetupStep, status: String, message: String) {
    appendLog("SETUP_PROGRESS step=\(step.rawValue) status=\(status) message=\(message)\n", to: logURL)
}

func writeSwiftFailureState(
    paths: AppPaths,
    reason: String,
    allowNetwork: Bool,
    setupAttemptID: String,
    step: SetupStep,
    returnCode: Int32 = 2
) {
    let payload: [String: Any] = [
        "schema": "totalsegmentator_wrapper_mac.setup_state.v1",
        "status": "failed",
        "reason": reason,
        "paths": [
            "app_support": paths.support.path,
            "env_dir": paths.support.appendingPathComponent("env", isDirectory: true).path,
            "logs_dir": paths.logs.path,
            "cache_dir": paths.cache.path,
        ],
        "allow_network": allowNetwork,
        "setup_attempt_id": setupAttemptID,
        "return_code": Int(returnCode),
        "steps": [
            [
                "name": step.rawValue,
                "status": "failed",
                "error": reason,
            ]
        ],
    ]
    writeJSON(payload, to: paths.stateJSON)
}

func currentBundleRecord(paths: AppPaths) -> [String: Any] {
    let manifest = readJSON(paths.manifest) ?? [:]
    var record: [String: Any] = [
        "schema": "totalsegmentator_wrapper_mac.installed_bundle.v1",
        "app_version": stringValue(manifest["app_version"] ?? manifest["version"]),
        "build_id": stringValue(manifest["build_id"]),
        "dependency_set_id": stringValue(manifest["dependency_set_id"]),
        "wheel_sha256": stringValue(manifest["wheel_sha256"]),
        "fpsample_wheel_sha256": stringValue(manifest["fpsample_wheel_sha256"]),
        "acvl_utils_wheel_sha256": stringValue(manifest["acvl_utils_wheel_sha256"]),
        "constraints_sha256": stringValue(manifest["constraints_sha256"]),
        "normalizer_sha256": stringValue(manifest["normalizer_sha256"]),
        "dcm2niix_sha256": stringValue(manifest["dcm2niix_sha256"]),
        "sample1_manifest_sha256": stringValue(manifest["sample1_manifest_sha256"]),
        "setup_weights_manifest_sha256": stringValue(manifest["setup_weights_manifest_sha256"]),
        // `python_runtime_fingerprint` is reserved for a complete, build-produced runtime
        // fingerprint.  Until the build manifest supplies it, the executable digest below is
        // only a supplementary same-path change detector; it cannot represent stdlib changes.
        "python_runtime_fingerprint": declaredPythonRuntimeFingerprint(manifest: manifest),
        "python_runtime_executable_sha256": pythonRuntimeExecutableSHA256(
            paths: paths,
            manifest: manifest
        ),
        "update_manifest_url": stringValue(manifest["update_manifest_url"]),
    ]
    for key in [
        "requirements_lock_sha256",
        "dependency_lock_metadata_sha256",
        "dependency_wheelhouse_manifest_sha256",
    ] {
        if let fingerprint = optionalBundleFingerprint(manifest[key]) {
            record[key] = fingerprint
        }
    }
    return record
}

func optionalBundleFingerprint(_ value: Any?) -> String? {
    guard let fingerprint = value as? String, !fingerprint.isEmpty else {
        return nil
    }
    return fingerprint
}

func markBundleCurrent(paths: AppPaths, reason: String) {
    var state = readJSON(paths.stateJSON) ?? [:]
    state["installed_bundle"] = currentBundleRecord(paths: paths)
    state["last_bundle_resync"] = [
        "reason": reason,
        "status": "state_updated",
        "timestamp": ISO8601DateFormatter().string(from: Date()),
    ]
    writeJSON(state, to: paths.stateJSON)
    writeInstalledWheelMarker(paths: paths)
}

func writeInstalledWheelMarker(paths: AppPaths) {
    guard let sha = currentBundleRecord(paths: paths)["wheel_sha256"] as? String, !sha.isEmpty else { return }
    let marker = paths.support.appendingPathComponent("installed_wheel_sha256.txt")
    try? FileManager.default.createDirectory(at: marker.deletingLastPathComponent(), withIntermediateDirectories: true)
    try? (sha + "\n").write(to: marker, atomically: true, encoding: .utf8)
}

func installedWheelMarkerMatches(paths: AppPaths, current: [String: Any]) -> Bool {
    let marker = paths.support.appendingPathComponent("installed_wheel_sha256.txt")
    guard let expected = current["wheel_sha256"] as? String,
          let text = try? String(contentsOf: marker, encoding: .utf8)
    else {
        return false
    }
    return text.trimmingCharacters(in: .whitespacesAndNewlines) == expected
}

func installedAppPackageMatchesBundle(paths: AppPaths, current: [String: Any]) -> Bool {
    guard let expectedVersion = current["app_version"] as? String,
          !expectedVersion.isEmpty,
          setupPathIsDirectoryWithoutSymlink(paths.venvSitePackages)
    else {
        return false
    }
    let package = paths.venvSitePackages.appendingPathComponent(
        "totalsegmentator_wrapper_mac",
        isDirectory: true
    )
    let packageInit = package.appendingPathComponent("__init__.py")
    guard setupPathIsDirectoryWithoutSymlink(package),
          setupPathIsNonemptyRegularFile(packageInit),
          let entries = try? FileManager.default.contentsOfDirectory(
              at: paths.venvSitePackages,
              includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
              options: [.skipsHiddenFiles]
          )
    else {
        return false
    }
    let distributions = entries.filter {
        $0.lastPathComponent.hasPrefix("totalsegmentator_wrapper_mac-")
            && $0.lastPathComponent.hasSuffix(".dist-info")
    }
    guard distributions.count == 1,
          setupPathIsDirectoryWithoutSymlink(distributions[0])
    else {
        return false
    }
    let metadataURL = distributions[0].appendingPathComponent("METADATA")
    guard setupPathIsNonemptyRegularFile(metadataURL),
          let metadata = try? String(contentsOf: metadataURL, encoding: .utf8)
    else {
        return false
    }
    let fields = metadata.split(separator: "\n").reduce(into: [String: String]()) { result, substring in
        let line = String(substring)
        guard let separator = line.firstIndex(of: ":") else { return }
        let key = String(line[..<separator])
        let value = String(line[line.index(after: separator)...])
            .trimmingCharacters(in: .whitespaces)
        if result[key] == nil {
            result[key] = value
        }
    }
    return fields["Name"]?.lowercased() == "totalsegmentator-wrapper-mac"
        && fields["Version"] == expectedVersion
}

func installedBundledDependenciesMatchBundle(paths: AppPaths) -> Bool {
    installedPythonDistributionMatches(
        sitePackages: paths.venvSitePackages,
        importPackage: "acvl_utils",
        requiredPackageRelativePath: "instance_segmentation/instance_as_semantic_seg.py",
        distributionPrefix: "acvl_utils-",
        expectedName: "acvl-utils",
        expectedVersion: "0.2.6"
    ) && installedPythonDistributionMatches(
        sitePackages: paths.venvSitePackages,
        importPackage: "fpsample",
        requiredPackageRelativePath: "__init__.py",
        distributionPrefix: "fpsample-",
        expectedName: "fpsample",
        expectedVersion: "1.0.2",
        requiredNativePrefix: "_fpsample.cpython-312-",
        requiredNativeSuffix: ".so"
    )
}

func installedPythonDistributionMatches(
    sitePackages: URL,
    importPackage: String,
    requiredPackageRelativePath: String,
    distributionPrefix: String,
    expectedName: String,
    expectedVersion: String,
    requiredNativePrefix: String? = nil,
    requiredNativeSuffix: String? = nil
) -> Bool {
    guard setupPathIsDirectoryWithoutSymlink(sitePackages) else {
        return false
    }
    let package = sitePackages.appendingPathComponent(importPackage, isDirectory: true)
    let requiredPackageFile = requiredPackageRelativePath.split(separator: "/").reduce(package) {
        $0.appendingPathComponent(String($1))
    }
    guard setupPathIsDirectoryWithoutSymlink(package),
          setupPathIsNonemptyRegularFile(requiredPackageFile),
          let entries = try? FileManager.default.contentsOfDirectory(
              at: sitePackages,
              includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
              options: [.skipsHiddenFiles]
          )
    else {
        return false
    }
    let distributions = entries.filter {
        $0.lastPathComponent.hasPrefix(distributionPrefix)
            && $0.lastPathComponent.hasSuffix(".dist-info")
    }
    guard distributions.count == 1,
          setupPathIsDirectoryWithoutSymlink(distributions[0]),
          let metadata = try? String(
              contentsOf: distributions[0].appendingPathComponent("METADATA"),
              encoding: .utf8
          )
    else {
        return false
    }
    let fields = metadata.split(separator: "\n").reduce(into: [String: String]()) { result, substring in
        let line = String(substring)
        guard let separator = line.firstIndex(of: ":") else { return }
        let key = String(line[..<separator])
        let value = String(line[line.index(after: separator)...])
            .trimmingCharacters(in: .whitespaces)
        if result[key] == nil {
            result[key] = value
        }
    }
    let normalizedName = fields["Name"]?
        .lowercased()
        .replacingOccurrences(of: "_", with: "-")
        .replacingOccurrences(of: ".", with: "-")
    guard normalizedName == expectedName, fields["Version"] == expectedVersion else {
        return false
    }
    if let requiredNativePrefix, let requiredNativeSuffix {
        guard let packageEntries = try? FileManager.default.contentsOfDirectory(
            at: package,
            includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey],
            options: [.skipsHiddenFiles]
        ) else {
            return false
        }
        let nativeFiles = packageEntries.filter {
            $0.lastPathComponent.hasPrefix(requiredNativePrefix)
                && $0.lastPathComponent.hasSuffix(requiredNativeSuffix)
                && setupPathIsNonemptyRegularFile($0)
        }
        guard nativeFiles.count == 1 else {
            return false
        }
    }
    return true
}

func setupPathIsNonemptyRegularFile(_ url: URL) -> Bool {
    guard let attributes = try? FileManager.default.attributesOfItem(atPath: url.path),
          attributes[.type] as? FileAttributeType == .typeRegular,
          let size = (attributes[.size] as? NSNumber)?.intValue
    else {
        return false
    }
    return size > 0
}

func venvPythonMatchesBundle(paths: AppPaths, python312: URL) -> Bool {
    guard setupPathIsNonemptyRegularFile(python312.resolvingSymlinksInPath()),
          FileManager.default.isExecutableFile(atPath: python312.path),
          FileManager.default.isExecutableFile(atPath: paths.venvPython.path)
    else {
        return false
    }
    let expected = python312.resolvingSymlinksInPath().standardizedFileURL.path
    let actual = paths.venvPython.resolvingSymlinksInPath().standardizedFileURL.path
    guard actual == expected else {
        return false
    }
    let pyvenv = paths.support
        .appendingPathComponent("env", isDirectory: true)
        .appendingPathComponent("pyvenv.cfg")
    guard setupPathIsNonemptyRegularFile(pyvenv),
          let text = try? String(contentsOf: pyvenv, encoding: .utf8)
    else {
        return false
    }
    var fields: [String: String] = [:]
    for line in text.split(separator: "\n") {
        guard let separator = line.firstIndex(of: "=") else { continue }
        let key = String(line[..<separator]).trimmingCharacters(in: .whitespaces)
        let value = String(line[line.index(after: separator)...])
            .trimmingCharacters(in: .whitespaces)
        if !key.isEmpty, !value.isEmpty, fields[key] == nil {
            fields[key] = value
        }
    }
    guard let configured = fields["executable"],
          let configuredHome = fields["home"]
    else {
        return false
    }
    let configuredPath = URL(fileURLWithPath: configured).resolvingSymlinksInPath().standardizedFileURL.path
    let configuredHomePath = URL(fileURLWithPath: configuredHome)
        .resolvingSymlinksInPath().standardizedFileURL.path
    let expectedHomePath = python312.deletingLastPathComponent()
        .resolvingSymlinksInPath().standardizedFileURL.path
    return configuredPath == expected && configuredHomePath == expectedHomePath
}

func venvPythonEntryExists(paths: AppPaths) -> Bool {
    var entryStatus = stat()
    return paths.venvPython.path.withCString { Darwin.lstat($0, &entryStatus) == 0 }
}

func pythonRuntimeCanSafelyCreateManagedVenv(paths: AppPaths, python312: URL) -> Bool {
    let resolvedPython = python312.resolvingSymlinksInPath().standardizedFileURL
    guard setupPathIsNonemptyRegularFile(resolvedPython),
          FileManager.default.isExecutableFile(atPath: python312.path),
          let manifest = readJSON(paths.manifest),
          let runtime = manifest["python_runtime"] as? [String: Any]
    else {
        return false
    }
    guard runtime["bundled"] as? Bool == true else {
        // An explicit external runtime remains supported.  Its executable and
        // version are checked before the managed env is refreshed below.
        return true
    }
    guard let relativeExecutable = runtime["python_executable"] as? String,
          safeSetupRelativePath(relativeExecutable)
    else {
        return false
    }
    let resources = paths.resources.standardizedFileURL
    let declaredPython = resources
        .appendingPathComponent(relativeExecutable)
        .standardizedFileURL
    let resolvedResources = resources.resolvingSymlinksInPath().standardizedFileURL
    return declaredPython.path == python312.standardizedFileURL.path
        && resolvedPython.path.hasPrefix(resolvedResources.path + "/")
}

enum ManagedVenvRefreshDecision: Equatable {
    case reuse
    case recreate(reason: String)
}

// This deliberately covers only changes that can leave dependency imports in an
// otherwise valid venv stale.  Resource-only updates, wrapper-wheel resync, and
// setup-weight manifest changes keep the environment intact.
func managedVenvRefreshDecision(
    paths: AppPaths,
    python312: URL,
    setupStatus: SetupStatus
) -> ManagedVenvRefreshDecision {
    if venvRequiresRecreation(paths: paths, python312: python312) {
        return .recreate(reason: "venv_python_changed")
    }
    guard setupStatus.action == "setup_required" else {
        return .reuse
    }
    // A declared dependency identity change is sufficient to recreate the
    // environment and is actionable in error/support reports. Preserve that
    // reason even when a legacy state record is absent. Conversely, setup
    // model changes are known not to alter Python dependencies, so keep a
    // compatible environment intact for those explicit reasons.
    switch setupStatus.reason {
    case "legacy_setup_state",
         "dependency_set_id_changed",
         "constraints_sha256_changed",
         "requirements_lock_sha256_changed",
         "dependency_lock_metadata_sha256_changed",
         "dependency_wheelhouse_manifest_sha256_changed",
         "fpsample_wheel_sha256_changed",
         "acvl_utils_wheel_sha256_changed",
         "installed_bundled_dependency_missing_or_invalid":
        return .recreate(reason: setupStatus.reason)
    case "setup_weights_manifest_sha256_changed",
         "setup_weights_missing_or_invalid":
        return .reuse
    default:
        // A failed, missing, malformed, or newly introduced setup-required
        // reason cannot establish that the existing venv is complete. Recreate
        // it rather than silently reusing potentially stale dependencies.
        return .recreate(reason: "previous_setup_failed_or_indeterminate")
    }
}

enum ManagedVenvRemovalResult: Equatable {
    case removed
    case notPresent
    case unsafeTarget
    case failed
}

// Only the fixed `App Support/.../env` child is eligible.  We check the parent
// and root with lstat, move the directory into the same verified parent, and
// remove that moved directory.  Therefore a root symlink is never followed and
// a race replacing it with a symlink is restored rather than traversed.
func safelyRemoveManagedVenv(paths: AppPaths) -> ManagedVenvRemovalResult {
    let support = paths.support.standardizedFileURL
    let environment = support
        .appendingPathComponent("env", isDirectory: true)
        .standardizedFileURL
    guard environment.lastPathComponent == "env",
          environment.deletingLastPathComponent().path == support.path,
          environment.path.hasPrefix(support.path + "/"),
          setupPathIsOwnedDirectoryWithoutSymlink(support)
    else {
        return .unsafeTarget
    }

    var environmentStatus = stat()
    let environmentLstat = environment.path.withCString {
        Darwin.lstat($0, &environmentStatus)
    }
    if environmentLstat != 0 {
        return errno == ENOENT ? .notPresent : .failed
    }
    guard (environmentStatus.st_mode & S_IFMT) == S_IFDIR,
          environmentStatus.st_uid == Darwin.geteuid()
    else {
        return .unsafeTarget
    }

    let staged = support.appendingPathComponent(
        ".totalsegmentator-wrapper-env-refresh-\(UUID().uuidString.lowercased())",
        isDirectory: true
    )
    var stagedStatus = stat()
    let stagedLstat = staged.path.withCString { Darwin.lstat($0, &stagedStatus) }
    guard stagedLstat != 0, errno == ENOENT else {
        return .failed
    }
    let renamed = environment.path.withCString { source in
        staged.path.withCString { destination in
            Darwin.rename(source, destination)
        }
    }
    guard renamed == 0 else {
        return .failed
    }

    var movedStatus = stat()
    let movedLstat = staged.path.withCString { Darwin.lstat($0, &movedStatus) }
    guard movedLstat == 0, (movedStatus.st_mode & S_IFMT) == S_IFDIR else {
        _ = staged.path.withCString { source in
            environment.path.withCString { destination in
                Darwin.rename(source, destination)
            }
        }
        return .unsafeTarget
    }

    do {
        // FileManager removes symbolic links contained in a directory as links;
        // the contract test keeps a target outside App Support to verify that.
        try FileManager.default.removeItem(at: staged)
        return .removed
    } catch {
        _ = staged.path.withCString { source in
            environment.path.withCString { destination in
                Darwin.rename(source, destination)
            }
        }
        return .failed
    }
}

private func setupPathIsOwnedDirectoryWithoutSymlink(_ url: URL) -> Bool {
    var status = stat()
    let result = url.path.withCString { Darwin.lstat($0, &status) }
    return result == 0
        && (status.st_mode & S_IFMT) == S_IFDIR
        && status.st_uid == Darwin.geteuid()
}

func venvRequiresRecreation(paths: AppPaths, python312: URL) -> Bool {
    let envDirectory = paths.support.appendingPathComponent("env", isDirectory: true)
    var envStatus = stat()
    guard envDirectory.path.withCString({ Darwin.lstat($0, &envStatus) == 0 }) else {
        return false
    }
    return !venvPythonMatchesBundle(paths: paths, python312: python312)
}

func declaredPythonRuntimeFingerprint(manifest: [String: Any]) -> String {
    if let fingerprint = manifest["python_runtime_fingerprint"] as? String,
       !fingerprint.isEmpty {
        return fingerprint
    }
    if let runtime = manifest["python_runtime"] as? [String: Any],
       let fingerprint = runtime["fingerprint"] as? String,
       !fingerprint.isEmpty {
        return fingerprint
    }
    return ""
}

func pythonRuntimeFingerprintRequiresEnvironmentRefresh(
    installed: [String: Any],
    current: [String: Any]
) -> Bool {
    let installedFingerprint = stringValue(installed["python_runtime_fingerprint"])
    let currentFingerprint = stringValue(current["python_runtime_fingerprint"])
    if !installedFingerprint.isEmpty || !currentFingerprint.isEmpty {
        // A complete build-produced runtime fingerprint is canonical.  In
        // particular, do not use the executable digest here: Developer ID
        // timestamp signatures can change it without changing the runtime.
        return installedFingerprint != currentFingerprint
    }
    // Older manifests have no full runtime fingerprint.  Retain the executable
    // digest as a conservative migration fallback only for that case.
    return stringValue(installed["python_runtime_executable_sha256"])
        != stringValue(current["python_runtime_executable_sha256"])
}

func pythonRuntimeExecutableSHA256(paths: AppPaths, manifest: [String: Any]) -> String {
    guard let runtime = manifest["python_runtime"] as? [String: Any],
          let bundled = runtime["bundled"] as? Bool
    else {
        return ""
    }
    if bundled {
        guard let relativeExecutable = runtime["python_executable"] as? String,
              safeSetupRelativePath(relativeExecutable)
        else {
            return ""
        }
        let resources = paths.resources.standardizedFileURL
        let executable = resources.appendingPathComponent(relativeExecutable).standardizedFileURL
        guard executable.path.hasPrefix(resources.path + "/") else {
            return ""
        }
        return setupSHA256File(executable.resolvingSymlinksInPath()) ?? ""
    }

    // External runtimes are development-only. Hash only the explicit launch
    // override already used by resolvePython312; do not discover or hash an
    // ambient host interpreter merely to populate a fallback value.
    guard let configured = ProcessInfo.processInfo.environment[
        "TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312"
    ], configured.hasPrefix("/") else {
        return ""
    }
    let executable = URL(fileURLWithPath: configured)
        .resolvingSymlinksInPath()
        .standardizedFileURL
    guard setupPathIsNonemptyRegularFile(executable),
          FileManager.default.isExecutableFile(atPath: executable.path)
    else {
        return ""
    }
    let inspector = ProcessRunner().runCapturing(
        [
            executable.path,
            "-I",
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ],
        environment: [
            "PATH": "/usr/bin:/bin",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": "",
        ]
    )
    guard inspector.0 == 0,
          inspector.1.trimmingCharacters(in: .whitespacesAndNewlines) == "3.12"
    else {
        return ""
    }
    return setupSHA256File(executable) ?? ""
}

private func setupSHA256File(_ url: URL) -> String? {
    guard setupPathIsNonemptyRegularFile(url) else {
        return nil
    }
    do {
        let stream = try FileHandle(forReadingFrom: url)
        defer { try? stream.close() }
        var digest = SHA256()
        while true {
            let block = try stream.read(upToCount: 1024 * 1024) ?? Data()
            if block.isEmpty {
                break
            }
            digest.update(data: block)
        }
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
    } catch {
        return nil
    }
}

func dictionariesEqual(_ lhs: [String: Any], _ rhs: [String: Any]) -> Bool {
    Set(lhs.keys) == Set(rhs.keys) && lhs.keys.allSatisfy { stringValue(lhs[$0]) == stringValue(rhs[$0]) }
}

func stringValue(_ value: Any?) -> String {
    switch value {
    case let string as String:
        return string
    case .some(let value):
        return String(describing: value)
    case .none:
        return ""
    }
}

func quoteForDisplay(_ value: String) -> String {
    if value.rangeOfCharacter(from: CharacterSet.whitespacesAndNewlines) == nil {
        return value
    }
    return "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

func openURLInWorkspace(_ url: URL) {
    NSWorkspace.shared.open(url)
}

func caseSurfacePreview(_ caseDir: URL) -> URL? {
    let preview = caseDir.appendingPathComponent("surface_preview/index.html")
    return FileManager.default.fileExists(atPath: preview.path) ? preview : nil
}

func defaultRunOutput(root: URL) -> URL {
    let formatter = DateFormatter()
    formatter.dateFormat = "yyyyMMdd_HHmmss"
    return root.appendingPathComponent("case_\(formatter.string(from: Date()))", isDirectory: true)
}
