import Foundation
import AppKit
import Combine
import Dispatch
import CryptoKit

let LOG_TAIL_BYTES = 64 * 1024

enum AppScreen {
    case setup
    case start
    case sample
    case ownData
    case running
    case ctPreview
    case result
}

enum ResultKind {
    case none
    case inference
    case dicomAudit
}

enum InputSource: Equatable {
    case none
    case sample
    case nifti
    case dicomFolder
}

struct CleanDicomSeriesCandidate: Identifiable, Equatable {
    let seriesKey: String
    let seriesNumber: Int?
    let description: String
    let fileCount: Int

    var id: String { seriesKey }

    var displayTitle: String {
        let number = seriesNumber.map { String($0) } ?? "番号なし"
        return "撮影 \(number): \(description.isEmpty ? "名称なし" : description)"
    }

    var displayDetail: String {
        "\(fileCount)枚"
    }
}

struct ViewerExportCandidate: Identifiable, Equatable {
    let seriesKey: String
    let seriesNumber: Int?
    let groupID: String
    let planeLabel: String
    let fileCount: Int
    let rows: Int
    let columns: Int
    let rowSpacing: Double
    let columnSpacing: Double
    let sliceSpacing: Double
    let aiEligibility: String

    var id: String { "\(seriesKey)#\(groupID)" }

    var displayTitle: String {
        let plane = japanesePlaneLabel(planeLabel)
        let number = seriesNumber.map { String($0) } ?? "番号なし"
        return "\(plane)（撮影 \(number) / \(groupID)）"
    }

    var displayDetail: String {
        "\(fileCount)枚 / \(rows)×\(columns) / \(formatSpacing(rowSpacing))×\(formatSpacing(columnSpacing))×\(formatSpacing(sliceSpacing)) mm"
    }

    var sliceToInPlaneRatio: Double {
        let inPlane = min(rowSpacing, columnSpacing)
        guard inPlane > 0, sliceSpacing > 0 else {
            return 0
        }
        return sliceSpacing / inPlane
    }

    var hasSparseSliceDirection: Bool {
        sliceToInPlaneRatio >= 2.0
    }

    var sparseSliceWarningText: String {
        guard hasSparseSliceDirection else {
            return ""
        }
        return "slice方向は面内より粗い場合があります。3D結果が階段状に見えることがあります。結果は非診断previewとして確認してください。"
    }
}

struct CTPreviewSlice: Identifiable, Equatable {
    let plane: String
    let label: String
    let url: URL
    let width: Int
    let height: Int
    let minValue: Double
    let maxValue: Double
    let uniformOrEmpty: Bool

    var id: String { plane }

    var detailText: String {
        "\(width)×\(height) / min \(formatNumber(minValue)) / max \(formatNumber(maxValue))"
    }
}

final class AppState: ObservableObject {
    let paths: AppPaths
    private let runner = ProcessRunner()
    private var logTimer: Timer?
    private var startedAt: Date?
    private var lastLogText = ""
    private var activeLogURL: URL?
    private var resultLogURL: URL?
    private var lastDicomDirURL: URL?
    private var lastRunProgressAt: Date?
    private var lastRunProgressSignature = ""

    @Published var screen: AppScreen = .setup
    @Published var selectedStep = 0
    @Published var setupStep: SetupStep = .idle
    @Published var setupHint = SetupStep.idle.hint
    @Published var setupRunning = false
    @Published var setupElapsed = "経過時間: 0秒"
    @Published var setupMessage = "管理者権限は不要です。App Support配下にだけ書き込みます。"
    @Published var setupError = ""

    @Published var logText = ""
    @Published var logInfoText = "詳細ログは最後の一部だけ表示します。全文はログファイルで確認できます。"
    @Published var showLog = false

    @Published var inputURL: URL?
    @Published var inputSource: InputSource = .none
    @Published var outputURL: URL?
    @Published var outputRootURL: URL?
    @Published var runMode: RunMode = .archPreview
    @Published var device = "mps"
    @Published var statusText = "待機中"
    @Published var progressText = "まだ実行していません。"
    @Published var runHeartbeatText = ""
    @Published var runProgressFraction: Double?
    @Published var runElapsed = "経過時間: 0秒"
    @Published var isRunning = false
    @Published var stopRequested = false
    @Published var surfacePreviewFailed = false
    @Published var resultMessage = ""
    @Published var summaryText = ""
    @Published var dicomSummaryText = ""
    @Published var dicomCleanCandidates: [CleanDicomSeriesCandidate] = []
    @Published var selectedDicomSeriesID: String?
    @Published var dicomViewerExportCandidates: [ViewerExportCandidate] = []
    @Published var selectedViewerExportCandidateID: String?
    @Published var pendingPreparedInputURL: URL?
    @Published var pendingViewerExportMetadataURL: URL?
    @Published var pendingViewerExportCandidate: ViewerExportCandidate?
    @Published var ctPreviewSlices: [CTPreviewSlice] = []
    @Published var ctPreviewWarning = ""
    @Published var resultKind: ResultKind = .none
    @Published var updateMessage = ""
    @Published var pendingDownloadURL: URL?
    @Published var pendingUpdateVersion = ""
    @Published var pendingUpdateSHA256 = ""
    @Published var showingUpdateConfirmation = false
    @Published var updateCheckRunning = false
    @Published var updateInstallRunning = false

    var retryButtonTitle: String {
        resultKind == .dicomAudit ? "もう一度確認" : "もう一度実行"
    }

    var canRetryFromResult: Bool {
        guard !isRunning else { return false }
        if resultKind == .dicomAudit {
            return lastDicomDirURL != nil
        }
        return inputURL != nil && (inputSource == .sample || inputSource == .nifti)
    }

    var canStartSampleRun: Bool {
        !isRunning && inputSource == .sample && inputURL != nil
    }

    var isSampleInputSelected: Bool {
        inputSource == .sample && inputURL != nil
    }

    var sampleInputButtonTitle: String {
        isSampleInputSelected ? "CTを選ぶ（Sample選択済み）" : "CTを選ぶ（Sample）"
    }

    var sampleInputButtonIcon: String {
        isSampleInputSelected ? "checkmark.circle" : "folder.badge.plus"
    }

    var ownDataPrimaryButtonTitle: String {
        "3Dプレビューを作成"
    }

    var canStartOwnDataRun: Bool {
        guard !isRunning else { return false }
        return inputSource == .nifti
    }

    var canRegenerateSurfacePreview: Bool {
        !isRunning && resultKind == .inference && outputURL != nil
    }

    var canUseSelectedDicomSeries: Bool {
        !isRunning && resultKind == .dicomAudit && lastDicomDirURL != nil && selectedDicomSeriesID != nil
    }

    var canUseSelectedViewerExportCandidate: Bool {
        !isRunning && resultKind == .dicomAudit && lastDicomDirURL != nil && selectedViewerExportCandidateID != nil
    }

    var canAcceptCTPreview: Bool {
        guard !isRunning, pendingPreparedInputURL != nil else {
            return false
        }
        let expectedPlanes = Set(["axial", "coronal", "sagittal"])
        let availablePlanes = Set(ctPreviewSlices.filter { FileManager.default.fileExists(atPath: $0.url.path) }.map(\.plane))
        if !expectedPlanes.isSubset(of: availablePlanes) {
            return false
        }
        return !ctPreviewSlices.allSatisfy(\.uniformOrEmpty)
    }

    var setupRecoveryText: String {
        guard !setupError.isEmpty else { return "" }
        let reason = readJSON(paths.stateJSON)?["reason"] as? String
        return setupRecoverySuggestion(reason)
    }

    var currentLogURL: URL {
        if let activeLogURL {
            return activeLogURL
        }
        if let resultLogURL {
            return resultLogURL
        }
        if resultKind == .inference, let outputURL {
            return outputURL.appendingPathComponent("logs/run.log")
        }
        return paths.launcherLog
    }

    var currentLogPathText: String {
        currentLogURL.path
    }

    var currentLogExists: Bool {
        FileManager.default.fileExists(atPath: currentLogURL.path)
    }

    init(paths: AppPaths = .current()) {
        self.paths = paths
        createRuntimeDirectories(paths: paths)
        outputRootURL = paths.runs
        if FileManager.default.fileExists(atPath: paths.sampleInput.path) {
            inputURL = paths.sampleInput
            inputSource = .sample
        }
        refreshLaunchState()
    }

    deinit {
        logTimer?.invalidate()
    }

    func refreshLaunchState() {
        let status = SetupCoordinator.setupStatus(paths: paths)
        if status.action == "current" || status.action == "mark_current" {
            if status.action == "mark_current" {
                markBundleCurrent(paths: paths, reason: status.reason)
            }
            screen = .start
            selectedStep = 0
            setupError = ""
            setupMessage = "セットアップ済みです。初回3Dプレビュー作成時にモデル取得で追加の通信と時間がかかる場合があります。"
        } else if status.action == "resync_wheel" {
            screen = .setup
            setupMessage = "同梱アプリ更新を専用環境へ反映します。"
            setupError = ""
        } else {
            screen = .setup
            setupMessage = "初回セットアップが必要です。セットアップ開始を押すまで通信しません。"
            if let reason = status.state?["reason"] as? String, !reason.isEmpty {
                setupError = setupReasonToJapanese(reason)
            }
        }
        refreshLog()
    }

    func startSetup() {
        setupRunning = true
        setupError = ""
        setupStep = .validatePython312
        setupHint = setupStep.hint
        setupElapsed = formatElapsed(0)
        startedAt = Date()
        startLogTimer()

        let paths = self.paths
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc: Int32
            let status = SetupCoordinator.setupStatus(paths: paths)
            if status.action == "resync_wheel" {
                DispatchQueue.main.async {
                    self?.setupStep = .syncBundle
                    self?.setupHint = SetupStep.syncBundle.hint
                    self?.setupMessage = "同梱アプリ更新を反映しています。"
                }
                rc = SetupCoordinator.resyncWheel(paths: paths)
            } else {
                rc = SetupCoordinator.runSetup(paths: paths) { step, message in
                    DispatchQueue.main.async { [weak self] in
                        self?.setupStep = step
                        self?.setupHint = step.hint
                        self?.setupMessage = message
                    }
                }
            }
            DispatchQueue.main.async {
                self?.setupRunning = false
                self?.refreshLog()
                if rc == 0 {
                    self?.setupStep = .complete
                    self?.setupHint = SetupStep.complete.hint
                    self?.setupMessage = "起動準備が完了しました。初回3Dプレビュー作成時にモデル取得で追加の通信と時間がかかる場合があります。"
                    self?.setupError = ""
                    self?.screen = .start
                    self?.selectedStep = 0
                } else {
                    let reason = readJSON(paths.stateJSON)?["reason"] as? String
                    self?.setupStep = .setupException
                    self?.setupHint = SetupStep.setupException.hint
                    self?.setupError = setupReasonToJapanese(reason)
                }
            }
        }
    }

    func openSampleViewer() {
        guard FileManager.default.fileExists(atPath: paths.sampleViewer.path) else {
            resultMessage = "Sample 1の3Dプレビューが見つかりません。"
            setupMessage = "Sample 1の3Dプレビューが見つかりません。"
            return
        }
        openURLInWorkspace(paths.sampleViewer)
    }

    func useSampleInput() {
        inputURL = paths.sampleInput
        inputSource = .sample
        outputURL = nil
        runMode = .archPreview
        statusText = "Sample 1を入力に設定しました。"
        progressText = "Sample 1で3Dプレビューを作成できます。自分のCTには触れません。"
        runHeartbeatText = ""
        runProgressFraction = nil
        screen = .sample
        selectedStep = 1
    }

    func chooseCTInput() {
        let panel = NSOpenPanel()
        panel.title = "CTを選択"
        panel.canChooseFiles = true
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            if isDirectory(url) {
                inputURL = url
                inputSource = .dicomFolder
                runDicomAudit(dicomDir: url)
            } else {
                prepareNiftiInput(url)
            }
        }
    }

    func chooseNifti() {
        let panel = NSOpenPanel()
        panel.title = "CTファイルを選択"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            prepareNiftiInput(url)
        }
    }

    private func prepareNiftiInput(_ url: URL) {
        inputURL = url
        inputSource = .nifti
        outputURL = nil
        resultKind = .none
        dicomSummaryText = ""
        summaryText = ""
        resultMessage = ""
        dicomCleanCandidates = []
        selectedDicomSeriesID = nil
        screen = .ownData
        selectedStep = 1
        statusText = "プレビュー作成準備完了"
        progressText = "CTを入力に設定しました。3Dプレビューを作成できます。"
    }

    func chooseOutputRoot() {
        let panel = NSOpenPanel()
        panel.title = "保存先フォルダを選択"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            outputRootURL = url
            outputURL = nil
        }
    }

    func chooseDicomFolderAndAudit() {
        let panel = NSOpenPanel()
        panel.title = "CTフォルダを選択"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let dicomDir = panel.url else {
            return
        }
        inputURL = dicomDir
        inputSource = .dicomFolder
        runDicomAudit(dicomDir: dicomDir)
    }

    func runDicomAudit(dicomDir: URL) {
        guard FileManager.default.fileExists(atPath: paths.venvPython.path) else {
            statusText = "セットアップが必要です。"
            screen = .setup
            return
        }
        let auditDir = paths.runs.appendingPathComponent("dicom_audit_\(Int(Date().timeIntervalSince1970))", isDirectory: true)
        let auditJSON = auditDir.appendingPathComponent("dicom_normalizer_audit.json")
        try? FileManager.default.createDirectory(at: auditDir, withIntermediateDirectories: true)
        inputURL = dicomDir
        inputSource = .dicomFolder
        lastDicomDirURL = dicomDir
        dicomCleanCandidates = []
        selectedDicomSeriesID = nil
        dicomViewerExportCandidates = []
        selectedViewerExportCandidateID = nil
        clearPendingCTPreview()
        logText = ""
        dicomSummaryText = ""
        summaryText = ""
        resultKind = .dicomAudit
        statusText = "CT確認中"
        progressText = "撮影データの種類を確認しています。プレビュー作成はまだ開始していません。"
        resetRunProgressTracking()
        runProgressFraction = nil
        isRunning = true
        stopRequested = false
        screen = .running
        selectedStep = 2
        activeLogURL = paths.launcherLog
        resultLogURL = nil
        runner.resetTerminationRequest()
        startRunTimer()

        let command = CommandBuilder.dicomAuditCommand(
            python: paths.venvPython,
            dicomDir: dicomDir,
            outputJSON: auditJSON,
            paths: paths
        )
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let logURL = paths.launcherLog
        let runner = self.runner
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = runner.run(command, environment: environment, logURL: logURL)
            let summary = formatDicomSummary(auditJSON: auditJSON)
            let cleanCandidates = rc == 0 ? cleanDicomSeriesCandidates(auditJSON: auditJSON) : []
            let viewerExportCandidates = rc == 0 ? viewerExportCandidates(auditJSON: auditJSON) : []
            DispatchQueue.main.async {
                let stopped = self?.stopRequested == true
                self?.isRunning = false
                self?.stopRequested = false
                self?.refreshLog()
                self?.activeLogURL = nil
                self?.resultLogURL = logURL
                self?.runHeartbeatText = ""
                self?.runProgressFraction = nil
                self?.dicomSummaryText = summary
                self?.dicomCleanCandidates = cleanCandidates
                self?.selectedDicomSeriesID = cleanCandidates.first?.id
                self?.dicomViewerExportCandidates = viewerExportCandidates
                self?.selectedViewerExportCandidateID = viewerExportCandidates.first?.id
                if stopped {
                    self?.screen = .result
                    self?.selectedStep = 3
                    self?.statusText = "停止しました"
                    self?.progressText = "撮影データの確認を停止しました。"
                    self?.resultMessage = "撮影データの確認を停止しました。入力は変更されていません。"
                } else if rc == 0 && cleanCandidates.count == 1, let candidate = cleanCandidates.first {
                    self?.startDicomCleanConversion(dicomDir: dicomDir, candidate: candidate)
                } else if rc == 0 && cleanCandidates.count > 1 {
                    self?.screen = .result
                    self?.selectedStep = 1
                    self?.statusText = "撮影を選んでください"
                    self?.progressText = "取り込む撮影を選ぶとCTを準備します。プレビュー作成はまだ開始していません。"
                    self?.resultMessage = "取り込める撮影候補が複数あります。使用する撮影を選んでください。"
                    self?.outputURL = auditDir
                } else if rc == 0 && !viewerExportCandidates.isEmpty {
                    self?.screen = .result
                    self?.selectedStep = 1
                    self?.statusText = "表示用断面画像の可能性があります"
                    self?.progressText = "CTを見るソフトから書き出された断面群を確認できます。プレビュー作成はまだ開始していません。"
                    self?.resultMessage = "CTを見るソフトから「表示用の断面画像」として書き出されたデータの可能性があります。断面群を確認して、3Dプレビューに進めるか判断します。"
                    self?.outputURL = auditDir
                } else {
                    self?.screen = .result
                    self?.selectedStep = 3
                    self?.statusText = rc == 0 ? "要確認" : "CT確認に失敗しました"
                    self?.progressText = "プレビュー作成へは進んでいません。理由と次の操作を確認してください。"
                    self?.resultMessage = rc == 0 ? dicomAutoImportUnavailableMessage() : dicomAuditFailureMessage(auditJSON: auditJSON)
                    self?.outputURL = auditDir
                }
            }
        }
    }

    func useSelectedDicomSeries() {
        guard let dicomDir = lastDicomDirURL,
              let selectedDicomSeriesID,
              let candidate = dicomCleanCandidates.first(where: { $0.id == selectedDicomSeriesID }) else {
            resultMessage = "取り込める通常CT候補が見つかりません。CT確認結果を確認してください。"
            return
        }
        startDicomCleanConversion(dicomDir: dicomDir, candidate: candidate)
    }

    func useSelectedViewerExportCandidate() {
        guard let dicomDir = lastDicomDirURL,
              let selectedViewerExportCandidateID,
              let candidate = dicomViewerExportCandidates.first(where: { $0.id == selectedViewerExportCandidateID }) else {
            resultMessage = "救済できる断面群が見つかりません。CT確認結果を確認してください。"
            return
        }
        startDicomViewerExportConversion(dicomDir: dicomDir, candidate: candidate)
    }

    func convertDicomToNiftiFromAudit() {
        useSelectedDicomSeries()
    }

    private func startDicomCleanConversion(dicomDir: URL, candidate: CleanDicomSeriesCandidate) {
        guard FileManager.default.fileExists(atPath: paths.venvPython.path) else {
            statusText = "セットアップが必要です。"
            screen = .setup
            return
        }
        let convertDir = paths.runs.appendingPathComponent("dicom_convert_\(Int(Date().timeIntervalSince1970))", isDirectory: true)
        let logURL = convertDir.appendingPathComponent("logs/dicom_convert.log")
        try? FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        outputURL = convertDir
        statusText = "CT取り込み中"
        progressText = "撮影データをプレビューに使える形に準備しています。プレビュー作成はまだ開始していません。"
        runHeartbeatText = "準備が終わると3Dプレビューを作成できる状態になります。"
        resetRunProgressTracking()
        runProgressFraction = nil
        isRunning = true
        stopRequested = false
        screen = .running
        selectedStep = 1
        activeLogURL = logURL
        resultLogURL = nil
        runner.resetTerminationRequest()
        startRunTimer()

        let command = CommandBuilder.dicomConvertCleanCommand(
            python: paths.venvPython,
            dicomDir: dicomDir,
            outputDir: convertDir,
            seriesNumber: candidate.seriesNumber,
            seriesKey: candidate.seriesKey,
            paths: paths
        )
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let runner = self.runner
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = runner.run(command, environment: environment, logURL: logURL)
            let metadataJSON = convertDir.appendingPathComponent("convert_clean_metadata.json")
            let niftiURL = rc == 0 ? convertedNiftiURL(metadataJSON: metadataJSON) : nil
            DispatchQueue.main.async {
                let stopped = self?.stopRequested == true
                self?.isRunning = false
                self?.stopRequested = false
                self?.refreshLog(from: logURL)
                self?.activeLogURL = nil
                self?.resultLogURL = logURL
                self?.runHeartbeatText = ""
                self?.runProgressFraction = nil
                if stopped {
                    self?.screen = .result
                    self?.selectedStep = 3
                    self?.statusText = "停止しました"
                    self?.progressText = "CT取り込みを停止しました。入力は変更されていません。"
                    self?.resultMessage = "CT取り込みを停止しました。必要ならもう一度CTを選び直してください。"
                    return
                }
                if let niftiURL, FileManager.default.fileExists(atPath: niftiURL.path) {
                    self?.inputURL = niftiURL
                    self?.inputSource = .nifti
                    self?.outputURL = nil
                    self?.resultKind = .none
                    self?.dicomSummaryText = ""
                    self?.summaryText = ""
                    self?.resultMessage = ""
                    self?.screen = .ownData
                    self?.selectedStep = 1
                    self?.statusText = "プレビュー作成準備完了"
                    self?.progressText = "CTを取り込みました。3Dプレビューを作成できます。"
                } else {
                    self?.screen = .result
                    self?.selectedStep = 3
                    self?.resultKind = .dicomAudit
                    self?.outputURL = convertDir
                    self?.statusText = "CT取り込みに失敗しました"
                    self?.progressText = "入力は変更されていません。詳細ログを確認してください。"
                    self?.resultMessage = "CT取り込みに失敗しました。入力は変更されていません。詳細ログを確認してください。"
                }
            }
        }
    }

    private func startDicomViewerExportConversion(dicomDir: URL, candidate: ViewerExportCandidate) {
        guard FileManager.default.fileExists(atPath: paths.venvPython.path) else {
            statusText = "セットアップが必要です。"
            screen = .setup
            return
        }
        let convertDir = paths.runs.appendingPathComponent("dicom_viewer_export_\(Int(Date().timeIntervalSince1970))", isDirectory: true)
        let logURL = convertDir.appendingPathComponent("logs/dicom_viewer_export.log")
        try? FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        outputURL = convertDir
        statusText = "救済データを作成中"
        progressText = "選択した断面群だけを使って、非診断preview用のCTデータを準備しています。プレビュー作成はまだ開始していません。"
        runHeartbeatText = "CTを見るソフトから書き出された断面画像の可能性があります。変換後にsliceを確認します。"
        resetRunProgressTracking()
        runProgressFraction = nil
        isRunning = true
        stopRequested = false
        screen = .running
        selectedStep = 1
        activeLogURL = logURL
        resultLogURL = nil
        runner.resetTerminationRequest()
        startRunTimer()

        let command = CommandBuilder.dicomPrepareViewerExportCommand(
            python: paths.venvPython,
            dicomDir: dicomDir,
            outputDir: convertDir,
            seriesNumber: candidate.seriesNumber,
            seriesKey: candidate.seriesKey,
            groupID: candidate.groupID,
            paths: paths
        )
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let runner = self.runner
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = runner.run(command, environment: environment, logURL: logURL)
            let metadataJSON = convertDir.appendingPathComponent("viewer_export_metadata.json")
            let niftiURL = rc == 0 ? convertedNiftiURL(metadataJSON: metadataJSON) : nil
            DispatchQueue.main.async {
                let stopped = self?.stopRequested == true
                self?.isRunning = false
                self?.stopRequested = false
                self?.refreshLog(from: logURL)
                self?.activeLogURL = nil
                self?.resultLogURL = logURL
                self?.runHeartbeatText = ""
                self?.runProgressFraction = nil
                if stopped {
                    self?.screen = .result
                    self?.selectedStep = 3
                    self?.statusText = "停止しました"
                    self?.progressText = "救済データ作成を停止しました。入力は変更されていません。"
                    self?.resultMessage = "救済データ作成を停止しました。必要ならもう一度CTを選び直してください。"
                    return
                }
                if let niftiURL, FileManager.default.fileExists(atPath: niftiURL.path) {
                    let slices = viewerExportPreviewSlices(metadataJSON: metadataJSON)
                    self?.pendingPreparedInputURL = niftiURL
                    self?.pendingViewerExportMetadataURL = metadataJSON
                    self?.pendingViewerExportCandidate = candidate
                    self?.ctPreviewSlices = slices
                    self?.ctPreviewWarning = makeCTPreviewWarning(slices: slices)
                    self?.resultKind = .dicomAudit
                    self?.dicomSummaryText = ""
                    self?.summaryText = ""
                    self?.resultMessage = "救済データを作成しました。プレビュー作成の前にsliceを確認してください。"
                    self?.screen = .ctPreview
                    self?.selectedStep = 1
                    self?.statusText = "CT確認プレビュー"
                    self?.progressText = "中央sliceを確認してから3Dプレビューへ進みます。"
                } else {
                    self?.screen = .result
                    self?.selectedStep = 3
                    self?.resultKind = .dicomAudit
                    self?.outputURL = convertDir
                    self?.statusText = "救済データ作成に失敗しました"
                    self?.progressText = "入力は変更されていません。詳細ログを確認してください。"
                    self?.resultMessage = "救済データ作成に失敗しました。元のCT DICOMがあればそちらを使用してください。"
                }
            }
        }
    }

    func startRun() {
        guard let inputURL else {
            statusText = "入力を選択してください。"
            return
        }
        if inputSource == .dicomFolder || isDirectory(inputURL) {
            runDicomAudit(dicomDir: inputURL)
            return
        }
        guard inputSource == .sample || inputSource == .nifti else {
            statusText = "入力を選択してください。"
            progressText = "SampleまたはCTを選んでください。"
            return
        }
        guard FileManager.default.fileExists(atPath: paths.venvPython.path) else {
            statusText = "セットアップが必要です。"
            screen = .setup
            return
        }
        let output = nextCaseOutput()
        outputURL = output
        try? FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        let logURL = output.appendingPathComponent("logs/run.log")
        logText = ""
        summaryText = ""
        dicomSummaryText = ""
        resultMessage = ""
        resultKind = .inference
        surfacePreviewFailed = false
        statusText = "3Dプレビュー作成中"
        progressText = runMode == .individualTeeth ? "歯を1本ずつ分けています。" : "歯列と顎骨をまとめて表示する結果を作成しています。"
        resetRunProgressTracking()
        runProgressFraction = nil
        isRunning = true
        stopRequested = false
        screen = .running
        selectedStep = 2
        activeLogURL = logURL
        resultLogURL = nil
        runner.resetTerminationRequest()
        startRunTimer()

        let command = CommandBuilder.runCommand(
            python: paths.venvPython,
            input: inputURL,
            output: output,
            mode: runMode,
            device: device,
            paths: paths
        )
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let runner = self.runner
        let venvPython = paths.venvPython
        let modeForRun = runMode

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = runner.run(command, environment: environment, logURL: logURL)
            var surfacePreviewRC: Int32? = nil
            if rc == 0 && self?.stopRequested != true {
                let previewLabel = modeForRun == .individualTeeth ? "個別歯" : "歯列・顎骨"
                DispatchQueue.main.async {
                    self?.statusText = "3Dプレビュー作成中"
                    self?.progressText = "ブラウザで開ける\(previewLabel)3Dプレビューを作成しています。"
                    self?.runProgressFraction = nil
                    self?.runHeartbeatText = "STLとHTML viewerを生成しています。数十秒かかることがあります。"
                }
                surfacePreviewRC = runner.run(
                    CommandBuilder.surfacePreviewCommand(python: venvPython, caseDir: output),
                    environment: environment,
                    logURL: logURL
                )
            }
            let stoppedBeforeSummary = runner.isTerminationRequested || self?.stopRequested == true
            let summary = stoppedBeforeSummary
                ? ""
                : runner.runCapturing(
                    CommandBuilder.summaryCommand(python: venvPython, caseDir: output),
                    environment: environment,
                    logURL: nil
                ).1
            DispatchQueue.main.async {
                let stopped = self?.stopRequested == true
                self?.isRunning = false
                self?.stopRequested = false
                self?.refreshLog(from: logURL)
                self?.activeLogURL = nil
                self?.resultLogURL = logURL
                self?.runHeartbeatText = ""
                if rc == 0 && !stopped {
                    self?.runProgressFraction = 1.0
                }
                self?.screen = .result
                self?.selectedStep = 3
                if stopped {
                    self?.statusText = "停止しました"
                    self?.progressText = "処理を停止しました。入力は変更されていません。"
                    self?.resultMessage = "処理を停止しました。必要ならもう一度実行できます。"
                } else if rc == 0 && surfacePreviewRC == 0 {
                    self?.statusText = "完了"
                    self?.progressText = "結果と3Dプレビューを確認できます。"
                    self?.resultMessage = "実行が完了し、3Dプレビューを作成しました。"
                } else if rc == 0 && surfacePreviewRC != nil {
                    self?.surfacePreviewFailed = true
                    self?.statusText = "完了（3Dプレビュー未作成）"
                    self?.progressText = "CT解析は完了しました。3Dプレビューだけ作り直せます。"
                    self?.resultMessage = "処理結果は保存されています。3Dプレビュー生成だけ失敗しました。"
                } else if rc == 0 {
                    self?.statusText = "完了"
                    self?.progressText = "結果を確認できます。"
                    self?.resultMessage = "実行が完了しました。"
                } else {
                    self?.statusText = "処理を完了できませんでした"
                    self?.progressText = "入力は変更されていません。もう一度実行するか、詳細ログを確認してください。"
                    self?.resultMessage = "処理を完了できませんでした。入力は変更されていません。もう一度実行するか、詳細ログを確認してください。"
                }
                self?.summaryText = summary
            }
        }
    }

    func stopRun() {
        guard isRunning else { return }
        stopRequested = true
        runner.terminate(graceSeconds: 10.0) { [weak self] in
            DispatchQueue.main.async {
                guard self?.isRunning == true, self?.stopRequested == true else { return }
                self?.statusText = "強制終了中"
                self?.progressText = "応答がないため強制終了しています。しばらくお待ちください。"
            }
        }
        statusText = "停止要求済み"
        progressText = "終了処理中です。数秒かかることがあります。"
        runHeartbeatText = "停止要求を送りました。終了処理中です。"
    }

    func acceptPreparedCTPreview() {
        guard canAcceptCTPreview, let pendingPreparedInputURL else {
            resultMessage = "slice previewを確認できないため、3Dプレビューへ進めません。断面群を選び直してください。"
            return
        }
        let acceptedInputURL = pendingPreparedInputURL
        clearPendingCTPreview()
        inputURL = acceptedInputURL
        inputSource = .nifti
        outputURL = nil
        resultKind = .none
        dicomSummaryText = ""
        summaryText = ""
        resultMessage = ""
        screen = .ownData
        selectedStep = 1
        statusText = "プレビュー作成準備完了"
        progressText = "slice確認済みのCTを入力に設定しました。3Dプレビューを作成できます。"
        runHeartbeatText = ""
        runProgressFraction = nil
    }

    func returnToViewerExportSelection() {
        guard !isRunning else { return }
        pendingPreparedInputURL = nil
        pendingViewerExportMetadataURL = nil
        pendingViewerExportCandidate = nil
        ctPreviewSlices = []
        ctPreviewWarning = ""
        screen = .result
        selectedStep = 1
        resultKind = .dicomAudit
        statusText = "断面群を選んでください"
        progressText = "別の断面群を選ぶか、元の撮影データがないか確認してください。プレビュー作成はまだ開始していません。"
        resultMessage = "救済できる可能性のある断面群を選び直してください。"
    }

    private func clearPendingCTPreview() {
        pendingPreparedInputURL = nil
        pendingViewerExportMetadataURL = nil
        pendingViewerExportCandidate = nil
        ctPreviewSlices = []
        ctPreviewWarning = ""
    }

    func goToStart() {
        guard !isRunning else { return }
        screen = .start
        selectedStep = 0
        statusText = "待機中"
        progressText = "Sampleか自分のCTを選んでください。"
        runHeartbeatText = ""
        runProgressFraction = nil
        dicomCleanCandidates = []
        selectedDicomSeriesID = nil
        dicomViewerExportCandidates = []
        selectedViewerExportCandidateID = nil
        clearPendingCTPreview()
    }

    func goToSample() {
        guard !isRunning else { return }
        if inputSource != .sample {
            inputURL = nil
            inputSource = .none
        }
        outputURL = nil
        runMode = .archPreview
        dicomCleanCandidates = []
        selectedDicomSeriesID = nil
        dicomViewerExportCandidates = []
        selectedViewerExportCandidateID = nil
        clearPendingCTPreview()
        screen = .sample
        selectedStep = 1
        statusText = inputSource == .sample ? "Sample 1を入力に設定しました。" : "Sample 1を選べます。"
        progressText = inputSource == .sample ? "Sample 1で3Dプレビューを作成できます。" : "CTを選ぶ（Sample）で、本番のCT選択と同じ流れを練習できます。"
        runHeartbeatText = ""
        runProgressFraction = nil
    }

    func goToOwnData() {
        guard !isRunning else { return }
        if inputSource == .sample {
            inputURL = nil
            inputSource = .none
        }
        outputURL = nil
        dicomCleanCandidates = []
        selectedDicomSeriesID = nil
        dicomViewerExportCandidates = []
        selectedViewerExportCandidateID = nil
        clearPendingCTPreview()
        screen = .ownData
        selectedStep = 1
        statusText = "自分のCTを選べます。"
        progressText = "CTファイルまたは撮影フォルダを選んでください。"
        runHeartbeatText = ""
        runProgressFraction = nil
    }

    func goToInput() {
        guard !isRunning else { return }
        if inputSource == .sample || inputURL.map({ sameFileURL($0, paths.sampleInput) }) == true {
            goToSample()
        } else {
            goToOwnData()
        }
        statusText = "入力を確認してください。"
        progressText = "設定を見直して再実行できます。"
        runHeartbeatText = ""
        runProgressFraction = nil
    }

    func retryRunFromResult() {
        guard !isRunning else { return }
        if resultKind == .dicomAudit {
            guard let lastDicomDirURL else {
                goToOwnData()
                return
            }
            runDicomAudit(dicomDir: lastDicomDirURL)
            return
        }
        startRun()
    }

    func openOutputFolder() {
        guard let outputURL, FileManager.default.fileExists(atPath: outputURL.path) else {
            resultMessage = "結果フォルダが見つかりません。"
            return
        }
        openURLInWorkspace(outputURL)
    }

    func openResultPreview() {
        guard let outputURL, let preview = caseSurfacePreview(outputURL) else {
            resultMessage = "3DプレビューHTMLが見つかりません。"
            return
        }
        openURLInWorkspace(preview)
    }

    func regenerateSurfacePreview() {
        guard let outputURL else {
            resultMessage = "結果フォルダが見つかりません。"
            return
        }
        guard FileManager.default.fileExists(atPath: paths.venvPython.path) else {
            statusText = "セットアップが必要です。"
            screen = .setup
            return
        }
        let logURL = outputURL.appendingPathComponent("logs/run.log")
        resultKind = .inference
        statusText = "3Dプレビュー作成中"
        progressText = "CT解析は再実行せず、3Dプレビューだけ作成しています。"
        runHeartbeatText = "STLとHTML viewerを生成しています。数十秒かかることがあります。"
        runProgressFraction = nil
        isRunning = true
        stopRequested = false
        screen = .running
        selectedStep = 2
        activeLogURL = logURL
        resultLogURL = nil
        runner.resetTerminationRequest()
        startRunTimer()

        let command = CommandBuilder.surfacePreviewCommand(python: paths.venvPython, caseDir: outputURL)
        let summaryCommand = CommandBuilder.summaryCommand(python: paths.venvPython, caseDir: outputURL)
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let runner = self.runner
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = runner.run(command, environment: environment, logURL: logURL)
            let stoppedBeforeSummary = runner.isTerminationRequested || self?.stopRequested == true
            let summary = stoppedBeforeSummary
                ? ""
                : runner.runCapturing(summaryCommand, environment: environment, logURL: nil).1
            DispatchQueue.main.async {
                let stopped = self?.stopRequested == true
                self?.isRunning = false
                self?.stopRequested = false
                self?.refreshLog(from: logURL)
                self?.activeLogURL = nil
                self?.resultLogURL = logURL
                self?.runHeartbeatText = ""
                self?.screen = .result
                self?.selectedStep = 3
                self?.summaryText = summary
                if stopped {
                    self?.statusText = "停止しました"
                    self?.progressText = "3Dプレビューの再生成を停止しました。"
                    self?.resultMessage = "3Dプレビューの再生成を停止しました。"
                } else if rc == 0 {
                    self?.surfacePreviewFailed = false
                    self?.statusText = "完了"
                    self?.progressText = "3Dプレビューを確認できます。"
                    self?.resultMessage = "3Dプレビューを再生成しました。"
                } else {
                    self?.surfacePreviewFailed = true
                    self?.statusText = "3Dプレビュー未作成"
                    self?.progressText = "3Dプレビュー生成に失敗しました。詳細ログを確認してください。"
                    self?.resultMessage = "3Dプレビュー生成に失敗しました。処理結果は保存されています。"
                }
            }
        }
    }

    func checkUpdates() {
        guard !updateCheckRunning else {
            return
        }
        pendingDownloadURL = nil
        pendingUpdateVersion = ""
        pendingUpdateSHA256 = ""
        showingUpdateConfirmation = false
        let manifest = readJSON(paths.manifest) ?? [:]
        let url = (manifest["update_manifest_url"] as? String) ?? ""
        guard !url.isEmpty else {
            updateMessage = "更新確認URLは設定されていません。"
            return
        }
        let version = (manifest["app_version"] as? String) ?? (manifest["version"] as? String) ?? "0.1.0"
        let allowedHosts = (manifest["update_allowed_hosts"] as? [String]) ?? []
        let updateJSON = paths.logs.appendingPathComponent("update_check.json")
        updateCheckRunning = true
        updateMessage = "更新を確認しています。DICOM/CT/path/logは送信しません。"
        let command = CommandBuilder.updateCheckCommand(
            python: paths.venvPython,
            manifestURL: url,
            json: updateJSON,
            currentVersion: version,
            allowedHosts: allowedHosts
        )
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let updateRunner = ProcessRunner()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            _ = updateRunner.run(command, environment: environment, logURL: nil)
            let result = readJSON(updateJSON) ?? [:]
            DispatchQueue.main.async {
                self?.updateCheckRunning = false
                let status = (result["status"] as? String) ?? "failed"
                if status == "update_available" || status == "critical_update_available" {
                    let latest = (result["latest_version"] as? String) ?? "unknown"
                    self?.updateMessage = "新しい版があります: \(latest)"
                    self?.pendingUpdateVersion = latest
                    self?.pendingUpdateSHA256 = (result["sha256"] as? String) ?? ""
                    if let download = result["download_url"] as? String {
                        self?.pendingDownloadURL = URL(string: download)
                    }
                } else if status == "current" {
                    self?.pendingDownloadURL = nil
                    self?.pendingUpdateVersion = ""
                    self?.pendingUpdateSHA256 = ""
                    self?.showingUpdateConfirmation = false
                    self?.updateMessage = "現在の版は最新です。"
                } else {
                    self?.pendingDownloadURL = nil
                    self?.pendingUpdateVersion = ""
                    self?.pendingUpdateSHA256 = ""
                    self?.showingUpdateConfirmation = false
                    self?.updateMessage = "更新確認に失敗しました。"
                }
            }
        }
    }

    func openPendingDownload() {
        showingUpdateConfirmation = true
    }

    func confirmOpenPendingDownload() {
        downloadAndInstallPendingUpdate()
    }

    private func downloadAndInstallPendingUpdate() {
        guard !updateInstallRunning else {
            return
        }
        guard let downloadURL = pendingDownloadURL else {
            updateMessage = "更新ファイルURLが見つかりません。"
            return
        }
        guard downloadURL.scheme == "https" else {
            updateMessage = "更新ファイルURLがHTTPSではありません。"
            return
        }
        guard !pendingUpdateSHA256.isEmpty else {
            updateMessage = "更新ファイルのSHA256がmanifestにありません。"
            return
        }
        let appURL = Bundle.main.bundleURL.standardizedFileURL
        guard appURL.pathExtension == "app" else {
            updateMessage = "現在のアプリ位置を確認できません。"
            return
        }
        let installParent = appURL.deletingLastPathComponent()
        guard FileManager.default.isWritableFile(atPath: installParent.path) else {
            updateMessage = "現在のアプリ保存先に書き込めません。Applicationsではなく、ユーザーのApplicationsへコピーしてから更新してください。"
            return
        }

        updateInstallRunning = true
        updateMessage = "更新ファイルをダウンロードしています。"
        let version = pendingUpdateVersion.isEmpty ? "latest" : pendingUpdateVersion
        let expectedSHA256 = pendingUpdateSHA256.lowercased()
        let updatesDir = paths.support.appendingPathComponent("updates", isDirectory: true)
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            do {
                try FileManager.default.createDirectory(at: updatesDir, withIntermediateDirectories: true)
                let data = try Data(contentsOf: downloadURL)
                let actualSHA256 = sha256Hex(data)
                guard actualSHA256 == expectedSHA256 else {
                    throw UpdateInstallError.sha256Mismatch(expected: expectedSHA256, actual: actualSHA256)
                }
                let localDMG = updatesDir.appendingPathComponent("TotalSegmentator Wrapper for Mac-\(version)-arm64.dmg")
                try data.write(to: localDMG, options: [.atomic])
                let scriptURL = try writeUpdateInstallerScript(
                    appURL: appURL,
                    dmgURL: localDMG,
                    helperRoot: updatesDir.appendingPathComponent("install_\(Int(Date().timeIntervalSince1970))", isDirectory: true)
                )
                try launchUpdateInstaller(scriptURL)
                DispatchQueue.main.async {
                    self?.updateInstallRunning = false
                    self?.updateMessage = "更新を開始しました。アプリを終了して置き換えます。"
                    NSApplication.shared.terminate(nil)
                }
            } catch {
                DispatchQueue.main.async {
                    self?.updateInstallRunning = false
                    self?.updateMessage = "更新に失敗しました: \(updateInstallMessage(error))"
                }
            }
        }
    }

    func showDetailedLog() {
        refreshLog(from: currentLogURL)
        showLog = true
    }

    func openCurrentLogFile() {
        openURLInWorkspace(currentLogURL)
    }

    func openCurrentLogFolder() {
        openURLInWorkspace(currentLogURL.deletingLastPathComponent())
    }

    func refreshLog(from url: URL? = nil) {
        let target = url ?? currentLogURL
        guard let snapshot = readLogTail(target, maxBytes: LOG_TAIL_BYTES) else {
            logInfoText = "ログファイルがまだ見つかりません。"
            logText = ""
            return
        }
        let text = snapshot.text
        if text != lastLogText {
            lastLogText = text
            logText = text
            if snapshot.truncated {
                logInfoText = "ログが大きいため最後の一部だけ表示しています。全文はログファイルで確認できます。"
            } else {
                logInfoText = "詳細ログは最後の一部だけ表示します。全文はログファイルで確認できます。"
            }
        }
        if let progress = runProgressFromLog(text) {
            let signature = progress.signature
            if signature != lastRunProgressSignature {
                lastRunProgressSignature = signature
                lastRunProgressAt = Date()
            }
            runProgressFraction = progress.fraction
            if isRunning {
                progressText = progress.displayText
            }
        }
        updateRunHeartbeat()
    }

    private func startLogTimer() {
        logTimer?.invalidate()
        logTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            guard let self else { return }
            if let startedAt = self.startedAt {
                self.setupElapsed = formatElapsed(Date().timeIntervalSince(startedAt))
                self.runElapsed = formatElapsed(Date().timeIntervalSince(startedAt))
            }
            self.refreshLog(from: self.activeLogURL)
            self.updateRunHeartbeat()
            if !self.setupRunning && !self.isRunning {
                self.logTimer?.invalidate()
                self.logTimer = nil
            }
        }
    }

    private func startRunTimer() {
        startedAt = Date()
        runElapsed = formatElapsed(0)
        startLogTimer()
    }

    private func nextCaseOutput() -> URL {
        defaultRunOutput(root: outputRootURL ?? paths.runs)
    }

    private func resetRunProgressTracking() {
        lastRunProgressAt = nil
        lastRunProgressSignature = ""
        if inputSource == .sample {
            runHeartbeatText = "サンプル1を解析中です。モデル取得済みの場合の目安は約100秒です。途中で数十秒表示が変わらないことがあります。"
        } else {
            runHeartbeatText = "ログを待っています。初回3Dプレビュー作成時はモデル取得で時間がかかる場合があります。"
        }
    }

    private func updateRunHeartbeat(now: Date = Date()) {
        guard isRunning else {
            runHeartbeatText = ""
            return
        }
        guard let lastRunProgressAt else {
            if inputSource == .sample {
                runHeartbeatText = "サンプル1を解析中です。モデル取得済みの場合の目安は約100秒です。途中で数十秒表示が変わらないことがあります。"
            } else {
                runHeartbeatText = "ログを待っています。初回3Dプレビュー作成時はモデル取得で時間がかかる場合があります。"
            }
            return
        }
        let seconds = max(0, Int(now.timeIntervalSince(lastRunProgressAt)))
        if seconds < 5 {
            runHeartbeatText = "進捗ログを受信しました。"
        } else if seconds < 30 {
            runHeartbeatText = "最終更新: \(seconds)秒前。処理は継続中です。"
        } else {
            runHeartbeatText = "最終更新: \(seconds)秒前。大きなデータではこの待ち時間が発生します。"
        }
    }
}

struct LogTailSnapshot {
    let text: String
    let truncated: Bool
}

func readLogTail(_ url: URL, maxBytes: Int = LOG_TAIL_BYTES) -> LogTailSnapshot? {
    guard maxBytes > 0 else { return nil }
    guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
          let size = attrs[.size] as? NSNumber else {
        return nil
    }
    guard let handle = try? FileHandle(forReadingFrom: url) else {
        return nil
    }
    defer {
        try? handle.close()
    }
    let fileSize = size.uint64Value
    let limit = UInt64(maxBytes)
    let offset = fileSize > limit ? fileSize - limit : 0
    handle.seek(toFileOffset: offset)
    let data = handle.readDataToEndOfFile()
    var text = String(decoding: data, as: UTF8.self)
    let truncated = offset > 0
    if truncated {
        text = "（ログが大きいため最後の一部だけ表示しています。全文は下のボタンから開けます。）\n\n" + text
    }
    return LogTailSnapshot(text: text, truncated: truncated)
}

func formatDicomSummary(auditJSON: URL) -> String {
    guard let payload = readJSON(auditJSON) else {
        return "撮影データの確認結果を読めませんでした。"
    }
    if (payload["status"] as? String) == "failed" {
        return formatDicomAuditFailure(payload)
    }
    var lines: [String] = []
    lines.append("撮影データ確認サマリー")
    lines.append("撮影データのまとまり数: \(payload["series_count"] ?? 0)")
    lines.append("プレビュー作成はまだ開始していません。")
    let candidates = cleanDicomSeriesCandidates(payload: payload)
    let viewerCandidates = viewerExportCandidates(payload: payload)
    if candidates.count == 1 {
        lines.append("通常のCTとして取り込める候補があります。自動で準備します。")
    } else if candidates.count > 1 {
        lines.append("通常のCTとして取り込める候補が複数あります。使用する撮影を選んでください。")
    } else if !viewerCandidates.isEmpty {
        lines.append("CTを見るソフトから表示用の断面画像として書き出されたデータの可能性があります。断面群を確認すると非診断preview用に準備できます。")
    } else {
        lines.append("このCTは自動取り込みできませんでした。CT画像そのものが壊れているとは限りません。")
    }
    if let series = payload["series"] as? [[String: Any]] {
        for (index, item) in series.prefix(12).enumerated() {
            let number = item["series_number"] ?? "-"
            let description = (item["series_description"] as? String) ?? "(no description)"
            let classification = item["classification"] as? [String: Any]
            let status = (classification?["status"] as? String).map(dicomClassificationLabel) ?? "-"
            let next = (classification?["next_action"] as? String).map(dicomNextActionShortLabel) ?? "-"
            lines.append("[\(index + 1)] 番号=\(number) \(description): 判定=\(status); 次の操作=\(next)")
        }
        if series.count > 12 {
            lines.append("追加の撮影データは詳細ログで確認できます。")
        }
    }
    return lines.joined(separator: "\n")
}

func cleanDicomSeriesCandidates(auditJSON: URL) -> [CleanDicomSeriesCandidate] {
    guard let payload = readJSON(auditJSON) else {
        return []
    }
    return cleanDicomSeriesCandidates(payload: payload)
}

func cleanDicomSeriesCandidates(payload: [String: Any]) -> [CleanDicomSeriesCandidate] {
    guard let series = payload["series"] as? [[String: Any]] else {
        return []
    }
    var candidates: [CleanDicomSeriesCandidate] = []
    for item in series {
        guard let classification = item["classification"] as? [String: Any] else {
            continue
        }
        let status = classification["status"] as? String
        let nextAction = classification["next_action"] as? String
        let requiresExternalTool = (classification["requires_external_tool"] as? Bool) ?? false
        if status == "original_ct_geometry_ok" && nextAction == "convert_clean" && !requiresExternalTool {
            guard let seriesKey = item["series_key"] as? String, !seriesKey.isEmpty else {
                continue
            }
            let seriesNumber = jsonInt(item["series_number"])
            let description = (item["series_description"] as? String) ?? ""
            let fileCount = jsonInt(item["file_count"]) ?? 0
            candidates.append(
                CleanDicomSeriesCandidate(
                    seriesKey: seriesKey,
                    seriesNumber: seriesNumber,
                    description: description,
                    fileCount: fileCount
                )
            )
        }
    }
    return candidates
}

func viewerExportCandidates(auditJSON: URL) -> [ViewerExportCandidate] {
    guard let payload = readJSON(auditJSON) else {
        return []
    }
    return viewerExportCandidates(payload: payload)
}

func viewerExportCandidates(payload: [String: Any]) -> [ViewerExportCandidate] {
    guard let series = payload["series"] as? [[String: Any]] else {
        return []
    }
    var candidates: [ViewerExportCandidate] = []
    for item in series {
        guard let classification = item["classification"] as? [String: Any],
              classification["status"] as? String == "viewer_export_mpr_mixed_candidate",
              let seriesKey = item["series_key"] as? String,
              let groups = item["viewer_export_groups"] as? [[String: Any]]
        else {
            continue
        }
        let seriesNumber = jsonInt(item["series_number"])
        for group in groups {
            guard let groupID = group["group_id"] as? String,
                  let planeLabel = group["plane_label"] as? String,
                  planeLabel == "axial_like" || planeLabel == "oblique_axial_like",
                  let ai = group["ai_eligibility"] as? [String: Any],
                  ai["status"] as? String == "rescue_go_with_warning",
                  let geometry = group["geometry_checks"] as? [String: Any],
                  (geometry["volume_like"] as? Bool) == true
            else {
                continue
            }
            let shape = group["shape"] as? [String: Any]
            let pixelSpacing = group["pixel_spacing_mm"] as? [String: Any]
            let sliceSpacing = group["slice_spacing_mm"] as? [String: Any]
            candidates.append(
                ViewerExportCandidate(
                    seriesKey: seriesKey,
                    seriesNumber: seriesNumber,
                    groupID: groupID,
                    planeLabel: planeLabel,
                    fileCount: jsonInt(group["file_count"]) ?? 0,
                    rows: jsonInt(shape?["rows"]) ?? 0,
                    columns: jsonInt(shape?["columns"]) ?? 0,
                    rowSpacing: jsonDouble(pixelSpacing?["row"]) ?? 0.0,
                    columnSpacing: jsonDouble(pixelSpacing?["column"]) ?? 0.0,
                    sliceSpacing: jsonDouble(sliceSpacing?["median"]) ?? 0.0,
                    aiEligibility: ai["status"] as? String ?? ""
                )
            )
        }
    }
    return candidates
}

func convertedNiftiURL(metadataJSON: URL) -> URL? {
    guard let payload = readJSON(metadataJSON),
          let outputs = payload["outputs"] as? [String: Any],
          let nifti = outputs["nifti"] as? String,
          !nifti.isEmpty
    else {
        return nil
    }
    return URL(fileURLWithPath: nifti)
}

func viewerExportPreviewSlices(metadataJSON: URL) -> [CTPreviewSlice] {
    guard let payload = readJSON(metadataJSON),
          let outputs = payload["outputs"] as? [String: Any],
          let previews = outputs["mpr_preview"] as? [[String: Any]]
    else {
        return []
    }
    var slices: [CTPreviewSlice] = []
    for preview in previews {
        guard let plane = preview["plane"] as? String,
              let path = preview["path"] as? String,
              !path.isEmpty
        else {
            continue
        }
        slices.append(
            CTPreviewSlice(
                plane: plane,
                label: japanesePreviewPlaneLabel(plane),
                url: URL(fileURLWithPath: path),
                width: jsonInt(preview["width"]) ?? 0,
                height: jsonInt(preview["height"]) ?? 0,
                minValue: jsonDouble(preview["min"]) ?? 0.0,
                maxValue: jsonDouble(preview["max"]) ?? 0.0,
                uniformOrEmpty: (preview["uniform_or_empty"] as? Bool) ?? true
            )
        )
    }
    return slices
}

func makeCTPreviewWarning(slices: [CTPreviewSlice]) -> String {
    let expectedPlanes = Set(["axial", "coronal", "sagittal"])
    let availablePlanes = Set(slices.map(\.plane))
    if !expectedPlanes.isSubset(of: availablePlanes) {
        return "slice previewを作成できませんでした。別の断面群または元CTを確認してください。"
    }
    if slices.allSatisfy(\.uniformOrEmpty) {
        return "画像がほぼ空に見えます。別の断面群または元CTを確認してください。"
    }
    return ""
}

func jsonDouble(_ value: Any?) -> Double? {
    if let doubleValue = value as? Double {
        return doubleValue
    }
    if let number = value as? NSNumber {
        return number.doubleValue
    }
    if let string = value as? String {
        return Double(string)
    }
    return nil
}

func jsonInt(_ value: Any?) -> Int? {
    if let intValue = value as? Int {
        return intValue
    }
    if let number = value as? NSNumber {
        return number.intValue
    }
    if let string = value as? String {
        return Int(string)
    }
    return nil
}

func japanesePlaneLabel(_ planeLabel: String) -> String {
    switch planeLabel {
    case "axial_like":
        return "横断像に近い候補"
    case "oblique_axial_like":
        return "斜め横断像に近い候補"
    case "coronal_like":
        return "冠状断像に近い候補"
    case "sagittal_like":
        return "矢状断像に近い候補"
    default:
        return "断面候補"
    }
}

func japanesePreviewPlaneLabel(_ plane: String) -> String {
    switch plane {
    case "axial":
        return "軸位"
    case "coronal":
        return "冠状"
    case "sagittal":
        return "矢状"
    default:
        return "slice"
    }
}

func formatSpacing(_ value: Double) -> String {
    if value == 0 {
        return "-"
    }
    return String(format: "%.3g", value)
}

func formatNumber(_ value: Double) -> String {
    String(format: "%.3g", value)
}

func dicomAuditFailureMessage(auditJSON: URL) -> String {
    guard let payload = readJSON(auditJSON),
          let reason = payload["reason"] as? String else {
        return dicomAutoImportUnavailableMessage()
    }
    return dicomAutoImportUnavailableMessage() + "\n\n詳細: \(dicomAuditReasonLabel(reason))"
}

func formatDicomAuditFailure(_ payload: [String: Any]) -> String {
    let reason = (payload["reason"] as? String) ?? "unknown"
    var lines: [String] = []
    lines.append(dicomAutoImportUnavailableMessage())
    lines.append("")
    lines.append("理由: \(dicomAuditReasonLabel(reason))")
    if let error = payload["error"] as? String, !error.isEmpty {
        lines.append("詳細: \(error)")
    }
    if let timeout = payload["timeout_sec"] {
        lines.append("確認時間の上限: \(timeout)秒")
    }
    lines.append("入力は変更されていません。プレビュー作成は開始していません。")
    if let causes = payload["possible_causes"] as? [String], !causes.isEmpty {
        lines.append("")
        lines.append("考えられる原因:")
        for cause in causes.prefix(6) {
            lines.append("- \(dicomAuditCauseLabel(cause))")
        }
    }
    if let actions = payload["next_actions"] as? [String], !actions.isEmpty {
        lines.append("")
        lines.append("次に試すこと:")
        for action in actions.prefix(6) {
            lines.append("- \(dicomAuditNextActionLabel(action))")
        }
    }
    return lines.joined(separator: "\n")
}

func dicomAutoImportUnavailableMessage() -> String {
    [
        "このCTは自動取り込みできませんでした。",
        "",
        "CTを見るソフトから「表示用の断面画像」として書き出されたデータの可能性があります。",
        "CT画像そのものが壊れているとは限りません。",
        "",
        "対応できる場合があります。",
        "必要であれば、開発者へご連絡ください。",
    ].joined(separator: "\n")
}

func dicomAuditReasonLabel(_ reason: String) -> String {
    switch reason {
    case "timeout":
        return "確認が時間切れになりました"
    case "normalizer_unavailable":
        return "CT確認用部品が見つからない、または起動できません"
    case "normalizer_failed":
        return "CT確認用部品が結果を作る前に停止しました"
    default:
        return reason
    }
}

func dicomClassificationLabel(_ status: String) -> String {
    switch status {
    case "original_ct_geometry_ok":
        return "通常CTとして取り込み可能"
    case "secondary_capture_rescue_candidate":
        return "救済候補（自動AI不可）"
    case "viewer_export_mpr_mixed_candidate":
        return "viewer書き出し救済候補"
    case "needs_dicom_library":
        return "追加確認が必要"
    case "compressed_pixel_data":
        return "圧縮形式（自動取り込み不可）"
    case "dicomdir_only":
        return "画像本体が不足"
    case "reject":
        return "自動取り込み不可"
    default:
        return status
    }
}

func dicomNextActionShortLabel(_ action: String) -> String {
    switch action {
    case "convert_clean":
        return "アプリが取り込み準備"
    case "prepare_rescue":
        return "手動救済のみ"
    case "select_viewer_export_group":
        return "断面群を選択"
    case "transcode_compressed":
        return "圧縮解除が必要"
    case "manual_review":
        return "手動確認が必要"
    case "reject":
        return "別のCTを選択"
    default:
        return action
    }
}

func dicomAuditCauseLabel(_ cause: String) -> String {
    switch cause {
    case "folder_contains_many_or_non_dicom_files":
        return "フォルダ内のファイルが多い、または撮影データ以外のファイルが混ざっています"
    case "cloud_storage_files_may_not_be_fully_local":
        return "iCloud/Dropbox等のファイルがMac上に完全にダウンロードされていない可能性があります"
    case "dicomdir_or_nested_export_requires_manual_review":
        return "深い階層の書き出しで、読むべきフォルダが別にある可能性があります"
    case "normalizer_audit_took_too_long":
        return "CT確認に時間がかかりすぎました"
    case "normalizer_binary_missing_or_not_executable":
        return "同梱CT確認用部品が見つからない、または実行できません"
    case "app_bundle_may_be_incomplete":
        return "アプリのコピーが不完全な可能性があります"
    case "normalizer_stopped_before_writing_json":
        return "CT確認用部品が結果ファイルを書く前に停止しました"
    case "unsupported_or_malformed_dicom_metadata":
        return "対応外または壊れた撮影データ情報の可能性があります"
    case "folder_contains_non_dicom_files":
        return "撮影データ以外のファイルが混ざっている可能性があります"
    default:
        return cause
    }
}

func dicomAuditNextActionLabel(_ action: String) -> String {
    switch action {
    case "Copy the DICOM folder to a local disk and ensure files are fully downloaded.":
        return "CTフォルダをローカルディスクへコピーし、ファイルが完全にダウンロード済みか確認してください。"
    case "Choose the innermost folder that directly contains DICOM slices.":
        return "撮影画像ファイルが直接入っている、一番内側のフォルダを選んでください。"
    case "If the folder contains reports, screenshots, or unrelated files, try the CT series folder only.":
        return "レポート、スクリーンショット、無関係なファイルが混ざる場合は、CT seriesだけのフォルダを試してください。"
    case "Open detailed log and share it only after checking local paths.":
        return "詳細ログを開き、ローカルパス等を確認してから共有してください。"
    case "Copy the app from the DMG again.":
        return "DMGからアプリをもう一度コピーしてください。"
    case "Run setup again so the bundled DICOM normalizer can be checked.":
        return "セットアップをもう一度実行し、同梱CT確認用部品を確認してください。"
    default:
        return action
    }
}

enum UpdateInstallError: Error {
    case sha256Mismatch(expected: String, actual: String)
    case helperLaunchFailed
}

func updateInstallMessage(_ error: Error) -> String {
    if let updateError = error as? UpdateInstallError {
        switch updateError {
        case let .sha256Mismatch(expected, actual):
            return "更新ファイルのSHA256が一致しません。expected \(expected), actual \(actual)"
        case .helperLaunchFailed:
            return "更新用helperを起動できませんでした。"
        }
    }
    return String(describing: error)
}

func sha256Hex(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

func writeUpdateInstallerScript(appURL: URL, dmgURL: URL, helperRoot: URL) throws -> URL {
    let mountURL = helperRoot.appendingPathComponent("mount", isDirectory: true)
    try FileManager.default.createDirectory(at: helperRoot, withIntermediateDirectories: true)
    let scriptURL = helperRoot.appendingPathComponent("install_update.zsh")
    let appPath = shellSingleQuote(appURL.path)
    let dmgPath = shellSingleQuote(dmgURL.path)
    let mountPath = shellSingleQuote(mountURL.path)
    let script = """
#!/bin/zsh
set -euo pipefail
DMG=\(dmgPath)
APP=\(appPath)
MOUNT=\(mountPath)
APP_PARENT="$(/usr/bin/dirname "$APP")"
BACKUP="$APP_PARENT/.TotalSegmentator Wrapper for Mac.app.update-backup.$$"
RESTORE_BACKUP=0
mkdir -p "$MOUNT"
sleep 2
hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MOUNT" >/dev/null
cleanup() {
  hdiutil detach "$MOUNT" >/dev/null 2>&1 || true
}
trap cleanup EXIT
NEW_APP="$MOUNT/TotalSegmentator Wrapper for Mac.app"
if [[ ! -d "$NEW_APP" ]]; then
  echo "TotalSegmentator Wrapper for Mac.app was not found in update DMG" >&2
  exit 3
fi
/usr/sbin/spctl --assess --type execute --verbose=2 "$NEW_APP"
if [[ -d "$APP" ]]; then
  /bin/chmod -R u+w "$APP" >/dev/null 2>&1 || true
  /bin/mv "$APP" "$BACKUP"
  RESTORE_BACKUP=1
fi
if ! /usr/bin/ditto "$NEW_APP" "$APP"; then
  if [[ "$RESTORE_BACKUP" == "1" && -d "$BACKUP" && ! -d "$APP" ]]; then
    /bin/mv "$BACKUP" "$APP" || true
  fi
  exit 4
fi
if [[ -d "$BACKUP" ]]; then
  /bin/rm -rf "$BACKUP"
fi
cleanup
/usr/bin/open "$APP"
"""
    try script.write(to: scriptURL, atomically: true, encoding: .utf8)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scriptURL.path)
    return scriptURL
}

func launchUpdateInstaller(_ scriptURL: URL) throws {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/bin/zsh")
    process.arguments = [scriptURL.path]
    do {
        try process.run()
    } catch {
        throw UpdateInstallError.helperLaunchFailed
    }
}

func shellSingleQuote(_ value: String) -> String {
    "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

struct RunLogProgress {
    let step: Int?
    let total: Int?
    let percent: Int?
    let stage: String?

    var signature: String {
        let stepText = step.map { String($0) } ?? ""
        let totalText = total.map { String($0) } ?? ""
        let percentText = percent.map { String($0) } ?? ""
        return "\(stage ?? "")|\(stepText)|\(totalText)|\(percentText)"
    }

    var fraction: Double? {
        if percent == 100 {
            return nil
        }
        if let percent {
            return max(0.0, min(1.0, Double(percent) / 100.0))
        }
        if let step, let total, total > 0 {
            return max(0.0, min(1.0, Double(step) / Double(total)))
        }
        return nil
    }

    var displayText: String {
        let stagePrefix = stage.flatMap { $0.isEmpty ? nil : "\($0) " } ?? ""
        if percent == 100 {
            return "プレビュー作成中: \(stagePrefix)完了。次の処理へ進んでいます..."
        }
        if let step, let total, total > 0 {
            if let percent {
                return "プレビュー作成中: \(stagePrefix)\(step)/\(total) (\(percent)%)"
            }
            return "プレビュー作成中: \(stagePrefix)\(step)/\(total)"
        }
        if let percent {
            return "プレビュー作成中: \(stagePrefix)\(percent)%"
        }
        return "プレビュー作成中: \(stagePrefix)進行中"
    }
}

func runProgressFromLog(_ text: String) -> RunLogProgress? {
    var last: RunLogProgress?
    for rawLine in text.split(whereSeparator: \.isNewline) {
        let line = String(rawLine)
        guard line.hasPrefix("RUN_PROGRESS ") else {
            continue
        }
        let jsonText = String(line.dropFirst("RUN_PROGRESS ".count))
        guard let data = jsonText.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data),
              let payload = object as? [String: Any]
        else {
            continue
        }
        last = RunLogProgress(
            step: intFromJSON(payload["step"]),
            total: intFromJSON(payload["total"]),
            percent: intFromJSON(payload["percent"]),
            stage: stringFromJSON(payload["stage"])
        )
    }
    return last
}

func intFromJSON(_ value: Any?) -> Int? {
    if value is NSNull {
        return nil
    }
    if let number = value as? NSNumber {
        return number.intValue
    }
    if let string = value as? String {
        return Int(string)
    }
    return nil
}

func stringFromJSON(_ value: Any?) -> String? {
    if value is NSNull {
        return nil
    }
    if let string = value as? String {
        return string
    }
    return nil
}

func isDirectory(_ url: URL) -> Bool {
    var isDirectory = ObjCBool(false)
    return FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory) && isDirectory.boolValue
}

func sameFileURL(_ left: URL, _ right: URL) -> Bool {
    left.standardizedFileURL.path == right.standardizedFileURL.path
}
