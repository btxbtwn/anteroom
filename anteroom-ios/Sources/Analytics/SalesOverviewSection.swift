import Charts
import SwiftUI

/// The figures and weekly chart above Sales records (SalesOverview.tsx).
struct SalesOverviewSection: View {
    let overview: SalesOverview
    let marketplaceChosen: Bool
    @State private var metric = "count"

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            StatGrid {
                ForEach(AnalyticsFormat.overviewFigures(overview.current, overview.previous?.figures,
                                                        marketplaceChosen: marketplaceChosen)) { figure in
                    Stat(label: figure.label, value: figure.value, hint: figure.hint,
                         change: figure.change, negative: figure.negative)
                }
            }
            if let previous = overview.previous {
                Text("Changes compare with \(Self.day(previous.start)) – \(Self.day(previous.end)), the period of the same length before.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            weekly
        }
    }

    private var weekly: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(metric == "count" ? "Items sold per week" : "Revenue per week").font(.subheadline.weight(.medium))
                Spacer()
                Picker("Weekly chart shows", selection: $metric) {
                    Text("Items sold").tag("count")
                    Text("Revenue").tag("revenue")
                }
                .pickerStyle(.segmented)
                .fixedSize()
            }
            if overview.weeks.allSatisfy({ $0.count == 0 }) {
                Text("No dated sales in this period.").font(.footnote).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 80)
            } else {
                Chart(overview.weeks) { week in
                    BarMark(x: .value("Week", Self.day(week.start)),
                            y: .value(metric == "count" ? "Items sold" : "Revenue", metric == "count" ? Double(week.count) : week.revenue),
                            width: .ratio(0.6))
                        .foregroundStyle(WorkspaceStyle.accent.opacity(0.8))
                        .clipShape(UnevenRoundedRectangle(topLeadingRadius: 4, topTrailingRadius: 4))
                        .accessibilityLabel("Week of \(Self.day(week.start))")
                        .accessibilityValue("\(AnalyticsFormat.saleCount(week.count)), \(AnalyticsFormat.money(week.revenue)) revenue")
                }
                .chartYAxis {
                    AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in
                        // Counts are whole: skip the half steps the automatic ticks add.
                        if let amount = value.as(Double.self), metric != "count" || amount == amount.rounded() {
                            AxisGridLine()
                            AxisValueLabel {
                                Text(metric == "count" ? String(Int(amount)) : AnalyticsFormat.money(amount))
                            }
                        }
                    }
                }
                .chartXAxis {
                    AxisMarks(values: labelledWeeks) { _ in AxisValueLabel(centered: true) }
                }
                .frame(height: 180)
            }
            if overview.weeksTruncated { Text("Showing the latest 52 weeks.").font(.caption).foregroundStyle(.secondary) }
            if !overview.marketplaces.isEmpty {
                Divider()
                Text("By marketplace").font(.subheadline.weight(.medium))
                ForEach(overview.marketplaces) { row in
                    HStack {
                        Text(Marketplaces.name(row.id)).font(.subheadline)
                        Spacer()
                        Text(AnalyticsFormat.saleCount(row.count)).font(.caption).foregroundStyle(.secondary)
                        Text(row.revenueKnown == 0 ? "Not recorded" : AnalyticsFormat.money(row.revenue))
                            .font(.subheadline).monospacedDigit().frame(minWidth: 64, alignment: .trailing)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
        }
        .padding(12)
        .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(WorkspaceStyle.border, lineWidth: 1))
    }

    /// About five labels along the axis, always including the latest week.
    private var labelledWeeks: [String] {
        let every = max(1, Int((Double(overview.weeks.count) / 5).rounded(.up)))
        return overview.weeks.enumerated()
            .filter { (overview.weeks.count - 1 - $0.offset) % every == 0 }
            .map { Self.day($0.element.start) }
    }

    private static func date(_ day: String) -> Date? {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC") ?? .current
        let parts = day.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: 12))
    }

    /// "Oct 26" for the week starting Monday 2026-10-26, read as a UTC day like the web.
    private static func day(_ iso: String) -> String {
        guard let date = date(iso) else { return iso }
        var style = Date.FormatStyle.dateTime.month(.abbreviated).day()
        style.timeZone = TimeZone(identifier: "UTC") ?? .current
        return date.formatted(style)
    }
}
