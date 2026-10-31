import Foundation

/// `GET /api/sales-ledger` (api/salesLedger.ts): one record per marketplace
/// order line, with amounts the seller records.
struct SalesLedgerPage: Decodable, Sendable {
    struct Currency: Decodable, Identifiable, Sendable {
        let currency: String
        let lines, netKnown, profitKnown: Int
        let netRevenue, profit: String?
        var id: String { currency }
        enum CodingKeys: String, CodingKey {
            case currency, lines, profit
            case netKnown = "net_known", profitKnown = "profit_known", netRevenue = "net_revenue"
        }
    }

    struct Summary: Decodable, Sendable {
        let lines, unmatched, unrecorded: Int
        let needsReview: Int
        let currencies: [Currency]
        enum CodingKeys: String, CodingKey {
            case lines, unmatched, unrecorded, currencies
            case needsReview = "needs_review"
        }
    }

    let rows: [SalesLedgerRow]
    let page: Int
    let hasMore: Bool
    let summary: Summary

    enum CodingKeys: String, CodingKey {
        case rows, page, summary
        case hasMore = "has_more"
    }
}

struct SalesLedgerRow: Decodable, Identifiable, Sendable {
    static let fields = ["item_revenue", "shipping_credit", "refunds", "fees", "shipping_cost", "item_cost"]
    static let labels = ["item_revenue": "Item revenue total", "shipping_credit": "Shipping collected",
                         "refunds": "Revenue refunded", "fees": "Net fees", "shipping_cost": "Shipping cost total",
                         "item_cost": "Item cost total"]

    let id: String
    let marketplace: String
    let orderID: String
    let lineID: String
    let quantity: Int?
    let orderStatus: String
    let conversationID: String?
    let title: String?
    let financials: [String: String?]?
    let financialsStale: Bool
    let financialRevision: Int
    let orderBasis: String
    let profit: String?

    var currency: String? { financials?["currency"] ?? nil }

    enum CodingKeys: String, CodingKey {
        case id, marketplace, quantity, title, financials, profit
        case orderID = "order_id", lineID = "line_id", orderStatus = "order_status"
        case conversationID = "conversation_id", financialsStale = "financials_stale"
        case financialRevision = "financial_revision", orderBasis = "order_basis"
    }
}

enum SalesLedgerFormat {
    static func money(_ value: String?, _ currency: String?) -> String {
        guard let value, let currency, !currency.isEmpty else { return "Not recorded" }
        return "\(currency) \(value)"
    }
}
