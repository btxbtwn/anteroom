import XCTest
@testable import Anteroom

final class SizeTagReadingTests: XCTestCase {
    private func plan(_ size: String = "") -> BulkListingPlan {
        var plan = BulkListingPlan()
        plan.add([UUID(), UUID()])
        plan.makeItemEach()
        plan.setSizeTag(size, for: plan.items[0].id)
        return plan
    }

    func testAcceptedFillsOnlyItsItemAndZeroIsAValidSize() throws {
        var plan = plan()
        plan.applySizeTag(.init(photosRead: 1, status: "accepted", size: "0", sizes: ["0"]), to: plan.items[0].id)
        XCTAssertEqual(plan.items[0].sizeTag, "0")
        XCTAssertTrue(plan.items[0].sizeSuggested)
        XCTAssertFalse(plan.items[0].sizeNeedsReview)
        XCTAssertEqual(plan.items[0].sizeNote, "Size filled from the tag. Check it.")
        XCTAssertEqual(plan.items[1].sizeTag, "")
        let notes = try XCTUnwrap(BulkListingPlan.notes(cog: "", labels: "", item: plan.items[0]))
        XCTAssertEqual(SellerInputs(notes: notes).size, "0")
        XCTAssertEqual(SellerInputs(notes: notes).sellerNotes, "")
    }

    func testManualValueWinsEvenWhenEditedWhileRequestWasRunning() {
        var plan = plan("0")
        let id = plan.items[0].id
        plan.applySizeTag(.init(photosRead: 1, status: "accepted", size: "M", sizes: ["M"]), to: id)
        XCTAssertEqual(plan.items[0].sizeTag, "0")
        XCTAssertTrue(plan.items[0].sizeNeedsReview)
        XCTAssertEqual(plan.items[0].sizeNote, "The tag reads M. Check the size you typed.")
        plan.setSizeTag("m", for: id)
        XCTAssertFalse(plan.items[0].sizeNeedsReview)
        XCTAssertNil(plan.items[0].sizeNote)
        plan.applySizeTag(.init(photosRead: 1, status: "accepted", size: "M", sizes: ["M"]), to: id)
        XCTAssertFalse(plan.items[0].sizeNeedsReview)
        XCTAssertEqual(plan.items[0].sizeNote, "Size matches the tag.")
    }

    func testMissingUnreadableAndConflictingTagsRequireReviewOnlyWhenNeeded() {
        for status in ["not_found", "unreadable", "conflict"] {
            for manual in ["", "0"] {
                var plan = plan(manual)
                plan.applySizeTag(.init(photosRead: 2, status: status, size: nil, sizes: ["M", "L"]), to: plan.items[0].id)
                XCTAssertEqual(plan.items[0].sizeTag, manual)
                XCTAssertEqual(plan.items[0].sizeNeedsReview, manual.isEmpty || status == "conflict")
                XCTAssertEqual(plan.items[0].sizeNote != nil, manual.isEmpty || status == "conflict")
                if status == "conflict" { XCTAssertTrue(plan.items[0].sizeNote?.contains("M, L") == true) }
            }
        }
    }

    func testRereadReplacesSuggestionsAndClearsThemOnConflict() {
        var plan = plan()
        let id = plan.items[0].id
        plan.applySizeTag(.init(photosRead: 1, status: "accepted", size: "M", sizes: ["M"]), to: id)
        plan.applySizeTag(.init(photosRead: 1, status: "accepted", size: "L", sizes: ["L"]), to: id)
        XCTAssertEqual(plan.items[0].sizeTag, "L")
        plan.applySizeTag(.init(photosRead: 2, status: "conflict", size: nil, sizes: ["M", "L"]), to: id)
        XCTAssertEqual(plan.items[0].sizeTag, "")
        XCTAssertTrue(plan.items[0].sizeNeedsReview)
        XCTAssertFalse(plan.items[0].sizeSuggested)
    }

    func testFailuresKeepManualValuesAndNameTheNextActionForBlankValues() {
        var plan = plan()
        let id = plan.items[0].id
        plan.failSizeTag("Read failed.", for: id)
        XCTAssertEqual(plan.items[0].sizeNote, "Read failed. Type the size from the tag.")
        XCTAssertTrue(plan.items[0].sizeNeedsReview)
        plan.setSizeTag("0", for: id)
        plan.failSizeTag("Read failed.", for: id)
        XCTAssertEqual(plan.items[0].sizeTag, "0")
        XCTAssertEqual(plan.items[0].sizeNote, "Read failed.")
        XCTAssertFalse(plan.items[0].sizeNeedsReview)
    }

    func testSizeOnlyNotesAreStructuredAndPreserveShippingAndUnknownFields() throws {
        var inputs = SellerInputs(notes: #"{"size":"0","packageDimensions":"12x9x3","sellerNotes":"Estate sale","future":true}"#)
        XCTAssertEqual(inputs.size, "0")
        XCTAssertEqual(SellerInputs(notes: #"{"size":"0"}"#).sellerNotes, "")
        inputs.sku = "A"
        let changes = try inputs.noteChanges()
        XCTAssertNil(changes["size"], "An unrelated stale save must omit size")
        XCTAssertEqual(changes["sku"], .string("A"))
        inputs.size = ""
        XCTAssertEqual(try inputs.noteChanges()["size"], .string(""))
        let encoded = try inputs.encodedNotes()
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(encoded.utf8)) as? [String: Any])
        XCTAssertEqual(object["packageDimensions"] as? String, "12x9x3")
        XCTAssertEqual(object["sellerNotes"] as? String, "Estate sale")
        XCTAssertEqual(object["future"] as? Bool, true)
    }

    func testChangedMeasurementsOmitUntouchedFieldsAndKeepTagSizeOutOfShipping() throws {
        var inputs = SellerInputs(notes: #"{"size":"0","measurements":{"top":{"length":"27","sleeve":"8"}},"packageDimensions":"12x9x3"}"#)
        inputs.setMeasurement("length", value: "28")
        XCTAssertEqual(try inputs.noteChanges(), ["garment": .string("top"), "measurements": .object(["top": .object(["length": .string("28")])])])
        var blank = SellerInputs()
        blank.size = " 0 "
        XCTAssertEqual(try blank.noteChanges(), ["size": .string("0")])
        blank.size = " "
        XCTAssertEqual(try blank.noteChanges(), [:])
    }

    func testMeasurementReaderMetadataNeverBecomesSellerCopy() throws {
        let raw = #"{"photos_read":1,"reader":"OpenRouter / Gemini Flash Latest","measurements":{"pitToPit":{"status":"accepted","value":20}}}"#
        let reading = try JSONDecoder().decode(MeasurementReading.self, from: Data(raw.utf8))
        var plan = plan()
        plan.apply(reading, garment: .top, to: plan.items[0].id)
        XCTAssertFalse(plan.items[0].readNote?.contains("OpenRouter") == true)
        let notes = try XCTUnwrap(BulkListingPlan.notes(cog: "", labels: "", item: plan.items[0]))
        XCTAssertFalse(notes.contains("Gemini"))
        XCTAssertFalse(notes.contains("OpenRouter"))
    }

    func testSecondSaveDoesNotResendSizeFromAnEarlierSave() throws {
        var inputs = SellerInputs(notes: #"{"size":"0","sellerNotes":"Estate sale"}"#)
        inputs.size = "M"
        let saved = inputs
        inputs.sku = "A"
        XCTAssertEqual(try inputs.noteChanges(since: saved), ["sku": .string("A")])
        inputs.size = ""
        XCTAssertEqual(try inputs.noteChanges(since: saved)["size"], .string(""))
    }
}
