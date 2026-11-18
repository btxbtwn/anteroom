import XCTest
@testable import Anteroom

final class SellerInputsRegressionTests: XCTestCase {
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
