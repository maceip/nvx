import AppKit
import Testing
@testable import NVXShowcase

@Suite(.serialized) @MainActor
struct WorkspaceTests {
    @Test func initialPagesAndIndependentRuntimes() {
        let workspace = ShowcaseWorkspace()
        defer { workspace.stopAll() }
        #expect(workspace.tabs.map(\.kind) == [.runtime, .snapshots])
        let original = workspace.selected
        original.controller.consoleLines = ["original run"]
        let added = workspace.insert()
        #expect(added.controller !== original.controller)
        #expect(added.controller.consoleLines.isEmpty)
        workspace.select(original.id)
        #expect(workspace.selected.controller.consoleLines == ["original run"])
    }
    @Test func transfersKeepControllersHostingViewAndContent() {
        _ = NSApplication.shared
        let source = ShowcaseWorkspace(), destination = ShowcaseWorkspace()
        defer { source.stopAll(); destination.stopAll() }
        let tab = source.selected, host = source.selected.pageView
        tab.controller.consoleLines = ["state before transfer"]
        #expect(source.transfer(tab.id, to: destination, at: 1))
        #expect(destination.selected === tab)
        #expect(destination.selected.pageView === host)
        #expect(destination.selected.controller.consoleLines == ["state before transfer"])
        #expect(tab.owner === destination)
        #expect(source.tabs.count == 1)
        #expect(source.selected.kind == .snapshots)
    }
    @Test func singleTabCannotTransferOrDetach() {
        let tab = ShowcasePageTab(.runtime)
        let source = ShowcaseWorkspace(tabs: [tab]), destination = ShowcaseWorkspace()
        defer { source.stopAll(); destination.stopAll() }
        #expect(!source.transfer(tab.id, to: destination, at: 0))
        source.detach(tab.id, at: .zero)
        #expect(source.tabs.count == 1)
        #expect(tab.owner === source)
    }
    @Test func snapshotUIStateTravelsWithTheTab() {
        let source = ShowcaseWorkspace(), destination = ShowcaseWorkspace()
        defer { source.stopAll(); destination.stopAll() }
        let tab = source.tabs[1]
        tab.snapshots.showsSidebar = false
        tab.snapshots.verifyOutput = "Existing manifest result"
        #expect(source.transfer(tab.id, to: destination, at: 0))
        #expect(destination.selected.snapshots === tab.snapshots)
        #expect(!destination.selected.snapshots.showsSidebar)
        #expect(destination.selected.snapshots.verifyOutput == "Existing manifest result")
    }
    @Test func pinsConstrainReorderAndCrossWindowInsertion() {
        let source = ShowcaseWorkspace(), destination = ShowcaseWorkspace()
        defer { source.stopAll(); destination.stopAll() }
        let pin = source.tabs[1], ordinary = source.tabs[0]
        source.setPinned(pin.id, true)
        source.move(ordinary.id, to: 0)
        #expect(source.tabs.first === pin)
        #expect(source.transfer(pin.id, to: destination, at: 99))
        #expect(destination.tabs.first === pin)
        destination.setPinned(pin.id, false)
        #expect(destination.selected === pin)
        #expect(destination.pinnedCount == 0)
    }
    @Test func closeOneTabPreservesOthersAndPinnedCommandClose() {
        let workspace = ShowcaseWorkspace()
        defer { workspace.stopAll() }
        let a = workspace.selected, b = workspace.insert()
        a.controller.consoleLines = ["keep this"]
        b.controller.phase = .live
        workspace.close(b.id)
        #expect(b.owner == nil)
        #expect(b.controller.phase == .done)
        #expect(a.controller.consoleLines == ["keep this"])
        workspace.select(a.id); workspace.setPinned(a.id, true); workspace.closeSelected()
        #expect(workspace.tabs.contains { $0 === a })
    }
    @Test func closingLastTabCreatesUsableReplacement() {
        let workspace = ShowcaseWorkspace(tabs: [ShowcasePageTab(.snapshots)])
        defer { workspace.stopAll() }
        let previous = workspace.selectedID
        workspace.close(previous)
        #expect(workspace.tabs.count == 1)
        #expect(workspace.selectedID != previous)
        #expect(workspace.selected.kind == .runtime)
    }
    @Test func manyTabStressPreservesAllIdentities() {
        let workspace = ShowcaseWorkspace()
        defer { workspace.stopAll() }
        for index in 0..<40 {
            let tab = workspace.insert(index.isMultiple(of: 2) ? .runtime : .snapshots)
            tab.customTitle = "Page \(index)"
            if index.isMultiple(of: 5) { workspace.setPinned(tab.id, true) }
        }
        let ids = Set(workspace.tabs.map(\.id))
        for tab in workspace.tabs.reversed() { workspace.move(tab.id, to: 0); workspace.select(tab.id) }
        #expect(Set(workspace.tabs.map(\.id)) == ids)
        #expect(workspace.tabs.prefix(workspace.pinnedCount).allSatisfy { $0.isPinned })
        for tab in workspace.tabs where !tab.isPinned { workspace.close(tab.id) }
        #expect(workspace.tabs.allSatisfy { $0.isPinned })
    }
    @Test func nativeGeometryHasEarlySnapAndReversalBand() {
        let frames = [CGRect(x: 0, y: 0, width: 280, height: 36), CGRect(x: 280, y: 0, width: 240, height: 36)]
        #expect(TabStripGeometry.destination(center: 285, slot: 0, frames: frames) == 1)
        #expect(TabStripGeometry.destination(center: 270, slot: 1, frames: frames) == 1)
        #expect(TabStripGeometry.destination(center: 250, slot: 1, frames: frames) == 0)
    }
    @Test func compressedRuntimeTitleFitsWithoutInvisibleRefreshControl() {
        _ = NSApplication.shared
        let item = CompactTabItem(id: UUID(), title: "Runtime", address: "Runtime", reloadTitle: nil)
        let owner = CompactTabStripView()
        owner.items = [item, CompactTabItem(id: UUID(), title: "Snapshots", address: "Snapshots")]
        let cell = CompactTabCell(owner: owner, item: item)
        cell.frame = NSRect(x: 0, y: 0, width: 120, height: 36)
        cell.configure(item: item, selected: true); cell.layout()
        let required = ("Runtime" as NSString).size(withAttributes: [.font: cell.address.font!]).width
        #expect(cell.address.frame.width >= required)
    }
    @Test func simultaneousRunsUseSeparateLogsAndStoppingOneLeavesOtherAlive() async throws {
        let fm = FileManager.default
        let root = fm.temporaryDirectory.appending(path: "nvx-tab-process-test-\(UUID())")
        let scripts = root.appending(path: "scripts"), runs = root.appending(path: "runs")
        try fm.createDirectory(at: scripts, withIntermediateDirectories: true)
        try fm.createDirectory(at: runs, withIntermediateDirectories: true)
        // Exercise actual Process ownership without booting a VM or invoking a
        // payload. No application preferences or real run directories are used.
        try "import os, time\nprint(os.getpid(), flush=True)\ntime.sleep(60)\n".write(
            to: scripts.appending(path: "nvx.py"), atomically: true, encoding: .utf8)
        let first = RunController(runsRoot: runs, resolveRepository: { root })
        let second = RunController(runsRoot: runs, resolveRepository: { root })
        defer { first.stop(); second.stop(); try? fm.removeItem(at: root) }
        first.launch(); second.launch()
        try await Task.sleep(for: .milliseconds(200))
        let dirs = try fm.contentsOfDirectory(at: runs, includingPropertiesForKeys: nil)
        #expect(dirs.count == 2)
        let pids = try dirs.compactMap { Int32(try String(contentsOf: $0.appending(path: "console.log"), encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)) }
        #expect(Set(pids).count == 2)
        #expect(first.phase == .live && second.phase == .live)
        first.stop()
        try await Task.sleep(for: .milliseconds(100))
        #expect(first.phase == .done)
        #expect(second.phase == .live)
        #expect(pids.filter { kill($0, 0) == 0 }.count == 1)
    }
    @Test func runtimeTabTitlesIdentifyPayloadAndPhase() {
        let tab = ShowcasePageTab(.runtime)
        defer { tab.stop() }
        #expect(tab.title == "Exfiltrator")
        tab.controller.phase = .launching
        #expect(tab.title == "Exfiltrator · Live")
        tab.controller.phase = .live
        #expect(tab.title == "Exfiltrator · Live")
        tab.controller.phase = .done
        tab.controller.verdict = .contained
        #expect(tab.title == "Exfiltrator · Contained")
        tab.customTitle = "Mine"
        #expect(tab.title == "Mine")
    }
    @Test func snapshotsTabTitleShowsRootLeaf() {
        let tab = ShowcasePageTab(.snapshots)
        defer { tab.stop() }
        #expect(tab.title == "Snapshots")
        tab.snapshots.rootURL = URL(fileURLWithPath: "/private/tmp/nvxsave-01")
        #expect(tab.title == "Snapshots · nvxsave-01")
    }
}
