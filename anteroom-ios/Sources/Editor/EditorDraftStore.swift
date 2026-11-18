import Foundation

/// A listing's unsaved editor changes, kept on the phone while the app is in the background.
struct EditorDraft: Codable, Equatable {
    let listingID: String
    /// The saved version the edits were made on.
    let revisionID: String?
    /// The seller notes the edits started from; notes save outside listing versions.
    let baselineInputs: SellerInputs
    let form: ListingEditorModel.Form
    let pending: [String: JSONValue]
    let savedAt: Date
    /// Unversioned listing edits need a field baseline as well as notes.
    var baselineForm: ListingEditorModel.Form? = nil

    /// Also used after the seller explicitly chooses a conflicting kept copy.
    func recoveredForm(onto current: ListingEditorModel.Form) -> ListingEditorModel.Form {
        var recovered = form
        recovered.sellerInputs = form.sellerInputs.restoringNotesBaseline(from: current.sellerInputs)
        return recovered
    }

    /// Only edits made on the version still saved come back.
    func restores(revisionID current: String?, inputs: SellerInputs, currentForm: ListingEditorModel.Form? = nil) -> Bool {
        guard revisionID == current, baselineInputs.hasSameEffectiveValues(as: inputs) else { return false }
        if current == nil {
            guard var baseline = baselineForm, let currentForm else { return false }
            baseline.sellerInputs = currentForm.sellerInputs
            return baseline == currentForm
        }
        return true
    }
}

/// One file per listing in Application Support, readable only while the phone is unlocked.
enum EditorDraftStore {
    static var directory: URL {
        URL.applicationSupportDirectory.appending(path: "EditorDrafts", directoryHint: .isDirectory)
    }

    @discardableResult
    static func save(_ draft: EditorDraft, recovery: Bool = false) -> Bool {
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try JSONEncoder().encode(draft).write(to: file(draft.listingID, recovery: recovery), options: [.atomic, .completeFileProtection])
            return true
        } catch {
            // Keeping edits is a safety net; the editor still holds them while the app runs.
        }
        return false
    }

    static func load(_ listingID: String, recovery: Bool = false) -> EditorDraft? {
        guard let data = try? Data(contentsOf: file(listingID, recovery: recovery)),
              let draft = try? JSONDecoder().decode(EditorDraft.self, from: data),
              draft.listingID == listingID else { return nil }
        return draft
    }

    static func remove(_ listingID: String, recovery: Bool = false) {
        try? FileManager.default.removeItem(at: file(listingID, recovery: recovery))
    }

    static func removeAll() {
        try? FileManager.default.removeItem(at: directory)
    }

    private static func file(_ listingID: String, recovery: Bool = false) -> URL {
        let name = listingID.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? listingID
        return directory.appending(path: "\(name)\(recovery ? ".recovery" : "").json")
    }
}
