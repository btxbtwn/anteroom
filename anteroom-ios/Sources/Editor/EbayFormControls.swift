import SwiftUI

/// eBay's category tree, read once per session: it is about 4 MB.
@MainActor
final class EbayCategoryCache {
    static let shared = EbayCategoryCache()
    private var loaded: CatalogCategories?
    private var loading: Task<CatalogCategories, Error>?

    func categories(api: AnteroomAPI) async throws -> CatalogCategories {
        if let loaded { return loaded }
        let task = loading ?? Task { try await api.catalogCategories(marketplace: "ebay") }
        loading = task
        do {
            let value = try await task.value
            loaded = value
            return value
        } catch {
            loading = nil
            throw error
        }
    }
}

/// eBay's category control (EbayCategorySelect): stores the category id, with
/// its breadcrumb beside it. Picking one empties the fields that depend on it,
/// and its own fields load after Save.
struct EbayCategoryControl: View {
    @Bindable var model: ListingEditorModel
    let field: NativeField
    let title: String
    let pathKey: String
    @State private var picking = false

    /// The breadcrumb the seller picked, else the form's own.
    private var shown: String {
        if let path = model.current(at: pathKey)?.text, !path.isEmpty { return path }
        return field.value?.text ?? ""
    }

    var body: some View {
        WorkspaceField(title) {
            Button { picking = true } label: {
                HStack {
                    Text(shown.isEmpty ? "Select" : shown)
                        .foregroundStyle(shown.isEmpty ? .secondary : .primary)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.right").font(.caption).foregroundStyle(.secondary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityValue(shown.isEmpty ? "Not set" : shown)
        }
        .sheet(isPresented: $picking) {
            EbayCategorySheet(api: model.api, title: field.label, current: shown) { leaf in
                model.setValue(.string(leaf.id), for: field)
                model.setValue(.string(CategoryBrowse.breadcrumb(leaf.path)), at: pathKey)
            }
        }
    }
}

/// Browse eBay's categories one level at a time, or search them. While the
/// synced tree is incomplete, eBay's own search fills the gaps.
private struct EbayCategorySheet: View {
    let api: AnteroomAPI
    let title: String
    let current: String
    let onPick: (CategoryLeaf) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var tree: CatalogCategories?
    @State private var failure: String?
    @State private var query = ""
    @State private var remote: [CategoryLeaf] = []
    @State private var remoteStatus = ""

    var body: some View {
        NavigationStack {
            Group {
                if let tree {
                    if query.trimmingCharacters(in: .whitespaces).isEmpty {
                        if tree.leaves.isEmpty {
                            ContentUnavailableView("Search eBay categories", systemImage: "magnifyingglass",
                                                   description: Text("Type at least 3 characters to search eBay categories."))
                        } else {
                            CategoryLevel(prefix: [], leaves: tree.leaves, current: current, pick: pick, close: nil)
                        }
                    } else {
                        searchResults(tree)
                    }
                } else if let failure {
                    ContentUnavailableView("Couldn't load categories", systemImage: "exclamationmark.triangle",
                                           description: Text(failure))
                } else {
                    ProgressView("Loading categories…")
                }
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $query, prompt: "Search categories")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            }
            .navigationDestination(for: CategoryPrefix.self) { level in
                // Each level keeps Cancel, so the sheet closes from any depth.
                if let tree { CategoryLevel(prefix: level.parts, leaves: tree.leaves, current: current, pick: pick, close: { dismiss() }) }
            }
        }
        .task {
            do { tree = try await EbayCategoryCache.shared.categories(api: api) } catch { failure = error.localizedDescription }
        }
        .task(id: query) { await searchEbay() }
    }

    private func searchResults(_ tree: CatalogCategories) -> some View {
        let local = CategoryBrowse.search(query, in: tree.leaves)
        let known = Set(local.map(\.id))
        let rows = local + remote.filter { !known.contains($0.id) }
        return List {
            ForEach(rows) { leaf in
                Button { pick(leaf) } label: {
                    Text(CategoryBrowse.breadcrumb(leaf.path)).foregroundStyle(.primary)
                }
            }
            if tree.needsSearch {
                Section { Text(remoteStatus).font(.footnote).foregroundStyle(.secondary) } header: {
                    if !tree.leaves.isEmpty { Text("Search if the category is not in the list yet.") }
                }
            } else if rows.isEmpty {
                Text("No matching categories").foregroundStyle(.secondary)
            }
        }
    }

    /// eBay's search, only while the synced tree is incomplete; three characters or more.
    private func searchEbay() async {
        guard tree?.needsSearch == true else { return }
        let words = query.trimmingCharacters(in: .whitespaces)
        guard words.count >= 3 else {
            remote = []
            remoteStatus = "Type at least 3 characters to search eBay categories."
            return
        }
        try? await Task.sleep(for: .milliseconds(300))
        guard !Task.isCancelled else { return }
        remoteStatus = "Searching eBay categories…"
        do {
            remote = try await api.searchEbayCategories(words)
            remoteStatus = "\(remote.count) eBay categories found."
        } catch is CancellationError {
        } catch {
            remote = []
            remoteStatus = "eBay category search failed. Check its connection and try again."
        }
    }

    private func pick(_ leaf: CategoryLeaf) {
        onPick(leaf)
        dismiss()
    }
}

private struct CategoryPrefix: Hashable { let parts: [String] }

private struct CategoryLevel: View {
    let prefix: [String]
    let leaves: [CategoryLeaf]
    let current: String
    let pick: (CategoryLeaf) -> Void
    /// Set on pushed levels; the first level shares the sheet's own Cancel.
    let close: (() -> Void)?

    var body: some View {
        let selected = current.components(separatedBy: " › ")
        List(CategoryBrowse.children(of: prefix, in: leaves), id: \.label) { child in
            if let leaf = child.leaf {
                Button { pick(leaf) } label: {
                    HStack {
                        Text(child.label).foregroundStyle(.primary)
                        Spacer()
                        if selected == prefix + [child.label] {
                            Image(systemName: "checkmark").foregroundStyle(WorkspaceStyle.accent)
                        }
                    }
                }
            } else {
                NavigationLink(child.label, value: CategoryPrefix(parts: prefix + [child.label]))
            }
        }
        .navigationTitle(prefix.last ?? "Categories")
        .toolbar {
            if let close {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel", action: close) }
            }
        }
    }
}

/// eBay variations (EbayVariationsEditor.tsx): what they differ by, one row
/// each with quantity, price and SKU, and photos per value of one aspect.
/// The listing's quantity follows the total.
struct EbayVariationsEditor: View {
    @Bindable var model: ListingEditorModel
    let field: NativeField
    @State private var photos: [ListingPhoto] = []
    @State private var itemPhotoIDs: [String] = []
    @State private var pickingAspects = false

    private var value: EbayVariations { EbayVariations(model.value(field)) }

    var body: some View {
        let draft = value
        VStack(alignment: .leading, spacing: 12) {
            Button { pickingAspects = true } label: {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Variations differ by").font(.footnote).foregroundStyle(.secondary)
                        Text(draft.aspects.isEmpty ? "Select" : draft.aspects.joined(separator: ", "))
                            .foregroundStyle(draft.aspects.isEmpty ? .secondary : .primary)
                    }
                    Spacer()
                    Image(systemName: "chevron.up.chevron.down").font(.caption).foregroundStyle(.secondary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .frame(minHeight: 44)
            if field.variationAspects.isEmpty {
                Text("eBay lists no aspects that can vary in this category.").font(.footnote).foregroundStyle(.secondary)
            }
            if !draft.aspects.isEmpty {
                ForEach(draft.rows.indices, id: \.self) { index in
                    rowEditor(draft, index: index)
                }
                HStack {
                    Button("Add variation", systemImage: "plus") {
                        var next = draft
                        next.rows.append(EbayVariations.Row(aspects: draft.aspects))
                        commit(next)
                    }
                    .buttonStyle(.bordered)
                    Spacer()
                    Text("\(draft.rows.count) variations · \(draft.total) in stock")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                if !draft.photoValues.isEmpty { photoSection(draft) }
            }
        }
        .sheet(isPresented: $pickingAspects) {
            MultiOptionSheet(title: "Variations differ by", selection: draft.aspects,
                             options: field.variationAspects.map(\.name), allowsCustom: false,
                             maxValues: EbayVariations.maxAspects) { names in
                commit(value.withAspects(names))
            }
        }
        .task { await loadPhotos() }
    }

    private func rowEditor(_ draft: EbayVariations, index: Int) -> some View {
        let row = draft.rows[index]
        return VStack(alignment: .leading, spacing: 10) {
            Text("Variation \(index + 1)").font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
            ForEach(draft.aspects, id: \.self) { name in
                let aspect = field.variationAspects.first { $0.name == name }
                AspectValueControl(name: name, number: index + 1, aspect: aspect, value: row.values[name] ?? "") { text in
                    update(index) { $0.values[name] = text }
                }
            }
            HStack(spacing: 12) {
                labeled("Quantity") {
                    TextField("0", text: Binding(get: { row.quantity.text }, set: { text in
                        update(index) { $0.quantity = Int(text).map(JSONValue.int) ?? .string(text) }
                    }))
                    .keyboardType(.numberPad)
                    .accessibilityLabel("Variation quantity \(index + 1)")
                }
                labeled("Price (optional)") {
                    TextField("Listing price", text: Binding(get: { row.price.text }, set: { text in
                        update(index) { $0.price = .string(text) }
                    }))
                    .keyboardType(.decimalPad)
                    .accessibilityLabel("Variation price \(index + 1)")
                }
            }
            labeled("SKU (optional)") {
                TextField("Made from the item SKU", text: Binding(get: { row.sku }, set: { text in
                    update(index) { $0.sku = String(text.prefix(50)) }
                }))
                .textInputAutocapitalization(.characters)
                .accessibilityLabel("Variation SKU \(index + 1)")
            }
            Button("Remove", role: .destructive) {
                var next = value
                next.rows.remove(at: index)
                commit(next)
            }
            .accessibilityLabel("Remove variation \(index + 1)")
        }
        .padding(12)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(WorkspaceStyle.border, lineWidth: 1))
    }

    private func photoSection(_ draft: EbayVariations) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Picker("Photos differ by", selection: Binding(get: { draft.photoAspect }, set: { name in
                var next = value
                if name != next.photoAspect { next.photos = [:] }
                next.photosBy = name
                commit(next)
            })) {
                ForEach(draft.aspects, id: \.self) { Text($0).tag($0) }
            }
            .pickerStyle(.menu)
            ForEach(draft.photoValues, id: \.self) { text in
                let chosen = draft.photos[text] ?? []
                VStack(alignment: .leading, spacing: 6) {
                    Text("\(text) · \(chosen.count)/\(EbayVariations.maxPhotos)").font(.footnote).foregroundStyle(.secondary)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(sentPhotos) { photo in
                                let on = chosen.contains(photo.id)
                                Button {
                                    var next = value
                                    next.photos[text] = on ? chosen.filter { $0 != photo.id } : chosen + [photo.id]
                                    commit(next)
                                } label: {
                                    RemotePhoto(api: model.api, photoID: photo.id, maxPixels: 120, version: photo.version)
                                        .frame(width: 48, height: 48)
                                        .clipShape(RoundedRectangle(cornerRadius: 6))
                                        .overlay(RoundedRectangle(cornerRadius: 6)
                                            .stroke(on ? WorkspaceStyle.accent : .clear, lineWidth: 2))
                                        .opacity(on ? 1 : 0.6)
                                }
                                .buttonStyle(.plain)
                                .disabled(!on && chosen.count >= EbayVariations.maxPhotos)
                                .accessibilityLabel("\(on ? "Remove" : "Use") photo for \(text)")
                                .accessibilityAddTraits(on ? .isSelected : [])
                            }
                        }
                    }
                }
            }
        }
    }

    private func labeled<Content: View>(_ label: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            content().textFieldStyle(.roundedBorder)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The photos the eBay form sends: the seller's pick, else the item's photos.
    private var sentPhotos: [ListingPhoto] {
        var pick: [String] = []
        if case .array(let items)? = model.current(at: "marketplace_photos.ebay") { pick = items.map(\.text) }
        let ids = (pick.isEmpty ? itemPhotoIDs : pick).prefix(24)
        return ids.compactMap { id in photos.first { $0.id == id } }
    }

    private func loadPhotos() async {
        let item = (try? await model.api.photos(listingID: model.listingID)) ?? []
        let own = (try? await model.api.photos(listingID: model.listingID, marketplace: "ebay")) ?? []
        itemPhotoIDs = item.map(\.id)
        photos = item + own
    }

    private func update(_ index: Int, _ change: (inout EbayVariations.Row) -> Void) {
        var next = value
        guard next.rows.indices.contains(index) else { return }
        change(&next.rows[index])
        commit(next)
    }

    /// The listing's quantity is the stock across its variations.
    private func commit(_ next: EbayVariations) {
        model.setValue(next.json, for: field)
        if !next.rows.isEmpty { model.setValue(.int(next.total), at: "quantity") }
    }
}

/// One aspect's value on a variation: eBay's values when it has them, else typed.
private struct AspectValueControl: View {
    let name: String
    let number: Int
    let aspect: VariationAspect?
    let value: String
    let onChange: (String) -> Void
    @State private var picking = false
    @State private var picked = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(name).font(.caption).foregroundStyle(.secondary)
            if let aspect, !aspect.options.isEmpty {
                Button {
                    picked = value
                    picking = true
                } label: {
                    HStack {
                        Text(value.isEmpty ? "Select" : value).foregroundStyle(value.isEmpty ? .secondary : .primary)
                        Spacer()
                        Image(systemName: "chevron.up.chevron.down").font(.caption).foregroundStyle(.secondary)
                    }
                    .padding(8)
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(WorkspaceStyle.border, lineWidth: 1))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(name) \(number)")
                .accessibilityValue(value.isEmpty ? "Not set" : value)
                .sheet(isPresented: $picking) {
                    OptionPickerSheet(title: name, value: $picked, options: aspect.options)
                }
                .onChange(of: picked) { _, choice in
                    if picking, choice != value { onChange(choice) }
                }
            } else {
                TextField(name, text: Binding(get: { value }, set: { onChange(String($0.prefix(50))) }))
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel("\(name) \(number)")
            }
        }
    }
}
