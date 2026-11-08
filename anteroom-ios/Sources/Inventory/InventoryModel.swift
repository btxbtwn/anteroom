import Foundation
import Observation

@MainActor
@Observable
final class InventoryModel {
    let api: AnteroomAPI
    private(set) var listings: [ListingSummary] = []
    private(set) var preferredMarketplaces: [String] = []
    private(set) var suggestions: [Suggestion] = []
    private(set) var isLoading = false
    private(set) var hasLoaded = false
    /// When the inventory last came back from the server.
    private(set) var loadedAt: Date?
    var filters = InventoryFilters()
    var error: String?
    /// Counts changes to `listings`; the cached queries recompute when it moves.
    private var revision = 0
    @ObservationIgnored private var rowsMemo = QueryMemo<RowsKey, [ListingSummary]>()
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
        do {
            let rows = try await api.listings()
            try Task.checkCancellation()
            changeListings { $0 = rows }
            hasLoaded = true
            loadedAt = Date()
            error = nil
        } catch is CancellationError {
        } catch let failure as URLError where failure.code == .cancelled {
        } catch {
            self.error = error.localizedDescription
        }
        // Suggestions and the filter menu's order are extras; inventory works without them.
        if let ranked = try? await api.suggestions() { suggestions = ranked }
        if let selected = try? await api.selectedMarketplaces() { preferredMarketplaces = selected }
    }

    /// Coming back to the app reloads, unless the inventory is under 30 seconds old.
    func shouldReloadOnReturn(now: Date = Date()) -> Bool {
        InventoryQuery.isStale(loadedAt: loadedAt, now: now)
    }

    /// Marketplace work changes a row's state only while that row is in progress.
    var hasWorkInProgress: Bool { InventoryQuery.hasWorkInProgress(listings) }

    /// Every change to the rows goes through here, so the cached queries recompute.
    private func changeListings(_ change: (inout [ListingSummary]) -> Void) {
        change(&listings)
        revision += 1
    }

    /// Cached: the screen redraws on every selection tap.
    func rows(search: String) -> [ListingSummary] {
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
    func facetSource(search: String) -> [ListingSummary] {
        let needle = search.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return listings.filter { InventoryQuery.matchesSearch($0, needle) && InventoryQuery.inTab($0, filters.status) }
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
    func createListing() async -> ListingSummary? {
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

    func rename(_ listing: ListingSummary, to title: String) async {
        do {
            try await api.rename(listing.id, title: title)
            await reload()
        } catch {
            self.error = error.localizedDescription
        }
    }

    func delete(_ listing: ListingSummary) async {
        do {
            try await api.deleteListing(listing.id)
            changeListings { rows in rows.removeAll { $0.id == listing.id } }
        } catch {
            self.error = error.localizedDescription
        }
    }
}
