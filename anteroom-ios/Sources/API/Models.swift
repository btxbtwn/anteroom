import Foundation

// The listing contract's models (ConversationSummary, Conversation, DirectRecord,
// ListingDocument, ListingSave, ListingSaveResult, ListingPhoto, PhotoEdits, CropBox,
// WorkspaceRuntime) are generated into Generated/APIContract.swift; this file adds
// the phone's own behavior to them.

extension ConversationSummary {
    /// A listing known only by its id and title, enough to open it from another
    /// screen; the editor loads the rest.
    init(id: String, title: String?, coverPhotoURL: String? = nil) {
        self.init(id: id, title: title, status: "", createdAt: "", updatedAt: "", coverPhotoURL: coverPhotoURL)
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

    /// The cover's content version (`v=`), which lets the thumbnail be kept.
    var coverPhotoVersion: String? {
        coverPhotoURL.flatMap { URLComponents(string: $0)?.queryItems?.first { $0.name == "v" }?.value }
    }
}

/// `GET /api/conversations/{id}/screen`: the editor's core reads in one
/// request, as the web app batches them (`listingScreen.ts`).
struct ListingScreen: Decodable, Sendable {
    let conversation: Conversation
    let listing: ListingDocument
    let photos: [ListingPhoto]
}

/// Any JSON value, so a listing's marketplace data survives an edit untouched.
enum JSONValue: Codable, Hashable, Sendable {
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

/// The General fields the editor shows, read from and written back into the
/// full listing. Only fields the seller changed are written, so values the
/// phone does not edit (marketplace specifics, categories, tags) keep their
/// exact type and content.
struct GeneralFields: Equatable, Sendable, Codable {
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

extension ListingPhoto {
    /// The content checksum the server puts in the photo URL; it changes when
    /// an edit renders a new file, so caches key on it.
    var version: String? {
        URLComponents(string: url)?.queryItems?.first { $0.name == "v" }?.value
    }

    var isEdited: Bool { !(edits ?? PhotoEdits()).isOriginal }
}

/// The desktop photo editor's adjustments (`services/photo_edits.py`). They
/// always render from the original upload, which the server keeps.
extension PhotoEdits {
    var isOriginal: Bool {
        let fullFrame = crop.map { $0.width >= 0.9999 && $0.height >= 0.9999 } ?? true
        var plain = PhotoEdits()
        plain.crop = crop
        return fullFrame && self == plain
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
