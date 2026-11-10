import Foundation

/// The saved listing's field check per marketplace
/// (`/api/conversations/{id}/native-marketplaces/preflight`).
struct NativePreflight: Decodable, Equatable {
    struct Gap: Decodable, Equatable {
        let field: String
        let message: String
        let blocking: Bool
    }

    struct Check: Decodable, Equatable {
        let ready: Bool
        let gaps: [Gap]
        /// What the marketplace still needs, counted once by the server: required
        /// fields left empty and fields it rejected on the last List.
        let missing: [MissingField]
    }

    let revisionID: String?
    let marketplaces: [String: Check]

    enum CodingKeys: String, CodingKey {
        case revisionID = "revision_id"
        case marketplaces
    }
}

/// A field a marketplace still needs, as the server names it.
struct MissingField: Decodable, Equatable, Hashable, Sendable {
    let field: String
    let label: String
    let message: String
}

/// "Needs 2 fields".
func needsText(_ count: Int) -> String { "Needs \(count) \(count == 1 ? "field" : "fields")" }

/// What the marketplace's form and List picks name.
func missingNames(_ missing: [MissingField]) -> String { missing.map(\.label).joined(separator: ", ") }

/// What Anteroom last did on a marketplace (`/native-marketplaces`, the web's `NativeDraft`).
struct NativeDraftState: Decodable, Equatable {
    struct Progress: Decodable, Equatable { let label: String }

    let state: String?
    let running: Bool?
    // What List and delist shows beside the state.
    var action: String? = nil
    var draftURL: String? = nil
    var listingURL: String? = nil
    var revisionID: String? = nil
    var error: String? = nil
    var errorCode: String? = nil
    var marketplaceMessage: String? = nil
    var retryAfter: String? = nil
    var progress: Progress? = nil

    enum CodingKeys: String, CodingKey {
        case state, running, action, error, progress
        case draftURL = "draft_url"
        case listingURL = "listing_url"
        case revisionID = "revision_id"
        case errorCode = "error_code"
        case marketplaceMessage = "marketplace_message"
        case retryAfter = "retry_after"
    }
}

/// A marketplace listing Anteroom knows about (`/marketplace-listings`).
struct MarketplaceLiveListing: Decodable, Equatable {
    let listingID: String?
    let soldHere: Bool?
    let state: String?
    // What List and delist shows: the listing's page and a failed Delist's reason.
    var url: String? = nil
    var error: String? = nil
    var marketplaceMessage: String? = nil

    enum CodingKeys: String, CodingKey {
        case listingID = "listing_id"
        case soldHere = "sold_here"
        case state, url, error
        case marketplaceMessage = "marketplace_message"
    }
}

/// One marketplace's card in List everywhere, and its line on the form picker.
struct ListChoice: Equatable, Identifiable {
    enum Tone: Equatable { case ready, needs, listed, sold, muted }

    let marketplace: String
    let label: String
    let tone: Tone
    let selectable: Bool
    /// The fields still needed, by name, when the label counts some.
    var detail: String? = nil
    /// The same fields, to open one.
    var missing: [MissingField] = []

    var id: String { marketplace }

    /// The same rules as the web picker: a live or sold listing is not listed again,
    /// and missing fields are advice, so the seller may still pick it.
    static func make(_ marketplace: String, canAct: Bool, check: NativePreflight.Check?,
                     draft: NativeDraftState?, live: MarketplaceLiveListing?, soldSomewhere: Bool) -> ListChoice {
        func choice(_ label: String, _ tone: Tone, _ selectable: Bool = false, detail: String? = nil,
                    missing: [MissingField] = []) -> ListChoice {
            ListChoice(marketplace: marketplace, label: label, tone: tone, selectable: selectable, detail: detail, missing: missing)
        }
        if live?.soldHere == true { return choice("Sold", .sold) }
        let removed = ["deleted", "deleting"].contains(live?.state ?? "")
        if live?.listingID != nil && !removed { return choice("Listed", .listed) }
        let state = draft?.state ?? ""
        if draft?.running == true || ["saving", "publishing", "updating"].contains(state) { return choice("Working…", .muted) }
        if state == "unknown" { return choice("Unconfirmed", .muted) }
        // After a delist the record may still read published; the delete wins.
        if state == "published" && !removed { return choice("Listed", .listed) }
        if state == "pending_review" && !removed { return choice("In review", .muted) }
        if !canAct { return choice("Not available here", .muted) }
        if soldSomewhere { return choice("Not listed", .muted) }
        if let check {
            if !check.missing.isEmpty {
                return choice(needsText(check.missing.count), .needs, true, detail: missingNames(check.missing),
                              missing: check.missing)
            }
            if check.ready { return choice("Ready", .ready, true) }
        }
        return choice("Not checked", .muted, true)
    }

    /// The marketplaces "Select all ready" picks.
    static func ready(_ choices: [ListChoice]) -> [String] {
        choices.filter { $0.selectable && $0.tone == .ready }.map(\.marketplace)
    }
}
