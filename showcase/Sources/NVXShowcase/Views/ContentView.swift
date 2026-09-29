import SwiftUI

struct ContentView: View {
    @State private var selection: SidebarSection? = .live
    @State private var controller = RunController()

    enum SidebarSection: Hashable {
        case live
        case snapshots
    }

    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                Section("Showcase") {
                    Label("Live detonation", systemImage: "flame")
                        .tag(SidebarSection.live)
                    Label("Snapshots", systemImage: "clock.arrow.circlepath")
                        .tag(SidebarSection.snapshots)
                }
            }
            .navigationSplitViewColumnWidth(min: 200, ideal: 220)
        } detail: {
            switch selection {
            case .live, nil:
                LiveView(controller: controller)
            case .snapshots:
                SnapshotBrowserView()
            }
        }
        .navigationTitle("NVX Showcase")
    }
}
