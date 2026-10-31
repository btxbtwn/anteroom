import SwiftUI

struct DraftEditorView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var model: DraftEditorModel
    @State private var confirmDiscard = false
    private let onSaved: (Draft) -> Void

    init(api: AnteroomAPI, draft: Draft, onSaved: @escaping (Draft) -> Void) {
        _model = State(initialValue: DraftEditorModel(api: api, draft: draft))
        self.onSaved = onSaved
    }

    var body: some View {
        @Bindable var model = model
        Form {
            if let loadError = model.loadError {
                Section {
                    Label(loadError, systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.red)
                    Button("Try again") { Task { await model.load() } }
                }
            }

            Section("Photos") {
                PhotoStrip(api: model.api, draftID: model.draftID)
            }

            Section("Listing") {
                TextField("Title", text: $model.form.title, axis: .vertical)
                    .lineLimit(1...3)
                TextField("Description", text: $model.form.fields.description, axis: .vertical)
                    .lineLimit(4...12)
            }

            Section("Price") {
                NumberField("Price", text: $model.form.price, prefix: "$")
                NumberField("Cost", text: $model.form.cost, prefix: "$")
                LabeledContent("Quantity") {
                    TextField("1", text: $model.form.quantity)
                        .keyboardType(.numberPad)
                        .multilineTextAlignment(.trailing)
                }
            }

            Section("Details") {
                TextRow("Brand", text: $model.form.fields.brand)
                TextRow("Condition", text: $model.form.fields.condition)
                TextRow("Size", text: $model.form.fields.size)
                TextRow("Primary color", text: $model.form.fields.primaryColor)
                TextRow("Secondary color", text: $model.form.fields.secondaryColor)
                TextRow("Department", text: $model.form.fields.department)
                TextRow("Category", text: $model.form.fields.categoryPath)
                TextRow("SKU", text: $model.form.fields.sku)
                TextRow("Labels", text: $model.form.labels)
            }

            Section("Shipping") {
                NumberField("Weight (oz)", text: $model.form.weightOz)
                TextRow("Package (in)", text: $model.form.fields.packageDimensionsIn)
                TextRow("ZIP code", text: $model.form.fields.zipCode)
                    .keyboardType(.numbersAndPunctuation)
            }

            Section("Notes") {
                TextField("Private notes", text: $model.form.notes, axis: .vertical)
                    .lineLimit(3...10)
            }
        }
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
        LabeledContent(label) {
            TextField(label, text: $text, prompt: Text("—"))
                .multilineTextAlignment(.trailing)
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
        LabeledContent(label) {
            HStack(spacing: 2) {
                Spacer()
                if let prefix, !text.isEmpty {
                    Text(prefix).foregroundStyle(.secondary)
                }
                TextField("0", text: $text)
                    .keyboardType(.decimalPad)
                    .multilineTextAlignment(.trailing)
                    .fixedSize()
            }
        }
    }
}
