import UIKit
import XCTest

@MainActor
private func openMorePage(_ name: String, app: XCUIApplication) {
    app.tabBars.buttons["More"].tap()
    if app.navigationBars[name].isHittable { return }
    let destination = app.buttons[name].firstMatch
    if !destination.isHittable {
        let back = app.navigationBars.buttons["More"].firstMatch
        XCTAssertTrue(back.waitForExistence(timeout: 10))
        back.tap()
    }
    XCTAssertTrue(destination.waitForExistence(timeout: 10))
    destination.tap()
}

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
        // Inventory reopens on Active; the saved draft is under Draft.
        showDrafts(app)
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
        showDrafts(app)
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
        let more = app.tabBars.buttons["More"]
        XCTAssertTrue(more.waitForExistence(timeout: 30))
        more.tap()
        let settings = app.buttons["Settings"].firstMatch
        XCTAssertTrue(settings.waitForExistence(timeout: 10))
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

        let activeTab = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Active,'")).firstMatch
        XCTAssertTrue(activeTab.waitForExistence(timeout: 30), "Inventory shows its status tabs")
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

        app.tabBars.buttons["Marketplaces"].tap()
        let connect = app.buttons["Connect eBay"]
        XCTAssertTrue(connect.waitForExistence(timeout: 30), "Marketplaces offers to connect eBay")
        keep(app, "Marketplaces eBay")
        connect.tap()
        XCTAssertTrue(app.switches.matching(NSPredicate(format: "label BEGINSWITH 'Buyer messages'")).firstMatch.waitForExistence(timeout: 10),
                      "The connection sheet lists the tools to authorize")
        keep(app, "eBay connection")
        app.buttons["Close"].tap()

        app.tabBars.buttons["More"].tap()
        let settings = app.buttons["Settings"].firstMatch
        XCTAssertTrue(settings.waitForExistence(timeout: 10))
        settings.tap()

        let listings = app.buttons["Listings"].firstMatch
        XCTAssertTrue(listings.waitForExistence(timeout: 10))
        listings.tap()
        XCTAssertTrue(app.switches.matching(NSPredicate(format: "label CONTAINS 'eBay'")).firstMatch.waitForExistence(timeout: 20),
                      "Where you list shows the marketplaces")
        keep(app, "Settings Listings")
        app.navigationBars.buttons.firstMatch.tap()
        app.buttons["AI providers"].firstMatch.tap()
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
        openMorePage("Settings", app: app)
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
        let activeTab = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Active,'")).firstMatch
        XCTAssertTrue(activeTab.waitForExistence(timeout: 30), "Inventory has loaded")
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
        // The Levi's sample is live, under Active, where Inventory opens.
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
    func testOfferActivityRefreshPreservesCustomDates() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        openMorePage("Automations", app: app)
        let period = app.buttons["offer-activity-period"]
        XCTAssertTrue(period.waitForExistence(timeout: 20))
        period.tap()
        app.buttons["Custom dates"].tap()
        // Compact DatePicker labels are sibling text in the cell; the
        // displayed date value belongs to the picker's child button.
        let from = app.cells.containing(.staticText, identifier: "From").datePickers.buttons.firstMatch
        let through = app.cells.containing(.staticText, identifier: "Through").datePickers.buttons.firstMatch
        XCTAssertTrue(from.waitForExistence(timeout: 10))
        XCTAssertTrue(through.exists)
        let fromValue = from.value as? String
        let throughValue = through.value as? String
        XCTAssertNotNil(fromValue)
        XCTAssertNotNil(throughValue)
        let before = XCTAttachment(screenshot: app.screenshot())
        before.name = "Offer activity custom dates before refresh"
        before.lifetime = .keepAlways
        add(before)
        // Drag from the top of the list to trigger the page's actual refresh.
        let list = app.collectionViews.firstMatch
        XCTAssertTrue(list.waitForExistence(timeout: 10))
        list.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.15))
            .press(forDuration: 0.05, thenDragTo:
                list.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.85)))
        XCTAssertTrue(from.waitForExistence(timeout: 20), "Refreshing must keep the selected custom period")
        XCTAssertEqual(from.value as? String, fromValue)
        XCTAssertEqual(through.value as? String, throughValue)
        XCTAssertTrue(period.exists)
        let after = XCTAttachment(screenshot: app.screenshot())
        after.name = "Offer activity custom dates after refresh"
        after.lifetime = .keepAlways
        add(after)
    }

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
        openMorePage("Automations", app: app)
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
        for _ in 0..<40 {
            if element.waitForExistence(timeout: 1), element.isHittable,
               viewport.contains(element.frame) { return }
            // Dynamic Type and the offer-activity section can place the target
            // beyond the lazy list's materialized rows. Move farther while it
            // is absent; keep overlapping viewports so a row cannot be skipped.
            // Once present, small drags fit the entire label into the viewport.
            let absent = !element.exists
            let scrollDown = !absent && !element.frame.isEmpty
                && element.frame.minY < viewport.minY
            let startY = scrollDown ? 0.45 : (absent ? 0.80 : 0.75)
            let endY = scrollDown ? 0.70 : (absent ? 0.25 : 0.50)
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
        // Match launchSandbox's bounded selection wait: a startup tap can
        // arrive before the adaptive tab bar has finished settling.
        for _ in 0..<3 where !inventoryTab.isSelected {
            inventoryTab.tap()
            let selected = XCTNSPredicateExpectation(predicate: NSPredicate(format: "selected == true"), object: inventoryTab)
            _ = XCTWaiter().wait(for: [selected], timeout: 3)
        }
        XCTAssertTrue(inventoryTab.isSelected, "The Inventory tab opens before looking for its controls")
        XCTAssertTrue(app.buttons["New listing"].firstMatch.waitForExistence(timeout: 30),
                      "The selected Inventory tab must show New listing")
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
        guard let tab else { return }
        // A tap that lands while the tab bar is still settling can be dropped,
        // which left the app on Assistant; tap until the tab is selected.
        // Two buttons can match while the bar settles ("Multiple matching
        // elements found"), so take the first.
        let button = app.tabBars.buttons[tab].firstMatch
        for _ in 0..<3 where !button.isSelected {
            button.tap()
            let selected = XCTNSPredicateExpectation(predicate: NSPredicate(format: "selected == true"), object: button)
            _ = XCTWaiter().wait(for: [selected], timeout: 3)
        }
        XCTAssertTrue(button.isSelected, "The \(tab) tab opens")
    }

    /// Inventory opens on Active; the sample drafts and any new draft are under Draft.
    @MainActor
    func showDrafts(_ app: XCUIApplication) {
        let drafts = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Draft,'")).firstMatch
        XCTAssertTrue(drafts.waitForExistence(timeout: 30), "Inventory shows its status tabs")
        drafts.tap()
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
        // A busy simulator can take a while to create and open the draft.
        XCTAssertTrue(editor.waitForExistence(timeout: 40), "New listing must open the untitled draft in the editor")
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

/// The polished screens in light and dark, and Inventory at an accessibility
/// text size, kept as screenshots for review. Ends on the System appearance.
@MainActor
final class PolishScreenshotTests: XCTestCase {
    func testPolishedScreensInLightAndDark() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        for theme in ["Light", "Dark"] {
            choose(theme, app)
            app.tabBars.buttons["Inventory"].tap()
            XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Active,'")).firstMatch.waitForExistence(timeout: 30))
            keep(app, "\(theme): Inventory")
            app.buttons["Sort listings, Recent activity"].firstMatch.tap()
            XCTAssertTrue(app.buttons["Date created"].waitForExistence(timeout: 10), "Sort by lists the fields")
            keep(app, "\(theme): Sort menu")
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.6)).tap()
            app.tabBars.buttons["Analytics"].tap()
            XCTAssertTrue(app.staticTexts["Items sold"].waitForExistence(timeout: 30))
            keep(app, "\(theme): Sales records")
            app.buttons["Inventory analytics"].tap()
            XCTAssertTrue(app.staticTexts["Median days to sell"].waitForExistence(timeout: 30))
            keep(app, "\(theme): Inventory analytics")
            openMorePage("Automations", app: app)
            _ = app.staticTexts["Your automations"].waitForExistence(timeout: 30)
            keep(app, "\(theme): Automations")
            openMorePage("Settings", app: app)
            XCTAssertTrue(app.staticTexts["About"].waitForExistence(timeout: 10) || app.buttons["What's new"].exists)
            keep(app, "\(theme): Settings")
        }
        choose("System", app)
        app.terminate()

        // The largest text sizes keep whole words on the status tabs and rows.
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityL"]
        launchSandbox(app)
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Active,'")).firstMatch.waitForExistence(timeout: 30))
        keep(app, "Accessibility L: Inventory")
    }

    private func choose(_ theme: String, _ app: XCUIApplication) {
        openMorePage("Settings", app: app)
        let picker = app.buttons["appearance-picker"]
        XCTAssertTrue(picker.waitForExistence(timeout: 15))
        expectation(for: NSPredicate(format: "enabled == true"), evaluatedWith: picker)
        waitForExpectations(timeout: 15)
        picker.tap()
        app.buttons[theme].tap()
        expectation(for: NSPredicate(format: "value == %@ AND enabled == true", theme), evaluatedWith: picker)
        waitForExpectations(timeout: 15)
    }

    private func keep(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}

/// The listing's core flows: a new draft left untouched leaves nothing behind,
/// Generate listing sits under the photos and the reply is a summary, and a
/// marketplace tab carries its own List button behind the confirmation.
@MainActor
final class CoreFlowSandboxTests: XCTestCase {
    func testHidingANewDraftKeepsUnsavedEdits() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        openNewListing(app)
        let title = "Unsaved draft \(UUID().uuidString.prefix(8))"
        enterListingTitle(title, app: app)
        XCTAssertTrue(app.navigationBars[title].waitForExistence(timeout: 15))
        let more = app.tabBars.buttons["More"]
        for _ in 0..<3 where !more.isSelected { more.tap() }
        XCTAssertTrue(more.isSelected)
        XCTAssertTrue(app.navigationBars["More"].waitForExistence(timeout: 15))
        let inventory = app.tabBars.buttons["Inventory"]
        for _ in 0..<3 where !inventory.isSelected { inventory.tap() }
        XCTAssertTrue(inventory.isSelected)
        XCTAssertTrue(app.navigationBars[title].waitForExistence(timeout: 15), "The unsaved title is restored")
        XCTAssertTrue(app.staticTexts["Unsaved changes"].exists)
    }

    func testBackingOutOfANewDraftLeavesNothing() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        let placeholder = app.staticTexts.matching(identifier: "New Listing")
        XCTAssertTrue(app.buttons["New listing"].firstMatch.waitForExistence(timeout: 30))
        let before = placeholder.count
        openNewListing(app)
        XCTAssertFalse(app.staticTexts["Saved"].exists, "A new draft says nothing about saving before any edit")
        XCTAssertFalse(app.staticTexts["Unsaved changes"].exists)
        keep(app, "New draft")
        app.navigationBars.buttons.firstMatch.tap()
        XCTAssertTrue(app.navigationBars["Inventory"].waitForExistence(timeout: 15))
        // Pull to refresh so the list is the server's, not the phone's.
        sleep(2)
        app.collectionViews.firstMatch.swipeDown()
        sleep(2)
        XCTAssertEqual(placeholder.count, before, "Backing out of an untouched draft removes it")
    }

    func testGenerateFromTheEditorShowsASummary() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        showDrafts(app)
        let listing = app.staticTexts["Sandbox photos only"].firstMatch
        XCTAssertTrue(listing.waitForExistence(timeout: 30))
        let generate = app.buttons["generate-listing"]
        // A tap that lands while the list is still settling can miss; open it again if so.
        for _ in 0..<3 where !generate.waitForExistence(timeout: 20) {
            if listing.exists && listing.isHittable { listing.tap() }
        }
        XCTAssertTrue(generate.exists, "A draft with photos and no copy offers Generate listing")
        // The editor ignores taps while it reloads the listing; wait until it takes them.
        expectation(for: NSPredicate(format: "enabled == true AND hittable == true"), evaluatedWith: generate)
        waitForExpectations(timeout: 30)
        keep(app, "Generate listing under photos")
        let assistant = app.navigationBars["Listing assistant"]
        for _ in 0..<3 where !assistant.exists {
            if generate.exists && generate.isHittable { generate.tap() }
            _ = assistant.waitForExistence(timeout: 10)
        }
        XCTAssertTrue(assistant.exists, "Generate listing opens the assistant")
        // No confirmation: there is no copy to replace.
        let review = app.buttons["Review in editor"]
        XCTAssertTrue(review.waitForExistence(timeout: 180), "The reply offers the editor")
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label CONTAINS '```' OR label CONTAINS '\"title\"'")).firstMatch.exists,
                       "The generated JSON is never shown")
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'filled.'")).firstMatch.exists)
        // The editor's own Generate listing sits under the sheet, so check for Regenerate only.
        XCTAssertTrue(app.buttons["Regenerate"].exists, "After a first run the assistant offers Regenerate")
        keep(app, "Assistant summary")
        review.tap()
        XCTAssertTrue(assistant.waitForNonExistence(timeout: 15), "Review closes the assistant sheet")
        // The editor reloads the saved draft on return; then it has copy and no Generate listing.
        expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: app.buttons["generate-listing"])
        waitForExpectations(timeout: 30)
    }

    func testMarketplaceTabHasItsOwnListButton() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        let listing = app.staticTexts["Free People S Pink Floral Midi Dress"].firstMatch
        XCTAssertTrue(listing.waitForExistence(timeout: 30))
        let formMenu = app.buttons["Form, General"]
        for _ in 0..<3 where !formMenu.waitForExistence(timeout: 20) {
            if listing.exists && listing.isHittable { listing.tap() }
        }
        XCTAssertTrue(formMenu.exists)
        expectation(for: NSPredicate(format: "enabled == true"), evaluatedWith: formMenu)
        waitForExpectations(timeout: 30)
        formMenu.tap()
        keep(app, "Form menu")
        // The sample dress is live on Poshmark and Etsy, not on eBay.
        let ebay = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'eBay'")).firstMatch
        XCTAssertTrue(ebay.waitForExistence(timeout: 10))
        ebay.tap()
        let list = app.buttons["List on eBay"]
        XCTAssertTrue(list.waitForExistence(timeout: 60), "The marketplace tab has its own List button")
        keep(app, "eBay tab action")
        if list.isEnabled {
            list.tap()
            // The confirmation names the marketplace; Cancel leaves everything as it was.
            let cancel = app.buttons["Cancel"].firstMatch
            XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'on eBay?'")).firstMatch.waitForExistence(timeout: 10))
            cancel.tap()
        }
    }

    private func keep(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}

/// The camera stays open between shots. On the sandbox the simulator has no
/// camera, so Debug builds stand in a generated photo for each shot.
@MainActor
final class CameraSandboxTests: XCTestCase {
    func testCameraKeepsShootingAndAddsTheKeptShotsInOrder() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        openNewListing(app)
        open(app.buttons["Take listing photos"].firstMatch, until: app.buttons["camera-shutter"])
        shoot(3, app: app)
        keep(app, "Camera tray with three shots")
        app.buttons["Discard photo 2"].tap()
        XCTAssertTrue(app.staticTexts["2 photos"].waitForExistence(timeout: 5), "Discarding a shot updates the count")
        let done = app.buttons["camera-done"]
        XCTAssertEqual(done.label, "Add 2 photos")
        done.tap()
        // Both kept shots upload to the draft.
        XCTAssertTrue(app.staticTexts["2 of 20 photos"].waitForExistence(timeout: 30), "Done adds every kept shot")
        keep(app, "Listing photos after the camera")
    }

    func testBulkListingTakesPhotosAndOpensFiles() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        open(app.buttons["Bulk listing"].firstMatch, until: app.buttons["bulk-camera"])
        XCTAssertTrue(app.buttons["bulk-choose-files"].exists, "Bulk listing offers Files")
        keep(app, "Bulk listing photo sources")
        open(app.buttons["bulk-camera"], until: app.buttons["camera-shutter"])
        shoot(2, app: app)
        app.buttons["camera-done"].tap()
        XCTAssertTrue(app.staticTexts["0 items · 2 photos to group"].waitForExistence(timeout: 15),
                      "Camera shots join the photos to group")
        keep(app, "Bulk listing after the camera")
    }

    /// A tap on a busy simulator can be dropped; tap until the next screen shows.
    private func open(_ button: XCUIElement, until shown: XCUIElement) {
        XCTAssertTrue(button.waitForExistence(timeout: 30))
        for _ in 0..<3 where !shown.exists {
            if button.exists && button.isHittable { button.tap() }
            _ = shown.waitForExistence(timeout: 20)
        }
        XCTAssertTrue(shown.exists, "\(button.label) opens")
    }

    private func shoot(_ count: Int, app: XCUIApplication) {
        let shutter = app.buttons["camera-shutter"]
        XCTAssertTrue(shutter.waitForExistence(timeout: 10))
        for number in 1...count {
            shutter.tap()
            let label = number == 1 ? "1 photo" : "\(number) photos"
            XCTAssertTrue(app.staticTexts[label].waitForExistence(timeout: 10), "The camera stays open after shot \(number)")
        }
    }

    private func keep(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}

/// Opening history (like the assistant and marketplaces) with unsaved edits
/// saves them first instead of asking the seller to.
@MainActor
final class EditorAutosaveSandboxTests: XCTestCase {
    func testOpeningHistorySavesUnsavedEditsFirst() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        openNewListing(app)
        let name = "Autosaved draft \(UUID().uuidString.prefix(8))"
        enterListingTitle(name, app: app)
        let bar = app.navigationBars[name]
        XCTAssertTrue(bar.waitForExistence(timeout: 15))
        XCTAssertTrue(bar.buttons["Save"].isEnabled, "The title is not saved yet")
        let actions = app.buttons["Listing actions"]
        XCTAssertTrue(actions.isEnabled, "Listing actions stay available with unsaved edits")
        let history = app.buttons["Listing history"]
        for _ in 0..<3 where !history.exists {
            actions.tap()
            _ = history.waitForExistence(timeout: 5)
        }
        history.tap()
        XCTAssertTrue(app.navigationBars["Listing history"].waitForExistence(timeout: 30), "History opens after saving")
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "History opened after saving"
        attachment.lifetime = .keepAlways
        add(attachment)
        app.buttons["Done"].firstMatch.tap()
        expectation(for: NSPredicate(format: "enabled == false"), evaluatedWith: app.navigationBars[name].buttons["Save"])
        waitForExpectations(timeout: 30)
    }
}
