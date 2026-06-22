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
                LogDrawer()
            }
            .padding(22)
            .frame(minWidth: 760, minHeight: 620)
        }
    }

    @ViewBuilder
    private var detailView: some View {
        switch state.screen {
        case .setup:
            SetupView()
        case .start:
            StartChoiceView()
        case .sample:
            SampleTutorialView()
        case .ownData:
            OwnDataView()
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
        ("1", "目的", "Sampleか自分のデータを選びます。"),
        ("2", "入力", "CT入力を確認します。"),
        ("3", "実行", "このMacで3Dプレビューを作成します。"),
        ("4", "結果", "3Dプレビューと要約を確認します。"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("TotalSegmentator Wrapper for Mac")
                .font(.title2.weight(.semibold))
            Text("TotalSegmentatorを利用する非公式Mac wrapperです。非臨床プレビュー用で、DICOM/CT/結果は送信しません。")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Divider()
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                HStack(alignment: .top, spacing: 10) {
                    Text(step.0)
                        .font(.headline)
                        .frame(width: 28, height: 28)
                        .background(index == state.selectedStep ? Color.accentColor : Color.secondary.opacity(0.15))
                        .foregroundStyle(index == state.selectedStep ? Color.white : Color.primary)
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
        case .setup: return "セットアップ"
        case .start: return "まず選んでください"
        case .sample: return "Sampleで流れを体験"
        case .ownData: return "自分のCTを開く"
        case .running: return "処理中"
        case .ctPreview: return "CT確認プレビュー"
        case .result: return "結果"
        }
    }

    private var subtitle: String {
        switch state.screen {
        case .setup: return "管理者権限不要。このMac内のアプリ専用フォルダだけを使います。"
        case .start: return "Sampleで完成形を触るか、自分のCTを確認します。"
        case .sample: return "同梱Sample 1で、入力から3Dプレビューまでの流れを確認できます。"
        case .ownData: return "CTファイルまたは撮影フォルダを選びます。フォルダは先に安全確認します。"
        case .running: return "止まっていないことが分かるよう、進捗と経過時間を表示します。"
        case .ctPreview: return "プレビュー作成へ進む前に中央sliceを確認します。"
        case .result: return "結果フォルダ、3Dプレビュー、要約を確認できます。"
        }
    }
}

struct SetupView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            InfoCard {
                VStack(alignment: .leading, spacing: 8) {
                    Label("管理者権限は不要です", systemImage: "lock.open")
                    Label("このMac内のアプリ専用フォルダだけを書き込みます", systemImage: "folder")
                    Label("DICOM/CT/作成結果は送信しません", systemImage: "icloud.slash")
                    Label("利用状況データの送信も止めます", systemImage: "hand.raised")
                    Label("セットアップ開始を押すまで通信しません", systemImage: "network.slash")
                }
                .font(.callout)
            }

            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text(state.setupStep.label)
                        .font(.title3.weight(.semibold))
                    Text(state.setupHint)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text(state.setupElapsed)
                    .foregroundStyle(.secondary)
            }
            ProgressView()
                .progressViewStyle(.linear)
                .opacity(state.setupRunning ? 1 : 0.25)

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
                    Label("セットアップ開始", systemImage: "play.fill")
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
                title: "Sampleで流れを体験する",
                subtitle: "まず完成形の3Dを触って、入力から結果確認までの流れを安全に試します。",
                icon: "sparkles",
                primaryAction: {
                    state.goToSample()
                }
            ) {
                VStack(alignment: .leading, spacing: 10) {
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
                title: "自分のCTを開く",
                subtitle: "CTファイルまたは撮影フォルダを選びます。必要な確認と取り込み準備はアプリ内で行います。",
                icon: "folder.badge.person.crop",
                primaryAction: {
                    state.goToOwnData()
                }
            ) {
                EmptyView()
            }
        }
    }
}

struct SampleTutorialView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            sampleInputButton

            InfoCard {
                VStack(alignment: .leading, spacing: 8) {
                    Label("本番ではここで自分のCTを選びます。Sampleでは同梱CTを使って同じ流れを練習できます。", systemImage: "checklist")
                    Label("完成イメージは目的画面のSampleボタンから確認できます", systemImage: "cube.transparent")
                }
            }

            RunSettingsView()
            Label("同梱Sample 1はUI確認用です。診断・治療計画・精度評価には使いません。", systemImage: "info.circle")
                .font(.callout)
                .foregroundStyle(.secondary)
            Label("Sample 1の3Dプレビュー作成は、モデル取得済みの場合このMacでおおむね100秒前後かかります。初回はモデル取得で追加の通信と時間がかかる場合があります。", systemImage: "clock")
                .font(.callout)
                .foregroundStyle(.secondary)
            HStack {
                Button {
                    state.startRun()
                } label: {
                    Label("3Dプレビューを作成", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                .disabled(!state.canStartSampleRun)
                Button("保存先を選ぶ") { state.chooseOutputRoot() }
                Spacer()
                Button("自分のCTを開く") { state.goToOwnData() }
                    .buttonStyle(.bordered)
                Button("最初に戻る") { state.goToStart() }
                    .buttonStyle(.bordered)
            }
        }
    }

    @ViewBuilder
    private var sampleInputButton: some View {
        if state.isSampleInputSelected {
            Button {
                state.useSampleInput()
            } label: {
                Label(state.sampleInputButtonTitle, systemImage: state.sampleInputButtonIcon)
            }
            .buttonStyle(.bordered)
        } else {
            Button {
                state.useSampleInput()
            } label: {
                Label(state.sampleInputButtonTitle, systemImage: state.sampleInputButtonIcon)
            }
            .buttonStyle(.borderedProminent)
        }
    }
}

struct OwnDataView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Button {
                state.chooseCTInput()
            } label: {
                Label("CTを選ぶ", systemImage: "folder.badge.plus")
            }
            .buttonStyle(.borderedProminent)

            InfoCard {
                VStack(alignment: .leading, spacing: 8) {
                    Label("撮影フォルダは、プレビュー作成の前にアプリが確認します", systemImage: "checklist")
                    Label("通常のCTとして取り込める場合は、自動で準備します", systemImage: "arrow.right.doc.on.clipboard")
                    Label("追加確認が必要な形式の場合、理由を表示してここで止めます", systemImage: "exclamationmark.triangle")
                    Label("このアプリは非臨床プレビュー用です。診断、治療計画、精度評価には使わないでください。", systemImage: "info.circle")
                }
            }

            RunSettingsView()
            HStack {
                Button {
                    state.startRun()
                } label: {
                    Label(state.ownDataPrimaryButtonTitle, systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                .disabled(!state.canStartOwnDataRun)
                Button("保存先を選ぶ") { state.chooseOutputRoot() }
                Spacer()
                Button("Sampleで流れを体験する") { state.goToSample() }
                    .buttonStyle(.bordered)
                Button("最初に戻る") { state.goToStart() }
                    .buttonStyle(.bordered)
            }
        }
    }
}

struct RunSettingsView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        InfoCard {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("入力")
                        .font(.headline)
                        .frame(width: 80, alignment: .leading)
                    Text(state.inputURL?.lastPathComponent ?? "未選択")
                        .foregroundStyle(state.inputURL == nil ? .secondary : .primary)
                    Spacer()
                }
                HStack {
                    Text("保存先")
                        .font(.headline)
                        .frame(width: 80, alignment: .leading)
                    Text(state.outputRootURL?.lastPathComponent ?? "アプリ専用フォルダ / runs")
                        .foregroundStyle(.secondary)
                    Spacer()
                }
                Picker("実行内容", selection: $state.runMode) {
                    ForEach(RunMode.allCases) { mode in
                        Text(mode.rawValue).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                Text(state.runMode.description)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                DisclosureGroup("詳細設定（通常は変更不要）") {
                    HStack {
                        Text("処理方法")
                        Picker("", selection: $state.device) {
                            Text("推奨 (mps)").tag("mps")
                            Text("低速 (cpu)").tag("cpu")
                            Text("自動 (auto)").tag("auto")
                        }
                        .labelsHidden()
                        .frame(width: 160)
                        Spacer()
                    }
                    Text("サポート情報: 処理実行ファイルはアプリ専用環境内にあります。詳細ログには実行内容が記録されます。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
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
            if let fraction = state.runProgressFraction {
                ProgressView(value: fraction)
                    .progressViewStyle(.linear)
            } else {
                ProgressView()
                    .progressViewStyle(.linear)
            }
            if !state.runHeartbeatText.isEmpty {
                Text(state.runHeartbeatText)
                    .font(.caption)
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
        VStack(alignment: .leading, spacing: 16) {
            Text("CT確認プレビュー")
                .font(.title2.weight(.semibold))
            InfoCard {
                VStack(alignment: .leading, spacing: 8) {
                    Text("CTを見るソフトから「表示用の断面画像」として書き出されたデータの可能性があります。")
                        .font(.headline)
                    Text("プレビュー作成へ進む前に、中央sliceに歯列や確認したい範囲が写っているかを見てください。これは非診断preview専用です。")
                        .foregroundStyle(.secondary)
                    if let candidate = state.pendingViewerExportCandidate {
                        Text("\(candidate.displayTitle) / \(candidate.displayDetail)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if !candidate.sparseSliceWarningText.isEmpty {
                            Label(candidate.sparseSliceWarningText, systemImage: "exclamationmark.triangle")
                                .font(.caption)
                                .foregroundStyle(.orange)
                        }
                    }
                }
            }

            if !state.ctPreviewWarning.isEmpty {
                Label(state.ctPreviewWarning, systemImage: "exclamationmark.triangle")
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
                    Label("このCTで3Dプレビューを作成", systemImage: "checkmark.circle")
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
                    Label("CTを選び直す", systemImage: "arrow.left")
                }
                .buttonStyle(.bordered)

                Button {
                    state.goToStart()
                } label: {
                    Label("最初に戻る", systemImage: "house")
                }
                .buttonStyle(.bordered)

                Spacer()

                Button {
                    state.showDetailedLog()
                } label: {
                    Label("詳細ログを表示", systemImage: "terminal")
                }
                .buttonStyle(.bordered)
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
        VStack(alignment: .leading, spacing: 14) {
            Text(state.resultMessage.isEmpty ? state.statusText : state.resultMessage)
                .font(.title3.weight(.semibold))
            HStack {
                if !state.dicomCleanCandidates.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("使用する撮影を選んでください")
                            .font(.headline)
                        ForEach(state.dicomCleanCandidates) { candidate in
                            Button {
                                state.selectedDicomSeriesID = candidate.id
                            } label: {
                                HStack {
                                    VStack(alignment: .leading) {
                                        Text(candidate.displayTitle)
                                        Text(candidate.displayDetail)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    if state.selectedDicomSeriesID == candidate.id {
                                        Image(systemName: "checkmark.circle.fill")
                                            .foregroundStyle(.tint)
                                    }
                                }
                            }
                            .buttonStyle(.bordered)
                        }
                        Button {
                            state.useSelectedDicomSeries()
                        } label: {
                            Label("この撮影を使う", systemImage: "checkmark.circle")
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(!state.canUseSelectedDicomSeries)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            if !state.dicomViewerExportCandidates.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    Text("救済できる可能性のある断面群")
                        .font(.headline)
                    Text("CTを見るソフトから表示用に書き出された断面画像の可能性があります。結果は非診断preview専用です。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
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
                                if state.selectedViewerExportCandidateID == candidate.id {
                                    Image(systemName: "checkmark.circle.fill")
                                        .foregroundStyle(.tint)
                                }
                            }
                        }
                        .buttonStyle(.bordered)
                    }
                    Button {
                        state.useSelectedViewerExportCandidate()
                    } label: {
                        Label("この断面群を確認する", systemImage: "square.stack.3d.up")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!state.canUseSelectedViewerExportCandidate)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack {
                Button {
                    state.goToInput()
                } label: {
                    Label("CTを選び直す", systemImage: "arrow.left")
                }
                .buttonStyle(.borderedProminent)
                .disabled(state.isRunning)

                Button {
                    state.retryRunFromResult()
                } label: {
                    Label(state.retryButtonTitle, systemImage: "arrow.clockwise")
                }
                .buttonStyle(.bordered)
                .disabled(!state.canRetryFromResult)

                Button {
                    state.goToStart()
                } label: {
                    Label("最初に戻る", systemImage: "house")
                }
                .buttonStyle(.bordered)
                .disabled(state.isRunning)

                Spacer()
            }
            HStack {
                Button {
                    state.openOutputFolder()
                } label: {
                    Label("結果フォルダを開く", systemImage: "folder")
                }
                .disabled(state.outputURL == nil)

                Button {
                    state.openResultPreview()
                } label: {
                    Label("3Dプレビューを開く（ブラウザ）", systemImage: "cube")
                }
                .disabled(state.outputURL.flatMap(caseSurfacePreview) == nil)

                if state.canRegenerateSurfacePreview {
                    Button {
                        state.regenerateSurfacePreview()
                    } label: {
                        Label("3Dプレビューを再生成", systemImage: "arrow.triangle.2.circlepath")
                    }
                    .disabled(state.isRunning)
                }

                Button {
                    state.showDetailedLog()
                } label: {
                    Label("詳細ログを表示", systemImage: "terminal")
                }
                Spacer()
            }
            if !state.summaryText.isEmpty {
                Text("結果の要約")
                    .font(.headline)
                ScrollView {
                    Text(state.summaryText)
                        .font(.system(.body, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                .frame(minHeight: 160)
            }
            if !state.dicomSummaryText.isEmpty {
                ScrollView {
                    Text(state.dicomSummaryText)
                        .font(.system(.body, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                .frame(minHeight: 160)
            }
        }
    }
}

struct LogDrawer: View {
    @EnvironmentObject var state: AppState
    private var logExpanded: Binding<Bool> {
        Binding(
            get: { state.showLog },
            set: { expanded in
                if expanded {
                    state.showDetailedLog()
                } else {
                    state.showLog = false
                }
            }
        )
    }

    var body: some View {
        DisclosureGroup(isExpanded: logExpanded) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Button {
                        state.openCurrentLogFile()
                    } label: {
                        Label("ログファイルを開く", systemImage: "doc.text")
                    }
                    .disabled(!state.currentLogExists)

                    Button {
                        state.openCurrentLogFolder()
                    } label: {
                        Label("ログフォルダを開く", systemImage: "folder")
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
        } label: {
            Label(state.showLog ? "詳細ログを隠す" : "詳細ログを表示", systemImage: "terminal")
        }
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
