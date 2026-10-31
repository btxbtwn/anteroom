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
