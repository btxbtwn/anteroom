import SwiftUI

/// Saved label suggestions and their colors (InventorySettings.tsx → Labels).
/// Deleting a suggestion keeps labels already assigned to items.
struct LabelsSettingsView: View {
    let api: AnteroomAPI

    static let colors = ["gray", "red", "orange", "yellow", "green", "teal", "blue", "purple", "pink"]
    static let swatches: [String: Color] = ["gray": .gray, "red": .red, "orange": .orange, "yellow": .yellow,
                                            "green": .green, "teal": .teal, "blue": .blue, "purple": .purple, "pink": .pink]

    @State private var prefs: LabelPreferences?
    @State private var newLabel = ""
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        Form {
            Section {
                HStack {
                    TextField("New label", text: $newLabel)
                        .onSubmit(add)
                    Button("Add label", action: add)
                        .disabled(newLabel.trimmingCharacters(in: .whitespaces).isEmpty || saving)
                }
            } footer: {
                Text("Manage saved suggestions and label colors. Deleting a suggestion keeps labels already assigned to items.")
            }
            if let prefs {
                Section("Labels") {
                    if prefs.labels.isEmpty {
                        Text("Labels you add to items appear here.").foregroundStyle(.secondary)
                    }
                    ForEach(prefs.labels, id: \.self) { label in
                        HStack {
                            Circle().fill(prefs.color(of: label).flatMap { Self.swatches[$0] } ?? .clear)
                                .overlay(Circle().stroke(WorkspaceStyle.border, lineWidth: 1))
                                .frame(width: 12, height: 12)
                                .accessibilityHidden(true)
                            Text(label)
                            Spacer()
                            Menu(prefs.color(of: label)?.capitalized ?? "No color") {
                                Button("No color") { setColor(label, nil) }
                                ForEach(Self.colors, id: \.self) { color in
                                    Button(color.capitalized) { setColor(label, color) }
                                }
                            }
                            .accessibilityLabel("Color for \(label)")
                        }
                        .swipeActions {
                            Button("Delete", role: .destructive) { save(["forget_label": .string(label)]) }
                        }
                    }
                }
                if !prefs.hidden.isEmpty {
                    Section("Deleted suggestions") {
                        ForEach(prefs.hidden, id: \.self) { label in
                            HStack {
                                Text(label)
                                Spacer()
                                Button("Restore") {
                                    save(["remember_labels": .array([.string(label)]), "restore_labels": .array([.string(label)])])
                                }
                                .buttonStyle(.borderless)
                            }
                        }
                    }
                }
            } else if error == nil {
                ProgressView()
            }
            if let error {
                Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
            }
        }
        .disabled(saving)
        .navigationTitle("Labels")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
    }

    private func add() {
        let label = newLabel.trimmingCharacters(in: .whitespaces)
        guard !label.isEmpty else { return }
        save(["remember_labels": .array([.string(label)]), "restore_labels": .array([.string(label)])]) { newLabel = "" }
    }

    private func setColor(_ label: String, _ color: String?) {
        guard let prefs else { return }
        var next = prefs.colors.filter { $0.key.lowercased() != label.lowercased() }
        if let color { next[label] = color }
        save(["label_colors": .object(next.mapValues(JSONValue.string))])
    }

    private func load() async {
        do {
            prefs = try await api.labelPreferences()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func save(_ change: [String: JSONValue], then done: (() -> Void)? = nil) {
        saving = true
        Task {
            defer { saving = false }
            do {
                prefs = try await api.updateLabelPreferences(change)
                error = nil
                done?()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// The label parts of `/api/settings/ui`.
struct LabelPreferences: Decodable, Sendable {
    let colors: [String: String]
    let recent: [String]
    let hidden: [String]

    enum CodingKeys: String, CodingKey {
        case colors = "label_colors", recent = "recent_vendoo_labels", hidden = "hidden_vendoo_labels"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        colors = (try? values.decodeIfPresent([String: String].self, forKey: .colors)) ?? [:]
        recent = (try? values.decodeIfPresent([String].self, forKey: .recent)) ?? []
        hidden = (try? values.decodeIfPresent([String].self, forKey: .hidden)) ?? []
    }

    /// Colored and recently used labels, case-insensitively once, without deleted ones.
    var labels: [String] {
        let deleted = Set(hidden.map { $0.lowercased() })
        var seen: [String: String] = [:]
        for label in Array(colors.keys) + recent where !deleted.contains(label.lowercased()) {
            if seen[label.lowercased()] == nil { seen[label.lowercased()] = label }
        }
        return seen.values.sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
    }

    func color(of label: String) -> String? {
        colors.first { $0.key.lowercased() == label.lowercased() }?.value
    }
}
