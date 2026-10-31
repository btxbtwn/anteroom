import SwiftUI

struct ListingAssistantView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var model: ListingAssistantModel
    @State private var action: AssistantAction?
    @State private var runID = UUID()
    @State private var stopping = false
    @State private var confirmGenerate = false

    init(api: AnteroomAPI, listingID: String) {
        _model = State(initialValue: ListingAssistantModel(api: api, listingID: listingID))
    }

    var body: some View {
        @Bindable var model = model
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    controls
                    ForEach(model.messages) { message in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(message.role == "user" ? "You" : "Anteroom")
                                .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                            Text(message.text).textSelection(.enabled)
                        }
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 10))
                        .accessibilityElement(children: .combine)
                    }
                }
                .padding(16)
            }
            .background(WorkspaceStyle.background)
            .navigationTitle("Listing assistant")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) { composer }
            .task { await model.load() }
            .task(id: runID) {
                if let action { await model.run(action) }
            }
            .confirmationDialog("Generate a listing from your photos?", isPresented: $confirmGenerate, titleVisibility: .visible) {
                Button("Generate listing") { start(.generate) }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This can replace generated listing details. Your photos stay in place. The result is saved as a draft.")
            }
        }
    }

    @ViewBuilder
    private var controls: some View {
        if model.isLoading {
            ProgressView("Opening assistant…")
        } else if !model.ready {
            Button("Try again") { Task { await model.load() } }
        } else if !model.generationAvailable {
            Text("Listing generation isn't available on the server yet. You can continue editing your draft.")
                .foregroundStyle(.secondary)
        } else if !model.providerConfigured {
            Text("Connect an AI provider in the web app's Settings before generating on iPhone.")
                .foregroundStyle(.secondary)
        } else {
            Button("Generate from photos", systemImage: "sparkles") { confirmGenerate = true }
                .buttonStyle(.borderedProminent)
                .disabled(!model.canStart)
                .frame(minHeight: 44)
        }
        if model.isWorking || model.serverBusy || model.needsReconnect {
            VStack(alignment: .leading, spacing: 12) {
                Text(model.progress.isEmpty ? "Checking work…" : model.progress)
                    .font(.subheadline)
                if !model.isWorking && model.needsReconnect {
                    Button("Reconnect", systemImage: "arrow.clockwise") { start(.reconnect) }
                        .disabled(!model.generationAvailable)
                }
                if model.isWorking || model.serverBusy {
                    Button("Stop", role: .destructive) {
                        stopping = true
                        Task { await model.stop(); stopping = false }
                    }
                    .disabled(stopping)
                }
                Text("Work runs on the server. Closing this screen doesn't stop it.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        if !model.isWorking && !model.serverBusy && !model.needsReconnect && !model.progress.isEmpty {
            Label(model.progress, systemImage: "checkmark.circle")
                .font(.footnote).foregroundStyle(.secondary)
        }
        if let error = model.error {
            Label(error, systemImage: "exclamationmark.triangle")
                .font(.footnote).foregroundStyle(.red)
        }
        if model.ready && model.messages.isEmpty && !model.isWorking {
            Text("Add product photos to your listing, then generate a draft or ask for help editing it.")
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private var composer: some View {
        @Bindable var model = model
        VStack(alignment: .leading, spacing: 8) {
            TextField("Ask about this listing", text: $model.text, axis: .vertical)
                .lineLimit(1...6)
                .textFieldStyle(.roundedBorder)
                .accessibilityLabel("Message to listing assistant")
                .disabled(model.isWorking)
            Button("Send", systemImage: "arrow.up.circle.fill") {
                let text = model.text.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !text.isEmpty else { return }
                start(.message(text))
            }
            .frame(minHeight: 44)
            .disabled(!model.canStart || model.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(16)
        .background(.regularMaterial)
    }

    private func start(_ action: AssistantAction) {
        self.action = action
        runID = UUID()
    }
}
