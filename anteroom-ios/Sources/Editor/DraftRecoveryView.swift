import SwiftUI

/// A conflicting copy stays on the phone until the seller chooses how to recover it.
struct DraftRecoveryView: View {
    let current: ListingEditorModel.Form
    let draft: EditorDraft
    let useKeptCopy: () -> Void
    let discardKeptCopy: () -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var confirmDiscard = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Compare your current details with the edits kept on this phone. Using the kept copy replaces the editor's values. Nothing is saved until you tap Save in the editor.")
                }
                ForEach(differences, id: \.label) { difference in
                    Section(difference.label) {
                        LabeledContent("Current", value: difference.current.isEmpty ? "Empty" : difference.current)
                        LabeledContent("Kept", value: difference.kept.isEmpty ? "Empty" : difference.kept)
                    }
                }
                if !draft.pending.isEmpty {
                    Section("Kept marketplace edits") {
                        Text("Review these fields in their marketplace tabs after using the kept copy.")
                        ForEach(draft.pending.keys.sorted(), id: \.self) { key in
                            LabeledContent(key.replacingOccurrences(of: ".", with: " · "),
                                           value: draft.pending[key]?.text ?? "Empty")
                        }
                    }
                }
                Section {
                    Button("Use kept copy", action: useKeptCopy)
                    Button("Discard kept edits", role: .destructive) { confirmDiscard = true }
                }
            }
            .navigationTitle("Review kept edits")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Later") { dismiss() } } }
            .confirmationDialog("Discard the edits kept on this phone?", isPresented: $confirmDiscard,
                                titleVisibility: .visible) {
                Button("Discard kept edits", role: .destructive) { discardKeptCopy(); dismiss() }
            }
        }
    }

    private struct Difference {
        let label: String
        let current: String
        let kept: String
    }

    private var differences: [Difference] {
        let before = values(current), after = values(draft.form)
        return zip(before, after).compactMap { saved, kept in
            saved.1 == kept.1 ? nil : Difference(label: saved.0, current: saved.1, kept: kept.1)
        }
    }

    private func values(_ form: ListingEditorModel.Form) -> [(String, String)] {
        let fields = form.fields, notes = form.sellerInputs
        var values = [
            ("Title", fields.title), ("Description", fields.description), ("Price", form.price),
            ("Cost", form.cost), ("Quantity", form.quantity), ("Brand", fields.brand),
            ("Condition", fields.condition), ("Primary color", fields.primaryColor),
            ("Secondary color", fields.secondaryColor), ("Size", fields.size), ("SKU", fields.sku),
            ("Labels", form.labels), ("ZIP code", fields.zipCode), ("Category", fields.categoryPath),
            ("Department", fields.department), ("Weight (oz)", form.weightOz),
            ("Package dimensions", fields.packageDimensionsIn), ("Tag size", notes.size),
            ("Item details SKU", notes.sku), ("Seller notes", notes.sellerNotes),
            ("Known flaws", notes.knownFlaws), ("Description measurements", notes.descriptionMeasurements),
            ("Item cost", notes.cog), ("Shipping size", notes.packageDimensions),
            ("Item labels", notes.vendooLabels), ("Garment", notes.garment.label),
        ]
        for garment in SellerInputs.Garment.allCases {
            var inputs = notes
            inputs.garment = garment
            for field in garment.fields {
                values.append(("\(garment.label): \(field.label)", inputs.measurement(field.key)))
            }
        }
        return values
    }
}
