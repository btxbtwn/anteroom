import Foundation
import Observation

@MainActor
@Observable
final class InventoryModel {
    let api: AnteroomAPI
    private(set) var listings: [ListingSummary] = []
    private(set) var preferredMarketplaces: [String] = []
    private(set) var isLoading = false
    private(set) var hasLoaded = false
    var filters = InventoryFilters()
    var error: String?

    init(api: AnteroomAPI) {
        self.api = api
    }

    func reload() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let rows = try await api.listings()
            try Task.checkCancellation()
            listings = rows
            hasLoaded = true
            error = nil
        } catch is CancellationError {
        } catch let failure as URLError where failure.code == .cancelled {
        } catch {
            self.error = error.localizedDescription
        }
        // The filter menu still works from the inventory alone if this fails.
        if let selected = try? await api.selectedMarketplaces() { preferredMarketplaces = selected }
    }

    func rows(search: String) -> [ListingSummary] {
        InventoryQuery.rows(listings, search: search, filters: filters)
    }

    func counts(search: String) -> [StatusTab: Int] {
        InventoryQuery.counts(listings, search: search)
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

    func created(_ listing: ListingSummary) {
        listings.removeAll { $0.id == listing.id }
        listings.insert(listing, at: 0)
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
            listings.removeAll { $0.id == listing.id }
        } catch {
            self.error = error.localizedDescription
        }
    }
}
