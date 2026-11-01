import Foundation

/// One inventory row from `GET /api/conversations`, the list the desktop
/// sidebar filters and sorts locally. Timestamps stay as the server's strings.
struct ListingSummary: Codable, Identifiable, Hashable, Sendable {
    let id: String
    var title: String?
    var status: String?
    var settledAt: String?
    var unsettledAt: String?
    var createdAt: String?
    var updatedAt: String?
    var sku: String?
    var price: Double?
    var quantity: Int?
    var directMarketplaces: [String]?
    var coverPhotoURL: String?

    enum CodingKeys: String, CodingKey {
        case id, title, status, sku, price, quantity
        case settledAt = "settled_at"
        case unsettledAt = "unsettled_at"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case directMarketplaces = "direct_marketplaces"
        case coverPhotoURL = "cover_photo_url"
    }

    var displayTitle: String {
        let trimmed = (title ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "Untitled" : trimmed
    }

    /// The cover photo's id, read from the thumbnail URL the server builds.
    var coverPhotoID: String? {
        guard let url = coverPhotoURL, let components = URLComponents(string: url) else { return nil }
        let parts = components.path.split(separator: "/")
        guard parts.count >= 3, parts[parts.count - 3] == "photos", parts.last == "thumb" else { return nil }
        return String(parts[parts.count - 2])
    }
}

/// `GET /api/conversations/{id}`: the summary plus the seller's notes.
struct ConversationDetail: Decodable, Sendable {
    let id: String
    let title: String?
    let status: String?
    let notes: String?
}

/// Any JSON value, so a listing's marketplace data survives an edit untouched.
enum JSONValue: Codable, Equatable, Sendable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Int.self) {
            self = .int(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            self = .object(try container.decode([String: JSONValue].self))
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null: try container.encodeNil()
        case .bool(let value): try container.encode(value)
        case .int(let value): try container.encode(value)
        case .double(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        }
    }

    var text: String {
        switch self {
        case .string(let value): return value
        case .int(let value): return String(value)
        case .double(let value): return GeneralFields.text(value)
        default: return ""
        }
    }

    var number: Double? {
        switch self {
        case .int(let value): return Double(value)
        case .double(let value): return value
        case .string(let value): return Double(value)
        default: return nil
        }
    }
}

/// `GET /api/conversations/{id}/listing`: the current revision and its pointer.
struct ListingDocument: Decodable, Sendable {
    let currentRevisionID: String?
    let listing: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case listing
        case currentRevisionID = "current_revision_id"
    }
}

struct ListingSave: Encodable, Sendable {
    let listing: [String: JSONValue]
    let expectedRevisionID: String

    enum CodingKeys: String, CodingKey {
        case listing
        case expectedRevisionID = "expected_revision_id"
    }
}

struct ListingSaveResult: Decodable, Sendable {
    let revisionID: String?
    let listing: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case listing
        case revisionID = "revision_id"
    }
}

/// The General fields the editor shows, read from and written back into the
/// full listing. Only fields the seller changed are written, so values the
/// phone does not edit (marketplace specifics, categories, tags) keep their
/// exact type and content.
struct GeneralFields: Equatable, Sendable {
    var title = ""
    var description = ""
    var price: Double?
    var cost: Double?
    var quantity: Int?
    var brand = ""
    var condition = ""
    var primaryColor = ""
    var secondaryColor = ""
    var size = ""
    var sku = ""
    var labels: [String] = []
    var zipCode = ""
    var categoryPath = ""
    var department = ""
    var weightOz: Double?
    var packageDimensionsIn = ""

    private static var textKeys: [(String, WritableKeyPath<GeneralFields, String>)] { [
        ("title", \.title), ("description", \.description), ("brand", \.brand), ("condition", \.condition),
        ("primaryColor", \.primaryColor), ("secondaryColor", \.secondaryColor), ("size", \.size), ("sku", \.sku),
        ("zipCode", \.zipCode), ("category_path", \.categoryPath), ("department", \.department),
        ("package_dimensions_in", \.packageDimensionsIn),
    ] }
    private static var numberKeys: [(String, WritableKeyPath<GeneralFields, Double?>)] { [
        ("price", \.price), ("cost", \.cost), ("weight_oz", \.weightOz),
    ] }

    init() {}

    init(listing: [String: JSONValue]) {
        for (key, path) in Self.textKeys { self[keyPath: path] = listing[key]?.text ?? "" }
        for (key, path) in Self.numberKeys { self[keyPath: path] = listing[key]?.number }
        if let value = listing["quantity"]?.number, value.rounded() == value { quantity = Int(value) }
        if case .array(let values)? = listing["labels"] {
            labels = values.compactMap { if case .string(let label) = $0 { return label } else { return nil } }
        }
    }

    /// `listing` with every field that differs from `original` written in.
    func applied(to listing: [String: JSONValue], changedFrom original: GeneralFields) -> [String: JSONValue] {
        var result = listing
        for (key, path) in Self.textKeys where self[keyPath: path] != original[keyPath: path] {
            result[key] = .string(self[keyPath: path])
        }
        for (key, path) in Self.numberKeys where self[keyPath: path] != original[keyPath: path] {
            result[key] = self[keyPath: path].map(JSONValue.double) ?? .null
        }
        if quantity != original.quantity { result["quantity"] = quantity.map(JSONValue.int) ?? .null }
        if labels != original.labels { result["labels"] = .array(labels.map(JSONValue.string)) }
        return result
    }

    static func text(_ value: Double?) -> String {
        guard let value else { return "" }
        return value.formatted(.number.grouping(.never).precision(.fractionLength(0...2))
            .locale(Locale(identifier: "en_US_POSIX")))
    }
}

struct ListingPhoto: Codable, Identifiable, Hashable, Sendable {
    let id: String
    let width: Int?
    let height: Int?
    let sizeBytes: Int?

    enum CodingKeys: String, CodingKey {
        case id, width, height
        case sizeBytes = "size_bytes"
    }
}

/// The server writes UTC timestamps without a zone and with microseconds.
enum ServerDate {
    nonisolated(unsafe) private static let formatter = ISO8601DateFormatter()

    static func parse(_ raw: String?) -> Date? {
        guard var text = raw, text.count >= 19 else { return nil }
        if let dot = text.firstIndex(of: ".") {
            let rest = text[text.index(after: dot)...].drop(while: \.isNumber)
            text = String(text[..<dot]) + rest
        }
        let zone = text.dropFirst(19)
        if !(zone.hasPrefix("Z") || zone.hasPrefix("+") || zone.hasPrefix("-")) {
            text += "Z"
        }
        return formatter.date(from: text)
    }
}
