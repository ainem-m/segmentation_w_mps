import Foundation
import AppKit
import Darwin

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

struct SetupCoordinator {
    static func setupStatus(paths: AppPaths) -> SetupStatus {
        guard let state = readJSON(paths.stateJSON), state["status"] as? String == "success" else {
            return SetupStatus(state: readJSON(paths.stateJSON), action: "setup_required", reason: "setup_missing")
        }
        if let python312 = CommandBuilder.resolvePython312(paths: paths),
           FileManager.default.fileExists(atPath: paths.venvPython.path),
           !venvPythonMatchesBundle(paths: paths, python312: python312) {
            return SetupStatus(state: state, action: "setup_required", reason: "venv_python_changed")
        }
        let current = currentBundleRecord(paths: paths)
        guard let installed = state["installed_bundle"] as? [String: Any] else {
            return SetupStatus(state: state, action: "setup_required", reason: "legacy_setup_state")
        }
        if dictionariesEqual(installed, current), installedWheelMarkerMatches(paths: paths, current: current) {
            return SetupStatus(state: state, action: "current", reason: "current")
        }
        for key in ["dependency_set_id", "constraints_sha256"] {
            if stringValue(installed[key]) != stringValue(current[key]) {
                return SetupStatus(state: state, action: "setup_required", reason: "\(key)_changed")
            }
        }
        if stringValue(installed["wheel_sha256"]) != stringValue(current["wheel_sha256"]) {
            return SetupStatus(state: state, action: "resync_wheel", reason: "wheel_changed")
        }
        if !installedWheelMarkerMatches(paths: paths, current: current) {
            return SetupStatus(state: state, action: "resync_wheel", reason: "wheel_marker_missing_or_stale")
        }
        return SetupStatus(state: state, action: "mark_current", reason: "resource_only_change")
    }

    static func runSetup(paths: AppPaths, onProgress: @escaping (SetupStep, String) -> Void) -> Int32 {
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let runner = ProcessRunner()
        let allowNetwork = ProcessInfo.processInfo.environment["TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_OFFLINE"] != "1"
        let skipMPSCheck = ProcessInfo.processInfo.environment["TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_SKIP_MPS_CHECK"] == "1"

        createRuntimeDirectories(paths: paths)
        writeProgress(paths.launcherLog, step: .createAppSupportDirs, status: "running", message: "Resources: \(paths.resources.path)")

        guard let python312 = CommandBuilder.resolvePython312(paths: paths) else {
            writeProgress(paths.launcherLog, step: .validatePython312, status: "failed", message: "同梱Python 3.12が見つかりません。")
            writeSwiftFailureState(paths: paths, reason: "python312_missing", allowNetwork: allowNetwork)
            onProgress(.setupException, "同梱Python 3.12が見つかりません。")
            return 2
        }
        guard let wheel = CommandBuilder.latestWheel(resources: paths.resources) else {
            writeProgress(paths.launcherLog, step: .installWheel, status: "failed", message: "同梱wheelが見つかりません。")
            writeSwiftFailureState(paths: paths, reason: "wheel_missing", allowNetwork: allowNetwork)
            onProgress(.setupException, "同梱アプリパッケージが見つかりません。")
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
            writeSwiftFailureState(paths: paths, reason: "python_version_unsupported", allowNetwork: allowNetwork)
            onProgress(.setupException, "Python 3.12の確認に失敗しました。")
            return pythonRC
        }

        if FileManager.default.fileExists(atPath: paths.venvPython.path),
           !venvPythonMatchesBundle(paths: paths, python312: python312) {
            onProgress(.createVenv, "専用Python環境を作り直しています。")
            writeProgress(paths.launcherLog, step: .createVenv, status: "running", message: "専用Python環境のPython参照を更新しています。")
            do {
                try FileManager.default.removeItem(at: paths.support.appendingPathComponent("env", isDirectory: true))
            } catch {
                writeSwiftFailureState(paths: paths, reason: "runtime_refresh_failed", allowNetwork: allowNetwork)
                onProgress(.setupException, "専用Python環境の更新に失敗しました。")
                appendLog("Failed to remove stale venv: \(error)\n", to: paths.launcherLog)
                return 2
            }
        }

        if !FileManager.default.fileExists(atPath: paths.venvPython.path) {
            onProgress(.createVenv, "専用Python環境を作成しています。")
            writeProgress(paths.launcherLog, step: .createVenv, status: "running", message: "専用Python環境を作成しています。")
            let rc = runner.run(CommandBuilder.createVenvCommand(python312: python312, paths: paths), environment: environment, logURL: paths.launcherLog)
            if rc != 0 {
                writeSwiftFailureState(paths: paths, reason: "runtime_install_failed", allowNetwork: allowNetwork)
                onProgress(.setupException, "専用Python環境の作成に失敗しました。")
                return rc
            }
        } else {
            writeProgress(paths.launcherLog, step: .createVenv, status: "skipped", message: "既存の専用Python環境を再利用します。")
        }

        onProgress(.bootstrapInstall, "アプリ本体を専用環境へ導入しています。")
        let bootstrapRC = runner.run(CommandBuilder.bootstrapInstallCommand(python: paths.venvPython, wheel: wheel), environment: environment, logURL: paths.launcherLog)
        if bootstrapRC != 0 {
            writeSwiftFailureState(paths: paths, reason: "runtime_install_failed", allowNetwork: allowNetwork)
            onProgress(.setupException, "アプリ本体の導入に失敗しました。")
            return bootstrapRC
        }

        onProgress(.installWheel, "依存パッケージを取得中です。数分かかることがあります。")
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
            writeInstalledWheelMarker(paths: paths)
            onProgress(.complete, "起動準備が完了しました。")
        } else {
            onProgress(.setupException, "セットアップが停止しました。")
        }
        return setupRC
    }

    static func resyncWheel(paths: AppPaths) -> Int32 {
        guard let wheel = CommandBuilder.latestWheel(resources: paths.resources) else {
            return 2
        }
        let runner = ProcessRunner()
        let rc = runner.run(
            CommandBuilder.bootstrapInstallCommand(python: paths.venvPython, wheel: wheel),
            environment: CommandBuilder.launchEnvironment(paths: paths),
            logURL: paths.launcherLog
        )
        if rc == 0 {
            markBundleCurrent(paths: paths, reason: "wheel_resync")
        }
        return rc
    }
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

func writeProgress(_ logURL: URL, step: SetupStep, status: String, message: String) {
    appendLog("SETUP_PROGRESS step=\(step.rawValue) status=\(status) message=\(message)\n", to: logURL)
}

func writeSwiftFailureState(paths: AppPaths, reason: String, allowNetwork: Bool) {
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
        "steps": [
            [
                "name": "validate_python_312",
                "status": "failed",
                "error": reason,
            ]
        ],
    ]
    writeJSON(payload, to: paths.stateJSON)
}

func currentBundleRecord(paths: AppPaths) -> [String: Any] {
    let manifest = readJSON(paths.manifest) ?? [:]
    return [
        "schema": "totalsegmentator_wrapper_mac.installed_bundle.v1",
        "app_version": stringValue(manifest["app_version"] ?? manifest["version"]),
        "build_id": stringValue(manifest["build_id"]),
        "dependency_set_id": stringValue(manifest["dependency_set_id"]),
        "wheel_sha256": stringValue(manifest["wheel_sha256"]),
        "constraints_sha256": stringValue(manifest["constraints_sha256"]),
        "normalizer_sha256": stringValue(manifest["normalizer_sha256"]),
        "dcm2niix_sha256": stringValue(manifest["dcm2niix_sha256"]),
        "sample1_manifest_sha256": stringValue(manifest["sample1_manifest_sha256"]),
        "update_manifest_url": stringValue(manifest["update_manifest_url"]),
    ]
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

func venvPythonMatchesBundle(paths: AppPaths, python312: URL) -> Bool {
    let expected = python312.resolvingSymlinksInPath().standardizedFileURL.path
    let actual = paths.venvPython.resolvingSymlinksInPath().standardizedFileURL.path
    guard actual == expected else {
        return false
    }
    let pyvenv = paths.support
        .appendingPathComponent("env", isDirectory: true)
        .appendingPathComponent("pyvenv.cfg")
    guard let text = try? String(contentsOf: pyvenv, encoding: .utf8) else {
        return true
    }
    let prefix = "executable = "
    guard let line = text.split(separator: "\n").first(where: { $0.hasPrefix(prefix) }) else {
        return true
    }
    let configured = String(line.dropFirst(prefix.count))
    let configuredPath = URL(fileURLWithPath: configured).resolvingSymlinksInPath().standardizedFileURL.path
    return configuredPath == expected
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
