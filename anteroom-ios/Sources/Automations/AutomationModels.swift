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
    let lastRunAt: String?

    enum CodingKeys: String, CodingKey {
        case id, name, prompt, status, timezone, schedule, filters, actions, marketplaces, summary
        case priceRepeat = "price_repeat", nextRunAt = "next_run_at", lastRunAt = "last_run_at"
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
    static let marketplaces = ["ebay", "poshmark", "mercari", "depop", "etsy"]
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

    /// "18:30" as "6:30 PM".
    static func clockLabel(_ time: String) -> String { clock(time) }

    private static func clock(_ time: String?) -> String {
        let parts = (time ?? "09:00").split(separator: ":").compactMap { Int($0) }
        let hour = parts.first ?? 9
        let minute = parts.count > 1 ? parts[1] : 0
        return "\(hour % 12 == 0 ? 12 : hour % 12):\(String(format: "%02d", minute)) \(hour < 12 ? "AM" : "PM")"
    }

    private static let months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    private static func when(_ schedule: AutomationSchedule?) -> String {
        guard let schedule else { return "No schedule yet" }
        switch schedule.kind {
        case "once":
            let at = schedule.at ?? ""
            let date = at.prefix(10).split(separator: "-").compactMap { Int($0) }
            guard date.count == 3, months.indices.contains(date[1] - 1) else { return "Once" }
            let time = at.count >= 16 ? ", \(clock(String(at.dropFirst(11).prefix(5))))" : ""
            return "Once \(months[date[1] - 1]) \(date[2])\(time)"
        case "daily":
            return "Every day \(clock(schedule.time))"
        default:
            let days = (schedule.weekdays ?? []).map { AutomationForm.weekdays.indices.contains($0) ? AutomationForm.weekdays[$0] : String($0) }
            return "Every \(days.isEmpty ? "week" : days.joined(separator: ", ")) \(clock(schedule.time))"
        }
    }

    /// One line for a saved automation: when it runs, which items and where (automationsFormat.ts describeTrigger).
    static func trigger(_ schedule: AutomationSchedule?, filters: AutomationFilters?, marketplaces: [String],
                        name: (String) -> String) -> String {
        var picks = [filters?.department, filters?.category, filters?.brand, filters?.status]
            .compactMap { $0 }.filter { !$0.isEmpty }
        if let age = filters?.minAgeDays, age > 0 { picks.append("listed \(age)+ days") }
        if let ids = filters?.conversationIDs, !ids.isEmpty { picks.append("\(ids.count) item\(ids.count == 1 ? "" : "s")") }
        let place = marketplaces.isEmpty ? "" : "on \(marketplaces.map(name).joined(separator: ", "))"
        let which = [picks.joined(separator: ", "), place].filter { !$0.isEmpty }.joined(separator: " ")
        return [when(schedule), which].filter { !$0.isEmpty }.joined(separator: " · ")
    }

    /// A server time as "Nov 5, 9:00 AM" in the automation's timezone.
    static func runTime(_ raw: String?, timezone: String) -> String {
        guard let raw else { return "—" }
        guard let date = ServerDate.parse(raw) else { return raw }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "MMM d, h:mm a"
        formatter.timeZone = TimeZone(identifier: timezone) ?? .current
        return formatter.string(from: date)
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
        case "vendoo_job": "imported Vendoo date"
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

// MARK: eBay offers, sharing and offers to likers

/// eBay offers (`GET /api/ebay-offers`): one setup per account, checked every 15 minutes.
/// The iPhone shows it in the list read-only; it is set up and turned on from the web or Mac.
struct EbayOffersSummary: Decodable, Sendable {
    struct AllListings: Decodable, Sendable {
        let enabled: Bool
        let discountPercent: Int
        let minimumPrice: String
    }
    struct Listing: Decodable, Sendable { let enabled: Bool }
    let connected: Bool
    let allListings: AllListings
    let listings: [Listing]
    let checkIntervalMinutes: Int?
    let lastCheck: String?

    var on: Bool { allListings.enabled || listings.contains(where: \.enabled) }
}

/// A Poshmark sharing schedule or an offers-to-likers rule (`GET /api/promotion-automations`).
/// Saved off; only the seller turns one on, and any edit turns it off again.
struct PromotionAutomationSummary: Decodable, Identifiable, Sendable {
    struct Run: Decodable, Sendable {
        let startedAt: String?
        enum CodingKeys: String, CodingKey { case startedAt = "started_at" }
    }
    let id: String
    let name: String
    let marketplace: String
    let action: String
    let scope: String
    let conversationIDs: [String]
    let times: [String]
    let timezone: String?
    let discountPercent: Int?
    let enabled: Bool
    let available: Bool
    let pausedReason: String?
    let nextRunAt: String?
    let lastRun: Run?

    enum CodingKeys: String, CodingKey {
        case id, name, marketplace, action, scope, times, timezone, enabled, available
        case conversationIDs = "conversation_ids", discountPercent = "discount_percent", pausedReason = "paused_reason"
        case nextRunAt = "next_run_at", lastRun = "last_run"
    }
}

struct PromotionAutomationList: Decodable, Sendable {
    let automations: [PromotionAutomationSummary]
}

/// One row for any automation, as the web list draws it (automationRows.ts).
struct AutomationRowModel: Equatable, Sendable {
    var name: String
    var type: String
    var when: String
    var lastRun: String
    var nextRun: String
    /// The switch position, or nil when `state` shows instead.
    var on: Bool?
    var state: String = ""
    var note: String = ""

    /// What New offers, in the web's order.
    static let kinds: [(kind: String, label: String, description: String)] = [
        ("scheduled", "Price drop, relist or regenerate", "On a schedule; each item waits for your approval."),
        ("ebay_offers", "eBay offers", "Offers to buyers watching your eBay listings."),
        ("share", "Poshmark sharing", "Shares your Poshmark closet at the times you pick."),
        ("offer", "Offers to likers", "Offers to new likers on Poshmark, Mercari or Depop."),
    ]

    static func type(_ actions: [AutomationAction]) -> String {
        let labels = ["price": "Price drop", "relist": "Relist", "delist": "Delist", "regenerate": "Regenerate", "update_fields": "Update fields"]
        var seen: [String] = []
        for action in actions {
            let label = labels[action.type] ?? action.type
            if !seen.contains(label) { seen.append(label) }
        }
        return seen.isEmpty ? "Scheduled" : seen.joined(separator: ", ")
    }

    init(name: String, type: String, when: String, lastRun: String, nextRun: String, on: Bool?, state: String = "", note: String = "") {
        self.name = name; self.type = type; self.when = when; self.lastRun = lastRun; self.nextRun = nextRun
        self.on = on; self.state = state; self.note = note
    }

    init(automation task: Automation, name market: (String) -> String) {
        let togglable = task.status == "active" || task.status == "paused"
        self.init(name: task.name, type: Self.type(task.actions),
                  when: AutomationFormat.trigger(task.schedule, filters: task.filters, marketplaces: task.marketplaces, name: market),
                  lastRun: AutomationFormat.runTime(task.lastRunAt, timezone: task.timezone),
                  nextRun: task.status == "active" ? AutomationFormat.runTime(task.nextRunAt, timezone: task.timezone) : "—",
                  on: togglable ? task.status == "active" : nil,
                  state: togglable ? "" : (task.status == "draft" ? "Draft" : "Done"))
    }

    init(ebay status: EbayOffersSummary, timezone: String = TimeZone.current.identifier) {
        let every = status.checkIntervalMinutes ?? 15
        let picked = status.listings.filter(\.enabled).count
        let all = status.allListings
        let scope = all.enabled
            ? "All listings, \(all.discountPercent)% off\(all.minimumPrice.isEmpty ? "" : ", minimum $\(all.minimumPrice)")"
            : picked > 0 ? "\(picked) listing\(picked == 1 ? "" : "s")" : "No listings yet"
        self.init(name: "eBay offers", type: "eBay offers", when: "Every \(every) minutes · \(scope)",
                  lastRun: AutomationFormat.runTime(status.lastCheck, timezone: timezone),
                  nextRun: status.on && status.connected ? "Within \(every) min" : "—",
                  on: status.connected ? status.on : nil, state: status.connected ? "" : "Not connected")
    }

    init(promotion row: PromotionAutomationSummary, name market: (String) -> String) {
        let timezone = row.timezone ?? TimeZone.current.identifier
        let count = row.conversationIDs.count
        let scope = row.scope == "all" ? "all live listings" : "\(count) chosen listing\(count == 1 ? "" : "s")"
        let when = row.action == "share"
            ? "Every day \(row.times.map(AutomationFormat.clockLabel).joined(separator: ", ")) · \(scope)"
            : "New likers on \(market(row.marketplace)) · \(row.discountPercent ?? 0)% off \(scope)"
        self.init(name: row.name, type: row.action == "share" ? "\(market(row.marketplace)) sharing" : "Offers to likers",
                  when: when, lastRun: AutomationFormat.runTime(row.lastRun?.startedAt, timezone: timezone),
                  nextRun: row.enabled ? AutomationFormat.runTime(row.nextRunAt, timezone: timezone) : "—", on: row.enabled,
                  note: row.enabled ? "" : row.pausedReason ?? (row.available ? "" : "\(market(row.marketplace)) offers are not available in Anteroom yet"))
    }
}
