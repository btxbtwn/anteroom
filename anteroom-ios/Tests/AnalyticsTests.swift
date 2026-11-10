import Foundation

@main
struct AnalyticsTests {
    static func main() throws {
        let data = try JSONDecoder().decode(InventoryAnalytics.self, from: Data(#"""
{"range": "12m", "last_updated_at": null, "incomplete_sales": [{"id": "sale-c", "conversation_id": "c", "title": "Coat", "missing": ["cost", "fees"]}], "undated_sales": 0, "periods_truncated": false, "inventory": {"active": 0, "draft": 9, "sold": 0, "failed": 0, "working": 0, "asking_value": 0.0, "cost_value": 0.0, "cost_known": 0, "stale_count": 0, "stale_value": 0.0, "undated_count": 0}, "sell_through_rate": null, "sales": {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null}, "previous": {"start": "2024-11-01T00:51:14.141012+00:00", "end": "2025-11-01T00:00:00+00:00", "sales": {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null}}, "periods": [{"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "Nov '25"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "Dec '25"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "Jan"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "Feb"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "Mar"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "Apr"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "May"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "Jun"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "Jul"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "Aug"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "Sep"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "Oct"}], "marketplaces": [{"count": 3, "revenue": 120.5, "profit": 40.0, "profit_known": 3, "fees_known": 2, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "eBay", "id": "ebay"}], "categories": [], "brands": [], "labels": [{"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "label": "Spring", "id": "spring", "color": "green"}], "price_tiers": [{"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "id": "budget", "label": "Under $20"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "id": "standard", "label": "$20\u2013$34"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "id": "premium", "label": "$35\u2013$59"}, {"count": 0, "revenue": 0.0, "profit": null, "profit_known": 0, "fees_known": 0, "revenue_known": 0, "days_known": 0, "average_price": null, "median_days": null, "margin": null, "id": "high", "label": "$60 and up"}], "aging": [{"label": "0-30 days", "count": 1, "asking_value": 40.0, "listings": [{"conversation_id": "b", "title": "Bag", "price": 40.0, "days_listed": null}]}], "stale": [], "recent": [{"conversation_id": "a", "title": "Shirt", "price": 25.0, "marketplace": "poshmark", "sold_at": "2026-10-20T10:00:00", "days_listed": 12, "event": null, "profit": 9.5}], "oldest": [], "ads": {"spend": 0.0, "profit": null, "profit_after_ads": null, "marketplaces": []}}
"""#.utf8))
        precondition(data.range == "12m" && data.periods.count == 12 && data.priceTiers.count == 4)
        precondition(data.marketplaces[0].id == "ebay" && data.marketplaces[0].sales.revenue == 120.5)
        precondition(data.labels[0].color == "green" && data.recent[0].profit == 9.5)
        precondition(data.aging[0].listings[0].daysListed == nil && data.incompleteSales[0].missing == ["cost", "fees"])
        precondition(data.previous != nil && data.sales.profit == nil && data.stale.isEmpty)
        let unlinked = try JSONDecoder().decode(InventoryAnalytics.IncompleteSale.self, from: Data(#"{"id":"sale-unlinked","conversation_id":null,"title":"Order 1","missing":["fees"]}"#.utf8))
        precondition(unlinked.id == "sale-unlinked" && unlinked.conversationID == nil)

        precondition(AnalyticsFormat.money(12) == "$12.00" && AnalyticsFormat.money(12.5) == "$12.50")
        precondition(AnalyticsFormat.money(1234) == "$1,234.00" && AnalyticsFormat.money(-3) == "-$3.00")
        precondition(AnalyticsFormat.axisMoney(250) == "$250" && AnalyticsFormat.axisMoney(1500) == "$1.5K")
        precondition(AnalyticsFormat.change(150, 100, AnalyticsFormat.money) == "Up $50.00 (50%)")
        precondition(AnalyticsFormat.change(50, 0, AnalyticsFormat.money) == "Up $50.00")
        precondition(AnalyticsFormat.change(80, 100, AnalyticsFormat.money) == "Down $20.00 (20%)")
        precondition(AnalyticsFormat.change(5, 5, AnalyticsFormat.money) == "No change")
        precondition(AnalyticsFormat.change(nil, 5, AnalyticsFormat.money) == "No comparison data")
        precondition(AnalyticsFormat.days(1) == "1 day" && AnalyticsFormat.saleCount(2) == "2 sales")
        precondition(AnalyticsFormat.profitHint(data.sales) == "Record sale prices and costs to see profit")

        let overview = try JSONDecoder().decode(SalesOverview.self, from: Data(#"""
{"start": "2026-10-06", "end": "2026-11-04", "undated_sales": 0, "weeks_truncated": false,
 "current": {"count": 4, "revenue": 200, "revenue_known": 4, "profit": null, "profit_known": 0, "fees_known": 0, "average_days": null, "days_known": 0, "sell_through": 25},
 "previous": {"start": "2026-09-06", "end": "2026-10-05", "count": 2, "revenue": 100, "revenue_known": 2, "profit": 60, "profit_known": 2, "fees_known": 2, "average_days": 20, "days_known": 2, "sell_through": null},
 "weeks": [{"start": "2026-10-26", "count": 3, "revenue": 150, "revenue_known": 3}],
 "marketplaces": [{"id": "ebay", "count": 4, "revenue": 200, "revenue_known": 4}]}
"""#.utf8))
        precondition(overview.previous?.start == "2026-09-06" && overview.previous?.figures.count == 2)
        precondition(overview.weeks[0].count == 3 && overview.marketplaces[0].id == "ebay")
        let cards = Dictionary(uniqueKeysWithValues: AnalyticsFormat.overviewFigures(
            overview.current, overview.previous?.figures, marketplaceChosen: false).map { ($0.label, $0) })
        precondition(cards["Revenue"]?.value == "$200.00" && cards["Revenue"]?.change == "Up $100.00 (100%)")
        precondition(cards["Profit"]?.value == nil && cards["Profit"]?.change == "No comparison: missing costs or fees")
        precondition(cards["Items sold"]?.change == "Up 2 (100%)")
        precondition(cards["Average days to sell"]?.value == nil && cards["Sell-through"]?.value == "25%")
        let filtered = AnalyticsFormat.overviewFigures(overview.current, nil, marketplaceChosen: true)
        precondition(filtered[4].hint == "Shown for all marketplaces only" && filtered[0].change == nil)
        // Sorted by revenue, the Sold column read 19, 9, 11: by items sold Depop comes before eBay.
        let breakdown = try JSONDecoder().decode([SalesOverview.Marketplace].self, from: Data(#"""
[{"id": "etsy", "count": 19, "revenue": 220.65, "revenue_known": 19},
 {"id": "ebay", "count": 9, "revenue": 122.78, "revenue_known": 9},
 {"id": "depop", "count": 11, "revenue": 112.6, "revenue_known": 11}]
"""#.utf8))
        precondition(AnalyticsFormat.rankBreakdown(breakdown, by: "count").map(\.id) == ["etsy", "depop", "ebay"])
        precondition(AnalyticsFormat.rankBreakdown(breakdown, by: "revenue").map(\.id) == ["etsy", "ebay", "depop"])
        print("Analytics tests passed")
    }
}
