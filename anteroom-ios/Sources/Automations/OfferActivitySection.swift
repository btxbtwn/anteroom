import SwiftUI

/// Read-only evidence; no marketplace calls or timer.
struct OfferActivitySection: View {
    let api: AnteroomAPI
    let refreshToken: UUID
    @State private var period = "today"
    @State private var start = Date()
    @State private var end = Date()
    @State private var data: OfferAnalyticsResponse?
    @State private var error: String?

    private var dateRange: (Date, Date) {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .current
        calendar.firstWeekday = 2
        let today = Date()
        let first: Date
        if period == "week" {
            first = calendar.dateInterval(of: .weekOfYear, for: today)?.start ?? today
        } else if period == "month" {
            first = calendar.dateInterval(of: .month, for: today)?.start ?? today
        } else {
            first = period == "custom" ? start : today
        }
        return (first, period == "custom" ? end : today)
    }

    var body: some View {
        let (first, last) = dateRange
        let dates = (OfferActivityDates.day(first, calendar: .current), OfferActivityDates.day(last, calendar: .current))
        let format = Date.FormatStyle.dateTime.month(.abbreviated).day().year()
        Section {
            Picker("Period", selection: $period) {
                Text("Today").tag("today")
                Text("This week").tag("week")
                Text("This month").tag("month")
                Text("Custom dates").tag("custom")
            }
            .accessibilityIdentifier("offer-activity-period")
            if period == "custom" {
                DatePicker("From", selection: $start, displayedComponents: .date)
                DatePicker("Through", selection: $end, displayedComponents: .date)
            }
            Text(dates.0 == dates.1 ? first.formatted(format) : "\(first.formatted(format)) – \(last.formatted(format))")
                .font(.caption).foregroundStyle(.secondary)
            if let error {
                Text("Could not read offer activity: \(error)").font(.footnote).foregroundStyle(.secondary)
            } else if let data {
                ForEach(data.marketplaces, id: \.marketplace) { row in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(Marketplaces.name(row.marketplace)).font(.subheadline.weight(.medium))
                        Text(OfferActivityText.sent(row)).font(.subheadline).monospacedDigit()
                        ForEach(OfferActivityText.reach(row), id: \.self) { line in
                            Text(line).font(.caption).foregroundStyle(.secondary)
                        }
                        if let problems = OfferActivityText.problems(row) {
                            Text(problems).font(.caption).foregroundStyle(.orange).monospacedDigit()
                        }
                    }
                }
            } else {
                ProgressView("Reading offer activity…")
            }
        } header: {
            Text("Offer activity")
        } footer: {
            Text("Counts include sent offers with a recorded send date, one per listing each time. A buyer can receive more than one. Marketplaces don’t report which offers led to sales.")
        }
        .task(id: "\(dates.0)/\(dates.1)/\(TimeZone.current.identifier)/\(refreshToken)") {
            data = nil
            error = nil
            do {
                let result = try await api.offerActivity(start: dates.0, end: dates.1, timezone: TimeZone.current.identifier)
                try Task.checkCancellation()
                data = result
                error = nil
            } catch is CancellationError {
                // A newer period owns the result.
            } catch {
                if !Task.isCancelled { self.error = error.localizedDescription }
            }
        }
    }
}

/// What each marketplace confirmed, in the same words as the web app.
enum OfferActivityText {
    private static func count(_ n: Int, _ one: String) -> String { "\(n) \(one)\(n == 1 ? "" : "s")" }

    static func sent(_ row: OfferAnalyticsMarketplace) -> String {
        let total = row.confirmedSends + row.acceptedRequests + row.completedJobs + row.unverified
        return total == 1 ? "1 offer sent" : "\(total) offers sent"
    }

    static func reach(_ row: OfferAnalyticsMarketplace) -> [String] {
        var lines: [String] = []
        if let recipients = row.confirmedRecipients {
            lines.append("Reached \(count(recipients, "buyer")), as \(Marketplaces.name(row.marketplace)) reported")
        }
        if row.acceptedRequests > 0 {
            let which = row.acceptedRequests == 1 ? "it" : "all \(row.acceptedRequests)"
            lines.append("Poshmark accepted \(which) but doesn't say how many likers got each offer")
        }
        if row.completedJobs > 0 {
            let which = row.completedJobs == 1 ? "it" : "all \(row.completedJobs)"
            lines.append("Depop finished \(which) but doesn't say how many likers got each offer")
        }
        if row.unverified > 0 {
            lines.append("\(count(row.unverified, "offer")) with no marketplace receipt saved")
        }
        return lines
    }

    static func problems(_ row: OfferAnalyticsMarketplace) -> String? {
        var parts: [String] = []
        if row.unknown > 0 { parts.append("\(row.unknown) not confirmed") }
        if row.failed > 0 { parts.append("\(row.failed) failed") }
        if row.pending > 0 { parts.append("\(row.pending) sending") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}
