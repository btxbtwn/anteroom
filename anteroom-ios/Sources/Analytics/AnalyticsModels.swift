import Foundation

/// `GET /api/analytics?range=` (routes/analytics.py), as the desktop
/// Inventory analytics view reads it.
struct InventoryAnalytics: Decodable, Sendable {
    struct Inventory: Decodable, Sendable {
        let active, draft, sold, failed, working: Int
        let askingValue, costValue: Double
        let costKnown, staleCount: Int
        let staleValue: Double
        let undatedCount: Int

        enum CodingKeys: String, CodingKey {
            case active, draft, sold, failed, working
            case askingValue = "asking_value", costValue = "cost_value", costKnown = "cost_known"
            case staleCount = "stale_count", staleValue = "stale_value", undatedCount = "undated_count"
        }
    }

    struct Sales: Decodable, Sendable {
        let count: Int
        let revenue: Double
        let profit: Double?
        let profitKnown, feesKnown, revenueKnown, daysKnown: Int
        let averagePrice: Double?
        let medianDays: Int?
        let margin: Double?

        enum CodingKeys: String, CodingKey {
            case count, revenue, profit, margin
            case profitKnown = "profit_known", feesKnown = "fees_known", revenueKnown = "revenue_known"
            case daysKnown = "days_known", averagePrice = "average_price", medianDays = "median_days"
        }
    }

    struct Previous: Decodable, Sendable {
        let start, end: String
        let sales: Sales
    }

    /// A chart period or a ranked group: the sales totals plus its name.
    struct Group: Decodable, Identifiable, Sendable {
        let id: String
        let label: String
        let color: String?
        let sales: Sales

        init(from decoder: Decoder) throws {
            let values = try decoder.container(keyedBy: Keys.self)
            label = try values.decode(String.self, forKey: .label)
            id = try values.decodeIfPresent(String.self, forKey: .id) ?? label
            color = try values.decodeIfPresent(String.self, forKey: .color)
            sales = try Sales(from: decoder)
        }

        private enum Keys: String, CodingKey { case id, label, color }
    }

    struct ActiveListing: Decodable, Identifiable, Sendable {
        let conversationID: String
        let title: String
        let price: Double
        let daysListed: Int?
        var id: String { conversationID }
        enum CodingKeys: String, CodingKey {
            case title, price
            case conversationID = "conversation_id", daysListed = "days_listed"
        }
    }

    struct Aging: Decodable, Identifiable, Sendable {
        let label: String
        let count: Int
        let askingValue: Double
        let listings: [ActiveListing]
        var id: String { label }
        enum CodingKeys: String, CodingKey {
            case label, count, listings
            case askingValue = "asking_value"
        }
    }

    struct Sale: Decodable, Identifiable, Sendable {
        let conversationID: String
        let title: String
        let price: Double?
        let marketplace: String
        let soldAt: String?
        let daysListed: Int?
        let event: String?
        let profit: Double?
        var id: String { conversationID + (soldAt ?? "") }
        enum CodingKeys: String, CodingKey {
            case title, price, marketplace, event, profit
            case conversationID = "conversation_id", soldAt = "sold_at", daysListed = "days_listed"
        }
    }

    /// A listing past the stale threshold, with the deeper cut its cost allows.
    struct StaleListing: Decodable, Identifiable, Sendable {
        let conversationID: String
        let title: String
        let daysListed: Int
        let price: Double
        let lowestPrice: Int?
        let discountPercent: Int?
        let salePrice: Double?
        var id: String { conversationID }
        enum CodingKeys: String, CodingKey {
            case title, price
            case conversationID = "conversation_id", daysListed = "days_listed", lowestPrice = "lowest_price"
            case discountPercent = "discount_percent", salePrice = "sale_price"
        }
    }

    struct IncompleteSale: Decodable, Identifiable, Sendable {
        let id: String
        let conversationID: String?
        let title: String
        let missing: [String]
        enum CodingKeys: String, CodingKey {
            case id, title, missing
            case conversationID = "conversation_id"
        }
    }

    let range: String
    let lastUpdatedAt: String?
    let incompleteSales: [IncompleteSale]
    let undatedSales: Int
    let periodsTruncated: Bool
    let inventory: Inventory
    let sellThroughRate: Double?
    let sales: Sales
    let previous: Previous?
    let periods: [Group]
    let marketplaces, categories, brands, labels, priceTiers: [Group]
    let aging: [Aging]
    let stale: [StaleListing]
    let staleDays: Int?
    let recent: [Sale]
    let oldest: [ActiveListing]

    enum CodingKeys: String, CodingKey {
        case range, inventory, sales, previous, periods, marketplaces, categories, brands, labels, aging, recent, oldest, stale
        case staleDays = "stale_days"
        case lastUpdatedAt = "last_updated_at", incompleteSales = "incomplete_sales", undatedSales = "undated_sales"
        case periodsTruncated = "periods_truncated", sellThroughRate = "sell_through_rate", priceTiers = "price_tiers"
    }
}

/// The desktop's analytics wording (analyticsFormat.ts).
enum AnalyticsFormat {
    static let ranges: [(id: String, label: String)] = [
        ("wtd", "This week"), ("mtd", "This month"), ("ytd", "This year"), ("7d", "Last 7 days"),
        ("30d", "Last 30 days"), ("90d", "Last 90 days"), ("12m", "Last 12 months"), ("all", "All time"),
    ]

    /// Whole dollars stay whole; a cent keeps two places.
    static func money(_ value: Double) -> String {
        guard value.isFinite else { return "$0" }
        let cents = (abs(value) * 100).rounded()
        let digits = cents.truncatingRemainder(dividingBy: 100) == 0 ? 0 : 2
        return value.formatted(.currency(code: "USD").precision(.fractionLength(digits)).locale(Locale(identifier: "en_US")))
    }

    static func days(_ days: Int) -> String { days == 1 ? "1 day" : "\(days) days" }

    static func saleCount(_ count: Int) -> String { count == 1 ? "1 sale" : "\(count) sales" }

    /// Largest first by the figure the weekly chart shows ("count" or "revenue"), then by the other one (rankBreakdown).
    static func rankBreakdown(_ rows: [SalesOverview.Marketplace], by metric: String) -> [SalesOverview.Marketplace] {
        let byCount = metric == "count"
        return rows.enumerated().sorted { left, right in
            let (a, b) = (left.element, right.element)
            let first = byCount ? (Double(a.count), Double(b.count)) : (a.revenue, b.revenue)
            let second = byCount ? (a.revenue, b.revenue) : (Double(a.count), Double(b.count))
            if first.0 != first.1 { return first.0 > first.1 }
            if second.0 != second.1 { return second.0 > second.1 }
            return left.offset < right.offset
        }.map(\.element)
    }

    /// An absolute change when a zero or negative baseline makes a percentage misleading.
    static func change(_ current: Double?, _ previous: Double?, _ format: (Double) -> String) -> String {
        guard let current, let previous else { return "No comparison data" }
        if current == previous { return "No change" }
        let difference = current - previous
        let percent = previous > 0 && current >= 0 ? " (\(Int((abs(difference / previous) * 100).rounded()))%)" : ""
        return "\(difference > 0 ? "Up" : "Down") \(format(abs(difference)))\(percent)"
    }

    static func soldDate(_ iso: String) -> String {
        guard let date = ServerDate.parse(iso) ?? ISO8601DateFormatter().date(from: iso) else { return "" }
        return date.formatted(.dateTime.month(.abbreviated).day().year())
    }

    static func profitHint(_ sales: InventoryAnalytics.Sales) -> String {
        if sales.profitKnown == 0 { return "Record sale prices and costs to see profit" }
        let scope = sales.profitKnown == sales.count ? "" : " on \(sales.profitKnown) of \(sales.count) sales"
        if sales.feesKnown == 0 { return "After recorded costs and shipping\(scope). Fees missing" }
        if sales.feesKnown == sales.profitKnown { return "After recorded cost, fees and shipping\(scope)" }
        return "After recorded cost, fees and shipping\(scope). Fees recorded on \(sales.feesKnown) of \(sales.profitKnown)"
    }
}

/// `GET /api/analytics/overview` (SalesOverview in api/salesLedger.ts): the figures
/// and weekly chart above Sales records, for its dates and marketplace.
struct SalesOverview: Decodable, Sendable {
    struct Figures: Decodable, Sendable {
        let count, revenueKnown, profitKnown, feesKnown, daysKnown: Int
        let revenue: Double
        let profit, averageDays, sellThrough: Double?
        enum CodingKeys: String, CodingKey {
            case count, revenue, profit
            case revenueKnown = "revenue_known", profitKnown = "profit_known", feesKnown = "fees_known"
            case daysKnown = "days_known", averageDays = "average_days", sellThrough = "sell_through"
        }
    }

    struct Previous: Decodable, Sendable {
        let start, end: String
        let figures: Figures
        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            start = try container.decode(String.self, forKey: .start)
            end = try container.decode(String.self, forKey: .end)
            figures = try Figures(from: decoder)
        }
        enum CodingKeys: String, CodingKey { case start, end }
    }

    struct Week: Decodable, Identifiable, Sendable {
        let start: String
        let count, revenueKnown: Int
        let revenue: Double
        var id: String { start }
        enum CodingKeys: String, CodingKey {
            case start, count, revenue
            case revenueKnown = "revenue_known"
        }
    }

    struct Marketplace: Decodable, Identifiable, Sendable {
        let id: String
        let count, revenueKnown: Int
        let revenue: Double
        enum CodingKeys: String, CodingKey {
            case id, count, revenue
            case revenueKnown = "revenue_known"
        }
    }

    let current: Figures
    let previous: Previous?
    let weeks: [Week]
    let weeksTruncated: Bool
    let undatedSales: Int
    let marketplaces: [Marketplace]

    enum CodingKeys: String, CodingKey {
        case current, previous, weeks, marketplaces
        case weeksTruncated = "weeks_truncated", undatedSales = "undated_sales"
    }
}

/// One card in the figures grid. A nil value can't be worked out from what was recorded.
struct OverviewFigure: Identifiable, Sendable {
    let label: String
    let value: String?
    var hint: String?
    var change: String?
    var negative = false
    var id: String { label }
}

extension AnalyticsFormat {
    /// The figures row (overviewFigures in analyticsFormat.ts).
    static func overviewFigures(_ current: SalesOverview.Figures, _ previous: SalesOverview.Figures?,
                                marketplaceChosen: Bool) -> [OverviewFigure] {
        let none = current.count == 0
        func pricesComplete(_ figures: SalesOverview.Figures) -> Bool { figures.revenueKnown == figures.count }
        func costsComplete(_ figures: SalesOverview.Figures) -> Bool {
            figures.profitKnown == figures.count && figures.feesKnown == figures.count
        }
        func compare(_ complete: Bool, _ missing: String, _ now: Double?, _ before: Double?, _ format: (Double) -> String) -> String? {
            guard previous != nil else { return nil }
            return complete ? change(now, before, format) : "No comparison: \(missing)"
        }
        let revenueHint: String? = none ? nil : current.revenueKnown == 0 ? "Record sale prices to see revenue"
            : current.revenueKnown < current.count ? "Recorded on \(current.revenueKnown) of \(current.count) sales" : nil
        let profitHint = none || (current.profit != nil && costsComplete(current)) ? "After fees, shipping and cost"
            : current.profit == nil ? "Record sale prices and costs to see profit"
            : "Cost recorded on \(current.profitKnown) of \(current.count) sales"
        let previousProfit = previous.flatMap { $0.count == 0 ? 0 : $0.profit }
        return [
            OverviewFigure(label: "Revenue",
                           value: none ? money(0) : current.revenueKnown == 0 ? nil : money(current.revenue),
                           hint: revenueHint,
                           change: compare(previous.map { pricesComplete(current) && pricesComplete($0) } ?? false,
                                           "missing prices", current.revenue, previous?.revenue, money)),
            OverviewFigure(label: "Profit",
                           value: none ? money(0) : current.profit.map(money),
                           hint: profitHint,
                           change: compare(previous.map { costsComplete(current) && costsComplete($0) } ?? false,
                                           "missing costs or fees", none ? 0 : current.profit, previousProfit, money),
                           negative: (current.profit ?? 0) < 0),
            OverviewFigure(label: "Items sold", value: String(current.count),
                           change: compare(true, "", Double(current.count), previous.map { Double($0.count) }) { String(Int($0.rounded())) }),
            OverviewFigure(label: "Average days to sell",
                           value: current.averageDays.map { days(Int($0.rounded())) },
                           hint: current.averageDays != nil && current.daysKnown < current.count
                               ? "Based on \(current.daysKnown) of \(current.count) sales" : nil,
                           change: previous?.averageDays != nil && current.averageDays != nil
                               ? change(current.averageDays, previous?.averageDays) { days(Int($0.rounded())) } : nil),
            OverviewFigure(label: "Sell-through",
                           value: current.sellThrough.map { "\($0.formatted(.number.precision(.fractionLength(0...1))))%" },
                           hint: marketplaceChosen ? "Shown for all marketplaces only" : "Sold ÷ (sold + still listed)"),
        ]
    }
}
