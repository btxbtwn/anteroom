import XCTest
@testable import Anteroom

/// A server restarting during a deploy answers 503 for a while; reads wait it
/// out, and nothing that changes data is ever sent twice.
final class ReadRetryTests: XCTestCase {
    private var signIn: StubSignIn!
    private var clock: TestClock!
    private var api: AnteroomAPI!

    override func setUp() {
        super.setUp()
        signIn = StubSignIn()
        clock = TestClock()
        api = signIn.api(readRetry: clock.retry)
    }

    private static let unavailable = StubProtocol.Reply(status: 503, body: "<html>Service Unavailable</html>")

    // MARK: Reads recover by themselves

    func testAReadWaitsOutAServerThatIsRestarting() async throws {
        StubProtocol.answer(Self.unavailable, .init(status: 502, body: ""), .init(status: 504, body: ""),
                            .init(status: 200, body: #"[{"id":"a","status":"draft"}]"#))
        let rows = try await api.listings()
        XCTAssertEqual(rows.map(\.id), ["a"])
        XCTAssertEqual(StubProtocol.requests.count, 4)
        XCTAssertEqual(clock.waits, [0.5, 1, 2], "Each wait doubles")
    }

    func testAReadRetriesAConnectionThatFailed() async throws {
        StubProtocol.answer(.init(status: 0, body: "", failure: .cannotConnectToHost),
                            .init(status: 0, body: "", failure: .networkConnectionLost),
                            .init(status: 200, body: "[]"))
        _ = try await api.listings()
        XCTAssertEqual(StubProtocol.requests.count, 3)
    }

    func testRetryAfterSetsTheWait() async throws {
        StubProtocol.answer(.init(status: 503, body: "", headers: ["Retry-After": "7"]), .init(status: 200, body: "[]"))
        _ = try await api.listings()
        XCTAssertEqual(clock.waits, [7])
    }

    func testRetryAfterAcceptsAnHTTPDate() {
        let now = Date(timeIntervalSince1970: 1_000_000)
        let later = "Mon, 12 Jan 1970 13:47:00 GMT" // 1_000_020
        XCTAssertEqual(ReadRetry.seconds(retryAfter: later, now: now), 20)
        XCTAssertEqual(ReadRetry.seconds(retryAfter: "-3", now: now), 0)
        XCTAssertNil(ReadRetry.seconds(retryAfter: "soon", now: now))
        XCTAssertNil(ReadRetry.seconds(retryAfter: nil, now: now))
    }

    // MARK: A real outage still shows an error

    func testAnOutageShowsItsErrorOnceTheBudgetIsSpent() async {
        StubProtocol.answer(Self.unavailable)
        do {
            _ = try await api.listings()
            XCTFail("Expected an error")
        } catch let error as APIError {
            XCTAssertEqual(error.status, 503)
        } catch {
            XCTFail("Unexpected \(error)")
        }
        let waited = clock.waits.reduce(0, +)
        XCTAssertLessThanOrEqual(waited, ReadRetry.standard.budget)
        XCTAssertGreaterThan(waited, ReadRetry.standard.budget - ReadRetry.standard.longestDelay)
        XCTAssertEqual(clock.waits.max(), ReadRetry.standard.longestDelay, "Waits stop growing at the longest delay")
        XCTAssertEqual(StubProtocol.requests.count, clock.waits.count + 1)
    }

    func testARetryAfterPastTheBudgetShowsTheErrorNow() async {
        StubProtocol.answer(.init(status: 503, body: "", headers: ["Retry-After": "600"]))
        _ = try? await api.listings()
        XCTAssertEqual(StubProtocol.requests.count, 1)
        XCTAssertEqual(clock.waits, [])
    }

    func testOtherErrorsAreNotRetried() async {
        for status in [400, 404, 409, 422, 500] {
            StubProtocol.answer(.init(status: status, body: #"{"detail":"No."}"#))
            _ = try? await api.listing("a")
            XCTAssertEqual(StubProtocol.requests.count, 1, "\(status) is the server's answer, not a restart")
        }
        XCTAssertEqual(clock.waits, [])
    }

    // MARK: Writes are never sent twice

    func testWritesAreNeverRetried() async {
        let writes: [(String, () async throws -> Void)] = [
            ("POST create", { _ = try await self.api.createListing() }),
            ("PUT save", { _ = try await self.api.saveListing("a", ListingSave(listing: [:], expectedRevisionID: "r1")) }),
            ("PATCH rename", { try await self.api.rename("a", title: "New") }),
            ("DELETE", { try await self.api.deleteListing("a") }),
            ("POST list on a marketplace", {
                try await self.api.listOnMarketplace("a", marketplace: "ebay", revisionID: "r1",
                                                     confirmation: .unitTestOnly(.list, ["ebay"]))
            }),
            ("POST delist", {
                _ = try await self.api.delist("a", marketplaces: ["ebay"], confirmation: .unitTestOnly(.delist, ["ebay"]))
            }),
        ]
        for (name, write) in writes {
            for reply in [Self.unavailable, .init(status: 0, body: "", failure: .networkConnectionLost)] {
                StubProtocol.answer(reply, .init(status: 200, body: "{}"))
                do {
                    try await write()
                    XCTFail("\(name) should fail")
                } catch {}
                XCTAssertEqual(StubProtocol.requests.count, 1, "\(name) may already have run, so it is sent once")
            }
        }
        XCTAssertEqual(clock.waits, [])
    }

    func testOnlyGETIsRetried() {
        for method in ["POST", "PUT", "PATCH", "DELETE", nil] {
            XCTAssertFalse(ReadRetry.retries(method: method, status: 503))
            XCTAssertFalse(ReadRetry.retries(method: method, error: URLError(.cannotConnectToHost)))
        }
        XCTAssertTrue(ReadRetry.retries(method: "GET", status: 503))
        XCTAssertFalse(ReadRetry.retries(method: "GET", error: URLError(.cancelled)))
        XCTAssertFalse(ReadRetry.retries(method: "GET", error: URLError(.badServerResponse)))
    }

    // MARK: Leaving the screen stops waiting

    func testCancellingStopsTheRetries() async throws {
        StubProtocol.answer(Self.unavailable)
        let started = DispatchSemaphore(value: 0)
        let proceed = DispatchSemaphore(value: 0)
        clock.onWait = {
            started.signal()
            proceed.wait()
        }
        let api = signIn.api(readRetry: clock.retry)
        let read = Task { try await api.listings() }
        await Task.detached { started.wait() }.value
        read.cancel()
        proceed.signal()
        do {
            _ = try await read.value
            XCTFail("Expected cancellation")
        } catch is CancellationError {
        } catch {
            XCTFail("Unexpected \(error)")
        }
        XCTAssertEqual(StubProtocol.requests.count, 1, "Nothing is sent after the screen goes away")
    }
}
