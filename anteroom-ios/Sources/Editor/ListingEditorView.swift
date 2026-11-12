import SwiftUI

struct ListingEditorView: View {
    @Environment(\.scenePhase) private var scenePhase
    @Environment(LiveChanges.self) private var live: LiveChanges?
    @Environment(\.dismiss) private var dismiss
    @State private var model: ListingEditorModel
    @State private var confirmDiscard = false
    /// The open assistant and how it opened. An item, not a flag: a sheet
    /// shown from a flag can be built with the values from before the tap.
    @State private var assistant: AssistantOpening?
    /// An Ask chat from the marketplaces sheet, opened in the assistant once that sheet closes.
    @State private var assistantPrompt: String?
    @State private var showingHistory = false
    @State private var showingTemplates = false
    @State private var showingListEverywhere = false
    @State private var showingMarketplaces = false
    @State private var tab = "general"
    @State private var confirmGenerate = false
    private var photoCount: Int { model.photos.count }
    private var photosUploading: Bool { PhotoUploadTracker.shared.isUploading(model.listingID) }
    /// A field to scroll to once its tab is on screen.
    @State private var focusPath: String?
    @State private var saves = 0
    @State private var saveFailures = 0
    /// The live-change count when this editor opened, or when it last reloaded for one.
    @State private var seenLiveChanges: Int?
    private let onSaved: () -> Void
    private let onLeave: (Bool) -> Void

    init(api: AnteroomAPI, listing: ConversationSummary, onLeave: @escaping (Bool) -> Void = { _ in },
         onSaved: @escaping () -> Void) {
        _model = State(initialValue: ListingEditorModel(api: api, listing: listing))
        self.onSaved = onSaved
        self.onLeave = onLeave
    }

    var body: some View {
        editor
            .alert("Unsaved changes", isPresented: draftNoticeShown) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(model.draftNotice ?? "")
            }
            .sensoryFeedback(.success, trigger: saves)
            .sensoryFeedback(.error, trigger: saveFailures)
            // iOS may end the app in the background; keep unsaved edits until it reopens.
            .onChange(of: scenePhase) { _, phase in
                if phase == .background { model.stashEdits() }
            }
            // The editor also closes without Save or Discard: on iPad when the
            // seller opens another listing or the window changes size, and on
            // either device when they switch tabs. Keep the edits until it reopens.
            .onDisappear {
                model.stashEdits()
                onLeave(model.hasChanges)
            }
            // Back on screen with the edits still in memory (from a tab switch or
            // a pushed picker): the copy on disk is no longer needed.
            .onAppear { if model.hasLoaded { model.forgetStash() } }
    }

    private var editor: some View {
        @Bindable var model = model
        return ScrollViewReader { proxy in
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let loadError = model.loadError {
                    WorkspaceSection("Couldn't load listing") {
                        Text("Your saved details weren't changed. Try again in a moment.")
                        Text(loadError).font(.footnote).foregroundStyle(WorkspaceStyle.destructive)
                        Button("Try again") { Task { await model.load() } }
                    }
                }
                // A listing that never loaded has no details to show; blank fields would read as lost ones.
                if model.hasLoaded || model.loadError == nil {
                    HStack(spacing: 8) {
                        formMenu
                        Spacer()
                        // Nothing to report until the seller changes something.
                        if let saveStatus {
                            Text(saveStatus).font(.caption).foregroundStyle(WorkspaceStyle.mutedText)
                        }
                    }
                    .font(.subheadline)
                    .padding(.horizontal, 4)

                    if tab == "general" {
                        ListingInspectorSection("Photos") {
                            PhotoStrip(api: model.api, listingID: model.listingID, photos: $model.photos,
                                       loaded: model.photosLoaded,
                                       cover: model.coverPhotoID.map { (id: $0, version: model.coverPhotoVersion) },
                                       canAdd: model.isDraft)
                            if model.isDraft && photoCount > 0 && model.needsGenerating && model.hasLoaded {
                                generateButton
                            }
                        }
                        GeneralListingFields(form: $model.form, options: model.options, api: model.api, focusPath: focusPath)
                    } else {
                        MarketplaceActionBar(editor: model, marketplace: tab, onFill: { fill($0, proxy: proxy) },
                                             onManage: { showingMarketplaces = true }, onChange: onSaved)
                            .id("\(tab)-\(model.revisionID ?? "")")
                        MarketplaceFormView(model: model, marketplace: tab)
                    }
                }
            }
            .padding(16)
        }
        .onChange(of: focusPath) { _, path in
            guard let path else { return }
            Task {
                // Let the tab's rows draw before scrolling to one.
                try? await Task.sleep(for: .milliseconds(350))
                withAnimation { proxy.scrollTo(FieldAnchor.id(path), anchor: .center) }
                focusPath = nil
            }
        }
        }
        // Room below the last field, clear of the tab bar.
        .contentMargins(.bottom, 32, for: .scrollContent)
        .background(WorkspaceStyle.background)
        .toolbarBackground(WorkspaceStyle.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .scrollDismissesKeyboard(.interactively)
        .disabled(model.isLoading && model.loadError == nil)
        .overlay { if model.isLoading { ProgressView() } }
        .navigationTitle(model.title.isEmpty ? "Listing" : model.title)
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(model.hasChanges)
        .toolbar {
            if model.hasChanges {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { confirmDiscard = true }
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Menu("Listing actions", systemImage: "ellipsis.circle") {
                    Button("Listing assistant", systemImage: "sparkles") { assistant = AssistantOpening() }
                    Button("Listing history", systemImage: "clock.arrow.circlepath") { showingHistory = true }
                    Button("Listing templates", systemImage: "rectangle.stack") { showingTemplates = true }
                    Divider()
                    Button("List everywhere…", systemImage: "paperplane") { showingListEverywhere = true }
                        .disabled(!model.choices.contains(where: \.selectable))
                    Button("Manage marketplaces", systemImage: "storefront") { showingMarketplaces = true }
                }
                .frame(minWidth: 44, minHeight: 44)
                .disabled(model.hasChanges || model.isLoading || model.isSaving || model.loadError != nil)
                .accessibilityHint("Save changes first to open the assistant, listing history or marketplaces")
            }
            ToolbarItem(placement: .topBarTrailing) {
                if model.isSaving {
                    ProgressView()
                } else {
                    Button("Save") { save() }
                        .fontWeight(.semibold)
                        .workspaceProminentButton()
                        .disabled(!model.hasChanges || model.isSaving)
                }
            }
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") { hideKeyboard() }
            }
        }
        .confirmationDialog("Discard your changes?", isPresented: $confirmDiscard, titleVisibility: .visible) {
            Button("Discard changes", role: .destructive) {
                // On iPad the editor is the detail column's root, where dismiss does nothing.
                model.discardChanges()
                dismiss()
            }
        }
        .confirmationDialog("Replace this listing's title?", isPresented: $confirmGenerate, titleVisibility: .visible) {
            Button("Generate listing") { startGenerating() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Generating writes a new title, description and item details from your photos. Your photos, notes and measurements stay. The result is saved as a draft.")
        }
        .alert("Couldn't save", isPresented: saveErrorShown) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.saveError ?? "")
        }
        .alert("Changed on another device", isPresented: $model.hasConflict) {
            Button("Reload", role: .destructive) {
                model.forgetStash()
                Task { await model.load() }
            }
            Button("Keep editing", role: .cancel) {}
        } message: {
            Text("Reloading shows the latest version and drops your edits here.")
        }
        .sheet(item: $assistant, onDismiss: {
            Task {
                guard !model.hasChanges else { return }
                await model.load()
                if model.loadError == nil { onSaved() }
            }
        }) { opening in
            ListingAssistantView(api: model.api, listingID: model.listingID, photoCount: photoCount,
                                 hasCopy: model.hasCopy, generate: opening.generate, text: opening.text) { assistant = nil }
        }
        .sheet(isPresented: $showingHistory, onDismiss: {
            Task {
                guard !model.hasChanges else { return }
                await model.load()
                if model.loadError == nil { onSaved() }
            }
        }) {
            ListingHistoryView(api: model.api, listingID: model.listingID)
        }
        .sheet(isPresented: $showingMarketplaces, onDismiss: {
            // Listing, updating and delisting change the tabs' readiness and the inventory row.
            Task { await model.loadReadiness() }
            onSaved()
            if let prompt = assistantPrompt {
                assistantPrompt = nil
                assistant = AssistantOpening(text: prompt)
            }
        }) {
            MarketplaceActionsView(api: model.api, listingID: model.listingID,
                                   revisionID: model.revisionID, title: model.title) { prompt in
                assistantPrompt = prompt
                showingMarketplaces = false
            }
        }
        .sheet(isPresented: $showingListEverywhere, onDismiss: onSaved) {
            ListEverywhereSheet(model: model) { marketplace, field in
                showingListEverywhere = false
                tab = marketplace
                focusPath = field
            }
        }
        .sheet(isPresented: $showingTemplates) {
            ListingTemplatesSheet(api: model.api, listingID: model.listingID) {
                Task {
                    await model.load()
                    onSaved()
                }
            }
        }
        .task {
            if !model.hasLoaded { await model.load() }
        }
        // Pick up changes from other devices, but never over unsaved edits.
        .onChange(of: scenePhase) { _, phase in
            if phase == .active && !model.hasChanges && !model.isSaving && !model.isLoading { Task { await model.load() } }
        }
        // Edits from another device show up live, unless this one has unsaved
        // changes. The count when the editor opens is already in the first load.
        .task(id: live?.listings) {
            let count = live?.listings ?? 0
            guard let seen = seenLiveChanges else { return seenLiveChanges = count }
            guard count != seen else { return }
            seenLiveChanges = count
            try? await Task.sleep(for: .milliseconds(1500))
            if !Task.isCancelled && !model.hasChanges && !model.isSaving && !model.isLoading { await model.load() }
        }
    }

    /// General and each marketplace form. Each marketplace says its state in
    /// words under its name, so the icons never need a legend.
    private var formMenu: some View {
        Menu {
            tabButton("general", name: "General", status: nil, symbol: "square.and.pencil")
            Section("Marketplaces") {
                ForEach(model.marketplaceTabs, id: \.self) { marketplace in
                    let choice = model.choice(marketplace)
                    tabButton(marketplace, name: Marketplaces.name(marketplace),
                              status: status(choice),
                              symbol: choice?.symbol)
                }
            }
        } label: {
            HStack(spacing: 6) {
                Text(formName).fontWeight(.semibold)
                    .lineLimit(1)
                if let choice = model.choice(tab) { ReadinessChip(choice: choice) }
                Image(systemName: "chevron.down").font(.caption)
            }
            .padding(.horizontal, 12).frame(minHeight: 44)
            .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(WorkspaceStyle.border, lineWidth: 1))
        }
        .disabled(model.marketplaceTabs.isEmpty)
        .accessibilityLabel("Form, \(formName)\(status(model.choice(tab)).map { ", \($0)" } ?? "")")
    }

    private func status(_ choice: ListChoice?) -> String? {
        guard let choice else { return nil }
        return choice.detail.map { "\(choice.label): \($0)" } ?? choice.label
    }

    private var formName: String { tab == "general" ? "General" : Marketplaces.name(tab) }

    /// "Unsaved changes" while editing, otherwise nothing: a disabled Save
    /// already says everything is saved, so a "Saved" label only repeated it.
    private var saveStatus: String? {
        if model.isLoading && model.hasLoaded { return nil }
        if model.loadError != nil && model.hasLoaded { return "Not loaded" }
        return model.hasChanges ? "Unsaved changes" : nil
    }

    /// Generate listing under the photos of a draft with photos and no description
    /// yet. It asks first only when a saved title would be replaced.
    private var generateButton: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                if model.hasCopy { confirmGenerate = true } else { startGenerating() }
            } label: {
                Label("Generate listing", systemImage: "sparkles")
                    .fontWeight(.semibold)
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .workspaceProminentButton()
            .disabled(model.hasChanges || photosUploading)
            .accessibilityIdentifier("generate-listing")
            Text(photosUploading ? "Wait for your photos to finish uploading."
                 : model.hasChanges ? "Save your changes first, then generate."
                 : "Writes the title, description and item details from your photos. You review them before anything is listed.")
                .font(.footnote).foregroundStyle(WorkspaceStyle.mutedText)
        }
    }

    private func startGenerating() {
        assistant = AssistantOpening(generate: true)
    }

    private func tabButton(_ id: String, name: String, status: String?, symbol: String?) -> some View {
        Button {
            tab = id
        } label: {
            Text(name)
            if let status { Text(status) }
            Image(systemName: tab == id ? "checkmark" : symbol ?? "circle.dashed")
        }
        .accessibilityAddTraits(tab == id ? .isSelected : [])
    }

    /// Shows a field a marketplace still needs: on this tab when its form has
    /// it, else on General, where the shared fields live.
    private func fill(_ path: String, proxy: ScrollViewProxy) {
        if tab != "general", model.forms[tab]?.fields.contains(where: { $0.path == path }) != true,
           FieldAnchor.general.contains(path) {
            tab = "general"
        }
        focusPath = path
    }

    private var draftNoticeShown: Binding<Bool> {
        Binding(get: { model.draftNotice != nil }, set: { if !$0 { model.draftNotice = nil } })
    }

    private var saveErrorShown: Binding<Bool> {
        Binding(get: { model.saveError != nil }, set: { if !$0 { model.saveError = nil } })
    }

    private func save() {
        hideKeyboard()
        Task {
            if await model.save() {
                saves += 1
                onSaved()
            } else {
                saveFailures += 1
            }
        }
    }

    private func hideKeyboard() {
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
    }
}

/// A labeled text field. The label sits above the box, so the placeholder
/// gives an example rather than repeating the label.
struct TextRow: View {
    let label: String
    @Binding var text: String
    var placeholder = ""

    init(_ label: String, text: Binding<String>, placeholder: String = "") {
        self.label = label
        _text = text
        self.placeholder = placeholder
    }

    var body: some View {
        WorkspaceField(label) {
            TextField(placeholder, text: $text, axis: .vertical).lineLimit(1...4)
        }
    }
}

struct NumberField: View {
    let label: String
    @Binding var text: String
    var prefix: String?
    /// Words, not a "0" that reads as a value already entered.
    var placeholder = ""

    init(_ label: String, text: Binding<String>, prefix: String? = nil, placeholder: String = "") {
        self.label = label
        _text = text
        self.prefix = prefix
        self.placeholder = placeholder
    }

    var body: some View {
        WorkspaceField(label) {
            HStack(spacing: 2) {
                if let prefix, !text.isEmpty {
                    Text(prefix).foregroundStyle(.secondary)
                }
                TextField(placeholder, text: $text)
                    .accessibilityLabel(label)
                    .keyboardType(.decimalPad)
            }
        }
    }
}

/// How the listing assistant opens: plainly, generating straight away, or
/// with an Ask chat prompt in the composer.
struct AssistantOpening: Identifiable {
    let id = UUID()
    var generate = false
    var text = ""
}
