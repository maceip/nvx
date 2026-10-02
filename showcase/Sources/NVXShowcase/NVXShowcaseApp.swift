import SwiftUI

@main
struct NVXShowcaseApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .frame(minWidth: 1100, minHeight: 700)
        }
        .windowStyle(.hiddenTitleBar)
        .commands {
            ShowcaseTabCommands()
        }
    }
}
