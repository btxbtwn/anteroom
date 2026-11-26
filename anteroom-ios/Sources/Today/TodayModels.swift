import Foundation

/// Today's wording and numbers, as the web's Today page (HomePage.tsx) shows them.
enum TodayText {
    static let periods: [(value: String, label: String)] = [
        ("weekly", "Weekly"), ("monthly", "Monthly"), ("quarterly", "Quarterly"), ("yearly", "Yearly"),
    ]

    static let metrics: [(value: String, label: String, placeholder: String)] = [
        ("profit", "Profit ($)", "2000"),
        ("sales", "Sales ($)", "5000"),
        ("listings", "Listings created", "100"),
        ("sell_through_rate", "Sell-through rate (%)", "35"),
    ]

    static func periodLabel(_ value: String) -> String {
        periods.first { $0.value == value }?.label ?? value
    }

    static func metricPlaceholder(_ value: String) -> String {
        metrics.first { $0.value == value }?.placeholder ?? ""
    }

    static func pace(_ pace: String) -> String {
        switch pace {
        case "achieved": "Achieved"
        case "ahead": "Ahead"
        case "on_track": "On track"
        case "behind": "Behind pace"
        default: pace
        }
    }

    /// Whole dollars without cents, otherwise two places: "$1,250", "-$12.50".
    /// Halves round away from zero, as the web's toLocaleString does.
    static func currency(_ value: Double?, locale: Locale = .current) -> String {
        guard let value, value.isFinite else { return "—" }
        let magnitude = abs(value)
        let digits = magnitude.truncatingRemainder(dividingBy: 1) == 0 ? 0 : 2
        let number = magnitude.formatted(.number.locale(locale).precision(.fractionLength(digits)).rounded(rule: .toNearestOrAwayFromZero))
        return "\(value < 0 ? "-" : "")$\(number)"
    }

    static func goalValue(_ value: Double, unit: String, locale: Locale = .current) -> String {
        switch unit {
        case "currency": currency(value, locale: locale)
        case "percent": "\(value.formatted(.number.locale(locale).precision(.fractionLength(0...1)).rounded(rule: .toNearestOrAwayFromZero)))%"
        default: value.formatted(.number.locale(locale).precision(.fractionLength(0)).rounded(rule: .toNearestOrAwayFromZero))
        }
    }

    static func remaining(_ goal: GoalProgress, locale: Locale = .current) -> String {
        goal.remaining <= 0 ? "Target reached" : "\(goalValue(goal.remaining, unit: goal.unit, locale: locale)) remaining"
    }

    static func window(_ goal: GoalProgress, locale: Locale = .current) -> String {
        let days = goal.daysRemaining == 1 ? "1 day left" : "\(goal.daysRemaining) days left"
        return "\(day(goal.windowStart, locale: locale)) – \(day(goal.windowEnd, locale: locale)) · \(days)"
    }

    /// "42% of target", and the even pace by now (or the sell-through note).
    static func progress(_ goal: GoalProgress) -> (done: String, pace: String) {
        let pace = goal.metric == "sell_through_rate"
            ? (goal.note ?? "Window \(goal.daysElapsed)/\(goal.daysTotal)d")
            : "Pace target \(Int(goal.expectedPercent.rounded()))%"
        return ("\(Int(goal.progressPercent.rounded()))% of target", pace)
    }

    static func listedHint(_ today: HomeToday) -> String {
        switch today.draftsCreated {
        case 0: "New listings today"
        case 1: "1 draft created today"
        default: "\(today.draftsCreated) drafts created today"
        }
    }

    static func profitHint(_ today: HomeToday) -> String {
        guard today.unitsSold > 0 else { return "Recorded profit today" }
        return today.profitKnown == today.unitsSold
            ? "All today's sales have cost recorded"
            : "\(today.profitKnown) of \(today.unitsSold) sales with cost recorded"
    }

    static func automationHint(_ today: HomeToday) -> String {
        "\(today.sharesSent) \(today.sharesSent == 1 ? "share" : "shares") · \(today.offersSent) \(today.offersSent == 1 ? "offer" : "offers")"
    }

    /// "Wednesday, November 25" for the summary's date.
    static func heading(_ isoDate: String, locale: Locale = .current) -> String {
        guard let date = parse(isoDate) else { return isoDate }
        var style = Date.FormatStyle(date: .omitted, time: .omitted, locale: locale, timeZone: utc).weekday(.wide).month(.wide).day()
        style.timeZone = utc
        return date.formatted(style)
    }

    /// Why a goal can't be saved yet, or nil when it can.
    static func targetProblem(_ text: String, metric: String) -> String? {
        let cleaned = text.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: ",", with: "")
        guard let target = Double(cleaned), target.isFinite, target > 0 else { return "Enter a target greater than zero." }
        if metric == "sell_through_rate" && target > 100 { return "Sell-through rate target cannot exceed 100%." }
        return nil
    }

    static func target(_ text: String) -> Double? {
        Double(text.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: ",", with: ""))
    }

    /// The tab badge: the active count, nothing when there is none.
    static func badge(_ active: Int?) -> Int { max(0, active ?? 0) }

    private static let utc = TimeZone(identifier: "UTC")!

    private static func parse(_ isoDate: String) -> Date? {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        let parts = isoDate.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: 12))
    }

    private static func day(_ isoDate: String, locale: Locale) -> String {
        guard let date = parse(isoDate) else { return isoDate }
        var style = Date.FormatStyle(date: .omitted, time: .omitted, locale: locale, timeZone: utc).month(.abbreviated).day()
        style.timeZone = utc
        return date.formatted(style)
    }
}
