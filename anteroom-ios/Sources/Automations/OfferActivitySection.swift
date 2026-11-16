import SwiftUI

/// Read-only evidence; no marketplace calls or timer.
struct OfferActivitySection: View {
    let api: AnteroomAPI
    @State private var period = "today"
    @State private var start = Date()
    @State private var end = Date()
    @State private var data: OfferAnalyticsResponse?
    @State private var error: String?

    private var dates: (String, String) {
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
        let format = DateFormatter()
        format.calendar = calendar
        format.timeZone = .current
        format.dateFormat = "yyyy-MM-dd"
        return (format.string(from: first), format.string(from: period == "custom" ? end : today))
    }

    var body: some View {
        Section {
            Picker("Period", selection: $period) {
                Text("Today").tag("today")
                Text("This week").tag("week")
                Text("This month").tag("month")
                Text("Custom dates").tag("custom")
            }
            if period == "custom" {
                DatePicker("From", selection: $start, displayedComponents: .date)
                DatePicker("Through", selection: $end, displayedComponents: .date)
            }
            Text("\(dates.0) – \(dates.1) · \(TimeZone.current.identifier) · Weeks start Monday.")
                .font(.caption).foregroundStyle(.secondary)
            if let error {
                Text("Could not read offer activity: \(error)").font(.footnote).foregroundStyle(.secondary)
            } else if let data {
                ForEach(data.marketplaces, id: \.marketplace) { row in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(Marketplaces.name(row.marketplace)).font(.subheadline.weight(.medium))
                        Text(row.confirmedRecipients.map { "\($0) confirmed recipient offers" } ?? "Not available")
                            .font(.subheadline).monospacedDigit()
                        Text(detail(row)).font(.caption).foregroundStyle(.secondary)
                        Text("\(row.unknown) unknown · \(row.unverified) unverified · \(row.failed) failed · \(row.pending) pending")
                            .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                    }
                }
            } else {
                ProgressView("Reading offer activity…")
            }
            Text("Converted sales: Not available. Saved offers cannot be reliably linked to orders. A later sale of the same listing does not prove conversion.")
                .font(.footnote).foregroundStyle(.secondary)
        } header: {
            Text("Offer activity")
        } footer: {
            Text("Partial history: only recorded sends are included; previews, cooldown checks and skips are excluded. eBay history before send-date tracking is excluded. Counts are recipient offers, not unique buyers; repeat offers count again.")
        }
        .task(id: "\(dates.0)/\(dates.1)/\(TimeZone.current.identifier)") {
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

    private func detail(_ row: OfferAnalyticsMarketplace) -> String {
        switch row.marketplace {
        case "poshmark": "\(row.acceptedRequests) accepted requests · recipients not reported"
        case "depop": "\(row.completedJobs) completed bulk jobs · recipients not reported"
        default: "\(row.confirmedSends) confirmed sends with recipient counts"
        }
    }
}
