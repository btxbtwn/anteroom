import Foundation

/// The desktop inventory's tabs, filters and sorts (`inventoryFilters.ts`,
/// `sidebar/listingStatus.ts`), over the hosted listing rows.

enum ListingSort: String, CaseIterable, Identifiable, Sendable {
    case recent
    case createdDesc = "created_desc"
    case createdAsc = "created_asc"
    case modifiedDesc = "modified_desc"
    case modifiedAsc = "modified_asc"
    case titleAsc = "title_asc"
    case titleDesc = "title_desc"
    case priceDesc = "price_desc"
    case priceAsc = "price_asc"
    case skuAsc = "sku_asc"
    case skuDesc = "sku_desc"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .recent: "Recent activity"
        case .createdDesc: "Date Created (Newest)"
        case .createdAsc: "Date Created (Oldest)"
        case .modifiedDesc: "Date Modified (Newest)"
        case .modifiedAsc: "Date Modified (Oldest)"
        case .titleAsc: "Alphabetical (A-Z)"
        case .titleDesc: "Alphabetical (Z-A)"
        case .priceDesc: "Price (Highest)"
        case .priceAsc: "Price (Lowest)"
        case .skuAsc: "SKU (A-Z)"
        case .skuDesc: "SKU (Z-A)"
        }
    }
}

enum StatusTab: String, CaseIterable, Identifiable, Sendable {
    case all, draft, active, sold, failed

    var id: String { rawValue }
    var label: String { rawValue.capitalized }
}

struct InventoryFilters: Equatable, Sendable {
    var status: StatusTab = .all
    var marketplaces: [String] = []
    /// Turns the marketplace picks around: keep listings missing from one of
    /// them, or, with none picked, the listings no marketplace carries.
    var notListed = false
    var sort: ListingSort = .recent

    /// The status tab shows itself, so this counts only the menu filters.
    var activeCount: Int { max(marketplaces.count, notListed ? 1 : 0) }
}

enum Marketplaces {
    static let order = ["ebay", "etsy", "poshmark", "mercari", "depop", "facebook", "shopify",
                        "vinted", "whatnot", "grailed", "vestiaire", "sellwild"]
    static let names = ["general": "Vendoo", "ebay": "eBay", "etsy": "Etsy", "poshmark": "Poshmark",
                        "mercari": "Mercari", "depop": "Depop", "facebook": "Facebook", "shopify": "Shopify",
                        "vinted": "Vinted", "whatnot": "Whatnot", "sellwild": "Sellwild", "grailed": "Grailed",
                        "vestiaire": "Vestiaire Collective", "other": "Other / in person"]

    static func name(_ id: String) -> String {
        names[id] ?? (id.prefix(1).uppercased() + id.dropFirst())
    }

    static func sorted(_ ids: some Sequence<String>) -> [String] {
        ids.sorted { (order.firstIndex(of: $0) ?? order.count, $0) < (order.firstIndex(of: $1) ?? order.count, $1) }
    }
}

extension ListingSummary {
    /// Where the listing is live. Hosted rows carry only Anteroom's own listings.
    var liveMarketplaces: [String] {
        var seen = Set<String>()
        return (directMarketplaces ?? []).filter { seen.insert($0).inserted }
    }

    /// A listing Anteroom published reads Active even if it was a draft; a sale
    /// and a send in progress keep their own labels.
    var inventoryStatus: String {
        let status = self.status ?? "draft"
        if status == "sold" || status == "listing" { return status }
        if !liveMarketplaces.isEmpty, ["draft", "in_progress", "failed"].contains(status) { return "active" }
        return status
    }

    var statusTab: String { inventoryStatus == "in_progress" ? "draft" : inventoryStatus }
    var statusLabel: String { inventoryStatus.replacingOccurrences(of: "_", with: " ").capitalized }

    fileprivate var activeAnchor: Double {
        [updatedAt, unsettledAt, createdAt].map(Self.stamp).max() ?? 0
    }

    fileprivate var settledAnchor: Double {
        let settled = Self.stamp(settledAt)
        return settled > 0 ? settled : Self.stamp(updatedAt)
    }

    fileprivate static func stamp(_ value: String?) -> Double {
        ServerDate.parse(value)?.timeIntervalSince1970 ?? 0
    }
}

enum InventoryQuery {
    /// Title and SKU, as the desktop search reads them.
    static func matchesSearch(_ listing: ListingSummary, _ needle: String) -> Bool {
        guard !needle.isEmpty else { return true }
        return listing.displayTitle.lowercased().contains(needle) || (listing.sku ?? "").lowercased().contains(needle)
    }

    static func inTab(_ listing: ListingSummary, _ tab: StatusTab) -> Bool {
        tab == .all || listing.statusTab == tab.rawValue
    }

    static func matchesFilters(_ listing: ListingSummary, _ filters: InventoryFilters) -> Bool {
        guard inTab(listing, filters.status) else { return false }
        let listed = listing.liveMarketplaces
        if filters.notListed {
            if filters.marketplaces.isEmpty { return listed.isEmpty }
            return !filters.marketplaces.allSatisfy(listed.contains)
        }
        return filters.marketplaces.isEmpty || filters.marketplaces.contains(where: listed.contains)
    }

    /// Finished listings stay out of the working list; search and Sold still find them.
    static func visible(_ listing: ListingSummary, tab: StatusTab, searching: Bool) -> Bool {
        listing.settledAt == nil || searching || tab == .sold
    }

    /// The rows the list shows: working listings by recent activity, then
    /// settled ones by when they settled, unless another sort replaces both.
    static func rows(_ listings: [ListingSummary], search: String, filters: InventoryFilters) -> [ListingSummary] {
        let needle = search.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let matching = listings.filter { matchesSearch($0, needle) && matchesFilters($0, filters) }
        let working = stableSorted(matching.filter { $0.settledAt == nil }) { $0.activeAnchor > $1.activeAnchor }
        let settled = stableSorted(matching.filter { $0.settledAt != nil }) {
            $0.settledAnchor != $1.settledAnchor ? $0.settledAnchor > $1.settledAnchor : $0.id < $1.id
        }
        let ordered = sorted(working, filters.sort) + sorted(settled, filters.sort)
        return ordered.filter { visible($0, tab: filters.status, searching: !needle.isEmpty) }
    }

    /// Tab counts over the searched inventory.
    static func counts(_ listings: [ListingSummary], search: String) -> [StatusTab: Int] {
        let needle = search.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        var counts: [StatusTab: Int] = [.all: 0]
        for listing in listings where matchesSearch(listing, needle) {
            if visible(listing, tab: .all, searching: !needle.isEmpty) { counts[.all, default: 0] += 1 }
            if let tab = StatusTab(rawValue: listing.statusTab), visible(listing, tab: tab, searching: !needle.isEmpty) {
                counts[tab, default: 0] += 1
            }
        }
        return counts
    }

    /// Settings order first, then any other marketplace the inventory uses. An
    /// unused marketplace is only offered when asking what is missing from it.
    static func marketplaceOptions(_ listings: [ListingSummary], preferred: [String], includeUnused: Bool) -> [String] {
        let used = Set(listings.flatMap(\.liveMarketplaces))
        let ordered = includeUnused ? preferred : preferred.filter(used.contains)
        return ordered + Marketplaces.sorted(used.subtracting(preferred))
    }

    /// What each marketplace row would keep: listed there, or missing under Not listed.
    static func marketplaceCounts(_ listings: [ListingSummary], options: [String], notListed: Bool) -> [String: Int] {
        var carried: [String: Int] = [:]
        for listing in listings {
            for id in Set(listing.liveMarketplaces) { carried[id, default: 0] += 1 }
        }
        guard notListed else { return carried }
        return Dictionary(uniqueKeysWithValues: options.map { ($0, listings.count - (carried[$0] ?? 0)) })
    }

    static func notListedCount(_ listings: [ListingSummary]) -> Int {
        listings.filter { $0.liveMarketplaces.isEmpty }.count
    }

    static func compactCount(_ value: Int) -> String {
        guard value >= 1000 else { return String(max(0, value)) }
        let thousands = Double(value) / 1000
        if thousands >= 100 { return "\(Int(thousands.rounded()))k" }
        let text = String(format: "%.1f", thousands)
        return (text.hasSuffix(".0") ? String(text.dropLast(2)) : text) + "k"
    }

    private static func sorted(_ listings: [ListingSummary], _ sort: ListingSort) -> [ListingSummary] {
        guard let compare = comparison(sort) else { return listings }
        return stableSorted(listings) { compare($0, $1) < 0 }
    }

    private static func comparison(_ sort: ListingSort) -> ((ListingSummary, ListingSummary) -> Int)? {
        let created: (ListingSummary) -> Double = { ListingSummary.stamp($0.createdAt) }
        let modified: (ListingSummary) -> Double = { ListingSummary.stamp($0.updatedAt) }
        switch sort {
        case .recent: return nil
        case .createdDesc: return { sign(created($1) - created($0)) }
        case .createdAsc: return { sign(created($0) - created($1)) }
        case .modifiedDesc: return { sign(modified($1) - modified($0)) }
        case .modifiedAsc: return { sign(modified($0) - modified($1)) }
        case .titleAsc: return { byText($0.displayTitle, $1.displayTitle, 1) }
        case .titleDesc: return { byText($0.displayTitle, $1.displayTitle, -1) }
        case .priceDesc: return { byNumber($0.price, $1.price, -1) }
        case .priceAsc: return { byNumber($0.price, $1.price, 1) }
        case .skuAsc: return { byText($0.sku, $1.sku, 1) }
        case .skuDesc: return { byText($0.sku, $1.sku, -1) }
        }
    }

    private static func sign(_ value: Double) -> Int { value < 0 ? -1 : value > 0 ? 1 : 0 }

    /// Empty values sort last in either direction, as on the desktop.
    private static func byText(_ left: String?, _ right: String?, _ direction: Int) -> Int {
        let a = left ?? "", b = right ?? ""
        if a.isEmpty != b.isEmpty { return a.isEmpty ? 1 : -1 }
        let order = a.compare(b, options: [.caseInsensitive, .diacriticInsensitive, .numeric])
        return (order == .orderedAscending ? -1 : order == .orderedDescending ? 1 : 0) * direction
    }

    private static func byNumber(_ left: Double?, _ right: Double?, _ direction: Int) -> Int {
        guard let a = left, let b = right else { return left == nil && right == nil ? 0 : left == nil ? 1 : -1 }
        return sign(a - b) * direction
    }

    private static func stableSorted(_ items: [ListingSummary],
                                     by before: (ListingSummary, ListingSummary) -> Bool) -> [ListingSummary] {
        items.enumerated().sorted { lhs, rhs in
            if before(lhs.element, rhs.element) { return true }
            if before(rhs.element, lhs.element) { return false }
            return lhs.offset < rhs.offset
        }.map(\.element)
    }
}
