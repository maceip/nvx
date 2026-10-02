import AppKit
import SwiftUI
import Observation

enum ShowcasePageKind: String, CaseIterable {
    case runtime = "Runtime"
    case snapshots = "Snapshots"
    var symbol: String { self == .runtime ? "cpu" : "clock.arrow.circlepath" }
}

/// The controller AND hosting view travel together; moving a tab never starts
/// another runtime or recreates its local SwiftUI state.
@MainActor @Observable
final class ShowcasePageTab: Identifiable {
    let id = UUID()
    let kind: ShowcasePageKind
    let icon: NSImage?
    let controller = RunController()
    @ObservationIgnored lazy var snapshots = SnapshotStore()
    var isPinned = false
    var customTitle: String?
    @ObservationIgnored weak var owner: ShowcaseWorkspace?
    @ObservationIgnored private var retainedPage: NSHostingView<ShowcasePage>?
    var pageView: NSHostingView<ShowcasePage> {
        if let retainedPage { return retainedPage }
        let host = NSHostingView(rootView: ShowcasePage(tab: self))
        host.sizingOptions = []
        retainedPage = host
        return host
    }
    init(_ kind: ShowcasePageKind) {
        self.kind = kind
        icon = NSImage(systemSymbolName: kind.symbol, accessibilityDescription: kind.rawValue)
    }
    /// Per-VM identity: a runtime tab names its payload and live state so
    /// parallel runs stay distinguishable; a snapshots tab names its root.
    /// Reads controller/store state directly so the strip refreshes live.
    var title: String {
        if let customTitle { return customTitle }
        switch kind {
        case .runtime:
            let name = controller.payload.name
            switch controller.phase {
            case .live, .launching:
                return "\(name) · Live"
            case .done:
                return "\(name) · \(controller.verdict.rawValue.capitalized)"
            case .idle:
                return name
            }
        case .snapshots:
            if let leaf = snapshots.rootURL?.lastPathComponent {
                return "Snapshots · \(leaf)"
            }
            return kind.rawValue
        }
    }
    var isBusy: Bool {
        kind == .runtime ? controller.phase == .live || controller.phase == .launching
            : snapshots.resumeRunning || snapshots.verifyState == .running
    }
    func stop() {
        if controller.phase == .live || controller.phase == .launching { controller.stop() }
        if kind == .snapshots { snapshots.stopResume() }
        // Break the retained host -> SwiftUI root -> tab cycle only on close.
        retainedPage?.removeFromSuperview()
        retainedPage?.rootView = ShowcasePage(tab: nil)
        retainedPage = nil
        owner = nil
    }
}

@MainActor @Observable
final class ShowcaseWorkspace {
    private(set) var tabs: [ShowcasePageTab]
    private(set) var selectedID: UUID
    @ObservationIgnored weak var window: NSWindow?
    init(tabs: [ShowcasePageTab]? = nil) {
        let pages = tabs?.isEmpty == false ? tabs! : [ShowcasePageTab(.runtime), ShowcasePageTab(.snapshots)]
        self.tabs = pages.filter(\.isPinned) + pages.filter { !$0.isPinned }
        selectedID = pages[0].id
        self.tabs.forEach { $0.owner = self }
    }
    var selected: ShowcasePageTab { tabs.first { $0.id == selectedID } ?? tabs[0] }
    var pinnedCount: Int { tabs.filter(\.isPinned).count }
    func select(_ id: UUID) {
        guard tabs.contains(where: { $0.id == id }) else { return }
        selectedID = id
    }
    @discardableResult func insert(_ kind: ShowcasePageKind = .runtime) -> ShowcasePageTab {
        let tab = ShowcasePageTab(kind); tab.owner = self
        let index = tabs.firstIndex { $0.id == selectedID } ?? tabs.count - 1
        tabs.insert(tab, at: max(pinnedCount, index + 1)); selectedID = tab.id
        return tab
    }
    func close(_ id: UUID) {
        guard let index = tabs.firstIndex(where: { $0.id == id }) else { return }
        tabs.remove(at: index).stop()
        if tabs.isEmpty {
            let replacement = ShowcasePageTab(.runtime); replacement.owner = self; tabs = [replacement]
        }
        if selectedID == id { selectedID = tabs[min(index, tabs.count - 1)].id }
    }
    func closeSelected() { if !selected.isPinned { close(selectedID) } }
    func move(_ id: UUID, to index: Int) {
        guard let old = tabs.firstIndex(where: { $0.id == id }) else { return }
        let tab = tabs.remove(at: old)
        tabs.insert(tab, at: insertionIndex(index, pinned: tab.isPinned))
    }
    private func insertionIndex(_ index: Int, pinned: Bool) -> Int {
        pinned ? min(max(0, index), pinnedCount) : min(max(pinnedCount, index), tabs.count)
    }
    func setPinned(_ id: UUID, _ pinned: Bool) {
        guard let index = tabs.firstIndex(where: { $0.id == id }), tabs[index].isPinned != pinned else { return }
        let tab = tabs.remove(at: index); tab.isPinned = pinned
        tabs.insert(tab, at: pinnedCount)
    }
    func renameSelected(_ text: String) {
        let title = text.trimmingCharacters(in: .whitespacesAndNewlines)
        selected.customTitle = title.isEmpty ? nil : title
    }
    func selectNext(_ offset: Int) {
        let index = tabs.firstIndex { $0.id == selectedID } ?? 0
        selectedID = tabs[(index + offset + tabs.count) % tabs.count].id
    }
    func reviewSnapshots(at root: URL) {
        let tab = tabs.first { $0.kind == .snapshots } ?? insert(.snapshots)
        tab.snapshots.rootURL = root; select(tab.id)
    }
    @discardableResult func transfer(_ id: UUID, to destination: ShowcaseWorkspace, at index: Int) -> Bool {
        guard tabs.count > 1, destination !== self,
              let sourceIndex = tabs.firstIndex(where: { $0.id == id }) else { return false }
        let tab = tabs.remove(at: sourceIndex)
        if selectedID == id { selectedID = tabs[min(sourceIndex, tabs.count - 1)].id }
        tab.owner = destination
        destination.tabs.insert(tab, at: destination.insertionIndex(index, pinned: tab.isPinned))
        destination.select(id); destination.window?.makeKeyAndOrderFront(nil)
        return true
    }
    func detach(_ id: UUID, at point: NSPoint) {
        guard tabs.count > 1, let index = tabs.firstIndex(where: { $0.id == id }) else { return }
        let tab = tabs.remove(at: index)
        if selectedID == id { selectedID = tabs[min(index, tabs.count - 1)].id }
        ShowcaseDetachedWindow.open(tab: tab, at: point, size: window?.frame.size)
    }
    func stopAll() { tabs.forEach { $0.stop() } }
}

struct ShowcasePage: View {
    // Optional only to release a closed tab's root; live roots are never replaced.
    var tab: ShowcasePageTab?
    var body: some View {
        if let tab {
            switch tab.kind {
            case .runtime:
                LiveView(controller: tab.controller, onSnapshotSaved: { [weak tab] root in
                    tab?.owner?.reviewSnapshots(at: root)
                })
            case .snapshots: SnapshotBrowserView(store: tab.snapshots)
            }
        }
    }
}

struct ShowcasePageHost: NSViewRepresentable {
    let tab: ShowcasePageTab
    func makeNSView(context: Context) -> Container { Container() }
    func sizeThatFits(_ proposal: ProposedViewSize, nsView: Container, context: Context) -> CGSize? {
        CGSize(width: proposal.width ?? 1100, height: proposal.height ?? 644)
    }
    func updateNSView(_ view: Container, context: Context) {
        let host = tab.pageView
        if host.superview !== view {
            view.subviews.forEach { $0.removeFromSuperview() }; host.removeFromSuperview()
            host.frame = view.bounds; host.autoresizingMask = [.width, .height]; view.addSubview(host)
        }
    }
    final class Container: NSView {
        override func layout() { super.layout(); subviews.first?.frame = bounds }
    }
}
