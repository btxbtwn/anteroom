import Foundation

struct APIError: LocalizedError, Sendable {
    let status: Int
    let message: String

    var errorDescription: String? { message }
    var isConflict: Bool { status == 409 }

    static let signInEnded = "Your sign-in has ended. Sign in again."
    static let signInRenewed = "Your sign-in was renewed. Try that again."
    static let signInNotRenewed = "Anteroom couldn't renew your sign-in. Check your connection and try again."
}

/// How the API client gets the seller's token, and what it does when the server refuses it.
struct APIAuthorization: Sendable {
    /// The current access token; the sign-in library refreshes it when it expires.
    /// Throws `SignInRejected` when the server refused the saved sign-in.
    var accessToken: @Sendable () async throws -> String
    /// A newly issued access token. Throws `SignInRejected` when the server
    /// refused the saved sign-in; any other error leaves it to try again.
    var refreshedToken: @Sendable () async throws -> String
    /// The sign-in has ended: go back to the sign-in screen.
    var signedOut: @Sendable () async -> Void
}

/// Client for the hosted Anteroom API (`vendoo_studio.hosted:app`). Every
/// request carries the seller's current Supabase access token.
///
/// When the server refuses the token (401) the client renews the sign-in once.
/// A read (GET) is then sent again; anything else is not, because it may have
/// already run. When the sign-in server refuses to renew it the seller is
/// signed out; a renewal that only failed (offline, a server error) is not.
///
/// A read the server can't answer yet (it is restarting, or the connection
/// dropped) is tried again for a while before its error shows; see `ReadRetry`.
final class AnteroomAPI: Sendable {
    private let baseURL: URL
    private let auth: APIAuthorization
    private let session: URLSession
    private let readRetry: ReadRetry
    private let thumbnails = RequestGate(limit: 4)
    private let choiceLists = ChoiceListCache()
    private let immutable: ImmutableReadCache
    private let runtimeCache = SharedRead<WorkspaceRuntime>(maxAge: 30)
    private let selectedCache = SharedRead<[String]>(maxAge: 30)
    private let optionsCache = SharedRead<GeneralOptions>(maxAge: .infinity)

    /// Where CSV exports are written for the share sheet; emptied on sign-out.
    static var exportsDirectory: URL {
        FileManager.default.temporaryDirectory.appending(path: "Exports", directoryHint: .isDirectory)
    }

    init(baseURL: URL, auth: APIAuthorization, configuration: URLSessionConfiguration = .ephemeral,
         readRetry: ReadRetry = .standard, immutableCache: ImmutableReadCache = ImmutableReadCache()) {
        self.baseURL = baseURL
        self.auth = auth
        self.readRetry = readRetry
        immutable = immutableCache
        session = URLSession(configuration: configuration)
    }

    /// Forgets what belongs to the signed-in seller: cached server answers,
    /// kept thumbnails and lists, and exported files.
    func forgetSeller() async {
        await runtimeCache.clear()
        await selectedCache.clear()
        await optionsCache.clear()
        await immutable.clear()
        try? FileManager.default.removeItem(at: Self.exportsDirectory)
    }

    // MARK: Listings (the shared workspace contract the desktop and web use)

    /// The whole inventory; like the desktop sidebar, the phone filters and
    /// sorts it locally.
    func listings() async throws -> [ConversationSummary] {
        try await decode(request("GET", "api/conversations"))
    }

    /// An untitled draft, like the web's New listing; generation names it.
    /// Bulk listing passes each item's Item Details notes and its box.
    func createListing(notes: String? = nil, boxID: String? = nil) async throws -> ConversationSummary {
        try await decode(request("POST", "api/conversations", json: ConversationInput(notes: notes, boxID: boxID)))
    }

    /// The boxes the seller bought stock in, newest first as the server orders them.
    func boxes() async throws -> [SourceBox] {
        struct Page: Decodable { let boxes: [SourceBox] }
        let page: Page = try await decode(request("GET", "api/boxes"))
        return page.boxes
    }

    /// Suggests an item's measurements from photos not uploaded yet, the way
    /// the web's bulk upload does. The server reads at most 20. Saves nothing.
    func readMeasurements(jpegs: [Data], garment: SellerInputs.Garment) async throws -> MeasurementReading {
        let boundary = "anteroom-\(UUID().uuidString)"
        var body = Data()
        for (index, jpeg) in jpegs.prefix(20).enumerated() {
            body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"files\"; filename=\"photo\(index).jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".utf8))
            body.append(jpeg)
            body.append(Data("\r\n".utf8))
        }
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"garment\"\r\n\r\n\(garment.rawValue)\r\n--\(boundary)--\r\n".utf8))
        var upload = try await request("POST", "api/measurements/read")
        upload.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        upload.httpBody = body
        // Each reading is several photo-model calls.
        upload.timeoutInterval = 180
        return try await decode(upload)
    }

    /// The editor's first read in one request: the listing, its notes and its photos.
    func listingScreen(_ id: String) async throws -> ListingScreen {
        try await decode(request("GET", "api/conversations/\(id)/screen"))
    }

    func conversation(_ id: String) async throws -> Conversation {
        try await decode(request("GET", "api/conversations/\(id)"))
    }

    func listing(_ id: String) async throws -> ListingDocument {
        try await decode(request("GET", "api/conversations/\(id)/listing"))
    }

    func saveListing(_ id: String, _ body: ListingSave) async throws -> ListingSaveResult {
        try await decode(request("PUT", "api/conversations/\(id)/listing", json: body))
    }

    func saveNotes(_ id: String, notes: String) async throws {
        _ = try await send(request("PATCH", "api/conversations/\(id)", json: ConversationInput(notes: notes)))
    }

    func rename(_ id: String, title: String) async throws {
        _ = try await send(request("PATCH", "api/conversations/\(id)", json: ConversationInput(title: title)))
    }

    /// The server only deletes listings that were never sent anywhere.
    func deleteListing(_ id: String) async throws {
        _ = try await send(request("DELETE", "api/conversations/\(id)"))
    }

    func discardBlankListing(_ id: String) async throws -> Bool {
        struct Answer: Decodable { let discarded: Bool }
        let answer: Answer = try await decode(request("POST", "api/conversations/\(id)/discard-blank"))
        return answer.discarded
    }

    /// Settings → Listings → Where you list, in the seller's order. Screens
    /// asking within 30 seconds share one answer; changing it asks again.
    func selectedMarketplaces() async throws -> [String] {
        try await selectedCache.value(fresh: false) {
            struct Payload: Decodable { let selected: [String] }
            let payload: Payload = try await self.decode(self.request("GET", "api/settings/marketplaces"))
            return payload.selected
        }
    }

    /// The desktop's General dropdown lists (Condition, colors, Size). They
    /// come with the app's release, so one answer serves the whole sign-in.
    func generalOptions() async throws -> GeneralOptions {
        try await optionsCache.value(fresh: false) {
            struct Payload: Decodable { let forms: [String: [String: [String]]] }
            let payload: Payload = try await self.decode(self.request("GET", "api/catalog/dropdown-options"))
            return GeneralOptions(vendoo: payload.forms["vendoo"] ?? [:])
        }
    }

    /// The inventory's ranked next steps.
    func suggestions() async throws -> [Suggestion] {
        struct Payload: Decodable { let suggestions: [Suggestion] }
        let payload: Payload = try await decode(request("GET", "api/suggestions"))
        return payload.suggestions
    }

    /// What this workspace offers (`/api/runtime`): views, features, the
    /// marketplace tabs it edits natively and the marketplaces it can list on
    /// now. Screens asking at about the same time share one request, and an
    /// answer is reused for 30 seconds; `fresh` asks the server again.
    func runtime(fresh: Bool = false) async throws -> WorkspaceRuntime {
        try await runtimeCache.value(fresh: fresh) { try await self.decode(self.request("GET", "api/runtime")) }
    }

    /// Which required fields each marketplace still needs, for the saved listing.
    func marketplacePreflight(_ id: String) async throws -> NativePreflight {
        try await decode(request("GET", "api/conversations/\(id)/native-marketplaces/preflight"))
    }

    func nativeMarketplaceStates(_ id: String) async throws -> [String: NativeDraftState] {
        let states: [String: NativeDraftState?] = try await decode(request("GET", "api/conversations/\(id)/native-marketplaces"))
        return states.compactMapValues { $0 }
    }

    func marketplaceLiveListings(_ id: String) async throws -> [String: MarketplaceLiveListing] {
        let listings: [String: MarketplaceLiveListing?] = try await decode(request("GET", "api/conversations/\(id)/marketplace-listings"))
        return listings.compactMapValues { $0 }
    }

    /// Lists the saved revision on one marketplace the seller named and confirmed.
    func listOnMarketplace(_ id: String, marketplace: String, revisionID: String, confirmation: SellerConfirmation) async throws {
        try Self.require(confirmation, .list, [marketplace])
        struct Body: Encodable { let revision_id: String; let confirm: Bool }
        _ = try await send(request("POST", "api/conversations/\(id)/native-marketplaces/\(marketplace)/list",
                                   json: Body(revision_id: revisionID, confirm: true)))
    }

    /// Sends the saved revision to the live listing on one marketplace the seller named and confirmed.
    func updateOnMarketplace(_ id: String, marketplace: String, revisionID: String, confirmation: SellerConfirmation) async throws {
        try Self.require(confirmation, .update, [marketplace])
        struct Body: Encodable { let revision_id: String; let confirm: Bool }
        _ = try await send(request("POST", "api/conversations/\(id)/native-marketplaces/\(marketplace)/update",
                                   json: Body(revision_id: revisionID, confirm: true)))
    }

    /// Saves the revision as an unpublished draft on the marketplace; buyers cannot see it.
    func saveMarketplaceDraft(_ id: String, marketplace: String, revisionID: String) async throws {
        struct Body: Encodable { let revision_id: String }
        _ = try await send(request("POST", "api/conversations/\(id)/native-marketplaces/\(marketplace)/draft",
                                   json: Body(revision_id: revisionID)))
    }

    /// Asks the marketplace what an unconfirmed List or Update did. It never acts again.
    func checkMarketplaceResult(_ id: String, marketplace: String) async throws {
        _ = try await send(request("POST", "api/conversations/\(id)/native-marketplaces/\(marketplace)/check",
                                   json: [String: String]()))
    }

    /// Stops a running List, Update or Save draft.
    func cancelMarketplaceAction(_ id: String, marketplace: String) async throws {
        _ = try await send(request("POST", "api/conversations/\(id)/native-marketplaces/\(marketplace)/cancel"))
    }

    /// Delists from the marketplaces the seller named and confirmed.
    func delist(_ id: String, marketplaces: [String], confirmation: SellerConfirmation) async throws -> [String: MarketplaceLiveListing] {
        try Self.require(confirmation, .delist, marketplaces)
        struct Body: Encodable { let marketplaces: [String]; let confirm: Bool }
        let listings: [String: MarketplaceLiveListing?] = try await decode(request(
            "POST", "api/conversations/\(id)/marketplace-listings/delist", json: Body(marketplaces: marketplaces, confirm: true)))
        return listings.compactMapValues { $0 }
    }

    /// Refuses before anything is sent unless the seller confirmed this action on these marketplaces.
    private static func require(_ confirmation: SellerConfirmation, _ action: SellerConfirmation.Action, _ marketplaces: [String]) throws {
        guard confirmation.covers(action, marketplaces) else {
            throw APIError(status: 0, message: "Nothing was sent. Confirm which marketplaces to \(action.rawValue) first.")
        }
    }

    /// A marketplace's synced category tree (eBay's is about 20,000 nodes).
    func catalogCategories(marketplace: String) async throws -> CatalogCategories {
        try await decode(request("GET", "api/catalog/categories", query: [URLQueryItem(name: "marketplace", value: marketplace)]))
    }

    /// eBay's own category search, through the seller's connected account.
    func searchEbayCategories(_ query: String) async throws -> [CategoryLeaf] {
        struct Page: Decodable {
            struct Row: Decodable { let id: String; let title: String?; let subtitle: String? }
            let rows: [Row]?
        }
        let page: Page = try await decode(request("GET", "api/ebay/read/categories", query: [URLQueryItem(name: "q", value: query)]))
        return (page.rows ?? []).map { row in
            CategoryLeaf(id: row.id, path: [row.subtitle, row.title].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " › "))
        }
    }

    /// A marketplace tab's form, with the shared lists it names downloaded side by side.
    func nativeForm(_ id: String, marketplace: String) async throws -> NativeForm {
        let form: NativeForm = try await decode(request("GET", "api/conversations/\(id)/native-marketplaces/\(marketplace)/form"))
        let refs = Set(form.fields.compactMap(\.choiceList))
        let lists = try await withThrowingTaskGroup(of: (ChoiceListRef, ChoiceList).self,
                                                    returning: [ChoiceListRef: ChoiceList].self) { group in
            for ref in refs {
                group.addTask {
                    let list = try await self.choiceList(ref)
                    return (ref, list)
                }
            }
            var lists: [ChoiceListRef: ChoiceList] = [:]
            for try await (ref, list) in group { lists[ref] = list }
            return lists
        }
        return form.filled(with: lists)
    }

    /// A shared list by version: from memory, else from disk, else downloaded once.
    private func choiceList(_ ref: ChoiceListRef) async throws -> ChoiceList {
        try await choiceLists.list(ref) { () async throws -> ChoiceList in
            let data = try await self.immutableRead(
                "choice-list/\(ref.name)/\(ref.version)", "api/catalog/choice-lists/\(ref.name)",
                query: [URLQueryItem(name: "v", value: ref.version)])
            return try JSONDecoder().decode(ChoiceList.self, from: data)
        }
    }

    // MARK: Listings settings (hosted rows of Settings → Listings)

    func marketplaceSettings() async throws -> MarketplaceSettings {
        try await decode(request("GET", "api/settings/marketplaces"))
    }

    func setMarketplaces(_ selected: [String]) async throws -> MarketplaceSettings {
        let settings: MarketplaceSettings = try await decode(request("PUT", "api/settings/marketplaces", json: ["selected": selected]))
        await selectedCache.clear()
        return settings
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

    func saveMimoKey(_ key: String) async throws {
        _ = try await send(request("PUT", "api/settings/provider", json: ["api_key": key]))
    }

    func removeMimoKey() async throws {
        _ = try await send(request("DELETE", "api/settings/provider/key"))
    }

    func testMimo() async throws -> String? {
        struct Payload: Decodable { let ok: Bool; let error: String?; let message: String? }
        let payload: Payload = try await decode(request("POST", "api/settings/provider/mimo/test"))
        return payload.ok ? nil : (payload.error ?? payload.message ?? "The connection failed.")
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

    // MARK: Listing templates

    func templates() async throws -> [ListingTemplate] {
        struct Payload: Decodable { let templates: [ListingTemplate] }
        let payload: Payload = try await decode(request("GET", "api/listing-templates"))
        return payload.templates
    }

    func templateSource(_ listingID: String) async throws -> TemplateSource {
        try await decode(request("GET", "api/listing-templates/source/\(listingID)"))
    }

    /// Saves the checked fields as a new template, or replaces an existing one's fields.
    func saveTemplate(name: String, source: TemplateSource, fields: [String], replacing: ListingTemplate?) async throws {
        var body: [String: JSONValue] = [
            "name": .string(name),
            "selection": .object(["source_id": .string(source.sourceID), "expected_basis": .string(source.expectedBasis),
                                  "fields": .array(fields.map(JSONValue.string))]),
        ]
        if let replacing { body["expected_revision"] = .int(replacing.revision) }
        let path = replacing.map { "api/listing-templates/\($0.id)" } ?? "api/listing-templates"
        _ = try await send(request(replacing == nil ? "POST" : "PUT", path, json: body))
    }

    func renameTemplate(_ template: ListingTemplate, to name: String) async throws {
        _ = try await send(request("PUT", "api/listing-templates/\(template.id)",
                                   json: ["name": JSONValue.string(name), "expected_revision": .int(template.revision)]))
    }

    func deleteTemplate(_ template: ListingTemplate) async throws {
        _ = try await send(request("DELETE", "api/listing-templates/\(template.id)",
                                   query: [URLQueryItem(name: "expected_revision", value: String(template.revision))]))
    }

    /// A new draft from a template. The request id makes a retry safe.
    func draftFromTemplate(_ template: ListingTemplate, requestID: String, title: String, sku: String, quantity: Int) async throws -> String {
        struct Payload: Decodable { let conversation_id: String }
        let body: [String: JSONValue] = ["request_id": .string(requestID), "expected_revision": .int(template.revision),
                                         "title": .string(title), "sku": .string(sku), "quantity": .int(quantity)]
        let payload: Payload = try await decode(request("POST", "api/listing-templates/\(template.id)/draft", json: body))
        return payload.conversation_id
    }

    func templateDraftResult(_ template: ListingTemplate, requestID: String) async throws -> String {
        struct Payload: Decodable { let conversation_id: String }
        let payload: Payload = try await decode(request("GET", "api/listing-templates/\(template.id)/draft-result/\(requestID)"))
        return payload.conversation_id
    }

    func previewTemplate(_ template: ListingTemplate, listingID: String, mode: String) async throws -> TemplateReview {
        try await decode(request("POST", "api/listing-templates/\(template.id)/apply-preview", json: [
            "conversation_id": JSONValue.string(listingID), "expected_template_revision": .int(template.revision), "mode": .string(mode),
        ]))
    }

    func applyTemplate(_ template: ListingTemplate, listingID: String, mode: String, review: TemplateReview) async throws {
        _ = try await send(request("POST", "api/listing-templates/\(template.id)/apply", json: [
            "conversation_id": JSONValue.string(listingID), "expected_template_revision": .int(template.revision), "mode": .string(mode),
            "expected_revision_id": .string(review.expectedRevisionID), "expected_basis": .string(review.expectedBasis),
            "expected_result": .string(review.expectedResult),
        ]))
    }

    // MARK: Similar items

    func similarItems() async throws -> (pairs: [SimilarPair], total: Int) {
        struct Payload: Decodable { let total: Int; let pairs: [SimilarPair] }
        let payload: Payload = try await decode(request("GET", "api/similar-items"))
        return (payload.pairs, payload.total)
    }

    func dismissSimilar(_ first: String, _ second: String) async throws {
        _ = try await send(request("POST", "api/similar-items/dismiss", json: ["first_id": first, "second_id": second]))
    }

    /// Moves the other item's links, sales and labels to the kept one. Nothing is deleted or delisted.
    func mergeSimilar(keep: String, merge: String) async throws {
        _ = try await send(request("POST", "api/similar-items/merge", json: ["keep_id": keep, "merge_id": merge]))
    }

    func labelPreferences() async throws -> LabelPreferences {
        try await decode(request("GET", "api/settings/ui"))
    }

    /// Sends only the label keys being changed.
    func updateLabelPreferences(_ change: [String: JSONValue]) async throws -> LabelPreferences {
        try await decode(request("PUT", "api/settings/ui", json: change))
    }

    // MARK: Automations (never publish or delist on their own)

    func automations() async throws -> [Automation] {
        try await decode(request("GET", "api/automations"))
    }

    /// Reads a sentence into a draft; nothing is saved.
    func draftAutomation(prompt: String, timezone: String) async throws -> AutomationDraft {
        try await decode(request("POST", "api/automations/draft", json: ["prompt": prompt, "timezone": timezone]))
    }

    func previewAutomation(_ body: AutomationBody) async throws -> AutomationPreview {
        try await decode(request("POST", "api/automations/preview", json: body))
    }

    func saveAutomation(_ body: AutomationBody, id: String?) async throws {
        _ = try await send(request(id == nil ? "POST" : "PATCH", id.map { "api/automations/\($0)" } ?? "api/automations", json: body))
    }

    func pauseAutomation(_ id: String) async throws {
        _ = try await send(request("POST", "api/automations/\(id)/pause"))
    }

    func resumeAutomation(_ id: String) async throws {
        _ = try await send(request("POST", "api/automations/\(id)/resume"))
    }

    func deleteAutomation(_ id: String) async throws {
        _ = try await send(request("DELETE", "api/automations/\(id)"))
    }

    func runAutomation(_ id: String, dryRun: Bool) async throws -> AutomationRun {
        try await decode(request("POST", "api/automations/\(id)/run", json: ["dry_run": dryRun]))
    }

    func automationHistory(_ id: String) async throws -> [AutomationRun] {
        struct Payload: Decodable { let runs: [AutomationRun] }
        let payload: Payload = try await decode(request("GET", "api/automations/\(id)/history"))
        return payload.runs
    }

    func automationApprovals() async throws -> [AutomationApproval] {
        try await decode(request("GET", "api/automations/approvals"))
    }

    // MARK: eBay offers (no listing or delisting; sending and automation carry `confirm`)

    func ebayOffers() async throws -> EbayOffersStatus {
        try await decode(request("GET", "api/ebay-offers"))
    }

    /// Prices offers for the chosen listings; nothing is sent.
    func previewEbayOffers(_ body: EbayOfferPreviewRequest) async throws -> [EbayOffer] {
        let result: EbayOfferPreviews = try await decode(request("POST", "api/ebay-offers/preview", json: body))
        return result.offers
    }

    func sendEbayOffers(_ attemptIDs: [String]) async throws -> [EbayOffer] {
        let result: EbayOfferPreviews = try await decode(request("POST", "api/ebay-offers/send", json: EbayOfferApproval(attemptIDs: attemptIDs)))
        return result.offers
    }

    func enableEbayOffers(_ attemptIDs: [String]) async throws {
        _ = try await send(request("POST", "api/ebay-offers/enable", json: EbayOfferApproval(attemptIDs: attemptIDs)))
    }

    func enableAllEbayOffers(_ body: EbayOffersEnableAll) async throws {
        _ = try await send(request("POST", "api/ebay-offers/enable-all", json: body))
    }

    /// Pauses one listing's rule, or every rule and the all-listings setting.
    func pauseEbayOffers(ruleID: String? = nil) async throws {
        _ = try await send(request("POST", ruleID.map { "api/ebay-offers/rules/\($0)/pause" } ?? "api/ebay-offers/pause"))
    }

    func markEbayOfferChecked(_ attemptID: String) async throws {
        _ = try await send(request("POST", "api/ebay-offers/attempts/\(attemptID)/checked", json: ["confirm": true]))
    }

    /// Starts eBay sign-in. The seller approves on the page this returns, and
    /// Safari comes back to the app, which finishes with `finishMarketplaceSignIn`.
    func connectEbay() async throws -> URL {
        struct Payload: Decodable { let authorizeUrl: URL }
        let payload: Payload = try await decode(request("POST", "api/marketplace-accounts/ebay/connect", json: ["client": "app"]))
        return payload.authorizeUrl
    }

    /// Hands over the one-time claim Safari came back with. Returns the account name.
    func finishMarketplaceSignIn(_ signIn: MarketplaceSignInReturn) async throws -> String? {
        struct Payload: Decodable { let label: String? }
        let payload: Payload = try await decode(request("POST", "api/marketplace-accounts/\(signIn.marketplace)/finish",
                                                        json: ["state": signIn.state, "claim": signIn.claim]))
        return payload.label
    }

    // MARK: Sharing and offers to likers (saved off; on only with `confirm`)

    func promotionAutomations() async throws -> PromotionAutomationList {
        try await decode(request("GET", "api/promotion-automations"))
    }

    /// Creates or updates a rule. Saving always leaves it off.
    func savePromotionAutomation(_ body: PromotionAutomationBody, id: String?) async throws {
        let path = id.map { "api/promotion-automations/\($0)" } ?? "api/promotion-automations"
        _ = try await send(request(id == nil ? "POST" : "PUT", path, json: body))
    }

    func enablePromotionAutomation(_ id: String) async throws {
        _ = try await send(request("POST", "api/promotion-automations/\(id)/enable", json: ["confirm": true]))
    }

    func pausePromotionAutomation(_ id: String) async throws {
        _ = try await send(request("POST", "api/promotion-automations/\(id)/pause"))
    }

    func deletePromotionAutomation(_ id: String) async throws {
        _ = try await send(request("DELETE", "api/promotion-automations/\(id)"))
    }

    func promotionCoverage(_ id: String) async throws -> PromotionCoverage {
        try await decode(request("GET", "api/promotion-automations/\(id)/coverage"))
    }

    func promotionActivity(_ id: String) async throws -> PromotionActivity {
        try await decode(request("GET", "api/promotion-automations/\(id)/activity"))
    }

    /// Individual offer minimums, keyed by item id; an empty value clears one.
    func savePromotionFloors(marketplace: String, floors: [String: String]) async throws {
        struct Body: Encodable { let marketplace: String; let floors: [String: String] }
        _ = try await send(request("PUT", "api/promotions/floors", json: Body(marketplace: marketplace, floors: floors)))
    }

    /// Asks the marketplace what happened to an unconfirmed send.
    func checkPromotion(_ id: String) async throws {
        _ = try await send(request("POST", "api/promotions/\(id)/check"))
    }

    func markPromotionChecked(_ id: String) async throws {
        _ = try await send(request("POST", "api/promotions/\(id)/checked", json: ["confirm": true]))
    }

    func relistGaps() async throws -> [RelistGap] {
        try await decode(request("GET", "api/errors"))
    }

    // MARK: Account

    /// Deletes the signed-in seller's Anteroom account: listings, photos, records,
    /// settings and stored sign-ins. Live marketplace listings are left alone.
    func deleteAccount() async throws {
        struct Body: Encodable { let confirm: Bool }
        _ = try await send(request("DELETE", "api/account", json: Body(confirm: true)))
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

    /// Figures, sales per week and sales by marketplace for Sales records' dates (whole UTC days).
    func salesOverview(start: String, end: String, marketplace: String?) async throws -> SalesOverview {
        var query = [URLQueryItem(name: "start", value: start), URLQueryItem(name: "end", value: end)]
        if let marketplace { query.append(URLQueryItem(name: "marketplace", value: marketplace)) }
        return try await decode(request("GET", "api/analytics/overview", query: query))
    }

    func salesRecord(_ id: String) async throws -> SalesLedgerRow {
        try await decode(request("GET", "api/sales-ledger/\(id)"))
    }

    /// Saves amounts for one order line; blank fields stay unknown.
    func saveSaleAmounts(_ id: String, currency: String, values: [String: String?], revision: Int, basis: String, soldOn: String?) async throws {
        var body: [String: JSONValue] = ["currency": .string(currency), "expected_revision": .int(revision),
                                         "expected_order_basis": .string(basis)]
        body["sold_on"] = soldOn.map(JSONValue.string) ?? .null
        for (field, value) in values { body[field] = value.map(JSONValue.string) ?? .null }
        _ = try await send(request("PUT", "api/sales-ledger/\(id)/financials", json: body))
    }

    /// A CSV export as a file to share. Delete it when the share sheet closes;
    /// sign-out empties the folder too.
    func exportCSV(_ path: String, query: [URLQueryItem] = [], filename: String) async throws -> URL {
        let data = try await send(request("GET", path, query: query))
        try FileManager.default.createDirectory(at: Self.exportsDirectory, withIntermediateDirectories: true)
        let url = Self.exportsDirectory.appending(path: filename)
        try data.write(to: url, options: [.atomic, .completeFileProtection])
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
        let created: Conversation = try await decode(request("POST", "api/conversations/\(id)/copy", json: body))
        return created.id
    }

    /// Whether an earlier copy request finished, for a reply that was lost.
    func copyResult(_ id: String, requestID: String) async throws -> String {
        let created: Conversation = try await decode(request("GET", "api/conversations/\(id)/copy-result/\(requestID)"))
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

    /// The item's photos, or with `marketplace` the photos added on that form only.
    func photos(listingID: String, marketplace: String? = nil) async throws -> [ListingPhoto] {
        try await decode(request("GET", "api/conversations/\(listingID)/photos",
                                 query: marketplace.map { [URLQueryItem(name: "marketplace", value: $0)] } ?? []))
    }

    /// Uploads JPEG bytes. The server re-encodes, strips metadata and limits
    /// size. Like the hosted web app, uploads go to drafts only.
    func uploadPhoto(listingID: String, jpeg: Data, marketplace: String? = nil) async throws -> ListingPhoto {
        var upload = try await request("POST", "api/drafts/\(listingID)/photos",
                                       query: marketplace.map { [URLQueryItem(name: "marketplace", value: $0)] } ?? [])
        upload.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        upload.httpBody = jpeg
        return try await decode(upload)
    }

    func photoData(listingID: String, photoID: String) async throws -> Data {
        try await send(request("GET", "api/conversations/\(listingID)/photos/\(photoID)/bytes"))
    }

    /// A photo's thumbnail at one of the sizes the server keeps. With the
    /// photo's `version` the server marks it immutable, and it is kept on disk.
    ///
    /// At most four downloads run at a time: the server resizes each one while
    /// holding one of its few database connections, so a screen of covers at
    /// once would queue a seller's save behind them. An `urgent` thumbnail (the
    /// open listing's) goes ahead of waiting inventory covers, and a cover whose
    /// row scrolled away leaves the queue.
    func photoThumbnailData(photoID: String, size: Int, version: String? = nil, urgent: Bool = false) async throws -> Data {
        let size = ThumbnailSize.snapped(size)
        let key = version.map { "thumb/" + ThumbnailSize.key(photoID: photoID, version: $0, size: size) }
        if let key, let kept = await immutable.data(for: key) { return kept }
        let generation = await immutable.generation
        try await thumbnails.enter(urgent: urgent)
        do {
            var query = [URLQueryItem(name: "size", value: String(size))]
            if let version { query.append(URLQueryItem(name: "v", value: version)) }
            let data = try await send(request("GET", "api/photos/\(photoID)/thumb", query: query))
            await thumbnails.leave()
            if let key { await immutable.store(data, for: key, generation: generation) }
            return data
        } catch {
            await thumbnails.leave()
            throw error
        }
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
    /// `crop: false` leaves the frame whole so the crop can be drawn on it.
    func previewPhotoEdits(listingID: String, photoID: String, edits: PhotoEdits, crop: Bool = true) async throws -> Data {
        var preview = try await request("POST", "api/conversations/\(listingID)/photos/\(photoID)/edits/preview",
                                        json: edits)
        if !crop, let url = preview.url { preview.url = url.appending(queryItems: [URLQueryItem(name: "crop", value: "false")]) }
        return try await send(preview)
    }

    // MARK: Shared workspace and durable draft tasks

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
        case .message(let text, let attachments):
            struct Body: Encodable { let text: String; let attachments: [ChatAttachment] }
            upload = try await request("POST", "api/conversations/\(id)/messages", json: Body(text: text, attachments: attachments))
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
            if http.statusCode == 401 { throw try await renewAfterRefusal(data).error }
            throw APIError(status: http.statusCode, message: Self.message(from: data, status: http.statusCode))
        }
        var finished = false
        try await bytes.forEachFrame { frame in
            try Task.checkCancellation()
            if frame.event == "error" { throw APIError(status: 0, message: frame.data) }
            if frame.data == "[DONE]" {
                finished = true
                return false
            }
            await receive(frame)
            return true
        }
        if !finished {
            throw APIError(status: 0, message: "The connection ended. Reconnect to check your draft before trying again.")
        }
    }

    // MARK: Business assistant

    func assistantThreads() async throws -> [AssistantThread] {
        try await decode(request("GET", "api/assistant/threads"))
    }

    func createAssistantThread() async throws -> AssistantThread {
        try await decode(request("POST", "api/assistant/threads"))
    }

    func deleteAssistantThread(_ id: String) async throws {
        _ = try await send(request("DELETE", "api/assistant/threads/\(id)"))
    }

    func assistantMessages(thread: String) async throws -> [AssistantTurn] {
        try await decode(request("GET", "api/assistant/messages", query: [URLQueryItem(name: "thread_id", value: thread)]))
    }

    /// Ask in a conversation and follow the answer. The server keeps writing it
    /// if this connection drops; `resumeAssistant` picks it back up. Returns the
    /// assistant's own error, if it reported one. A refusal throws an APIError
    /// with the HTTP status; any other error is a dropped connection.
    func askAssistant(_ text: String, attachments: [ChatAttachment], thread: String,
                      receive: @MainActor @Sendable (StreamFrame) -> Void) async throws -> String? {
        struct Body: Encodable {
            let text: String
            let attachments: [ChatAttachment]
            let threadID: String
            enum CodingKeys: String, CodingKey { case text, attachments; case threadID = "thread_id" }
        }
        return try await followAssistant(request("POST", "api/assistant/messages",
                                                 json: Body(text: text, attachments: attachments, threadID: thread)), receive: receive)
    }

    /// Follow the answer in progress from its start; ends at once when there is none in this conversation.
    func resumeAssistant(thread: String, receive: @MainActor @Sendable (StreamFrame) -> Void) async throws -> String? {
        try await followAssistant(request("POST", "api/assistant/messages/resume",
                                          query: [URLQueryItem(name: "thread_id", value: thread)]), receive: receive)
    }

    /// Stop the answer in progress; what it had written is kept.
    func stopAssistant() async throws {
        _ = try await send(request("POST", "api/assistant/messages/stop"))
    }

    func applyAssistantChanges(_ messageID: String) async throws -> AssistantTurn {
        try await decode(request("POST", "api/assistant/messages/\(messageID)/changes/apply"))
    }

    func dismissAssistantChanges(_ messageID: String) async throws -> AssistantTurn {
        try await decode(request("POST", "api/assistant/messages/\(messageID)/changes/dismiss"))
    }

    func confirmAssistantAutomation(_ messageID: String) async throws -> AssistantTurn {
        try await decode(request("POST", "api/assistant/messages/\(messageID)/automation/confirm"))
    }

    func dismissAssistantAutomation(_ messageID: String) async throws -> AssistantTurn {
        try await decode(request("POST", "api/assistant/messages/\(messageID)/automation/dismiss"))
    }

    private func followAssistant(_ request: URLRequest, receive: @MainActor @Sendable (StreamFrame) -> Void) async throws -> String? {
        var upload = request
        upload.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        upload.timeoutInterval = 300
        let (bytes, response) = try await session.bytes(for: upload)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        guard (200..<300).contains(http.statusCode) else {
            var data = Data()
            for try await byte in bytes {
                if data.count >= 65536 { break }
                data.append(byte)
            }
            if http.statusCode == 401 { throw try await renewAfterRefusal(data).error }
            throw APIError(status: http.statusCode, message: Self.message(from: data, status: http.statusCode))
        }
        var failure: String?
        // The answer ends when the server closes the stream.
        try await bytes.forEachFrame { frame in
            try Task.checkCancellation()
            if frame.data == "[DONE]" { return false }
            if frame.event == "error" { failure = frame.data } else { await receive(frame) }
            return true
        }
        return failure
    }

    // MARK: Marketplaces

    func marketplaceAccounts() async throws -> DirectConnections {
        try await decode(request("GET", "api/marketplace-accounts"))
    }

    /// Start eBay sign-in for the chosen tool groups; returns the page the seller opens.
    func connectEbay(features: [String]) async throws -> URL {
        struct Answer: Decodable { let authorizeUrl: String }
        let answer: Answer = try await decode(request("POST", "api/marketplace-accounts/ebay/connect", json: ["features": features]))
        guard let url = URL(string: answer.authorizeUrl), url.scheme == "https" else {
            throw APIError(status: 0, message: "eBay sign-in didn't return a page to open.")
        }
        return url
    }

    /// One page of an eBay section, read with the seller's own eBay account.
    func ebayRead(_ section: String, params: [String: String] = [:], offset: Int = 0) async throws -> EbayPage {
        var query = params.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        query.append(URLQueryItem(name: "offset", value: String(offset)))
        return try EbayPage.decode(await send(request("GET", "api/ebay/read/\(section)", query: query)))
    }

    /// Active eBay listings that look like a photo (JPEG or PNG).
    func ebayImageSearch(_ image: Data, mime: String) async throws -> EbayPage {
        try EbayPage.decode(await send(request("POST", "api/ebay/research/image",
                                               json: ["data": image.base64EncodedString(), "mime": mime])))
    }

    /// What an eBay account change would do. Nothing is changed until `confirmEbay`.
    func previewEbay(kind: String, payload: [String: JSONValue]) async throws -> EbayReview {
        struct Body: Encodable { let kind: String; let payload: [String: JSONValue] }
        return try await decode(request("POST", "api/ebay/actions/preview", json: Body(kind: kind, payload: payload)))
    }

    /// Make the change the seller reviewed. `confirm` is the seller's explicit approval.
    func confirmEbay(reviewID: String, kind: String, payload: [String: JSONValue]) async throws -> EbayResult {
        struct Body: Encodable { let id: String; let kind: String; let payload: [String: JSONValue]; let confirm = true }
        return try await decode(request("POST", "api/ebay/actions/confirm", json: Body(id: reviewID, kind: kind, payload: payload)))
    }

    /// The seller checked in eBay that an action whose outcome was unknown went through. Nothing is repeated.
    func markEbayActionChecked(_ id: String) async throws {
        _ = try await send(request("POST", "api/ebay/actions/\(id)/checked", json: ["confirm": true]))
    }

    func ebayLiveListing(_ id: String) async throws -> EbayLiveListing {
        try await decode(request("GET", "api/ebay/listing/\(id)"))
    }

    func ebayOfferListings() async throws -> EbayOfferListings {
        try await decode(request("GET", "api/ebay-offers"))
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
        let token: String
        do {
            token = try await auth.accessToken()
        } catch is SignInRejected {
            // The saved sign-in is gone or the server refused to renew it.
            await auth.signedOut()
            throw APIError(status: 401, message: APIError.signInEnded)
        } catch let stopped where stopped is URLError || stopped.isCancellation {
            throw stopped
        } catch {
            throw APIError(status: 0, message: APIError.signInNotRenewed)
        }
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

    /// A read whose URL names a version that never changes, answered from disk when it was kept.
    private func immutableRead(_ key: String, _ path: String, query: [URLQueryItem]) async throws -> Data {
        if let kept = await immutable.data(for: key) { return kept }
        let generation = await immutable.generation
        let data = try await send(request("GET", path, query: query))
        await immutable.store(data, for: key, generation: generation)
        return data
    }

    private func decode<T: Decodable>(_ request: URLRequest) async throws -> T {
        try JSONDecoder().decode(T.self, from: try await send(request))
    }

    private func send(_ request: URLRequest) async throws -> Data {
        let (data, status) = try await perform(request)
        if status == 401 {
            let refusal = try await renewAfterRefusal(data)
            // Only a read is safe to send again; anything else may already have run.
            guard let token = refusal.token, request.httpMethod == "GET" else { throw refusal.error }
            var retry = request
            retry.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (again, againStatus) = try await perform(retry)
            if againStatus == 401, Self.isSessionRefusal(again) {
                // A token issued moments ago was refused too: the account can't be used.
                await auth.signedOut()
                throw APIError(status: 401, message: APIError.signInEnded)
            }
            guard (200..<300).contains(againStatus) else {
                throw APIError(status: againStatus, message: Self.message(from: again, status: againStatus))
            }
            return again
        }
        guard (200..<300).contains(status) else {
            throw APIError(status: status, message: Self.message(from: data, status: status))
        }
        return data
    }

    private func perform(_ request: URLRequest) async throws -> (Data, Int) {
        let (data, response) = try await readRetry.run(request) { request in
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                throw APIError(status: 0, message: "Anteroom didn't answer. Try again.")
            }
            return (data, http)
        }
        return (data, response.statusCode)
    }

    /// The server refused a request with 401. A refusal of the seller's session
    /// renews the sign-in (or signs out when the server refuses to renew it); any other 401,
    /// such as a marketplace that signed Anteroom out, keeps the server's message.
    /// Returns the error to report, and the renewed token when there is one.
    private func renewAfterRefusal(_ data: Data) async throws -> (error: APIError, token: String?) {
        guard Self.isSessionRefusal(data) else {
            return (APIError(status: 401, message: Self.message(from: data, status: 401)), nil)
        }
        do {
            let token = try await auth.refreshedToken()
            return (APIError(status: 401, message: APIError.signInRenewed), token)
        } catch is SignInRejected {
            await auth.signedOut()
            return (APIError(status: 401, message: APIError.signInEnded), nil)
        } catch let stopped where stopped.isCancellation {
            throw stopped
        } catch {
            // Offline, or the sign-in server failed: the saved sign-in still works later.
            return (APIError(status: 0, message: APIError.signInNotRenewed), nil)
        }
    }

    /// The hosted API's own refusals name the seller session; a marketplace's 401 does not.
    private static func isSessionRefusal(_ data: Data) -> Bool {
        guard let refusal = try? JSONDecoder().decode(ErrorDetail.self, from: data) else { return true }
        return refusal.detail.localizedCaseInsensitiveContains("session")
    }

    /// The contract's errors carry `detail` as a sentence (`ErrorDetail`) or,
    /// for a request FastAPI rejected, a list of validation issues.
    private static func message(from data: Data, status: Int) -> String {
        if status == 401 && isSessionRefusal(data) { return APIError.signInEnded }
        if let refusal = try? JSONDecoder().decode(ErrorDetail.self, from: data) { return refusal.detail }
        if let invalid = try? JSONDecoder().decode(HTTPValidationError.self, from: data) {
            let messages = (invalid.detail ?? []).map(\.msg)
            if !messages.isEmpty { return messages.joined(separator: "\n") }
        }
        return "Something went wrong (\(status)). Try again."
    }
}

extension WorkspaceRuntime {
    /// What the phone assumes when `/api/runtime` cannot be read: no optional features.
    static let unavailable = WorkspaceRuntime(mode: "hosted", features: [], views: [], marketplaceForms: [], actionMarketplaces: [])
}

/// One answer shared by the screens that ask for it within `maxAge` seconds
/// (such as `/api/runtime`); screens asking at the same time share one request.
actor SharedRead<Value: Sendable> {
    private let maxAge: TimeInterval
    private var current: (task: Task<Value, Error>, asked: Date)?

    init(maxAge: TimeInterval) {
        self.maxAge = maxAge
    }

    func value(fresh: Bool, load: @escaping @Sendable () async throws -> Value) async throws -> Value {
        if fresh || current.map({ Date().timeIntervalSince($0.asked) > maxAge }) ?? true {
            current = (Task { try await load() }, Date())
        }
        let task = current!.task
        do {
            return try await task.value
        } catch {
            // A failed answer is not kept; the next screen asks again.
            if current?.task == task { current = nil }
            throw error
        }
    }

    func clear() {
        current = nil
    }
}

/// Shared marketplace lists, downloaded once per version while the app runs.
actor ChoiceListCache {
    private var lists: [ChoiceListRef: Task<ChoiceList, Error>] = [:]

    func list(_ ref: ChoiceListRef, load: @escaping @Sendable () async throws -> ChoiceList) async throws -> ChoiceList {
        let task = lists[ref] ?? Task { try await load() }
        lists[ref] = task
        do {
            return try await task.value
        } catch {
            // A failed download is tried again the next time a form names the list.
            lists[ref] = nil
            throw error
        }
    }
}

/// Lets a fixed number of callers through at once; the rest wait their turn.
/// Urgent callers wait ahead of the others. A caller cancelled while it waits
/// leaves the queue at once, with `CancellationError`, and takes no turn.
actor RequestGate {
    private struct Waiter {
        let id: UUID
        let urgent: Bool
        let continuation: CheckedContinuation<Void, Error>
    }

    private let limit: Int
    private var running = 0
    private var waiting: [Waiter] = []

    init(limit: Int) { self.limit = limit }

    /// How many callers are waiting; for tests.
    var waitingCount: Int { waiting.count }

    /// Returns once the caller may run. Every successful `enter` needs one `leave`.
    func enter(urgent: Bool = false) async throws {
        try Task.checkCancellation()
        if running < limit {
            running += 1
            return
        }
        let id = UUID()
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                if Task.isCancelled {
                    continuation.resume(throwing: CancellationError())
                    return
                }
                let waiter = Waiter(id: id, urgent: urgent, continuation: continuation)
                if urgent, let firstOther = waiting.firstIndex(where: { !$0.urgent }) {
                    waiting.insert(waiter, at: firstOther)
                } else {
                    waiting.append(waiter)
                }
            }
        } onCancel: {
            Task { await self.cancel(id) }
        }
    }

    func leave() {
        if waiting.isEmpty {
            running -= 1
        } else {
            // The turn passes straight to the next caller.
            waiting.removeFirst().continuation.resume()
        }
    }

    private func cancel(_ id: UUID) {
        guard let index = waiting.firstIndex(where: { $0.id == id }) else { return }
        waiting.remove(at: index).continuation.resume(throwing: CancellationError())
    }
}
