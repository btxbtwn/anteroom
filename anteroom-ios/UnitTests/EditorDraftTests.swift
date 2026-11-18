import XCTest
@testable import Anteroom

final class EditorDraftTests: XCTestCase {
    func testUnversionedDraftRejectsChangedListingButAllowsOpaqueMetadata() {
        var baseline = ListingEditorModel.Form()
        baseline.fields.title = "Shirt"
        var edited = baseline
        edited.price = "20"
        let draft = EditorDraft(listingID: "unversioned", revisionID: nil, baselineInputs: baseline.sellerInputs,
                                form: edited, pending: [:], savedAt: .now, baselineForm: baseline)
        var fresh = baseline
        fresh.sellerInputs = SellerInputs(notes: #"{"future":true}"#)
        XCTAssertTrue(draft.restores(revisionID: nil, inputs: fresh.sellerInputs, currentForm: fresh))
        fresh.fields.title = "Linen Shirt"
        XCTAssertFalse(draft.restores(revisionID: nil, inputs: fresh.sellerInputs, currentForm: fresh))
        let legacy = EditorDraft(listingID: "legacy", revisionID: nil, baselineInputs: baseline.sellerInputs,
                                 form: edited, pending: [:], savedAt: .now)
        XCTAssertFalse(legacy.restores(revisionID: nil, inputs: baseline.sellerInputs, currentForm: baseline))
    }

    func testLegacyDraftRestoresUnsavedEditsWithoutSizeProperties() throws {
        let baseline = SellerInputs(notes: #"{"size":"0","future":true}"#)
        // Frozen synthetic payload from the pre-size draft schema; do not regenerate with today's encoder.
        let raw = #"""
        {
          "listingID":"legacy","revisionID":"r1","pending":{},"savedAt":800000000,
          "baselineInputs":{
            "originalNotes":"{\"size\":\"0\",\"future\":true}","sku":"","sellerNotes":"",
            "knownFlaws":"","descriptionMeasurements":"","cog":"","packageDimensions":"",
            "vendooLabels":"","garment":"top","measurements":{}
          },
          "form":{
            "sellerInputs":{
              "originalNotes":"{\"size\":\"0\",\"future\":true}","sku":"Unsaved SKU","sellerNotes":"",
              "knownFlaws":"","descriptionMeasurements":"","cog":"","packageDimensions":"",
              "vendooLabels":"","garment":"top","measurements":{}
            },
            "fields":{
              "title":"Unsaved title","description":"","brand":"","condition":"","primaryColor":"",
              "secondaryColor":"","size":"","sku":"","labels":[],"zipCode":"","categoryPath":"",
              "department":"","packageDimensionsIn":""
            },
            "price":"","cost":"","quantity":"","weightOz":"","labels":""
          }
        }
        """#
        let restored = try JSONDecoder().decode(EditorDraft.self, from: Data(raw.utf8))
        XCTAssertTrue(restored.restores(revisionID: "r1", inputs: baseline))
        XCTAssertEqual(restored.form.fields.title, "Unsaved title")
        XCTAssertEqual(restored.form.sellerInputs.sku, "Unsaved SKU")
        XCTAssertEqual(restored.form.sellerInputs.size, "0")
    }

    func testNotesSaveBaselineRestoresLaterUnsavedListingEdits() {
        var savedInputs = SellerInputs()
        savedInputs.sku = "A"
        var form = ListingEditorModel.Form()
        form.fields.title = "Later unsaved title"
        form.sellerInputs = savedInputs
        let draft = EditorDraft(listingID: "saved-notes", revisionID: "r1", baselineInputs: savedInputs,
                                form: form, pending: [:], savedAt: .now)
        let freshInputs = SellerInputs(notes: #"{"sku":"A"}"#)
        XCTAssertTrue(draft.restores(revisionID: "r1", inputs: freshInputs))
        XCTAssertEqual(draft.form.sellerInputs.restoringNotesBaseline(from: freshInputs), freshInputs,
                       "Listing-only edits keep the current notes baseline, avoiding phantom changes and empty PATCHes")
        XCTAssertFalse(draft.restores(revisionID: "r2", inputs: freshInputs))
        XCTAssertFalse(draft.restores(revisionID: "r1", inputs: SellerInputs(notes: #"{"sku":"B"}"#)))
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
