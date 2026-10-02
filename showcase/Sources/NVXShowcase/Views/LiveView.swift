import SwiftUI
import UniformTypeIdentifiers

/// The glass box: live guest console beside the host denial feed,
/// with the attempted-vs-denied verdict on top.
struct LiveView: View {
    @Bindable var controller: RunController
    /// Called with the save directory's parent when the user reviews a
    /// snapshot captured by the Save sheet.
    var onSnapshotSaved: ((URL) -> Void)? = nil
    @State private var showingSave = false

    private var canLaunch: Bool {
        controller.phase == .idle || controller.phase == .done
    }

    var body: some View {
        VStack(spacing: 0) {
            runControls
            Divider()
            verdictBanner
            Divider()
            HSplitView {
                consolePane
                    .frame(minWidth: 400)
                eventsPane
                    .frame(minWidth: 280, idealWidth: 340)
            }
            if controller.phase == .done, let saved = controller.savedSnapshotURL {
                Divider()
                HStack {
                    Label("Snapshot saved to \(saved.lastPathComponent)",
                          systemImage: "tray.and.arrow.down.fill")
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Review in Snapshots") {
                        onSnapshotSaved?(saved.deletingLastPathComponent())
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(.bar)
            }
        }
        .sheet(isPresented: $showingSave) {
            SaveSnapshotSheet(controller: controller)
        }
    }

    private var runControls: some View {
        HStack(spacing: 12) {
            Group {
                Menu(controller.payload.name) {
                    ForEach(Payload.all) { payload in
                        Button {
                            controller.payload = payload
                        } label: {
                            if controller.payload == payload {
                                Label(payload.name, systemImage: "checkmark")
                            } else {
                                Text(payload.name)
                            }
                        }
                    }
                }
                .help(controller.payload.blurb)
                .disabled(!canLaunch)
            }
            Group {
                Button("Save snapshot…", systemImage: "tray.and.arrow.down") {
                    showingSave = true
                }
                .disabled(!canLaunch)
            }
            Spacer()
            Group {
                if controller.phase == .live {
                    Button("Stop", systemImage: "stop.fill") {
                        controller.stop()
                    }
                } else {
                    Button("Run", systemImage: "play.fill") {
                        controller.launch()
                    }
                    .disabled(controller.phase == .launching)
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(.bar)
    }

    private var verdictBanner: some View {
        HStack(spacing: 16) {
            Image(systemName: controller.verdict.systemImage)
                .font(.system(size: 36))
                .foregroundStyle(verdictColor)
            VStack(alignment: .leading, spacing: 2) {
                Text(controller.verdict.rawValue)
                    .font(.system(size: 28, weight: .bold))
                Text("\(controller.payload.name) · \(controller.phase.rawValue)")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Text(controller.payload.blurb)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            StatBlock(label: "Attempted", value: controller.attempted)
            StatBlock(label: "Denied", value: controller.denied)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
        .background(.bar)
    }

    private var verdictColor: Color {
        switch controller.verdict {
        case .contained: .green
        case .escaped: .red
        case .failed: .orange
        case .running: .blue
        case .clean: .green
        }
    }

    private var consolePane: some View {
        VStack(alignment: .leading, spacing: 0) {
            paneHeader("Guest console", systemImage: "terminal")
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        ForEach(controller.consoleLines.indices, id: \.self) { index in
                            Text(controller.consoleLines[index])
                                .font(.system(size: 12, design: .monospaced))
                                .textSelection(.enabled)
                                .id(index)
                        }
                    }
                    .padding(8)
                }
                .background(Color(nsColor: .textBackgroundColor))
                .onChange(of: controller.consoleLines.count) {
                    if let last = controller.consoleLines.indices.last {
                        proxy.scrollTo(last, anchor: .bottom)
                    }
                }
            }
        }
    }

    private var eventsPane: some View {
        VStack(alignment: .leading, spacing: 0) {
            paneHeader("Policy denials", systemImage: "shield.slash")
            List(controller.events) { event in
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: eventIcon(for: event.kind))
                        .foregroundStyle(eventColor(for: event.kind))
                    Text(StreamParser.displayText(for: event.text))
                        .font(.callout)
                        .textSelection(.enabled)
                }
                .padding(.vertical, 2)
            }
            .listStyle(.plain)
        }
    }

    private func paneHeader(_ title: String, systemImage: String) -> some View {
        HStack {
            Label(title, systemImage: systemImage)
                .font(.headline)
            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.bar)
    }

    private func eventIcon(for kind: GuestEvent.Kind) -> String {
        switch kind {
        case .boot: "power"
        case .networkUp: "network"
        case .shareMounted: "folder"
        case .probeSucceeded: "paperplane"
        case .probeFailed: "paperplane.slash"
        case .denied: "nosign"
        case .snapshotSaved: "tray.and.arrow.down.fill"
        case .info: "info.circle"
        }
    }

    private func eventColor(for kind: GuestEvent.Kind) -> Color {
        switch kind {
        case .denied, .probeFailed: .red
        case .probeSucceeded, .boot, .snapshotSaved: .green
        default: .secondary
        }
    }
}

/// Captures a snapshot through the openvmm REPL: boots the payload,
/// waits for a marker, saves, shuts down. The leaf must not exist.
private struct SaveSnapshotSheet: View {
    @Bindable var controller: RunController
    @Environment(\.dismiss) private var dismiss
    @State private var parent: URL = URL(
        fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
    @State private var name: String = "nvxsave-" + ISO8601DateFormatter()
        .string(from: Date()).replacingOccurrences(of: ":", with: "-")
    @State private var showingPicker = false
    @State private var errorText = ""
    @State private var timeoutText = "600"

    private var timeoutSeconds: Int {
        max(1, Int(timeoutText) ?? 600)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Save snapshot")
                .font(.headline)
            Text("Runs \(controller.payload.name) and captures the guest " +
                "once the marker appears.")
                .font(.callout)
                .foregroundStyle(.secondary)
            LabeledContent("Directory") {
                HStack {
                    Text(parent.path + "/" + name)
                        .font(.callout.monospaced())
                        .textSelection(.enabled)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Button("Choose…") { showingPicker = true }
                }
            }
            LabeledContent("Marker") {
                Text(controller.payload.saveMarker)
                    .font(.callout.monospaced())
            }
            LabeledContent("Timeout (s)") {
                TextField("600", text: $timeoutText)
                    .frame(width: 80)
            }
            if !errorText.isEmpty {
                Text(errorText)
                    .font(.callout)
                    .foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Save") {
                    let dir = parent.appending(path: name,
                                               directoryHint: .isDirectory)
                    if controller.launchSaving(
                        to: dir,
                        marker: controller.payload.saveMarker,
                        timeoutSeconds: timeoutSeconds)
                    {
                        dismiss()
                    } else {
                        errorText = "That directory already exists; " +
                            "pick a new name (snap refuses an existing leaf)."
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(controller.phase != .idle &&
                    controller.phase != .done)
            }
        }
        .padding(20)
        .frame(width: 460)
        .fileImporter(isPresented: $showingPicker,
                      allowedContentTypes: [.folder],
                      allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first {
                parent = url
            }
        }
    }
}

private struct StatBlock: View {
    let label: String
    let value: Int

    var body: some View {
        VStack {
            Text("\(value)")
                .font(.system(size: 30, weight: .semibold))
                .monospacedDigit()
            Text(label.uppercased())
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(minWidth: 90)
    }
}
