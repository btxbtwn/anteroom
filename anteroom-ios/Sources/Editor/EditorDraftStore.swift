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

    /// Only edits made on the version still saved come back.
    func restores(revisionID current: String?, inputs: SellerInputs) -> Bool {
        revisionID == current && baselineInputs == inputs
    }
}

/// One file per listing in Application Support, readable only while the phone is unlocked.
enum EditorDraftStore {
    static var directory: URL {
        URL.applicationSupportDirectory.appending(path: "EditorDrafts", directoryHint: .isDirectory)
    }

    static func save(_ draft: EditorDraft) {
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try JSONEncoder().encode(draft).write(to: file(draft.listingID), options: [.atomic, .completeFileProtection])
        } catch {
            // Keeping edits is a safety net; the editor still holds them while the app runs.
        }
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

    static func removeAll() {
        try? FileManager.default.removeItem(at: directory)
    }

    private static func file(_ listingID: String) -> URL {
        let name = listingID.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? listingID
        return directory.appending(path: "\(name).json")
    }
}
