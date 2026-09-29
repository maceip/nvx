import Foundation

/// One artifact file inside a snapshot generation.
struct SnapshotFile: Identifiable, Hashable {
    var id: String { name }
    let name: String
    let size: Int64
    let modified: Date
}

/// One snapshot generation directory on disk.
struct SnapshotInfo: Identifiable, Hashable {
    let id = UUID()
    let url: URL
    let files: [SnapshotFile]

    var totalBytes: Int64 { files.map(\.size).reduce(0, +) }

    static func == (lhs: SnapshotInfo, rhs: SnapshotInfo) -> Bool {
        lhs.url == rhs.url
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(url)
    }
}

/// Scans a root for snapshot generations (directories with manifest.bin),
/// verifies them through `nvx.py snapshot verify`, and runs restores.
@Observable
@MainActor
final class SnapshotStore {
    var rootURL: URL? {
        didSet {
            if let rootURL {
                UserDefaults.standard.set(rootURL.path, forKey: "snapshotRoot")
            }
            rescan()
        }
    }
    var snapshots: [SnapshotInfo] = []
    var selection: SnapshotInfo?
    var verifyOutput: String = ""
    var verifyState: VerifyState = .idle
    var resumeOutput: String = ""
    var resumeRunning = false

    enum VerifyState: String {
        case idle = "Not verified"
        case running = "Verifying…"
        case ok = "Verified"
        case failed = "Failed"
    }

    static let repoRoot = URL(fileURLWithPath: "/Users/mac/nvx")

    init() {
        if let path = UserDefaults.standard.string(forKey: "snapshotRoot") {
            rootURL = URL(fileURLWithPath: path)
            rescan()
        }
    }

    func rescan() {
        snapshots = []
        selection = nil
        guard let rootURL else { return }
        let fm = FileManager.default
        // Resolve first: isDirectoryKey reports false for symlink roots
        // such as /tmp -> /private/tmp, which would skip enumeration.
        let resolved = rootURL.resolvingSymlinksInPath()
        var candidates = [resolved]
        if let children = try? fm.contentsOfDirectory(at: resolved,
                                                      includingPropertiesForKeys: [.isDirectoryKey]) {
            candidates += children.filter {
                (try? $0.resourceValues(forKeys: [.isDirectoryKey]))?.isDirectory == true
            }
        }
        // Only directories containing a manifest are generations; filter
        // before capping so a busy root (e.g. /tmp) still yields results.
        let generationDirs = candidates.filter { dir in
            (try? dir.appending(path: "manifest.bin").checkResourceIsReachable()) == true
        }.prefix(256)
        for dir in generationDirs {
            let files = ((try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.fileSizeKey, .contentModificationDateKey])) ?? [])
                .compactMap { url -> SnapshotFile? in
                    guard let vals = try? url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey, .isDirectoryKey]),
                          vals.isDirectory != true else { return nil }
                    return SnapshotFile(name: url.lastPathComponent,
                                        size: Int64(vals.fileSize ?? 0),
                                        modified: vals.contentModificationDate ?? .distantPast)
                }
                .sorted { $0.name < $1.name }
            snapshots.append(SnapshotInfo(url: dir, files: files))
        }
        snapshots.sort { $0.url.path < $1.url.path }
        selection = snapshots.first
    }

    func verify(_ snapshot: SnapshotInfo) {
        verifyState = .running
        verifyOutput = ""
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = ["python3",
                          Self.repoRoot.appending(path: "scripts/nvx.py").path,
                          "snapshot", "verify", snapshot.url.path]
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        do {
            try proc.run()
            proc.waitUntilExit()
            verifyOutput = String(data: pipe.fileHandleForReading.readDataToEndOfFile(),
                                  encoding: .utf8) ?? ""
            verifyState = proc.terminationStatus == 0 ? .ok : .failed
        } catch {
            verifyOutput = "verify failed to launch: \(error)"
            verifyState = .failed
        }
    }

    func resume(_ snapshot: SnapshotInfo) {
        resumeRunning = true
        resumeOutput = ""
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = ["python3",
                          Self.repoRoot.appending(path: "scripts/nvx.py").path,
                          "run", "--hypervisor", "hvf",
                          "--restore-snapshot", snapshot.url.path]
        proc.standardInput = FileHandle.nullDevice
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        proc.currentDirectoryURL = Self.repoRoot
        do {
            try proc.run()
        } catch {
            resumeOutput = "resume failed to launch: \(error)"
            resumeRunning = false
            return
        }
        DispatchQueue.global().async { [weak self] in
            proc.waitUntilExit()
            let text = String(data: pipe.fileHandleForReading.readDataToEndOfFile(),
                              encoding: .utf8) ?? ""
            Task { @MainActor [weak self] in
                self?.resumeOutput = text
                self?.resumeRunning = false
            }
        }
    }
}
