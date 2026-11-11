import Foundation
import Observation

@MainActor
@Observable
final class InventoryModel {
    let api: AnteroomAPI
    private(set) var listings: [ConversationSummary] = []
    private(set) var preferredMarketplaces: [String] = []
    private(set) var suggestions: [Suggestion] = []
    private(set) var isLoading = false
    private(set) var hasLoaded = false
    /// When the inventory last came back from the server.
    private(set) var loadedAt: Date?
    var filters = InventoryFilters()
    var error: String?
    /// The markdown review for the stale listings, read ahead so Mark down opens filled.
    @ObservationIgnored private var markdownPreview: MarkdownPreview?
    /// Counts changes to `listings`; the cached queries recompute when it moves.
    private var revision = 0
    @ObservationIgnored private var rowsMemo = QueryMemo<RowsKey, [ConversationSummary]>()
    @ObservationIgnored private var countsMemo = QueryMemo<CountsKey, [StatusTab: Int]>()

    private struct RowsKey: Equatable {
        let revision: Int
        let search: String
        let filters: InventoryFilters
    }

    private struct CountsKey: Equatable {
        let revision: Int
        let search: String
    }

    init(api: AnteroomAPI) {
        self.api = api
    }

    func reload() async {
        isLoading = true
        defer { isLoading = false }
        // Suggestions and the filter menu's order are extras, asked beside the
        // inventory; it works without them.
        async let rankedRead = try? api.suggestions()
        async let selectedRead = try? api.selectedMarketplaces()
        do {
            let rows = try await api.listings()
            try Task.checkCancellation()
            changeListings { $0 = rows }
            hasLoaded = true
            loadedAt = Date()
            error = nil
        } catch let failure where failure.isCancellation {
        } catch {
            self.error = error.localizedDescription
        }
        if let ranked = await rankedRead {
            suggestions = ranked
            prefetchMarkdowns()
        }
        if let selected = await selectedRead { preferredMarketplaces = selected }
    }

    // MARK: Mark down prices

    private struct MarkdownPreview {
        let ids: [String]
        let asked: Date
        let task: Task<[MarkdownReviewRow], Error>
    }

    /// The stale listings the batch markdown offers.
    var staleListingIDs: [String] {
        suggestions.filter { $0.kind == "stale_active" }.map(\.conversationID)
    }

    /// Reads the markdown review for the stale listings in the background, at
    /// most every five minutes for the same listings. Nothing is saved: the
    /// seller still reviews each price, and Save sends each row's revision, so
    /// a listing changed since is skipped rather than overwritten.
    private func prefetchMarkdowns(now: Date = Date()) {
        let ids = staleListingIDs
        guard !ids.isEmpty else { return markdownPreview = nil }
        if let kept = markdownPreview, kept.ids == ids, now.timeIntervalSince(kept.asked) < 300 { return }
        let task = Task { [api] in try await api.previewMarkdowns(ids: ids) }
        markdownPreview = MarkdownPreview(ids: ids, asked: now, task: task)
    }

    /// The review read ahead for exactly these listings, if there is one.
    func markdownReview(for ids: [String]) -> Task<[MarkdownReviewRow], Error>? {
        guard let kept = markdownPreview, kept.ids == ids else { return nil }
        return kept.task
    }

    /// Titles to show while a markdown review loads.
    func titles(for ids: [String]) -> [String: String] {
        let wanted = Set(ids)
        var titles: [String: String] = [:]
        for suggestion in suggestions where wanted.contains(suggestion.conversationID) {
            titles[suggestion.conversationID] = suggestion.title
        }
        for listing in listings where titles[listing.id] == nil && wanted.contains(listing.id) {
            titles[listing.id] = listing.displayTitle
        }
        return titles
    }

    /// After markdowns are saved, the next review is read again.
    func forgetMarkdownReview() {
        markdownPreview = nil
    }

    /// Coming back to the app reloads, unless the inventory is under 30 seconds old.
    func shouldReloadOnReturn(now: Date = Date()) -> Bool {
        InventoryQuery.isStale(loadedAt: loadedAt, now: now)
    }

    /// Marketplace work changes a row's state only while that row is in progress.
    var hasWorkInProgress: Bool { InventoryQuery.hasWorkInProgress(listings) }

    /// Every change to the rows goes through here, so the cached queries recompute.
    private func changeListings(_ change: (inout [ConversationSummary]) -> Void) {
        change(&listings)
        revision += 1
    }

    /// Cached: the screen redraws on every selection tap.
    func rows(search: String) -> [ConversationSummary] {
        rowsMemo.value(for: RowsKey(revision: revision, search: search, filters: filters)) {
            InventoryQuery.rows(listings, search: search, filters: filters)
        }
    }

    func counts(search: String) -> [StatusTab: Int] {
        countsMemo.value(for: CountsKey(revision: revision, search: search)) {
            InventoryQuery.counts(listings, search: search)
        }
    }

    /// Tabs in desktop order; Failed only appears while something failed.
    func tabs(counts: [StatusTab: Int]) -> [StatusTab] {
        StatusTab.allCases.filter { $0 != .failed || (counts[.failed] ?? 0) > 0 || filters.status == .failed }
    }

    /// Facet counts are taken over the tab in front of the seller.
    func facetSource(search: String) -> [ConversationSummary] {
        let needle = search.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return listings.filter { InventoryQuery.matchesSearch($0, needle) && InventoryQuery.inTab($0, filters.status, searching: !needle.isEmpty) }
    }

    func marketplaceOptions() -> [String] {
        InventoryQuery.marketplaceOptions(listings, preferred: preferredMarketplaces, includeUnused: filters.notListed)
    }

    func toggleMarketplace(_ id: String) {
        if let index = filters.marketplaces.firstIndex(of: id) {
            filters.marketplaces.remove(at: index)
        } else {
            filters.marketplaces.append(id)
        }
    }

    /// Creates an untitled draft and returns it for the editor to open.
    func createListing() async -> ConversationSummary? {
        do {
            let listing = try await api.createListing()
            changeListings { rows in
                rows.removeAll { $0.id == listing.id }
                rows.insert(listing, at: 0)
            }
            return listing
        } catch {
            self.error = error.localizedDescription
            return nil
        }
    }

    /// Removes a draft made with New listing when the seller leaves it without
    /// adding a photo, a note, a detail or a message. Anything unclear keeps it,
    /// and so do photos still uploading.
    func discardIfUntouched(_ id: String) async {
        guard EditorDraftStore.load(id) == nil, !PhotoUploadTracker.shared.isUploading(id) else { return }
        do {
            guard try await api.discardBlankListing(id) else { return }
            changeListings { rows in rows.removeAll { $0.id == id } }
        } catch {
            // Keeping an empty draft is harmless; deleting one by mistake is not.
        }
    }

    func rename(_ listing: ConversationSummary, to title: String) async {
        do {
            try await api.rename(listing.id, title: title)
            await reload()
        } catch {
            self.error = error.localizedDescription
        }
    }

    func delete(_ listing: ConversationSummary) async {
        do {
            try await api.deleteListing(listing.id)
            changeListings { rows in rows.removeAll { $0.id == listing.id } }
        } catch {
            self.error = error.localizedDescription
        }
    }
}
