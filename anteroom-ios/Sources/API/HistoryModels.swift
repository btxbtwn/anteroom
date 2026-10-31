import Foundation

struct ListingRevision: Decodable, Identifiable, Hashable, Sendable {
    let id: String
    let source: String
    let title: String
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, source, title
        case createdAt = "created_at"
    }
}

struct ListingRevisionPointer: Decodable, Sendable {
    let currentRevisionID: String?
    enum CodingKeys: String, CodingKey { case currentRevisionID = "current_revision_id" }
}

/// Read-only snapshot. The server restores the complete revision; the client
/// never reconstructs a saved listing from this preview's displayed fields.
struct RevisionSnapshot: Decodable, Sendable {
    let id: String
    let listing: [String: RevisionValue]
}

indirect enum RevisionValue: Decodable, Sendable {
    case string(String), number(Double), bool(Bool), null
    case array([RevisionValue]), object([String: RevisionValue])

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let text = try? value.decode(String.self) { self = .string(text) }
        else if let flag = try? value.decode(Bool.self) { self = .bool(flag) }
        else if let number = try? value.decode(Double.self) { self = .number(number) }
        else if let items = try? value.decode([RevisionValue].self) { self = .array(items) }
        else { self = .object(try value.decode([String: RevisionValue].self)) }
    }

    var displayText: String? {
        switch self {
        case .string(let text): return text.isEmpty ? nil : text
        case .number(let number): return number.formatted(.number.grouping(.never))
        case .bool(let flag): return flag ? "Yes" : "No"
        case .array(let values):
            let texts = values.compactMap(\.displayText)
            return texts.isEmpty ? nil : texts.joined(separator: ", ")
        case .null, .object: return nil
        }
    }
}
