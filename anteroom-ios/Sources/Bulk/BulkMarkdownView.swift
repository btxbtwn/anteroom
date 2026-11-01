import SwiftUI

/// Reviewed price reductions, suggested from the seller's own sales
/// (BulkPriceDropDialog.tsx). Prices save in Anteroom only; nothing is sent
/// to a marketplace.
struct BulkMarkdownView: View {
    let api: AnteroomAPI
    let ids: [String]
    let onFinished: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var rows: [MarkdownReviewRow] = []
    @State private var prices: [String: String] = [:]
    @State private var excluded: Set<String> = []
    @State private var loaded = false
    @State private var working = false
    @State private var result: MarkdownResult?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                if let result {
                    Section {
                        Label("\(result.applied.count) prices saved", systemImage: "checkmark.circle")
                        ForEach(result.applied, id: \.conversationID) { applied in
                            LabeledContent(rows.first { $0.id == applied.conversationID }?.title ?? "Listing",
                                           value: applied.price, format: .currency(code: "USD"))
                        }
                    }
                    if !result.skipped.isEmpty {
                        Section("Not changed") {
                            ForEach(result.skipped, id: \.conversationID) { skipped in
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(rows.first { $0.id == skipped.conversationID }?.title ?? "Listing")
                                    Text(skipped.reason).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                } else if loaded {
                    Section {
                        Text("Suggested from your own sales. Prices save in Anteroom; nothing is sent to a marketplace.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    ForEach(rows) { row in markdownSection(row) }
                }
                if let error {
                    Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
                }
            }
            .overlay { if !loaded && error == nil { ProgressView() } }
            .navigationTitle(result == nil ? "Mark down prices" : "Markdowns")
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
                        Button("Save \(drops.count)") { apply() }
                            .fontWeight(.semibold)
                            .disabled(drops.isEmpty || invalid)
                    }
                }
            }
            .task { await load() }
        }
        .interactiveDismissDisabled(working)
    }

    @ViewBuilder
    private func markdownSection(_ row: MarkdownReviewRow) -> some View {
        Section {
            Toggle(row.title, isOn: Binding(get: { !excluded.contains(row.id) },
                                            set: { if $0 { excluded.remove(row.id) } else { excluded.insert(row.id) } }))
                .fontWeight(.medium)
                .disabled(row.skipReason != nil || row.revisionID == nil)
            if let reason = row.skipReason {
                Text(reason).font(.footnote).foregroundStyle(.secondary)
            } else if !excluded.contains(row.id) {
                if let current = row.currentPrice {
                    LabeledContent("Current price", value: current, format: .currency(code: "USD"))
                }
                LabeledContent("New price") {
                    HStack(spacing: 6) {
                        TextField("0", text: Binding(get: { prices[row.id] ?? GeneralFields.text(row.suggestedPrice) },
                                                     set: { prices[row.id] = $0 }))
                            .keyboardType(.numberPad)
                            .multilineTextAlignment(.trailing)
                            .accessibilityLabel("New price for \(row.title)")
                        if let current = row.currentPrice, current > 0, let price = price(row) {
                            Text("\(Int(((price - current) / current * 100).rounded()))%")
                                .monospacedDigit().foregroundStyle(.secondary)
                        }
                    }
                }
                if let reason = row.suggestedReason, !reason.isEmpty {
                    Text(reason).font(.footnote).foregroundStyle(.secondary)
                }
                if price(row) == nil {
                    Text("Enter a whole-dollar price of 1 or more.").font(.footnote).foregroundStyle(.red)
                }
            }
        }
    }

    private func price(_ row: MarkdownReviewRow) -> Double? {
        // Whole dollars, as the desktop's markdown input takes them.
        let text = (prices[row.id] ?? GeneralFields.text(row.suggestedPrice))
            .replacingOccurrences(of: "$", with: "").trimmingCharacters(in: .whitespaces)
        guard let value = Int(text), (1...1_000_000).contains(value) else { return nil }
        return Double(value)
    }

    private var included: [MarkdownReviewRow] {
        rows.filter { $0.skipReason == nil && $0.revisionID != nil && !excluded.contains($0.id) }
    }

    private var invalid: Bool { included.contains { price($0) == nil } }

    private var drops: [MarkdownDrop] {
        included.compactMap { row in
            guard let price = price(row), let revision = row.revisionID else { return nil }
            return MarkdownDrop(conversationID: row.id, price: price, expectedRevisionID: revision)
        }
    }

    private func load() async {
        do {
            rows = try await api.previewMarkdowns(ids: ids)
            loaded = true
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func apply() {
        working = true
        error = nil
        Task {
            defer { working = false }
            do {
                result = try await api.applyMarkdowns(drops)
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
