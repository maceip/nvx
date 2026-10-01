import SwiftUI

/// The glass box: live guest console beside the host denial feed,
/// with the attempted-vs-denied verdict on top.
struct LiveView: View {
    @Bindable var controller: RunController

    var body: some View {
        VStack(spacing: 0) {
            verdictBanner
            Divider()
            HSplitView {
                consolePane
                    .frame(minWidth: 400)
                eventsPane
                    .frame(minWidth: 280, idealWidth: 340)
            }
        }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
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
        .navigationTitle(controller.payload.name)
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
        case .info: "info.circle"
        }
    }

    private func eventColor(for kind: GuestEvent.Kind) -> Color {
        switch kind {
        case .denied, .probeFailed: .red
        case .probeSucceeded, .boot: .green
        default: .secondary
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
