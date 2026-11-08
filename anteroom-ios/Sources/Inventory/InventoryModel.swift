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

    // MARK: Readiness

    /// Each listing's missing fields per marketplace, as the editor's tabs count them.
    private(set) var readiness: [String: [String: [MissingField]]] = [:]
    private var readinessRead: [String: String] = [:]
    private var readinessPending: [String] = []
    private var readinessTask: Task<Void, Never>?

    /// The missing fields a row shows: only the marketplaces the seller sells on.
    func missing(_ listing: ListingSummary) -> [String: [MissingField]] {
        let all = readiness[listing.id] ?? [:]
        return preferredMarketplaces.isEmpty ? all : all.filter { preferredMarketplaces.contains($0.key) }
    }

    /// A row came on screen: read its readiness with the other rows that did, once per change.
    func wantsReadiness(_ listing: ListingSummary) {
        guard listing.settledAt == nil, readinessRead[listing.id] != listing.readinessKey else { return }
        readinessRead[listing.id] = listing.readinessKey
        readinessPending.append(listing.id)
        guard readinessTask == nil else { return }
        readinessTask = Task { [weak self] in
            // Rows appear together while scrolling; one request reads them.
            try? await Task.sleep(for: .milliseconds(150))
            await self?.readPendingReadiness()
        }
    }

    private func readPendingReadiness() async {
        let ids = readinessPending
        readinessPending = []
        readinessTask = nil
        for start in stride(from: 0, to: ids.count, by: 25) {
            let block = Array(ids[start..<min(start + 25, ids.count)])
            do {
                for (id, check) in try await api.listingReadiness(block) {
                    readiness[id] = check.marketplaces.mapValues(\.missing)
                }
            } catch {
                // Advice only: the row keeps its record's state, and the next appearance asks again.
                block.forEach { readinessRead[$0] = nil }
            }
        }
    }

    /// Every change to the rows goes through here, so the cached queries recompute.
    private func changeListings(_ change: (inout [ListingSummary]) -> Void) {
        change(&listings)
        revision += 1
    }

    /// Cached: the screen redraws on every selection tap and readiness update.
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
