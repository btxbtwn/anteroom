import Foundation

/// Active tab at the top of Inventory: the existing Overview list or one
/// marketplace's scan + Quick Edit sheet.
enum InventorySheetTab: Hashable, Identifiable, Sendable {
    case overview
    case marketplace(String)

    var id: String {
        switch self {
        case .overview: "overview"
        case .marketplace(let market): market
        }
    }

    var label: String {
        switch self {
        case .overview: "Overview"
        case .marketplace(let market): Marketplaces.name(market)
        }
    }

    var marketplace: String? {
        switch self {
        case .overview: nil
        case .marketplace(let market): market
        }
    }
}

enum SheetCellKind: String, Equatable, Sendable {
    case category, select, multi, number, text, textarea, complex
}

struct MarketplaceSheetColumn: Equatable, Identifiable, Sendable {
    let key: String
    let label: String
    let kind: SheetCellKind
    let inGrid: Bool

    var id: String { key }
}

struct MarketplaceScanSummary: Equatable, Sendable {
    /// Highest-priority missing or rejected field label ("Fix Size" / "Missing Package size").
    let alert: String?
    let alertIsError: Bool
    /// Compact middot-joined metadata ("Pendleton · M · Used - Good").
    let meta: String
    /// True when eBay/Etsy has no cached category form yet.
    let needsLoad: Bool
}

struct MarketplaceSheetStatus: Equatable, Sendable {
    enum Kind: Equatable, Sendable {
        case listed, sold, working, needs, failed, draft, delisted, notListed
    }

    let kind: Kind
    let label: String
    let url: URL?
}

/// Pure rules behind the per-marketplace Inventory tabs and Quick Edit sheet.
/// Mirrors `marketplaceSheetConfig.ts`, `nativeFieldCommit.ts` and `listing_sheet.py`.
enum MarketplaceSheetRules {
    /// Per-marketplace price paths (`listing-editor-fields.json`).
    static let marketplacePriceKeys: [String: String] = [
        "ebay": "ebay_specifics.price",
        "poshmark": "poshmark_specifics.poshmarkPrice",
        "mercari": "mercari_specifics.price",
        "depop": "depop_specifics.price",
        "etsy": "etsy_specifics.price",
    ]

    /// General fields each marketplace form follows until edited on that marketplace.
    static let followedGeneral: Set<String> = ["title", "description", "brand", "size", "department"]

    /// Ordered columns per marketplace — kept in lockstep with `SHEET_COLUMNS`
    /// in `listing_sheet.py` and `MARKETPLACE_SHEET_COLUMNS` in `marketplaceSheetConfig.ts`.
    static let columnsByMarketplace: [String: [MarketplaceSheetColumn]] = [
        "depop": [
            MarketplaceSheetColumn(key: "direct_category_ids.depop", label: "Category", kind: .category, inGrid: true),
            MarketplaceSheetColumn(key: "depop_specifics.brand", label: "Brand", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "depop_specifics.condition", label: "Condition", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "depop_specifics.size", label: "Size", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "depop_specifics.parcelSize", label: "Package size", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "price", label: "Price", kind: .number, inGrid: true),
            MarketplaceSheetColumn(key: "depop_specifics.colour", label: "Color", kind: .multi, inGrid: true),
            MarketplaceSheetColumn(key: "depop_specifics.description", label: "Description", kind: .textarea, inGrid: false),
        ],
        "poshmark": [
            MarketplaceSheetColumn(key: "direct_category_ids.poshmark", label: "Category", kind: .category, inGrid: true),
            MarketplaceSheetColumn(key: "poshmark_specifics.brand", label: "Brand", kind: .text, inGrid: true),
            MarketplaceSheetColumn(key: "poshmark_specifics.condition", label: "Condition", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "poshmark_specifics.inventoryRows", label: "Size", kind: .complex, inGrid: true),
            MarketplaceSheetColumn(key: "poshmark_specifics.colors", label: "Color", kind: .multi, inGrid: true),
            MarketplaceSheetColumn(key: "price", label: "Price", kind: .number, inGrid: true),
            MarketplaceSheetColumn(key: "poshmark_specifics.title", label: "Title", kind: .text, inGrid: false),
            MarketplaceSheetColumn(key: "poshmark_specifics.description", label: "Description", kind: .textarea, inGrid: false),
        ],
        "mercari": [
            MarketplaceSheetColumn(key: "direct_category_ids.mercari", label: "Category", kind: .category, inGrid: true),
            MarketplaceSheetColumn(key: "mercari_specifics.brand", label: "Brand", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "mercari_specifics.condition", label: "Condition", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "mercari_specifics.nativeSizeId", label: "Size", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "mercari_specifics.shippingClassId", label: "Shipping", kind: .complex, inGrid: true),
            MarketplaceSheetColumn(key: "price", label: "Price", kind: .number, inGrid: true),
            MarketplaceSheetColumn(key: "mercari_specifics.title", label: "Title", kind: .text, inGrid: false),
            MarketplaceSheetColumn(key: "mercari_specifics.description", label: "Description", kind: .textarea, inGrid: false),
        ],
        "ebay": [
            MarketplaceSheetColumn(key: "direct_category_ids.ebay", label: "Category", kind: .category, inGrid: true),
            MarketplaceSheetColumn(key: "ebay_specifics.condition", label: "Condition", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "ebay_specifics.Brand", label: "Brand", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "ebay_specifics.Size", label: "Size", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "price", label: "Price", kind: .number, inGrid: true),
            MarketplaceSheetColumn(key: "quantity", label: "Qty", kind: .number, inGrid: true),
            MarketplaceSheetColumn(key: "ebay_specifics.title", label: "Title", kind: .text, inGrid: false),
        ],
        "etsy": [
            MarketplaceSheetColumn(key: "direct_category_ids.etsy", label: "Category", kind: .category, inGrid: true),
            MarketplaceSheetColumn(key: "etsy_specifics.whenMade", label: "When made", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "etsy_specifics.whoMade", label: "Who made it", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "etsy_specifics.whatIsIt", label: "What is it", kind: .select, inGrid: true),
            MarketplaceSheetColumn(key: "price", label: "Price", kind: .number, inGrid: true),
            MarketplaceSheetColumn(key: "quantity", label: "Qty", kind: .number, inGrid: true),
            MarketplaceSheetColumn(key: "etsy_specifics.title", label: "Title", kind: .text, inGrid: false),
        ],
    ]

    static func columns(for marketplace: String) -> [MarketplaceSheetColumn] {
        columnsByMarketplace[marketplace] ?? []
    }

    /// Marketplace tabs shown beside Overview, matching web `editorTabs(selected, runtime)`:
    /// respects Settings → Where you list, and on hosted workspaces only exposes
    /// marketplaces present in `runtime.marketplaceForms`. When `runtime` is nil
    /// (not yet loaded or unavailable), defaults to no hosted marketplace tabs.
    static func availableMarketplaceTabs(preferred: [String], runtime: WorkspaceRuntime?) -> [String] {
        guard let runtime else { return [] }
        let order = MarketplaceActions.order
        let base: [String]
        if preferred.isEmpty {
            base = order
        } else {
            let preferredSet = Set(preferred)
            let filtered = order.filter(preferredSet.contains)
            base = filtered.isEmpty ? order : filtered
        }
        if runtime.mode == "hosted" {
            let supported = Set(runtime.marketplaceForms)
            return base.filter(supported.contains)
        }
        return base
    }

    /// True when Quick Edit must treat `marketplace` as read-only.
    /// Falls back to unavailable (read-only) when `runtime` has not loaded.
    static func isFormDisabled(marketplace: String, runtime: WorkspaceRuntime?) -> Bool {
        guard let runtime else { return true }
        return runtime.mode == "hosted" && !runtime.marketplaceForms.contains(marketplace)
    }

    /// Companion breadcrumb path key for marketplaces that store category ID and path separately.
    static func categoryPathKey(for columnKey: String) -> String? {
        switch columnKey {
        case "direct_category_ids.ebay": "direct_category_ids.ebay_path"
        case "direct_category_ids.etsy": "direct_category_ids.etsy_path"
        default: nil
        }
    }

    static func categoryPathCompanion(columnKey: String, label: String) -> [String: JSONValue] {
        guard let pathKey = categoryPathKey(for: columnKey) else { return [:] }
        return [pathKey: .string(label)]
    }

    /// True when an HTTP 409 response is an agent field lock rather than a stale revision conflict.
    static func isAgentLockConflict(_ message: String) -> Bool {
        message.localizedCaseInsensitiveContains("agent is editing")
    }

    /// Rewrites a bare General key (like `"price"` or `"title"`) to this
    /// marketplace's own specifics key so a marketplace edit never reprices or
    /// rewrites General or sibling marketplaces.
    static func independentFieldKey(marketplace: String, key: String) -> String {
        guard marketplace != "general", !key.contains(".") else { return key }
        if key == "price" { return marketplacePriceKeys[marketplace] ?? key }
        if followedGeneral.contains(key) { return "\(marketplace)_specifics.\(key)" }
        return key
    }

    /// True when a native form row is marked locked only because it inherits from General.
    static func isInheritedGeneralLock(locked: Bool, help: String?) -> Bool {
        guard locked, let help else { return false }
        return help.range(of: "change it there", options: .caseInsensitive) != nil
    }

    /// Rewrites form fields for independent marketplace editing (unlocking rows
    /// that only inherit from General and mapping bare keys to marketplace keys).
    static func independentFields(_ fields: [NativeField], marketplace: String) -> [NativeField] {
        fields.map { raw in
            var copy = raw
            let rewritten = independentFieldKey(marketplace: marketplace, key: raw.path)
            copy.path = rewritten
            if isInheritedGeneralLock(locked: raw.locked, help: raw.help) {
                copy.locked = false
            }
            return copy
        }
    }

    /// Matches the web's `agentFieldLocked`: blocks edits while the listing
    /// assistant is writing that field or any parent/child path of it.
    static func isAgentLocked(_ path: String, lockedFields: [String]) -> Bool {
        guard !lockedFields.isEmpty else { return false }
        for locked in lockedFields {
            if locked == "*" || locked == path { return true }
            if path.hasPrefix(locked + ".") || locked.hasPrefix(path + ".") { return true }
        }
        return false
    }

    /// Resolves the current value to show for a rewritten marketplace field:
    /// prefers the listing's own marketplace value, falling back to the
    /// followed General value when unset, then the form's default value.
    static func displayValue(for field: NativeField, in document: [String: JSONValue], marketplace: String) -> JSONValue? {
        if field.locked { return field.value }
        if let stored = document.value(at: field.path), !isEmpty(stored), fits(field, stored) {
            return stored
        }
        if field.path == marketplacePriceKeys[marketplace],
           let generalPrice = document["price"], !isEmpty(generalPrice) {
            return generalPrice
        }
        let prefix = "\(marketplace)_specifics."
        if field.path.hasPrefix(prefix) {
            let tail = String(field.path.dropFirst(prefix.count))
            if followedGeneral.contains(tail),
               let general = document[tail], !isEmpty(general), fits(field, general) {
                return general
            }
        }
        return field.value
    }

    /// Applies one Quick Edit change to a copy of `listing`, enforcing:
    /// - per-marketplace key isolation (never overwriting General `price`),
    /// - disabled / hosted read-only gating,
    /// - agent field locks,
    /// - non-inherited field locks,
    /// - option label → stored value mapping,
    /// - numeric coercion,
    /// - companion paths (e.g. `direct_category_ids.ebay_path`),
    /// - dependent field clears (`field.clears`).
    /// Returns `nil` if the edit is blocked.
    static func applyEdit(
        to listing: [String: JSONValue],
        marketplace: String,
        field: NativeField?,
        columnKey: String,
        columnKind: SheetCellKind,
        value: JSONValue,
        companion: [String: JSONValue] = [:],
        lockedFields: [String] = [],
        disabled: Bool = false
    ) -> [String: JSONValue]? {
        let targetPath = independentFieldKey(marketplace: marketplace, key: field?.path ?? columnKey)
        guard !disabled else { return nil }
        guard !isAgentLocked(targetPath, lockedFields: lockedFields) else { return nil }
        if let field, field.locked, !isInheritedGeneralLock(locked: field.locked, help: field.help) {
            return nil
        }
        // Structured columns never accept free-text writes without a loaded field spec.
        if field == nil && columnKind != .text && columnKind != .textarea && columnKind != .number {
            return nil
        }

        var updated = listing
        let stored: JSONValue
        switch value {
        case .string(let text):
            if let mapped = field?.optionValues[text] {
                stored = .string(mapped)
            } else if field?.inputType == "number" || columnKind == .number {
                stored = coerceNumber(text)
            } else {
                stored = .string(text)
            }
        case .array(let items):
            stored = .array(items.map { item in
                if case .string(let label) = item, let mapped = field?.optionValues[label] {
                    return .string(mapped)
                }
                return item
            })
        default:
            stored = value
        }

        updated.set(stored, at: targetPath)
        for (extraPath, extraValue) in companion {
            updated.set(extraValue, at: extraPath)
        }
        for dependent in field?.clears ?? [] {
            let empty: JSONValue = dependent.hasSuffix(".attributes") ? .object([:])
                : dependent.hasSuffix(".inventoryRows") ? .array([]) : .string("")
            updated.set(empty, at: dependent)
        }
        return updated
    }

    static func coerceNumber(_ text: String) -> JSONValue {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "$", with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return .null }

        let normalized: String
        if trimmed.contains(",") {
            if trimmed.range(of: #"^-?\d{1,3}(,\d{3})+(\.\d+)?$"#, options: .regularExpression) != nil {
                normalized = trimmed.replacingOccurrences(of: ",", with: "")
            } else if !trimmed.contains("."),
                      trimmed.range(of: #"^-?\d+,\d{1,2}$"#, options: .regularExpression) != nil {
                normalized = trimmed.replacingOccurrences(of: ",", with: ".")
            } else {
                return .string(trimmed)
            }
        } else {
            normalized = trimmed
        }

        if let whole = Int(normalized) { return .int(whole) }
        if let number = Double(normalized), number.isFinite { return .double(number) }
        return .string(trimmed)
    }

    /// Shortens breadcrumb paths (`A > B > C` or `A / B / C`) to the leaf label.
    static func shortCategoryLabel(_ path: String) -> String {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "" }
        let parts = trimmed
            .components(separatedBy: CharacterSet(charactersIn: ">›/"))
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        return parts.last ?? trimmed
    }

    static func formatMoney(_ amount: Double) -> String {
        let formatted = GeneralFields.text(amount)
        return "$\(formatted)"
    }

    static func formatCellValue(column: MarketplaceSheetColumn, cell: ListingSheetCell?) -> String {
        guard let raw = cell?.value.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else { return "" }
        if column.key == "price" {
            if let numeric = Double(raw), numeric.isFinite {
                return formatMoney(numeric)
            }
            return raw
        }
        if column.kind == .category {
            return shortCategoryLabel(raw)
        }
        return raw
    }

    /// Per-marketplace price label for rows and List confirmations.
    /// Preserves non-numeric eBay variation/auction labels when `cell.editable == false`.
    static func priceLabel(
        marketplace: String,
        sheetRow: ListingSheetRow?,
        document: [String: JSONValue]?,
        fallbackPrice: Double?
    ) -> String? {
        if let cell = sheetRow?.cells?["price"] {
            let trimmed = cell.value.trimmingCharacters(in: .whitespacesAndNewlines)
            if !cell.editable && !trimmed.isEmpty {
                return trimmed
            }
        }
        if let document, let priceKey = marketplacePriceKeys[marketplace],
           let override = document.value(at: priceKey)?.number, override > 0 {
            return formatMoney(override)
        }
        if let cell = sheetRow?.cells?["price"] {
            let trimmed = cell.value.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                if let numeric = Double(trimmed), numeric.isFinite {
                    return formatMoney(numeric)
                }
                return trimmed
            }
        }
        if let docGeneral = document?["price"]?.number, docGeneral > 0 {
            return formatMoney(docGeneral)
        }
        if let fallbackPrice, fallbackPrice > 0 {
            return formatMoney(fallbackPrice)
        }
        return nil
    }

    /// Status for one listing on one marketplace tab (matches web `cellStatus` + `MarketplaceBadge`).
    static func rowStatus(
        listing: ConversationSummary,
        marketplace: String,
        sheetRow: ListingSheetRow?
    ) -> MarketplaceSheetStatus {
        let record = listing.directRecords[marketplace]
        let sold = listing.soldMarketplaces.contains(marketplace)
        let imported = listing.vendooMarketplaces.contains(marketplace)
        let removed = listing.removedMarketplaces.contains(marketplace)
        let importedURL = listing.vendooListingURLs[marketplace]
        let missingCount = sheetRow?.missing?.count ?? 0
        let live = ["published", "pending_review"].contains(record?.state ?? "")
        let busy = ["saving", "publishing", "updating"].contains(record?.state ?? "")
        let importedLive = imported && !removed && !busy
        let link = (sold || live || importedLive) ? (record?.listingURL ?? importedURL).flatMap(URL.init(string:)) : nil

        if sold { return MarketplaceSheetStatus(kind: .sold, label: "Sold", url: link) }
        switch record?.state {
        case "published", "pending_review":
            let label = record?.state == "pending_review" ? "In review" : "Listed"
            return MarketplaceSheetStatus(kind: .listed, label: label, url: link)
        case "saving", "publishing", "updating":
            return MarketplaceSheetStatus(kind: .working, label: "Working", url: nil)
        case "unknown":
            return MarketplaceSheetStatus(kind: .working, label: "Checking", url: nil)
        default:
            break
        }
        if importedLive {
            return MarketplaceSheetStatus(kind: .listed, label: "Listed", url: link)
        }
        if missingCount > 0 {
            return MarketplaceSheetStatus(kind: .needs, label: "Needs \(missingCount)", url: nil)
        }
        if let record, record.failed {
            if record.fields > 0 {
                return MarketplaceSheetStatus(kind: .needs, label: "Needs \(record.fields)", url: nil)
            }
            return MarketplaceSheetStatus(kind: .failed, label: "Failed", url: nil)
        }
        if record?.state == "draft" {
            return MarketplaceSheetStatus(kind: .draft, label: "Draft", url: nil)
        }
        if removed {
            return MarketplaceSheetStatus(kind: .delisted, label: "Delisted", url: nil)
        }
        return MarketplaceSheetStatus(kind: .notListed, label: "Not listed", url: nil)
    }

    /// Single source of truth for whether the row or Quick Edit sheet may offer `List`.
    static func canListFromSheet(
        listing: ConversationSummary,
        status: MarketplaceSheetStatus,
        sheetRow: ListingSheetRow?,
        canAct: Bool,
        revisionID: String? = nil
    ) -> Bool {
        guard listing.settledAt == nil else { return false }
        guard canAct else { return false }
        guard status.kind == .notListed || status.kind == .draft || status.kind == .delisted else { return false }
        if let sheetRow, !sheetRow.ready { return false }
        let rev = (revisionID ?? sheetRow?.revisionID ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return !rev.isEmpty
    }

    /// Floats rejected fields first, then missing fields, then remaining columns.
    static func orderedQuickEditColumns(marketplace: String, sheetRow: ListingSheetRow?) -> [MarketplaceSheetColumn] {
        let cols = columns(for: marketplace)
        guard let cells = sheetRow?.cells else { return cols }
        return cols.enumerated().sorted { lhs, rhs in
            let lCell = cells[lhs.element.key]
            let rCell = cells[rhs.element.key]
            let lRank = (lCell?.rejected == true) ? 0 : (lCell?.missing == true ? 1 : 2)
            let rRank = (rCell?.rejected == true) ? 0 : (rCell?.missing == true ? 1 : 2)
            if lRank != rRank { return lRank < rRank }
            return lhs.offset < rhs.offset
        }.map(\.element)
    }

    /// Compact one-line summary for a phone scan row.
    static func scanSummary(marketplace: String, sheetRow: ListingSheetRow?) -> MarketplaceScanSummary {
        guard let sheetRow else {
            return MarketplaceScanSummary(alert: nil, alertIsError: false, meta: "", needsLoad: false)
        }
        let missing = sheetRow.missing ?? []
        var alert: String?
        var alertIsError = false
        if let rejected = missing.first(where: \.rejected) {
            alert = "Fix \(rejected.label)"
            alertIsError = true
        } else if let firstMissing = missing.first {
            alert = "Missing \(firstMissing.label)"
        }

        let cols = columns(for: marketplace).filter { $0.inGrid && $0.key != "price" }
        var pieces: [String] = []
        for col in cols {
            let cell = sheetRow.cells?[col.key]
            if cell?.missing == true || cell?.rejected == true { continue }
            let shown = formatCellValue(column: col, cell: cell)
            if !shown.isEmpty {
                pieces.append(shown)
                if pieces.count == 3 { break }
            }
        }
        return MarketplaceScanSummary(
            alert: alert,
            alertIsError: alertIsError,
            meta: pieces.joined(separator: " · "),
            needsLoad: sheetRow.needsLoad
        )
    }

    /// Finds the next non-listed, non-sold listing in the current filtered rows
    /// for rapid Quick Edit traversal.
    static func nextQuickEditListingID(
        rows: [ConversationSummary],
        currentID: String,
        marketplace: String
    ) -> String? {
        guard let index = rows.firstIndex(where: { $0.id == currentID }), rows.count > 1 else { return nil }
        for offset in 1..<rows.count {
            let candidate = rows[(index + offset) % rows.count]
            let status = rowStatus(listing: candidate, marketplace: marketplace, sheetRow: nil)
            if status.kind != .listed && status.kind != .sold && status.kind != .working && candidate.settledAt == nil {
                return candidate.id
            }
        }
        return nil
    }

    private static func isEmpty(_ value: JSONValue) -> Bool {
        switch value {
        case .null: true
        case .string(let s): s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        case .array(let items): items.isEmpty
        default: false
        }
    }

    private static func fits(_ field: NativeField, _ value: JSONValue) -> Bool {
        guard field.selectionOnly, !field.options.isEmpty else { return true }
        let known = field.choices.known
        switch value {
        case .array(let items): return items.allSatisfy { known.contains($0.text) }
        default: return known.contains(value.text)
        }
    }
}
