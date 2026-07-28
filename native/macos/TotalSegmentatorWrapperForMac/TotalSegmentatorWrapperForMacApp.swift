import SwiftUI
import AppKit
import Darwin

@main
struct TotalSegmentatorWrapperForMacApp: App {
    @StateObject private var state: AppState

    init() {
        let paths = AppPaths.current()
        if ProcessInfo.processInfo.environment["TOTALSEGMENTATOR_WRAPPER_MAC_HEADLESS"] == "1" {
            let rc = SetupCoordinator.runSetup(paths: paths) { _, _ in }
            exit(rc)
        }
        let appState = AppState(paths: paths)
#if DEBUG
        appState.applyUIPreview()
#endif
        _state = StateObject(wrappedValue: appState)
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(state)
        }
        .defaultSize(width: 1280, height: 800)
        .commands {
            CommandGroup(replacing: .help) {
                Button("TotalSegmentator Wrapper for Macについて") {
                    NSApplication.shared.orderFrontStandardAboutPanel(
                        options: [
                            .applicationName: appTitle,
                            .applicationVersion: currentAppVersion(),
                            .credits: NSAttributedString(
                                string: """
                                無料のオープンソースソフトウェアです。アプリ本体はApache License 2.0で提供され、無保証です。

                                TotalSegmentatorを利用した非公式wrapperです。TotalSegmentator公式アプリではありません。研究・教育・検証用の非臨床プレビューで、診断や治療計画には使用できません。

                                第三者コード、別途取得するモデル、Sample 1と派生画像には各別条件が適用されます。LICENSE、NOTICE、第三者表示はアプリ内のContents/Resourcesにあります。

                                バグ報告: https://github.com/ainem-m/segmentation_w_mps/issues
                                DICOM/CT/処理結果は送信しません。
                                """
                            ),
                        ]
                    )
                }
                Divider()
                Button("Apache License 2.0を表示") {
                    openBundledDocument("LICENSE")
                }
                Button("NOTICE（適用範囲）を表示") {
                    openBundledDocument("NOTICE")
                }
                Button("第三者ライセンスを表示") {
                    openBundledDocument("licenses/THIRD_PARTY_LICENSES.txt")
                }
            }
        }
    }
}

private func openBundledDocument(_ relativePath: String) {
    let resourceURL = AppPaths.current().resources.appendingPathComponent(relativePath)
    guard FileManager.default.fileExists(atPath: resourceURL.path) else {
        let alert = NSAlert()
        alert.messageText = "ライセンス文書を開けません"
        alert.informativeText = "アプリをDMGからもう一度コピーしてからお試しください。"
        alert.alertStyle = .warning
        alert.runModal()
        return
    }
    NSWorkspace.shared.open(resourceURL)
}
