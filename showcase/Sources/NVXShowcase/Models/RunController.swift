import Foundation

/// Owns one live detonation: launches `nvx.py run`, tails its combined
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

    private var process: Process?
    private var timer: Timer?
    private var logURL: URL?
    private var logReader: FileHandle?
    private var logOffset: UInt64 = 0

    var runsRoot: URL {
        FileManager.default.urls(for: .applicationSupportDirectory,
                                 in: .userDomainMask)[0]
            .appending(path: "NVXShowcase/runs", directoryHint: .isDirectory)
    }

    func launch() {
        guard phase == .idle || phase == .done else { return }
        reset()
        phase = .launching

        guard let repo = RepoRoot.resolve() else {
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
        let dir = runsRoot.appending(path: "\(payload.id)-\(stamp)",
                                     directoryHint: .isDirectory)
        try? FileManager.default.createDirectory(at: dir,
                                                 withIntermediateDirectories: true)
        pruneRuns(keeping: 20)
        let log = dir.appending(path: "console.log")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        logURL = log

        let proc = Process()
        // /usr/bin/python3 is Apple Python 3.9 (no dataclass slots);
        // resolve the modern interpreter off PATH instead.
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = ["python3",
                          repo.appending(path: "scripts/nvx.py").path,
                          "run", "--hypervisor", "hvf"] + payload.arguments
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
    }

    /// Drop all but the newest `keeping` run directories so repeated
    /// detonations don't fill Application Support.
    private func pruneRuns(keeping: Int) {
        let fm = FileManager.default
        guard let dirs = try? fm.contentsOfDirectory(
            at: runsRoot,
            includingPropertiesForKeys: [.contentModificationDateKey]) else { return }
        func mtime(_ url: URL) -> Date {
            (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
        }
        let ordered = dirs.sorted { mtime($0) < mtime($1) }
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
        if phase == .live {
            phase = .done
            if verdict == .running {
                verdict = attempted > 0 ? .escaped : .failed
            }
        }
    }
}
