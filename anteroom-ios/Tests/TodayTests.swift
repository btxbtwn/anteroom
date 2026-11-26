import Foundation

/// Today on the phone reads the web's Today API and says what the web says.
@main
struct TodayTests {
    static func main() {
        let us = Locale(identifier: "en_US")
        let json = """
        {"needs_attention": [], "dismissed": [], "resolved": [],
         "counts": {"active": 3, "dismissed": 0, "resolved": 1, "by_kind": {"still_listed": 3}},
         "today": {"date": "2026-11-25", "timezone": "America/New_York", "units_listed": 4, "drafts_created": 2,
                   "units_sold": 3, "revenue": 125.5, "profit": null, "profit_known": 1,
                   "shares_sent": 1, "offers_sent": 2, "automation_actions": 3},
         "goals": [{"id": "g1", "period": "monthly", "metric": "profit", "label": "Monthly profit",
                    "custom_label": null, "target": 2000, "current": 1250, "remaining": 750,
                    "progress_percent": 62.5, "expected_percent": 80.2, "pace": "behind",
                    "window_start": "2026-11-01", "window_end": "2026-11-30", "days_elapsed": 25,
                    "days_total": 30, "days_remaining": 5, "unit": "currency", "note": null},
                   {"id": "g2", "period": "weekly", "metric": "sell_through_rate", "label": "Weekly sell-through",
                    "target": 35, "current": 40.25, "remaining": 0, "progress_percent": 115,
                    "expected_percent": 50, "pace": "achieved", "window_start": "2026-11-23",
                    "window_end": "2026-11-29", "days_elapsed": 3, "days_total": 7, "days_remaining": 1,
                    "unit": "percent", "note": "12 of 30 sold"}]}
        """
        let home = try! JSONDecoder().decode(HomeSummary.self, from: Data(json.utf8))
        precondition(home.counts.active == 3 && home.today.profit == nil && home.goals.count == 2)
        precondition(TodayText.badge(home.counts.active) == 3 && TodayText.badge(nil) == 0)

        // Today's summary hints.
        precondition(TodayText.listedHint(home.today) == "2 drafts created today")
        precondition(TodayText.profitHint(home.today) == "1 of 3 sales with cost recorded")
        precondition(TodayText.automationHint(home.today) == "1 share · 2 offers")
        precondition(TodayText.currency(home.today.revenue, locale: us) == "$125.50")
        precondition(TodayText.currency(1250, locale: us) == "$1,250")
        precondition(TodayText.currency(-12.5, locale: us) == "-$12.50")
        precondition(TodayText.currency(nil) == "—")
        precondition(TodayText.heading("2026-11-25", locale: us) == "Wednesday, November 25")

        // Goals: value, remaining, window and pace, as on the web.
        let profit = home.goals[0], sellThrough = home.goals[1]
        precondition(TodayText.goalValue(profit.current, unit: profit.unit, locale: us) == "$1,250")
        precondition(TodayText.remaining(profit, locale: us) == "$750 remaining")
        precondition(TodayText.window(profit, locale: us) == "Nov 1 – Nov 30 · 5 days left")
        precondition(TodayText.pace(profit.pace) == "Behind pace")
        precondition(TodayText.progress(profit) == ("63% of target", "Pace target 80%"))
        precondition(TodayText.goalValue(sellThrough.current, unit: "percent", locale: us) == "40.3%")
        precondition(TodayText.remaining(sellThrough) == "Target reached")
        precondition(TodayText.window(sellThrough, locale: us).hasSuffix("1 day left"))
        precondition(TodayText.progress(sellThrough).pace == "12 of 30 sold")
        precondition(TodayText.goalValue(12, unit: "count", locale: us) == "12")

        // A goal needs a positive target; sell-through stops at 100%.
        precondition(TodayText.targetProblem("", metric: "profit") == "Enter a target greater than zero.")
        precondition(TodayText.targetProblem("0", metric: "sales") != nil)
        precondition(TodayText.targetProblem("120", metric: "sell_through_rate") == "Sell-through rate target cannot exceed 100%.")
        precondition(TodayText.targetProblem("2,000", metric: "profit") == nil && TodayText.target("2,000") == 2000)
        precondition(TodayText.metricPlaceholder("listings") == "100" && TodayText.periodLabel("quarterly") == "Quarterly")

        // A goal edit sends only what it names; an empty label clears it.
        let update = String(data: try! JSONEncoder().encode(GoalUpdate(period: nil, metric: nil, target: 50, label: "")), encoding: .utf8)!
        precondition(update.contains("\"label\":\"\"") && !update.contains("period"))
        print("Today tests passed")
    }
}
