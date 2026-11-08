import Foundation

/// List, Update, Save draft and Delist from the phone, on the hosted contracts
/// the web's Marketplaces pane uses (MarketplacesSection.tsx,
/// routes/hosted_direct_listing.py), over the records in MarketplaceReadiness.swift.
/// eBay and Etsy act through their APIs; Poshmark, Mercari and Depop through
/// the seller's signed-in Anteroom tab in Chrome on their computer. Every
/// action names its marketplace and carries `confirm`; nothing runs on its own.

/// What a marketplace row says, and what it lets the seller do.
struct MarketplaceRow: Equatable, Sendable {
    enum Tone: Equatable, Sendable { case live, sold, neutral, busy, attention, failed, idle }

    let marketplace: String
    let tone: Tone
    let label: String
    var link: URL?
    var linkLabel: String?
    var detail: String?
    var said: String?
    /// What Ask chat sends when chat can fix the refusal by changing the listing.
    var chatPrompt: String?
    /// A List, Update or Delist is running there now.
    var running = false
    /// The last action's outcome is not known yet; Check result before acting again.
    var unconfirmed = false
    var canList = false
    /// Save draft: an unpublished draft on the marketplace, offered wherever List is.
    var canSaveDraft: Bool { canList }
    /// Update: send the saved version to the live listing.
    var canUpdate = false
    /// The live listing holds an older saved version than the one in Anteroom.
    var outdated = false
    var canDelist = false
}

enum MarketplaceActions {
    /// The marketplaces the phone can list on, in the web's order.
    static let order = ["ebay", "poshmark", "mercari", "depop", "etsy"]
    /// Through the seller's Chrome rather than an API.
    static let throughChrome: Set<String> = ["poshmark", "mercari", "depop"]
    /// What Delist does on each marketplace: eBay has no delete.
    static let removed = ["ebay": "Ended", "etsy": "Deleted", "poshmark": "Deleted", "mercari": "Deleted", "depop": "Deleted"]

    private static let busy: Set<String> = ["saving", "publishing", "updating"]

    /// A record whose job is running now. A busy state with no job running is unconfirmed until checked.
    static func isRunning(_ draft: NativeDraftState?) -> Bool {
        guard let draft else { return false }
        if let running = draft.running { return running }
        return busy.contains(draft.state ?? "")
    }

    static func isUnconfirmed(_ draft: NativeDraftState?) -> Bool {
        guard let draft, !isRunning(draft) else { return false }
        return draft.state == "unknown" || busy.contains(draft.state ?? "")
    }

    /// A listing Delist can remove: live, not the one that sold, not already going.
    static func deletable(_ live: MarketplaceLiveListing?) -> Bool {
        guard let live, live.listingID != nil else { return false }
        return live.soldHere != true && live.state != "deleted" && live.state != "deleting"
    }

    /// Seconds before a rate-limited List may run again.
    static func secondsUntil(_ iso: String?, now: Date) -> Int {
        guard let iso, let at = parseDate(iso) else { return 0 }
        return max(0, Int((at.timeIntervalSince(now)).rounded(.up)))
    }

    /// One row per marketplace, by the web's rules (`rowStatus`, `listable`, `deletable`).
    static func rows(actionable: Set<String>, drafts: [String: NativeDraftState], live: [String: MarketplaceLiveListing],
                     revisionID: String?) -> [MarketplaceRow] {
        let soldSomewhere = live.values.contains { $0.soldHere == true }
        return order.filter(actionable.contains).map { marketplace in
            var row = status(marketplace, live: live[marketplace], draft: drafts[marketplace], revisionID: revisionID)
            let draft = drafts[marketplace]
            row.running = isRunning(draft) || live[marketplace]?.state == "deleting"
            row.unconfirmed = isUnconfirmed(draft)
            row.canDelist = !row.running && !row.unconfirmed && deletable(live[marketplace])
            // The web's `updatable` and `outdated`.
            row.canUpdate = !row.running && !row.unconfirmed && row.tone == .live && row.label == "Listed"
            row.outdated = draft?.state == "published" && draft?.revisionID != nil && draft?.revisionID != revisionID
            // A sold item is not listed anywhere else; a live one is updated, not listed again.
            row.canList = !row.running && !row.unconfirmed && !soldSomewhere
                && row.tone != .live && row.label != "Sold here" && row.label != "In review"
                // A delisted listing keeps its ID, but nothing is live there.
                && (live[marketplace]?.listingID == nil || live[marketplace]?.state == "deleted")
            return row
        }
    }

    /// Problems outside the listing: chat cannot sign in, wait out a limit, fix an outage or check a result.
    static let notForChat: Set<String> = ["signed_out", "challenge", "rate_limited", "network", "uncertain"]

    /// The words chat gets for a refused List, as the web's askChatPrompt; nil when chat cannot fix it.
    static func chatPrompt(_ marketplace: String, draft: NativeDraftState) -> String? {
        guard draft.error != nil, !notForChat.contains(draft.errorCode ?? "") else { return nil }
        let name = Marketplaces.name(marketplace)
        var lines = ["Listing this item on \(name) failed. Anteroom said: \(draft.error ?? "the listing was refused.")"]
        if let said = draft.marketplaceMessage { lines.append("\(name) said: \(said)") }
        lines.append("Fill or fix those fields in this listing so \(name) accepts it. Do not list it; I will press List again.")
        lines.append("Then tell me in plain words what you changed, naming fields as the form labels them. No codes or field keys.")
        return lines.joined(separator: "\n")
    }

    /// One status per marketplace. A live listing wins; anything else is not listed.
    static func status(_ marketplace: String, live: MarketplaceLiveListing?, draft: NativeDraftState?, revisionID: String?) -> MarketplaceRow {
        let name = Marketplaces.name(marketplace)
        let said = draft?.marketplaceMessage.map { "\(name) said: \($0)" }
        func row(_ tone: MarketplaceRow.Tone, _ label: String, link: String? = nil, linkLabel: String? = nil,
                 detail: String? = nil, said: String? = nil) -> MarketplaceRow {
            MarketplaceRow(marketplace: marketplace, tone: tone, label: label,
                           link: link.flatMap(URL.init(string:)), linkLabel: link == nil ? nil : linkLabel,
                           detail: detail, said: said)
        }
        if let live, live.soldHere == true { return row(.sold, "Sold here", link: live.url, linkLabel: "View") }
        if let draft, isRunning(draft) {
            let label = draft.state == "updating" ? "Updating…" : draft.action == "save_draft" ? "Saving draft…" : "Listing…"
            return row(.busy, label, detail: draft.progress.map { "\($0.label)…" })
        }
        if let draft, draft.state == "unknown" || busy.contains(draft.state ?? "") {
            return row(.attention, "Unconfirmed", detail: draft.error ?? "Check the result before trying again.", said: said)
        }
        if live?.state == "deleting" { return row(.busy, "Delisting…") }
        if live?.state == "deleted" { return row(.neutral, removed[marketplace] ?? "Deleted") }
        if let live, live.state == "failed" {
            return row(.failed, "Not delisted", link: live.url, linkLabel: "View", detail: live.error,
                       said: live.marketplaceMessage.map { "\(name) said: \($0)" })
        }
        // Anteroom records the revision it last sent, so it knows when the listing has moved on.
        let behind = draft?.state == "published" && draft?.revisionID != nil && draft?.revisionID != revisionID
            ? "Your latest edits aren't on \(name) yet." : nil
        if let live, live.listingID != nil {
            return row(.live, "Listed", link: live.url, linkLabel: "View listing",
                       detail: draft?.state == "published" ? draft?.error ?? behind : nil, said: said)
        }
        if let draft, let state = draft.state, state != "draft" {
            if state == "published" {
                return row(.live, "Listed", link: draft.listingURL, linkLabel: "View listing", detail: draft.error ?? behind)
            }
            if state == "pending_review" { return row(.attention, "In review", link: draft.listingURL, linkLabel: "View") }
            if let error = draft.error {
                var failed = row(.failed, "Not listed", detail: error, said: said)
                failed.chatPrompt = chatPrompt(marketplace, draft: draft)
                return failed
            }
        }
        if let draft, draft.state == "draft" {
            if let error = draft.error {
                var failed = row(.failed, "Not listed", detail: error, said: said)
                failed.chatPrompt = chatPrompt(marketplace, draft: draft)
                return failed
            }
            if draft.draftURL != nil {
                return row(.neutral, "Draft", link: draft.draftURL, linkLabel: "View draft",
                           detail: draft.revisionID != revisionID ? "Changes in Anteroom have not been saved to this draft." : nil)
            }
            if marketplace == "ebay" {
                return row(.neutral, "Saved on eBay", detail: draft.revisionID != revisionID
                           ? "Changes in Anteroom have not been saved to eBay." : "Not listed. Buyers cannot see it until you List.")
            }
        }
        return row(.idle, "Not listed")
    }

    /// The confirmation for List on one marketplace (the web's `listNow`). Several
    /// at once go through List everywhere's picker.
    static func listQuestion(title: String, marketplace: String) -> (title: String, message: String, button: String) {
        let name = Marketplaces.name(marketplace)
        return ("List “\(title)” on \(name)?",
                "This creates a live \(name) listing from the listing saved in Anteroom.\(chromeNote([marketplace]))",
                "List on \(name)")
    }

    /// The confirmation for List everywhere, naming every marketplace picked.
    static func listEverywhereQuestion(title: String, marketplaces: [String]) -> (title: String, message: String, button: String) {
        guard marketplaces.count != 1 else { return listQuestion(title: title, marketplace: marketplaces[0]) }
        let names = marketplaces.map(Marketplaces.name)
        let list = names.dropLast().joined(separator: ", ") + " and " + (names.last ?? "")
        return ("List “\(title)” on \(names.count) marketplaces?",
                "This creates a live listing on \(list) from the listing saved in Anteroom.\(chromeNote(marketplaces))",
                "List on \(names.count) marketplaces")
    }

    /// The confirmation for Update (the web's `updateNow`).
    static func updateQuestion(title: String, marketplace: String) -> (title: String, message: String, button: String) {
        let name = Marketplaces.name(marketplace)
        return ("Update the live \(name) listing for “\(title)” with what's saved in Anteroom?",
                "This changes the listing that is already up.\(chromeNote([marketplace]))",
                "Update on \(name)")
    }

    /// The confirmation for Delist, naming what happens on each marketplace (the web's `runDelist`).
    static func delistQuestion(title: String, marketplaces: [String]) -> (title: String, message: String, button: String) {
        let names = marketplaces.map(Marketplaces.name)
        let lines = marketplaces.map { "• \(Marketplaces.name($0)): \((removed[$0] ?? "Deleted").lowercased())" }.joined(separator: "\n")
        let question = "Delist “\(title)” from \(names.count == 1 ? names[0] : "\(names.count) marketplaces")?"
        return (question, lines + "\nA deleted listing can't be restored; listing again creates a new one.\(chromeNote(marketplaces))",
                names.count == 1 ? "Delist from \(names[0])" : "Delist from \(names.count)")
    }

    private static func chromeNote(_ marketplaces: [String]) -> String {
        let chrome = marketplaces.filter(throughChrome.contains).map(Marketplaces.name)
        guard !chrome.isEmpty else { return "" }
        let list = chrome.count == 1 ? chrome[0] : chrome.dropLast().joined(separator: ", ") + " and " + chrome.last!
        return "\n\(list) \(chrome.count == 1 ? "goes" : "go") through Chrome on your computer: keep Anteroom open there."
    }

    private static func parseDate(_ iso: String) -> Date? {
        let full = ISO8601DateFormatter()
        full.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = full.date(from: iso) { return date }
        full.formatOptions = [.withInternetDateTime]
        return full.date(from: iso)
    }
}
