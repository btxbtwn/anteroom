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
    private let baseURL: URL
    private let accessToken: @Sendable () async throws -> String
    private let session = URLSession(configuration: .ephemeral)

    init(baseURL: URL, accessToken: @escaping @Sendable () async throws -> String) {
        self.baseURL = baseURL
        self.accessToken = accessToken
    }

    // MARK: Listings (the shared workspace contract the desktop and web use)

    /// The whole inventory; like the desktop sidebar, the phone filters and
    /// sorts it locally.
    func listings() async throws -> [ListingSummary] {
        try await decode(request("GET", "api/conversations"))
    }

    func createListing(title: String) async throws -> ListingSummary {
        try await decode(request("POST", "api/conversations", json: ["title": title]))
    }

    func conversation(_ id: String) async throws -> ConversationDetail {
        try await decode(request("GET", "api/conversations/\(id)"))
    }

    func listing(_ id: String) async throws -> ListingDocument {
        try await decode(request("GET", "api/conversations/\(id)/listing"))
    }

    func saveListing(_ id: String, _ body: ListingSave) async throws -> ListingSaveResult {
        try await decode(request("PUT", "api/conversations/\(id)/listing", json: body))
    }

    func saveNotes(_ id: String, notes: String) async throws {
        _ = try await send(request("PATCH", "api/conversations/\(id)", json: ["notes": notes]))
    }

    func rename(_ id: String, title: String) async throws {
        _ = try await send(request("PATCH", "api/conversations/\(id)", json: ["title": title]))
    }

    /// The server only deletes listings that were never sent anywhere.
    func deleteListing(_ id: String) async throws {
        _ = try await send(request("DELETE", "api/conversations/\(id)"))
    }

    /// Settings → Listings → Where you list, in the seller's order.
    func selectedMarketplaces() async throws -> [String] {
        struct Payload: Decodable { let selected: [String] }
        let payload: Payload = try await decode(request("GET", "api/settings/marketplaces"))
        return payload.selected
    }

    /// The desktop's General dropdown lists (Condition, colors, Size).
    func generalOptions() async throws -> GeneralOptions {
        struct Payload: Decodable { let forms: [String: [String: [String]]] }
        let payload: Payload = try await decode(request("GET", "api/catalog/dropdown-options"))
        return GeneralOptions(vendoo: payload.forms["vendoo"] ?? [:])
    }

    /// The inventory's ranked next steps.
    func suggestions() async throws -> [Suggestion] {
        struct Payload: Decodable { let suggestions: [Suggestion] }
        let payload: Payload = try await decode(request("GET", "api/suggestions"))
        return payload.suggestions
    }

    /// Marketplace tabs this workspace can edit natively (`/api/runtime`).
    func marketplaceForms() async throws -> [String] {
        struct Payload: Decodable { let marketplace_forms: [String]? }
        let payload: Payload = try await decode(request("GET", "api/runtime"))
        return payload.marketplace_forms ?? []
    }

    func nativeForm(_ id: String, marketplace: String) async throws -> NativeForm {
        try await decode(request("GET", "api/conversations/\(id)/native-marketplaces/\(marketplace)/form"))
    }

    // MARK: Listings settings (hosted rows of Settings → Listings)

    func marketplaceSettings() async throws -> MarketplaceSettings {
        try await decode(request("GET", "api/settings/marketplaces"))
    }

    func setMarketplaces(_ selected: [String]) async throws -> MarketplaceSettings {
        try await decode(request("PUT", "api/settings/marketplaces", json: ["selected": selected]))
    }

    func packageDimensions() async throws -> PackageDimensions {
        try await decode(request("GET", "api/settings/package-dimensions"))
    }

    func setPackageDimensions(_ dimensions: PackageDimensions) async throws -> PackageDimensions {
        try await decode(request("PUT", "api/settings/package-dimensions", json: dimensions))
    }

    func shipsFromZip() async throws -> String {
        struct Payload: Decodable { let zip: String }
        let payload: Payload = try await decode(request("GET", "api/settings/ships-from-zip"))
        return payload.zip
    }

    func setShipsFromZip(_ zip: String) async throws -> String {
        struct Payload: Decodable { let zip: String }
        let payload: Payload = try await decode(request("PUT", "api/settings/ships-from-zip", json: ["zip": zip]))
        return payload.zip
    }

    func hiddenFields() async throws -> HiddenFields {
        try await decode(request("GET", "api/settings/hidden-fields"))
    }

    func restoreHiddenField(_ field: HiddenField) async throws -> HiddenFields {
        try await decode(request("DELETE", "api/settings/hidden-fields",
                                 json: ["marketplace": field.marketplace, "field": field.field, "scope": "always"]))
    }

    func listingFormulas() async throws -> ListingFormulas {
        try await decode(request("GET", "api/settings/formulas"))
    }

    /// Empty text restores the built-in formula.
    func setListingFormulas(title: String, description: String) async throws -> ListingFormulas {
        try await decode(request("PUT", "api/settings/formulas", json: ["title": title, "description": description]))
    }

    func staleListingDays() async throws -> Int {
        struct Payload: Decodable { let stale_listing_days: Int? }
        let payload: Payload = try await decode(request("GET", "api/settings/ui"))
        return payload.stale_listing_days ?? 60
    }

    func setStaleListingDays(_ days: Int) async throws -> Int {
        struct Payload: Decodable { let stale_listing_days: Int? }
        let payload: Payload = try await decode(request("PUT", "api/settings/ui", json: ["stale_listing_days": days]))
        return payload.stale_listing_days ?? days
    }

    // MARK: Providers (API keys, kept encrypted server-side and never returned)

    func keyProviders() async throws -> [KeyProvider] {
        struct Payload: Decodable { let providers: [KeyProvider] }
        let payload: Payload = try await decode(request("GET", "api/settings/ai-keys"))
        return payload.providers
    }

    func providerOrder() async throws -> ProviderOrder {
        try await decode(request("GET", "api/settings/provider"))
    }

    func setProviderOrder(primary: String, fallback: String) async throws {
        _ = try await send(request("PUT", "api/settings/provider/preferred",
                                   json: ["primary": primary, "fallback": fallback]))
    }

    func setPhotoProvider(_ choice: String) async throws {
        _ = try await send(request("PUT", "api/settings/provider/photos", json: ["choice": choice]))
    }

    func saveProviderKey(_ id: String, key: String) async throws {
        _ = try await send(request("PUT", "api/settings/ai-keys/\(id)", json: ["api_key": key]))
    }

    func removeProviderKey(_ id: String) async throws {
        _ = try await send(request("DELETE", "api/settings/ai-keys/\(id)"))
    }

    /// Nil when the connection works; otherwise the provider's message.
    func testProviderKey(_ id: String) async throws -> String? {
        struct Payload: Decodable { let ok: Bool; let error: String? }
        let payload: Payload = try await decode(request("POST", "api/settings/ai-keys/\(id)/test"))
        return payload.ok ? nil : (payload.error ?? "The connection failed.")
    }

    func providerModels(_ id: String) async throws -> ProviderModels {
        try await decode(request("GET", "api/settings/ai-keys/\(id)/models"))
    }

    func setProviderModels(_ id: String, vision: String? = nil, listing: String? = nil, reasoning: String? = nil) async throws {
        var body: [String: String] = [:]
        if let vision { body["vision_model"] = vision }
        if let listing { body["listing_model"] = listing }
        if let reasoning { body["reasoning_effort"] = reasoning }
        _ = try await send(request("PUT", "api/settings/ai-keys/\(id)/models", json: body))
    }

    // MARK: Analytics

    func analytics(range: String, start: Date? = nil, end: Date? = nil) async throws -> InventoryAnalytics {
        var query = [URLQueryItem(name: "range", value: range)]
        let day = Date.ISO8601FormatStyle().year().month().day()
        if let start { query.append(URLQueryItem(name: "start", value: start.formatted(day))) }
        if let end { query.append(URLQueryItem(name: "end", value: end.formatted(day))) }
        return try await decode(request("GET", "api/analytics", query: query))
    }

    // MARK: Sales records

    func salesLedger(start: String, end: String, marketplace: String?, page: Int) async throws -> SalesLedgerPage {
        var query = [URLQueryItem(name: "start", value: start), URLQueryItem(name: "end", value: end),
                     URLQueryItem(name: "page", value: String(page))]
        if let marketplace { query.append(URLQueryItem(name: "marketplace", value: marketplace)) }
        return try await decode(request("GET", "api/sales-ledger", query: query))
    }

    func salesRecord(_ id: String) async throws -> SalesLedgerRow {
        try await decode(request("GET", "api/sales-ledger/\(id)"))
    }

    /// Saves amounts for one order line; blank fields stay unknown.
    func saveSaleAmounts(_ id: String, currency: String, values: [String: String?], revision: Int, basis: String) async throws {
        var body: [String: JSONValue] = ["currency": .string(currency), "expected_revision": .int(revision),
                                         "expected_order_basis": .string(basis)]
        for (field, value) in values { body[field] = value.map(JSONValue.string) ?? .null }
        _ = try await send(request("PUT", "api/sales-ledger/\(id)/financials", json: body))
    }

    /// A CSV export as a file to share.
    func exportCSV(_ path: String, query: [URLQueryItem] = [], filename: String) async throws -> URL {
        let data = try await send(request("GET", path, query: query))
        let url = FileManager.default.temporaryDirectory.appending(path: filename)
        try data.write(to: url, options: .atomic)
        return url
    }

    // MARK: Bulk actions (reviewed first, as on the desktop)

    func previewBulkEdit(ids: [String], edits: BulkEdits) async throws -> [BulkEditReviewRow] {
        struct Body: Encodable { let conversation_ids: [String]; let edits: BulkEdits }
        struct Payload: Decodable { let rows: [BulkEditReviewRow] }
        let payload: Payload = try await decode(request("POST", "api/listings/bulk-edit/preview",
                                                        json: Body(conversation_ids: ids, edits: edits)))
        return payload.rows
    }

    func applyBulkEdit(edits: BulkEdits, rows: [BulkReviewToken]) async throws -> BulkEditResult {
        struct Body: Encodable { let edits: BulkEdits; let rows: [BulkReviewToken] }
        return try await decode(request("POST", "api/listings/bulk-edit/apply", json: Body(edits: edits, rows: rows)))
    }

    func previewBulkDelete(ids: [String]) async throws -> [BulkDeleteReviewRow] {
        struct Payload: Decodable { let rows: [BulkDeleteReviewRow] }
        let payload: Payload = try await decode(request("POST", "api/listings/bulk-delete/preview",
                                                        json: ["conversation_ids": ids]))
        return payload.rows
    }

    /// Deletes only the reviewed items, each with the token from its review.
    func bulkDelete(rows: [BulkDeleteReviewRow]) async throws -> BulkDeleteResult {
        struct Row: Encodable { let conversation_id: String; let review_token: String }
        struct Body: Encodable { let rows: [Row]; let confirm: Bool }
        let reviewed = rows.compactMap { row in row.reviewToken.map { Row(conversation_id: row.conversationID, review_token: $0) } }
        return try await decode(request("POST", "api/listings/bulk-delete/delete", json: Body(rows: reviewed, confirm: true)))
    }

    func previewCopies(ids: [String]) async throws -> [CopyReviewRow] {
        struct Payload: Decodable { let rows: [CopyReviewRow] }
        let payload: Payload = try await decode(request("POST", "api/listings/copies/preview",
                                                        json: ["conversation_ids": ids]))
        return payload.rows
    }

    /// Creates one local draft copy. Returns the new listing's id.
    func copyListing(_ id: String, _ body: CopyRequest) async throws -> String {
        let created: ConversationDetail = try await decode(request("POST", "api/conversations/\(id)/copy", json: body))
        return created.id
    }

    /// Whether an earlier copy request finished, for a reply that was lost.
    func copyResult(_ id: String, requestID: String) async throws -> String {
        let created: ConversationDetail = try await decode(request("GET", "api/conversations/\(id)/copy-result/\(requestID)"))
        return created.id
    }

    func previewMarkdowns(ids: [String]) async throws -> [MarkdownReviewRow] {
        struct Payload: Decodable { let rows: [MarkdownReviewRow] }
        let payload: Payload = try await decode(request("POST", "api/price-drop/batch/preview",
                                                        json: ["conversation_ids": ids]))
        return payload.rows
    }

    /// Saves reviewed prices in Anteroom. Nothing is sent to a marketplace.
    func applyMarkdowns(_ drops: [MarkdownDrop]) async throws -> MarkdownResult {
        try await decode(request("POST", "api/price-drop/batch", json: ["drops": drops]))
    }

    // MARK: Photos

    func photos(listingID: String) async throws -> [ListingPhoto] {
        try await decode(request("GET", "api/conversations/\(listingID)/photos"))
    }

    /// Uploads JPEG bytes. The server re-encodes, strips metadata and limits
    /// size. Like the hosted web app, uploads go to drafts only.
    func uploadPhoto(listingID: String, jpeg: Data) async throws -> ListingPhoto {
        var upload = try await request("POST", "api/drafts/\(listingID)/photos")
        upload.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        upload.httpBody = jpeg
        return try await decode(upload)
    }

    func photoData(listingID: String, photoID: String) async throws -> Data {
        try await send(request("GET", "api/conversations/\(listingID)/photos/\(photoID)/bytes"))
    }

    func photoThumbnailData(photoID: String, size: Int) async throws -> Data {
        try await send(request("GET", "api/photos/\(photoID)/thumb",
                               query: [URLQueryItem(name: "size", value: String(size))]))
    }

    func deletePhoto(listingID: String, photoID: String) async throws {
        _ = try await send(request("DELETE", "api/conversations/\(listingID)/photos/\(photoID)"))
    }

    /// Saves a new photo order; every photo of the listing, each once.
    func reorderPhotos(listingID: String, ids: [String]) async throws {
        _ = try await send(request("PATCH", "api/conversations/\(listingID)/photos/order", json: ids))
    }

    /// Turns the photo a further quarter turn; -90 turns it left.
    func rotatePhoto(listingID: String, photoID: String, degrees: Int) async throws -> ListingPhoto {
        try await decode(request("POST", "api/conversations/\(listingID)/photos/\(photoID)/rotate",
                                 json: ["degrees": degrees]))
    }

    func savePhotoEdits(listingID: String, photoID: String, edits: PhotoEdits) async throws -> ListingPhoto {
        try await decode(request("PUT", "api/conversations/\(listingID)/photos/\(photoID)/edits", json: edits))
    }

    func resetPhotoEdits(listingID: String, photoID: String) async throws -> ListingPhoto {
        try await decode(request("DELETE", "api/conversations/\(listingID)/photos/\(photoID)/edits"))
    }

    /// A smaller JPEG of unsaved edits, rendered by the server.
    func previewPhotoEdits(listingID: String, photoID: String, edits: PhotoEdits) async throws -> Data {
        try await send(request("POST", "api/conversations/\(listingID)/photos/\(photoID)/edits/preview", json: edits))
    }

    // MARK: Shared workspace and durable draft tasks

    func capabilities() async throws -> WorkspaceCapabilities {
        try await decode(request("GET", "api/runtime"))
    }

    func workspaceStatus() async throws -> WorkspaceStatus {
        try await decode(request("GET", "api/status"))
    }

    func messages(_ id: String) async throws -> [ListingMessage] {
        try await decode(request("GET", "api/conversations/\(id)/messages"))
    }

    func activity(_ id: String) async throws -> ListingActivity {
        try await decode(request("GET", "api/conversations/\(id)/activity"))
    }

    /// Regenerate's wipe: keeps photos, measurements, flaws, cost, labels and notes.
    func resetListing(_ id: String, keepInputs: Bool) async throws {
        _ = try await send(request("POST", "api/conversations/\(id)/reset", json: ["keep_inputs": keepInputs]))
    }

    func stopTask(_ id: String) async throws {
        _ = try await send(request("POST", "api/conversations/\(id)/stop"))
    }

    /// Disconnecting this stream never cancels or retries server work. Only
    /// reconnect uses resume=true; it can observe work but cannot enqueue it.
    func runAssistant(_ id: String, action: AssistantAction,
                      receive: @MainActor @Sendable (StreamFrame) -> Void) async throws {
        var upload: URLRequest
        switch action {
        case .generate, .regenerate:
            upload = try await request("POST", "api/conversations/\(id)/generate")
        case .message(let text):
            upload = try await request("POST", "api/conversations/\(id)/messages", json: ["text": text])
        case .reconnect:
            upload = try await request("POST", "api/conversations/\(id)/generate",
                                       query: [URLQueryItem(name: "resume", value: "true")])
        }
        upload.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        upload.timeoutInterval = 300
        let (bytes, response) = try await session.bytes(for: upload)
        guard let http = response as? HTTPURLResponse else {
            throw APIError(status: 0, message: "Anteroom didn't answer. Reconnect to check your draft.")
        }
        guard (200..<300).contains(http.statusCode) else {
            var data = Data()
            for try await byte in bytes {
                if data.count >= 65536 { break }
                data.append(byte)
            }
            throw APIError(status: http.statusCode, message: Self.message(from: data, status: http.statusCode))
        }
        var frames = StreamFrames()
        for try await line in bytes.lines {
            try Task.checkCancellation()
            guard let frame = frames.accept(line) else { continue }
            if frame.event == "error" { throw APIError(status: 0, message: frame.data) }
            if frame.data == "[DONE]" { return }
            await receive(frame)
        }
        throw APIError(status: 0, message: "The connection ended. Reconnect to check your draft before trying again.")
    }

    // MARK: Shared listing history

    func revisions(_ id: String) async throws -> [ListingRevision] {
        try await decode(request("GET", "api/conversations/\(id)/revisions"))
    }

    func revisionPointer(_ id: String) async throws -> ListingRevisionPointer {
        try await decode(request("GET", "api/conversations/\(id)/listing"))
    }

    func revision(_ id: String, revisionID: String) async throws -> RevisionSnapshot {
        try await decode(request("GET", "api/conversations/\(id)/revisions/\(revisionID)"))
    }

    func restoreRevision(_ id: String, revisionID: String, expectedRevisionID: String) async throws {
        _ = try await send(request("POST", "api/conversations/\(id)/revisions/\(revisionID)/restore",
                                   json: ["expected_revision_id": expectedRevisionID]))
    }

    // MARK: Shared appearance preferences

    func appearance() async throws -> AppearancePreferences {
        try await decode(request("GET", "api/settings/ui"))
    }

    func setAppearance(_ theme: AppearanceTheme) async throws -> AppearancePreferences {
        try await decode(request("PUT", "api/settings/ui", json: AppearanceUpdate(theme: theme)))
    }

    func setListingNames(_ names: ListingNames) async throws -> AppearancePreferences {
        try await decode(request("PUT", "api/settings/ui", json: AppearanceUpdate(listingNames: names)))
    }

    /// Every change to Anteroom, newest first.
    func changelog() async throws -> Changelog {
        try await decode(request("GET", "api/changelog"))
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
