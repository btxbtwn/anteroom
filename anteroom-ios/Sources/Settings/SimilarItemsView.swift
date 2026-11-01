import SwiftUI

struct SimilarItem: Decodable, Identifiable, Sendable {
    let id: String
    let title: String
    let status: String
    let brand: String?
    let size: String?
    let price: Double?
    let sku: String?
    let coverURL: String?

    enum CodingKeys: String, CodingKey {
        case id, title, status, brand, size, price, sku
        case coverURL = "cover_url"
    }

    var meta: String {
        [status.prefix(1).uppercased() + status.dropFirst(), brand, size.map { "Size \($0)" },
         price.map(AnalyticsFormat.money), sku.map { "SKU \($0)" }]
            .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }

    var listing: ListingSummary { ListingSummary(id: id, title: title, coverPhotoURL: coverURL) }
}

struct SimilarPair: Decodable, Identifiable, Sendable {
    let first: SimilarItem
    let second: SimilarItem
    let reasons: [String]
    var id: String { first.id + "|" + second.id }
}

/// Settings → Listings → Similar items (SimilarItemsButton.tsx): likely
/// duplicates to merge or mark as different. Nothing is deleted or delisted.
struct SimilarItemsView: View {
    let api: AnteroomAPI

    private static let reasons = ["same_photo": "Same photo", "same_sku": "Same SKU",
                                  "similar_title": "Similar title", "similar_photo": "Cover photos look alike"]

    @State private var pairs: [SimilarPair]?
    @State private var total = 0
    @State private var busy = false
    @State private var merging: (keep: SimilarItem, other: SimilarItem)?
    @State private var error: String?

    var body: some View {
        List {
            Section {
                Text("Items that share a photo or SKU, or whose titles match with the same brand and size. Open them to compare, keep one and merge the other into it, or mark them as different.")
                    .font(.footnote).foregroundStyle(.secondary)
                if let pairs, total > pairs.count {
                    Text("Showing the \(pairs.count) closest of \(total) pairs.").font(.footnote).foregroundStyle(.secondary)
                }
            }
            if let error {
                Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
            }
            if let pairs {
                if pairs.isEmpty {
                    Text("No similar items found.").foregroundStyle(.secondary)
                }
                ForEach(pairs) { pair in
                    Section {
                        ForEach([(pair.first, pair.second), (pair.second, pair.first)], id: \.0.id) { item, other in
                            VStack(alignment: .leading, spacing: 6) {
                                NavigationLink(value: item.listing) {
                                    HStack(spacing: 12) {
                                        Group {
                                            if let cover = item.listing.coverPhotoID {
                                                RemotePhoto(api: api, photoID: cover, maxPixels: 120)
                                            } else {
                                                Image(systemName: "photo").foregroundStyle(.secondary)
                                            }
                                        }
                                        .frame(width: 48, height: 48)
                                        .clipShape(RoundedRectangle(cornerRadius: 6))
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(item.title).lineLimit(2)
                                            Text(item.meta).font(.caption).foregroundStyle(.secondary)
                                        }
                                    }
                                }
                                Button("Keep this one") { merging = (item, other) }
                                    .buttonStyle(.bordered).font(.footnote).disabled(busy)
                            }
                        }
                        Button("Not duplicates") { dismiss(pair) }.disabled(busy)
                    } header: {
                        Text(pair.reasons.map { Self.reasons[$0] ?? $0 }.joined(separator: " · "))
                    }
                }
            } else if error == nil {
                ProgressView("Comparing titles, SKUs and photos…")
            }
        }
        .navigationTitle("Similar items")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task { await load() }
        .navigationDestination(for: ListingSummary.self) { listing in
            ListingEditorView(api: api, listing: listing) {}
        }
        .confirmationDialog(mergeTitle, isPresented: Binding(get: { merging != nil }, set: { if !$0 { merging = nil } }),
                            titleVisibility: .visible) {
            Button("Merge") {
                if let merging { merge(keep: merging.keep, other: merging.other) }
            }
        } message: {
            Text("Its marketplace links, sales and labels move to the item you keep, and it moves to Finished. Nothing is deleted or delisted.")
        }
    }

    private var mergeTitle: String {
        guard let merging else { return "" }
        return "Keep \"\(merging.keep.title)\" and merge \"\(merging.other.title)\" into it?"
    }

    private func load() async {
        do {
            let result = try await api.similarItems()
            pairs = result.pairs
            total = result.total
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func dismiss(_ pair: SimilarPair) {
        run { try await api.dismissSimilar(pair.first.id, pair.second.id) }
    }

    private func merge(keep: SimilarItem, other: SimilarItem) {
        run { try await api.mergeSimilar(keep: keep.id, merge: other.id) }
    }

    private func run(_ change: @escaping () async throws -> Void) {
        busy = true
        Task {
            defer { busy = false }
            do {
                try await change()
                await load()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
