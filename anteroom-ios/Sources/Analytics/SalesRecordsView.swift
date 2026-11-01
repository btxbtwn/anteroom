import SwiftUI

/// Analytics → Sales records (SalesLedger.tsx): record amounts, fees,
/// refunds and costs per marketplace order line, and export CSVs.
struct SalesRecordsView: View {
    let api: AnteroomAPI

    private static let markets = ["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild", "vinted", "facebook", "other"]

    @State private var start = Calendar.current.date(byAdding: .day, value: -29, to: Date()) ?? Date()
    @State private var end = Date()
    @State private var marketplace = "all"
    @State private var page = 1
    @State private var data: SalesLedgerPage?
    @State private var loading = false
    @State private var error: String?
    @State private var editing: SalesLedgerRow?
    @State private var export: URL?

    var body: some View {
        List {
            Section {
                DatePicker("Sales from (UTC)", selection: $start, in: ...end, displayedComponents: .date)
                DatePicker("Through (UTC)", selection: $end, in: start...Date(), displayedComponents: .date)
                Picker("Marketplace", selection: $marketplace) {
                    Text("All marketplaces").tag("all")
                    ForEach(Self.markets, id: \.self) { Text(Marketplaces.name($0)).tag($0) }
                }
                Menu("Export CSV") {
                    Button("Export inventory CSV") { exportCSV(inventory: true) }
                    Button("Export sales CSV") { exportCSV(inventory: false) }
                }
            } footer: {
                Text("Record sales amounts, fees, refunds and costs in Anteroom. Export your inventory and sales from here.")
            }

            if let data {
                Section {
                    Text("\(data.summary.lines) order lines · \(data.summary.unrecorded) without recorded amounts · \(data.summary.needsReview) need review · \(data.summary.unmatched) need an inventory link. Totals cover the full filtered period; currencies stay separate.")
                        .font(.footnote).foregroundStyle(.secondary)
                    ForEach(data.summary.currencies) { group in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(group.currency).font(.subheadline.weight(.medium))
                            Text("Net revenue: \(SalesLedgerFormat.money(group.netRevenue, group.currency)) · known on \(group.netKnown) of \(group.lines) lines")
                            Text("Recorded profit: \(SalesLedgerFormat.money(group.profit, group.currency)) · known on \(group.profitKnown) of \(group.lines) lines")
                        }
                        .font(.caption)
                    }
                }
                Section {
                    if data.rows.isEmpty {
                        Text("No recorded orders in this period. Recover orders from a marketplace's Orders tab to add missing sales.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    ForEach(data.rows) { row in recordRow(row) }
                    if data.page > 1 || data.hasMore {
                        HStack {
                            Button("Previous") { page -= 1 }.disabled(page == 1 || loading)
                            Spacer()
                            Text("Page \(page)").font(.footnote)
                            Spacer()
                            Button("Next") { page += 1 }.disabled(!data.hasMore || loading)
                        }
                        .buttonStyle(.borderless)
                    }
                } header: {
                    Text("Sales records")
                } footer: {
                    Text("One record per marketplace order line, including dismissed alerts and cancellation/refund updates. Record line totals after discounts. Blank amounts stay unknown; enter zero when confirmed.")
                }
            } else if loading {
                ProgressView("Loading sales records…")
            }
            if let error {
                Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
            }
        }
        .refreshable { await load() }
        .task(id: "\(start)|\(end)|\(marketplace)|\(page)") { await load() }
        .onChange(of: marketplace) { page = 1 }
        .onChange(of: start) { page = 1 }
        .onChange(of: end) { page = 1 }
        .sheet(item: $editing) { row in
            SaleAmountsSheet(api: api, row: row) { Task { await load() } }
        }
        .sheet(item: Binding(get: { export.map(ExportFile.init) }, set: { export = $0?.url })) { file in
            ShareSheet(items: [file.url])
        }
    }

    private func recordRow(_ row: SalesLedgerRow) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(row.title ?? "\(Marketplaces.name(row.marketplace)) order \(row.orderID)").font(.subheadline.weight(.medium))
            Text("\(Marketplaces.name(row.marketplace)) · \(row.orderStatus.replacingOccurrences(of: "_", with: " ")) · quantity \(row.quantity.map(String.init) ?? "unknown")")
                .font(.caption).foregroundStyle(.secondary)
            Text("Order \(row.orderID) · line \(row.lineID)").font(.caption).foregroundStyle(.secondary)
            Text("Profit: \(SalesLedgerFormat.money(row.profit, row.currency))").font(.caption)
            if row.financialsStale {
                Text("Order changed or recorded amounts need review.").font(.caption).foregroundStyle(.orange)
            }
            HStack {
                if let id = row.conversationID {
                    NavigationLink("Open item", value: ListingSummary(id: id, title: row.title))
                        .font(.footnote)
                }
                Spacer()
                Button("Record amounts") { editing = row }.font(.footnote).buttonStyle(.bordered)
            }
        }
        .padding(.vertical, 4)
    }

    private static func day(_ date: Date) -> String {
        // The day the seller picked; the server reads it as a UTC day, as the desktop does.
        let parts = Calendar.current.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 1970, parts.month ?? 1, parts.day ?? 1)
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            data = try await api.salesLedger(start: Self.day(start), end: Self.day(end),
                                             marketplace: marketplace == "all" ? nil : marketplace, page: page)
            error = nil
        } catch is CancellationError {
        } catch let failure as URLError where failure.code == .cancelled {
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func exportCSV(inventory: Bool) {
        Task {
            do {
                if inventory {
                    export = try await api.exportCSV("api/sales-ledger/export/inventory.csv", filename: "anteroom-inventory.csv")
                } else {
                    var query = [URLQueryItem(name: "start", value: Self.day(start)), URLQueryItem(name: "end", value: Self.day(end))]
                    if marketplace != "all" { query.append(URLQueryItem(name: "marketplace", value: marketplace)) }
                    export = try await api.exportCSV("api/sales-ledger/export/sales.csv", query: query, filename: "anteroom-sales.csv")
                }
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

private struct ExportFile: Identifiable {
    let url: URL
    var id: String { url.path }
}

private struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

/// Record sale amounts for one order line (FinancialEditor).
private struct SaleAmountsSheet: View {
    let api: AnteroomAPI
    @State var row: SalesLedgerRow
    let onSaved: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var currency = ""
    @State private var values: [String: String] = [:]
    @State private var working = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Enter totals for this order line, excluding buyer taxes. Blank is unknown; zero is a confirmed zero. Net fees may be negative for fee credits. Saving records your figures; quantities and marketplace listings stay unchanged.")
                        .font(.footnote).foregroundStyle(.secondary)
                    if row.financialsStale {
                        Text("This order changed. Review every amount before saving again.").font(.footnote).foregroundStyle(.orange)
                    }
                }
                Section {
                    LabeledContent("Currency code") {
                        TextField("USD", text: Binding(get: { currency }, set: { currency = String($0.uppercased().prefix(3)) }))
                            .multilineTextAlignment(.trailing)
                            .textInputAutocapitalization(.characters)
                    }
                    ForEach(SalesLedgerRow.fields, id: \.self) { field in
                        LabeledContent(SalesLedgerRow.labels[field] ?? field) {
                            TextField("Unknown", text: Binding(get: { values[field] ?? "" }, set: { values[field] = $0 }))
                                .keyboardType(.numbersAndPunctuation)
                                .multilineTextAlignment(.trailing)
                        }
                    }
                } footer: {
                    Text("\(Marketplaces.name(row.marketplace)) order \(row.orderID) · line \(row.lineID) · quantity \(row.quantity.map(String.init) ?? "unknown") · \(row.orderStatus.replacingOccurrences(of: "_", with: " "))")
                }
                Section { Button("Reload record") { reload() }.disabled(working) }
                if let error {
                    Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
                }
            }
            .navigationTitle("Record sale amounts")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(working) }
                ToolbarItem(placement: .confirmationAction) {
                    if working { ProgressView() } else {
                        Button("Save") { save() }.fontWeight(.semibold)
                            .disabled(currency.count != 3 || !currency.allSatisfy(\.isLetter))
                    }
                }
            }
            .onAppear { fill(row) }
        }
        .interactiveDismissDisabled(working)
    }

    private func fill(_ record: SalesLedgerRow) {
        currency = record.currency ?? ""
        values = Dictionary(uniqueKeysWithValues: SalesLedgerRow.fields.map { ($0, (record.financials?[$0] ?? nil) ?? "") })
    }

    private func save() {
        working = true
        error = nil
        let entered = Dictionary(uniqueKeysWithValues: SalesLedgerRow.fields.map { field -> (String, String?) in
            let text = (values[field] ?? "").trimmingCharacters(in: .whitespaces)
            return (field, text.isEmpty ? nil : text)
        })
        Task {
            defer { working = false }
            do {
                try await api.saveSaleAmounts(row.id, currency: currency, values: entered,
                                              revision: row.financialRevision, basis: row.orderBasis)
                onSaved()
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    private func reload() {
        working = true
        Task {
            defer { working = false }
            do {
                row = try await api.salesRecord(row.id)
                fill(row)
                error = nil
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
