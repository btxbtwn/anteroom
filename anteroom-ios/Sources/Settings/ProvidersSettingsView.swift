import SwiftUI

/// Settings → Providers for the hosted workspace (ProvidersSettings.tsx):
/// API-key providers only. Keys are encrypted in the account's private vault
/// and never sent back to this iPhone.
struct ProvidersSettingsView: View {
    let api: AnteroomAPI

    @State private var providers: [KeyProvider] = []
    @State private var order: ProviderOrder?
    @State private var loaded = false
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        Form {
            if loaded {
                Section {
                    Picker("Primary", selection: Binding(get: { order?.primary ?? "" }, set: { setOrder(primary: $0) })) {
                        if !configured.contains(where: { $0.id == order?.primary }) {
                            Text("Choose").tag(order?.primary ?? "")
                        }
                        ForEach(configured) { Text($0.label).tag($0.id) }
                    }
                    Picker("Fallback", selection: Binding(get: { order?.fallback ?? "none" }, set: { setOrder(fallback: $0) })) {
                        Text("None").tag("none")
                        ForEach(configured.filter { $0.id != order?.primary }) { Text($0.label).tag($0.id) }
                        if let fallback = order?.fallback, fallback != "none", !configured.contains(where: { $0.id == fallback }) {
                            Text(fallback).tag(fallback)
                        }
                    }
                    Picker("Photos", selection: Binding(get: { order?.photoProvider ?? "same" }, set: { setPhotos($0) })) {
                        Text("Same as listing").tag("same")
                        ForEach(configured) { Text($0.label).tag($0.id) }
                        if let photo = order?.photoProvider, photo != "same", !configured.contains(where: { $0.id == photo }) {
                            Text(photo).tag(photo)
                        }
                    }
                } header: {
                    Text("Listing AI")
                } footer: {
                    Text(configured.isEmpty
                         ? "Add an API key below, then choose it here to write listings in the web app and on this iPhone."
                         : "The primary writes your listings; the fallback takes over if it fails. Photos can use a different provider.")
                }
                .disabled(saving)
                Section {
                    NavigationLink {
                        MimoKeyView(api: api, maskedKey: order?.mimoMaskedKey) { Task { await load() } }
                    } label: {
                        HStack {
                            Text("Xiaomi MiMo")
                            Spacer()
                            Text(order?.mimoConfigured == true ? "Key saved" : "Not set")
                                .font(.footnote)
                                .foregroundStyle(order?.mimoConfigured == true ? .green : .secondary)
                        }
                    }
                    ForEach(providers) { provider in
                        NavigationLink {
                            ProviderKeyView(api: api, provider: provider) { Task { await load() } }
                        } label: {
                            HStack {
                                Text(provider.label)
                                Spacer()
                                Text(provider.configured ? "Key saved" : "Not set")
                                    .font(.footnote)
                                    .foregroundStyle(provider.configured ? .green : .secondary)
                            }
                        }
                    }
                } header: {
                    Text("API keys")
                } footer: {
                    Text("Encrypted in your account's private vault. Usage is billed by each provider to your own account. Sign-in providers need a connected Mac.")
                }
            }
            if let error {
                Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
            }
        }
        .overlay { if !loaded && error == nil { ProgressView() } }
        .navigationTitle("Providers")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task { await load() }
    }

    /// Every provider with a key, MiMo included, as listing-AI choices.
    private var configured: [Choice] {
        var choices = providers.filter(\.configured).map { Choice(id: $0.id, label: $0.label) }
        if order?.mimoConfigured == true { choices.append(Choice(id: "mimo", label: "Xiaomi MiMo")) }
        return choices
    }

    private struct Choice: Identifiable {
        let id: String
        let label: String
    }

    private func load() async {
        do {
            async let keys = api.keyProviders()
            async let current = api.providerOrder()
            providers = try await keys
            order = try await current
            loaded = true
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func setOrder(primary: String? = nil, fallback: String? = nil) {
        guard let order else { return }
        let nextPrimary = primary ?? order.primary
        var nextFallback = fallback ?? order.fallback
        if nextFallback == nextPrimary { nextFallback = "none" }
        run { try await api.setProviderOrder(primary: nextPrimary, fallback: nextFallback) }
    }

    private func setPhotos(_ choice: String) {
        run { try await api.setPhotoProvider(choice) }
    }

    private func run(_ change: @escaping () async throws -> Void) {
        saving = true
        Task {
            defer { saving = false }
            do {
                try await change()
                await load()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// One provider: its key, a connection test, and the models it uses.
private struct ProviderKeyView: View {
    let api: AnteroomAPI
    let provider: KeyProvider
    let onChanged: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var newKey = ""
    @State private var configured: Bool
    @State private var working = false
    @State private var message: String?
    @State private var failure: String?
    @State private var models: ProviderModels?
    @State private var confirmRemove = false

    init(api: AnteroomAPI, provider: KeyProvider, onChanged: @escaping () -> Void) {
        self.api = api
        self.provider = provider
        self.onChanged = onChanged
        _configured = State(initialValue: provider.configured)
    }

    var body: some View {
        Form {
            Section {
                if configured {
                    LabeledContent("Saved key", value: provider.maskedKey ?? "Saved")
                }
                SecureField(configured ? "Replace key" : (provider.keyHint ?? "API key"), text: $newKey)
                    .textContentType(.password)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                Button(configured ? "Replace key" : "Save key") { saveKey() }
                    .disabled(newKey.trimmingCharacters(in: .whitespaces).isEmpty || working)
                if let link = provider.keyURL.flatMap(URL.init(string:)) {
                    Link("Get a \(provider.label) key", destination: link)
                }
            } header: {
                Text("API key")
            } footer: {
                Text("Encrypted in your account's private vault and never shown again. Usage is billed by \(provider.label) to your account.")
            }
            if configured {
                Section {
                    Button("Test connection") { test() }.disabled(working)
                    Button("Remove key", role: .destructive) { confirmRemove = true }.disabled(working)
                }
                modelSection
            }
            if let message {
                Section { Label(message, systemImage: "checkmark.circle").foregroundStyle(.green) }
            }
            if let failure {
                Section { Label(failure, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
            }
        }
        .navigationTitle(provider.label)
        .navigationBarTitleDisplayMode(.inline)
        .overlay { if working { ProgressView() } }
        .confirmationDialog("Remove the \(provider.label) key?", isPresented: $confirmRemove, titleVisibility: .visible) {
            Button("Remove key", role: .destructive) { remove() }
        } message: {
            Text("Listings stop using \(provider.label) until you add a key again.")
        }
        .task { if configured { await loadModels() } }
    }

    @ViewBuilder
    private var modelSection: some View {
        Section("Models") {
            if let models {
                if let error = models.error {
                    Text(error).font(.footnote).foregroundStyle(.red)
                }
                if !models.models.isEmpty {
                    Picker("Listing model", selection: Binding(get: { models.listingModel ?? "" }, set: { choose(listing: $0) })) {
                        ForEach(models.models, id: \.self) { Text(models.labels?[$0] ?? $0).tag($0) }
                    }
                    Picker("Photo model", selection: Binding(get: { models.visionModel ?? "" }, set: { choose(vision: $0) })) {
                        ForEach(models.models, id: \.self) { Text(models.labels?[$0] ?? $0).tag($0) }
                    }
                }
                if let reasoning = models.reasoning {
                    Picker("Reasoning", selection: Binding(get: { reasoning.value }, set: { choose(reasoning: $0) })) {
                        ForEach(reasoning.options, id: \.value) { Text($0.label).tag($0.value) }
                    }
                }
            } else {
                ProgressView("Loading models")
            }
        }
        .disabled(working)
    }

    private func run(_ change: @escaping () async throws -> Void) {
        working = true
        message = nil
        failure = nil
        Task {
            defer { working = false }
            do { try await change() } catch { failure = error.localizedDescription }
        }
    }

    private func saveKey() {
        let key = newKey
        run {
            try await api.saveProviderKey(provider.id, key: key)
            newKey = ""
            configured = true
            message = "Key saved."
            onChanged()
            await loadModels()
        }
    }

    private func test() {
        run {
            if let problem = try await api.testProviderKey(provider.id) {
                failure = problem
            } else {
                message = "\(provider.label) answered. The key works."
            }
        }
    }

    private func remove() {
        run {
            try await api.removeProviderKey(provider.id)
            onChanged()
            dismiss()
        }
    }

    private func loadModels() async {
        models = try? await api.providerModels(provider.id)
    }

    private func choose(listing: String? = nil, vision: String? = nil, reasoning: String? = nil) {
        run {
            try await api.setProviderModels(provider.id, vision: vision, listing: listing, reasoning: reasoning)
            await loadModels()
            onChanged()
        }
    }
}

/// Xiaomi MiMo: one key, fixed models (ProvidersSettings.tsx, hosted).
private struct MimoKeyView: View {
    let api: AnteroomAPI
    let maskedKey: String?
    let onChanged: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var newKey = ""
    @State private var configured: Bool
    @State private var working = false
    @State private var message: String?
    @State private var failure: String?
    @State private var confirmRemove = false

    init(api: AnteroomAPI, maskedKey: String?, onChanged: @escaping () -> Void) {
        self.api = api
        self.maskedKey = maskedKey
        self.onChanged = onChanged
        _configured = State(initialValue: !(maskedKey ?? "").isEmpty)
    }

    var body: some View {
        Form {
            Section {
                if configured { LabeledContent("Saved key", value: maskedKey ?? "Saved") }
                SecureField(configured ? "Replace key" : "MiMo API key", text: $newKey)
                    .textContentType(.password).autocorrectionDisabled().textInputAutocapitalization(.never)
                Button(configured ? "Replace key" : "Save key") { save() }
                    .disabled(newKey.trimmingCharacters(in: .whitespaces).isEmpty || working)
            } header: {
                Text("API key")
            } footer: {
                Text("Encrypted in your account's private vault. Usage is billed to your MiMo account. MiMo reads photos with mimo-v2.5 and writes listings with mimo-v2.5-pro. These are fixed.")
            }
            if configured {
                Section {
                    Button("Test connection") { test() }.disabled(working)
                    Button("Remove key", role: .destructive) { confirmRemove = true }.disabled(working)
                }
            }
            if let message { Section { Label(message, systemImage: "checkmark.circle").foregroundStyle(.green) } }
            if let failure { Section { Label(failure, systemImage: "exclamationmark.triangle").foregroundStyle(.red) } }
        }
        .navigationTitle("Xiaomi MiMo")
        .navigationBarTitleDisplayMode(.inline)
        .overlay { if working { ProgressView() } }
        .confirmationDialog("Remove the MiMo key?", isPresented: $confirmRemove, titleVisibility: .visible) {
            Button("Remove key", role: .destructive) { remove() }
        }
    }

    private func run(_ change: @escaping () async throws -> Void) {
        working = true
        message = nil
        failure = nil
        Task {
            defer { working = false }
            do { try await change() } catch { failure = error.localizedDescription }
        }
    }

    private func save() {
        let key = newKey
        run {
            try await api.saveMimoKey(key)
            newKey = ""
            configured = true
            message = "Key saved."
            onChanged()
        }
    }

    private func test() {
        run {
            if let problem = try await api.testMimo() { failure = problem } else { message = "MiMo answered. The key works." }
        }
    }

    private func remove() {
        run {
            try await api.removeMimoKey()
            onChanged()
            dismiss()
        }
    }
}
