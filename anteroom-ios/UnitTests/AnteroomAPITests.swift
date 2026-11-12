import XCTest
@testable import Anteroom

final class AnteroomAPITests: XCTestCase {
    private var signIn: StubSignIn!
    private var api: AnteroomAPI!

    override func setUp() {
        super.setUp()
        signIn = StubSignIn()
        api = signIn.api()
    }

    // MARK: Request building

    func testReadCarriesTokenAndPath() async throws {
        StubProtocol.answer(.init(status: 200, body: #"[{"id":"a","title":"Blue shirt","status":"draft","created_at":"2026-11-01T10:00:00","updated_at":"2026-11-01T10:00:00"}]"#))
        let rows = try await api.listings()
        XCTAssertEqual(rows.map(\.id), ["a"])
        let sent = try XCTUnwrap(StubProtocol.requests.first)
        XCTAssertEqual(sent.httpMethod, "GET")
        XCTAssertEqual(sent.url?.absoluteString, "https://anteroom.test/api/conversations")
        XCTAssertEqual(sent.value(forHTTPHeaderField: "Authorization"), "Bearer token-1")
        XCTAssertEqual(sent.value(forHTTPHeaderField: "Accept"), "application/json")
    }

    func testSaveSendsJSONBodyWithExpectedRevision() async throws {
        StubProtocol.answer(.init(status: 200, body: #"{"ok":true,"listing":{"title":"New"},"revision_id":"r2","etsy_eligible":true,"validation":{"valid":true,"errors":[],"warnings":[],"info":[],"can_send":true}}"#))
        _ = try? await api.saveListing("a", ListingSave(listing: ["title": .string("New")], expectedRevisionID: "r1"))
        let sent = try XCTUnwrap(StubProtocol.requests.first)
        XCTAssertEqual(sent.httpMethod, "PUT")
        XCTAssertEqual(sent.url?.path, "/api/conversations/a/listing")
        XCTAssertEqual(sent.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(sent.json?["expected_revision_id"] as? String, "r1")
        XCTAssertEqual((sent.json?["listing"] as? [String: Any])?["title"] as? String, "New")
    }

    func testCreateListingSendsNoTitleAndOptionalNotes() async throws {
        StubProtocol.answer(.init(status: 200, body: #"{"id":"n","title":"New Listing","status":"draft","created_at":"2026-11-01T10:00:00","updated_at":"2026-11-01T10:00:00","notes":""}"#))
        _ = try await api.createListing()
        let plain = try XCTUnwrap(StubProtocol.requests.last)
        XCTAssertEqual(plain.httpMethod, "POST")
        XCTAssertEqual(plain.url?.path, "/api/conversations")
        XCTAssertEqual(plain.json?.count, 0)

        StubProtocol.answer(.init(status: 200, body: #"{"id":"m","title":"New Listing","status":"draft","created_at":"2026-11-01T10:00:00","updated_at":"2026-11-01T10:00:00","notes":""}"#))
        _ = try await api.createListing(notes: #"{"sku":"BX-1"}"#)
        let withNotes = try XCTUnwrap(StubProtocol.requests.last)
        XCTAssertEqual(withNotes.json?["notes"] as? String, #"{"sku":"BX-1"}"#)
        XCTAssertNil(withNotes.json?["title"])
    }

    func testCreateListingCarriesTheBox() async throws {
        StubProtocol.answer(.init(status: 200, body: #"{"id":"n","title":"New Listing","status":"draft","created_at":"2026-11-01T10:00:00","updated_at":"2026-11-01T10:00:00","notes":""}"#))
        _ = try await api.createListing(boxID: "box-1")
        XCTAssertEqual(StubProtocol.requests.last?.json?["box_id"] as? String, "box-1")
    }

    func testReadMeasurementsSendsPhotosAndGarmentAsAForm() async throws {
        StubProtocol.answer(.init(status: 200, body: #"{"photos_read":1,"measurements":{"waist":{"status":"accepted","value":16,"reads":[16,16]}}}"#))
        let reading = try await api.readMeasurements(jpegs: [Data("one".utf8), Data("two".utf8)], garment: .pants)
        XCTAssertEqual(reading.photosRead, 1)
        XCTAssertEqual(reading.measurements["waist"]?.value, 16)
        let sent = try XCTUnwrap(StubProtocol.requests.last)
        XCTAssertEqual(sent.url?.path, "/api/measurements/read")
        XCTAssertTrue(sent.value(forHTTPHeaderField: "Content-Type")?.hasPrefix("multipart/form-data; boundary=") == true)
        let body = String(decoding: try XCTUnwrap(sent.httpBody), as: UTF8.self)
        XCTAssertEqual(body.components(separatedBy: "name=\"files\"").count - 1, 2)
        XCTAssertTrue(body.contains("name=\"garment\"\r\n\r\npants\r\n"))
    }

    func testQueryItemsAreEncoded() async throws {
        StubProtocol.answer(.init(status: 200, body: #"{"rows":[]}"#))
        _ = try await api.searchEbayCategories("linen t-shirt")
        let url = try XCTUnwrap(StubProtocol.requests.first?.url)
        let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(query, [URLQueryItem(name: "q", value: "linen t-shirt")])
    }

    // MARK: Error mapping

    func testServerSentenceBecomesTheMessage() async {
        StubProtocol.answer(.init(status: 409, body: #"{"detail":"Changed on another device."}"#))
        do {
            _ = try await api.listing("a")
            XCTFail("Expected an error")
        } catch let error as APIError {
            XCTAssertEqual(error.status, 409)
            XCTAssertTrue(error.isConflict)
            XCTAssertEqual(error.localizedDescription, "Changed on another device.")
        } catch {
            XCTFail("Unexpected \(error)")
        }
    }

    func testValidationIssuesAreJoined() async {
        StubProtocol.answer(.init(status: 422, body: #"{"detail":[{"loc":["body","listing","price"],"msg":"Price is required","type":"missing"},{"loc":["body","listing","title"],"msg":"Title is too long","type":"string_too_long"}]}"#))
        await assertMessage("Price is required\nTitle is too long") { _ = try await self.api.listing("a") }
    }

    func testUnreadableErrorSaysTryAgain() async {
        StubProtocol.answer(.init(status: 502, body: "<html>bad gateway</html>"))
        await assertMessage("Something went wrong (502). Try again.") { _ = try await self.api.listing("a") }
    }

    // MARK: Seller confirmation

    func testListSendsConfirmOnlyWithTheSellersConfirmation() async throws {
        StubProtocol.answer(.init(status: 200, body: "{}"))
        try await api.listOnMarketplace("a", marketplace: "ebay", revisionID: "r1",
                                        confirmation: .unitTestOnly(.list, ["ebay"]))
        let sent = try XCTUnwrap(StubProtocol.requests.first)
        XCTAssertEqual(sent.url?.path, "/api/conversations/a/native-marketplaces/ebay/list")
        XCTAssertEqual(sent.json?["confirm"] as? Bool, true)
        XCTAssertEqual(sent.json?["revision_id"] as? String, "r1")
    }

    func testAConfirmationForAnotherMarketplaceOrActionSendsNothing() async {
        StubProtocol.answer(.init(status: 200, body: "{}"))
        await assertRefused { try await self.api.listOnMarketplace("a", marketplace: "poshmark", revisionID: "r1",
                                                                   confirmation: .unitTestOnly(.list, ["ebay"])) }
        await assertRefused { try await self.api.updateOnMarketplace("a", marketplace: "ebay", revisionID: "r1",
                                                                     confirmation: .unitTestOnly(.list, ["ebay"])) }
        await assertRefused { _ = try await self.api.delist("a", marketplaces: ["ebay", "depop"],
                                                            confirmation: .unitTestOnly(.delist, ["ebay"])) }
        await assertRefused { _ = try await self.api.delist("a", marketplaces: [],
                                                            confirmation: .unitTestOnly(.delist, ["ebay"])) }
        XCTAssertTrue(StubProtocol.requests.isEmpty, "Nothing reaches the server without a matching confirmation")
    }

    func testDelistNamesEachConfirmedMarketplace() async throws {
        StubProtocol.answer(.init(status: 200, body: "{}"))
        _ = try await api.delist("a", marketplaces: ["ebay", "depop"], confirmation: .unitTestOnly(.delist, ["depop", "ebay"]))
        let sent = try XCTUnwrap(StubProtocol.requests.first)
        XCTAssertEqual(sent.json?["marketplaces"] as? [String], ["ebay", "depop"])
        XCTAssertEqual(sent.json?["confirm"] as? Bool, true)
    }

    func testConfirmationCoversOnlyWhatWasConfirmed() {
        let confirmed = SellerConfirmation.unitTestOnly(.delist, ["ebay", "depop"])
        XCTAssertTrue(confirmed.covers(.delist, ["depop"]))
        XCTAssertTrue(confirmed.covers(.delist, ["ebay", "depop"]))
        XCTAssertFalse(confirmed.covers(.delist, ["mercari"]))
        XCTAssertFalse(confirmed.covers(.list, ["ebay"]))
        XCTAssertFalse(confirmed.covers(.delist, []))
    }

    // MARK: Expired sign-in (401)

    func testRefusedReadRenewsTheSignInAndRetriesOnce() async throws {
        StubProtocol.answer(.init(status: 401, body: #"{"detail":"A valid seller session is required"}"#),
                            .init(status: 200, body: "[]"))
        let rows = try await api.listings()
        XCTAssertEqual(rows.count, 0)
        XCTAssertEqual(signIn.refreshCount, 1)
        XCTAssertEqual(signIn.signOutCount, 0)
        let sent = StubProtocol.requests
        XCTAssertEqual(sent.count, 2)
        XCTAssertEqual(sent.last?.value(forHTTPHeaderField: "Authorization"), "Bearer token-2")
    }

    func testRefusedWriteRenewsButIsNotSentAgain() async {
        StubProtocol.answer(.init(status: 401, body: #"{"detail":"A valid seller session is required"}"#),
                            .init(status: 200, body: "{}"))
        await assertMessage(APIError.signInRenewed) { try await self.api.rename("a", title: "New") }
        XCTAssertEqual(StubProtocol.requests.count, 1, "A write may already have run, so it is never repeated")
        XCTAssertEqual(signIn.refreshCount, 1)
        XCTAssertEqual(signIn.signOutCount, 0)
    }

    func testASignInTheServerRefusesToRenewSignsOut() async {
        signIn.refreshFailure = SignInRejected()
        StubProtocol.answer(.init(status: 401, body: #"{"detail":"A valid seller session is required"}"#))
        await assertMessage(APIError.signInEnded) { _ = try await self.api.listings() }
        XCTAssertEqual(signIn.signOutCount, 1)
        XCTAssertEqual(StubProtocol.requests.count, 1)
    }

    func testARenewalTheSignInServerFailedKeepsTheSignIn() async {
        signIn.refreshFailure = SignInServerFailed()
        StubProtocol.answer(.init(status: 401, body: #"{"detail":"A valid seller session is required"}"#))
        await assertMessage(APIError.signInNotRenewed) { _ = try await self.api.listings() }
        XCTAssertEqual(signIn.signOutCount, 0, "A 5xx or rate limit from the sign-in server must not delete the saved sign-in")
    }

    func testAnExpiredTokenTheServerRefusesSignsOutBeforeSending() async {
        signIn.accessFailure = SignInRejected()
        StubProtocol.answer(.init(status: 200, body: "[]"))
        await assertMessage(APIError.signInEnded) { _ = try await self.api.listings() }
        XCTAssertEqual(signIn.signOutCount, 1)
        XCTAssertEqual(StubProtocol.requests.count, 0)
    }

    func testAnExpiredTokenThatCouldntBeRenewedKeepsTheSignIn() async {
        signIn.accessFailure = SignInServerFailed()
        StubProtocol.answer(.init(status: 200, body: "[]"))
        await assertMessage(APIError.signInNotRenewed) { _ = try await self.api.listings() }
        XCTAssertEqual(signIn.signOutCount, 0)
        XCTAssertEqual(StubProtocol.requests.count, 0)
    }

    func testOfflineRenewalKeepsTheSellerSignedIn() async {
        signIn.refreshFailure = URLError(.notConnectedToInternet)
        StubProtocol.answer(.init(status: 401, body: #"{"detail":"A valid seller session is required"}"#))
        do {
            _ = try await api.listings()
            XCTFail("Expected an error")
        } catch {
            XCTAssertEqual(signIn.signOutCount, 0)
        }
    }

    func testAFreshTokenRefusedAgainSignsOut() async {
        StubProtocol.answer(.init(status: 401, body: #"{"detail":"A verified seller session is required"}"#))
        await assertMessage(APIError.signInEnded) { _ = try await self.api.listings() }
        XCTAssertEqual(signIn.refreshCount, 1)
        XCTAssertEqual(signIn.signOutCount, 1)
        XCTAssertEqual(StubProtocol.requests.count, 2)
    }

    func testAMarketplaces401KeepsItsMessageAndTheSignIn() async {
        StubProtocol.answer(.init(status: 401, body: #"{"detail":"eBay signed Anteroom out. Reconnect eBay in Settings → Connections."}"#))
        await assertMessage("eBay signed Anteroom out. Reconnect eBay in Settings → Connections.") {
            _ = try await self.api.searchEbayCategories("shirt")
        }
        XCTAssertEqual(signIn.refreshCount, 0)
        XCTAssertEqual(signIn.signOutCount, 0)
    }

    // MARK: Runtime

    func testRuntimeIsAskedOnceForScreensOpeningTogether() async throws {
        StubProtocol.answer(.init(status: 200, body: #"{"mode":"hosted","views":["assistant"],"features":["generation"],"marketplace_forms":["ebay"],"action_marketplaces":["ebay","etsy"]}"#))
        async let first = api.runtime()
        async let second = api.runtime()
        let (a, b) = try await (first, second)
        XCTAssertEqual(a, b)
        XCTAssertEqual(a.actionMarketplaces, ["ebay", "etsy"])
        XCTAssertEqual(StubProtocol.requests.count, 1)
        _ = try await api.runtime(fresh: true)
        XCTAssertEqual(StubProtocol.requests.count, 2)
        await api.forgetSeller()
        _ = try await api.runtime()
        XCTAssertEqual(StubProtocol.requests.count, 3)
    }

    // MARK: Helpers

    private func assertMessage(_ expected: String, file: StaticString = #filePath, line: UInt = #line,
                               _ work: @escaping () async throws -> Void) async {
        do {
            try await work()
            XCTFail("Expected an error", file: file, line: line)
        } catch {
            XCTAssertEqual(error.localizedDescription, expected, file: file, line: line)
        }
    }

    private func assertRefused(file: StaticString = #filePath, line: UInt = #line, _ work: @escaping () async throws -> Void) async {
        do {
            try await work()
            XCTFail("Expected the client to refuse", file: file, line: line)
        } catch let error as APIError {
            XCTAssertEqual(error.status, 0, file: file, line: line)
            XCTAssertTrue(error.message.hasPrefix("Nothing was sent."), file: file, line: line)
        } catch {
            XCTFail("Unexpected \(error)", file: file, line: line)
        }
    }
}
