import XCTest
@testable import Anteroom

final class SellerInputsRegressionTests: XCTestCase {
    func testOpaqueMeasurementGroupsDoNotChangeEffectiveValues() throws {
        let empty = SellerInputs()
        let metadata = SellerInputs(notes: #"{"measurements":{"top":{"futureField":true}}}"#)
        XCTAssertTrue(empty.hasSameEffectiveValues(as: metadata))
        XCTAssertEqual(empty.restoringNotesBaseline(from: metadata), metadata)
        var edited = empty
        edited.sku = "A"
        let rebased = edited.restoringNotesBaseline(from: metadata)
        XCTAssertEqual(rebased.sku, "A")
        XCTAssertTrue(try rebased.encodedNotes().contains("futureField"))
        XCTAssertFalse(empty.hasSameEffectiveValues(as: SellerInputs(notes: #"{"measurements":{"top":{"length":"20"}}}"#)))
    }
    func testSavedNotesMatchFreshInputsForDraftRestoration() throws {
        var saved = SellerInputs()
        saved.sku = "A"
        saved.size = "0"
        let fresh = SellerInputs(notes: #"{"sku":"A","size":"0"}"#)
        XCTAssertTrue(saved.hasSameEffectiveValues(as: fresh))
        XCTAssertEqual(try saved.noteChanges(since: fresh), [:], "A restored baseline needs no notes write when effective values match")
        var changed = fresh
        changed.sku = "B"
        XCTAssertFalse(saved.hasSameEffectiveValues(as: changed))
        changed = fresh
        changed.packageDimensions = "12x9x3"
        XCTAssertFalse(saved.hasSameEffectiveValues(as: changed))
    }

    func testMeasurementOnlyPatchDoesNotReplaySavedGarment() throws {
        var current = SellerInputs(notes: #"{"garment":"top","measurements":{"top":{"length":"27"}}}"#)
        current.setMeasurement("length", value: "28")
        XCTAssertNil(try current.noteChanges()["garment"])
        var saved = SellerInputs()
        saved.setMeasurement("length", value: "27")
        current = saved
        current.setMeasurement("length", value: "28")
        XCTAssertNil(try current.noteChanges(since: saved)["garment"])
        current.garment = .pants
        XCTAssertEqual(try current.noteChanges(since: saved)["garment"], .string("pants"))
    }
    func testDraftSavedBeforeSizeWasAddedStillDecodes() throws {
        var inputs = SellerInputs(notes: #"{"sku":"A","future":true}"#)
        inputs.sellerNotes = "Unsaved provenance"
        var old = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(inputs)) as? [String: Any])
        old.removeValue(forKey: "size")
        let restored = try JSONDecoder().decode(SellerInputs.self, from: JSONSerialization.data(withJSONObject: old))
        XCTAssertEqual(restored.size, "")
        XCTAssertEqual(restored.sku, "A")
        XCTAssertEqual(restored.sellerNotes, "Unsaved provenance")
        XCTAssertTrue(try restored.encodedNotes().contains("future"))
    }

    func testClearingValuesAddedByAPreviousSaveSendsExplicitClears() throws {
        var saved = SellerInputs()
        saved.size = "0"
        saved.sku = "A"
        saved.descriptionMeasurements = "Length 27"
        var current = saved
        current.size = ""
        current.sku = ""
        current.descriptionMeasurements = ""
        XCTAssertEqual(try current.noteChanges(since: saved), [
            "size": .string(""), "sku": .string(""), "descriptionMeasurements": .string("")
        ])
    }

    func testLegacyDraftRecoversStructuredSizeFromOriginalNotes() throws {
        let inputs = SellerInputs(notes: #"{"size":"0","sku":"A"}"#)
        var old = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(inputs)) as? [String: Any])
        old.removeValue(forKey: "size")
        let restored = try JSONDecoder().decode(SellerInputs.self, from: JSONSerialization.data(withJSONObject: old))
        XCTAssertEqual(restored, inputs, "A restored baseline must still match the server notes")
        XCTAssertEqual(restored.size, "0")
    }

    func testWhitespaceOnlyBlankSizeIsNotAnEditButClearingZeroIs() throws {
        var blank = SellerInputs()
        blank.size = " "
        XCTAssertEqual(try blank.noteChanges(), [:])
        var saved = SellerInputs()
        saved.size = "0"
        XCTAssertEqual(try blank.noteChanges(since: saved), ["size": .string("")])
    }

    func testRemovedMeasurementsSendOnlyTheirExplicitClears() throws {
        var saved = SellerInputs()
        saved.setMeasurement("length", value: "27")
        let current = SellerInputs()
        XCTAssertEqual(try current.noteChanges(since: saved), [
            "measurements": .object(["top": .object(["length": .string("")])])
        ])
    }

    func testUnchangedPaddedSizeIsNotResentWhenAnotherFieldChanges() throws {
        let saved = SellerInputs(notes: #"{"size":" M ","packageDimensions":"12x9x3","future":true}"#)
        var current = saved
        current.sku = "A"
        XCTAssertEqual(try current.noteChanges(since: saved), ["sku": .string("A")])
        let notes = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(current.encodedNotes().utf8)) as? [String: Any])
        XCTAssertEqual(notes["size"] as? String, " M ")
        XCTAssertEqual(notes["packageDimensions"] as? String, "12x9x3")
    }
}
