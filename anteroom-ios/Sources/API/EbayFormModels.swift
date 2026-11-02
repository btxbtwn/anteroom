import Foundation

/// eBay: one aspect a listing's variations can differ by, with eBay's values for it.
struct VariationAspect: Decodable, Equatable, Sendable {
    let name: String
    let selectionOnly: Bool
    let options: [String]

    enum CodingKeys: String, CodingKey {
        case name, options
        case selectionOnly = "selection_only"
    }

    init(name: String, selectionOnly: Bool = false, options: [String] = []) {
        self.name = name
        self.selectionOnly = selectionOnly
        self.options = options
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        selectionOnly = try c.decodeIfPresent(Bool.self, forKey: .selectionOnly) ?? false
        options = try c.decodeIfPresent([String].self, forKey: .options) ?? []
    }
}

/// `ebay_specifics.variations` in its stored shape (EbayVariationsEditor.tsx).
/// Rows keep any keys this app does not edit.
struct EbayVariations: Equatable, Sendable {
    /// eBay allows five aspects to vary and twelve photos for each value of the photo aspect.
    static let maxAspects = 5
    static let maxPhotos = 12

    struct Row: Equatable, Sendable {
        var values: [String: String] = [:]
        var quantity: JSONValue = .int(1)
        var price: JSONValue = .string("")
        var sku = ""
        var other: [String: JSONValue] = [:]

        init(aspects: [String]) {
            values = Dictionary(uniqueKeysWithValues: aspects.map { ($0, "") })
        }

        init(_ object: [String: JSONValue]) {
            var rest = object
            if case .object(let raw)? = rest.removeValue(forKey: "values") {
                values = raw.mapValues(\.text)
            }
            quantity = rest.removeValue(forKey: "quantity") ?? .string("")
            price = rest.removeValue(forKey: "price") ?? .string("")
            sku = rest.removeValue(forKey: "sku")?.text ?? ""
            other = rest
        }

        var json: JSONValue {
            var object = other
            object["values"] = .object(values.mapValues(JSONValue.string))
            object["quantity"] = quantity
            object["price"] = price
            object["sku"] = .string(sku)
            return .object(object)
        }
    }

    var aspects: [String] = []
    var photosBy: String?
    var rows: [Row] = []
    var photos: [String: [String]] = [:]

    init() {}

    /// The listing's variations, whatever partial value it held.
    init(_ value: JSONValue?) {
        guard case .object(let raw)? = value else { return }
        if case .array(let names)? = raw["aspects"] {
            aspects = names.compactMap { if case .string(let name) = $0 { return name } else { return nil } }
        }
        if case .string(let name)? = raw["photosBy"], !name.isEmpty { photosBy = name }
        if case .array(let items)? = raw["rows"] {
            rows = items.compactMap { if case .object(let row) = $0 { return Row(row) } else { return nil } }
        }
        if case .object(let byValue)? = raw["photos"] {
            photos = byValue.compactMapValues { item in
                guard case .array(let ids) = item else { return nil }
                return ids.map(\.text)
            }
        }
    }

    var json: JSONValue {
        var object: [String: JSONValue] = [
            "aspects": .array(aspects.map(JSONValue.string)),
            "rows": .array(rows.map(\.json)),
            "photos": .object(photos.mapValues { .array($0.map(JSONValue.string)) }),
        ]
        if let photosBy { object["photosBy"] = .string(photosBy) }
        return .object(object)
    }

    /// Units across every variation; blank or invalid quantities count as none.
    var total: Int {
        rows.reduce(0) { sum, row in
            guard let number = row.quantity.number, number > 0, number.rounded() == number else { return sum }
            return sum + Int(number)
        }
    }

    /// The aspect photos are chosen by: the seller's choice, else the first aspect.
    var photoAspect: String { photosBy ?? aspects.first ?? "" }

    /// Each value of the photo aspect, once, in row order.
    var photoValues: [String] {
        var seen: [String] = []
        for row in rows {
            let value = row.values[photoAspect] ?? ""
            if !value.isEmpty, !seen.contains(value) { seen.append(value) }
        }
        return seen
    }

    /// Change what the variations differ by. Rows keep the values they already had for aspects that stay.
    func withAspects(_ names: [String]) -> EbayVariations {
        var next = self
        next.aspects = Array(names.prefix(Self.maxAspects))
        next.rows = rows.map { row in
            var kept = row
            kept.values = Dictionary(uniqueKeysWithValues: next.aspects.map { ($0, row.values[$0] ?? "") })
            return kept
        }
        next.photosBy = photosBy.flatMap { next.aspects.contains($0) ? $0 : nil }
        // Photos are kept per value of one aspect; they stay only while that aspect does.
        if next.photoAspect != photoAspect { next.photos = [:] }
        return next
    }
}

/// eBay's own page rules for its listing form (nativeFormStability.ts): which
/// rows show for the current format, offers and schedule, and how format,
/// duration and quantity read with variations.
enum EbayFormRules {
    static let auctionPriceKeys: Set<String> = [
        "ebay_specifics.auctionStartPrice", "ebay_specifics.auctionReservePrice", "ebay_specifics.auctionBuyItNow",
    ]
    static let offerKeys: Set<String> = [
        "ebay_specifics.bestOffer.enabled", "ebay_specifics.bestOffer.autoAcceptPrice", "ebay_specifics.bestOffer.minimumOfferPrice",
    ]
    static let gtc = "GTC"
    static let gtcLabel = "Good 'Til Cancelled"

    /// Whether a row shows. `shown` reads a control's stored value.
    static func visible(_ key: String, shown: (String) -> String, varied: Bool) -> Bool {
        if key == "ebay_specifics.schedule.startAt" { return shown("ebay_specifics.schedule.enabled") == "true" }
        let auction = shown("ebay_specifics.listingFormat") == "AUCTION"
        // An auction has a duration and bids instead of an item price; Buy It Now runs until cancelled.
        if auctionPriceKeys.contains(key) || key == "ebay_specifics.listingDuration" { return auction }
        if key == "ebay_specifics.price" { return !auction }
        if offerKeys.contains(key) {
            if auction || varied { return false }
            // Minimum offer and Auto accept show only while Allow offers is on.
            if key == "ebay_specifics.bestOffer.enabled" { return true }
            return shown("ebay_specifics.bestOffer.enabled") == "true"
        }
        if key == "ebay_specifics.variations" { return !(auction && !varied) }
        return true
    }

    /// Format, duration and quantity as the current format and variations present them.
    static func present(_ field: NativeField, format: String, varied: Bool) -> NativeField {
        var field = field
        switch field.key {
        case "ebay_specifics.listingDuration":
            if format != "AUCTION" {
                field.locked = true
                field.selectionOnly = true
                field.options = [gtcLabel]
                field.optionValues = [gtcLabel: gtc]
                field.value = .string(gtc)
                field.help = "eBay fixed-price listings run Good 'Til Cancelled."
                return field
            }
            let auctionOptions = field.options.filter { field.optionValues[$0].map { $0 != gtc } ?? false }
            field.locked = false
            guard !auctionOptions.isEmpty else { return field }
            let values = field.optionValues.filter { auctionOptions.contains($0.key) }
            let currentID = field.optionValues[field.value?.text ?? ""] ?? field.value?.text ?? ""
            let fallback = values["7 days"] ?? values[auctionOptions[0]] ?? ""
            field.selectionOnly = true
            field.options = auctionOptions
            field.optionValues = values
            field.value = .string(values.values.contains(currentID) ? currentID : fallback)
            field.help = nil
        case "ebay_specifics.listingFormat":
            guard varied, format != "AUCTION" else { return field }
            let options = field.options.filter { field.optionValues[$0] != "AUCTION" }
            guard !options.isEmpty, options.count != field.options.count else { return field }
            field.optionValues = field.optionValues.filter { options.contains($0.key) }
            field.options = options
            field.help = "A listing with variations is fixed price. Remove the variations to sell at auction."
        case "quantity":
            field.locked = varied
            field.help = varied ? "The quantity is the total of the variations." : nil
        default:
            break
        }
        return field
    }

    /// Sentences that follow the current variations, not the last form load.
    static func notes(varied: Bool) -> [String] {
        varied ? ["With variations, Price is the price of any variation left without its own. eBay takes no offers on a listing with variations."] : []
    }

    /// The category controls: each stores an id, with its breadcrumb beside it.
    static let categoryPathKeys = [
        "direct_category_ids.ebay": "direct_category_ids.ebay_path",
        "ebay_specifics.secondaryCategoryId": "ebay_specifics.secondaryCategoryPath",
    ]
}

/// Fields behind a link ("See title options", "Show more"). A saved value
/// starts its link open; the seller's own choice wins after that.
enum NativeReveal {
    /// The links in a section, in field order: "See …" links sit above the fields, the rest below.
    static func links(_ fields: [NativeField]) -> (top: [String], bottom: [String]) {
        var names: [String] = []
        for field in fields { if let reveal = field.reveal, !names.contains(reveal) { names.append(reveal) } }
        return (names.filter { $0.hasPrefix("See ") }, names.filter { !$0.hasPrefix("See ") })
    }

    /// A value that opens its link: present, and not false.
    static func opens(_ value: JSONValue?) -> Bool {
        switch value {
        case nil, .null, .bool(false): return false
        case .string(let text): return !text.isEmpty && text != "false"
        case .array(let items): return !items.isEmpty
        case .object(let object): return !object.isEmpty
        default: return true
        }
    }

    /// What the link says: "Show more" becomes "Show less" while open.
    static func title(_ reveal: String, open: Bool) -> String { open && reveal == "Show more" ? "Show less" : reveal }
}

/// One eBay leaf category from Anteroom's synced tree.
struct CategoryLeaf: Identifiable, Hashable, Sendable {
    let id: String
    let path: String
}

/// `GET /api/catalog/categories?marketplace=ebay`.
struct CatalogCategories: Decodable, Sendable {
    struct Node: Decodable, Sendable {
        let id: String?
        let parentID: String?
        let path: String
        let label: String
        let isLeaf: Bool?
        let hasChildren: Bool?

        enum CodingKeys: String, CodingKey {
            case id, path, label
            case parentID = "parent_id"
            case isLeaf = "is_leaf"
            case hasChildren = "has_children"
        }
    }

    let coverage: String?
    let complete: Bool?
    let nodes: [Node]

    /// Final categories with an id, as the web offers them (catalogLeafChoices).
    var leaves: [CategoryLeaf] {
        guard coverage == "tree" else { return [] }
        return nodes.compactMap { node in
            guard node.isLeaf == true || node.hasChildren == false, let id = node.id, !id.isEmpty, !node.path.isEmpty else { return nil }
            return CategoryLeaf(id: id, path: node.path)
        }
    }

    /// Search stays available until the tree is complete.
    var needsSearch: Bool { leaves.isEmpty || complete == false }
}

enum CategoryBrowse {
    /// The next level under `prefix` (path segments): each child label and whether it is a final category.
    static func children(of prefix: [String], in leaves: [CategoryLeaf]) -> [(label: String, leaf: CategoryLeaf?)] {
        var order: [String] = []
        var finals: [String: CategoryLeaf] = [:]
        var branches: Set<String> = []
        for leaf in leaves {
            let parts = leaf.path.components(separatedBy: " > ")
            guard parts.count > prefix.count, Array(parts.prefix(prefix.count)) == prefix else { continue }
            let label = parts[prefix.count]
            if !order.contains(label) { order.append(label) }
            if parts.count == prefix.count + 1 { finals[label] = leaf } else { branches.insert(label) }
        }
        return order.map { ($0, branches.contains($0) ? nil : finals[$0]) }
    }

    /// Leaves whose path holds every word typed, best first: the shorter path wins.
    static func search(_ query: String, in leaves: [CategoryLeaf], limit: Int = 50) -> [CategoryLeaf] {
        let words = query.lowercased().split(whereSeparator: \.isWhitespace).map(String.init)
        guard !words.isEmpty else { return [] }
        return Array(leaves.filter { leaf in
            let path = leaf.path.lowercased()
            return words.allSatisfy { path.contains($0) }
        }.sorted { $0.path.count < $1.path.count }.prefix(limit))
    }

    /// A breadcrumb as the web writes it.
    static func breadcrumb(_ path: String) -> String { path.components(separatedBy: " > ").joined(separator: " › ") }
}
