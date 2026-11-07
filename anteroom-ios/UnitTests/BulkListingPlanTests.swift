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

    // The web's bulkListingNotes: shared cost and labels plus the item's SKU.
    func testNotesMatchTheItemDetailsContract() throws {
        let notes = try XCTUnwrap(BulkListingPlan.notes(cog: " 4.50 ", labels: "Spring, ready,, Spring", sku: " BX-1 "))
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(notes.utf8)) as? [String: Any])
        XCTAssertEqual(object["cog"] as? String, "4.50")
        XCTAssertEqual(object["vendooLabels"] as? String, "Spring, ready")
        XCTAssertEqual(object["sku"] as? String, "BX-1")
        XCTAssertNil(object["garment"])
    }

    func testNoNotesWhenNothingWasTyped() throws {
        XCTAssertNil(try BulkListingPlan.notes(cog: " ", labels: "", sku: ""))
    }
}
