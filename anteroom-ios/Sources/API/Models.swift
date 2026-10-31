import Foundation

/// A draft as listed in inventory. Timestamps stay as the server's strings so
/// a save can echo `updated_at` back exactly for its conflict check.
struct Draft: Codable, Identifiable, Hashable, Sendable {
    let id: String
    var title: String?
    var notes: String?
    let createdAt: String?
    var updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title, notes
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    var displayTitle: String {
        let trimmed = (title ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "Untitled" : trimmed
    }

    var updatedDate: Date? { ServerDate.parse(updatedAt) }
}

/// The General fields the hosted API accepts. A save replaces all of them, so
/// the editor always sends back the full set it loaded.
struct DraftFields: Codable, Equatable, Sendable {
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

    enum CodingKeys: String, CodingKey {
        case description, price, cost, quantity, brand, condition, primaryColor, secondaryColor
        case size, sku, labels, zipCode, department
        case categoryPath = "category_path"
        case weightOz = "weight_oz"
        case packageDimensionsIn = "package_dimensions_in"
    }
}

struct DraftDetail: Decodable, Sendable {
    let id: String
    let title: String?
    let notes: String?
    let createdAt: String?
    let updatedAt: String?
    let fields: DraftFields

    enum CodingKeys: String, CodingKey {
        case id, title, notes, fields
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    var summary: Draft {
        Draft(id: id, title: title, notes: notes, createdAt: createdAt, updatedAt: updatedAt)
    }
}

struct DraftSave: Encodable, Sendable {
    let title: String
    let notes: String
    let expectedUpdatedAt: String
    let fields: DraftFields

    enum CodingKeys: String, CodingKey {
        case title, notes, fields
        case expectedUpdatedAt = "expected_updated_at"
    }
}

struct DraftPhoto: Codable, Identifiable, Hashable, Sendable {
    let id: String
    let width: Int?
    let height: Int?
    let sizeBytes: Int

    enum CodingKeys: String, CodingKey {
        case id, width, height
        case sizeBytes = "size_bytes"
    }
}

/// The server writes UTC timestamps without a zone and with microseconds.
enum ServerDate {
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
        return ISO8601DateFormatter().date(from: text)
    }
}
