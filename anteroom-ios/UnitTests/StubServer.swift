import Foundation
@testable import Anteroom

/// Answers the API client's requests in-process and records what it sent.
final class StubProtocol: URLProtocol {
    struct Reply {
        var status: Int
        var body: String
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
        let response = HTTPURLResponse(url: request.url!, statusCode: reply.status, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "application/json"])!
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

    var refreshCount: Int { lock.lock(); defer { lock.unlock() }; return refreshes }
    var signOutCount: Int { lock.lock(); defer { lock.unlock() }; return signOuts }

    var authorization: APIAuthorization {
        APIAuthorization(
            accessToken: { [self] in
                lock.lock(); defer { lock.unlock() }
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

    func api() -> AnteroomAPI {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        return AnteroomAPI(baseURL: URL(string: "https://anteroom.test")!, auth: authorization, configuration: configuration)
    }
}

struct SignInGone: Error {}

extension URLRequest {
    var json: [String: Any]? {
        httpBody.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
    }
}
