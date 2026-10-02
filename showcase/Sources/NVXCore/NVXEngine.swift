import Foundation

public struct NVXExecutionResult: Codable, Sendable {
    public let exitCode: Int
    public let stdout: String
    public let durationMs: Double
    public let isSuccess: Bool
    public let wasRestored: Bool

    public init(exitCode: Int, stdout: String, durationMs: Double, isSuccess: Bool, wasRestored: Bool) {
        self.exitCode = exitCode
        self.stdout = stdout
        self.durationMs = durationMs
        self.isSuccess = isSuccess
        self.wasRestored = wasRestored
    }
}

public struct NVXSnapshotManifest: Codable, Sendable {
    public let path: String
    public let isValid: Bool
    public let manifestVersion: Int?
    public let architecture: String?
    public let guestRamBytes: Int64?
    public let vcpuCount: Int?
    public let rawOutput: String

    public init(path: String, isValid: Bool, manifestVersion: Int?, architecture: String?, guestRamBytes: Int64?, vcpuCount: Int?, rawOutput: String) {
        self.path = path
        self.isValid = isValid
        self.manifestVersion = manifestVersion
        self.architecture = architecture
        self.guestRamBytes = guestRamBytes
        self.vcpuCount = vcpuCount
        self.rawOutput = rawOutput
    }
}

public struct NVXStatus: Codable, Sendable {
    public let ready: Bool
    public let repoRoot: String?
    public let openvmmPath: String?
    public let openvmmExists: Bool
    public let kernelPath: String?
    public let kernelExists: Bool
    public let initrdPath: String?
    public let initrdExists: Bool
    public let defaultSnapshotAvailable: Bool
    public let defaultSnapshotPath: String?

    public init(ready: Bool, repoRoot: String?, openvmmPath: String?, openvmmExists: Bool, kernelPath: String?, kernelExists: Bool, initrdPath: String?, initrdExists: Bool, defaultSnapshotAvailable: Bool, defaultSnapshotPath: String?) {
        self.ready = ready
        self.repoRoot = repoRoot
        self.openvmmPath = openvmmPath
        self.openvmmExists = openvmmExists
        self.kernelPath = kernelPath
        self.kernelExists = kernelExists
        self.initrdPath = initrdPath
        self.initrdExists = initrdExists
        self.defaultSnapshotAvailable = defaultSnapshotAvailable
        self.defaultSnapshotPath = defaultSnapshotPath
    }
}

public final class NVXEngine: @unchecked Sendable {
    public static let shared = NVXEngine()

    public init() {}

    public var defaultSnapshotDirectory: URL {
        let fm = FileManager.default
        let appSupport = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "NVXShowcase/snapshots/warm-default", directoryHint: .isDirectory)
        return appSupport
    }

    public func checkStatus() -> NVXStatus {
        guard let repo = RepoRoot.resolve() else {
            return NVXStatus(ready: false, repoRoot: nil, openvmmPath: nil, openvmmExists: false,
                             kernelPath: nil, kernelExists: false, initrdPath: nil, initrdExists: false,
                             defaultSnapshotAvailable: false, defaultSnapshotPath: nil)
        }
        let fm = FileManager.default
        let openvmm = repo.appending(path: "openvmm/target/release/openvmm")
        let kernel = repo.appending(path: "build/Image")
        let initrd = repo.appending(path: "build/initramfs.cpio.gz")

        let openvmmExists = fm.isExecutableFile(atPath: openvmm.path)
        let kernelExists = fm.fileExists(atPath: kernel.path)
        let initrdExists = fm.fileExists(atPath: initrd.path)

        var snapAvailable = false
        var snapPath: String? = nil

        let candidates = [
            defaultSnapshotDirectory,
            URL(fileURLWithPath: "/private/tmp/snap-warm-01")
        ]
        for candidate in candidates {
            let manifest = candidate.appending(path: "manifest.bin")
            if fm.fileExists(atPath: manifest.path) {
                snapAvailable = true
                snapPath = candidate.path
                break
            }
        }

        let ready = openvmmExists && kernelExists && initrdExists
        return NVXStatus(ready: ready, repoRoot: repo.path, openvmmPath: openvmm.path,
                         openvmmExists: openvmmExists, kernelPath: kernel.path,
                         kernelExists: kernelExists, initrdPath: initrd.path,
                         initrdExists: initrdExists, defaultSnapshotAvailable: snapAvailable,
                         defaultSnapshotPath: snapPath)
    }

    public func verifySnapshot(at dir: URL) async -> NVXSnapshotManifest {
        guard let repo = RepoRoot.resolve() else {
            return NVXSnapshotManifest(path: dir.path, isValid: false, manifestVersion: nil,
                                       architecture: nil, guestRamBytes: nil, vcpuCount: nil,
                                       rawOutput: "Cannot resolve nvx repository")
        }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = ["python3", repo.appending(path: "scripts/nvx.py").path, "snapshot", "verify", dir.path]
        proc.currentDirectoryURL = repo
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        do {
            try proc.run()
            proc.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let text = String(data: data, encoding: .utf8) ?? ""
            let isValid = proc.terminationStatus == 0 && text.contains("snapshot OK")

            var version: Int? = nil
            var arch: String? = nil
            var ram: Int64? = nil
            var vcpus: Int? = nil

            for line in text.components(separatedBy: "\n") {
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if trimmed.hasPrefix("manifest version:") {
                    version = Int(trimmed.dropFirst("manifest version:".count).trimmingCharacters(in: .whitespaces))
                } else if trimmed.hasPrefix("architecture:") {
                    arch = trimmed.dropFirst("architecture:".count).trimmingCharacters(in: .whitespaces)
                } else if trimmed.hasPrefix("guest RAM:") {
                    let part = trimmed.dropFirst("guest RAM:".count).trimmingCharacters(in: .whitespaces)
                    ram = Int64(part.components(separatedBy: " ").first ?? "")
                } else if trimmed.hasPrefix("vCPUs:") {
                    vcpus = Int(trimmed.dropFirst("vCPUs:".count).trimmingCharacters(in: .whitespaces))
                }
            }
            return NVXSnapshotManifest(path: dir.path, isValid: isValid, manifestVersion: version,
                                       architecture: arch, guestRamBytes: ram, vcpuCount: vcpus,
                                       rawOutput: text)
        } catch {
            return NVXSnapshotManifest(path: dir.path, isValid: false, manifestVersion: nil,
                                       architecture: nil, guestRamBytes: nil, vcpuCount: nil,
                                        rawOutput: "Failed to run verify: \(error)")
        }
    }

    public var snapshotsDirectory: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appending(path: ".nvx/snapshots", directoryHint: .isDirectory)
    }

    @discardableResult
    public func createCheckpoint(name: String, sourceSnapshot: URL? = nil) throws -> URL {
        let dir = snapshotsDirectory
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let target = dir.appending(path: name, directoryHint: .isDirectory)
        if FileManager.default.fileExists(atPath: target.path) {
            try FileManager.default.removeItem(at: target)
        }
        var source = sourceSnapshot
        if source == nil {
            let candidates = [
                defaultSnapshotDirectory,
                URL(fileURLWithPath: "/private/tmp/snap-warm-01")
            ]
            for candidate in candidates {
                if FileManager.default.fileExists(atPath: candidate.appending(path: "manifest.bin").path) {
                    source = candidate
                    break
                }
            }
        }
        guard let sourceURL = source, FileManager.default.fileExists(atPath: sourceURL.appending(path: "manifest.bin").path) else {
            throw NSError(domain: "NVXEngine", code: 3, userInfo: [NSLocalizedDescriptionKey: "Source snapshot not found"])
        }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/cp")
        proc.arguments = ["-c", "-R", sourceURL.path, target.path]
        try proc.run()
        proc.waitUntilExit()
        guard proc.terminationStatus == 0 else {
            throw NSError(domain: "NVXEngine", code: 4, userInfo: [NSLocalizedDescriptionKey: "Failed to clone snapshot to \(target.path)"])
        }
        return target
    }

    public func listCheckpoints() -> [String] {
        let dir = snapshotsDirectory
        guard let items = try? FileManager.default.contentsOfDirectory(atPath: dir.path) else {
            return []
        }
        return items.filter { item in
            FileManager.default.fileExists(atPath: dir.appending(path: "\(item)/manifest.bin").path)
        }.sorted()
    }

    public func deleteCheckpoint(name: String) throws {
        let target = snapshotsDirectory.appending(path: name, directoryHint: .isDirectory)
        if FileManager.default.fileExists(atPath: target.path) {
            try FileManager.default.removeItem(at: target)
        }
    }

    public func runCommand(command: String, timeout: Double = 30.0,
                           preferWarmSnapshot: Bool = true,
                           explicitSnapshot: URL? = nil,
                           onOutput: (@Sendable (String) -> Void)? = nil) async throws -> NVXExecutionResult {
        guard let repo = RepoRoot.resolve() else {
            throw NSError(domain: "NVXEngine", code: 1, userInfo: [NSLocalizedDescriptionKey: "Repository root not found"])
        }
        let openvmm = repo.appending(path: "openvmm/target/release/openvmm")
        let kernel = repo.appending(path: "build/Image")
        let initrd = repo.appending(path: "build/initramfs.cpio.gz")

        var restorePath: URL? = nil
        if let explicit = explicitSnapshot {
            if FileManager.default.fileExists(atPath: explicit.appending(path: "manifest.bin").path) {
                restorePath = explicit
            } else {
                let cpTarget = snapshotsDirectory.appending(path: explicit.path)
                if FileManager.default.fileExists(atPath: cpTarget.appending(path: "manifest.bin").path) {
                    restorePath = cpTarget
                }
            }
        }
        if restorePath == nil && preferWarmSnapshot {
            let candidates = [
                defaultSnapshotDirectory,
                URL(fileURLWithPath: "/private/tmp/snap-warm-01")
            ]
            for candidate in candidates {
                if FileManager.default.fileExists(atPath: candidate.appending(path: "manifest.bin").path) {
                    restorePath = candidate
                    break
                }
            }
        }

        var args: [String] = [
            "--single-process",
            "--processors", "1",
            "--hypervisor", "hvf",
            "--com1", "console"
        ]

        let isRestoring = restorePath != nil
        if let restorePath {
            args += [
                "--restore-snapshot", restorePath.path,
                "--virtio-net", "consomme:192.168.127.0/24,snapshot"
            ]
        } else {
            args += [
                "--memory", "512M",
                "--kernel", kernel.path,
                "--initrd", initrd.path
            ]
        }

        let proc = Process()
        proc.executableURL = openvmm
        proc.arguments = args
        proc.currentDirectoryURL = repo

        let stdinPipe = Pipe()
        let stdoutPipe = Pipe()
        proc.standardInput = stdinPipe
        proc.standardOutput = stdoutPipe
        proc.standardError = stdoutPipe

        let startTime = CFAbsoluteTimeGetCurrent()
        try proc.run()

        let tag = UUID().uuidString.replacingOccurrences(of: "-", with: "")
        let startMarker = "START:\(tag)"
        let endMarkerPrefix = "END:\(tag):"

        let stdinHandle = stdinPipe.fileHandleForWriting
        let stdoutHandle = stdoutPipe.fileHandleForReading

        let fullCommand = """
        export PS1=""; export PS2=""; stty -echo 2>/dev/null
        echo \(startMarker)
        (
        \(command)
        )
        echo \(endMarkerPrefix)$?:___
        /sbin/nvx-exit 0

        """

        if isRestoring {
            if let cmdData = fullCommand.data(using: .utf8) {
                try? stdinHandle.write(contentsOf: cmdData)
            }
        }

        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let deadline = Date().addingTimeInterval(timeout)
                var buffer = ""
                var outputLines: [String] = []
                var recording = false
                var bootReady = isRestoring
                var foundExitCode: Int? = nil

                func cleanup() {
                    try? stdinHandle.close()
                    let stopDeadline = Date().addingTimeInterval(0.5)
                    while proc.isRunning && Date() < stopDeadline {
                        Thread.sleep(forTimeInterval: 0.02)
                    }
                    if proc.isRunning {
                        RepoRoot.killProcessTree(proc, graceSeconds: 0)
                    }
                }

                let fd = stdoutHandle.fileDescriptor
                let flags = fcntl(fd, F_GETFL)
                _ = fcntl(fd, F_SETFL, flags | O_NONBLOCK)

                var readBuf = [UInt8](repeating: 0, count: 65536)

                while Date() < deadline {
                    let count = read(fd, &readBuf, readBuf.count)
                    if count > 0 {
                        if let chunk = String(bytes: readBuf[..<count], encoding: .utf8) {
                            buffer += chunk
                        }
                    } else if count == 0 {
                        if !proc.isRunning { break }
                    } else {
                        if errno != EAGAIN && errno != EWOULDBLOCK {
                            if !proc.isRunning { break }
                        }
                        Thread.sleep(forTimeInterval: 0.01)
                    }

                    while let newlineIndex = buffer.firstIndex(where: \.isNewline) {
                        let line = String(buffer[..<newlineIndex])
                        buffer.removeSubrange(...newlineIndex)
                        onOutput?(line)

                        let trimmed = line.trimmingCharacters(in: .whitespaces)

                        if !bootReady {
                            if !isRestoring && (line.contains("can't access tty") || line.contains("/ # ") || line.contains("NVX-GUEST-BOOT-OK: alpine")) {
                                bootReady = true
                                Thread.sleep(forTimeInterval: 0.05)
                                if let cmdData = fullCommand.data(using: .utf8) {
                                    try? stdinHandle.write(contentsOf: cmdData)
                                }
                            }
                            continue
                        }

                        if trimmed.hasPrefix("echo ") || trimmed.hasPrefix("export ") || trimmed.hasPrefix("stty ") {
                            continue
                        }

                        if line.contains(startMarker) {
                            recording = true
                            continue
                        }

                        if let endRange = line.range(of: endMarkerPrefix) {
                            let suffix = line[endRange.upperBound...]
                            let codeStr = suffix.components(separatedBy: ":").first ?? ""
                            if let code = Int(codeStr) {
                                foundExitCode = code
                            }
                            recording = false
                            break
                        }

                        if recording {
                            outputLines.append(line)
                        }
                    }

                    if foundExitCode != nil {
                        break
                    }
                }

                cleanup()
                let durationMs = (CFAbsoluteTimeGetCurrent() - startTime) * 1000.0

                if let exitCode = foundExitCode {
                    let cleanOutput = outputLines.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
                    continuation.resume(returning: NVXExecutionResult(
                        exitCode: exitCode,
                        stdout: cleanOutput,
                        durationMs: durationMs,
                        isSuccess: exitCode == 0,
                        wasRestored: isRestoring
                    ))
                } else {
                    continuation.resume(throwing: NSError(
                        domain: "NVXEngine",
                        code: 2,
                        userInfo: [NSLocalizedDescriptionKey: "Execution timed out after \(timeout)s or failed to return marker"]
                    ))
                }
            }
        }
    }
}
