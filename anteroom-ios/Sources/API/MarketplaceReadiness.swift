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

        /// Required fields still empty, each counted once.
        var missingRequired: Int { Set(gaps.filter(\.blocking).map(\.field)).count }
    }

    let revisionID: String?
    let marketplaces: [String: Check]

    enum CodingKeys: String, CodingKey {
        case revisionID = "revision_id"
        case marketplaces
    }
}

/// What Anteroom last did on a marketplace (`/native-marketplaces`).
struct NativeDraftState: Decodable, Equatable {
    let state: String?
    let running: Bool?
}

/// A marketplace listing Anteroom knows about (`/marketplace-listings`).
struct MarketplaceLiveListing: Decodable, Equatable {
    let listingID: String?
    let soldHere: Bool?
    let state: String?

    enum CodingKeys: String, CodingKey {
        case listingID = "listing_id"
        case soldHere = "sold_here"
        case state
    }
}

/// One marketplace's card in List everywhere, and its line on the form picker.
struct ListChoice: Equatable, Identifiable {
    enum Tone: Equatable { case ready, needs, listed, sold, muted }

    let marketplace: String
    let label: String
    let tone: Tone
    let selectable: Bool

    var id: String { marketplace }

    /// The same rules as the web picker: a live or sold listing is not listed again,
    /// and missing fields are advice, so the seller may still pick it.
    static func make(_ marketplace: String, canAct: Bool, check: NativePreflight.Check?,
                     draft: NativeDraftState?, live: MarketplaceLiveListing?, soldSomewhere: Bool) -> ListChoice {
        func choice(_ label: String, _ tone: Tone, _ selectable: Bool = false) -> ListChoice {
            ListChoice(marketplace: marketplace, label: label, tone: tone, selectable: selectable)
        }
        if live?.soldHere == true { return choice("Sold", .sold) }
        let removed = ["deleted", "deleting"].contains(live?.state ?? "")
        if live?.listingID != nil && !removed { return choice("Listed", .listed) }
        let state = draft?.state ?? ""
        if draft?.running == true || ["saving", "publishing", "updating"].contains(state) { return choice("Working…", .muted) }
        if state == "unknown" { return choice("Unconfirmed", .muted) }
        if state == "published" { return choice("Listed", .listed) }
        if state == "pending_review" { return choice("In review", .muted) }
        if !canAct { return choice("Not available here", .muted) }
        if soldSomewhere { return choice("Not listed", .muted) }
        if let check {
            let missing = check.missingRequired
            if missing > 0 { return choice("Needs \(missing) \(missing == 1 ? "field" : "fields")", .needs, true) }
            if check.ready { return choice("Ready", .ready, true) }
        }
        return choice("Not checked", .muted, true)
    }

    /// The marketplaces "Select all ready" picks.
    static func ready(_ choices: [ListChoice]) -> [String] {
        choices.filter { $0.selectable && $0.tone == .ready }.map(\.marketplace)
    }
}
