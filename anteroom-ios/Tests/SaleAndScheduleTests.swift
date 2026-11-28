import Foundation

/// Mark as sold and scheduled publishing send what the web sends, and refuse
/// what the web refuses, before anything reaches the server.
@main
struct SaleAndScheduleTests {
    static func main() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York")!
        let today = calendar.date(from: DateComponents(year: 2026, month: 11, day: 25, hour: 20))!

        var form = SaleForm(marketplace: "poshmark", soldOn: today, requestID: String(repeating: "a", count: 32))
        form.price = "$24.50"
        form.fees = " 4.9 "
        guard case .success(let sale) = form.input(today: today, calendar: calendar) else { fatalError("valid sale refused") }
        precondition(sale.soldOn == "2026-11-25" && sale.quantity == 1 && sale.itemRevenue == "24.50")
        precondition(sale.fees == "4.9" && sale.shippingCost == nil && sale.shippingCredit == nil)
        let json = String(data: try! JSONEncoder().encode(sale), encoding: .utf8)!
        precondition(json.contains("\"sold_on\":\"2026-11-25\"") && json.contains("\"request_id\"") && json.contains("\"currency\":\"USD\""))

        form.price = "24,50"
        precondition(form.input(today: today, calendar: calendar) == .failure(SaleProblem("Enter amounts like 24.50, or leave them blank.")))
        form.price = ""
        form.quantity = "0"
        precondition(form.input(today: today, calendar: calendar) == .failure(SaleProblem("Enter how many sold, 1 or more.")))
        form.quantity = "2"
        form.soldOn = today.addingTimeInterval(86_400)
        precondition(form.input(today: today, calendar: calendar) == .failure(SaleProblem("Choose the day it sold, today or earlier.")))
        precondition(SaleForm.marketplaces.last == "other")

        let result = try! JSONDecoder().decode(SellerSaleResult.self, from: Data("""
        {"sale": {}, "status": "sold", "units_sold": 1, "quantity": 1, "listings": {}, "delist_offer": ["ebay", "depop"]}
        """.utf8))
        precondition(result.delistOffer == ["ebay", "depop"] && result.status == "sold")

        // Scheduling names marketplaces and a future time, and says edits unschedule it.
        precondition(DraftScheduling.problem(marketplaces: [], at: today.addingTimeInterval(60), now: today) == "Choose at least one marketplace.")
        precondition(DraftScheduling.problem(marketplaces: ["ebay"], at: today, now: today) == "Choose a future publish date and time.")
        precondition(DraftScheduling.problem(marketplaces: ["ebay"], at: today.addingTimeInterval(60), now: today) == nil)
        precondition(DraftScheduling.ordered(["depop", "ebay", "poshmark"]) == ["ebay", "poshmark", "depop"])
        let question = DraftScheduling.question(title: "Coat", marketplaces: ["ebay", "poshmark"], at: "Nov 26 at 9:00 AM")
        precondition(question.title == "Schedule “Coat” to publish on eBay, Poshmark?")
        precondition(question.message.contains("unschedules it automatically") && question.button == "Schedule publish")
        precondition(DraftScheduling.wire(Date(timeIntervalSince1970: 0)) == "1970-01-01T00:00:00Z")
        let suggested = DraftScheduling.defaultTime(now: today)
        precondition(suggested > today && suggested.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 60) == 0)

        let schedule = try! JSONDecoder().decode(DraftSchedule.self, from: Data("""
        {"conversation_id": "c1", "title": "Coat", "ready_to_list": true, "scheduled_publish_at": "2026-11-26T14:00:00Z",
         "scheduled_marketplaces": ["ebay", "poshmark"], "scheduled_status": "scheduled"}
        """.utf8))
        precondition(DraftScheduling.summary(schedule, at: "Nov 26") == "Scheduled for Nov 26 on eBay, Poshmark")
        print("Sale and schedule tests passed")
    }
}
