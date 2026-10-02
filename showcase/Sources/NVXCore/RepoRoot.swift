import Foundation

/// Locates the nvx checkout (`scripts/nvx.py`) and owns process-tree
/// teardown. Shared by RunController and SnapshotStore so neither
/// hardcodes a machine-specific path and no VM outlives its run.
public enum RepoRoot {
    private static let overrideKey = "repoRoot"

    public static var override: URL? {
        get {
            UserDefaults.standard.string(forKey: overrideKey)
                .map { URL(fileURLWithPath: $0) }
        }
        set {
            if let newValue {
                UserDefaults.standard.set(newValue.path, forKey: overrideKey)
            } else {
                UserDefaults.standard.removeObject(forKey: overrideKey)
            }
        }
    }

    /// First candidate directory containing `scripts/nvx.py`, or nil.
    /// Order: user override, $NVX_REPO, bundle-relative walk-up
    /// (covers both the .app in showcase/dist and SPM .build output),
    /// then ~/nvx.
    public static func resolve() -> URL? {
        var candidates: [URL] = []
        if let o = override { candidates.append(o) }
        if let env = ProcessInfo.processInfo.environment["NVX_REPO"] {
            candidates.append(URL(fileURLWithPath: env))
        }
        let fm = FileManager.default
        for base in [Bundle.main.bundleURL,
                     URL(fileURLWithPath: CommandLine.arguments[0])
                        .deletingLastPathComponent()] {
            var dir: URL? = base
            for _ in 0..<8 {
                guard let d = dir else { break }
                candidates.append(d)
                dir = d.deletingLastPathComponent()
                if d.path == "/" { break }
            }
        }
        candidates.append(fm.homeDirectoryForCurrentUser
            .appending(path: "nvx", directoryHint: .isDirectory))
        for dir in candidates {
            let script = dir.appending(path: "scripts/nvx.py")
            if fm.fileExists(atPath: script.path) { return dir }
        }
        return nil
    }

    /// Direct children of a pid via pgrep.
    private static func children(of pid: Int32) -> [Int32] {
        let out = Pipe()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        p.arguments = ["-P", String(pid)]
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return [] }
        p.waitUntilExit()
        let text = String(data: out.fileHandleForReading.readDataToEndOfFile(),
                          encoding: .utf8) ?? ""
        return text.split(separator: "\n").compactMap { Int32($0) }
    }

    /// SIGTERM the whole tree (deepest first), then SIGKILL survivors
    /// after a grace period. The grace wait runs off-caller so this is
    /// safe from the main thread. A plain `terminate()` only kills the
    /// `nvx.py` shim and orphans the openvmm child, which keeps the
    /// guest running indefinitely.
    public static func killProcessTree(_ proc: Process,
                                       graceSeconds: UInt32 = 2) {
        let root = proc.processIdentifier
        guard root > 0 else { proc.terminate(); return }
        var all: [Int32] = []
        var stack = [root]
        while let pid = stack.popLast() {
            let kids = children(of: pid)
            all.append(pid)
            stack.append(contentsOf: kids)
        }
        let tree = all.reversed() as [Int32]
        for pid in tree { kill(pid, SIGTERM) }
        proc.terminate()
        DispatchQueue.global().async {
            if graceSeconds > 0 { sleep(graceSeconds) }
            for pid in tree where kill(pid, 0) == 0 { kill(pid, SIGKILL) }
        }
    }
}
