import Foundation

/// A starred listing decodes from the inventory, and the Favorites filter keeps only those, as on the web.
@main
struct InventoryFavoritesTests {
    static func main() throws {
        let rows = try JSONDecoder().decode([ConversationSummary].self, from: Data(#"""
        [
          {"id":"a","title":"Levi's jeans","status":"draft","created_at":"2026-10-01T10:00:00","updated_at":"2026-10-30T10:00:00","favorite":true},
          {"id":"b","title":"Wool coat","status":"draft","created_at":"2026-10-02T10:00:00","updated_at":"2026-10-29T10:00:00","favorite":false},
          {"id":"c","title":"Coach bag","status":"draft","created_at":"2026-10-03T10:00:00","updated_at":"2026-10-28T10:00:00"}
        ]
        """#.utf8))
        // An older server sends no star: the item reads unstarred.
        precondition(rows.map(\.favorite) == [true, false, false])

        var filters = InventoryFilters()
        filters.status = .draft
        precondition(filters.activeCount == 0)
        precondition(InventoryQuery.rows(rows, search: "", filters: filters).map(\.id) == ["a", "b", "c"])
        filters.favorites = true
        precondition(filters.activeCount == 1)
        precondition(InventoryQuery.rows(rows, search: "", filters: filters).map(\.id) == ["a"])

        let body = try JSONEncoder().encode(FavoriteInput(favorite: true))
        precondition(String(decoding: body, as: UTF8.self) == #"{"favorite":true}"#)
        let result = try JSONDecoder().decode(FavoriteResult.self, from: Data(#"{"id":"a","favorite":false}"#.utf8))
        precondition(result.id == "a" && !result.favorite)
        print("Inventory favorites OK")
    }
}
