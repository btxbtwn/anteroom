import SwiftUI
import UIKit
import XCTest
@testable import Anteroom

/// Mirrors the web app: a database-unavailable answer is confirmed with
/// `/api/ready` before the phone says the data can't be reached, and only a
/// passing check clears it.
@MainActor
final class DataReachabilityTests: XCTestCase {
    private final class Answers: @unchecked Sendable {
        private let lock = NSLock()
        private var answers: [Bool]
        private(set) var asked = 0
        init(_ answers: [Bool]) { self.answers = answers }
        func next() -> Bool {
            lock.withLock {
                asked += 1
                return answers.isEmpty ? false : answers.removeFirst()
            }
        }
    }

    private func waitUntil(_ condition: @MainActor () -> Bool) async throws {
        for _ in 0..<200 where !condition() { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(condition())
    }

    func testOneUnconfirmedAnswerChangesNothing() async throws {
        let answers = Answers([true])
        let reachability = DataReachability(pollInterval: .milliseconds(10)) { answers.next() }
        reachability.note()
        XCTAssertEqual(reachability.state, .checking)
        try await waitUntil { reachability.state == .ok }
        XCTAssertEqual(reachability.recoveries, 0)
        XCTAssertEqual(answers.asked, 1)
    }

    func testUnreachableUntilReadyPassesThenScreensReload() async throws {
        let answers = Answers([false, false, true])
        let reachability = DataReachability(pollInterval: .milliseconds(10)) { answers.next() }
        reachability.note()
        try await waitUntil { reachability.state == .unreachable }
        reachability.note()  // More failing reads don't start another check.
        try await waitUntil { reachability.state == .ok }
        XCTAssertEqual(reachability.recoveries, 1)
        XCTAssertEqual(answers.asked, 3)
    }

    func testSignOutStopsAsking() async throws {
        let answers = Answers([])
        let reachability = DataReachability(pollInterval: .milliseconds(10)) { answers.next() }
        reachability.note()
        try await waitUntil { reachability.state == .unreachable }
        reachability.reset()
        let asked = answers.asked
        try await Task.sleep(for: .milliseconds(60))
        XCTAssertEqual(reachability.state, .ok)
        XCTAssertLessThanOrEqual(answers.asked, asked + 1)
    }

    func testTheAPINotesADatabaseUnavailableAnswer() async throws {
        final class Count: @unchecked Sendable {
            private let lock = NSLock()
            private var value = 0
            func add() { lock.withLock { value += 1 } }
            var current: Int { lock.withLock { value } }
        }
        let noted = Count()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        let api = AnteroomAPI(baseURL: URL(string: "https://anteroom.test")!, auth: StubSignIn().authorization,
                              configuration: configuration, readRetry: ReadRetry(budget: 0),
                              immutableCache: ImmutableReadCache(directory: StubSignIn.temporaryFolder()),
                              databaseUnavailable: { noted.add() })
        StubProtocol.answer(.init(status: 503, body: #"{"detail":"Cloud data is temporarily unavailable"}"#,
                                  headers: ["X-Anteroom-Reason": "database_unavailable"]))
        _ = try? await api.listings()
        XCTAssertEqual(noted.current, 1)
        StubProtocol.answer(.init(status: 503, body: "<html>Service Unavailable</html>"))
        _ = try? await api.listings()
        XCTAssertEqual(noted.current, 1, "A gateway 503 during a deploy is not the database")
    }

    /// Renders the banner over a sample inventory on the Simulator, with invented data only.
    func testBannerLayoutOverTheWorkspace() async throws {
        let reachability = DataReachability(pollInterval: .seconds(60)) { false }
        reachability.note()
        try await waitUntil { reachability.state == .unreachable }
        let view = NavigationStack {
            List(["Linen button-down shirt", "Leather shoulder bag", "Vintage cotton crewneck"], id: \.self) { Text($0) }
                .navigationTitle("Inventory")
                .navigationBarTitleDisplayMode(.inline)
        }
        .dataUnreachableBanner(reachability)
        .tint(WorkspaceStyle.accent)
        let host = UIHostingController(rootView: view)
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 393, height: 852))
        window.rootViewController = host
        window.makeKeyAndVisible()
        defer { window.isHidden = true }
        host.view.layoutIfNeeded()
        try await Task.sleep(for: .milliseconds(800))
        let image = UIGraphicsImageRenderer(bounds: host.view.bounds).image { _ in
            host.view.drawHierarchy(in: host.view.bounds, afterScreenUpdates: true)
        }
        let attachment = XCTAttachment(image: image)
        attachment.name = "Data unreachable banner iPhone"
        attachment.lifetime = .keepAlways
        add(attachment)
        XCTAssertEqual(image.size.width, 393)
        reachability.reset()
    }
}
