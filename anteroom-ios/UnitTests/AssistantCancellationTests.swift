import XCTest
@testable import Anteroom

@MainActor
final class AssistantCancellationTests: XCTestCase {
    func testListingStreamCancellationDoesNotReportFailure() async throws {
        StubProtocol.answer([{ request in
            switch request.url?.path {
            case "/api/runtime":
                return .init(status: 200, body: #"{"mode":"hosted","features":["generation"],"views":[],"marketplace_forms":[],"action_marketplaces":[]}"#)
            case "/api/status":
                return .init(status: 200, body: #"{"provider_configured":true}"#)
            case "/api/conversations/listing/activity":
                return .init(status: 200, body: #"{"busy":false,"items":[]}"#)
            case "/api/conversations/listing/generate":
                return .init(status: 0, body: "", failure: .cancelled)
            default:
                return .init(status: 200, body: "[]")
            }
        }])
        let model = ListingAssistantModel(api: StubSignIn().api(), listingID: "listing")
        await model.load()
        XCTAssertTrue(model.canStart)

        await model.run(.generate)

        XCTAssertNil(model.error)
        XCTAssertEqual(model.runsFailed, 0)
        XCTAssertEqual(model.runsFinished, 0)
        XCTAssertFalse(model.isWorking)
        XCTAssertTrue(model.needsReconnect)
        XCTAssertEqual(model.progress, "Reconnect to check the saved draft.")
        XCTAssertEqual(StubProtocol.requests.filter { $0.url?.path.hasSuffix("/generate") == true }.count, 1)
    }

    func testBusinessStreamCancellationDoesNotReconnect() async throws {
        StubProtocol.answer([{ request in
            if request.url?.path == "/api/assistant/messages/resume" {
                // A second attempt succeeds so a regression terminates and reports
                // the unexpected reconnect rather than leaving a retry task running.
                let attempts = StubProtocol.requests.filter { $0.url?.path == request.url?.path }.count
                if attempts == 1 { return .init(status: 0, body: "", failure: .cancelled) }
                return .init(status: 200, body: "")
            }
            return .init(status: 200, body: "[]")
        }])
        let model = BusinessAssistantModel(api: StubSignIn().api())
        await model.open("thread")
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .seconds(5))
        while model.busy, clock.now < deadline {
            try await Task.sleep(for: .milliseconds(10))
        }

        XCTAssertFalse(model.busy)
        XCTAssertNil(model.error)
        XCTAssertEqual(model.answersFailed, 0)
        XCTAssertEqual(StubProtocol.requests.filter { $0.url?.path == "/api/assistant/messages/resume" }.count, 1)
    }
}
