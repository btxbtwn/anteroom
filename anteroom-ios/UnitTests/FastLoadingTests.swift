import XCTest
@testable import Anteroom

/// What makes listings, tabs and markdowns open quickly: thumbnails at the
/// sizes the server keeps and kept on disk, the editor's single read, shared
/// lookups, and a thumbnail queue that puts the open listing first.
final class FastLoadingTests: XCTestCase {
    private var signIn: StubSignIn!
    private var cache: ImmutableReadCache!
    private var api: AnteroomAPI!

    override func setUp() {
        super.setUp()
        signIn = StubSignIn()
        cache = ImmutableReadCache(directory: StubSignIn.temporaryFolder())
        api = signIn.api(cache: cache)
    }

    // MARK: Thumbnails

    func testThumbnailAsksForAKeptSizeWithTheVersionAndIsKeptOnDisk() async throws {
        StubProtocol.answer(.init(status: 200, body: "jpeg-bytes"))
        let first = try await api.photoThumbnailData(photoID: "p1", size: 400, version: "abc123")
        XCTAssertEqual(first, Data("jpeg-bytes".utf8))
        let sent = try XCTUnwrap(StubProtocol.requests.first?.url)
        XCTAssertEqual(sent.path, "/api/photos/p1/thumb")
        let query = URLComponents(url: sent, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(query, [URLQueryItem(name: "size", value: "384"), URLQueryItem(name: "v", value: "abc123")])

        // Any request that snaps to the same size reads the kept copy; even a new client does.
        let again = try await api.photoThumbnailData(photoID: "p1", size: 384, version: "abc123")
        let reopened = try await signIn.api(cache: cache).photoThumbnailData(photoID: "p1", size: 300, version: "abc123")
        XCTAssertEqual(again, first)
        XCTAssertEqual(reopened, first)
        XCTAssertEqual(StubProtocol.requests.count, 1)
    }

    func testAThumbnailWithoutAVersionIsNotKept() async throws {
        StubProtocol.answer(.init(status: 200, body: "jpeg-bytes"))
        _ = try await api.photoThumbnailData(photoID: "p1", size: 120)
        _ = try await api.photoThumbnailData(photoID: "p1", size: 120)
        XCTAssertEqual(StubProtocol.requests.count, 2)
        let query = URLComponents(url: try XCTUnwrap(StubProtocol.requests.first?.url), resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(query, [URLQueryItem(name: "size", value: "192")])
    }

    func testSignOutEmptiesTheKeptThumbnails() async throws {
        StubProtocol.answer(.init(status: 200, body: "jpeg-bytes"))
        _ = try await api.photoThumbnailData(photoID: "p1", size: 192, version: "v1")
        await api.forgetSeller()
        _ = try await api.photoThumbnailData(photoID: "p1", size: 192, version: "v1")
        XCTAssertEqual(StubProtocol.requests.count, 2)
    }

    func testADownloadFromBeforeSignOutIsNotKept() async {
        let generation = await cache.generation
        await cache.clear()
        await cache.store(Data("old seller".utf8), for: "k", generation: generation)
        let kept = await cache.data(for: "k")
        XCTAssertNil(kept)
        await cache.store(Data("new".utf8), for: "k", generation: await cache.generation)
        let stored = await cache.data(for: "k")
        XCTAssertEqual(stored, Data("new".utf8))
    }

    // MARK: Thumbnail queue

    func testUrgentCallersGoAheadOfWaitingOnes() async throws {
        let gate = RequestGate(limit: 1)
        try await gate.enter()
        let order = Recorder()
        let background = Task {
            try await gate.enter()
            await order.add("cover")
            await gate.leave()
        }
        await waitUntil { await gate.waitingCount == 1 }
        let urgent = Task {
            try await gate.enter(urgent: true)
            await order.add("open listing")
            await gate.leave()
        }
        await waitUntil { await gate.waitingCount == 2 }
        await gate.leave()
        try await background.value
        try await urgent.value
        let recorded = await order.items
        XCTAssertEqual(recorded, ["open listing", "cover"])
    }

    func testACancelledWaiterLeavesTheQueueWithoutTakingATurn() async throws {
        let gate = RequestGate(limit: 1)
        try await gate.enter()
        let offscreen = Task { try await gate.enter() }
        await waitUntil { await gate.waitingCount == 1 }
        offscreen.cancel()
        do {
            try await offscreen.value
            XCTFail("A cancelled waiter should not get a turn")
        } catch {
            XCTAssertTrue(error.isCancellation)
        }
        await waitUntil { await gate.waitingCount == 0 }
        // The running caller's turn is free again once it leaves.
        await gate.leave()
        try await gate.enter()
        await gate.leave()
    }

    func testCancellationIsNotAFailure() {
        XCTAssertTrue(CancellationError().isCancellation)
        XCTAssertTrue(URLError(.cancelled).isCancellation)
        XCTAssertFalse(URLError(.timedOut).isCancellation)
        XCTAssertFalse(APIError(status: 500, message: "x").isCancellation)
    }

    // MARK: Editor reads

    func testTheEditorReadsListingNotesAndPhotosInOneRequest() async throws {
        StubProtocol.answer(.init(status: 200, body: #"""
        {"conversation":{"id":"a","title":"Blue shirt","status":"draft","notes":"{}"},
         "listing":{"listing":{"title":"Blue shirt","price":12},"current_revision_id":"r1"},
         "photos":[{"id":"p1","url":"/api/photos/p1?v=abc"}],
         "messages":[],"jobs":[],"activity":{}}
        """#))
        let screen = try await api.listingScreen("a")
        XCTAssertEqual(StubProtocol.requests.first?.url?.path, "/api/conversations/a/screen")
        XCTAssertEqual(screen.conversation.title, "Blue shirt")
        XCTAssertEqual(screen.listing.currentRevisionID, "r1")
        XCTAssertEqual(screen.photos.map(\.id), ["p1"])
        XCTAssertEqual(screen.photos.first?.version, "abc")
    }

    func testGeneralListsAndWhereYouListAreSharedBetweenListings() async throws {
        StubProtocol.answer([{ request in
            request.url?.path == "/api/catalog/dropdown-options"
                ? .init(status: 200, body: #"{"forms":{"vendoo":{"condition":["New"]}}}"#)
                : .init(status: 200, body: #"{"available":[],"selected":["ebay"]}"#)
        }])
        _ = try await api.generalOptions()
        _ = try await api.generalOptions()
        _ = try await api.selectedMarketplaces()
        let selected = try await api.selectedMarketplaces()
        XCTAssertEqual(selected, ["ebay"])
        XCTAssertEqual(StubProtocol.requests.count, 2)
        // Changing Where you list asks again.
        _ = try await api.setMarketplaces(["ebay", "depop"])
        _ = try await api.selectedMarketplaces()
        XCTAssertEqual(StubProtocol.requests.count, 4)
    }

    func testAFormsSharedListsAreKeptOnDiskByVersion() async throws {
        let form = #"""
        {"marketplace":"depop","fields":[
          {"key":"depop_specifics.brand","label":"Brand","selection_only":true,"choice_list":{"name":"depop-brands","version":"v1"}},
          {"key":"depop_specifics.style","label":"Style","choice_list":{"name":"depop-styles","version":"v2"}}]}
        """#
        StubProtocol.answer([{ request in
            switch request.url?.path {
            case "/api/catalog/choice-lists/depop-brands": return .init(status: 200, body: #"{"options":["Nike"],"option_values":{"Nike":"nike"}}"#)
            case "/api/catalog/choice-lists/depop-styles": return .init(status: 200, body: #"{"options":["Y2K"]}"#)
            default: return .init(status: 200, body: form)
            }
        }])
        let loaded = try await api.nativeForm("a", marketplace: "depop")
        XCTAssertEqual(loaded.fields[0].label(for: "nike"), "Nike")
        XCTAssertEqual(loaded.fields[1].options, ["Y2K"])
        XCTAssertEqual(StubProtocol.requests.count, 3)

        // A new client (the app reopened) reads the lists from disk: only the form is asked for.
        let reopened = try await signIn.api(cache: cache).nativeForm("a", marketplace: "depop")
        XCTAssertEqual(reopened.fields[0].options, ["Nike"])
        XCTAssertEqual(StubProtocol.requests.count, 4)
        XCTAssertEqual(StubProtocol.requests.last?.url?.path, "/api/conversations/a/native-marketplaces/depop/form")
    }

    // MARK: Helpers

    private func waitUntil(_ condition: () async -> Bool) async {
        for _ in 0..<500 {
            if await condition() { return }
            try? await Task.sleep(nanoseconds: 2_000_000)
        }
        XCTFail("Timed out waiting")
    }
}

private actor Recorder {
    private(set) var items: [String] = []
    func add(_ item: String) { items.append(item) }
}
