import Foundation

@main
struct HistoryModelsTests {
    static func main() throws {
        let decoder = JSONDecoder()
        let pointer = try decoder.decode(ListingRevisionPointer.self, from: Data(#"{"current_revision_id":null}"#.utf8))
        precondition(pointer.currentRevisionID == nil)
        let snapshot = try decoder.decode(RevisionSnapshot.self, from: Data(#"{"id":"version-1","listing":{"title":"Blue shirt","price":12.5,"quantity":2,"labels":["Ready","Spring"],"empty":null,"enabled":true,"ebay":{"category_id":"123"}}}"#.utf8))
        precondition(snapshot.id == "version-1")
        precondition(snapshot.listing["title"]?.displayText == "Blue shirt")
        precondition(snapshot.listing["price"]?.displayText?.isEmpty == false)
        if case .number(let quantity) = snapshot.listing["quantity"] { precondition(quantity == 2) }
        else { preconditionFailure("Quantity must remain a number") }
        precondition(snapshot.listing["labels"]?.displayText == "Ready, Spring")
        precondition(snapshot.listing["empty"]?.displayText == nil)
        precondition(snapshot.listing["enabled"]?.displayText == "Yes")
        precondition(snapshot.listing["ebay"]?.displayText == nil)
        let history = try decoder.decode([ListingRevision].self, from: Data(#"[{"id":"version-1","source":"user_form","title":"Blue shirt","created_at":"2026-10-31T07:00:00","parent_revision_id":null}]"#.utf8))
        precondition(history.count == 1 && history[0].source == "user_form")
        print("History model tests passed")
    }
}
