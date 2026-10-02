import AppKit
import SwiftUI

struct ContentView: View {
    @State var workspace = ShowcaseWorkspace()
    @State private var controls: TitlebarControlMetrics?

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                CompactTabStrip(
                    items: workspace.tabs.map { tab in
                        CompactTabItem(id: tab.id, title: tab.title, address: tab.title,
                            searchPrompt: "Rename tab", symbol: tab.kind.symbol, iconImage: tab.icon,
                            isBusy: tab.isBusy, isPinned: tab.isPinned,
                            reloadTitle: tab.kind == .snapshots ? "Refresh Snapshots" : nil,
                            addressLabel: "Rename tab")
                    },
                    selection: workspace.selectedID,
                    onSelect: workspace.select, onClose: workspace.close,
                    onInsert: { workspace.insert(.runtime) },
                    onMove: workspace.move, onDetach: workspace.detach,
                    onSearch: workspace.renameSelected,
                    onReload: { workspace.selected.snapshots.rescan() },
                    onSetPinned: workspace.setPinned,
                    previewSourceView: workspace.selected.pageView,
                    transferOwner: workspace,
                    onTransfer: { id, target, index in
                        guard let destination = target.transferOwner as? ShowcaseWorkspace else { return false }
                        return workspace.transfer(id, to: destination, at: index)
                    })
                    .frame(height: 36)
                    .layoutPriority(1)
                Menu {
                    Button("New Runtime Tab", systemImage: "cpu") { workspace.insert(.runtime) }
                    Button("New Snapshots Tab", systemImage: "clock.arrow.circlepath") { workspace.insert(.snapshots) }
                    Divider()
                    ForEach(workspace.tabs) { tab in
                        Button { workspace.select(tab.id) } label: {
                            Label(tab.title, systemImage: tab.id == workspace.selectedID ? "checkmark" : tab.kind.symbol)
                        }
                    }
                } label: { Image(systemName: "rectangle.stack") }
                .menuStyle(.borderlessButton)
                .fixedSize()
                .help("Open tabs and new tab options")
                .accessibilityLabel("Tab list")
            }
            .padding(.leading, controls?.leadingInset ?? 90)
            .padding(.trailing, controls?.trailingInset ?? 14)
            .padding(.vertical, max(0, ((controls?.rowHeight ?? 52) - 36) / 2))
            .background(Color(nsColor: .windowBackgroundColor))
            .background(TitlebarControlLayout { controls = $0 })
            Divider()
            ShowcasePageHost(tab: workspace.selected)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .ignoresSafeArea(.container, edges: .top)
        .frame(minWidth: 1100, minHeight: 700)
        .background(ShowcaseWindowProbe(workspace: workspace))
        .focusedSceneValue(\.showcaseWorkspace, workspace)
    }
}
