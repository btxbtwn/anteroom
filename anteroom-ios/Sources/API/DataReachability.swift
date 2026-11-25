import Foundation
import Observation

/// Whether the phone can reach the seller's data on the hosted API, as the web app tells it.
///
/// The server marks its "Cloud data is temporarily unavailable" 503 with
/// `X-Anteroom-Reason: database_unavailable`. One such answer is only a hint:
/// the phone asks `/api/ready` (no sign-in needed), and only when that fails too
/// does it say the data is unreachable. While it does, `/api/ready` is asked
/// every 5 seconds; only a passing answer clears the state, and `recoveries`
/// then counts up so screens that failed meanwhile load again.
@MainActor
@Observable
final class DataReachability {
    enum State: Equatable { case ok, checking, unreachable }

    nonisolated static let reasonHeader = "X-Anteroom-Reason"
    nonisolated static let databaseUnavailable = "database_unavailable"

    private(set) var state: State = .ok
    private(set) var recoveries = 0
    private let probe: @Sendable () async -> Bool
    private let pollInterval: Duration
    private var polling: Task<Void, Never>?

    init(pollInterval: Duration = .seconds(5), probe: @escaping @Sendable () async -> Bool) {
        self.probe = probe
        self.pollInterval = pollInterval
    }

    /// A response said the database is unavailable: confirm with `/api/ready`.
    func note() {
        guard state == .ok else { return }
        state = .checking
        polling = Task { await poll() }
    }

    private func poll() async {
        while !Task.isCancelled {
            if await probe() {
                guard !Task.isCancelled else { return }
                if state == .unreachable { recoveries += 1 }
                state = .ok
                polling = nil
                return
            }
            guard !Task.isCancelled else { return }
            state = .unreachable
            do { try await Task.sleep(for: pollInterval) } catch { return }
        }
    }

    /// Sign-out: stop asking and forget the state.
    func reset() {
        polling?.cancel()
        polling = nil
        state = .ok
    }

    /// Whether `/api/ready` at `baseURL` passes within five seconds.
    nonisolated static func ready(at baseURL: URL, session: URLSession = .shared) async -> Bool {
        var request = URLRequest(url: baseURL.appending(path: "api/ready"))
        request.timeoutInterval = 5
        request.cachePolicy = .reloadIgnoringLocalCacheData
        guard let (_, response) = try? await session.data(for: request) else { return false }
        return (response as? HTTPURLResponse)?.statusCode == 200
    }

    /// Whether a hosted answer names the database as unavailable.
    nonisolated static func namesDatabase(_ response: HTTPURLResponse) -> Bool {
        response.statusCode == 503 && response.value(forHTTPHeaderField: reasonHeader) == databaseUnavailable
    }
}
