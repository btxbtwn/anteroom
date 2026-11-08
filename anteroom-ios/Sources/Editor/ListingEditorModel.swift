import Foundation
import Observation

/// Editable copy of one listing in any state, through the same listing
/// contract the desktop and web workspace use. Numbers are edited as text and
/// checked on save so a half-typed price never silently becomes empty.
@MainActor
@Observable
final class ListingEditorModel {
    struct Form: Equatable, Codable {
        var sellerInputs = SellerInputs()
        var fields = GeneralFields()
        var price = ""
        var cost = ""
        var quantity = ""
        var weightOz = ""
        var labels = ""
    }

    let api: AnteroomAPI
    let listingID: String
    private(set) var status: String
    private(set) var options = GeneralOptions()
    private(set) var marketplaceTabs: [String] = []
    var form = Form()
    private var saved = Form()
    /// The full listing as loaded; edits are written into it, never rebuilt.
    private var document: [String: JSONValue] = [:]
    /// The saved version, which List, Update and Save draft send.
    private(set) var revisionID: String?
    /// Marketplace tab edits by listing path, written into the listing on Save.
    private(set) var pending: [String: JSONValue] = [:]
    /// Each marketplace tab's form. A form stays while a newer one loads.
    private(set) var forms: [String: NativeForm] = [:]
    private(set) var formErrors: [String: String] = [:]
    /// Form downloads in flight, owned here so switching tabs never cancels one.
    @ObservationIgnored private var formLoads: [String: FormLoad] = [:]
    /// The item's photos, from the same read as the listing.
    var photos: [ListingPhoto] = []
    private(set) var photosLoaded = false
    /// The inventory row's cover, shown while the photos load.
    let coverPhotoID: String?
    let coverPhotoVersion: String?
    private(set) var isLoading = true
    private(set) var isSaving = false
    private(set) var loadError: String?
    /// False until the saved listing has been read once. Before that the form
    /// holds placeholders, not the seller's details, so it is neither shown nor saved.
    private(set) var hasLoaded = false
    var saveError: String?
    var hasConflict = false
    /// Said once after opening: unsaved edits kept from last time came back, or were dropped.
    var draftNotice: String?
    private var checkedStash = false
    /// Each marketplace tab's state for the saved listing: ready, fields missing, listed or sold.
    private(set) var choices: [ListChoice] = []

    init(api: AnteroomAPI, listing: ListingSummary) {
        self.api = api
        listingID = listing.id
        status = listing.status ?? "draft"
        coverPhotoID = listing.coverPhotoID
        coverPhotoVersion = listing.coverPhotoVersion
        form.fields.title = listing.title ?? ""
        saved = form
    }

    var hasChanges: Bool { hasLoaded && !isLoading && (form != saved || !pending.isEmpty) }
    var isDraft: Bool { status == "draft" }
    var title: String { form.fields.title }

    /// Reads the listing, its notes and photos in one request. The General
    /// lists and the marketplace tabs load beside it and never hold the editor
    /// up; once both are in, every tab's form starts loading.
    func load() async {
        isLoading = true
        defer { isLoading = false }
        let lookups = Task { await loadLookups() }
        do {
            let screen = try await api.listingScreen(listingID)
            let revisionChanged = hasLoaded && screen.listing.currentRevisionID != revisionID
            apply(screen)
            isLoading = false
            await lookups.value
            Task { await loadReadiness() }
            prefetchForms(refresh: revisionChanged)
        } catch {
            loadError = error.localizedDescription
        }
    }

    private func apply(_ screen: ListingScreen) {
        status = screen.conversation.status ?? "draft"
        document = screen.listing.listing
        revisionID = screen.listing.currentRevisionID
        pending = [:]
        var next = Self.form(from: document)
        if next.fields.title.isEmpty { next.fields.title = screen.conversation.title ?? "" }
        next.sellerInputs = SellerInputs(notes: screen.conversation.notes)
        form = next
        saved = next
        photos = screen.photos
        photosLoaded = true
        hasLoaded = true
        loadError = nil
        if !checkedStash {
            checkedStash = true
            restoreStash()
        }
    }

    /// The General dropdown lists and the marketplace tabs, asked at once. The
    /// API keeps both, so a second listing opens without asking again.
    private func loadLookups() async {
        async let listsRead = try? api.generalOptions()
        async let formsRead = try? api.runtime().marketplaceForms
        async let chosenRead = try? api.selectedMarketplaces()
        // Without the lists the fields stay editable as text.
        if let lists = await listsRead, lists != options { options = lists }
        // Settings → Where you list decides which forms show, as on the web.
        if marketplaceTabs.isEmpty, let tabs = await formsRead {
            let selected = await chosenRead.map(Set.init)
            marketplaceTabs = selected.map { picked in tabs.filter(picked.contains) } ?? tabs
        }
    }

    // MARK: Unsaved edits kept on the phone

    /// Keeps unsaved edits on the device while the app is in the background,
    /// where iOS may end it, or while the editor is closed. Save, Discard and
    /// sign-out remove them.
    func stashEdits() {
        guard hasChanges else { return EditorDraftStore.remove(listingID) }
        EditorDraftStore.save(EditorDraft(listingID: listingID, revisionID: revisionID, baselineInputs: saved.sellerInputs,
                                          form: form, pending: pending, savedAt: Date()))
    }

    func forgetStash() {
        EditorDraftStore.remove(listingID)
    }

    /// Puts the form back to what is saved and drops any kept copy.
    func discardChanges() {
        form = saved
        pending = [:]
        forgetStash()
    }

    /// Puts kept edits back when they were made on the version still saved;
    /// otherwise drops them, since they'd overwrite changes made elsewhere.
    private func restoreStash() {
        guard let draft = EditorDraftStore.load(listingID) else { return }
        EditorDraftStore.remove(listingID)
        let when = draft.savedAt.formatted(date: .abbreviated, time: .shortened)
        if draft.restores(revisionID: revisionID, inputs: saved.sellerInputs) {
            form = draft.form
            pending = draft.pending
            draftNotice = "Your unsaved changes from \(when) are back. Save to keep them."
        } else {
            draftNotice = "This listing changed somewhere else after \(when), so your unsaved changes from then were discarded."
        }
    }

    // MARK: Readiness and List everywhere

    static let listMarketplaces = ["ebay", "etsy", "poshmark", "mercari", "depop"]

    func choice(_ marketplace: String) -> ListChoice? {
        choices.first { $0.marketplace == marketplace }
    }

    /// Reads the saved listing's field check and marketplace states. A failed read
    /// leaves the tabs without a readiness mark rather than a wrong one.
    func loadReadiness() async {
        let shown = marketplaceTabs.filter(Self.listMarketplaces.contains)
        guard !shown.isEmpty else { return choices = [] }
        async let acting = try? api.runtime().actionMarketplaces
        async let check = try? api.marketplacePreflight(listingID)
        async let states = try? api.nativeMarketplaceStates(listingID)
        async let live = try? api.marketplaceLiveListings(listingID)
        let (canAct, preflight, drafts, listings) = await (Set(acting ?? []), check, states ?? [:], live ?? [:])
        let soldSomewhere = listings.values.contains { $0.soldHere == true }
        // A check for an older revision would describe fields since changed.
        let current = preflight?.revisionID == revisionID ? preflight : nil
        choices = shown.map { marketplace in
            ListChoice.make(marketplace, canAct: canAct.contains(marketplace), check: current?.marketplaces[marketplace],
                            draft: drafts[marketplace], live: listings[marketplace], soldSomewhere: soldSomewhere)
        }
    }

    /// Starts List on each marketplace the seller picked and confirmed. Returns what failed, if anything.
    func list(on marketplaces: [String], confirmation: SellerConfirmation) async -> String? {
        guard let revisionID else { return "Save the listing first." }
        var failures: [String] = []
        await withTaskGroup(of: String?.self) { group in
            for marketplace in marketplaces {
                group.addTask { [api, listingID] in
                    do {
                        try await api.listOnMarketplace(listingID, marketplace: marketplace, revisionID: revisionID,
                                                        confirmation: confirmation)
                        return nil
                    } catch {
                        return "\(Marketplaces.name(marketplace)): \(error.localizedDescription)"
                    }
                }
            }
            for await failure in group { if let failure { failures.append(failure) } }
        }
        await loadReadiness()
        return failures.isEmpty ? nil : failures.joined(separator: "\n")
    }

    /// Saves the listing, then the seller's notes. Returns true when both saved.
    func save() async -> Bool {
        var fields: GeneralFields
        let notes: String
        do {
            fields = try validatedFields()
            notes = try form.sellerInputs.encodedNotes()
        } catch {
            saveError = error.localizedDescription
            return false
        }
        fields.title = fields.title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !fields.title.isEmpty else {
            saveError = "Enter a title."
            return false
        }
        isSaving = true
        defer { isSaving = false }
        if fields != saved.fields || !pending.isEmpty {
            var listing = fields.applied(to: document, changedFrom: saved.fields)
            for (path, value) in pending { listing.set(value, at: path) }
            do {
                let result = try await api.saveListing(listingID, ListingSave(
                    listing: listing, expectedRevisionID: revisionID ?? ""))
                pending = [:]
                document = result.listing
                revisionID = result.revisionID
                // The server recomputes marketplace rows (earnings, category
                // fields); the tabs keep showing theirs until the new ones arrive.
                prefetchForms(refresh: true)
                let reloaded = Self.form(from: document)
                form.fields = reloaded.fields
                form.price = reloaded.price
                form.cost = reloaded.cost
                form.quantity = reloaded.quantity
                form.weightOz = reloaded.weightOz
                form.labels = reloaded.labels
                saved.fields = reloaded.fields
                saved.price = reloaded.price
                saved.cost = reloaded.cost
                saved.quantity = reloaded.quantity
                saved.weightOz = reloaded.weightOz
                saved.labels = reloaded.labels
            } catch let error as APIError where error.isConflict {
                hasConflict = true
                return false
            } catch {
                saveError = error.localizedDescription
                return false
            }
        }
        Task { await loadReadiness() }
        forgetStash()
        if form.sellerInputs != saved.sellerInputs {
            do {
                try await api.saveNotes(listingID, notes: notes)
                saved.sellerInputs = form.sellerInputs
            } catch {
                saveError = "Your listing saved, but your notes didn't: \(error.localizedDescription)"
                return false
            }
        }
        return true
    }

    // MARK: Marketplace tabs

    private struct FormLoad {
        let revisionID: String?
        let task: Task<Void, Never>
    }

    /// Loads a tab's form, or joins the download already running for it.
    /// The download belongs to the model: leaving the tab doesn't stop it.
    func loadForm(_ marketplace: String) async {
        await startFormLoad(marketplace).value
    }

    /// Starts every tab's form at once, so switching tabs finds it ready.
    /// `refresh` asks again for forms already loaded, keeping them on screen meanwhile.
    func prefetchForms(refresh: Bool = false) {
        for marketplace in marketplaceTabs where refresh || forms[marketplace] == nil {
            startFormLoad(marketplace)
        }
    }

    @discardableResult
    private func startFormLoad(_ marketplace: String) -> Task<Void, Never> {
        if let running = formLoads[marketplace] {
            if running.revisionID == revisionID { return running.task }
            // Asked for a revision since replaced.
            running.task.cancel()
        }
        let revision = revisionID
        formErrors[marketplace] = nil
        let task = Task { [api, listingID] in
            let result: Result<NativeForm, Error>
            do {
                result = .success(try await api.nativeForm(listingID, marketplace: marketplace))
            } catch {
                result = .failure(error)
            }
            self.finishFormLoad(marketplace, revision: revision, result)
        }
        formLoads[marketplace] = FormLoad(revisionID: revision, task: task)
        return task
    }

    private func finishFormLoad(_ marketplace: String, revision: String?, _ result: Result<NativeForm, Error>) {
        // A download for a replaced revision says nothing about the current one.
        guard revision == revisionID else { return }
        formLoads[marketplace] = nil
        switch result {
        case .success(let form):
            forms[marketplace] = form
            formErrors[marketplace] = nil
        case .failure(let error) where error.isCancellation:
            // Not loaded, not failed: the tab asks again when it shows.
            break
        case .failure(let error):
            // A form already on screen stays; only a tab with nothing to show reports the error.
            if forms[marketplace] == nil { formErrors[marketplace] = error.localizedDescription }
        }
    }

    /// What a marketplace control shows: an unsaved edit, else the saved
    /// listing value when it fits the field, else the form's own value.
    func value(_ field: NativeField) -> JSONValue? {
        if field.locked { return field.value }
        if let edit = pending[field.path] { return edit }
        if let saved = document.value(at: field.path), saved != .null, saved != .string(""), fits(field, saved) {
            return saved
        }
        return field.value
    }

    func setValue(_ value: JSONValue, for field: NativeField) {
        guard !field.locked else { return }
        pending[field.path] = value
        // A new category or similar choice empties the fields that depend on it.
        for dependent in field.clears {
            pending[dependent] = dependent.hasSuffix(".attributes") ? .object([:])
                : dependent.hasSuffix(".inventoryRows") ? .array([]) : .string("")
        }
    }

    /// An unsaved edit at a listing path, else the saved value.
    func current(at path: String) -> JSONValue? {
        pending[path] ?? document.value(at: path)
    }

    func setValue(_ value: JSONValue, at path: String) {
        pending[path] = value
    }

    /// A category picked but not saved yet: its own rows load after Save.
    func categoryChanged(_ marketplace: String) -> Bool {
        pending["direct_category_ids.\(marketplace)"] != nil
    }

    private func fits(_ field: NativeField, _ value: JSONValue) -> Bool {
        guard field.selectionOnly, !field.options.isEmpty else { return true }
        let known = field.choices.known
        switch value {
        case .array(let items): return items.allSatisfy { known.contains($0.text) }
        default: return known.contains(value.text)
        }
    }

    private static func form(from listing: [String: JSONValue]) -> Form {
        let fields = GeneralFields(listing: listing)
        var form = Form()
        form.fields = fields
        form.price = GeneralFields.text(fields.price)
        form.cost = GeneralFields.text(fields.cost)
        form.quantity = fields.quantity.map { String($0) } ?? ""
        form.weightOz = GeneralFields.text(fields.weightOz)
        form.labels = fields.labels.joined(separator: ", ")
        return form
    }

    private func validatedFields() throws -> GeneralFields {
        var fields = form.fields
        fields.price = try Self.number(form.price, name: "Price")
        fields.cost = try Self.number(form.cost, name: "Cost")
        fields.weightOz = try Self.number(form.weightOz, name: "Weight")
        let quantity = form.quantity.trimmingCharacters(in: .whitespaces)
        if quantity.isEmpty {
            fields.quantity = nil
        } else if let value = Int(quantity), value >= 1 {
            fields.quantity = value
        } else {
            throw ValidationError("Quantity must be a whole number of 1 or more.")
        }
        fields.labels = form.labels
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        // Unchanged text keeps the loaded value exactly, so "12.50" typed back
        // as "12.5" does not count as an edit.
        if form.price == saved.price { fields.price = saved.fields.price }
        if form.cost == saved.cost { fields.cost = saved.fields.cost }
        if form.weightOz == saved.weightOz { fields.weightOz = saved.fields.weightOz }
        if form.labels == saved.labels { fields.labels = saved.fields.labels }
        return fields
    }

    private static func number(_ text: String, name: String) throws -> Double? {
        var trimmed = text.trimmingCharacters(in: .whitespaces)
            .replacingOccurrences(of: "$", with: "")
        // A keypad that types "12,50" means 12.50; "1,200.00" uses grouping.
        trimmed = trimmed.contains(".")
            ? trimmed.replacingOccurrences(of: ",", with: "")
            : trimmed.replacingOccurrences(of: ",", with: ".")
        if trimmed.isEmpty { return nil }
        guard let value = Double(trimmed), value >= 0, value.isFinite else {
            throw ValidationError("\(name) must be a number.")
        }
        return value
    }
}

struct ValidationError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}
