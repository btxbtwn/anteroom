import Foundation

/// The business assistant reads the hosted transcript the way AssistantPage.tsx
/// does, and words its change and automation cards the same.
@main
struct BusinessAssistantTests {
    static func main() throws {
        let turns = try JSONDecoder().decode([AssistantTurn].self, from: Data(#"""
        [{"id":"u1","role":"user","text":"Mark down old stock","created_at":"2026-11-01T10:00:00+00:00",
          "changes":null,"automation":null,"attachments":[{"id":"a","name":"tag.jpg","mime_type":"image/jpeg"}]},
         {"id":"a1","role":"assistant","text":"Here is the plan.","created_at":null,
          "changes":{"status":"applied","items":[
            {"listing_id":"c1","title":"Levi's tee","fields":[{"field":"asking","old":25,"new":21.25},{"field":"labels","old":[],"new":["Sale","Old"]}]},
            {"listing_id":"c2","title":"Jacket","fields":[{"field":"size","old":"M","new":"L"}],"skipped":"Anteroom was generating or sending it."}]},
          "automation":{"status":"proposed","proposal":{"op":"create","name":"Stale drop","timezone":"UTC",
            "schedule":{"kind":"daily","time":"08:00"},"actions":[{"type":"price","mode":"percent","value":10,"repeat":"step"}],
            "marketplaces":[],"issues":["Pick a floor"],"ready":false}},
          "attachments":[]},
         {"id":"a2","role":"assistant","text":"Odd card","changes":{"status":"proposed"},"automation":{"status":"dismissed","proposal":{"actions":"bad"}}}]
        """#.utf8))
        precondition(turns.count == 3 && turns[0].attachmentNames == ["tag.jpg"])
        // An unreadable card leaves the turn, and an unreadable draft leaves its status.
        precondition(turns[2].changes == nil && turns[2].automation?.status == "dismissed" && turns[2].automation?.proposal == nil)

        let changes = try required(turns[1].changes)
        precondition(AssistantFormat.changesHeading(changes) == "Applied to 1 listing, 1 skipped")
        let fields = changes.items[0].fields
        precondition(AssistantFormat.changeValue("asking", fields[0].old) == "$25")
        precondition(AssistantFormat.changeValue("asking", fields[0].new) == "$21.25")
        precondition(AssistantFormat.changeValue("labels", fields[1].old) == "none")
        precondition(AssistantFormat.changeValue("labels", fields[1].new) == "Sale, Old")
        precondition(AssistantFormat.fieldLabel("ebay_size") == "eBay size" && AssistantFormat.fieldLabel("other") == "other")
        let proposed = try JSONDecoder().decode(AssistantChanges.self, from: Data(#"{"status":"proposed","items":[]}"#.utf8))
        precondition(AssistantFormat.changesHeading(proposed) == "Proposed changes to 0 listings")

        let automation = try required(turns[1].automation)
        precondition(AssistantFormat.automationHeading(automation) == "Review “Stale drop” before it is saved")
        let proposal = try required(automation.proposal)
        precondition(AssistantFormat.automationSummary(proposal)
                     == "Daily at 08:00 (UTC) · drop 10% each cycle · no marketplaces named")
        precondition(automation.proposal?.ready == false && automation.proposal?.issues == ["Pick a floor"])

        precondition(AssistantFormat.linkedListingID("#listing-abc_12-x") == "abc_12-x")
        precondition(AssistantFormat.linkedListingID("anteroom://app#listing-abc") == "abc")
        precondition(AssistantFormat.linkedListingID("https://example.com/listing-abc") == nil)
        precondition(AssistantFormat.linkedListingID("#0a398113547b") == "0a398113547b")
        precondition(AssistantFormat.linkedListingID("#notes") == nil)
        precondition(AssistantFormat.linkedListingID("#listing-") == nil && AssistantFormat.linkedListingID("#listing-a/b") == nil)

        // A question asked here is drawn until the saved conversation brings it back, then its answer.
        let asked = AssistantLiveTurn(question: "Q", asked: 1)
        precondition(AssistantFormat.liveParts(asked, turns: Array(turns.prefix(1))) == (true, true))
        precondition(AssistantFormat.liveParts(asked, turns: Array(turns.prefix(2))) == (false, true))
        precondition(AssistantFormat.liveParts(asked, turns: turns) == (false, false))
        // A resumed answer draws only while the saved conversation still ends with the question.
        let resumed = AssistantLiveTurn(question: nil, asked: 1)
        precondition(AssistantFormat.liveParts(resumed, turns: Array(turns.prefix(1))) == (false, true))
        precondition(AssistantFormat.liveParts(resumed, turns: Array(turns.prefix(2))) == (false, false))

        precondition(AssistantFormat.sendsNow(busy: false, queued: 0))
        precondition(!AssistantFormat.sendsNow(busy: true, queued: 0) && !AssistantFormat.sendsNow(busy: false, queued: 1))
        // The home screen greets by the seller's local time.
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let at = { (hour: Int) in
            AssistantFormat.greeting(at: calendar.date(from: DateComponents(year: 2026, month: 11, day: 7, hour: hour))!, calendar: calendar)
        }
        precondition([4, 5, 11, 12, 17, 18, 23].map(at) == [
            "Good evening", "Good morning", "Good morning", "Good afternoon", "Good afternoon", "Good evening", "Good evening",
        ])
        precondition(AssistantFormat.starters.count == 4)

        // History groups by local day, newest first, and leaves empty groups out.
        let now = calendar.date(from: DateComponents(year: 2026, month: 11, day: 8, hour: 15))!
        let stamp = { (day: Int, hour: Int) in
            String(ISO8601DateFormatter().string(from: calendar.date(from: DateComponents(year: 2026, month: 11, day: day, hour: hour))!).dropLast())
        }
        precondition([8, 7, 6, 2, 1].map { AssistantFormat.chatGroup(stamp($0, 10), now: now, calendar: calendar) }
                     == ["Today", "Yesterday", "Previous 7 days", "Previous 7 days", "Older"])
        precondition(AssistantFormat.chatGroup(nil, now: now, calendar: calendar) == "Older")
        precondition(AssistantFormat.chatTime(stamp(7, 10), now: now, calendar: calendar) == "Yesterday")
        precondition(AssistantFormat.chatTime(nil, now: now, calendar: calendar) == "")
        let chats = [("old", stamp(1, 10)), ("early", stamp(8, 8)), ("late", stamp(8, 14)), ("yesterday", stamp(7, 10))]
            .map { AssistantThread(id: $0.0, title: $0.0, updatedAt: $0.1) }
        let grouped = AssistantFormat.groupChats(chats, now: now, calendar: calendar)
        precondition(grouped.map(\.group) == ["Today", "Yesterday", "Older"])
        precondition(grouped[0].threads.map(\.id) == ["late", "early"])
        // Streamed frames build the answer; reasoning only changes the status.
        var live = AssistantLiveTurn(question: "How are sales?", asked: 0)
        AssistantFormat.fold(event: "status", data: "Reading your shop…", into: &live)
        AssistantFormat.fold(event: "message", data: "Sales are ", into: &live)
        AssistantFormat.fold(event: "thinking", data: "hmm", into: &live)
        AssistantFormat.fold(event: "message", data: "up.", into: &live)
        AssistantFormat.fold(event: "agent", data: "{}", into: &live)
        precondition(live.text == "Sales are up." && live.status == "Thinking…")

        // The first frame paints at once, then at most one paint per interval.
        var throttle = LivePaintThrottle()
        let start = ContinuousClock.now
        precondition(throttle.paintsNow(at: start))
        throttle.painted(at: start)
        precondition(!throttle.paintsNow(at: start + .milliseconds(20)))
        precondition(throttle.paintsNow(at: start + LivePaintThrottle.interval))

        // Following the newest line: at the end (the composer covers the bottom inset), near it, and well above it.
        precondition(ChatScroll.nearBottom(offsetY: 1060, containerHeight: 800, bottomInset: 60, contentHeight: 1800))
        precondition(ChatScroll.nearBottom(offsetY: 1000, containerHeight: 800, bottomInset: 60, contentHeight: 1800))
        precondition(!ChatScroll.nearBottom(offsetY: 600, containerHeight: 800, bottomInset: 60, contentHeight: 1800))
        // A short conversation is always at its end.
        precondition(ChatScroll.nearBottom(offsetY: 0, containerHeight: 800, bottomInset: 60, contentHeight: 300))
        print("Business assistant tests passed")
    }

    private static func required<T>(_ value: T?) throws -> T {
        guard let value else { throw CocoaError(.coderValueNotFound) }
        return value
    }
}
