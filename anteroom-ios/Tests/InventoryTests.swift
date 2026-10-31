import Foundation

/// Inventory tabs/filters/sorts match the desktop rules, and an edit keeps
/// every listing value the phone does not show.
@main
struct InventoryTests {
    static func main() throws {
        let rows = try JSONDecoder().decode([ListingSummary].self, from: Data(#"""
        [
          {"id":"a","title":"Blue shirt","status":"draft","created_at":"2026-10-01T10:00:00","updated_at":"2026-10-30T10:00:00.123456","sku":"B-2","price":12.5,"quantity":1,"direct_marketplaces":[],"cover_photo_url":"/api/photos/p1/thumb?size=192&v=abc"},
          {"id":"b","title":"Red bag","status":"draft","created_at":"2026-10-02T10:00:00","updated_at":"2026-10-29T10:00:00","sku":null,"price":40,"direct_marketplaces":["poshmark","ebay"]},
          {"id":"c","title":"Old coat","status":"sold","settled_at":"2026-10-20T10:00:00","created_at":"2026-09-01T10:00:00","updated_at":"2026-10-20T10:00:00","sku":"A-1","price":null,"direct_marketplaces":[]},
          {"id":"d","title":"","status":"failed","created_at":"2026-10-03T10:00:00","updated_at":"2026-10-31T10:00:00","sku":"b-10","price":5,"direct_marketplaces":[]},
          {"id":"e","title":"Green hat","status":"in_progress","created_at":"2026-10-04T10:00:00","updated_at":"2026-10-28T10:00:00","direct_marketplaces":[]}
        ]
        """#.utf8))

        // Status words: a draft live on a marketplace reads Active; in progress files under Draft.
        precondition(rows[1].inventoryStatus == "active")
        precondition(rows[4].statusTab == "draft" && rows[4].statusLabel == "In Progress")
        precondition(rows[0].coverPhotoID == "p1" && rows[1].coverPhotoID == nil)
        precondition(rows[3].displayTitle == "Untitled")

        // Settled listings stay out of All, appear in Sold and in search.
        var filters = InventoryFilters()
        precondition(ids(InventoryQuery.rows(rows, search: "", filters: filters)) == ["d", "a", "b", "e"])
        let counts = InventoryQuery.counts(rows, search: "")
        precondition(counts[.all] == 4 && counts[.draft] == 2 && counts[.active] == 1 && counts[.sold] == 1 && counts[.failed] == 1)
        filters.status = .sold
        precondition(ids(InventoryQuery.rows(rows, search: "", filters: filters)) == ["c"])
        filters.status = .all
        precondition(ids(InventoryQuery.rows(rows, search: "coat", filters: filters)) == ["c"])
        precondition(ids(InventoryQuery.rows(rows, search: "b-1", filters: filters)) == ["d"])

        // Marketplace filter, and Not listed with and without picks.
        filters.marketplaces = ["ebay"]
        precondition(ids(InventoryQuery.rows(rows, search: "", filters: filters)) == ["b"])
        filters.notListed = true
        precondition(ids(InventoryQuery.rows(rows, search: "", filters: filters)) == ["d", "a", "e"])
        filters.marketplaces = []
        precondition(ids(InventoryQuery.rows(rows, search: "", filters: filters)) == ["d", "a", "e"])
        precondition(InventoryQuery.marketplaceOptions(rows, preferred: ["poshmark", "etsy"], includeUnused: false) == ["poshmark", "ebay"])
        precondition(InventoryQuery.marketplaceOptions(rows, preferred: ["poshmark", "etsy"], includeUnused: true) == ["poshmark", "etsy", "ebay"])
        precondition(InventoryQuery.marketplaceCounts(rows, options: ["etsy"], notListed: true)["etsy"] == 5)

        // Sorts: empty values last both ways; SKU compares numerically and ignores case.
        filters = InventoryFilters(sort: .priceAsc)
        precondition(ids(InventoryQuery.rows(rows, search: "", filters: filters)) == ["d", "a", "b", "e"])
        filters.sort = .priceDesc
        precondition(ids(InventoryQuery.rows(rows, search: "", filters: filters)) == ["b", "a", "d", "e"])
        filters.sort = .skuAsc
        precondition(ids(InventoryQuery.rows(rows, search: "", filters: filters)) == ["a", "d", "b", "e"])
        filters.sort = .titleAsc
        precondition(ids(InventoryQuery.rows(rows, search: "", filters: filters)) == ["a", "e", "b", "d"])
        filters.sort = .createdAsc
        precondition(ids(InventoryQuery.rows(rows, search: "", filters: filters)) == ["a", "b", "d", "e"])
        precondition(InventoryQuery.compactCount(999) == "999" && InventoryQuery.compactCount(1121) == "1.1k"
                     && InventoryQuery.compactCount(2000) == "2k" && InventoryQuery.compactCount(150_000) == "150k")

        // Editing General fields writes only what changed; marketplace data is untouched.
        let document = try JSONDecoder().decode([String: JSONValue].self, from: Data(#"""
        {"title":"Blue shirt","price":12.5,"quantity":2,"labels":["Ready"],"size":38,
         "ebay_specifics":{"Sleeve Length":"Long","Count":[1,2.5,true,null]},"tags":["linen"]}
        """#.utf8))
        let original = GeneralFields(listing: document)
        precondition(original.price == 12.5 && original.quantity == 2 && original.size == "38" && original.labels == ["Ready"])
        var edited = original
        edited.title = "Blue linen shirt"
        edited.price = nil
        let saved = edited.applied(to: document, changedFrom: original)
        precondition(saved["title"] == .string("Blue linen shirt"))
        precondition(saved["price"] == .null)
        precondition(saved["size"] == .int(38), "An unedited number keeps its type")
        precondition(saved["quantity"] == .int(2) && saved["tags"] == document["tags"])
        precondition(saved["ebay_specifics"] == document["ebay_specifics"])
        precondition(saved["description"] == nil, "An unedited absent field stays absent")
        let encoded = try JSONEncoder().encode(saved)
        let reread = try JSONDecoder().decode([String: JSONValue].self, from: encoded)
        precondition(reread == saved)
        print("Inventory tests passed")
    }

    private static func ids(_ rows: [ListingSummary]) -> [String] { rows.map(\.id) }
}
