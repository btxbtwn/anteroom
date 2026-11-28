import Foundation

/// Mark as sold, as the web's MarkSoldForm.tsx asks it: where and when it
/// sold and what it brought in. Recording a sale never changes a marketplace;
/// delisting the rest is offered afterwards behind its own confirmation.
struct SaleForm: Equatable {
    static let marketplaces = ["ebay", "etsy", "poshmark", "mercari", "depop", "other"]

    var marketplace: String
    var soldOn: Date
    var quantity = "1"
    var price = ""
    var shippingCharged = ""
    var fees = ""
    var shippingCost = ""
    /// One per form, so a second tap on Save records one sale.
    let requestID: String

    init(marketplace: String = "ebay", soldOn: Date = Date(), requestID: String = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()) {
        self.marketplace = marketplace
        self.soldOn = soldOn
        self.requestID = requestID
    }

    /// The sale as the API takes it, or why it can't be saved yet.
    /// A blank amount stays unknown; `calendar` reads the sold day as the seller sees it.
    func input(today: Date = Date(), calendar: Calendar = .current) -> Result<SellerSaleInput, SaleProblem> {
        var amounts: [String: String?] = [:]
        for (key, raw) in [("item_revenue", price), ("shipping_credit", shippingCharged), ("fees", fees), ("shipping_cost", shippingCost)] {
            var value = raw.trimmingCharacters(in: .whitespaces)
            if value.hasPrefix("$") { value.removeFirst() }
            if !value.isEmpty && value.range(of: #"^\d{1,12}(\.\d{1,4})?$"#, options: .regularExpression) == nil {
                return .failure(SaleProblem("Enter amounts like 24.50, or leave them blank."))
            }
            amounts[key] = value.isEmpty ? nil : value
        }
        guard let count = Int(quantity.trimmingCharacters(in: .whitespaces)), count >= 1 else {
            return .failure(SaleProblem("Enter how many sold, 1 or more."))
        }
        let day = calendar.dateComponents([.year, .month, .day], from: soldOn)
        let todayParts = calendar.dateComponents([.year, .month, .day], from: today)
        guard let sold = calendar.date(from: day), let now = calendar.date(from: todayParts), sold <= now else {
            return .failure(SaleProblem("Choose the day it sold, today or earlier."))
        }
        let soldOnText = String(format: "%04d-%02d-%02d", day.year ?? 0, day.month ?? 0, day.day ?? 0)
        return .success(SellerSaleInput(marketplace: marketplace, soldOn: soldOnText, quantity: count,
                                        itemRevenue: amounts["item_revenue"] ?? nil, shippingCredit: amounts["shipping_credit"] ?? nil,
                                        fees: amounts["fees"] ?? nil, shippingCost: amounts["shipping_cost"] ?? nil,
                                        requestID: requestID))
    }
}

struct SaleProblem: Error, Equatable {
    let message: String
    init(_ message: String) { self.message = message }
}

/// `POST …/marketplace-listings/sold`.
struct SellerSaleInput: Encodable, Equatable {
    let marketplace: String
    let soldOn: String
    let quantity: Int
    var currency = "USD"
    let itemRevenue: String?
    let shippingCredit: String?
    let fees: String?
    let shippingCost: String?
    let requestID: String

    init(marketplace: String, soldOn: String, quantity: Int, itemRevenue: String?, shippingCredit: String?,
         fees: String?, shippingCost: String?, requestID: String) {
        self.marketplace = marketplace
        self.soldOn = soldOn
        self.quantity = quantity
        self.itemRevenue = itemRevenue
        self.shippingCredit = shippingCredit
        self.fees = fees
        self.shippingCost = shippingCost
        self.requestID = requestID
    }

    enum CodingKeys: String, CodingKey {
        case marketplace, quantity, currency, fees
        case soldOn = "sold_on"
        case itemRevenue = "item_revenue"
        case shippingCredit = "shipping_credit"
        case shippingCost = "shipping_cost"
        case requestID = "request_id"
    }
}

/// What a recorded sale answers. `delistOffer` names the live listings
/// elsewhere the seller may delist through the confirmed Delist; nothing is
/// delisted by recording the sale.
struct SellerSaleResult: Decodable, Equatable {
    let status: String
    let unitsSold: Int
    let quantity: Int
    let delistOffer: [String]

    enum CodingKeys: String, CodingKey {
        case status, quantity
        case unitsSold = "units_sold"
        case delistOffer = "delist_offer"
    }
}

/// Scheduled Draft Publishing, as the web's DraftScheduleControl.tsx offers it.
enum DraftScheduling {
    static let marketplaces = ["ebay", "etsy", "poshmark", "mercari", "depop"]

    /// An hour from now, on the minute: the web's default.
    static func defaultTime(now: Date = Date()) -> Date {
        let later = now.addingTimeInterval(3600)
        return Date(timeIntervalSinceReferenceDate: (later.timeIntervalSinceReferenceDate / 60).rounded(.down) * 60)
    }

    /// Why this schedule can't be confirmed yet, or nil.
    static func problem(marketplaces: [String], at: Date, now: Date = Date()) -> String? {
        if marketplaces.isEmpty { return "Choose at least one marketplace." }
        if at <= now { return "Choose a future publish date and time." }
        return nil
    }

    /// The marketplaces in the web editor's order.
    static func ordered(_ marketplaces: Set<String>) -> [String] {
        Self.marketplaces.filter(marketplaces.contains)
    }

    static func question(title: String, marketplaces: [String], at: String) -> (title: String, message: String, button: String) {
        let names = marketplaces.map(Marketplaces.name).joined(separator: ", ")
        return ("Schedule “\(title)” to publish on \(names)?",
                "It publishes at \(at) to \(names) only. If you edit this draft before then, Anteroom unschedules it automatically.",
                "Schedule publish")
    }

    /// "Scheduled for Nov 26, 2026 at 9:00 AM on eBay, Poshmark".
    static func summary(_ schedule: DraftSchedule, at: String) -> String {
        "Scheduled for \(at) on \(schedule.scheduledMarketplaces.map(Marketplaces.name).joined(separator: ", "))"
    }

    /// The UTC timestamp the API takes.
    static func wire(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }
}
