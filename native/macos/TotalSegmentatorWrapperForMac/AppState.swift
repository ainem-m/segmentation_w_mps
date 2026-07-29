import Foundation
import AppKit
import Combine
import Dispatch
import CryptoKit

let LOG_TAIL_BYTES = 64 * 1024

enum AppScreen {
    case setup
    case start
    case inputAndCreation
    case running
    case dicomRescue
    case ctPreview
    case result
}

enum ResultOutputFlavor: String, CaseIterable, Identifiable {
    case craniofacial
    case toothSeg

    var id: String { rawValue }

    var title: String {
        switch self {
        case .craniofacial:
            return "歯列・顎骨"
        case .toothSeg:
            return "高精細歯（ToothSeg）"
        }
    }
}

enum CreationChoice: String, CaseIterable, Identifiable {
    case standardArchJaw = "歯列と顎骨の3Dプレビュー"
    case individualTeethBeta = "歯を1本ずつ分ける（ベータ）"
    case dentalSegmentatorExperimental = "DentalSegmentator（実験的）"
    case toothSegExperimental = "ToothSeg（個別歯・実験的）"

    var id: String { rawValue }

    static let primaryChoices: [CreationChoice] = [
        .standardArchJaw,
        .dentalSegmentatorExperimental,
    ]

    static let advancedChoices: [CreationChoice] = [
        .individualTeethBeta,
    ]

    var runMode: RunMode {
        self == .individualTeethBeta || self == .toothSegExperimental ? .individualTeeth : .archPreview
    }

    var backend: SegmentationBackend {
        switch self {
        case .dentalSegmentatorExperimental: return .dentalSegmentator
        case .toothSegExperimental: return .toothSeg
        default: return .totalSegmentator
        }
    }

    var detail: String {
        switch self {
        case .standardArchJaw:
            return "歯列と顎骨をまとめて表示します。"
        case .individualTeethBeta:
            return "歯を1本ずつ分けて表示します。処理に時間がかかります。"
        case .dentalSegmentatorExperimental:
            return "歯列と顎骨を5つの領域に分けます。"
        case .toothSegExperimental:
            return "歯を1本ずつ分け、FDI歯式番号を付けます。初回に約920 MBを取得します。"
        }
    }
}

enum ResultKind {
    case none
    case inference
    case dicomAudit
}

enum ResultOutcome: Equatable {
    case none
    case success
    case failure
}

enum ModelPreparationPurpose: Equatable {
    case creationSelection
    case toothSegRefine
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
        return "画像の間隔が粗いため、3Dプレビューが段々に見えることがあります。"
    }
}

enum DicomRescueWorkflowState: String, CaseIterable {
    case rescueAvailable
    case estimating
    case editableReady
    case userModified
    case manualOnly
    case sourceStackUnavailable
    case preparingNifti
    case validatingNifti
    case prepareFailed
    case readbackMismatch

    var label: String {
        switch self {
        case .rescueAvailable: return "救済候補があります"
        case .estimating: return "形状候補を作成中"
        case .editableReady: return "候補作成済み"
        case .userModified: return "手動調整中"
        case .manualOnly: return "手動調整が必要"
        case .sourceStackUnavailable: return "画像を準備できません"
        case .preparingNifti: return "確定した形状を適用中"
        case .validatingNifti: return "作成した画像を確認中"
        case .prepareFailed: return "救済データを作成できませんでした"
        case .readbackMismatch: return "確定値と出力が一致しません"
        }
    }
}

enum RescueAxisPermutation: String, CaseIterable, Identifiable {
    case xyz
    case xzy
    case yxz
    case yzx
    case zxy
    case zyx

    var id: String { rawValue }
    var displayName: String { rawValue.uppercased() }
}

enum RescueCalibrationAxis: String, CaseIterable, Identifiable {
    case x
    case y
    case z

    var id: String { rawValue }
    var displayName: String { rawValue.uppercased() }
}

func rescueEvidenceDisplayText(_ rawValue: String) -> String {
    let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !value.isEmpty else {
        return "この情報だけでは推定の確かさを判断できません"
    }
    if value.unicodeScalars.contains(where: { $0.value > 0x7f }) {
        return value
    }
    switch value.lowercased() {
    case "pixel_spacing":
        return "DICOMに残っている画素間隔を使用しました"
    case "ipp_iop_projected_spacing":
        return "撮影位置と向きから断面間隔を計算しました"
    case "spacing_between_slices":
        return "DICOMに残っている断面間隔を使用しました"
    case "slice_thickness", "axial_slice_step":
        return "DICOMに残っているスライス厚をZ方向の候補に使用しました"
    case "fallback_initial_candidate":
        return "情報が不足している方向には編集用の仮初期値を使用しました"
    case "sagittal_series_count_fov_seed":
        return "SAGITTAL系列の枚数と範囲をX方向の候補に使用しました"
    case "coronal_series_count_fov_seed":
        return "CORONAL系列の枚数と範囲をY方向の候補に使用しました"
    case "tri_planar_registration":
        return "三方向画像の位置関係を候補作成に使用しました"
    case "x_standard_or_vendor_tag_hint":
        return "DICOM内の利用可能な情報をX方向の候補に使用しました"
    case "y_standard_or_vendor_tag_hint":
        return "DICOM内の利用可能な情報をY方向の候補に使用しました"
    case "z_standard_or_vendor_tag_hint":
        return "DICOM内の利用可能な情報をZ方向の候補に使用しました"
    case "x_spacing_uses_fallback":
        return "X方向は情報不足のため仮初期値です"
    case "y_spacing_uses_fallback":
        return "Y方向は情報不足のため仮初期値です"
    case "z_spacing_uses_fallback":
        return "Z方向は情報不足のため仮初期値です"
    case "series_count_crop_and_zoom_unknown":
        return "余白・切り抜き・保存時の拡大率は特定できません"
    case "screen_capture_crop_offset_and_zoom_may_be_non_unique":
        return "余白や位置ずれを含むため、同程度に見える候補が複数あります"
    case "registration_not_validated_on_target_real_data":
        return "このデータで推定精度を保証できるものではありません"
    case "registration_evidence_unavailable", "reference_planes_unavailable":
        return "三方向画像による十分な照合結果を得られませんでした"
    case "foreground_not_detected":
        return "画像内の対象範囲を自動判定できませんでした"
    case "large_border_or_burned_in_overlay_candidate":
        return "大きな余白・枠・文字が推定に影響している可能性があります"
    default:
        return "画像だけでは推定の確かさを十分に判断できません"
    }
}

struct RescueSpacing: Equatable {
    var x: Double
    var y: Double
    var z: Double

    var isValid: Bool {
        [x, y, z].allSatisfy { $0.isFinite && $0 > 0 && $0 <= 20 }
    }

    var commandValue: String {
        [x, y, z].map { String(format: "%.6f", $0) }.joined(separator: ",")
    }
}

struct SecondaryCaptureRescueCandidate: Identifiable, Equatable {
    let seriesKey: String
    let seriesNumber: Int?
    let classificationStatus: String
    let plane: String
    let role: String
    let reconstructionGroup: String
    let fileCount: Int
    let rows: Int
    let columns: Int
    let pixelSpacingRow: Double?
    let pixelSpacingColumn: Double?
    let projectedSliceSpacing: Double?
    let spacingBetweenSlices: Double?
    let sliceThickness: Double?
    let contentManifestSHA256: String?
    let studyKeySHA256: String?

    var id: String { seriesKey }

    var initialSpacing: RescueSpacing {
        RescueSpacing(
            x: validSpacing(pixelSpacingColumn) ?? 1.0,
            y: validSpacing(pixelSpacingRow) ?? 1.0,
            z: validSpacing(projectedSliceSpacing)
                ?? validSpacing(spacingBetweenSlices)
                ?? validSpacing(sliceThickness)
                ?? 1.0
        )
    }

    var preferredSliceStep: Double? {
        validSpacing(projectedSliceSpacing)
            ?? validSpacing(spacingBetweenSlices)
            ?? validSpacing(sliceThickness)
    }

    var initialSpacingEvidence: [String] {
        var evidence: [String] = []
        if validSpacing(pixelSpacingRow) != nil, validSpacing(pixelSpacingColumn) != nil {
            evidence.append("Pixel SpacingをX/Y候補に使用（X=column、Y=row）")
        } else {
            evidence.append("X/Yは編集用の仮初期値")
        }
        if validSpacing(projectedSliceSpacing) != nil {
            evidence.append("IPPをIOP法線へ投影した隣接差をZ候補に使用")
        } else if validSpacing(spacingBetweenSlices) != nil {
            evidence.append("Spacing Between SlicesをZ候補に使用")
        } else if validSpacing(sliceThickness) != nil {
            evidence.append("Slice Thicknessを低信頼のZ候補に使用")
        } else {
            evidence.append("Zは編集用の仮初期値")
        }
        return evidence
    }

    var hasFallbackSpacingAxis: Bool {
        validSpacing(pixelSpacingRow) == nil
            || validSpacing(pixelSpacingColumn) == nil
            || (
                validSpacing(projectedSliceSpacing) == nil
                    && validSpacing(spacingBetweenSlices) == nil
                    && validSpacing(sliceThickness) == nil
            )
    }

    private func validSpacing(_ value: Double?) -> Double? {
        value.flatMap { $0.isFinite && $0 > 0 && $0 <= 20 ? $0 : nil }
    }

    var displayPlane: String {
        switch plane {
        case "axial": return "AXIAL"
        case "coronal": return "CORONAL"
        case "sagittal": return "SAGITTAL"
        default: return "方向不明"
        }
    }

    var displayRole: String {
        switch role {
        case "primary": return "primary"
        case "reference": return "reference"
        case "cross_validation": return "cross-validation"
        default: return "excluded"
        }
    }
}

struct CTPreviewSlice: Identifiable, Equatable {
    let plane: String
    let label: String
    let url: URL
    let width: Int
    let height: Int
    let rowSpacingMM: Double?
    let columnSpacingMM: Double?
    let minValue: Double
    let maxValue: Double
    let uniformOrEmpty: Bool

    var id: String { plane }

}

struct RunReadinessItem: Identifiable, Equatable {
    let id: String
    let title: String
    let value: String
    let detail: String
    let systemImage: String
    let state: String
}

struct RunLocationItem: Identifiable, Equatable {
    let id: String
    let title: String
    let path: String
    let detail: String
    let systemImage: String
    let exists: Bool
}

private enum UserSettingKey {
    static let creationChoice = "\(appSupportName).creationChoice"
    static let runMode = "\(appSupportName).runMode"
    static let segmentationBackend = "\(appSupportName).segmentationBackend"
    static let device = "\(appSupportName).device"
    static let higherOrderResampling = "\(appSupportName).higherOrderResampling"
    static let outputRootURL = "\(appSupportName).outputRootURL"
}

final class AppState: ObservableObject {
    let paths: AppPaths
    private let runner = ProcessRunner()
    private let dentalPreparationRunner = ProcessRunner()
    private var logTimer: Timer?
    private var dentalPreparationTimer: Timer?
    private var stlStatusTimer: Timer?
    private var dentalPreparationCancellationRequested = false
    private var startedAt: Date?
    private var lastLogText = ""
    private var activeLogURL: URL?
    private var resultLogURL: URL?
    var lastDicomDirURL: URL?
    private var lastRunProgressAt: Date?
    private var lastRunProgressSignature = ""
    private var rescueCalibrationRecords: [[String: Any]] = []
    private var rescuePreparationCancellationRequested = false
    private var rescuePreviewWorkItem: DispatchWorkItem?

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
    @Published var showDicomSeriesSelection = false
    @Published var showDentalPreparationConfirmation = false
    @Published var showDentalPreparationSheet = false
    @Published var dentalPreparationRunning = false
    @Published var dentalPreparationElapsed = "経過時間: 0秒"
    @Published var dentalPreparationMessage = "DentalSegmentatorのモデルを準備します。"
    @Published var dentalPreparationFraction: Double?
    @Published var dentalPreparationDetail = ""
    @Published var pendingModelPreparationChoice: CreationChoice = .dentalSegmentatorExperimental
    @Published var modelPreparationPurpose: ModelPreparationPurpose = .creationSelection
    private var dentalPreparationStartedAt: Date?

    @Published var inputURL: URL?
    @Published var inputSource: InputSource = .none
    @Published var creationChoice: CreationChoice = .standardArchJaw {
        didSet {
            runMode = creationChoice.runMode
            segmentationBackend = creationChoice.backend
            device = "mps"
            saveUserSettings()
        }
    }
    @Published var outputURL: URL?
    @Published var outputRootURL: URL? {
        didSet { saveUserSettings() }
    }
    @Published var runMode: RunMode = .archPreview {
        didSet { saveUserSettings() }
    }
    @Published var segmentationBackend: SegmentationBackend = .totalSegmentator {
        didSet { saveUserSettings() }
    }
    @Published var device = "mps" {
        didSet {
            if device != "mps" { device = "mps" }
            saveUserSettings()
        }
    }
    @Published var higherOrderResampling = false {
        didSet { saveUserSettings() }
    }
    @Published var statusText = "待機中"
    @Published var progressText = "まだ実行していません。"
    @Published var runHeartbeatText = ""
    @Published var runProgressFraction: Double?
    @Published var runStageEvent: RunStageEvent?
    @Published var runStageProgress: RunLogProgress?
    @Published var runStageStartedAt: Date?
    @Published var runElapsed = "経過時間: 0秒"
    @Published var isRunning = false
    @Published var stopRequested = false
    @Published var surfacePreviewFailed = false
    @Published var failureReasonText = ""
    @Published var safeErrorCode = ""
    @Published var safeErrorReason = ""
    @Published var safeMPSState = "unknown"
    @Published var safeErrorOccurredAt = ""
    @Published var activeRunBackend: SegmentationBackend = .totalSegmentator
    @Published var activeRunMode: RunMode = .archPreview
    @Published var activeRunDevice = "mps"
    @Published var primaryRunBackend: SegmentationBackend = .totalSegmentator
    @Published var primaryRunMode: RunMode = .archPreview
    @Published var activeResultFlavor: ResultOutputFlavor = .craniofacial
    @Published var canRunToothSegRefine = false
    @Published var teethDetected = false
    @Published var refineAvailable = false
    @Published var primaryRunTeethDetected = false
    @Published var toothSegRefineFailed = false
    @Published var resultMessage = ""
    @Published var summaryText = ""
    @Published var dicomSummaryText = ""
    @Published var dicomCleanCandidates: [CleanDicomSeriesCandidate] = []
    @Published var selectedDicomSeriesID: String?
    @Published var pendingDicomSeriesID: String?
    @Published var dicomSelectionWasChanged = false
    @Published var dicomViewerExportCandidates: [ViewerExportCandidate] = []
    @Published var selectedViewerExportCandidateID: String?
    @Published var dicomRescueCandidates: [SecondaryCaptureRescueCandidate] = []
    @Published var selectedDicomRescueCandidateID: String?
    @Published var rescueWorkflowState: DicomRescueWorkflowState = .rescueAvailable
    @Published var rescueSpacingX = 1.0
    @Published var rescueSpacingY = 1.0
    @Published var rescueSpacingZ = 1.0
    @Published var rescueEstimatedSpacing = RescueSpacing(x: 1.0, y: 1.0, z: 1.0)
    @Published var rescueConfidence = "低"
    @Published var rescueEvidence: [String] = []
    @Published var rescueXYLocked = false
    @Published var rescueAxisPermutation: RescueAxisPermutation = .xyz
    @Published var rescueRotationQuarterTurns = 0
    @Published var rescueSliceOrderReversed = false
    @Published var rescueCalibrationAxis: RescueCalibrationAxis = .x
    @Published var rescueMeasuredLengthMM = 0.0
    @Published var rescueKnownLengthMM = 0.0
    @Published var rescueMeasurementPlane = "axial"
    @Published var rescueMeasurementStartNormalized: CGPoint?
    @Published var rescueMeasurementEndNormalized: CGPoint?
    @Published var rescuePreviewShapeXYZ: [Int] = []
    @Published var rescueInlineWarning = ""
    @Published var rescueConfirmationWasExplicit = false
    @Published var rescuePreviewRevision = 0
    @Published var rescueCropMinX = 0
    @Published var rescueCropMinY = 0
    @Published var rescueCropMinZ = 0
    @Published var rescueCropMaxX = 1
    @Published var rescueCropMaxY = 1
    @Published var rescueCropMaxZ = 1
    @Published var rescueMPRPreviewSlices: [CTPreviewSlice] = []
    @Published var rescuePseudo3DPreviewURL: URL?
    @Published var rescuePreviewMetadataInferenceStarted = false
    @Published var rescuePreviewStatus = "三方向の画像を準備しています"
    @Published var rescueImageUpdateFailed = false
    @Published var rescueConfirmationToken = ""
    @Published var rescueDecodedVolumeURL: URL?
    @Published var rescueGeometryJSONURL: URL?
    @Published var rescueSourceManifestSHA256 = ""
    @Published var pendingPreparedInputURL: URL?
    @Published var pendingViewerExportMetadataURL: URL?
    @Published var pendingViewerExportCandidate: ViewerExportCandidate?
    @Published var ctPreviewSlices: [CTPreviewSlice] = []
    @Published var ctPreviewWarning = ""
    @Published var inputCTPreviewRequired = false
    @Published var inputCTPreviewSlices: [CTPreviewSlice] = []
    @Published var inputCTPreviewWarning = ""
    @Published var inputCTPreviewVolumeEmpty = false
    @Published var inputCTPreviewFailed = false
    @Published var stlGenerationStatus = "unavailable"
    @Published var resultKind: ResultKind = .none
    @Published var resultOutcome: ResultOutcome = .none
    @Published var updateMessage = ""
    @Published var pendingDownloadURL: URL?
    @Published var pendingUpdateVersion = ""
    @Published var pendingUpdateSHA256 = ""
    @Published var showingUpdateConfirmation = false
    @Published var updateCheckRunning = false
    @Published var updateInstallRunning = false
    @Published private(set) var uiPreviewScenario = ""

    var isUIPreviewMode: Bool {
        !uiPreviewScenario.isEmpty
    }

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
        !isRunning && inputSource == .sample && inputURL != nil && canRunSelectedSettings
    }

    var isSampleInputSelected: Bool {
        inputSource == .sample && inputURL != nil
    }

    var inputDisplayName: String {
        if inputSource == .sample {
            return "Sample 1"
        }
        return inputURL?.lastPathComponent ?? "入力が選択されていません"
    }

    var ownDataPrimaryButtonTitle: String {
        "3Dプレビューを作成"
    }

    var selectedCreationChoice: CreationChoice {
        get { creationChoice }
        set { creationChoice = newValue }
    }

    var creationChoiceNeedsPreparation: Bool {
        switch creationChoice {
        case .dentalSegmentatorExperimental: return !isDentalSegmentatorModelReady
        case .toothSegExperimental: return !isToothSegModelReady
        default: return false
        }
    }

    var canStartOwnDataRun: Bool {
        guard !isRunning else { return false }
        return inputSource == .nifti && canRunSelectedSettings
    }

    var effectiveRunDevice: String {
        "mps"
    }

    var activeRunTaskText: String {
        "\(activeRunMode.rawValue) / task=\(activeRunMode.task)"
    }

    var activeRunFeatureName: String {
        if activeRunBackend == .dentalSegmentator {
            return "DentalSegmentator（実験的）"
        }
        if activeRunBackend == .toothSeg {
            return "ToothSeg（個別歯・実験的）"
        }
        return activeRunMode == .individualTeeth ? "TotalSegmentator（個別歯ベータ）" : "TotalSegmentator"
    }

    var runWeightedProgress: RunWeightedProgress? {
        guard let runStageEvent else { return nil }
        return RunWeightedProgress.calculate(stage: runStageEvent, progress: runStageProgress)
    }

    var activeRunStageWeights: [Double] {
        guard let route = runStageEvent?.route else { return [] }
        return RunProgressProfile.profiles[route]?.weights ?? []
    }

    var runStageElapsedText: String {
        guard let runStageStartedAt else { return "この工程の経過時間: 0秒" }
        return "この工程の経過時間: \(formatCompactDuration(max(0, Int(Date().timeIntervalSince(runStageStartedAt)))))"
    }

    var activeRunDeviceText: String {
        activeRunBackend == .totalSegmentator ? activeRunDevice : "mps（CPUへ自動切替しません）"
    }

    var canRunSelectedSettings: Bool {
        runPreflightBlockingReason.isEmpty
    }

    var runSettingsWarning: String {
        if !runPreflightBlockingReason.isEmpty {
            return runPreflightBlockingReason
        }
        if segmentationBackend == .dentalSegmentator {
            return "DentalSegmentatorは実験的な追加機能です。"
        }
        if segmentationBackend == .toothSeg {
            return "ToothSegは実験的な追加機能です。"
        }
        return ""
    }

    var runPreflightBlockingReason: String {
        if dentalPreparationRunning {
            return "追加モデルの準備が終了するまでお待ちください。"
        }
        if inputCTPreviewRequired && inputCTPreviewVolumeEmpty {
            return "CT画像の内容を確認できません。空の画像、または正しく書き出されていない画像の可能性があります。"
        }
        if inputCTPreviewRequired
            && (inputCTPreviewFailed || Set(inputCTPreviewSlices.map(\.plane)).count < 3) {
            return "CTの簡易プレビューを作成できませんでした。再度CTを選び直してください。"
        }
        if segmentationBackend == .dentalSegmentator && runMode == .individualTeeth {
            return "DentalSegmentatorは歯列・顎骨の5ラベルpreview用です。個別歯ベータはTotalSegmentator backendを選んでください。"
        }
        if segmentationBackend == .dentalSegmentator && !isDentalSegmentatorModelReady {
            return "DentalSegmentatorの初回準備を完了してから実行してください。"
        }
        if segmentationBackend == .toothSeg && !isToothSegModelReady {
            return "ToothSegの初回準備を完了してから実行してください。"
        }
        return ""
    }

    var isDentalSegmentatorModelReady: Bool {
        guard let marker = readJSON(paths.dentalsegReadyMarker) else {
            return false
        }
        return marker["schema"] as? String == "totalsegmentator_wrapper_mac.dentalsegmentator_model_status.v1"
            && marker["model_state"] as? String == "ready"
            && marker["expected_md5"] as? String == dentalsegExpectedMD5
            && marker["dataset_id"] as? String == "112"
            && marker["dataset_name"] as? String == "Dataset112_DentalSegmentator_v100"
            && FileManager.default.fileExists(atPath: paths.dentalsegInstalledModel.path)
    }

    var dentalSegmentatorModelStatusText: String {
        if setupRunning && setupStep == .downloadDentalsegWeights {
            return "モデル準備中"
        }
        if isDentalSegmentatorModelReady {
            return "モデル準備済み"
        }
        return "モデル未準備"
    }

    var isToothSegModelReady: Bool {
        guard let marker = readJSON(paths.toothsegReadyMarker) else {
            return false
        }
        return marker["schema"] as? String == "totalsegmentator_wrapper_mac.toothseg_model_status.v1"
            && marker["model_state"] as? String == "ready"
            && marker["expected_md5"] as? String == toothsegExpectedMD5
            && marker["pair_distributions_sha256"] as? String == toothsegPairDistributionsSHA256
            && marker["semantic_mps_patch_size"] as? [Int] == toothsegSemanticMPSPatchSize
            && FileManager.default.fileExists(atPath: paths.toothsegSemanticModel.path)
            && FileManager.default.fileExists(atPath: paths.toothsegInstanceModel.path)
            && FileManager.default.fileExists(atPath: paths.toothsegPairDistributions.path)
    }

    var toothSegModelStatusText: String {
        if dentalPreparationRunning && pendingModelPreparationChoice == .toothSegExperimental {
            return "モデル準備中"
        }
        return isToothSegModelReady ? "モデル準備済み" : "モデル未準備"
    }

    var toothSegModelDetailText: String {
        isToothSegModelReady
            ? paths.toothsegResults.path
            : "ToothSegを初めて選んだときに追加モデルを準備します。"
    }

    var dentalSegmentatorModelDetailText: String {
        if isDentalSegmentatorModelReady {
            return paths.dentalsegInstalledModel.path
        }
        return "DentalSegmentatorを初めて選んだときに追加モデルを準備します。"
    }

    var selectedModelStatusText: String {
        if segmentationBackend == .dentalSegmentator {
            return dentalSegmentatorModelStatusText
        }
        if segmentationBackend == .toothSeg {
            return toothSegModelStatusText
        }
        return FileManager.default.fileExists(atPath: paths.totalsegBinary.path) ? "セットアップ済み" : "セットアップ未完了"
    }

    var selectedModelDetailText: String {
        if segmentationBackend == .dentalSegmentator {
            return dentalSegmentatorModelDetailText
        }
        if segmentationBackend == .toothSeg {
            return toothSegModelDetailText
        }
        return "TotalSegmentatorモデルをApp Support配下から使います。"
    }

    var selectedOutputRootURL: URL {
        outputRootURL ?? paths.runs
    }

    var runReadinessItems: [RunReadinessItem] {
        let inputSelected = inputURL != nil && (inputSource == .sample || inputSource == .nifti)
        let modelBlocked = (segmentationBackend == .dentalSegmentator && !isDentalSegmentatorModelReady)
            || (segmentationBackend == .toothSeg && !isToothSegModelReady)
        let taskBlocked = segmentationBackend == .dentalSegmentator && runMode == .individualTeeth
        let deviceValue = "mps（固定）"
        return [
            RunReadinessItem(
                id: "input",
                title: "入力",
                value: inputURL?.lastPathComponent ?? "未選択",
                detail: inputSelected ? (inputURL?.path ?? "") : "SampleまたはCTファイルを選んでください。",
                systemImage: "doc.viewfinder",
                state: inputSelected ? "ok" : "blocked"
            ),
            RunReadinessItem(
                id: "backend",
                title: "Backend",
                value: segmentationBackend.rawValue,
                detail: segmentationBackend == .totalSegmentator ? "既定のTotalSegmentator backendです。" : "明示opt-inの実験的backendです。別モデルへ自動fallbackしません。",
                systemImage: "switch.2",
                state: "ok"
            ),
            RunReadinessItem(
                id: "task",
                title: "Task",
                value: runMode.rawValue,
                detail: "CLI task: \(runMode.task)",
                systemImage: taskBlocked ? "exclamationmark.triangle" : "list.bullet.rectangle",
                state: taskBlocked ? "blocked" : "ok"
            ),
            RunReadinessItem(
                id: "device",
                title: "Device",
                value: deviceValue,
                detail: segmentationBackend == .totalSegmentator ? "TotalSegmentatorへ渡す処理方法です。" : "MPS固定。CPUへ自動切替しません。",
                systemImage: "cpu",
                state: "ok"
            ),
            RunReadinessItem(
                id: "model",
                title: "Model",
                value: selectedModelStatusText,
                detail: selectedModelDetailText,
                systemImage: modelBlocked ? "exclamationmark.triangle" : "shippingbox",
                state: modelBlocked ? "blocked" : "ok"
            ),
            RunReadinessItem(
                id: "output",
                title: "保存先",
                value: selectedOutputRootURL.lastPathComponent,
                detail: selectedOutputRootURL.path,
                systemImage: "folder",
                state: "ok"
            ),
            RunReadinessItem(
                id: "log",
                title: "ログ",
                value: "実行ごとの logs/run.log",
                detail: "実行開始後、出力caseフォルダ内に記録します。",
                systemImage: "terminal",
                state: "info"
            ),
        ]
    }

    var activeRunContextItems: [RunReadinessItem] {
        [
            RunReadinessItem(
                id: "backend",
                title: "Backend",
                value: activeRunBackend.rawValue,
                detail: activeRunBackend == .totalSegmentator ? "TotalSegmentator backendで実行中です。" : "MPSで実行中。CPUや別モデルへ自動切替しません。",
                systemImage: "switch.2",
                state: "ok"
            ),
            RunReadinessItem(
                id: "task",
                title: "Task",
                value: activeRunTaskText,
                detail: "研究・教育目的の非臨床3D previewを作成しています。",
                systemImage: "list.bullet.rectangle",
                state: "ok"
            ),
            RunReadinessItem(
                id: "device",
                title: "Device",
                value: activeRunDeviceText,
                detail: "実行コマンドと詳細ログにも記録されます。",
                systemImage: "cpu",
                state: "ok"
            ),
            RunReadinessItem(
                id: "output",
                title: "出力先",
                value: outputURL?.lastPathComponent ?? "未作成",
                detail: outputURL?.path ?? "出力caseフォルダを準備しています。",
                systemImage: "folder",
                state: "info"
            ),
            RunReadinessItem(
                id: "log",
                title: "ログ",
                value: currentLogURL.lastPathComponent,
                detail: currentLogURL.path,
                systemImage: "terminal",
                state: "info"
            ),
        ]
    }

    var resultLocationItems: [RunLocationItem] {
        guard resultKind == .inference, let outputURL else {
            return []
        }
        let activeLabelmap = expectedResultLabelmapURL(caseDir: outputURL, flavor: activeResultFlavor)
        let secondaryLabelmap = alternativeResultLabelmapURL(
            caseDir: outputURL,
            forFlavor: activeResultFlavor
        )
        let preview = expectedSurfacePreviewURL(caseDir: outputURL, flavor: activeResultFlavor)
        let stlDirectory = expectedSTLDirectoryURL(caseDir: outputURL, flavor: activeResultFlavor)
        let log = activeResultFlavor == .toothSeg
            ? outputURL.appendingPathComponent("logs/toothseg_refine/run.log")
            : outputURL.appendingPathComponent("logs/run.log")
        var items: [RunLocationItem] = [
            RunLocationItem(
                id: "output",
                title: "結果フォルダ",
                path: outputURL.path,
                detail: "case全体の出力先です。",
                systemImage: "folder",
                exists: FileManager.default.fileExists(atPath: outputURL.path)
            ),
            RunLocationItem(
                id: "active_labelmap",
                title: "3D preview用labelmap",
                path: activeLabelmap.path,
                detail: "現在表示対象: \(activeResultFlavor.title)",
                systemImage: "square.stack.3d.up",
                exists: FileManager.default.fileExists(atPath: activeLabelmap.path)
            ),
            RunLocationItem(
                id: "preview",
                title: "3D preview",
                path: preview.path,
                detail: "ブラウザで開くoffline HTML viewerです。",
                systemImage: "cube.transparent",
                exists: FileManager.default.fileExists(atPath: preview.path)
            ),
            RunLocationItem(
                id: "stl",
                title: "STLフォルダ",
                path: stlDirectory.path,
                detail: stlGenerationStatusText,
                systemImage: "folder.badge.gearshape",
                exists: FileManager.default.fileExists(atPath: stlDirectory.path)
            ),
            RunLocationItem(
                id: "log",
                title: "実行ログ",
                path: log.path,
                detail: "backend、device、失敗理由の確認に使います。",
                systemImage: "terminal",
                exists: FileManager.default.fileExists(atPath: log.path)
            ),
        ]
        if let secondaryLabelmap {
            items.append(
                RunLocationItem(
                    id: "alternative_labelmap",
                    title: "代替labelmap",
                    path: secondaryLabelmap.path,
                    detail: secondaryResultLabelDescription(current: activeResultFlavor),
                    systemImage: "square.stack.3d.up.fill",
                    exists: FileManager.default.fileExists(atPath: secondaryLabelmap.path)
                )
            )
        }
        return items
    }

    var canRegenerateSurfacePreview: Bool {
        !isRunning && resultKind == .inference && outputURL != nil
    }

    var canExportForSlicer: Bool {
        !isRunning && resultKind == .inference && outputURL != nil
    }

    var availableResultFlavors: [ResultOutputFlavor] {
        guard let outputURL else { return [] }
        var flavors: [ResultOutputFlavor] = [.craniofacial]
        let craniofacial = expectedResultLabelmapURL(caseDir: outputURL, flavor: .craniofacial)
        if FileManager.default.fileExists(atPath: craniofacial.path) {
            if !flavors.contains(.craniofacial) { flavors.append(.craniofacial) }
        } else {
            flavors = flavors.filter { $0 != .craniofacial }
        }
        let toothSeg = expectedResultLabelmapURL(caseDir: outputURL, flavor: .toothSeg)
        if FileManager.default.fileExists(atPath: toothSeg.path), !flavors.contains(.toothSeg) {
            flavors.append(.toothSeg)
        }
        if flavors.isEmpty {
            flavors = [.craniofacial]
        }
        return flavors
    }

    var canSwitchResultFlavor: Bool {
        availableResultFlavors.count > 1
    }

    private func alternativeResultLabelmapURL(caseDir: URL, forFlavor flavor: ResultOutputFlavor) -> URL? {
        let alternativeFlavor: ResultOutputFlavor = flavor == .craniofacial ? .toothSeg : .craniofacial
        let candidate = expectedResultLabelmapURL(caseDir: caseDir, flavor: alternativeFlavor)
        guard FileManager.default.fileExists(atPath: candidate.path) else {
            return nil
        }
        return candidate
    }

    private func secondaryResultLabelDescription(current: ResultOutputFlavor) -> String {
        switch current {
        case .craniofacial:
            return "高精細ToothSeg（未表示）"
        case .toothSeg:
            return "歯列・顎骨"
        }
    }

    var canShowToothSegRefine: Bool {
        toothSegRefinePrerequisitesMet && !toothSegRefineFailed && !isToothSegRefineSuccessful
    }

    var canRunToothSegRefineAction: Bool {
        canShowToothSegRefine
    }

    var canRetryToothSegRefine: Bool {
        toothSegRefinePrerequisitesMet && toothSegRefineFailed
    }

    private var toothSegRefinePrerequisitesMet: Bool {
        resultOutcome == .success && !isRunning && primaryRunBackend == .totalSegmentator && primaryRunTeethDetected
    }

    private var isToothSegRefineSuccessful: Bool {
        guard !toothSegRefineFailed else { return false }
        if isUIPreviewMode && activeResultFlavor == .toothSeg {
            return true
        }
        guard let outputURL else { return false }
        let labelmap = expectedResultLabelmapURL(caseDir: outputURL, flavor: .toothSeg)
        return FileManager.default.fileExists(atPath: labelmap.path)
    }

    var isToothSegRefinePreparation: Bool {
        modelPreparationPurpose == .toothSegRefine
    }

    var canUseSelectedDicomSeries: Bool {
        !isRunning && resultKind == .dicomAudit && lastDicomDirURL != nil
            && pendingDicomSeriesID != nil
    }

    var selectedDicomSeries: CleanDicomSeriesCandidate? {
        guard let selectedDicomSeriesID else { return nil }
        return dicomCleanCandidates.first(where: { $0.id == selectedDicomSeriesID })
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

    var safeErrorCopyText: String {
        let reason = safeErrorReason.isEmpty ? "The requested operation did not complete." : safeErrorReason
        let code = safeErrorCode.isEmpty ? "operation_failed" : safeErrorCode
        let occurredAt = safeErrorOccurredAt.isEmpty ? ISO8601DateFormatter().string(from: Date()) : safeErrorOccurredAt
        return "app_version=\(currentAppVersion())\nfeature=\(safeErrorFeatureText)\nmps_state=\(safeMPSState)\nreason=\(reason)\ntimestamp=\(occurredAt)\nerror_code=\(code)"
    }

    var safeErrorFeatureText: String {
        if toothSegRefineFailed || safeErrorCode.hasPrefix("toothseg_") {
            return "ToothSeg高精細化"
        }
        return creationChoice.rawValue
    }

    init(paths: AppPaths = .current()) {
        self.paths = paths
        createRuntimeDirectories(paths: paths)
        restoreUserSettings()
        if outputRootURL == nil {
            outputRootURL = paths.runs
        }
        if FileManager.default.fileExists(atPath: paths.sampleInput.path) {
            inputURL = paths.sampleInput
            inputSource = .sample
        }
        refreshLaunchState()
    }

#if DEBUG
    func applyUIPreview(arguments: [String] = CommandLine.arguments) {
        guard let flagIndex = arguments.firstIndex(of: "--ui-preview"),
              arguments.indices.contains(flagIndex + 1) else {
            return
        }
        let scenario = arguments[flagIndex + 1]
        let supported = [
            "setup", "start", "input", "input-advanced", "input-comparison",
            "input-dicom-preview", "running",
            "running-known", "running-unknown", "running-download", "running-stopped",
            "ct-preview", "dicom-rescue", "dicom-rescue-updating", "result", "result-toothseg",
            "result-toothseg-failure", "result-failure",
        ]
        guard supported.contains(scenario) else {
            return
        }

        uiPreviewScenario = scenario
        inputURL = paths.sampleInput
        inputSource = .sample
        creationChoice = .standardArchJaw
        outputURL = paths.runs.appendingPathComponent("ui_preview_case", isDirectory: true)
        statusText = "UIプレビュー"
        progressText = "画面確認用の表示です。処理は実行しません。"
        resultKind = .none
        resultOutcome = .none
        isRunning = false
        runProgressFraction = nil
        failureReasonText = ""
        primaryRunBackend = .totalSegmentator
        primaryRunMode = .archPreview
        primaryRunTeethDetected = false
        canRunToothSegRefine = false
        activeResultFlavor = .craniofacial

        switch scenario {
        case "setup":
            screen = .setup
            selectedStep = 0
            setupMessage = "このMacに必要な機能を準備します。"
        case "start":
            screen = .start
            selectedStep = 0
        case "input", "input-advanced", "input-comparison", "input-dicom-preview":
            screen = .inputAndCreation
            selectedStep = 1
            statusText = "プレビュー作成準備完了"
            if scenario == "input-advanced" {
                creationChoice = .individualTeethBeta
            }
            if scenario == "input-dicom-preview" {
                inputSource = .nifti
                inputCTPreviewRequired = true
                let fixtureRoot = URL(
                    fileURLWithPath: FileManager.default.currentDirectoryPath,
                    isDirectory: true
                ).appendingPathComponent(
                    "artifacts/ui-transition-map/fixtures",
                    isDirectory: true
                )
                inputCTPreviewSlices = [
                    CTPreviewSlice(
                        plane: "axial", label: "上から",
                        url: fixtureRoot.appendingPathComponent("axial.pgm"),
                        width: 230, height: 220,
                        rowSpacingMM: nil, columnSpacingMM: nil,
                        minValue: 0, maxValue: 255, uniformOrEmpty: false
                    ),
                    CTPreviewSlice(
                        plane: "coronal", label: "正面から",
                        url: fixtureRoot.appendingPathComponent("coronal.pgm"),
                        width: 230, height: 160,
                        rowSpacingMM: nil, columnSpacingMM: nil,
                        minValue: 0, maxValue: 255, uniformOrEmpty: false
                    ),
                    CTPreviewSlice(
                        plane: "sagittal", label: "横から",
                        url: fixtureRoot.appendingPathComponent("sagittal.pgm"),
                        width: 220, height: 160,
                        rowSpacingMM: nil, columnSpacingMM: nil,
                        minValue: 0, maxValue: 255, uniformOrEmpty: false
                    ),
                ]
            }
        case "running", "running-known", "running-unknown", "running-download", "running-stopped":
            screen = .running
            selectedStep = 2
            isRunning = true
            statusText = "歯列・顎骨を作成中"
            progressText = "TotalSegmentatorでCTデータを処理しています。"
            runElapsed = "経過時間: 11分42秒"
            runStageStartedAt = Date().addingTimeInterval(-161)
            if scenario == "running-known" {
                activeRunBackend = .toothSeg
                runStageEvent = RunStageEvent(
                    route: "toothseg_refine", stageID: "instance", index: 3,
                    total: 5, label: "ToothSeg instance枝"
                )
                runStageProgress = RunLogProgress(
                    step: 40, total: 80, percent: 50, stage: "ToothSeg instance",
                    etaSeconds: 613, route: "toothseg_refine", stageID: "instance", scope: "stage"
                )
                runHeartbeatText = "進捗ログを受信しました。"
            } else {
                runStageEvent = RunStageEvent(
                    route: "totalsegmentator", stageID: "segment", index: 2,
                    total: 4, label: "顎顔面を抽出中"
                )
                if scenario == "running-download" {
                    runStageProgress = RunLogProgress(
                        step: 58, total: 100, percent: 58, stage: "Downloading weights",
                        etaSeconds: 60, route: "totalsegmentator", stageID: "segment", scope: "subtask"
                    )
                }
                runHeartbeatText = scenario == "running-stopped"
                    ? "最終更新: 48秒前。大きなデータではこの待ち時間が発生します。"
                    : "処理を継続しています。"
                stopRequested = scenario == "running-stopped"
            }
        case "ct-preview":
            screen = .ctPreview
            selectedStep = 1
            statusText = "CT画像を確認"
        case "dicom-rescue", "dicom-rescue-updating":
            screen = .dicomRescue
            selectedStep = 1
            statusText = "形状を確認"
            rescueWorkflowState = .editableReady
            rescueEstimatedSpacing = RescueSpacing(x: 0.72, y: 0.72, z: 0.9375)
            rescueSpacingX = rescueEstimatedSpacing.x
            rescueSpacingY = rescueEstimatedSpacing.y
            rescueSpacingZ = rescueEstimatedSpacing.z
            rescueConfidence = "低"
            rescueEvidence = [
                "Slice Thicknessを低信頼のZ候補に使用",
                "三方向系列の範囲を比較してX/Y候補を作成",
            ]
            rescuePreviewShapeXYZ = [230, 220, 160]
            rescuePreviewStatus = "preview更新済み（AI推論は開始していません）"
            let sourceRepositoryRoot = URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
            let fixtureRoots = [
                ProcessInfo.processInfo.environment["TOTALSEGMENTATOR_WRAPPER_MAC_UI_PREVIEW_FIXTURE_DIR"]
                    .map { URL(fileURLWithPath: $0, isDirectory: true) },
                URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
                    .appendingPathComponent("artifacts/ui-transition-map/fixtures", isDirectory: true),
                sourceRepositoryRoot
                    .appendingPathComponent("artifacts/ui-transition-map/fixtures", isDirectory: true),
            ].compactMap { $0 }
            let fixtureRoot = fixtureRoots.first {
                FileManager.default.fileExists(
                    atPath: $0.appendingPathComponent("axial.pgm").path
                )
            } ?? fixtureRoots[0]
            rescueMPRPreviewSlices = [
                CTPreviewSlice(
                    plane: "axial", label: "AXIAL",
                    url: fixtureRoot.appendingPathComponent("axial.pgm"),
                    width: 230, height: 220,
                    rowSpacingMM: nil, columnSpacingMM: nil,
                    minValue: 0, maxValue: 255, uniformOrEmpty: false
                ),
                CTPreviewSlice(
                    plane: "coronal", label: "CORONAL",
                    url: fixtureRoot.appendingPathComponent("coronal.pgm"),
                    width: 230, height: 160,
                    rowSpacingMM: nil, columnSpacingMM: nil,
                    minValue: 0, maxValue: 255, uniformOrEmpty: false
                ),
                CTPreviewSlice(
                    plane: "sagittal", label: "SAGITTAL",
                    url: fixtureRoot.appendingPathComponent("sagittal.pgm"),
                    width: 220, height: 160,
                    rowSpacingMM: nil, columnSpacingMM: nil,
                    minValue: 0, maxValue: 255, uniformOrEmpty: false
                ),
            ]
            rescueInlineWarning = ""
            rescuePreviewRevision = 0
            if scenario == "dicom-rescue-updating" {
                isRunning = true
                rescuePreviewStatus = "画像の向きを更新しています（AI推論なし）"
            }
        case "result", "result-toothseg", "result-toothseg-failure":
            screen = .result
            selectedStep = 3
            resultKind = .inference
            resultOutcome = .success
            resultMessage = "3Dプレビューを作成しました"
            statusText = "作成完了"
            primaryRunTeethDetected = true
            canRunToothSegRefine = true
            if scenario == "result-toothseg" {
                activeResultFlavor = .toothSeg
            } else if scenario == "result-toothseg-failure" {
                toothSegRefineFailed = true
                failureReasonText = "MPSメモリ不足（Out Of Memory）で失敗しました。他のアプリを終了するかMacを再起動してから再試行してください。"
                statusText = "高精細化を完了できませんでした"
                resultMessage = "ToothSeg高精細化に失敗しました"
            }
        case "result-failure":
            screen = .result
            selectedStep = 3
            resultKind = .inference
            resultOutcome = .failure
            statusText = "処理を完了できませんでした"
            failureReasonText = "MPSのメモリが不足しました。入力データは変更されていません。"
        default:
            break
        }
    }
#endif

    deinit {
        logTimer?.invalidate()
        dentalPreparationTimer?.invalidate()
        stlStatusTimer?.invalidate()
    }

    private func restoreUserSettings() {
        let defaults = UserDefaults.standard
        if let rawChoice = defaults.string(forKey: UserSettingKey.creationChoice),
           let restoredChoice = CreationChoice(rawValue: rawChoice) {
            creationChoice = restoredChoice == .toothSegExperimental ? .standardArchJaw : restoredChoice
        } else if defaults.string(forKey: UserSettingKey.segmentationBackend) == SegmentationBackend.dentalSegmentator.rawValue {
            creationChoice = .dentalSegmentatorExperimental
        } else if defaults.string(forKey: UserSettingKey.segmentationBackend) == SegmentationBackend.toothSeg.rawValue {
            creationChoice = .standardArchJaw
        } else if defaults.string(forKey: UserSettingKey.runMode) == RunMode.individualTeeth.rawValue {
            creationChoice = .individualTeethBeta
        } else {
            creationChoice = .standardArchJaw
        }
        device = "mps"
        if defaults.object(forKey: UserSettingKey.higherOrderResampling) != nil {
            higherOrderResampling = defaults.bool(forKey: UserSettingKey.higherOrderResampling)
        }
        if let outputPath = defaults.string(forKey: UserSettingKey.outputRootURL), !outputPath.isEmpty {
            let restoredOutput = URL(fileURLWithPath: outputPath, isDirectory: true).standardizedFileURL
            outputRootURL = isDirectory(restoredOutput) ? restoredOutput : paths.runs
        } else {
            outputRootURL = paths.runs
        }
    }

    private func saveUserSettings() {
        guard !isUIPreviewMode else { return }
        let defaults = UserDefaults.standard
        defaults.set(creationChoice.rawValue, forKey: UserSettingKey.creationChoice)
        defaults.set(runMode.rawValue, forKey: UserSettingKey.runMode)
        defaults.set(segmentationBackend.rawValue, forKey: UserSettingKey.segmentationBackend)
        defaults.set("mps", forKey: UserSettingKey.device)
        defaults.set(higherOrderResampling, forKey: UserSettingKey.higherOrderResampling)
        if let outputRootURL {
            defaults.set(outputRootURL.path, forKey: UserSettingKey.outputRootURL)
        } else {
            defaults.removeObject(forKey: UserSettingKey.outputRootURL)
        }
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
            setupMessage = "このアプリを使う準備は完了しています。追加モデルは初めて選んだときに準備します。"
        } else if status.action == "resync_wheel" {
            screen = .setup
            setupMessage = "アプリ更新の反映が必要です。準備を始めるまで通信しません。"
            setupError = ""
        } else {
            screen = .setup
            setupMessage = "はじめの準備が必要です。準備を始めるまで通信しません。"
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
                    self?.setupMessage = "このアプリを使う準備が完了しました。"
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
        clearInputCTPreview()
        inputURL = paths.sampleInput
        inputSource = .sample
        outputURL = nil
        runMode = .archPreview
        statusText = "Sample 1を入力に設定しました。"
        progressText = "Sample 1で3Dプレビューを作成できます。手元のCTデータには触れません。"
        runHeartbeatText = ""
        runProgressFraction = nil
        failureReasonText = ""
        resultOutcome = .none
        dicomCleanCandidates = []
        selectedDicomSeriesID = nil
        pendingDicomSeriesID = nil
        dicomSelectionWasChanged = false
        screen = .inputAndCreation
        selectedStep = 1
    }

    func chooseCTInput() {
        let panel = NSOpenPanel()
        panel.title = "CTを選択"
        panel.canChooseFiles = true
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            clearInputCTPreview()
            if isDirectory(url) {
                inputURL = url
                inputSource = .dicomFolder
                runDicomAudit(dicomDir: url)
            } else {
                prepareNiftiInput(url)
            }
        }
    }

    func chooseAnotherCTFromRescue() {
        guard !isRunning else { return }
        let panel = NSOpenPanel()
        panel.title = "別のCTを選択"
        panel.canChooseFiles = true
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else {
            return
        }
        resetSecondaryCaptureRescue()
        clearPendingCTPreview()
        clearInputCTPreview()
        if isDirectory(url) {
            inputURL = url
            inputSource = .dicomFolder
            runDicomAudit(dicomDir: url)
        } else {
            prepareNiftiInput(url)
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
        clearInputCTPreview()
        inputURL = url
        inputSource = .nifti
        outputURL = nil
        resultKind = .none
        dicomSummaryText = ""
        summaryText = ""
        resultMessage = ""
        failureReasonText = ""
        resultOutcome = .none
        dicomCleanCandidates = []
        selectedDicomSeriesID = nil
        pendingDicomSeriesID = nil
        dicomSelectionWasChanged = false
        screen = .inputAndCreation
        selectedStep = 1
        statusText = "プレビュー作成準備完了"
        progressText = "CTを入力に設定しました。3Dプレビューを作成できます。"
    }

    func requestCreationChoice(_ choice: CreationChoice) {
        let requiresPreparation = (choice == .dentalSegmentatorExperimental && !isDentalSegmentatorModelReady)
            || (choice == .toothSegExperimental && !isToothSegModelReady)
        guard requiresPreparation else {
            creationChoice = choice
            return
        }
        modelPreparationPurpose = .creationSelection
        pendingModelPreparationChoice = choice
        showDentalPreparationConfirmation = true
    }

    func requestToothSegRefine() {
        guard canShowToothSegRefine || canRetryToothSegRefine else { return }
        if isToothSegModelReady {
            startToothSegRefineRun()
            return
        }
        modelPreparationPurpose = .toothSegRefine
        pendingModelPreparationChoice = .toothSegExperimental
        showDentalPreparationConfirmation = true
    }

    func cancelModelPreparationConfirmation() {
        showDentalPreparationConfirmation = false
        if modelPreparationPurpose == .creationSelection {
            creationChoice = .standardArchJaw
        }
    }

    func confirmDentalPreparation() {
        showDentalPreparationConfirmation = false
        showDentalPreparationSheet = true
        dentalPreparationRunning = true
        dentalPreparationStartedAt = Date()
        dentalPreparationElapsed = formatElapsed(0)
        dentalPreparationFraction = nil
        dentalPreparationDetail = ""
        let pendingChoice = pendingModelPreparationChoice
        let modelName = pendingChoice == .toothSegExperimental ? "ToothSeg" : "DentalSegmentator"
        dentalPreparationMessage = "\(modelName)のモデルを準備しています。"
        dentalPreparationCancellationRequested = false
        dentalPreparationRunner.resetTerminationRequest()
        startDentalPreparationTimer()
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let runner = dentalPreparationRunner
        let python = paths.venvPython
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let activePaths = self?.paths ?? AppPaths.current()
            let statusCommand = pendingChoice == .toothSegExperimental
                ? CommandBuilder.toothsegStatusCommand(python: python, paths: activePaths)
                : CommandBuilder.dentalsegStatusCommand(python: python, paths: activePaths)
            _ = runner.run(statusCommand, environment: environment, logURL: nil)
            let prepareCommand = pendingChoice == .toothSegExperimental
                ? CommandBuilder.toothsegPrepareCommand(python: python, paths: activePaths)
                : CommandBuilder.dentalsegPrepareCommand(python: python, paths: activePaths)
            let preparationLog = pendingChoice == .toothSegExperimental
                ? self?.paths.toothsegPrepareLog
                : self?.paths.dentalsegPrepareLog
            let rc = runner.run(prepareCommand, environment: environment, logURL: preparationLog)
            DispatchQueue.main.async {
                guard let self else { return }
                self.dentalPreparationRunning = false
                self.dentalPreparationTimer?.invalidate()
                self.dentalPreparationTimer = nil
                self.dentalPreparationElapsed = formatElapsed(self.dentalPreparationStartedAt.map { Date().timeIntervalSince($0) } ?? 0)
                if self.dentalPreparationCancellationRequested {
                    self.dentalPreparationCancellationRequested = false
                    self.dentalPreparationMessage = "モデル準備をキャンセルしました。"
                    if self.modelPreparationPurpose == .creationSelection {
                        self.creationChoice = .standardArchJaw
                    } else {
                        self.resultOutcome = .success
                        self.statusText = "ToothSegモデル準備をキャンセルしました"
                        self.progressText = "元の歯列・顎骨結果は引き続き利用できます。"
                    }
                    return
                }
                let resultURL = pendingChoice == .toothSegExperimental
                    ? self.paths.toothsegPrepareResultJSON
                    : self.paths.dentalsegPrepareResultJSON
                let result = readJSON(resultURL)
                let modelReady = pendingChoice == .toothSegExperimental
                    ? self.isToothSegModelReady
                    : self.isDentalSegmentatorModelReady
                if rc == 0 && result?["model_state"] as? String == "ready" && modelReady {
                    self.dentalPreparationMessage = "\(modelName)のモデルを準備しました。"
                    self.dentalPreparationFraction = 1.0
                    self.dentalPreparationDetail = ""
                    if self.modelPreparationPurpose == .creationSelection {
                        self.creationChoice = pendingChoice
                    } else {
                        self.resultOutcome = .success
                        self.toothSegRefineFailed = false
                        self.failureReasonText = ""
                        self.statusText = "ToothSegモデル準備完了"
                        self.progressText = "結果画面のボタンをもう一度押すと高精細歯分割を開始します。"
                        self.resultMessage = "元の歯列・顎骨結果は引き続き利用できます"
                    }
                } else {
                    if self.modelPreparationPurpose == .creationSelection {
                        self.dentalPreparationMessage = "モデルを準備できませんでした。標準の選択に戻します。"
                        self.creationChoice = .standardArchJaw
                    } else {
                        self.dentalPreparationMessage = "ToothSegモデルを準備できませんでした。"
                        self.resultOutcome = .success
                        self.toothSegRefineFailed = true
                        self.failureReasonText = "ToothSegモデルのダウンロードまたは検証に失敗しました。ネットワーク状態を確認して再試行してください。"
                        self.statusText = "ToothSegモデルを準備できませんでした"
                        self.progressText = "元の歯列・顎骨結果は引き続き利用できます。"
                        self.resultMessage = "ToothSegモデル準備に失敗しました"
                        self.setSafeError(
                            code: "toothseg_model_preparation_failed",
                            reason: "The ToothSeg model download or validation did not complete.",
                            mpsState: "unknown"
                        )
                    }
                }
            }
        }
    }

    func cancelDentalPreparation() {
        guard dentalPreparationRunning else {
            showDentalPreparationSheet = false
            if modelPreparationPurpose == .creationSelection {
                creationChoice = .standardArchJaw
            }
            return
        }
        dentalPreparationCancellationRequested = true
        dentalPreparationRunner.terminate(graceSeconds: 2.0)
        dentalPreparationTimer?.invalidate()
        dentalPreparationTimer = nil
        dentalPreparationMessage = "モデル準備を終了しています。"
        dentalPreparationFraction = nil
        showDentalPreparationSheet = false
        if modelPreparationPurpose == .creationSelection {
            creationChoice = .standardArchJaw
        } else {
            resultOutcome = .success
            statusText = "ToothSegモデル準備をキャンセルしました"
            progressText = "元の歯列・顎骨結果は引き続き利用できます。"
        }
    }

    private func startDentalPreparationTimer() {
        dentalPreparationTimer?.invalidate()
        dentalPreparationTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self, let started = self.dentalPreparationStartedAt else { return }
            self.dentalPreparationElapsed = formatElapsed(Date().timeIntervalSince(started))
            if self.pendingModelPreparationChoice == .toothSegExperimental,
               let snapshot = readLogTail(self.paths.toothsegPrepareLog, maxBytes: LOG_TAIL_BYTES),
               let progress = toothSegPreparationProgressFromLog(snapshot.text) {
                self.dentalPreparationFraction = progress.fraction
                self.dentalPreparationMessage = progress.message
                self.dentalPreparationDetail = progress.detailText
            }
        }
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
            statusText = "はじめの準備が必要です。"
            screen = .setup
            return
        }
        let auditDir = paths.runs.appendingPathComponent("dicom_audit_\(Int(Date().timeIntervalSince1970))", isDirectory: true)
        let auditJSON = auditDir.appendingPathComponent("dicom_normalizer_audit.json")
        try? FileManager.default.createDirectory(at: auditDir, withIntermediateDirectories: true)
        clearInputCTPreview()
        inputURL = dicomDir
        inputSource = .dicomFolder
        lastDicomDirURL = dicomDir
        dicomCleanCandidates = []
        selectedDicomSeriesID = nil
        pendingDicomSeriesID = nil
        dicomSelectionWasChanged = false
        dicomViewerExportCandidates = []
        selectedViewerExportCandidateID = nil
        resetSecondaryCaptureRescue()
        clearPendingCTPreview()
        logText = ""
        dicomSummaryText = ""
        summaryText = ""
        failureReasonText = ""
        resultKind = .dicomAudit
        statusText = "CT確認中"
        progressText = "撮影データの種類を確認しています。プレビュー作成はまだ開始していません。"
        resetRunProgressTracking()
        beginStructuredRunProgress(route: progressRoute(backend: segmentationBackend, mode: runMode))
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
            let rescueCandidates = rc == 0 ? secondaryCaptureRescueCandidates(auditJSON: auditJSON) : []
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
                self?.pendingDicomSeriesID = nil
                self?.dicomSelectionWasChanged = false
                self?.dicomViewerExportCandidates = viewerExportCandidates
                self?.selectedViewerExportCandidateID = viewerExportCandidates.first?.id
                self?.dicomRescueCandidates = rescueCandidates
                self?.selectedDicomRescueCandidateID = rescueCandidates.first(where: { $0.role == "primary" })?.id
                    ?? rescueCandidates.first?.id
                if stopped {
                    self?.resultOutcome = .failure
                    self?.failureReasonText = "撮影データの確認を停止しました。"
                    self?.setSafeError(code: "dicom_audit_cancelled", reason: "The CT data check was cancelled.", mpsState: "not_applicable")
                    self?.screen = .result
                    self?.selectedStep = 3
                    self?.statusText = "停止しました"
                    self?.progressText = "撮影データの確認を停止しました。"
                    self?.resultMessage = "撮影データの確認を停止しました。入力は変更されていません。"
                } else if rc == 0, let candidate = cleanCandidates.first {
                    self?.startDicomCleanConversion(dicomDir: dicomDir, candidate: candidate)
                } else if rc == 0 && !rescueCandidates.isEmpty {
                    self?.beginSecondaryCaptureRescue(candidates: rescueCandidates)
                } else if rc == 0 && !viewerExportCandidates.isEmpty {
                    self?.resultOutcome = .none
                    self?.screen = .result
                    self?.selectedStep = 1
                    self?.statusText = "表示用断面画像の可能性があります"
                    self?.progressText = "CTを見るソフトから書き出された断面群を確認できます。プレビュー作成はまだ開始していません。"
                    self?.resultMessage = "CTを見るソフトから「表示用の断面画像」として書き出されたデータの可能性があります。断面群を確認して、3Dプレビューに進めるか判断します。"
                    self?.outputURL = auditDir
                } else {
                    self?.resultOutcome = .failure
                    self?.failureReasonText = "撮影データを確認できませんでした。"
                    self?.setSafeError(code: "dicom_audit_failed", reason: "The CT data could not be prepared for preview.", mpsState: "not_applicable")
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
              let pendingDicomSeriesID,
              let candidate = dicomCleanCandidates.first(where: { $0.id == pendingDicomSeriesID }) else {
            resultMessage = "取り込める通常CT候補が見つかりません。CT確認結果を確認してください。"
            return
        }
        selectedDicomSeriesID = candidate.id
        dicomSelectionWasChanged = candidate.id != dicomCleanCandidates.first?.id
        self.pendingDicomSeriesID = nil
        showDicomSeriesSelection = false
        clearInputCTPreview()
        startDicomCleanConversion(dicomDir: dicomDir, candidate: candidate)
    }

    func beginDicomSeriesSelection() {
        pendingDicomSeriesID = selectedDicomSeriesID
        showDicomSeriesSelection = true
    }

    func cancelDicomSeriesSelection() {
        pendingDicomSeriesID = nil
        showDicomSeriesSelection = false
    }

    func selectDicomSeries(_ candidate: CleanDicomSeriesCandidate) {
        pendingDicomSeriesID = candidate.id
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

    var selectedDicomRescueCandidate: SecondaryCaptureRescueCandidate? {
        guard let selectedDicomRescueCandidateID else { return nil }
        return dicomRescueCandidates.first(where: { $0.id == selectedDicomRescueCandidateID })
    }

    var currentRescueSpacing: RescueSpacing {
        RescueSpacing(x: rescueSpacingX, y: rescueSpacingY, z: rescueSpacingZ)
    }

    var rescueCropIsValid: Bool {
        rescueCropMinX >= 0 && rescueCropMinY >= 0 && rescueCropMinZ >= 0
            && rescueCropMaxX > rescueCropMinX
            && rescueCropMaxY > rescueCropMinY
            && rescueCropMaxZ > rescueCropMinZ
    }

    var rescueUsesNonIdentityTransform: Bool {
        guard let candidate = selectedDicomRescueCandidate else { return true }
        return rescueAxisPermutation != .xyz
            || rescueRotationQuarterTurns != 0
            || rescueSliceOrderReversed
            || rescueCropMinX != 0
            || rescueCropMinY != 0
            || rescueCropMinZ != 0
            || rescueCropMaxX != max(candidate.columns, 1)
            || rescueCropMaxY != max(candidate.rows, 1)
            || rescueCropMaxZ != max(candidate.fileCount, 1)
    }

    var canConfirmSecondaryCaptureRescue: Bool {
        !isRunning
            && selectedDicomRescueCandidate != nil
            && currentRescueSpacing.isValid
            && rescueCropIsValid
            && canFinalizeRescueTransform
    }

    var canRequestRescuePreview: Bool {
        !isRunning
            && rescueDecodedVolumeURL != nil
            && rescueGeometryJSONURL != nil
            && !rescueSourceManifestSHA256.isEmpty
            && currentRescueSpacing.isValid
            && rescueCropIsValid
    }

    var canFinalizeRescueTransform: Bool {
        rescueDecodedVolumeURL != nil
            && rescueGeometryJSONURL != nil
            && rescueConfirmationToken.count == 64
            && rescueConfirmationToken.allSatisfy { $0.isHexDigit }
    }

    var rescueConfirmationUnavailableReason: String? {
        guard !canConfirmSecondaryCaptureRescue else { return nil }
        if isRunning {
            return "画像を更新しています。完了すると作成できます。"
        }
        if rescueImageUpdateFailed {
            return "画像の更新に失敗しました。もう一度調整するか、別のCTを選んでください。"
        }
        if !rescueMPRPreviewSlices.isEmpty {
            return "三方向の画像を確認すると作成できます。"
        }
        if selectedDicomRescueCandidate == nil {
            return "使用する画像を選ぶと作成できます。"
        }
        return "三方向の画像を確認すると作成できます。"
    }

    var rescueConfidenceDisplayText: String {
        switch rescueConfidence.lowercased() {
        case "high", "高": return "高"
        case "medium", "moderate", "中": return "中"
        case "low", "低": return "低"
        case "unknown", "unavailable", "未推定（仮の初期値を含む）": return "未推定"
        default: return "未推定"
        }
    }

    func beginSecondaryCaptureRescue(candidates: [SecondaryCaptureRescueCandidate]) {
        dicomRescueCandidates = candidates
        let primary = candidates.first(where: { $0.role == "primary" }) ?? candidates.first
        selectedDicomRescueCandidateID = primary?.id
        rescueEstimatedSpacing = primary?.initialSpacing
            ?? RescueSpacing(x: 1.0, y: 1.0, z: 1.0)
        rescueSpacingX = rescueEstimatedSpacing.x
        rescueSpacingY = rescueEstimatedSpacing.y
        rescueSpacingZ = rescueEstimatedSpacing.z
        rescueXYLocked = false
        rescueConfidence = primary?.hasFallbackSpacingAxis == false
            ? "低"
            : "未推定（仮の初期値を含む）"
        rescueEvidence = primary?.initialSpacingEvidence
            ?? ["標準DICOM幾何タグが不足", "X/Y/Zは編集用の仮初期値"]
        rescueWorkflowState = .manualOnly
        rescueCropMinX = 0
        rescueCropMinY = 0
        rescueCropMinZ = 0
        rescueCropMaxX = max(primary?.columns ?? 0, 1)
        rescueCropMaxY = max(primary?.rows ?? 0, 1)
        rescueCropMaxZ = max(primary?.fileCount ?? 0, 1)
        rescueMPRPreviewSlices = []
        rescuePseudo3DPreviewURL = nil
        rescuePreviewShapeXYZ = []
        clearRescueMeasurement()
        rescuePreviewMetadataInferenceStarted = false
        rescuePreviewStatus = "三方向の画像を準備しています（AI推論は開始していません）"
        rescueImageUpdateFailed = false
        rescueCalibrationRecords = []
        rescueInlineWarning = ""
        rescueConfirmationWasExplicit = false
        screen = .dicomRescue
        selectedStep = 1
        statusText = "形状を確認してください"
        progressText = "推定候補を確認し、必要なら手動で調整してください。AI推論はまだ開始していません。"
        resultKind = .dicomAudit
        resultOutcome = .none
        if let primary {
            exportPrimaryRescueStackIfAvailable(
                primary,
                referenceCandidates: candidates.filter { $0.role == "reference" }
            )
        }
    }

    private func exportPrimaryRescueStackIfAvailable(
        _ candidate: SecondaryCaptureRescueCandidate,
        referenceCandidates: [SecondaryCaptureRescueCandidate]
    ) {
        guard candidate.role == "primary",
              let dicomDir = lastDicomDirURL,
              FileManager.default.isExecutableFile(atPath: paths.normalizer.path) else {
            rescueWorkflowState = .sourceStackUnavailable
            rescuePreviewStatus = "三方向の画像を安全に準備できませんでした"
            rescueImageUpdateFailed = true
            return
        }
        let sessionDir = paths.runs.appendingPathComponent(
            "dicom_rescue_\(UUID().uuidString.lowercased())",
            isDirectory: true
        )
        let stackDir = sessionDir.appendingPathComponent("stack", isDirectory: true)
        let logURL = sessionDir.appendingPathComponent("logs/export_rescue_stack.log")
        try? FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard makeRescueDirectoryPrivate(sessionDir),
              makeRescueDirectoryPrivate(logURL.deletingLastPathComponent()) else {
            rescueWorkflowState = .sourceStackUnavailable
            rescuePreviewStatus = "救済データの専用保存領域を安全に作成できません"
            rescueImageUpdateFailed = true
            return
        }
        rescueWorkflowState = .estimating
        rescuePreviewStatus = "画像準備中（AI推論は開始していません）"
        rescueImageUpdateFailed = false
        rescuePreparationCancellationRequested = false
        isRunning = true
        runner.resetTerminationRequest()
        let command = CommandBuilder.dicomExportRescueStackCommand(
            dicomDir: dicomDir,
            outputDir: stackDir,
            seriesNumber: candidate.seriesNumber,
            seriesKey: candidate.seriesKey,
            paths: paths
        )
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let appPaths = paths
        let runner = self.runner
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = runner.run(command, environment: environment, logURL: logURL)
            let volumeURL = stackDir.appendingPathComponent("preview_stack.npy")
            let manifestURL = stackDir.appendingPathComponent("source_manifest.json")
            let manifestHash = rescueStackManifestSHA256(manifestURL)
            var referenceVolumes: [String: URL] = [:]
            if rc == 0, manifestHash != nil {
                for reference in referenceCandidates
                where referenceVolumes[reference.plane] == nil
                    && (reference.plane == "coronal" || reference.plane == "sagittal") {
                    let referenceDir = sessionDir.appendingPathComponent(
                        "reference_\(reference.plane)",
                        isDirectory: true
                    )
                    let referenceLog = sessionDir.appendingPathComponent(
                        "logs/export_\(reference.plane)_reference.log"
                    )
                    let referenceCommand = CommandBuilder.dicomExportRescueStackCommand(
                        dicomDir: dicomDir,
                        outputDir: referenceDir,
                        seriesNumber: reference.seriesNumber,
                        seriesKey: reference.seriesKey,
                        paths: appPaths
                    )
                    let referenceRC = runner.run(
                        referenceCommand,
                        environment: environment,
                        logURL: referenceLog
                    )
                    let referenceVolume = referenceDir.appendingPathComponent(
                        "preview_stack.npy"
                    )
                    if referenceRC == 0,
                       FileManager.default.fileExists(atPath: referenceVolume.path) {
                        referenceVolumes[reference.plane] = referenceVolume
                    }
                }
            }
            DispatchQueue.main.async {
                self?.isRunning = false
                guard self?.rescuePreparationCancellationRequested != true else {
                    self?.rescueWorkflowState = .manualOnly
                    self?.rescuePreviewStatus = "自動処理をキャンセルしました。手動調整を続けられます"
                    self?.rescueImageUpdateFailed = false
                    return
                }
                guard rc == 0,
                      FileManager.default.fileExists(atPath: volumeURL.path),
                      let manifestHash else {
                    self?.rescueWorkflowState = .sourceStackUnavailable
                    self?.rescuePreviewStatus = "画像の形式または並び順を安全に確認できませんでした"
                    self?.rescueImageUpdateFailed = true
                    return
                }
                let estimateJSON = sessionDir.appendingPathComponent("estimate/rescue_geometry.v2.json")
                self?.startSecondaryCaptureSpacingEstimation(
                    decodedVolume: volumeURL,
                    sourceManifestSHA256: manifestHash,
                    coronalReference: referenceVolumes["coronal"],
                    sagittalReference: referenceVolumes["sagittal"],
                    outputJSON: estimateJSON
                )
            }
        }
    }

    func startSecondaryCaptureSpacingEstimation(
        decodedVolume: URL,
        sourceManifestSHA256: String,
        coronalReference: URL? = nil,
        sagittalReference: URL? = nil,
        outputJSON: URL
    ) {
        let isSHA256 = sourceManifestSHA256.count == 64
            && sourceManifestSHA256.allSatisfy { $0.isHexDigit }
        guard FileManager.default.fileExists(atPath: decodedVolume.path), isSHA256 else {
            rescueWorkflowState = .manualOnly
            rescuePreviewStatus = "画像を安全に確認できないため、手動調整を使用します"
            rescueImageUpdateFailed = true
            return
        }
        rescueDecodedVolumeURL = decodedVolume
        rescueGeometryJSONURL = outputJSON
        rescueSourceManifestSHA256 = sourceManifestSHA256.lowercased()
        rescueWorkflowState = .estimating
        rescuePreviewStatus = "画像の情報を確認中"
        rescueImageUpdateFailed = false
        rescuePreparationCancellationRequested = false
        isRunning = true
        runner.resetTerminationRequest()
        let selectedCandidate = selectedDicomRescueCandidate
        let hintValues: [Double?] = [
            selectedCandidate?.pixelSpacingColumn,
            selectedCandidate?.pixelSpacingRow,
            selectedCandidate?.preferredSliceStep,
        ]
        let hints = hintValues.map { value in
            guard let value, value.isFinite, value > 0, value <= 20 else {
                return "unknown"
            }
            return String(format: "%.6f", value)
        }.joined(separator: ",")
        let evidenceJSON = outputJSON.deletingLastPathComponent().appendingPathComponent(
            "rescue_evidence.json"
        )
        let usedSeries: [[String: Any]] = dicomRescueCandidates.compactMap { candidate in
            guard let seriesHash = candidate.contentManifestSHA256 else { return nil }
            return [
                "series_hash": seriesHash,
                "role": candidate.role,
                "plane": candidate.plane,
                "reconstruction_group": candidate.reconstructionGroup,
                "file_count": candidate.fileCount,
                "rows": candidate.rows,
                "columns": candidate.columns,
            ]
        }
        var usedTags: [[String: Any]] = []
        if let row = selectedCandidate?.pixelSpacingRow,
           let column = selectedCandidate?.pixelSpacingColumn {
            usedTags.append([
                "tag": "0028,0030",
                "name": "PixelSpacing",
                "value_mm": [row, column],
                "consistency": "all_equal",
                "source": "standard_dicom",
            ])
        }
        if let projected = selectedCandidate?.projectedSliceSpacing {
            usedTags.append([
                "tag": "0020,0032+0020,0037",
                "name": "IPPProjectedSliceSpacing",
                "value_mm": projected,
                "consistency": "median_unique_positions",
                "source": "standard_dicom_derived",
            ])
        } else if let spacing = selectedCandidate?.spacingBetweenSlices {
            usedTags.append([
                "tag": "0018,0088",
                "name": "SpacingBetweenSlices",
                "value_mm": spacing,
                "consistency": "all_equal",
                "source": "standard_dicom",
            ])
        }
        if let thickness = selectedDicomRescueCandidate?.sliceThickness {
            usedTags.append([
                "tag": "0018,0050",
                "name": "SliceThickness",
                "value_mm": thickness,
                "consistency": "all_equal",
                "source": "standard_dicom",
            ])
        }
        var spacingSources: [String] = []
        if selectedCandidate?.pixelSpacingRow != nil,
           selectedCandidate?.pixelSpacingColumn != nil {
            spacingSources.append("pixel_spacing")
        }
        if selectedCandidate?.projectedSliceSpacing != nil {
            spacingSources.append("ipp_iop_projected_spacing")
        } else if selectedCandidate?.spacingBetweenSlices != nil {
            spacingSources.append("spacing_between_slices")
        } else if selectedCandidate?.sliceThickness != nil {
            spacingSources.append("slice_thickness")
        }
        if selectedCandidate?.hasFallbackSpacingAxis != false {
            spacingSources.append("fallback_initial_candidate")
        }
        writeJSON(
            [
                "spacing_sources": spacingSources.isEmpty
                    ? ["fallback_initial_candidate"]
                    : spacingSources,
                "used_series": usedSeries,
                "used_dicom_tags": usedTags,
            ],
            to: evidenceJSON
        )
        let coronal = dicomRescueCandidates.first(where: { $0.plane == "coronal" })
        let sagittal = dicomRescueCandidates.first(where: { $0.plane == "sagittal" })
        let command = CommandBuilder.dicomRescueEstimateCommand(
            python: paths.venvPython,
            decodedVolume: decodedVolume,
            sourceManifestSHA256: rescueSourceManifestSHA256,
            spacingHints: hints,
            evidenceJSON: evidenceJSON,
            axialSliceStepMM: selectedCandidate?.preferredSliceStep,
            coronalCount: coronal?.fileCount,
            coronalSliceStepMM: coronal?.preferredSliceStep,
            sagittalCount: sagittal?.fileCount,
            sagittalSliceStepMM: sagittal?.preferredSliceStep,
            coronalReference: coronalReference,
            sagittalReference: sagittalReference,
            outputJSON: outputJSON
        )
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let logURL = outputJSON.deletingLastPathComponent().appendingPathComponent("estimate/rescue_estimate.log")
        try? FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard makeRescueDirectoryPrivate(outputJSON.deletingLastPathComponent()),
              makeRescueDirectoryPrivate(logURL.deletingLastPathComponent()) else {
            isRunning = false
            rescueWorkflowState = .manualOnly
            rescuePreviewStatus = "推定用の専用保存領域を安全に作成できません"
            rescueImageUpdateFailed = true
            return
        }
        let runner = self.runner
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = runner.run(command, environment: environment, logURL: logURL)
            DispatchQueue.main.async {
                self?.isRunning = false
                guard self?.rescuePreparationCancellationRequested != true else {
                    self?.rescueWorkflowState = .manualOnly
                    self?.rescuePreviewStatus = "自動推定をキャンセルしました。手動調整を続けられます"
                    self?.rescueImageUpdateFailed = false
                    return
                }
                if rc == 0, self?.applyRescueEstimateMetadata(outputJSON) == true {
                    self?.rescueWorkflowState = .editableReady
                    self?.rescuePreviewStatus = "候補作成済み（AI推論は開始していません）"
                    self?.rescueImageUpdateFailed = false
                } else {
                    self?.rescueWorkflowState = .manualOnly
                    self?.rescuePreviewStatus = "自動推定に失敗したため手動調整を使用します"
                    self?.rescueImageUpdateFailed = false
                }
            }
        }
    }

    @discardableResult
    func applyRescueEstimateMetadata(_ metadataJSON: URL) -> Bool {
        guard let payload = readJSON(metadataJSON),
              (payload["inference_started"] as? Bool) != true,
              let estimate = payload["estimate"] as? [String: Any],
              let values = estimate["estimated_spacing_xyz"] as? [Any]
        else {
            return false
        }
        let spacing = values.compactMap(jsonDouble)
        guard spacing.count == 3 else { return false }
        let candidate = RescueSpacing(x: spacing[0], y: spacing[1], z: spacing[2])
        guard candidate.isValid else { return false }
        rescueEstimatedSpacing = candidate
        rescueXYLocked = false
        rescueSpacingX = candidate.x
        rescueSpacingY = candidate.y
        rescueSpacingZ = candidate.z
        let confidence = estimate["confidence"] as? [String: Any]
        rescueConfidence = (confidence?["overall"] as? String) ?? "低"
        let rawEvidence = ((confidence?["reasons"] as? [String]) ?? [])
            + ((confidence?["limitations"] as? [String]) ?? [])
        var seenEvidence = Set<String>()
        rescueEvidence = rawEvidence
            .map(rescueEvidenceDisplayText)
            .filter { seenEvidence.insert($0).inserted }
        if rescueEvidence.isEmpty {
            rescueEvidence = ["画像だけでは推定の確かさを十分に判断できません"]
        }
        rescueConfirmationWasExplicit = false
        rescueConfirmationToken = ""
        rescueMPRPreviewSlices = []
        rescuePseudo3DPreviewURL = nil
        rescuePreviewShapeXYZ = []
        clearRescueMeasurement()
        rescuePreviewStatus = "三方向の画像を更新します"
        rescueImageUpdateFailed = false
        rescuePreviewRevision &+= 1
        return true
    }

    func selectSecondaryCaptureRescueCandidate(_ candidate: SecondaryCaptureRescueCandidate) {
        guard candidate.role == "primary" else {
            rescueInlineWarning = "CORONAL/SAGITTAL系列は推定の参考です。作成元にはAXIAL系列を選んでください。"
            return
        }
        selectedDicomRescueCandidateID = candidate.id
        rescueEstimatedSpacing = candidate.initialSpacing
        rescueSpacingX = rescueEstimatedSpacing.x
        rescueSpacingY = rescueEstimatedSpacing.y
        rescueSpacingZ = rescueEstimatedSpacing.z
        rescueXYLocked = false
        rescueConfidence = candidate.hasFallbackSpacingAxis
            ? "未推定（仮の初期値を含む）"
            : "低"
        rescueEvidence = candidate.initialSpacingEvidence
        rescueCropMaxX = max(candidate.columns, 1)
        rescueCropMaxY = max(candidate.rows, 1)
        rescueCropMaxZ = max(candidate.fileCount, 1)
        rescueConfirmationToken = ""
        rescueGeometryJSONURL = nil
        rescueDecodedVolumeURL = nil
        exportPrimaryRescueStackIfAvailable(
            candidate,
            referenceCandidates: dicomRescueCandidates.filter { $0.role == "reference" }
        )
    }

    func beginRescueStretchAdjustment() {
        rescuePreviewWorkItem?.cancel()
        rescuePreviewWorkItem = nil
        rescueWorkflowState = .userModified
        rescueConfirmationWasExplicit = false
        rescueConfirmationToken = ""
        rescuePreviewStatus = "形状を調整中（AI推論は開始していません）"
        rescueImageUpdateFailed = false
        rescuePreviewRevision &+= 1
    }

    func setRescueStretchSpacing(axis: RescueCalibrationAxis, value: Double) {
        guard value.isFinite else { return }
        let clamped = min(max(value, 0.01), 20)
        rescueXYLocked = false
        switch axis {
        case .x:
            rescueSpacingX = clamped
        case .y:
            rescueSpacingY = clamped
        case .z:
            rescueSpacingZ = clamped
        }
        rescueWorkflowState = .userModified
        rescueConfirmationWasExplicit = false
        rescueConfirmationToken = ""
        updateRescueInlineWarning()
    }

    func finishRescueStretchAdjustment(axis: RescueCalibrationAxis) {
        rescueSpacingDidChange(axis: axis)
    }

    func rescueSpacingDidChange(axis: RescueCalibrationAxis) {
        if rescueXYLocked {
            if axis == .x {
                rescueSpacingY = rescueSpacingX
            } else if axis == .y {
                rescueSpacingX = rescueSpacingY
            }
        }
        rescueWorkflowState = .userModified
        rescueConfirmationWasExplicit = false
        rescueConfirmationToken = ""
        rescuePseudo3DPreviewURL = nil
        clearRescueMeasurement()
        rescuePreviewStatus = "調整した形状を確認中（AI推論なし）"
        rescuePreviewRevision &+= 1
        updateRescueInlineWarning()
        scheduleRescuePreviewUpdate()
    }

    func rescueTransformDidChange() {
        rescueWorkflowState = .userModified
        rescueConfirmationWasExplicit = false
        rescueConfirmationToken = ""
        rescuePseudo3DPreviewURL = nil
        clearRescueMeasurement()
        rescuePreviewStatus = "画像の向きを更新しています（AI推論なし）"
        rescueImageUpdateFailed = false
        rescuePreviewRevision &+= 1
        updateRescueInlineWarning()
        scheduleRescuePreviewUpdate()
    }

    func resetRescueGeometryToEstimate() {
        rescueSpacingX = rescueEstimatedSpacing.x
        rescueSpacingY = rescueEstimatedSpacing.y
        rescueSpacingZ = rescueEstimatedSpacing.z
        rescueAxisPermutation = .xyz
        rescueRotationQuarterTurns = 0
        rescueSliceOrderReversed = false
        rescueCalibrationRecords = []
        if let candidate = selectedDicomRescueCandidate {
            rescueCropMinX = 0
            rescueCropMinY = 0
            rescueCropMinZ = 0
            rescueCropMaxX = max(candidate.columns, 1)
            rescueCropMaxY = max(candidate.rows, 1)
            rescueCropMaxZ = max(candidate.fileCount, 1)
        }
        rescueWorkflowState = .editableReady
        rescueConfirmationWasExplicit = false
        rescueConfirmationToken = ""
        rescuePseudo3DPreviewURL = nil
        clearRescueMeasurement()
        rescuePreviewStatus = "推定形状を確認中（AI推論なし）"
        rescueImageUpdateFailed = false
        rescuePreviewRevision &+= 1
        updateRescueInlineWarning()
        scheduleRescuePreviewUpdate()
    }

    func applyRescueKnownLengthCalibration() {
        guard rescueMeasuredLengthMM.isFinite, rescueMeasuredLengthMM > 0,
              rescueKnownLengthMM.isFinite, rescueKnownLengthMM > 0 else {
            rescueInlineWarning = "計測した長さと既知の長さには0より大きい値を入力してください。"
            return
        }
        let scale = rescueKnownLengthMM / rescueMeasuredLengthMM
        var record: [String: Any] = [
            "axis": rescueCalibrationAxis.rawValue,
            "plane": rescueMeasurementPlane,
            "measured_length_mm": rescueMeasuredLengthMM,
            "known_length_mm": rescueKnownLengthMM,
            "scale": scale,
            "method": "known_length_manual",
        ]
        if let points = rescueMeasurementVoxelPoints() {
            record["voxel_points_xyz"] = points
        }
        rescueCalibrationRecords.append(record)
        switch rescueCalibrationAxis {
        case .x:
            rescueSpacingX *= scale
            if rescueXYLocked { rescueSpacingY = rescueSpacingX }
        case .y:
            rescueSpacingY *= scale
            if rescueXYLocked { rescueSpacingX = rescueSpacingY }
        case .z:
            rescueSpacingZ *= scale
        }
        rescueSpacingDidChange(axis: rescueCalibrationAxis)
    }

    func updateRescueMeasurement(
        plane: String,
        startNormalized: CGPoint,
        endNormalized: CGPoint
    ) {
        guard let slice = rescueMPRPreviewSlices.first(where: { $0.plane == plane }),
              let rowSpacing = slice.rowSpacingMM,
              let columnSpacing = slice.columnSpacingMM,
              rowSpacing.isFinite,
              columnSpacing.isFinite,
              rowSpacing > 0,
              columnSpacing > 0
        else {
            rescueInlineWarning = "この断面の物理寸法を読み取れないため、距離を計測できません。"
            return
        }
        let start = CGPoint(
            x: min(max(startNormalized.x, 0), 1),
            y: min(max(startNormalized.y, 0), 1)
        )
        let end = CGPoint(
            x: min(max(endNormalized.x, 0), 1),
            y: min(max(endNormalized.y, 0), 1)
        )
        let horizontalMM = Double(end.x - start.x) * Double(slice.width) * columnSpacing
        let verticalMM = Double(end.y - start.y) * Double(slice.height) * rowSpacing
        let distance = hypot(horizontalMM, verticalMM)
        guard distance.isFinite, distance > 0 else {
            rescueInlineWarning = "2点を離して計測線を引いてください。"
            return
        }
        rescueMeasurementPlane = plane
        rescueMeasurementStartNormalized = start
        rescueMeasurementEndNormalized = end
        rescueMeasuredLengthMM = distance
        rescueCalibrationAxis = measurementAxis(
            plane: plane,
            horizontalMM: horizontalMM,
            verticalMM: verticalMM
        )
        rescueInlineWarning = ""
    }

    private func measurementAxis(
        plane: String,
        horizontalMM: Double,
        verticalMM: Double
    ) -> RescueCalibrationAxis {
        let horizontalDominates = abs(horizontalMM) >= abs(verticalMM)
        switch plane {
        case "coronal":
            return horizontalDominates ? .x : .z
        case "sagittal":
            return horizontalDominates ? .y : .z
        default:
            return horizontalDominates ? .x : .y
        }
    }

    private func rescueMeasurementVoxelPoints() -> [[Double]]? {
        guard rescuePreviewShapeXYZ.count == 3,
              let start = rescueMeasurementStartNormalized,
              let end = rescueMeasurementEndNormalized
        else {
            return nil
        }
        let maximum = rescuePreviewShapeXYZ.map { Double(max($0 - 1, 0)) }
        func point(_ normalized: CGPoint) -> [Double] {
            switch rescueMeasurementPlane {
            case "coronal":
                return [
                    Double(normalized.x) * maximum[0],
                    maximum[1] * 0.5,
                    Double(normalized.y) * maximum[2],
                ]
            case "sagittal":
                return [
                    maximum[0] * 0.5,
                    Double(normalized.x) * maximum[1],
                    Double(normalized.y) * maximum[2],
                ]
            default:
                return [
                    Double(normalized.x) * maximum[0],
                    Double(normalized.y) * maximum[1],
                    maximum[2] * 0.5,
                ]
            }
        }
        return [point(start), point(end)]
    }

    func clearRescueMeasurement() {
        rescueMeasurementStartNormalized = nil
        rescueMeasurementEndNormalized = nil
        rescueMeasuredLengthMM = 0
    }

    private func makeRescueDirectoryPrivate(_ directory: URL) -> Bool {
        do {
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true
            )
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o700],
                ofItemAtPath: directory.path
            )
            let attributes = try FileManager.default.attributesOfItem(atPath: directory.path)
            guard let permissions = attributes[.posixPermissions] as? NSNumber else {
                return false
            }
            return permissions.intValue & 0o777 == 0o700
        } catch {
            return false
        }
    }

    func applyRescuePreviewMetadata(_ metadataJSON: URL) {
        guard let payload = readJSON(metadataJSON) else {
            rescuePreviewStatus = "画像の更新結果を読み取れませんでした"
            rescueImageUpdateFailed = true
            return
        }
        let inferenceStarted = (payload["inference_started"] as? Bool) ?? false
        rescuePreviewMetadataInferenceStarted = inferenceStarted
        guard !inferenceStarted else {
            rescueMPRPreviewSlices = []
            rescuePseudo3DPreviewURL = nil
            rescueConfirmationToken = ""
            rescuePreviewStatus = "確認前の処理開始が検出されたため、画像を表示しません"
            rescueImageUpdateFailed = true
            return
        }
        rescueMPRPreviewSlices = rescuePreviewSlices(payload: payload)
        rescuePseudo3DPreviewURL = parsedRescuePseudo3DPreviewURL(payload: payload)
        rescuePreviewShapeXYZ = (
            (payload["preview"] as? [String: Any])?["shape"] as? [Any]
        )?.compactMap(jsonInt) ?? []
        rescueConfirmationToken = (payload["confirmation_token"] as? String) ?? ""
        rescuePreviewStatus = rescueMPRPreviewSlices.isEmpty
            ? "三方向の画像を待っています（AI推論は開始していません）"
            : "三方向の画像を更新しました（AI推論は開始していません）"
        rescueImageUpdateFailed = rescueMPRPreviewSlices.isEmpty
    }

    func requestRescuePreviewUpdate() {
        guard canRequestRescuePreview,
              let decodedVolume = rescueDecodedVolumeURL else {
            rescuePreviewStatus = "元画像を準備できていないため、三方向の画像を更新できません"
            rescueImageUpdateFailed = true
            return
        }
        let previewDir = (rescueGeometryJSONURL?.deletingLastPathComponent() ?? paths.runs)
            .appendingPathComponent("preview", isDirectory: true)
        let requestJSON = previewDir.appendingPathComponent("rescue_preview_request.json")
        let outputVolume = previewDir.appendingPathComponent("preview_volume.npy")
        let outputJSON = previewDir.appendingPathComponent("preview.json")
        try? FileManager.default.createDirectory(at: previewDir, withIntermediateDirectories: true)
        guard makeRescueDirectoryPrivate(previewDir) else {
            rescuePreviewStatus = "画像更新用の保存領域を安全に作成できません"
            rescueImageUpdateFailed = true
            return
        }
        guard var request = rescueGeometryJSONURL.flatMap(readJSON),
              (request["schema"] as? String) == "totalsegmentator_wrapper_mac.rescue_geometry.v2",
              let source = request["source"] as? [String: Any],
              (source["content_manifest_sha256"] as? String) == rescueSourceManifestSHA256,
              (request["inference_started"] as? Bool) != true else {
            isRunning = false
            rescuePreviewStatus = "画像情報が元データと一致しないため更新できません"
            rescueImageUpdateFailed = true
            return
        }
        request["workflow_status"] = "preview_requested"
        request["confirmed"] = [
            "confirmed_spacing_xyz": [rescueSpacingX, rescueSpacingY, rescueSpacingZ],
            "manual_changed": currentRescueSpacing != rescueEstimatedSpacing || rescueUsesNonIdentityTransform,
        ]
        request["transform"] = [
            "axis_permutation": Array(rescueAxisPermutation.rawValue).map(String.init),
            "rotation_quarter_turns": rescueRotationQuarterTurns,
            "slice_order_reversed": rescueSliceOrderReversed,
            "crop_voxels_xyz": [
                "min": [rescueCropMinX, rescueCropMinY, rescueCropMinZ],
                "max_exclusive": [rescueCropMaxX, rescueCropMaxY, rescueCropMaxZ],
            ],
        ]
        request["calibrations"] = rescueCalibrationRecords
        request["inference_started"] = false
        writeJSON(request, to: requestJSON)
        rescuePreviewStatus = "三方向の画像を更新中（AI推論なし）"
        rescueImageUpdateFailed = false
        rescuePreparationCancellationRequested = false
        isRunning = true
        runner.resetTerminationRequest()
        let command = CommandBuilder.dicomRescuePreviewCommand(
            python: paths.venvPython,
            decodedVolume: decodedVolume,
            geometryJSON: requestJSON,
            outputVolume: outputVolume,
            outputJSON: outputJSON
        )
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let logURL = previewDir.appendingPathComponent("preview.log")
        let runner = self.runner
        let requestRevision = rescuePreviewRevision
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = runner.run(command, environment: environment, logURL: logURL)
            DispatchQueue.main.async {
                self?.isRunning = false
                guard self?.rescuePreparationCancellationRequested != true else {
                    self?.rescuePreviewStatus = "画像の更新を中止しました（AI推論は開始していません）"
                    self?.rescueImageUpdateFailed = false
                    return
                }
                guard self?.rescuePreviewRevision == requestRevision else {
                    self?.rescueConfirmationToken = ""
                    self?.rescuePreviewStatus = "変更後の三方向画像を再計算しています"
                    self?.rescueImageUpdateFailed = false
                    self?.scheduleRescuePreviewUpdate()
                    return
                }
                if rc == 0 {
                    self?.rescueGeometryJSONURL = outputJSON
                    self?.applyRescuePreviewMetadata(outputJSON)
                } else {
                    self?.rescueConfirmationToken = ""
                    self?.rescuePreviewStatus = "画像の更新に失敗しました（AI推論は開始していません）"
                    self?.rescueImageUpdateFailed = true
                }
            }
        }
    }

    private func scheduleRescuePreviewUpdate() {
        rescueConfirmationToken = ""
        rescuePreviewWorkItem?.cancel()
        guard rescueDecodedVolumeURL != nil else { return }
        let workItem = DispatchWorkItem { [weak self] in
            guard let self, self.canRequestRescuePreview else { return }
            self.requestRescuePreviewUpdate()
        }
        rescuePreviewWorkItem = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3, execute: workItem)
    }

    func cancelSecondaryCaptureRescuePreparation() {
        guard isRunning, screen == .dicomRescue else { return }
        rescuePreparationCancellationRequested = true
        runner.terminate(graceSeconds: 2)
        isRunning = false
        rescueWorkflowState = .manualOnly
        rescuePreviewStatus = "自動処理をキャンセルしました。手動調整を続けられます"
        rescueImageUpdateFailed = false
        rescueConfirmationToken = ""
        rescuePreviewWorkItem?.cancel()
        rescuePreviewWorkItem = nil
    }

    func confirmSecondaryCaptureRescue() {
        guard selectedDicomRescueCandidate != nil else {
            rescueInlineWarning = "使用する系列を選択してください。"
            return
        }
        guard currentRescueSpacing.isValid else {
            rescueInlineWarning = "形状の伸縮量が範囲外です。推定形状へ戻して調整し直してください。"
            return
        }
        guard rescueCropIsValid else {
            rescueInlineWarning = "画像の表示範囲を確認できません。別のCTを選んでください。"
            return
        }
        guard canFinalizeRescueTransform else {
            rescueInlineWarning = "三方向の画像を更新してから形状を確定してください。AI推論はまだ開始していません。"
            return
        }
        finalizeSecondaryCaptureRescue()
    }

    private func finalizeSecondaryCaptureRescue() {
        guard let decodedVolume = rescueDecodedVolumeURL,
              let geometryJSON = rescueGeometryJSONURL,
              canFinalizeRescueTransform else {
            rescueInlineWarning = "現在の形状に対応する三方向の画像を更新してください。"
            return
        }
        rescueConfirmationWasExplicit = true
        let finalizeDir = geometryJSON.deletingLastPathComponent().appendingPathComponent(
            "final_\(UUID().uuidString.lowercased())",
            isDirectory: true
        )
        let outputNifti = finalizeDir.appendingPathComponent("rescue_volume.nii")
        let outputJSON = finalizeDir.appendingPathComponent("rescue_geometry.v2.json")
        let logURL = finalizeDir.appendingPathComponent("finalize.log")
        try? FileManager.default.createDirectory(at: finalizeDir, withIntermediateDirectories: true)
        guard makeRescueDirectoryPrivate(finalizeDir) else {
            rescueInlineWarning = "確定成果物の専用保存領域を安全に作成できません。"
            return
        }
        rescueWorkflowState = .preparingNifti
        isRunning = true
        stopRequested = false
        screen = .running
        selectedStep = 2
        outputURL = finalizeDir
        activeLogURL = logURL
        resultLogURL = nil
        runner.resetTerminationRequest()
        startRunTimer()
        statusText = "確定した形状を適用中"
        progressText = "確認済みの形状から3D作成用データを準備しています。AI推論はまだ開始していません。"
        let command = CommandBuilder.dicomRescueFinalizeCommand(
            python: paths.venvPython,
            decodedVolume: decodedVolume,
            geometryJSON: geometryJSON,
            confirmationToken: rescueConfirmationToken,
            outputNifti: outputNifti,
            outputJSON: outputJSON
        )
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let runner = self.runner
        let requestedSpacing = currentRescueSpacing
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = runner.run(command, environment: environment, logURL: logURL)
            let valid = rc == 0 && rescueFinalizedNiftiMatches(
                outputNifti: outputNifti,
                metadataJSON: outputJSON,
                requested: requestedSpacing
            )
            DispatchQueue.main.async {
                self?.isRunning = false
                self?.stopRequested = false
                self?.refreshLog(from: logURL)
                self?.activeLogURL = nil
                self?.resultLogURL = logURL
                if valid {
                    self?.acceptPreparedRescueNifti(outputNifti)
                } else {
                    self?.rescueWorkflowState = rc == 0 ? .readbackMismatch : .prepareFailed
                    self?.rescueConfirmationWasExplicit = false
                    self?.screen = .dicomRescue
                    self?.selectedStep = 1
                    self?.rescueInlineWarning = "確定した形状を安全に読み直せませんでした。AI推論は開始していません。"
                    self?.rescueImageUpdateFailed = true
                }
            }
        }
    }

    private func acceptPreparedRescueNifti(_ preparedURL: URL) {
        guard rescueConfirmationWasExplicit,
              FileManager.default.fileExists(atPath: preparedURL.path) else {
            rescueWorkflowState = .readbackMismatch
            screen = .dicomRescue
            return
        }
        inputURL = preparedURL
        inputSource = .nifti
        creationChoice = .standardArchJaw
        resultKind = .none
        outputURL = nil
        statusText = "形状確認済み"
        progressText = "確定した形状を確認できました。3Dプレビュー作成を開始します。"
        startRun()
    }

    private func updateRescueInlineWarning() {
        if !currentRescueSpacing.isValid {
            rescueInlineWarning = "形状の伸縮量が範囲外です。推定形状へ戻して調整し直してください。"
        } else if !rescueCropIsValid {
            rescueInlineWarning = "画像の表示範囲を確認できません。別のCTを選んでください。"
        } else if rescueUsesNonIdentityTransform {
            rescueInlineWarning = "画像の向きを変更しました。更新が終わると形状を確定できます。"
        } else {
            rescueInlineWarning = ""
        }
    }

    private func resetSecondaryCaptureRescue() {
        dicomRescueCandidates = []
        selectedDicomRescueCandidateID = nil
        rescueWorkflowState = .rescueAvailable
        rescueConfirmationWasExplicit = false
        rescueInlineWarning = ""
        rescueMPRPreviewSlices = []
        rescuePseudo3DPreviewURL = nil
        rescuePreviewMetadataInferenceStarted = false
        rescueImageUpdateFailed = false
        rescueDecodedVolumeURL = nil
        rescueGeometryJSONURL = nil
        rescueSourceManifestSHA256 = ""
        rescuePreparationCancellationRequested = false
        rescueConfirmationToken = ""
        rescuePreviewWorkItem?.cancel()
        rescuePreviewWorkItem = nil
    }

    func convertDicomToNiftiFromAudit() {
        useSelectedDicomSeries()
    }

    private func startDicomCleanConversion(dicomDir: URL, candidate: CleanDicomSeriesCandidate) {
        guard FileManager.default.fileExists(atPath: paths.venvPython.path) else {
            statusText = "はじめの準備が必要です。"
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
        let venvPython = paths.venvPython
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = runner.run(command, environment: environment, logURL: logURL)
            let metadataJSON = convertDir.appendingPathComponent("convert_clean_metadata.json")
            let niftiURL = rc == 0 ? convertedNiftiURL(metadataJSON: metadataJSON) : nil
            let previewDir = convertDir.appendingPathComponent("input_preview", isDirectory: true)
            let previewJSON = previewDir.appendingPathComponent("preview.json")
            var previewRC: Int32 = 1
            if let niftiURL, FileManager.default.fileExists(atPath: niftiURL.path) {
                previewRC = runner.run(
                    CommandBuilder.niftiPreviewCommand(
                        python: venvPython,
                        input: niftiURL,
                        outputDir: previewDir.appendingPathComponent("images", isDirectory: true),
                        outputJSON: previewJSON
                    ),
                    environment: environment,
                    logURL: logURL
                )
            }
            let previewSlices = previewRC == 0 ? viewerExportPreviewSlices(metadataJSON: previewJSON) : []
            let previewVolumeEmpty = previewRC == 0
                ? niftiPreviewVolumeIsUniformOrEmpty(metadataJSON: previewJSON)
                : false
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
                    self?.resultOutcome = .failure
                    self?.failureReasonText = "CTの取り込みを停止しました。"
                    self?.setSafeError(code: "dicom_conversion_cancelled", reason: "The CT conversion was cancelled.", mpsState: "not_applicable")
                    self?.screen = .result
                    self?.selectedStep = 3
                    self?.statusText = "停止しました"
                    self?.progressText = "CT取り込みを停止しました。入力は変更されていません。"
                    self?.resultMessage = "CT取り込みを停止しました。必要ならもう一度CTを選び直してください。"
                    return
                }
                if let niftiURL, FileManager.default.fileExists(atPath: niftiURL.path) {
                    self?.resultOutcome = .none
                    self?.inputURL = niftiURL
                    self?.inputSource = .nifti
                    self?.inputCTPreviewRequired = true
                    self?.inputCTPreviewSlices = previewSlices
                    self?.inputCTPreviewVolumeEmpty = previewVolumeEmpty
                    self?.inputCTPreviewFailed = previewRC != 0 || previewSlices.count < 3
                    self?.inputCTPreviewWarning = makeInputCTPreviewWarning(
                        slices: previewSlices,
                        volumeEmpty: previewVolumeEmpty,
                        failed: previewRC != 0
                    )
                    self?.outputURL = nil
                    self?.resultKind = .none
                    self?.dicomSummaryText = ""
                    self?.summaryText = ""
                    self?.resultMessage = ""
                    self?.screen = .inputAndCreation
                    self?.selectedStep = 1
                    self?.statusText = "プレビュー作成準備完了"
                    self?.progressText = "CTを取り込みました。3Dプレビューを作成できます。"
                } else {
                    self?.resultOutcome = .failure
                    self?.failureReasonText = "CTを取り込めませんでした。"
                    self?.setSafeError(code: "dicom_conversion_failed", reason: "The CT data could not be converted.", mpsState: "not_applicable")
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
            statusText = "はじめの準備が必要です。"
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
                    self?.resultOutcome = .failure
                    self?.failureReasonText = "断面群の準備を停止しました。"
                    self?.setSafeError(code: "viewer_export_cancelled", reason: "The CT slice preparation was cancelled.", mpsState: "not_applicable")
                    self?.screen = .result
                    self?.selectedStep = 3
                    self?.statusText = "停止しました"
                    self?.progressText = "救済データ作成を停止しました。入力は変更されていません。"
                    self?.resultMessage = "救済データ作成を停止しました。必要ならもう一度CTを選び直してください。"
                    return
                }
                if let niftiURL, FileManager.default.fileExists(atPath: niftiURL.path) {
                    self?.resultOutcome = .none
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
                    self?.statusText = "CT画像を確認"
                    self?.progressText = "歯や顎が3枚とも見えているか確認してください。"
                } else {
                    self?.resultOutcome = .failure
                    self?.failureReasonText = "断面群を準備できませんでした。"
                    self?.setSafeError(code: "viewer_export_failed", reason: "The CT slice data could not be prepared.", mpsState: "not_applicable")
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
        guard canRunSelectedSettings else {
            statusText = "設定を確認してください。"
            progressText = runSettingsWarning
            return
        }
        guard FileManager.default.fileExists(atPath: paths.venvPython.path) else {
            statusText = "はじめの準備が必要です。"
            screen = .setup
            return
        }
        let output = nextCaseOutput()
        outputURL = output
        let caseLogURL = output.appendingPathComponent("logs/run.log")
        let appRunLogURL = paths.appRunLog
        let provenance = inputProvenancePayload()
        try? FileManager.default.removeItem(at: appRunLogURL)
        try? FileManager.default.removeItem(at: paths.runResultJSON)
        logText = ""
        summaryText = ""
        dicomSummaryText = ""
        resultMessage = ""
        failureReasonText = ""
        safeErrorCode = ""
        safeErrorReason = ""
        safeMPSState = "unknown"
        safeErrorOccurredAt = ""
        toothSegRefineFailed = false
        resultKind = .inference
        resultOutcome = .none
        surfacePreviewFailed = false
        activeRunBackend = segmentationBackend
        activeRunMode = runMode
        activeRunDevice = effectiveRunDevice
        primaryRunBackend = segmentationBackend
        primaryRunMode = runMode
        activeResultFlavor = segmentationBackend == .toothSeg ? .toothSeg : .craniofacial
        statusText = segmentationBackend == .dentalSegmentator
            ? "DentalSegmentatorで3Dプレビューを作成中"
            : (segmentationBackend == .toothSeg ? "ToothSegで個別歯を作成中" : "3Dプレビューを作成中")
        if segmentationBackend == .dentalSegmentator {
            progressText = "CTデータを処理しています。"
        } else if segmentationBackend == .toothSeg {
            progressText = "歯列ROIを抽出し、0.3 mmと0.2 mmの両branchで歯を1本ずつ分けています。"
        } else {
            progressText = runMode == .individualTeeth ? "歯を1本ずつ分けています。" : "CTデータを処理しています。"
        }
        resetRunProgressTracking()
        runProgressFraction = nil
        isRunning = true
        stopRequested = false
        screen = .running
        selectedStep = 2
        activeLogURL = appRunLogURL
        resultLogURL = nil
        runner.resetTerminationRequest()
        startRunTimer()

        let command = CommandBuilder.runCommand(
            python: paths.venvPython,
            input: inputURL,
            output: output,
            mode: runMode,
            backend: segmentationBackend,
            device: "mps",
            higherOrderResampling: higherOrderResampling,
            paths: paths
        )
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let runner = self.runner
        let venvPython = paths.venvPython
        let modeForRun = runMode
        let backendForRun = segmentationBackend
        let deviceForRun = effectiveRunDevice
        let smoothSurfacesForRun = higherOrderResampling

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = runner.run(command, environment: environment, logURL: appRunLogURL)
            let caseWasCreated = FileManager.default.fileExists(atPath: output.path)
            if caseWasCreated {
                writeJSON(provenance, to: output.appendingPathComponent("input_provenance.json"))
            }
            let finalLogURL = caseWasCreated && FileManager.default.fileExists(atPath: caseLogURL.path)
                ? caseLogURL
                : appRunLogURL
            var surfacePreviewRC: Int32? = nil
            if rc == 0 && self?.stopRequested != true {
                let previewLabel = backendForRun == .dentalSegmentator ? "DentalSegmentator" : (backendForRun == .toothSeg ? "ToothSeg個別歯" : (modeForRun == .individualTeeth ? "個別歯" : "歯列・顎骨"))
                let previewLabelmap = self?.expectedResultLabelmapURL(
                    caseDir: output,
                    flavor: backendForRun == .toothSeg ? .toothSeg : .craniofacial
                )
                DispatchQueue.main.sync {
                    self?.advanceStructuredRunToPreview(logURLs: [appRunLogURL, caseLogURL])
                    self?.statusText = "3Dプレビュー作成中"
                    self?.progressText = "ブラウザで開ける\(previewLabel)3Dプレビューを作成しています。"
                    self?.runProgressFraction = nil
                    self?.runHeartbeatText = "STLとHTML viewerを生成しています。数十秒かかることがあります。"
                }
                let sourceLabelmap = previewLabelmap.flatMap {
                    FileManager.default.fileExists(atPath: $0.path) ? $0 : nil
                }
                surfacePreviewRC = runner.run(
                    CommandBuilder.surfacePreviewCommand(
                        python: venvPython,
                        caseDir: output,
                        sourceInput: sourceLabelmap,
                        smoothSurfaces: smoothSurfacesForRun
                    ),
                    environment: environment,
                    logURL: caseLogURL
                )
            }
            let stoppedBeforeSummary = runner.isTerminationRequested || self?.stopRequested == true
            let summary = stoppedBeforeSummary || rc != 0
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
                self?.refreshLog(from: finalLogURL)
                self?.activeLogURL = nil
                self?.resultLogURL = finalLogURL
                if !caseWasCreated {
                    self?.outputURL = nil
                }
                self?.runHeartbeatText = ""
                self?.activeRunBackend = backendForRun
                self?.activeRunMode = modeForRun
                self?.activeRunDevice = deviceForRun
                self?.activeResultFlavor = backendForRun == .toothSeg ? .toothSeg : .craniofacial
                self?.primaryRunBackend = backendForRun
                self?.primaryRunMode = modeForRun
                self?.loadSafeRunResult()
                self?.primaryRunTeethDetected = self?.primaryRunBackend == .totalSegmentator
                    ? (self?.teethDetected == true)
                    : false
                self?.canRunToothSegRefine = self?.primaryRunBackend == .totalSegmentator ? (self?.primaryRunTeethDetected == true) : false
                self?.toothSegRefineFailed = false
                self?.normalizeActiveResultFlavor()
                self?.screen = .result
                self?.selectedStep = 3
                if stopped {
                    self?.setSafeError(code: "cancelled", reason: "The operation was cancelled before completion.", mpsState: "unknown")
                    self?.resultOutcome = .failure
                    self?.failureReasonText = "処理を停止しました。"
                    self?.statusText = "停止しました"
                    self?.progressText = "処理を停止しました。入力は変更されていません。"
                    self?.resultMessage = "3Dプレビューを作成できませんでした"
                } else if rc == 0 && surfacePreviewRC == 0 {
                    self?.runProgressFraction = 1.0
                    self?.resultOutcome = .success
                    self?.failureReasonText = ""
                    self?.statusText = "完了"
                    self?.progressText = "結果と3Dプレビューを確認できます。"
                    self?.resultMessage = "3Dプレビューを作成しました"
                } else if rc == 0 && surfacePreviewRC != nil {
                    self?.setSafeError(code: "preview_generation_failed", reason: "The 3D preview could not be generated.", mpsState: "validated")
                    self?.resultOutcome = .failure
                    let reason = runFailureReason(from: finalLogURL)
                    self?.failureReasonText = reason.isEmpty ? "3D preview生成だけが完了できませんでした。詳細ログを確認してください。" : reason
                    self?.surfacePreviewFailed = true
                    self?.statusText = "完了（3Dプレビュー未作成）"
                    self?.progressText = "3D preview用の出力作成は完了しました。3D previewだけ作り直せます。"
                    self?.resultMessage = "3Dプレビューを作成できませんでした"
                } else if rc == 0 {
                    self?.resultOutcome = .success
                    self?.failureReasonText = ""
                    self?.statusText = "完了"
                    self?.progressText = "結果を確認できます。"
                    self?.resultMessage = "3Dプレビューを作成しました"
                } else {
                    if self?.safeErrorCode.isEmpty ?? true {
                        self?.setSafeError(code: "backend_failed", reason: "The segmentation backend did not complete.", mpsState: "unknown")
                    }
                    self?.resultOutcome = .failure
                    let reason = runFailureReason(from: finalLogURL)
                    self?.failureReasonText = reason.isEmpty ? "実行コマンドが完了できませんでした。詳細ログを確認してください。" : reason
                    self?.statusText = "処理を完了できませんでした"
                    self?.progressText = self?.failureReasonText ?? "入力は変更されていません。もう一度実行するか、詳細ログを確認してください。"
                    self?.resultMessage = "3Dプレビューを作成できませんでした"
                }
                self?.summaryText = summary
            }
        }
    }

    func startToothSegRefineRun() {
        guard let inputURL else {
            statusText = "入力を確認してください。"
            return
        }
        guard let outputURL else {
            statusText = "結果フォルダが見つかりません。"
            return
        }
        guard !isRunning else {
            return
        }
        guard primaryRunBackend == .totalSegmentator else {
            failureReasonText = "高精細化はTotalSegmentatorの初回出力時のみ使用できます。"
            statusText = "対象外"
            progressText = failureReasonText
            resultOutcome = .failure
            return
        }
        guard primaryRunTeethDetected else {
            failureReasonText = "初回の歯抽出結果がありません。"
            statusText = "高精細化を開始できません"
            progressText = "初回結果に歯が含まれないため、ToothSeg高精細化は不要です。"
            resultOutcome = .failure
            return
        }
        guard isToothSegModelReady else {
            requestToothSegRefine()
            return
        }
        let refineDiagnosticsURL = outputURL.appendingPathComponent("logs/toothseg_refine", isDirectory: true)
        let refineLogURL = refineDiagnosticsURL.appendingPathComponent("process.log")
        let refineResultURL = refineDiagnosticsURL.appendingPathComponent("result.json")
        try? FileManager.default.removeItem(at: refineResultURL)
        logText = ""
        summaryText = ""
        resultMessage = ""
        failureReasonText = ""
        safeErrorCode = ""
        safeErrorReason = ""
        safeMPSState = "unknown"
        safeErrorOccurredAt = ""
        toothSegRefineFailed = false
        resultKind = .inference
        activeRunBackend = .toothSeg
        activeRunMode = .individualTeeth
        activeRunDevice = "mps"
        activeResultFlavor = .toothSeg
        statusText = "ToothSeg（高精細歯分割）を実行中"
        progressText = "歯を個別化してFDI番号付きの高精細ラベルマップを生成しています。"
        resetRunProgressTracking()
        beginStructuredRunProgress(route: "toothseg_refine")
        runProgressFraction = nil
        isRunning = true
        stopRequested = false
        screen = .running
        selectedStep = 2
        activeLogURL = refineLogURL
        resultLogURL = nil
        runner.resetTerminationRequest()
        startRunTimer()

        let command = CommandBuilder.toothSegRefineCommand(
            python: paths.venvPython,
            input: inputURL,
            output: outputURL,
            craniofacialCase: outputURL,
            paths: paths
        )
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let runner = self.runner
        let venvPython = paths.venvPython
        let outputForRefine = outputURL
        let previewOutput = expectedSurfacePreviewOutputURL(
            caseDir: outputURL,
            flavor: .toothSeg
        )
        let smoothSurfacesForRun = higherOrderResampling

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = runner.run(command, environment: environment, logURL: refineLogURL)
            let previewInput = outputForRefine
                .appendingPathComponent("segmentations", isDirectory: true)
                .appendingPathComponent("toothseg", isDirectory: true)
                .appendingPathComponent("toothseg_fdi_multilabel.nii.gz")
            let previewLog = outputForRefine.appendingPathComponent("logs/toothseg_refine/preview.log")
            let stoppedBeforePreview = runner.isTerminationRequested || self?.stopRequested == true
            var surfacePreviewRC: Int32? = nil
            if rc == 0 && !stoppedBeforePreview {
                DispatchQueue.main.sync {
                    self?.advanceStructuredRunToPreview(logURLs: [refineLogURL, previewLog])
                }
                let hasPreviewInput = FileManager.default.fileExists(atPath: previewInput.path)
                surfacePreviewRC = runner.run(
                    CommandBuilder.surfacePreviewCommand(
                        python: venvPython,
                        caseDir: outputForRefine,
                        sourceInput: hasPreviewInput ? previewInput : nil,
                        outputDir: previewOutput,
                        smoothSurfaces: smoothSurfacesForRun
                    ),
                    environment: environment,
                    logURL: previewLog
                )
            }
            let summaryCommand = CommandBuilder.summaryCommand(python: venvPython, caseDir: outputForRefine)
            let stoppedBeforeSummary = runner.isTerminationRequested || self?.stopRequested == true
            let summary = stoppedBeforeSummary
                ? ""
                : runner.runCapturing(summaryCommand, environment: environment, logURL: nil).1
            DispatchQueue.main.async {
                let stopped = self?.stopRequested == true
                self?.isRunning = false
                self?.stopRequested = false
                self?.refreshLog(from: refineLogURL)
                self?.activeLogURL = nil
                self?.resultLogURL = refineLogURL
                self?.runHeartbeatText = ""
                self?.screen = .result
                self?.selectedStep = 3
                self?.summaryText = summary
                self?.loadSafeRunResult(from: refineResultURL, treatAsPrimaryResult: false)
                if stopped {
                    self?.resultOutcome = .success
                    self?.toothSegRefineFailed = true
                    self?.activeResultFlavor = .craniofacial
                    self?.setSafeError(code: "toothseg_refine_cancelled", reason: "The ToothSeg high-resolution run was cancelled.", mpsState: "unknown")
                    self?.failureReasonText = "高精細化を停止しました。"
                    self?.statusText = "停止しました"
                    self?.progressText = "高精細化を停止しました。元の歯列・顎骨結果は引き続き利用できます。"
                    self?.resultMessage = "高精細化は停止しました"
                } else if rc == 0 {
                    self?.activeResultFlavor = .toothSeg
                    self?.toothSegRefineFailed = false
                    if surfacePreviewRC == 0 {
                        self?.runProgressFraction = 1.0
                        self?.resultOutcome = .success
                        self?.surfacePreviewFailed = false
                        self?.failureReasonText = ""
                        self?.statusText = "高精細化が完了"
                        self?.progressText = "高精細化ラベルマップと3D previewが生成されました。"
                        self?.resultMessage = "ToothSeg高精細化を作成しました"
                    } else {
                        self?.resultOutcome = .success
                        self?.surfacePreviewFailed = true
                        self?.setSafeError(code: "preview_generation_failed", reason: "The 3D preview could not be generated.", mpsState: "validated")
                        let reason = runFailureReason(from: previewLog)
                        self?.failureReasonText = reason.isEmpty ? "3D preview生成が完了できませんでした。詳細ログを確認してください。" : reason
                        self?.statusText = "高精細化は完了（3D preview未作成）"
                        self?.progressText = "高精細化の結果は保存されています。"
                        self?.resultMessage = "ToothSeg高精細化は完了しましたが、3D previewの再生成に失敗しました"
                    }
                    self?.canRunToothSegRefine = false
                } else {
                    self?.resultOutcome = .success
                    self?.toothSegRefineFailed = true
                    self?.activeResultFlavor = .craniofacial
                    if self?.safeErrorCode.isEmpty ?? true {
                        self?.setSafeError(
                            code: "toothseg_refine_failed",
                            reason: "The high-resolution ToothSeg refinement did not complete.",
                            mpsState: "validated"
                        )
                    }
                    let reason = self?.toothSegRefineFailureReason(from: refineLogURL) ?? ""
                    self?.failureReasonText = reason.isEmpty ? "ToothSeg高精細化を完了できませんでした。" : reason
                    self?.statusText = "高精細化を完了できませんでした"
                    self?.progressText = "原因を確認して高精細化を再実行してください。"
                    self?.resultMessage = "ToothSeg高精細化に失敗しました"
                }
            }
        }
    }

    private func loadSafeRunResult(from resultURL: URL? = nil, treatAsPrimaryResult: Bool = true) {
        guard let payload = readJSON(resultURL ?? paths.runResultJSON) else { return }
        safeErrorCode = payload["error_code"] as? String ?? ""
        safeErrorReason = payload["safe_reason"] as? String ?? ""
        safeMPSState = payload["mps_state"] as? String ?? "unknown"
        safeErrorOccurredAt = payload["occurred_at"] as? String ?? ""
        let detected = payload["teeth_detected"] as? Bool ?? false
        teethDetected = detected
        refineAvailable = payload["refine_available"] as? Bool ?? detected
        if treatAsPrimaryResult {
            primaryRunTeethDetected = detected
        }
    }

    private func toothSegRefineFailureReason(from logURL: URL) -> String {
        switch safeErrorCode {
        case "toothseg_mps_oom":
            return "MPSメモリ不足（Out Of Memory）で失敗しました。他のアプリを終了するかMacを再起動してから再試行してください。CTの範囲によっては、このMacでは高精細化を実行できない場合があります。"
        case "toothseg_input_invalid":
            return "元の歯列結果から有効な高精細化範囲を作成できませんでした。通常の歯列・顎骨結果は引き続き利用できます。"
        case "toothseg_download_failed", "toothseg_model_preparation_failed":
            return "ToothSegモデルの取得に失敗しました。ネットワーク状態を確認して再試行してください。"
        default:
            return runFailureReason(from: logURL)
        }
    }

    private func normalizeActiveResultFlavor() {
        let flavors = availableResultFlavors
        if flavors.isEmpty {
            activeResultFlavor = .craniofacial
            return
        }
        if !flavors.contains(activeResultFlavor) {
            activeResultFlavor = flavors.first ?? .craniofacial
        }
    }

    private func setResultFlavor(_ flavor: ResultOutputFlavor) {
        guard availableResultFlavors.contains(flavor) else { return }
        activeResultFlavor = flavor
        startSTLStatusMonitoring()
    }

    func setActiveResultFlavor(_ flavor: ResultOutputFlavor) {
        setResultFlavor(flavor)
    }

    private func setSafeError(code: String, reason: String, mpsState: String) {
        safeErrorCode = code
        safeErrorReason = reason
        safeMPSState = mpsState
        safeErrorOccurredAt = ISO8601DateFormatter().string(from: Date())
    }

    private func inputProvenancePayload() -> [String: Any] {
        let sourceKind: String
        if !dicomCleanCandidates.isEmpty {
            sourceKind = "dicom"
        } else if inputSource == .sample {
            sourceKind = "sample"
        } else {
            sourceKind = "nifti"
        }
        var payload: [String: Any] = [
            "schema": "totalsegmentator_wrapper_mac.input_provenance.v1",
            "source_kind": sourceKind,
        ]
        if let candidate = selectedDicomSeries {
            payload["series_key"] = candidate.seriesKey
            if let seriesNumber = candidate.seriesNumber {
                payload["series_number"] = seriesNumber
            }
            payload["series_description"] = candidate.description
            payload["selection_basis"] = dicomSelectionWasChanged ? "user_selected" : "first_geometry_ok"
        }
        return payload
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
            resultMessage = "3枚の画像を確認できないため、3Dプレビューへ進めません。別の画像を確認してください。"
            return
        }
        let acceptedInputURL = pendingPreparedInputURL
        let acceptedSlices = ctPreviewSlices
        let acceptedWarning = ctPreviewWarning
        clearPendingCTPreview()
        inputURL = acceptedInputURL
        inputSource = .nifti
        inputCTPreviewRequired = true
        inputCTPreviewSlices = acceptedSlices
        inputCTPreviewWarning = acceptedWarning
        inputCTPreviewVolumeEmpty = acceptedSlices.allSatisfy(\.uniformOrEmpty)
        inputCTPreviewFailed = acceptedSlices.count < 3
        outputURL = nil
        resultKind = .none
        dicomSummaryText = ""
        summaryText = ""
        resultMessage = ""
        failureReasonText = ""
        screen = .inputAndCreation
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

    private func clearInputCTPreview() {
        inputCTPreviewRequired = false
        inputCTPreviewSlices = []
        inputCTPreviewWarning = ""
        inputCTPreviewVolumeEmpty = false
        inputCTPreviewFailed = false
    }

    func goToStart() {
        guard !isRunning else { return }
        screen = .start
        selectedStep = 0
        statusText = "待機中"
        progressText = "Sampleか手元のCTデータを選んでください。"
        runHeartbeatText = ""
        runProgressFraction = nil
        failureReasonText = ""
        resultOutcome = .none
        dicomCleanCandidates = []
        selectedDicomSeriesID = nil
        pendingDicomSeriesID = nil
        dicomViewerExportCandidates = []
        selectedViewerExportCandidateID = nil
        resetSecondaryCaptureRescue()
        clearPendingCTPreview()
    }

    func goToSample() {
        guard !isRunning else { return }
        useSampleInput()
    }

    func goToOwnData() {
        guard !isRunning else { return }
        if inputSource == .sample {
            inputURL = nil
            inputSource = .none
        }
        outputURL = nil
        resultOutcome = .none
        screen = .inputAndCreation
        selectedStep = 1
        statusText = "CTデータを選んでください"
        progressText = "1つのボタンからDICOMフォルダまたはNIfTIファイルを選べます。"
    }

    func goToInputAndCreation() {
        guard !isRunning else { return }
        if inputSource != .sample && inputSource != .nifti {
            inputURL = nil
            inputSource = .none
        }
        outputURL = nil
        failureReasonText = ""
        resultOutcome = .none
        clearPendingCTPreview()
        screen = .inputAndCreation
        selectedStep = 1
        statusText = inputSource == .sample ? "Sample 1を選べます。" : "CTを選べます。"
        progressText = inputSource == .sample ? "Sample 1で流れを確認できます。" : "Sample 1またはCTを選んでください。"
        runHeartbeatText = ""
        runProgressFraction = nil
    }

    func goToInput() {
        guard !isRunning else { return }
        goToInputAndCreation()
        statusText = "入力を確認してください。"
        progressText = "設定を見直して再実行できます。"
        runHeartbeatText = ""
        runProgressFraction = nil
    }

    func retryRunFromResult() {
        guard !isRunning else { return }
        if resultKind == .dicomAudit {
            guard let lastDicomDirURL else {
                goToInputAndCreation()
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

    var canOpenSTLFolder: Bool {
        guard stlGenerationStatus == "complete", let outputURL else {
            return false
        }
        let directory = expectedSTLDirectoryURL(
            caseDir: outputURL,
            flavor: activeResultFlavor
        )
        return FileManager.default.fileExists(atPath: directory.path)
    }

    var stlFolderButtonTitle: String {
        switch stlGenerationStatus {
        case "pending", "running":
            return "STLを作成中…"
        case "failed":
            return "STL作成に失敗"
        case "inconsistent":
            return "STLフォルダが見つかりません"
        default:
            return "STLフォルダを開く"
        }
    }

    var stlGenerationStatusText: String {
        switch stlGenerationStatus {
        case "pending": return "STL生成待ちです。"
        case "running": return "STLをバックグラウンドで生成しています。"
        case "complete": return "STL作成済みです。"
        case "failed": return "STL作成に失敗しました。stl_generation.logを確認してください。"
        case "inconsistent": return "完了記録がありますが、STLフォルダが見つかりません。"
        default: return "STL生成状態を確認できません。"
        }
    }

    func openSTLFolder() {
        guard let outputURL else {
            resultMessage = "結果フォルダが見つかりません。"
            return
        }
        refreshSTLGenerationStatus()
        let directory = expectedSTLDirectoryURL(
            caseDir: outputURL,
            flavor: activeResultFlavor
        )
        guard stlGenerationStatus == "complete",
              FileManager.default.fileExists(atPath: directory.path)
        else {
            resultMessage = stlGenerationStatusText
            return
        }
        openURLInWorkspace(directory)
    }

    func openSTLGenerationLog() {
        guard let outputURL else {
            resultMessage = "結果フォルダが見つかりません。"
            return
        }
        let logURL = expectedSurfacePreviewOutputURL(
            caseDir: outputURL,
            flavor: activeResultFlavor
        ).appendingPathComponent("stl_generation.log")
        guard FileManager.default.fileExists(atPath: logURL.path) else {
            resultMessage = "STL生成ログが見つかりません。"
            return
        }
        openURLInWorkspace(logURL)
    }

    func startSTLStatusMonitoring() {
        stlStatusTimer?.invalidate()
        refreshSTLGenerationStatus()
        guard stlGenerationStatus == "pending" || stlGenerationStatus == "running" else {
            return
        }
        stlStatusTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) {
            [weak self] timer in
            self?.refreshSTLGenerationStatus()
            guard let status = self?.stlGenerationStatus,
                  status == "pending" || status == "running"
            else {
                timer.invalidate()
                return
            }
        }
    }

    func stopSTLStatusMonitoring() {
        stlStatusTimer?.invalidate()
        stlStatusTimer = nil
    }

    private func refreshSTLGenerationStatus() {
        guard resultKind == .inference, let outputURL else {
            stlGenerationStatus = "unavailable"
            return
        }
        let previewOutput = expectedSurfacePreviewOutputURL(
            caseDir: outputURL,
            flavor: activeResultFlavor
        )
        let summaryURL = previewOutput.appendingPathComponent("preview_summary.json")
        guard let summary = readJSON(summaryURL),
              let generation = summary["stl_generation"] as? [String: Any],
              let status = generation["status"] as? String
        else {
            stlGenerationStatus = "unavailable"
            return
        }
        if status == "complete" {
            let directory = previewOutput.appendingPathComponent("combined", isDirectory: true)
            stlGenerationStatus = FileManager.default.fileExists(atPath: directory.path)
                ? "complete"
                : "inconsistent"
            return
        }
        stlGenerationStatus = ["pending", "running", "failed"].contains(status)
            ? status
            : "unavailable"
    }

    func copySafeErrorInfo() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(safeErrorCopyText, forType: .string)
    }

    func openResultPreview() {
        guard let outputURL else {
            resultMessage = "3DプレビューHTMLが見つかりません。"
            return
        }
        let preview = expectedSurfacePreviewURL(caseDir: outputURL, flavor: activeResultFlavor)
        guard FileManager.default.fileExists(atPath: preview.path) else {
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
            statusText = "はじめの準備が必要です。"
            screen = .setup
            return
        }
        let logURL = outputURL.appendingPathComponent("logs/run.log")
            resultKind = .inference
            resultOutcome = .none
            failureReasonText = ""
            statusText = "3Dプレビュー作成中"
            progressText = "labelmap作成は再実行せず、3Dプレビューだけ作成しています。"
        runHeartbeatText = "3D表示用ファイルを作成しています。"
        runProgressFraction = nil
        isRunning = true
        stopRequested = false
        screen = .running
        selectedStep = 2
        activeLogURL = logURL
        resultLogURL = nil
        runner.resetTerminationRequest()
        startRunTimer()

        let sourceInput = expectedResultLabelmapURL(caseDir: outputURL, flavor: activeResultFlavor)
        let previewOutput = expectedSurfacePreviewOutputURL(
            caseDir: outputURL,
            flavor: activeResultFlavor
        )
        let command = CommandBuilder.surfacePreviewCommand(
            python: paths.venvPython,
            caseDir: outputURL,
            sourceInput: sourceInput,
            outputDir: previewOutput,
            smoothSurfaces: higherOrderResampling
        )
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
                    self?.resultOutcome = .failure
                    self?.setSafeError(code: "preview_generation_cancelled", reason: "The 3D preview generation was cancelled.", mpsState: "not_applicable")
                    self?.statusText = "停止しました"
                    self?.progressText = "3Dプレビューの再生成を停止しました。"
                    self?.resultMessage = "3Dプレビューの再生成を停止しました。"
                } else if rc == 0 {
                    self?.resultOutcome = .success
                    self?.surfacePreviewFailed = false
                    self?.failureReasonText = ""
                    self?.statusText = "完了"
                    self?.progressText = "3Dプレビューを確認できます。"
                    self?.resultMessage = "3Dプレビューを作成しました"
                } else {
                    self?.resultOutcome = .failure
                    self?.setSafeError(code: "preview_generation_failed", reason: "The 3D preview could not be generated.", mpsState: "not_applicable")
                    let reason = runFailureReason(from: logURL)
                    self?.surfacePreviewFailed = true
                    self?.failureReasonText = reason.isEmpty ? "3D preview生成が完了できませんでした。詳細ログを確認してください。" : reason
                    self?.statusText = "3Dプレビュー未作成"
                    self?.progressText = "3Dプレビュー生成に失敗しました。詳細ログを確認してください。"
                    self?.resultMessage = "3Dプレビュー生成に失敗しました。処理結果は保存されています。"
                }
            }
        }
    }

    func exportForSlicer() {
        guard let outputURL else {
            resultMessage = "結果フォルダが見つかりません。"
            return
        }
        guard FileManager.default.fileExists(atPath: paths.venvPython.path) else {
            statusText = "はじめの準備が必要です。"
            screen = .setup
            return
        }
        let logURL = outputURL.appendingPathComponent("logs/run.log")
        let source = inputURL.flatMap {
            isDirectory($0) || !FileManager.default.fileExists(atPath: $0.path) ? nil : $0
        }
        let command = CommandBuilder.slicerExportCommand(
            python: paths.venvPython,
            caseDir: outputURL,
            source: source
        )
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let runner = self.runner
        resultMessage = "Slicerで開くファイルを書き出しています。"
        isRunning = true
        activeLogURL = logURL
        resultLogURL = nil
        runner.resetTerminationRequest()

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = runner.run(command, environment: environment, logURL: logURL)
            DispatchQueue.main.async {
                self?.isRunning = false
                self?.refreshLog(from: logURL)
                self?.activeLogURL = nil
                self?.resultLogURL = logURL
                if rc == 0 {
                    let exportURL = outputURL.appendingPathComponent("slicer_export", isDirectory: true)
                    self?.resultMessage = "Slicerで開けるファイルを書き出しました。3D Slicerを手動で開き、フォルダ内のファイルをドラッグしてください。"
                    openURLInWorkspace(exportURL)
                } else {
                    self?.resultMessage = "Slicer用ファイルの書き出しに失敗しました。詳細ログを確認してください。"
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
        let version = (manifest["app_version"] as? String) ?? (manifest["version"] as? String) ?? "0.3.0"
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
        let executionState = runExecutionStateFromLog(text)
        let resolvedStage = executionState.stage
            ?? executionState.progress.flatMap(inferredRunStage(from:))
        if let stage = resolvedStage {
            if runStageEvent?.signature != stage.signature {
                runStageEvent = stage
                runStageProgress = nil
                runStageStartedAt = Date()
            }
            runStageProgress = executionState.progress
            let signature = stage.signature + "|" + (executionState.progress?.signature ?? "")
            if signature != lastRunProgressSignature {
                lastRunProgressSignature = signature
                lastRunProgressAt = Date()
            }
            runProgressFraction = runWeightedProgress?.estimate
        } else if let progress = executionState.progress {
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

    private func expectedResultLabelmapURL(
        caseDir: URL,
        flavor: ResultOutputFlavor = .craniofacial
    ) -> URL {
        switch flavor {
        case .craniofacial:
            if activeRunBackend == .dentalSegmentator {
                return caseDir
                    .appendingPathComponent("segmentations", isDirectory: true)
                    .appendingPathComponent("dentalsegmentator", isDirectory: true)
                    .appendingPathComponent("dentalsegmentator_multilabel.nii.gz")
            }
            return caseDir
                .appendingPathComponent("segmentations", isDirectory: true)
                .appendingPathComponent("derived", isDirectory: true)
                .appendingPathComponent("craniofacial_arch_jaw_multilabel.nii.gz")
        case .toothSeg:
            return caseDir
                .appendingPathComponent("segmentations", isDirectory: true)
                .appendingPathComponent("toothseg", isDirectory: true)
                .appendingPathComponent("toothseg_fdi_multilabel.nii.gz")
        }
    }

    private func expectedSurfacePreviewOutputURL(
        caseDir: URL,
        flavor: ResultOutputFlavor
    ) -> URL {
        switch flavor {
        case .craniofacial:
            return caseDir.appendingPathComponent("surface_preview", isDirectory: true)
        case .toothSeg:
            return caseDir.appendingPathComponent("surface_preview/toothseg", isDirectory: true)
        }
    }

    private func expectedSurfacePreviewURL(
        caseDir: URL,
        flavor: ResultOutputFlavor
    ) -> URL {
        let preferred = expectedSurfacePreviewOutputURL(caseDir: caseDir, flavor: flavor)
            .appendingPathComponent("index.html")
        if FileManager.default.fileExists(atPath: preferred.path) {
            return preferred
        }
        if flavor == .toothSeg {
            let legacy = caseDir.appendingPathComponent("surface_preview/index.html")
            if FileManager.default.fileExists(atPath: legacy.path) {
                return legacy
            }
        }
        return preferred
    }

    private func expectedSTLDirectoryURL(
        caseDir: URL,
        flavor: ResultOutputFlavor
    ) -> URL {
        expectedSurfacePreviewOutputURL(caseDir: caseDir, flavor: flavor)
            .appendingPathComponent("combined", isDirectory: true)
    }

    private func primaryResultLabelmapURL(
        caseDir: URL,
        backend: SegmentationBackend,
        runMode: RunMode
    ) -> URL {
        if backend == .dentalSegmentator {
            return caseDir
                .appendingPathComponent("segmentations", isDirectory: true)
                .appendingPathComponent("dentalsegmentator", isDirectory: true)
                .appendingPathComponent("dentalsegmentator_multilabel.nii.gz")
        }
        if backend == .toothSeg {
            return caseDir
                .appendingPathComponent("segmentations", isDirectory: true)
                .appendingPathComponent("toothseg", isDirectory: true)
                .appendingPathComponent("toothseg_fdi_multilabel.nii.gz")
        }
        if runMode == .individualTeeth {
            return caseDir
                .appendingPathComponent("segmentations", isDirectory: true)
                .appendingPathComponent("teeth_experimental", isDirectory: true)
                .appendingPathComponent("teeth_multilabel_fullspace.nii.gz")
        }
        return caseDir
                .appendingPathComponent("segmentations", isDirectory: true)
                .appendingPathComponent("derived", isDirectory: true)
                .appendingPathComponent("craniofacial_arch_jaw_multilabel.nii.gz")
    }

    private func primaryResultLabelmapPath() -> URL {
        guard let outputURL else {
            return paths.runResultJSON
        }
        return primaryResultLabelmapURL(
            caseDir: outputURL,
            backend: activeRunBackend,
            runMode: activeRunMode
        )
    }

    private func nextCaseOutput() -> URL {
        defaultRunOutput(root: outputRootURL ?? paths.runs)
    }

    private func resetRunProgressTracking() {
        lastRunProgressAt = nil
        lastRunProgressSignature = ""
        runStageEvent = nil
        runStageProgress = nil
        runStageStartedAt = nil
        if activeRunBackend == .dentalSegmentator {
            runHeartbeatText = "DentalSegmentatorの処理を継続しています。"
        } else if activeRunBackend == .toothSeg {
            runHeartbeatText = "ToothSegのsemantic/instance処理を継続しています。"
        } else if inputSource == .sample {
            runHeartbeatText = "Sample 1の処理を継続しています。"
        } else {
            runHeartbeatText = "ログを待っています。モデル準備済みでも初回処理には時間がかかる場合があります。"
        }
    }

    private func progressRoute(backend: SegmentationBackend, mode: RunMode) -> String {
        if backend == .dentalSegmentator { return "dentalsegmentator" }
        if backend == .toothSeg { return "toothseg_refine" }
        return mode == .individualTeeth ? "individual_teeth_beta" : "totalsegmentator"
    }

    private func beginStructuredRunProgress(route: String) {
        guard let stage = runStageCatalog[route]?.first else { return }
        runStageEvent = RunStageEvent(
            route: route, stageID: stage.id, index: 1,
            total: runStageCatalog[route]?.count ?? 1, label: stage.label
        )
        runStageProgress = nil
        runStageStartedAt = Date()
    }

    private func advanceStructuredRunToPreview(logURLs: [URL]) {
        guard let route = runStageEvent?.route,
              let stages = runStageCatalog[route],
              let stage = stages.last
        else { return }
        runStageEvent = RunStageEvent(
            route: route, stageID: stage.id, index: stages.count,
            total: stages.count, label: stage.label
        )
        runStageProgress = nil
        runStageStartedAt = Date()
        runProgressFraction = nil
        let line = runStageLogLine(runStageEvent!) + "\n"
        for url in Set(logURLs) {
            appendLog(line, to: url)
        }
    }

    private func updateRunHeartbeat(now: Date = Date()) {
        guard isRunning else {
            runHeartbeatText = ""
            return
        }
        guard let lastRunProgressAt else {
            if activeRunBackend == .dentalSegmentator {
                runHeartbeatText = "DentalSegmentatorの処理を継続しています。"
            } else if activeRunBackend == .toothSeg {
                runHeartbeatText = "ToothSegのsemantic/instance処理を継続しています。"
            } else if inputSource == .sample {
                runHeartbeatText = "Sample 1の処理を継続しています。"
            } else {
                runHeartbeatText = "ログを待っています。モデル準備済みでも初回処理には時間がかかる場合があります。"
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

func runFailureReason(from logURL: URL) -> String {
    guard let snapshot = readLogTail(logURL, maxBytes: LOG_TAIL_BYTES) else {
        return ""
    }
    let lines = snapshot.text
        .split(whereSeparator: \.isNewline)
        .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }
    guard !lines.isEmpty else {
        return ""
    }
    if lines.reversed().contains(where: { $0.lowercased().contains("mps backend out of memory") }) {
        return "MPSメモリ不足（Out Of Memory）で失敗しました。他のアプリを終了するかMacを再起動してから再試行してください。CTの範囲によっては、このMacでは高精細化を実行できない場合があります。"
    }
    if lines.reversed().contains(where: {
        let lower = $0.lowercased()
        return (lower.contains("download") || lower.contains("downloading"))
            && (lower.contains("error") || lower.contains("failed") || lower.contains("timed out"))
    }) {
        return "ダウンロード関連で失敗しました。ネットワーク状態を確認して再試行してください。"
    }
    if let inputLine = lines.reversed().first(where: {
        let lower = $0.lowercased()
        return lower.contains("no teeth")
            || lower.contains("teeth detected")
            || lower.contains("input") && lower.contains("empty")
    }) {
        return compactFailureLine(inputLine)
    }
    let markers = [
        "DENTALSEGMENTATOR FAILED",
        "DEVICE CHECK FAILED",
        "TASK BLOCKED",
        "TOOTHSEG FAILED",
        "TOOTHSEG_PREP_PROGRESS",
    ]
    if let markerIndex = lines.lastIndex(where: { line in markers.contains { line.contains($0) } }) {
        let following = lines.dropFirst(markerIndex + 1).first ?? lines[markerIndex]
        return compactFailureLine(following)
    }
    if let returncode = lines.last(where: { $0.hasPrefix("returncode=") && $0 != "returncode=0" }) {
        return "\(returncode)。詳細ログを確認してください。"
    }
    if let errorLine = lines.reversed().first(where: { line in
        let lower = line.lowercased()
        return lower.contains("error") || lower.contains("failed") || lower.contains("exception")
    }) {
        return compactFailureLine(errorLine)
    }
    return ""
}

func compactFailureLine(_ text: String) -> String {
    let normalized = text.replacingOccurrences(of: "\t", with: " ")
    guard normalized.count > 240 else {
        return normalized
    }
    return String(normalized.prefix(240)) + "..."
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
    let rescueCandidates = secondaryCaptureRescueCandidates(payload: payload)
    let viewerCandidates = viewerExportCandidates(payload: payload)
    if candidates.count == 1 {
        lines.append("通常のCTとして取り込める候補があります。自動で準備します。")
    } else if candidates.count > 1 {
        lines.append("通常のCTとして取り込める候補が複数あります。使用する撮影を選んでください。")
    } else if !rescueCandidates.isEmpty {
        lines.append("寸法情報が不足した断面画像があります。推定候補を手動で確認して参考用3Dプレビューへ進めます。")
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

func secondaryCaptureRescueCandidates(auditJSON: URL) -> [SecondaryCaptureRescueCandidate] {
    guard let payload = readJSON(auditJSON) else {
        return []
    }
    return secondaryCaptureRescueCandidates(payload: payload)
}

func secondaryCaptureRescueCandidates(payload: [String: Any]) -> [SecondaryCaptureRescueCandidate] {
    guard let series = payload["series"] as? [[String: Any]] else {
        return []
    }
    let candidates = series.compactMap { item -> SecondaryCaptureRescueCandidate? in
        guard let classification = item["classification"] as? [String: Any],
              let status = classification["status"] as? String,
              status == "secondary_capture_rescue_candidate"
                || status == "geometry_rescue_candidate"
                || status == "secondary_capture_reference_candidate",
              let seriesKey = item["series_key"] as? String,
              !seriesKey.isEmpty
        else {
            return nil
        }
        let description = ((item["series_description"] as? String) ?? "").uppercased()
        let imageType = ((item["image_type"] as? [String]) ?? []).joined(separator: " ").uppercased()
        let planeText = description + " " + imageType
        let explicitPlane = ((item["plane_label"] as? String)
            ?? (classification["plane_label"] as? String)
            ?? "").lowercased()
        let plane: String
        if explicitPlane.contains("axial") || planeText.contains("AXIAL") {
            plane = "axial"
        } else if explicitPlane.contains("coronal") || planeText.contains("CORONAL") {
            plane = "coronal"
        } else if explicitPlane.contains("sagittal") || planeText.contains("SAGITTAL") {
            plane = "sagittal"
        } else {
            plane = "unknown"
        }
        let role = (classification["rescue_role"] as? String)
            ?? (status == "secondary_capture_reference_candidate" ? "reference" : "primary")
        let reconstructionGroup: String
        if description.split(whereSeparator: { $0 == " " || $0 == "_" || $0 == "-" }).contains("BO") {
            reconstructionGroup = "BO"
        } else if description.split(whereSeparator: { $0 == " " || $0 == "_" || $0 == "-" }).contains("ST") {
            reconstructionGroup = "ST"
        } else {
            reconstructionGroup = "未分類"
        }
        let sliceThickness: Double? = {
            if let summary = item["slice_thickness"] as? [String: Any],
               summary["consistent"] as? Bool == true,
               let values = summary["values_mm"] as? [Any] {
                return values.compactMap(jsonDouble).first
            }
            return jsonDouble(item["slice_thickness_mm"])
        }()
        let spacingBetweenSlices: Double? = {
            guard let summary = item["spacing_between_slices"] as? [String: Any],
                  summary["consistent"] as? Bool == true,
                  let values = summary["values_mm"] as? [Any]
            else {
                return nil
            }
            return values.compactMap(jsonDouble).first
        }()
        let pixelSpacing = item["pixel_spacing_mm"] as? [String: Any]
        let contentManifestSHA256: String? = {
            guard let manifest = item["ordered_content_manifest"] as? [String: Any],
                  manifest["algorithm"] as? String == "sha256",
                  let hash = manifest["manifest_sha256"] as? String,
                  hash.count == 64,
                  hash.allSatisfy({ $0.isHexDigit })
            else {
                return nil
            }
            return hash.lowercased()
        }()
        return SecondaryCaptureRescueCandidate(
            seriesKey: seriesKey,
            seriesNumber: jsonInt(item["series_number"]),
            classificationStatus: status,
            plane: plane,
            role: role,
            reconstructionGroup: reconstructionGroup,
            fileCount: jsonInt(item["effective_frame_count"]) ?? jsonInt(item["file_count"]) ?? 0,
            rows: jsonInt(item["rows"]) ?? 0,
            columns: jsonInt(item["columns"]) ?? 0,
            pixelSpacingRow: pixelSpacing.flatMap { jsonDouble($0["row"]) },
            pixelSpacingColumn: pixelSpacing.flatMap { jsonDouble($0["column"]) },
            projectedSliceSpacing: jsonDouble(item["projected_slice_spacing_mm"]),
            spacingBetweenSlices: spacingBetweenSlices,
            sliceThickness: sliceThickness,
            contentManifestSHA256: contentManifestSHA256,
            studyKeySHA256: item["study_key_sha256"] as? String
        )
    }
    let primaryGroups = Dictionary(
        grouping: candidates.filter { $0.role == "primary" },
        by: { $0.studyKeySHA256 ?? "" }
    )
    guard let selectedStudy = primaryGroups.max(by: { lhs, rhs in
        let lhsCount = lhs.value.reduce(0) { $0 + $1.fileCount }
        let rhsCount = rhs.value.reduce(0) { $0 + $1.fileCount }
        return lhsCount == rhsCount ? lhs.key > rhs.key : lhsCount < rhsCount
    })?.key else {
        return []
    }
    return candidates.filter { ($0.studyKeySHA256 ?? "") == selectedStudy }
}

func rescueStackManifestSHA256(_ manifestJSON: URL) -> String? {
    guard let payload = readJSON(manifestJSON),
          payload["schema"] as? String == "totalsegmentator_wrapper_mac.rescue_stack.v1",
          payload["status"] as? String == "success",
          let source = payload["source"] as? [String: Any],
          source["algorithm"] as? String == "sha256",
          let hash = source["manifest_sha256"] as? String,
          hash.count == 64,
          hash.allSatisfy({ $0.isHexDigit }),
          let ordering = payload["ordering"] as? [String: Any],
          ordering["ambiguous"] as? Bool == false
    else {
        return nil
    }
    return hash.lowercased()
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

func preparedRescueNiftiURL(
    metadataJSON: URL,
    validationJSON: URL,
    requested: RescueSpacing
) -> URL? {
    let readbackSpacing = (readJSON(validationJSON)?["requested_spacing"] as? [Any])?.compactMap(jsonDouble) ?? []
    guard requested.isValid,
          let metadata = readJSON(metadataJSON),
          let validation = readJSON(validationJSON),
          validation["status"] as? String == "success",
          validation["patched_spacing_matches_requested"] as? Bool == true,
          readbackSpacing.count == 3,
          zip(readbackSpacing, [requested.x, requested.y, requested.z])
            .allSatisfy({ abs($0.0 - $0.1) <= 0.0001 }),
          let patched = validation["patched_nifti"] as? [String: Any],
          (patched["ok"] as? Bool) == true,
          let outputs = metadata["outputs"] as? [String: Any],
          let path = outputs["patched_nifti"] as? String,
          !path.isEmpty
    else {
        return nil
    }
    return URL(fileURLWithPath: path)
}

func rescueFinalizedNiftiMatches(
    outputNifti: URL,
    metadataJSON: URL,
    requested: RescueSpacing
) -> Bool {
    guard requested.isValid,
          FileManager.default.fileExists(atPath: outputNifti.path),
          let payload = readJSON(metadataJSON),
          payload["workflow_status"] as? String == "finalized",
          (payload["inference_started"] as? Bool) != true,
          let confirmed = payload["confirmed"] as? [String: Any],
          let confirmedValues = confirmed["confirmed_spacing_xyz"] as? [Any],
          confirmedValues.compactMap(jsonDouble).count == 3,
          zip(confirmedValues.compactMap(jsonDouble), [requested.x, requested.y, requested.z])
            .allSatisfy({ abs($0.0 - $0.1) <= 0.0001 }),
          let validation = payload["output_validation"] as? [String: Any],
          validation["affine_consistent"] as? Bool == true,
          validation["voxel_payload_consistent"] as? Bool == true,
          validation["input_hash_matches"] as? Bool == true,
          let shape = validation["shape"] as? [Any],
          shape.compactMap(jsonInt).count == 3,
          shape.compactMap(jsonInt).allSatisfy({ $0 > 0 }),
          let spacing = validation["spacing_xyz"] as? [Any],
          spacing.compactMap(jsonDouble).count == 3,
          spacing.compactMap(jsonDouble).allSatisfy({ $0.isFinite && $0 > 0 })
    else {
        return false
    }
    return true
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
                rowSpacingMM: nil,
                columnSpacingMM: nil,
                minValue: jsonDouble(preview["min"]) ?? 0.0,
                maxValue: jsonDouble(preview["max"]) ?? 0.0,
                uniformOrEmpty: (preview["uniform_or_empty"] as? Bool) ?? true
            )
        )
    }
    return slices
}

func rescuePreviewSlices(payload: [String: Any]) -> [CTPreviewSlice] {
    let previewPayload = (payload["outputs"] as? [String: Any]) ?? payload
    let previews = (previewPayload["mpr_preview"] as? [[String: Any]])
        ?? (previewPayload["previews"] as? [[String: Any]])
        ?? []
    return previews.compactMap { preview in
        guard let plane = preview["plane"] as? String,
              let path = preview["path"] as? String,
              !path.isEmpty
        else {
            return nil
        }
        return CTPreviewSlice(
            plane: plane,
            label: japanesePreviewPlaneLabel(plane),
            url: URL(fileURLWithPath: path),
            width: jsonInt(preview["width"]) ?? 0,
            height: jsonInt(preview["height"]) ?? 0,
            rowSpacingMM: jsonDouble(preview["row_spacing_mm"]),
            columnSpacingMM: jsonDouble(preview["column_spacing_mm"]),
            minValue: jsonDouble(preview["min"]) ?? 0,
            maxValue: jsonDouble(preview["max"]) ?? 0,
            uniformOrEmpty: (preview["uniform_or_empty"] as? Bool) ?? false
        )
    }
}

func parsedRescuePseudo3DPreviewURL(payload: [String: Any]) -> URL? {
    let previewPayload = (payload["outputs"] as? [String: Any]) ?? payload
    let path = (previewPayload["pseudo_3d_preview"] as? String)
        ?? (previewPayload["pseudo_3d_preview_path"] as? String)
    guard let path, !path.isEmpty else { return nil }
    return URL(fileURLWithPath: path)
}

func makeCTPreviewWarning(slices: [CTPreviewSlice]) -> String {
    let expectedPlanes = Set(["axial", "coronal", "sagittal"])
    let availablePlanes = Set(slices.map(\.plane))
    if !expectedPlanes.isSubset(of: availablePlanes) {
        return "3枚の画像を作れませんでした。別の画像か別のCTを選んでください。"
    }
    if slices.allSatisfy(\.uniformOrEmpty) {
        return "画像がほとんど見えません。別の画像か別のCTを選んでください。"
    }
    return ""
}

func niftiPreviewVolumeIsUniformOrEmpty(metadataJSON: URL) -> Bool {
    guard let payload = readJSON(metadataJSON),
          let volume = payload["volume"] as? [String: Any]
    else {
        return false
    }
    return (volume["uniform_or_empty"] as? Bool) ?? false
}

func makeInputCTPreviewWarning(
    slices: [CTPreviewSlice],
    volumeEmpty: Bool,
    failed: Bool
) -> String {
    if volumeEmpty {
        return "CT画像の内容を確認できません。空の画像、または正しく書き出されていない画像の可能性があります。"
    }
    if failed || Set(slices.map(\.plane)).count < 3 {
        return "CTの簡易プレビューを作成できませんでした。別のCTを選んでください。"
    }
    if slices.contains(where: \.uniformOrEmpty) {
        return "一部の断面がほぼ空に見えます。顎顔面が含まれているか確認してください。"
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
        return "寸法確認が必要な救済候補"
    case "geometry_rescue_candidate":
        return "一部の寸法確認が必要なCT"
    case "secondary_capture_reference_candidate":
        return "三方向推定の参照系列"
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
    case "prepare_rescue_with_explicit_spacing":
        return "寸法候補を確認"
    case "use_as_rescue_reference_series":
        return "推定根拠として使用"
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
    let etaSeconds: Int?
    let route: String?
    let stageID: String?
    let scope: String

    var signature: String {
        let stepText = step.map { String($0) } ?? ""
        let totalText = total.map { String($0) } ?? ""
        let percentText = percent.map { String($0) } ?? ""
        return "\(route ?? "")|\(stageID ?? "")|\(scope)|\(stage ?? "")|\(stepText)|\(totalText)|\(percentText)|\(etaSeconds ?? -1)"
    }

    var fraction: Double? {
        if let percent {
            return max(0.0, min(1.0, Double(percent) / 100.0))
        }
        if let step, let total, total > 0 {
            return max(0.0, min(1.0, Double(step) / Double(total)))
        }
        return nil
    }

    var displayText: String {
        let rawStage = stage.flatMap { $0.isEmpty ? nil : $0 } ?? "処理"
        let stageText = rawStage == "ToothSeg semantic" ? "ToothSeg semantic枝" : (rawStage == "ToothSeg instance" ? "ToothSeg instance枝" : rawStage)
        if percent == 100 {
            return "\(stageText)を終え、次の処理へ進んでいます。"
        }
        var parts = ["\(stageText)を進めています。"]
        if let step, let total, let percent {
            parts.append("\(step)/\(total)（\(percent)%）")
        }
        if let etaSeconds, etaSeconds > 0 {
            parts.append("残り約\(formatCompactDuration(etaSeconds))")
        }
        return parts.joined(separator: " ")
    }
}

private let runStageCatalog: [String: [(id: String, label: String)]] = [
    "totalsegmentator": [
        ("prepare", "実行準備"), ("segment", "顎顔面を抽出中"),
        ("finalize", "結果を整理中"), ("preview", "3D表示・結果情報を作成中"),
    ],
    "dentalsegmentator": [
        ("prepare", "入力準備"), ("predict", "DentalSegmentatorで推論中"),
        ("finalize", "ラベル結果を整理中"), ("preview", "3D表示・結果情報を作成中"),
    ],
    "individual_teeth_beta": [
        ("prepare", "実行準備"), ("craniofacial", "顎顔面を抽出中"),
        ("roi", "歯列ROIを作成中"), ("individual", "歯を1本ずつ抽出中"),
        ("restore", "元画像へ復元中"), ("preview", "3D表示・結果情報を作成中"),
    ],
    "toothseg_refine": [
        ("roi", "12mm ROI・入力を準備中"), ("semantic", "ToothSeg semantic枝"),
        ("instance", "ToothSeg instance枝"), ("restore", "FDI番号付与・元画像へ復元中"),
        ("preview", "3D表示・結果情報を作成中"),
    ],
]

func inferredRunStage(from progress: RunLogProgress) -> RunStageEvent? {
    guard let route = progress.route,
          let stageID = progress.stageID,
          let stages = runStageCatalog[route],
          let offset = stages.firstIndex(where: { $0.id == stageID })
    else { return nil }
    return RunStageEvent(
        route: route,
        stageID: stageID,
        index: offset + 1,
        total: stages.count,
        label: stages[offset].label
    )
}

func runStageLogLine(_ stage: RunStageEvent) -> String {
    let payload: [String: Any] = [
        "route": stage.route,
        "stage_id": stage.stageID,
        "index": stage.index,
        "total": stage.total,
        "label": stage.label,
    ]
    guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]),
          let json = String(data: data, encoding: .utf8)
    else { return "" }
    return "RUN_STAGE " + json
}

struct RunStageEvent: Equatable {
    let route: String
    let stageID: String
    let index: Int
    let total: Int
    let label: String

    var signature: String { "\(route)|\(stageID)|\(index)|\(total)" }
}

struct RunExecutionLogState {
    let stage: RunStageEvent?
    let progress: RunLogProgress?
    let hasStructuredStages: Bool
}

struct RunProgressProfile {
    let route: String
    let weights: [Double]

    static let profiles: [String: RunProgressProfile] = [
        "totalsegmentator": .init(route: "totalsegmentator", weights: [0.01, 0.68, 0.01, 0.30]),
        "dentalsegmentator": .init(route: "dentalsegmentator", weights: [0.01, 0.91, 0.01, 0.07]),
        "individual_teeth_beta": .init(route: "individual_teeth_beta", weights: [0.01, 0.62, 0.01, 0.29, 0.01, 0.06]),
        "toothseg_refine": .init(route: "toothseg_refine", weights: [0.01, 0.19, 0.79, 0.006, 0.004]),
    ]
}

struct RunWeightedProgress {
    let lowerBound: Double
    let upperBound: Double
    let estimate: Double?
    let stageFraction: Double?

    static func calculate(stage: RunStageEvent, progress: RunLogProgress?) -> RunWeightedProgress? {
        guard let profile = RunProgressProfile.profiles[stage.route],
              profile.weights.count == stage.total,
              stage.index > 0,
              stage.index <= profile.weights.count
        else { return nil }
        let lower = profile.weights.prefix(stage.index - 1).reduce(0, +)
        let upper = lower + profile.weights[stage.index - 1]
        let matchingStageProgress = progress.flatMap { candidate -> Double? in
            guard candidate.scope == "stage",
                  candidate.route == stage.route,
                  candidate.stageID == stage.stageID
            else { return nil }
            return candidate.fraction
        }
        return RunWeightedProgress(
            lowerBound: lower,
            upperBound: upper,
            estimate: matchingStageProgress.map { lower + profile.weights[stage.index - 1] * $0 },
            stageFraction: matchingStageProgress
        )
    }
}

func runExecutionStateFromLog(_ text: String) -> RunExecutionLogState {
    var currentStage: RunStageEvent?
    var currentProgress: RunLogProgress?
    var hasStructuredStages = false
    for rawLine in text.split(whereSeparator: \.isNewline) {
        let line = String(rawLine)
        if line.hasPrefix("RUN_STAGE ") {
            let jsonText = String(line.dropFirst("RUN_STAGE ".count))
            if let data = jsonText.data(using: .utf8),
               let object = try? JSONSerialization.jsonObject(with: data),
               let payload = object as? [String: Any],
               let route = stringFromJSON(payload["route"]),
               let stageID = stringFromJSON(payload["stage_id"]),
               let index = intFromJSON(payload["index"]),
               let total = intFromJSON(payload["total"]),
               let label = stringFromJSON(payload["label"]) {
                currentStage = RunStageEvent(
                    route: route, stageID: stageID, index: index, total: total, label: label
                )
                currentProgress = nil
                hasStructuredStages = true
            }
            continue
        }
        guard line.hasPrefix("RUN_PROGRESS ") else { continue }
        let jsonText = String(line.dropFirst("RUN_PROGRESS ".count))
        guard let data = jsonText.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data),
              let payload = object as? [String: Any]
        else { continue }
        currentProgress = RunLogProgress(
            step: intFromJSON(payload["step"]),
            total: intFromJSON(payload["total"]),
            percent: intFromJSON(payload["percent"]),
            stage: stringFromJSON(payload["stage"]),
            etaSeconds: intFromJSON(payload["eta_seconds"]),
            route: stringFromJSON(payload["route"]),
            stageID: stringFromJSON(payload["stage_id"]),
            scope: stringFromJSON(payload["scope"]) ?? "subtask"
        )
    }
    return RunExecutionLogState(
        stage: currentStage, progress: currentProgress, hasStructuredStages: hasStructuredStages
    )
}

func runProgressFromLog(_ text: String) -> RunLogProgress? {
    runExecutionStateFromLog(text).progress
}

struct ToothSegPreparationProgress {
    let stage: String
    let message: String
    let downloadedBytes: Int?
    let totalBytes: Int?
    let percent: Int?
    let rateBPS: Double?
    let etaSeconds: Int?
    let resumed: Bool

    var fraction: Double? {
        if let percent { return max(0, min(1, Double(percent) / 100)) }
        if let downloadedBytes, let totalBytes, totalBytes > 0 {
            return max(0, min(1, Double(downloadedBytes) / Double(totalBytes)))
        }
        return stage == "complete" ? 1.0 : nil
    }

    var detailText: String {
        guard stage == "download" else { return "" }
        var parts: [String] = []
        if let downloadedBytes, let totalBytes {
            parts.append("\(formatByteCount(downloadedBytes)) / \(formatByteCount(totalBytes))")
        }
        if let percent { parts.append("\(percent)%") }
        if let rateBPS, rateBPS > 0 { parts.append("\(formatByteCount(Int(rateBPS)))/秒") }
        if let etaSeconds, etaSeconds > 0 { parts.append("残り約\(formatCompactDuration(etaSeconds))") }
        if resumed { parts.append("中断位置から再開") }
        return parts.joined(separator: " ・ ")
    }
}

func toothSegPreparationProgressFromLog(_ text: String) -> ToothSegPreparationProgress? {
    var last: ToothSegPreparationProgress?
    for rawLine in text.split(whereSeparator: \.isNewline) {
        let line = String(rawLine)
        guard line.hasPrefix("TOOTHSEG_PREP_PROGRESS ") else { continue }
        let jsonText = String(line.dropFirst("TOOTHSEG_PREP_PROGRESS ".count))
        guard let data = jsonText.data(using: .utf8),
              let payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let stage = payload["stage"] as? String,
              let message = payload["message"] as? String else { continue }
        last = ToothSegPreparationProgress(
            stage: stage,
            message: message,
            downloadedBytes: intFromJSON(payload["downloaded_bytes"]),
            totalBytes: intFromJSON(payload["total_bytes"]),
            percent: intFromJSON(payload["percent"]),
            rateBPS: (payload["rate_bps"] as? NSNumber)?.doubleValue,
            etaSeconds: intFromJSON(payload["eta_seconds"]),
            resumed: (payload["resumed"] as? Bool) ?? false
        )
    }
    return last
}

func formatCompactDuration(_ seconds: Int) -> String {
    let safe = max(0, seconds)
    let hours = safe / 3600
    let minutes = (safe % 3600) / 60
    let remaining = safe % 60
    if hours > 0 { return "\(hours)時間\(minutes)分" }
    if minutes > 0 { return "\(minutes)分\(String(format: "%02d", remaining))秒" }
    return "\(remaining)秒"
}

func formatByteCount(_ bytes: Int) -> String {
    ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
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
