import Foundation
import AppKit
import Combine
import CoreFoundation
import Dispatch
import CryptoKit
import UniformTypeIdentifiers
import Darwin

let LOG_TAIL_BYTES = 64 * 1024
let MAX_UPDATE_DMG_BYTES: Int64 = 4 * 1024 * 1024 * 1024

private let dentalSegmentatorArchiveSHA256 = "bc5510cc93bc2100ab1faccb63512e09c1ca326c738b0a9939c074d82b38a4ac"
private let dentalSegmentatorSHA256Provenance = "locally-observed official asset verified against publisher MD5"
private let dentalSegmentatorRuntimeRelativePaths = [
    "nnUNetTrainer__nnUNetPlans__3d_fullres/dataset.json",
    "nnUNetTrainer__nnUNetPlans__3d_fullres/plans.json",
    "nnUNetTrainer__nnUNetPlans__3d_fullres/fold_0/checkpoint_final.pth",
]
private let toothSegDatasetIDs = ["121", "123"]
private let toothSegDatasetNames = [
    "Dataset121_ToothFairy2_Teeth",
    "Dataset123_ToothFairy2fixed_teeth_spacing02_brd3px",
]
private let toothSegRuntimeRelativePaths = [
    "Dataset121_ToothFairy2_Teeth/nnUNetTrainer_onlyMirror01_DASegOrd0__nnUNetPlans__3d_fullres_resample_torch_256_bs8_ctnorm/dataset.json",
    "Dataset121_ToothFairy2_Teeth/nnUNetTrainer_onlyMirror01_DASegOrd0__nnUNetPlans__3d_fullres_resample_torch_256_bs8_ctnorm/plans.json",
    "Dataset121_ToothFairy2_Teeth/nnUNetTrainer_onlyMirror01_DASegOrd0__nnUNetPlans__3d_fullres_resample_torch_256_bs8_ctnorm/fold_5/checkpoint_final.pth",
    "Dataset123_ToothFairy2fixed_teeth_spacing02_brd3px/nnUNetTrainer__nnUNetPlans__3d_fullres_resample_torch_192_bs8_ctnorm/dataset.json",
    "Dataset123_ToothFairy2fixed_teeth_spacing02_brd3px/nnUNetTrainer__nnUNetPlans__3d_fullres_resample_torch_192_bs8_ctnorm/plans.json",
    "Dataset123_ToothFairy2fixed_teeth_spacing02_brd3px/nnUNetTrainer__nnUNetPlans__3d_fullres_resample_torch_192_bs8_ctnorm/fold_5/checkpoint_final.pth",
]

enum AppScreen {
    case setup
    case start
    case inputAndCreation
    case iosMesh
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

enum IOSMeshJaw: String, CaseIterable, Identifiable {
    case upper
    case lower

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .upper: return "上顎"
        case .lower: return "下顎"
        }
    }
}

struct IOSMeshModelPreparationFailure {
    let code: String
    let reason: String
    let statusText: String
}

func iosMeshModelPreparationFailure(for sourceCode: String?) -> IOSMeshModelPreparationFailure {
    switch sourceCode {
    case "model_download_failed":
        return IOSMeshModelPreparationFailure(
            code: "ios_mesh_model_download_failed",
            reason: "The MeshSegNet model download did not complete.",
            statusText: "MeshSegNetモデルを取得できませんでした。ネットワーク状態を確認して再試行してください。"
        )
    case "model_integrity_failed":
        return IOSMeshModelPreparationFailure(
            code: "ios_mesh_model_integrity_failed",
            reason: "The MeshSegNet model failed integrity verification.",
            statusText: "MeshSegNetモデルの完全性を確認できませんでした。もう一度実行して再取得してください。"
        )
    case "model_prepare_busy":
        return IOSMeshModelPreparationFailure(
            code: "ios_mesh_model_prepare_busy",
            reason: "MeshSegNet model preparation is already running.",
            statusText: "MeshSegNetモデルを別の処理で準備中です。しばらく待ってから再試行してください。"
        )
    default:
        return IOSMeshModelPreparationFailure(
            code: "ios_mesh_model_prepare_failed",
            reason: "The MeshSegNet model preparation did not complete.",
            statusText: "MeshSegNetモデルを準備できませんでした。詳細ログを確認してください。"
        )
    }
}

struct SafeRunResultFields: Equatable {
    let errorCode: String
    let reason: String
    let mpsState: String
    let occurredAt: String
    let runAttemptID: String
    let failedStage: String
    let specificCause: String
    let retryable: String
    let recoveryHintCode: String
    let diagnosticLogKind: String
    let diagnosticLogReference: String
    let backendVersion: String
    let modelVersion: String
    let runtimePythonVersion: String
    let runtimeTorchVersion: String
    let inputKind: String
    let inputSizeBucket: String
    let actualDevice: String
    let fallbackUsed: String
}

private let safeRunResultSchema = "totalsegmentator_wrapper_mac.safe_run_result.v1"
private let safeRunResultStatuses: Set<String> = ["success", "failed"]

func isCurrentSafeRunResultPayload(
    _ payload: [String: Any],
    expectedRunAttemptID: String
) -> Bool {
    guard payload["schema"] as? String == safeRunResultSchema,
          let status = payload["status"] as? String,
          safeRunResultStatuses.contains(status),
          let canonicalExpectedAttemptID = safeRunAttemptID(expectedRunAttemptID),
          canonicalExpectedAttemptID == expectedRunAttemptID,
          let suppliedAttemptID = payload["run_attempt_id"] as? String,
          suppliedAttemptID == canonicalExpectedAttemptID else {
        return false
    }
    return true
}

func safeRunResultFields(from payload: [String: Any]) -> SafeRunResultFields {
    let rawCode = payload["error_code"] as? String
    let code = rawCode.flatMap { $0.isEmpty ? nil : canonicalSafeErrorCode($0) } ?? ""
    let runAttemptID = safeRunAttemptID(payload["run_attempt_id"]) ?? ""
    let rawDiagnosticReference = safeRunAttemptID(payload["diagnostic_log_reference"])
    let matchingDiagnosticReference = rawDiagnosticReference == runAttemptID ? runAttemptID : ""
    let diagnosticKind = matchingDiagnosticReference.isEmpty
        ? "none"
        : safeRunDiagnosticToken(
            payload["diagnostic_log_kind"],
            allowed: ["local_engineering_diagnostic"],
            fallback: "none"
        )
    let diagnosticReference = diagnosticKind == "local_engineering_diagnostic"
        ? matchingDiagnosticReference
        : ""
    return SafeRunResultFields(
        errorCode: code,
        reason: code.isEmpty ? "" : safeReasonForErrorCode(code),
        mpsState: safeMPSDiagnosticState(payload["mps_state"] as? String),
        occurredAt: canonicalDiagnosticTimestamp(payload["occurred_at"] as? String) ?? "",
        runAttemptID: runAttemptID,
        failedStage: safeRunDiagnosticToken(
            payload["failed_stage"],
            allowed: [
                "backend_launch", "backend_inference", "preflight_execution_profile",
                "preflight_mps_validation", "preflight_model_validation", "preflight_unknown",
            ],
            fallback: "unknown"
        ),
        specificCause: safeRunDiagnosticToken(
            payload["specific_cause"],
            allowed: [
                "backend_process_exited_nonzero", "backend_process_launch_failed",
                "mps_requirement_not_met", "mps_validation_failed",
                "model_preparation_required", "setup_weights_validation_failed",
                "preflight_validation_failed",
            ],
            fallback: "unknown"
        ),
        retryable: safeRunDiagnosticBool(payload["retryable"]),
        recoveryHintCode: safeRunDiagnosticToken(
            payload["recovery_hint_code"],
            allowed: [
                "review_local_log_then_retry", "select_mps_then_retry",
                "restore_mps_then_retry", "prepare_model_then_retry",
                "rerun_setup_then_retry", "review_setup_then_retry",
            ],
            fallback: "unknown"
        ),
        diagnosticLogKind: diagnosticKind,
        diagnosticLogReference: diagnosticReference,
        backendVersion: safeRunDiagnosticToken(
            payload["backend_version"],
            allowed: ["2.14.0", "unknown"],
            fallback: "unknown"
        ),
        modelVersion: safeRunDiagnosticToken(
            payload["model_version"],
            allowed: ["2.14.0", "unknown"],
            fallback: "unknown"
        ),
        runtimePythonVersion: safeRunDiagnosticToken(
            payload["runtime_python_version"],
            allowed: ["3.12", "unknown"],
            fallback: "unknown"
        ),
        runtimeTorchVersion: safeRunDiagnosticToken(
            payload["runtime_torch_version"],
            allowed: ["2.12.0", "unknown"],
            fallback: "unknown"
        ),
        inputKind: safeRunDiagnosticToken(
            payload["input_kind"],
            allowed: ["nifti", "unknown"],
            fallback: "unknown"
        ),
        inputSizeBucket: safeRunDiagnosticToken(
            payload["input_size_bucket"],
            allowed: ["lt_10_mib", "10_to_100_mib", "100_to_500_mib", "ge_500_mib", "unknown"],
            fallback: "unknown"
        ),
        actualDevice: safeRunDiagnosticToken(
            payload["actual_device"],
            allowed: ["cpu", "mps", "unknown"],
            fallback: "unknown"
        ),
        fallbackUsed: safeRunDiagnosticBool(payload["fallback_used"])
    )
}

private func safeRunAttemptID(_ value: Any?) -> String? {
    guard let string = value as? String,
          UUID(uuidString: string) != nil else {
        return nil
    }
    return string.lowercased()
}

private func safeRunDiagnosticToken(
    _ value: Any?,
    allowed: Set<String>,
    fallback: String
) -> String {
    guard let string = value as? String,
          isSafeDiagnosticToken(string),
          allowed.contains(string) else {
        return fallback
    }
    return string
}

private func safeRunDiagnosticBool(_ value: Any?) -> String {
    if let value = value as? Bool {
        return value ? "true" : "false"
    }
    if let value = value as? String,
       value == "true" || value == "false" {
        return value
    }
    return "unknown"
}

func safeRunErrorReportText(
    fields: SafeRunResultFields,
    appVersion: String,
    osVersion: String,
    architecture: String,
    feature: String,
    fallbackInputKind: String,
    timestamp: String
) -> String {
    // The AppState call site normally supplies fields that have already passed
    // safeRunResultFields. Normalize again here so this shared text formatter
    // remains a one-way boundary even when future callers construct fields.
    let safeFields = safeRunResultFields(from: [
        "error_code": fields.errorCode.isEmpty ? "operation_failed" : fields.errorCode,
        "mps_state": fields.mpsState,
        "occurred_at": fields.occurredAt,
        "run_attempt_id": fields.runAttemptID,
        "failed_stage": fields.failedStage,
        "specific_cause": fields.specificCause,
        "retryable": fields.retryable,
        "recovery_hint_code": fields.recoveryHintCode,
        "diagnostic_log_kind": fields.diagnosticLogKind,
        "diagnostic_log_reference": fields.diagnosticLogReference,
        "backend_version": fields.backendVersion,
        "model_version": fields.modelVersion,
        "runtime_python_version": fields.runtimePythonVersion,
        "runtime_torch_version": fields.runtimeTorchVersion,
        "input_kind": fields.inputKind,
        "input_size_bucket": fields.inputSizeBucket,
        "actual_device": fields.actualDevice,
        "fallback_used": fields.fallbackUsed,
    ])
    let safeFeatures: Set<String> = [
        "DentalSegmentator（実験的）", "ToothSeg（個別歯・実験的）",
        "ToothSeg高精細化", "口腔内スキャン（PLY/STL）",
        "歯列と顎骨の3Dプレビュー", "歯を1本ずつ分ける（ベータ）",
    ]
    let safeInputKinds: Set<String> = [
        "dicom", "dicom_rescue", "ios_mesh", "nifti", "sample", "unknown",
    ]
    let safeFeature = safeFeatures.contains(feature) ? feature : "unknown"
    let safeFallbackInputKind = safeInputKinds.contains(fallbackInputKind)
        ? fallbackInputKind
        : "unknown"
    let safeInputKind = safeFields.inputKind == "unknown"
        ? safeFallbackInputKind
        : safeFields.inputKind
    let safeTimestamp = canonicalDiagnosticTimestamp(timestamp)
        ?? ISO8601DateFormatter().string(from: Date())
    let safeOSVersion = safeSystemDiagnosticValue(osVersion)
    let lines = [
        "report_schema=totalsegmentator_wrapper_mac.safe_error_report.v1",
        "app_version=\(isSafeDiagnosticToken(appVersion) ? appVersion : "unknown")",
        "os_version=\(safeOSVersion)",
        "architecture=\(architecture == "arm64" || architecture == "x86_64" ? architecture : "unknown")",
        "feature=\(safeFeature)",
        "input_kind=\(safeInputKind)",
        "run_attempt_id=\(safeFields.runAttemptID.isEmpty ? "unknown" : safeFields.runAttemptID)",
        "failed_stage=\(safeFields.failedStage)",
        "specific_cause=\(safeFields.specificCause)",
        "mps_state=\(safeFields.mpsState)",
        "actual_device=\(safeFields.actualDevice)",
        "fallback_used=\(safeFields.fallbackUsed)",
        "reason=\(safeFields.reason)",
        "timestamp=\(safeTimestamp)",
        "error_code=\(safeFields.errorCode)",
        "retryable=\(safeFields.retryable)",
        "recovery_hint_code=\(safeFields.recoveryHintCode)",
        "diagnostic_log_kind=\(safeFields.diagnosticLogKind)",
        "diagnostic_log_reference=\(safeFields.diagnosticLogReference.isEmpty ? "none" : safeFields.diagnosticLogReference)",
        "backend_version=\(safeFields.backendVersion)",
        "model_version=\(safeFields.modelVersion)",
        "runtime_python_version=\(safeFields.runtimePythonVersion)",
        "runtime_torch_version=\(safeFields.runtimeTorchVersion)",
        "input_size_bucket=\(safeFields.inputSizeBucket)",
    ]
    return lines.joined(separator: "\n")
}

private func safeSystemDiagnosticValue(_ value: String) -> String {
    guard !value.isEmpty,
          value.count <= 80,
          !value.contains("\n"),
          !value.contains("\r"),
          value.unicodeScalars.allSatisfy({ scalar in
              CharacterSet.letters.contains(scalar)
                  || CharacterSet.decimalDigits.contains(scalar)
                  || CharacterSet(charactersIn: " .()_-").contains(scalar)
          }) else {
        return "unknown"
    }
    return value
}

private func canonicalSafeErrorCode(_ rawCode: String?) -> String {
    guard let rawCode,
          isSafeDiagnosticToken(rawCode),
          knownSafeErrorReason(rawCode) != nil else {
        return "operation_failed"
    }
    return rawCode
}

private func safeReasonForErrorCode(_ code: String) -> String {
    knownSafeErrorReason(code) ?? "The requested operation did not complete."
}

private func knownSafeErrorReason(_ code: String) -> String? {
    switch code {
    case "operation_failed":
        return "The requested operation did not complete."
    case "runner_failed":
        return "The segmentation run did not complete."
    case "mps_required":
        return "This app execution profile requires MPS."
    case "mps_unavailable":
        return "MPS validation did not pass for this app run."
    case "totalseg_setup_weights_missing_or_invalid":
        return "The app setup models are missing or failed validation."
    case "insufficient_disk_space":
        return "Model preparation could not continue because available disk space is insufficient."
    case "dentalseg_prepare_required":
        return "Prepare the DentalSegmentator model before starting an app-profile run."
    case "toothseg_prepare_required":
        return "Prepare the ToothSeg model before starting an app-profile run."
    case "backend_failed":
        return "The segmentation backend did not complete."
    case "totalseg_backend_nonzero_exit":
        return "The TotalSegmentator backend exited without completing the requested inference."
    case "totalseg_backend_launch_failed":
        return "The TotalSegmentator backend could not start."
    case "dentalseg_failed":
        return "DentalSegmentator did not complete."
    case "toothseg_failed":
        return "ToothSeg did not complete."
    case "toothseg_mps_oom":
        return "ToothSeg exceeded available MPS memory after dental ROI preparation."
    case "toothseg_input_invalid":
        return "ToothSeg could not create a valid dental ROI from the existing teeth result."
    case "toothseg_download_failed":
        return "The ToothSeg model download did not complete."
    case "cancelled":
        return "The operation was cancelled before completion."
    case "preview_generation_failed":
        return "The 3D preview could not be generated."
    case "preview_generation_cancelled":
        return "The 3D preview generation was cancelled."
    case "toothseg_refine_failed":
        return "The high-resolution ToothSeg refinement did not complete."
    case "toothseg_refine_cancelled":
        return "The ToothSeg high-resolution run was cancelled."
    case "toothseg_model_preparation_failed":
        return "ToothSeg model preparation did not complete."
    case "dentalseg_model_preparation_failed":
        return "DentalSegmentator model preparation did not complete."
    case "dicom_audit_failed":
        return "The CT data could not be prepared for preview."
    case "dicom_audit_cancelled":
        return "The CT data check was cancelled."
    case "dicom_conversion_failed":
        return "The CT data could not be converted."
    case "dicom_conversion_cancelled":
        return "The CT conversion was cancelled."
    case "viewer_export_failed":
        return "The CT slice data could not be prepared."
    case "viewer_export_cancelled":
        return "The CT slice preparation was cancelled."
    case "stl_generation_failed":
        return "STL generation did not complete."
    case "ios_mesh_cancelled":
        return "The intraoral mesh run was cancelled."
    case "ios_mesh_inference_failed":
        return "The intraoral mesh run did not produce tooth STL files on strict MPS."
    case "ios_mesh_model_download_failed":
        return "The MeshSegNet model download did not complete."
    case "ios_mesh_model_integrity_failed":
        return "The MeshSegNet model failed integrity verification."
    case "ios_mesh_model_prepare_busy":
        return "MeshSegNet model preparation is already running."
    case "ios_mesh_model_prepare_failed":
        return "The MeshSegNet model preparation did not complete."
    default:
        return nil
    }
}

private func safeMPSDiagnosticState(_ rawState: String?) -> String {
    let allowed: Set<String> = [
        "cpu", "failed_or_unknown", "not_applicable", "not_required",
        "required", "unavailable", "unknown", "validated",
    ]
    guard let rawState,
          isSafeDiagnosticToken(rawState),
          allowed.contains(rawState) else {
        return "unknown"
    }
    return rawState
}

private func canonicalDiagnosticTimestamp(_ rawTimestamp: String?) -> String? {
    guard let rawTimestamp,
          rawTimestamp.count <= 40,
          !rawTimestamp.contains("\n"),
          !rawTimestamp.contains("\r") else {
        return nil
    }
    let options: [ISO8601DateFormatter.Options] = [
        [.withInternetDateTime, .withFractionalSeconds],
        [.withInternetDateTime],
    ]
    for option in options {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = option
        if let date = parser.date(from: rawTimestamp) {
            return ISO8601DateFormatter().string(from: date)
        }
    }
    return nil
}

func safeTGNetValidationDetail(from payload: [String: Any]?) -> String {
    guard let code = payload?["error_code"] as? String,
          isSafeDiagnosticToken(code) else {
        return "重みの検証処理を完了できませんでした。詳細はローカルログで確認できます。"
    }
    switch code {
    case "tgnet_selection_invalid":
        return "指定のckpts(new).zip、またはその展開済みフォルダを選択してください。"
    case "tgnet_checkpoint_set_incomplete":
        return "必要な2つのcheckpointが揃っていないか、配置が異なります。"
    case "tgnet_checkpoint_hash_mismatch":
        return "checkpointが指定の配布セットと一致しません。"
    case "tgnet_checkpoint_archive_invalid":
        return "ZIPを安全に展開して確認できませんでした。"
    case "tgnet_validation_failed":
        return "重みの検証処理を完了できませんでした。詳細はローカルログで確認できます。"
    default:
        return "重みの検証処理を完了できませんでした。詳細はローカルログで確認できます。"
    }
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

/// The only rescue-origin data that may survive into an inference result.
///
/// This deliberately contains fixed classification tokens and digests only.
/// It must never contain DICOM paths, UIDs, descriptions, patient metadata, or
/// editable raw geometry JSON.
struct RescueInputContext: Equatable {
    let classification: String
    let sourceManifestSHA256: String
    let confirmationSHA256: String
    let transformSHA256: String
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

private func runtimeManifestIsStructurallyValid(
    _ value: Any?,
    root: URL,
    expectedRelativePaths: [String]
) -> Bool {
    guard let manifest = value as? [[String: Any]], manifest.count == expectedRelativePaths.count else {
        return false
    }
    var seenPaths = Set<String>()
    let expectedKeys = Set(["path", "size_bytes", "sha256"])
    for (index, entry) in manifest.enumerated() {
        guard Set(entry.keys) == expectedKeys,
              let relativePath = entry["path"] as? String,
              runtimeRelativePathIsSafe(relativePath),
              relativePath == expectedRelativePaths[index],
              seenPaths.insert(relativePath).inserted,
              let expectedSize = positiveJSONInteger(entry["size_bytes"]),
              isCanonicalLowercaseSHA256(entry["sha256"]),
              runtimeFileIsRegularAndMatchesSize(
                  root: root,
                  relativePath: relativePath,
                  expectedSize: expectedSize
              ) else {
            return false
        }
    }
    return seenPaths == Set(expectedRelativePaths)
}

private func runtimeRelativePathIsSafe(_ value: String) -> Bool {
    guard !value.isEmpty, !value.hasPrefix("/"), !value.contains("\\") else {
        return false
    }
    let components = value.split(separator: "/", omittingEmptySubsequences: false)
    return !components.isEmpty && components.allSatisfy { component in
        !component.isEmpty && component != "." && component != ".."
    }
}

private func positiveJSONInteger(_ value: Any?) -> Int64? {
    guard let number = value as? NSNumber,
          CFGetTypeID(number) != CFBooleanGetTypeID() else {
        return nil
    }
    let doubleValue = number.doubleValue
    let integerValue = number.int64Value
    guard doubleValue.isFinite,
          doubleValue == Double(integerValue),
          integerValue > 0 else {
        return nil
    }
    return integerValue
}

private func isCanonicalLowercaseSHA256(_ value: Any?) -> Bool {
    guard let digest = value as? String, digest.utf8.count == 64 else {
        return false
    }
    return digest.utf8.allSatisfy { byte in
        (byte >= Character("0").asciiValue! && byte <= Character("9").asciiValue!)
            || (byte >= Character("a").asciiValue! && byte <= Character("f").asciiValue!)
    }
}

private func runtimeFileIsRegularAndMatchesSize(
    root: URL,
    relativePath: String,
    expectedSize: Int64? = nil
) -> Bool {
    guard runtimeRelativePathIsSafe(relativePath) else {
        return false
    }
    do {
        let rootValues = try root.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard rootValues.isDirectory == true, rootValues.isSymbolicLink != true else {
            return false
        }
        let components = relativePath.split(separator: "/").map(String.init)
        var current = root
        for (index, component) in components.enumerated() {
            let isFinal = index == components.count - 1
            current.appendPathComponent(component, isDirectory: !isFinal)
            let values = try current.resourceValues(
                forKeys: [.isDirectoryKey, .isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]
            )
            guard values.isSymbolicLink != true else {
                return false
            }
            if isFinal {
                guard values.isRegularFile == true,
                      let actualSize = values.fileSize,
                      actualSize > 0,
                      expectedSize == nil || Int64(actualSize) == expectedSize else {
                    return false
                }
            } else if values.isDirectory != true {
                return false
            }
        }
        return true
    } catch {
        return false
    }
}

private func markerHasValidDentalArchiveProvenance(_ marker: [String: Any]) -> Bool {
    guard let legacyMigrated = marker["legacy_marker_migrated"] as? Bool else {
        return false
    }
    if legacyMigrated {
        return (marker["archive_sha256"] == nil || marker["archive_sha256"] is NSNull)
            && (marker["sha256_provenance"] == nil || marker["sha256_provenance"] is NSNull)
            && marker["archive_md5_verified"] as? Bool == false
            && marker["archive_sha256_verified"] as? Bool == false
    }
    return marker["archive_md5_verified"] as? Bool == true
        && marker["archive_sha256"] as? String == dentalSegmentatorArchiveSHA256
        && marker["archive_sha256_verified"] as? Bool == true
        && marker["sha256_provenance"] as? String == dentalSegmentatorSHA256Provenance
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
    private let tgnetValidationRunner = ProcessRunner()
    private var logTimer: Timer?
    private var dentalPreparationTimer: Timer?
    private var stlStatusTimer: Timer?
    private var dentalPreparationCancellationRequested = false
    private var startedAt: Date?
    private var lastLogText = ""
    private var activeLogURL: URL?
    private var resultLogURL: URL?
    var lastDicomDirURL: URL?
    private var lastDicomAuditJSONURL: URL?
    private var lastRunProgressAt: Date?
    private var lastRunProgressSignature = ""
    private var rescueCalibrationRecords: [[String: Any]] = []
    private var rescuePreparationCancellationRequested = false
    private var rescuePreviewWorkItem: DispatchWorkItem?
    private var rescueInputContext: RescueInputContext?
    private var setupAttemptID = UUID().uuidString.lowercased()
    private var runAttemptID = UUID().uuidString.lowercased()
    private var setupFailureOccurredAt = ""
    private var setupReturnCode: Int?
    private var lastSetupStage = SetupStep.idle.rawValue

    @Published var screen: AppScreen = .setup
    @Published var selectedStep = 0
    @Published var setupStep: SetupStep = .idle
    @Published var setupHint = SetupStep.idle.hint
    @Published var setupRunning = false
    @Published var setupElapsed = "経過時間: 0秒"
    @Published var setupMessage = "管理者権限は不要です。App Support配下にだけ書き込みます。"
    @Published var setupError = ""
    @Published var setupDownloadProgress: SetupDownloadProgress?

    @Published var logText = ""
    @Published var logInfoText = "詳細ログは最後の一部だけ表示します。全文はログファイルで確認できます。"
    @Published var showLog = false
    @Published var showDicomSeriesSelection = false
    @Published var showDentalPreparationConfirmation = false
    @Published var showDentalPreparationSheet = false
    @Published var dentalPreparationRunning = false
    @Published var dentalPreparationFailed = false
    @Published var dentalPreparationElapsed = "経過時間: 0秒"
    @Published var dentalPreparationMessage = "DentalSegmentatorのモデルを準備します。"
    @Published var dentalPreparationFraction: Double?
    @Published var dentalPreparationDetail = ""
    @Published var pendingModelPreparationChoice: CreationChoice = .dentalSegmentatorExperimental
    @Published var modelPreparationPurpose: ModelPreparationPurpose = .creationSelection
    private var dentalPreparationStartedAt: Date?

    @Published var inputURL: URL?
    @Published var inputSource: InputSource = .none
    @Published var iosMeshJaw: IOSMeshJaw = .upper {
        didSet {
            guard oldValue != iosMeshJaw else { return }
            iosMeshInputURL = nil
            iosMeshOutputURL = nil
            iosMeshSucceeded = false
            iosMeshToothCount = 0
            iosMeshDownloadProgress = nil
            iosMeshGingivaPresent = nil
            selectedStep = 1
            iosMeshStatus = "\(iosMeshJaw.displayName)のPLYまたはSTLを選択してください。"
        }
    }
    @Published var iosMeshInputURL: URL?
    @Published var iosMeshCustomModelURL: URL?
    @Published var iosMeshOutputURL: URL?
    @Published var iosMeshRunning = false
    @Published var iosMeshStatus = "上顎のPLYまたはSTLを選択してください。"
    @Published var iosMeshSucceeded = false
    @Published var iosMeshToothCount = 0
    @Published var iosMeshDownloadProgress: SetupDownloadProgress?
    @Published var iosMeshGingivaPresent: Bool?
    @Published var iosMeshTGNetValidationRunning = false
    @Published var iosMeshTGNetValidationError = ""
    @Published var iosMeshTGNetValidationDetail = ""
    private var iosMeshPreparingModel = false
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
    private var safeRunAttemptID = ""
    private var safeRunFailedStage = "unknown"
    private var safeRunSpecificCause = "unknown"
    private var safeRunRetryable = "unknown"
    private var safeRunRecoveryHintCode = "unknown"
    private var safeRunDiagnosticLogKind = "none"
    private var safeRunDiagnosticLogReference = ""
    private var safeRunBackendVersion = "unknown"
    private var safeRunModelVersion = "unknown"
    private var safeRunRuntimePythonVersion = "unknown"
    private var safeRunRuntimeTorchVersion = "unknown"
    private var safeRunInputKind = "unknown"
    private var safeRunInputSizeBucket = "unknown"
    private var safeRunActualDevice = "unknown"
    private var safeRunFallbackUsed = "unknown"
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
    @Published var pendingUpdateFileSizeBytes: Int?
    @Published var showingUpdateConfirmation = false
    @Published var updateCheckRunning = false
    @Published var updateInstallRunning = false
    @Published var updateInstallProgressFraction: Double?
    @Published var updateInstallProgressText = ""
    @Published private(set) var uiPreviewScenario = ""
    private var pendingUpdateAllowedHosts: [String] = []
    private var pendingUpdateManifestURL: URL?
    private var updateDownloadTask: URLSessionDownloadTask?
    private var updateDownloadProgressObservation: NSKeyValueObservation?

    var isUIPreviewMode: Bool {
        !uiPreviewScenario.isEmpty
    }

    var hasRescueInputContext: Bool {
        rescueInputContext != nil
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

    var showsDentalPreparationFailureActions: Bool {
        dentalPreparationFailed && !dentalPreparationRunning
    }

    var isDentalSegmentatorModelReady: Bool {
        guard let marker = readJSON(paths.dentalsegReadyMarker) else {
            return false
        }
        guard marker["schema"] as? String == "totalsegmentator_wrapper_mac.dentalsegmentator_model_status.v1"
            && marker["model_state"] as? String == "ready"
            && marker["expected_md5"] as? String == dentalsegExpectedMD5
            && marker["dataset_id"] as? String == "112"
            && marker["dataset_name"] as? String == "Dataset112_DentalSegmentator_v100"
            && markerHasValidDentalArchiveProvenance(marker) else {
            return false
        }
        // This SwiftUI-facing check intentionally validates the signed marker structure,
        // safe regular-file paths, and sizes without hashing multi-GB checkpoints during
        // view recomputation. Python's strict preflight remains the final content-hash and
        // checkpoint ZIP/CRC authority before inference.
        return runtimeManifestIsStructurallyValid(
            marker["runtime_files"],
            root: paths.dentalsegReadyMarker.deletingLastPathComponent(),
            expectedRelativePaths: dentalSegmentatorRuntimeRelativePaths
        )
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
        guard marker["schema"] as? String == "totalsegmentator_wrapper_mac.toothseg_model_status.v1"
            && marker["model_state"] as? String == "ready"
            && marker["expected_md5"] as? String == toothsegExpectedMD5
            && marker["pair_distributions_sha256"] as? String == toothsegPairDistributionsSHA256
            && marker["semantic_mps_patch_size"] as? [Int] == toothsegSemanticMPSPatchSize
            && marker["dataset_ids"] as? [String] == toothSegDatasetIDs
            && marker["dataset_names"] as? [String] == toothSegDatasetNames
            && runtimeFileIsRegularAndMatchesSize(
                root: paths.toothsegRoot,
                relativePath: "fdi_pair_distrs.json"
            ) else {
            return false
        }
        // Content digests and semantic-plan contents are verified by Python's strict
        // preflight. Keeping large-file hashing out of this computed property prevents
        // SwiftUI refreshes from blocking on checkpoint I/O.
        return runtimeManifestIsStructurallyValid(
            marker["runtime_files"],
            root: paths.toothsegResults,
            expectedRelativePaths: toothSegRuntimeRelativePaths
        )
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
        if reason == "app_running_from_disk_image" {
            return "DMGや外部ボリューム内から直接実行せず、アプリをApplicationsまたは~/Applicationsへコピーし、コピー先から開き直してセットアップしてください。"
        }
        return setupRecoverySuggestion(reason)
    }

    private func setupErrorText(for reason: String?) -> String {
        if reason == "app_running_from_disk_image" {
            return "DMGや外部ボリューム内からアプリを直接実行しているため、セットアップを開始できません。"
        }
        return setupReasonToJapanese(reason)
    }

    var safeSetupErrorCopyText: String {
        let state = readJSON(paths.stateJSON) ?? [:]
        let stateIsCurrentFailure = safeSetupString(state["status"]) == "failed"
        let appVersion = currentAppVersion()
        let occurredAt = setupFailureOccurredAt.isEmpty
            ? ISO8601DateFormatter().string(from: Date())
            : setupFailureOccurredAt
        let reasonCode = safeSetupReasonCode(from: state)
        let reportedAttemptID = (stateIsCurrentFailure
            ? safeSetupAttemptID(state["setup_attempt_id"])
            : nil)
            ?? setupAttemptID
        let reportedReturnCode = (stateIsCurrentFailure
            ? jsonInt(state["return_code"])
            : nil)
            ?? setupReturnCode
        var lines = [
            "report_schema=totalsegmentator_wrapper_mac.safe_setup_error_report.v1",
            "app_version=\(isSafeDiagnosticToken(appVersion) ? appVersion : "unknown")",
            "os_version=\(ProcessInfo.processInfo.operatingSystemVersionString)",
            "architecture=\(currentArchitectureName())",
            "feature=setup",
            "setup_stage=\(safeSetupStage(from: state))",
            "reason_code=\(reasonCode)",
            "timestamp=\(occurredAt)",
            "setup_attempt_id=\(reportedAttemptID)",
            "retryable=\(setupRetryableState(for: reasonCode))",
            "recovery_hint_code=\(setupRecoveryHintCode(for: reasonCode))",
            "diagnostic_log_kind=local_setup_log",
            "diagnostic_log_reference=\(reportedAttemptID)",
        ]
        if let reportedReturnCode {
            lines.append("return_code=\(reportedReturnCode)")
        }
        if let pythonVersion = safeSetupString(state["python_version"]) {
            lines.append("python_version=\(pythonVersion)")
        }
        if let installedBundle = state["installed_bundle"] as? [String: Any] {
            if let dependencySetID = safeSetupString(installedBundle["dependency_set_id"]) {
                lines.append("dependency_set_id=\(dependencySetID)")
            }
            if let buildID = safeSetupString(installedBundle["build_id"]) {
                lines.append("build_id=\(buildID)")
            }
        }
        return lines.joined(separator: "\n")
    }

    private func safeSetupString(_ value: Any?) -> String? {
        guard let string = value as? String,
              isSafeDiagnosticToken(string) else {
            return nil
        }
        return string
    }

    private func safeSetupReasonCode(from state: [String: Any]) -> String {
        let allowedReasonCodes: Set<String> = [
            "app_running_from_disk_image", "bundle_manifest_invalid",
            "bundled_wheel_invalid", "bundled_wheel_install_failed",
            "constraints_missing", "dentalseg_weights_download_failed",
            "dependency_build_failed", "dependency_distribution_unavailable",
            "dependency_network_failed", "dependency_resolution_failed",
            "dependency_consistency_failed",
            "dependency_set_id_changed", "constraints_sha256_changed",
            "requirements_lock_sha256_changed", "dependency_lock_metadata_sha256_changed",
            "fpsample_wheel_sha256_changed", "acvl_utils_wheel_sha256_changed",
            "insufficient_disk_space", "installed_package_missing_or_invalid",
            "installed_bundled_dependency_missing_or_invalid",
            "legacy_setup_state", "mps_unavailable",
            "needs_network", "normalizer_missing", "python312_missing",
            "python_version_unsupported", "resource_only_change",
            "runtime_install_failed", "runtime_refresh_failed", "setup_busy",
            "setup_exception", "setup_missing", "setup_weights_missing_or_invalid",
            "setup_weights_manifest_sha256_changed", "setup_lock_failed",
            "totalseg_privacy_config_failed", "venv_missing",
            "venv_python_changed", "weights_download_failed",
            "weights_integrity_failed", "weights_manifest_incompatible",
            "weights_setup_busy", "wheel_changed", "wheel_marker_missing_or_stale",
            "wheel_missing", "wheel_resync",
        ]
        guard let reasonCode = safeSetupString(state["reason"]),
              allowedReasonCodes.contains(reasonCode) else {
            return "setup_failed"
        }
        return reasonCode
    }

    private func safeSetupAttemptID(_ value: Any?) -> String? {
        guard let string = value as? String,
              UUID(uuidString: string) != nil else {
            return nil
        }
        return string.lowercased()
    }

    private func safeSetupStage(from state: [String: Any]) -> String {
        let allowedStages: Set<String> = [
            "acquire_setup_lock", "bootstrap_install", "configure_totalseg_privacy",
            "create_app_support_dirs", "create_venv", "doctor",
            "download_dentalseg_weights", "download_totalseg_weights",
            "install_bundled_wheels", "install_wheel", "read_bundle_manifest",
            "setup_exception", "sync_bundle", "validate_bundled_wheels",
            "validate_python_312", "verify_dependencies",
        ]
        if let steps = state["steps"] as? [[String: Any]] {
            for status in ["failed", "running"] {
                if let name = steps.reversed().first(where: {
                    safeSetupString($0["status"]) == status
                }).flatMap({ safeSetupString($0["name"]) }),
                   allowedStages.contains(name) {
                    return name
                }
            }
            if let name = steps.reversed().compactMap({ safeSetupString($0["name"]) })
                .first(where: allowedStages.contains) {
                return name
            }
        }
        return isSafeDiagnosticToken(lastSetupStage) ? lastSetupStage : "setup_unknown"
    }

    private func setupRetryableState(for reasonCode: String) -> String {
        switch reasonCode {
        case "needs_network", "dependency_network_failed", "insufficient_disk_space",
             "bundled_wheel_install_failed", "runtime_install_failed", "weights_download_failed",
             "dentalseg_weights_download_failed", "setup_busy",
             "weights_integrity_failed", "weights_setup_busy",
             "setup_weights_missing_or_invalid",
             "setup_weights_manifest_sha256_changed",
             "dependency_set_id_changed", "constraints_sha256_changed",
             "requirements_lock_sha256_changed", "dependency_lock_metadata_sha256_changed",
             "fpsample_wheel_sha256_changed", "acvl_utils_wheel_sha256_changed",
             "installed_package_missing_or_invalid",
             "installed_bundled_dependency_missing_or_invalid",
             "dependency_consistency_failed",
             "venv_missing", "venv_python_changed", "setup_missing":
            return "true"
        case "app_running_from_disk_image":
            return "true"
        case "python_version_unsupported", "mps_unavailable",
             "weights_manifest_incompatible", "bundle_manifest_invalid",
             "bundled_wheel_invalid":
            return "false"
        default:
            return "unknown"
        }
    }

    private func setupRecoveryHintCode(for reasonCode: String) -> String {
        switch reasonCode {
        case "app_running_from_disk_image":
            return "copy_to_applications_then_retry"
        case "setup_busy", "weights_setup_busy":
            return "wait_for_existing_setup_then_retry"
        case "needs_network", "dependency_network_failed", "weights_download_failed",
             "dentalseg_weights_download_failed":
            return "restore_network_then_retry"
        case "insufficient_disk_space":
            return "free_disk_space_then_retry"
        case "mps_unavailable":
            return "restart_mac_then_retry"
        default:
            return "review_local_setup_log_then_retry"
        }
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
        let normalized = safeRunResultFields(from: [
            "error_code": safeErrorCode.isEmpty ? "operation_failed" : safeErrorCode,
            "mps_state": safeMPSState,
            "occurred_at": safeErrorOccurredAt,
            "run_attempt_id": safeRunAttemptID,
            "failed_stage": safeRunFailedStage,
            "specific_cause": safeRunSpecificCause,
            "retryable": safeRunRetryable,
            "recovery_hint_code": safeRunRecoveryHintCode,
            "diagnostic_log_kind": safeRunDiagnosticLogKind,
            "diagnostic_log_reference": safeRunDiagnosticLogReference,
            "backend_version": safeRunBackendVersion,
            "model_version": safeRunModelVersion,
            "runtime_python_version": safeRunRuntimePythonVersion,
            "runtime_torch_version": safeRunRuntimeTorchVersion,
            "input_kind": safeRunInputKind,
            "input_size_bucket": safeRunInputSizeBucket,
            "actual_device": safeRunActualDevice,
            "fallback_used": safeRunFallbackUsed,
        ])
        let report = safeRunErrorReportText(
            fields: normalized,
            appVersion: currentAppVersion(),
            osVersion: ProcessInfo.processInfo.operatingSystemVersionString,
            architecture: currentArchitectureName(),
            feature: safeErrorFeatureText,
            fallbackInputKind: safeErrorInputKind,
            timestamp: normalized.occurredAt
        )
        return ([report] + safeDicomErrorCopyLines).joined(separator: "\n")
    }

    var safeDicomErrorCopyLines: [String] {
        var lines: [String] = []
        if let rescue = rescueInputContext {
            // RescueInputContext is constructed from fixed tokens and canonical
            // SHA-256 digests only; do not add any raw DICOM-derived field here.
            lines.append("dicom_rescue=true")
            lines.append("dicom_rescue_classification=\(rescue.classification)")
            lines.append("dicom_rescue_source_manifest_sha256=\(rescue.sourceManifestSHA256)")
            lines.append("dicom_rescue_confirmation_sha256=\(rescue.confirmationSHA256)")
            lines.append("dicom_rescue_transform_sha256=\(rescue.transformSHA256)")
        }
        guard resultKind == .dicomAudit,
              let auditJSON = lastDicomAuditJSONURL,
              let payload = readJSON(auditJSON)
        else {
            return lines
        }
        if let status = payload["status"] as? String,
           isSafeDiagnosticToken(status) {
            lines.append("dicom_audit_status=\(status)")
        }
        if let reason = payload["reason"] as? String,
           isSafeDiagnosticToken(reason) {
            lines.append("dicom_audit_reason=\(reason)")
        }
        if let count = jsonInt(payload["series_count"]) {
            lines.append("dicom_series_count=\(count)")
        }
        if let counts = payload["classification_counts"] as? [String: Any] {
            let safeCounts = counts.compactMap { key, value -> String? in
                guard isSafeDiagnosticToken(key), let count = jsonInt(value) else {
                    return nil
                }
                return "\(key):\(count)"
            }.sorted()
            if !safeCounts.isEmpty {
                lines.append("dicom_classification_counts=\(safeCounts.joined(separator: ","))")
            }
        }
        if let causes = payload["possible_causes"] as? [String] {
            let safeCauses = causes.filter(isSafeDiagnosticToken).sorted()
            if !safeCauses.isEmpty {
                lines.append("dicom_possible_causes=\(safeCauses.joined(separator: ","))")
            }
        }
        return lines
    }

    var safeErrorInputKind: String {
        let code = safeErrorCode.isEmpty ? "" : canonicalSafeErrorCode(safeErrorCode)
        if code.hasPrefix("ios_mesh_") {
            return "ios_mesh"
        }
        if rescueInputContext != nil {
            return "dicom_rescue"
        }
        if resultKind == .dicomAudit || inputSource == .dicomFolder {
            return "dicom"
        }
        switch inputSource {
        case .sample:
            return "sample"
        case .nifti:
            return "nifti"
        case .none:
            return "unknown"
        case .dicomFolder:
            return "dicom"
        }
    }

    var safeErrorFeatureText: String {
        let code = safeErrorCode.isEmpty ? "" : canonicalSafeErrorCode(safeErrorCode)
        if code.hasPrefix("ios_mesh_") {
            return "口腔内スキャン（PLY/STL）"
        }
        if code.hasPrefix("dentalseg_") {
            return "DentalSegmentator（実験的）"
        }
        if code == "insufficient_disk_space", dentalPreparationFailed {
            return pendingModelPreparationChoice == .toothSegExperimental
                ? "ToothSeg（個別歯・実験的）"
                : "DentalSegmentator（実験的）"
        }
        if code == "toothseg_model_preparation_failed",
           dentalPreparationFailed,
           modelPreparationPurpose == .creationSelection {
            return "ToothSeg（個別歯・実験的）"
        }
        if toothSegRefineFailed || code.hasPrefix("toothseg_") {
            return "ToothSeg高精細化"
        }
        return creationChoice.rawValue
    }

    var errorReportFormURL: URL {
        URL(string: "https://forms.gle/QFPwF1Pi5C8bmSuw6")!
    }

    var tgnetCheckpointPageURL: URL {
        URL(
            string: "https://drive.google.com/drive/folders/15oP0CZM_O_-Bir18VbSM8wRUEzoyLXby?usp=sharing"
        )!
    }

    var iosMeshUsesTGNetFinal: Bool {
        guard let model = iosMeshCustomModelURL else {
            return false
        }
        return model.hasDirectoryPath || model.pathExtension.lowercased() == "zip"
    }

    var iosMeshGingivaStatusText: String {
        switch iosMeshGingivaPresent {
        case true:
            return iosMeshUsesTGNetFinal
                ? "歯別STLとは別に、歯肉を gingiva.stl として保存しました。"
                : "label 0の「歯肉または背景候補」を gingiva.stl として保存しました。背景を含む可能性があるため目視確認してください。"
        case false:
            return iosMeshUsesTGNetFinal
                ? "この結果では歯肉領域が検出されなかったため、gingiva.stl は作成されませんでした。"
                : "この結果ではlabel 0がなかったため、gingiva.stl は作成されませんでした。"
        case nil:
            return "歯肉STLの作成状況を結果JSONから確認できませんでした。出力フォルダをご確認ください。"
        }
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
        let updateRecovery = recoverInterruptedUpdateTransaction(
            appURL: Bundle.main.bundleURL,
            statusURL: paths.updateInstallStatusJSON
        )
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
            let stateReason = status.state?["reason"] as? String
            let stateIsFailed = status.state?["status"] as? String == "failed"
            let reason = status.reason == "setup_missing" && stateIsFailed
                ? (stateReason ?? status.reason)
                : status.reason
            if !reason.isEmpty {
                setupError = setupErrorText(for: reason)
                setupFailureOccurredAt = ISO8601DateFormatter().string(from: Date())
            }
        }
        refreshUpdateInstallerStatus()
        switch updateRecovery {
        case .none:
            break
        case .preservedPreviousApp:
            updateMessage = "更新処理が中断されました。以前のアプリはそのままです。"
        case .finalizedInstalledApp:
            updateMessage = "中断された更新を安全に確認して完了しました。"
        case .manualRecoveryRequired:
            updateMessage = "更新処理の状態を安全に確認できません。指定の配布ページからDMGを取得し、アプリを置き換えてください。"
        }
        refreshLog()
    }

    private func refreshUpdateInstallerStatus() {
        guard let payload = readJSON(paths.updateInstallStatusJSON),
              payload["schema"] as? String
                == "totalsegmentator_wrapper_mac.update_install_status.v1",
              payload["status"] as? String == "failed",
              let reason = payload["reason"] as? String
        else {
            return
        }
        let allowedReasons: Set<String> = [
            "update_install_failed_before_replace",
            "update_install_failed_rolled_back",
            "update_rollback_failed",
            "update_install_interrupted_before_swap",
            "update_recovery_required",
        ]
        guard allowedReasons.contains(reason) else {
            return
        }
        switch reason {
        case "update_install_failed_rolled_back":
            updateMessage = "更新に失敗したため、以前のアプリへ戻して再度開きました。"
        case "update_rollback_failed":
            updateMessage = "更新に失敗し、以前のアプリへ自動で戻せませんでした。Applicationsフォルダを確認してください。"
        case "update_install_interrupted_before_swap":
            updateMessage = "更新処理が中断されました。以前のアプリはそのままです。"
        case "update_recovery_required":
            updateMessage = "更新処理の状態を安全に確認できません。指定の配布ページからDMGを取得し、アプリを置き換えてください。"
        default:
            updateMessage = "更新を完了できなかったため、現在のアプリを再度開きました。"
        }
    }

    func startSetup() {
        setupAttemptID = UUID().uuidString.lowercased()
        setupFailureOccurredAt = ""
        setupReturnCode = nil
        lastSetupStage = SetupStep.validatePython312.rawValue
        setupRunning = true
        setupError = ""
        setupDownloadProgress = nil
        setupStep = .validatePython312
        setupHint = setupStep.hint
        setupElapsed = formatElapsed(0)
        activeLogURL = paths.launcherLog
        startedAt = Date()
        startLogTimer()

        let paths = self.paths
        let activeSetupAttemptID = setupAttemptID
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc: Int32
            let status = SetupCoordinator.setupStatus(paths: paths)
            if status.action == "resync_wheel" {
                DispatchQueue.main.async {
                    self?.setupStep = .syncBundle
                    self?.lastSetupStage = SetupStep.syncBundle.rawValue
                    self?.setupHint = SetupStep.syncBundle.hint
                    self?.setupMessage = "同梱アプリ更新を反映しています。"
                }
                rc = SetupCoordinator.resyncWheel(
                    paths: paths,
                    setupAttemptID: activeSetupAttemptID
                )
            } else {
                rc = SetupCoordinator.runSetup(
                    paths: paths,
                    setupAttemptID: activeSetupAttemptID
                ) { step, message in
                    DispatchQueue.main.async { [weak self] in
                        self?.setupStep = step
                        self?.lastSetupStage = step.rawValue
                        self?.setupHint = step.hint
                        self?.setupMessage = message
                    }
                }
            }
            DispatchQueue.main.async {
                self?.setupRunning = false
                self?.setupDownloadProgress = nil
                self?.refreshLog()
                self?.activeLogURL = nil
                if rc == 0 {
                    self?.setupStep = .complete
                    self?.setupHint = SetupStep.complete.hint
                    self?.setupMessage = "このアプリを使う準備が完了しました。"
                    self?.setupError = ""
                    self?.screen = .start
                    self?.selectedStep = 0
                } else {
                    let reason = readJSON(paths.stateJSON)?["reason"] as? String
                    self?.setupReturnCode = Int(rc)
                    self?.setupFailureOccurredAt = ISO8601DateFormatter().string(from: Date())
                    self?.setupStep = .setupException
                    self?.setupHint = SetupStep.setupException.hint
                    self?.setupError = self?.setupErrorText(for: reason) ?? setupReasonToJapanese(reason)
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
        clearRescueInputContext()
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
            prepareSelectedCTInput(url)
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
        prepareSelectedCTInput(url)
    }

    func chooseNifti() {
        let panel = NSOpenPanel()
        panel.title = "CTファイルを選択"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            prepareSelectedCTInput(url)
        }
    }

    func goToIOSMesh() {
        screen = .iosMesh
        selectedStep = 1
        statusText = "口腔内スキャン"
    }

    func chooseIOSMesh() {
        guard !iosMeshRunning else { return }
        let panel = NSOpenPanel()
        panel.title = "\(iosMeshJaw.displayName)の口腔内スキャンを選択"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = ["ply", "stl"].compactMap {
            UTType(filenameExtension: $0)
        }
        guard panel.runModal() == .OK, let url = panel.url else { return }
        iosMeshInputURL = url
        iosMeshOutputURL = nil
        iosMeshSucceeded = false
        iosMeshToothCount = 0
        iosMeshDownloadProgress = nil
        iosMeshGingivaPresent = nil
        selectedStep = 1
        iosMeshStatus = "入力を選択しました。選択したモデルで歯別STLを作成できます。"
    }

    func chooseIOSTGNetSet() {
        guard !iosMeshRunning, !iosMeshTGNetValidationRunning else { return }
        let panel = NSOpenPanel()
        panel.title = "TGNet用ZIP／フォルダを選ぶ"
        panel.message = "指定の配布ページから取得したckpts(new).zip、またはMacが自動展開したckpts(new)フォルダを選択してください。"
        panel.canChooseFiles = true
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = ["zip"].compactMap {
            UTType(filenameExtension: $0)
        }
        guard panel.runModal() == .OK, let url = panel.url else { return }
        validateIOSTGNetSet(url)
    }

    func openTGNetCheckpointPage() {
        openURLInWorkspace(tgnetCheckpointPageURL)
    }

    private func validateIOSTGNetSet(_ url: URL) {
        guard FileManager.default.fileExists(atPath: paths.venvPython.path) else {
            screen = .setup
            statusText = "はじめの準備が必要です。"
            return
        }
        iosMeshCustomModelURL = nil
        iosMeshOutputURL = nil
        iosMeshSucceeded = false
        iosMeshToothCount = 0
        iosMeshDownloadProgress = nil
        iosMeshGingivaPresent = nil
        selectedStep = 1
        iosMeshTGNetValidationRunning = true
        iosMeshTGNetValidationError = ""
        iosMeshTGNetValidationDetail = ""
        iosMeshStatus = "TGNetの重みを確認しています…"

        let resultJSON = paths.iosMeshTGNetValidationJSON
        let logURL = paths.iosMeshTGNetValidationLog
        try? FileManager.default.removeItem(at: resultJSON)
        try? FileManager.default.removeItem(at: logURL)
        tgnetValidationRunner.resetTerminationRequest()
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let command = CommandBuilder.iosMeshTGNetValidateCommand(
            python: paths.venvPython,
            model: url,
            resultJSON: resultJSON
        )
        let validationRunner = tgnetValidationRunner
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = validationRunner.run(
                command,
                environment: environment,
                logURL: logURL
            )
            let result = readJSON(resultJSON)
            DispatchQueue.main.async {
                guard let self else { return }
                self.iosMeshTGNetValidationRunning = false
                if rc == 0, result?["status"] as? String == "success" {
                    self.iosMeshCustomModelURL = url
                    self.iosMeshTGNetValidationError = ""
                    self.iosMeshTGNetValidationDetail = ""
                    self.iosMeshStatus = "TGNetの重みを確認しました。"
                } else {
                    self.iosMeshCustomModelURL = nil
                    self.iosMeshTGNetValidationError = "TGNetの重みを確認できませんでした。"
                    self.iosMeshTGNetValidationDetail = safeTGNetValidationDetail(from: result)
                    self.iosMeshStatus = "TGNetの重みを確認できませんでした。"
                }
            }
        }
    }

    func resetIOSMeshModel() {
        guard !iosMeshRunning else { return }
        iosMeshCustomModelURL = nil
        iosMeshOutputURL = nil
        iosMeshSucceeded = false
        iosMeshToothCount = 0
        iosMeshDownloadProgress = nil
        iosMeshGingivaPresent = nil
        iosMeshTGNetValidationError = ""
        iosMeshTGNetValidationDetail = ""
        selectedStep = 1
        iosMeshStatus = "同梱モデル（MeshSegNet）を使用します。"
    }

    func startIOSMeshRun() {
        guard !iosMeshTGNetValidationRunning else {
            iosMeshStatus = "TGNetの重みを確認しています…"
            return
        }
        guard let input = iosMeshInputURL else {
            iosMeshStatus = "\(iosMeshJaw.displayName)のPLYまたはSTLを選択してください。"
            return
        }
        guard FileManager.default.fileExists(atPath: paths.venvPython.path) else {
            screen = .setup
            statusText = "はじめの準備が必要です。"
            return
        }
        let output = defaultRunOutput(root: selectedOutputRootURL)
        iosMeshOutputURL = output
        iosMeshRunning = true
        iosMeshSucceeded = false
        iosMeshToothCount = 0
        iosMeshDownloadProgress = nil
        iosMeshGingivaPresent = nil
        iosMeshPreparingModel = iosMeshCustomModelURL == nil
        selectedStep = 2
        if iosMeshCustomModelURL != nil {
            iosMeshStatus = "MPSで歯を分けています。"
        } else if FileManager.default.fileExists(atPath: paths.iosMeshSegNetModel.path) {
            iosMeshStatus = "モデルを確認しています。"
        } else {
            iosMeshStatus = "初回モデルを準備しています。"
        }
        statusText = "口腔内スキャン処理中"
        let logURL = paths.iosMeshSegNetRunLog
        try? FileManager.default.removeItem(at: logURL)
        try? FileManager.default.removeItem(at: paths.iosMeshSegNetStatusJSON)
        runner.resetTerminationRequest()
        activeLogURL = logURL
        startedAt = Date()
        startLogTimer()
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let runner = self.runner
        let appPaths = paths
        let customModel = iosMeshCustomModelURL
        let selectedModel = customModel ?? appPaths.iosMeshSegNetModel
        let selectedJaw = iosMeshJaw
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var rc: Int32 = 0
            var modelPreparationFailed = false
            var modelPreparationErrorCode: String?
            if customModel == nil {
                rc = runner.run(
                    CommandBuilder.iosMeshSegNetPrepareCommand(
                        python: appPaths.venvPython,
                        paths: appPaths
                    ),
                    environment: environment,
                    logURL: logURL
                )
                if rc != 0 {
                    modelPreparationFailed = true
                    let preparationStatus = readJSON(appPaths.iosMeshSegNetStatusJSON)
                    modelPreparationErrorCode = preparationStatus?["error_code"] as? String
                }
            }
            if rc == 0 && !runner.isTerminationRequested {
                DispatchQueue.main.async {
                    self?.iosMeshStatus = "MPSで歯を分けています。"
                    self?.iosMeshPreparingModel = false
                    self?.iosMeshDownloadProgress = nil
                }
                rc = runner.run(
                    CommandBuilder.iosMeshSegNetRunCommand(
                        python: appPaths.venvPython,
                        input: input,
                        output: output,
                        model: selectedModel,
                        isCustomModel: customModel != nil,
                        jaw: selectedJaw.rawValue
                    ),
                    environment: environment,
                    logURL: logURL
                )
            }
            let summaryURL = output.appendingPathComponent("result_summary.json")
            let summary = readJSON(summaryURL)
            let outputs = summary?["outputs"] as? [String: Any]
            let teeth = (outputs?["teeth"] as? [[String: Any]]) ?? []
            let gingivaPresent = (outputs?["gingiva"] as? [String: Any])?["present"] as? Bool
            let runtime = summary?["runtime"] as? [String: Any]
            let usedMPS = runtime?["device"] as? String == "mps"
                && runtime?["mps_fallback_env"] is NSNull
            DispatchQueue.main.async {
                let stopped = runner.isTerminationRequested
                self?.iosMeshPreparingModel = false
                self?.iosMeshDownloadProgress = nil
                self?.iosMeshRunning = false
                self?.activeLogURL = nil
                self?.resultLogURL = logURL
                self?.refreshLog(from: logURL)
                self?.iosMeshToothCount = teeth.count
                self?.iosMeshGingivaPresent = gingivaPresent
                self?.iosMeshSucceeded = rc == 0 && usedMPS && !teeth.isEmpty
                if stopped {
                    self?.setSafeError(
                        code: "ios_mesh_cancelled",
                        reason: "The intraoral mesh run was cancelled.",
                        mpsState: "unknown"
                    )
                    self?.iosMeshStatus = "処理を停止しました。"
                    self?.statusText = "停止しました"
                } else if modelPreparationFailed {
                    let failure = iosMeshModelPreparationFailure(
                        for: modelPreparationErrorCode
                    )
                    self?.setSafeError(
                        code: failure.code,
                        reason: failure.reason,
                        mpsState: "unknown"
                    )
                    self?.iosMeshStatus = failure.statusText
                    self?.statusText = "モデル準備失敗"
                } else if rc == 0 && usedMPS && !teeth.isEmpty {
                    self?.iosMeshStatus = "\(teeth.count)本の歯別STLを作成しました。"
                    self?.statusText = "口腔内スキャン完了"
                    self?.selectedStep = 3
                } else {
                    self?.setSafeError(
                        code: "ios_mesh_inference_failed",
                        reason: "The intraoral mesh run did not produce tooth STL files on strict MPS.",
                        mpsState: usedMPS ? "validated" : "failed_or_unknown"
                    )
                    self?.iosMeshStatus = "処理を完了できませんでした。詳細ログを確認してください。"
                    self?.statusText = "口腔内スキャン失敗"
                }
            }
        }
    }

    func stopIOSMeshRun() {
        guard iosMeshRunning else { return }
        iosMeshStatus = "停止しています。"
        runner.terminate()
    }

    func openIOSMeshPreview() {
        guard let output = iosMeshOutputURL else { return }
        let candidates = [
            output.appendingPathComponent(
                "ios_\(iosMeshJaw.rawValue)_meshsegnet_dense_preview.png"
            ),
            output.appendingPathComponent("ios_tgnet_colored.ply"),
        ]
        guard let result = candidates.first(where: {
            FileManager.default.fileExists(atPath: $0.path)
        }) else { return }
        openURLInWorkspace(result)
    }

    func openIOSMeshOutput() {
        guard let output = iosMeshOutputURL,
              FileManager.default.fileExists(atPath: output.path) else { return }
        NSWorkspace.shared.activateFileViewerSelecting([output])
    }

    private func prepareSelectedCTInput(_ url: URL) {
        if !isDirectory(url) && isNiftiFile(url) {
            prepareNiftiInput(url)
            return
        }
        inputURL = url
        inputSource = .dicomFolder
        runDicomAudit(dicomDir: url)
    }

    private func prepareNiftiInput(_ url: URL) {
        clearRescueInputContext()
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
        dentalPreparationFailed = false
        dentalPreparationRunning = false
        dentalPreparationStartedAt = nil
        dentalPreparationElapsed = formatElapsed(0)
        dentalPreparationFraction = nil
        dentalPreparationDetail = ""
        safeErrorCode = ""
        safeErrorReason = ""
        safeMPSState = "unknown"
        safeErrorOccurredAt = ""
        let pendingChoice = pendingModelPreparationChoice
        let modelName = pendingChoice == .toothSegExperimental ? "ToothSeg" : "DentalSegmentator"
        dentalPreparationMessage = "\(modelName)のモデルを準備しています。"
        dentalPreparationCancellationRequested = false
        dentalPreparationRunner.resetTerminationRequest()
        let preparationLog = pendingChoice == .toothSegExperimental
            ? paths.toothsegPrepareLog
            : paths.dentalsegPrepareLog
        let preparationResult = pendingChoice == .toothSegExperimental
            ? paths.toothsegPrepareResultJSON
            : paths.dentalsegPrepareResultJSON
        guard clearModelPreparationAttemptArtifacts(
            logURL: preparationLog,
            resultURL: preparationResult,
            logsRoot: paths.logs
        ) else {
            dentalPreparationFailed = true
            dentalPreparationMessage = "モデル準備を開始できませんでした。標準の選択に戻します。"
            setSafeError(
                code: pendingChoice == .toothSegExperimental
                    ? "toothseg_model_preparation_failed"
                    : "dentalseg_model_preparation_failed",
                reason: pendingChoice == .toothSegExperimental
                    ? "ToothSeg model preparation did not start safely."
                    : "DentalSegmentator model preparation did not start safely.",
                mpsState: "unknown"
            )
            if modelPreparationPurpose == .creationSelection {
                creationChoice = .standardArchJaw
            } else {
                resultOutcome = .success
                toothSegRefineFailed = true
            }
            return
        }
        dentalPreparationRunning = true
        dentalPreparationStartedAt = Date()
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
            let rc = runner.run(prepareCommand, environment: environment, logURL: preparationLog)
            DispatchQueue.main.async {
                guard let self else { return }
                self.dentalPreparationRunning = false
                self.dentalPreparationTimer?.invalidate()
                self.dentalPreparationTimer = nil
                self.dentalPreparationElapsed = formatElapsed(self.dentalPreparationStartedAt.map { Date().timeIntervalSince($0) } ?? 0)
                if self.dentalPreparationCancellationRequested {
                    self.dentalPreparationCancellationRequested = false
                    self.dentalPreparationFailed = false
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
                let result = readJSON(preparationResult)
                let modelReady = pendingChoice == .toothSegExperimental
                    ? self.isToothSegModelReady
                    : self.isDentalSegmentatorModelReady
                if rc == 0 && result?["model_state"] as? String == "ready" && modelReady {
                    self.dentalPreparationFailed = false
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
                    self.dentalPreparationFailed = true
                    let reportedPreparationErrorCode = result?["error_code"] as? String
                    let failureCode: String
                    if reportedPreparationErrorCode == "insufficient_disk_space" {
                        failureCode = "insufficient_disk_space"
                    } else {
                        failureCode = pendingChoice == .toothSegExperimental
                            ? "toothseg_model_preparation_failed"
                            : "dentalseg_model_preparation_failed"
                    }
                    self.setSafeError(
                        code: failureCode,
                        reason: pendingChoice == .toothSegExperimental
                            ? "ToothSeg model preparation did not complete."
                            : "DentalSegmentator model preparation did not complete.",
                        mpsState: "unknown"
                    )
                    if self.modelPreparationPurpose == .creationSelection {
                        self.dentalPreparationMessage = failureCode == "insufficient_disk_space"
                            ? "モデル準備に必要な空き容量が不足しています。標準の選択に戻します。"
                            : "モデルを準備できませんでした。標準の選択に戻します。"
                        self.creationChoice = .standardArchJaw
                    } else {
                        self.dentalPreparationMessage = failureCode == "insufficient_disk_space"
                            ? "ToothSegモデル準備に必要な空き容量が不足しています。"
                            : "ToothSegモデルを準備できませんでした。"
                        self.resultOutcome = .success
                        self.toothSegRefineFailed = true
                        self.failureReasonText = failureCode == "insufficient_disk_space"
                            ? "ToothSegモデル準備に必要な空き容量が不足しています。空き容量を確保して再試行してください。"
                            : "ToothSegモデルのダウンロードまたは検証に失敗しました。ネットワーク状態を確認して再試行してください。"
                        self.statusText = "ToothSegモデルを準備できませんでした"
                        self.progressText = "元の歯列・顎骨結果は引き続き利用できます。"
                        self.resultMessage = "ToothSegモデル準備に失敗しました"
                    }
                }
            }
        }
    }

    func cancelDentalPreparation() {
        dentalPreparationFailed = false
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
            let progress: ToothSegPreparationProgress?
            if self.pendingModelPreparationChoice == .toothSegExperimental,
               let snapshot = readLogTail(
                   self.paths.toothsegPrepareLog,
                   maxBytes: LOG_TAIL_BYTES
               ) {
                progress = toothSegPreparationProgressFromLog(snapshot.text)
            } else if let snapshot = readLogTail(
                self.paths.dentalsegPrepareLog,
                maxBytes: LOG_TAIL_BYTES
            ) {
                progress = dentalSegmentatorPreparationProgressFromLog(snapshot.text)
            } else {
                progress = nil
            }
            if let progress {
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
        lastDicomAuditJSONURL = auditJSON
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
        clearRescueInputContext()
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
                guard let self else { return }
                self.isRunning = false
                self.stopRequested = false
                self.refreshLog(from: logURL)
                self.activeLogURL = nil
                self.resultLogURL = logURL
                if valid,
                   let context = self.makeRescueInputContext(finalizedMetadataJSON: outputJSON) {
                    self.acceptPreparedRescueNifti(outputNifti, context: context)
                } else {
                    self.rescueWorkflowState = rc == 0 ? .readbackMismatch : .prepareFailed
                    self.rescueConfirmationWasExplicit = false
                    self.screen = .dicomRescue
                    self.selectedStep = 1
                    self.rescueInlineWarning = "確定した形状を安全に読み直せませんでした。AI推論は開始していません。"
                    self.rescueImageUpdateFailed = true
                }
            }
        }
    }

    private func makeRescueInputContext(
        finalizedMetadataJSON: URL
    ) -> RescueInputContext? {
        guard let candidate = selectedDicomRescueCandidate,
              candidate.role == "primary",
              isSupportedRescueClassification(candidate.classificationStatus),
              let candidateManifest = candidate.contentManifestSHA256,
              let canonicalCandidateManifest = canonicalRescueSHA256(candidateManifest),
              let sourceManifest = canonicalRescueSHA256(rescueSourceManifestSHA256),
              canonicalCandidateManifest == sourceManifest,
              let payload = readJSON(finalizedMetadataJSON),
              payload["schema"] as? String == "totalsegmentator_wrapper_mac.rescue_geometry.v2",
              payload["workflow_status"] as? String == "finalized",
              (payload["inference_started"] as? Bool) == false,
              let source = payload["source"] as? [String: Any],
              let metadataSourceManifest = source["content_manifest_sha256"] as? String,
              canonicalRescueSHA256(metadataSourceManifest) == sourceManifest,
              let confirmation = payload["confirmation"] as? [String: Any],
              confirmation["schema"] as? String
                == "totalsegmentator_wrapper_mac.rescue_confirmation.v1",
              (confirmation["confirmed"] as? Bool) == true,
              let confirmationSHA256 = confirmation["token_sha256"] as? String,
              let canonicalConfirmationSHA256 = canonicalRescueSHA256(confirmationSHA256),
              let transform = payload["transform"] as? [String: Any],
              let transformSHA256 = rescueTransformSHA256(transform),
              isSHA256Hex(rescueConfirmationToken),
              sha256Hex(Data(rescueConfirmationToken.utf8)) == canonicalConfirmationSHA256
        else {
            return nil
        }
        return RescueInputContext(
            classification: candidate.classificationStatus,
            sourceManifestSHA256: sourceManifest,
            confirmationSHA256: canonicalConfirmationSHA256,
            transformSHA256: transformSHA256
        )
    }

    private func canonicalRescueSHA256(_ value: String) -> String? {
        isSHA256Hex(value) ? value : nil
    }

    private func isSupportedRescueClassification(_ value: String) -> Bool {
        [
            "secondary_capture_rescue_candidate",
            "geometry_rescue_candidate",
            "secondary_capture_reference_candidate",
        ].contains(value)
    }

    private func rescueTransformSHA256(_ transform: [String: Any]) -> String? {
        let expectedKeys: Set<String> = [
            "axis_permutation",
            "rotation_quarter_turns",
            "slice_order_reversed",
            "crop_voxels_xyz",
        ]
        guard Set(transform.keys) == expectedKeys,
              let axisPermutation = transform["axis_permutation"] as? [String],
              axisPermutation.count == 3,
              Set(axisPermutation) == Set(["x", "y", "z"]),
              let rotation = strictRescueJSONInteger(transform["rotation_quarter_turns"]),
              (0 ... 3).contains(rotation),
              let sliceOrderReversed = transform["slice_order_reversed"] as? Bool
        else {
            return nil
        }

        let canonicalCrop: Any
        if transform["crop_voxels_xyz"] is NSNull {
            canonicalCrop = NSNull()
        } else {
            guard let crop = transform["crop_voxels_xyz"] as? [String: Any],
                  Set(crop.keys) == Set(["min", "max_exclusive"]),
                  let lowerRaw = crop["min"] as? [Any],
                  let upperRaw = crop["max_exclusive"] as? [Any],
                  lowerRaw.count == 3,
                  upperRaw.count == 3
            else {
                return nil
            }
            let lower = lowerRaw.compactMap(strictRescueJSONInteger)
            let upper = upperRaw.compactMap(strictRescueJSONInteger)
            guard lower.count == 3,
                  upper.count == 3,
                  zip(lower, upper).allSatisfy({ bounds in
                      bounds.0 >= 0 && bounds.1 > bounds.0
                  })
            else {
                return nil
            }
            canonicalCrop = [
                "min": lower,
                "max_exclusive": upper,
            ]
        }

        let canonicalTransform: [String: Any] = [
            "axis_permutation": axisPermutation,
            "rotation_quarter_turns": rotation,
            "slice_order_reversed": sliceOrderReversed,
            "crop_voxels_xyz": canonicalCrop,
        ]
        guard JSONSerialization.isValidJSONObject(canonicalTransform),
              let encoded = try? JSONSerialization.data(
                  withJSONObject: canonicalTransform,
                  options: [.sortedKeys]
              )
        else {
            return nil
        }
        return sha256Hex(encoded)
    }

    private func strictRescueJSONInteger(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID() else {
            return nil
        }
        let doubleValue = number.doubleValue
        let integerValue = number.intValue
        guard doubleValue.isFinite, doubleValue == Double(integerValue) else {
            return nil
        }
        return integerValue
    }

    private func acceptPreparedRescueNifti(
        _ preparedURL: URL,
        context: RescueInputContext
    ) {
        guard rescueConfirmationWasExplicit,
              FileManager.default.fileExists(atPath: preparedURL.path) else {
            rescueWorkflowState = .readbackMismatch
            screen = .dicomRescue
            return
        }
        // RescueInputContext was bound to the finalized metadata before this run.
        rescueInputContext = context
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
        clearRescueInputContext()
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

    private func clearRescueInputContext() {
        rescueInputContext = nil
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
        runAttemptID = UUID().uuidString.lowercased()
        resetSafeRunDiagnostics(attemptID: runAttemptID)
        teethDetected = false
        refineAvailable = false
        primaryRunTeethDetected = false
        canRunToothSegRefine = false
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
            runAttemptID: runAttemptID,
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
                    self?.failureReasonText = "3D preview生成だけが完了できませんでした。ローカルの詳細ログを確認してください。"
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
                    self?.failureReasonText = self?.safePrimaryRunFailureText()
                        ?? "実行コマンドが完了できませんでした。詳細ログを確認してください。"
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
        runAttemptID = UUID().uuidString.lowercased()
        resetSafeRunDiagnostics(attemptID: runAttemptID)
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
            runAttemptID: runAttemptID,
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
                        self?.failureReasonText = "3D preview生成が完了できませんでした。ローカルの詳細ログを確認してください。"
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
                    let reason = self?.toothSegRefineFailureReason() ?? ""
                    self?.failureReasonText = reason.isEmpty ? "ToothSeg高精細化を完了できませんでした。" : reason
                    self?.statusText = "高精細化を完了できませんでした"
                    self?.progressText = "原因を確認して高精細化を再実行してください。"
                    self?.resultMessage = "ToothSeg高精細化に失敗しました"
                }
            }
        }
    }

    func loadSafeRunResult(from resultURL: URL? = nil, treatAsPrimaryResult: Bool = true) {
        guard let payload = readJSON(resultURL ?? paths.runResultJSON),
              isCurrentSafeRunResultPayload(
                  payload,
                  expectedRunAttemptID: runAttemptID
              ) else {
            resetRejectedSafeRunResult(treatAsPrimaryResult: treatAsPrimaryResult)
            return
        }
        let safeFields = safeRunResultFields(from: payload)
        guard safeFields.runAttemptID == runAttemptID else {
            resetRejectedSafeRunResult(treatAsPrimaryResult: treatAsPrimaryResult)
            return
        }
        safeErrorCode = safeFields.errorCode
        safeErrorReason = safeFields.reason
        safeMPSState = safeFields.mpsState
        safeErrorOccurredAt = safeFields.occurredAt
        safeRunAttemptID = safeFields.runAttemptID
        safeRunFailedStage = safeFields.failedStage
        safeRunSpecificCause = safeFields.specificCause
        safeRunRetryable = safeFields.retryable
        safeRunRecoveryHintCode = safeFields.recoveryHintCode
        safeRunDiagnosticLogKind = safeFields.diagnosticLogKind
        safeRunDiagnosticLogReference = safeFields.diagnosticLogReference
        safeRunBackendVersion = safeFields.backendVersion
        safeRunModelVersion = safeFields.modelVersion
        safeRunRuntimePythonVersion = safeFields.runtimePythonVersion
        safeRunRuntimeTorchVersion = safeFields.runtimeTorchVersion
        safeRunInputKind = safeFields.inputKind
        safeRunInputSizeBucket = safeFields.inputSizeBucket
        safeRunActualDevice = safeFields.actualDevice
        safeRunFallbackUsed = safeFields.fallbackUsed
        let detected = payload["teeth_detected"] as? Bool ?? false
        teethDetected = detected
        refineAvailable = payload["refine_available"] as? Bool ?? detected
        if treatAsPrimaryResult {
            primaryRunTeethDetected = detected
        }
    }

    private func resetRejectedSafeRunResult(treatAsPrimaryResult: Bool) {
        // Refinement results are secondary to an already displayed primary result.
        // A stale or malformed secondary JSON must not erase that primary state.
        guard treatAsPrimaryResult else {
            return
        }
        safeErrorCode = ""
        safeErrorReason = ""
        safeMPSState = "unknown"
        safeErrorOccurredAt = ""
        resetSafeRunDiagnostics(attemptID: runAttemptID)
        teethDetected = false
        refineAvailable = false
        primaryRunTeethDetected = false
        canRunToothSegRefine = false
    }

    private func resetSafeRunDiagnostics(attemptID: String? = nil) {
        safeRunAttemptID = attemptID.flatMap { UUID(uuidString: $0)?.uuidString.lowercased() } ?? ""
        safeRunFailedStage = "unknown"
        safeRunSpecificCause = "unknown"
        safeRunRetryable = "unknown"
        safeRunRecoveryHintCode = "unknown"
        safeRunDiagnosticLogKind = "none"
        safeRunDiagnosticLogReference = ""
        safeRunBackendVersion = "unknown"
        safeRunModelVersion = "unknown"
        safeRunRuntimePythonVersion = "unknown"
        safeRunRuntimeTorchVersion = "unknown"
        safeRunInputKind = "unknown"
        safeRunInputSizeBucket = "unknown"
        safeRunActualDevice = "unknown"
        safeRunFallbackUsed = "unknown"
    }

    private func toothSegRefineFailureReason() -> String {
        switch safeErrorCode {
        case "toothseg_mps_oom":
            return "MPSメモリ不足（Out Of Memory）で失敗しました。他のアプリを終了するかMacを再起動してから再試行してください。CTの範囲によっては、このMacでは高精細化を実行できない場合があります。"
        case "toothseg_input_invalid":
            return "元の歯列結果から有効な高精細化範囲を作成できませんでした。通常の歯列・顎骨結果は引き続き利用できます。"
        case "toothseg_download_failed", "toothseg_model_preparation_failed":
            return "ToothSegモデルの取得に失敗しました。ネットワーク状態を確認して再試行してください。"
        default:
            return "ToothSeg高精細化を完了できませんでした。ローカルの詳細ログを確認してから再試行してください。"
        }
    }

    private func safePrimaryRunFailureText() -> String {
        switch canonicalSafeErrorCode(safeErrorCode) {
        case "totalseg_backend_nonzero_exit":
            return "TotalSegmentatorの推論処理が終了コード異常で停止しました。CPUや別のbackendには切り替えていません。ローカルの詳細ログを確認してから再試行してください。"
        case "totalseg_backend_launch_failed":
            return "TotalSegmentatorの推論処理を開始できませんでした。ローカルの詳細ログを確認してから再試行してください。"
        case "mps_unavailable":
            return "MPSの確認を完了できませんでした。Macを再起動するか、セットアップをやり直してから再試行してください。"
        default:
            return "実行コマンドが完了できませんでした。ローカルの詳細ログを確認してから再試行してください。"
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

    private func setSafeError(code: String, reason _: String, mpsState: String) {
        // A UI-side failure (cancel, preview generation, etc.) does not inherit
        // a prior inference diagnostic's stage, log reference, or attempt ID.
        resetSafeRunDiagnostics()
        let safeCode = canonicalSafeErrorCode(code)
        safeErrorCode = safeCode
        safeErrorReason = safeReasonForErrorCode(safeCode)
        safeMPSState = safeMPSDiagnosticState(mpsState)
        safeErrorOccurredAt = ISO8601DateFormatter().string(from: Date())
    }

    private func inputProvenancePayload() -> [String: Any] {
        if let rescue = rescueInputContext {
            return [
                "schema": "totalsegmentator_wrapper_mac.input_provenance.v1",
                "source_kind": "dicom_rescue",
                "non_diagnostic_preview": true,
                "classification": rescue.classification,
                "source_manifest_sha256": rescue.sourceManifestSHA256,
                "confirmation_sha256": rescue.confirmationSHA256,
                "transform_sha256": rescue.transformSHA256,
            ]
        }
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

    func copySafeSetupErrorInfo() {
        guard !setupRunning, !setupError.isEmpty else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(safeSetupErrorCopyText, forType: .string)
    }

    func openSetupErrorReportForm() {
        guard !setupRunning, !setupError.isEmpty else { return }
        copySafeSetupErrorInfo()
        openURLInWorkspace(errorReportFormURL)
    }

    func openErrorReportForm() {
        copySafeErrorInfo()
        openURLInWorkspace(errorReportFormURL)
    }

    func openSTLGenerationErrorReportForm() {
        setSafeError(
            code: "stl_generation_failed",
            reason: "STL generation did not complete.",
            mpsState: "not_applicable"
        )
        openErrorReportForm()
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
                    self?.surfacePreviewFailed = true
                    self?.failureReasonText = "3D preview生成が完了できませんでした。ローカルの詳細ログを確認してください。"
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
        guard !updateCheckRunning && !updateInstallRunning else {
            return
        }
        pendingDownloadURL = nil
        pendingUpdateVersion = ""
        pendingUpdateSHA256 = ""
        pendingUpdateFileSizeBytes = nil
        pendingUpdateAllowedHosts = []
        pendingUpdateManifestURL = nil
        showingUpdateConfirmation = false
        let manifest = readJSON(paths.manifest) ?? [:]
        let url = (manifest["update_manifest_url"] as? String) ?? ""
        guard let manifestURL = URL(string: url),
              isHTTPSURL(manifestURL) else {
            updateMessage = "更新確認URLは設定されていません。"
            return
        }
        guard let version = (manifest["app_version"] as? String)
                ?? (manifest["version"] as? String),
              semanticVersionTripletParts(version) != nil else {
            updateMessage = "現在のアプリ版を安全に確認できません。DMGからもう一度コピーしてください。"
            return
        }
        let allowedHosts = (manifest["update_allowed_hosts"] as? [String]) ?? []
        let updateJSON = paths.logs.appendingPathComponent("update_check.json")
        guard let command = updateCheckCommand(
            manifestURL: url,
            json: updateJSON,
            currentVersion: version,
            allowedHosts: allowedHosts
        ) else {
            updateMessage = "更新確認に必要な同梱Pythonまたはアプリパッケージが見つかりません。DMGからもう一度コピーしてください。"
            return
        }
        try? FileManager.default.removeItem(at: updateJSON)
        updateCheckRunning = true
        updateMessage = "更新を確認しています。DICOM/CT/path/logは送信しません。"
        let environment = CommandBuilder.launchEnvironment(paths: paths)
        let updateRunner = ProcessRunner()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let rc = updateRunner.run(command, environment: environment, logURL: nil)
            let result = readJSON(updateJSON) ?? [:]
            DispatchQueue.main.async {
                self?.updateCheckRunning = false
                let status = validatedUpdateCheckStatus(
                    result,
                    expectedManifestURL: url,
                    expectedCurrentVersion: version
                ) ?? "failed"
                if rc == 0 && (status == "update_available" || status == "critical_update_available") {
                    let latest = (result["latest_version"] as? String) ?? "unknown"
                    let sha256 = ((result["sha256"] as? String) ?? "").lowercased()
                    let downloadURL = (result["download_url"] as? String).flatMap(URL.init(string:))
                    let fileSizeBytes = jsonInt(result["file_size_bytes"])
                    guard isSafeDiagnosticToken(latest),
                          isSHA256Hex(sha256),
                          let fileSizeBytes,
                          fileSizeBytes > 0,
                          Int64(fileSizeBytes) <= MAX_UPDATE_DMG_BYTES,
                          let downloadURL,
                          isAllowedUpdateURL(
                            downloadURL,
                            manifestURL: manifestURL,
                            additionalHosts: allowedHosts
                          ) else {
                        self?.pendingDownloadURL = nil
                        self?.pendingUpdateVersion = ""
                        self?.pendingUpdateSHA256 = ""
                        self?.pendingUpdateFileSizeBytes = nil
                        self?.pendingUpdateAllowedHosts = []
                        self?.pendingUpdateManifestURL = nil
                        self?.showingUpdateConfirmation = false
                        self?.updateMessage = "更新情報の署名値または配布先を安全に確認できませんでした。"
                        return
                    }
                    self?.updateMessage = "新しい版があります: \(latest)"
                    self?.pendingUpdateVersion = latest
                    self?.pendingUpdateSHA256 = sha256
                    self?.pendingUpdateFileSizeBytes = fileSizeBytes
                    self?.pendingUpdateAllowedHosts = allowedHosts
                    self?.pendingUpdateManifestURL = manifestURL
                    self?.pendingDownloadURL = downloadURL
                } else if rc == 0 && status == "current" {
                    self?.pendingDownloadURL = nil
                    self?.pendingUpdateVersion = ""
                    self?.pendingUpdateSHA256 = ""
                    self?.pendingUpdateFileSizeBytes = nil
                    self?.pendingUpdateAllowedHosts = []
                    self?.pendingUpdateManifestURL = nil
                    self?.showingUpdateConfirmation = false
                    self?.updateMessage = "現在の版は最新です。"
                } else {
                    self?.pendingDownloadURL = nil
                    self?.pendingUpdateVersion = ""
                    self?.pendingUpdateSHA256 = ""
                    self?.pendingUpdateFileSizeBytes = nil
                    self?.pendingUpdateAllowedHosts = []
                    self?.pendingUpdateManifestURL = nil
                    self?.showingUpdateConfirmation = false
                    self?.updateMessage = "更新確認に失敗しました。"
                }
            }
        }
    }

    private func updateCheckCommand(
        manifestURL: String,
        json: URL,
        currentVersion: String,
        allowedHosts: [String]
    ) -> [String]? {
        if let python = CommandBuilder.resolvePython312(paths: paths),
           FileManager.default.isExecutableFile(atPath: python.path),
           let wheel = CommandBuilder.latestWheel(resources: paths.resources),
           FileManager.default.fileExists(atPath: wheel.path) {
            let bootstrap = "import runpy,sys;wheel=sys.argv[1];sys.path.insert(0,wheel);sys.argv=['totalsegmentator_wrapper_mac']+sys.argv[2:];runpy.run_module('totalsegmentator_wrapper_mac',run_name='__main__')"
            var command = [
                python.path,
                "-I",
                "-c",
                bootstrap,
                wheel.path,
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
        if FileManager.default.isExecutableFile(atPath: paths.venvPython.path) {
            return CommandBuilder.updateCheckCommand(
                python: paths.venvPython,
                manifestURL: manifestURL,
                json: json,
                currentVersion: currentVersion,
                allowedHosts: allowedHosts
            )
        }
        return nil
    }

    func openPendingDownload() {
        showingUpdateConfirmation = true
    }

    func confirmOpenPendingDownload() {
        downloadAndInstallPendingUpdate()
    }

    private func downloadAndInstallPendingUpdate() {
        guard !updateInstallRunning && !updateCheckRunning else {
            return
        }
        guard let downloadURL = pendingDownloadURL else {
            updateMessage = "更新ファイルURLが見つかりません。"
            return
        }
        guard let manifestURL = pendingUpdateManifestURL,
              isAllowedUpdateURL(
                downloadURL,
                manifestURL: manifestURL,
                additionalHosts: pendingUpdateAllowedHosts
              ) else {
            updateMessage = "更新ファイルURLのHTTPS配布先を確認できません。"
            return
        }
        guard isSHA256Hex(pendingUpdateSHA256) else {
            updateMessage = "更新ファイルのSHA256がmanifestにありません。"
            return
        }
        guard !pendingUpdateVersion.isEmpty,
              isSafeDiagnosticToken(pendingUpdateVersion) else {
            updateMessage = "更新版の番号を確認できません。"
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
        guard canUseAtomicUpdateSwap(appURL: appURL) else {
            updateMessage = "このアプリの保存先では安全な自動更新に対応していません。指定の配布ページからDMGを取得し、アプリを置き換えてください。"
            return
        }
        guard !hasPendingUpdateArtifacts(appURL: appURL) else {
            updateMessage = "前回の更新処理のファイルが残っているため、安全に自動更新できません。指定の配布ページからDMGを取得し、アプリを置き換えてください。"
            return
        }

        updateInstallRunning = true
        updateMessage = "更新ファイルをダウンロードしています。"
        updateInstallProgressFraction = nil
        updateInstallProgressText = "ダウンロードを開始しています。"
        let version = pendingUpdateVersion
        let expectedSHA256 = pendingUpdateSHA256.lowercased()
        guard let pendingUpdateFileSizeBytes,
              pendingUpdateFileSizeBytes > 0,
              Int64(pendingUpdateFileSizeBytes) <= MAX_UPDATE_DMG_BYTES else {
            updateInstallRunning = false
            updateInstallProgressText = ""
            updateMessage = "更新ファイルのサイズを安全に確認できません。"
            return
        }
        let expectedFileSizeBytes = Int64(pendingUpdateFileSizeBytes)
        let allowedHosts = pendingUpdateAllowedHosts
        let updatesDir = paths.support.appendingPathComponent("updates", isDirectory: true)
        guard prepareOwnedUpdateDirectory(updatesDir) else {
            updateInstallRunning = false
            updateInstallProgressText = ""
            updateMessage = "更新ファイルの保存先を準備できませんでした。"
            return
        }
        let request = URLRequest(url: downloadURL)
        let task = URLSession.shared.downloadTask(with: request) { [weak self] temporaryURL, response, error in
            guard let self else { return }
            var stagedDMGForCleanup: URL?
            do {
                if let error {
                    throw error
                }
                guard let temporaryURL,
                      let http = response as? HTTPURLResponse,
                      (200..<300).contains(http.statusCode),
                      let finalURL = http.url,
                      isAllowedUpdateURL(
                        finalURL,
                        manifestURL: manifestURL,
                        additionalHosts: allowedHosts
                      ) else {
                    throw UpdateInstallError.invalidDownloadResponse
                }
                let attributes = try FileManager.default.attributesOfItem(atPath: temporaryURL.path)
                guard let number = attributes[.size] as? NSNumber else {
                    throw UpdateInstallError.invalidDownloadResponse
                }
                let actualFileSizeBytes = number.int64Value
                if actualFileSizeBytes != expectedFileSizeBytes {
                    throw UpdateInstallError.fileSizeMismatch(
                        expected: expectedFileSizeBytes,
                        actual: actualFileSizeBytes
                    )
                }
                if http.expectedContentLength > 0,
                   actualFileSizeBytes != http.expectedContentLength {
                    throw UpdateInstallError.fileSizeMismatch(
                        expected: http.expectedContentLength,
                        actual: actualFileSizeBytes
                    )
                }
                let actualSHA256 = try sha256HexFile(temporaryURL)
                guard actualSHA256 == expectedSHA256 else {
                    throw UpdateInstallError.sha256Mismatch(
                        expected: expectedSHA256,
                        actual: actualSHA256
                    )
                }
                let installID = UUID().uuidString.lowercased()
                let localDMG = updatesDir.appendingPathComponent(
                    "TotalSegmentator Wrapper for Mac-\(version)-\(installID)-arm64.dmg"
                )
                try FileManager.default.moveItem(at: temporaryURL, to: localDMG)
                stagedDMGForCleanup = localDMG
                let scriptURL = try writeUpdateInstallerScript(
                    appURL: appURL,
                    dmgURL: localDMG,
                    expectedVersion: version,
                    helperRoot: updatesDir.appendingPathComponent(
                        "install_\(installID)",
                        isDirectory: true
                    )
                )
                try launchUpdateInstaller(scriptURL)
                stagedDMGForCleanup = nil
                DispatchQueue.main.async {
                    self.updateDownloadProgressObservation = nil
                    self.updateDownloadTask = nil
                    self.updateInstallProgressFraction = 1
                    self.updateInstallProgressText = "ダウンロードと検証が完了しました。"
                    self.updateInstallRunning = false
                    self.updateMessage = "更新を開始しました。アプリを終了して置き換えます。"
                    NSApplication.shared.terminate(nil)
                }
            } catch {
                if let stagedDMGForCleanup {
                    try? FileManager.default.removeItem(at: stagedDMGForCleanup)
                }
                DispatchQueue.main.async {
                    self.updateDownloadProgressObservation = nil
                    self.updateDownloadTask = nil
                    self.updateInstallProgressFraction = nil
                    self.updateInstallProgressText = ""
                    self.updateInstallRunning = false
                    self.updateMessage = "更新に失敗しました: \(updateInstallMessage(error))"
                }
            }
        }
        updateDownloadProgressObservation = task.progress.observe(
            \.fractionCompleted,
            options: [.initial, .new]
        ) { [weak self] progress, _ in
            let fraction = max(0, min(1, progress.fractionCompleted))
            let completed = progress.completedUnitCount
            let total = progress.totalUnitCount
            if completed > expectedFileSizeBytes || completed > MAX_UPDATE_DMG_BYTES {
                task.cancel()
            }
            DispatchQueue.main.async {
                self?.updateInstallProgressFraction = total > 0 ? fraction : nil
                if total > 0 {
                    self?.updateInstallProgressText = "更新ファイルを取得中: \(Int(fraction * 100))%"
                } else {
                    self?.updateInstallProgressText = "更新ファイルを取得中です。"
                }
            }
        }
        updateDownloadTask = task
        task.resume()
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
        if setupRunning, let setupState = setupExecutionStateFromLog(text) {
            if let parsedStep = SetupStep(rawValue: setupState.event.step) {
                setupStep = parsedStep
                setupHint = parsedStep.hint
            }
            if !setupState.event.message.isEmpty {
                setupMessage = setupState.event.message
            }
            let downloadSteps = [
                SetupStep.downloadTotalsegWeights.rawValue,
                SetupStep.downloadDentalsegWeights.rawValue,
            ]
            if downloadSteps.contains(setupState.event.step), setupState.event.status == "running" {
                setupDownloadProgress = setupState.downloadProgress
            } else {
                setupDownloadProgress = nil
            }
        }
        if iosMeshRunning && iosMeshPreparingModel {
            iosMeshDownloadProgress = iosMeshDownloadProgressFromLog(text)
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
            if !self.setupRunning && !self.isRunning && !self.iosMeshRunning {
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
    if lines.reversed().contains(where: {
        $0.contains("totalseg_setup_weights_missing_or_invalid")
    }) {
        return "セットアップ用モデルが見つからないか、整合性の確認に失敗しました。アプリを再起動してセットアップをやり直してください。"
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
    case fileSizeMismatch(expected: Int64, actual: Int64)
    case invalidDownloadResponse
    case helperLaunchFailed
    case atomicSwapUnsupported
    case updateTransactionPending
    case unsafeUpdateWorkspace
}

func updateInstallMessage(_ error: Error) -> String {
    if let updateError = error as? UpdateInstallError {
        switch updateError {
        case let .sha256Mismatch(expected, actual):
            return "更新ファイルのSHA256が一致しません。expected \(expected), actual \(actual)"
        case let .fileSizeMismatch(expected, actual):
            return "更新ファイルのサイズが一致しません。expected \(expected), actual \(actual)"
        case .invalidDownloadResponse:
            return "更新ファイルのHTTPS配布先または応答を確認できません。"
        case .helperLaunchFailed:
            return "更新用helperを起動できませんでした。"
        case .atomicSwapUnsupported:
            return "この保存先では安全な自動更新に対応していません。DMGから手動で更新してください。"
        case .updateTransactionPending:
            return "前回の更新処理のファイルが残っているため、安全に自動更新できません。指定の配布ページからDMGを取得し、アプリを置き換えてください。"
        case .unsafeUpdateWorkspace:
            return "更新用フォルダの所有者またはファイル種別を安全に確認できません。"
        }
    }
    return String(describing: error)
}

func sha256Hex(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

func sha256HexFile(_ url: URL) throws -> String {
    let stream = try FileHandle(forReadingFrom: url)
    defer { try? stream.close() }
    var digest = SHA256()
    while true {
        let block = try stream.read(upToCount: 1024 * 1024) ?? Data()
        if block.isEmpty { break }
        digest.update(data: block)
    }
    return digest.finalize().map { String(format: "%02x", $0) }.joined()
}

func isHTTPSURL(_ url: URL) -> Bool {
    url.scheme?.lowercased() == "https"
        && url.host?.isEmpty == false
        && url.user == nil
        && url.password == nil
        && (url.port == nil || url.port == 443)
}

func isAllowedUpdateURL(
    _ url: URL,
    manifestURL: URL,
    additionalHosts: [String]
) -> Bool {
    guard isHTTPSURL(url), isHTTPSURL(manifestURL),
          let host = url.host?.lowercased(),
          let manifestHost = manifestURL.host?.lowercased() else {
        return false
    }
    let explicitlyAllowed = additionalHosts.compactMap { candidate -> String? in
        let normalized = candidate.lowercased()
        guard !normalized.isEmpty,
              normalized.unicodeScalars.allSatisfy({
                CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789.-")
                    .contains($0)
              }) else {
            return nil
        }
        return normalized
    }
    return Set(explicitlyAllowed + [manifestHost]).contains(host)
}

func isSHA256Hex(_ value: String) -> Bool {
    value.count == 64 && value.unicodeScalars.allSatisfy {
        CharacterSet(charactersIn: "0123456789abcdef").contains($0)
    }
}

private func isOwnedNormalUpdateDirectory(_ url: URL) -> Bool {
    var info = stat()
    guard lstat(url.standardizedFileURL.path, &info) == 0,
          (info.st_mode & mode_t(S_IFMT)) == mode_t(S_IFDIR),
          info.st_uid == getuid(),
          (info.st_mode & mode_t(S_IWGRP | S_IWOTH)) == 0 else {
        return false
    }
    return true
}

private func updateStatusDestinationIsSafeForReplace(_ url: URL) -> Bool {
    let statusURL = url.standardizedFileURL
    if !fileSystemEntryExists(statusURL) {
        return true
    }
    var info = stat()
    guard lstat(statusURL.path, &info) == 0,
          (info.st_mode & mode_t(S_IFMT)) == mode_t(S_IFREG),
          info.st_uid == getuid(),
          info.st_nlink == 1,
          (info.st_mode & mode_t(S_IWGRP | S_IWOTH)) == 0 else {
        return false
    }
    return true
}

func prepareOwnedUpdateDirectory(_ url: URL) -> Bool {
    let directory = url.standardizedFileURL
    let parent = directory.deletingLastPathComponent().standardizedFileURL
    guard isOwnedNormalUpdateDirectory(parent) else { return false }
    if fileSystemEntryExists(directory) {
        return isOwnedNormalUpdateDirectory(directory)
    }
    do {
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o755]
        )
    } catch {
        return false
    }
    return isOwnedNormalUpdateDirectory(directory)
}

func createFreshOwnedUpdateDirectory(_ url: URL) -> Bool {
    let directory = url.standardizedFileURL
    guard !fileSystemEntryExists(directory) else { return false }
    return prepareOwnedUpdateDirectory(directory)
}

private let updateAtomicAppBundleName = "TotalSegmentator Wrapper for Mac.app"
private let updateTransactionSchema = "totalsegmentator_wrapper_mac.update_transaction.v1"
private let updateTransactionFilename = ".totalsegmentator-wrapper-update-transaction.json"
private let updateStagePrefix = ".TotalSegmentator Wrapper for Mac.app.update-stage-"

private func semanticVersionTripletParts(_ value: String) -> [Substring]? {
    let parts = value.split(separator: ".", omittingEmptySubsequences: false)
    guard parts.count == 3 else {
        return nil
    }
    for part in parts {
        guard !part.isEmpty,
              (part.count == 1 || part.first != "0"),
              part.utf8.allSatisfy({ byte in byte >= 48 && byte <= 57 }) else {
            return nil
        }
    }
    return parts
}

func compareSemanticVersionTriplets(_ left: String, _ right: String) -> Int? {
    guard let leftParts = semanticVersionTripletParts(left),
          let rightParts = semanticVersionTripletParts(right) else {
        return nil
    }
    for (leftPart, rightPart) in zip(leftParts, rightParts) {
        if leftPart.count != rightPart.count {
            return leftPart.count > rightPart.count ? 1 : -1
        }
        if leftPart != rightPart {
            return leftPart.lexicographicallyPrecedes(rightPart) ? -1 : 1
        }
    }
    return 0
}

func validatedUpdateCheckStatus(
    _ result: [String: Any],
    expectedManifestURL: String,
    expectedCurrentVersion: String
) -> String? {
    guard result["schema"] as? String
            == "totalsegmentator_wrapper_mac.update_check_result.v1",
          result["manifest_url"] as? String == expectedManifestURL,
          result["current_version"] as? String == expectedCurrentVersion,
          let status = result["status"] as? String,
          let latestVersion = result["latest_version"] as? String,
          let updateAvailable = result["update_available"] as? Bool,
          let critical = result["critical"] as? Bool,
          let versionOrder = compareSemanticVersionTriplets(
            latestVersion,
            expectedCurrentVersion
          ) else {
        return nil
    }
    switch status {
    case "current":
        return versionOrder == 0 && !updateAvailable && !critical ? status : nil
    case "update_available":
        return versionOrder == 1 && updateAvailable && !critical ? status : nil
    case "critical_update_available":
        return versionOrder == 1 && updateAvailable && critical ? status : nil
    default:
        return nil
    }
}

struct UpdateBundleIdentity: Equatable {
    let bundleID: String
    let teamID: String
    let version: String

    init?(bundleID: String, teamID: String, version: String) {
        guard isSafeDiagnosticToken(bundleID),
              isSafeDiagnosticToken(teamID),
              isSafeDiagnosticToken(version),
              semanticVersionTripletParts(version) != nil else {
            return nil
        }
        self.bundleID = bundleID
        self.teamID = teamID
        self.version = version
    }
}

enum UpdateTransactionRecoveryDecision: Equatable {
    case discardStagedUpdate
    case finalizeInstalledUpdate
    case manualRecoveryRequired
}

func updateTransactionRecoveryDecision(
    active: UpdateBundleIdentity?,
    staged: UpdateBundleIdentity?,
    stageExists: Bool,
    transactionStage: String,
    previous: UpdateBundleIdentity,
    target: UpdateBundleIdentity
) -> UpdateTransactionRecoveryDecision {
    guard compareSemanticVersionTriplets(target.version, previous.version) == 1,
          ["swap", "swapped"].contains(transactionStage),
          (stageExists || staged == nil),
          (!stageExists || staged != nil) else {
        return .manualRecoveryRequired
    }
    if active == previous {
        // The only safe automatic discard is the narrow window after the
        // verified target was journaled and before (or after a rolled-back)
        // swap.  A missing or invalid stage can be a partial copy, so it must
        // remain for manual recovery.
        return transactionStage == "swap" && stageExists && staged == target
            ? .discardStagedUpdate
            : .manualRecoveryRequired
    }
    if active == target {
        // After a successful swap, the controlled stage holds the verified
        // previous bundle.  It can also be absent when cleanup completed just
        // before an interruption.  Any present-but-invalid stage is manual.
        if !stageExists || staged == previous {
            return .finalizeInstalledUpdate
        }
    }
    return .manualRecoveryRequired
}

private enum UpdateAtomicSwapExitCode: Int32 {
    case success = 0
    case invalidArguments = 64
    case invalidLocation = 65
    case helperVerificationFailed = 66
    case inputVerificationFailed = 67
    case swapFailed = 68
}

private enum UpdateAtomicSwapMode: String {
    case install = "--update-atomic-swap"
    case rollback = "--update-atomic-rollback"
}

private enum UpdateAtomicSwapError: Error {
    case invalidLocation
    case unsupportedVolume
    case swapFailed(Int32)
}

private struct UpdateTransaction {
    let token: String
    let stage: String
    let previous: UpdateBundleIdentity
    let target: UpdateBundleIdentity

    init?(payload: [String: Any], appURL: URL) {
        guard payload["schema"] as? String == updateTransactionSchema,
              let token = payload["token"] as? String,
              updateTransactionTokenIsValid(token),
              payload["app_name"] as? String == updateAtomicAppBundleName,
              let stage = payload["stage"] as? String,
              ["swap", "swapped"].contains(stage),
              payload["stage_name"] as? String
                == updateStagingURL(appURL: appURL, token: token).lastPathComponent,
              let previousBundleID = payload["previous_bundle_id"] as? String,
              let previousTeamID = payload["previous_team_id"] as? String,
              let previousVersion = payload["previous_version"] as? String,
              let targetVersion = payload["target_version"] as? String,
              let previous = UpdateBundleIdentity(
                bundleID: previousBundleID,
                teamID: previousTeamID,
                version: previousVersion
              ),
              let target = UpdateBundleIdentity(
                bundleID: previousBundleID,
                teamID: previousTeamID,
                version: targetVersion
              ),
              compareSemanticVersionTriplets(target.version, previous.version) == 1,
              appURL.lastPathComponent == updateAtomicAppBundleName
        else {
            return nil
        }
        self.token = token
        self.stage = stage
        self.previous = previous
        self.target = target
    }
}

enum UpdateTransactionRecoveryResult: Equatable {
    case none
    case preservedPreviousApp
    case finalizedInstalledApp
    case manualRecoveryRequired
}

func updateTransactionURL(appURL: URL) -> URL {
    appURL
        .deletingLastPathComponent()
        .appendingPathComponent(updateTransactionFilename)
        .standardizedFileURL
}

func updateStagingURL(appURL: URL, token: String) -> URL {
    appURL
        .deletingLastPathComponent()
        .appendingPathComponent("\(updateStagePrefix)\(token)", isDirectory: true)
        .standardizedFileURL
}

private func updateTransactionTokenIsValid(_ token: String) -> Bool {
    token == token.lowercased() && UUID(uuidString: token)?.uuidString.lowercased() == token
}

private func updateStageToken(stageURL: URL, appURL: URL) -> String? {
    guard sameFileURL(stageURL.deletingLastPathComponent(), appURL.deletingLastPathComponent()),
          stageURL.lastPathComponent.hasPrefix(updateStagePrefix) else {
        return nil
    }
    let token = String(stageURL.lastPathComponent.dropFirst(updateStagePrefix.count))
    guard updateTransactionTokenIsValid(token),
          stageURL.lastPathComponent == updateStagingURL(appURL: appURL, token: token).lastPathComponent
    else {
        return nil
    }
    return token
}

private func isNormalDirectory(_ url: URL) -> Bool {
    guard let values = try? url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey]) else {
        return false
    }
    return values.isDirectory == true && values.isSymbolicLink != true
}

private func isNormalRegularFile(_ url: URL) -> Bool {
    guard let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey]) else {
        return false
    }
    return values.isRegularFile == true && values.isSymbolicLink != true
}

private func fileSystemEntryExists(_ url: URL) -> Bool {
    var info = stat()
    return lstat(url.path, &info) == 0
}

/// Returns true for every matching directory entry, including files and
/// symlinks.  A partial staging path is opaque until a human resolves it; do
/// not infer ownership from its name or remove it on a later automatic update.
func hasAnyUpdateStageArtifact(appURL: URL) -> Bool {
    let app = appURL.standardizedFileURL
    let parent = app.deletingLastPathComponent().standardizedFileURL
    guard app.lastPathComponent == updateAtomicAppBundleName,
          isNormalDirectory(parent),
          let entries = try? FileManager.default.contentsOfDirectory(atPath: parent.path)
    else {
        return true
    }
    return entries.contains { $0.hasPrefix(updateStagePrefix) }
}

func hasPendingUpdateArtifacts(appURL: URL) -> Bool {
    let app = appURL.standardizedFileURL
    guard app.lastPathComponent == updateAtomicAppBundleName else {
        return true
    }
    return fileSystemEntryExists(updateTransactionURL(appURL: app))
        || hasAnyUpdateStageArtifact(appURL: app)
}

func canUseAtomicUpdateSwap(appURL: URL) -> Bool {
    let app = appURL.standardizedFileURL
    let parent = app.deletingLastPathComponent().standardizedFileURL
    guard app.lastPathComponent == updateAtomicAppBundleName,
          isNormalDirectory(app),
          isNormalDirectory(parent),
          FileManager.default.isWritableFile(atPath: parent.path),
          let values = try? parent.resourceValues(forKeys: [.volumeSupportsSwapRenamingKey])
    else {
        return false
    }
    return values.volumeSupportsSwapRenaming == true
}

private func isValidAtomicUpdateSwapPair(appURL: URL, stageURL: URL) -> Bool {
    let app = appURL.standardizedFileURL
    let stage = stageURL.standardizedFileURL
    return canUseAtomicUpdateSwap(appURL: app)
        && isNormalDirectory(stage)
        && updateStageToken(stageURL: stage, appURL: app) != nil
}

func performAtomicUpdateSwap(appURL: URL, stageURL: URL) throws {
    let app = appURL.standardizedFileURL
    let stage = stageURL.standardizedFileURL
    guard canUseAtomicUpdateSwap(appURL: app) else {
        throw UpdateAtomicSwapError.unsupportedVolume
    }
    guard isValidAtomicUpdateSwapPair(appURL: app, stageURL: stage) else {
        throw UpdateAtomicSwapError.invalidLocation
    }
    let result: Int32 = app.path.withCString { appPath in
        stage.path.withCString { stagePath in
            renameatx_np(AT_FDCWD, appPath, AT_FDCWD, stagePath, UInt32(RENAME_SWAP))
        }
    }
    guard result == 0 else {
        throw UpdateAtomicSwapError.swapFailed(errno)
    }
}

private func updateProcessOutput(executable: String, arguments: [String]) -> (Int32, String)? {
    let process = Process()
    let pipe = Pipe()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    process.standardOutput = pipe
    process.standardError = pipe
    do {
        try process.run()
        process.waitUntilExit()
    } catch {
        return nil
    }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    return (process.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}

private func updateCommandSucceeds(executable: String, arguments: [String]) -> Bool {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do {
        try process.run()
        process.waitUntilExit()
        return process.terminationStatus == 0
    } catch {
        return false
    }
}

private func updateCodeSigningTeamID(appURL: URL) -> String? {
    guard let result = updateProcessOutput(
        executable: "/usr/bin/codesign",
        arguments: ["-dv", "--verbose=4", appURL.path]
    ), result.0 == 0 else {
        return nil
    }
    for line in result.1.split(whereSeparator: \.isNewline) {
        guard line.hasPrefix("TeamIdentifier=") else { continue }
        let value = String(line.dropFirst("TeamIdentifier=".count))
        return isSafeDiagnosticToken(value) ? value : nil
    }
    return nil
}

private func updateBundleIdentifierAndVersion(for appURL: URL) -> (bundleID: String, version: String)? {
    guard isNormalDirectory(appURL) else { return nil }
    let infoURL = appURL.appendingPathComponent("Contents/Info.plist")
    guard isNormalRegularFile(infoURL),
          let info = NSDictionary(contentsOf: infoURL) as? [String: Any],
          let bundleID = info["CFBundleIdentifier"] as? String,
          let version = info["CFBundleShortVersionString"] as? String,
          isSafeDiagnosticToken(bundleID),
          isSafeDiagnosticToken(version)
    else {
        return nil
    }
    return (bundleID, version)
}

private func updateBundleIdentity(for appURL: URL) -> UpdateBundleIdentity? {
    guard let unsignedIdentity = updateBundleIdentifierAndVersion(for: appURL),
          let teamID = updateCodeSigningTeamID(appURL: appURL)
    else {
        return nil
    }
    return UpdateBundleIdentity(
        bundleID: unsignedIdentity.bundleID,
        teamID: teamID,
        version: unsignedIdentity.version
    )
}

private func verifySignedUpdateBundle(_ appURL: URL, expected: UpdateBundleIdentity) -> Bool {
    guard isNormalDirectory(appURL),
          updateCommandSucceeds(
            executable: "/usr/bin/codesign",
            arguments: ["--verify", "--deep", "--strict", "--verbose=2", appURL.path]
          ),
          updateCommandSucceeds(
            executable: "/usr/sbin/spctl",
            arguments: ["--assess", "--type", "execute", "--verbose=2", appURL.path]
          )
    else {
        return false
    }
    return updateBundleIdentity(for: appURL) == expected
}

private func runningUpdateHelperBundleURL() -> URL? {
    let bundle = Bundle.main.bundleURL.standardizedFileURL
    guard bundle.lastPathComponent == updateAtomicAppBundleName,
          isNormalDirectory(bundle) else {
        return nil
    }
    return bundle
}

func runAtomicUpdateSwapIfRequested(arguments: [String]) -> Int32? {
    guard arguments.dropFirst().contains(where: {
        $0 == UpdateAtomicSwapMode.install.rawValue || $0 == UpdateAtomicSwapMode.rollback.rawValue
    }) else {
        return nil
    }
    guard arguments.count == 9,
          let mode = UpdateAtomicSwapMode(rawValue: arguments[1]),
          let expectedApp = UpdateBundleIdentity(
            bundleID: arguments[4], teamID: arguments[5], version: arguments[6]
          ),
          let expectedStage = UpdateBundleIdentity(
            bundleID: arguments[4], teamID: arguments[5], version: arguments[7]
          ),
          let expectedHelper = UpdateBundleIdentity(
            bundleID: arguments[4], teamID: arguments[5], version: arguments[8]
          ),
          let helperURL = runningUpdateHelperBundleURL()
    else {
        return UpdateAtomicSwapExitCode.invalidArguments.rawValue
    }
    let versionDirectionIsValid: Bool
    switch mode {
    case .install:
        versionDirectionIsValid = compareSemanticVersionTriplets(
            expectedStage.version,
            expectedApp.version
        ) == 1 && expectedHelper.version == expectedStage.version
    case .rollback:
        versionDirectionIsValid = compareSemanticVersionTriplets(
            expectedApp.version,
            expectedStage.version
        ) == 1 && expectedHelper.version == expectedApp.version
    }
    guard versionDirectionIsValid else {
        return UpdateAtomicSwapExitCode.invalidArguments.rawValue
    }
    let appURL = URL(fileURLWithPath: arguments[2]).standardizedFileURL
    let stageURL = URL(fileURLWithPath: arguments[3]).standardizedFileURL
    guard isValidAtomicUpdateSwapPair(appURL: appURL, stageURL: stageURL) else {
        return UpdateAtomicSwapExitCode.invalidLocation.rawValue
    }
    guard verifySignedUpdateBundle(helperURL, expected: expectedHelper) else {
        return UpdateAtomicSwapExitCode.helperVerificationFailed.rawValue
    }
    let inputsAreValid: Bool
    switch mode {
    case .install:
        inputsAreValid = verifySignedUpdateBundle(appURL, expected: expectedApp)
            && verifySignedUpdateBundle(stageURL, expected: expectedStage)
    case .rollback:
        // The target may be the reason for this rollback (for example, a
        // post-swap codesign or Gatekeeper failure).  The mounted helper and
        // previous bundle remain fully verified; requiring the broken target
        // to verify here would make recovery impossible.
        let appIdentity = updateBundleIdentifierAndVersion(for: appURL)
        inputsAreValid = appIdentity?.bundleID == expectedApp.bundleID
            && appIdentity?.version == expectedApp.version
            && verifySignedUpdateBundle(stageURL, expected: expectedStage)
    }
    guard inputsAreValid else {
        return UpdateAtomicSwapExitCode.inputVerificationFailed.rawValue
    }
    do {
        try performAtomicUpdateSwap(appURL: appURL, stageURL: stageURL)
        return UpdateAtomicSwapExitCode.success.rawValue
    } catch {
        return UpdateAtomicSwapExitCode.swapFailed.rawValue
    }
}

func updateTransactionFileURLIsSafe(_ url: URL, appURL: URL) -> Bool {
    let app = appURL.standardizedFileURL
    let transactionURL = url.standardizedFileURL
    let expectedURL = updateTransactionURL(appURL: app)
    let parent = app.deletingLastPathComponent().standardizedFileURL
    guard app.lastPathComponent == updateAtomicAppBundleName,
          transactionURL.path == expectedURL.path,
          transactionURL.deletingLastPathComponent().path == parent.path,
          transactionURL.lastPathComponent == updateTransactionFilename,
          isNormalDirectory(parent) else {
        return false
    }
    var info = stat()
    guard lstat(transactionURL.path, &info) == 0,
          (info.st_mode & mode_t(S_IFMT)) == mode_t(S_IFREG),
          info.st_nlink == 1,
          info.st_uid == getuid() else {
        return false
    }
    return true
}

private func removeUpdateTransaction(_ transactionURL: URL, appURL: URL) -> Bool {
    guard updateTransactionFileURLIsSafe(transactionURL, appURL: appURL) else { return false }
    do {
        try FileManager.default.removeItem(at: transactionURL)
        return true
    } catch {
        return false
    }
}

private func removeOwnedUpdateStage(appURL: URL, stageURL: URL) -> Bool {
    guard isValidAtomicUpdateSwapPair(appURL: appURL, stageURL: stageURL) else {
        return false
    }
    do {
        try FileManager.default.removeItem(at: stageURL)
        return true
    } catch {
        return false
    }
}

private func writeUpdateInstallerStatus(
    to statusURL: URL,
    status: String,
    reason: String,
    stage: String,
    returnCode: Int
) {
    guard isOwnedNormalUpdateDirectory(statusURL.deletingLastPathComponent()),
          updateStatusDestinationIsSafeForReplace(statusURL) else {
        return
    }
    writeJSON(
        [
            "schema": "totalsegmentator_wrapper_mac.update_install_status.v1",
            "status": status,
            "reason": reason,
            "stage": stage,
            "return_code": returnCode,
        ],
        to: statusURL
    )
}

func recoverInterruptedUpdateTransaction(
    appURL: URL,
    statusURL: URL
) -> UpdateTransactionRecoveryResult {
    let app = appURL.standardizedFileURL
    let transactionURL = updateTransactionURL(appURL: app)
    guard fileSystemEntryExists(transactionURL) else {
        // A forced stop during ditto can leave a partial stage before a
        // transaction is intentionally written.  It has no authenticated
        // ownership record, so do not remove it or wait for the user to press
        // the next update button before surfacing manual recovery.
        guard app.lastPathComponent == updateAtomicAppBundleName,
              hasAnyUpdateStageArtifact(appURL: app) else {
            return .none
        }
        writeUpdateInstallerStatus(
            to: statusURL,
            status: "failed",
            reason: "update_recovery_required",
            stage: "recovery",
            returnCode: 1
        )
        return .manualRecoveryRequired
    }
    guard canUseAtomicUpdateSwap(appURL: app),
          updateTransactionFileURLIsSafe(transactionURL, appURL: app),
          let payload = readJSON(transactionURL),
          let transaction = UpdateTransaction(payload: payload, appURL: app)
    else {
        writeUpdateInstallerStatus(
            to: statusURL,
            status: "failed",
            reason: "update_recovery_required",
            stage: "recovery",
            returnCode: 1
        )
        return .manualRecoveryRequired
    }
    let stageURL = updateStagingURL(appURL: app, token: transaction.token)
    let stageExists = fileSystemEntryExists(stageURL)
    let active = verifySignedUpdateBundle(app, expected: transaction.previous)
        ? transaction.previous
        : (verifySignedUpdateBundle(app, expected: transaction.target) ? transaction.target : nil)
    let staged = stageExists && isNormalDirectory(stageURL)
        ? (verifySignedUpdateBundle(stageURL, expected: transaction.previous)
            ? transaction.previous
            : (verifySignedUpdateBundle(stageURL, expected: transaction.target) ? transaction.target : nil))
        : nil
    switch updateTransactionRecoveryDecision(
        active: active,
        staged: staged,
        stageExists: stageExists,
        transactionStage: transaction.stage,
        previous: transaction.previous,
        target: transaction.target
    ) {
    case .discardStagedUpdate:
        guard stageExists,
              staged == transaction.target,
              removeOwnedUpdateStage(appURL: app, stageURL: stageURL),
              removeUpdateTransaction(transactionURL, appURL: app) else {
            writeUpdateInstallerStatus(
                to: statusURL,
                status: "failed",
                reason: "update_recovery_required",
                stage: "recovery",
                returnCode: 1
            )
            return .manualRecoveryRequired
        }
        writeUpdateInstallerStatus(
            to: statusURL,
            status: "failed",
            reason: "update_install_interrupted_before_swap",
            stage: transaction.stage,
            returnCode: 1
        )
        return .preservedPreviousApp
    case .finalizeInstalledUpdate:
        if stageExists {
            guard staged == transaction.previous,
                  removeOwnedUpdateStage(appURL: app, stageURL: stageURL) else {
                writeUpdateInstallerStatus(
                    to: statusURL,
                    status: "failed",
                    reason: "update_recovery_required",
                    stage: "recovery",
                    returnCode: 1
                )
                return .manualRecoveryRequired
            }
        }
        guard removeUpdateTransaction(transactionURL, appURL: app) else {
            writeUpdateInstallerStatus(
                to: statusURL,
                status: "failed",
                reason: "update_recovery_required",
                stage: "recovery",
                returnCode: 1
            )
            return .manualRecoveryRequired
        }
        writeUpdateInstallerStatus(
            to: statusURL,
            status: "success",
            reason: "update_installed",
            stage: "complete",
            returnCode: 0
        )
        return .finalizedInstalledApp
    case .manualRecoveryRequired:
        writeUpdateInstallerStatus(
            to: statusURL,
            status: "failed",
            reason: "update_recovery_required",
            stage: "recovery",
            returnCode: 1
        )
        return .manualRecoveryRequired
    }
}

func writeUpdateInstallerScript(
    appURL: URL,
    dmgURL: URL,
    expectedVersion: String,
    helperRoot: URL
) throws -> URL {
    let app = appURL.standardizedFileURL
    guard canUseAtomicUpdateSwap(appURL: app) else {
        throw UpdateInstallError.atomicSwapUnsupported
    }
    guard isSafeDiagnosticToken(expectedVersion) else {
        throw UpdateInstallError.invalidDownloadResponse
    }
    let updateToken = UUID().uuidString.lowercased()
    let stagedURL = updateStagingURL(appURL: app, token: updateToken)
    let transactionURL = updateTransactionURL(appURL: app)
    guard !hasPendingUpdateArtifacts(appURL: app) else {
        throw UpdateInstallError.updateTransactionPending
    }
    let mountURL = helperRoot.appendingPathComponent("mount", isDirectory: true)
    guard createFreshOwnedUpdateDirectory(helperRoot) else {
        throw UpdateInstallError.unsafeUpdateWorkspace
    }
    let scriptURL = helperRoot.appendingPathComponent("install_update.zsh")
    let appPath = shellSingleQuote(app.path)
    let dmgPath = shellSingleQuote(dmgURL.path)
    let mountPath = shellSingleQuote(mountURL.path)
    let version = shellSingleQuote(expectedVersion)
    let stagedPath = shellSingleQuote(stagedURL.path)
    let transactionPath = shellSingleQuote(transactionURL.path)
    let updateTokenPath = shellSingleQuote(updateToken)
    let statusPath = shellSingleQuote(
        helperRoot.deletingLastPathComponent()
            .appendingPathComponent("update_install_status.json")
            .path
    )
    let logPath = shellSingleQuote(
        helperRoot.appendingPathComponent("update_install.log").path
    )
    let script = """
#!/bin/zsh
set -euo pipefail
DMG=\(dmgPath)
APP=\(appPath)
MOUNT=\(mountPath)
EXPECTED_VERSION=\(version)
STAGED_NEW=\(stagedPath)
UPDATE_TRANSACTION=\(transactionPath)
UPDATE_TOKEN=\(updateTokenPath)
UPDATE_STATUS_JSON=\(statusPath)
UPDATE_INSTALL_LOG=\(logPath)
APP_PARENT="$(/usr/bin/dirname "$APP")"
UPDATE_SUCCEEDED=0
SWAP_COMPLETED=0
SWAP_ROLLED_BACK=0
STAGE_CREATED=0
TRANSACTION_WRITTEN=0
STAGED_NEW_ID=""
UPDATE_TRANSACTION_ID=""
PENDING_ARTIFACTS_DETECTED=0
UPDATE_STAGE=initialize
/bin/mkdir -p "$MOUNT"
is_safe_token() {
  local VALUE="$1"
  [[ -n "$VALUE" && ${#VALUE} -le 80 ]] || return 1
  case "$VALUE" in
    (*[!A-Za-z0-9._-]*) return 1 ;;
  esac
  return 0
}
is_semantic_version_triplet() {
  local VALUE="$1"
  local PART
  local -a PARTS
  PARTS=("${(@s:.:)VALUE}")
  [[ "${#PARTS[@]}" == "3" ]] || return 1
  for PART in "${PARTS[@]}"; do
    [[ -n "$PART" ]] || return 1
    case "$PART" in
      (*[!0-9]*) return 1 ;;
    esac
    [[ "$PART" == "0" || "$PART" != 0* ]] || return 1
  done
  return 0
}
compare_semantic_version_triplets() {
  local LEFT="$1"
  local RIGHT="$2"
  local INDEX
  local LEFT_PART
  local RIGHT_PART
  local -a LEFT_PARTS
  local -a RIGHT_PARTS
  is_semantic_version_triplet "$LEFT" || return 2
  is_semantic_version_triplet "$RIGHT" || return 2
  LEFT_PARTS=("${(@s:.:)LEFT}")
  RIGHT_PARTS=("${(@s:.:)RIGHT}")
  for INDEX in 1 2 3; do
    LEFT_PART="${LEFT_PARTS[$INDEX]}"
    RIGHT_PART="${RIGHT_PARTS[$INDEX]}"
    if (( ${#LEFT_PART} > ${#RIGHT_PART} )); then
      /usr/bin/printf '1'
      return 0
    fi
    if (( ${#LEFT_PART} < ${#RIGHT_PART} )); then
      /usr/bin/printf '%s' '-1'
      return 0
    fi
    if [[ "$LEFT_PART" > "$RIGHT_PART" ]]; then
      /usr/bin/printf '1'
      return 0
    fi
    if [[ "$LEFT_PART" < "$RIGHT_PART" ]]; then
      /usr/bin/printf '%s' '-1'
      return 0
    fi
  done
  /usr/bin/printf '0'
}
has_pending_update_artifacts() {
  local CANDIDATE
  if [[ -e "$UPDATE_TRANSACTION" || -L "$UPDATE_TRANSACTION" ]]; then
    return 0
  fi
  setopt local_options null_glob
  for CANDIDATE in "$APP_PARENT"/".TotalSegmentator Wrapper for Mac.app.update-stage-"*; do
    if [[ -e "$CANDIDATE" || -L "$CANDIDATE" ]]; then
      return 0
    fi
  done
  return 1
}
file_identity() {
  local TARGET_PATH="$1"
  /usr/bin/stat -f '%d:%i' "$TARGET_PATH"
}
status_destination_is_safe_for_replace() {
  local OWNER
  local LINK_COUNT
  local FILE_TYPE
  if [[ ! -e "$UPDATE_STATUS_JSON" && ! -L "$UPDATE_STATUS_JSON" ]]; then
    return 0
  fi
  [[ ! -L "$UPDATE_STATUS_JSON" && -f "$UPDATE_STATUS_JSON" ]] || return 1
  OWNER="$(/usr/bin/stat -f '%u' "$UPDATE_STATUS_JSON")" || return 1
  LINK_COUNT="$(/usr/bin/stat -f '%l' "$UPDATE_STATUS_JSON")" || return 1
  FILE_TYPE="$(/usr/bin/stat -f '%HT' "$UPDATE_STATUS_JSON")" || return 1
  [[ "$OWNER" == "$(/usr/bin/id -u)" && "$LINK_COUNT" == "1" && "$FILE_TYPE" == "Regular File" ]]
}
record_owned_staged_new() {
  [[ ! -L "$STAGED_NEW" && -d "$STAGED_NEW" ]] || return 1
  STAGED_NEW_ID="$(file_identity "$STAGED_NEW")" || return 1
  [[ -n "$STAGED_NEW_ID" ]]
}
owned_staged_new_identity_matches() {
  [[ -n "$STAGED_NEW_ID" && -e "$STAGED_NEW" && ! -L "$STAGED_NEW" && -d "$STAGED_NEW" ]] || return 1
  [[ "$(file_identity "$STAGED_NEW")" == "$STAGED_NEW_ID" ]]
}
remove_staged_new() {
  if [[ ! -e "$STAGED_NEW" && ! -L "$STAGED_NEW" ]]; then
    return 0
  fi
  [[ ! -L "$STAGED_NEW" && -d "$STAGED_NEW" ]] || return 1
  /bin/rm -rf "$STAGED_NEW"
}
remove_owned_staged_new() {
  if [[ "$STAGE_CREATED" != "1" ]]; then
    if [[ ! -e "$STAGED_NEW" && ! -L "$STAGED_NEW" ]]; then
      return 0
    fi
    return 1
  fi
  owned_staged_new_identity_matches || return 1
  remove_staged_new
}
update_transaction_is_safe() {
  local OWNER
  local LINK_COUNT
  local FILE_TYPE
  [[ "$UPDATE_TRANSACTION" == "$APP_PARENT/.totalsegmentator-wrapper-update-transaction.json" ]] || return 1
  [[ ! -L "$UPDATE_TRANSACTION" && -f "$UPDATE_TRANSACTION" ]] || return 1
  OWNER="$(/usr/bin/stat -f '%u' "$UPDATE_TRANSACTION")" || return 1
  LINK_COUNT="$(/usr/bin/stat -f '%l' "$UPDATE_TRANSACTION")" || return 1
  FILE_TYPE="$(/usr/bin/stat -f '%HT' "$UPDATE_TRANSACTION")" || return 1
  [[ "$OWNER" == "$(/usr/bin/id -u)" && "$LINK_COUNT" == "1" && "$FILE_TYPE" == "Regular File" ]]
}
remove_update_transaction() {
  if [[ ! -e "$UPDATE_TRANSACTION" && ! -L "$UPDATE_TRANSACTION" ]]; then
    return 0
  fi
  update_transaction_is_safe || return 1
  /bin/rm -f "$UPDATE_TRANSACTION"
}
remove_own_update_transaction() {
  if [[ "$TRANSACTION_WRITTEN" != "1" ]]; then
    if [[ ! -e "$UPDATE_TRANSACTION" && ! -L "$UPDATE_TRANSACTION" ]]; then
      return 0
    fi
    return 1
  fi
  [[ -n "$UPDATE_TRANSACTION_ID" && -e "$UPDATE_TRANSACTION" && ! -L "$UPDATE_TRANSACTION" ]] || return 1
  [[ "$(file_identity "$UPDATE_TRANSACTION")" == "$UPDATE_TRANSACTION_ID" ]] || return 1
  remove_update_transaction
}
write_update_status() {
  local STATUS="$1"
  local REASON="$2"
  local STAGE="$3"
  local RETURN_CODE="$4"
  local STATUS_TMP
  STATUS_TMP="$(/usr/bin/mktemp "${UPDATE_STATUS_JSON}.tmp.XXXXXX")"
  /usr/bin/printf '{"schema":"totalsegmentator_wrapper_mac.update_install_status.v1","status":"%s","reason":"%s","stage":"%s","return_code":%d}\\n' \
    "$STATUS" "$REASON" "$STAGE" "$RETURN_CODE" > "$STATUS_TMP"
  if ! status_destination_is_safe_for_replace; then
    /bin/rm -f "$STATUS_TMP"
    return 1
  fi
  if ! /bin/mv "$STATUS_TMP" "$UPDATE_STATUS_JSON"; then
    /bin/rm -f "$STATUS_TMP"
    return 1
  fi
  status_destination_is_safe_for_replace
}
write_update_transaction() {
  local STAGE="$1"
  local TRANSACTION_TMP
  TRANSACTION_TMP="$(/usr/bin/mktemp "${UPDATE_TRANSACTION}.tmp.XXXXXX")"
  /usr/bin/printf '{"schema":"totalsegmentator_wrapper_mac.update_transaction.v1","token":"%s","app_name":"TotalSegmentator Wrapper for Mac.app","stage_name":".TotalSegmentator Wrapper for Mac.app.update-stage-%s","stage":"%s","previous_bundle_id":"%s","previous_team_id":"%s","previous_version":"%s","target_version":"%s"}\\n' \
    "$UPDATE_TOKEN" "$UPDATE_TOKEN" "$STAGE" "$CURRENT_BUNDLE_ID" "$CURRENT_TEAM_ID" "$CURRENT_VERSION" "$EXPECTED_VERSION" > "$TRANSACTION_TMP"
  if /bin/ln "$TRANSACTION_TMP" "$UPDATE_TRANSACTION"; then
    /bin/rm -f "$TRANSACTION_TMP"
    update_transaction_is_safe || return 1
    UPDATE_TRANSACTION_ID="$(file_identity "$UPDATE_TRANSACTION")" || return 1
    [[ -n "$UPDATE_TRANSACTION_ID" ]] || return 1
    TRANSACTION_WRITTEN=1
  else
    /bin/rm -f "$TRANSACTION_TMP"
    return 1
  fi
}
mark_update_stage() {
  UPDATE_STAGE="$1"
  /usr/bin/printf 'stage=%s\\n' "$UPDATE_STAGE"
  write_update_status "running" "update_install_running" "$UPDATE_STAGE" 0
}
rollback_after_failed_postcheck() {
  if /usr/bin/arch -arm64 "$UPDATE_SWAP_EXECUTABLE" --update-atomic-rollback \
    "$APP" "$STAGED_NEW" "$CURRENT_BUNDLE_ID" "$CURRENT_TEAM_ID" \
    "$EXPECTED_VERSION" "$CURRENT_VERSION" "$EXPECTED_VERSION"; then
    record_owned_staged_new || return 1
    return 0
  fi
  return 1
}
verify_rolled_back_app() {
  local ROLLED_BACK_BUNDLE_ID
  local ROLLED_BACK_TEAM_ID
  local ROLLED_BACK_VERSION
  /usr/bin/codesign --verify --deep --strict --verbose=2 "$APP" || return 1
  /usr/sbin/spctl --assess --type execute --verbose=2 "$APP" || return 1
  ROLLED_BACK_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist")" || return 1
  ROLLED_BACK_TEAM_ID="$(/usr/bin/codesign -dv --verbose=4 "$APP" 2>&1 | /usr/bin/sed -n 's/^TeamIdentifier=//p')" || return 1
  ROLLED_BACK_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")" || return 1
  [[ "$ROLLED_BACK_BUNDLE_ID" == "$CURRENT_BUNDLE_ID" \
    && "$ROLLED_BACK_TEAM_ID" == "$CURRENT_TEAM_ID" \
    && "$ROLLED_BACK_VERSION" == "$CURRENT_VERSION" ]]
}
verify_installed_target_app() {
  /usr/bin/codesign --verify --deep --strict --verbose=2 "$APP" || return 1
  /usr/sbin/spctl --assess --type execute --verbose=2 "$APP" || return 1
  INSTALLED_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist")" || return 1
  INSTALLED_TEAM_ID="$(/usr/bin/codesign -dv --verbose=4 "$APP" 2>&1 | /usr/bin/sed -n 's/^TeamIdentifier=//p')" || return 1
  INSTALLED_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")" || return 1
  [[ "$INSTALLED_BUNDLE_ID" == "$CURRENT_BUNDLE_ID" \
    && "$INSTALLED_TEAM_ID" == "$CURRENT_TEAM_ID" \
    && "$INSTALLED_VERSION" == "$EXPECTED_VERSION" ]]
}
cleanup() {
  local RETURN_CODE=$?
  trap - EXIT
  set +e
  if [[ "$UPDATE_SUCCEEDED" != "1" && "$SWAP_COMPLETED" == "1" && "$SWAP_ROLLED_BACK" != "1" ]]; then
    if rollback_after_failed_postcheck && verify_rolled_back_app; then
      SWAP_ROLLED_BACK=1
    fi
  fi
  /usr/bin/hdiutil detach "$MOUNT" >/dev/null 2>&1 || true
  if [[ "$PENDING_ARTIFACTS_DETECTED" == "1" ]]; then
    write_update_status "failed" "update_recovery_required" "recovery" 1
    if [[ -d "$APP" ]]; then
      /usr/bin/open "$APP" || true
    fi
    exit "$RETURN_CODE"
  fi
  if [[ "$UPDATE_SUCCEEDED" == "1" ]]; then
    /bin/rm -f "$DMG"
    if remove_owned_staged_new && remove_own_update_transaction; then
      write_update_status "success" "update_installed" "complete" 0
    else
      write_update_status "failed" "update_recovery_required" "recovery" 1
    fi
    /usr/bin/open "$APP"
    exit 0
  fi
  if [[ "$RETURN_CODE" == "0" ]]; then
    RETURN_CODE=2
  fi
  if [[ "$SWAP_ROLLED_BACK" == "1" ]]; then
    /bin/rm -f "$DMG"
    if remove_owned_staged_new && remove_own_update_transaction; then
      write_update_status "failed" "update_install_failed_rolled_back" "$UPDATE_STAGE" "$RETURN_CODE"
    else
      write_update_status "failed" "update_recovery_required" "recovery" 1
    fi
  elif [[ "$SWAP_COMPLETED" == "1" ]]; then
    write_update_status "failed" "update_recovery_required" "recovery" "$RETURN_CODE"
  elif remove_owned_staged_new && remove_own_update_transaction; then
    /bin/rm -f "$DMG"
    write_update_status "failed" "update_install_failed_before_replace" "$UPDATE_STAGE" "$RETURN_CODE"
  else
    write_update_status "failed" "update_recovery_required" "recovery" 1
  fi
  if [[ -d "$APP" ]]; then
    /usr/bin/open "$APP" || true
  fi
  exit "$RETURN_CODE"
}
trap cleanup EXIT
unsetopt CLOBBER
if ! exec {UPDATE_LOG_FD}> "$UPDATE_INSTALL_LOG"; then
  setopt CLOBBER
  exit 74
fi
setopt CLOBBER
exec 1>&$UPDATE_LOG_FD 2>&1
write_update_status "running" "update_install_running" "$UPDATE_STAGE" 0
/bin/sleep 2
mark_update_stage mount
/usr/bin/hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MOUNT" >/dev/null
NEW_APP="$MOUNT/TotalSegmentator Wrapper for Mac.app"
if [[ ! -d "$APP" || -L "$APP" || "$(/usr/bin/basename "$APP")" != "TotalSegmentator Wrapper for Mac.app" ]]; then
  echo "Installed app is not a normal TotalSegmentator Wrapper for Mac.app bundle" >&2
  exit 3
fi
if [[ ! -d "$NEW_APP" || -L "$NEW_APP" ]]; then
  echo "TotalSegmentator Wrapper for Mac.app was not found in update DMG" >&2
  exit 4
fi
UPDATE_SWAP_EXECUTABLE="$NEW_APP/Contents/MacOS/TotalSegmentatorWrapperForMac"
if [[ ! -f "$UPDATE_SWAP_EXECUTABLE" || -L "$UPDATE_SWAP_EXECUTABLE" || ! -x "$UPDATE_SWAP_EXECUTABLE" ]]; then
  echo "Verified update bundle is missing its atomic update helper" >&2
  exit 10
fi
mark_update_stage verify_download
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$NEW_APP"
/usr/sbin/spctl --assess --type execute --verbose=2 "$NEW_APP"
CURRENT_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist")"
NEW_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$NEW_APP/Contents/Info.plist")"
CURRENT_TEAM_ID="$(/usr/bin/codesign -dv --verbose=4 "$APP" 2>&1 | /usr/bin/sed -n 's/^TeamIdentifier=//p')"
NEW_TEAM_ID="$(/usr/bin/codesign -dv --verbose=4 "$NEW_APP" 2>&1 | /usr/bin/sed -n 's/^TeamIdentifier=//p')"
CURRENT_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
NEW_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$NEW_APP/Contents/Info.plist")"
if ! is_safe_token "$CURRENT_BUNDLE_ID" || ! is_safe_token "$CURRENT_TEAM_ID" || ! is_safe_token "$CURRENT_VERSION" || ! is_safe_token "$EXPECTED_VERSION"; then
  echo "Update identity metadata is not safe" >&2
  exit 5
fi
if [[ -z "$CURRENT_BUNDLE_ID" || "$NEW_BUNDLE_ID" != "$CURRENT_BUNDLE_ID" ]]; then
  echo "Update app bundle identifier does not match the installed app" >&2
  exit 6
fi
if [[ -z "$CURRENT_TEAM_ID" || "$CURRENT_TEAM_ID" == "not set" || "$NEW_TEAM_ID" != "$CURRENT_TEAM_ID" ]]; then
  echo "Update app Developer Team does not match the installed app" >&2
  exit 7
fi
if [[ "$NEW_VERSION" != "$EXPECTED_VERSION" ]]; then
  echo "Update app version does not match the selected update" >&2
  exit 8
fi
if ! VERSION_ORDER="$(compare_semantic_version_triplets "$EXPECTED_VERSION" "$CURRENT_VERSION")"; then
  echo "Update app versions must be semantic version triplets" >&2
  exit 9
fi
if [[ "$VERSION_ORDER" != "1" ]]; then
  echo "Update app version must be newer than the installed version" >&2
  exit 9
fi
if has_pending_update_artifacts; then
  echo "A previous update transaction is still present" >&2
  PENDING_ARTIFACTS_DETECTED=1
  exit 11
fi
mark_update_stage stage_copy
/usr/bin/ditto "$NEW_APP" "$STAGED_NEW"
STAGE_CREATED=1
if [[ ! -d "$STAGED_NEW" || -L "$STAGED_NEW" ]]; then
  echo "Staged update bundle is not a normal directory" >&2
  exit 12
fi
record_owned_staged_new || exit 12
mark_update_stage verify_stage
/usr/bin/codesign --verify --deep --strict --verbose=2 "$STAGED_NEW"
/usr/sbin/spctl --assess --type execute --verbose=2 "$STAGED_NEW"
STAGED_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$STAGED_NEW/Contents/Info.plist")"
STAGED_TEAM_ID="$(/usr/bin/codesign -dv --verbose=4 "$STAGED_NEW" 2>&1 | /usr/bin/sed -n 's/^TeamIdentifier=//p')"
STAGED_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$STAGED_NEW/Contents/Info.plist")"
STAGED_EXECUTABLE="$STAGED_NEW/Contents/MacOS/TotalSegmentatorWrapperForMac"
if [[ ! -f "$STAGED_EXECUTABLE" || -L "$STAGED_EXECUTABLE" || ! -x "$STAGED_EXECUTABLE" ]]; then
  echo "Staged update bundle is missing its atomic update helper" >&2
  exit 13
fi
if [[ "$STAGED_BUNDLE_ID" != "$CURRENT_BUNDLE_ID" || "$STAGED_TEAM_ID" != "$CURRENT_TEAM_ID" || "$STAGED_VERSION" != "$EXPECTED_VERSION" ]]; then
  echo "Staged update identity does not match the verified update" >&2
  exit 13
fi
write_update_transaction "swap"
mark_update_stage swap
if /usr/bin/arch -arm64 "$UPDATE_SWAP_EXECUTABLE" --update-atomic-swap \
  "$APP" "$STAGED_NEW" "$CURRENT_BUNDLE_ID" "$CURRENT_TEAM_ID" \
  "$CURRENT_VERSION" "$EXPECTED_VERSION" "$EXPECTED_VERSION"; then
  :
else
  SWAP_RETURN_CODE=$?
  exit "$SWAP_RETURN_CODE"
fi
SWAP_COMPLETED=1
record_owned_staged_new || exit 71
mark_update_stage verify_installed
if ! verify_installed_target_app; then
  if rollback_after_failed_postcheck && verify_rolled_back_app; then
    SWAP_ROLLED_BACK=1
    exit 72
  fi
  exit 73
fi
UPDATE_SUCCEEDED=1
UPDATE_STAGE=complete
exit 0
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

struct SetupDownloadProgress {
    let source: String
    let status: String
    let taskID: Int?
    let index: Int
    let taskTotal: Int
    let completedBytes: Int?
    let totalBytes: Int?
    let percent: Int?
    let rateBPS: Double?
    let etaSeconds: Int?
    let resumed: Bool
    let resumeFromBytes: Int?

    var fraction: Double? {
        if let percent { return max(0, min(1, Double(percent) / 100)) }
        if let completedBytes, let totalBytes, totalBytes > 0 {
            return max(0, min(1, Double(completedBytes) / Double(totalBytes)))
        }
        return nil
    }

    var displayText: String {
        let model = "モデル \(index)/\(taskTotal)"
        switch status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "downloading":
            var parts = ["\(model)を取得中"]
            if let completedBytes, let totalBytes {
                parts.append("\(formatByteCount(completedBytes)) / \(formatByteCount(totalBytes))")
            }
            if let percent { parts.append("\(percent)%") }
            if let rateBPS, rateBPS > 0 { parts.append("\(formatByteCount(Int(rateBPS)))/秒") }
            if let etaSeconds, etaSeconds > 0 { parts.append("残り約\(formatCompactDuration(etaSeconds))") }
            if resumed {
                if let resumeFromBytes, resumeFromBytes > 0 {
                    parts.append("\(formatByteCount(resumeFromBytes))の中断位置から再開")
                } else {
                    parts.append("中断位置から再開")
                }
            }
            return parts.joined(separator: " ・ ")
        case "verifying":
            var parts = ["\(model)の完全性を確認中"]
            if let completedBytes, let totalBytes {
                parts.append("\(formatByteCount(completedBytes)) / \(formatByteCount(totalBytes))")
            }
            if let percent { parts.append("\(percent)%") }
            return parts.joined(separator: " ・ ")
        case "restart":
            return "\(model)は再開条件を確認できなかったため先頭から再取得します"
        case "complete":
            return "\(model)の準備完了"
        case "failed":
            return "\(model)の取得失敗"
        case "starting":
            return "\(model)の取得を開始しています"
        default:
            return "\(model)の状態を確認中"
        }
    }
}

struct SetupProgressEvent {
    let step: String
    let status: String
    let message: String
}

struct SetupExecutionLogState {
    let event: SetupProgressEvent
    let downloadProgress: SetupDownloadProgress?
}

func setupExecutionStateFromLog(_ text: String) -> SetupExecutionLogState? {
    var event: SetupProgressEvent?
    var downloadProgress: SetupDownloadProgress?
    for rawLine in text.split(whereSeparator: \.isNewline) {
        let line = String(rawLine)
        if line.hasPrefix("SETUP_PROGRESS ") {
            let fields = String(line.dropFirst("SETUP_PROGRESS ".count))
            guard let messageRange = fields.range(of: " message=") else { continue }
            let prefix = fields[..<messageRange.lowerBound]
            let values = Dictionary(
                uniqueKeysWithValues: prefix.split(separator: " ").compactMap { field -> (String, String)? in
                    let parts = field.split(separator: "=", maxSplits: 1).map(String.init)
                    return parts.count == 2 ? (parts[0], parts[1]) : nil
                }
            )
            if let step = values["step"], let status = values["status"] {
                event = SetupProgressEvent(
                    step: step,
                    status: status,
                    message: String(fields[messageRange.upperBound...])
                )
                if step != SetupStep.downloadTotalsegWeights.rawValue || status != "running" {
                    downloadProgress = nil
                }
            }
            continue
        }
        guard line.hasPrefix("SETUP_DOWNLOAD_PROGRESS ") else { continue }
        let jsonText = String(line.dropFirst("SETUP_DOWNLOAD_PROGRESS ".count))
        guard let data = jsonText.data(using: .utf8),
              let payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let source = stringFromJSON(payload["source"]),
              let status = stringFromJSON(payload["status"]),
              let index = intFromJSON(payload["index"]),
              let taskTotal = intFromJSON(payload["task_total"])
        else { continue }
        downloadProgress = SetupDownloadProgress(
            source: source,
            status: status,
            taskID: intFromJSON(payload["task_id"]),
            index: index,
            taskTotal: taskTotal,
            completedBytes: intFromJSON(payload["completed_bytes"]),
            totalBytes: intFromJSON(payload["total_bytes"]),
            percent: intFromJSON(payload["percent"]),
            rateBPS: (payload["rate_bps"] as? NSNumber)?.doubleValue,
            etaSeconds: intFromJSON(payload["eta_seconds"]),
            resumed: (payload["resumed"] as? Bool) ?? false,
            resumeFromBytes: intFromJSON(payload["resume_from_bytes"])
        )
        let downloadStep = source == "dentalsegmentator"
            ? SetupStep.downloadDentalsegWeights
            : SetupStep.downloadTotalsegWeights
        event = SetupProgressEvent(
            step: downloadStep.rawValue,
            status: "running",
            message: downloadStep.hint
        )
    }
    guard let event else { return nil }
    return SetupExecutionLogState(event: event, downloadProgress: downloadProgress)
}

func iosMeshDownloadProgressFromLog(_ text: String) -> SetupDownloadProgress? {
    guard let progress = setupExecutionStateFromLog(text)?.downloadProgress,
          progress.source == "ios-meshsegnet"
    else {
        return nil
    }
    return progress
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
    let resumeFromBytes: Int?

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
        if resumed {
            if let resumeFromBytes, resumeFromBytes > 0 {
                parts.append("\(formatByteCount(resumeFromBytes))の中断位置から再開")
            } else {
                parts.append("中断位置から再開")
            }
        }
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
            resumed: (payload["resumed"] as? Bool) ?? false,
            resumeFromBytes: intFromJSON(payload["resume_from_bytes"])
        )
    }
    return last
}

func dentalSegmentatorPreparationProgressFromLog(
    _ text: String
) -> ToothSegPreparationProgress? {
    guard let download = setupExecutionStateFromLog(text)?.downloadProgress,
          download.source == "dentalsegmentator"
    else {
        return nil
    }
    let status = download.status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    let stage: String
    let message: String
    switch status {
    case "downloading":
        stage = "download"
        if download.resumed {
            message = "DentalSegmentatorのモデル取得を中断位置から再開しています。"
        } else {
            message = "DentalSegmentatorのモデルを取得しています。"
        }
    case "verifying":
        stage = "verifying"
        message = "DentalSegmentatorのモデルの完全性を確認中です。"
    case "restart":
        stage = "restart"
        message = "DentalSegmentatorのモデルは、再開条件を確認できなかったため先頭から再取得します。"
    case "complete":
        stage = "complete"
        message = "DentalSegmentatorのモデルの準備完了です。"
    case "failed":
        stage = "failed"
        message = "DentalSegmentatorのモデルの取得失敗です。"
    case "starting":
        stage = "starting"
        message = "DentalSegmentatorのモデル取得を開始しています。"
    default:
        stage = "unknown"
        message = "DentalSegmentatorのモデルの状態を確認中です。"
    }
    return ToothSegPreparationProgress(
        stage: stage,
        message: message,
        downloadedBytes: download.completedBytes,
        totalBytes: download.totalBytes,
        percent: download.percent,
        rateBPS: download.rateBPS,
        etaSeconds: download.etaSeconds,
        resumed: download.resumed,
        resumeFromBytes: download.resumeFromBytes
    )
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

func isNiftiFile(_ url: URL) -> Bool {
    let name = url.lastPathComponent.lowercased()
    return name.hasSuffix(".nii") || name.hasSuffix(".nii.gz")
}

func currentArchitectureName() -> String {
#if arch(arm64)
    return "arm64"
#elseif arch(x86_64)
    return "x86_64"
#else
    return "unknown"
#endif
}

func isSafeDiagnosticToken(_ value: String) -> Bool {
    guard !value.isEmpty, value.count <= 80 else {
        return false
    }
    let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789_-.")
    return value.unicodeScalars.allSatisfy { allowed.contains($0) }
}

func sameFileURL(_ left: URL, _ right: URL) -> Bool {
    left.standardizedFileURL.path == right.standardizedFileURL.path
}
