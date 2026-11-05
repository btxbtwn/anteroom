import Foundation

/// The server's ranked next steps (SuggestionsPanel.tsx). Each shows as a hint
/// on its listing's own inventory row, so no listing appears twice.
struct Suggestion: Decodable, Identifiable, Sendable {
    let conversationID: String
    let kind: String
    let title: String
    let reason: String
    let coverPhotoURL: String?
    let ageDays: Int?
    let similarSold: Int?
    let seasonFit: String?

    var id: String { conversationID + kind }

    enum CodingKeys: String, CodingKey {
        case kind, title, reason
        case conversationID = "conversation_id"
        case coverPhotoURL = "cover_photo_url"
        case ageDays = "age_days"
        case similarSold = "similar_sold"
        case seasonFit = "season_fit"
    }

    static let rowHints = [
        "failed": "Last send failed",
        "ready_to_generate": "Ready to generate",
        "fix_validation": "Fields to fix",
        "stale_active": "Stale",
        "ready_to_review": "Needs review",
    ]

    /// "Ready to generate", or "Stale 45 days · 3 like it sold · In season".
    var hint: String {
        let hint = Self.rowHints[kind] ?? kind.replacingOccurrences(of: "_", with: " ")
        guard kind == "stale_active", let ageDays else { return hint }
        var parts = ["\(hint) \(ageDays == 1 ? "1 day" : "\(ageDays) days")"]
        if let similarSold, similarSold > 0 { parts.append("\(similarSold) like it sold") }
        if seasonFit == "in" { parts.append("In season") }
        return parts.joined(separator: " · ")
    }

    /// Each listing's hint, keyed by its id.
    static func hints(_ suggestions: [Suggestion]) -> [String: String] {
        Dictionary(suggestions.map { ($0.conversationID, $0.hint) }, uniquingKeysWith: { first, _ in first })
    }
}
