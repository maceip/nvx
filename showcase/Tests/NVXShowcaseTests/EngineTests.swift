import Foundation
import Testing
@testable import NVXCore

@Suite(.serialized)
struct EngineTests {
    @Test func statusReportsReady() {
        let engine = NVXEngine.shared
        let status = engine.checkStatus()
        print("Status ready:", status.ready)
        print("Repo root:", status.repoRoot ?? "nil")
        print("Default snapshot available:", status.defaultSnapshotAvailable)
        #expect(status.ready == true)
    }

    @Test func verifyExistingSnapshot() async {
        let engine = NVXEngine.shared
        let snapURL = URL(fileURLWithPath: "/private/tmp/snap-warm-01")
        guard FileManager.default.fileExists(atPath: snapURL.appending(path: "manifest.bin").path) else {
            return
        }
        let manifest = await engine.verifySnapshot(at: snapURL)
        print("Manifest valid:", manifest.isValid, "arch:", manifest.architecture ?? "nil")
        #expect(manifest.isValid == true)
        #expect(manifest.architecture == "aarch64")
    }

    @Test func runCommandColdBoot() async throws {
        let engine = NVXEngine.shared
        let res = try await engine.runCommand(
            command: "echo HELLO_NVX; uname -a",
            timeout: 15.0,
            preferWarmSnapshot: false,
            onOutput: { line in
                print("[VM LINE]:", line)
            }
        )
        print("Cold boot duration: \(res.durationMs)ms, exit: \(res.exitCode)")
        print("Stdout:\n\(res.stdout)")
        #expect(res.exitCode == 0)
        #expect(res.stdout.contains("HELLO_NVX"))
        #expect(res.stdout.contains("Linux"))
    }

    @Test func runCommandWarmSnapshot() async throws {
        let engine = NVXEngine.shared
        let snapURL = URL(fileURLWithPath: "/private/tmp/snap-warm-01")
        guard FileManager.default.fileExists(atPath: snapURL.appending(path: "manifest.bin").path) else {
            return
        }
        let res = try await engine.runCommand(
            command: "echo HELLO_RESTORE; uname -a",
            timeout: 5.0,
            preferWarmSnapshot: true,
            explicitSnapshot: snapURL,
            onOutput: { line in
                print("[RESTORE LINE]:", line)
            }
        )
        print("Warm restore duration: \(res.durationMs)ms, exit: \(res.exitCode)")
        print("Stdout:\n\(res.stdout)")
        #expect(res.exitCode == 0)
        #expect(res.wasRestored == true)
        #expect(res.stdout.contains("HELLO_RESTORE"))
        #expect(res.stdout.contains("Linux"))
    }

    @Test func checkpointLifecycle() throws {
        let engine = NVXEngine.shared
        let testCheckpointName = "test-checkpoint-\(UUID().uuidString.prefix(8))"
        defer {
            try? engine.deleteCheckpoint(name: testCheckpointName)
        }
        let url = try engine.createCheckpoint(name: testCheckpointName)
        #expect(FileManager.default.fileExists(atPath: url.appending(path: "manifest.bin").path))
        let list = engine.listCheckpoints()
        #expect(list.contains(testCheckpointName))
        try engine.deleteCheckpoint(name: testCheckpointName)
        #expect(!engine.listCheckpoints().contains(testCheckpointName))
    }
}
