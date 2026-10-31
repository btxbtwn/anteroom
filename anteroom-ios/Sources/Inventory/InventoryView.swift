import SwiftUI

struct InventoryView: View {
    @State private var model: InventoryModel
    @State private var search = ""
    @State private var path: [ListingSummary] = []
    @State private var showingNewListing = false
    @State private var createdListing: ListingSummary?
    @State private var renaming: ListingSummary?
    @State private var newTitle = ""
    @State private var deleting: ListingSummary?
    @State private var selecting = false
    @State private var selected: Set<String> = []
    @State private var bulk: BulkAction?

    init(api: AnteroomAPI) {
        _model = State(initialValue: InventoryModel(api: api))
    }

    var body: some View {
        @Bindable var model = model
        let counts = model.counts(search: search)
        let rows = model.rows(search: search)
        NavigationStack(path: $path) {
            List(selection: $selected) {
                if !selecting {
                    SuggestionsSection(api: model.api, suggestions: model.suggestions) { bulk = .markdown($0) }
                }
                ForEach(rows) { listing in
                    NavigationLink(value: listing) {
                        ListingRow(api: model.api, listing: listing)
                    }
                    .listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)
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
            .toolbar {
                ToolbarItemGroup(placement: .topBarLeading) {
                    filterMenu
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
                            Button("Delete", systemImage: "trash", role: .destructive) { bulk = .delete(ordered(rows)) }
                        }
                        .disabled(selected.isEmpty || selected.count > 300)
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("New listing", systemImage: "plus") { showingNewListing = true }
                        .frame(minWidth: 44, minHeight: 44)
                        .accessibilityHint("Create a draft, then add photos and item details")
                }
            }
            .sheet(isPresented: $showingNewListing, onDismiss: openCreatedListing) {
                NewListingView(api: model.api) { listing in
                    model.created(listing)
                    createdListing = listing
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
                }
            }
            .navigationDestination(for: ListingSummary.self) { listing in
                ListingEditorView(api: model.api, listing: listing) { Task { await model.reload() } }
            }
        }
    }

    private var filterMenu: some View {
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

    private var sortMenu: some View {
        Menu {
            Picker("Sort listings", selection: Binding(get: { model.filters.sort }, set: { model.filters.sort = $0 })) {
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
                    Text("Create a listing, add your photos, and keep your draft ready to edit.")
                } actions: {
                    Button("New listing") { showingNewListing = true }
                        .buttonStyle(.borderedProminent)
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
                    .buttonStyle(.borderedProminent)
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

    private func openCreatedListing() {
        guard let listing = createdListing else { return }
        createdListing = nil
        path.append(listing)
    }
}

/// All / Draft / Active / Sold (and Failed while anything failed), with counts.
private struct StatusTabs: View {
    let tabs: [StatusTab]
    let counts: [StatusTab: Int]
    @Binding var selection: StatusTab

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(tabs) { tab in
                    let count = counts[tab] ?? 0
                    Button {
                        selection = tab
                    } label: {
                        HStack(spacing: 5) {
                            Text(tab.label).fontWeight(.medium)
                            Text(InventoryQuery.compactCount(count)).foregroundStyle(.secondary).monospacedDigit()
                        }
                        .font(.subheadline)
                        .padding(.horizontal, 12)
                        .frame(minHeight: 36)
                        .background(selection == tab ? WorkspaceStyle.surface : .clear, in: Capsule())
                        .overlay(Capsule().stroke(selection == tab ? WorkspaceStyle.border : .clear, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .frame(minHeight: 44)
                    .accessibilityLabel("\(tab.label), \(count) listings")
                    .accessibilityAddTraits(selection == tab ? .isSelected : [])
                }
            }
            .padding(.horizontal, 16)
        }
        .background(WorkspaceStyle.sidebar)
    }
}

struct ListingRow: View {
    let api: AnteroomAPI
    let listing: ListingSummary

    var body: some View {
        ListingRowContent(listing: listing) {
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

/// The desktop row: cover, name, colored lifecycle word, then where it is live.
struct ListingRowContent<Thumbnail: View>: View {
    let listing: ListingSummary
    let thumbnail: Thumbnail

    init(listing: ListingSummary, @ViewBuilder thumbnail: () -> Thumbnail) {
        self.listing = listing
        self.thumbnail = thumbnail()
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
                Text(listing.displayTitle).font(.subheadline.weight(.medium)).lineLimit(2)
                HStack(spacing: 6) {
                    Text(listing.statusLabel).fontWeight(.medium).foregroundStyle(statusColor)
                    if let price = listing.price {
                        Text(price, format: .currency(code: "USD")).foregroundStyle(.secondary)
                    }
                    if let sku = listing.sku, !sku.isEmpty {
                        Text(sku).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                .font(.caption)
                let live = listing.liveMarketplaces
                if !live.isEmpty {
                    Text(live.map(Marketplaces.name).joined(separator: " · "))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .accessibilityLabel("Listed on \(live.map(Marketplaces.name).joined(separator: ", "))")
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }

    private var statusColor: Color {
        switch listing.inventoryStatus {
        case "draft": .orange
        case "in_progress": .teal
        case "listing": .purple
        case "active": .green
        case "sold": WorkspaceStyle.accent
        case "failed": .red
        default: .secondary
        }
    }
}

private enum BulkAction: Identifiable {
    case edit([String]), delete([String]), copy([String]), markdown([String])

    var id: String {
        switch self {
        case .edit(let ids): "edit-" + ids.joined(separator: ",")
        case .delete(let ids): "delete-" + ids.joined(separator: ",")
        case .copy(let ids): "copy-" + ids.joined(separator: ",")
        case .markdown(let ids): "markdown-" + ids.joined(separator: ",")
        }
    }
}
