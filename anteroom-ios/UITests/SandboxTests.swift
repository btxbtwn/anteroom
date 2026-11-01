import XCTest

@MainActor
final class SandboxTests: XCTestCase {
    func testCreateAndReopenDraft() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        let title = openNewListing(app)
        XCTAssertTrue(title.waitForExistence(timeout: 10))
        let name = "Simulator draft \(UUID().uuidString.prefix(8))"
        enterListingTitle(name, app: app)
        app.buttons["Create draft"].tap()
        XCTAssertTrue(app.navigationBars[name].waitForExistence(timeout: 15))
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
        _ = openNewListing(app)
        let name = "Seller inputs \(UUID().uuidString.prefix(8))"
        enterListingTitle(name, app: app)
        app.buttons["Create draft"].tap()
        XCTAssertTrue(app.navigationBars[name].waitForExistence(timeout: 15))

        enter("seller-notes", text: "Found at a local estate sale", app: app)
        enter("seller-flaws", text: "Small mark near hem", app: app)
        enter("seller-labels", text: "Spring, Ready", app: app)
        attach(app, name: "Seller notes flaws and labels")
        enter("seller-cog", text: "8.50", app: app)
        enter("seller-package", text: "12x9x3", app: app)
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
        expectValue("seller-package", "12x9x3", app: app)
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
        let usage = app.buttons["Usage"].firstMatch
        XCTAssertTrue(usage.waitForExistence(timeout: 10))
        usage.tap()
        XCTAssertTrue(app.staticTexts["ChatGPT"].waitForExistence(timeout: 30))
        keep(app, "Usage")
        app.navigationBars.buttons.firstMatch.tap()

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

/// The business assistant answers a starter question on the sandbox's fake
/// provider, streams it in, and keeps the conversation in Chats.
@MainActor
final class AssistantSandboxTests: XCTestCase {
    func testStarterQuestionIsAnsweredAndKept() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        launchSandbox(app)
        app.tabBars.buttons["Assistant"].tap()
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

        app.buttons["Chats"].tap()
        let thread = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Which brands'")).firstMatch
        XCTAssertTrue(thread.waitForExistence(timeout: 15), "Chats lists the conversation")
        keep(app, "Assistant chats")
        app.buttons["Done"].tap()

        app.terminate()
        launchSandbox(app)
        app.tabBars.buttons["Assistant"].tap()
        app.buttons["Chats"].tap()
        XCTAssertTrue(thread.waitForExistence(timeout: 20))
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

fileprivate extension XCTestCase {
    @MainActor
    func enterListingTitle(_ text: String, app: XCUIApplication) {
        // The sheet focuses this field automatically. Tapping it again can
        // open the AutoFill edit menu and intercept the next keyboard event.
        dismissKeyboardIntroduction(app)
        // SwiftUI's multiline input wrapper can report no keyboard focus or
        // value while its text view visibly has both. Send keyboard input to
        // the focused app; creation and reopen assertions verify the title.
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 10))
        app.typeText(text)
    }

    @MainActor
    func launchSandbox(_ app: XCUIApplication) {
        app.launch()
        // launch() can return while the disposable account is still signing
        // in on a cold CI simulator. Screen waits start at workspace readiness.
        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 90),
                      "Sandbox sign-in must finish before interacting with the workspace")
    }

    @MainActor
    func openNewListing(_ app: XCUIApplication) -> XCUIElement {
        // A vertical TextField can appear as either native input type.
        let title = app.descendants(matching: .any).matching(identifier: "new-listing-title").firstMatch
        for _ in 0..<2 {
            if title.exists { return title }
            let create = app.buttons["New listing"].firstMatch
            XCTAssertTrue(create.waitForExistence(timeout: 30))
            XCTAssertTrue(create.isHittable)
            create.tap()
            if title.waitForExistence(timeout: 10) { return title }
            // Only retry opening the sheet while still at Inventory. This
            // navigation has no write; callers submit Create draft once.
            XCTAssertTrue(app.navigationBars["Inventory"].exists)
        }
        XCTFail("New listing must open its title input before typing")
        return title
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
