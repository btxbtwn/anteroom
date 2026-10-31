import SwiftUI

struct DraftEditorView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var model: DraftEditorModel
    @State private var confirmDiscard = false
    @State private var showingAssistant = false
    @State private var showingHistory = false
    private let onSaved: (Draft) -> Void

    init(api: AnteroomAPI, draft: Draft, onSaved: @escaping (Draft) -> Void) {
        _model = State(initialValue: DraftEditorModel(api: api, draft: draft))
        self.onSaved = onSaved
    }

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let loadError = model.loadError {
                    WorkspaceSection("Couldn't load listing") {
                        Text(loadError).foregroundStyle(.red)
                        Button("Try again") { Task { await model.load() } }
                    }
                }
                HStack(spacing: 8) {
                    Image(systemName: "square.and.pencil")
                    Text("General").fontWeight(.semibold)
                    Spacer()
                    Text(model.isLoading ? "Loading…" : model.loadError != nil ? "Not loaded" : model.hasChanges ? "Unsaved changes" : "Saved draft")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .font(.subheadline)
                .padding(.horizontal, 4)

                WorkspaceSection("Listing assistant") {
                    Button("Generate or edit with Anteroom", systemImage: "sparkles") { showingAssistant = true }
                        .frame(minHeight: 44)
                        .disabled(model.hasChanges || model.isLoading || model.loadError != nil || model.isSaving)
                    if model.hasChanges {
                        Text("Save your changes before opening the assistant.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                WorkspaceSection("Photos") {
                    PhotoStrip(api: model.api, draftID: model.draftID)
                }
                WorkspaceSection("Listing details") {
                    WorkspaceField("Title") {
                        TextField("Title", text: $model.form.title, axis: .vertical).lineLimit(1...3)
                    }
                    WorkspaceField("Description") {
                        TextField("Describe your item", text: $model.form.fields.description, axis: .vertical)
                            .lineLimit(5...16)
                    }
                    let priceLayout = dynamicTypeSize.isAccessibilitySize
                        ? AnyLayout(VStackLayout(alignment: .leading, spacing: 12))
                        : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
                    priceLayout {
                        NumberField("Price", text: $model.form.price, prefix: "$")
                        NumberField("Cost", text: $model.form.cost, prefix: "$")
                    }
                    WorkspaceField("Quantity") {
                        TextField("1", text: $model.form.quantity).keyboardType(.numberPad)
                    }
                    TextRow("Brand", text: $model.form.fields.brand)
                    TextRow("Condition", text: $model.form.fields.condition)
                    TextRow("Primary Color", text: $model.form.fields.primaryColor)
                    TextRow("Secondary Color", text: $model.form.fields.secondaryColor)
                    TextRow("Size", text: $model.form.fields.size)
                    TextRow("SKU", text: $model.form.fields.sku)
                    TextRow("Inventory location", text: $model.form.fields.zipCode)
                    TextRow("Category", text: $model.form.fields.categoryPath)
                    TextRow("Department", text: $model.form.fields.department)
                    NumberField("Package Weight (oz)", text: $model.form.weightOz)
                    TextRow("Package Dimensions (LxWxH in)", text: $model.form.fields.packageDimensionsIn)
                }
                WorkspaceSection("Organization") {
                    TextRow("Labels", text: $model.form.labels)
                    WorkspaceField("Private notes") {
                        TextField("Notes about this item", text: $model.form.notes, axis: .vertical).lineLimit(3...10)
                    }
                }
            }
            .padding(16)
        }
        .background(WorkspaceStyle.background)
        .toolbarBackground(WorkspaceStyle.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .scrollDismissesKeyboard(.interactively)
        .disabled(model.isLoading && model.loadError == nil)
        .overlay { if model.isLoading { ProgressView() } }
        .navigationTitle(model.form.title.isEmpty ? "Draft" : model.form.title)
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(model.hasChanges)
        .toolbar {
            if model.hasChanges {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { confirmDiscard = true }
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button("Listing history", systemImage: "clock.arrow.circlepath") { showingHistory = true }
                    .frame(minWidth: 44, minHeight: 44)
                    .disabled(model.hasChanges || model.isLoading || model.isSaving || model.loadError != nil)
                    .accessibilityHint("Save changes first to preview and restore earlier versions")
            }
            ToolbarItem(placement: .topBarTrailing) {
                if model.isSaving {
                    ProgressView()
                } else {
                    Button("Save") { save() }
                        .fontWeight(.semibold)
                        .disabled(!model.hasChanges)
                }
            }
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") { hideKeyboard() }
            }
        }
        .confirmationDialog("Discard your changes?", isPresented: $confirmDiscard, titleVisibility: .visible) {
            Button("Discard changes", role: .destructive) { dismiss() }
        }
        .alert("Couldn't save", isPresented: saveErrorShown) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.saveError ?? "")
        }
        .alert("Changed on another device", isPresented: $model.hasConflict) {
            Button("Reload", role: .destructive) { Task { await model.load() } }
            Button("Keep editing", role: .cancel) {}
        } message: {
            Text("Reloading shows the latest version and drops your edits here.")
        }
        .sheet(isPresented: $showingAssistant, onDismiss: {
            Task {
                guard !model.hasChanges else { return }
                await model.load()
                if model.loadError == nil { onSaved(model.summary) }
            }
        }) {
            ListingAssistantView(api: model.api, listingID: model.draftID)
        }
        .sheet(isPresented: $showingHistory, onDismiss: {
            Task {
                guard !model.hasChanges else { return }
                await model.load()
                if model.loadError == nil { onSaved(model.summary) }
            }
        }) {
            ListingHistoryView(api: model.api, listingID: model.draftID)
        }
        .task { await model.load() }
    }

    private var saveErrorShown: Binding<Bool> {
        Binding(get: { model.saveError != nil }, set: { if !$0 { model.saveError = nil } })
    }

    private func save() {
        hideKeyboard()
        Task {
            if let draft = await model.save() {
                onSaved(draft)
            }
        }
    }

    private func hideKeyboard() {
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
    }
}

private struct TextRow: View {
    let label: String
    @Binding var text: String

    init(_ label: String, text: Binding<String>) {
        self.label = label
        _text = text
    }

    var body: some View {
        WorkspaceField(label) {
            TextField(label, text: $text, axis: .vertical).lineLimit(1...4)
        }
    }
}

private struct NumberField: View {
    let label: String
    @Binding var text: String
    var prefix: String?

    init(_ label: String, text: Binding<String>, prefix: String? = nil) {
        self.label = label
        _text = text
        self.prefix = prefix
    }

    var body: some View {
        WorkspaceField(label) {
            HStack(spacing: 2) {
                if let prefix, !text.isEmpty {
                    Text(prefix).foregroundStyle(.secondary)
                }
                TextField("0", text: $text)
                    .accessibilityLabel(label)
                    .keyboardType(.decimalPad)
            }
        }
    }
}
