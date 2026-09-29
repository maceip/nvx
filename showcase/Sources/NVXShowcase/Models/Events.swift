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

/// A malicious-behavior payload the dashboard can detonate on HVF.
struct Payload: Identifiable, Hashable {
    let id: String
    let name: String
    let blurb: String
    /// Extra `nvx.py run` arguments for this payload.
    let arguments: [String]

    static let exfiltrator = Payload(
        id: "exfiltrator",
        name: "Exfiltrator",
        blurb: "Phones home to a criminal server. Egress policy blackholes it.",
        arguments: [
            "--virtio-net", "consomme:192.168.127.0/24",
            "--network-egress", "deny",
            "--cmdline", "virtnet_probe=192.168.127.1",
        ]
    )

    static let all: [Payload] = [.exfiltrator]
}
