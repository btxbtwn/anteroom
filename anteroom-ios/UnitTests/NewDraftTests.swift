import XCTest
@testable import Anteroom

/// Backing out of a new draft without adding anything leaves no listing behind;
/// a draft with anything in it is never deleted.
@MainActor
final class NewDraftTests: XCTestCase {
    func testBlankDraftUsesOneAtomicServerRequest() async {
        StubProtocol.answer([{ request in
            let path = request.url?.path ?? ""
            if path.hasSuffix("/discard-blank") { return .init(status: 200, body: #"{"discarded":true}"#) }
            if path.hasSuffix("/photos") || path.hasSuffix("/messages") { return .init(status: 200, body: "[]") }
            if path.hasSuffix("/listing") { return .init(status: 200, body: #"{"listing":{},"current_revision_id":null}"#) }
            return .init(status: 200, body: #"{"id":"n","title":"New Listing","status":"draft","notes":null,"created_at":"","updated_at":""}"#)
        }])
        let model = InventoryModel(api: StubSignIn().api())
        await model.discardIfUntouched("n")
        XCTAssertEqual(StubProtocol.requests.count, 1)
        XCTAssertEqual(StubProtocol.requests.first?.httpMethod, "POST")
        XCTAssertEqual(StubProtocol.requests.first?.url?.path, "/api/conversations/n/discard-blank")
        XCTAssertFalse(StubProtocol.requests.contains { $0.httpMethod == "DELETE" })
    }

    func testServerKeepsDraftChangedByAnotherClient() async {
        StubProtocol.answer(.init(status: 200, body: #"{"discarded":false}"#))
        let model = InventoryModel(api: StubSignIn().api())
        await model.discardIfUntouched("n")
        XCTAssertEqual(StubProtocol.requests.count, 1)
        XCTAssertEqual(StubProtocol.requests.first?.httpMethod, "POST")
        XCTAssertFalse(StubProtocol.requests.contains { $0.httpMethod == "DELETE" })
    }

    func testUnreadableDraftIsKept() async {
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
