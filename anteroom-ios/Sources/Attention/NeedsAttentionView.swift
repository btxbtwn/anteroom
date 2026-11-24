import SwiftUI

/// More → Needs attention (the web's Home "Needs attention" panel). Snooze,
/// Resolve and Restore only change which alerts Anteroom shows; nothing here
/// touches a listing or a marketplace.
struct NeedsAttentionView: View {
    let api: AnteroomAPI
    @State private var tab: AttentionTab = .active
    @State private var inbox: AttentionInbox?
    @State private var error: String?
    @State private var actionError: String?
    /// Alerts with a save in flight. Their actions are disabled until it
    /// finishes, so a Restore can never race the Resolve or Snooze before it.
    @State private var saving: Set<String> = []

    var body: some View {
        List {
            Section {
                Picker("Show", selection: $tab) {
                    ForEach(AttentionTab.allCases, id: \.self) { tab in
                        Text(label(tab)).tag(tab)
                    }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("attention-tab")
            } footer: {
                Text(footer)
            }
            if let actionError {
                Section {
                    Label(actionError, systemImage: "exclamationmark.triangle")
                        .font(.footnote).foregroundStyle(.red)
                }
            }
            if let inbox {
                let items = inbox.items(tab)
                Section {
                    if items.isEmpty {
                        Text(AttentionText.empty(tab)).font(.footnote).foregroundStyle(.secondary)
                    }
                    ForEach(items, id: \.key) { item in
                        row(item)
                    }
                }
            } else if let error {
                Section {
                    Text("Could not load Needs attention: \(error)").font(.footnote).foregroundStyle(.secondary)
                    Button("Try again") { Task { await load() } }
                }
            } else {
                ProgressView("Loading alerts…")
            }
        }
        .navigationTitle("Needs attention")
        .task { await load() }
        .refreshable { await load() }
    }

    private var footer: String {
        switch tab {
        case .active: "Resolve hides an alert until something changes, like another sale or a new error. Snooze hides it for 7 days."
        case .snoozed: "Back after 7 days if they still apply."
        case .resolved: "Back when something changes. Pairs marked not duplicates stay resolved."
        }
    }

    private func label(_ tab: AttentionTab) -> String {
        guard let inbox else { return tab.rawValue }
        let count = inbox.items(tab).count
        return count > 0 ? "\(tab.rawValue) \(count)" : tab.rawValue
    }

    private func row(_ item: AttentionItem) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(AttentionText.kind(item.kind))
                    .font(.caption.weight(.medium))
                    .foregroundStyle(item.priority == "high" && tab == .active ? Color.red : Color.secondary)
                if let status = AttentionText.status(item, in: tab) {
                    Text("· \(status)").font(.caption).foregroundStyle(.secondary)
                }
            }
            Text(item.title).font(.subheadline.weight(.medium))
            Text(item.detail).font(.caption).foregroundStyle(.secondary)
            actions(item).font(.footnote).padding(.top, 4)
        }
        .padding(.vertical, 2)
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            if tab == .active {
                Button { Task { await resolve(item) } } label: {
                    Label(AttentionText.resolve(item.kind), systemImage: "checkmark")
                }
                .tint(.green)
                Button { Task { await snooze(item) } } label: {
                    Label("Snooze 7 days", systemImage: "alarm")
                }
                .tint(.orange)
            } else {
                Button { Task { await restore(item) } } label: {
                    Label("Restore", systemImage: "arrow.uturn.backward")
                }
            }
        }
        .disabled(saving.contains(item.key))
    }

    @ViewBuilder
    private func actions(_ item: AttentionItem) -> some View {
        HStack(spacing: 16) {
            if tab == .active {
                Button { Task { await resolve(item) } } label: {
                    Label(AttentionText.resolve(item.kind), systemImage: "checkmark")
                }
                .accessibilityLabel("\(AttentionText.resolve(item.kind)): \(item.title)")
                Button { Task { await snooze(item) } } label: {
                    Label("Snooze 7 days", systemImage: "alarm")
                }
                .accessibilityLabel("Snooze \(item.title) for 7 days")
            } else {
                Button { Task { await restore(item) } } label: {
                    Label("Restore", systemImage: "arrow.uturn.backward")
                }
                .accessibilityLabel("Restore \(item.title)")
            }
        }
        .buttonStyle(.borderless)
        .disabled(saving.contains(item.key))
    }

    private func load() async {
        do {
            let result = try await api.attentionInbox()
            try Task.checkCancellation()
            inbox = result
            error = nil
        } catch is CancellationError {
            // A newer load owns the result.
        } catch {
            if !Task.isCancelled { self.error = error.localizedDescription }
        }
    }

    /// Move the row at once, then reload from the server if the save fails.
    /// One save per alert at a time: an action on an alert whose save is
    /// still in flight is ignored (its buttons are disabled meanwhile).
    private func change(_ key: String, _ move: (inout AttentionInbox) -> Void, _ save: () async throws -> Void, failure: String) async {
        guard !saving.contains(key), var next = inbox else { return }
        saving.insert(key)
        defer { saving.remove(key) }
        move(&next)
        inbox = next
        actionError = nil
        do {
            try await save()
        } catch {
            actionError = "\(failure) \(error.localizedDescription)"
            await load()
        }
    }

    private func snooze(_ item: AttentionItem) async {
        await change(item.key, { $0.snooze(item) }, { _ = try await api.snoozeAttention(item) }, failure: "Could not snooze “\(item.title)”.")
    }

    private func resolve(_ item: AttentionItem) async {
        await change(item.key, { $0.resolve(item) }, { _ = try await api.resolveAttention(item) }, failure: "Could not resolve “\(item.title)”.")
    }

    private func restore(_ item: AttentionItem) async {
        await change(item.key, { $0.restore(item.key) }, { _ = try await api.restoreAttention(item.key) }, failure: "Could not restore “\(item.title)”.")
    }
}
