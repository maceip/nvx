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
                    Label("Runtime", systemImage: "cpu")
                        .tag(SidebarSection.live)
                    Label("Snapshots", systemImage: "clock.arrow.circlepath")
                        .tag(SidebarSection.snapshots)
                }
                Section("Payload") {
                    ForEach(Payload.all) { payload in
                        HStack {
                            Button(payload.name) {
                                controller.payload = payload
                            }
                            .buttonStyle(.plain)
                            Spacer()
                            if controller.payload == payload {
                                Image(systemName: "checkmark")
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .help(payload.blurb)
                    }
                }
                .disabled(controller.phase == .live ||
                    controller.phase == .launching)
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
