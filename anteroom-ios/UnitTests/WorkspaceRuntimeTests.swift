import XCTest
@testable import Anteroom

@MainActor
final class WorkspaceRuntimeTests: XCTestCase {
    private nonisolated static let fullRuntime = WorkspaceRuntime(
        mode: "hosted",
        features: ["managed_ai", "chat_attachments", "account_deletion"],
        views: ["assistant", "marketplaces", "automations"],
        marketplaceForms: ["ebay"],
        actionMarketplaces: ["ebay"]
    )

    func testInitialLoadSelectsAssistantWhenOffered() async {
        let model = WorkspaceRuntimeModel { _ in Self.fullRuntime }
        await model.load()
        XCTAssertEqual(model.runtime, Self.fullRuntime)
        XCTAssertEqual(model.selection, .assistant)
        XCTAssertFalse(model.isUnavailable)
        XCTAssertNil(model.error)
    }

    func testFailedLoadRecordsErrorAndRetryRecovers() async {
        let attempts = Counter()
        let model = WorkspaceRuntimeModel { fresh in
            let count = await attempts.next()
            if count == 1 {
                XCTAssertFalse(fresh)
                throw APIError(status: 503, message: "Server is starting.")
            }
            XCTAssertTrue(fresh)
            return Self.fullRuntime
        }

        await model.load(fresh: false)
        XCTAssertNil(model.runtime)
        XCTAssertEqual(model.error, "Server is starting.")
        XCTAssertFalse(model.isUnavailable)

        await model.load(fresh: true)
        XCTAssertEqual(model.runtime, Self.fullRuntime)
        XCTAssertEqual(model.selection, .assistant)
        XCTAssertNil(model.error)
        XCTAssertFalse(model.isUnavailable)
    }

    func testContinueUnavailableOpensCoreTabsAndRecoversWithoutReplacingSelection() async {
        let model = WorkspaceRuntimeModel { fresh in
            XCTAssertTrue(fresh)
            return Self.fullRuntime
        }

        model.continueUnavailable()
        XCTAssertEqual(model.runtime, .unavailable)
        XCTAssertEqual(model.selection, .inventory)
        XCTAssertTrue(model.isUnavailable)

        model.selection = .more
        await model.recoverIfUnavailable(reportError: true)
        XCTAssertEqual(model.runtime, Self.fullRuntime)
        XCTAssertEqual(model.selection, .more, "Recovering optional tabs keeps the seller on their current tab")
        XCTAssertFalse(model.isUnavailable)
        XCTAssertNil(model.recoveryError)
    }

    func testFailedRecoveryKeepsUnavailableAndReportsErrorOnlyWhenRequested() async {
        let model = WorkspaceRuntimeModel { _ in
            throw APIError(status: 503, message: "Still offline.")
        }

        model.continueUnavailable()
        await model.recoverIfUnavailable(reportError: false)
        XCTAssertTrue(model.isUnavailable)
        XCTAssertEqual(model.runtime, .unavailable)
        XCTAssertNil(model.recoveryError)

        await model.recoverIfUnavailable(reportError: true)
        XCTAssertTrue(model.isUnavailable)
        XCTAssertEqual(model.recoveryError, "Still offline.")
    }
}

private actor Counter {
    private var count = 0
    func next() -> Int {
        count += 1
        return count
    }
}
