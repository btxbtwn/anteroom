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

    func testListingRunReadsTheScheduleAgainAfterSavingAChange() async throws {
        StubProtocol.answer([{ request in
            switch request.url?.path {
            case "/api/runtime":
                return .init(status: 200, body: #"{"mode":"hosted","features":["generation"],"views":[],"marketplace_forms":[],"action_marketplaces":[]}"#)
            case "/api/status":
                return .init(status: 200, body: #"{"provider_configured":true}"#)
            case "/api/conversations/listing/activity":
                return .init(status: 200, body: #"{"busy":false,"items":[]}"#)
            case "/api/conversations/listing":
                // Saving the change unscheduled the draft on the server.
                return .init(status: 200, body: #"{"id":"listing","status":"draft","created_at":"2026-11-25T10:00:00Z","updated_at":"2026-11-25T10:05:00Z","scheduled_publish_at":null}"#)
            case "/api/conversations/listing/generate":
                return .init(status: 200, body: "")
            default:
                return .init(status: 200, body: "[]")
            }
        }])
        let scheduled = Date(timeIntervalSince1970: 1_800_000_000)
        let model = ListingAssistantModel(api: StubSignIn().api(), listingID: "listing", scheduledPublishAt: scheduled)
        await model.load()
        XCTAssertEqual(model.scheduledPublishAt, scheduled)

        await model.run(.generate)

        XCTAssertNil(model.scheduledPublishAt)
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
