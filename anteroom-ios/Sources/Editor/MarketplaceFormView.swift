import PhotosUI
import SwiftUI

/// A marketplace tab: the server's native form for that marketplace, with its
/// sections, labels, order and side-by-side rows. Edits save with the
/// listing's Save button. Nothing here lists, updates or publishes anything.
struct MarketplaceFormView: View {
    @Bindable var model: ListingEditorModel
    let marketplace: String
    /// The seller's open or closed choice for "Show more" and "See … options", per section.
    @State private var revealOverride: [String: Bool] = [:]

    var body: some View {
        Group {
            if let loaded = model.forms[marketplace] {
                let form = presented(loaded)
                if marketplace == "ebay" {
                    ForEach(EbayFormRules.notes(varied: ebayVaried(form)), id: \.self) { note in
                        Text(note).font(.footnote).foregroundStyle(.secondary)
                    }
                }
                ForEach(sections(form), id: \.id) { section in
                    let links = NativeReveal.links(section.fields)
                    ListingInspectorSection(section.title) {
                        // "See … options" sits at the top of eBay's section; "Show more" sits under the fields.
                        ForEach(links.top, id: \.self) { revealLink($0, section: section) }
                        ForEach(rows(section.fields.filter { revealed($0, section: section) }), id: \.first!.key) { row in
                            if row.count == 2 {
                                HStack(alignment: .top, spacing: 12) {
                                    NativeFieldControl(model: model, marketplace: marketplace, field: row[0], form: form)
                                    NativeFieldControl(model: model, marketplace: marketplace, field: row[1], form: form)
                                }
                            } else {
                                NativeFieldControl(model: model, marketplace: marketplace, field: row[0], form: form)
                            }
                        }
                        ForEach(links.bottom, id: \.self) { revealLink($0, section: section) }
                    }
                }
                if model.categoryChanged(marketplace) {
                    Text("Save to load the fields for this category.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            } else if let error = model.formErrors[marketplace] {
                WorkspaceSection("Couldn't load the \(Marketplaces.name(marketplace)) form") {
                    Text(error).foregroundStyle(.red)
                    Button("Try again") { Task { await model.loadForm(marketplace) } }
                }
            } else {
                ProgressView("Loading \(Marketplaces.name(marketplace))…").frame(maxWidth: .infinity, minHeight: 120)
            }
        }
        .task(id: "\(marketplace)-\(model.forms[marketplace] == nil)") {
            if model.forms[marketplace] == nil { await model.loadForm(marketplace) }
        }
    }

    private struct FormSection {
        let id: String
        let title: String
        let fields: [NativeField]
    }

    /// Consecutive fields under one heading, in the form's own order.
    private func sections(_ form: NativeForm) -> [FormSection] {
        var result: [FormSection] = []
        for field in form.fields where visible(field, form) {
            let title = field.section ?? ""
            if let last = result.last, last.title == title {
                result[result.count - 1] = FormSection(id: last.id, title: title, fields: last.fields + [field])
            } else {
                result.append(FormSection(id: "\(result.count)-\(title)", title: title, fields: [field]))
            }
        }
        return result
    }

    /// Two short fields in a row sit side by side, as on the page. A phone has
    /// room for two, so quarter-width fields pair like half-width ones.
    private func rows(_ fields: [NativeField]) -> [[NativeField]] {
        var result: [[NativeField]] = []
        var index = 0
        while index < fields.count {
            if fields[index].half || fields[index].quarter, index + 1 < fields.count,
               fields[index + 1].half || fields[index + 1].quarter {
                result.append([fields[index], fields[index + 1]])
                index += 2
            } else {
                result.append([fields[index]])
                index += 1
            }
        }
        return result
    }

    /// Rows behind a link show once it is open; a saved value opens it.
    private func revealed(_ field: NativeField, section: FormSection) -> Bool {
        guard let reveal = field.reveal else { return true }
        return revealOpen(reveal, section: section)
    }

    private func revealOpen(_ reveal: String, section: FormSection) -> Bool {
        if let choice = revealOverride["\(section.title)|\(reveal)"] { return choice }
        return section.fields.contains { $0.reveal == reveal && NativeReveal.opens(model.value($0)) }
    }

    private func revealLink(_ reveal: String, section: FormSection) -> some View {
        let open = revealOpen(reveal, section: section)
        return Button(NativeReveal.title(reveal, open: open)) {
            revealOverride["\(section.title)|\(reveal)"] = !open
        }
        .font(.subheadline)
        .frame(minHeight: 44)
        .accessibilityValue(open ? "Expanded" : "Collapsed")
    }

    /// eBay's format, duration and quantity follow the current format and variations.
    private func presented(_ form: NativeForm) -> NativeForm {
        guard marketplace == "ebay" else { return form }
        let format = shown("ebay_specifics.listingFormat", form) == "AUCTION" ? "AUCTION" : "FIXED_PRICE"
        let varied = ebayVaried(form)
        return NativeForm(marketplace: form.marketplace, categoryID: form.categoryID,
                          fields: form.fields.map { EbayFormRules.present($0, format: format, varied: varied) })
    }

    private func ebayVaried(_ form: NativeForm) -> Bool {
        let field = form.fields.first { $0.key == "ebay_specifics.variations" }
        let value = field.map { model.value($0) } ?? model.current(at: "ebay_specifics.variations")
        return !EbayVariations(value).rows.isEmpty
    }

    /// The desktop's reveal rules (nativeFormStability.ts).
    private func visible(_ field: NativeField, _ form: NativeForm) -> Bool {
        if field.categorySpecific && model.categoryChanged(marketplace) { return false }
        if marketplace == "ebay" {
            return EbayFormRules.visible(field.key, shown: { shown($0, form) }, varied: ebayVaried(form))
        }
        switch field.key {
        case "depop_specifics.worldwideShipping.price":
            return shown("depop_specifics.worldwideShipping.enabled", form) == "true"
        case "poshmark_specifics.smartSell.minimumPrice":
            return shown("poshmark_specifics.smartSell.enabled", form) == "true"
        case "poshmark_specifics.makeAvailableAt":
            return shown("poshmark_specifics.availability", form) == "coming_soon"
        case "mercari_specifics.smartPricing.minimumPrice":
            return shown("mercari_specifics.smartPricing.enabled", form) == "true"
        default:
            break
        }
        // Mercari shows the label, its package questions and the free-shipping
        // choice only with Prepaid label; the sides only for a package that does
        // not fit a shoebox, unless the seller skips them.
        if marketplace == "mercari" && (["mercari_specifics.shippingClassId", "mercari_specifics.shippingPayerId"].contains(field.key)
                                        || field.key.hasPrefix("mercari_specifics.package.")) {
            if shown("mercari_specifics.shippingMethod", form) == "SOYO" { return false }
            let notShoebox = shown("mercari_specifics.package.fitsShoebox", form) == "no"
            if field.key == "mercari_specifics.package.skipDimensions" { return notShoebox }
            if ["mercari_specifics.package.length", "mercari_specifics.package.width", "mercari_specifics.package.height"].contains(field.key) {
                return notShoebox && shown("mercari_specifics.package.skipDimensions", form) != "true"
            }
        }
        return true
    }

    private func shown(_ key: String, _ form: NativeForm) -> String {
        guard let field = form.fields.first(where: { $0.key == key }) else { return "" }
        let text = model.value(field)?.text ?? ""
        return field.stored(for: text)
    }
}

/// One native field, drawn by its input type.
private struct NativeFieldControl: View {
    @Bindable var model: ListingEditorModel
    let marketplace: String
    let field: NativeField
    let form: NativeForm
    @State private var picking = false
    @State private var editingDate = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            control
            if let help = field.help, !help.isEmpty, field.inputType != "note" {
                Text(help).font(.footnote).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var title: String { field.required ? "\(field.label) *" : field.label }
    private var current: JSONValue? { model.value(field) }

    @ViewBuilder
    private var control: some View {
        switch field.inputType {
        case "note":
            VStack(alignment: .leading, spacing: 4) {
                Text(field.label).font(.subheadline.weight(.medium))
                if let help = field.help { Text(help).font(.footnote).foregroundStyle(.secondary) }
            }
        case "photos":
            MarketplacePhotoPicker(model: model, marketplace: marketplace, field: field)
        case "inventory":
            WorkspaceField(title) {
                NativeInventoryEditor(model: model, field: field, form: form)
            }
        case "boolean", "badge":
            Toggle(isOn: Binding(get: { current == .bool(true) || current?.text == "true" },
                                 set: { model.setValue(.bool($0), for: field) })) {
                Text(field.label).font(.subheadline)
            }
            .frame(minHeight: 44)
        case "segmented":
            WorkspaceField(title) {
                Picker(field.label, selection: Binding(get: { field.label(for: current?.text ?? "") },
                                                       set: { model.setValue(.string(field.stored(for: $0)), for: field) })) {
                    ForEach(field.options, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented)
            }
        case "datetime-local":
            dateControl
        case "variations":
            VStack(alignment: .leading, spacing: 8) {
                Text(title).font(.footnote.weight(.medium)).foregroundStyle(.secondary)
                EbayVariationsEditor(model: model, field: field)
            }
        default:
            if marketplace == "ebay", let pathKey = EbayFormRules.categoryPathKeys[field.key], !field.locked {
                EbayCategoryControl(model: model, field: field, title: title, pathKey: pathKey)
            } else if marketplace == "poshmark" && field.key == "poshmark_specifics.earnings" {
                readOnly(earnings)
            } else if field.locked {
                readOnly(current?.text ?? "")
            } else if field.multi {
                multiControl
            } else if field.selectionOnly || field.inputType == "cascade" {
                selectControl
            } else if field.inputType == "number" {
                WorkspaceField(title) {
                    TextField(field.placeholder ?? "0", text: Binding(get: { current?.text ?? "" }, set: { setNumber($0) }))
                        // A whole-number field ("Quantity in lot") gets the number pad.
                        .keyboardType(field.step == 1 ? .numberPad : .decimalPad)
                }
            } else {
                WorkspaceField(title) {
                    TextField(field.placeholder ?? field.label, text: Binding(get: { current?.text ?? "" },
                                                                             set: { setText($0) }),
                              axis: .vertical)
                        .lineLimit(field.inputType == "textarea" || field.key.hasSuffix("description") ? 4...14 : 1...4)
                }
                if let max = field.maxLength, (current?.text.count ?? 0) > max * 9 / 10 {
                    Text("\(current?.text.count ?? 0)/\(max)").font(.caption).monospacedDigit()
                        .foregroundStyle((current?.text.count ?? 0) > max ? .red : .secondary)
                }
            }
        }
    }

    private func readOnly(_ text: String) -> some View {
        WorkspaceField(field.label) {
            Text(text.isEmpty ? "—" : text).foregroundStyle(.secondary)
        }
    }

    private var selectControl: some View {
        let shown = field.label(for: current?.text ?? "")
        return WorkspaceField(title) {
            Button { picking = true } label: {
                HStack {
                    Text(shown.isEmpty ? "Select" : shown)
                        .foregroundStyle(shown.isEmpty ? .secondary : .primary)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.up.chevron.down").font(.caption).foregroundStyle(.secondary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityValue(shown.isEmpty ? "Not set" : shown)
        }
        .sheet(isPresented: $picking) {
            OptionPickerSheet(title: field.label, value: Binding(
                get: { shown },
                set: { model.setValue(.string($0.isEmpty ? "" : field.stored(for: $0)), for: field) }
            ), options: field.options)
        }
    }

    private var multiControl: some View {
        let values: [String] = {
            if case .array(let items)? = current { return items.map { field.label(for: $0.text) } }
            return []
        }()
        return WorkspaceField(title) {
            Button { picking = true } label: {
                HStack {
                    Text(values.isEmpty ? "Select" : values.joined(separator: ", "))
                        .foregroundStyle(values.isEmpty ? .secondary : .primary)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.up.chevron.down").font(.caption).foregroundStyle(.secondary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityValue(values.isEmpty ? "None" : values.joined(separator: ", "))
        }
        .sheet(isPresented: $picking) {
            MultiOptionSheet(title: field.label, selection: values, options: field.options,
                             allowsCustom: !field.selectionOnly, maxValues: field.maxValues) { chosen in
                model.setValue(.array(chosen.map { .string(field.stored(for: $0)) }), for: field)
            }
        }
    }

    private var dateControl: some View {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm"
        let text = current?.text ?? ""
        let date = formatter.date(from: text)
        return WorkspaceField(title) {
            if let date {
                HStack {
                    DatePicker(field.label, selection: Binding(get: { date }, set: {
                        model.setValue(.string(formatter.string(from: $0)), for: field)
                    }), in: Date()...Date().addingTimeInterval(7 * 86_400))
                    .labelsHidden()
                    Spacer()
                    Button("Clear") { model.setValue(.string(""), for: field) }
                }
            } else {
                Button("Set \(field.label.lowercased())") {
                    model.setValue(.string(formatter.string(from: Date().addingTimeInterval(3600))), for: field)
                }
            }
        }
    }

    private var earnings: String {
        let priceField = form.fields.first { $0.key == "poshmark_specifics.poshmarkPrice" || $0.key == "price" }
        let price = priceField.flatMap { model.value($0)?.number }
        let shipping = form.fields.first { $0.key == "poshmark_specifics.shippingDiscountId" }
        let discountID = shipping.map { $0.stored(for: model.value($0)?.text ?? "") } ?? ""
        return poshmarkEarnings(price: price, discount: shipping?.optionAmounts[discountID] ?? 0)
    }

    private func setText(_ text: String) {
        model.setValue(.string(text), for: field)
    }

    /// Numbers save as numbers, as the desktop's editor coerces them.
    private func setNumber(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty {
            model.setValue(.string(""), for: field)
        } else if let whole = Int(trimmed) {
            model.setValue(.int(whole), for: field)
        } else if let number = Double(trimmed) {
            model.setValue(.double(number), for: field)
        } else {
            model.setValue(.string(trimmed), for: field)
        }
    }
}

/// Which photos this marketplace sends, in order, like Vendoo's Photo
/// Manager (services/marketplace_media.py). Without a pick the form sends the
/// item's photos in item order, up to the marketplace's limit.
private struct MarketplacePhotoPicker: View {
    @Bindable var model: ListingEditorModel
    let marketplace: String
    let field: NativeField
    @State private var photos: [ListingPhoto] = []
    @State private var itemPhotoIDs: [String] = []
    @State private var picked: [PhotosPickerItem] = []
    @State private var uploading = false
    @State private var uploadError: String?

    private static let limits = ["ebay": 24, "etsy": 20, "poshmark": 16, "mercari": 12, "depop": 8, "vinted": 20, "sellwild": 20]
    private var limit: Int { Self.limits[marketplace] ?? 20 }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if photos.isEmpty {
                Text("Add photos in General first.").font(.footnote).foregroundStyle(.secondary)
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 72), spacing: 8)], spacing: 8) {
                    ForEach(photos) { photo in
                        let position = sent.firstIndex(of: photo.id)
                        Button { toggle(photo.id) } label: {
                            RemotePhoto(api: model.api, photoID: photo.id, maxPixels: 200, version: photo.version)
                                .frame(height: 80)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                                .opacity(position == nil ? 0.35 : 1)
                                .overlay(alignment: .topLeading) {
                                    if let position {
                                        Text(String(format: "%02d", position + 1))
                                            .font(.caption2.weight(.bold)).monospacedDigit()
                                            .padding(4)
                                            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 4))
                                            .padding(4)
                                    }
                                }
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(position.map { "Photo, sent \($0 + 1)" } ?? "Photo, not sent")
                        .accessibilityHint("Double-tap to \(position == nil ? "send" : "leave out") this photo")
                    }
                }
                if model.isDraft {
                    let title = uploading ? "Adding…" : "Add a photo for \(Marketplaces.name(marketplace)) only"
                    PhotosPicker(selection: $picked, maxSelectionCount: 4, matching: .images) {
                        Label(title, systemImage: "plus").font(.footnote)
                    }
                    .disabled(uploading)
                }
                if let uploadError { Text(uploadError).font(.footnote).foregroundStyle(.red) }
                HStack {
                    Text("\(sent.count) of \(limit) photos sent")
                        .font(.footnote).foregroundStyle(sent.count > limit ? .red : .secondary)
                    Spacer()
                    if !pick.isEmpty {
                        Button("Use item photos") { model.setValue(.array([]), for: field) }
                            .font(.footnote)
                    }
                }
            }
        }
        .task { await load() }
        .onChange(of: picked) { _, items in
            guard !items.isEmpty else { return }
            picked = []
            Task { await upload(items) }
        }
    }

    /// The seller's own pick, or empty when the item's photos are used.
    private var pick: [String] {
        if case .array(let items)? = model.value(field) {
            let ids = items.map(\.text).filter { id in photos.contains { $0.id == id } }
            return ids
        }
        return []
    }

    /// Without a pick, the form sends the item's photos only, in item order.
    private var sent: [String] {
        Array((pick.isEmpty ? itemPhotoIDs : pick).prefix(limit))
    }

    private func load() async {
        let item = (try? await model.api.photos(listingID: model.listingID)) ?? []
        let own = (try? await model.api.photos(listingID: model.listingID, marketplace: marketplace)) ?? []
        itemPhotoIDs = item.map(\.id)
        photos = item + own
    }

    /// Photos added here belong to this form only and are ticked to send.
    private func upload(_ items: [PhotosPickerItem]) async {
        uploading = true
        defer { uploading = false }
        var next = pick.isEmpty ? Array(itemPhotoIDs.prefix(limit)) : pick
        for item in items {
            guard let data = try? await item.loadTransferable(type: Data.self),
                  let jpeg = await Task.detached(priority: .userInitiated, operation: { PhotoPreparer.jpeg(from: data) }).value else {
                uploadError = "Couldn't read one of the photos."
                continue
            }
            do {
                let photo = try await model.api.uploadPhoto(listingID: model.listingID, jpeg: jpeg, marketplace: marketplace)
                photos.append(photo)
                next.append(photo.id)
                uploadError = nil
            } catch {
                uploadError = error.localizedDescription
            }
        }
        model.setValue(.array(next.map(JSONValue.string)), for: field)
    }

    private func toggle(_ id: String) {
        var next = pick.isEmpty ? Array(itemPhotoIDs.prefix(limit)) : pick
        if let index = next.firstIndex(of: id) { next.remove(at: index) } else { next.append(id) }
        model.setValue(.array(next.map(JSONValue.string)), for: field)
    }
}

/// Pick several values, up to the field's limit; custom entries where allowed.
struct MultiOptionSheet: View {
    let title: String
    @State var selection: [String]
    let options: [String]
    let allowsCustom: Bool
    let maxValues: Int?
    let onDone: ([String]) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var search = ""

    init(title: String, selection: [String], options: [String], allowsCustom: Bool, maxValues: Int?,
         onDone: @escaping ([String]) -> Void) {
        self.title = title
        _selection = State(initialValue: selection)
        self.options = options
        self.allowsCustom = allowsCustom
        self.maxValues = maxValues
        self.onDone = onDone
    }

    var body: some View {
        NavigationStack {
            List {
                if allowsCustom, !needle.isEmpty, !all.contains(where: { $0.caseInsensitiveCompare(needle) == .orderedSame }) {
                    Button("Add \"\(needle)\"") {
                        toggle(needle)
                        search = ""
                    }
                    .disabled(full)
                }
                ForEach(filtered, id: \.self) { option in
                    Button { toggle(option) } label: {
                        HStack {
                            Text(option).foregroundStyle(.primary)
                            Spacer()
                            if selection.contains(option) {
                                Image(systemName: "checkmark").foregroundStyle(WorkspaceStyle.accent)
                            }
                        }
                        .frame(minHeight: 32)
                        .contentShape(Rectangle())
                    }
                    .disabled(full && !selection.contains(option))
                    .accessibilityAddTraits(selection.contains(option) ? .isSelected : [])
                }
            }
            .searchable(text: $search, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search")
            .navigationTitle(maxValues.map { "\(title) (\(selection.count)/\($0))" } ?? title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        onDone(selection)
                        dismiss()
                    }.fontWeight(.semibold)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private var needle: String { search.trimmingCharacters(in: .whitespaces) }
    private var full: Bool { maxValues.map { selection.count >= $0 } ?? false }
    /// Saved custom values stay listed so they can be removed.
    private var all: [String] { options + selection.filter { !options.contains($0) } }
    private var filtered: [String] {
        needle.isEmpty ? all : all.filter { $0.localizedCaseInsensitiveContains(needle) }
    }

    private func toggle(_ option: String) {
        if let index = selection.firstIndex(of: option) {
            selection.remove(at: index)
        } else if !full {
            selection.append(option)
        }
    }
}

/// Poshmark's size picker (NativeInventoryEditor.tsx): one size for Single
/// Item, a size and quantity per row for Multi Item, and the US/AU/EU/UK switch.
struct NativeInventoryEditor: View {
    @Bindable var model: ListingEditorModel
    let field: NativeField
    let form: NativeForm
    @State private var showSKUs = false

    private static let systems = ["us", "au", "eu", "uk"]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if groups.contains(where: { !($0.systems ?? [:]).isEmpty }) {
                Picker("Sizing", selection: Binding(get: { system }, set: { switchSystem(to: $0) })) {
                    ForEach(Self.systems, id: \.self) { Text($0.uppercased()).tag($0) }
                }
                .pickerStyle(.segmented)
            }
            if groups.isEmpty {
                Text("Choose a category, then Save, to see its sizes.").font(.footnote).foregroundStyle(.secondary)
            } else {
                ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                    rowEditor(row, index: index)
                }
                if multi {
                    HStack {
                        Button("Add another size", systemImage: "plus") {
                            commit(rows + [InventoryRow(sizeSetID: rows.first?.sizeSetID ?? groups.first?.id ?? "")])
                        }
                        Spacer()
                        Toggle("Item SKUs", isOn: $showSKUs).fixedSize()
                    }
                    .font(.footnote)
                }
            }
        }
        .onAppear { showSKUs = rows.contains { !$0.sku.isEmpty } }
    }

    private var groups: [InventoryGroup] { field.inventoryGroups }

    private var system: String {
        let chosen = model.current(at: "poshmark_specifics.sizeSystem")?.text ?? ""
        return Self.systems.contains(chosen) ? chosen : (field.inventorySystem ?? "us")
    }

    /// Multi Item keeps a quantity per size; Single Item keeps one size at quantity 1.
    private var multi: Bool {
        guard let multiField = form.fields.first(where: { $0.key == "poshmark_specifics.multiItem" }) else { return true }
        return multiField.stored(for: model.value(multiField)?.text ?? "") == "true"
    }

    private var rows: [InventoryRow] {
        var stored: [InventoryRow] = []
        if case .array(let items)? = model.value(field) { stored = items.compactMap(InventoryRow.init) }
        let shown = multi ? stored : Array(stored.prefix(1))
        return shown.isEmpty ? [InventoryRow(sizeSetID: groups.first?.id ?? "")] : shown
    }

    @ViewBuilder
    private func rowEditor(_ row: InventoryRow, index: Int) -> some View {
        let group = groups.first { $0.id == row.sizeSetID } ?? groups.first { $0.label == row.sizeSetID }
        let sizes = group?.sizes(in: system) ?? []
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Menu {
                    ForEach(groups, id: \.id) { choice in
                        Button(choice.label) { update(index) { $0.sizeSetID = choice.id; $0.sizeID = "" } }
                    }
                } label: {
                    menuLabel(group?.label ?? "Size group")
                }
                .accessibilityLabel("Size group \(index + 1), \(group?.label ?? "not set")")
                Menu {
                    ForEach(sizes, id: \.id) { size in
                        Button(size.label) { update(index) { $0.sizeID = size.id } }
                    }
                } label: {
                    menuLabel(sizes.first { $0.id == row.sizeID }?.label ?? (row.sizeID.isEmpty ? "Size" : row.sizeID))
                }
                .disabled(group == nil)
                .accessibilityLabel("Size \(index + 1), \(row.sizeID.isEmpty ? "not set" : row.sizeID)")
            }
            if multi {
                HStack(spacing: 8) {
                    TextField("Quantity", value: Binding(get: { row.quantity }, set: { value in
                        update(index) { $0.quantity = max(1, value) }
                    }), format: .number)
                    .keyboardType(.numberPad)
                    .accessibilityLabel("Quantity \(index + 1)")
                    if showSKUs {
                        TextField("Item SKU", text: Binding(get: { row.sku }, set: { value in
                            update(index) { $0.sku = String(value.prefix(50)) }
                        }))
                        .accessibilityLabel("SKU \(index + 1)")
                    }
                    Button("Remove size", systemImage: "minus.circle", role: .destructive) {
                        commit(rows.enumerated().filter { $0.offset != index }.map(\.element))
                    }
                    .labelStyle(.iconOnly)
                    .disabled(rows.count < 2)
                    .frame(minWidth: 44, minHeight: 44)
                }
            }
        }
    }

    private func menuLabel(_ text: String) -> some View {
        HStack {
            Text(text).lineLimit(1)
            Spacer(minLength: 4)
            Image(systemName: "chevron.up.chevron.down").font(.caption2).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 10)
        .frame(maxWidth: .infinity, minHeight: 44)
        .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(WorkspaceStyle.border, lineWidth: 1))
    }

    private func update(_ index: Int, _ change: (inout InventoryRow) -> Void) {
        var next = rows
        guard next.indices.contains(index) else { return }
        change(&next[index])
        commit(next)
    }

    private func commit(_ next: [InventoryRow]) {
        let saved = multi ? next : [next.first.map { row in var single = row; single.quantity = 1; return single } ?? InventoryRow()]
        model.setValue(.array(saved.map(\.json)), for: field)
    }

    /// The same sizes in another system, by Poshmark's equivalents.
    private func switchSystem(to next: String) {
        let remapped = rows.map { row -> InventoryRow in
            guard row.sizeSetID != "custom", next != system else { return row }
            let group = groups.first { $0.id == row.sizeSetID }
            let currentSize = group?.sizes(in: system).first { $0.id == row.sizeID }
            var mapped = row
            mapped.sizeID = currentSize?.equivalents?[next] ?? ""
            return mapped
        }
        model.setValue(.string(next), at: "poshmark_specifics.sizeSystem")
        commit(remapped)
    }
}
