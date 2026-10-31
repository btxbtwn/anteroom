import XCTest

@MainActor
final class SandboxTests: XCTestCase {
    func testCreateAndReopenDraft() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["ANTEROOM_SANDBOX_URL"] = "http://localhost:4320"
        app.launch()
        let create = app.buttons["New listing"].firstMatch
        XCTAssertTrue(create.waitForExistence(timeout: 30))
        create.tap()
        let title = app.textFields["Listing title"].waitForExistence(timeout: 5) ? app.textFields["Listing title"] : app.textViews["Listing title"]
        XCTAssertTrue(title.waitForExistence(timeout: 10))
        title.tap()
        let name = "Simulator draft \(UUID().uuidString.prefix(8))"
        title.typeText(name)
        app.buttons["Create draft"].tap()
        XCTAssertTrue(app.navigationBars[name].waitForExistence(timeout: 15))
        app.terminate()
        app.launch()
        XCTAssertTrue(app.staticTexts[name].waitForExistence(timeout: 30))
    }
}
