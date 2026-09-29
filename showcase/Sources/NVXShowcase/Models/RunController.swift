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
    private var logOffset = 0

    static let repoRoot = URL(fileURLWithPath: "/Users/mac/nvx")

    var runsRoot: URL {
        FileManager.default.urls(for: .applicationSupportDirectory,
                                 in: .userDomainMask)[0]
            .appending(path: "NVXShowcase/runs", directoryHint: .isDirectory)
    }

    func launch() {
        guard phase == .idle || phase == .done else { return }
        reset()
        phase = .launching

        let stamp = ISO8601DateFormatter().string(from: Date())
            .replacingOccurrences(of: ":", with: "-")
        let dir = runsRoot.appending(path: "\(payload.id)-\(stamp)",
                                     directoryHint: .isDirectory)
        try? FileManager.default.createDirectory(at: dir,
                                                 withIntermediateDirectories: true)
        let log = dir.appending(path: "console.log")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        logURL = log

        let proc = Process()
        // /usr/bin/python3 is Apple Python 3.9 (no dataclass slots);
        // resolve the modern interpreter off PATH instead.
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = ["python3",
                          Self.repoRoot.appending(path: "scripts/nvx.py").path,
                          "run", "--hypervisor", "hvf"] + payload.arguments
        proc.standardInput = FileHandle.nullDevice
        proc.standardOutput = try? FileHandle(forWritingTo: log)
        proc.standardError = proc.standardOutput
        proc.currentDirectoryURL = Self.repoRoot
        do {
            try proc.run()
        } catch {
            events.append(GuestEvent(kind: .info, text: "launch failed: \(error)"))
            phase = .done
            verdict = .failed
            return
        }
        process = proc
        phase = .live

        timer = Timer.scheduledTimer(withTimeInterval: 0.4, repeats: true) {
            [weak self] _ in
            Task { @MainActor in self?.drain() }
        }
    }

    func stop() {
        process?.terminate()
        finish()
    }

    private func reset() {
        process = nil
        timer?.invalidate()
        timer = nil
        consoleLines = []
        events = []
        attempted = 0
        denied = 0
        verdict = .running
        logOffset = 0
    }

    private func drain() {
        guard let logURL,
              let data = try? Data(contentsOf: logURL),
              data.count > logOffset else { return }
        let chunk = data[logOffset...]
        logOffset = data.count
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
        if process?.isRunning == false { finish() }
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
        if phase == .live {
            phase = .done
            if verdict == .running {
                verdict = attempted > 0 ? .escaped : .failed
            }
        }
    }
}
