import Foundation

/// A direct marketplace tab, built by the server from that marketplace's own
/// listing form (`/api/conversations/{id}/native-marketplaces/{m}/form`):
/// the same fields, labels, sections and order, no more and no fewer.
struct NativeForm: Decodable, Sendable {
    let marketplace: String
    let categoryID: String?
    let fields: [NativeField]

    enum CodingKeys: String, CodingKey {
        case marketplace, fields
        case categoryID = "category_id"
    }
}

struct NativeField: Decodable, Identifiable, Sendable {
    let key: String
    let path: String
    let label: String
    let value: JSONValue?
    let required: Bool
    let multi: Bool
    let selectionOnly: Bool
    let options: [String]
    let optionValues: [String: String]
    let inputType: String
    let help: String?
    let section: String?
    let half: Bool
    let locked: Bool
    let maxLength: Int?
    let maxValues: Int?
    let placeholder: String?
    let clears: [String]
    let categorySpecific: Bool
    let inventoryGroups: [InventoryGroup]
    let optionAmounts: [String: Double]
    let inventorySystem: String?

    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, path, label, value, required, multi, options, help, section, locked, placeholder, clears, width
        case selectionOnly = "selection_only"
        case optionValues = "option_values"
        case inputType = "input_type"
        case controlWidth = "control_width"
        case maxLength = "max_length"
        case maxValues = "max_values"
        case categorySpecific = "category_specific"
        case inventoryGroups = "inventory_groups"
        case optionAmounts = "option_amounts"
        case inventorySystem = "inventory_system"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        path = try c.decodeIfPresent(String.self, forKey: .path) ?? key
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        value = try c.decodeIfPresent(JSONValue.self, forKey: .value)
        required = try c.decodeIfPresent(Bool.self, forKey: .required) ?? false
        multi = try c.decodeIfPresent(Bool.self, forKey: .multi) ?? false
        selectionOnly = try c.decodeIfPresent(Bool.self, forKey: .selectionOnly) ?? false
        options = try c.decodeIfPresent([String].self, forKey: .options) ?? []
        optionValues = try c.decodeIfPresent([String: String].self, forKey: .optionValues) ?? [:]
        inputType = try c.decodeIfPresent(String.self, forKey: .inputType) ?? "text"
        help = try c.decodeIfPresent(String.self, forKey: .help)
        section = try c.decodeIfPresent(String.self, forKey: .section)
        let width = try c.decodeIfPresent(String.self, forKey: .controlWidth) ?? c.decodeIfPresent(String.self, forKey: .width)
        half = width == "half"
        locked = try c.decodeIfPresent(Bool.self, forKey: .locked) ?? false
        maxLength = try c.decodeIfPresent(Int.self, forKey: .maxLength)
        maxValues = try c.decodeIfPresent(Int.self, forKey: .maxValues)
        placeholder = try c.decodeIfPresent(String.self, forKey: .placeholder)
        clears = try c.decodeIfPresent([String].self, forKey: .clears) ?? []
        categorySpecific = try c.decodeIfPresent(Bool.self, forKey: .categorySpecific) ?? false
        inventoryGroups = (try? c.decodeIfPresent([InventoryGroup].self, forKey: .inventoryGroups)) ?? []
        optionAmounts = (try? c.decodeIfPresent([String: Double].self, forKey: .optionAmounts)) ?? [:]
        inventorySystem = try c.decodeIfPresent(String.self, forKey: .inventorySystem)
    }

    /// The label a stored value is shown as.
    func label(for stored: String) -> String {
        optionValues.first { $0.value == stored }?.key ?? stored
    }

    /// The value a chosen label is stored as.
    func stored(for label: String) -> String {
        optionValues[label] ?? label
    }
}

/// Poshmark's size sets: sizes per set, and the same sizes per sizing system.
struct InventoryGroup: Decodable, Sendable {
    struct Size: Decodable, Sendable {
        let id: String
        let label: String
        let equivalents: [String: String]?
    }

    let id: String
    let label: String
    let sizes: [Size]
    let systems: [String: [Size]]?

    func sizes(in system: String) -> [Size] { systems?[system] ?? sizes }
}

/// One Poshmark inventory row.
struct InventoryRow: Equatable, Sendable {
    var sizeSetID = ""
    var sizeID = ""
    var quantity = 1
    var sku = ""

    init(sizeSetID: String = "", sizeID: String = "", quantity: Int = 1, sku: String = "") {
        self.sizeSetID = sizeSetID
        self.sizeID = sizeID
        self.quantity = quantity
        self.sku = sku
    }

    init?(_ value: JSONValue) {
        guard case .object(let row) = value else { return nil }
        sizeSetID = row["sizeSetId"]?.text ?? ""
        sizeID = row["sizeId"]?.text ?? ""
        quantity = row["quantity"]?.number.map { Int($0) } ?? 1
        sku = row["sku"]?.text ?? ""
    }

    var json: JSONValue {
        .object(["sizeSetId": .string(sizeSetID), "sizeId": .string(sizeID), "quantity": .int(quantity), "sku": .string(sku)])
    }
}

extension Dictionary where Key == String, Value == JSONValue {
    /// The value at a dotted listing path ("poshmark_specifics.smartSell.enabled").
    func value(at path: String) -> JSONValue? {
        var current: JSONValue = .object(self)
        for part in path.split(separator: ".").map(String.init) {
            guard case .object(let object) = current, let next = object[part] else { return nil }
            current = next
        }
        return current
    }

    /// Writes a value at a dotted path, creating objects on the way and
    /// leaving every sibling value untouched.
    mutating func set(_ value: JSONValue, at path: String) {
        let parts = path.split(separator: ".").map(String.init)
        guard let first = parts.first else { return }
        if parts.count == 1 {
            self[first] = value
            return
        }
        var child: [String: JSONValue]
        if case .object(let existing)? = self[first] { child = existing } else { child = [:] }
        child.set(value, at: parts.dropFirst().joined(separator: "."))
        self[first] = .object(child)
    }
}

/// Poshmark's seller fee, read 2026-10-27: $2.95 under $15, 20% from $15,
/// then the seller's shipping discount, floored at $0 (nativeFormStability.ts).
func poshmarkEarnings(price: Double?, discount: Double = 0) -> String {
    guard let price, price > 0 else { return "" }
    let cents = (price * 100).rounded()
    let fee = price < 15 ? 295 : (price * 20).rounded()
    let off = (discount * 100).rounded()
    return String(format: "$%.2f", max(0, cents - fee - off) / 100)
}
