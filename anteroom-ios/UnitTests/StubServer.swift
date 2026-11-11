import Foundation
@testable import Anteroom

/// Answers the API client's requests in-process and records what it sent.
final class StubProtocol: URLProtocol {
    struct Reply {
        var status: Int
        var body: String
        var headers: [String: String] = [:]
        /// Fails the request without an answer, like a dropped connection.
        var failure: URLError.Code?
    }

    private static let lock = NSLock()
    private static var replies: [(URLRequest) -> Reply] = []
    private static var recorded: [URLRequest] = []

    /// Replies in order; the last one repeats.
    static func answer(_ replies: [(URLRequest) -> Reply]) {
        lock.lock(); defer { lock.unlock() }
        self.replies = replies
        recorded = []
    }

    static func answer(_ replies: Reply...) {
        answer(replies.map { reply in { _ in reply } })
    }

    static var requests: [URLRequest] {
        lock.lock(); defer { lock.unlock() }
        return recorded
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        var sent = request
        if sent.httpBody == nil, let stream = sent.httpBodyStream {
            sent.httpBody = Self.read(stream)
        }
        Self.lock.lock()
        Self.recorded.append(sent)
        let index = min(Self.recorded.count - 1, Self.replies.count - 1)
        let respond: (URLRequest) -> Reply = index >= 0 ? Self.replies[index] : { _ in Reply(status: 500, body: "") }
        Self.lock.unlock()
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
    /// Thrown by refresh; nil renews the sign-in.
    var refreshFailure: Error?
    /// Thrown when the current token is read; nil returns it.
    var accessFailure: Error?

    var refreshCount: Int { lock.lock(); defer { lock.unlock() }; return refreshes }
    var signOutCount: Int { lock.lock(); defer { lock.unlock() }; return signOuts }

    var authorization: APIAuthorization {
        APIAuthorization(
            accessToken: { [self] in
                lock.lock(); defer { lock.unlock() }
                if let accessFailure { throw accessFailure }
                return token
            },
            refreshedToken: { [self] in
                lock.lock(); defer { lock.unlock() }
                refreshes += 1
                if let refreshFailure { throw refreshFailure }
                token = "token-\(refreshes + 1)"
                return token
            },
            signedOut: { [self] in
                lock.lock(); defer { lock.unlock() }
                signOuts += 1
            })
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
    /// Runs during each wait, before time moves on.
    var onWait: (@Sendable () -> Void)?

    var waits: [TimeInterval] { lock.lock(); defer { lock.unlock() }; return recorded }

    var retry: ReadRetry {
        var retry = ReadRetry()
        retry.now = { [self] in lock.lock(); defer { lock.unlock() }; return time }
        retry.sleep = { [self] seconds in
            onWait?()
            try Task.checkCancellation()
            lock.lock(); defer { lock.unlock() }
            recorded.append(seconds)
            time += seconds
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
