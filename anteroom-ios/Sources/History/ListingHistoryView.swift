import SwiftUI

struct ListingHistoryView: View {
    let api: AnteroomAPI
    let listingID: String
    @Environment(\.dismiss) private var dismiss
    @State private var revisions: [ListingRevision] = []
    @State private var currentID: String?
    @State private var restoring = false
    @State private var loading = true
    @State private var error: String?

    var body: some View {
        NavigationStack {
            List(revisions) { revision in
                NavigationLink(value: revision) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(revision.title.isEmpty ? "Untitled listing" : revision.title)
                            .font(.body)
                        if let date = ServerDate.parse(revision.createdAt) {
                            Text(date, format: .dateTime.month().day().year().hour().minute())
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        if revision.id == currentID {
                            Label("Current version", systemImage: "checkmark.circle")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, 6)
                    .accessibilityElement(children: .combine)
                }
            }
            .overlay { loadState }
            .navigationTitle("Listing history")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }.disabled(restoring)
                }
            }
            .interactiveDismissDisabled(restoring)
            .refreshable { await load() }
            .task { await load() }
            .navigationDestination(for: ListingRevision.self) { revision in
                RevisionPreviewView(api: api, listingID: listingID, revision: revision,
                                    currentID: currentID, onRestored: { dismiss() }, restoring: $restoring)
            }
        }
    }

    @ViewBuilder
    private var loadState: some View {
        if loading && revisions.isEmpty {
            ProgressView("Loading history…")
        } else if let error {
            ContentUnavailableView {
                Label("Couldn't load history", systemImage: "exclamationmark.triangle")
            } description: {
                Text(error)
            } actions: {
                Button("Try again") { Task { await load() } }.disabled(loading)
            }
        } else if revisions.isEmpty {
            ContentUnavailableView("No saved versions yet", systemImage: "clock.arrow.circlepath",
                                   description: Text("Save or generate a listing to start its history."))
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            async let history = api.revisions(listingID)
            async let pointer = api.revisionPointer(listingID)
            let values = try await (history, pointer)
            try Task.checkCancellation()
            revisions = values.0
            currentID = values.1.currentRevisionID
            error = nil
        } catch is CancellationError {
        } catch {
            self.error = error.localizedDescription
        }
    }
}

private struct RevisionPreviewView: View {
    let api: AnteroomAPI
    let listingID: String
    let revision: ListingRevision
    let currentID: String?
    let onRestored: () -> Void
    @State private var snapshot: RevisionSnapshot?
    @State private var loading = true
    @Binding var restoring: Bool
    @State private var error: String?
    @State private var confirmRestore = false
    @State private var conflict = false

    var body: some View {
        Form {
            Section {
                Text(revision.title.isEmpty ? "Untitled listing" : revision.title)
                    .font(.headline).textSelection(.enabled)
                if let date = ServerDate.parse(revision.createdAt) {
                    Text(date, format: .dateTime.month().day().year().hour().minute())
                        .foregroundStyle(.secondary)
                }
            }
            if loading {
                ProgressView("Loading version…")
            } else if let snapshot {
                Section("Listing details") {
                    ForEach(Self.previewFields, id: \.key) { field in
                        if let text = snapshot.listing[field.key]?.displayText {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(field.label).font(.footnote).foregroundStyle(.secondary)
                                Text(text).textSelection(.enabled)
                            }
                            .accessibilityElement(children: .combine)
                        }
                    }
                }
                Section {
                    Button(revision.id == currentID ? "Current version" : "Restore this version") {
                        confirmRestore = true
                    }
                    .disabled(revision.id == currentID || restoring || currentID == nil || conflict)
                    if restoring { ProgressView("Restoring…") }
                } footer: {
                    Text("Restores the complete saved listing, including marketplace details. Photos and private notes are not restored. Nothing is published.")
                }
            }
            if let error {
                Section {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                    if conflict {
                        Text("Go back and pull to refresh history before choosing a version again.")
                    } else if snapshot == nil {
                        Button("Try again") { Task { await load() } }
                    }
                }
            }
        }
        .navigationTitle("Saved version")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(restoring)
        .interactiveDismissDisabled(restoring)
        .task { await load() }
        .confirmationDialog("Restore this listing version?", isPresented: $confirmRestore, titleVisibility: .visible) {
            Button("Restore version") { Task { await restore() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("A new saved version will be created from this snapshot. Your current version remains in history.")
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            snapshot = try await api.revision(listingID, revisionID: revision.id)
            error = nil
        } catch is CancellationError {
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func restore() async {
        guard !restoring, !conflict, let currentID else { return }
        restoring = true
        defer { restoring = false }
        do {
            try await api.restoreRevision(listingID, revisionID: revision.id, expectedRevisionID: currentID)
            onRestored()
        } catch let failure as APIError where failure.isConflict {
            conflict = true
            error = failure.localizedDescription
        } catch {
            self.error = error.localizedDescription
        }
    }

    // Labels and order are generated from the canonical General-field contract.
    private static let previewFields: [(key: String, label: String)] = [
        ("title", "Title"),
        ("description", "Description"),
        ("price", "Price"),
        ("cost", "Cost"),
        ("quantity", "Quantity"),
        ("brand", "Brand"),
        ("condition", "Condition"),
        ("primaryColor", "Primary Color"),
        ("secondaryColor", "Secondary Color"),
        ("size", "Size"),
        ("sku", "SKU"),
        ("zipCode", "Inventory location"),
        ("category_path", "Category *"),
        ("department", "Department"),
        ("weight_oz", "Package Weight (oz)"),
        ("package_dimensions_in", "Package Dimensions (LxWxH in)")
    ]
}
