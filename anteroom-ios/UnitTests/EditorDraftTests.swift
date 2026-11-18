import XCTest
@testable import Anteroom

final class EditorDraftTests: XCTestCase {
    func testLegacyDraftRestoresUnsavedEditsWithoutSizeProperties() throws {
        let baseline = SellerInputs(notes: #"{"size":"0","future":true}"#)
        var form = ListingEditorModel.Form()
        form.fields.title = "Unsaved title"
        form.sellerInputs = baseline
        form.sellerInputs.sku = "Unsaved SKU"
        let draft = EditorDraft(listingID: "legacy", revisionID: "r1", baselineInputs: baseline,
                                form: form, pending: [:], savedAt: .now)
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(draft)) as? [String: Any])
        var oldBaseline = try XCTUnwrap(object["baselineInputs"] as? [String: Any])
        oldBaseline.removeValue(forKey: "size")
        object["baselineInputs"] = oldBaseline
        var oldForm = try XCTUnwrap(object["form"] as? [String: Any])
        var oldInputs = try XCTUnwrap(oldForm["sellerInputs"] as? [String: Any])
        oldInputs.removeValue(forKey: "size")
        oldForm["sellerInputs"] = oldInputs
        object["form"] = oldForm
        let restored = try JSONDecoder().decode(EditorDraft.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertTrue(restored.restores(revisionID: "r1", inputs: baseline))
        XCTAssertEqual(restored.form.fields.title, "Unsaved title")
        XCTAssertEqual(restored.form.sellerInputs.sku, "Unsaved SKU")
        XCTAssertEqual(restored.form.sellerInputs.size, "0")
    }

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
