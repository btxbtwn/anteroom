import Foundation

/// Tab readiness and List everywhere's cards follow the web's rules.
@main
struct MarketplaceReadinessTests {
    static func main() throws {
        let preflight = try JSONDecoder().decode(NativePreflight.self, from: Data(#"""
        {"revision_id":"r1","marketplaces":{
          "ebay":{"ready":true,"gaps":[{"field":"marketplace_photos.ebay","message":"One photo is left off.","blocking":false}],"missing":[]},
          "depop":{"ready":false,"gaps":[
            {"field":"price","message":"Depop needs a price.","blocking":true},
            {"field":"depop_specifics.condition","message":"Condition: Required field is empty.","blocking":false,"required":true,"label":"Condition"}],
           "missing":[
            {"field":"price","label":"Item price","message":"Depop needs a price."},
            {"field":"depop_specifics.condition","label":"Condition","message":"Condition: Required field is empty."}]}
        }}
        """#.utf8))
        // The count is the server's: required fields left empty count, not only the ones refusing List.
        precondition(preflight.marketplaces["ebay"]?.missing.isEmpty == true)
        precondition(preflight.marketplaces["depop"]?.missing.count == 2)

        func make(_ marketplace: String, canAct: Bool = true, draft: NativeDraftState? = nil,
                  live: MarketplaceLiveListing? = nil, sold: Bool = false) -> ListChoice {
            ListChoice.make(marketplace, canAct: canAct, check: preflight.marketplaces[marketplace],
                            draft: draft, live: live, soldSomewhere: sold)
        }
        let ready = make("ebay")
        let needs = make("depop")
        let listed = make("mercari", live: MarketplaceLiveListing(listingID: "m1", soldHere: false, state: "live"))
        let sold = make("poshmark", live: MarketplaceLiveListing(listingID: "p1", soldHere: true, state: "live"))
        let unchecked = make("etsy")
        precondition(ready == ListChoice(marketplace: "ebay", label: "Ready", tone: .ready, selectable: true))
        precondition(needs.label == "Needs 2 fields" && needs.tone == .needs && needs.selectable)
        precondition(needs.detail == "Item price, Condition")
        precondition(listed.label == "Listed" && !listed.selectable)
        precondition(sold.label == "Sold" && !sold.selectable)
        precondition(unchecked.label == "Not checked" && unchecked.selectable)
        precondition(!make("ebay", canAct: false).selectable)
        precondition(!make("ebay", sold: true).selectable)
        precondition(make("ebay", draft: NativeDraftState(state: "draft", running: true)).label == "Working…")
        precondition(make("ebay", live: MarketplaceLiveListing(listingID: "e1", soldHere: false, state: "deleted")).label == "Ready")
        // Delisted with the record still reading published: List everywhere can pick it again.
        let relist = make("ebay", draft: NativeDraftState(state: "published", running: false),
                          live: MarketplaceLiveListing(listingID: "e1", soldHere: false, state: "deleted"))
        precondition(relist.label == "Ready" && relist.selectable)

        // Nothing is picked for the seller except by Select all ready, and only ready ones.
        precondition(ListChoice.ready([ready, needs, listed, sold, unchecked]) == ["ebay"])
        print("Marketplace readiness tests passed")
    }
}
