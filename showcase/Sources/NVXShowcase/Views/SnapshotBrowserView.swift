import SwiftUI
import UniformTypeIdentifiers

/// The time machine: browse snapshot generations, inspect the frozen
/// moment, and resume it.
struct SnapshotBrowserView: View {
    @State private var store = SnapshotStore()
    @State private var showingPicker = false

    var body: some View {
        NavigationSplitView {
            List(selection: $store.selection) {
                Section("Generations") {
                    ForEach(store.snapshots) { snap in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(snap.url.lastPathComponent)
                                .font(.headline)
                            Text(ByteCountFormatter.string(fromByteCount: snap.totalBytes,
                                                           countStyle: .file))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .tag(snap)
                    }
                }
            }
            .navigationSplitViewColumnWidth(min: 220, ideal: 260)
            .toolbar {
                ToolbarItem {
                    Button("Choose…", systemImage: "folder") {
                        showingPicker = true
                    }
                }
            }
        } detail: {
            if let snap = store.selection {
                SnapshotDetailView(snapshot: snap, store: store)
            } else {
                ContentUnavailableView(
                    "No snapshots",
                    systemImage: "clock.arrow.circlepath",
                    description: Text("Choose a directory containing snapshot generations.")
                )
            }
        }
        .navigationTitle("Snapshots")
        .fileImporter(isPresented: $showingPicker,
                      allowedContentTypes: [.folder],
                      allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first {
                store.rootURL = url
            }
        }
        .onAppear {
            if store.rootURL == nil {
                store.rootURL = URL(fileURLWithPath: "/tmp")
            }
        }
    }
}

private struct SnapshotDetailView: View {
    let snapshot: SnapshotInfo
    @Bindable var store: SnapshotStore

    var body: some View {
        HSplitView {
            VStack(alignment: .leading, spacing: 0) {
                paneHeader("Frozen moment", systemImage: "snowflake")
                Form {
                    LabeledContent("Directory", value: snapshot.url.path)
                        .textSelection(.enabled)
                    LabeledContent("Files", value: "\(snapshot.files.count)")
                    LabeledContent("Total", value: ByteCountFormatter.string(
                        fromByteCount: snapshot.totalBytes, countStyle: .file))
                }
                .formStyle(.grouped)
                .frame(minWidth: 300)

                paneHeader("Manifest", systemImage: "doc.text.magnifyingglass")
                ScrollView {
                    Text(store.verifyOutput.isEmpty
                         ? "Press Verify to inspect the manifest."
                         : store.verifyOutput)
                        .font(.system(size: 12, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                }
                HStack {
                    Button("Verify") { store.verify(snapshot) }
                    Text(store.verifyState.rawValue)
                        .font(.callout)
                        .foregroundStyle(store.verifyState == .ok ? .green
                            : store.verifyState == .failed ? .red : .secondary)
                    Spacer()
                    if store.resumeRunning {
                        ProgressView().controlSize(.small)
                    } else {
                        Button("Resume this moment", systemImage: "play.fill") {
                            store.resume(snapshot)
                        }
                    }
                }
                .padding(8)
            }
            .frame(minWidth: 380)

            VStack(alignment: .leading, spacing: 0) {
                paneHeader("Artifacts", systemImage: "archivebox")
                Table(snapshot.files, selection: .constant(nil)) {
                    TableColumn("File", value: \.name)
                    TableColumn("Size") { row in
                        Text(ByteCountFormatter.string(fromByteCount: row.size,
                                                       countStyle: .file))
                            .monospacedDigit()
                    }
                    .width(110)
                }
                if !store.resumeOutput.isEmpty {
                    paneHeader("Resume output", systemImage: "terminal")
                    ScrollView {
                        Text(store.resumeOutput)
                            .font(.system(size: 11, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(8)
                    }
                    .frame(minHeight: 140)
                }
            }
            .frame(minWidth: 300)
        }
        .onChange(of: snapshot) {
            store.verifyOutput = ""
            store.verifyState = .idle
            store.resumeOutput = ""
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
}
