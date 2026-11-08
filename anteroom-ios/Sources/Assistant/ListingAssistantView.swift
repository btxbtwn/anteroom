import PhotosUI
import SwiftUI

struct ListingAssistantView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var model: ListingAssistantModel
    @State private var action: AssistantAction?
    @State private var runID = UUID()
    @State private var stopping = false
    @State private var confirmRegenerate = false
    @State private var picked: [PhotosPickerItem] = []
    /// The listing has a title or description, so generating again replaces copy.
    @State private var hasCopy: Bool
    @State private var autoGenerate: Bool
    private let photoCount: Int

    /// `text` starts the composer, e.g. an Ask chat about a marketplace refusal; the seller sends it.
    /// `generate` starts generating as soon as the assistant is ready: the
    /// editor's Generate listing button has already asked when it had to.
    init(api: AnteroomAPI, listingID: String, photoCount: Int, hasCopy: Bool, generate: Bool = false, text: String = "") {
        _model = State(initialValue: ListingAssistantModel(api: api, listingID: listingID, text: text))
        _hasCopy = State(initialValue: hasCopy)
        _autoGenerate = State(initialValue: generate)
        self.photoCount = photoCount
    }

    var body: some View {
        @Bindable var model = model
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    controls
                    ForEach(model.messages) { message in
                        let reply = message.role == "user" ? nil : AssistantReply(message.text)
                        if reply?.text.isEmpty != true || message.attachmentCount > 0 {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(message.role == "user" ? "You" : "Anteroom")
                                .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                            if let reply, reply.isListing {
                                Label(reply.text, systemImage: "checkmark.circle")
                                Button("Review in editor") { dismiss() }
                                    .buttonStyle(.bordered)
                                    .frame(minHeight: 44)
                                    .accessibilityHint("Closes the assistant and shows the saved draft")
                            } else {
                                Text(reply?.text ?? message.text).textSelection(.enabled)
                            }
                            if message.attachmentCount > 0 {
                                Label(message.attachmentCount == 1 ? "1 image attached" : "\(message.attachmentCount) images attached",
                                      systemImage: "paperclip")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 10))
                        .accessibilityElement(children: .contain)
                        }
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
            .task {
                await model.load()
                if autoGenerate && model.canStart && photoCount > 0 { start(.generate) }
                autoGenerate = false
            }
            .reloadingOnReturn(when: !model.ready && model.error != nil) { await model.load() }
            .task(id: runID) {
                guard let action else { return }
                await model.run(action)
                if model.messages.contains(where: { $0.role == "assistant" && AssistantReply($0.text).isListing }) { hasCopy = true }
            }
            .confirmationDialog("Regenerate this listing?", isPresented: $confirmRegenerate, titleVisibility: .visible) {
                Button("Regenerate", role: .destructive) { start(.regenerate) }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Rewrites the generated listing from your photos and item details. Photos, measurements, flaws, cost, labels and notes stay. The result is saved as a draft.")
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
            Text("Add an AI provider in Settings → Providers before generating.")
                .foregroundStyle(.secondary)
        } else {
            generateButton.disabled(!model.canStart)
        }
        if model.isWorking || model.serverBusy || model.needsReconnect {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    if model.isWorking || model.serverBusy { ProgressView() }
                    Text(model.progress.isEmpty ? "Checking work…" : model.progress)
                        .font(.subheadline)
                }
                .accessibilityElement(children: .combine)
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
                .font(.footnote).foregroundStyle(WorkspaceStyle.destructive)
        }
        if model.ready && model.messages.isEmpty && !model.isWorking {
            Text(Self.emptyText(photoCount: photoCount, hasCopy: hasCopy))
                .foregroundStyle(.secondary)
        }
    }

    /// Generate listing until there is copy; then Regenerate (clear, then generate), which asks first.
    @ViewBuilder
    private var generateButton: some View {
        if hasCopy {
            Button("Regenerate", systemImage: "arrow.triangle.2.circlepath") { confirmRegenerate = true }
                .buttonStyle(.bordered)
                .frame(minHeight: 44)
        } else {
            Button("Generate listing", systemImage: "sparkles") { start(.generate) }
                .workspaceProminentButton()
                .frame(minHeight: 44)
                .disabled(photoCount == 0)
        }
    }

    static func emptyText(photoCount: Int, hasCopy: Bool) -> String {
        if photoCount == 0 { return "Add photos of your item in the editor, then generate a listing from them. You can also ask a question here." }
        if hasCopy { return "Ask for a change, like a shorter title or a lower price, and Anteroom saves it to the draft." }
        return "Generate a listing from your \(photoCount == 1 ? "photo" : "\(photoCount) photos"), or ask a question about this item."
    }

    @ViewBuilder
    private var composer: some View {
        @Bindable var model = model
        VStack(alignment: .leading, spacing: 8) {
            if !model.attachments.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(model.attachments) { attachment in
                            ZStack(alignment: .topTrailing) {
                                if let image = UIImage(data: attachment.preview) {
                                    Image(uiImage: image).resizable().scaledToFill()
                                        .frame(width: 56, height: 56).clipShape(RoundedRectangle(cornerRadius: 6))
                                }
                                Button("Remove image", systemImage: "xmark.circle.fill") {
                                    model.attachments.removeAll { $0.id == attachment.id }
                                }
                                .labelStyle(.iconOnly)
                                .foregroundStyle(.white, .black.opacity(0.6))
                                .frame(width: 32, height: 32)
                            }
                        }
                    }
                }
            }
            // The web's composer: the message on top, its tools along the bottom.
            VStack(alignment: .leading, spacing: 0) {
                TextField("Ask about this listing", text: $model.text, axis: .vertical)
                    .lineLimit(1...6)
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 14)
                    .padding(.top, 12)
                    .padding(.bottom, 4)
                    .accessibilityLabel("Message to listing assistant")
                    .disabled(model.isWorking)
                HStack(spacing: 4) {
                    if model.attachmentsAvailable {
                        PhotosPicker(selection: $picked, maxSelectionCount: max(1, 6 - model.attachments.count), matching: .images) {
                            Image(systemName: "paperclip").foregroundStyle(WorkspaceStyle.mutedText)
                        }
                        .frame(minWidth: 44, minHeight: 44)
                        .disabled(model.isWorking || model.attachments.count >= 6)
                        .accessibilityLabel("Attach images, \(model.attachments.count) of 6")
                    }
                    Spacer()
                    ComposerSendButton(
                        label: "Send",
                        disabled: !model.canStart || (model.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && model.attachments.isEmpty)
                    ) {
                        let text = model.text.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !text.isEmpty || !model.attachments.isEmpty else { return }
                        start(.message(text, model.attachments))
                    }
                }
                .padding(.horizontal, 6)
                .padding(.bottom, 4)
            }
            .workspaceComposer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .workspaceComposerBar()
        .onChange(of: picked) { _, items in
            guard !items.isEmpty else { return }
            picked = []
            Task {
                for item in items {
                    if let data = try? await item.loadTransferable(type: Data.self) { await model.attach(data) }
                }
            }
        }
    }

    private func start(_ action: AssistantAction) {
        self.action = action
        runID = UUID()
    }
}
