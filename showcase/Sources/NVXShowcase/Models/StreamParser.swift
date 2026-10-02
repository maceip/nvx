import Foundation

/// Parses the combined VMM/guest console stream into structured events.
///
/// Pure functions over text so the mapping stays reviewable; verified
/// against real HVF boot logs, not fixtures.
enum StreamParser {
    /// Guest-side markers printed by the initramfs init.
    static func guestEvent(for line: String) -> GuestEvent.Kind? {
        if line.contains("snapshot saved to") {
            return .snapshotSaved
        }
        if line.contains("NVX-GUEST-BOOT-OK") || line.contains("BOOT-OK:") {
            return .boot
        }
        if line.contains("VIRTNET-DHCP-OK") || line.contains("VIRTNET-OK:") {
            return .networkUp
        }
        if line.contains("VIRT9P-OK") {
            return .shareMounted
        }
        if line.contains("VIRTNET-PROBE-OK") {
            return .probeSucceeded
        }
        if line.contains("VIRTNET-PROBE-FAIL") {
            return .probeFailed
        }
        return nil
    }

    /// Host-side denial evidence in VMM log output.
    static func denialReason(for line: String) -> String? {
        if line.contains("egress policy denied packet") {
            return "egress denied by policy"
        }
        if line.contains("read-only file system") || line.contains("EROFS") {
            return "read-only layer refused write"
        }
        if line.contains("EPERM") && line.contains("caller") {
            return "caller identity refused"
        }
        return nil
    }

    /// Strips VMM tracing prefixes (`0.123s  INFO ...:`) for display.
    static func displayText(for line: String) -> String {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return trimmed }
        if let range = trimmed.range(of: #"^\s*[\d.]+s\s+\w+\s+[\w:]+:\s*"#,
                                      options: .regularExpression) {
            return String(trimmed[range.upperBound...])
        }
        return trimmed
    }
}
