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

    /// What the seller reads before Run now: what it does, now instead of on schedule.
    static func runNowMessage(_ automation: Automation) -> String {
        let what = automation.actions.isEmpty ? "It has no actions yet." : "It will \(actions(automation.actions)) on the listings it matches."
        return "\(what) This runs once now instead of waiting for its schedule. Listing, relisting or delisting still waits for your approval."
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

/// eBay offers (`GET /api/ebay-offers`, EbayOffersSettings.tsx): one setup per
/// account, checked every 15 minutes by the hosted API. Offers go only to
/// buyers eBay marks eligible; sending and turning automation on both carry `confirm`.
struct EbayOffersStatus: Decodable, Sendable {
    struct AllListings: Decodable, Equatable, Sendable {
        let enabled: Bool
        let discountPercent: Int
        let minimumPrice: String
    }
    struct Listing: Decodable, Identifiable, Equatable, Sendable {
        let conversationId: String
        let listingId: String
        let title: String
        let ruleId: String?
        let enabled: Bool
        let discountPercent: Int
        let minimumPrice: String
        var id: String { listingId }
    }
    let connected: Bool
    let allListings: AllListings
    let listings: [Listing]
    let history: [EbayOffer]
    let checkIntervalMinutes: Int?
    let lastCheck: String?
    let lastError: String?

    var on: Bool { allListings.enabled || listings.contains(where: \.enabled) }
}

/// A previewed, sent or skipped offer (`attempt_out`).
struct EbayOffer: Decodable, Equatable, Sendable {
    let id: String?
    let ruleId: String?
    let conversationId: String?
    let listingId: String?
    let title: String?
    let currentPrice: String?
    let offerPrice: String?
    let minimumPrice: String?
    let discountPercent: Int?
    let eligible: Bool?
    let state: String
    let note: String?
    let createdAt: String?

    /// What the preview says about this offer, as the web does.
    var previewNote: String {
        if state == "preview" {
            return eligible == true ? "Ready to send to eBay-eligible buyers." : "No eligible buyers yet. You can enable automatic offers for future buyers."
        }
        return note ?? state
    }
}

struct EbayOfferPreviews: Decodable, Sendable { let offers: [EbayOffer] }

struct EbayOfferSelection: Encodable, Equatable, Sendable {
    let conversationID: String
    let minimumPrice: String
    enum CodingKeys: String, CodingKey { case conversationID = "conversation_id", minimumPrice = "minimum_price" }
}

struct EbayOfferPreviewRequest: Encodable, Equatable, Sendable {
    let selections: [EbayOfferSelection]
    let discountPercent: Int
    enum CodingKeys: String, CodingKey { case selections, discountPercent = "discount_percent" }
}

/// Send now or turn automatic offers on for previewed listings: always confirmed.
struct EbayOfferApproval: Encodable, Equatable, Sendable {
    let attemptIDs: [String]
    let confirm = true
    enum CodingKeys: String, CodingKey { case attemptIDs = "attempt_ids", confirm }
}

struct EbayOffersEnableAll: Encodable, Equatable, Sendable {
    let discountPercent: Int
    let minimumPrice: String?
    let confirm = true
    enum CodingKeys: String, CodingKey { case discountPercent = "discount_percent", minimumPrice = "minimum_price", confirm }
}

/// The eBay offers form (EbayOffersSettings.tsx), with the web's checks.
struct EbayOffersForm: Equatable, Sendable {
    static let maxPreview = 50

    /// "all" or "selected"; nil follows what is saved.
    var scope: String?
    var selected: Set<String> = []
    var minimums: [String: String] = [:]
    var discount = "10"
    /// nil shows what is saved.
    var allDiscount: String?
    var allMinimum: String?

    /// A whole percent from 5 to 99, like the web's `Number.isInteger` check.
    static func percent(_ text: String) -> Int? {
        guard let value = Double(text.trimmingCharacters(in: .whitespaces)), value.rounded() == value, value >= 5, value <= 99 else { return nil }
        return Int(value)
    }

    /// Dollars with at most two decimals, above zero.
    static func isPrice(_ text: String) -> Bool {
        text.wholeMatch(of: #/\d+(\.\d{1,2})?/#) != nil && (Double(text) ?? 0) > 0
    }

    func activeScope(_ status: EbayOffersStatus) -> String { scope ?? (status.allListings.enabled ? "all" : "selected") }
    func globalDiscount(_ status: EbayOffersStatus) -> String { allDiscount ?? String(status.allListings.discountPercent) }
    func globalMinimum(_ status: EbayOffersStatus) -> String { allMinimum ?? status.allListings.minimumPrice }

    func globalValid(_ status: EbayOffersStatus) -> Bool {
        let minimum = globalMinimum(status)
        return Self.percent(globalDiscount(status)) != nil && (minimum.isEmpty || Self.isPrice(minimum))
    }

    /// All listings is on with exactly these settings, so there is nothing to save.
    func allUnchanged(_ status: EbayOffersStatus) -> Bool {
        let saved = status.allListings
        guard saved.enabled, let discount = Double(globalDiscount(status)), Int(exactly: discount) == saved.discountPercent else { return false }
        let minimum = globalMinimum(status)
        if minimum.isEmpty { return saved.minimumPrice.isEmpty }
        return !saved.minimumPrice.isEmpty && Double(minimum) == Double(saved.minimumPrice)
    }

    func enableAll(_ status: EbayOffersStatus) -> EbayOffersEnableAll? {
        guard globalValid(status), let percent = Self.percent(globalDiscount(status)) else { return nil }
        let minimum = globalMinimum(status)
        return EbayOffersEnableAll(discountPercent: percent, minimumPrice: minimum.isEmpty ? nil : minimum)
    }

    /// The button that sends previewed offers, and the question it asks first.
    static func sendButton(count: Int) -> String {
        "Send \(count) \(count == 1 ? "offer" : "offers") now"
    }

    static func sendQuestion(count: Int) -> String {
        "Send \(count) \(count == 1 ? "offer" : "offers") to buyers on eBay now?"
    }

    /// The question before automatic offers start for the previewed listings.
    static func enableQuestion(count: Int) -> String {
        "Turn on automatic offers for \(count) \(count == 1 ? "listing" : "listings")?"
    }

    /// What the seller reads before turning offers on for every listing.
    func enableAllMessage(_ status: EbayOffersStatus) -> String {
        let minimum = globalMinimum(status)
        return "Enable \(globalDiscount(status))% off offers on all current and future eBay listings in Anteroom? "
            + (minimum.isEmpty ? "No minimum price will apply." : "Offers below $\(minimum) are skipped.")
            + " This replaces individual automatic offer rules. Anteroom checks every 15 minutes and sends to all buyers eBay marks eligible."
    }

    func minimum(for listing: EbayOffersStatus.Listing) -> String { minimums[listing.conversationId] ?? listing.minimumPrice }

    func selectedListings(_ status: EbayOffersStatus) -> [EbayOffersStatus.Listing] {
        status.listings.filter { selected.contains($0.conversationId) }
    }

    /// The preview request, or nil while the selection, discount or a minimum is not ready.
    func preview(_ status: EbayOffersStatus) -> EbayOfferPreviewRequest? {
        let picked = selectedListings(status)
        guard !picked.isEmpty, picked.count <= Self.maxPreview, let percent = Self.percent(discount),
              picked.allSatisfy({ Self.isPrice(minimum(for: $0)) }) else { return nil }
        return EbayOfferPreviewRequest(selections: picked.map { EbayOfferSelection(conversationID: $0.conversationId, minimumPrice: minimum(for: $0)) },
                                       discountPercent: percent)
    }

    /// Previewed offers that can be sent now, and those that can turn automatic offers on.
    static func readyIDs(_ offers: [EbayOffer]) -> [String] {
        offers.filter { $0.state == "preview" && $0.eligible == true }.compactMap(\.id)
    }

    static func enableIDs(_ offers: [EbayOffer]) -> [String] {
        offers.filter { $0.state == "preview" }.compactMap(\.id)
    }
}

/// A Poshmark sharing schedule or an offers-to-likers rule (`GET /api/promotion-automations`).
/// Saved off; only the seller turns one on, and any edit turns it off again.
/// Rounds run in the seller's own Chrome, through an open Anteroom tab with Bridge.
struct PromotionAutomation: Decodable, Identifiable, Sendable {
    struct Run: Decodable, Identifiable, Sendable {
        let id: String
        let startedAt: String?
        let sent: Int
        let skipped: Int
        let failed: Int
        let note: String?
        enum CodingKeys: String, CodingKey { case id, sent, skipped, failed, note, startedAt = "started_at" }
    }
    let id: String
    let name: String
    let marketplace: String
    let action: String
    let scope: String
    let conversationIDs: [String]
    let excludedIDs: [String]
    let minDaysListed: Int
    let shareOrder: String?
    let speed: String?
    let dailyCap: Int?
    let times: [String]
    let timezone: String?
    let discountPercent: Int?
    let minLikes: Int?
    let shippingLabel: String?
    let minimumPrice: String?
    let enabled: Bool
    let available: Bool
    let pausedReason: String?
    let nextRunAt: String?
    let lastRun: Run?

    enum CodingKeys: String, CodingKey {
        case id, name, marketplace, action, scope, times, timezone, speed, enabled, available
        case conversationIDs = "conversation_ids", excludedIDs = "excluded_ids", minDaysListed = "min_days_listed"
        case shareOrder = "share_order", dailyCap = "daily_cap", discountPercent = "discount_percent", minLikes = "min_likes"
        case shippingLabel = "shipping_label", minimumPrice = "minimum_price", pausedReason = "paused_reason"
        case nextRunAt = "next_run_at", lastRun = "last_run"
    }
}

struct PromotionAutomationList: Decodable, Sendable {
    let automations: [PromotionAutomation]
    let awaitingCapture: [String: [String]]
    let maxDailyCap: Int
    let shippingLabels: [String]

    enum CodingKeys: String, CodingKey {
        case automations, awaitingCapture = "awaiting_capture", maxDailyCap = "max_daily_cap", shippingLabels = "shipping_labels"
    }
}

/// The body create and update accept (`AutomationBody` in routes/promotion_automations.py).
struct PromotionAutomationBody: Encodable, Equatable, Sendable {
    var marketplace: String
    var action: String
    var name: String
    var scope: String
    var conversationIDs: [String]
    var excludedIDs: [String]
    var minDaysListed: Int
    var shareOrder: String?
    var speed: String?
    var dailyCap: Int?
    var times: [String]?
    var timezone: String?
    var discountPercent: Int?
    var minimumPrice: String?
    var minLikes: Int?
    var shippingLabel: String?

    enum CodingKeys: String, CodingKey {
        case marketplace, action, name, scope, speed, times, timezone
        case conversationIDs = "conversation_ids", excludedIDs = "excluded_ids", minDaysListed = "min_days_listed"
        case shareOrder = "share_order", dailyCap = "daily_cap", discountPercent = "discount_percent"
        case minimumPrice = "minimum_price", minLikes = "min_likes", shippingLabel = "shipping_label"
    }
}

/// Which listings a sharing schedule or offer rule covers (`/coverage`).
struct PromotionCoverage: Decodable, Sendable {
    struct Item: Decodable, Identifiable, Sendable {
        let conversationID: String
        let title: String?
        let listingID: String
        let listedDays: Int?
        let floor: String?
        let included: Bool
        let tooNew: Bool
        var id: String { conversationID }
        enum CodingKeys: String, CodingKey {
            case title, floor, included
            case conversationID = "conversation_id", listingID = "listing_id", listedDays = "listed_days", tooNew = "too_new"
        }
    }
    let items: [Item]
    let included: Int
}

/// What a schedule's rounds did (`/activity`).
struct PromotionActivity: Decodable, Sendable {
    struct Send: Decodable, Identifiable, Sendable {
        let id: String
        let action: String
        let title: String?
        let listingID: String
        let offerPrice: String?
        let state: String
        let note: String?
        enum CodingKeys: String, CodingKey { case id, action, title, state, note, listingID = "listing_id", offerPrice = "offer_price" }
    }
    let runs: [PromotionAutomation.Run]
    let sends: [Send]
}

struct PromotionFormError: Error, Equatable { let message: String }

/// The sharing and offers-to-likers form (promotionAutomationsFormat.ts), field for field.
struct PromotionForm: Equatable, Sendable {
    static let offerMarketplaces = ["poshmark", "mercari", "depop"]
    static let speeds: [(value: String, label: String)] = [
        ("slow", "Slow (about 15 s apart)"), ("normal", "Normal (about 7 s apart)"), ("fast", "Fast (about 4 s apart)")]
    static let orders: [(value: String, label: String)] = [
        ("newest", "Newest listed first"), ("oldest", "Oldest listed first"), ("random", "Random")]

    var id: String?
    var marketplace = "poshmark"
    var action: String
    var name = ""
    var scope = "all"
    var conversationIDs: [String] = []
    var excludedIDs: [String] = []
    var minDays = "0"
    var order = "newest"
    var speed = "normal"
    var dailyCap = "300"
    var times = "09:00, 20:00"
    var timezone: String
    var discount = "10"
    var minimumPrice = ""
    var minLikes = "1"
    var shippingLabel = ""

    init(action: String, marketplace: String = "poshmark", timezone: String = TimeZone.current.identifier) {
        self.action = action
        self.marketplace = marketplace
        self.timezone = timezone
    }

    init(automation row: PromotionAutomation) {
        self.init(action: row.action, marketplace: row.marketplace, timezone: row.timezone ?? TimeZone.current.identifier)
        id = row.id
        name = row.name
        scope = row.scope
        conversationIDs = row.conversationIDs
        excludedIDs = row.excludedIDs
        minDays = String(row.minDaysListed)
        order = row.shareOrder ?? "newest"
        speed = row.speed ?? "normal"
        dailyCap = String(row.dailyCap ?? 300)
        times = row.times.joined(separator: ", ")
        discount = String(row.discountPercent ?? 10)
        minLikes = String(row.minLikes ?? 1)
        shippingLabel = row.shippingLabel ?? ""
        minimumPrice = row.minimumPrice ?? ""
    }

    private static func whole(_ text: String, _ low: Int, _ high: Int) -> Int? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard trimmed.wholeMatch(of: #/\d+/#) != nil, let number = Int(trimmed), number >= low, number <= high else { return nil }
        return number
    }

    /// The request body, or what the seller still has to fix.
    func body(maxDailyCap: Int) -> Result<PromotionAutomationBody, PromotionFormError> {
        guard let days = Self.whole(minDays, 0, 365) else { return .failure(.init(message: "Minimum days listed is a whole number from 0 to 365.")) }
        if scope == "items" && conversationIDs.isEmpty { return .failure(.init(message: "Choose at least one listing, or cover all live listings.")) }
        var body = PromotionAutomationBody(marketplace: marketplace, action: action, name: name.trimmingCharacters(in: .whitespaces), scope: scope,
                                           conversationIDs: scope == "items" ? conversationIDs : [], excludedIDs: excludedIDs, minDaysListed: days)
        if action == "share" {
            guard let cap = Self.whole(dailyCap, 1, maxDailyCap) else {
                return .failure(.init(message: "The daily cap is 1 to \(maxDailyCap.formatted()) shares."))
            }
            var rounds: [String] = []
            for time in times.split(whereSeparator: { $0 == "," || $0.isWhitespace }).map(String.init) where !rounds.contains(time) { rounds.append(time) }
            if rounds.isEmpty || rounds.count > 6 || rounds.contains(where: { $0.wholeMatch(of: #/([01]?\d|2[0-3]):[0-5]\d/#) == nil }) {
                return .failure(.init(message: "Enter 1 to 6 round times like 09:00, 20:00."))
            }
            let zone = timezone.trimmingCharacters(in: .whitespaces)
            body.shareOrder = order
            body.speed = speed
            body.dailyCap = cap
            body.times = rounds
            body.timezone = zone.isEmpty ? "UTC" : zone
            return .success(body)
        }
        guard let percent = Self.whole(discount, 1, 99) else { return .failure(.init(message: "Enter the discount as a whole percent from 1 to 99.")) }
        guard let likes = Self.whole(minLikes, 1, 1000) else { return .failure(.init(message: "Minimum likes is a whole number from 1 to 1,000.")) }
        let minimum = minimumPrice.trimmingCharacters(in: .whitespaces)
        if !minimum.isEmpty && (minimum.wholeMatch(of: #/\d+(\.\d{1,2})?/#) == nil || (Double(minimum) ?? 0) <= 0 || (Double(minimum) ?? 0) > 9_999_999.99) {
            return .failure(.init(message: "Enter a positive minimum price with at most two decimal places, or leave it blank."))
        }
        body.discountPercent = percent
        body.minimumPrice = minimum.isEmpty ? nil : minimum
        body.minLikes = likes
        let label = shippingLabel.trimmingCharacters(in: .whitespaces)
        body.shippingLabel = marketplace == "poshmark" && !label.isEmpty ? label : nil
        return .success(body)
    }

    func includes(_ conversationID: String) -> Bool {
        scope == "items" ? conversationIDs.contains(conversationID) : !excludedIDs.contains(conversationID)
    }

    /// Include or leave out one listing, keeping the scope the seller chose.
    mutating func set(_ conversationID: String, included: Bool) {
        if scope == "items" {
            conversationIDs.removeAll { $0 == conversationID }
            if included { conversationIDs.append(conversationID) }
        } else {
            excludedIDs.removeAll { $0 == conversationID }
            if !included { excludedIDs.append(conversationID) }
        }
    }
}

enum PromotionFormat {
    static func orderLabel(_ order: String?) -> String {
        PromotionForm.orders.first { $0.value == (order ?? "newest") }?.label ?? "Newest listed first"
    }

    static func offerChecks(_ marketplace: String) -> String {
        marketplace == "poshmark" || marketplace == "depop"
            ? "checks recent likes about every minute; catch-up checks every 15 minutes"
            : "checks every 15 minutes"
    }

    static func minimum(_ row: PromotionAutomation) -> String {
        "\(row.minimumPrice.map { "minimum $\($0)" } ?? "no shared minimum price"); saved individual minimums also apply"
    }

    /// One sentence for what a rule does (describePromotionAutomation).
    static func describe(_ row: PromotionAutomation, name market: (String) -> String) -> String {
        let count = row.conversationIDs.count
        let scope = row.scope == "all" ? "all live listings" : "\(count) chosen \(count == 1 ? "listing" : "listings")"
        let excluded = row.excludedIDs.isEmpty ? "" : ", \(row.excludedIDs.count) excluded"
        let age = row.minDaysListed > 0 ? ", listed \(row.minDaysListed)+ days" : ""
        if row.action == "share" {
            return "Shares \(scope)\(excluded)\(age) at \(row.times.joined(separator: ", ")) (\(row.timezone ?? "UTC")) · "
                + "\(orderLabel(row.shareOrder).lowercased()) · \(row.speed ?? "normal") · up to \((row.dailyCap ?? 0).formatted()) a day"
        }
        let shipping = row.marketplace == "poshmark"
            ? " · \(row.shippingLabel.map { "shipping discount “\($0)”" } ?? "no shipping discount")" : ""
        let likes = row.minLikes ?? 1
        return "\(row.discountPercent ?? 0)% off to new likers on \(scope)\(excluded)\(age) with \(likes)+ \(likes == 1 ? "like" : "likes")"
            + " · \(minimum(row))\(shipping) · \(offerChecks(row.marketplace)) on \(market(row.marketplace))"
    }

    /// What the seller reads before turning a rule on (confirmPromotionOn).
    static func confirmOn(_ row: PromotionAutomation, name market: (String) -> String) -> String {
        let what = row.action == "share"
            ? "Anteroom will share these \(market(row.marketplace)) listings to your followers on its own at \(row.times.joined(separator: ", ")) (\(row.timezone ?? "UTC")), up to \((row.dailyCap ?? 0).formatted()) a day."
            : "Anteroom will send \(row.discountPercent ?? 0)% off offers to new likers on its own; \(offerChecks(row.marketplace)); \(minimum(row))."
        return "\(what) It stops and turns itself off at the first limit or unconfirmed result. Nothing runs until the next round."
    }
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

    init(ebay status: EbayOffersStatus, timezone: String = TimeZone.current.identifier) {
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

    init(promotion row: PromotionAutomation, name market: (String) -> String) {
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
