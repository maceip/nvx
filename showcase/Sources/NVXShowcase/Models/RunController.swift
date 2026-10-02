import Foundation
import NVXCore

/// Owns one live runtime run: launches `nvx.py run`, tails its combined
/// output, and derives attempted/denied counts plus the verdict.
@Observable
@MainActor
final class RunController {
    enum Phase: String {
        case idle = "Idle"
        case launching = "Launching"
        case live = "Live"
        case done = "Done"
    }

    var phase: Phase = .idle
    var consoleLines: [String] = []
    var events: [GuestEvent] = []
    var attempted = 0
    var denied = 0
    var verdict: Verdict = .running
    var payload: Payload = .exfiltrator
    /// Snapshot directory captured by the last save run, if any.
    var savedSnapshotURL: URL?

    private var process: Process?
    private var timer: Timer?
    private var logURL: URL?
    private var logReader: FileHandle?
    private var logOffset: UInt64 = 0
    private var saveTargetURL: URL?
    private static var activeRunDirectories: Set<String> = []
    private var runDirectory: URL?
    private let runsRootOverride: URL?
    private let resolveRepository: () -> URL?

    init(runsRoot: URL? = nil, resolveRepository: @escaping () -> URL? = RepoRoot.resolve) {
        runsRootOverride = runsRoot
        self.resolveRepository = resolveRepository
    }

    var runsRoot: URL {
        runsRootOverride ?? FileManager.default.urls(for: .applicationSupportDirectory,
                                 in: .userDomainMask)[0]
            .appending(path: "NVXShowcase/runs", directoryHint: .isDirectory)
    }

    func launch() {
        beginRun(extraArgs: [], saveTarget: nil)
    }

    /// Boot the payload and drive the openvmm REPL to save a snapshot
    /// into `dir` once `marker` appears. Returns false (running nothing)
    /// when `dir` already exists — `snap` refuses an existing leaf.
    func launchSaving(to origDir: URL, marker: String, timeoutSeconds: Int) -> Bool {
        // openvmm rejects a symlinked snapshot parent (/tmp on macOS),
        // so resolve before anything touches the path. (URL's
        // resolvingSymlinksInPath leaves /tmp unresolved; realpath on
        // the existing parent does not.)
        let dir = RunController.resolvedDirectory(origDir)
        guard phase == .idle || phase == .done else { return false }
        guard FileManager.default.fileExists(atPath: dir.path) == false else {
            return false
        }
        beginRun(extraArgs: [
            "--save-snapshot", dir.path,
            "--save-on", marker,
            "--save-timeout", String(timeoutSeconds),
        ], saveTarget: dir, backingFileName: "membacking.bin")
        return true
    }

    /// Execute an arbitrary agent command in the sandbox using the native NVXEngine.
    func runAgentCommand(_ command: String, preferWarmSnapshot: Bool = true) async {
        guard phase == .idle || phase == .done else { return }
        reset()
        phase = .launching
        verdict = .running
        events.append(GuestEvent(kind: .info, text: "Launching agent command: \(command)"))
        phase = .live

        do {
            let res = try await NVXEngine.shared.runCommand(
                command: command,
                preferWarmSnapshot: preferWarmSnapshot,
                onOutput: { [weak self] line in
                    Task { @MainActor in
                        self?.consoleLines.append(line)
                        if self?.consoleLines.count ?? 0 > 2000 {
                            self?.consoleLines.removeFirst(100)
                        }
                    }
                }
            )
            let statusText = res.wasRestored
                ? "Restored warm snapshot in \(String(format: "%.1f", res.durationMs))ms"
                : "Cold boot in \(String(format: "%.1f", res.durationMs))ms"
            events.append(GuestEvent(kind: .info, text: "\(statusText) (Exit code: \(res.exitCode))"))
            verdict = res.isSuccess ? .contained : .failed
        } catch {
            events.append(GuestEvent(kind: .denied, text: "Execution failed: \(error.localizedDescription)"))
            verdict = .failed
        }
        phase = .done
    }

    /// Resolve `dir` through any symlinked parent components (/tmp on
    /// macOS). Falls back to `dir` unchanged when the parent is missing.
    static func resolvedDirectory(_ dir: URL) -> URL {
        var buffer = [CChar](repeating: 0, count: Int(PATH_MAX))
        let parent = dir.deletingLastPathComponent().path
        guard realpath(parent, &buffer) != nil else { return dir }
        return URL(filePath: String(cString: buffer))
            .appending(path: dir.lastPathComponent)
    }

    private func beginRun(extraArgs: [String], saveTarget: URL?,
                          backingFileName: String? = nil) {
        guard phase == .idle || phase == .done else { return }
        reset()
        saveTargetURL = saveTarget
        phase = .launching

        guard let repo = resolveRepository() else {
            events.append(GuestEvent(
                kind: .info,
                text: "launch failed: no nvx checkout found " +
                    "(set NVX_REPO or the repoRoot default)"))
            phase = .done
            verdict = .failed
            return
        }

        let stamp = ISO8601DateFormatter().string(from: Date())
            .replacingOccurrences(of: ":", with: "-")
        let dir = runsRoot.appending(path: "\(payload.id)-\(stamp)-\(UUID().uuidString)",
                                     directoryHint: .isDirectory)
        try? FileManager.default.createDirectory(at: dir,
                                                 withIntermediateDirectories: true)
        runDirectory = dir
        Self.activeRunDirectories.insert(dir.path)
        pruneRuns(keeping: 20)
        let log = dir.appending(path: "console.log")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        logURL = log
        var extraArgs = extraArgs
        if let backingFileName {
            // Fresh hvf boots need file-backed guest RAM before the REPL
            // can save. nvx.py floors hvf runs at 512 MiB, so a sparse
            // file that size always covers the UI's fixed memory shape.
            let backing = dir.appending(path: backingFileName)
            do {
                FileManager.default.createFile(atPath: backing.path, contents: nil)
                let handle = try FileHandle(forWritingTo: backing)
                try handle.truncate(atOffset: 512 * 1024 * 1024)
                try handle.close()
            } catch {
                events.append(GuestEvent(
                    kind: .info,
                    text: "launch failed: cannot create memory backing file: \(error)"))
                phase = .done
                verdict = .failed
                releaseRunDirectory()
                return
            }
            extraArgs += ["--memory-backing-file", backing.path]
        }

        let proc = Process()
        // /usr/bin/python3 is Apple Python 3.9 (no dataclass slots);
        // resolve the modern interpreter off PATH instead.
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = ["python3",
                          repo.appending(path: "scripts/nvx.py").path,
                          "run", "--hypervisor", "hvf"] + payload.arguments + extraArgs
        proc.standardInput = FileHandle.nullDevice
        proc.standardOutput = try? FileHandle(forWritingTo: log)
        proc.standardError = proc.standardOutput
        proc.currentDirectoryURL = repo
        do {
            try proc.run()
        } catch {
            events.append(GuestEvent(kind: .info, text: "launch failed: \(error)"))
            phase = .done
            verdict = .failed
            releaseRunDirectory()
            return
        }
        process = proc
        logReader = try? FileHandle(forReadingFrom: log)
        phase = .live

        timer = Timer.scheduledTimer(withTimeInterval: 0.4, repeats: true) {
            [weak self] _ in
            Task { @MainActor in self?.drain() }
        }
    }

    func stop() {
        if let proc = process { RepoRoot.killProcessTree(proc) }
        process = nil
        finish()
    }

    private func reset() {
        if let proc = process { RepoRoot.killProcessTree(proc, graceSeconds: 0) }
        process = nil
        timer?.invalidate()
        timer = nil
        try? logReader?.close()
        logReader = nil
        consoleLines = []
        events = []
        attempted = 0
        denied = 0
        verdict = .running
        logOffset = 0
        savedSnapshotURL = nil
        saveTargetURL = nil
        releaseRunDirectory()
    }

    private func releaseRunDirectory() {
        if let runDirectory { Self.activeRunDirectories.remove(runDirectory.path) }
        runDirectory = nil
    }

    /// Drop all but the newest `keeping` run directories so repeated
    /// runs don't fill Application Support.
    private func pruneRuns(keeping: Int) {
        let fm = FileManager.default
        guard let dirs = try? fm.contentsOfDirectory(
            at: runsRoot,
            includingPropertiesForKeys: [.contentModificationDateKey]) else { return }
        func mtime(_ url: URL) -> Date {
            (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
        }
        let ordered = dirs.filter { !Self.activeRunDirectories.contains($0.path) }.sorted { mtime($0) < mtime($1) }
        for stale in ordered.dropLast(keeping) {
            try? fm.removeItem(at: stale)
        }
    }

    private func drain() {
        guard let reader = logReader else { return }
        do {
            let end = try reader.seekToEnd()
            guard end > logOffset else { return }
            try reader.seek(toOffset: logOffset)
            let chunk = reader.readData(ofLength: Int(end - logOffset))
            logOffset = end
            ingest(chunk)
        } catch {
            return
        }
    }

    private func ingest(_ chunk: Data) {
        guard let text = String(data: chunk, encoding: .utf8) else { return }
        for raw in text.components(separatedBy: "\n") {
            let line = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !line.isEmpty else { continue }
            consoleLines.append(StreamParser.displayText(for: line))
            if let kind = StreamParser.guestEvent(for: line) {
                events.append(GuestEvent(kind: kind, text: line))
                switch kind {
                case .probeFailed, .probeSucceeded:
                    attempted += 1
                    if kind == .probeFailed { denied += 1 }
                case .snapshotSaved:
                    savedSnapshotURL = saveTargetURL
                default:
                    break
                }
            }
            if let reason = StreamParser.denialReason(for: line) {
                denied += 1
                events.append(GuestEvent(kind: .denied, text: reason))
            }
        }
        if consoleLines.count > 4000 {
            consoleLines.removeFirst(consoleLines.count - 4000)
        }
        updateVerdict()
        if let proc = process, !proc.isRunning {
            // The shim exited; make sure no openvmm child lingers.
            RepoRoot.killProcessTree(proc, graceSeconds: 0)
            process = nil
            finish()
        }
    }

    private func updateVerdict() {
        if phase != .live { return }
        if denied > 0 && attempted > 0 && denied >= attempted {
            verdict = .contained
        } else {
            verdict = .running
        }
    }

    private func finish() {
        timer?.invalidate()
        timer = nil
        drain()
        try? logReader?.close()
        logReader = nil
        process = nil
        releaseRunDirectory()
        if phase == .live {
            phase = .done
            if verdict == .running {
                verdict = attempted > 0 ? .escaped : .failed
            }
        }
    }
}
