import PhotosUI
import SwiftUI

struct ListingAssistantView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var model: ListingAssistantModel
    @State private var action: AssistantAction?
    @State private var runID = UUID()
    @State private var stopping = false
    @State private var confirmGenerate = false
    @State private var confirmRegenerate = false
    @State private var picked: [PhotosPickerItem] = []
    @State private var following = true

    /// `text` starts the composer, e.g. an Ask chat about a marketplace refusal; the seller sends it.
    init(api: AnteroomAPI, listingID: String, text: String = "") {
        _model = State(initialValue: ListingAssistantModel(api: api, listingID: listingID, text: text))
    }

    var body: some View {
        @Bindable var model = model
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 16) {
                        controls
                        ForEach(model.messages) { message in
                            VStack(alignment: .leading, spacing: 6) {
                                Text(message.role == "user" ? "You" : "Anteroom")
                                    .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                                Text(message.text).textSelection(.enabled)
                                if message.attachmentCount > 0 {
                                    Label(message.attachmentCount == 1 ? "1 image attached" : "\(message.attachmentCount) images attached",
                                          systemImage: "paperclip")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            .padding(16)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 10))
                            .accessibilityElement(children: .combine)
                        }
                        Color.clear.frame(height: 1).id(ChatScroll.bottomID)
                    }
                    .padding(16)
                }
                .scrollDismissesKeyboard(.interactively)
                .followsLatest(proxy, following: $following, on: model.messages.count)
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
            .sensoryFeedback(.success, trigger: model.runsFinished)
            .sensoryFeedback(.error, trigger: model.runsFailed)
            .sensoryFeedback(.impact(weight: .light), trigger: model.runsStopped)
            .onChange(of: model.runsFinished) { AccessibilityNotification.Announcement("Work finished. Review the saved draft.").post() }
            .onChange(of: model.runsFailed) { AccessibilityNotification.Announcement("The assistant stopped with an error").post() }
            .reloadingOnReturn(when: !model.ready && model.error != nil) { await model.load() }
            .task(id: runID) {
                if let action { await model.run(action) }
            }
            .confirmationDialog("Regenerate this listing?", isPresented: $confirmRegenerate, titleVisibility: .visible) {
                Button("Regenerate", role: .destructive) { start(.regenerate) }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Rewrites the generated listing from your photos and item details. Photos, measurements, flaws, cost, labels and notes stay. The result is saved as a draft.")
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
            Text("Add an AI provider in Settings → Providers before generating.")
                .foregroundStyle(.secondary)
        } else {
            HStack(spacing: 12) {
                Button("Generate from photos", systemImage: "sparkles") { confirmGenerate = true }
                    .workspaceProminentButton()
                Button("Regenerate", systemImage: "arrow.triangle.2.circlepath") { confirmRegenerate = true }
                    .buttonStyle(.bordered)
            }
            .disabled(!model.canStart)
            .frame(minHeight: 44)
        }
        if model.ready && model.messages.isEmpty && !model.isWorking {
            Text("Add product photos to your listing, then generate a draft or ask for help editing it.")
                .foregroundStyle(.secondary)
        }
    }

    /// What the assistant is doing, with Stop and Reconnect, docked above the composer
    /// where it stays in view and in reach however far the conversation scrolls.
    @ViewBuilder
    private var progressBar: some View {
        if model.isWorking || model.serverBusy || model.needsReconnect {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    if model.isWorking || model.serverBusy { ProgressView() }
                    Text(stopping ? "Stopping…" : model.progress.isEmpty ? "Checking work…" : model.progress)
                        .font(.subheadline)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if !model.isWorking && model.needsReconnect {
                        Button("Reconnect", systemImage: "arrow.clockwise") { start(.reconnect) }
                            .disabled(!model.generationAvailable)
                            .frame(minHeight: 44)
                    }
                    if model.isWorking || model.serverBusy {
                        Button("Stop", role: .destructive) {
                            stopping = true
                            Task { await model.stop(); stopping = false }
                        }
                        .buttonStyle(.bordered)
                        .frame(minHeight: 44)
                        .disabled(stopping)
                    }
                }
                Text("Work runs on the server. Closing this screen doesn't stop it.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            .accessibilityElement(children: .contain)
        }
        if !model.isWorking && !model.serverBusy && !model.needsReconnect && !model.progress.isEmpty {
            Label(model.progress, systemImage: "checkmark.circle")
                .font(.footnote).foregroundStyle(.secondary)
        }
        if let error = model.error {
            Label(error, systemImage: "exclamationmark.triangle")
                .font(.footnote).foregroundStyle(.red)
        }
    }

    @ViewBuilder
    private var composer: some View {
        @Bindable var model = model
        VStack(alignment: .leading, spacing: 8) {
            progressBar
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
        // Starting work brings the conversation back to its end, to show the result.
        following = true
        self.action = action
        runID = UUID()
    }
}
