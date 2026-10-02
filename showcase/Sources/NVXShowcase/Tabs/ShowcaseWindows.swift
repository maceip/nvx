import AppKit
import SwiftUI

@MainActor
final class ShowcaseDetachedWindow: NSWindowController, NSWindowDelegate {
    private static var retained: [ShowcaseDetachedWindow] = []
    let workspace: ShowcaseWorkspace
    init(tab: ShowcasePageTab) {
        workspace = ShowcaseWorkspace(tabs: [tab])
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 700),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        super.init(window: window)
        window.title = "NVX Showcase"; window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true; window.tabbingMode = .disallowed
        window.minSize = NSSize(width: 1100, height: 700); window.isReleasedWhenClosed = false
        window.contentView = NSHostingView(rootView: ContentView(workspace: workspace)); window.delegate = self
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }
    static func open(tab: ShowcasePageTab, at point: NSPoint, size: NSSize?) {
        let controller = ShowcaseDetachedWindow(tab: tab); retained.append(controller)
        if let window = controller.window {
            let visible = (NSScreen.screens.first { $0.frame.contains(point) } ?? NSScreen.main)?.visibleFrame
                ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
            let requested = size ?? window.frame.size
            let fitted = NSSize(width: min(requested.width, visible.width), height: min(requested.height, visible.height))
            window.setFrame(NSRect(x: min(max(point.x - fitted.width / 2, visible.minX), visible.maxX - fitted.width),
                y: min(max(point.y + 34 - fitted.height, visible.minY), visible.maxY - fitted.height),
                width: fitted.width, height: fitted.height), display: false)
        }
        controller.showWindow(nil); controller.window?.makeKeyAndOrderFront(nil)
    }
    func windowWillClose(_ notification: Notification) {
        workspace.stopAll(); Self.retained.removeAll { $0 === self }
    }
}

struct ShowcaseFocusKey: FocusedValueKey { typealias Value = ShowcaseWorkspace }
extension FocusedValues {
    var showcaseWorkspace: ShowcaseWorkspace? {
        get { self[ShowcaseFocusKey.self] }
        set { self[ShowcaseFocusKey.self] = newValue }
    }
}

struct ShowcaseWindowProbe: NSViewRepresentable {
    let workspace: ShowcaseWorkspace
    func makeNSView(context: Context) -> Probe { Probe(workspace: workspace) }
    func updateNSView(_ view: Probe, context: Context) { view.configure() }
    final class Probe: NSView {
        let workspace: ShowcaseWorkspace
        private weak var observed: NSWindow?
        private var closeObserver: NSObjectProtocol?
        private var terminateObserver: NSObjectProtocol?
        init(workspace: ShowcaseWorkspace) { self.workspace = workspace; super.init(frame: .zero) }
        required init?(coder: NSCoder) { fatalError() }
        override func hitTest(_ point: NSPoint) -> NSView? { nil }
        override func viewDidMoveToWindow() { super.viewDidMoveToWindow(); configure() }
        func configure() {
            guard let window else { return }
            workspace.window = window; window.title = "NVX Showcase — \(workspace.selected.title)"
            guard observed !== window else { return }
            observed = window
            window.titleVisibility = .hidden; window.titlebarAppearsTransparent = true
            window.styleMask.insert(.fullSizeContentView); window.tabbingMode = .disallowed
            window.isMovableByWindowBackground = false
            if let closeObserver { NotificationCenter.default.removeObserver(closeObserver) }
            closeObserver = NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification,
                object: window, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated { self?.workspace.stopAll() }
                }
            if terminateObserver == nil {
                terminateObserver = NotificationCenter.default.addObserver(forName: NSApplication.willTerminateNotification,
                    object: nil, queue: .main) { [weak self] _ in
                        MainActor.assumeIsolated { self?.workspace.stopAll() }
                    }
            }
        }
        deinit {
            if let closeObserver { NotificationCenter.default.removeObserver(closeObserver) }
            if let terminateObserver { NotificationCenter.default.removeObserver(terminateObserver) }
        }
    }
}

struct ShowcaseTabCommands: Commands {
    @FocusedValue(\.showcaseWorkspace) private var workspace
    var body: some Commands {
        CommandGroup(after: .newItem) {
            Button("New Runtime Tab") { workspace?.insert(.runtime) }
                .keyboardShortcut("t", modifiers: .command).disabled(workspace == nil)
            Button("New Snapshots Tab") { workspace?.insert(.snapshots) }
                .keyboardShortcut("t", modifiers: [.command, .shift]).disabled(workspace == nil)
            Button("Close Tab") { workspace?.closeSelected() }
                .keyboardShortcut("w", modifiers: .command).disabled(workspace == nil || workspace?.selected.isPinned == true)
        }
        CommandMenu("Tabs") {
            Button("Next Tab") { workspace?.selectNext(1) }.keyboardShortcut("]", modifiers: [.command, .shift])
            Button("Previous Tab") { workspace?.selectNext(-1) }.keyboardShortcut("[", modifiers: [.command, .shift])
            Divider()
            Button(workspace?.selected.isPinned == true ? "Unpin Tab" : "Pin Tab") {
                guard let workspace else { return }
                workspace.setPinned(workspace.selectedID, !workspace.selected.isPinned)
            }
            Button("Move Tab to New Window") {
                guard let workspace else { return }
                workspace.detach(workspace.selectedID, at: NSEvent.mouseLocation)
            }.disabled((workspace?.tabs.count ?? 0) < 2)
        }
    }
}
