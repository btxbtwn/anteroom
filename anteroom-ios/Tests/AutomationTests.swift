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
        print("Automation tests passed")
    }
}
