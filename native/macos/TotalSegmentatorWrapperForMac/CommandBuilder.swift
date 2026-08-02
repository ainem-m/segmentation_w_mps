import Foundation
import AppKit
import CryptoKit
import Darwin

let appSupportName = "TotalSegmentatorWrapperMac"
let appTitle = "TotalSegmentator Wrapper for Mac"
let defaultTeethMarginMM = "5.0"
let toothsegRefineMarginMM = "12"
let dentalsegExpectedMD5 = "b71cd5230168d28a4f71b078265b76be"
let toothsegExpectedMD5 = "5d8dd061cce9529943567aeba3271143"
let toothsegPairDistributionsSHA256 = "82ab04892277d36013be5ba9763ac334ea073fca7ebe8679086f1e33ed64ff29"
let toothsegSemanticMPSPatchSize = [192, 192, 192]
let iosMeshSegNetFilename = "model.tar"

enum SetupStep: String {
    case idle
    case acquireSetupLock = "acquire_setup_lock"
    case createAppSupportDirs = "create_app_support_dirs"
    case validatePython312 = "validate_python_312"
    case validateBundledWheels = "validate_bundled_wheels"
    case createVenv = "create_venv"
    case bootstrapInstall = "bootstrap_install"
    case syncBundle = "sync_bundle"
    case installBundledWheels = "install_bundled_wheels"
    case installLockedDependencies = "install_locked_dependencies"
    case installWheel = "install_wheel"
    case verifyDependencies = "verify_dependencies"
    case configureTotalsegPrivacy = "configure_totalseg_privacy"
    case downloadTotalsegWeights = "download_totalseg_weights"
    case downloadDentalsegWeights = "download_dentalseg_weights"
    case doctor
    case complete
    case setupException = "setup_exception"

    var label: String {
        switch self {
        case .idle: return "待機中"
        case .acquireSetupLock: return "セットアップ排他確認"
        case .createAppSupportDirs: return "保存先準備"
        case .validatePython312: return "Python確認"
        case .validateBundledWheels: return "同梱依存確認"
        case .createVenv: return "専用環境作成"
        case .bootstrapInstall: return "アプリ本体導入"
        case .syncBundle: return "アプリ更新反映"
        case .installBundledWheels: return "同梱依存導入"
        case .installLockedDependencies: return "固定済み依存取得"
        case .installWheel: return "依存パッケージ取得"
        case .verifyDependencies: return "依存関係確認"
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
            return "準備を始めてください。"
        case .acquireSetupLock:
            return "ほかのセットアップが実行中でないか確認しています。"
        case .createAppSupportDirs:
            return "App Support配下に専用ディレクトリを準備しています。"
        case .validatePython312:
            return "同梱Python 3.12を確認しています。"
        case .validateBundledWheels:
            return "同梱依存パッケージの完全性を確認しています。"
        case .createVenv:
            return "このアプリ専用のPython環境を作成しています。"
        case .bootstrapInstall:
            return "セットアップ管理用のアプリ本体を専用環境へ導入しています。"
        case .syncBundle:
            return "同梱アプリ更新を専用環境へ反映しています。"
        case .installBundledWheels:
            return "検証済みの同梱依存パッケージを専用環境へ導入しています。"
        case .installLockedDependencies:
            return "SHA-256で固定された依存パッケージを取得・導入しています。通信状況により数分かかることがあります。"
        case .installWheel:
            return "依存パッケージを取得中です。数分かかることがあります。"
        case .verifyDependencies:
            return "導入した依存パッケージの整合性を確認しています。"
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
    case toothSeg = "ToothSeg"

    var id: String { rawValue }

    var cliValue: String {
        switch self {
        case .totalSegmentator: return "totalsegmentator"
        case .dentalSegmentator: return "dentalsegmentator"
        case .toothSeg: return "toothseg"
        }
    }

    var description: String {
        switch self {
        case .totalSegmentator:
            return "既定のTotalSegmentator backendです。"
        case .dentalSegmentator:
            return "nnU-Net版DentalSegmentatorを使う実験的backendです。セットアップ済みのZenodoモデルをMPS指定で使います。"
        case .toothSeg:
            return "歯列ROIを自動抽出してToothSegのsemantic/instance両branchを実行し、個別歯をFDI番号付きで分ける実験的backendです。"
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
    var updateInstallStatusJSON: URL {
        support.appendingPathComponent("updates/update_install_status.json")
    }
    var updateInstallLog: URL { support.appendingPathComponent("updates/update_install.log") }
    var venvPython: URL { support.appendingPathComponent("env/bin/python") }
    var venvSitePackages: URL {
        support.appendingPathComponent("env/lib/python3.12/site-packages", isDirectory: true)
    }
    var totalsegBinary: URL { support.appendingPathComponent("env/bin/TotalSegmentator") }
    var totalsegWeightsRoot: URL {
        support.appendingPathComponent("models/totalsegmentator/weights", isDirectory: true)
    }
    var totalsegWeightsRegistry: URL {
        totalsegWeightsRoot.appendingPathComponent(".totalsegmentator-wrapper-setup-weights.json")
    }
    var dentalsegRoot: URL { support.appendingPathComponent("models/dentalsegmentator", isDirectory: true) }
    var dentalsegRaw: URL { dentalsegRoot.appendingPathComponent("nnUNet_raw", isDirectory: true) }
    var dentalsegPreprocessed: URL { dentalsegRoot.appendingPathComponent("nnUNet_preprocessed", isDirectory: true) }
    var dentalsegResults: URL { dentalsegRoot.appendingPathComponent("nnUNet_results", isDirectory: true) }
    var dentalsegModelMetadata: URL { dentalsegRoot.appendingPathComponent("dentalsegmentator_model.json") }
    var dentalsegStatusJSON: URL { logs.appendingPathComponent("dentalsegmentator_status.json") }
    var dentalsegPrepareResultJSON: URL { logs.appendingPathComponent("dentalsegmentator_prepare_result.json") }
    var dentalsegPrepareLog: URL { logs.appendingPathComponent("dentalsegmentator_prepare.log") }
    var toothsegRoot: URL { support.appendingPathComponent("models/toothseg", isDirectory: true) }
    var toothsegResults: URL { toothsegRoot.appendingPathComponent("nnUNet_results", isDirectory: true) }
    var toothsegStatusJSON: URL { logs.appendingPathComponent("toothseg_status.json") }
    var toothsegPrepareResultJSON: URL { logs.appendingPathComponent("toothseg_prepare_result.json") }
    var toothsegPrepareLog: URL { logs.appendingPathComponent("toothseg_prepare.log") }
    var toothsegReadyMarker: URL { toothsegResults.appendingPathComponent(".toothseg_model_ready.json") }
    var toothsegPairDistributions: URL { toothsegRoot.appendingPathComponent("fdi_pair_distrs.json") }
    var iosMeshSegNetRoot: URL { support.appendingPathComponent("models/ios-meshsegnet", isDirectory: true) }
    var iosMeshSegNetModel: URL { iosMeshSegNetRoot.appendingPathComponent(iosMeshSegNetFilename) }
    var iosMeshSegNetStatusJSON: URL { logs.appendingPathComponent("ios_meshsegnet_status.json") }
    var iosMeshSegNetRunLog: URL { logs.appendingPathComponent("ios_meshsegnet_run.log") }
    var iosMeshTGNetValidationJSON: URL { logs.appendingPathComponent("ios_tgnet_validation.json") }
    var iosMeshTGNetValidationLog: URL { logs.appendingPathComponent("ios_tgnet_validation.log") }
    var toothsegSemanticModel: URL {
        toothsegResults
            .appendingPathComponent("Dataset121_ToothFairy2_Teeth", isDirectory: true)
            .appendingPathComponent("nnUNetTrainer_onlyMirror01_DASegOrd0__nnUNetPlans__3d_fullres_resample_torch_256_bs8_ctnorm", isDirectory: true)
            .appendingPathComponent("fold_5/checkpoint_final.pth")
    }
    var toothsegInstanceModel: URL {
        toothsegResults
            .appendingPathComponent("Dataset123_ToothFairy2fixed_teeth_spacing02_brd3px", isDirectory: true)
            .appendingPathComponent("nnUNetTrainer__nnUNetPlans__3d_fullres_resample_torch_192_bs8_ctnorm", isDirectory: true)
            .appendingPathComponent("fold_5/checkpoint_final.pth")
    }
    var runResultJSON: URL { logs.appendingPathComponent("latest_run_result.json") }
    var appRunLog: URL { logs.appendingPathComponent("app_run.log") }
    var dentalsegInstalledModel: URL {
        dentalsegResults
            .appendingPathComponent("Dataset112_DentalSegmentator_v100", isDirectory: true)
            .appendingPathComponent("nnUNetTrainer__nnUNetPlans__3d_fullres", isDirectory: true)
    }
    var dentalsegReadyMarker: URL {
        dentalsegResults
            .appendingPathComponent("Dataset112_DentalSegmentator_v100", isDirectory: true)
            .appendingPathComponent(".dentalsegmentator_model_ready.json")
    }
    var manifest: URL { resources.appendingPathComponent("setup_manifest.json") }
    var constraints: URL { resources.appendingPathComponent("constraints/macos-arm64-py312.txt") }
    var normalizer: URL { resources.appendingPathComponent("bin/totalsegmentator-wrapper-dicom-normalizer") }
    var dcm2niix: URL { resources.appendingPathComponent("bin/dcm2niix") }
    var sampleInput: URL { resources.appendingPathComponent("sample1/input/owner_cbct_jawcrop_0p5mm.nii.gz") }
    var sampleViewer: URL { resources.appendingPathComponent("sample1/surface_preview/index.html") }
}

struct BundledSetupResources {
    let wheel: URL
    let constraints: URL
}

struct CommandBuilder {
    private static let wrapperWheelPrefix = "totalsegmentator_wrapper_mac-"

    // Kept for existing callers, but no longer chooses a lexicographically
    // "latest" wheel.  A bootstrap wheel is usable only when the bundle
    // manifest names the sole wrapper wheel and both setup inputs validate.
    static func latestWheel(resources: URL) -> URL? {
        bundledSetupResources(resources: resources)?.wheel
    }

    static func bundledSetupResources(resources: URL) -> BundledSetupResources? {
        let fileManager = FileManager.default
        let resourcesRoot = resources.standardizedFileURL
        guard directoryWithoutSymlink(resourcesRoot),
              let resolvedResources = resolvedDirectoryWithoutSymlink(resourcesRoot)
        else {
            return nil
        }

        let manifestURL = resourcesRoot.appendingPathComponent("setup_manifest.json")
        guard regularFileWithoutSymlink(manifestURL),
              let manifest = readJSON(manifestURL),
              let bundled = manifest["bundled"] as? [String: Any],
              let wrapperName = bundled["wheel"] as? String,
              safeWrapperWheelBasename(wrapperName),
              let expectedWheelSHA256 = manifest["wheel_sha256"] as? String,
              strictLowercaseSHA256(expectedWheelSHA256),
              let constraintsRelativePath = bundled["constraints"] as? String,
              safeBundleRelativePath(constraintsRelativePath),
              let expectedConstraintsSHA256 = manifest["constraints_sha256"] as? String,
              strictLowercaseSHA256(expectedConstraintsSHA256)
        else {
            return nil
        }

        let wheelsDirectory = resourcesRoot.appendingPathComponent("wheels", isDirectory: true)
        guard directoryWithoutSymlink(wheelsDirectory),
              let wheelEntries = try? fileManager.contentsOfDirectory(
                  at: wheelsDirectory,
                  includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey],
                  options: []
              )
        else {
            return nil
        }
        let wrapperEntries = wheelEntries.filter {
            looksLikeWrapperWheel($0.lastPathComponent)
        }
        guard wrapperEntries.count == 1,
              wrapperEntries[0].lastPathComponent == wrapperName,
              let wheel = bundledRegularFile(
                  resources: resourcesRoot,
                  resolvedResources: resolvedResources,
                  relativePath: "wheels/\(wrapperName)"
              ),
              sha256HexFile(wheel) == expectedWheelSHA256,
              let constraints = bundledRegularFile(
                  resources: resourcesRoot,
                  resolvedResources: resolvedResources,
                  relativePath: constraintsRelativePath
              ),
              sha256HexFile(constraints) == expectedConstraintsSHA256
        else {
            return nil
        }
        return BundledSetupResources(wheel: wheel, constraints: constraints)
    }

    private static func bundledRegularFile(
        resources: URL,
        resolvedResources: URL,
        relativePath: String
    ) -> URL? {
        guard let components = bundleRelativePathComponents(relativePath) else {
            return nil
        }
        var candidate = resources
        for (index, component) in components.enumerated() {
            candidate = candidate.appendingPathComponent(component)
            if index < components.count - 1, !directoryWithoutSymlink(candidate) {
                return nil
            }
        }
        guard regularFileWithoutSymlink(candidate) else {
            return nil
        }
        let resolved = candidate.resolvingSymlinksInPath().standardizedFileURL
        guard isWithin(resolved, root: resolvedResources) else {
            return nil
        }
        return candidate
    }

    private static func resolvedDirectoryWithoutSymlink(_ url: URL) -> URL? {
        let resolved = url.resolvingSymlinksInPath().standardizedFileURL
        return directoryWithoutSymlink(resolved) ? resolved : nil
    }

    private static func directoryWithoutSymlink(_ url: URL) -> Bool {
        guard let values = try? url.resourceValues(
            forKeys: [.isDirectoryKey, .isSymbolicLinkKey]
        ) else {
            return false
        }
        return values.isDirectory == true && values.isSymbolicLink != true
    }

    private static func regularFileWithoutSymlink(_ url: URL) -> Bool {
        guard let values = try? url.resourceValues(
            forKeys: [.isRegularFileKey, .isSymbolicLinkKey]
        ) else {
            return false
        }
        return values.isRegularFile == true && values.isSymbolicLink != true
    }

    private static func safeWrapperWheelBasename(_ value: String) -> Bool {
        guard let components = bundleRelativePathComponents(value), components.count == 1 else {
            return false
        }
        return looksLikeWrapperWheel(value)
    }

    private static func looksLikeWrapperWheel(_ value: String) -> Bool {
        value.lowercased().hasPrefix(wrapperWheelPrefix)
            && value.lowercased().hasSuffix(".whl")
    }

    private static func safeBundleRelativePath(_ value: String) -> Bool {
        bundleRelativePathComponents(value) != nil
    }

    private static func bundleRelativePathComponents(_ value: String) -> [String]? {
        guard !value.isEmpty,
              !value.hasPrefix("/"),
              !value.contains("\\"),
              !value.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 })
        else {
            return nil
        }
        let components = value.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        guard !components.isEmpty,
              components.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." })
        else {
            return nil
        }
        return components
    }

    private static func strictLowercaseSHA256(_ value: String) -> Bool {
        value.unicodeScalars.count == 64
            && value.unicodeScalars.allSatisfy {
                CharacterSet(charactersIn: "0123456789abcdef").contains($0)
            }
    }

    private static func isWithin(_ child: URL, root: URL) -> Bool {
        let normalizedChild = child.standardizedFileURL.path
        let normalizedRoot = root.standardizedFileURL.path
        return normalizedChild == normalizedRoot
            || normalizedChild.hasPrefix(normalizedRoot.hasSuffix("/") ? normalizedRoot : normalizedRoot + "/")
    }

    private static func sha256HexFile(_ url: URL) -> String? {
        guard let handle = try? FileHandle(forReadingFrom: url) else {
            return nil
        }
        defer { try? handle.close() }
        var digest = SHA256()
        do {
            while true {
                let block = try handle.read(upToCount: 1024 * 1024) ?? Data()
                if block.isEmpty { break }
                digest.update(data: block)
            }
            return digest.finalize().map { String(format: "%02x", $0) }.joined()
        } catch {
            return nil
        }
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

    static func launchEnvironment(
        paths: AppPaths,
        baseEnvironment: [String: String] = ProcessInfo.processInfo.environment
    ) -> [String: String] {
        var env = baseEnvironment
        for key in Array(env.keys) where key.uppercased().hasPrefix("PIP_") {
            env.removeValue(forKey: key)
        }
        for key in [
            "PYTHONPATH",
            "PYTHONHOME",
            "PYTHONUSERBASE",
            "PYTHONSTARTUP",
            "PYTHONSAFEPATH",
            "VIRTUAL_ENV",
        ] {
            env.removeValue(forKey: key)
        }
        // This is deliberately explicit: the app's MPS path must never gain
        // a host-configured CPU fallback while a setup or run is in progress.
        env.removeValue(forKey: "PYTORCH_ENABLE_MPS_FALLBACK")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONNOUSERSITE"] = "1"
        env["PIP_CONFIG_FILE"] = "/dev/null"
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
        env["TOTALSEG_WEIGHTS_PATH"] = paths.totalsegWeightsRoot.path
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
        [python312.path, "-I", "-m", "venv", paths.support.appendingPathComponent("env", isDirectory: true).path]
    }

    static func bootstrapInstallCommand(python: URL, wheel: URL) -> [String] {
        [python.path, "-I", "-m", "pip", "--isolated", "install", "--force-reinstall", "--no-deps", wheel.path]
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
            "-I",
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
            "--skip-dentalseg-model",
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
        runAttemptID: String,
        paths: AppPaths
    ) -> [String] {
        _ = device
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
            "mps",
            "--execution-profile",
            "macos-app",
            "--require-mps",
            "--run-attempt-id",
            runAttemptID,
            "--result-json",
            paths.runResultJSON.path,
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
        } else if backend == .toothSeg {
            command.append("--toothseg-nnunet-results")
            command.append(paths.toothsegResults.path)
            command.append("--teeth-crop-margin-mm")
            command.append(defaultTeethMarginMM)
            command.append("--teeth-robust-craniofacial-preflight")
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

    static func iosMeshSegNetPrepareCommand(
        python: URL,
        paths: AppPaths
    ) -> [String] {
        [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac.ios_meshsegnet_setup",
            "prepare",
            "--model-root",
            paths.iosMeshSegNetRoot.path,
            "--json",
            paths.iosMeshSegNetStatusJSON.path,
            "--progress-log",
            paths.iosMeshSegNetRunLog.path,
        ]
    }

    static func iosMeshSegNetRunCommand(
        python: URL,
        input: URL,
        output: URL,
        model: URL,
        isCustomModel: Bool,
        jaw: String
    ) -> [String] {
        var command = [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac.ios_model_dispatch",
            "--input",
            input.path,
            "--model",
            model.path,
            "--output-dir",
            output.path,
            "--jaw",
            jaw,
            "--preprocess",
            "official",
            "--orientation",
            "rotate_y_180",
            "--device",
            "mps",
        ]
        if isCustomModel {
            command.append("--allow-custom-model")
        }
        return command
    }

    static func iosMeshTGNetValidateCommand(
        python: URL,
        model: URL,
        resultJSON: URL
    ) -> [String] {
        [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac.ios_tgnet_validate",
            "--model",
            model.path,
            "--json",
            resultJSON.path,
        ]
    }

    static func toothSegRefineCommand(
        python: URL,
        input: URL,
        output: URL,
        craniofacialCase: URL,
        runAttemptID: String,
        paths: AppPaths
    ) -> [String] {
        let command = [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "run",
            "--input",
            input.path,
            "--output",
            output.path,
            "--backend",
            SegmentationBackend.toothSeg.cliValue,
            "--task",
            RunMode.individualTeeth.task,
            "--device",
            "mps",
            "--execution-profile",
            "macos-app",
            "--require-mps",
            "--run-attempt-id",
            runAttemptID,
            "--result-json",
            craniofacialCase
                .appendingPathComponent("logs/toothseg_refine/result.json")
                .path,
            "--totalseg-bin",
            paths.totalsegBinary.path,
            "--toothseg-refine",
            "--toothseg-nnunet-results",
            paths.toothsegResults.path,
            "--teeth-crop-margin-mm",
            toothsegRefineMarginMM,
            "--teeth-craniofacial-case",
            craniofacialCase.path,
            "--no-copy-input",
        ]
        return command
    }

    static func dentalsegStatusCommand(python: URL, paths: AppPaths) -> [String] {
        [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "dentalseg-status",
            "--model-root",
            paths.dentalsegRoot.path,
            "--json",
            paths.dentalsegStatusJSON.path,
        ]
    }

    static func dentalsegPrepareCommand(python: URL, paths: AppPaths) -> [String] {
        [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "dentalseg-prepare",
            "--model-root",
            paths.dentalsegRoot.path,
            "--json",
            paths.dentalsegPrepareResultJSON.path,
            "--progress-log",
            paths.dentalsegPrepareLog.path,
        ]
    }

    static func toothsegStatusCommand(python: URL, paths: AppPaths) -> [String] {
        [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "toothseg-status",
            "--model-root",
            paths.toothsegRoot.path,
            "--json",
            paths.toothsegStatusJSON.path,
        ]
    }

    static func toothsegPrepareCommand(python: URL, paths: AppPaths) -> [String] {
        [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "toothseg-prepare",
            "--model-root",
            paths.toothsegRoot.path,
            "--json",
            paths.toothsegPrepareResultJSON.path,
            "--progress-log",
            paths.toothsegPrepareLog.path,
        ]
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
        if !seriesKey.isEmpty {
            command.append("--series-key")
            command.append(seriesKey)
        } else if let seriesNumber {
            command.append("--series-number")
            command.append(String(seriesNumber))
        }
        command.append("--dcm2niix")
        command.append(paths.dcm2niix.path)
        return command
    }

    static func niftiPreviewCommand(
        python: URL,
        input: URL,
        outputDir: URL,
        outputJSON: URL
    ) -> [String] {
        [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "nifti-preview",
            "--input",
            input.path,
            "--output-dir",
            outputDir.path,
            "--output-json",
            outputJSON.path,
        ]
    }

    static func dicomPrepareRescueCommand(
        python: URL,
        dicomDir: URL,
        outputDir: URL,
        seriesNumber: Int?,
        seriesKey: String,
        spacing: RescueSpacing,
        paths: AppPaths
    ) -> [String] {
        var command = [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "dicom-normalizer-prepare-rescue",
            "--dicom-dir",
            dicomDir.path,
            "--output",
            outputDir.path,
            "--patched-spacing",
            spacing.commandValue,
            "--binary",
            paths.normalizer.path,
        ]
        if !seriesKey.isEmpty {
            command.append("--series-key")
            command.append(seriesKey)
        } else if let seriesNumber {
            command.append("--series-number")
            command.append(String(seriesNumber))
        }
        command.append("--dcm2niix")
        command.append(paths.dcm2niix.path)
        return command
    }

    static func dicomExportRescueStackCommand(
        dicomDir: URL,
        outputDir: URL,
        seriesNumber: Int?,
        seriesKey: String,
        paths: AppPaths
    ) -> [String] {
        var command = [
            paths.normalizer.path,
            "export-rescue-stack",
            "--dicom-dir",
            dicomDir.path,
            "--output",
            outputDir.path,
        ]
        if !seriesKey.isEmpty {
            command.append("--series-key")
            command.append(seriesKey)
        } else if let seriesNumber {
            command.append("--series-number")
            command.append(String(seriesNumber))
        }
        return command
    }

    // Phase-2 rescue hooks. These commands operate only on the decoded rescue
    // volume and safe geometry metadata. Preview never starts inference; finalize
    // requires the token bound to the source hash, spacing and transform.
    static func dicomRescueEstimateCommand(
        python: URL,
        decodedVolume: URL,
        sourceManifestSHA256: String,
        spacingHints: String,
        evidenceJSON: URL,
        axialSliceStepMM: Double?,
        coronalCount: Int?,
        coronalSliceStepMM: Double?,
        sagittalCount: Int?,
        sagittalSliceStepMM: Double?,
        coronalReference: URL?,
        sagittalReference: URL?,
        outputJSON: URL
    ) -> [String] {
        var command = [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "dicom-rescue-estimate",
            "--volume",
            decodedVolume.path,
            "--source-manifest-sha256",
            sourceManifestSHA256,
            "--spacing-hints",
            spacingHints,
            "--evidence",
            evidenceJSON.path,
        ]
        if let axialSliceStepMM {
            command += ["--axial-slice-step-mm", String(axialSliceStepMM)]
        }
        if let coronalCount {
            command += ["--coronal-count", String(coronalCount)]
        }
        if let coronalSliceStepMM {
            command += ["--coronal-slice-step-mm", String(coronalSliceStepMM)]
        }
        if let sagittalCount {
            command += ["--sagittal-count", String(sagittalCount)]
        }
        if let sagittalSliceStepMM {
            command += ["--sagittal-slice-step-mm", String(sagittalSliceStepMM)]
        }
        if let coronalReference {
            command += ["--coronal-reference", coronalReference.path]
        }
        if let sagittalReference {
            command += ["--sagittal-reference", sagittalReference.path]
        }
        command += ["--output", outputJSON.path]
        return command
    }

    static func dicomRescuePreviewCommand(
        python: URL,
        decodedVolume: URL,
        geometryJSON: URL,
        outputVolume: URL,
        outputJSON: URL
    ) -> [String] {
        [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "dicom-rescue-preview",
            "--volume",
            decodedVolume.path,
            "--geometry",
            geometryJSON.path,
            "--output-volume",
            outputVolume.path,
            "--output",
            outputJSON.path,
        ]
    }

    static func dicomRescueFinalizeCommand(
        python: URL,
        decodedVolume: URL,
        geometryJSON: URL,
        confirmationToken: String,
        outputNifti: URL,
        outputJSON: URL
    ) -> [String] {
        [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "dicom-rescue-finalize",
            "--volume",
            decodedVolume.path,
            "--geometry",
            geometryJSON.path,
            "--confirmation-token",
            confirmationToken,
            "--output-nifti",
            outputNifti.path,
            "--output",
            outputJSON.path,
        ]
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
        if !seriesKey.isEmpty {
            command.append("--series-key")
            command.append(seriesKey)
        } else if let seriesNumber {
            command.append("--series-number")
            command.append(String(seriesNumber))
        }
        command.append("--dcm2niix")
        command.append(paths.dcm2niix.path)
        return command
    }

    static func summaryCommand(python: URL, caseDir: URL) -> [String] {
        [python.path, "-m", "totalsegmentator_wrapper_mac", "summary", "--case", caseDir.path, "--format", "text"]
    }

    static func surfacePreviewCommand(
        python: URL,
        caseDir: URL,
        sourceInput: URL? = nil,
        outputDir: URL? = nil,
        smoothSurfaces: Bool
    ) -> [String] {
        var command = [
            python.path,
            "-m",
            "totalsegmentator_wrapper_mac",
            "surface-preview",
            "--case",
            caseDir.path,
        ]
        if let sourceInput {
            command.append("--input")
            command.append(sourceInput.path)
        }
        if let outputDir {
            command.append("--output")
            command.append(outputDir.path)
        }
        command.append("--smooth-preset")
        command.append(smoothSurfaces ? "slicer_like" : "none")
        command.append("--defer-stl")
        return command
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
        try data.write(to: url, options: .atomic)
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

func currentAppVersion() -> String {
    let paths = AppPaths.current()
    let manifest = readJSON(paths.manifest) ?? [:]
    return (manifest["app_version"] as? String) ?? (manifest["version"] as? String) ?? "0.4.1"
}

func setupReasonToJapanese(_ reason: String?) -> String {
    switch reason {
    case "needs_network": return "ネットワーク接続が必要です。"
    case "mps_unavailable": return "MPS確認に失敗しました。"
    case "python312_missing": return "同梱Python 3.12が見つかりません。"
    case "python_version_unsupported": return "Python 3.12以外ではセットアップできません。"
    case "constraints_missing": return "依存固定ファイルが見つかりません。"
    case "wheel_missing": return "同梱アプリパッケージが見つかりません。"
    case "bundled_wheel_invalid": return "同梱依存パッケージの完全性を確認できません。"
    case "bundled_wheel_install_failed": return "同梱依存パッケージの導入に失敗しました。"
    case "runtime_install_failed": return "依存パッケージの導入に失敗しました。"
    case "dependency_build_failed": return "依存パッケージをこのMac上でビルドできませんでした。"
    case "dependency_resolution_failed": return "依存パッケージのバージョンを解決できませんでした。"
    case "dependency_distribution_unavailable": return "このMacに対応する依存パッケージが見つかりませんでした。"
    case "dependency_network_failed": return "依存パッケージの取得中に通信エラーが発生しました。"
    case "dependency_consistency_failed": return "導入した依存パッケージの整合性を確認できませんでした。"
    case "dependency_set_id_changed", "constraints_sha256_changed", "requirements_lock_sha256_changed", "dependency_lock_metadata_sha256_changed", "fpsample_wheel_sha256_changed", "acvl_utils_wheel_sha256_changed": return "アプリの依存構成が更新されました。"
    case "insufficient_disk_space": return "セットアップに必要な空き容量が不足しています。"
    case "setup_busy": return "別のセットアップが実行中です。"
    case "setup_lock_failed": return "セットアップの排他制御を開始できませんでした。"
    case "weights_setup_busy": return "別のモデル準備処理が実行中です。"
    case "app_running_from_disk_image": return "DMGや外部ボリューム内からはセットアップできません。"
    case "venv_missing": return "専用Python環境が見つかりません。"
    case "venv_python_changed": return "専用Python環境が以前のアプリを参照しています。"
    case "setup_weights_missing_or_invalid": return "セットアップ済みモデルが見つからないか、完全性を確認できません。"
    case "setup_weights_manifest_sha256_changed": return "アプリのモデル定義が更新されました。"
    case "installed_package_missing_or_invalid": return "専用Python環境のアプリ本体を確認できません。"
    case "installed_bundled_dependency_missing_or_invalid": return "専用Python環境の同梱依存パッケージを確認できません。"
    case "normalizer_missing": return "CT確認用部品の確認に失敗しました。"
    case "totalseg_privacy_config_failed": return "プライバシー設定に失敗しました。"
    case "weights_download_failed": return "モデルの取得に失敗しました。"
    case "weights_integrity_failed": return "取得したモデルが公式配布物として確認できませんでした。"
    case "weights_manifest_incompatible": return "モデル定義とTotalSegmentatorのバージョンが一致しません。"
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
    case "bundled_wheel_invalid":
        return "DMGをもう一度ダウンロードしてアプリをコピーし直してください。改善しない場合はエラー報告フォームへ診断情報を貼り付けてください。"
    case "python312_missing", "wheel_missing", "constraints_missing", "bundle_manifest_invalid":
        return "アプリをDMGからもう一度コピーしてから起動してください。改善しない場合はログ回収コマンドを実行してください。"
    case "dependency_network_failed":
        return "ネットワーク接続、VPN、プロキシ設定を確認してから再試行してください。"
    case "insufficient_disk_space":
        return "Macの空き容量を増やしてから、もう一度セットアップしてください。"
    case "setup_busy", "weights_setup_busy":
        return "実行中のセットアップが完了してから、もう一度お試しください。"
    case "setup_lock_failed":
        return "アプリの保存先を確認して再試行してください。改善しない場合はエラー報告フォームへ診断情報を貼り付けてください。"
    case "app_running_from_disk_image":
        return "DMGや外部ボリュームからアプリをApplicationsまたはホーム内のApplicationsへコピーし、コピー先から開き直してください。"
    case "venv_missing", "venv_python_changed", "setup_weights_missing_or_invalid", "setup_weights_manifest_sha256_changed", "installed_package_missing_or_invalid", "installed_bundled_dependency_missing_or_invalid", "dependency_set_id_changed", "constraints_sha256_changed", "requirements_lock_sha256_changed", "dependency_lock_metadata_sha256_changed", "fpsample_wheel_sha256_changed", "acvl_utils_wheel_sha256_changed":
        return "セットアップをもう一度実行してください。中断したモデル取得は可能な範囲で再開されます。"
    case "dependency_build_failed", "dependency_resolution_failed", "dependency_distribution_unavailable", "dependency_consistency_failed":
        return "アプリ側の依存パッケージ構成に問題がある可能性があります。エラー報告フォームへ診断情報を貼り付けてください。"
    case "weights_integrity_failed":
        return "再試行しても同じ場合は、配布物が更新された可能性があります。エラー報告フォームへ診断情報を貼り付けてください。"
    case "weights_manifest_incompatible":
        return "アプリと同梱依存関係の組み合わせに問題があります。エラー報告フォームへ診断情報を貼り付けてください。"
    case "bundled_wheel_install_failed":
        return "セットアップをもう一度実行してください。改善しない場合はエラー報告フォームへ診断情報を貼り付けてください。"
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
