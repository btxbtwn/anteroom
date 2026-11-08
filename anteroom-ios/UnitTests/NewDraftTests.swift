import XCTest
@testable import Anteroom

/// Backing out of a new draft without adding anything leaves no listing behind;
/// a draft with anything in it is never deleted.
@MainActor
final class NewDraftTests: XCTestCase {
    private func answer(photos: String) {
        StubProtocol.answer([{ request in
            let path = request.url?.path ?? ""
            if request.httpMethod == "DELETE" { return .init(status: 200, body: "{}") }
            if path.hasSuffix("/photos") { return .init(status: 200, body: photos) }
            if path.hasSuffix("/messages") { return .init(status: 200, body: "[]") }
            if path.hasSuffix("/listing") { return .init(status: 200, body: #"{"listing":{},"current_revision_id":null}"#) }
            return .init(status: 200, body: #"{"id":"n","title":"New Listing","status":"draft","notes":null}"#)
        }])
    }

    func testUntouchedNewDraftIsDeleted() async throws {
        answer(photos: "[]")
        let model = InventoryModel(api: StubSignIn().api())
        await model.discardIfUntouched("n")
        let delete = StubProtocol.requests.first { $0.httpMethod == "DELETE" }
        XCTAssertEqual(delete?.url?.path, "/api/conversations/n")
    }

    func testDraftWithAPhotoIsKept() async throws {
        answer(photos: #"[{"id":"p1"}]"#)
        let model = InventoryModel(api: StubSignIn().api())
        await model.discardIfUntouched("n")
        XCTAssertFalse(StubProtocol.requests.contains { $0.httpMethod == "DELETE" })
    }

    func testUnreadableDraftIsKept() async throws {
        StubProtocol.answer(.init(status: 400, body: #"{"detail":"nope"}"#))
        let model = InventoryModel(api: StubSignIn().api())
        await model.discardIfUntouched("n")
        XCTAssertFalse(StubProtocol.requests.contains { $0.httpMethod == "DELETE" })
    }

    /// A draft never saved has only its name as a title, so Generate listing asks nothing.
    func testUnsavedDraftNameIsNotCopy() {
        XCTAssertFalse(ListingEditorModel.hasCopy(title: "Sandbox photos only", description: "", revisionID: nil))
        XCTAssertFalse(ListingEditorModel.hasCopy(title: "New Listing", description: "", revisionID: "r1"))
        XCTAssertTrue(ListingEditorModel.hasCopy(title: "Blue tee", description: "", revisionID: "r1"))
        XCTAssertTrue(ListingEditorModel.hasCopy(title: "", description: "Soft cotton", revisionID: "r1"))
    }
}
