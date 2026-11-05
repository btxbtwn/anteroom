import SwiftUI

/// Analytics → Sales records (SalesLedger.tsx): record amounts, fees,
/// refunds and costs per marketplace order line, and export CSVs.
struct SalesRecordsView: View {
    let api: AnteroomAPI

    private static let markets = ["ebay", "etsy", "poshmark", "mercari", "depop", "other"]

    @State private var start = Calendar.current.date(byAdding: .day, value: -29, to: Date()) ?? Date()
    @State private var end = Date()
    @State private var marketplace = "all"
    @State private var page = 1
    @State private var data: SalesLedgerPage?
    @State private var overview: SalesOverview?
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
                Text("How your sales are doing, and every sale with its price, fees and profit.")
            }

            if let overview {
                Section {
                    SalesOverviewSection(overview: overview, marketplaceChosen: marketplace != "all")
                        .listRowInsets(EdgeInsets(top: 8, leading: 0, bottom: 8, trailing: 0))
                        .listRowBackground(Color.clear)
                }
            }

            if let data {
                Section {
                    Text("\(data.summary.lines) sale records · \(data.summary.unrecorded) without recorded amounts · \(data.summary.needsReview) need review · \(data.summary.unmatched) need an inventory link.")
                        .font(.footnote).foregroundStyle(.secondary)
                    // Dollar totals are in the figures above; other currencies keep their own.
                    ForEach(data.summary.currencies.filter { $0.currency != "USD" }) { group in
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Sales in \(group.currency)").font(.subheadline.weight(.medium))
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
                    Text("Record totals after discounts; leave an amount blank if unknown.")
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
            if !row.missingSaleData.isEmpty {
                Text("Missing: \(row.missingSaleData.joined(separator: ", "))").font(.caption).foregroundStyle(.orange)
            }
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
            let market = marketplace == "all" ? nil : marketplace
            async let figures = api.salesOverview(start: Self.day(start), end: Self.day(end), marketplace: market)
            data = try await api.salesLedger(start: Self.day(start), end: Self.day(end), marketplace: market, page: page)
            overview = try await figures
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
    @State private var soldOn = ""
    @State private var values: [String: String] = [:]
    @State private var working = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Sold on \(Marketplaces.name(row.marketplace)) · quantity \(row.quantity.map(String.init) ?? "unknown") · \(row.orderStatus.replacingOccurrences(of: "_", with: " "))")
                        .font(.footnote).foregroundStyle(.secondary)
                    if row.financialsStale {
                        Text("This order changed. Review every amount before saving again.").font(.footnote).foregroundStyle(.orange)
                    }
                }
                Section {
                    LabeledContent("Date") {
                        TextField("YYYY-MM-DD", text: $soldOn)
                            .multilineTextAlignment(.trailing)
                            .textInputAutocapitalization(.never)
                    }
                    ForEach(SalesLedgerRow.fields, id: \.self) { field in
                        LabeledContent(SalesLedgerRow.labels[field] ?? field) {
                            TextField("0.00", text: Binding(get: { values[field] ?? "" }, set: { values[field] = $0 }))
                                .keyboardType(.decimalPad)
                                .multilineTextAlignment(.trailing)
                        }
                    }
                } footer: {
                    Text(footnote)
                }
                .disabled(working)
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
                    }
                }
            }
            .onAppear { fill(row) }
        }
        .interactiveDismissDisabled(working)
    }

    private var footnote: String {
        let cost = SalesLedgerFormat.money((row.financials?["item_cost"] ?? nil), currency)
        var refunded = ""
        if let refunds = row.financials?["refunds"] ?? nil, let amount = Decimal(string: refunds), amount != 0 {
            refunded = " Refunded: \(SalesLedgerFormat.money(refunds, currency))."
        }
        return "Cost of goods: \(cost), from the item's form.\(refunded) Leave an amount blank if you don't know it. Nothing changes on any marketplace."
    }

    private func fill(_ record: SalesLedgerRow) {
        soldOn = record.soldAt.map { String($0.prefix(10)) } ?? ""
        currency = record.currency ?? "USD"
        values = Dictionary(uniqueKeysWithValues: SalesLedgerRow.fields.map { ($0, (record.financials?[$0] ?? nil) ?? "") })
    }

    private func save() {
        working = true
        error = nil
        let entered = Dictionary(uniqueKeysWithValues: SalesLedgerRow.fields.map { field -> (String, String?) in
            var text = (values[field] ?? "").trimmingCharacters(in: .whitespaces)
            if text.hasPrefix("$") { text.removeFirst() }
            return (field, text.isEmpty ? nil : text)
        })
        Task {
            defer { working = false }
            do {
                try await api.saveSaleAmounts(row.id, currency: currency, values: entered,
                                              revision: row.financialRevision, basis: row.orderBasis, soldOn: soldOn.isEmpty ? nil : soldOn)
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
