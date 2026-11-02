import Foundation

/// The eBay tab follows the web editor's rules (nativeFormStability.ts,
/// EbayVariationsEditor.tsx, categoryTree.ts).
@main
struct EbayFormTests {
    static func main() throws {
        let form = try JSONDecoder().decode(NativeForm.self, from: Data(#"""
        {"marketplace":"ebay","category_id":"15687","fields":[
         {"key":"ebay_specifics.subtitle","label":"Subtitle","section":"Title","reveal":"See title options"},
         {"key":"weight_lb","label":"Package weight (lbs.)","section":"Shipping","width":"quarter","locked":true},
         {"key":"ebay_specifics.lotSize","label":"Quantity in lot","input_type":"number","min_value":2,"step":1},
         {"key":"ebay_specifics.listingFormat","label":"Format","selection_only":true,"options":["Buy It Now","Auction"],
          "option_values":{"Buy It Now":"FIXED_PRICE","Auction":"AUCTION"},"value":"FIXED_PRICE"},
         {"key":"ebay_specifics.listingDuration","label":"Duration","options":["3 days","7 days","Good 'Til Cancelled"],
          "option_values":{"3 days":"DAYS_3","7 days":"DAYS_7","Good 'Til Cancelled":"GTC"},"value":"GTC"},
         {"key":"quantity","label":"Quantity","input_type":"number","value":1},
         {"key":"ebay_specifics.variations","label":"Variations","input_type":"variations","category_specific":true,
          "variation_aspects":[{"name":"Size","selection_only":true,"options":["S","M"]},{"name":"Color","options":[]}]}]}
        """#.utf8))
        let fields = Dictionary(uniqueKeysWithValues: form.fields.map { ($0.key, $0) })
        precondition(fields["ebay_specifics.subtitle"]?.reveal == "See title options")
        precondition(fields["weight_lb"]?.quarter == true && fields["weight_lb"]?.half == false)
        precondition(fields["ebay_specifics.lotSize"]?.minValue == 2 && fields["ebay_specifics.lotSize"]?.step == 1)
        precondition(fields["ebay_specifics.variations"]?.variationAspects == [
            VariationAspect(name: "Size", selectionOnly: true, options: ["S", "M"]), VariationAspect(name: "Color")])

        // Rows for the format, offers and schedule.
        var values = ["ebay_specifics.listingFormat": "FIXED_PRICE", "ebay_specifics.bestOffer.enabled": "true"]
        let shown: (String) -> String = { values[$0] ?? "" }
        precondition(!EbayFormRules.visible("ebay_specifics.auctionStartPrice", shown: shown, varied: false))
        precondition(!EbayFormRules.visible("ebay_specifics.listingDuration", shown: shown, varied: false))
        precondition(EbayFormRules.visible("ebay_specifics.bestOffer.minimumOfferPrice", shown: shown, varied: false))
        precondition(!EbayFormRules.visible("ebay_specifics.bestOffer.enabled", shown: shown, varied: true))
        precondition(!EbayFormRules.visible("ebay_specifics.schedule.startAt", shown: shown, varied: false))
        precondition(EbayFormRules.visible("ebay_specifics.variations", shown: shown, varied: false))
        values["ebay_specifics.bestOffer.enabled"] = "false"
        values["ebay_specifics.schedule.enabled"] = "true"
        let shownAgain: (String) -> String = { values[$0] ?? "" }
        precondition(!EbayFormRules.visible("ebay_specifics.bestOffer.autoAcceptPrice", shown: shownAgain, varied: false))
        precondition(EbayFormRules.visible("ebay_specifics.schedule.startAt", shown: shownAgain, varied: false))
        values["ebay_specifics.listingFormat"] = "AUCTION"
        let auction: (String) -> String = { values[$0] ?? "" }
        precondition(EbayFormRules.visible("ebay_specifics.auctionBuyItNow", shown: auction, varied: false))
        precondition(!EbayFormRules.visible("ebay_specifics.price", shown: auction, varied: false))
        precondition(!EbayFormRules.visible("ebay_specifics.bestOffer.enabled", shown: auction, varied: false))
        precondition(!EbayFormRules.visible("ebay_specifics.variations", shown: auction, varied: false))
        precondition(EbayFormRules.visible("ebay_specifics.variations", shown: auction, varied: true))

        // Buy It Now runs until cancelled; an auction picks a duration, 7 days by default.
        let duration = try required(fields["ebay_specifics.listingDuration"])
        let fixed = EbayFormRules.present(duration, format: "FIXED_PRICE", varied: false)
        precondition(fixed.locked && fixed.options == [EbayFormRules.gtcLabel] && fixed.value == .string("GTC"))
        let timed = EbayFormRules.present(duration, format: "AUCTION", varied: false)
        precondition(!timed.locked && timed.options == ["3 days", "7 days"] && timed.value == .string("DAYS_7"))
        // Variations take auction off the format and lock the quantity.
        let format = EbayFormRules.present(try required(fields["ebay_specifics.listingFormat"]), format: "FIXED_PRICE", varied: true)
        precondition(format.options == ["Buy It Now"] && format.optionValues == ["Buy It Now": "FIXED_PRICE"] && format.help != nil)
        let quantity = try required(fields["quantity"])
        precondition(EbayFormRules.present(quantity, format: "FIXED_PRICE", varied: true).locked)
        precondition(!EbayFormRules.present(quantity, format: "FIXED_PRICE", varied: false).locked)
        precondition(EbayFormRules.notes(varied: true).count == 1 && EbayFormRules.notes(varied: false).isEmpty)

        // Variations round-trip, keep unknown keys, and follow aspect changes.
        let stored: JSONValue = .object([
            "aspects": .array([.string("Size"), .string("Color")]),
            "rows": .array([
                .object(["values": .object(["Size": .string("S"), "Color": .string("Red")]), "quantity": .int(2),
                         "price": .string(""), "sku": .string("A-S"), "offerId": .string("o1")]),
                .object(["values": .object(["Size": .string("M"), "Color": .string("Red")]), "quantity": .string("3"),
                         "price": .double(19.5), "sku": .string("")]),
                .object(["values": .object(["Size": .string("L"), "Color": .string("Blue")]), "quantity": .string("x")]),
            ]),
            "photos": .object(["Red": .array([.string("p1")])]),
        ])
        let variations = EbayVariations(stored)
        precondition(variations.aspects == ["Size", "Color"] && variations.rows.count == 3 && variations.total == 5)
        precondition(variations.rows[0].other["offerId"] == .string("o1"))
        precondition(EbayVariations(variations.json) == variations)
        precondition(variations.photoAspect == "Size" && variations.photoValues == ["S", "M", "L"])
        let byColor = variations.withAspects(["Color"])
        precondition(byColor.rows[0].values == ["Color": "Red"] && byColor.photos.isEmpty && byColor.photoValues == ["Red", "Blue"])
        var chosen = variations
        chosen.photosBy = "Color"
        let kept = chosen.withAspects(["Color", "Size"])
        precondition(kept.photosBy == "Color" && kept.photos == ["Red": ["p1"]])
        precondition(variations.withAspects(["A", "B", "C", "D", "E", "F"]).aspects.count == EbayVariations.maxAspects)
        precondition(EbayVariations(nil).rows.isEmpty && EbayVariations(.string("bad")).aspects.isEmpty)

        // Reveal links: "See …" above the fields, the rest below; a saved value opens its link.
        let links = NativeReveal.links(try JSONDecoder().decode([NativeField].self, from: Data(#"""
        [{"key":"a","reveal":"See pricing options"},{"key":"b","reveal":"Show more"},{"key":"c","reveal":"See pricing options"},{"key":"d"}]
        """#.utf8)))
        precondition(links.top == ["See pricing options"] && links.bottom == ["Show more"])
        precondition(!NativeReveal.opens(nil) && !NativeReveal.opens(.bool(false)) && !NativeReveal.opens(.string("false")))
        precondition(!NativeReveal.opens(.string("")) && NativeReveal.opens(.string("Sub")) && NativeReveal.opens(.int(0)))
        precondition(NativeReveal.title("Show more", open: true) == "Show less" && NativeReveal.title("See title options", open: true) == "See title options")

        // Categories: leaves from the synced tree, one level at a time, and search.
        let tree = try JSONDecoder().decode(CatalogCategories.self, from: Data(#"""
        {"coverage":"tree","complete":true,"nodes":[
         {"id":"11450","parent_id":"__root","path":"Clothing","label":"Clothing","is_leaf":false,"has_children":true},
         {"id":"1059","parent_id":"11450","path":"Clothing > Men","label":"Men","is_leaf":false,"has_children":true},
         {"id":"15687","parent_id":"1059","path":"Clothing > Men > T-Shirts","label":"T-Shirts","is_leaf":true,"has_children":false},
         {"id":"57990","parent_id":"1059","path":"Clothing > Men > Casual Shirts","label":"Casual Shirts","is_leaf":true,"has_children":false},
         {"id":"99","parent_id":"11450","path":"Clothing > Hats","label":"Hats","is_leaf":true,"has_children":false}]}
        """#.utf8))
        precondition(tree.leaves.map(\.id) == ["15687", "57990", "99"] && !tree.needsSearch)
        let top = CategoryBrowse.children(of: ["Clothing"], in: tree.leaves)
        precondition(top.map(\.label) == ["Men", "Hats"] && top[0].leaf == nil && top[1].leaf?.id == "99")
        precondition(CategoryBrowse.children(of: ["Clothing", "Men"], in: tree.leaves).map { $0.leaf?.id } == ["15687", "57990"])
        precondition(CategoryBrowse.search("men shirts", in: tree.leaves).map(\.id) == ["15687", "57990"])
        precondition(CategoryBrowse.search("  ", in: tree.leaves).isEmpty)
        precondition(CategoryBrowse.breadcrumb("Clothing > Men > T-Shirts") == "Clothing › Men › T-Shirts")
        let observed = try JSONDecoder().decode(CatalogCategories.self, from: Data(#"{"coverage":"observed","complete":false,"nodes":[]}"#.utf8))
        precondition(observed.leaves.isEmpty && observed.needsSearch)
        print("eBay form tests passed")
    }

    private static func required<T>(_ value: T?) throws -> T {
        guard let value else { throw CocoaError(.coderValueNotFound) }
        return value
    }
}
