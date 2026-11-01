import SwiftUI

/// Change chosen fields and labels on many listings: choose, review every
/// change per item, then apply only what was reviewed (BulkEditDialog.tsx).
struct BulkEditView: View {
    let api: AnteroomAPI
    let ids: [String]
    let onFinished: () -> Void

    private enum Field: String, CaseIterable, Identifiable {
        case title, description, brand, size, sku, price, cost, quantity, condition, primaryColor, secondaryColor
        var id: String { rawValue }
        var label: String {
            switch self {
            case .sku: "SKU"
            case .primaryColor: "Primary Color"
            case .secondaryColor: "Secondary Color"
            default: rawValue.capitalized
            }
        }
        var numeric: Bool { [.price, .cost, .quantity].contains(self) }
    }

    private enum Stage { case choose, review, done }

    @Environment(\.dismiss) private var dismiss
    @State private var chosen: Set<Field> = []
    @State private var values: [Field: String] = [:]
    @State private var labelsMode: ListMode = .keep
    @State private var labelsText = ""
    @State private var options = GeneralOptions()
    @State private var stage = Stage.choose
    @State private var edits = BulkEdits()
    @State private var rows: [BulkEditReviewRow] = []
    @State private var result: BulkEditResult?
    @State private var working = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                switch stage {
                case .choose: chooseSections
                case .review: reviewSections
                case .done: doneSections
                }
                if let error {
                    Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
                }
            }
            .navigationTitle(stage == .done ? "Edit fields" : "Edit \(ids.count) listings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbar }
            .task { if let lists = try? await api.generalOptions() { options = lists } }
        }
        .interactiveDismissDisabled(working)
    }

    @ViewBuilder
    private var chooseSections: some View {
        Section {
            ForEach(Field.allCases) { field in
                Toggle(field.label, isOn: Binding(get: { chosen.contains(field) },
                                                  set: { if $0 { chosen.insert(field) } else { chosen.remove(field) } }))
                if chosen.contains(field) { input(field) }
            }
        } header: {
            Text("Fields to change")
        } footer: {
            Text("Each chosen field gets the same value on every selected listing. Price and quantity are whole numbers; leave Cost empty to clear it.")
        }
        Section("Labels") {
            Picker("Labels", selection: $labelsMode) {
                ForEach(ListMode.allCases) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            if labelsMode != .keep {
                TextField("Labels, separated by commas", text: $labelsText)
                    .textInputAutocapitalization(.words)
            }
        }
    }

    @ViewBuilder
    private func input(_ field: Field) -> some View {
        let binding = Binding(get: { values[field] ?? "" }, set: { values[field] = $0 })
        switch field {
        case .condition, .primaryColor, .secondaryColor:
            let list = field == .condition ? options.condition : field == .primaryColor ? options.primaryColor : options.secondaryColor
            Picker(field.label, selection: binding) {
                Text(field == .secondaryColor ? "None" : "Choose").tag("")
                ForEach(list, id: \.self) { Text($0).tag($0) }
            }
        case .description:
            TextField("New description", text: binding, axis: .vertical).lineLimit(3...8)
        default:
            TextField("New \(field.label.lowercased())", text: binding)
                .keyboardType(field == .cost ? .decimalPad : field.numeric ? .numberPad : .default)
        }
    }

    @ViewBuilder
    private var reviewSections: some View {
        let ready = rows.filter(\.applicable)
        Section {
            Text("\(ready.count) of \(rows.count) listings will change.")
        }
        ForEach(rows) { row in
            Section(row.title) {
                if let reason = row.skipReason {
                    Label(reason, systemImage: "minus.circle").foregroundStyle(.secondary)
                } else {
                    ForEach(Array(row.changes.enumerated()), id: \.offset) { _, change in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(change.field).font(.caption).foregroundStyle(.secondary)
                            Text("\(change.old?.reviewText ?? "—") → \(change.new?.reviewText ?? "—")")
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var doneSections: some View {
        if let result {
            Section {
                Label("\(result.applied.count) listings updated", systemImage: "checkmark.circle")
            }
            if !result.skipped.isEmpty {
                Section("Not changed") {
                    ForEach(result.skipped, id: \.conversationID) { skipped in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(rows.first { $0.id == skipped.conversationID }?.title ?? "Listing")
                            Text(skipped.reason).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .cancellationAction) {
            if stage == .review {
                Button("Back") { stage = .choose }.disabled(working)
            } else if stage == .choose {
                Button("Cancel") { dismiss() }.disabled(working)
            }
        }
        ToolbarItem(placement: .confirmationAction) {
            if working {
                ProgressView()
            } else {
                switch stage {
                case .choose: Button("Review") { review() }.fontWeight(.semibold)
                case .review:
                    Button("Apply") { apply() }.fontWeight(.semibold).disabled(!rows.contains(where: \.applicable))
                case .done:
                    Button("Done") {
                        onFinished()
                        dismiss()
                    }.fontWeight(.semibold)
                }
            }
        }
    }

    private func review() {
        error = nil
        do {
            edits = try buildEdits()
        } catch {
            self.error = error.localizedDescription
            return
        }
        working = true
        Task {
            defer { working = false }
            do {
                rows = try await api.previewBulkEdit(ids: ids, edits: edits)
                stage = .review
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    private func apply() {
        working = true
        error = nil
        Task {
            defer { working = false }
            do {
                result = try await api.applyBulkEdit(edits: edits, rows: rows.compactMap { $0.applicable ? $0.token : nil })
                stage = .done
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    /// The desktop's checks (bulkEditsFromInputs), so mistakes show before review.
    private func buildEdits() throws -> BulkEdits {
        var result = BulkEdits()
        for field in Field.allCases where chosen.contains(field) {
            let text = (values[field] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if field.numeric {
                if text.isEmpty && field == .cost {
                    result.values[field.rawValue] = .null
                    continue
                }
                guard text.range(of: #"^\d+(?:\.\d{1,2})?$"#, options: .regularExpression) != nil,
                      let number = Double(text) else {
                    throw ValidationError("Enter a valid \(field.label.lowercased()).")
                }
                if number > 1_000_000 || (field != .cost && (number.rounded() != number || number < 1)) {
                    throw ValidationError("\(field.label) must be a positive whole number up to 1000000.")
                }
                result.values[field.rawValue] = field == .cost ? .double(number) : .int(Int(number))
            } else {
                if text.isEmpty && (field == .title || field == .size) {
                    throw ValidationError("\(field.label) cannot be empty.")
                }
                if text.isEmpty && (field == .condition || field == .primaryColor) {
                    throw ValidationError("Choose a \(field == .condition ? "condition" : "primary color").")
                }
                result.values[field.rawValue] = .string(text)
            }
        }
        result.labelsMode = labelsMode
        var seen = Set<String>()
        result.labels = labelsMode == .keep ? [] : labelsText.split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && seen.insert($0).inserted }
        if result.values.isEmpty && labelsMode == .keep {
            throw ValidationError("Choose at least one field or label action.")
        }
        if (labelsMode == .add || labelsMode == .remove) && result.labels.isEmpty {
            throw ValidationError("Enter labels to add or remove.")
        }
        return result
    }
}
