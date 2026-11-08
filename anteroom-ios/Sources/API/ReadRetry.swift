import Foundation

/// Waits out a server that is briefly unavailable, such as the hosted API
/// restarting during a deploy, so a screen recovers by itself instead of
/// showing an error the seller has to retry.
///
/// Only reads (GET) are sent again, after a gateway answer (502, 503, 504) or
/// a connection that failed or dropped. Anything else may already have run on
/// the server, so it is never repeated: nothing is saved, generated, listed or
/// delisted twice. Waits grow exponentially with jitter, follow the server's
/// `Retry-After`, and stop at `budget`, so a real outage still shows an error.
/// Leaving the screen cancels the task and with it the wait.
struct ReadRetry: Sendable {
    /// The longest a read keeps trying before its error is shown.
    var budget: TimeInterval = 90
    var firstDelay: TimeInterval = 0.5
    var longestDelay: TimeInterval = 8
    var now: @Sendable () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }
    var sleep: @Sendable (TimeInterval) async throws -> Void = { try await Task.sleep(for: .seconds($0)) }
    /// A random share of the delay, so phones don't all come back at the same moment.
    var jitter: @Sendable (ClosedRange<Double>) -> Double = { Double.random(in: $0) }

    static let standard = ReadRetry()

    /// What a proxy answers while the server behind it is down or restarting.
    static let unavailableStatuses: Set<Int> = [502, 503, 504]
    /// Failures where the server did not answer and the connection may work again.
    static let connectionErrors: Set<URLError.Code> = [
        .timedOut, .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed,
        .networkConnectionLost, .notConnectedToInternet,
    ]

    static func retries(method: String?, status: Int) -> Bool {
        method == "GET" && unavailableStatuses.contains(status)
    }

    static func retries(method: String?, error: Error) -> Bool {
        guard method == "GET", let failure = error as? URLError else { return false }
        return connectionErrors.contains(failure.code)
    }

    /// The wait before retry number `attempt` (0 for the first), or nil when
    /// waiting would pass the budget.
    func delay(attempt: Int, retryAfter: String?, elapsed: TimeInterval) -> TimeInterval? {
        let wait: TimeInterval
        if let asked = Self.seconds(retryAfter: retryAfter) {
            wait = asked
        } else {
            let ceiling = min(longestDelay, firstDelay * pow(2, Double(min(attempt, 30))))
            wait = ceiling / 2 + jitter(0...(ceiling / 2))
        }
        return elapsed + wait <= budget ? wait : nil
    }

    /// `Retry-After` as seconds to wait: a number of seconds or an HTTP date.
    static func seconds(retryAfter value: String?, now: Date = Date()) -> TimeInterval? {
        guard let value = value?.trimmingCharacters(in: .whitespaces), !value.isEmpty else { return nil }
        if let seconds = TimeInterval(value) { return max(0, seconds) }
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.timeZone = TimeZone(identifier: "GMT")
        format.dateFormat = "EEE, dd MMM yyyy HH:mm:ss zzz"
        return format.date(from: value).map { max(0, $0.timeIntervalSince(now)) }
    }

    /// Sends `request` through `exchange`, again while it is a read the server
    /// could not answer yet. Returns the last answer, or throws the last error.
    func run(_ request: URLRequest,
             _ exchange: (URLRequest) async throws -> (Data, HTTPURLResponse)) async throws -> (Data, HTTPURLResponse) {
        let started = now()
        var attempt = 0
        while true {
            let wait: TimeInterval
            do {
                let (data, response) = try await exchange(request)
                guard Self.retries(method: request.httpMethod, status: response.statusCode),
                      let next = delay(attempt: attempt, retryAfter: response.value(forHTTPHeaderField: "Retry-After"),
                                       elapsed: now() - started)
                else { return (data, response) }
                wait = next
            } catch {
                guard Self.retries(method: request.httpMethod, error: error),
                      let next = delay(attempt: attempt, retryAfter: nil, elapsed: now() - started)
                else { throw error }
                wait = next
            }
            try Task.checkCancellation()
            try await sleep(wait)
            try Task.checkCancellation()
            attempt += 1
        }
    }
}
