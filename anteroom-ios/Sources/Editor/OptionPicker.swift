import SwiftUI

/// The General dropdown lists the desktop editor offers (Vendoo's reference
/// lists from `/api/catalog/dropdown-options`), keyed by listing field.
struct GeneralOptions: Equatable, Sendable {
    var condition: [String] = []
    var primaryColor: [String] = []
    var secondaryColor: [String] = []
    var size: [String] = []

    /// `forms.vendoo` names size `usSize`; the listing calls it `size`.
    init(vendoo: [String: [String]]) {
        condition = vendoo["condition"] ?? []
        primaryColor = vendoo["primaryColor"] ?? []
        secondaryColor = vendoo["secondaryColor"] ?? []
        size = vendoo["usSize"] ?? vendoo["size"] ?? []
    }

    init() {}
}

/// A field that picks one value from a list in a searchable sheet, like the
/// desktop's SearchableSelect. Without a list it stays a text field, so a
/// missing catalog never blocks editing.
struct OptionField: View {
    let label: String
    @Binding var value: String
    let options: [String]
    @State private var picking = false

    init(_ label: String, value: Binding<String>, options: [String]) {
        self.label = label
        _value = value
        self.options = options
    }

    var body: some View {
        if options.isEmpty {
            TextRow(label, text: $value)
        } else {
            WorkspaceField(label) {
                Button { picking = true } label: {
                    HStack {
                        Text(value.isEmpty ? "Select" : value)
                            .foregroundStyle(value.isEmpty ? .secondary : .primary)
                            .multilineTextAlignment(.leading)
                        Spacer(minLength: 8)
                        Image(systemName: "chevron.up.chevron.down").font(.caption).foregroundStyle(.secondary)
                    }
                    .frame(minHeight: 22)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityValue(value.isEmpty ? "Not set" : value)
                .accessibilityHint("Choose \(label.lowercased())")
            }
            .sheet(isPresented: $picking) {
                OptionPickerSheet(title: label, value: $value, options: options)
            }
        }
    }
}

struct OptionPickerSheet: View {
    let title: String
    @Binding var value: String
    let options: [String]
    @Environment(\.dismiss) private var dismiss
    @State private var search = ""

    var body: some View {
        NavigationStack {
            List {
                // A saved value outside the list stays visible, as on the desktop.
                if !value.isEmpty, !options.contains(value), search.isEmpty {
                    Section("Current") { row(value) }
                }
                Section {
                    ForEach(filtered, id: \.self) { row($0) }
                }
            }
            .overlay {
                if filtered.isEmpty && !search.isEmpty { ContentUnavailableView.search(text: search) }
            }
            .searchable(text: $search, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search")
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                if !value.isEmpty {
                    ToolbarItem(placement: .destructiveAction) {
                        Button("Clear") {
                            value = ""
                            dismiss()
                        }
                    }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private var filtered: [String] {
        let needle = search.trimmingCharacters(in: .whitespaces)
        return needle.isEmpty ? options : options.filter { $0.localizedCaseInsensitiveContains(needle) }
    }

    private func row(_ option: String) -> some View {
        Button {
            value = option
            dismiss()
        } label: {
            HStack {
                Text(option).foregroundStyle(.primary)
                Spacer()
                if option == value { Image(systemName: "checkmark").foregroundStyle(WorkspaceStyle.accent) }
            }
            .frame(minHeight: 32)
            .contentShape(Rectangle())
        }
        .accessibilityAddTraits(option == value ? .isSelected : [])
    }
}
