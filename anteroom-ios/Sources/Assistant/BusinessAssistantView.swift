import PhotosUI
import SwiftUI

/// The Assistant tab (AssistantPage.tsx with its chats panel): questions about
/// the business as a whole, apart from any listing's chat.
struct BusinessAssistantView: View {
    let attachmentsAvailable: Bool
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var model: BusinessAssistantModel
    @State private var path = NavigationPath()
    @State private var showingChats = false
    @State private var picked: [PhotosPickerItem] = []
    @State private var following = true

    init(api: AnteroomAPI, attachmentsAvailable: Bool) {
        self.attachmentsAvailable = attachmentsAvailable
        _model = State(initialValue: BusinessAssistantModel(api: api))
    }

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if model.isEmpty {
                    starters
                } else {
                    transcript
                }
            }
            .background(WorkspaceStyle.background)
            .safeAreaInset(edge: .bottom, spacing: 0) { composer }
            .navigationTitle(model.threadTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                // History appears once there is a chat to go back to.
                if !model.threads.isEmpty {
                    ToolbarItem(placement: .topBarLeading) {
                        Button("Chats", systemImage: "clock.arrow.circlepath") { showingChats = true }
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("New chat", systemImage: "square.and.pencil") { Task { await model.open(nil) } }
                        .disabled(model.isEmpty)
                }
            }
            .navigationDestination(for: ConversationSummary.self) { listing in
                ListingEditorView(api: model.api, listing: listing) {}
            }
            .navigationDestination(for: ProvidersDestination.self) { _ in
                ProvidersSettingsView(api: model.api)
            }
            .sheet(isPresented: $showingChats) {
                AssistantChatsSheet(model: model)
            }
            .environment(\.openURL, OpenURLAction { url in
                // Answers link the listings they name as `#listing-<id>`; open those here.
                guard let id = AssistantFormat.linkedListingID(url.absoluteString) else { return .systemAction }
                path.append(ConversationSummary(id: id, title: nil))
                return .handled
            })
            .task { await model.loadThreads() }
            // One haptic and one announcement per answer, never per word (Apple HIG).
            .sensoryFeedback(.success, trigger: model.answersFinished)
            .sensoryFeedback(.error, trigger: model.answersFailed)
            .sensoryFeedback(.impact(weight: .light), trigger: model.answersStopped)
            .onChange(of: model.answersFinished) { AccessibilityNotification.Announcement("Answer finished").post() }
            .onChange(of: model.answersFailed) { AccessibilityNotification.Announcement("The answer stopped with an error").post() }
            // Pick an answer back up after locking the phone or leaving the app.
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { Task { await model.refresh() } }
            }
        }
    }

    // MARK: Empty conversation

    private var starters: some View {
        // The greeting and questions sit centered in the space above the
        // composer, which stays docked at the bottom within thumb reach.
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text(AssistantFormat.greeting())
                        .font(.title2.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .multilineTextAlignment(.center)
                        .padding(.bottom, 8)
                    // A plain list with dividers, as on the web (DESIGN.md).
                    VStack(spacing: 0) {
                        ForEach(Array(AssistantFormat.starters.enumerated()), id: \.offset) { index, starter in
                            if index > 0 { Divider() }
                            Button { model.ask(starter.text) } label: {
                                Label {
                                    Text(starter.text).multilineTextAlignment(.leading)
                                } icon: {
                                    Image(systemName: starter.systemImage).foregroundStyle(.secondary)
                                }
                                .font(.subheadline)
                                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel(starter.text)
                        }
                    }
                    .accessibilityElement(children: .contain)
                    .accessibilityLabel("Example questions")
                    recentChats
                    errorRow
                }
                .padding(16)
                .frame(minHeight: geometry.size.height)
            }
        }
    }

    /// The latest chats to pick back up (Gemini's "Recent"); Show all opens Chats.
    /// iPhone keeps its home to the greeting and questions; the Chats button holds the history.
    @ViewBuilder
    private var recentChats: some View {
        let recent = sizeClass == .regular ? Array(model.threads.prefix(AssistantFormat.recentChats)) : []
        if !recent.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    Text("Recent chats").font(.caption.weight(.medium)).foregroundStyle(.secondary)
                    Spacer()
                    if model.threads.count > recent.count {
                        Button("Show all") { showingChats = true }.font(.caption)
                    }
                }
                .padding(.bottom, 4)
                ForEach(Array(recent.enumerated()), id: \.element.id) { index, thread in
                    if index > 0 { Divider() }
                    Button { Task { await model.open(thread.id) } } label: {
                        HStack(spacing: 12) {
                            Text(thread.title).lineLimit(1).foregroundStyle(.primary)
                            Spacer(minLength: 8)
                            Text(AssistantFormat.chatTime(thread.updatedAt)).font(.caption).foregroundStyle(.secondary)
                        }
                        .font(.subheadline)
                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.top, 8)
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Recent chats")
        }
    }

    // MARK: Conversation

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    if model.loadingTurns && model.turns.isEmpty {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                    ForEach(model.turns) { turn in
                        if turn.role == "user" {
                            UserBubble(text: turn.text, images: turn.attachmentNames)
                        } else {
                            AnswerView(turn: turn, model: model)
                        }
                    }
                    if let live = model.live {
                        let drawn = AssistantFormat.liveParts(live, turns: model.turns)
                        if drawn.question, let question = live.question {
                            UserBubble(text: question, images: live.questionImages)
                        }
                        if drawn.answer {
                            VStack(alignment: .leading, spacing: 8) {
                                if !live.text.isEmpty {
                                    // Drawn as the saved answer will be, so it does not change look when saved.
                                    Text(AnswerView.markdown(live.text))
                                        .tint(WorkspaceStyle.accent)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                                HStack(spacing: 8) {
                                    ProgressView()
                                    Text(model.stopping ? "Stopping…" : live.status.isEmpty ? "Answering…" : live.status)
                                        .font(.footnote).foregroundStyle(.secondary)
                                }
                                .accessibilityElement(children: .combine)
                            }
                        }
                    }
                    ForEach(model.queued) { question in
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Sends when this answer is finished").font(.caption).foregroundStyle(.secondary)
                                Text(question.text)
                            }
                            Spacer()
                            Button("Remove", systemImage: "xmark.circle.fill") { model.removeQueued(question.id) }
                                .labelStyle(.iconOnly).foregroundStyle(.secondary)
                                .frame(minWidth: 44, minHeight: 44)
                        }
                        .padding(12)
                        .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 12))
                    }
                    errorRow
                    Color.clear.frame(height: 1).id(ChatScroll.bottomID)
                }
                .padding(16)
            }
            .scrollDismissesKeyboard(.interactively)
            .followsLatest(proxy, following: $following,
                           on: [model.turns.count, model.live?.text.count ?? -1, model.queued.count, model.error == nil ? 0 : 1])
            // Asking a question always brings the conversation back to its end.
            .onChange(of: model.live?.question) { _, question in
                guard question != nil else { return }
                following = true
                proxy.scrollTo(ChatScroll.bottomID, anchor: .bottom)
            }
        }
    }

    @ViewBuilder
    private var errorRow: some View {
        if let error = model.error {
            VStack(alignment: .leading, spacing: 8) {
                Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                if model.needsProvider {
                    NavigationLink("Open provider settings", value: ProvidersDestination())
                }
            }
            .font(.subheadline)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // MARK: Composer

    private var composer: some View {
        @Bindable var model = model
        return VStack(alignment: .leading, spacing: 8) {
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
                TextField("Ask about sales, inventory or listings…", text: $model.text, axis: .vertical)
                    .lineLimit(1...6)
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 14)
                    .padding(.top, 12)
                    .padding(.bottom, 4)
                    .accessibilityIdentifier("assistant-input")
                HStack(spacing: 4) {
                    if attachmentsAvailable {
                        PhotosPicker(selection: $picked, maxSelectionCount: max(1, 6 - model.attachments.count), matching: .images) {
                            Image(systemName: "paperclip").foregroundStyle(WorkspaceStyle.mutedText)
                        }
                        .frame(minWidth: 44, minHeight: 44)
                        .disabled(model.attachments.count >= 6)
                        .accessibilityLabel("Attach images, \(model.attachments.count) of 6")
                    }
                    Spacer()
                    if model.live != nil {
                        Button(model.stopping ? "Stopping" : "Stop answering", systemImage: "stop.circle.fill") { Task { await model.stop() } }
                            .labelStyle(.iconOnly).font(.title2)
                            .foregroundStyle(WorkspaceStyle.mutedText)
                            .frame(minWidth: 44, minHeight: 44)
                            .disabled(model.stopping)
                    }
                    ComposerSendButton(
                        label: model.busy ? "Send when this answer is finished" : "Send",
                        disabled: model.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && model.attachments.isEmpty
                    ) { model.ask() }
                    .accessibilityIdentifier("assistant-send")
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
}

private struct ProvidersDestination: Hashable {}

private struct UserBubble: View {
    let text: String
    let images: [String]

    var body: some View {
        HStack {
            Spacer(minLength: 48)
            VStack(alignment: .trailing, spacing: 4) {
                if !text.isEmpty { Text(text).textSelection(.enabled) }
                if !images.isEmpty {
                    Label(images.joined(separator: ", "), systemImage: "paperclip").font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(12)
            .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 16))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(WorkspaceStyle.border, lineWidth: 1))
        }
    }
}

/// A saved answer, with any changes or automation it proposed.
private struct AnswerView: View {
    let turn: AssistantTurn
    let model: BusinessAssistantModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(Self.markdown(turn.text))
                .textSelection(.enabled)
                .tint(WorkspaceStyle.accent)
                .frame(maxWidth: .infinity, alignment: .leading)
            if let changes = turn.changes {
                ChangesCard(turn: turn, changes: changes, model: model)
            }
            if let automation = turn.automation {
                AutomationCard(turn: turn, automation: automation, model: model)
            }
        }
    }

    /// Bold, links and line breaks as the answer wrote them.
    static func markdown(_ text: String) -> AttributedString {
        (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(text)
    }
}

/// Changes an answer proposed, each value before and after. Nothing is written until Apply.
private struct ChangesCard: View {
    let turn: AssistantTurn
    let changes: AssistantChanges
    let model: BusinessAssistantModel
    @State private var showAll = false

    /// Listings shown before the rest fold behind "Show all".
    private static let previewRows = 8

    var body: some View {
        let items = showAll ? changes.items : Array(changes.items.prefix(Self.previewRows))
        VStack(alignment: .leading, spacing: 12) {
            Text(AssistantFormat.changesHeading(changes)).font(.subheadline.weight(.semibold))
            ForEach(items) { item in
                VStack(alignment: .leading, spacing: 4) {
                    NavigationLink(value: ConversationSummary(id: item.listingID, title: item.title)) {
                        Text(item.title).font(.subheadline.weight(.medium)).multilineTextAlignment(.leading)
                    }
                    ForEach(item.fields, id: \.field) { change in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(AssistantFormat.fieldLabel(change.field)).foregroundStyle(.secondary)
                            Text(AssistantFormat.changeValue(change.field, change.old)).strikethrough().foregroundStyle(.secondary)
                            Image(systemName: "arrow.right").font(.caption).foregroundStyle(.secondary).accessibilityHidden(true)
                            Text(AssistantFormat.changeValue(change.field, change.new))
                        }
                        .font(.footnote)
                        .accessibilityElement(children: .combine)
                    }
                    if let skipped = item.skipped {
                        Text("Not changed. \(skipped)").font(.footnote).foregroundStyle(.orange)
                    }
                }
            }
            if items.count < changes.items.count {
                Button("Show all \(changes.items.count)") { showAll = true }.font(.subheadline)
            }
            if changes.status == "proposed" {
                Text("Applying saves these in Anteroom. Update the live listing on the marketplace when you want buyers to see them.")
                    .font(.footnote).foregroundStyle(.secondary)
                CardFailure(turn: turn, model: model)
                HStack {
                    Spacer()
                    Button("Dismiss") { Task { await model.settle(turn, .dismissChanges) } }
                        .buttonStyle(.bordered)
                    Button(model.settling == turn.id ? "Applying…" : "Apply") { Task { await model.settle(turn, .apply) } }
                        .workspaceProminentButton()
                }
                .disabled(model.settling != nil)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(WorkspaceStyle.border, lineWidth: 1))
    }
}

/// A task the assistant drafted. Marketplace actions wait until Confirm.
private struct AutomationCard: View {
    let turn: AssistantTurn
    let automation: AssistantAutomation
    let model: BusinessAssistantModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(AssistantFormat.automationHeading(automation)).font(.subheadline.weight(.semibold))
            if let proposal = automation.proposal {
                Text(AssistantFormat.automationSummary(proposal)).font(.footnote).foregroundStyle(.secondary)
                ForEach(proposal.issues ?? [], id: \.self) { issue in
                    Label(issue, systemImage: "exclamationmark.circle").font(.footnote).foregroundStyle(.secondary)
                }
            }
            if automation.status == "proposed" {
                CardFailure(turn: turn, model: model)
                HStack {
                    Button("Confirm") { Task { await model.settle(turn, .confirm) } }
                        .workspaceProminentButton()
                        .disabled(automation.proposal?.ready == false)
                    Button("Dismiss") { Task { await model.settle(turn, .dismissAutomation) } }
                        .buttonStyle(.bordered)
                }
                .disabled(model.settling != nil)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(WorkspaceStyle.border, lineWidth: 1))
    }
}

private struct CardFailure: View {
    let turn: AssistantTurn
    let model: BusinessAssistantModel

    var body: some View {
        if let failure = model.cardError, failure.id == turn.id {
            Text(failure.message).font(.footnote).foregroundStyle(.red)
        }
    }
}

/// The assistant's conversations (AssistantThreadPanel.tsx): search, new chat and delete.
private struct AssistantChatsSheet: View {
    let model: BusinessAssistantModel
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var deleting: AssistantThread?

    var body: some View {
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        let shown = model.threads.filter { needle.isEmpty || $0.title.lowercased().contains(needle) }
        NavigationStack {
            List {
                Group {
                    Button("New chat", systemImage: "plus") {
                        Task { await model.open(nil) }
                        dismiss()
                    }
                    if !model.threadsLoaded {
                        Section { ProgressView() }
                    } else if shown.isEmpty {
                        Section {
                            Text(model.threads.isEmpty ? "No chats yet" : "No matching chats").foregroundStyle(.secondary)
                        }
                    }
                    // Grouped by day, newest first, as ChatGPT and Claude list their history.
                    ForEach(AssistantFormat.groupChats(shown), id: \.group) { section in
                        Section(section.group) {
                            ForEach(section.threads) { thread in
                                Button {
                                    Task { await model.open(thread.id) }
                                    dismiss()
                                } label: {
                                    HStack {
                                        Text(thread.title).foregroundStyle(.primary).lineLimit(2)
                                        Spacer()
                                        if thread.id == model.threadID {
                                            Image(systemName: "checkmark").foregroundStyle(WorkspaceStyle.accent)
                                        } else {
                                            Text(AssistantFormat.chatTime(thread.updatedAt))
                                                .font(.caption).foregroundStyle(.secondary)
                                        }
                                    }
                                }
                                .swipeActions {
                                    Button("Delete", role: .destructive) { deleting = thread }
                                }
                            }
                        }
                    }
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
            .searchable(text: $query, prompt: "Search chats")
            .navigationTitle("Chats")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .refreshable { await model.loadThreads() }
            .confirmationDialog("Delete “\(deleting?.title ?? "")”?",
                                isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                                titleVisibility: .visible) {
                Button("Delete thread", role: .destructive) {
                    if let thread = deleting { Task { await model.delete(thread) } }
                }
            } message: {
                Text("The messages in it are removed. Listings are not.")
            }
        }
    }
}
