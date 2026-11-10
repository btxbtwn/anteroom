import Foundation

/// The automation form builds the same request bodies as the desktop's
/// automationsFormat.ts, and reads drafts back into the same boxes.
@main
struct AutomationTests {
    static func main() throws {
        // A sentence alone goes to the server to parse.
        var form = AutomationForm(timezone: "America/Chicago")
        form.prompt = "Drop stale items 10% every Monday"
        let promptOnly = form.body(activate: false)
        precondition(promptOnly.schedule == nil && promptOnly.actions == nil && promptOnly.prompt != nil)

        // Filled boxes send the structured task.
        form.kind = "weekly"
        form.weekdays = [0, 4]
        form.priceOn = true
        form.priceValue = "10"
        form.floor = "15"
        form.marketplaces = ["ebay"]
        form.minAge = "60"
        form.itemIDs = "a, b c"
        let body = form.body(activate: true)
        precondition(body.activate && body.schedule == AutomationSchedule(kind: "weekly", time: "09:00", weekdays: [0, 4]))
        precondition(body.actions == [AutomationAction(type: "price", mode: "percent", value: 10, floor: 15, repeatMode: "step")])
        precondition(body.filters?.minAgeDays == 60 && body.filters?.conversationIDs == ["a", "b", "c"] && body.filters?.brand == nil)
        let json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(body)) as! [String: Any]
        let action = (json["actions"] as! [[String: Any]])[0]
        precondition(action["repeat"] as? String == "step" && json["price_repeat"] as? String == "step")
        precondition((json["filters"] as! [String: Any])["min_age_days"] as? Int == 60)

        // A one-time task is structured even without actions.
        var once = AutomationForm()
        once.kind = "once"
        once.at = "2026-11-02T09:30"
        precondition(once.body(activate: false).schedule == AutomationSchedule(kind: "once", at: "2026-11-02T09:30"))

        // Drafts read back into the boxes.
        let draft = try JSONDecoder().decode(AutomationDraft.self, from: Data(#"""
        {"name":"Stale drop","prompt":"p","timezone":"UTC","schedule":{"kind":"daily","time":"08:15"},
         "filters":{"min_age_days":45,"status":"active"},"actions":[{"type":"price","mode":"amount","value":5,"repeat":"once"},{"type":"relist"}],
         "marketplaces":["poshmark"],"price_repeat":"once","issues":["Pick a floor"],"ready":false}
        """#.utf8))
        let filled = AutomationForm(draft: draft)
        precondition(filled.kind == "daily" && filled.time == "08:15" && filled.minAge == "45" && filled.status == "active")
        precondition(filled.priceOn && filled.priceMode == "amount" && filled.priceValue == "5" && filled.priceRepeat == "once")
        precondition(filled.relist && !filled.delist && filled.marketplaces == ["poshmark"])

        precondition(AutomationFormat.schedule(AutomationSchedule(kind: "weekly", time: "09:00", weekdays: [0, 4]), timezone: "UTC")
                     == "Weekly on Mon, Fri at 09:00 (UTC)")
        precondition(AutomationFormat.actions([AutomationAction(type: "price", mode: "amount", value: 5, floor: 15, repeatMode: "once"),
                                               AutomationAction(type: "relist")]) == "drop $5 once, floor $15, relist")
        precondition(AutomationFormat.runsLine(last: "—", next: "—") == nil)
        precondition(AutomationFormat.runsLine(last: "—", next: "Nov 9, 8:00 AM") == "Next Nov 9, 8:00 AM")
        precondition(AutomationFormat.runsLine(last: "Nov 2, 9:00 AM", next: "—") == "Last run Nov 2, 9:00 AM")
        precondition(AutomationFormat.approvalNote([AutomationAction(type: "price"), AutomationAction(type: "relist")])
                     == "Waits for your approval before relisting")
        precondition(AutomationFormat.approvalNote([AutomationAction(type: "regenerate")]) == nil)
        precondition(AutomationFormat.money(12) == "$12" && AutomationFormat.money(12.5) == "$12.50" && AutomationFormat.money(nil) == "—")

        // Rows say when an automation runs and what it picks, like the web list.
        let name = { (id: String) in ["ebay": "eBay", "poshmark": "Poshmark", "mercari": "Mercari"][id] ?? id }
        precondition(AutomationFormat.trigger(AutomationSchedule(kind: "daily", time: "09:00"), filters: AutomationFilters(minAgeDays: 60),
                                              marketplaces: ["ebay", "poshmark"], name: name)
                     == "Every day 9:00 AM · listed 60+ days on eBay, Poshmark")
        precondition(AutomationFormat.trigger(AutomationSchedule(kind: "weekly", time: "18:30", weekdays: [0, 5]),
                                              filters: AutomationFilters(department: "Men", brand: "Levi's"), marketplaces: [], name: name)
                     == "Every Mon, Sat 6:30 PM · Men, Levi's")
        precondition(AutomationFormat.trigger(AutomationSchedule(kind: "once", at: "2026-11-03T00:05"),
                                              filters: AutomationFilters(conversationIDs: ["a", "b"]), marketplaces: ["mercari"], name: name)
                     == "Once Nov 3, 12:05 AM · 2 items on Mercari")
        precondition(AutomationFormat.trigger(nil, filters: nil, marketplaces: [], name: name) == "No schedule yet")
        precondition(AutomationFormat.runTime("2026-11-05T15:00:00+00:00", timezone: "America/Chicago") == "Nov 5, 9:00 AM")
        precondition(AutomationFormat.runTime(nil, timezone: "UTC") == "—")

        // One list: every automation reads the same way, whichever backend owns it.
        precondition(AutomationRowModel.type([AutomationAction(type: "price"), AutomationAction(type: "relist"), AutomationAction(type: "price")])
                     == "Price drop, Relist")
        precondition(AutomationRowModel.kinds.map(\.label) == ["Price drop, relist or regenerate", "eBay offers", "Poshmark sharing", "Offers to likers"])
        let ebay = try JSONDecoder().decode(EbayOffersStatus.self, from: Data(#"""
        {"connected":true,"allListings":{"enabled":true,"discountPercent":15,"minimumPrice":"20"},"listings":[],"history":[],
         "checkIntervalMinutes":15,"lastCheck":null,"lastError":null}
        """#.utf8))
        precondition(AutomationRowModel(ebay: ebay).when == "Every 15 minutes · All listings, 15% off, minimum $20")
        precondition(AutomationRowModel(ebay: ebay).on == true)
        let offline = try JSONDecoder().decode(EbayOffersStatus.self, from: Data(#"""
        {"connected":false,"allListings":{"enabled":false,"discountPercent":10,"minimumPrice":""},"history":[],
         "listings":[{"conversationId":"c","listingId":"1","title":"T","ruleId":null,"enabled":false,"discountPercent":10,"minimumPrice":""}]}
        """#.utf8))
        precondition(AutomationRowModel(ebay: offline).on == nil && AutomationRowModel(ebay: offline).state == "Not connected")
        let promotions = try JSONDecoder().decode(PromotionAutomationList.self, from: Data(#"""
        {"automations":[
          {"id":"s","name":"Closet","marketplace":"poshmark","action":"share","scope":"all","conversation_ids":[],"excluded_ids":[],
           "times":["09:00","20:30"],"timezone":"UTC","min_days_listed":0,"enabled":false,"available":true,"paused_reason":null,"next_run_at":null,"last_run":null},
          {"id":"m","name":"Likers","marketplace":"mercari","action":"offer","scope":"items","conversation_ids":["a"],"excluded_ids":[],
           "times":[],"timezone":null,"min_days_listed":0,"discount_percent":10,"enabled":false,"available":false,"paused_reason":null,"next_run_at":null,"last_run":null}
        ],"supported":{},"awaiting_capture":{},"speeds":[],"orders":[],"max_daily_cap":6000,"shipping_labels":[]}
        """#.utf8))
        let share = AutomationRowModel(promotion: promotions.automations[0], name: name)
        precondition(share.type == "Poshmark sharing" && share.when == "Every day 9:00 AM, 8:30 PM · all live listings" && share.on == false)
        let likers = AutomationRowModel(promotion: promotions.automations[1], name: name)
        precondition(likers.type == "Mercari offers" && likers.when == "10% off · 1 chosen listing · new likers")
        precondition(likers.note == "Mercari offers are not available in Anteroom yet")
        print("Automation tests passed")
    }
}
