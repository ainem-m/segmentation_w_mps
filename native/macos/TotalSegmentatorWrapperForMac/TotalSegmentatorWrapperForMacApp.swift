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
        _state = StateObject(wrappedValue: AppState(paths: paths))
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(state)
        }
        .commands {
            CommandGroup(replacing: .help) {
                Button("TotalSegmentator Wrapper for Macについて") {
                    NSApplication.shared.orderFrontStandardAboutPanel(
                        options: [
                            .applicationName: appTitle,
                            .applicationVersion: currentAppVersion(),
                            .credits: NSAttributedString(
                                string: "TotalSegmentatorを利用した非公式wrapperです。TotalSegmentator公式アプリではありません。研究・教育目的の非臨床プレビューです。DICOM/CT/処理結果は送信しません。"
                            ),
                        ]
                    )
                }
            }
        }
    }
}

func currentAppVersion() -> String {
    let paths = AppPaths.current()
    let manifest = readJSON(paths.manifest) ?? [:]
    return (manifest["app_version"] as? String) ?? (manifest["version"] as? String) ?? "0.1.2"
}
