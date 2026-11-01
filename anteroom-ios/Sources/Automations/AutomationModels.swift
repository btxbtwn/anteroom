import Foundation

/// Scheduled tasks the seller confirms before they delist, relist, regenerate
/// or change a price (api/automations.ts). Scheduling never publishes or
/// delists on its own: marketplace writes wait for approval of each item.

struct AutomationAction: Codable, Equatable, Sendable {
    var type: String
    var mode: String?
    var value: Double?
    var floor: Double?
    var repeatMode: String?

    enum CodingKeys: String, CodingKey {
        case type, mode, value, floor
        case repeatMode = "repeat"
    }
}

struct AutomationSchedule: Codable, Equatable, Sendable {
    var kind: String
    var at: String?
    var time: String?
    var weekdays: [Int]?
}

struct AutomationFilters: Codable, Equatable, Sendable {
    var category: String?
    var department: String?
    var brand: String?
    var minAgeDays: Int?
    var status: String?
    var conversationIDs: [String]?

    enum CodingKeys: String, CodingKey {
        case category, department, brand, status
        case minAgeDays = "min_age_days"
        case conversationIDs = "conversation_ids"
    }
}

struct Automation: Decodable, Identifiable, Sendable {
    let id: String
    let name: String
    let prompt: String?
    let status: String
    let timezone: String
    let schedule: AutomationSchedule?
    let filters: AutomationFilters?
    let actions: [AutomationAction]
    let marketplaces: [String]
    let priceRepeat: String?
    let summary: String?
    let nextRunAt: String?

    enum CodingKeys: String, CodingKey {
        case id, name, prompt, status, timezone, schedule, filters, actions, marketplaces, summary
        case priceRepeat = "price_repeat", nextRunAt = "next_run_at"
    }
}

/// What a sentence turned into. Nothing is saved until the seller confirms it.
struct AutomationDraft: Decodable, Sendable {
    let name: String?
    let prompt: String?
    let timezone: String?
    let schedule: AutomationSchedule?
    let filters: AutomationFilters?
    let actions: [AutomationAction]?
    let marketplaces: [String]?
    let priceRepeat: String?
    let issues: [String]?

    enum CodingKeys: String, CodingKey {
        case name, prompt, timezone, schedule, filters, actions, marketplaces, issues
        case priceRepeat = "price_repeat"
    }
}

struct AutomationPreview: Decodable, Sendable {
    struct Item: Decodable, Identifiable, Sendable {
        let conversationID: String
        let title: String
        let include: Bool
        let skipReason: String?
        let ageDays: Int?
        let listedAt: String?
        let listedSource: String?
        let price: Double?
        let nextPrice: Double?
        let originalPrice: Double?
        let marketplaces: [String]
        var id: String { conversationID }

        enum CodingKeys: String, CodingKey {
            case title, include, price, marketplaces
            case conversationID = "conversation_id", skipReason = "skip_reason", ageDays = "age_days"
            case listedAt = "listed_at", listedSource = "listed_source", nextPrice = "next_price", originalPrice = "original_price"
        }
    }
    let issues: [String]?
    let summary: String
    let count: Int
    let items: [Item]
}

struct AutomationRun: Decodable, Identifiable, Sendable {
    struct Item: Decodable, Sendable {
        let conversationID: String
        let title: String?
        let ok: Bool
        let error: String?
        let state: String?
        let down: [String]?
        let price: Double?
        let nextPrice: Double?

        enum CodingKeys: String, CodingKey {
            case title, ok, error, state, down, price
            case conversationID = "conversation_id", nextPrice = "next_price"
        }
    }
    let id: String
    let origin: String
    let status: String
    let dryRun: Bool
    let scheduledFor: String?
    let startedAt: String?
    let error: String?
    let items: [Item]

    enum CodingKeys: String, CodingKey {
        case id, origin, status, error, items
        case dryRun = "dry_run", scheduledFor = "scheduled_for", startedAt = "started_at"
    }
}

struct AutomationApproval: Decodable, Identifiable, Sendable {
    let id: String
    let conversationID: String
    let title: String?
    let action: String
    let marketplaces: [String]
    let state: String

    enum CodingKeys: String, CodingKey {
        case id, title, action, marketplaces, state
        case conversationID = "conversation_id"
    }
}

/// A marketplace a relist left offline.
struct RelistGap: Decodable, Identifiable, Sendable {
    let id: String
    let state: String
    let title: String?
    let marketplace: String
    let conversationID: String
    let error: String?

    enum CodingKeys: String, CodingKey {
        case id, state, title, marketplace, error
        case conversationID = "conversation_id"
    }
}

/// The body create, update and preview accept; `activate` confirms it may run.
struct AutomationBody: Encodable, Sendable {
    var name: String?
    var prompt: String?
    var timezone: String
    var schedule: AutomationSchedule?
    var filters: AutomationFilters?
    var actions: [AutomationAction]?
    var marketplaces: [String]?
    var priceRepeat: String?
    var activate: Bool

    enum CodingKeys: String, CodingKey {
        case name, prompt, timezone, schedule, filters, actions, marketplaces, activate
        case priceRepeat = "price_repeat"
    }
}

/// The desktop form (automationsFormat.ts), field for field.
struct AutomationForm: Equatable, Sendable {
    static let marketplaces = ["ebay", "poshmark", "mercari", "depop", "etsy", "sellwild", "vinted"]
    static let weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    var name = ""
    var prompt = ""
    var timezone = TimeZone.current.identifier
    var kind = "daily"
    var at = ""
    var time = "09:00"
    var weekdays = [0]
    var category = ""
    var department = ""
    var brand = ""
    var minAge = ""
    var status = ""
    var itemIDs = ""
    var regenerate = false
    var delist = false
    var relist = false
    var priceOn = false
    var priceMode = "percent"
    var priceRepeat = "step"
    var priceValue = ""
    var floor = ""
    var marketplaces: [String] = []

    init(timezone: String = TimeZone.current.identifier) { self.timezone = timezone }

    init(draft: AutomationDraft) {
        self.init(timezone: draft.timezone ?? "UTC")
        name = draft.name ?? ""
        prompt = draft.prompt ?? ""
        if let schedule = draft.schedule, ["once", "daily", "weekly"].contains(schedule.kind) { kind = schedule.kind }
        at = String((draft.schedule?.at ?? "").prefix(16))
        time = draft.schedule?.time ?? "09:00"
        weekdays = (draft.schedule?.weekdays ?? []).isEmpty ? [0] : draft.schedule?.weekdays ?? [0]
        category = draft.filters?.category ?? ""
        department = draft.filters?.department ?? ""
        brand = draft.filters?.brand ?? ""
        minAge = draft.filters?.minAgeDays.map(String.init) ?? ""
        status = draft.filters?.status ?? ""
        itemIDs = (draft.filters?.conversationIDs ?? []).joined(separator: ", ")
        marketplaces = draft.marketplaces ?? []
        for action in draft.actions ?? [] {
            switch action.type {
            case "regenerate": regenerate = true
            case "delist": delist = true
            case "relist": relist = true
            case "price":
                priceOn = true
                priceMode = action.mode == "amount" ? "amount" : "percent"
                priceRepeat = action.repeatMode == "once" ? "once" : "step"
                priceValue = action.value.map { GeneralFields.text($0) } ?? ""
                floor = action.floor.map { GeneralFields.text($0) } ?? ""
            default: break
            }
        }
        if let repeatMode = draft.priceRepeat, ["once", "step"].contains(repeatMode) { priceRepeat = repeatMode }
    }

    init(automation: Automation) {
        self.init(draft: AutomationDraft(
            name: automation.name, prompt: automation.prompt, timezone: automation.timezone,
            schedule: automation.schedule, filters: automation.filters, actions: automation.actions,
            marketplaces: automation.marketplaces, priceRepeat: automation.priceRepeat, issues: []))
    }

    private var schedule: AutomationSchedule {
        switch kind {
        case "once": AutomationSchedule(kind: "once", at: at)
        case "weekly": AutomationSchedule(kind: "weekly", time: time.isEmpty ? "09:00" : time, weekdays: weekdays)
        default: AutomationSchedule(kind: "daily", time: time.isEmpty ? "09:00" : time)
        }
    }

    private var actions: [AutomationAction] {
        var result: [AutomationAction] = []
        if regenerate { result.append(AutomationAction(type: "regenerate")) }
        if priceOn {
            result.append(AutomationAction(type: "price", mode: priceMode, value: Double(priceValue),
                                           floor: floor.isEmpty ? nil : Double(floor), repeatMode: priceRepeat))
        }
        if delist { result.append(AutomationAction(type: "delist")) }
        if relist { result.append(AutomationAction(type: "relist")) }
        return result
    }

    private var filters: AutomationFilters {
        func clean(_ text: String) -> String? {
            let trimmed = text.trimmingCharacters(in: .whitespaces)
            return trimmed.isEmpty ? nil : trimmed
        }
        let ids = itemIDs.split(whereSeparator: { $0 == "," || $0.isWhitespace }).map(String.init)
        return AutomationFilters(category: clean(category), department: clean(department), brand: clean(brand),
                                 minAgeDays: clean(minAge).flatMap { Int($0) }, status: clean(status),
                                 conversationIDs: ids.isEmpty ? nil : ids)
    }

    private var hasFilters: Bool { filters != AutomationFilters() }

    /// A sentence with no boxes filled is parsed on the server.
    func body(activate: Bool) -> AutomationBody {
        let trimmedName = name.trimmingCharacters(in: .whitespaces)
        let trimmedPrompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        var body = AutomationBody(name: trimmedName.isEmpty ? nil : trimmedName,
                                  prompt: trimmedPrompt.isEmpty ? nil : trimmedPrompt,
                                  timezone: timezone.trimmingCharacters(in: .whitespaces).isEmpty ? "UTC" : timezone,
                                  activate: activate)
        let structured = !actions.isEmpty || !marketplaces.isEmpty || hasFilters || kind == "once"
        if !structured && !trimmedPrompt.isEmpty { return body }
        body.schedule = schedule
        body.filters = filters
        body.actions = actions
        body.marketplaces = marketplaces
        body.priceRepeat = priceRepeat
        return body
    }
}

enum AutomationFormat {
    static func schedule(_ schedule: AutomationSchedule?, timezone: String) -> String {
        guard let schedule else { return "No schedule yet" }
        if schedule.kind == "once" { return "Once at \(schedule.at ?? "a time") (\(timezone))" }
        let clock = schedule.time ?? "09:00"
        if schedule.kind == "daily" { return "Daily at \(clock) (\(timezone))" }
        let days = (schedule.weekdays ?? []).map { AutomationForm.weekdays.indices.contains($0) ? AutomationForm.weekdays[$0] : String($0) }
        return "Weekly on \(days.isEmpty ? "no days" : days.joined(separator: ", ")) at \(clock) (\(timezone))"
    }

    static func actions(_ actions: [AutomationAction]) -> String {
        guard !actions.isEmpty else { return "No actions" }
        return actions.map { action in
            guard action.type == "price" else { return action.type.replacingOccurrences(of: "_", with: " ") }
            let value = action.value.map { GeneralFields.text($0) } ?? "?"
            let amount = action.mode == "amount" ? "$\(value)" : "\(value)%"
            let cadence = action.repeatMode == "step" ? " each cycle" : action.repeatMode == "once" ? " once" : ""
            let floor = action.floor.map { ", floor $\(GeneralFields.text($0))" } ?? ""
            return "drop \(amount)\(cadence)\(floor)"
        }.joined(separator: ", ")
    }

    static func listedSource(_ source: String?) -> String {
        switch source {
        case "publish_job": "publish job"
        case "vendoo_job": "Vendoo"
        case "item_listed": "item date"
        case "marketplace_listing": "marketplace record"
        default: "listed date"
        }
    }

    static func money(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "—" }
        return value.rounded() == value ? "$\(Int(value))" : String(format: "$%.2f", value)
    }
}
