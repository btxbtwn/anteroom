import SwiftUI
import UIKit
import XCTest
@testable import Anteroom

@MainActor
final class BulkSizeViewTests: XCTestCase {
    func testKeptDraftRecoveryComparisonLayout() async throws {
        var current = ListingEditorModel.Form()
        current.fields.title = "Current linen shirt"
        var kept = current
        kept.fields.title = "Seller's unsaved title"
        kept.price = "19"
        kept.sellerInputs.sku = "Kept SKU"
        let draft = EditorDraft(listingID: "recovery-layout", revisionID: nil, baselineInputs: SellerInputs(),
                                form: kept, pending: [:], savedAt: .now)
        let view = DraftRecoveryView(current: current, draft: draft, useKeptCopy: {}, discardKeptCopy: {})
        let host = UIHostingController(rootView: view)
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 393, height: 852))
        window.rootViewController = host
        window.makeKeyAndVisible()
        defer { window.isHidden = true }
        host.view.layoutIfNeeded()
        try await Task.sleep(for: .milliseconds(500))
        let image = UIGraphicsImageRenderer(bounds: host.view.bounds).image { _ in
            host.view.drawHierarchy(in: host.view.bounds, afterScreenUpdates: true)
        }
        let attachment = XCTAttachment(image: image)
        attachment.name = "Kept edits recovery iPhone"
        attachment.lifetime = .keepAlways
        add(attachment)
        XCTAssertEqual(image.size.width, 393)
    }

    /// Render the production item editor on the Simulator with synthetic data only.
    func testReviewAndSuggestedSizeLayouts() async throws {
        for review in [false, true] {
            var plan = BulkListingPlan()
            plan.add([UUID()])
            plan.makeItemEach()
            let id = plan.items[0].id
            plan.applySizeTag(.init(photosRead: 2, status: review ? "conflict" : "accepted",
                                    size: review ? nil : "0", sizes: review ? ["M", "L"] : ["0"]), to: id)
            let editor = BulkItemEditor(plan: .constant(plan), itemID: id, number: 1, thumbnails: [:],
                                        reading: false, readingSize: false, capturedSizeRead: false, readSize: {}, read: {})
            let host = UIHostingController(rootView: NavigationStack { editor })
            host.overrideUserInterfaceStyle = review ? .dark : .light
            let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 393, height: 852))
            window.rootViewController = host
            window.makeKeyAndVisible()
            host.view.setNeedsLayout()
            host.view.layoutIfNeeded()
            try await Task.sleep(for: .milliseconds(500))
            let image = UIGraphicsImageRenderer(bounds: host.view.bounds).image { _ in
                host.view.drawHierarchy(in: host.view.bounds, afterScreenUpdates: true)
            }
            let attachment = XCTAttachment(image: image)
            attachment.name = review ? "Size conflict dark iPhone" : "Size zero suggestion light iPhone"
            attachment.lifetime = .keepAlways
            add(attachment)
            XCTAssertEqual(image.size.width, 393)
            window.isHidden = true
        }
    }
}
