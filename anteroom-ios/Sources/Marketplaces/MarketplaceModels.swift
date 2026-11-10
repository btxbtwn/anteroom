import Foundation

/// `GET /api/marketplace-accounts`: eBay and Etsy, connected through Anteroom's relay.
struct DirectConnections: Decodable, Sendable {
    struct Account: Decodable, Sendable {
        let connected: Bool
        /// eBay username or Etsy shop name.
        let label: String?
        let connectedAt: Double?
        /// A sign-in page is open and Anteroom is waiting for it.
        let pending: Bool
        let authorizeURL: String?
        let error: String?

        enum CodingKeys: String, CodingKey {
            case connected, label, connectedAt, pending, error
            case authorizeURL = "authorizeUrl"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            connected = try c.decodeIfPresent(Bool.self, forKey: .connected) ?? false
            label = try c.decodeIfPresent(String.self, forKey: .label)
            connectedAt = try? c.decodeIfPresent(Double.self, forKey: .connectedAt)
            pending = try c.decodeIfPresent(Bool.self, forKey: .pending) ?? false
            authorizeURL = try c.decodeIfPresent(String.self, forKey: .authorizeURL)
            error = try c.decodeIfPresent(String.self, forKey: .error)
        }
    }

    let accounts: [String: Account]
}

/// One record from an eBay read (`/api/ebay/read/{section}`): an order, a
/// conversation, a payout, a listing. Every section uses this one shape.
struct EbayRow: Decodable, Identifiable, Hashable, Sendable {
    let id: String
    let title: String
    let subtitle: String
    let status: String
    let amount: String?
    /// In the server's order once the page is read with `EbayPage.decode`.
    var fields: [(key: String, value: String?)]
    var children: [EbayRow]

    enum CodingKeys: String, CodingKey { case id, title, subtitle, status, amount, fields, children }

    private struct Key: CodingKey {
        var stringValue: String
        var intValue: Int? { nil }
        init(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { nil }
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        subtitle = try c.decodeIfPresent(String.self, forKey: .subtitle) ?? ""
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? ""
        let money = try c.decodeIfPresent(JSONValue.self, forKey: .amount)
        amount = money.flatMap { $0 == .null ? nil : $0.text }
        var pairs: [(String, String?)] = []
        if c.contains(.fields) {
            let fieldBox = try c.nestedContainer(keyedBy: Key.self, forKey: .fields)
            // `allKeys` comes in no set order; `EbayPage.decode` restores the server's.
            for key in fieldBox.allKeys.sorted(by: { $0.stringValue < $1.stringValue }) {
                let value = try fieldBox.decodeIfPresent(JSONValue.self, forKey: key)
                pairs.append((key.stringValue, value.flatMap { $0 == .null ? nil : $0.text }))
            }
        }
        fields = pairs.map { (key: $0.0, value: $0.1) }
        children = (try? c.decodeIfPresent([EbayRow].self, forKey: .children)) ?? []
    }

    func field(_ name: String) -> String? { fields.first { $0.key == name }?.value }

    static func == (lhs: EbayRow, rhs: EbayRow) -> Bool { lhs.id == rhs.id && lhs.title == rhs.title }
    func hash(into hasher: inout Hasher) { hasher.combine(id) }
}

struct EbayPage: Decodable, Sendable {
    var rows: [EbayRow]
    let total: Int?
    let nextOffset: Int?

    /// Decode a page with each record's fields in the order the server wrote them.
    static func decode(_ data: Data) throws -> EbayPage {
        var page = try JSONDecoder().decode(EbayPage.self, from: data)
        var orders = JSONKeyOrder.objects(named: "fields", in: data).makeIterator()
        func apply(_ row: inout EbayRow) {
            if let order = orders.next() {
                let rank = Dictionary(order.enumerated().map { ($1, $0) }, uniquingKeysWith: { first, _ in first })
                row.fields.sort { (rank[$0.key] ?? .max) < (rank[$1.key] ?? .max) }
            }
            for index in row.children.indices { apply(&row.children[index]) }
        }
        for index in page.rows.indices { apply(&page.rows[index]) }
        return page
    }
}

/// The keys of each JSON object stored under a given name, in document order.
/// Foundation's decoders forget object key order; the server's order is how
/// eBay's records read.
enum JSONKeyOrder {
    static func objects(named name: String, in data: Data) -> [[String]] {
        let bytes = [UInt8](data)
        var result: [[String]] = []
        // Open containers: whether each collects keys, and its slot in `result`
        // (-1 for any other object, -2 for an array).
        var stack: [(collect: Bool, slot: Int)] = []
        var index = 0
        var lastString: String?
        var expectKey = false
        var pendingName = false
        while index < bytes.count {
            let byte = bytes[index]
            switch byte {
            case UInt8(ascii: "\""):
                var end = index + 1
                var raw: [UInt8] = []
                while end < bytes.count, bytes[end] != UInt8(ascii: "\"") {
                    if bytes[end] == UInt8(ascii: "\\"), end + 1 < bytes.count {
                        raw.append(bytes[end]); raw.append(bytes[end + 1]); end += 2
                    } else {
                        raw.append(bytes[end]); end += 1
                    }
                }
                let text = (try? JSONSerialization.jsonObject(with: Data([UInt8(ascii: "\"")] + raw + [UInt8(ascii: "\"")]),
                                                              options: .fragmentsAllowed) as? String) ?? String(decoding: raw, as: UTF8.self)
                if expectKey {
                    if let top = stack.last, top.collect { result[top.slot].append(text) }
                    lastString = text
                } else {
                    lastString = nil
                }
                index = end + 1
                continue
            case UInt8(ascii: ":"):
                pendingName = expectKey && lastString == name
                expectKey = false
            case UInt8(ascii: "{"):
                if pendingName {
                    result.append([])
                    stack.append((true, result.count - 1))
                } else {
                    stack.append((false, -1))
                }
                pendingName = false
                expectKey = true
            case UInt8(ascii: "}"):
                _ = stack.popLast()
                expectKey = false
            case UInt8(ascii: ","):
                // A comma inside an object starts the next key; inside an array, the next value.
                expectKey = stack.last.map { $0.slot != -2 } ?? false
                pendingName = false
            case UInt8(ascii: "["):
                stack.append((false, -2))
                pendingName = false
                expectKey = false
            case UInt8(ascii: "]"):
                _ = stack.popLast()
                expectKey = false
            default:
                break
            }
            index += 1
        }
        return result
    }
}

/// What the eBay workspace says, as the web's ebay/*.tsx says it.
enum EbayWorkspaceFormat {
    /// The groups of tools a seller can authorize (EbayWorkspace.tsx).
    static let features: [(id: String, name: String, detail: String)] = [
        ("analytics", "Listing performance", "Impressions, views, and conversion."),
        ("finances", "Earnings and payouts", "Fees, refunds, and order earnings."),
        ("orders", "Order management", "Tracking updates and reviewed refunds."),
        ("disputes", "Payment disputes", "Evidence and dispute responses."),
        ("messages", "Buyer messages", "Read conversations and send reviewed replies."),
        ("feedback", "Buyer feedback", "Leave positive feedback for completed sales."),
        ("marketing", "Discounts and advertising", "Promotions, campaigns, reports, and store emails."),
        ("listings", "Listing tools", "Imports, inventory, and live listing edits."),
    ]

    /// A field name as a label: "listingId" reads "listing ID".
    static func label(_ key: String) -> String {
        var out = ""
        var previous: Character?
        for character in key {
            if let previous, previous.isLowercase, character.isUppercase { out.append(" ") }
            out.append(character)
            previous = character
        }
        return out.hasSuffix("Id") ? String(out.dropLast(2)) + "ID" : out
    }

    /// "IN_PROGRESS" reads "in progress".
    static func status(_ raw: String) -> String { raw.replacingOccurrences(of: "_", with: " ").lowercased() }

    /// Only links to eBay's own site open from a record.
    static func ebayLink(_ value: String?) -> URL? {
        guard let value, let url = URL(string: value), url.scheme == "https",
              let host = url.host?.lowercased(), host == "ebay.com" || host == "www.ebay.com" else { return nil }
        return url
    }

    /// Page totals, never estimates for orders that have not loaded (dashboardSummary.ts).
    static func summary(_ rows: [EbayRow]) -> (earnings: Double?, costs: Double?, profit: Double?) {
        (total(rows.map(\.amount)), total(rows.map { $0.field("Item cost") }), total(rows.map { $0.field("Profit") }))
    }

    private static func total(_ values: [String?]) -> Double? {
        guard !values.isEmpty else { return nil }
        var cents = 0
        for value in values {
            guard let value, !value.isEmpty, let number = Double(value), number.isFinite else { return nil }
            cents += Int((number * 100).rounded())
        }
        return Double(cents) / 100
    }

    static func money(_ value: Double?) -> String {
        guard let value else { return "Not available" }
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = "USD"
        formatter.locale = Locale(identifier: "en_US")
        formatter.maximumFractionDigits = 2
        return formatter.string(from: NSNumber(value: value)) ?? "$\(value)"
    }

    /// "First 25 of 40 orders · totals cover these orders only".
    static func coverage(shown: Int, total: Int?) -> String {
        "First \(shown)\(total.map { " of \($0)" } ?? "") \(total == 1 ? "order" : "orders") · totals cover these orders only"
    }

    /// The result count above a list.
    static func count(total: Int?, shown: Int) -> String {
        if let total { return "\(total) \(total == 1 ? "result" : "results")" }
        return "\(shown) shown"
    }

    /// Finances pages hold 10 orders; every other section 25.
    static func pageSize(_ section: String) -> Int { section == "finances" ? 10 : 25 }
}
