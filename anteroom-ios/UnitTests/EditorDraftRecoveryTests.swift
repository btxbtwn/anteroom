import XCTest
@testable import Anteroom

@MainActor
final class EditorDraftRecoveryTests: XCTestCase {
    func testOldUnversionedEditsSurviveUntilExplicitRecoveryOrDiscard() async throws {
        let id = "recovery-\(UUID().uuidString)"
        var kept = ListingEditorModel.Form()
        kept.fields.title = "Unsaved seller title"
        kept.sellerInputs.sku = "Unsaved SKU"
        let draft = EditorDraft(listingID: id, revisionID: nil, baselineInputs: SellerInputs(),
                                form: kept, pending: [:], savedAt: .now)
        EditorDraftStore.save(draft)
        defer {
            EditorDraftStore.remove(id)
            EditorDraftStore.remove(id, recovery: true)
        }
        StubProtocol.answer([{ request in
            if request.url?.path.hasSuffix("/screen") == true {
                return .init(status: 200, body: """
                {"conversation":{"id":"\(id)","title":"Current title","status":"draft","created_at":"","updated_at":"","notes":"{}"},
                 "listing":{"conversation_id":"\(id)","listing":{"title":"Current title"},"current_revision_id":null,
                            "revision_count":0,"can_send":false},
                 "photos":[],"messages":[],"jobs":[],"activity":{}}
                """)
            }
            return .init(status: 500, body: #"{"detail":"Not needed for this test"}"#)
        }])
        let model = ListingEditorModel(api: StubSignIn().api(), listing: .init(id: id, title: "Current title"))
        await model.load()
        XCTAssertEqual(model.form.fields.title, "Current title", "No automatic application of the conflicting copy")
        XCTAssertNotNil(model.recoveryDraft)
        XCTAssertEqual(EditorDraftStore.load(id), draft, "Nothing deleted before the seller's decision")
        model.draftNotice = nil // Later
        model.stashEdits() // No-change/background stash
        model.forgetStash() // A return to the screen must not drop the kept copy.
        model.form.fields.brand = "Current unsaved brand"
        model.stashEdits()
        XCTAssertEqual(EditorDraftStore.load(id, recovery: true), draft, "Unrelated edits cannot overwrite unresolved recovery")
        XCTAssertEqual(EditorDraftStore.load(id)?.form.fields.brand, "Current unsaved brand",
                       "Current unsaved work is kept separately")
        let reopened = ListingEditorModel(api: StubSignIn().api(), listing: .init(id: id, title: "Current title"))
        await reopened.load()
        XCTAssertEqual(reopened.form.fields.brand, "Current unsaved brand")
        XCTAssertEqual(reopened.recoveryDraft, draft, "Both copies remain available after reopening")
        model.recoverKeptDraft()
        XCTAssertEqual(model.form.fields.title, "Unsaved seller title")
        XCTAssertEqual(model.form.sellerInputs.sku, "Unsaved SKU")
        XCTAssertNil(model.recoveryDraft)
        XCTAssertNotNil(EditorDraftStore.load(id), "Recovery keeps a disk copy until Save or Discard")
        XCTAssertEqual(EditorDraftStore.load(id)?.form.fields.title, "Unsaved seller title")
        XCTAssertFalse(StubProtocol.requests.contains { $0.httpMethod == "PUT" || $0.httpMethod == "PATCH" })
        model.discardChanges()
        XCTAssertNil(EditorDraftStore.load(id), "Explicit Discard can remove the recovered copy")
        XCTAssertNil(EditorDraftStore.load(id, recovery: true))
    }
}
