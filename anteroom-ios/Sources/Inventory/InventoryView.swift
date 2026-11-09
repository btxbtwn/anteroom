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
    @State private var path: [ConversationSummary] = []
    /// The listing in the editor beside the list, in the side-by-side layout.
    @State private var open: ConversationSummary?
    @State private var creatingListing = false
    @State private var bulkListing = false
    @State private var renaming: ConversationSummary?
    @State private var newTitle = ""
    @State private var deleting: ConversationSummary?
    @State private var selecting = false
    @State private var selected: Set<String> = []
    @State private var bulk: BulkAction?
    @State private var templating: ConversationSummary?

    init(api: AnteroomAPI) {
        _model = State(initialValue: InventoryModel(api: api))
    }

    private var sideBySide: Bool { sizeClass == .regular }

    var body: some View {
        Group {
            if sideBySide {
                NavigationSplitView {
                    // The system's 320-point column folds New listing and Bulk
                    // listing into a More menu; this width keeps them visible.
                    inventory.navigationSplitViewColumnWidth(min: 380, ideal: 420, max: 480)
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
                    inventory.navigationDestination(for: ConversationSummary.self) { editor($0) }
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

    private func editor(_ listing: ConversationSummary) -> some View {
        ListingEditorView(api: model.api, listing: listing) { Task { await model.reload() } }
    }

    /// Opens the listing beside the list, or pushes it on iPhone.
    private func show(_ listing: ConversationSummary) {
        if sideBySide {
            open = listing
        } else {
            path.append(listing)
        }
    }

    /// The web's phone row (sidebar/ListingRow.tsx): no disclosure chevron, and
    /// the row's actions behind one full-size "…" button.
    private func link(_ listing: ConversationSummary, hint: String?) -> some View {
        let row = ListingRow(api: model.api, listing: listing, hint: hint, selling: model.preferredMarketplaces)
        return HStack(spacing: 0) {
            if sideBySide {
                Button { open = listing } label: {
                    row.frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(open?.id == listing.id ? .isSelected : [])
            } else {
                row.frame(maxWidth: .infinity, alignment: .leading)
                    .background { NavigationLink(value: listing) { EmptyView() }.opacity(0) }
            }
            if !selecting {
                Menu { actions(listing) } label: {
                    Image(systemName: "ellipsis")
                        .font(.body)
                        .foregroundStyle(WorkspaceStyle.mutedText)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.borderless)
                .accessibilityLabel("Actions for \(listing.displayTitle)")
            }
        }
    }

    @ViewBuilder
    private func actions(_ listing: ConversationSummary) -> some View {
        Button("Rename", systemImage: "pencil") {
            newTitle = listing.title ?? ""
            renaming = listing
        }
        Button("Copy listing", systemImage: "doc.on.doc") { bulk = .copy([listing.id]) }
        Button("Listing templates", systemImage: "rectangle.stack") { templating = listing }
        Button("Delete", systemImage: "trash", role: .destructive) { deleting = listing }
    }

    private var inventory: some View {
        @Bindable var model = model
        let counts = model.counts(search: search)
        let rows = model.rows(search: search)
        let hints = Suggestion.hints(model.suggestions)
        let stale = model.staleListingIDs
        return VStack(spacing: 0) {
            StatusTabs(tabs: model.tabs(counts: counts), counts: counts, selection: $model.filters.status)
            List(selection: $selected) {
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
                    .listRowSeparator(.hidden)
                    .listRowBackground(sideBySide && open?.id == listing.id ? WorkspaceStyle.selected : Color.clear)
                    .listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 0, trailing: 4))
                    .swipeActions(edge: .trailing) {
                        Button("Delete", systemImage: "trash") { deleting = listing }
                            .tint(.red)
                        Button("Rename", systemImage: "pencil") {
                            newTitle = listing.title ?? ""
                            renaming = listing
                        }
                    }
                    .contextMenu { actions(listing) }
            }
        }
        .listStyle(.plain)
        .environment(\.editMode, .constant(selecting ? .active : .inactive))
        .overlay { emptyState(rows: rows) }
        .navigationTitle("Inventory")
        .navigationBarTitleDisplayMode(.inline)
        .scrollContentBackground(.hidden)
        .background(WorkspaceStyle.sidebar)
        .toolbarBackground(WorkspaceStyle.sidebar, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .searchable(text: $search, prompt: "Search title or SKU")
        .refreshable { await model.reload() }
        }
        // The status pills sit above the list, so the whole column needs the sidebar color, not only the list.
        .background(WorkspaceStyle.sidebar)
        .task { if !model.hasLoaded { await model.reload() } }
        .reloadingOnReturn(when: !model.hasLoaded && model.error != nil) { await model.reload() }
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
                BulkMarkdownView(api: model.api, ids: ids, titles: model.titles(for: ids),
                                 prefetched: model.markdownReview(for: ids)) { finishBulk() }
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
    private func emptyState(rows: [ConversationSummary]) -> some View {
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
        } else if !model.hasLoaded, model.error != nil, !model.isLoading {
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
    private func ordered(_ rows: [ConversationSummary]) -> [String] {
        rows.map(\.id).filter(selected.contains)
    }

    private func finishBulk() {
        selecting = false
        selected = []
        // Prices or listings changed: the next markdown review is read again.
        model.forgetMarkdownReview()
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
        ScrollView(.horizontal) {
            if #available(iOS 26, *) {
                GlassEffectContainer(spacing: 8) {
                    pills
                }
            } else {
                pills
            }
        }
        .scrollIndicators(.hidden)
        .contentMargins(.horizontal, 16, for: .scrollContent)
        .padding(.vertical, 6)
    }

    private var pills: some View {
        HStack(spacing: 8) {
            ForEach(tabs) { tab in
                let count = counts[tab] ?? 0
                let isSelected = selection == tab
                Button {
                    selection = tab
                } label: {
                    HStack(spacing: 4) {
                        Text(tab.label).lineLimit(1)
                        Text(InventoryQuery.compactCount(count)).opacity(0.7).monospacedDigit()
                    }
                    .font(.subheadline.weight(.medium))
                    .padding(.horizontal, 8)
                    .frame(minHeight: 32)
                    .fixedSize(horizontal: true, vertical: false)
                }
                .modifier(StatusPillStyle(selected: isSelected))
                .controlSize(.small)
                .frame(minHeight: 44)
                .accessibilityLabel("\(tab.label), \(count) listings")
                .accessibilityAddTraits(isSelected ? .isSelected : [])
            }
        }
    }
}

private struct StatusPillStyle: ViewModifier {
    let selected: Bool

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(iOS 26, *) {
            if selected {
                content.buttonStyle(.glassProminent).buttonBorderShape(.capsule)
            } else {
                content.buttonStyle(.glass).buttonBorderShape(.capsule)
            }
        } else {
            content
                .buttonStyle(.plain)
                .foregroundStyle(selected ? Color.primary : Color.secondary)
                .background(selected ? WorkspaceStyle.selected : WorkspaceStyle.surface, in: Capsule())
        }
    }
}

struct ListingRow: View {
    let api: AnteroomAPI
    let listing: ConversationSummary
    var hint: String?
    var selling: [String] = []
    @Environment(AppearanceModel.self) private var appearance

    var body: some View {
        ListingRowContent(listing: listing, bySKU: appearance.listingNames == .sku, hint: hint, selling: selling) {
            if let cover = listing.coverPhotoID {
                RemotePhoto(api: api, photoID: cover, maxPixels: 192, version: listing.coverPhotoVersion, urgent: false)
            } else {
                Image(systemName: "photo")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(WorkspaceStyle.surface)
            }
        }
    }
}

/// The web's listing row (sidebar/ListingRow.tsx): cover, one-line name, the
/// lifecycle dot and word, then a logo for each marketplace it is listed on.
struct ListingRowContent<Thumbnail: View>: View {
    let listing: ConversationSummary
    var bySKU = false
    var hint: String?
    /// The marketplaces the seller sells on; the logos show only these.
    var selling: [String] = []
    let thumbnail: Thumbnail

    init(listing: ConversationSummary, bySKU: Bool = false, hint: String? = nil, selling: [String] = [],
         @ViewBuilder thumbnail: () -> Thumbnail) {
        self.listing = listing
        self.bySKU = bySKU
        self.hint = hint
        self.selling = selling
        self.thumbnail = thumbnail()
    }

    /// Settings → General → Listing names; a listing without a SKU keeps its title.
    private var name: String {
        if bySKU, let sku = listing.sku?.trimmingCharacters(in: .whitespaces), !sku.isEmpty { return sku }
        return listing.displayTitle
    }

    var body: some View {
        HStack(spacing: 16) {
            thumbnail
                .frame(width: 64, height: 64)
                .clipped()
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(WorkspaceStyle.border, lineWidth: 1))
                .opacity(listing.settledAt == nil ? 1 : 0.55)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(name).font(.body.weight(.medium)).lineLimit(1)
                HStack(spacing: 8) {
                    HStack(spacing: 6) {
                        Circle().fill(statusColor).frame(width: 6, height: 6).accessibilityHidden(true)
                        Text(listing.statusLabel)
                            .foregroundStyle(listing.inventoryStatus == "failed" ? WorkspaceStyle.destructive : WorkspaceStyle.mutedText)
                    }
                    if listing.status == "sold", !listing.missingSaleData.isEmpty {
                        Text("Missing sale data")
                            .font(.caption2.weight(.medium))
                            .foregroundStyle(WorkspaceStyle.warning)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(WorkspaceStyle.warning.opacity(0.14), in: Capsule())
                            .accessibilityLabel("Missing sale data: \(listing.missingSaleData.joined(separator: ", "))")
                    }
                    if let hint {
                        Text("· \(hint)").foregroundStyle(WorkspaceStyle.mutedText).lineLimit(1)
                    }
                }
                .font(.subheadline)
                let logos = listing.rowLogos(selling: selling)
                if !logos.isEmpty {
                    HStack(spacing: 8) {
                        ForEach(logos, id: \.marketplace) { logo in
                            MarketplaceLogo(id: logo.marketplace).opacity(logo.sold ? 0.55 : 1)
                        }
                    }
                    .padding(.top, 4)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("Listed on " + logos.map { Marketplaces.name($0.marketplace) }.joined(separator: ", "))
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 12)
        .frame(minHeight: 96)
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

/// The web's marketplace logo (MarketplaceLogo.tsx), from the same artwork; a
/// marketplace without one shows its initial.
struct MarketplaceLogo: View {
    let id: String
    var size: CGFloat = 20

    var body: some View {
        Group {
            if UIImage(named: "Marketplaces/\(id)") != nil {
                Image("Marketplaces/\(id)").resizable().scaledToFit().padding(1)
            } else {
                Text(Marketplaces.name(id).prefix(1).uppercased())
                    .font(.system(size: size * 0.6, weight: .semibold))
                    .foregroundStyle(WorkspaceStyle.mutedText)
            }
        }
        .frame(width: size, height: size)
        .accessibilityLabel(Marketplaces.name(id))
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
