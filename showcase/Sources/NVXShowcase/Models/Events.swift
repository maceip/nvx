import Foundation

/// A single guest event extracted from the live console/VMM stream.
struct GuestEvent: Identifiable, Hashable {
    enum Kind: Hashable {
        case boot
        case networkUp
        case shareMounted
        case probeSucceeded
        case probeFailed
        /// A host-side denial (policy drop, EPERM, read-only refusal).
        case denied
        /// A `--save-snapshot` capture completed on the openvmm REPL.
        case snapshotSaved
        case info
    }

    let id = UUID()
    let kind: Kind
    let text: String
    let timestamp = Date()
}

/// Verdict computed from attempted vs. denied counts.
enum Verdict: String {
    case running = "RUNNING"
    case contained = "CONTAINED"
    case escaped = "ESCAPED"
    case failed = "FAILED"
    case clean = "CLEAN"

    var systemImage: String {
        switch self {
        case .running: "circle.dotted"
        case .contained: "lock.shield.fill"
        case .escaped: "exclamationmark.triangle.fill"
        case .failed: "xmark.octagon.fill"
        case .clean: "checkmark.shield.fill"
        }
    }
}

/// A malicious-behavior payload the dashboard can run on HVF.
struct Payload: Identifiable, Hashable {
    let id: String
    let name: String
    let blurb: String
    /// Extra `nvx.py run` arguments for this payload.
    let arguments: [String]
    /// Boot marker this payload always prints; the Save sheet default.
    let saveMarker: String

    static let exfiltrator = Payload(
        id: "exfiltrator",
        name: "Exfiltrator",
        blurb: "Phones home to a criminal server. Egress policy blackholes it.",
        arguments: [
            "--virtio-net", "consomme:192.168.127.0/24",
            "--network-egress", "deny",
            "--cmdline", "virtnet_probe=192.168.127.1",
        ],
        // Fires at the end of every boot regardless of network policy
        // (egress-deny blackholes DHCP, so VIRTNET-DHCP-OK never prints
        // for this payload), capturing the fully booted shell.
        saveMarker: "NVX-GUEST-BOOT-OK"
    )

    static let all: [Payload] = [.exfiltrator]
}
