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
        let conversationID: String
        let title: String
        let missing: [String]
        var id: String { conversationID }
        enum CodingKeys: String, CodingKey {
            case title, missing
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
