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
            for copy in EditorDraftStore.recoveryCopies(id) ?? [] { EditorDraftStore.removeRecovery(copy) }
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
        model.form.fields.brand = "Current unsaved brand"
        model.stashEdits()
        XCTAssertEqual(EditorDraftStore.recoveryCopies(id), [draft], "Unrelated edits cannot overwrite unresolved recovery")
        XCTAssertEqual(EditorDraftStore.load(id)?.form.fields.brand, "Current unsaved brand",
                       "Current unsaved work is kept separately")
        let reopened = ListingEditorModel(api: StubSignIn().api(), listing: .init(id: id, title: "Current title"))
        await reopened.load()
        XCTAssertEqual(reopened.form.fields.brand, "Current unsaved brand")
        XCTAssertEqual(reopened.recoveryDraft, draft, "Both copies remain available after reopening")
        XCTAssertFalse(reopened.draftNotice?.contains("are back") == true,
                       "Recovery buttons must describe the kept copy, not only the restored current work")
        model.recoverKeptDraft()
        XCTAssertEqual(model.form.fields.title, "Unsaved seller title")
        XCTAssertEqual(model.form.sellerInputs.sku, "Unsaved SKU")
        XCTAssertNil(model.recoveryDraft)
        XCTAssertNotNil(EditorDraftStore.load(id), "Recovery keeps a disk copy until Save or Discard")
        XCTAssertEqual(EditorDraftStore.load(id)?.form.fields.title, "Unsaved seller title")
        XCTAssertFalse(StubProtocol.requests.contains { $0.httpMethod == "PUT" || $0.httpMethod == "PATCH" })
        let afterRecovery = ListingEditorModel(api: StubSignIn().api(), listing: .init(id: id, title: "Current title"))
        await afterRecovery.load()
        XCTAssertEqual(afterRecovery.form.fields.title, "Unsaved seller title")
        XCTAssertNil(afterRecovery.recoveryDraft, "The explicitly chosen copy does not reappear as an unresolved copy")
        afterRecovery.discardChanges()
        XCTAssertNil(EditorDraftStore.load(id), "Explicit Discard can remove the recovered copy")
        XCTAssertEqual(EditorDraftStore.recoveryCopies(id), [])
    }

    func testTwoConflictingCopiesSurviveReopenStashAndIndividualDiscard() async throws {
        let id = "multiple-recovery-\(UUID().uuidString)"
        defer {
            EditorDraftStore.remove(id)
            for copy in EditorDraftStore.recoveryCopies(id) ?? [] { EditorDraftStore.removeRecovery(copy) }
        }
        var kept = ListingEditorModel.Form()
        kept.fields.title = "Legacy unsaved title"
        let older = EditorDraft(listingID: id, revisionID: nil, baselineInputs: SellerInputs(),
                                form: kept, pending: [:], savedAt: .now)
        EditorDraftStore.save(older)
        stubScreen(id, title: "Current title")
        let first = ListingEditorModel(api: StubSignIn().api(), listing: .init(id: id, title: "Current title"))
        await first.load()
        first.form.fields.brand = "Newer unsaved brand"
        first.stashEdits()
        let newer = try XCTUnwrap(EditorDraftStore.load(id))
        stubScreen(id, title: "Changed elsewhere")
        let reopened = ListingEditorModel(api: StubSignIn().api(), listing: .init(id: id, title: "Changed elsewhere"))
        await reopened.load()
        XCTAssertEqual(reopened.form.fields.title, "Changed elsewhere", "Neither conflicting copy applies automatically")
        XCTAssertEqual(reopened.recoveryDrafts, [older, newer])
        reopened.form.cost = "5"
        reopened.stashEdits()
        XCTAssertEqual(EditorDraftStore.recoveryCopies(id), [older, newer])
        let afterStash = ListingEditorModel(api: StubSignIn().api(), listing: .init(id: id, title: "Changed elsewhere"))
        await afterStash.load()
        XCTAssertEqual(afterStash.recoveryDrafts, [older, newer], "Both copies remain shown after another reopening")
        reopened.nextKeptDraft()
        XCTAssertEqual(reopened.recoveryDraft, newer, "The seller can inspect either copy without losing one")
        reopened.nextKeptDraft()
        reopened.discardKeptDraft()
        XCTAssertEqual(EditorDraftStore.recoveryCopies(id), [newer], "Discard removes only the selected older copy")
        XCTAssertEqual(reopened.recoveryDraft, newer)
        reopened.recoverKeptDraft()
        XCTAssertEqual(reopened.form.fields.brand, "Newer unsaved brand")
        XCTAssertEqual(EditorDraftStore.recoveryCopies(id), [newer], "Chosen edits remain recoverable until Save/Discard")
        XCTAssertFalse(StubProtocol.requests.contains { $0.httpMethod == "PUT" || $0.httpMethod == "PATCH" })
        reopened.discardChanges()
        XCTAssertEqual(EditorDraftStore.recoveryCopies(id), [])
    }

    func testSuccessfulSaveRemovesOnlyCurrentWorkStash() async throws {
        let id = "saved-with-recovery-\(UUID().uuidString)"
        defer { clean(id) }
        let older = legacyDraft(id)
        EditorDraftStore.save(older)
        stubScreen(id, title: "Current title", savedTitle: "Saved current title")
        let model = ListingEditorModel(api: StubSignIn().api(), listing: .init(id: id, title: "Current title"))
        await model.load()
        model.form.fields.title = "Saved current title"
        model.stashEdits()
        let success = await model.save()
        XCTAssertTrue(success)
        XCTAssertNil(EditorDraftStore.load(id), "Saved current work must not return as a false kept copy")
        XCTAssertEqual(EditorDraftStore.recoveryCopies(id), [older])
        stubScreen(id, title: "Saved current title")
        let reopened = ListingEditorModel(api: StubSignIn().api(), listing: .init(id: id, title: "Saved current title"))
        await reopened.load()
        XCTAssertEqual(reopened.form.fields.title, "Saved current title")
        XCTAssertEqual(reopened.recoveryDrafts, [older])
    }

    func testUndoingCurrentEditsRemovesNormalStashButKeepsRecovery() async throws {
        let id = "undone-with-recovery-\(UUID().uuidString)"
        defer { clean(id) }
        let older = legacyDraft(id)
        EditorDraftStore.save(older)
        stubScreen(id, title: "Current title")
        let model = ListingEditorModel(api: StubSignIn().api(), listing: .init(id: id, title: "Current title"))
        await model.load()
        model.stashEdits()
        XCTAssertEqual(EditorDraftStore.load(id), older, "No-change cleanup must preserve a kept normal file")
        model.form.fields.brand = "Undone brand"
        model.stashEdits()
        model.form.fields.brand = ""
        model.stashEdits()
        XCTAssertNil(EditorDraftStore.load(id))
        XCTAssertEqual(EditorDraftStore.recoveryCopies(id), [older])
        let reopened = ListingEditorModel(api: StubSignIn().api(), listing: .init(id: id, title: "Current title"))
        await reopened.load()
        XCTAssertEqual(reopened.form.fields.brand, "")
        XCTAssertEqual(reopened.recoveryDraft, older)
    }

    func testRecoveryIsBlockedDuringLoadAndReloadDoesNotConsumeChosenCopy() async throws {
        let id = "loading-with-recovery-\(UUID().uuidString)"
        defer { clean(id) }
        let older = legacyDraft(id)
        EditorDraftStore.save(older)
        stubScreen(id, title: "Current title")
        let model = ListingEditorModel(api: StubSignIn().api(), listing: .init(id: id, title: "Current title"))
        await model.load()
        let entered = expectation(description: "Screen read entered")
        let release = DispatchSemaphore(value: 0)
        stubScreen(id, title: "Reloaded title", beforeScreen: {
            entered.fulfill()
            _ = release.wait(timeout: .now() + 5)
        })
        let loading = Task { await model.load() }
        await fulfillment(of: [entered], timeout: 2)
        XCTAssertFalse(model.canRecoverKeptDraft)
        model.recoverKeptDraft()
        XCTAssertEqual(model.form.fields.title, "Current title")
        XCTAssertEqual(EditorDraftStore.recoveryCopies(id), [older])
        release.signal()
        await loading.value
        model.recoverKeptDraft()
        XCTAssertEqual(model.form.fields.title, "Legacy unsaved title")
        stubScreen(id, title: "Latest server title")
        await model.load()
        XCTAssertEqual(model.recoveryDraft, older, "Reload returns the unsaved chosen copy to recovery")
        let success = await model.save()
        XCTAssertTrue(success)
        XCTAssertEqual(EditorDraftStore.recoveryCopies(id), [older],
                       "A save of reloaded server values must not consume the earlier chosen copy")
    }

    private func legacyDraft(_ id: String) -> EditorDraft {
        var form = ListingEditorModel.Form()
        form.fields.title = "Legacy unsaved title"
        return EditorDraft(listingID: id, revisionID: nil, baselineInputs: SellerInputs(),
                           form: form, pending: [:], savedAt: .now)
    }

    private func clean(_ id: String) {
        EditorDraftStore.remove(id)
        for copy in EditorDraftStore.recoveryCopies(id) ?? [] { EditorDraftStore.removeRecovery(copy) }
    }

    private func stubScreen(_ id: String, title: String, savedTitle: String? = nil, beforeScreen: (() -> Void)? = nil) {
        StubProtocol.answer([{ request in
            if request.httpMethod == "PUT", let savedTitle {
                return .init(status: 200, body: """
                {"ok":true,"revision_id":"r-saved","listing":{"title":"\(savedTitle)"},"etsy_eligible":false,
                 "validation":{"valid":true,"errors":[],"warnings":[],"info":[],"can_send":false}}
                """)
            }
            if request.url?.path.hasSuffix("/screen") == true {
                beforeScreen?()
                return .init(status: 200, body: """
                {"conversation":{"id":"\(id)","title":"\(title)","status":"draft","created_at":"","updated_at":"","notes":"{}"},
                 "listing":{"conversation_id":"\(id)","listing":{"title":"\(title)"},"current_revision_id":null,
                            "revision_count":0,"can_send":false},
                 "photos":[],"messages":[],"jobs":[],"activity":{}}
                """)
            }
            return .init(status: 500, body: #"{"detail":"Not needed for this test"}"#)
        }])
    }
}
