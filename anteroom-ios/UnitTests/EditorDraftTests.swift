import XCTest
@testable import Anteroom

final class EditorDraftTests: XCTestCase {
    func testDraftRoundTripsAndRestoresOnlyOnTheSameVersion() throws {
        var form = ListingEditorModel.Form()
        form.fields.title = "Blue linen shirt"
        form.price = "12."
        form.sellerInputs = SellerInputs(notes: #"{"sku":"B-2","other":"kept"}"#)
        form.sellerInputs.knownFlaws = "Small mark on cuff"
        let baseline = SellerInputs(notes: #"{"sku":"B-2","other":"kept"}"#)
        let draft = EditorDraft(listingID: "test-\(UUID().uuidString)", revisionID: "r1", baselineInputs: baseline,
                                form: form, pending: ["ebay.title": .string("Shirt")], savedAt: Date(timeIntervalSince1970: 1_800_000_000))

        EditorDraftStore.save(draft)
        defer { EditorDraftStore.remove(draft.listingID) }
        let loaded = try XCTUnwrap(EditorDraftStore.load(draft.listingID))
        XCTAssertEqual(loaded, draft)
        XCTAssertEqual(try loaded.form.sellerInputs.encodedNotes(), try form.sellerInputs.encodedNotes(),
                       "Unknown note fields survive the trip")

        XCTAssertTrue(loaded.restores(revisionID: "r1", inputs: baseline))
        XCTAssertFalse(loaded.restores(revisionID: "r2", inputs: baseline), "Saved elsewhere since: dropped")
        XCTAssertFalse(loaded.restores(revisionID: "r1", inputs: SellerInputs(notes: "changed")), "Notes changed elsewhere: dropped")

        EditorDraftStore.remove(draft.listingID)
        XCTAssertNil(EditorDraftStore.load(draft.listingID))
    }
}
