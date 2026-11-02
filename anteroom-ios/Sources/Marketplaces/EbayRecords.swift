import SwiftUI

/// Where a record opens: the conversation, order, dispute or inventory item behind it.
enum EbayDestination: Hashable {
    case conversation(EbayRow)
    case order(EbayRow)
    case dispute(EbayRow)
    case offers(EbayRow)
}

/// One eBay record as the web's RowCards draws it: title and ID, status,
/// amount, description, fields and line items.
struct EbayRecordCard: View {
    let row: EbayRow

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.title.isEmpty ? row.id : row.title).font(.subheadline.weight(.semibold))
                    Text("ID \(row.id)").font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                }
                Spacer(minLength: 8)
                VStack(alignment: .trailing, spacing: 4) {
                    if let amount = row.amount { Text("$\(amount)").font(.subheadline.weight(.semibold)).monospacedDigit() }
                    if !row.status.isEmpty {
                        Text(EbayWorkspaceFormat.status(row.status))
                            .font(.caption).padding(.horizontal, 8).padding(.vertical, 2)
                            .background(WorkspaceStyle.border, in: Capsule())
                    }
                }
            }
            if !row.subtitle.isEmpty {
                Text(row.subtitle).font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(row.fields, id: \.key) { field in
                if field.key == "url" {
                    if let link = EbayWorkspaceFormat.ebayLink(field.value) {
                        Link("View on eBay", destination: link).font(.footnote)
                    }
                } else {
                    HStack(alignment: .firstTextBaseline) {
                        Text(EbayWorkspaceFormat.label(field.key)).foregroundStyle(.secondary)
                        Spacer(minLength: 12)
                        Text(field.value ?? "Not available").multilineTextAlignment(.trailing)
                    }
                    .font(.footnote)
                    .accessibilityElement(children: .combine)
                }
            }
            if !row.children.isEmpty {
                Divider()
                ForEach(row.children) { child in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(child.title).font(.footnote.weight(.medium))
                        Text(child.fields.map { "\($0.key): \($0.value ?? "—")" }.joined(separator: " · "))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(.vertical, 4)
    }
}

/// A section read from eBay, a page at a time, with Refresh (RemoteList).
/// Records open `link`'s destination when it gives one.
struct EbayRemoteList: View {
    let api: AnteroomAPI
    let section: String
    var params: [String: String] = [:]
    var link: ((EbayRow) -> EbayDestination?)? = nil
    /// The first page as it loads, for totals drawn above the list.
    var onFirstPage: ((EbayPage) -> Void)? = nil

    @State private var offset = 0
    @State private var page: EbayPage?
    @State private var loading = false
    @State private var error: String?

    var body: some View {
        Group {
            HStack {
                Text(page.map { EbayWorkspaceFormat.count(total: $0.total, shown: $0.rows.count) } ?? (loading ? "Loading…" : ""))
                    .font(.footnote).foregroundStyle(.secondary)
                Spacer()
                Button("Refresh", systemImage: "arrow.clockwise") { Task { await load() } }
                    .font(.footnote)
                    .disabled(loading)
                    .accessibilityLabel("Refresh \(section)")
            }
            if let error {
                Label(error, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.red)
            }
            if let page {
                if page.rows.isEmpty {
                    Label("No items to show here yet.", systemImage: "tray").font(.footnote).foregroundStyle(.secondary)
                }
                ForEach(page.rows) { row in
                    if let destination = link?(row) {
                        NavigationLink(value: destination) { EbayRecordCard(row: row) }
                    } else {
                        EbayRecordCard(row: row)
                    }
                }
                if offset > 0 || page.nextOffset != nil {
                    HStack {
                        Button("Previous", systemImage: "chevron.left") {
                            offset = max(0, offset - EbayWorkspaceFormat.pageSize(section))
                        }
                        .disabled(offset == 0 || loading)
                        Spacer()
                        Text("Page \(offset / EbayWorkspaceFormat.pageSize(section) + 1)").font(.footnote).foregroundStyle(.secondary)
                        Spacer()
                        Button("Next", systemImage: "chevron.right") {
                            if let next = page.nextOffset { offset = next }
                        }
                        .labelStyle(.titleAndIcon)
                        .disabled(page.nextOffset == nil || loading)
                    }
                    .buttonStyle(.borderless)
                    .font(.footnote)
                }
            } else if loading {
                HStack(spacing: 8) { ProgressView(); Text("Loading your eBay data…").font(.footnote).foregroundStyle(.secondary) }
            }
        }
        .task(id: "\(section)|\(params.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }.joined(separator: "&"))|\(offset)") {
            await load()
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            let next = try await api.ebayRead(section, params: params, offset: offset)
            page = next
            error = nil
            if offset == 0 { onFirstPage?(next) }
        } catch is CancellationError {
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// A conversation, an order, a dispute, or an inventory item's offers.
struct EbayRecordDetail: View {
    let api: AnteroomAPI
    let destination: EbayDestination

    var body: some View {
        List {
            switch destination {
            case .conversation(let row):
                Section("Messages") { EbayRemoteList(api: api, section: "messages", params: ["target": row.id]) }
            case .order(let row):
                Section("Order") { EbayRemoteList(api: api, section: "order", params: ["target": row.id]) }
            case .dispute(let row):
                Section("Dispute") { EbayRemoteList(api: api, section: "dispute", params: ["target": row.id]) }
            case .offers(let row):
                Section("Offers for \(row.id)") { EbayRemoteList(api: api, section: "offers", params: ["target": row.id]) }
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }

    private var title: String {
        switch destination {
        case .conversation(let row): return row.title.isEmpty ? "Conversation" : row.title
        case .order(let row): return "Order \(row.id)"
        case .dispute(let row): return "Dispute \(row.id)"
        case .offers(let row): return row.title.isEmpty ? row.id : row.title
        }
    }
}
