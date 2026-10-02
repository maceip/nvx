import AppKit
import SwiftUI

struct TitlebarControlMetrics: Equatable {
    var leadingInset: CGFloat
    var rowHeight: CGFloat
    var trailingInset: CGFloat
}

/// Measures the native controls in the header's coordinate space. The system
/// sidebar button changes its width and placement when the sidebar collapses.
struct TitlebarControlLayout: NSViewRepresentable {
    var onChange: (TitlebarControlMetrics) -> Void
    func makeNSView(context: Context) -> Probe { Probe() }
    func updateNSView(_ view: Probe, context: Context) {
        view.onChange = onChange
        view.measure()
    }

    final class Probe: NSView {
        var onChange: (TitlebarControlMetrics) -> Void = { _ in }
        private var last: TitlebarControlMetrics?
        private var observers: [NSObjectProtocol] = []
        override func hitTest(_ point: NSPoint) -> NSView? { nil }
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            observers.forEach(NotificationCenter.default.removeObserver)
            observers.removeAll()
            last = nil
            if let window {
                for name in [NSWindow.didResizeNotification, NSWindow.didUpdateNotification] {
                    observers.append(NotificationCenter.default.addObserver(forName: name, object: window, queue: .main) { [weak self] _ in self?.measure() })
                }
            }
            measure()
            DispatchQueue.main.async { [weak self] in self?.measure() }
        }
        override func layout() { super.layout(); measure() }
        deinit { observers.forEach(NotificationCenter.default.removeObserver) }
        func measure() {
            guard let window, bounds.width > 0, let close = window.standardWindowButton(.closeButton) else { return }
            let windowControls = [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton].compactMap(window.standardWindowButton)
            let sidebarControls = window.toolbar?.items.filter {
                $0.itemIdentifier == .toggleSidebar || $0.itemIdentifier.rawValue.lowercased().contains("sidebar")
            }.compactMap(\.view) ?? []
            let leading = (windowControls + sidebarControls).reduce(CGFloat(14)) { inset, control in
                guard !control.isHiddenOrHasHiddenAncestor else { return inset }
                let rect = convert(control.bounds, from: control)
                guard rect.maxX > 0, rect.minX < bounds.maxX else { return inset }
                return max(inset, rect.maxX + 12)
            }
            let headerFrame = convert(bounds, to: nil)
            let headerTop = headerFrame.maxY
            let controlsCenter = close.convert(close.bounds, to: nil).midY
            let height = min(80, max(36, 2 * (headerTop - controlsCenter)))
            let contentRight = window.contentView.map { $0.convert($0.bounds, to: nil).maxX } ?? window.frame.width
            let trailing = max(14, headerFrame.maxX - contentRight + 14)
            let value = TitlebarControlMetrics(leadingInset: ceil(leading), rowHeight: height, trailingInset: ceil(trailing))
            guard value != last else { return }
            last = value
            // A representable can be measuring during a SwiftUI layout pass.
            DispatchQueue.main.async { [weak self] in
                guard let self, self.last == value else { return }
                self.onChange(value)
            }
        }
    }
}
