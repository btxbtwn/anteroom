import Foundation
import Observation

/// Editable copy of one listing in any state, through the same listing
/// contract the desktop and web workspace use. Numbers are edited as text and
/// checked on save so a half-typed price never silently becomes empty.
@MainActor
@Observable
final class ListingEditorModel {
    struct Form: Equatable {
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
    var form = Form()
    private var saved = Form()
    /// The full listing as loaded; edits are written into it, never rebuilt.
    private var document: [String: JSONValue] = [:]
    private var revisionID: String?
    private(set) var isLoading = true
    private(set) var isSaving = false
    private(set) var loadError: String?
    var saveError: String?
    var hasConflict = false

    init(api: AnteroomAPI, listing: ListingSummary) {
        self.api = api
        listingID = listing.id
        status = listing.status ?? "draft"
        form.fields.title = listing.title ?? ""
        saved = form
    }

    var hasChanges: Bool { !isLoading && form != saved }
    var isDraft: Bool { status == "draft" }
    var title: String { form.fields.title }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            async let detail = api.conversation(listingID)
            async let listing = api.listing(listingID)
            let (conversation, current) = try await (detail, listing)
            // Without the lists the fields stay editable as text.
            if options == GeneralOptions(), let lists = try? await api.generalOptions() { options = lists }
            status = conversation.status ?? "draft"
            document = current.listing
            revisionID = current.currentRevisionID
            var next = Self.form(from: document)
            if next.fields.title.isEmpty { next.fields.title = conversation.title ?? "" }
            next.sellerInputs = SellerInputs(notes: conversation.notes)
            form = next
            saved = next
            loadError = nil
        } catch {
            loadError = error.localizedDescription
        }
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
        if fields != saved.fields {
            do {
                let result = try await api.saveListing(listingID, ListingSave(
                    listing: fields.applied(to: document, changedFrom: saved.fields),
                    expectedRevisionID: revisionID ?? ""))
                document = result.listing
                revisionID = result.revisionID
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
