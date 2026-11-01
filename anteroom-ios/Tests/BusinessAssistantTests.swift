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
        print("Business assistant tests passed")
    }

    private static func required<T>(_ value: T?) throws -> T {
        guard let value else { throw CocoaError(.coderValueNotFound) }
        return value
    }
}
