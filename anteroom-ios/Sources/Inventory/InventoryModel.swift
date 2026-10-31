import Foundation
import Observation

@MainActor
@Observable
final class InventoryModel {
    let api: AnteroomAPI
    private(set) var drafts: [Draft] = []
    private(set) var isLoading = false
    private(set) var hasLoaded = false
    private(set) var canLoadMore = false
    var error: String?

    init(api: AnteroomAPI) {
        self.api = api
    }

    func reload(search: String) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let page = try await api.drafts(offset: 0, search: search)
            try Task.checkCancellation()
            drafts = page
            canLoadMore = page.count == AnteroomAPI.pageSize
            hasLoaded = true
            error = nil
        } catch is CancellationError {
        } catch let failure as URLError where failure.code == .cancelled {
        } catch {
            self.error = error.localizedDescription
        }
    }

    func loadMore(search: String) async {
        guard canLoadMore, !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let page = try await api.drafts(offset: drafts.count, search: search)
            let known = Set(drafts.map(\.id))
            drafts += page.filter { !known.contains($0.id) }
            canLoadMore = page.count == AnteroomAPI.pageSize
        } catch {
            self.error = error.localizedDescription
        }
    }

    func create(title: String) async -> Draft? {
        do {
            let draft = try await api.createDraft(title: title)
            drafts.insert(draft, at: 0)
            return draft
        } catch {
            self.error = error.localizedDescription
            return nil
        }
    }

    /// Moves a saved draft to the top, matching the server's newest-first order.
    func didSave(_ draft: Draft) {
        drafts.removeAll { $0.id == draft.id }
        drafts.insert(draft, at: 0)
    }
}
