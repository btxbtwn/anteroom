import SwiftUI

/// On iPhone, and in a narrow iPad window, the list pushes the editor. With
/// room for both (a regular-width iPad window), the list stays on the left and
/// the open listing fills the right, as in Mail.
struct InventoryView: View {
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(LiveChanges.self) private var live: LiveChanges?
    @State private var model: InventoryModel
    @State private var search = ""
    @State private var path: [ListingSummary] = []
    /// The listing in the editor beside the list, in the side-by-side layout.
    @State private var open: ListingSummary?
    @State private var creatingListing = false
    @State private var bulkListing = false
    @State private var renaming: ListingSummary?
    @State private var newTitle = ""
    @State private var deleting: ListingSummary?
    @State private var selecting = false
    @State private var selected: Set<String> = []
    @State private var bulk: BulkAction?
    @State private var templating: ListingSummary?

    init(api: AnteroomAPI) {
        _model = State(initialValue: InventoryModel(api: api))
    }

    private var sideBySide: Bool { sizeClass == .regular }

    var body: some View {
        Group {
            if sideBySide {
                NavigationSplitView {
                    inventory
                } detail: {
                    NavigationStack {
                        if let open {
                            editor(open).id(open.id)
                        } else {
                            ContentUnavailableView("No listing open", systemImage: "tag",
                                                   description: Text("Choose a listing to edit it here."))
                        }
                    }
                }
            } else {
                NavigationStack(path: $path) {
                    inventory.navigationDestination(for: ListingSummary.self) { editor($0) }
                }
            }
        }
        // Resizing an iPad window can switch layouts; the open listing stays open.
        .onChange(of: sideBySide) { _, wide in
            if wide {
                open = path.first
            } else {
                path = open.map { [$0] } ?? []
            }
        }
        .onChange(of: model.listings.map(\.id)) { _, ids in
            if let listing = open, !ids.contains(listing.id) { open = nil }
        }
    }

    private func editor(_ listing: ListingSummary) -> some View {
        ListingEditorView(api: model.api, listing: listing) { Task { await model.reload() } }
    }

    /// Opens the listing beside the list, or pushes it on iPhone.
    private func show(_ listing: ListingSummary) {
        if sideBySide {
            open = listing
        } else {
            path.append(listing)
        }
    }

    @ViewBuilder
    private func link(_ listing: ListingSummary, hint: String?) -> some View {
        let row = ListingRow(api: model.api, listing: listing, hint: hint, missing: model.missing(listing))
        if sideBySide {
            Button { open = listing } label: {
                row.frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityAddTraits(open?.id == listing.id ? .isSelected : [])
        } else {
            NavigationLink(value: listing) { row }
        }
    }

    private var inventory: some View {
        @Bindable var model = model
        let counts = model.counts(search: search)
        let rows = model.rows(search: search)
        let hints = Suggestion.hints(model.suggestions)
        let stale = model.suggestions.filter { $0.kind == "stale_active" }.map(\.conversationID)
        return List(selection: $selected) {
            // Suggestions show on each listing's own row; only the batch markdown needs a place of its own.
            if !selecting && !stale.isEmpty {
                Button(stale.count == 1 ? "Mark down 1 stale listing" : "Mark down \(stale.count) stale listings",
                       systemImage: "tag") { bulk = .markdown(stale) }
                    .font(.footnote)
                    .accessibilityHint("Review a lower price for each stale listing. Saves in Anteroom only.")
                    .listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)
            }
            ForEach(rows) { listing in
                link(listing, hint: hints[listing.id])
                    .onAppear { model.wantsReadiness(listing) }
                    .listRowSeparator(.hidden)
                    .listRowBackground(sideBySide && open?.id == listing.id ? WorkspaceStyle.selected : Color.clear)
                    .listRowInsets(EdgeInsets(top: 2, leading: 16, bottom: 2, trailing: 16))
                    .swipeActions(edge: .trailing) {
                        Button("Delete", systemImage: "trash") { deleting = listing }
                            .tint(.red)
                        Button("Rename", systemImage: "pencil") {
                            newTitle = listing.title ?? ""
                            renaming = listing
                        }
                    }
                    .contextMenu {
                        Button("Rename", systemImage: "pencil") {
                            newTitle = listing.title ?? ""
                            renaming = listing
                        }
                        Button("Copy listing", systemImage: "doc.on.doc") { bulk = .copy([listing.id]) }
                        Button("Listing templates", systemImage: "rectangle.stack") { templating = listing }
                        Button("Delete", systemImage: "trash", role: .destructive) { deleting = listing }
                    }
            }
        }
        .listStyle(.plain)
        .environment(\.editMode, .constant(selecting ? .active : .inactive))
        .safeAreaInset(edge: .top, spacing: 0) {
            StatusTabs(tabs: model.tabs(counts: counts), counts: counts, selection: $model.filters.status)
        }
        .overlay { emptyState(rows: rows) }
        .navigationTitle("Inventory")
        .navigationBarTitleDisplayMode(.inline)
        .scrollContentBackground(.hidden)
        .background(WorkspaceStyle.sidebar)
        .toolbarBackground(WorkspaceStyle.sidebar, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .searchable(text: $search, prompt: "Search title or SKU")
        .refreshable { await model.reload() }
        .task { if !model.hasLoaded { await model.reload() } }
        // Listings change on other devices; catch up when the app comes back
        // after a while (a quick switch away keeps what's on screen).
        .onChange(of: scenePhase) { _, phase in
            if phase == .active && model.hasLoaded && model.shouldReloadOnReturn() { Task { await model.reload() } }
        }
        // And live, while the app is open; a burst of changes reloads once.
        .task(id: live?.listings) {
            guard model.hasLoaded, (live?.listings ?? 0) > 0 else { return }
            try? await Task.sleep(for: .milliseconds(1500))
            if !Task.isCancelled { await model.reload() }
        }
        // Marketplace work moves a row on only while it is listing, updating or generating.
        .task(id: live?.jobs) {
            guard model.hasLoaded, (live?.jobs ?? 0) > 0, model.hasWorkInProgress else { return }
            try? await Task.sleep(for: .milliseconds(1500))
            if !Task.isCancelled { await model.reload() }
        }
        .toolbar {
            ToolbarItemGroup(placement: .topBarLeading) {
                InventoryFilterMenu(model: model, search: search)
                sortMenu
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button(selecting ? "Done" : "Select") {
                    selecting.toggle()
                    selected = []
                }
                .frame(minHeight: 44)
                .disabled(model.listings.isEmpty)
            }
            if selecting {
                ToolbarItemGroup(placement: .bottomBar) {
                    let visible = Set(rows.map(\.id))
                    Button(visible.isSubset(of: selected) && !visible.isEmpty ? "Deselect all" : "Select all") {
                        selected = visible.isSubset(of: selected) ? [] : visible
                    }
                    Spacer()
                    Text("\(selected.count) selected").font(.footnote).foregroundStyle(.secondary)
                    Spacer()
                    Menu("Actions") {
                        Button("Edit fields", systemImage: "square.and.pencil") { bulk = .edit(ordered(rows)) }
                        Button("Copy items", systemImage: "doc.on.doc") { bulk = .copy(ordered(rows)) }
                        Button("Mark down prices", systemImage: "tag") { bulk = .markdown(ordered(rows)) }
                        Button("Regenerate", systemImage: "arrow.triangle.2.circlepath") { bulk = .regenerate(ordered(rows)) }
                        Button("Delete", systemImage: "trash", role: .destructive) { bulk = .delete(ordered(rows)) }
                    }
                    .disabled(selected.isEmpty || selected.count > 300)
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button("Bulk listing", systemImage: "square.stack.3d.up") { bulkListing = true }
                    .frame(minWidth: 44, minHeight: 44)
                    .accessibilityHint("Make many drafts from one batch of photos")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button("New listing", systemImage: "plus", action: newListing)
                    .disabled(creatingListing)
                    .frame(minWidth: 44, minHeight: 44)
                    .accessibilityHint("Create a draft, then add photos and item details")
            }
        }
        .alert("Rename listing", isPresented: renameShown) {
            TextField("Title", text: $newTitle)
            Button("Cancel", role: .cancel) {}
            Button("Rename") {
                let title = newTitle.trimmingCharacters(in: .whitespacesAndNewlines)
                if let listing = renaming, !title.isEmpty { Task { await model.rename(listing, to: title) } }
            }
        }
        .confirmationDialog(deleteTitle, isPresented: deleteShown, titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                if let listing = deleting { Task { await model.delete(listing) } }
            }
        } message: {
            Text("This removes the listing and its photos from Anteroom.")
        }
        .alert("Something went wrong", isPresented: errorShown) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.error ?? "")
        }
        .sheet(item: $templating) { listing in
            ListingTemplatesSheet(api: model.api, listingID: listing.id) { Task { await model.reload() } }
        }
        .sheet(isPresented: $bulkListing) {
            BulkListingView(api: model.api) { Task { await model.reload() } }
        }
        .sheet(item: $bulk) { action in
            switch action {
            case .edit(let ids):
                BulkEditView(api: model.api, ids: ids) { finishBulk() }
            case .delete(let ids):
                BulkDeleteView(api: model.api, ids: ids) { finishBulk() }
            case .copy(let ids):
                BulkCopyView(api: model.api, ids: ids) { finishBulk() }
            case .markdown(let ids):
                BulkMarkdownView(api: model.api, ids: ids) { finishBulk() }
            case .regenerate(let ids):
                BulkRegenerateView(api: model.api, listings: ids.compactMap { id in model.listings.first { $0.id == id } }) { finishBulk() }
            }
        }
    }

    private var sortMenu: some View {
        Menu {
            Picker("Sort listings", selection: Binding(get: { model.filters.sort }, set: { model.filters.choose($0) })) {
                ForEach(ListingSort.allCases) { sort in Text(sort.label).tag(sort) }
            }
        } label: {
            Label("Sort", systemImage: "arrow.up.arrow.down")
        }
        .frame(minWidth: 44, minHeight: 44)
        .accessibilityLabel("Sort listings, \(model.filters.sort.label)")
    }

    @ViewBuilder
    private func emptyState(rows: [ListingSummary]) -> some View {
        if rows.isEmpty && model.hasLoaded {
            if model.listings.isEmpty {
                ContentUnavailableView {
                    Label("Your inventory starts here", systemImage: "tag")
                } description: {
                    Text("Create a listing, or make many drafts at once from one batch of photos.")
                } actions: {
                    Button("New listing", action: newListing)
                        .disabled(creatingListing)
                        .workspaceProminentButton()
                        .frame(minHeight: 44)
                    Button("Bulk listing") { bulkListing = true }
                        .frame(minHeight: 44)
                }
            } else if !search.isEmpty {
                ContentUnavailableView.search(text: search)
            } else {
                ContentUnavailableView {
                    Label("No matching listings", systemImage: "line.3.horizontal.decrease.circle")
                } actions: {
                    if model.filters.activeCount > 0 || model.filters.status != .all {
                        Button("Show all listings") { model.filters = InventoryFilters(sort: model.filters.sort) }
                            .frame(minHeight: 44)
                    }
                }
            }
        } else if !model.hasLoaded, model.error != nil {
            ContentUnavailableView {
                Label("Couldn't load inventory", systemImage: "wifi.exclamationmark")
            } description: {
                Text("Check your connection and try again.")
            } actions: {
                Button("Try again") { Task { await model.reload() } }
                    .disabled(model.isLoading)
                    .workspaceProminentButton()
                    .frame(minHeight: 44)
            }
        } else if !model.hasLoaded && model.isLoading {
            ProgressView()
        }
    }

    private var errorShown: Binding<Bool> {
        Binding(get: { model.hasLoaded && model.error != nil }, set: { if !$0 { model.error = nil } })
    }

    private var renameShown: Binding<Bool> {
        Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })
    }

    private var deleteShown: Binding<Bool> {
        Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })
    }

    private var deleteTitle: String {
        "Delete \"\(deleting?.displayTitle ?? "")\"?"
    }

    /// The selection in the order the list shows it.
    private func ordered(_ rows: [ListingSummary]) -> [String] {
        rows.map(\.id).filter(selected.contains)
    }

    private func finishBulk() {
        selecting = false
        selected = []
        Task { await model.reload() }
    }

    /// Like the web: one tap makes an untitled draft and opens it. The
    /// seller adds photos there and generation writes the title.
    private func newListing() {
        guard !creatingListing else { return }
        creatingListing = true
        Task {
            defer { creatingListing = false }
            if let listing = await model.createListing() { show(listing) }
        }
    }
}

/// Its own view so a redraw of the list (a selection tap) does not recount
/// the marketplace facets over the whole inventory.
private struct InventoryFilterMenu: View {
    let model: InventoryModel
    let search: String

    var body: some View {
        let source = model.facetSource(search: search)
        let options = model.marketplaceOptions()
        let counts = InventoryQuery.marketplaceCounts(source, options: options, notListed: model.filters.notListed)
        return Menu {
            Section(model.filters.notListed ? "Not listed on" : "Marketplaces") {
                ForEach(options, id: \.self) { id in
                    Toggle(isOn: Binding(get: { model.filters.marketplaces.contains(id) },
                                         set: { _ in model.toggleMarketplace(id) })) {
                        Text("\(Marketplaces.name(id))  \(counts[id] ?? 0)")
                    }
                }
                if options.isEmpty {
                    Text("No listing is live on a marketplace yet")
                }
            }
            Section {
                Toggle(isOn: Binding(get: { model.filters.notListed }, set: { model.filters.notListed = $0 })) {
                    Text("Not listed  \(InventoryQuery.notListedCount(source))")
                }
            }
            if model.filters.activeCount > 0 {
                Button("Clear filters", role: .destructive) {
                    model.filters.marketplaces = []
                    model.filters.notListed = false
                }
            }
        } label: {
            Label(model.filters.activeCount > 0 ? "Filters (\(model.filters.activeCount))" : "Filters",
                  systemImage: model.filters.activeCount > 0
                      ? "line.3.horizontal.decrease.circle.fill" : "line.3.horizontal.decrease.circle")
        }
        .menuActionDismissBehavior(.disabled)
        .frame(minWidth: 44, minHeight: 44)
        .accessibilityLabel(model.filters.activeCount > 0 ? "Filters, \(model.filters.activeCount) on" : "Filters")
    }
}

/// All / Draft / Active / Sold (and Failed while anything failed), with counts.
private struct StatusTabs: View {
    let tabs: [StatusTab]
    let counts: [StatusTab: Int]
    @Binding var selection: StatusTab

    var body: some View {
        // The web's status tabs (ListingFilters.tsx): one bordered track
        // with the selected tab raised inside it.
        HStack(spacing: 2) {
            ForEach(tabs) { tab in
                let count = counts[tab] ?? 0
                let isSelected = selection == tab
                Button {
                    selection = tab
                } label: {
                    HStack(spacing: 4) {
                        Text(tab.label).lineLimit(1)
                        Text(InventoryQuery.compactCount(count)).foregroundStyle(WorkspaceStyle.mutedText).monospacedDigit()
                    }
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(isSelected ? Color.primary : WorkspaceStyle.mutedText)
                    .frame(maxWidth: .infinity, minHeight: 36)
                    .background(isSelected ? WorkspaceStyle.selected : .clear,
                                in: RoundedRectangle(cornerRadius: WorkspaceStyle.controlRadius - 2, style: .continuous))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .frame(minHeight: 44)
                .accessibilityLabel("\(tab.label), \(count) listings")
                .accessibilityAddTraits(isSelected ? .isSelected : [])
            }
        }
        .padding(2)
        .background(WorkspaceStyle.groupedBackground, in: RoundedRectangle(cornerRadius: WorkspaceStyle.controlRadius, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: WorkspaceStyle.controlRadius, style: .continuous).stroke(WorkspaceStyle.border, lineWidth: 1))
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
        .background(WorkspaceStyle.sidebar)
    }
}

struct ListingRow: View {
    let api: AnteroomAPI
    let listing: ListingSummary
    var hint: String?
    var missing: [String: [MissingField]] = [:]
    @Environment(AppearanceModel.self) private var appearance

    var body: some View {
        ListingRowContent(listing: listing, bySKU: appearance.listingNames == .sku, hint: hint, missing: missing) {
            if let cover = listing.coverPhotoID {
                RemotePhoto(api: api, photoID: cover, maxPixels: 96)
            } else {
                Image(systemName: "photo")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(WorkspaceStyle.surface)
            }
        }
    }
}

/// The desktop row: cover, name, colored lifecycle word, the suggested next
/// step, then each marketplace's status as a compact badge.
struct ListingRowContent<Thumbnail: View>: View {
    let listing: ListingSummary
    var bySKU = false
    var hint: String?
    var missing: [String: [MissingField]] = [:]
    let thumbnail: Thumbnail

    init(listing: ListingSummary, bySKU: Bool = false, hint: String? = nil, missing: [String: [MissingField]] = [:],
         @ViewBuilder thumbnail: () -> Thumbnail) {
        self.listing = listing
        self.bySKU = bySKU
        self.hint = hint
        self.missing = missing
        self.thumbnail = thumbnail()
    }

    /// Settings → General → Listing names; a listing without a SKU keeps its title.
    private var name: String {
        if bySKU, let sku = listing.sku?.trimmingCharacters(in: .whitespaces), !sku.isEmpty { return sku }
        return listing.displayTitle
    }

    var body: some View {
        HStack(spacing: 12) {
            thumbnail
                .frame(width: 44, height: 44)
                .clipped()
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(WorkspaceStyle.border, lineWidth: 1))
                .opacity(listing.settledAt == nil ? 1 : 0.6)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(name).font(.subheadline.weight(.medium)).lineLimit(2)
                HStack(spacing: 6) {
                    HStack(spacing: 5) {
                        Circle().fill(statusColor).frame(width: 6, height: 6).accessibilityHidden(true)
                        Text(listing.statusLabel).fontWeight(.medium)
                            .foregroundStyle(listing.inventoryStatus == "failed" ? WorkspaceStyle.destructive : WorkspaceStyle.mutedText)
                    }
                    if let price = listing.price {
                        Text(price, format: .currency(code: "USD")).foregroundStyle(.secondary)
                    }
                    if bySKU, name != listing.displayTitle {
                        Text(listing.displayTitle).foregroundStyle(.secondary).lineLimit(1)
                    } else if let sku = listing.sku, !sku.isEmpty {
                        Text(sku).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                .font(.caption)
                if let hint {
                    Text(hint).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
                if listing.status == "sold", let missing = listing.missingSaleData, !missing.isEmpty {
                    Text("Missing sale data")
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(WorkspaceStyle.warning)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(WorkspaceStyle.warning.opacity(0.14), in: Capsule())
                        .accessibilityLabel("Missing sale data: \(missing.joined(separator: ", "))")
                }
                let badges = listing.marketplaceBadges(missing: missing)
                if !badges.isEmpty {
                    MarketplaceBadges(badges: badges)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }

    /// The web's status dots (ListingBits.tsx): the word stays muted.
    private var statusColor: Color {
        switch listing.inventoryStatus {
        case "draft": WorkspaceStyle.mutedText
        case "in_progress", "listing": WorkspaceStyle.info
        case "active": WorkspaceStyle.success
        case "sold": WorkspaceStyle.accent
        case "failed": WorkspaceStyle.destructive
        default: WorkspaceStyle.mutedText
        }
    }
}

/// "eBay Listed · Poshmark Needs 2 fields", wrapped to the row's width.
private struct MarketplaceBadges: View {
    let badges: [MarketplaceBadge]

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 4) { chips }
            VStack(alignment: .leading, spacing: 4) { chips }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(badges.map(Self.spoken).joined(separator: ", "))
    }

    private static func spoken(_ badge: MarketplaceBadge) -> String {
        var text = "\(Marketplaces.name(badge.marketplace)) \(badge.label)"
        if badge.tone == .attention, let detail = badge.detail { text += ": \(detail)" }
        guard let price = badge.price else { return text }
        return "\(text) at \(price.formatted(.currency(code: "USD")))\(badge.priceDiffers ? ", not the item price" : "")"
    }

    private var chips: some View {
        ForEach(badges, id: \.marketplace) { badge in
            HStack(spacing: 3) {
                Text(Marketplaces.name(badge.marketplace)).foregroundStyle(.secondary)
                Text(badge.label).fontWeight(.medium).foregroundStyle(color(badge.tone))
                if let price = badge.price {
                    // A price apart from the item's own stands out, as on the desktop table.
                    Text(price, format: .currency(code: "USD"))
                        .monospacedDigit()
                        .fontWeight(badge.priceDiffers ? .semibold : .regular)
                        .foregroundStyle(badge.priceDiffers ? Color.primary : Color.secondary)
                }
            }
            .font(.caption2)
            .lineLimit(1)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(WorkspaceStyle.surface, in: Capsule())
            .overlay(Capsule().stroke(WorkspaceStyle.border, lineWidth: 1))
        }
    }

    private func color(_ tone: MarketplaceBadge.Tone) -> Color {
        switch tone {
        case .good: .green
        case .attention: .orange
        case .problem: .red
        case .neutral: .secondary
        }
    }
}

private enum BulkAction: Identifiable {
    case edit([String]), delete([String]), copy([String]), markdown([String]), regenerate([String])

    var id: String {
        switch self {
        case .edit(let ids): "edit-" + ids.joined(separator: ",")
        case .delete(let ids): "delete-" + ids.joined(separator: ",")
        case .copy(let ids): "copy-" + ids.joined(separator: ",")
        case .markdown(let ids): "markdown-" + ids.joined(separator: ",")
        case .regenerate(let ids): "regenerate-" + ids.joined(separator: ",")
        }
    }
}
