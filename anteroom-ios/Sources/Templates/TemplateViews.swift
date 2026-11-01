import SwiftUI

/// A listing's Templates action (ListingTemplatesDialog.tsx): save selected
/// reusable fields, or review a saved template against this item. All
/// changes stay in Anteroom.
struct ListingTemplatesSheet: View {
    let api: AnteroomAPI
    let listingID: String
    let onApplied: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    NavigationLink("Save fields as a template") {
                        SaveTemplateView(api: api, listingID: listingID) { dismiss() }
                    }
                    NavigationLink("Apply a saved template") {
                        ApplyTemplateView(api: api, listingID: listingID) {
                            onApplied()
                            dismiss()
                        }
                    }
                } footer: {
                    Text("Save selected reusable fields or review a saved template against this item. Manage templates and create new drafts from Settings → Listings → Saved templates.")
                }
            }
            .navigationTitle("Listing templates")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
    }
}

private struct SaveTemplateView: View {
    let api: AnteroomAPI
    let listingID: String
    let onSaved: () -> Void

    @State private var source: TemplateSource?
    @State private var templates: [ListingTemplate] = []
    @State private var replacing: ListingTemplate?
    @State private var name = ""
    @State private var checked: Set<String> = []
    @State private var working = false
    @State private var error: String?

    var body: some View {
        Form {
            if let source {
                Section {
                    Picker("Save as", selection: Binding(get: { replacing?.id ?? "new" }, set: { choose($0) })) {
                        Text("New template").tag("new")
                        ForEach(templates) { Text($0.name).tag($0.id) }
                    }
                    TextField("Template name", text: $name)
                } footer: {
                    if replacing != nil {
                        Text("Replacing saves only the checked fields below; other fields in this template are removed.")
                    }
                }
                Section {
                    ForEach(TemplateFields.groups(source.fields)) { group in
                        Toggle(isOn: Binding(get: { checked.contains(group.key) }, set: { on in
                            if on { checked.insert(group.key) } else { checked.remove(group.key) }
                        })) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(group.name)
                                Text(TemplateFields.value(group.value)).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                            }
                        }
                    }
                } header: {
                    Text("Fields from \(source.sourceTitle)")
                } footer: {
                    Text("Templates never include SKU, private notes, photos or marketplace links.")
                }
            } else if error == nil {
                ProgressView("Loading saved fields…")
            }
            if let error {
                Section {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                    Button("Reload item and templates") { Task { await load() } }
                }
            }
        }
        .navigationTitle("Save listing template")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                if working { ProgressView() } else {
                    Button(replacing == nil ? "Save" : "Replace") { save() }.fontWeight(.semibold)
                        .disabled(source == nil || name.trimmingCharacters(in: .whitespaces).isEmpty || selectedFields.isEmpty)
                }
            }
        }
        .task { await load() }
    }

    private var selectedFields: [String] {
        guard let source else { return [] }
        return TemplateFields.groups(source.fields).filter { checked.contains($0.key) }.flatMap(\.fields)
    }

    private func choose(_ id: String) {
        replacing = templates.first { $0.id == id }
        name = replacing?.name ?? ""
        checked = Set(replacing.map { TemplateFields.groups($0.fields).map(\.key) } ?? [])
    }

    private func load() async {
        do {
            async let item = api.templateSource(listingID)
            async let saved = api.templates()
            source = try await item
            templates = try await saved
            if let current = replacing { replacing = templates.first { $0.id == current.id } }
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func save() {
        guard let source else { return }
        working = true
        error = nil
        let fields = selectedFields
        let title = name.trimmingCharacters(in: .whitespaces)
        Task {
            defer { working = false }
            do {
                try await api.saveTemplate(name: title, source: source, fields: fields, replacing: replacing)
                onSaved()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

private struct ApplyTemplateView: View {
    let api: AnteroomAPI
    let listingID: String
    let onApplied: () -> Void

    @State private var templates: [ListingTemplate]?
    @State private var selected: ListingTemplate?
    @State private var mode = "fill_blank"
    @State private var review: TemplateReview?
    @State private var working = false
    @State private var error: String?

    var body: some View {
        Form {
            if let review {
                Section {
                    if review.changes.isEmpty {
                        Text("Nothing would change.").foregroundStyle(.secondary)
                    }
                    ForEach(Array(review.changes.enumerated()), id: \.offset) { _, change in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(TemplateFields.name(change.field)).font(.caption).foregroundStyle(.secondary)
                            Text("\(change.old?.reviewText ?? "—") → \(change.new?.reviewText ?? "—")")
                        }
                    }
                } header: {
                    Text("Changes to \(review.title)")
                }
            } else if let templates {
                Section {
                    if templates.isEmpty {
                        Text("No saved templates yet. Save selected fields from an inventory item first.").foregroundStyle(.secondary)
                    } else {
                        Picker("Template", selection: Binding(get: { selected?.id ?? "" }, set: { id in
                            selected = templates.first { $0.id == id }
                        })) {
                            Text("Choose template").tag("")
                            ForEach(templates) { Text($0.name).tag($0.id) }
                        }
                        Picker("Mode", selection: $mode) {
                            Text("Fill blanks only").tag("fill_blank")
                            Text("Replace selected fields").tag("replace")
                        }
                    }
                } footer: {
                    Text("Fill blanks keeps known values, including zero. Replace overwrites the selected fields; a selected form replaces its saved answer object. Unselected fields stay as they are. Related size and title fields may also align; they appear in the review.")
                }
                if let selected {
                    Section("Fields in \(selected.name)") {
                        ForEach(TemplateFields.groups(selected.fields)) { group in
                            LabeledContent(group.name, value: TemplateFields.value(group.value))
                        }
                    }
                }
            } else if error == nil {
                ProgressView("Loading templates…")
            }
            if let error {
                Section {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                    Button("Reload templates") { Task { await load() } }
                }
            }
        }
        .navigationTitle("Apply template")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if review != nil {
                ToolbarItem(placement: .cancellationAction) { Button("Back") { review = nil }.disabled(working) }
            }
            ToolbarItem(placement: .confirmationAction) {
                if working { ProgressView() } else if let review {
                    Button("Save changes") { apply(review) }.fontWeight(.semibold).disabled(review.changes.isEmpty)
                } else {
                    Button("Review") { preview() }.fontWeight(.semibold).disabled(selected == nil)
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        do {
            templates = try await api.templates()
            selected = templates?.first { $0.id == selected?.id }
            review = nil
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func preview() {
        guard let selected else { return }
        working = true
        error = nil
        Task {
            defer { working = false }
            do { review = try await api.previewTemplate(selected, listingID: listingID, mode: mode) } catch {
                self.error = error.localizedDescription
            }
        }
    }

    private func apply(_ review: TemplateReview) {
        guard let selected else { return }
        working = true
        error = nil
        Task {
            defer { working = false }
            do {
                try await api.applyTemplate(selected, listingID: listingID, mode: mode, review: review)
                onApplied()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// Settings → Listings → Saved templates (ListingTemplatesSettings.tsx).
struct SavedTemplatesView: View {
    let api: AnteroomAPI

    @State private var templates: [ListingTemplate]?
    @State private var renaming: ListingTemplate?
    @State private var newName = ""
    @State private var deleting: ListingTemplate?
    @State private var drafting: ListingTemplate?
    @State private var created: ListingSummary?
    @State private var error: String?

    var body: some View {
        List {
            Section {
                if let templates {
                    if templates.isEmpty {
                        Text("No saved templates yet.").foregroundStyle(.secondary)
                    }
                    ForEach(templates) { template in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(template.name)
                            Text(TemplateFields.groups(template.fields).map(\.name).joined(separator: ", "))
                                .font(.caption).foregroundStyle(.secondary)
                            HStack {
                                Button("Create draft") { drafting = template }
                                Spacer()
                                Button("Rename") {
                                    newName = template.name
                                    renaming = template
                                }
                                Button("Delete", role: .destructive) { deleting = template }
                            }
                            .buttonStyle(.borderless)
                            .font(.footnote)
                        }
                        .padding(.vertical, 4)
                    }
                } else if error == nil {
                    ProgressView("Loading templates…")
                }
            } footer: {
                Text("Save selected fields from a listing's Templates action. To change a template's fields, save from a listing again and choose that template. Templates never include SKU, private notes, photos or marketplace links.")
            }
            if let error {
                Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
            }
        }
        .navigationTitle("Saved templates")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task { await load() }
        .alert("Rename template", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Name", text: $newName)
            Button("Cancel", role: .cancel) {}
            Button("Save name") {
                guard let template = renaming, !newName.trimmingCharacters(in: .whitespaces).isEmpty else { return }
                let name = newName.trimmingCharacters(in: .whitespaces)
                run { try await api.renameTemplate(template, to: name) }
            }
        } message: {
            Text("Existing drafts keep their saved values.")
        }
        .confirmationDialog("Delete \"\(deleting?.name ?? "")\"?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                            titleVisibility: .visible) {
            Button("Delete template", role: .destructive) {
                if let template = deleting { run { try await api.deleteTemplate(template) } }
            }
        }
        .sheet(item: $drafting) { template in
            TemplateDraftSheet(api: api, template: template) { id in
                drafting = nil
                created = ListingSummary(id: id, title: nil)
            }
        }
        .navigationDestination(item: $created) { listing in
            ListingEditorView(api: api, listing: listing) {}
        }
    }

    private func load() async {
        do {
            templates = try await api.templates()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func run(_ change: @escaping () async throws -> Void) {
        Task {
            do {
                try await change()
                await load()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// Create a new draft from a template, safely retried by request id.
private struct TemplateDraftSheet: View {
    let api: AnteroomAPI
    let template: ListingTemplate
    let onCreated: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var requestID = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    @State private var title = ""
    @State private var sku = ""
    @State private var quantity = "1"
    @State private var working = false
    @State private var failed = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Title", text: $title)
                    TextField("SKU (optional)", text: $sku)
                    TextField("Quantity", text: $quantity).keyboardType(.numberPad)
                } footer: {
                    Text("The draft has no photos, marketplace links, sales history or sourcing-box assignment. Saved cost and labels are used only when they are part of this template.")
                }
                Section("Fields") {
                    ForEach(TemplateFields.groups(template.fields)) { group in
                        LabeledContent(group.name, value: TemplateFields.value(group.value))
                    }
                }
                if let error {
                    Section {
                        Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                        if failed { Button("Check draft result") { check() }.disabled(working) }
                    }
                }
            }
            .navigationTitle("Create draft")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(working) }
                ToolbarItem(placement: .confirmationAction) {
                    if working { ProgressView() } else {
                        Button("Create") { create() }.fontWeight(.semibold).disabled(!valid)
                    }
                }
            }
            .onAppear {
                title = String("\(template.name) draft".prefix(200))
                if case .int(let saved)? = template.fields["quantity"], saved > 0 { quantity = String(saved) }
            }
        }
        .interactiveDismissDisabled(working)
    }

    private var valid: Bool {
        guard let count = Int(quantity), (1...1_000_000).contains(count) else { return false }
        return !title.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private func create() {
        working = true
        error = nil
        Task {
            defer { working = false }
            do {
                let id = try await api.draftFromTemplate(template, requestID: requestID, title: title.trimmingCharacters(in: .whitespaces),
                                                         sku: sku.trimmingCharacters(in: .whitespaces), quantity: Int(quantity) ?? 1)
                onCreated(id)
            } catch {
                failed = true
                self.error = error.localizedDescription
            }
        }
    }

    private func check() {
        working = true
        Task {
            defer { working = false }
            do { onCreated(try await api.templateDraftResult(template, requestID: requestID)) } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
