import Foundation

/// Reviewed bulk edits and deletes (`/api/listings/bulk-edit`, `/bulk-delete`),
/// the same preview-then-apply contract the desktop dialogs use.

enum ListMode: String, CaseIterable, Identifiable, Codable, Sendable {
    case keep, add, remove, replace
    var id: String { rawValue }
    var label: String { rawValue.capitalized }
}

/// Only the fields the seller chose are encoded; the server applies exactly those.
struct BulkEdits: Encodable, Equatable, Sendable {
    var values: [String: JSONValue] = [:]
    var labelsMode: ListMode = .keep
    var labels: [String] = []

    enum CodingKeys: String, CodingKey {
        case values, labels
        case labelsMode = "labels_mode"
    }
}

struct BulkChange: Decodable, Sendable {
    let field: String
    let old: JSONValue?
    let new: JSONValue?
}

struct BulkEditReviewRow: Decodable, Identifiable, Sendable {
    let conversationID: String
    let title: String
    let skipReason: String?
    let changes: [BulkChange]
    let expectedRevisionID: String?
    let expectedBasis: String?
    let expectedResult: String?

    var id: String { conversationID }
    var applicable: Bool { skipReason == nil && !changes.isEmpty && token != nil }

    var token: BulkReviewToken? {
        guard let expectedRevisionID, let expectedBasis, let expectedResult else { return nil }
        return BulkReviewToken(conversationID: conversationID, expectedRevisionID: expectedRevisionID,
                               expectedBasis: expectedBasis, expectedResult: expectedResult)
    }

    enum CodingKeys: String, CodingKey {
        case title, changes
        case conversationID = "conversation_id"
        case skipReason = "skip_reason"
        case expectedRevisionID = "expected_revision_id"
        case expectedBasis = "expected_basis"
        case expectedResult = "expected_result"
    }
}

struct BulkReviewToken: Encodable, Sendable {
    let conversationID: String
    let expectedRevisionID: String
    let expectedBasis: String
    let expectedResult: String

    enum CodingKeys: String, CodingKey {
        case conversationID = "conversation_id"
        case expectedRevisionID = "expected_revision_id"
        case expectedBasis = "expected_basis"
        case expectedResult = "expected_result"
    }
}

struct BulkSkipped: Decodable, Sendable {
    let conversationID: String
    let reason: String
    enum CodingKeys: String, CodingKey {
        case reason
        case conversationID = "conversation_id"
    }
}

struct BulkEditResult: Decodable, Sendable {
    struct Applied: Decodable, Sendable {
        let conversationID: String
        enum CodingKeys: String, CodingKey { case conversationID = "conversation_id" }
    }
    let applied: [Applied]
    let skipped: [BulkSkipped]
}

struct BulkDeleteReviewRow: Decodable, Identifiable, Sendable {
    let conversationID: String
    let title: String?
    let skipReason: String?
    let warning: String?
    let reviewToken: String?

    var id: String { conversationID }
    var deletable: Bool { skipReason == nil && reviewToken != nil }

    enum CodingKeys: String, CodingKey {
        case title, warning
        case conversationID = "conversation_id"
        case skipReason = "skip_reason"
        case reviewToken = "review_token"
    }
}

struct BulkDeleteResult: Decodable, Sendable {
    let deleted: [String]
    let skipped: [BulkSkipped]
}

extension JSONValue {
    /// How a changed value reads in a review row.
    var reviewText: String {
        switch self {
        case .null: return "—"
        case .bool(let value): return value ? "Yes" : "No"
        case .array(let values): return values.map(\.reviewText).joined(separator: ", ")
        case .object: return "…"
        default: return text.isEmpty ? "—" : text
        }
    }
}

/// One source item in a copy review (`/api/listings/copies/preview`).
struct CopyReviewRow: Decodable, Identifiable, Sendable {
    let sourceID: String
    let sourceTitle: String
    let skipReason: String?
    let expectedBasis: String?
    let photoCount: Int?
    let title: String?

    var id: String { sourceID }

    enum CodingKeys: String, CodingKey {
        case title
        case sourceID = "source_id"
        case sourceTitle = "source_title"
        case skipReason = "skip_reason"
        case expectedBasis = "expected_basis"
        case photoCount = "photo_count"
    }
}

/// The approved values for one copy. The request ID stays fixed across
/// retries so a lost reply never creates a second draft.
struct CopyRequest: Encodable, Sendable {
    let requestID: String
    let expectedBasis: String
    let title: String
    let sku: String
    let quantity: Int
    let includePhotos: Bool
    let includeCost: Bool

    enum CodingKeys: String, CodingKey {
        case title, sku, quantity
        case requestID = "request_id"
        case expectedBasis = "expected_basis"
        case includePhotos = "include_photos"
        case includeCost = "include_cost"
    }
}

/// A suggested markdown from the seller's own sales (`/api/price-drop/batch/preview`).
struct MarkdownReviewRow: Decodable, Identifiable, Sendable {
    let conversationID: String
    let title: String
    let revisionID: String?
    let currentPrice: Double?
    let suggestedPrice: Double?
    let suggestedReason: String?
    let ageDays: Int?
    let skipReason: String?

    var id: String { conversationID }

    enum CodingKeys: String, CodingKey {
        case title
        case conversationID = "conversation_id"
        case revisionID = "revision_id"
        case currentPrice = "current_price"
        case suggestedPrice = "suggested_price"
        case suggestedReason = "suggested_reason"
        case ageDays = "age_days"
        case skipReason = "skip_reason"
    }
}

struct MarkdownDrop: Encodable, Sendable {
    let conversationID: String
    let price: Double
    let expectedRevisionID: String

    enum CodingKeys: String, CodingKey {
        case price
        case conversationID = "conversation_id"
        case expectedRevisionID = "expected_revision_id"
    }
}

struct MarkdownResult: Decodable, Sendable {
    struct Applied: Decodable, Sendable {
        let conversationID: String
        let price: Double
        let previousPrice: Double?
        enum CodingKeys: String, CodingKey {
            case price
            case conversationID = "conversation_id"
            case previousPrice = "previous_price"
        }
    }
    let applied: [Applied]
    let skipped: [BulkSkipped]
}
