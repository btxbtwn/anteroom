import SwiftUI

/// Settings → Listings, the rows the hosted workspace offers
/// (ListingsSettings.tsx, InventorySettings.tsx).
struct ListingsSettingsView: View {
    let api: AnteroomAPI

    @State private var marketplaces: MarketplaceSettings?
    @State private var staleDays: Int?
    @State private var dimensions: PackageDimensions?
    @State private var dimensionDraft: [String] = ["", "", ""]
    @State private var zip: String?
    @State private var zipDraft = ""
    @State private var hidden: HiddenFields?
    @State private var formulas: ListingFormulas?
    @State private var titleFormula = ""
    @State private var descriptionFormula = ""
    @State private var saving: String?
    @State private var errors: [String: String] = [:]

    var body: some View {
        Form {
            Section {
                NavigationLink("Saved templates") { SavedTemplatesView(api: api) }
            } footer: {
                Text("Reusable listing fields, saved from a listing's Templates action.")
            }
            whereYouList
            inventory
            shipping
            fields
            listingFormulas
            Section {
                NavigationLink("Similar items") { SimilarItemsView(api: api) }
            } header: {
                Text("Duplicate check")
            } footer: {
                Text("Find items that look like the same thing listed twice, then keep one and merge the other into it, or mark them as different. Nothing is deleted or delisted.")
            }
        }
        .navigationTitle("Listings")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
    }

    // MARK: Sections

    @ViewBuilder
    private var whereYouList: some View {
        Section {
            if let marketplaces {
                let chosen = Set(marketplaces.selected)
                ForEach(marketplaces.available) { item in
                    Toggle(isOn: Binding(get: { chosen.contains(item.id) }, set: { on in
                        var next = chosen
                        if on { next.insert(item.id) } else { next.remove(item.id) }
                        saveMarketplaces(next)
                    })) {
                        HStack {
                            Text(item.label)
                            if item.id == "vinted" {
                                Badge("Direct")
                            } else if !item.fillable {
                                Badge("Not listed from here")
                            }
                        }
                    }
                    .disabled(saving == "marketplaces")
                }
                HStack {
                    Button("All") { saveMarketplaces(Set(marketplaces.available.map(\.id))) }
                        .disabled(saving == "marketplaces" || marketplaces.available.allSatisfy { chosen.contains($0.id) })
                    Spacer()
                    Button("None") { saveMarketplaces([]) }
                        .disabled(saving == "marketplaces" || chosen.isEmpty)
                }
                .buttonStyle(.borderless)
            } else {
                loadingRow("marketplaces", "Loading marketplaces")
            }
            errorRow("marketplaces")
        } header: {
            Text("Where you list")
        } footer: {
            Text("The marketplaces you list on. The status popup and inventory show only the ones you check. Anteroom lists on the ones it supports when you confirm it. Direct means Anteroom lists there when you confirm it; the others are not sent from here.")
        }
    }

    @ViewBuilder
    private var inventory: some View {
        Section {
            if let staleDays {
                Stepper(value: Binding(get: { staleDays }, set: { saveStaleDays($0) }), in: 15...120, step: 15) {
                    LabeledContent("Stale after", value: "\(staleDays) days")
                }
                .disabled(saving == "stale")
            } else {
                loadingRow("stale", "Loading")
            }
            errorRow("stale")
            NavigationLink("Labels") { LabelsSettingsView(api: api) }
        } header: {
            Text("Inventory")
        } footer: {
            Text("Days a listing can stay up unsold before Suggestions and Analytics call it stale, from 15 to 120.")
        }
    }

    @ViewBuilder
    private var shipping: some View {
        Section {
            if dimensions != nil {
                HStack(spacing: 8) {
                    ForEach(0..<3, id: \.self) { index in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(["Length", "Width", "Height"][index] + " (in)").font(.caption).foregroundStyle(.secondary)
                            TextField("0", text: $dimensionDraft[index]).keyboardType(.numberPad)
                                .accessibilityLabel(["Length", "Width", "Height"][index] + " in inches")
                        }
                    }
                }
                Button(saving == "dimensions" ? "Saving…" : "Save dimensions") { saveDimensions() }
                    .disabled(parsedDimensions == nil || parsedDimensions == dimensions || saving == "dimensions")
                if parsedDimensions == nil {
                    Text("Use whole inches from 1 to 120.").font(.footnote).foregroundStyle(.red)
                }
            } else {
                loadingRow("dimensions", "Loading package dimensions")
            }
            errorRow("dimensions")
            if zip != nil {
                LabeledContent("Ships from ZIP") {
                    TextField("ZIP", text: $zipDraft).keyboardType(.numberPad).multilineTextAlignment(.trailing)
                        .accessibilityLabel("Ships from ZIP")
                }
                Button(saving == "zip" ? "Saving…" : "Save ZIP") { saveZip() }
                    .disabled(!zipValid || zipDraft == zip || saving == "zip")
                if !zipValid { Text("A ZIP code is five digits.").font(.footnote).foregroundStyle(.red) }
            } else {
                loadingRow("zip", "Loading ZIP")
            }
            errorRow("zip")
        } header: {
            Text("Shipping defaults")
        } footer: {
            Text("Package dimensions are the L × W × H default on every generated listing; packaged weight is inferred for each item. Mercari and Depop use the ZIP as the ships-from address when Anteroom lists there directly.")
        }
    }

    @ViewBuilder
    private var fields: some View {
        Section {
            if let hidden {
                if hidden.always.isEmpty {
                    Text("None yet.").foregroundStyle(.secondary)
                }
                ForEach(hidden.always) { field in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(field.label ?? field.field)
                            Text(Marketplaces.name(field.marketplace)).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Restore") { restore(field) }
                            .buttonStyle(.borderless)
                            .disabled(saving == field.id)
                    }
                }
            } else {
                loadingRow("hidden", "Loading hidden fields")
            }
            errorRow("hidden")
        } header: {
            Text("Fields")
        } footer: {
            Text("These marketplace fields stay hidden on every listing. Restore any you want back.")
        }
    }

    @ViewBuilder
    private var listingFormulas: some View {
        Section {
            if let formulas {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Title").font(.caption).foregroundStyle(.secondary)
                    TextField("Title formula", text: $titleFormula, axis: .vertical)
                        .font(.system(.footnote, design: .monospaced))
                        .autocorrectionDisabled().textInputAutocapitalization(.never)
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("Description").font(.caption).foregroundStyle(.secondary)
                    TextField("Description formula", text: $descriptionFormula, axis: .vertical)
                        .lineLimit(4...14)
                        .font(.system(.footnote, design: .monospaced))
                        .autocorrectionDisabled().textInputAutocapitalization(.never)
                }
                Button(saving == "formulas" ? "Saving…" : "Save formulas") { saveFormulas() }
                    .disabled(!formulasDirty || saving == "formulas")
                Text(formulas.title.isEmpty && formulas.description.isEmpty
                     ? "Using the built-in list-this formulas." : "Custom formulas are active for new generations.")
                    .font(.footnote).foregroundStyle(.secondary)
            } else {
                loadingRow("formulas", "Loading formulas")
            }
            errorRow("formulas")
        } header: {
            Text("Listing formulas")
        } footer: {
            Text("Anteroom pins these formulas into every listing generation. Leave them as the defaults, or edit the placeholders for your shop. Clearing a field and saving restores the built-in formula.")
        }
    }

    // MARK: Helpers

    @ViewBuilder
    private func loadingRow(_ key: String, _ text: String) -> some View {
        if errors[key] == nil { ProgressView(text) }
    }

    @ViewBuilder
    private func errorRow(_ key: String) -> some View {
        if let error = errors[key] {
            Label(error, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.red)
        }
    }

    private var parsedDimensions: PackageDimensions? {
        let values = dimensionDraft.map { Int($0.trimmingCharacters(in: .whitespaces)) }
        guard values.allSatisfy({ ($0 ?? 0) >= 1 && ($0 ?? 0) <= 120 }) else { return nil }
        return PackageDimensions(length: values[0]!, width: values[1]!, height: values[2]!)
    }

    private var zipValid: Bool { zipDraft.isEmpty || (zipDraft.count == 5 && zipDraft.allSatisfy(\.isNumber)) }

    private var formulasDirty: Bool {
        guard let formulas else { return false }
        let title = formulas.title.isEmpty ? formulas.defaultTitle : formulas.title
        let description = formulas.description.isEmpty ? formulas.defaultDescription : formulas.description
        return titleFormula.trimmingCharacters(in: .whitespacesAndNewlines) != title.trimmingCharacters(in: .whitespacesAndNewlines)
            || descriptionFormula.trimmingCharacters(in: .whitespacesAndNewlines) != description.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func load() async {
        async let marketplaces = attempt("marketplaces") { try await api.marketplaceSettings() }
        async let stale = attempt("stale") { try await api.staleListingDays() }
        async let dimensions = attempt("dimensions") { try await api.packageDimensions() }
        async let zip = attempt("zip") { try await api.shipsFromZip() }
        async let hidden = attempt("hidden") { try await api.hiddenFields() }
        async let formulas = attempt("formulas") { try await api.listingFormulas() }
        self.marketplaces = await marketplaces
        staleDays = await stale
        if let loaded = await dimensions { show(loaded) }
        if let loaded = await zip {
            self.zip = loaded
            zipDraft = loaded
        }
        self.hidden = await hidden
        if let loaded = await formulas { show(loaded) }
    }

    private func attempt<T>(_ key: String, _ call: () async throws -> T) async -> T? {
        do {
            let value = try await call()
            errors[key] = nil
            return value
        } catch {
            errors[key] = error.localizedDescription
            return nil
        }
    }

    private func show(_ loaded: PackageDimensions) {
        dimensions = loaded
        dimensionDraft = [loaded.length, loaded.width, loaded.height].map(String.init)
    }

    private func show(_ loaded: ListingFormulas) {
        formulas = loaded
        titleFormula = loaded.title.isEmpty ? loaded.defaultTitle : loaded.title
        descriptionFormula = loaded.description.isEmpty ? loaded.defaultDescription : loaded.description
    }

    private func run(_ key: String, _ change: @escaping () async throws -> Void) {
        saving = key
        Task {
            defer { saving = nil }
            do {
                try await change()
                errors[key] = nil
            } catch {
                errors[key] = error.localizedDescription
            }
        }
    }

    /// Saved in the catalog's own order, as the desktop does.
    private func saveMarketplaces(_ chosen: Set<String>) {
        guard let available = marketplaces?.available else { return }
        let ordered = available.map(\.id).filter(chosen.contains)
        run("marketplaces") { marketplaces = try await api.setMarketplaces(ordered) }
    }

    private func saveStaleDays(_ days: Int) {
        run("stale") { staleDays = try await api.setStaleListingDays(days) }
    }

    private func saveDimensions() {
        guard let next = parsedDimensions else { return }
        run("dimensions") { show(try await api.setPackageDimensions(next)) }
    }

    private func saveZip() {
        let next = zipDraft
        run("zip") {
            let saved = try await api.setShipsFromZip(next)
            zip = saved
            zipDraft = saved
        }
    }

    private func restore(_ field: HiddenField) {
        run(field.id) { hidden = try await api.restoreHiddenField(field) }
    }

    /// Text equal to the built-in formula saves as empty, so it follows future defaults.
    private func saveFormulas() {
        guard let formulas else { return }
        let title = titleFormula.trimmingCharacters(in: .whitespacesAndNewlines) == formulas.defaultTitle.trimmingCharacters(in: .whitespacesAndNewlines) ? "" : titleFormula
        let description = descriptionFormula.trimmingCharacters(in: .whitespacesAndNewlines) == formulas.defaultDescription.trimmingCharacters(in: .whitespacesAndNewlines) ? "" : descriptionFormula
        run("formulas") { show(try await api.setListingFormulas(title: title, description: description)) }
    }
}

private struct Badge: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text).font(.caption2).foregroundStyle(.secondary)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .overlay(Capsule().stroke(WorkspaceStyle.border, lineWidth: 1))
    }
}
