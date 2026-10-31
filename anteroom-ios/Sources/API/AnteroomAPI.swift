import Foundation

struct APIError: LocalizedError, Sendable {
    let status: Int
    let message: String

    var errorDescription: String? { message }
    var isConflict: Bool { status == 409 }
}

/// Client for the hosted Anteroom API (`vendoo_studio.hosted:app`). Every
/// request carries the seller's current Supabase access token.
final class AnteroomAPI: Sendable {
    static let pageSize = 100

    private let baseURL: URL
    private let accessToken: @Sendable () async throws -> String
    private let session = URLSession(configuration: .ephemeral)

    init(baseURL: URL, accessToken: @escaping @Sendable () async throws -> String) {
        self.baseURL = baseURL
        self.accessToken = accessToken
    }

    // MARK: Drafts

    func drafts(offset: Int, search: String) async throws -> [Draft] {
        var query = [URLQueryItem(name: "offset", value: String(offset))]
        if !search.isEmpty { query.append(URLQueryItem(name: "search", value: search)) }
        return try await decode(request("GET", "api/drafts", query: query))
    }

    func createDraft(title: String, notes: String = "") async throws -> Draft {
        try await decode(request("POST", "api/drafts", json: ["title": title, "notes": notes]))
    }

    func draft(_ id: String) async throws -> DraftDetail {
        try await decode(request("GET", "api/drafts/\(id)"))
    }

    func save(_ id: String, _ body: DraftSave) async throws -> DraftDetail {
        try await decode(request("PATCH", "api/drafts/\(id)", json: body))
    }

    // MARK: Photos

    func photos(draftID: String) async throws -> [DraftPhoto] {
        try await decode(request("GET", "api/drafts/\(draftID)/photos"))
    }

    /// Uploads JPEG bytes. The server re-encodes, strips metadata and limits size.
    func uploadPhoto(draftID: String, jpeg: Data) async throws -> DraftPhoto {
        var upload = try await request("POST", "api/drafts/\(draftID)/photos")
        upload.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        upload.httpBody = jpeg
        return try await decode(upload)
    }

    func photoData(draftID: String, photoID: String) async throws -> Data {
        try await send(request("GET", "api/drafts/\(draftID)/photos/\(photoID)"))
    }

    func deletePhoto(draftID: String, photoID: String) async throws {
        _ = try await send(request("DELETE", "api/drafts/\(draftID)/photos/\(photoID)"))
    }

    // MARK: Transport

    private func request(_ method: String, _ path: String, query: [URLQueryItem] = []) async throws -> URLRequest {
        var url = baseURL.appending(path: path)
        if !query.isEmpty { url.append(queryItems: query) }
        let token = try await accessToken()
        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = method
        urlRequest.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        urlRequest.setValue("application/json", forHTTPHeaderField: "Accept")
        return urlRequest
    }

    private func request(_ method: String, _ path: String, json body: some Encodable) async throws -> URLRequest {
        var withBody = try await request(method, path)
        withBody.setValue("application/json", forHTTPHeaderField: "Content-Type")
        withBody.httpBody = try JSONEncoder().encode(body)
        return withBody
    }

    private func decode<T: Decodable>(_ request: URLRequest) async throws -> T {
        try JSONDecoder().decode(T.self, from: try await send(request))
    }

    private func send(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIError(status: 0, message: "Anteroom didn't answer. Try again.")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError(status: http.statusCode, message: Self.message(from: data, status: http.statusCode))
        }
        return data
    }

    /// FastAPI errors carry `detail` as a sentence or a list of validation issues.
    private static func message(from data: Data, status: Int) -> String {
        if status == 401 { return "Your sign-in has ended. Sign in again." }
        if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            if let detail = object["detail"] as? String { return detail }
            if let issues = object["detail"] as? [[String: Any]] {
                let messages = issues.compactMap { $0["msg"] as? String }
                if !messages.isEmpty { return messages.joined(separator: "\n") }
            }
        }
        return "Something went wrong (\(status)). Try again."
    }
}
