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
            .sheet(isPresented: $state.showDentalPreparationConfirmation) {
                DentalPreparationConfirmationSheet()
                    .environmentObject(state)
            }
            .sheet(isPresented: $state.showDentalPreparationSheet) {
                DentalPreparationSheet()
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
        case .dicomRescue:
            DicomRescueView()
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
            if state.isUIPreviewMode {
                Text("UI PREVIEW · \(state.uiPreviewScenario)")
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(Color.orange.opacity(0.16))
                    .foregroundStyle(.orange)
                    .clipShape(Capsule())
            }
            StatusPill(text: state.statusText)
        }
    }

    private var title: String {
        switch state.screen {
        case .setup: return "はじめの準備"
        case .start: return "最初はSampleで流れを確認"
        case .inputAndCreation: return "入力と作成内容"
        case .running: return "処理中"
        case .dicomRescue: return "形状を確認"
        case .ctPreview: return "CT画像を確認"
        case .result: return "結果"
        }
    }

    private var subtitle: String {
        switch state.screen {
        case .setup: return "管理者権限不要。このMac内のアプリ専用フォルダだけを使います。"
        case .start: return "入力から結果確認までを先に試せます。"
        case .inputAndCreation: return "入力を確認し、作成する3Dプレビューを選びます。"
        case .running: return "現在の処理と経過時間を表示します。"
        case .dicomRescue: return "三方向の断面を見ながら、形が自然に見えるよう調整します。"
        case .ctPreview: return "歯や顎が3枚とも見えていれば、このCTを使えます。"
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

            NextPhaseButton(
                title: "準備を始める",
                systemImage: "play.fill",
                isEnabled: !state.setupRunning
            ) {
                state.startSetup()
            }
        }
    }
}

struct StartChoiceView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            PhaseChoiceCard(
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
            PhaseChoiceCard(
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
    @State private var isCreationComparisonPresented = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("入力")
                        .font(.headline)
                    if state.inputSource == .none {
                        Text(state.inputDisplayName)
                            .font(.headline)
                        NextPhaseButton(
                            title: "CTデータを選ぶ",
                            systemImage: "folder.badge.plus"
                        ) {
                            state.chooseCTInput()
                        }
                    } else {
                        HStack {
                            Text(state.inputDisplayName)
                                .font(.headline)
                            Spacer()
                            Button {
                                state.chooseCTInput()
                            } label: {
                                Label(state.inputSource == .sample ? "手元のCTを選ぶ" : "別のCTを選ぶ", systemImage: "folder.badge.plus")
                            }
                            .buttonStyle(.bordered)
                        }
                    }
                }
                if let candidate = state.selectedDicomSeries, state.dicomCleanCandidates.count > 1 {
                    HStack {
                        Text("使用する撮影: \(candidate.displayTitle)")
                            .font(.callout)
                        Button("変更") { state.showDicomSeriesSelection = true }
                            .buttonStyle(.bordered)
                    }
                }
                VStack(alignment: .leading, spacing: 10) {
                    Text("作成する3Dプレビュー")
                        .font(.headline)
                    HStack(alignment: .top, spacing: 12) {
                        CreationCategoryCard(
                            title: "標準モデル",
                            modelName: "TotalSegmentator",
                            detail: "顎骨と歯列をまとめて作成します。最初におすすめの方法です。",
                            badge: "おすすめ",
                            systemImage: "cube.transparent",
                            isSelected: state.creationChoice == .standardArchJaw
                        ) {
                            state.requestCreationChoice(.standardArchJaw)
                        }
                        CreationCategoryCard(
                            title: "その他のモデル",
                            modelName: alternateModelName,
                            detail: alternateModelDetail,
                            badge: nil,
                            systemImage: "square.grid.2x2",
                            isSelected: state.creationChoice != .standardArchJaw
                        ) {
                            isCreationComparisonPresented = true
                        }
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
                VStack(alignment: .leading, spacing: 8) {
                    Text("仕上がり")
                        .font(.headline)
                    Toggle("境界を滑らかにする", isOn: $state.higherOrderResampling)
                        .disabled(state.segmentationBackend != .totalSegmentator)
                    if state.segmentationBackend != .totalSegmentator {
                        Text("このモデルでは仕上がりが固定されています。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                DisclosureGroup("入力・保存情報") {
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

                NextPhaseButton(
                    title: primaryTitle,
                    systemImage: "play.fill",
                    isEnabled: canStart
                ) {
                    state.startRun()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, 12)
        }
        .sheet(isPresented: $isCreationComparisonPresented) {
            CreationMethodComparisonSheet(selectedChoice: state.creationChoice) { choice in
                isCreationComparisonPresented = false
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                    state.requestCreationChoice(choice)
                }
            }
        }
        .onAppear {
            isCreationComparisonPresented = state.uiPreviewScenario == "input-comparison"
        }
    }

    private var canStart: Bool {
        if state.inputSource == .sample { return state.canStartSampleRun }
        return state.canStartOwnDataRun
    }

    private var primaryTitle: String {
        state.inputSource == .sample ? "Sampleで3Dプレビューを作る" : "このCTで3Dプレビューを作る"
    }

    private var alternateModelName: String {
        switch state.creationChoice {
        case .dentalSegmentatorExperimental, .individualTeethBeta:
            return "選択中：\(state.creationChoice.rawValue)"
        default:
            return "比較して選ぶ"
        }
    }

    private var alternateModelDetail: String {
        switch state.creationChoice {
        case .dentalSegmentatorExperimental, .individualTeethBeta:
            return "現在はこの方法を使用します。クリックすると結果画像と処理時間を比較できます。"
        default:
            return "結果画像と処理時間を見ながら、用途に合う方法を選べます。"
        }
    }
}

struct CreationCategoryCard: View {
    let title: String
    let modelName: String
    let detail: String
    let badge: String?
    let systemImage: String
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 9) {
                HStack(alignment: .center, spacing: 8) {
                    Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(isSelected ? Color.accentColor : Color.secondary)
                    Text(title)
                        .font(.headline)
                    Spacer(minLength: 4)
                    if let badge {
                        Text(badge)
                            .font(.caption.weight(.semibold))
                            .padding(.horizontal, 7)
                            .padding(.vertical, 3)
                            .background(Color.accentColor.opacity(0.14))
                            .foregroundStyle(Color.accentColor)
                            .clipShape(Capsule())
                    }
                }
                Label(modelName, systemImage: systemImage)
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(.primary)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .padding(13)
            .frame(maxWidth: .infinity, minHeight: 132, alignment: .topLeading)
            .background(Color.secondary.opacity(0.07))
            .overlay {
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(isSelected ? Color.accentColor : Color.secondary.opacity(0.22), lineWidth: isSelected ? 2 : 1)
            }
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(title)、\(modelName)")
        .accessibilityValue(isSelected ? "選択中" : "未選択")
    }
}

struct CreationMethodComparisonSheet: View {
    let selectedChoice: CreationChoice
    let onSelect: (CreationChoice) -> Void
    @Environment(\.dismiss) private var dismiss

    private let columns = [
        GridItem(.flexible(), spacing: 16),
        GridItem(.flexible(), spacing: 16),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("作成方法を比較")
                        .font(.title2.weight(.semibold))
                    Text("同じCT・同じ角度・同じ倍率の結果です。通常はTotalSegmentatorがおすすめです。")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("閉じる") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }

            ScrollView {
                LazyVGrid(columns: columns, alignment: .leading, spacing: 16) {
                    CreationMethodComparisonCard(
                        title: "通常（TotalSegmentator）",
                        badge: "おすすめ",
                        imageName: "totalseg",
                        estimatedDuration: "約3〜6分",
                        summary: "顎骨と歯列をまとめて作成します。比較的速く、最初に試す方法です。",
                        note: "完了後、歯が検出された場合はToothSegの高精細化を追加できます。",
                        isSelected: selectedChoice == .standardArchJaw,
                        actionTitle: "この方法を選ぶ"
                    ) {
                        onSelect(.standardArchJaw)
                    }

                    CreationMethodComparisonCard(
                        title: "DentalSegmentator",
                        badge: "実験的",
                        imageName: "dentalseg",
                        estimatedDuration: "約7〜12分",
                        summary: "上下の歯列・顎骨・下顎管を5領域に分ける追加モデルです。",
                        note: "初回のみ追加モデルの準備が必要です。通常経路より実行環境の検証が少ない機能です。",
                        isSelected: selectedChoice == .dentalSegmentatorExperimental,
                        actionTitle: "この方法を選ぶ"
                    ) {
                        onSelect(.dentalSegmentatorExperimental)
                    }

                    CreationMethodComparisonCard(
                        title: "個別歯ベータ",
                        badge: "ベータ",
                        imageName: "individual",
                        estimatedDuration: "約2〜7分",
                        summary: "TotalSegmentatorの旧個別歯経路で、歯を1本ずつ表示します。",
                        note: "検証用のベータ機能です。この比較画面から選択できます。",
                        isSelected: selectedChoice == .individualTeethBeta,
                        actionTitle: "この方法を選ぶ"
                    ) {
                        onSelect(.individualTeethBeta)
                    }

                    CreationMethodComparisonCard(
                        title: "高精細歯（ToothSeg）",
                        badge: "結果画面で追加",
                        imageName: "toothseg",
                        estimatedDuration: "追加で約15〜40分",
                        summary: "歯を個別に分け、FDI歯式番号を付ける高精細な追加推論です。",
                        note: "通常のTotalSegmentator完了後、歯が検出された結果画面から明示的に実行できます。ここでは選択しません。",
                        isSelected: false,
                        actionTitle: nil,
                        action: nil
                    )
                }
                Label(
                    "処理時間はM1 Mac・メモリ16 GBでの実測目安です。CTの範囲、解像度、歯の検出範囲により変わります。",
                    systemImage: "clock.badge.questionmark"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.top, 12)
                .padding(.bottom, 4)
            }
        }
        .padding(22)
        .frame(minWidth: 940, idealWidth: 980, minHeight: 680, idealHeight: 740)
    }
}

struct CreationMethodComparisonCard: View {
    let title: String
    let badge: String
    let imageName: String
    let estimatedDuration: String
    let summary: String
    let note: String
    let isSelected: Bool
    let actionTitle: String?
    let action: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(title)
                    .font(.headline)
                Spacer()
                Text(badge)
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background((isSelected ? Color.accentColor : Color.secondary).opacity(0.14))
                    .foregroundStyle(isSelected ? Color.accentColor : Color.secondary)
                    .clipShape(Capsule())
            }

            ModelComparisonImage(name: imageName, accessibilityLabel: "\(title)の3D分割結果")

            Label("処理時間の目安: \(estimatedDuration)", systemImage: "clock")
                .font(.callout.weight(.semibold))
                .foregroundStyle(.secondary)

            Text(summary)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
            Text(note)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if let actionTitle, let action {
                if isSelected {
                    Button("選択中") { action() }
                        .buttonStyle(.bordered)
                        .disabled(true)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                } else {
                    Button(actionTitle) { action() }
                        .buttonStyle(.borderedProminent)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, minHeight: 445, alignment: .topLeading)
        .background(Color.secondary.opacity(0.07))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(isSelected ? Color.accentColor : Color.secondary.opacity(0.2), lineWidth: isSelected ? 2 : 1)
        }
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct ModelComparisonImage: View {
    let name: String
    let accessibilityLabel: String

    private var image: NSImage? {
        NSImage(contentsOf: AppPaths.current().resources
            .appendingPathComponent("model_comparison", isDirectory: true)
            .appendingPathComponent("\(name).png"))
    }

    var body: some View {
        Group {
            if let image {
                Image(nsImage: image)
                    .resizable()
                    .scaledToFill()
            } else {
                ZStack {
                    Color.black.opacity(0.82)
                    Label("比較画像を読み込めません", systemImage: "photo")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .frame(maxWidth: .infinity)
        .aspectRatio(579.0 / 440.0, contentMode: .fit)
        .clipped()
        .background(Color.black.opacity(0.82))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .accessibilityLabel(accessibilityLabel)
    }
}

struct DentalPreparationConfirmationSheet: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(state.pendingModelPreparationChoice == .toothSegExperimental ? "ToothSeg（個別歯・実験的）" : "DentalSegmentator（実験的）")
                .font(.title2.weight(.semibold))
            Text(state.pendingModelPreparationChoice == .toothSegExperimental
                 ? "semantic/instance両branchの追加モデル（約920 MB）を取得します。通信環境により時間がかかります。"
                 : "追加モデルデータを取得するので少し時間がかかります。")
                .foregroundStyle(.secondary)
            NextPhaseButton(title: "準備を始める", systemImage: "arrow.down.circle") {
                state.confirmDentalPreparation()
            }
            HStack {
                Button("キャンセル") {
                    state.cancelModelPreparationConfirmation()
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
            Text(state.pendingModelPreparationChoice == .toothSegExperimental ? "ToothSegの準備" : "DentalSegmentatorの準備")
                .font(.title2.weight(.semibold))
            Text(state.dentalPreparationMessage)
                .foregroundStyle(.secondary)
            if state.dentalPreparationRunning {
                if let fraction = state.dentalPreparationFraction {
                    ProgressView(value: fraction)
                        .progressViewStyle(.linear)
                } else {
                    ProgressView()
                        .progressViewStyle(.linear)
                }
                if !state.dentalPreparationDetail.isEmpty {
                    Text(state.dentalPreparationDetail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
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
            NextPhaseButton(
                title: "この撮影を使う",
                systemImage: "checkmark.circle.fill",
                isEnabled: state.canUseSelectedDicomSeries
            ) {
                state.useSelectedDicomSeries()
            }
            HStack {
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
            if let stage = state.runStageEvent,
               let weighted = state.runWeightedProgress {
                structuredProgress(stage: stage, weighted: weighted)
            } else if let fraction = state.runProgressFraction {
                ProgressView(value: fraction)
                    .progressViewStyle(.linear)
                    .accessibilityLabel("処理の進捗")
                    .accessibilityValue("約\(Int((fraction * 100).rounded()))パーセント")
            } else {
                ProgressView()
                    .progressViewStyle(.linear)
            }
            if let heartbeatText = visibleRunHeartbeatText {
                Text(heartbeatText)
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

    @ViewBuilder
    private func structuredProgress(
        stage: RunStageEvent,
        weighted: RunWeightedProgress
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let estimate = weighted.estimate {
                Text("全体の目安：約\(estimatePercentText(estimate))%")
                    .font(.headline)
            }

            WeightedRunProgressBar(
                weights: state.activeRunStageWeights,
                currentIndex: stage.index,
                currentFraction: weighted.stageFraction
            )

            Text("工程 \(stage.index) / \(stage.total)　\(stage.label)")
                .font(.headline)

            if let progress = state.runStageProgress,
               progress.scope == "stage",
               progress.route == stage.route,
               progress.stageID == stage.stageID {
                if let step = progress.step, let total = progress.total, let percent = progress.percent {
                    Text("\(step) / \(total)（この工程 \(percent)%）")
                } else if let percent = progress.percent {
                    Text("この工程 \(percent)%")
                }
                if let eta = progress.etaSeconds, eta > 0 {
                    Text("この工程の残り目安：約\(formatCompactDuration(eta))")
                        .foregroundStyle(.secondary)
                }
            } else if let progress = state.runStageProgress, progress.scope == "subtask" {
                Text(subtaskProgressText(progress))
                    .foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("工程 \(stage.index) / \(stage.total)、\(stage.label)")
        .accessibilityValue(accessibilityProgressValue(weighted))
    }

    private func estimatePercentText(_ fraction: Double) -> Int {
        let rounded = Int((fraction * 100).rounded())
        return fraction < 1 ? min(99, rounded) : 100
    }

    private func subtaskProgressText(_ progress: RunLogProgress) -> String {
        var text = "内部処理"
        if let step = progress.step, let total = progress.total, let percent = progress.percent {
            text += " \(step) / \(total)（\(percent)%）"
        } else if let percent = progress.percent {
            text += " \(percent)%"
        } else if let rawLabel = progress.stage?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !rawLabel.isEmpty,
                  !["現在の処理", "現在の内部処理", "処理"].contains(rawLabel) {
            text += "：\(rawLabel)"
        }
        if let eta = progress.etaSeconds, eta > 0 {
            text += "・残り約\(formatCompactDuration(eta))"
        }
        return text
    }

    private func accessibilityProgressValue(_ weighted: RunWeightedProgress) -> String {
        if let estimate = weighted.estimate {
            return "全体の目安、約\(estimatePercentText(estimate))パーセント"
        }
        return "現在工程の進捗率は不定"
    }

    private var visibleRunHeartbeatText: String? {
        let text = state.runHeartbeatText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty,
              text != "進捗ログを受信しました。",
              !text.contains("処理を継続しています"),
              !text.contains("処理は継続中です") else {
            return nil
        }
        return text
    }
}

private struct WeightedRunProgressBar: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let weights: [Double]
    let currentIndex: Int
    let currentFraction: Double?
    @State private var pulse = false

    var body: some View {
        GeometryReader { geometry in
            HStack(spacing: 2) {
                ForEach(Array(weights.enumerated()), id: \.offset) { offset, weight in
                    let index = offset + 1
                    ZStack(alignment: .leading) {
                        RoundedRectangle(cornerRadius: 3)
                            .fill(Color.secondary.opacity(0.18))
                        if index < currentIndex {
                            RoundedRectangle(cornerRadius: 3)
                                .fill(Color.accentColor)
                        } else if index == currentIndex, let fraction = currentFraction {
                            GeometryReader { segment in
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(Color.accentColor)
                                    .frame(width: segment.size.width * max(0, min(1, fraction)))
                            }
                        } else if index == currentIndex {
                            RoundedRectangle(cornerRadius: 3)
                                .fill(Color.accentColor.opacity(pulse ? 0.65 : 0.25))
                        }
                    }
                    .frame(width: max(
                        2,
                        (geometry.size.width - CGFloat(max(0, weights.count - 1)) * 2) * CGFloat(weight)
                    ))
                }
            }
        }
        .frame(height: 10)
        .onAppear {
            guard !reduceMotion, currentFraction == nil else { return }
            withAnimation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true)) {
                pulse = true
            }
        }
        .onChange(of: currentIndex) { _ in
            pulse = false
            guard !reduceMotion, currentFraction == nil else { return }
            withAnimation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true)) {
                pulse = true
            }
        }
        .onChange(of: currentFraction) { fraction in
            pulse = false
            guard !reduceMotion, fraction == nil else { return }
            withAnimation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true)) {
                pulse = true
            }
        }
    }
}

struct DicomRescueView: View {
    @EnvironmentObject var state: AppState
    @State private var showsEstimateDetails = false
    @State private var showsOrientationControls = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 5) {
                        Label(
                            "三方向の形が自然に見えるよう、画像の端を動かしてください。",
                            systemImage: "info.circle"
                        )
                        .font(.headline)
                        Text("同じ色のハンドルは連動します。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text("推定の確かさ：\(state.rescueConfidenceDisplayText)")
                        .font(.callout.weight(.semibold))
                    Button {
                        showsEstimateDetails.toggle()
                    } label: {
                        Label("理由を見る", systemImage: "info.circle")
                    }
                    .buttonStyle(.bordered)
                    .popover(isPresented: $showsEstimateDetails, arrowEdge: .bottom) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("推定について")
                                .font(.headline)
                            Text("推定の確かさ: \(state.rescueConfidenceDisplayText)")
                                .font(.callout.weight(.semibold))
                            ForEach(state.rescueEvidence, id: \.self) { reason in
                                Label(reason, systemImage: "info.circle")
                                    .font(.caption)
                            }
                            Text("候補値は正確な寸法が確認できたことを意味しません。")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .padding()
                        .frame(width: 360)
                    }
                    if state.isRunning {
                        Button("画像更新を中止") {
                            state.cancelSecondaryCaptureRescuePreparation()
                        }
                        .buttonStyle(.bordered)
                    }
                }
                .padding(14)
                .background(Color.secondary.opacity(0.07))
                .clipShape(RoundedRectangle(cornerRadius: 10))

                if selectablePrimaryCandidates.count > 1 {
                    Picker(
                        "使用する系列",
                        selection: Binding(
                            get: { state.selectedDicomRescueCandidateID ?? "" },
                            set: { id in
                                if let candidate = selectablePrimaryCandidates.first(
                                    where: { $0.id == id }
                                ) {
                                    state.selectSecondaryCaptureRescueCandidate(candidate)
                                }
                            }
                        )
                    ) {
                        ForEach(selectablePrimaryCandidates) { candidate in
                            Text(
                                "\(candidate.displayPlane) · \(candidate.reconstructionGroup) · "
                                    + "\(candidate.fileCount)枚"
                            )
                            .tag(candidate.id)
                        }
                    }
                    .pickerStyle(.menu)
                    .frame(maxWidth: 420, alignment: .leading)
                }

                HStack(alignment: .top, spacing: 14) {
                    rescuePlaneCard(
                        label: "AXIAL",
                        key: "axial",
                        horizontalAxis: .x,
                        verticalAxis: .y
                    )
                    rescuePlaneCard(
                        label: "CORONAL",
                        key: "coronal",
                        horizontalAxis: .x,
                        verticalAxis: .z
                    )
                    rescuePlaneCard(
                        label: "SAGITTAL",
                        key: "sagittal",
                        horizontalAxis: .y,
                        verticalAxis: .z
                    )
                }

                if state.rescueMPRPreviewSlices.isEmpty || state.isRunning
                    || state.rescueImageUpdateFailed {
                    HStack(spacing: 8) {
                        if state.isRunning {
                            ProgressView()
                                .controlSize(.small)
                        }
                        Text(state.rescuePreviewStatus)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                if showsOrientationControls {
                    GroupBox("画像の向き") {
                        HStack {
                            Picker("軸の対応", selection: $state.rescueAxisPermutation) {
                                ForEach(RescueAxisPermutation.allCases) { permutation in
                                    Text(permutation.displayName).tag(permutation)
                                }
                            }
                            .frame(maxWidth: 260)
                            .onChange(of: state.rescueAxisPermutation) { _ in
                                state.rescueTransformDidChange()
                            }
                            Button {
                                state.rescueRotationQuarterTurns =
                                    (state.rescueRotationQuarterTurns + 1) % 4
                                state.rescueTransformDidChange()
                            } label: {
                                Label("90°回転", systemImage: "rotate.right")
                            }
                            .buttonStyle(.bordered)
                            Toggle(
                                "スライス順を反転",
                                isOn: $state.rescueSliceOrderReversed
                            )
                            .toggleStyle(.checkbox)
                            .onChange(of: state.rescueSliceOrderReversed) { _ in
                                state.rescueTransformDidChange()
                            }
                            Spacer()
                        }
                    }
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                }

                if !state.rescueInlineWarning.isEmpty {
                    Label(state.rescueInlineWarning, systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                }

                HStack {
                    HStack(spacing: 8) {
                        Button {
                            state.chooseAnotherCTFromRescue()
                        } label: {
                            Label("別のCTを選ぶ", systemImage: "folder.badge.plus")
                        }
                        .buttonStyle(.bordered)
                        .disabled(state.isRunning)

                        Button {
                            state.resetRescueGeometryToEstimate()
                        } label: {
                            Label("推定形状に戻す", systemImage: "arrow.counterclockwise")
                        }
                        .buttonStyle(.bordered)
                    }

                    Spacer()

                    Button {
                        withAnimation(.easeInOut(duration: 0.18)) {
                            showsOrientationControls.toggle()
                        }
                    } label: {
                        Label("画像の向きを修正", systemImage: "rotate.3d")
                    }
                    .buttonStyle(.bordered)

                    Spacer()

                    NextPhaseButton(
                        title: "この形状で作成",
                        systemImage: "checkmark.circle.fill",
                        isEnabled: state.canConfirmSecondaryCaptureRescue
                    ) {
                        state.confirmSecondaryCaptureRescue()
                    }
                    .frame(width: 280)
                }

                if let reason = state.rescueConfirmationUnavailableReason {
                    Label(reason, systemImage: state.isRunning ? "clock" : "info.circle")
                        .font(.caption)
                        .foregroundStyle(state.rescueImageUpdateFailed ? .orange : .secondary)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                }

                Text("この操作まではAI推論を開始しません。生成結果は参考用です。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
            .padding(.vertical, 4)
        }
    }

    private var selectablePrimaryCandidates: [SecondaryCaptureRescueCandidate] {
        state.dicomRescueCandidates.filter { $0.role == "primary" }
    }

    private func rescuePlaneCard(
        label: String,
        key: String,
        horizontalAxis: RescueCalibrationAxis,
        verticalAxis: RescueCalibrationAxis
    ) -> some View {
        RescueStretchPlaneCard(
            label: label,
            horizontalAxis: horizontalAxis,
            verticalAxis: verticalAxis,
            horizontalSpacing: spacingBinding(horizontalAxis),
            verticalSpacing: spacingBinding(verticalAxis),
            horizontalEstimate: estimatedSpacing(horizontalAxis),
            verticalEstimate: estimatedSpacing(verticalAxis),
            imageURL: state.rescueMPRPreviewSlices.first(
                where: { $0.plane.lowercased().contains(key) }
            )?.url,
            displayAspectRatio: rescueDisplayAspectRatio(key),
            revision: state.rescuePreviewRevision,
            isUpdating: state.isRunning,
            beginAdjustment: state.beginRescueStretchAdjustment,
            finishAdjustment: state.finishRescueStretchAdjustment
        )
    }

    private func spacingBinding(_ axis: RescueCalibrationAxis) -> Binding<Double> {
        Binding(
            get: {
                switch axis {
                case .x: return state.rescueSpacingX
                case .y: return state.rescueSpacingY
                case .z: return state.rescueSpacingZ
                }
            },
            set: { state.setRescueStretchSpacing(axis: axis, value: $0) }
        )
    }

    private func estimatedSpacing(_ axis: RescueCalibrationAxis) -> Double {
        switch axis {
        case .x: return state.rescueEstimatedSpacing.x
        case .y: return state.rescueEstimatedSpacing.y
        case .z: return state.rescueEstimatedSpacing.z
        }
    }

    private func rescueDisplayAspectRatio(_ plane: String) -> CGFloat {
        guard state.rescuePreviewShapeXYZ.count == 3 else {
            if let slice = state.rescueMPRPreviewSlices.first(
                where: { $0.plane.lowercased().contains(plane) }
            ) {
                return CGFloat(max(slice.width, 1)) / CGFloat(max(slice.height, 1))
            }
            return 1
        }
        let shape = state.rescuePreviewShapeXYZ.map { Double(max($0, 1)) }
        let width: Double
        let height: Double
        switch plane {
        case "coronal":
            width = shape[0] * state.rescueSpacingX
            height = shape[2] * state.rescueSpacingZ
        case "sagittal":
            width = shape[1] * state.rescueSpacingY
            height = shape[2] * state.rescueSpacingZ
        default:
            width = shape[0] * state.rescueSpacingX
            height = shape[1] * state.rescueSpacingY
        }
        return CGFloat(min(max(width / max(height, 0.000_001), 0.1), 10))
    }
}

private struct RescueStretchPlaneCard: View {
    let label: String
    let horizontalAxis: RescueCalibrationAxis
    let verticalAxis: RescueCalibrationAxis
    @Binding var horizontalSpacing: Double
    @Binding var verticalSpacing: Double
    let horizontalEstimate: Double
    let verticalEstimate: Double
    let imageURL: URL?
    let displayAspectRatio: CGFloat
    let revision: Int
    let isUpdating: Bool
    let beginAdjustment: () -> Void
    let finishAdjustment: (RescueCalibrationAxis) -> Void

    var body: some View {
        VStack(spacing: 10) {
            Text(label)
                .font(.headline)

            HStack(spacing: 8) {
                RescueMPRCanvas(
                    plane: label,
                    imageURL: imageURL,
                    displayAspectRatio: displayAspectRatio,
                    revision: revision,
                    isUpdating: isUpdating
                )
                RescueStretchSlider(
                    axis: verticalAxis,
                    orientation: .vertical,
                    spacing: $verticalSpacing,
                    estimatedSpacing: verticalEstimate,
                    beginAdjustment: beginAdjustment,
                    finishAdjustment: { finishAdjustment(verticalAxis) }
                )
            }
            RescueStretchSlider(
                axis: horizontalAxis,
                orientation: .horizontal,
                spacing: $horizontalSpacing,
                estimatedSpacing: horizontalEstimate,
                beginAdjustment: beginAdjustment,
                finishAdjustment: { finishAdjustment(horizontalAxis) }
            )
        }
        .padding(12)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay {
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.secondary.opacity(0.18), lineWidth: 1)
        }
        .frame(maxWidth: .infinity)
    }
}

private enum RescueStretchOrientation {
    case horizontal
    case vertical
}

private struct RescueStretchSlider: View {
    let axis: RescueCalibrationAxis
    let orientation: RescueStretchOrientation
    @Binding var spacing: Double
    let estimatedSpacing: Double
    let beginAdjustment: () -> Void
    let finishAdjustment: () -> Void

    private var axisColor: Color {
        switch axis {
        case .x: return .blue
        case .y: return .green
        case .z: return .orange
        }
    }

    private var logarithmicValue: Binding<Double> {
        Binding(
            get: {
                log2(max(spacing, 0.01) / max(estimatedSpacing, 0.01))
            },
            set: { position in
                spacing = min(max(estimatedSpacing * pow(2, position), 0.01), 20)
            }
        )
    }

    var body: some View {
        Group {
            if orientation == .horizontal {
                HStack(spacing: 6) {
                    Image(systemName: "arrow.left")
                    slider
                    Image(systemName: "arrow.right")
                }
                .frame(height: 28)
            } else {
                VStack(spacing: 5) {
                    Image(systemName: "arrow.up")
                    slider
                        .frame(width: 170)
                        .rotationEffect(.degrees(-90))
                        .frame(width: 28, height: 170)
                    Image(systemName: "arrow.down")
                }
                .frame(width: 34)
            }
        }
        .foregroundStyle(axisColor)
    }

    private var slider: some View {
        Slider(
            value: logarithmicValue,
            in: -2...2,
            onEditingChanged: { editing in
                if editing {
                    beginAdjustment()
                } else {
                    finishAdjustment()
                }
            }
        )
        .tint(axisColor)
        .accessibilityLabel(
            "\(axis.rawValue.uppercased())方向を"
                + (orientation == .horizontal ? "横に伸縮" : "縦に伸縮")
        )
        .accessibilityValue(
            "\(Int((spacing / max(estimatedSpacing, 0.01) * 100).rounded()))パーセント"
        )
        .accessibilityHint("同じ色のハンドルと連動します")
        .help("画像をこの方向へ伸縮します。同じ色のハンドルは連動します。")
    }
}

private struct RescueMPRCanvas: View {
    let plane: String
    let imageURL: URL?
    let displayAspectRatio: CGFloat
    let revision: Int
    let isUpdating: Bool

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 7)
                .fill(Color.black.opacity(0.92))
            if let imageURL, let image = loadPGMImage(imageURL) {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.high)
                    .aspectRatio(displayAspectRatio, contentMode: .fit)
            } else {
                VStack(spacing: 7) {
                    Image(systemName: "viewfinder")
                        .font(.largeTitle)
                    Text("画像を準備中")
                        .font(.caption)
                }
                .foregroundStyle(.white.opacity(0.62))
            }
            GeometryReader { proxy in
                Path { path in
                    path.move(to: CGPoint(x: proxy.size.width / 2, y: 0))
                    path.addLine(to: CGPoint(x: proxy.size.width / 2, y: proxy.size.height))
                    path.move(to: CGPoint(x: 0, y: proxy.size.height / 2))
                    path.addLine(to: CGPoint(x: proxy.size.width, y: proxy.size.height / 2))
                }
                .stroke(Color.white.opacity(0.45), lineWidth: 0.8)
            }
            if isUpdating, imageURL != nil {
                VStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                    Text("画像を更新中")
                        .font(.caption.weight(.semibold))
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 8))
            }
        }
        .aspectRatio(1, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: 7))
        .accessibilityLabel("\(plane) 三方向画像")
    }
}

private struct RescueArtifactImage: View {
    let title: String
    let url: URL

    var body: some View {
        VStack(spacing: 5) {
            if let image = NSImage(contentsOf: url) {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.high)
                    .scaledToFit()
                    .background(Color.black)
            } else {
                Rectangle()
                    .fill(Color.black.opacity(0.85))
                    .overlay(Image(systemName: "exclamationmark.triangle").foregroundStyle(.orange))
            }
            Text(title)
                .font(.caption.weight(.semibold))
        }
        .aspectRatio(1, contentMode: .fit)
    }
}

private struct RescueMPRPlaceholder: View {
    let plane: String
    let revision: Int

    var body: some View {
        VStack(spacing: 5) {
            ZStack {
                Rectangle()
                    .fill(Color.black.opacity(0.85))
                Image(systemName: "viewfinder")
                    .font(.largeTitle)
                    .foregroundStyle(.white.opacity(0.65))
            }
            .aspectRatio(1, contentMode: .fit)
            Text("\(plane) · preview \(revision)")
                .font(.caption.weight(.semibold))
        }
        .accessibilityLabel("\(plane) MPR preview")
    }
}

struct CTPreviewView: View {
    @EnvironmentObject var state: AppState

    private let planeOrder = [
        ("axial", "上から"),
        ("coronal", "正面から"),
        ("sagittal", "横から"),
    ]

    var body: some View {
        let slicesByPlane = Dictionary(uniqueKeysWithValues: state.ctPreviewSlices.map { ($0.plane, $0) })
        let candidateWarning = state.pendingViewerExportCandidate?.sparseSliceWarningText ?? ""
        let warning = state.ctPreviewWarning.isEmpty ? candidateWarning : state.ctPreviewWarning
        VStack(alignment: .leading, spacing: 12) {
            if let multipleSeriesNotice {
                HStack {
                    Label(multipleSeriesNotice, systemImage: "square.stack.3d.up")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button {
                        state.returnToViewerExportSelection()
                    } label: {
                        Label("同じフォルダのほかの撮影を見る", systemImage: "square.stack.3d.up")
                    }
                    .buttonStyle(.bordered)
                }
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

            NextPhaseButton(
                title: "表示中の撮影で3Dプレビュー作成へ進む",
                systemImage: "checkmark.circle.fill",
                isEnabled: state.canAcceptCTPreview
            ) {
                state.acceptPreparedCTPreview()
            }

            Divider()
            HStack {
                Text("入力を変える")
                    .font(.callout.weight(.semibold))
                Button {
                    state.chooseDicomFolderAndAudit()
                } label: {
                    Label("別のDICOMフォルダを選ぶ", systemImage: "folder.badge.plus")
                }
                .buttonStyle(.bordered)
                Spacer()
            }
        }
    }

    private var multipleSeriesNotice: String? {
        let seriesCount = Set(state.dicomViewerExportCandidates.map(\.seriesKey)).count
        guard seriesCount > 1 else { return nil }
        let current = state.pendingViewerExportCandidate?.seriesNumber.map { "表示中: 撮影 \($0)" }
            ?? "選択した撮影を表示中"
        return "複数の撮影データがあります。\(current)"
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
                    if state.activeRunBackend != .totalSegmentator {
                        Text("CPUや別の機能には切り替えていません。")
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Button {
                            state.copySafeErrorInfo()
                        } label: {
                            Label("エラー情報をコピー", systemImage: "doc.on.doc")
                        }
                        .buttonStyle(.bordered)
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
                    if state.canRetryToothSegRefine {
                        VStack(alignment: .leading, spacing: 10) {
                            Label("ToothSeg高精細化を完了できませんでした", systemImage: "exclamationmark.triangle.fill")
                                .font(.headline)
                                .foregroundStyle(.orange)
                            Text("元の歯列・顎骨結果は利用できます。")
                                .font(.callout.weight(.semibold))
                            if !state.failureReasonText.isEmpty {
                                Text(state.failureReasonText)
                                    .font(.callout)
                                    .foregroundStyle(.secondary)
                            }
                            HStack {
                                Button {
                                    state.requestToothSegRefine()
                                } label: {
                                    Label("ToothSegを再実行", systemImage: "arrow.clockwise")
                                }
                                .buttonStyle(.borderedProminent)
                                Button {
                                    state.copySafeErrorInfo()
                                } label: {
                                    Label("エラー情報をコピー", systemImage: "doc.on.doc")
                                }
                                .buttonStyle(.bordered)
                                Button {
                                    state.showDetailedLog()
                                } label: {
                                    Label("詳細ログを見る", systemImage: "terminal")
                                }
                                .buttonStyle(.bordered)
                            }
                        }
                        .padding(12)
                        .background(Color.orange.opacity(0.10))
                        .overlay(
                            RoundedRectangle(cornerRadius: 10)
                                .stroke(Color.orange.opacity(0.45), lineWidth: 1)
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                    if let candidate = state.selectedDicomSeries {
                        Text("使用した撮影: \(candidate.displayTitle)")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    if state.canSwitchResultFlavor {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("結果表示対象")
                                .font(.headline)
                            Picker("結果表示対象", selection: Binding(
                                get: { state.activeResultFlavor },
                                set: { state.setActiveResultFlavor($0) }
                            )) {
                                ForEach(state.availableResultFlavors) { flavor in
                                    Text(flavor.title).tag(flavor)
                                }
                            }
                            .pickerStyle(.segmented)
                        }
                        .padding(10)
                        .background(Color.secondary.opacity(0.08))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    NextPhaseButton(title: "3Dプレビューを開く", systemImage: "cube.transparent") {
                        state.openResultPreview()
                    }
                    if state.canShowToothSegRefine {
                        NextPhaseButton(
                            title: "高精細歯分割（ToothSeg）を実行",
                            systemImage: "sparkles",
                            isEnabled: state.canRunToothSegRefineAction
                        ) {
                            state.requestToothSegRefine()
                        }
                    } else if state.primaryRunBackend == .totalSegmentator
                                && state.resultOutcome == .success
                                && !state.primaryRunTeethDetected {
                        Text("歯の初回抽出結果がありませんでした。ToothSeg高精細化は表示しません。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Button {
                            state.exportForSlicer()
                        } label: {
                            Label("3D Slicer用に書き出す", systemImage: "square.and.arrow.up")
                        }
                        .buttonStyle(.bordered)
                        Button {
                            state.openOutputFolder()
                        } label: {
                                Label("結果フォルダを開く", systemImage: "folder")
                            }
                        .buttonStyle(.bordered)
                        Button {
                            state.showDetailedLog()
                        } label: {
                                Label("詳細ログを見る", systemImage: "terminal")
                        }
                        .buttonStyle(.bordered)
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
                        NextPhaseButton(
                            title: "選択した画像を確認して次へ",
                            systemImage: "square.stack.3d.up",
                            isEnabled: state.canUseSelectedViewerExportCandidate
                        ) {
                            state.useSelectedViewerExportCandidate()
                        }
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

struct NextPhaseButton: View {
    let title: String
    let systemImage: String
    var isEnabled = true
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack {
                Image(systemName: systemImage)
                Text(title)
                    .font(.headline)
                Spacer()
                Image(systemName: "arrow.right")
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background(isEnabled ? Color.accentColor : Color.secondary.opacity(0.18))
            .foregroundStyle(isEnabled ? Color.white : Color.secondary)
            .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
        }
        .buttonStyle(.plain)
        .disabled(!isEnabled)
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

struct PhaseChoiceCard<Controls: View>: View {
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
