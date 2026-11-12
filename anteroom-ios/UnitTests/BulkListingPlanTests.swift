import XCTest
@testable import Anteroom

final class BulkListingPlanTests: XCTestCase {
    private let a = UUID(), b = UUID(), c = UUID(), d = UUID()

    func testMakeItemKeepsPickingOrderAndLeavesTheRestLoose() throws {
        var plan = BulkListingPlan()
        plan.add([a, b, c, d])
        // Selection order doesn't matter: the first photo picked is the cover.
        let item = try XCTUnwrap(plan.makeItem(from: [c, a]))
        XCTAssertEqual(item.photos, [a, c])
        XCTAssertEqual(plan.loose, [b, d])
        XCTAssertEqual(plan.items.count, 1)
    }

    func testAddingAPhotoTwiceKeepsOneCopy() {
        var plan = BulkListingPlan()
        plan.add([a, b])
        plan.makeItem(from: [a])
        plan.add([a, b, c])
        XCTAssertEqual(plan.loose, [b, c])
    }

    func testMakeItemRefusesAnEmptyOrOversizedSelection() {
        var plan = BulkListingPlan()
        XCTAssertNil(plan.makeItem(from: []))
        let many = (0...BulkListingPlan.photosPerItem).map { _ in UUID() }
        plan.add(many)
        XCTAssertNil(plan.makeItem(from: Set(many)))
        XCTAssertEqual(plan.loose.count, many.count)
    }

    func testEachPhotoBecomesItsOwnItem() {
        var plan = BulkListingPlan()
        plan.add([a, b])
        plan.makeItemEach()
        XCTAssertEqual(plan.items.map(\.photos), [[a], [b]])
        XCTAssertTrue(plan.loose.isEmpty)
    }

    func testUngroupReturnsPhotosAndRemovingTheLastPhotoDropsTheItem() throws {
        var plan = BulkListingPlan()
        plan.add([a, b, c])
        let first = try XCTUnwrap(plan.makeItem(from: [a, b]))
        plan.makeItem(from: [c])
        plan.ungroup(first.id)
        XCTAssertEqual(plan.loose, [a, b])
        plan.remove(c)
        XCTAssertTrue(plan.items.isEmpty)
    }

    func testSKUIsKeptPerItem() throws {
        var plan = BulkListingPlan()
        plan.add([a, b])
        let first = try XCTUnwrap(plan.makeItem(from: [a]))
        plan.makeItem(from: [b])
        plan.setSKU("BX-1", for: first.id)
        XCTAssertEqual(plan.items.map(\.sku), ["BX-1", ""])
    }

    func testPhotosMoveWithinTheirItemAndAnyCanBecomeTheCover() throws {
        var plan = BulkListingPlan()
        plan.add([a, b, c])
        let item = try XCTUnwrap(plan.makeItem(from: [a, b, c]))
        plan.movePhotos(in: item.id, from: IndexSet(integer: 0), to: 3)
        XCTAssertEqual(plan.items[0].photos, [b, c, a])
        plan.makeCover(a, in: item.id)
        XCTAssertEqual(plan.items[0].photos, [a, b, c])
    }

    func testTurnsMapToTheRotateRoutesDegrees() {
        var plan = BulkListingPlan()
        plan.add([a])
        XCTAssertNil(plan.rotationDegrees(a))
        plan.rotate(a, by: 1)
        XCTAssertEqual(plan.rotationDegrees(a), 90)
        plan.rotate(a, by: 1)
        XCTAssertEqual(plan.rotationDegrees(a), 180)
        plan.rotate(a, by: -3)
        XCTAssertEqual(plan.rotationDegrees(a), -90)
        plan.rotate(a, by: 1)
        XCTAssertNil(plan.rotationDegrees(a))
        plan.rotate(a, by: -1)
        plan.remove(a)
        XCTAssertNil(plan.rotationDegrees(a))
    }

    // The web's applyReading: typed values win, blanks take accepted reads,
    // and fields the photos couldn't settle are named.
    func testReadingFillsOnlyBlankFieldsAndNamesTheRest() throws {
        var plan = BulkListingPlan()
        plan.add([a])
        let item = try XCTUnwrap(plan.makeItem(from: [a]))
        plan.setMeasurement("length", "27", for: item.id)
        let reading = MeasurementReading(photosRead: 1, measurements: [
            "pitToPit": .init(status: "accepted", value: 21.5),
            "length": .init(status: "accepted", value: 30),
            "sleeve": .init(status: "cannot_read", value: nil),
        ])
        plan.apply(reading, garment: .top, to: item.id)
        let read = plan.items[0]
        XCTAssertEqual(read.measurements["pitToPit"], "21.5")
        XCTAssertEqual(read.measurements["length"], "27")
        XCTAssertEqual(read.suggested, ["pitToPit"])
        XCTAssertEqual(read.readNote, "Filled from the photos. Check them. Measure by hand: Sleeve.")
        plan.setMeasurement("pitToPit", "22", for: item.id)
        XCTAssertTrue(plan.items[0].suggested.isEmpty)
    }

    func testReadingIsDroppedWhenTheGarmentChangedMeanwhile() throws {
        var plan = BulkListingPlan()
        plan.add([a])
        let item = try XCTUnwrap(plan.makeItem(from: [a]))
        plan.setGarment(.pants, for: item.id)
        plan.apply(MeasurementReading(photosRead: 1, measurements: ["pitToPit": .init(status: "accepted", value: 20)]),
                   garment: .top, to: item.id)
        XCTAssertTrue(plan.items[0].measurements.isEmpty)
        plan.apply(MeasurementReading(photosRead: 0, measurements: [:]), garment: .pants, to: item.id)
        XCTAssertEqual(plan.items[0].readNote, "No yardstick photo found.")
    }

    // The web's bulkListingNotes: shared cost and labels, the item's SKU,
    // and its garment and measurements only when some were typed.
    func testNotesMatchTheItemDetailsContract() throws {
        var item = BulkListingPlan.Item(id: UUID(), photos: [a])
        item.sku = " BX-1 "
        item.sizeTag = " 32 × 30 "
        item.garment = .pants
        item.measurements = ["waist": "16", "rise": " ", "pitToPit": "20"]
        let notes = try XCTUnwrap(BulkListingPlan.notes(cog: " 4.50 ", labels: "Spring, ready,, Spring", item: item))
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(notes.utf8)) as? [String: Any])
        XCTAssertEqual(object["cog"] as? String, "4.50")
        XCTAssertEqual(object["vendooLabels"] as? String, "Spring, ready")
        XCTAssertEqual(object["sku"] as? String, "BX-1")
        XCTAssertEqual(object["sellerNotes"] as? String, "Size tag: 32 × 30")
        XCTAssertEqual(object["garment"] as? String, "pants")
        XCTAssertEqual((object["measurements"] as? [String: Any])?["pants"] as? [String: String], ["waist": "16"])
    }

    func testNoGarmentOrNotesWhenNothingWasTyped() throws {
        var item = BulkListingPlan.Item(id: UUID(), photos: [a])
        item.garment = .skirt
        XCTAssertNil(try BulkListingPlan.notes(cog: " ", labels: "", item: item))
    }

    func testSizeTagUsesSellerNotesAndStaysWithItsItem() throws {
        var plan = BulkListingPlan()
        plan.add([a, UUID()])
        plan.makeItemEach()
        plan.setSizeTag(" XL / TG ", for: plan.items[0].id)
        let notes = try XCTUnwrap(BulkListingPlan.notes(cog: "", labels: "", item: plan.items[0]))
        XCTAssertEqual(SellerInputs(notes: notes).sellerNotes, "Size tag: XL / TG")
        XCTAssertNil(try BulkListingPlan.notes(cog: "", labels: "", item: plan.items[1]))
        plan.setSizeTag(" ", for: plan.items[0].id)
        XCTAssertNil(try BulkListingPlan.notes(cog: "", labels: "", item: plan.items[0]))
    }
}
