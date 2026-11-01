import Charts
import SwiftUI

/// The desktop's Inventory analytics view (AnalyticsPage.tsx), section by
/// section, as a scrolling page.
struct InventoryAnalyticsView: View {
    let api: AnteroomAPI

    @State private var range = "12m"
    @State private var customStart = Calendar.current.date(byAdding: .day, value: -29, to: Date()) ?? Date()
    @State private var customEnd = Date()
    @State private var data: InventoryAnalytics?
    @State private var loading = false
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                header
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                }
                if let data, range != "custom" || customStart <= customEnd {
                    content(data)
                } else if loading {
                    ProgressView("Loading analytics…").frame(maxWidth: .infinity, minHeight: 200)
                }
            }
            .padding(16)
        }
        .background(WorkspaceStyle.background)
        .refreshable { await load() }
        .task(id: "\(range)|\(customStart)|\(customEnd)") { await load() }
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Sales and inventory from your listings.").font(.subheadline).foregroundStyle(.secondary)
            if let data {
                Group {
                    if let updated = data.lastUpdatedAt, let date = ServerDate.parse(updated) {
                        Text("Last updated on \(date.formatted(date: .abbreviated, time: .shortened)).")
                    } else {
                        Text("Last updated on: not recorded yet.")
                    }
                }
                .font(.caption).foregroundStyle(.secondary)
                Text("Current inventory: \(inventoryStrip(data.inventory))")
                    .font(.caption).monospacedDigit()
            }
            HStack {
                Menu {
                    Picker("Sales period", selection: $range) {
                        ForEach(AnalyticsFormat.ranges, id: \.id) { Text($0.label).tag($0.id) }
                        Text("Custom").tag("custom")
                    }
                } label: {
                    Label(rangeLabel, systemImage: "calendar").frame(minHeight: 44)
                }
                .accessibilityLabel("Sales period, \(rangeLabel)")
                Spacer()
                if loading && data != nil {
                    ProgressView().accessibilityLabel("Updating analytics")
                }
            }
            if range == "custom" {
                DatePicker("From", selection: $customStart, in: ...customEnd, displayedComponents: .date)
                DatePicker("To", selection: $customEnd, in: customStart..., displayedComponents: .date)
            }
        }
    }

    private var rangeLabel: String {
        range == "custom" ? "Custom" : AnalyticsFormat.ranges.first { $0.id == range }?.label ?? "Sales period"
    }

    private var heading: String {
        guard range == "custom" else { return rangeLabel }
        let start = customStart.formatted(.dateTime.month(.abbreviated).day().year())
        let end = customEnd.formatted(.dateTime.month(.abbreviated).day().year())
        return start == end ? start : "\(start) – \(end)"
    }

    private func inventoryStrip(_ inventory: InventoryAnalytics.Inventory) -> String {
        var parts = ["\(inventory.active) active", "\(AnalyticsFormat.money(inventory.askingValue)) asking"]
        if inventory.draft > 0 { parts.append("\(inventory.draft) draft\(inventory.draft == 1 ? "" : "s")") }
        if inventory.sold > 0 { parts.append("\(inventory.sold) sold") }
        if inventory.failed > 0 { parts.append("\(inventory.failed) failed") }
        if inventory.working > 0 { parts.append("\(inventory.working) in progress") }
        return parts.joined(separator: " · ")
    }

    // MARK: Sections

    @ViewBuilder
    private func content(_ data: InventoryAnalytics) -> some View {
        if !data.incompleteSales.isEmpty {
            AnalyticsSection(data.incompleteSales.count == 1 ? "1 sale needs sales data" : "\(data.incompleteSales.count) sales need sales data",
                             note: "Across all time. Add the missing details on the listing to update your analytics. Missing prices, costs, or fees leave revenue and profit incomplete.") {
                DisclosureGroup("Review sales with missing data") {
                    ForEach(data.incompleteSales) { sale in
                        ListingLink(id: sale.conversationID, title: sale.title, meta: "Missing \(sale.missing.joined(separator: ", "))")
                    }
                }
                .font(.footnote)
            }
            .padding(12)
            .background(Color.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
        }

        AnalyticsSection(heading) {
            salesStats(data)
            if let previous = data.previous {
                Note("Compared with \(AnalyticsFormat.soldDate(previous.start)) – \(AnalyticsFormat.soldDate(previous.end)) (previous period, same length).")
            }
            if data.sales.revenueKnown < data.sales.count {
                let missing = data.sales.count - data.sales.revenueKnown
                Note("\(missing) \(missing == 1 ? "sale has" : "sales have") no recorded price. Revenue, averages, and breakdowns include recorded amounts only.")
            }
            if data.undatedSales > 0 {
                Note("\(data.undatedSales) \(data.undatedSales == 1 ? "sale has" : "sales have") no date." + (data.range == "all"
                    ? " Included in totals, but excluded from the chart." : " Excluded from this period; included in All time."))
            }
            if data.periodsTruncated { Note("The chart shows the latest 18 months.") }
            SalesChart(periods: data.periods)
        }

        PerformanceList(title: "Marketplaces", rows: data.marketplaces, marketplace: true)
        PerformanceList(title: "Categories", rows: data.categories)
        PerformanceList(title: "Brands", rows: data.brands)
        PerformanceList(title: "Labels", rows: data.labels)
        PriceTiers(tiers: data.priceTiers)

        AnalyticsSection("Inventory today", note: "Current active listings, across all sales periods.") {
            StatGrid {
                Stat(label: "Active listings", value: "\(data.inventory.active)")
                Stat(label: "Asking value", value: AnalyticsFormat.money(data.inventory.askingValue), hint: "Total asking prices; potential revenue")
                Stat(label: "Recorded inventory cost", value: data.inventory.costKnown > 0 ? AnalyticsFormat.money(data.inventory.costValue) : "—",
                     hint: "Cost recorded on \(data.inventory.costKnown) of \(data.inventory.active) active listings")
                Stat(label: "Listed 90+ days", value: "\(data.inventory.staleCount)", hint: "\(AnalyticsFormat.money(data.inventory.staleValue)) at asking prices")
            }
            if data.inventory.undatedCount > 0 {
                Note("\(data.inventory.undatedCount) active \(data.inventory.undatedCount == 1 ? "listing has" : "listings have") no list date and cannot be aged.")
            }
        }

        AnalyticsSection("Current inventory by age", note: "All active listings, regardless of the sales period. Open an age group to review its listings.") {
            if data.aging.isEmpty {
                Empty("No active listings.")
            } else {
                ForEach(data.aging) { group in
                    DisclosureGroup {
                        ForEach(group.listings) { listing in
                            ListingLink(id: listing.conversationID, title: listing.title,
                                        meta: listing.daysListed.map(AnalyticsFormat.days) ?? "No list date",
                                        value: AnalyticsFormat.money(listing.price))
                        }
                    } label: {
                        HStack {
                            Text(group.label)
                            Spacer()
                            Text("\(group.count) listings").font(.caption).foregroundStyle(.secondary)
                            Text("\(AnalyticsFormat.money(group.askingValue)) asking").monospacedDigit()
                        }
                        .font(.subheadline)
                    }
                    .frame(minHeight: 44)
                }
            }
        }

        AnalyticsSection("Review your oldest listings", note: "Up to 8 listings that have been active for 90+ days. Open one to review its price, photos, or details.") {
            if data.oldest.isEmpty {
                Empty("No active listings with a recorded list date are 90+ days old.")
            } else {
                ForEach(data.oldest) { item in
                    ListingLink(id: item.conversationID, title: item.title,
                                meta: "\(AnalyticsFormat.days(item.daysListed ?? 0)) listed", value: AnalyticsFormat.money(item.price))
                }
            }
        }

        AnalyticsSection("Recent sales") {
            if data.recent.isEmpty {
                Empty("No sales in this period.")
            } else {
                ForEach(data.recent) { sale in
                    let meta = [Marketplaces.name(sale.marketplace), sale.soldAt.map(AnalyticsFormat.soldDate),
                                sale.daysListed.map(AnalyticsFormat.days), sale.event]
                        .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
                    ListingLink(id: sale.conversationID, title: sale.title, meta: meta,
                                value: sale.price.map(AnalyticsFormat.money) ?? "Price unknown",
                                valueHint: sale.profit.map { "\(AnalyticsFormat.money($0)) profit" } ?? "Profit unavailable")
                }
            }
        }
    }

    private func salesStats(_ data: InventoryAnalytics) -> some View {
        let sales = data.sales
        let previous = data.previous?.sales
        let revenueComplete = previous.map { sales.revenueKnown == sales.count && $0.revenueKnown == $0.count } ?? false
        let profitComplete = previous.map {
            sales.profitKnown == sales.count && $0.profitKnown == $0.count && sales.feesKnown == sales.count && $0.feesKnown == $0.count
        } ?? false
        return StatGrid {
            Stat(label: "Revenue", value: AnalyticsFormat.money(sales.revenue),
                 hint: "\(sales.revenueKnown) of \(sales.count) sales with recorded prices",
                 change: previous.map { revenueComplete ? AnalyticsFormat.change(sales.revenue, $0.revenue, AnalyticsFormat.money) : "Comparison unavailable: missing prices" })
            Stat(label: "Sold", value: "\(sales.count)", hint: sales.averagePrice.map { "\(AnalyticsFormat.money($0)) average" },
                 change: previous.map { AnalyticsFormat.change(Double(sales.count), Double($0.count)) { String(Int($0)) } })
            Stat(label: "Sell-through rate", value: data.sellThroughRate.map { "\(GeneralFields.text($0))%" } ?? "—",
                 hint: "Sales in this period ÷ (sales in this period + active listings today)")
            Stat(label: "Profit", value: sales.profit.map(AnalyticsFormat.money) ?? "—", hint: AnalyticsFormat.profitHint(sales),
                 change: previous.map { profitComplete ? AnalyticsFormat.change(sales.profit, $0.profit, AnalyticsFormat.money) : "Comparison unavailable: missing costs or fees" },
                 negative: (sales.profit ?? 0) < 0)
            Stat(label: "Profit margin", value: sales.margin.map { "\(GeneralFields.text($0))%" } ?? "—",
                 hint: "Profit ÷ revenue on sales with recorded cost", negative: (sales.margin ?? 0) < 0)
            Stat(label: "Median time to sell", value: sales.medianDays.map(AnalyticsFormat.days) ?? "—",
                 hint: "Based on \(sales.daysKnown) of \(sales.count) sales",
                 change: previous.map { AnalyticsFormat.change(sales.medianDays.map(Double.init), $0.medianDays.map(Double.init)) { AnalyticsFormat.days(Int($0)) } })
        }
    }

    private func load() async {
        if range == "custom" && customStart > customEnd { return }
        loading = true
        defer { loading = false }
        do {
            data = try await api.analytics(range: range, start: range == "custom" ? customStart : nil,
                                           end: range == "custom" ? customEnd : nil)
            error = nil
        } catch is CancellationError {
        } catch let failure as URLError where failure.code == .cancelled {
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: Building blocks (WorkspacePage.tsx, AnalyticsStat.tsx)

struct AnalyticsSection<Content: View>: View {
    let title: String
    let note: String?
    let content: Content

    init(_ title: String, note: String? = nil, @ViewBuilder content: () -> Content) {
        self.title = title
        self.note = note
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline).accessibilityAddTraits(.isHeader)
            if let note { Text(note).font(.footnote).foregroundStyle(.secondary) }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct StatGrid<Content: View>: View {
    @ViewBuilder let content: Content
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 10, alignment: .top),
                                 count: dynamicTypeSize.isAccessibilitySize ? 1 : 2), spacing: 10) {
            content
        }
    }
}

struct Stat: View {
    let label: String
    let value: String
    var hint: String?
    var change: String?
    var negative = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title3.weight(.semibold)).monospacedDigit()
                .foregroundStyle(negative ? .red : .primary)
                .lineLimit(1).minimumScaleFactor(0.7)
            if let change { Text(change).font(.caption2).foregroundStyle(.secondary) }
            if let hint { Text(hint).font(.caption2).foregroundStyle(.secondary) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(WorkspaceStyle.border, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}

private struct Note: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View { Text(text).font(.footnote).foregroundStyle(.secondary) }
}

private struct Empty: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text).font(.subheadline).foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, minHeight: 56)
            .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 10))
    }
}

/// A row that opens the listing it names.
struct ListingLink: View {
    let id: String
    let title: String
    var meta: String?
    var value: String?
    var valueHint: String?

    var body: some View {
        NavigationLink(value: ListingSummary(id: id, title: title)) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.subheadline).lineLimit(2).foregroundStyle(.primary)
                    if let meta { Text(meta).font(.caption).foregroundStyle(.secondary) }
                }
                Spacer(minLength: 8)
                if value != nil || valueHint != nil {
                    VStack(alignment: .trailing, spacing: 2) {
                        if let value { Text(value).font(.subheadline).monospacedDigit().foregroundStyle(.primary) }
                        if let valueHint { Text(valueHint).font(.caption).foregroundStyle(.secondary) }
                    }
                }
            }
            .padding(.vertical, 8)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// Revenue, sales or profit per period, losses below zero.
private struct SalesChart: View {
    let periods: [InventoryAnalytics.Group]
    @State private var metric = "revenue"

    var body: some View {
        if periods.isEmpty || periods.allSatisfy({ $0.sales.count == 0 }) {
            Empty("No dated sales in this period.")
        } else {
            VStack(alignment: .leading, spacing: 10) {
                Picker("Chart metric", selection: $metric) {
                    Text("Revenue").tag("revenue")
                    Text("Sales").tag("count")
                    Text("Profit").tag("profit")
                }
                .pickerStyle(.segmented)
                if metric == "profit" {
                    Note("Profit uses sales with recorded cost and deducts recorded fees and shipping. Missing costs appear as —; loss bars extend below zero.")
                }
                Chart(Array(periods.enumerated()), id: \.offset) { _, period in
                    BarMark(x: .value("Period", period.label), y: .value(metricName, value(period) ?? 0))
                        .foregroundStyle((value(period) ?? 0) < 0 ? Color.red.opacity(0.7) : WorkspaceStyle.accent.opacity(0.8))
                        .accessibilityLabel(period.label)
                        .accessibilityValue(detail(period))
                }
                .chartYAxis { AxisMarks(position: .leading) }
                .frame(height: 220)
            }
            .padding(12)
            .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 10))
        }
    }

    private var metricName: String { metric == "count" ? "Sales" : metric == "profit" ? "Profit" : "Revenue" }

    private func value(_ period: InventoryAnalytics.Group) -> Double? {
        switch metric {
        case "count": Double(period.sales.count)
        case "profit": period.sales.profit
        default: period.sales.revenue
        }
    }

    private func detail(_ period: InventoryAnalytics.Group) -> String {
        "\(AnalyticsFormat.money(period.sales.revenue)), \(AnalyticsFormat.saleCount(period.sales.count)), "
            + (period.sales.profit.map { "\(AnalyticsFormat.money($0)) profit" } ?? "profit unavailable")
    }
}

/// The top six by the chosen measure (PerformanceList).
private struct PerformanceList: View {
    let title: String
    let rows: [InventoryAnalytics.Group]
    var marketplace = false
    @State private var metric = "revenue"

    var body: some View {
        AnalyticsSection(title) {
            Picker("Rank \(title.lowercased()) by", selection: $metric) {
                Text("Revenue").tag("revenue")
                Text("Profit").tag("profit")
                Text("Sales").tag("count")
            }
            .pickerStyle(.segmented)
            if ranked.isEmpty {
                Empty("No \(title.lowercased()) recorded on sales in this period.")
            } else {
                ForEach(ranked) { row in
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(marketplace ? Marketplaces.name(row.id) : row.label).font(.subheadline)
                            Text(AnalyticsFormat.saleCount(row.sales.count)
                                 + (row.sales.medianDays.map { " · \(AnalyticsFormat.days($0)) median" } ?? ""))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text(valueText(row)).font(.subheadline).monospacedDigit()
                            if metric == "profit" {
                                Text(row.sales.profitKnown == 0 ? "Cost missing"
                                     : "Cost on \(row.sales.profitKnown)/\(row.sales.count) · fees on \(row.sales.feesKnown)/\(row.sales.profitKnown)")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    .padding(.vertical, 6)
                    .accessibilityElement(children: .combine)
                }
            }
        }
    }

    private func measure(_ row: InventoryAnalytics.Group) -> Double? {
        switch metric {
        case "count": Double(row.sales.count)
        case "profit": row.sales.profit
        default: row.sales.revenue
        }
    }

    private var ranked: [InventoryAnalytics.Group] {
        Array(rows.sorted { a, b in
            switch (measure(a), measure(b)) {
            case (nil, nil): return a.label < b.label
            case (nil, _): return false
            case (_, nil): return true
            case let (x?, y?): return x != y ? x > y : a.label < b.label
            }
        }.prefix(6))
    }

    private func valueText(_ row: InventoryAnalytics.Group) -> String {
        if metric == "count" { return "\(row.sales.count)" }
        return measure(row).map(AnalyticsFormat.money) ?? "—"
    }
}

/// How this period's sales split across price bands, low to high.
private struct PriceTiers: View {
    let tiers: [InventoryAnalytics.Group]

    var body: some View {
        let total = tiers.reduce(0) { $0 + $1.sales.count }
        AnalyticsSection("Sales by price", note: "This period's sales with a recorded price, by what they sold for. A higher average sale means more of them in the top two bands, without them taking longer to sell.") {
            if total == 0 {
                Empty("No priced sales in this period.")
            } else {
                ForEach(tiers) { tier in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(tier.label).font(.subheadline)
                                Text("\(AnalyticsFormat.saleCount(tier.sales.count)) · \(Int((Double(tier.sales.count) / Double(total) * 100).rounded()))% of sales"
                                     + (tier.sales.medianDays.map { " · \(AnalyticsFormat.days($0)) median" } ?? ""))
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            VStack(alignment: .trailing, spacing: 2) {
                                Text(AnalyticsFormat.money(tier.sales.revenue)).font(.subheadline).monospacedDigit()
                                Text(tier.sales.profit.map { "\(AnalyticsFormat.money($0)) profit" } ?? "Profit unavailable")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        ProgressView(value: Double(tier.sales.count), total: Double(total))
                            .tint(WorkspaceStyle.accent)
                            .accessibilityHidden(true)
                    }
                    .padding(.vertical, 6)
                    .accessibilityElement(children: .combine)
                }
            }
        }
    }
}
