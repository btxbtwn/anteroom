import Foundation

/// List, Update, Save draft and Delist rows follow the web's rules (MarketplacesSection.tsx), and
/// each confirmation names every marketplace it acts on.
@main
struct MarketplaceActionsTests {
    static func main() throws {
        let drafts = try JSONDecoder().decode([String: NativeDraftState].self, from: Data(#"""
        {
          "ebay": {"state":"published","revision_id":"r1","listing_url":"https://www.ebay.com/itm/1","running":false},
          "poshmark": {"state":"publishing","action":"list","running":true,"progress":{"label":"Uploading photos"}},
          "mercari": {"state":"unknown","error":"Chrome stopped answering."},
          "depop": {"state":"draft","error":"Add a size.","error_code":"missing_fields"}
        }
        """#.utf8))
        let live = try JSONDecoder().decode([String: MarketplaceLiveListing].self, from: Data(#"""
        {"ebay": {"url":"https://www.ebay.com/itm/1","listing_id":"1","source":"anteroom","sold_here":false,"state":"live"}}
        """#.utf8))
        let all = Set(MarketplaceActions.order)

        let rows = Dictionary(uniqueKeysWithValues: MarketplaceActions.rows(
            actionable: all, drafts: drafts, live: live, revisionID: "r2").map { ($0.marketplace, $0) })

        // Live on eBay: Delist, not List, and the newer saved version is noted.
        let ebay = rows["ebay"]!
        precondition(ebay.label == "Listed" && ebay.tone == .live && ebay.canDelist && !ebay.canList)
        precondition(ebay.detail == "Your latest edits aren't on eBay yet.")
        precondition(ebay.link?.absoluteString == "https://www.ebay.com/itm/1")
        // A live listing behind the saved version offers Update, not Save draft.
        precondition(ebay.canUpdate && ebay.outdated && !ebay.canSaveDraft)
        let current = MarketplaceActions.rows(actionable: ["ebay"], drafts: drafts, live: live, revisionID: "r1")[0]
        precondition(current.canUpdate && !current.outdated)

        // A running List shows its step and allows nothing else.
        let poshmark = rows["poshmark"]!
        precondition(poshmark.label == "Listing…" && poshmark.running && !poshmark.canList && !poshmark.canDelist)
        precondition(poshmark.detail == "Uploading photos…")

        // An outcome nobody confirmed must be checked before acting again.
        let mercari = rows["mercari"]!
        precondition(mercari.label == "Unconfirmed" && mercari.unconfirmed && !mercari.canList)

        // A refused List can be tried again; never listed anywhere can be listed.
        precondition(rows["depop"]!.label == "Not listed" && rows["depop"]!.tone == .failed && rows["depop"]!.canList)
        precondition(rows["etsy"]!.label == "Not listed" && rows["etsy"]!.canList && !rows["etsy"]!.canDelist)
        precondition(rows["etsy"]!.canSaveDraft && !rows["etsy"]!.canUpdate)
        precondition(!poshmark.canUpdate && !poshmark.canSaveDraft && !mercari.canUpdate)

        // Only the marketplaces the server can act on get a row, in the web's order.
        let some = MarketplaceActions.rows(actionable: ["depop", "ebay"], drafts: drafts, live: live, revisionID: "r2")
        precondition(some.map(\.marketplace) == ["ebay", "depop"])

        // A sold item is not listed anywhere else, and the sold listing is not delisted.
        let sold = try JSONDecoder().decode([String: MarketplaceLiveListing].self, from: Data(#"""
        {"mercari": {"url":"https://www.mercari.com/us/item/m1","listing_id":"m1","sold_here":true,"state":"live"}}
        """#.utf8))
        let soldRows = MarketplaceActions.rows(actionable: all, drafts: [:], live: sold, revisionID: "r2")
        precondition(soldRows.allSatisfy { !$0.canList })
        precondition(soldRows.first { $0.marketplace == "mercari" }!.label == "Sold here")
        precondition(!soldRows.first { $0.marketplace == "mercari" }!.canDelist)

        // Delisting in progress, and a delisted listing that can be listed again.
        let gone = try JSONDecoder().decode([String: MarketplaceLiveListing].self, from: Data(#"""
        {"etsy": {"url":null,"listing_id":"e1","sold_here":false,"state":"deleting"},
         "depop": {"url":null,"listing_id":null,"sold_here":false,"state":"deleted"}}
        """#.utf8))
        let goneRows = Dictionary(uniqueKeysWithValues: MarketplaceActions.rows(
            actionable: all, drafts: [:], live: gone, revisionID: "r2").map { ($0.marketplace, $0) })
        precondition(goneRows["etsy"]!.label == "Delisting…" && goneRows["etsy"]!.running && !goneRows["etsy"]!.canDelist)
        precondition(goneRows["depop"]!.label == "Deleted" && goneRows["depop"]!.canList)
        // A delisted listing keeps its ID and may still have a published record; it can be listed again.
        let delisted = try JSONDecoder().decode([String: MarketplaceLiveListing].self, from: Data(#"""
        {"mercari": {"url":"https://www.mercari.com/us/item/m1","listing_id":"m1","sold_here":false,"state":"deleted"}}
        """#.utf8))
        let stale = try JSONDecoder().decode([String: NativeDraftState].self, from: Data(#"""
        {"mercari": {"state":"published","revision_id":"r2","listing_url":"https://www.mercari.com/us/item/m1"}}
        """#.utf8))
        let relist = MarketplaceActions.rows(actionable: ["mercari"], drafts: stale, live: delisted, revisionID: "r2")[0]
        precondition(relist.label == "Deleted" && relist.canList && !relist.canUpdate && !relist.canDelist)

        // Confirmations name each marketplace and say what Delist does there.
        let one = MarketplaceActions.listQuestion(title: "Blue shirt", marketplace: "ebay")
        precondition(one.title == "List “Blue shirt” on eBay?" && one.button == "List on eBay")
        precondition(!one.message.contains("Chrome"))
        let chrome = MarketplaceActions.listQuestion(title: "Blue shirt", marketplace: "depop")
        precondition(chrome.message.contains("Depop goes through Chrome on your computer"))
        let update = MarketplaceActions.updateQuestion(title: "Blue shirt", marketplace: "poshmark")
        precondition(update.title == "Update the live Poshmark listing for “Blue shirt” with what's saved in Anteroom?")
        precondition(update.button == "Update on Poshmark" && update.message.contains("already up") && update.message.contains("Chrome"))
        let delist = MarketplaceActions.delistQuestion(title: "Blue shirt", marketplaces: ["ebay", "mercari"])
        precondition(delist.title == "Delist “Blue shirt” from 2 marketplaces?" && delist.button == "Delist from 2")
        precondition(delist.message.contains("• eBay: ended\n• Mercari: deleted"))
        let everywhere = MarketplaceActions.listEverywhereQuestion(title: "Blue shirt", marketplaces: ["ebay", "etsy", "depop"])
        precondition(everywhere.title == "List “Blue shirt” on 3 marketplaces?" && everywhere.button == "List on 3 marketplaces")
        precondition(everywhere.message.contains("eBay, Etsy and Depop") && everywhere.message.contains("Depop goes through Chrome"))
        precondition(MarketplaceActions.listEverywhereQuestion(title: "Blue shirt", marketplaces: ["ebay"]) == one)

        // A rate limit counts down from the marketplace's retry time.
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        precondition(MarketplaceActions.secondsUntil("2027-01-15T08:00:30Z", now: now) == 30)
        precondition(MarketplaceActions.secondsUntil(nil, now: now) == 0)
        print("MarketplaceActionsTests passed")
    }
}
