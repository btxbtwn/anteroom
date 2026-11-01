import Foundation
import Observation

/// The business assistant (AssistantPage.tsx): one conversation at a time,
/// answered from all of the shop's data. An answer may propose listing changes
/// or draft an automation; nothing is written until the seller applies or
/// confirms it, and nothing here publishes a listing.
@MainActor
@Observable
final class BusinessAssistantModel {
    let api: AnteroomAPI
    private(set) var threads: [AssistantThread] = []
    private(set) var threadsLoaded = false
    private(set) var threadID: String?
    private(set) var turns: [AssistantTurn] = []
    private(set) var loadingTurns = false
    private(set) var live: AssistantLiveTurn?
    private(set) var queued: [AssistantQueuedQuestion] = []
    /// The card whose Apply, Dismiss or Confirm is in flight.
    private(set) var settling: String?
    var error: String?
    var cardError: (id: String, message: String)?
    var text = ""
    var attachments: [ChatAttachment] = []

    /// The conversation the answer being followed belongs to.
    private var followThread: String?
    private var follow: Task<Void, Never>?

    /// How often, and how many times, a dropped connection is retried before saying so.
    private static let reconnectDelay: Duration = .milliseconds(1500)
    private static let reconnectTries = 8

    init(api: AnteroomAPI) { self.api = api }

    var busy: Bool { live != nil || follow != nil }
    var threadTitle: String { threads.first { $0.id == threadID }?.title ?? "Assistant" }
    var needsProvider: Bool { error?.range(of: "sign in with chatgpt", options: .caseInsensitive) != nil }
    var isEmpty: Bool { threadID == nil && live == nil && turns.isEmpty }

    func loadThreads() async {
        do {
            threads = try await api.assistantThreads()
            threadsLoaded = true
        } catch is CancellationError {
        } catch {
            if !threadsLoaded { self.error = error.localizedDescription }
        }
    }

    /// Open a conversation, or nil for a new one, and pick up an answer still being written in it.
    func open(_ id: String?) async {
        guard id != threadID else { return }
        if followThread != id {
            follow?.cancel()
            follow = nil
            live = nil
            followThread = nil
        }
        // Follow-ups wait for the conversation they were asked in.
        queued.removeAll { $0.threadID != id }
        threadID = id
        turns = []
        error = nil
        guard let id else { return }
        await reloadTurns(id, showLoading: true)
        resumeIfIdle()
    }

    /// The app came back, or the screen reappeared: catch up and reattach.
    func refresh() async {
        await loadThreads()
        guard let threadID else { return }
        await reloadTurns(threadID, showLoading: false)
        if error == AssistantFormat.lostConnection { error = nil }
        resumeIfIdle()
    }

    private func resumeIfIdle() {
        guard let threadID, follow == nil, live == nil else { return }
        start(question: nil, attachments: [], thread: threadID)
    }

    private func reloadTurns(_ id: String, showLoading: Bool) async {
        if showLoading { loadingTurns = true }
        defer { if showLoading { loadingTurns = false } }
        do {
            let saved = try await api.assistantMessages(thread: id)
            if threadID == id { turns = saved }
        } catch is CancellationError {
        } catch {
            if threadID == id { self.error = error.localizedDescription }
        }
    }

    func ask(_ raw: String? = nil) {
        let question = (raw ?? text).trimmingCharacters(in: .whitespacesAndNewlines)
        let images = raw == nil ? attachments : []
        guard !question.isEmpty || !images.isEmpty else { return }
        if raw == nil {
            text = ""
            attachments = []
        }
        guard AssistantFormat.sendsNow(busy: busy, queued: queued.count) else {
            queued.append(AssistantQueuedQuestion(text: question, attachments: images, threadID: followThread ?? threadID))
            return
        }
        // Shown at once, so a second tap holds its question instead of sending twice.
        showAsked(question, images)
        Task { await send(question, images, thread: threadID) }
    }

    private func showAsked(_ question: String, _ images: [ChatAttachment]) {
        error = nil
        live = AssistantLiveTurn(question: question, questionImages: images.map(\.name), asked: turns.count, status: "Reading your shop…")
    }

    func removeQueued(_ id: UUID) { queued.removeAll { $0.id == id } }

    private func send(_ question: String, _ images: [ChatAttachment], thread: String?) async {
        var id = thread
        if id == nil {
            do {
                let created = try await api.createAssistantThread()
                id = created.id
                threads.insert(created, at: 0)
                threadID = created.id
                turns = []
            } catch {
                live = nil
                self.error = error.localizedDescription
                if text.isEmpty { text = question }
                sendNextQueued()
                return
            }
        }
        guard let id else { return }
        start(question: question, attachments: images, thread: id)
    }

    private func start(question: String?, attachments images: [ChatAttachment], thread: String) {
        guard follow == nil else { return }
        followThread = thread
        let asked = live?.asked ?? turns.count
        follow = Task { [weak self] in
            await self?.run(question: question, attachments: images, thread: thread, asked: asked)
        }
    }

    private func run(question: String?, attachments images: [ChatAttachment], thread: String, asked: Int) async {
        var asking = question != nil
        var dropped = false
        var tries = 0
        while true {
            // A resumed answer is replayed from its first word.
            if var current = live {
                current.text = ""
                live = current
            }
            let receive: @MainActor @Sendable (StreamFrame) -> Void = { [weak self] frame in
                self?.receive(frame, thread: thread, asked: asked)
            }
            do {
                let failure: String?
                if asking, let question {
                    failure = try await api.askAssistant(question, attachments: images, thread: thread, receive: receive)
                } else {
                    failure = try await api.resumeAssistant(thread: thread, receive: receive)
                }
                if let failure, threadID == thread { error = failure }
                break
            } catch is CancellationError {
                break
            } catch let refused as APIError where refused.status > 0 {
                if threadID == thread {
                    error = refused.message
                    if asking, let question, text.isEmpty { text = question }
                }
                break
            } catch {
                if Task.isCancelled { break }
                asking = false
                dropped = true
                tries += 1
                if tries > Self.reconnectTries {
                    if threadID == thread { self.error = AssistantFormat.lostConnection }
                    break
                }
                if question != nil, var current = live {
                    current.status = "Reconnecting…"
                    live = current
                }
                try? await Task.sleep(for: Self.reconnectDelay)
                if Task.isCancelled { break }
            }
        }
        // Show the saved turns before dropping the live one, so the answer does not blink.
        if threadID == thread {
            await reloadTurns(thread, showLoading: false)
            if let question, dropped, !Task.isCancelled,
               !turns.contains(where: { $0.role == "user" && $0.text == question }) {
                // The connection may have dropped before the server received the question.
                error = "Could not reach Anteroom. Check the connection and ask again."
                if text.isEmpty { text = question }
            }
        }
        await loadThreads()
        guard followThread == thread, !Task.isCancelled else { return }
        follow = nil
        live = nil
        followThread = nil
        sendNextQueued()
    }

    private func receive(_ frame: StreamFrame, thread: String, asked: Int) {
        guard followThread == thread else { return }
        var turn = live ?? AssistantLiveTurn(question: nil, asked: asked)
        switch frame.event {
        case "message": turn.text += frame.data
        case "status": turn.status = frame.data
        case "thinking": turn.status = "Thinking…"
        default: break
        }
        live = turn
    }

    /// Send the oldest held follow-up once the answer before it has ended.
    private func sendNextQueued() {
        guard follow == nil, !queued.isEmpty else { return }
        let next = queued.removeFirst()
        showAsked(next.text, next.attachments)
        Task { await send(next.text, next.attachments, thread: next.threadID ?? threadID) }
    }

    func stop() async {
        try? await api.stopAssistant()
        follow?.cancel()
        follow = nil
        live = nil
        followThread = nil
        if let threadID { await reloadTurns(threadID, showLoading: false) }
        sendNextQueued()
    }

    func delete(_ thread: AssistantThread) async {
        do {
            try await api.deleteAssistantThread(thread.id)
            threads.removeAll { $0.id == thread.id }
            if threadID == thread.id { await open(nil) }
        } catch {
            self.error = error.localizedDescription
        }
    }

    enum CardAction { case apply, dismissChanges, confirm, dismissAutomation }

    func settle(_ turn: AssistantTurn, _ action: CardAction) async {
        settling = turn.id
        cardError = nil
        defer { settling = nil }
        do {
            let saved: AssistantTurn
            switch action {
            case .apply: saved = try await api.applyAssistantChanges(turn.id)
            case .dismissChanges: saved = try await api.dismissAssistantChanges(turn.id)
            case .confirm: saved = try await api.confirmAssistantAutomation(turn.id)
            case .dismissAutomation: saved = try await api.dismissAssistantAutomation(turn.id)
            }
            if let index = turns.firstIndex(where: { $0.id == turn.id }) { turns[index] = saved }
        } catch {
            cardError = (turn.id, error.localizedDescription)
        }
    }

    /// Images go as JPEGs no larger than the listing photos, up to six per question.
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
