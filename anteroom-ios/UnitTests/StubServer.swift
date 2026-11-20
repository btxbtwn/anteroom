import Foundation
@testable import Anteroom

/// Answers the API client's requests in-process and records what it sent.
final class StubProtocol: URLProtocol {
    struct Reply: Sendable {
        var status: Int
        var body: String
        var headers: [String: String] = [:]
        /// Fails the request without an answer, like a dropped connection.
        var failure: URLError.Code?
    }

    private final class State: @unchecked Sendable {
        private let lock = NSLock()
        private var replies: [@Sendable (URLRequest) -> Reply] = []
        private var recorded: [URLRequest] = []

        func answer(_ replies: [@Sendable (URLRequest) -> Reply]) {
            lock.withLock {
                self.replies = replies
                recorded = []
            }
        }

        var requests: [URLRequest] {
            lock.withLock { recorded }
        }

        func nextResponder(recording request: URLRequest) -> @Sendable (URLRequest) -> Reply {
            lock.withLock {
                recorded.append(request)
                let index = min(recorded.count - 1, replies.count - 1)
                return index >= 0 ? replies[index] : { _ in Reply(status: 500, body: "") }
            }
        }
    }

    private static let state = State()

    /// Replies in order; the last one repeats.
    static func answer(_ replies: [@Sendable (URLRequest) -> Reply]) {
        state.answer(replies)
    }

    static func answer(_ replies: Reply...) {
        answer(replies.map { reply in { _ in reply } })
    }

    static var requests: [URLRequest] {
        state.requests
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        var sent = request
        if sent.httpBody == nil, let stream = sent.httpBodyStream {
            sent.httpBody = Self.read(stream)
        }
        let respond = Self.state.nextResponder(recording: sent)
        let reply = respond(sent)
        if let failure = reply.failure {
            client?.urlProtocol(self, didFailWithError: URLError(failure))
            return
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: reply.status, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "application/json"].merging(reply.headers) { $1 })!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(reply.body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}

    private static func read(_ stream: InputStream) -> Data {
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return data
    }
}

/// The seller's sign-in as the API client sees it, with counts of what it was asked.
final class StubSignIn: @unchecked Sendable {
    private let lock = NSLock()
    private var token = "token-1"
    private var refreshes = 0
    private var signOuts = 0
    private var _refreshFailure: Error?
    private var _accessFailure: Error?

    /// Thrown by refresh; nil renews the sign-in.
    var refreshFailure: Error? {
        get { lock.withLock { _refreshFailure } }
        set { lock.withLock { _refreshFailure = newValue } }
    }

    /// Thrown when the current token is read; nil returns it.
    var accessFailure: Error? {
        get { lock.withLock { _accessFailure } }
        set { lock.withLock { _accessFailure = newValue } }
    }

    var refreshCount: Int { lock.withLock { refreshes } }
    var signOutCount: Int { lock.withLock { signOuts } }

    private func nextAccessToken() throws -> String {
        try lock.withLock {
            if let failure = _accessFailure { throw failure }
            return token
        }
    }

    private func nextRefreshedToken() throws -> String {
        try lock.withLock {
            refreshes += 1
            if let failure = _refreshFailure { throw failure }
            token = "token-\(refreshes + 1)"
            return token
        }
    }

    private func recordSignOut() {
        lock.withLock { signOuts += 1 }
    }

    var authorization: APIAuthorization {
        APIAuthorization(
            accessToken: { [self] in try nextAccessToken() },
            refreshedToken: { [self] in try nextRefreshedToken() },
            signedOut: { [self] in recordSignOut() })
    }

    /// Retries without waiting, on a clock that only the waits move. Each
    /// client keeps immutable reads in a folder of its own.
    func api(readRetry: ReadRetry = TestClock().retry,
             cache: ImmutableReadCache = ImmutableReadCache(directory: StubSignIn.temporaryFolder())) -> AnteroomAPI {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        return AnteroomAPI(baseURL: URL(string: "https://anteroom.test")!, auth: authorization, configuration: configuration,
                           readRetry: readRetry, immutableCache: cache)
    }

    static func temporaryFolder() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("anteroom-tests-\(UUID().uuidString)", isDirectory: true)
    }
}

/// Time for `ReadRetry` that passes only when it waits, and records each wait.
final class TestClock: @unchecked Sendable {
    private let lock = NSLock()
    private var time: TimeInterval = 0
    private var recorded: [TimeInterval] = []
    private var _onWait: (@Sendable () -> Void)?

    /// Runs during each wait, before time moves on.
    var onWait: (@Sendable () -> Void)? {
        get { lock.withLock { _onWait } }
        set { lock.withLock { _onWait = newValue } }
    }

    var waits: [TimeInterval] { lock.withLock { recorded } }

    private func currentTime() -> TimeInterval {
        lock.withLock { time }
    }

    private func recordWait(_ seconds: TimeInterval) {
        lock.withLock {
            recorded.append(seconds)
            time += seconds
        }
    }

    var retry: ReadRetry {
        var retry = ReadRetry()
        retry.now = { [self] in currentTime() }
        retry.sleep = { [self] seconds in
            onWait?()
            try Task.checkCancellation()
            recordWait(seconds)
        }
        // The longest wait each step allows, so budgets are exact.
        retry.jitter = { $0.upperBound }
        return retry
    }
}

/// A sign-in server that failed without refusing the sign-in, such as a 503.
struct SignInServerFailed: Error {}

extension URLRequest {
    var json: [String: Any]? {
        httpBody.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
    }
}
