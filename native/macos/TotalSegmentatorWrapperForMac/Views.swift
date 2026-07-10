import SwiftUI
import AppKit

struct RootView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        NavigationSplitView {
            SidebarView()
        } detail: {
            VStack(alignment: .leading, spacing: 18) {
                HeaderView()
                Divider()
                detailView
                Spacer(minLength: 0)
            }
            .padding(22)
            .frame(minWidth: 760, minHeight: 620)
            .sheet(isPresented: $state.showLog) {
                LogSheetView()
                    .environmentObject(state)
            }
            .sheet(isPresented: $state.showDicomSeriesSelection) {
                DicomSeriesSelectionSheet()
                    .environmentObject(state)
            }
            .toolbar {
                ToolbarItem {
                    Button {
                        state.showDetailedLog()
                    } label: {
                        Label("詳細ログ", systemImage: "terminal")
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var detailView: some View {
        switch state.screen {
        case .setup:
            SetupView()
        case .start:
            StartChoiceView()
        case .inputAndCreation:
            InputAndCreationView()
        case .running:
            RunProgressView()
        case .ctPreview:
            CTPreviewView()
        case .result:
            ResultView()
        }
    }
}

struct SidebarView: View {
    @EnvironmentObject var state: AppState

    private let steps = [
        ("1", "目的", "Sampleか手元のCTデータを選びます。"),
        ("2", "入力", "CT入力を確認します。"),
        ("3", "実行", "このMacで3Dプレビューを作成します。"),
        ("4", "結果", "3Dプレビューと要約を確認します。"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("TotalSegmentator Wrapper for Mac")
                .font(.title2.weight(.semibold))
            Text("研究・教育・検証用の3Dプレビューを作成します。")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Divider()
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                HStack(alignment: .top, spacing: 10) {
                    Text(step.0)
                        .font(.headline)
                        .frame(width: 28, height: 28)
                        .background(index == state.selectedStep && state.screen != .setup ? Color.accentColor : Color.secondary.opacity(0.15))
                        .foregroundStyle(index == state.selectedStep && state.screen != .setup ? Color.white : Color.primary)
                        .clipShape(Circle())
                    VStack(alignment: .leading, spacing: 3) {
                        Text(step.1)
                            .font(.headline)
                        Text(step.2)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.vertical, 4)
                .contentShape(Rectangle())
                .onTapGesture {
                    guard !state.isRunning && state.screen != .setup else { return }
                    if index == 0 {
                        state.goToStart()
                    } else if index == 1 {
                        state.goToInput()
                    }
                }
            }
            Spacer()
            Button {
                state.checkUpdates()
            } label: {
                Label(state.updateCheckRunning ? "更新確認中" : "更新を確認", systemImage: "arrow.triangle.2.circlepath")
            }
            .buttonStyle(.bordered)
            .disabled(state.updateCheckRunning)
            if !state.updateMessage.isEmpty {
                Text(state.updateMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if state.pendingDownloadURL != nil {
                Button {
                    state.openPendingDownload()
                } label: {
                    Label(state.updateInstallRunning ? "更新中" : "更新をインストール", systemImage: "arrow.down.app")
                }
                .buttonStyle(.bordered)
                .disabled(state.updateInstallRunning)
                .confirmationDialog(
                    "更新をダウンロードしてインストールしますか？",
                    isPresented: $state.showingUpdateConfirmation,
                    titleVisibility: .visible
                ) {
                    Button("更新する") { state.confirmOpenPendingDownload() }
                    Button("キャンセル", role: .cancel) {}
                } message: {
                    Text("更新ファイルを検証し、このアプリを置き換えて再起動します。DICOM/CT/処理結果は送信しません。")
                }
            }
        }
        .padding(18)
        .frame(minWidth: 270)
    }
}

struct HeaderView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 6) {
                Text(title)
                    .font(.largeTitle.weight(.semibold))
                Text(subtitle)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            StatusPill(text: state.statusText)
        }
    }

    private var title: String {
        switch state.screen {
        case .setup: return "はじめの準備"
        case .start: return "最初はSampleで流れを確認"
        case .inputAndCreation: return "入力と作成内容"
        case .running: return "処理中"
        case .ctPreview: return "中央断面を確認"
        case .result: return "結果"
        }
    }

    private var subtitle: String {
        switch state.screen {
        case .setup: return "管理者権限不要。このMac内のアプリ専用フォルダだけを使います。"
        case .start: return "入力から結果確認までを先に試せます。"
        case .inputAndCreation: return "入力を確認し、作成する3Dプレビューを選びます。"
        case .running: return "現在の処理と経過時間を表示します。"
        case .ctPreview: return "歯列と確認したい範囲が3方向すべてに写っているか確認してください。"
        case .result: return "作成したファイルと次の操作を確認できます。"
        }
    }
}

struct SetupView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("3Dプレビューを作成するための機能を、このMacに準備します。")
                .foregroundStyle(.secondary)
            DisclosureGroup("データの扱い") {
                Text("入力データ、作成結果、ログはこのMacのアプリ専用フォルダに保存します。外部へ送信しません。研究・教育目的の非臨床プレビューです。医療機器ではなく、診断、治療方針の決定、治療計画、またはその他の医療上の判断には使用できません。")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text(state.setupRunning ? state.setupStep.label : "準備を始める")
                        .font(.title3.weight(.semibold))
                    Text(state.setupHint)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if state.setupRunning {
                    Text(state.setupElapsed)
                        .foregroundStyle(.secondary)
                }
            }
            if state.setupRunning {
                ProgressView()
                    .progressViewStyle(.linear)
            }

            if !state.setupError.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text(state.setupError)
                        .foregroundStyle(.red)
                    if !state.setupRecoveryText.isEmpty {
                        Text(state.setupRecoveryText)
                            .foregroundStyle(.secondary)
                    }
                }
            } else {
                Text(state.setupMessage)
                    .foregroundStyle(.secondary)
            }

            HStack {
                Button {
                    state.startSetup()
                } label: {
                    Label("準備を始める", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                .disabled(state.setupRunning)

                Button {
                    state.openSampleViewer()
                } label: {
                    Label("3Dサンプルを開く", systemImage: "cube.transparent")
                }
                .buttonStyle(.bordered)

                Spacer()
            }
        }
    }
}

struct StartChoiceView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            ChoiceCardContent(
                title: "Sampleから始める",
                subtitle: "入力から結果確認までの流れを先に試します。",
                icon: "sparkles",
                primaryAction: {
                    state.goToSample()
                }
            ) {
                VStack(alignment: .leading, spacing: 10) {
                    Label("おすすめ", systemImage: "star.fill")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.tint)
                    Button {
                        state.openSampleViewer()
                    } label: {
                        Label("Sample 1の3Dプレビューを開く", systemImage: "cube.transparent")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }
            }
            ChoiceCardContent(
                title: "手元のCTデータを使う",
                subtitle: "CTファイルまたは撮影フォルダを選びます。必要な確認と取り込み準備はアプリ内で行います。",
                icon: "folder",
                primaryAction: {
                    state.goToOwnData()
                }
            ) {
                EmptyView()
            }
        }
    }
}

struct InputAndCreationView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 10) {
                    Button {
                        state.useSampleInput()
                    } label: {
                        Label(state.isSampleInputSelected ? "Sample 1（選択済み）" : "Sample 1を選ぶ", systemImage: state.sampleInputButtonIcon)
                    }
                    .buttonStyle(.borderedProminent)
                    Button {
                        state.chooseCTInput()
                    } label: {
                        Label(state.inputSource == .sample ? "CTデータを選ぶ" : (state.inputURL?.lastPathComponent ?? "CTデータを選ぶ"), systemImage: "folder.badge.plus")
                    }
                    .buttonStyle(.bordered)
                }

                Text(state.inputDisplayName)
                    .font(.headline)
                if let candidate = state.selectedDicomSeries, state.dicomCleanCandidates.count > 1 {
                    HStack {
                        Text("使用する撮影: \(candidate.displayTitle)")
                            .font(.callout)
                        Button("変更") { state.showDicomSeriesSelection = true }
                            .buttonStyle(.bordered)
                    }
                }
                if state.inputSource == .sample {
                    Button {
                        state.openSampleViewer()
                    } label: {
                        Label("Sample 1の3Dプレビューを開く", systemImage: "cube.transparent")
                    }
                    .buttonStyle(.bordered)
                }

                VStack(alignment: .leading, spacing: 10) {
                    Text("作成する3Dプレビュー")
                        .font(.headline)
                    ForEach(CreationChoice.allCases) { choice in
                        Button {
                            state.requestCreationChoice(choice)
                        } label: {
                            HStack(alignment: .top, spacing: 12) {
                                Image(systemName: state.creationChoice == choice ? "checkmark.circle.fill" : "circle")
                                    .foregroundStyle(state.creationChoice == choice ? Color.accentColor : Color.secondary)
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(choice.rawValue)
                                        .font(.headline)
                                    Text(choice.detail)
                                        .font(.callout)
                                        .foregroundStyle(.secondary)
                                        .multilineTextAlignment(.leading)
                                }
                                Spacer(minLength: 0)
                            }
                            .padding(12)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.secondary.opacity(0.08))
                            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                        }
                        .buttonStyle(.plain)
                    }
                }

                Label("このMacのGPUを使用", systemImage: "cpu")
                    .foregroundStyle(.secondary)
                HStack {
                    Text("保存先")
                    TextField("フォルダ名", text: Binding(
                        get: { state.selectedOutputRootURL.lastPathComponent },
                        set: { _ in }
                    ))
                    .disabled(true)
                    Button("変更") { state.chooseOutputRoot() }
                        .buttonStyle(.bordered)
                }
                DisclosureGroup("追加機能") {
                    Toggle("境界を滑らかにする", isOn: $state.higherOrderResampling)
                        .disabled(state.creationChoice == .dentalSegmentatorExperimental)
                    Text("DentalSegmentatorでは使用できません。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .opacity(state.creationChoice == .dentalSegmentatorExperimental ? 1 : 0)
                }
                DisclosureGroup("保存先と詳細") {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("入力: \(state.inputURL?.path ?? "未選択")")
                        Text("保存先: \(state.selectedOutputRootURL.path)")
                        Text("使用機能: \(state.segmentationBackend.rawValue) / task=\(state.runMode.task) / device=mps")
                        Text("作成内容は研究・教育・検証用の非臨床プレビューです。")
                        if state.inputSource == .sample {
                            Text("同梱Sample 1はUI確認・動作確認用です。診断、治療方針の決定、治療計画、定量的な精度評価、または臨床利用には使用できません。")
                        }
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                }

                if !state.runPreflightBlockingReason.isEmpty {
                    Label(state.runPreflightBlockingReason, systemImage: "exclamationmark.triangle")
                        .font(.callout)
                        .foregroundStyle(.orange)
                }

                HStack {
                    Button {
                        state.startRun()
                    } label: {
                        Label(primaryTitle, systemImage: "play.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!canStart)
                    Spacer()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, 12)
        }
        .sheet(isPresented: $state.showDentalPreparationConfirmation) {
            DentalPreparationConfirmationSheet()
                .environmentObject(state)
        }
        .sheet(isPresented: $state.showDentalPreparationSheet) {
            DentalPreparationSheet()
                .environmentObject(state)
        }
    }

    private var canStart: Bool {
        if state.inputSource == .sample { return state.canStartSampleRun }
        return state.canStartOwnDataRun
    }

    private var primaryTitle: String {
        state.inputSource == .sample ? "Sampleで3Dプレビューを作る" : "このCTで3Dプレビューを作る"
    }
}

struct DentalPreparationConfirmationSheet: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("DentalSegmentator（実験的）")
                .font(.title2.weight(.semibold))
            Text("歯列と顎骨を5つの領域に分けます。")
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 8) {
                Label("追加モデルデータを取得します", systemImage: "arrow.down.circle")
                Label("このMacのアプリ専用フォルダへ保存します", systemImage: "internaldrive")
                Label("推論はこのMacのGPUを使用します", systemImage: "cpu")
                Label("CPUやTotalSegmentatorへ切り替えません", systemImage: "arrow.triangle.branch")
            }
            .font(.callout)
            HStack {
                Button("準備を始める") { state.confirmDentalPreparation() }
                    .buttonStyle(.borderedProminent)
                Button("キャンセル") {
                    state.showDentalPreparationConfirmation = false
                    state.creationChoice = .standardArchJaw
                }
                .buttonStyle(.bordered)
                Spacer()
            }
        }
        .padding(24)
        .frame(width: 480)
    }
}

struct DentalPreparationSheet: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("DentalSegmentatorの準備")
                .font(.title2.weight(.semibold))
            Text(state.dentalPreparationMessage)
                .foregroundStyle(.secondary)
            if state.dentalPreparationRunning {
                ProgressView()
                    .progressViewStyle(.linear)
                Text(state.dentalPreparationElapsed)
                    .foregroundStyle(.secondary)
                Button("キャンセル") { state.cancelDentalPreparation() }
                    .buttonStyle(.bordered)
            } else {
                Button("閉じる") { state.showDentalPreparationSheet = false }
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
        .frame(width: 420)
    }
}

struct DicomSeriesSelectionSheet: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("使用する撮影を変更")
                .font(.title2.weight(.semibold))
            Text("最初の候補を選択しています。別の撮影を使う場合だけ変更してください。")
                .foregroundStyle(.secondary)
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(state.dicomCleanCandidates) { candidate in
                        Button {
                            state.selectDicomSeries(candidate)
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(candidate.displayTitle)
                                    Text(candidate.displayDetail)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: state.selectedDicomSeriesID == candidate.id ? "checkmark.circle.fill" : "circle")
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .buttonStyle(.bordered)
                    }
                }
            }
            HStack {
                Button("この撮影を使う") { state.useSelectedDicomSeries() }
                    .buttonStyle(.borderedProminent)
                    .disabled(!state.canUseSelectedDicomSeries)
                Button("閉じる") { state.showDicomSeriesSelection = false }
                    .buttonStyle(.bordered)
                Spacer()
            }
        }
        .padding(24)
        .frame(width: 560, height: 420)
    }
}

struct RunProgressView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text(state.statusText)
                        .font(.title2.weight(.semibold))
                    Text(state.progressText)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text(state.runElapsed)
                    .foregroundStyle(.secondary)
            }
            ProgressView()
                .progressViewStyle(.linear)
            if !state.runHeartbeatText.isEmpty {
                Text(state.runHeartbeatText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("使用機能: \(state.activeRunFeatureName)")
                    .font(.headline)
                Text("保存先: \(state.outputURL?.lastPathComponent ?? "準備中")")
                    .foregroundStyle(.secondary)
            }
            if state.stopRequested {
                Label("停止要求済み。終了処理中です。", systemImage: "hourglass")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            HStack {
                Button {
                    state.stopRun()
                } label: {
                    Label("停止", systemImage: "stop.fill")
                }
                .buttonStyle(.bordered)
                .disabled(!state.isRunning || state.stopRequested)
                Spacer()
            }
        }
    }
}

struct CTPreviewView: View {
    @EnvironmentObject var state: AppState

    private let planeOrder = [
        ("axial", "軸位"),
        ("coronal", "冠状"),
        ("sagittal", "矢状"),
    ]

    var body: some View {
        let slicesByPlane = Dictionary(uniqueKeysWithValues: state.ctPreviewSlices.map { ($0.plane, $0) })
        let candidateWarning = state.pendingViewerExportCandidate?.sparseSliceWarningText ?? ""
        let warning = state.ctPreviewWarning.isEmpty ? candidateWarning : state.ctPreviewWarning
        VStack(alignment: .leading, spacing: 16) {
            Text("軸位・冠状・矢状のすべてに、確認したい範囲が写っていることを確認してください。")
                .foregroundStyle(.secondary)
            if let candidate = state.pendingViewerExportCandidate {
                Text("\(candidate.displayTitle) / \(candidate.displayDetail)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if !warning.isEmpty {
                Label(warning, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
            }

            HStack(alignment: .top, spacing: 12) {
                ForEach(planeOrder, id: \.0) { plane, label in
                    SlicePreviewCard(label: label, slice: slicesByPlane[plane])
                }
            }

            HStack {
                Button {
                    state.acceptPreparedCTPreview()
                } label: {
                    Label("このCTを使う", systemImage: "checkmark.circle")
                }
                .buttonStyle(.borderedProminent)
                .disabled(!state.canAcceptCTPreview)

                Button {
                    state.returnToViewerExportSelection()
                } label: {
                    Label("断面群を選び直す", systemImage: "square.stack.3d.up")
                }
                .buttonStyle(.bordered)

                Button {
                    state.goToInput()
                } label: {
                    Label("別のCTを選ぶ", systemImage: "arrow.left")
                }
                .buttonStyle(.bordered)

                Spacer()
            }
        }
    }
}

struct SlicePreviewCard: View {
    let label: String
    let slice: CTPreviewSlice?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(label)
                .font(.headline)
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.secondary.opacity(0.08))
                if let slice, let image = loadPGMImage(slice.url) {
                    Image(nsImage: image)
                        .resizable()
                        .interpolation(.high)
                        .scaledToFit()
                        .padding(6)
                } else {
                    Text("slice previewを作成できませんでした")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .padding()
                }
            }
            .frame(minHeight: 180)
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(Color.secondary.opacity(0.2), lineWidth: 1)
            )
            if let slice {
                Text(slice.detailText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if slice.uniformOrEmpty {
                    Text("画像がほぼ空に見えます")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct ResultView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if state.resultOutcome == .failure {
                    Text("3Dプレビューを作成できませんでした")
                        .font(.title2.weight(.semibold))
                    Text("処理を停止しました。入力データは変更されていません。")
                        .foregroundStyle(.secondary)
                    if state.activeRunBackend == .dentalSegmentator {
                        Text("CPUや別の機能には切り替えていません。")
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Button {
                            state.copySafeErrorInfo()
                        } label: {
                            Label("エラー情報をコピー", systemImage: "doc.on.doc")
                        }
                        .buttonStyle(.borderedProminent)
                        Button {
                            state.showDetailedLog()
                        } label: {
                            Label("詳細ログを見る", systemImage: "terminal")
                        }
                        .buttonStyle(.bordered)
                        Spacer()
                    }
                    navigationActions
                } else if state.resultOutcome == .success {
                    Text("3Dプレビューを作成しました")
                        .font(.title2.weight(.semibold))
                    if let candidate = state.selectedDicomSeries {
                        Text("使用した撮影: \(candidate.displayTitle)")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Button {
                            state.openResultPreview()
                        } label: {
                            Label("3Dプレビューを開く", systemImage: "cube.transparent")
                        }
                        .buttonStyle(.borderedProminent)
                        Button {
                            state.exportForSlicer()
                        } label: {
                            Label("3D Slicer用に書き出す", systemImage: "square.and.arrow.up")
                        }
                        .buttonStyle(.bordered)
                        Spacer()
                    }
                    HStack {
                        Button {
                            state.openOutputFolder()
                        } label: {
                            Label("結果フォルダを開く", systemImage: "folder")
                        }
                        Button {
                            state.showDetailedLog()
                        } label: {
                            Label("詳細ログを見る", systemImage: "terminal")
                        }
                        Spacer()
                    }
                    if !state.resultLocationItems.isEmpty {
                        DisclosureGroup("保存されたファイル") {
                            VStack(alignment: .leading, spacing: 8) {
                                ForEach(state.resultLocationItems) { item in
                                    RunLocationRow(item: item)
                                }
                            }
                        }
                    }
                    if state.canRegenerateSurfacePreview {
                        DisclosureGroup("3Dプレビューに問題がある場合") {
                            Button {
                                state.regenerateSurfacePreview()
                            } label: {
                                Label("3Dプレビューを再生成", systemImage: "arrow.triangle.2.circlepath")
                            }
                            .buttonStyle(.bordered)
                        }
                    }
                    navigationActions
                } else {
                    Text(state.resultMessage.isEmpty ? state.statusText : state.resultMessage)
                        .font(.title3.weight(.semibold))
                    if !state.dicomViewerExportCandidates.isEmpty {
                        Text("確認する断面群")
                            .font(.headline)
                        ForEach(state.dicomViewerExportCandidates) { candidate in
                            Button {
                                state.selectedViewerExportCandidateID = candidate.id
                            } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(candidate.displayTitle)
                                        Text(candidate.displayDetail)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Image(systemName: state.selectedViewerExportCandidateID == candidate.id ? "checkmark.circle.fill" : "circle")
                                }
                            }
                            .buttonStyle(.bordered)
                        }
                        Button("この断面群を確認する") { state.useSelectedViewerExportCandidate() }
                            .buttonStyle(.borderedProminent)
                            .disabled(!state.canUseSelectedViewerExportCandidate)
                    }
                    navigationActions
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, 12)
        }
    }

    @ViewBuilder
    private var navigationActions: some View {
        HStack {
            Button {
                state.goToInput()
            } label: {
                Label("入力と作成内容へ戻る", systemImage: "arrow.left")
            }
            .buttonStyle(.bordered)
            Button {
                state.chooseCTInput()
            } label: {
                Label("別のCTを選ぶ", systemImage: "folder.badge.plus")
            }
            .buttonStyle(.bordered)
            if state.resultOutcome == .success {
                Button {
                    state.retryRunFromResult()
                } label: {
                    Label("もう一度作成", systemImage: "arrow.clockwise")
                }
                .buttonStyle(.bordered)
                .disabled(!state.canRetryFromResult)
            }
            if state.dicomCleanCandidates.count > 1 {
                Button {
                    state.showDicomSeriesSelection = true
                } label: {
                    Label("別の撮影で作り直す", systemImage: "square.stack.3d.up")
                }
                .buttonStyle(.bordered)
            }
            Button {
                state.goToStart()
            } label: {
                Label("最初に戻る", systemImage: "house")
            }
            .buttonStyle(.bordered)
            Spacer()
        }
    }
}

struct RunReadinessRow: View {
    let item: RunReadinessItem

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: item.systemImage)
                .foregroundStyle(rowColor)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(item.title)
                        .font(.caption.weight(.semibold))
                        .frame(width: 74, alignment: .leading)
                    Text(item.value)
                        .font(.callout.weight(.medium))
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .textSelection(.enabled)
                    Spacer(minLength: 0)
                }
                if !item.detail.isEmpty {
                    Text(item.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .truncationMode(.middle)
                        .textSelection(.enabled)
                }
            }
        }
    }

    private var rowColor: Color {
        switch item.state {
        case "ok": return .green
        case "blocked": return .orange
        default: return .secondary
        }
    }
}

struct RunLocationRow: View {
    let item: RunLocationItem

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: item.exists ? item.systemImage : "exclamationmark.triangle")
                .foregroundStyle(item.exists ? Color.secondary : Color.orange)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(item.title)
                        .font(.caption.weight(.semibold))
                        .frame(width: 124, alignment: .leading)
                    Text(item.exists ? "作成済み" : "未作成")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(statusColor)
                    Spacer(minLength: 0)
                }
                Text(item.path)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
                if !item.detail.isEmpty {
                    Text(item.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var statusColor: Color {
        item.exists ? .secondary : .orange
    }
}

struct LogSheetView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("詳細ログ")
                    .font(.title2.weight(.semibold))
                Spacer()
                Button("閉じる") { state.showLog = false }
            }
            VStack(alignment: .leading, spacing: 8) {
                if state.resultOutcome == .failure {
                    Button {
                        state.copySafeErrorInfo()
                    } label: {
                        Label("エラー情報をコピー", systemImage: "doc.on.doc")
                    }
                    .buttonStyle(.borderedProminent)
                }
                HStack {
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(state.logText, forType: .string)
                    } label: {
                        Label("ログをコピー", systemImage: "doc.on.doc")
                    }
                    Button {
                        state.openCurrentLogFile()
                    } label: {
                        Label("ログファイルを開く", systemImage: "doc.text")
                    }
                    .disabled(!state.currentLogExists)

                    Button {
                        state.openCurrentLogFolder()
                    } label: {
                        Label("Finderで表示", systemImage: "folder")
                    }
                    .disabled(!state.currentLogExists)

                    Spacer()
                }
                Text(state.logInfoText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(state.currentLogPathText)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
                ScrollView {
                    Text(state.logText.isEmpty ? "まだログはありません。" : state.logText)
                        .font(.system(.caption, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                .frame(minHeight: 120, maxHeight: 220)
            }
        }
        .padding(20)
        .frame(width: 720, height: 420)
    }
}

struct StatusPill: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.callout.weight(.medium))
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .background(Color.secondary.opacity(0.13))
            .clipShape(Capsule())
    }
}

struct InfoCard<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.secondary.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

struct ChoiceCard: View {
    let title: String
    let subtitle: String
    let icon: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 12) {
                Image(systemName: icon)
                    .font(.largeTitle)
                Text(title)
                    .font(.title2.weight(.semibold))
                Text(subtitle)
                    .font(.body)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
            }
            .padding(18)
            .frame(maxWidth: .infinity, minHeight: 220, alignment: .leading)
            .background(Color.secondary.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

struct ChoiceCardContent<Controls: View>: View {
    let title: String
    let subtitle: String
    let icon: String
    let primaryAction: () -> Void
    let controls: Controls

    init(
        title: String,
        subtitle: String,
        icon: String,
        primaryAction: @escaping () -> Void,
        @ViewBuilder controls: () -> Controls
    ) {
        self.title = title
        self.subtitle = subtitle
        self.icon = icon
        self.primaryAction = primaryAction
        self.controls = controls()
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            Button(action: primaryAction) {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(Color.secondary.opacity(0.08))
            }
            .buttonStyle(.plain)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(RoundedRectangle(cornerRadius: 8, style: .continuous))

            VStack(alignment: .leading, spacing: 12) {
                VStack(alignment: .leading, spacing: 12) {
                    Image(systemName: icon)
                        .font(.largeTitle)
                    Text(title)
                        .font(.title2.weight(.semibold))
                    Text(subtitle)
                        .font(.body)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .allowsHitTesting(false)
                controls
                Spacer()
            }
            .padding(18)
            .frame(maxWidth: .infinity, minHeight: 220, alignment: .leading)
        }
        .frame(maxWidth: .infinity, minHeight: 220, alignment: .leading)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .contentShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

struct ActionRow<Controls: View>: View {
    let number: String
    let title: String
    let detail: String

    let controls: Controls

    init(number: String, title: String, detail: String, @ViewBuilder controls: () -> Controls) {
        self.number = number
        self.title = title
        self.detail = detail
        self.controls = controls()
    }

    var body: some View {
        InfoCard {
            HStack(alignment: .center, spacing: 14) {
                Text(number)
                    .font(.headline)
                    .frame(width: 30, height: 30)
                    .background(Color.accentColor)
                    .foregroundStyle(Color.white)
                    .clipShape(Circle())
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.headline)
                    Text(detail)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                controls
            }
        }
    }
}

func loadPGMImage(_ url: URL) -> NSImage? {
    guard let data = try? Data(contentsOf: url) else {
        return nil
    }
    let bytes = [UInt8](data)
    var index = 0

    func skipWhitespaceAndComments() {
        while index < bytes.count {
            if bytes[index] == 35 {
                while index < bytes.count && bytes[index] != 10 {
                    index += 1
                }
            } else if bytes[index] == 9 || bytes[index] == 10 || bytes[index] == 13 || bytes[index] == 32 {
                index += 1
            } else {
                break
            }
        }
    }

    func readToken() -> String? {
        skipWhitespaceAndComments()
        let start = index
        while index < bytes.count,
              bytes[index] != 9,
              bytes[index] != 10,
              bytes[index] != 13,
              bytes[index] != 32 {
            index += 1
        }
        guard index > start else { return nil }
        return String(bytes: bytes[start..<index], encoding: .ascii)
    }

    guard readToken() == "P5",
          let widthText = readToken(), let width = Int(widthText), width > 0,
          let heightText = readToken(), let height = Int(heightText), height > 0,
          let maxText = readToken(), let maxValue = Int(maxText), maxValue > 0, maxValue <= 255
    else {
        return nil
    }
    if index < bytes.count,
       bytes[index] == 9 || bytes[index] == 10 || bytes[index] == 13 || bytes[index] == 32 {
        index += 1
    }
    let pixelCount = width * height
    guard index + pixelCount <= bytes.count else {
        return nil
    }
    let pixelData = Data(bytes[index..<(index + pixelCount)])
    guard let provider = CGDataProvider(data: pixelData as CFData) else {
        return nil
    }
    let colorSpace = CGColorSpaceCreateDeviceGray()
    guard let cgImage = CGImage(
        width: width,
        height: height,
        bitsPerComponent: 8,
        bitsPerPixel: 8,
        bytesPerRow: width,
        space: colorSpace,
        bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
        provider: provider,
        decode: nil,
        shouldInterpolate: true,
        intent: .defaultIntent
    ) else {
        return nil
    }
    return NSImage(cgImage: cgImage, size: NSSize(width: width, height: height))
}
