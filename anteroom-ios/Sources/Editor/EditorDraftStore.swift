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
    /// Which kept snapshot this current-work stash came from, if explicitly chosen.
    var recoveredCopySavedAt: Date? = nil
    /// Kept before Weight (lb) existed, so the seller never edited pounds in it.
    /// Recorded so the flag survives when the copy is written again for recovery.
    var formLacksWeightLb = false

    enum CodingKeys: String, CodingKey {
        case listingID, revisionID, baselineInputs, form, pending, savedAt, baselineForm, recoveredCopySavedAt, formLacksWeightLb
    }

    private enum FormKeys: String, CodingKey { case weightLb }

    /// Also used after the seller explicitly chooses a conflicting kept copy.
    func recoveredForm(onto current: ListingEditorModel.Form) -> ListingEditorModel.Form {
        var recovered = form
        recovered.sellerInputs = form.sellerInputs.restoringNotesBaseline(from: current.sellerInputs)
        // Pounds the kept copy never had stay as saved, so Save does not clear them.
        if formLacksWeightLb { recovered.weightLb = current.weightLb }
        return recovered
    }

    /// Only edits made on the version still saved come back.
    func restores(revisionID current: String?, inputs: SellerInputs, currentForm: ListingEditorModel.Form? = nil) -> Bool {
        guard revisionID == current, baselineInputs.hasSameEffectiveValues(as: inputs) else { return false }
        if current == nil {
            guard var baseline = baselineForm, let currentForm else { return false }
            baseline.sellerInputs = currentForm.sellerInputs
            if formLacksWeightLb { baseline.weightLb = currentForm.weightLb }
            return baseline == currentForm
        }
        return true
    }
}

extension EditorDraft {
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        listingID = try values.decode(String.self, forKey: .listingID)
        revisionID = try values.decodeIfPresent(String.self, forKey: .revisionID)
        baselineInputs = try values.decode(SellerInputs.self, forKey: .baselineInputs)
        form = try values.decode(ListingEditorModel.Form.self, forKey: .form)
        pending = try values.decode([String: JSONValue].self, forKey: .pending)
        savedAt = try values.decode(Date.self, forKey: .savedAt)
        baselineForm = try values.decodeIfPresent(ListingEditorModel.Form.self, forKey: .baselineForm)
        recoveredCopySavedAt = try values.decodeIfPresent(Date.self, forKey: .recoveredCopySavedAt)
        let keptForm = try values.nestedContainer(keyedBy: FormKeys.self, forKey: .form)
        formLacksWeightLb = try values.decodeIfPresent(Bool.self, forKey: .formLacksWeightLb)
            ?? !keptForm.contains(.weightLb)
    }
}

/// One file per listing in Application Support, readable only while the phone is unlocked.
enum EditorDraftStore {
    static var directory: URL {
        URL.applicationSupportDirectory.appending(path: "EditorDrafts", directoryHint: .isDirectory)
    }

    @discardableResult
    static func save(_ draft: EditorDraft) -> Bool {
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try JSONEncoder().encode(draft).write(to: file(draft.listingID), options: [.atomic, .completeFileProtection])
            return true
        } catch {
            // Keeping edits is a safety net; the editor still holds them while the app runs.
        }
        return false
    }

    static func load(_ listingID: String) -> EditorDraft? {
        guard let data = try? Data(contentsOf: file(listingID)),
              let draft = try? JSONDecoder().decode(EditorDraft.self, from: data),
              draft.listingID == listingID else { return nil }
        return draft
    }

    static func remove(_ listingID: String) {
        try? FileManager.default.removeItem(at: file(listingID))
    }

    /// Every conflicting snapshot remains available until individually resolved.
    static func recoveryCopies(_ listingID: String) -> [EditorDraft]? {
        let url = file(listingID, recovery: true)
        guard FileManager.default.fileExists(atPath: url.path) else { return [] }
        guard let data = try? Data(contentsOf: url),
              let copies = try? JSONDecoder().decode([EditorDraft].self, from: data),
              copies.allSatisfy({ $0.listingID == listingID }) else { return nil }
        return copies
    }

    @discardableResult
    static func keepForRecovery(_ draft: EditorDraft) -> Bool {
        guard var copies = recoveryCopies(draft.listingID) else { return false }
        if copies.contains(draft) { return true }
        copies.append(draft)
        return writeRecoveryCopies(copies, listingID: draft.listingID)
    }

    @discardableResult
    static func removeRecovery(_ draft: EditorDraft) -> Bool {
        guard let copies = recoveryCopies(draft.listingID) else { return false }
        return writeRecoveryCopies(copies.filter { $0 != draft }, listingID: draft.listingID)
    }

    private static func writeRecoveryCopies(_ copies: [EditorDraft], listingID: String) -> Bool {
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try JSONEncoder().encode(copies).write(to: file(listingID, recovery: true), options: [.atomic, .completeFileProtection])
            return true
        } catch { return false }
    }

    static func removeAll() {
        try? FileManager.default.removeItem(at: directory)
    }

    private static func file(_ listingID: String, recovery: Bool = false) -> URL {
        let name = listingID.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? listingID
        return directory.appending(path: "\(name)\(recovery ? ".recovery" : "").json")
    }
}
