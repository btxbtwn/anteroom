import SwiftUI

/// A self-contained creation task. Dismissing before Create writes nothing.
struct NewListingView: View {
    let api: AnteroomAPI
    let onCreated: (Draft) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var creating = false
    @State private var error: String?
    @FocusState private var titleFocused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Listing title", text: $title, axis: .vertical)
                        .lineLimit(1...4)
                        .focused($titleFocused)
                        .accessibilityLabel("Listing title")
                } header: {
                    Text("What are you listing?")
                } footer: {
                    Text("Add photos and item details on the next screen. You can change the title later.")
                }
                if let error {
                    Section {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.red)
                            .accessibilityLabel("Couldn't create listing. \(error)")
                    }
                }
            }
            .navigationTitle("New listing")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }.disabled(creating)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(action: create) {
                        if creating {
                            ProgressView().accessibilityLabel("Creating draft")
                        } else {
                            Text("Create").fontWeight(.semibold)
                        }
                    }
                    .disabled(creating || trimmedTitle.isEmpty)
                    .accessibilityLabel("Create draft")
                }
            }
            .interactiveDismissDisabled(creating)
            .task { titleFocused = true }
        }
    }

    private var trimmedTitle: String { title.trimmingCharacters(in: .whitespacesAndNewlines) }

    private func create() {
        guard !creating, !trimmedTitle.isEmpty else { return }
        let value = trimmedTitle
        creating = true
        error = nil
        Task {
            defer { creating = false }
            do {
                let draft = try await api.createDraft(title: value)
                onCreated(draft)
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
