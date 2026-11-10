import UIKit
import XCTest

@MainActor
final class SandboxTests: XCTestCase {
    func testCreateAndReopenDraft() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        // New listing opens an untitled draft straight away; no title first.
        openNewListing(app)
        let name = "Simulator draft \(UUID().uuidString.prefix(8))"
        enterListingTitle(name, app: app)
        XCTAssertTrue(app.navigationBars[name].waitForExistence(timeout: 15))
        app.navigationBars[name].buttons["Save"].tap()
        let saved = NSPredicate(format: "enabled == false")
        expectation(for: saved, evaluatedWith: app.navigationBars[name].buttons["Save"])
        waitForExpectations(timeout: 30)
        app.terminate()
        launchSandbox(app)
        XCTAssertTrue(app.staticTexts[name].waitForExistence(timeout: 30))
    }
}

@MainActor
final class SellerInputSandboxTests: XCTestCase {
    func testSellerInputsAndMeasurementsSurviveReopen() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        openNewListing(app)
        let name = "Seller inputs \(UUID().uuidString.prefix(8))"
        enterListingTitle(name, app: app)
        XCTAssertTrue(app.navigationBars[name].waitForExistence(timeout: 15))

        enter("seller-notes", text: "Found at a local estate sale", app: app)
        enter("seller-flaws", text: "Small mark near hem", app: app)
        enter("seller-labels", text: "Spring, Ready", app: app)
        attach(app, name: "Seller notes flaws and labels")
        enter("measurement-top-pitToPit", text: "22.5", app: app)
        let picker = app.segmentedControls["seller-garment"]
        XCTAssertTrue(picker.exists)
        picker.buttons["Pants"].tap()
        enter("measurement-pants-waist", text: "16", app: app)
        reveal(picker, app: app)
        picker.buttons["Top"].tap()
        XCTAssertEqual(field("measurement-top-pitToPit", app: app).value as? String, "22.5")
        attach(app, name: "Seller inputs before save")
        let done = app.toolbars.buttons["Done"].firstMatch
        if done.exists { done.tap() }
        app.navigationBars[name].buttons["Save"].tap()
        // The disposable sandbox on a shared CI runner can take a while to save.
        XCTAssertTrue(app.navigationBars[name].buttons["Save"].waitForExistence(timeout: 30))
        let saved = NSPredicate(format: "enabled == false")
        expectation(for: saved, evaluatedWith: app.navigationBars[name].buttons["Save"])
        waitForExpectations(timeout: 15)

        app.terminate()
        launchSandbox(app)
        let row = app.staticTexts[name]
        XCTAssertTrue(row.waitForExistence(timeout: 30))
        row.tap()
        XCTAssertTrue(app.navigationBars[name].waitForExistence(timeout: 15))
        // The editor opens at once and fills its fields when the listing loads.
        expectValue("seller-notes", "Found at a local estate sale", app: app)
        expectValue("seller-flaws", "Small mark near hem", app: app)
        expectValue("measurement-top-pitToPit", "22.5", app: app)
        reveal(app.segmentedControls["seller-garment"], app: app)
        app.segmentedControls["seller-garment"].buttons["Pants"].tap()
        XCTAssertEqual(field("measurement-pants-waist", app: app).value as? String, "16")
        attach(app, name: "Reopened garment measurements")
    }

    private func expectValue(_ id: String, _ text: String, app: XCUIApplication) {
        let control = field(id, app: app)
        expectation(for: NSPredicate(format: "value == %@", text), evaluatedWith: control)
        waitForExpectations(timeout: 20)
    }

    private func enter(_ id: String, text: String, app: XCUIApplication) {
        let control = field(id, app: app)
        // A tap during a scroll or keyboard animation can miss focus; typing
        // then fails with "no keyboard focus". Tap until the field has it.
        for _ in 0..<3 {
            control.tap()
            if app.keyboards.firstMatch.waitForExistence(timeout: 3),
               (control.value(forKey: "hasKeyboardFocus") as? Bool) == true { break }
        }
        control.typeText(text)
        dismissKeyboard(app)
        XCTAssertEqual(control.value as? String, text)
    }

    private func dismissKeyboard(_ app: XCUIApplication) {
        let keyboard = app.keyboards.firstMatch
        guard keyboard.exists else { return }
        let done = app.buttons["Done"].firstMatch
        XCTAssertTrue(done.waitForExistence(timeout: 5), "The editor must provide a keyboard dismissal action")
        done.tap()
        XCTAssertTrue(keyboard.waitForNonExistence(timeout: 5), "Dismiss the keyboard before scrolling the form")
    }

    private func field(_ id: String, app: XCUIApplication) -> XCUIElement {
        let control = app.descendants(matching: .any).matching(identifier: id).firstMatch
        XCTAssertTrue(control.waitForExistence(timeout: 10), "Missing native seller field: \(id)")
        reveal(control, app: app)
        return control
    }

    private func reveal(_ control: XCUIElement, app: XCUIApplication) {
        // A full ScrollView swipe can otherwise start on the keyboard and
        // insert predictive text instead of moving the form (CI video repro).
        dismissKeyboard(app)
        for _ in 0..<12 {
            if control.isHittable { return }
            app.scrollViews.firstMatch.swipeUp()
        }
        // A previously edited field may now be above the viewport.
        for _ in 0..<12 {
            if control.isHittable { return }
            app.scrollViews.firstMatch.swipeDown()
        }
        XCTAssertTrue(control.isHittable)
    }

    private func attach(_ app: XCUIApplication, name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}

@MainActor
final class AppearanceSandboxTests: XCTestCase {
    func testAppearanceChoicesPersistAcrossRelaunch() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        for choice in ["Light", "Dark", "System"] {
            openSettings(app)
            let picker = app.buttons["appearance-picker"]
            waitForReady(picker)
            picker.tap()
            app.buttons[choice].tap()
            waitForValue(choice, picker: picker)
            let screenshot = XCTAttachment(screenshot: app.screenshot())
            screenshot.name = "Appearance \(choice)"
            screenshot.lifetime = .keepAlways
            add(screenshot)
            app.terminate()
            launchSandbox(app)
            openSettings(app)
            waitForValue(choice, picker: app.buttons["appearance-picker"])
        }
    }

    private func openSettings(_ app: XCUIApplication) {
        let settings = app.tabBars.buttons["Settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: 30))
        settings.tap()
        XCTAssertTrue(app.buttons["appearance-picker"].waitForExistence(timeout: 10))
    }

    private func waitForReady(_ picker: XCUIElement) {
        expectation(for: NSPredicate(format: "enabled == true"), evaluatedWith: picker)
        waitForExpectations(timeout: 15)
    }

    private func waitForValue(_ choice: String, picker: XCUIElement) {
        expectation(for: NSPredicate(format: "value == %@ AND enabled == true", choice), evaluatedWith: picker)
        waitForExpectations(timeout: 15)
    }
}

/// Each destination the hosted workspace offers opens on iPhone against the
/// disposable hosted sandbox, with screenshots kept for review.
@MainActor
final class DestinationSandboxTests: XCTestCase {
    func testDestinationsOpen() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)

        let allTab = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'All,'")).firstMatch
        XCTAssertTrue(allTab.waitForExistence(timeout: 30), "Inventory shows its status tabs")
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Draft,'")).firstMatch.exists)
        keep(app, "Inventory with status tabs")
        let filters = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Filters'")).firstMatch
        XCTAssertTrue(filters.exists)
        filters.tap()
        let notListed = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Not listed'")).firstMatch
        XCTAssertTrue(notListed.waitForExistence(timeout: 5))
        keep(app, "Inventory filters")
        // The native menu occludes the title and tab bar in the AX tree.
        // Tap the background outside its popover, then verify dismissal.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: notListed)
        waitForExpectations(timeout: 5)

        let analytics = app.tabBars.buttons["Analytics"]
        XCTAssertTrue(analytics.isHittable, "The Filters menu must release the tab bar")
        analytics.tap()
        XCTAssertTrue(app.buttons["Sales records"].waitForExistence(timeout: 15))
        keep(app, "Analytics sales records")
        app.buttons["Inventory analytics"].tap()
        XCTAssertTrue(app.staticTexts["Inventory today"].waitForExistence(timeout: 30))
        keep(app, "Inventory analytics")

        app.tabBars.buttons["Settings"].tap()

        let listings = app.buttons["Listings"].firstMatch
        XCTAssertTrue(listings.waitForExistence(timeout: 10))
        listings.tap()
        XCTAssertTrue(app.switches.matching(NSPredicate(format: "label CONTAINS 'eBay'")).firstMatch.waitForExistence(timeout: 20),
                      "Where you list shows the marketplaces")
        keep(app, "Settings Listings")
        app.navigationBars.buttons.firstMatch.tap()
        app.buttons["Providers"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Xiaomi MiMo"].waitForExistence(timeout: 20))
        keep(app, "Settings Providers")
    }

    private func keep(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}

/// Settings → Account → Delete account explains what goes and what stays
/// before anything is deleted. Cancelling keeps the account.
@MainActor
final class AccountDeletionSandboxTests: XCTestCase {
    func testDeleteAccountExplainsAndCancels() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        app.tabBars.buttons["Settings"].tap()
        let delete = app.buttons["delete-account"]
        for _ in 0..<4 where !delete.isHittable { app.swipeUp() }
        XCTAssertTrue(delete.waitForExistence(timeout: 15))
        delete.tap()
        let alert = app.alerts["Delete your Anteroom account?"]
        XCTAssertTrue(alert.waitForExistence(timeout: 10))
        XCTAssertTrue(alert.staticTexts.matching(NSPredicate(format: "label CONTAINS 'stay there'")).firstMatch.exists,
                      "The confirmation says live marketplace listings stay")
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Delete account confirmation"
        attachment.lifetime = .keepAlways
        add(attachment)
        alert.buttons["Cancel"].tap()
        XCTAssertTrue(delete.waitForExistence(timeout: 5))
        XCTAssertTrue(app.tabBars.firstMatch.exists, "Cancelling keeps the seller signed in")
    }
}

/// Sales records lead with the period's figures and sales per week. The
/// sandbox seeds sales, so both have data.
@MainActor
final class AnalyticsSandboxTests: XCTestCase {
    func testSalesRecordsShowFiguresAndWeeklyChart() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        let allTab = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'All,'")).firstMatch
        XCTAssertTrue(allTab.waitForExistence(timeout: 30), "Inventory has loaded")
        let analytics = app.tabBars.buttons["Analytics"]
        analytics.tap()
        // The first tap on the floating tab bar after launch can land before it accepts touches.
        if !app.buttons["Sales records"].waitForExistence(timeout: 5) { analytics.tap() }
        XCTAssertTrue(app.buttons["Sales records"].waitForExistence(timeout: 15))
        XCTAssertTrue(app.staticTexts["Revenue"].waitForExistence(timeout: 30))
        XCTAssertTrue(app.staticTexts["Items sold"].exists)
        keep(app, "Analytics figures")
        app.swipeUp()
        XCTAssertTrue(app.staticTexts["Items sold per week"].waitForExistence(timeout: 10))
        keep(app, "Analytics weekly chart")
    }

    private func keep(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}

/// The business assistant answers a starter question on the sandbox's fake
/// provider, streams it in, and keeps the conversation in Chats.
@MainActor
final class AssistantSandboxTests: XCTestCase {
    func testStarterQuestionIsAnsweredAndKept() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        // Signed in, the app opens on the Assistant.
        launchSandbox(app, tab: nil)
        XCTAssertTrue(app.tabBars.buttons["Assistant"].isSelected, "The Assistant is home")
        let starter = app.buttons["Which brands and categories make me the most profit?"]
        XCTAssertTrue(starter.waitForExistence(timeout: 20), "A new chat offers starter questions")
        keep(app, "Assistant starters")
        starter.tap()

        let answer = app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'Sandbox Levi'")).firstMatch
        XCTAssertTrue(answer.waitForExistence(timeout: 60), "The answer streams into the conversation")
        let send = app.buttons["assistant-send"]
        expectation(for: NSPredicate(format: "label == 'Send'"), evaluatedWith: send)
        waitForExpectations(timeout: 60)
        keep(app, "Assistant answer")

        XCTAssertTrue(app.buttons["Chats"].waitForExistence(timeout: 30), "History shows once there is a chat")
        app.buttons["Chats"].tap()
        // The starter with this same title remains in the background after a
        // relaunch. Only a row in the Chats list is a saved conversation.
        let thread = app.collectionViews.buttons.matching(NSPredicate(format: "label CONTAINS 'Which brands'")).firstMatch
        XCTAssertTrue(thread.waitForExistence(timeout: 60), "Chats lists the conversation")
        keep(app, "Assistant chats")
        app.buttons["Done"].tap()

        app.terminate()
        launchSandbox(app, tab: "Assistant")
        XCTAssertTrue(app.buttons["Chats"].waitForExistence(timeout: 30), "History shows once there is a chat")
        app.buttons["Chats"].tap()
        XCTAssertTrue(thread.waitForExistence(timeout: 60), "Chats finishes loading the saved conversation")
        thread.tap()
        XCTAssertTrue(answer.waitForExistence(timeout: 20), "Reopening the conversation shows the saved answer")
    }

    private func keep(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}

/// The eBay tab draws eBay's own form: links that open more fields, offer
/// fields that follow Allow offers, and eBay's category tree.
@MainActor
final class EbayFormSandboxTests: XCTestCase {
    func testEbayFormRevealsFieldsAndBrowsesCategories() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        let listing = app.staticTexts["Levi's M Blue Cotton Crew Neck T-Shirt"].firstMatch
        XCTAssertTrue(listing.waitForExistence(timeout: 30))
        listing.tap()
        let formMenu = app.buttons["Form, General"]
        XCTAssertTrue(formMenu.waitForExistence(timeout: 30))
        expectation(for: NSPredicate(format: "enabled == true"), evaluatedWith: formMenu)
        waitForExpectations(timeout: 30)
        formMenu.tap()
        let ebay = app.buttons["eBay"].firstMatch
        XCTAssertTrue(ebay.waitForExistence(timeout: 10))
        ebay.tap()

        let titleOptions = app.buttons["See title options"]
        XCTAssertTrue(titleOptions.waitForExistence(timeout: 60), "eBay's title section offers its options link")
        XCTAssertFalse(app.textFields["Subtitle"].exists, "Subtitle waits behind See title options")
        keep(app, "eBay form")
        titleOptions.tap()
        XCTAssertTrue(app.textFields["Subtitle"].waitForExistence(timeout: 10), "See title options shows Subtitle")

        let category = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Category'")).firstMatch
        XCTAssertTrue(category.waitForExistence(timeout: 10))
        category.tap()
        let root = app.buttons["Clothing, Shoes & Accessories"].firstMatch
        XCTAssertTrue(root.waitForExistence(timeout: 60), "The category sheet lists eBay's tree")
        root.tap()
        XCTAssertTrue(app.buttons["Men"].firstMatch.waitForExistence(timeout: 10), "Categories open one level at a time")
        keep(app, "eBay categories")
        app.buttons["Cancel"].firstMatch.tap()
    }

    private func keep(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}

/// Actual automation views with sandbox-only terminal history. These tests
/// never toggle a rule, send an offer, or check a live marketplace result.
@MainActor
final class AutomationOffersSandboxTests: XCTestCase {
    func testOfferOverviewAndEvidence() {
        reviewOffers(accessibilityText: false)
    }

    func testOfferOverviewAndEvidenceWithAccessibilityText() {
        reviewOffers(accessibilityText: true)
    }

    private func reviewOffers(accessibilityText: Bool) {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        if accessibilityText {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName",
                                    UIContentSizeCategory.accessibilityExtraExtraExtraLarge.rawValue]
        }
        launchSandbox(app)
        let tab = app.tabBars.buttons["Automations"]
        XCTAssertTrue(tab.waitForExistence(timeout: 30))
        tab.tap()
        XCTAssertTrue(app.navigationBars["Automations"].waitForExistence(timeout: 15))

        let menu = app.buttons["More for Likers get 10% off"]
        reveal(menu, app: app)
        let size = accessibilityText ? "Accessibility text" : "Default text"
        keep(app, "Offer automation overview - \(size)")
        menu.tap()
        let details = app.buttons["Listings and activity"]
        XCTAssertTrue(details.waitForExistence(timeout: 10))
        details.tap()
        XCTAssertTrue(app.navigationBars["Likers get 10% off"].waitForExistence(timeout: 15))

        let accepted = app.staticTexts["Request accepted · delivery unverified"]
        reveal(accepted, app: app)
        XCTAssertTrue(app.staticTexts["sandbox-offer-1"].exists, "The sample listing ID remains visible to accessibility")
        keep(app, "Accepted offer evidence - \(size)")

        let legacy = app.staticTexts["Completed · delivery unverified"]
        reveal(legacy, app: app)
        XCTAssertTrue(app.staticTexts["sandbox-offer-2"].exists)
        keep(app, "Legacy offer evidence - \(size)")

        let totals = app.staticTexts["2 actions completed · 0 skipped · 0 not completed"]
        reveal(totals, app: app)
        keep(app, "Offer action totals - \(size)")
        app.navigationBars["Likers get 10% off"].buttons["Done"].tap()
        XCTAssertTrue(app.navigationBars["Automations"].waitForExistence(timeout: 10), "Done returns to the overview")
    }

    private func reveal(_ element: XCUIElement, app: XCUIApplication) {
        // Hittable can mean only one line is onscreen. Screenshots need the
        // whole label, especially the multiline totals at accessibility sizes.
        var viewport = app.frame.insetBy(dx: 8, dy: 8)
        let navigationBar = app.navigationBars.firstMatch
        if navigationBar.exists {
            let top = navigationBar.frame.maxY + 8
            viewport.size.height -= top - viewport.minY
            viewport.origin.y = top
        }
        let tabBar = app.tabBars.firstMatch
        if tabBar.exists, tabBar.isHittable {
            viewport.size.height = min(viewport.maxY, tabBar.frame.minY - 8) - viewport.minY
        }
        for _ in 0..<20 {
            if element.waitForExistence(timeout: 1), element.isHittable,
               viewport.contains(element.frame) { return }
            // Small drags avoid skipping a tall label. Reverse only when the
            // target has moved above the unobscured content area.
            let scrollDown = element.exists && !element.frame.isEmpty
                && element.frame.minY < viewport.minY
            let startY = scrollDown ? 0.45 : 0.75
            let endY = scrollDown ? 0.70 : 0.50
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: startY))
                .press(forDuration: 0.05, thenDragTo:
                    app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: endY)))
        }
        XCTAssertTrue(element.isHittable && viewport.contains(element.frame),
                      "Expected offer content must be fully visible without horizontal scrolling")
    }

    private func keep(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}

/// iPad only: the inventory list stays beside the open listing, and unsaved
/// edits come back after the seller opens another listing. CI runs this on an
/// iPad simulator; on iPhone it is skipped.
@MainActor
final class IPadSandboxTests: XCTestCase {
    func testListStaysBesideEditorAndKeepsUnsavedEdits() throws {
        try XCTSkipUnless(UIDevice.current.userInterfaceIdiom == .pad, "Runs on iPad")
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        app.launch()
        // The app opens on the Assistant. On iPad the tabs may draw as a
        // sidebar, so find the Inventory tab wherever it is, then wait for Inventory itself.
        let inventoryTab = app.buttons["Inventory"].firstMatch
        XCTAssertTrue(inventoryTab.waitForExistence(timeout: 90),
                      "Sandbox sign-in must finish before interacting with the workspace")
        inventoryTab.tap()
        XCTAssertTrue(app.buttons["New listing"].firstMatch.waitForExistence(timeout: 30),
                      "Sandbox sign-in must finish before interacting with the workspace")
        XCTAssertTrue(app.staticTexts["No listing open"].waitForExistence(timeout: 15),
                      "With nothing open, the editor column says so")

        openNewListing(app)
        XCTAssertTrue(app.navigationBars["Inventory"].exists, "The list stays on screen beside the editor")
        let name = "iPad draft \(UUID().uuidString.prefix(8))"
        enterListingTitle(name, app: app)
        XCTAssertTrue(app.navigationBars[name].waitForExistence(timeout: 15))
        app.navigationBars[name].buttons["Save"].tap()
        expectation(for: NSPredicate(format: "enabled == false"), evaluatedWith: app.navigationBars[name].buttons["Save"])
        waitForExpectations(timeout: 30)

        // Edit without saving, then open another listing.
        let edited = "\(name) edited"
        enterListingTitle(edited, app: app)
        XCTAssertTrue(app.navigationBars[edited].waitForExistence(timeout: 15))
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "iPad editor beside the list"
        screenshot.lifetime = .keepAlways
        add(screenshot)
        openNewListing(app)

        let row = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", name)).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 30))
        row.tap()
        let notice = app.alerts["Unsaved changes"]
        XCTAssertTrue(notice.waitForExistence(timeout: 30), "Reopening says the unsaved edits came back")
        notice.buttons["OK"].tap()
        XCTAssertTrue(app.navigationBars[edited].waitForExistence(timeout: 15), "The unsaved title is back")
    }
}

fileprivate extension XCTestCase {
    @MainActor
    func enterListingTitle(_ text: String, app: XCUIApplication) {
        // The editor's Title field sits below photos and seller inputs.
        let field = app.descendants(matching: .any).matching(identifier: "listing-title").firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 15), "The new draft must open in the editor")
        for _ in 0..<12 where !field.isHittable { app.scrollViews.firstMatch.swipeUp() }
        XCTAssertTrue(field.isHittable)
        // A tap during a scroll can miss focus; tap until the field has it.
        // Tapping the right end puts the cursor after any existing text.
        for _ in 0..<3 {
            field.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
            dismissKeyboardIntroduction(app)
            if app.keyboards.firstMatch.waitForExistence(timeout: 3),
               (field.value(forKey: "hasKeyboardFocus") as? Bool) == true { break }
        }
        // The hosted API names a new draft "New Listing"; replace that.
        if let current = field.value as? String, !current.isEmpty, current != "Title" {
            field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: current.count))
        }
        field.typeText(text)
        let done = app.buttons["Done"].firstMatch
        if app.keyboards.firstMatch.exists, done.waitForExistence(timeout: 5) { done.tap() }
        // Seller-input steps after this scroll back up to their own fields.
    }

    /// Signs in and opens `tab`; nil stays on the tab the app opens on, the Assistant.
    @MainActor
    func launchSandbox(_ app: XCUIApplication, tab: String? = "Inventory") {
        app.launch()
        // launch() can return while the disposable account is still signing
        // in on a cold CI simulator. Screen waits start at workspace readiness.
        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 90),
                      "Sandbox sign-in must finish before interacting with the workspace")
        if let tab { app.tabBars.buttons[tab].tap() }
    }

    @MainActor
    func openNewListing(_ app: XCUIApplication) {
        // One tap creates an untitled draft and opens it (no title sheet),
        // so tap once: a second tap would make a second draft.
        let create = app.buttons["New listing"].firstMatch
        XCTAssertTrue(create.waitForExistence(timeout: 30))
        XCTAssertTrue(create.isHittable)
        create.tap()
        // The editor shows the draft's title, or "Listing" while it has none.
        let editor = app.navigationBars.matching(NSPredicate(format: "identifier IN %@", ["Listing", "New Listing"])).firstMatch
        XCTAssertTrue(editor.waitForExistence(timeout: 15), "New listing must open the untitled draft in the editor")
    }

    @MainActor
    func dismissKeyboardIntroduction(_ app: XCUIApplication) {
        // A fresh simulator can present QuickPath onboarding over the first
        // keyboard. Scope Continue to that system view, never an app action.
        let introduction = app.otherElements["UIContinuousPathIntroductionView"]
        if introduction.waitForExistence(timeout: 5) {
            introduction.buttons["Continue"].tap()
            expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: introduction)
            waitForExpectations(timeout: 5)
        }
    }
}
