import Foundation
import Observation

@MainActor
@Observable
final class ListingAssistantModel {
    let api: AnteroomAPI
    let listingID: String
    private(set) var messages: [ListingMessage] = []
    private(set) var isLoading = false
    private(set) var isWorking = false
    private(set) var serverBusy = false
    private(set) var ready = false
    private(set) var generationAvailable = false
    private(set) var providerConfigured = false
    private(set) var progress = ""
    private(set) var needsReconnect = false
    var error: String?
    var text = ""
    var attachments: [ChatAttachment] = []
    private(set) var attachmentsAvailable = false

    init(api: AnteroomAPI, listingID: String) {
        self.api = api
        self.listingID = listingID
    }

    var canStart: Bool {
        ready && generationAvailable && providerConfigured && !isLoading && !isWorking && !serverBusy && !needsReconnect
    }

    func load() async {
        guard !isWorking else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            async let capabilities = api.capabilities()
            async let status = api.workspaceStatus()
            async let transcript = api.messages(listingID)
            async let activity = api.activity(listingID)
            let values = try await (capabilities, status, transcript, activity)
            generationAvailable = values.0.features.contains("generation")
            attachmentsAvailable = values.0.features.contains("chat_attachments")
            providerConfigured = values.1.providerConfigured
            messages = values.2.filter { $0.role == "user" || $0.role == "assistant" }
            serverBusy = values.3.busy
            needsReconnect = serverBusy
            progress = values.3.items.joined(separator: " · ")
            ready = true
            error = nil
        } catch is CancellationError {
        } catch {
            self.error = error.localizedDescription
        }
    }

    func run(_ action: AssistantAction) async {
        guard !isWorking, !isLoading else { return }
        if case .reconnect = action {
            guard ready, generationAvailable else { return }
        } else {
            guard canStart else { return }
        }
        isWorking = true
        progress = "Connecting…"
        error = nil
        defer { isWorking = false }
        do {
            if case .regenerate = action {
                progress = "Clearing the generated listing…"
                try await api.resetListing(listingID, keepInputs: true)
            }
            try await api.runAssistant(listingID, action: action) { [weak self] frame in
                if frame.event == "status" { self?.progress = frame.data }
            }
            if case .message = action {
                text = ""
                attachments = []
            }
            async let transcript = api.messages(listingID)
            async let activity = api.activity(listingID)
            let values = try await (transcript, activity)
            messages = values.0.filter { $0.role == "user" || $0.role == "assistant" }
            serverBusy = values.1.busy
            needsReconnect = serverBusy
            progress = serverBusy ? values.1.items.joined(separator: " · ") : "Work finished. Review the saved draft."
        } catch is CancellationError {
            needsReconnect = true
            progress = "Reconnect to check the saved draft."
        } catch {
            self.error = error.localizedDescription
            // A failed connection can happen after the server accepted work.
            // Require an explicit reconnect instead of submitting it twice.
            needsReconnect = true
        }
    }

    func stop() async {
        do {
            try await api.stopTask(listingID)
            progress = "Stop requested. Reconnect to check the draft."
            needsReconnect = true
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Images go as JPEGs no larger than the listing photos, up to six per message.
    func attach(_ data: Data) async {
        guard attachments.count < 6 else { return }
        let prepared = await Task.detached(priority: .userInitiated) { () -> (Data, Data)? in
            guard let jpeg = PhotoPreparer.jpeg(from: data),
                  let thumb = PhotoPreparer.thumbnail(from: jpeg, maxPixels: 160)?.jpegData(compressionQuality: 0.7) else { return nil }
            return (jpeg, thumb)
        }.value
        guard let (jpeg, thumb) = prepared else {
            error = "That image couldn't be opened."
            return
        }
        attachments.append(ChatAttachment(name: "image-\(attachments.count + 1).jpg",
                                          dataURL: "data:image/jpeg;base64," + jpeg.base64EncodedString(), preview: thumb))
    }
}
