import Foundation
import AppKit
import Darwin

let appSupportName = "TotalSegmentatorWrapperMac"
let appTitle = "TotalSegmentator Wrapper for Mac"
let defaultTeethMarginMM = "5.0"

enum SetupStep: String {
    case idle
    case createAppSupportDirs = "create_app_support_dirs"
    case validatePython312 = "validate_python_312"
    case createVenv = "create_venv"
    case bootstrapInstall = "bootstrap_install"
    case syncBundle = "sync_bundle"
    case installWheel = "install_wheel"
    case configureTotalsegPrivacy = "configure_totalseg_privacy"
    case downloadTotalsegWeights = "download_totalseg_weights"
    case downloadDentalsegWeights = "download_dentalseg_weights"
    case doctor
    case complete
    case setupException = "setup_exception"

    var label: String {
        switch self {
        case .idle: return "待機中"
        case .createAppSupportDirs: return "保存先準備"
        case .validatePython312: return "Python確認"
        case .createVenv: return "専用環境作成"
        case .bootstrapInstall: return "アプリ本体導入"
        case .syncBundle: return "アプリ更新反映"
        case .installWheel: return "依存パッケージ取得"
        case .configureTotalsegPrivacy: return "プライバシー設定"
        case .downloadTotalsegWeights: return "TotalSegmentatorモデル準備"
        case .downloadDentalsegWeights: return "DentalSegmentatorモデル準備"
        case .doctor: return "MPS確認"
        case .complete: return "起動準備完了"
        case .setupException: return "エラー"
        }
    }

    var hint: String {
        switch self {
        case .idle:
            return "セットアップ開始を押してください。"
        case .createAppSupportDirs:
            return "App Support配下に専用ディレクトリを準備しています。"
        case .validatePython312:
            return "同梱Python 3.12を確認しています。"
        case .createVenv:
            return "このアプリ専用のPython環境を作成しています。"
        case .bootstrapInstall:
            return "セットアップ管理用のアプリ本体を専用環境へ導入しています。"
        case .syncBundle:
            return "同梱アプリ更新を専用環境へ反映しています。"
        case .installWheel:
            return "依存パッケージを取得中です。数分かかることがあります。"
        case .configureTotalsegPrivacy:
            return "利用状況データの送信を止めています。"
        case .downloadTotalsegWeights:
            return "初回実行に必要なモデルを取得しています。"
        case .downloadDentalsegWeights:
            return "DentalSegmentatorモデルを取得しています。"
        case .doctor:
            return "PyTorch MPSとCT確認用部品を確認しています。"
        case .complete:
            return "起動準備が完了しました。"
        case .setupException:
            return "セットアップ中にエラーが発生しました。"
        }
    }
}

enum RunMode: String, CaseIterable, Identifiable {
    case archPreview = "歯列と顎骨をまとめて表示"
    case individualTeeth = "歯を1本ずつ分けて表示（ベータ）"

    var id: String { rawValue }

    var task: String {
        switch self {
        case .archPreview: return "craniofacial_structures"
        case .individualTeeth: return "teeth"
        }
    }

    var description: String {
        switch self {
        case .archPreview:
            return "歯列と顎骨をまとめて確認する通常プレビューです。"
        case .individualTeeth:
            return "歯を1本ずつ分けます。ベータ機能のため時間がかかります。"
        }
    }
}

enum SegmentationBackend: String, CaseIterable, Identifiable {
    case totalSegmentator = "TotalSegmentator"
    case dentalSegmentator = "DentalSegmentator"

    var id: String { rawValue }

    var cliValue: String {
        switch self {
        case .totalSegmentator: return "totalsegmentator"
        case .dentalSegmentator: return "dentalsegmentator"
        }
    }

    var description: String {
        switch self {
        case .totalSegmentator:
            return "既定のTotalSegmentator backendです。"
        case .dentalSegmentator:
            return "nnU-Net版DentalSegmentatorを使う実験的backendです。セットアップ済みのZenodoモデルをMPS指定で使います。"
        }
    }
}

struct AppPaths {
    let resources: URL
    let support: URL

    static func current() -> AppPaths {
        let resources: URL
        if let configured = ProcessInfo.processInfo.environment["TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_RESOURCES_DIR"], !configured.isEmpty {
            resources = URL(fileURLWithPath: configured).standardizedFileURL
        } else if let inferredResources = AppPaths.inferBundleResourcesFromExecutable() {
            resources = inferredResources
        } else if let bundleResources = Bundle.main.resourceURL {
            resources = bundleResources.standardizedFileURL
        } else {
            resources = URL(fileURLWithPath: FileManager.default.currentDirectoryPath).standardizedFileURL
        }

        let home = ProcessInfo.processInfo.environment["HOME"].flatMap { $0.isEmpty ? nil : $0 } ?? NSHomeDirectory()
        let supportRoot = URL(fileURLWithPath: home, isDirectory: true)
            .appendingPathComponent("Library/Application Support", isDirectory: true)
        let support = supportRoot.appendingPathComponent(appSupportName, isDirectory: true).standardizedFileURL
        return AppPaths(resources: resources, support: support)
    }

    static func inferBundleResourcesFromExecutable() -> URL? {
        if let bundleResources = resourcesURL(fromBundle: Bundle.main.bundleURL) {
            return bundleResources
        }
        for executablePath in executablePathCandidates() {
            let executable = URL(fileURLWithPath: executablePath).resolvingSymlinksInPath()
            if let resources = resourcesURL(fromExecutable: executable) {
                return resources
            }
        }
        return nil
    }

    static func executablePathCandidates() -> [String] {
        var candidates: [String] = []
        if let executableURL = Bundle.main.executableURL {
            candidates.append(executableURL.path)
        }
        var size: UInt32 = 0
        _ = _NSGetExecutablePath(nil, &size)
        if size > 0 {
            var buffer = [CChar](repeating: 0, count: Int(size))
            if _NSGetExecutablePath(&buffer, &size) == 0 {
                candidates.append(String(cString: buffer))
            }
        }
        if let first = CommandLine.arguments.first, !first.isEmpty {
            candidates.append(first)
        }
        return candidates
    }

    static func resourcesURL(fromBundle bundle: URL) -> URL? {
        let resolved = bundle.resolvingSymlinksInPath()
        guard resolved.pathExtension == "app" else {
            return nil
        }
        let resources = resolved
            .appendingPathComponent("Contents", isDirectory: true)
            .appendingPathComponent("Resources", isDirectory: true)
            .standardizedFileURL
        guard FileManager.default.fileExists(atPath: resources.path) else {
            return nil
        }
        return resources
    }

    static func resourcesURL(fromExecutable executable: URL) -> URL? {
        let macOSDir = executable.deletingLastPathComponent()
        guard macOSDir.lastPathComponent == "MacOS" else {
            return nil
        }
        let contentsDir = macOSDir.deletingLastPathComponent()
        guard contentsDir.lastPathComponent == "Contents" else {
            return nil
        }
        let resources = contentsDir.appendingPathComponent("Resources", isDirectory: true).standardizedFileURL
        guard FileManager.default.fileExists(atPath: resources.path) else {
            return nil
        }
        return resources
    }

    var logs: URL { support.appendingPathComponent("logs", isDirectory: true) }
    var cache: URL { support.appendingPathComponent("cache", isDirectory: true) }
    var runs: URL { support.appendingPathComponent("runs", isDirectory: true) }
    var stateJSON: URL { support.appendingPathComponent("setup_state.json") }
    var setupResultJSON: URL { logs.appendingPathComponent("setup_result.json") }
    var launcherLog: URL { logs.appendingPathComponent("launcher.log") }
    var venvPython: URL { support.appendingPathComponent("env/bin/python") }
    var totalsegBinary: URL { support.appendingPathComponent("env/bin/TotalSegmentator") }
    var dentalsegRoot: URL { support.appendingPathComponent("models/dentalsegmentator", isDirectory: true) }
    var dentalsegRaw: URL { dentalsegRoot.appendingPathComponent("nnUNet_raw", isDirectory: true) }
    var dentalsegPreprocessed: URL { dentalsegRoot.appendingPathComponent("nnUNet_preprocessed", isDirectory: true) }
    var dentalsegResults: URL { dentalsegRoot.appendingPathComponent("nnUNet_results", isDirectory: true) }
    var dentalsegModelMetadata: URL { dentalsegRoot.appendingPathComponent("dentalsegmentator_model.json") }
    var dentalsegInstalledModel: URL {
        dentalsegResults
            .appendingPathComponent("Dataset112_DentalSegmentator_v100", isDirectory: true)
            .appendingPathComponent("nnUNetTrainer__nnUNetPlans__3d_fullres", isDirectory: true)
    }
    var manifest: URL { resources.appendingPathComponent("setup_manifest.json") }
    var constraints: URL { resources.appendingPathComponent("constraints/macos-arm64-py312.txt") }
    var normalizer: URL { resources.appendingPathComponent("bin/totalsegmentator-wrapper-dicom-normalizer") }
    var dcm2niix: URL { resources.appendingPathComponent("bin/dcm2niix") }
    var sampleInput: URL { resources.appendingPathComponent("sample1/input/DZ-CBCT_jawcrop_0p5mm.nii.gz") }
    var sampleViewer: URL { resources.appendingPathComponent("sample1/surface_preview/index.html") }
}

struct CommandBuilder {
    static func latestWheel(resources: URL) -> URL? {
        let wheelDir = resources.appendingPathComponent("wheels", isDirectory: true)
        guard let items = try? FileManager.default.contentsOfDirectory(
            at: wheelDir,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        ) else {
            return nil
        }
        return items
            .filter { $0.lastPathComponent.hasPrefix("totalsegmentator_wrapper_mac-") && $0.pathExtension == "whl" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
            .last
    }

    static func resolvePython312(paths: AppPaths) -> URL? {
        if let configured = ProcessInfo.processInfo.environment["TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312"], !configured.isEmpty {
            return URL(fileURLWithPath: configured).standardizedFileURL
        }
        guard let manifest = readJSON(paths.manifest),
              let runtime = manifest["python_runtime"] as? [String: Any],
              let python = runtime["python_executable"] as? String,
              !python.isEmpty
        else {
            return nil
        }
        if python.hasPrefix("/") {
            return URL(fileURLWithPath: python).standardizedFileURL
        }
        return paths.resources.appendingPathComponent(python).standardizedFileURL
    }

    static func launchEnvironment(paths: AppPaths) -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env.removeValue(forKey: "PYTHONPATH")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PIP_NO_INPUT"] = "1"
        env["PIP_CACHE_DIR"] = paths.cache.appendingPathComponent("pip", isDirectory: true).path
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        env["PYTHONPYCACHEPREFIX"] = paths.cache.appendingPathComponent("pycache", isDirectory: true).path
        env["TOTALSEGMENTATOR_WRAPPER_MAC_APP_SUPPORT"] = paths.support.path
        env["TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_RESOURCES_DIR"] = paths.resources.path
        env["TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER"] = paths.normalizer.path
        env["TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX"] = paths.dcm2niix.path
        env["XDG_CACHE_HOME"] = paths.cache.path
        env["MPLCONFIGDIR"] = paths.cache.appendingPathComponent("matplotlib", isDirectory: true).path
        env["TOTALSEG_HOME_DIR"] = paths.support.appendingPathComponent("models/totalsegmentator", isDirectory: true).path
        env["TOTALSEG_WEIGHTS_PATH"] = paths.support.appendingPathComponent("models/totalsegmentator/weights", isDirectory: true).path
        env["nnUNet_raw"] = paths.dentalsegRaw.path
        env["nnUNet_preprocessed"] = paths.dentalsegPreprocessed.path
        env["nnUNet_results"] = paths.dentalsegResults.path
        let venvBin = paths.support.appendingPathComponent("env/bin", isDirectory: true).path
        let resourceBin = paths.resources.appendingPathComponent("bin", isDirectory: true).path
        let existingPath = env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        env["PATH"] = "\(venvBin):\(resourceBin):\(existingPath)"
        return env
    }

    static func createVenvCommand(python312: URL, paths: AppPaths) -> [String] {
        [python312.path, "-m", "venv", paths.support.appendingPathComponent("env", isDirectory: true).path]
    }

    static func bootstrapInstallCommand(python: URL, wheel: URL) -> [String] {
        [python.path, "-m", "pip", "install", "--force-reinstall", "--no-deps", wheel.path]
    }

    static func setupCommand(
        python: URL,
        python312: URL,
        wheel: URL,
        paths: AppPaths,
        allowNetwork: Bool,
        skipMPSCheck: Bool
    ) -> [String] {
        var command = [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "setup",
            "--python",
            python312.path,
            "--wheel",
            wheel.path,
            "--constraints",
            paths.constraints.path,
            "--json",
            paths.setupResultJSON.path,
            "--use-existing-env",
            "--bundle-manifest",
            paths.manifest.path,
            "--progress-log",
            paths.launcherLog.path,
        ]
        if allowNetwork {
            command.append("--allow-network")
        }
        if skipMPSCheck {
            command.append("--skip-mps-check")
        }
        return command
    }

    static func runCommand(
        python: URL,
        input: URL,
        output: URL,
        mode: RunMode,
        backend: SegmentationBackend,
        device: String,
        higherOrderResampling: Bool,
        paths: AppPaths
    ) -> [String] {
        let runDevice = backend == .dentalSegmentator ? "mps" : device
        var command = [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "run",
            "--input",
            input.path,
            "--output",
            output.path,
            "--backend",
            backend.cliValue,
            "--task",
            mode.task,
            "--device",
            runDevice,
            "--totalseg-bin",
            paths.totalsegBinary.path,
            "--no-copy-input",
        ]
        if backend == .dentalSegmentator {
            command.append("--dentalseg-nnunet-raw")
            command.append(paths.dentalsegRaw.path)
            command.append("--dentalseg-nnunet-preprocessed")
            command.append(paths.dentalsegPreprocessed.path)
            command.append("--dentalseg-nnunet-results")
            command.append(paths.dentalsegResults.path)
            command.append("--dentalseg-fold")
            command.append("0")
            command.append("--dentalseg-disable-tta")
        } else if mode == .individualTeeth {
            command.append("--experimental-teeth")
            command.append("--teeth-crop-margin-mm")
            command.append(defaultTeethMarginMM)
            command.append("--teeth-robust-craniofacial-preflight")
        } else {
            command.append("--robust-crop")
        }
        if backend == .totalSegmentator && higherOrderResampling {
            command.append("--higher-order-resampling")
        }
        return command
    }

    static func dicomAuditCommand(python: URL, dicomDir: URL, outputJSON: URL, paths: AppPaths) -> [String] {
        [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "dicom-normalizer-audit",
            "--dicom-dir",
            dicomDir.path,
            "--output",
            outputJSON.path,
            "--binary",
            paths.normalizer.path,
            "--timeout-sec",
            "120",
        ]
    }

    static func dicomConvertCleanCommand(python: URL, dicomDir: URL, outputDir: URL, seriesNumber: Int?, seriesKey: String, paths: AppPaths) -> [String] {
        var command = [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "dicom-normalizer-convert-clean",
            "--dicom-dir",
            dicomDir.path,
            "--output",
            outputDir.path,
            "--binary",
            paths.normalizer.path,
        ]
        if let seriesNumber {
            command.append("--series-number")
            command.append(String(seriesNumber))
        } else {
            command.append("--series-key")
            command.append(seriesKey)
        }
        command.append("--dcm2niix")
        command.append(paths.dcm2niix.path)
        return command
    }

    static func dicomPrepareViewerExportCommand(python: URL, dicomDir: URL, outputDir: URL, seriesNumber: Int?, seriesKey: String, groupID: String, paths: AppPaths) -> [String] {
        var command = [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "dicom-normalizer-prepare-viewer-export",
            "--dicom-dir",
            dicomDir.path,
            "--output",
            outputDir.path,
            "--group-id",
            groupID,
            "--binary",
            paths.normalizer.path,
        ]
        if let seriesNumber {
            command.append("--series-number")
            command.append(String(seriesNumber))
        } else {
            command.append("--series-key")
            command.append(seriesKey)
        }
        command.append("--dcm2niix")
        command.append(paths.dcm2niix.path)
        return command
    }

    static func summaryCommand(python: URL, caseDir: URL) -> [String] {
        [python.path, "-m", "totalsegmentator_wrapper_mac", "summary", "--case", caseDir.path, "--format", "text"]
    }

    static func surfacePreviewCommand(python: URL, caseDir: URL) -> [String] {
        [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "surface-preview",
            "--case",
            caseDir.path,
        ]
    }

    static func slicerExportCommand(python: URL, caseDir: URL, source: URL?) -> [String] {
        var command = [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "slicer-export",
            "--case",
            caseDir.path,
        ]
        if let source {
            command.append("--source")
            command.append(source.path)
        }
        return command
    }

    static func updateCheckCommand(python: URL, manifestURL: String, json: URL, currentVersion: String, allowedHosts: [String]) -> [String] {
        var command = [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "update-check",
            "--manifest-url",
            manifestURL,
            "--json",
            json.path,
            "--current-version",
            currentVersion,
        ]
        for host in allowedHosts {
            command.append("--allowed-link-host")
            command.append(host)
        }
        return command
    }
}

func readJSON(_ url: URL) -> [String: Any]? {
    guard let data = try? Data(contentsOf: url),
          let object = try? JSONSerialization.jsonObject(with: data),
          let payload = object as? [String: Any]
    else {
        return nil
    }
    return payload
}

func writeJSON(_ payload: [String: Any], to url: URL) {
    do {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url)
    } catch {
        NSLog("Failed to write JSON \(url.path): \(error)")
    }
}

func formatElapsed(_ seconds: TimeInterval) -> String {
    let total = max(0, Int(seconds))
    let minutes = total / 60
    let remaining = total % 60
    if minutes > 0 {
        return "経過時間: \(minutes)分\(String(format: "%02d", remaining))秒"
    }
    return "経過時間: \(remaining)秒"
}

func setupReasonToJapanese(_ reason: String?) -> String {
    switch reason {
    case "needs_network": return "ネットワーク接続が必要です。"
    case "mps_unavailable": return "MPS確認に失敗しました。"
    case "python312_missing": return "同梱Python 3.12が見つかりません。"
    case "python_version_unsupported": return "Python 3.12以外ではセットアップできません。"
    case "constraints_missing": return "依存固定ファイルが見つかりません。"
    case "wheel_missing": return "同梱アプリパッケージが見つかりません。"
    case "runtime_install_failed": return "依存パッケージの導入に失敗しました。"
    case "normalizer_missing": return "CT確認用部品の確認に失敗しました。"
    case "totalseg_privacy_config_failed": return "プライバシー設定に失敗しました。"
    case "weights_download_failed": return "モデルの取得に失敗しました。"
    case "dentalseg_weights_download_failed": return "DentalSegmentatorモデルの取得に失敗しました。"
    case "bundle_manifest_invalid": return "アプリ同梱manifestを読めません。"
    case "setup_exception": return "セットアップ中にエラーが発生しました。"
    case .some(let value): return "未対応のエラーです: \(value)"
    case .none: return "原因は記録されていません。"
    }
}

func setupRecoverySuggestion(_ reason: String?) -> String {
    switch reason {
    case "needs_network":
        return "ネットワーク接続を確認してから、もう一度セットアップ開始を押してください。"
    case "mps_unavailable":
        return "このMacでMPS確認に失敗しました。Apple Silicon Macか、macOS/PyTorch環境を確認してください。"
    case "python312_missing", "wheel_missing", "constraints_missing", "bundle_manifest_invalid":
        return "アプリをDMGからもう一度コピーしてから起動してください。改善しない場合はログ回収コマンドを実行してください。"
    case "runtime_install_failed", "setup_exception", "totalseg_privacy_config_failed", "weights_download_failed", "dentalseg_weights_download_failed":
        return "ネットワークを確認して再試行してください。改善しない場合はDMG内のログ回収コマンドを実行してください。"
    case "normalizer_missing":
        return "CT確認用部品が見つかりません。アプリをDMGからもう一度コピーしてください。"
    case .some:
        return "詳細ログを確認してください。必要ならDMG内のログ回収コマンドを実行してください。"
    case .none:
        return "詳細ログを確認してください。"
    }
}
