import SwiftUI

struct ContentView: View {
    @State private var tab: ShowcaseTab = .runtime
    @State private var controller = RunController()
    @State private var snapshotStore = SnapshotStore()

    enum ShowcaseTab: Hashable {
        case runtime
        case snapshots
    }

    var body: some View {
        // Top tab bar (Pie pattern: TabView + selection + tags) so the
        // demo switches between the live runtime and saved moments.
        TabView(selection: $tab) {
            LiveView(controller: controller, onSnapshotSaved: { parent in
                snapshotStore.rootURL = parent
                tab = .snapshots
            })
            .tabItem { Label("Runtime", systemImage: "cpu") }
            .tag(ShowcaseTab.runtime)
            SnapshotBrowserView(store: snapshotStore)
                .tabItem { Label("Snapshots", systemImage: "clock.arrow.circlepath") }
                .tag(ShowcaseTab.snapshots)
        }
        .navigationTitle("NVX Showcase")
    }
}
