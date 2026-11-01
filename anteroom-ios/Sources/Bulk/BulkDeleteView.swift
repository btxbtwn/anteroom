import SwiftUI

/// Remove selected items from Anteroom after reviewing each one
/// (BulkDeleteDialog.tsx). Nothing is delisted from any marketplace.
struct BulkDeleteView: View {
    let api: AnteroomAPI
    let ids: [String]
    let onFinished: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var rows: [BulkDeleteReviewRow] = []
    @State private var unchecked: Set<String> = []
    @State private var loaded = false
    @State private var working = false
    @State private var confirming = false
    @State private var result: BulkDeleteResult?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            List {
                if let result {
                    Section {
                        Label("\(result.deleted.count) items deleted", systemImage: "checkmark.circle")
                    }
                    if !result.skipped.isEmpty {
                        Section("Not deleted") {
                            ForEach(result.skipped, id: \.conversationID) { skipped in
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(title(skipped.conversationID))
                                    Text(skipped.reason).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                } else if loaded {
                    Section {
                        ForEach(rows) { row in reviewRow(row) }
                    } footer: {
                        Text("Deleting removes items and their photos from Anteroom only. Nothing is delisted from any marketplace.")
                    }
                }
                if let error {
                    Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
                }
            }
            .overlay { if !loaded && error == nil { ProgressView() } }
            .navigationTitle(result == nil ? "Delete \(ids.count) items" : "Delete")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    if result == nil { Button("Cancel") { dismiss() }.disabled(working) }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if working {
                        ProgressView()
                    } else if result != nil {
                        Button("Done") {
                            onFinished()
                            dismiss()
                        }.fontWeight(.semibold)
                    } else {
                        Button("Delete \(selected.count)", role: .destructive) { confirming = true }
                            .fontWeight(.semibold)
                            .disabled(selected.isEmpty)
                    }
                }
            }
            .confirmationDialog("Delete \(selected.count) items from Anteroom?", isPresented: $confirming,
                                titleVisibility: .visible) {
                Button("Delete \(selected.count) items", role: .destructive) { delete() }
            } message: {
                Text("This can't be undone. Nothing is delisted.")
            }
            .task { await review() }
        }
        .interactiveDismissDisabled(working)
    }

    private var selected: [BulkDeleteReviewRow] {
        rows.filter { $0.deletable && !unchecked.contains($0.id) }
    }

    private func title(_ id: String) -> String {
        rows.first { $0.id == id }?.title ?? "Item"
    }

    private func reviewRow(_ row: BulkDeleteReviewRow) -> some View {
        Button {
            guard row.deletable else { return }
            if unchecked.contains(row.id) { unchecked.remove(row.id) } else { unchecked.insert(row.id) }
        } label: {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: !row.deletable ? "minus.circle" : unchecked.contains(row.id) ? "circle" : "checkmark.circle.fill")
                    .foregroundStyle(row.deletable ? WorkspaceStyle.accent : .secondary)
                    .font(.title3)
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.title ?? "Untitled item").foregroundStyle(.primary)
                    if let reason = row.skipReason {
                        Text(reason).font(.caption).foregroundStyle(.secondary)
                    } else if let warning = row.warning {
                        Text(warning).font(.caption).foregroundStyle(.orange)
                    }
                }
            }
            .frame(minHeight: 44)
        }
        .disabled(!row.deletable)
        .accessibilityAddTraits(row.deletable && !unchecked.contains(row.id) ? .isSelected : [])
    }

    private func review() async {
        do {
            rows = try await api.previewBulkDelete(ids: ids)
            loaded = true
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func delete() {
        working = true
        error = nil
        Task {
            defer { working = false }
            do {
                result = try await api.bulkDelete(rows: selected)
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
