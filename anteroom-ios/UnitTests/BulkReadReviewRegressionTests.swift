import XCTest
@testable import Anteroom

final class BulkReadReviewRegressionTests: XCTestCase {
    private struct ReadFailure: LocalizedError {
        var errorDescription: String? { "Read failed." }
    }
    func testSizeReadSelectionStaysInPickingOrderAcrossCoverChanges() throws {
        var plan = BulkListingPlan()
        let photos = (0..<30).map { _ in UUID() }
        plan.add(photos)
        let item = try XCTUnwrap(plan.makeItem(from: Set(photos)))
        let selected = Array(item.sizeReadPhotos.prefix(20))
        plan.makeCover(photos[20], in: item.id)
        XCTAssertEqual(Array(plan.items[0].sizeReadPhotos.prefix(20)), selected)
        plan.movePhotos(in: item.id, from: IndexSet(integer: 0), to: 30)
        XCTAssertEqual(Array(plan.items[0].sizeReadPhotos.prefix(20)), selected)
        plan.remove(photos[0])
        XCTAssertFalse(plan.items[0].sizeReadPhotos.contains(photos[0]))
    }
    func testPhotoRemovalDuringRereadKeepsUnresolvedMismatch() throws {
        var plan = BulkListingPlan()
        let photos = [UUID(), UUID()]
        plan.add(photos)
        let item = try XCTUnwrap(plan.makeItem(from: Set(photos)))
        plan.setSizeTag("M", for: item.id)
        plan.applySizeTag(.init(photosRead: 2, status: "accepted", size: "L", sizes: ["L"]), to: item.id)
        plan.remove(photos[1], readingSizes: [item.id])
        XCTAssertEqual(plan.items[0].sizeTag, "M")
        XCTAssertTrue(plan.items[0].sizeNeedsReview)
        XCTAssertTrue(plan.items[0].sizeNote?.contains("removed while reading") == true)
    }

    func testRemovingPhotoBeforeWorkerCapturesItDoesNotShowInterruptedRead() throws {
        var plan = BulkListingPlan()
        let photos = [UUID(), UUID()]
        plan.add(photos)
        let item = try XCTUnwrap(plan.makeItem(from: Set(photos)))
        plan.remove(photos[1])
        XCTAssertNil(plan.items[0].sizeNote)
        XCTAssertFalse(plan.items[0].sizeNeedsReview)
        let capturedWhenWorkerStarts = plan.items[0]
        plan.completeSizeRead(.success(.init(photosRead: 1, status: "accepted", size: "0", sizes: ["0"])),
                              from: capturedWhenWorkerStarts)
        XCTAssertEqual(plan.items[0].sizeTag, "0")
        XCTAssertEqual(plan.items[0].id, item.id)
    }
    func testReorderAndMakeCoverKeepBothSuccessAndErrorResults() throws {
        for makeCover in [false, true] {
            for failed in [false, true] {
                var plan = BulkListingPlan()
                let photos = [UUID(), UUID(), UUID()]
                plan.add(photos)
                let captured = try XCTUnwrap(plan.makeItem(from: Set(photos)))
                if makeCover { plan.makeCover(photos[2], in: captured.id) }
                else { plan.movePhotos(in: captured.id, from: IndexSet(integer: 0), to: 3) }
                let result: Result<SizeTagReading, Error> = failed ? .failure(ReadFailure())
                    : .success(.init(photosRead: 3, status: "accepted", size: "0", sizes: ["0"]))
                plan.completeSizeRead(result, from: captured)
                XCTAssertEqual(plan.items[0].sizeTag, failed ? "" : "0")
                XCTAssertEqual(plan.items[0].sizeNeedsReview, failed)
                XCTAssertEqual(plan.items[0].sizeNote, failed ? "Read failed. Type the size from the tag." : "Size filled from the tag. Check it.")
            }
        }
    }

    func testRemovedPhotosRejectBothCallbacksAndKeepRecoveryNote() throws {
        for failed in [false, true] {
            var plan = BulkListingPlan()
            let photos = [UUID(), UUID()]
            plan.add(photos)
            let captured = try XCTUnwrap(plan.makeItem(from: Set(photos)))
            plan.remove(photos[0], readingSizes: [captured.id])
            let result: Result<SizeTagReading, Error> = failed ? .failure(ReadFailure())
                : .success(.init(photosRead: 2, status: "accepted", size: "0", sizes: ["0"]))
            plan.completeSizeRead(result, from: captured)
            XCTAssertEqual(plan.items[0].sizeTag, "")
            XCTAssertTrue(plan.items[0].sizeNeedsReview)
            XCTAssertEqual(plan.items[0].sizeNote, "A photo was removed while reading. Read size tags again or type the size.")
            plan.ungroup(captured.id)
            plan.completeSizeRead(result, from: captured)
            XCTAssertTrue(plan.items.isEmpty)
        }
    }

    func testRemovingPhotoClearsItsUntouchedSizeSuggestion() throws {
        var plan = BulkListingPlan()
        let photos = [UUID(), UUID()]
        plan.add(photos)
        let item = try XCTUnwrap(plan.makeItem(from: Set(photos)))
        plan.applySizeTag(.init(photosRead: 2, status: "accepted", size: "0", sizes: ["0"]), to: item.id)
        plan.remove(photos[0])
        XCTAssertEqual(plan.items[0].sizeTag, "")
        XCTAssertFalse(plan.items[0].sizeSuggested)
        XCTAssertTrue(plan.items[0].sizeNeedsReview)
        XCTAssertEqual(plan.items[0].sizeNote, "A photo was removed, so the size read from it was cleared. Read size tags again or type the size.")
    }

    func testAcceptedMeasurementsWithNoBlankFieldsSayNothingNew() throws {
        var plan = BulkListingPlan()
        let photo = UUID()
        plan.add([photo])
        let item = try XCTUnwrap(plan.makeItem(from: [photo]))
        for field in item.garment.fields { plan.setMeasurement(field.key, "20", for: item.id) }
        plan.apply(.init(photosRead: 1, measurements: ["length": .init(status: "accepted", value: 27)]),
                   garment: .top, to: item.id)
        XCTAssertEqual(plan.items[0].readNote, "Nothing new could be read from the photos.")
        XCTAssertEqual(plan.items[0].measurements["length"], "20")
    }

    func testRemovingPhotoDuringReadFlagsBlankSizeButKeepsManualZero() throws {
        for size in ["", "0"] {
            var plan = BulkListingPlan()
            let photos = [UUID(), UUID()]
            plan.add(photos)
            let item = try XCTUnwrap(plan.makeItem(from: Set(photos)))
            plan.setSizeTag(size, for: item.id)
            plan.remove(photos[0], readingSizes: [item.id])
            XCTAssertEqual(plan.items[0].sizeTag, size)
            XCTAssertEqual(plan.items[0].sizeNeedsReview, size.isEmpty)
            XCTAssertEqual(plan.items[0].sizeNote, "A photo was removed while reading. Read size tags again or type the size.")
        }
    }

    func testUnacceptedMeasurementsKeepUnreadableCopyAndNameBlankFields() throws {
        var plan = BulkListingPlan()
        let photo = UUID()
        plan.add([photo])
        let item = try XCTUnwrap(plan.makeItem(from: [photo]))
        plan.apply(.init(photosRead: 1, measurements: [:]), garment: .top, to: item.id)
        XCTAssertEqual(plan.items[0].readNote, "The yardstick could not be read. Measure by hand: Pit to Pit, Length, Sleeve.")
    }
}
