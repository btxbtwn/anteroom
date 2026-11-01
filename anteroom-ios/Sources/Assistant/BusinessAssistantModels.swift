import Foundation

/// One conversation with the business assistant, apart from any listing's chat (api/assistant.ts).
struct AssistantThread: Decodable, Identifiable, Hashable, Sendable {
    let id: String
    let title: String
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title
        case updatedAt = "updated_at"
    }
}

/// One turn of a conversation with the business assistant.
struct AssistantTurn: Decodable, Identifiable, Sendable {
    let id: String
    let role: String
    let text: String
    let createdAt: String?
    /// Changes this answer proposed, and what the seller did with them.
    let changes: AssistantChanges?
    /// An automation this answer drafted, and whether the seller confirmed it.
    let automation: AssistantAutomation?
    /// The names of images attached to a question.
    let attachmentNames: [String]

    enum CodingKeys: String, CodingKey {
        case id, role, text, changes, automation, attachments
        case createdAt = "created_at"
    }

    private struct Attachment: Decodable { let name: String? }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        role = try values.decode(String.self, forKey: .role)
        text = try values.decodeIfPresent(String.self, forKey: .text) ?? ""
        createdAt = try values.decodeIfPresent(String.self, forKey: .createdAt)
        // A card the app cannot read must not hide the conversation around it.
        changes = try? values.decodeIfPresent(AssistantChanges.self, forKey: .changes)
        automation = try? values.decodeIfPresent(AssistantAutomation.self, forKey: .automation)
        let attachments = (try? values.decodeIfPresent([Attachment].self, forKey: .attachments)) ?? nil
        attachmentNames = (attachments ?? []).map { $0.name ?? "image" }
    }
}

/// One value that applying a proposal would change on a listing, before and after.
struct AssistantFieldChange: Decodable, Sendable {
    let field: String
    let old: JSONValue?
    let new: JSONValue?
}

struct AssistantListingChange: Decodable, Identifiable, Sendable {
    let listingID: String
    let title: String
    let fields: [AssistantFieldChange]
    /// Why this listing was left alone when the rest were applied.
    let skipped: String?

    var id: String { listingID }

    enum CodingKeys: String, CodingKey {
        case title, fields, skipped
        case listingID = "listing_id"
    }
}

/// Nothing in a proposal is written until the seller applies it.
struct AssistantChanges: Decodable, Sendable {
    let status: String
    let items: [AssistantListingChange]
}

/// An automation an answer drafted. Nothing is saved until the seller confirms it.
struct AssistantAutomation: Decodable, Sendable {
    let status: String
    let proposal: AssistantAutomationProposal?

    enum CodingKeys: String, CodingKey { case status, proposal }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        status = try values.decode(String.self, forKey: .status)
        // An unreadable draft still shows its status and Dismiss.
        proposal = (try? values.decodeIfPresent(AssistantAutomationProposal.self, forKey: .proposal)) ?? nil
    }
}

struct AssistantAutomationProposal: Decodable, Sendable {
    let name: String?
    let timezone: String?
    let schedule: AutomationSchedule?
    let actions: [AutomationAction]?
    let marketplaces: [String]?
    let issues: [String]?
    let ready: Bool?
}

/// The turn being written: the question asked here, if any, and the answer so far.
struct AssistantLiveTurn: Sendable {
    /// Nil when an answer already under way was picked back up; its question is among the saved turns.
    var question: String?
    var questionImages: [String] = []
    /// How many turns were saved when the question was asked.
    var asked: Int
    var text = ""
    var status = ""
}

/// A follow-up asked while an answer is still being written.
struct AssistantQueuedQuestion: Identifiable, Sendable {
    let id = UUID()
    let text: String
    let attachments: [ChatAttachment]
    let threadID: String?
}

/// What the assistant screens say, as AssistantPage.tsx and its cards say it.
enum AssistantFormat {
    /// Questions that show what the assistant can see, offered before the first one is asked.
    static let starters = [
        "How did last month compare with the month before?",
        "Which brands and categories make me the most profit?",
        "What has been listed longest, and what should I do with it?",
        "Which wholesale boxes paid off, and which did not?",
        "Mark down everything listed over 90 days by 15%.",
        "Find listings whose size differs between forms and fix them.",
    ]

    static let lostConnection =
        "Lost the connection to Anteroom. If it was still answering, the answer appears here when you come back."

    private static let fieldLabels = [
        "asking": "Asking", "cost": "Cost", "title": "Title", "description": "Description", "brand": "Brand",
        "size": "Size", "ebay_size": "eBay size", "poshmark_size": "Poshmark size", "mercari_size": "Mercari size",
        "depop_size": "Depop size", "etsy_size": "Etsy size", "sku": "SKU", "labels": "Labels", "box": "Box",
    ]

    static func fieldLabel(_ field: String) -> String { fieldLabels[field] ?? field }

    /// One side of a change as the seller reads it.
    static func changeValue(_ field: String, _ value: JSONValue?) -> String {
        switch value {
        case nil, .null: return "none"
        case .string(let text): return text.isEmpty ? "none" : text
        case .array(let items):
            if items.isEmpty { return "none" }
            return items.map { changeValue(field, $0) }.joined(separator: ", ")
        case .int(let number):
            return field == "asking" || field == "cost" ? "$\(number)" : String(number)
        case .double(let number):
            if field == "asking" || field == "cost" {
                return number.rounded() == number ? "$\(Int(number))" : String(format: "$%.2f", number)
            }
            return GeneralFields.text(number)
        case .bool(let flag): return flag ? "true" : "false"
        case .object: return "…"
        }
    }

    /// The card's heading: what is proposed, or what became of it.
    static func changesHeading(_ changes: AssistantChanges) -> String {
        func count(_ n: Int) -> String { "\(n) listing\(n == 1 ? "" : "s")" }
        let skipped = changes.items.filter { $0.skipped != nil }.count
        if changes.status == "dismissed" { return "Dismissed. Nothing was changed." }
        if changes.status == "proposed" { return "Proposed changes to \(count(changes.items.count))" }
        let applied = "Applied to \(count(changes.items.count - skipped))"
        return skipped > 0 ? "\(applied), \(skipped) skipped" : applied
    }

    /// What the automation card says before and after the seller confirms the draft.
    static func automationHeading(_ automation: AssistantAutomation) -> String {
        let name = automation.proposal?.name.flatMap { $0.isEmpty ? nil : $0 } ?? "Automation"
        if automation.status == "dismissed" { return "Dismissed “\(name)”. Nothing was saved." }
        if automation.status == "confirmed" { return "Confirmed “\(name)”. It can run on the schedule you set." }
        return "Review “\(name)” before it is saved"
    }

    static func automationSummary(_ proposal: AssistantAutomationProposal) -> String {
        let marketplaces = proposal.marketplaces ?? []
        return [AutomationFormat.schedule(proposal.schedule, timezone: proposal.timezone ?? "UTC"),
                AutomationFormat.actions(proposal.actions ?? []),
                marketplaces.isEmpty ? "no marketplaces named" : marketplaces.joined(separator: ", ")]
            .joined(separator: " · ")
    }

    /// The listing an answer's link opens (`[title](#listing-<id>)`), or nil for any other link.
    static func linkedListingID(_ link: String) -> String? {
        let fragment = link.split(separator: "#", omittingEmptySubsequences: false).last.map(String.init) ?? ""
        guard link.contains("#"), fragment.hasPrefix("listing-") else { return nil }
        let id = String(fragment.dropFirst("listing-".count))
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "_-"))
        guard !id.isEmpty, id.unicodeScalars.allSatisfy(allowed.contains) else { return nil }
        return id
    }

    /// What of the turn being written still has to be drawn. The saved conversation
    /// can refresh mid-answer and bring the question, or the finished answer, with it;
    /// drawing the live copy as well would show it twice.
    static func liveParts(_ live: AssistantLiveTurn, turns: [AssistantTurn]) -> (question: Bool, answer: Bool) {
        if live.question == nil { return (false, turns.last?.role != "assistant") }
        return (turns.count <= live.asked, turns.count < live.asked + 2)
    }

    /// Asking while an answer is being written holds the question until it ends.
    static func sendsNow(busy: Bool, queued: Int) -> Bool { !busy && queued == 0 }
}
