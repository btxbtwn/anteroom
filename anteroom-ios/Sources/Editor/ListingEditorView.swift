import SwiftUI

struct ListingEditorView: View {
    @Environment(\.scenePhase) private var scenePhase
    @Environment(LiveChanges.self) private var live: LiveChanges?
    @Environment(\.dismiss) private var dismiss
    @State private var model: ListingEditorModel
    @State private var confirmDiscard = false
    @State private var showingAssistant = false
    @State private var showingHistory = false
    @State private var showingTemplates = false
    @State private var showingListEverywhere = false
    @State private var showingMarketplaces = false
    @State private var tab = "general"
    @State private var saves = 0
    @State private var saveFailures = 0
    private let onSaved: () -> Void

    init(api: AnteroomAPI, listing: ListingSummary, onSaved: @escaping () -> Void) {
        _model = State(initialValue: ListingEditorModel(api: api, listing: listing))
        self.onSaved = onSaved
    }

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let loadError = model.loadError {
                    WorkspaceSection("Couldn't load listing") {
                        Text("Your saved details weren't changed. Try again in a moment.")
                        Text(loadError).font(.footnote).foregroundStyle(.red)
                        Button("Try again") { Task { await model.load() } }
                    }
                }
                // A listing that never loaded has no details to show; blank fields would read as lost ones.
                if model.hasLoaded || model.loadError == nil {
                    HStack(spacing: 8) {
                        Menu {
                            Picker("Form", selection: $tab) {
                                Label("General", systemImage: "square.and.pencil").tag("general")
                                ForEach(model.marketplaceTabs, id: \.self) { marketplace in
                                    if let choice = model.choice(marketplace) {
                                        Label {
                                            Text(Marketplaces.name(marketplace))
                                            Text(choice.detail.map { "\(choice.label): \($0)" } ?? choice.label)
                                        } icon: {
                                            Image(systemName: choice.symbol)
                                        }
                                        .tag(marketplace)
                                    } else {
                                        Text(Marketplaces.name(marketplace)).tag(marketplace)
                                    }
                                }
                            }
                        } label: {
                            HStack(spacing: 6) {
                                Text(tab == "general" ? "General" : Marketplaces.name(tab)).fontWeight(.semibold)
                                if let choice = model.choice(tab) { ReadinessChip(choice: choice) }
                                Image(systemName: "chevron.down").font(.caption)
                            }
                            .padding(.horizontal, 12).frame(minHeight: 44)
                            .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 8))
                            .overlay(RoundedRectangle(cornerRadius: 8).stroke(WorkspaceStyle.border, lineWidth: 1))
                        }
                        .disabled(model.marketplaceTabs.isEmpty)
                        .accessibilityLabel("Form, \(tab == "general" ? "General" : Marketplaces.name(tab))\(model.choice(tab).map { ", \($0.label)\($0.detail.map { ": \($0)" } ?? "")" } ?? "")")
                        Spacer()
                        Text(model.isLoading ? "Loading…" : model.loadError != nil ? "Not loaded" : model.hasChanges ? "Unsaved changes" : "Saved")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .font(.subheadline)
                    .padding(.horizontal, 4)

                    if tab == "general" {
                        ListingInspectorSection("Photos") {
                            PhotoStrip(api: model.api, listingID: model.listingID, canAdd: model.isDraft)
                        }
                        SellerInputFields(inputs: $model.form.sellerInputs)
                        GeneralListingFields(form: $model.form, options: model.options, api: model.api)
                    } else {
                        MarketplaceFormView(model: model, marketplace: tab)
                    }
                }
            }
            .padding(16)
        }
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
                    Button("Listing assistant", systemImage: "sparkles") { showingAssistant = true }
                    Button("Listing history", systemImage: "clock.arrow.circlepath") { showingHistory = true }
                    Button("Listing templates", systemImage: "rectangle.stack") { showingTemplates = true }
                    Divider()
                    Button("List everywhere…", systemImage: "paperplane") { showingListEverywhere = true }
                        .disabled(!model.choices.contains(where: \.selectable))
                    Button("Manage marketplaces…", systemImage: "storefront") { showingMarketplaces = true }
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
                        .buttonStyle(.borderedProminent)
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
                model.forgetStash()
                dismiss()
            }
        }
        .alert("Couldn't save", isPresented: saveErrorShown) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.saveError ?? "")
        }
        .alert("Unsaved changes", isPresented: draftNoticeShown) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.draftNotice ?? "")
        }
        .sensoryFeedback(.success, trigger: saves)
        .sensoryFeedback(.error, trigger: saveFailures)
        .alert("Changed on another device", isPresented: $model.hasConflict) {
            Button("Reload", role: .destructive) {
                model.forgetStash()
                Task { await model.load() }
            }
            Button("Keep editing", role: .cancel) {}
        } message: {
            Text("Reloading shows the latest version and drops your edits here.")
        }
        .sheet(isPresented: $showingAssistant, onDismiss: {
            Task {
                guard !model.hasChanges else { return }
                await model.load()
                if model.loadError == nil { onSaved() }
            }
        }) {
            ListingAssistantView(api: model.api, listingID: model.listingID)
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
        }) {
            MarketplaceActionsView(api: model.api, listingID: model.listingID,
                                   revisionID: model.revisionID, title: model.title)
        }
        .sheet(isPresented: $showingListEverywhere, onDismiss: onSaved) {
            ListEverywhereSheet(model: model)
        }
        .sheet(isPresented: $showingTemplates) {
            ListingTemplatesSheet(api: model.api, listingID: model.listingID) {
                Task {
                    await model.load()
                    onSaved()
                }
            }
        }
        .task { await model.load() }
        // Pick up changes from other devices, but never over unsaved edits.
        .onChange(of: scenePhase) { _, phase in
            if phase == .active && !model.hasChanges && !model.isSaving && !model.isLoading { Task { await model.load() } }
            // iOS may end the app in the background; keep unsaved edits until it reopens.
            if phase == .background { model.stashEdits() }
        }
        // Edits from another device show up live, unless this one has unsaved changes.
        .task(id: live?.listings) {
            guard (live?.listings ?? 0) > 0 else { return }
            try? await Task.sleep(for: .milliseconds(1500))
            if !Task.isCancelled && !model.hasChanges && !model.isSaving && !model.isLoading { await model.load() }
        }
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

struct TextRow: View {
    let label: String
    @Binding var text: String

    init(_ label: String, text: Binding<String>) {
        self.label = label
        _text = text
    }

    var body: some View {
        WorkspaceField(label) {
            TextField(label, text: $text, axis: .vertical).lineLimit(1...4)
        }
    }
}

struct NumberField: View {
    let label: String
    @Binding var text: String
    var prefix: String?

    init(_ label: String, text: Binding<String>, prefix: String? = nil) {
        self.label = label
        _text = text
        self.prefix = prefix
    }

    var body: some View {
        WorkspaceField(label) {
            HStack(spacing: 2) {
                if let prefix, !text.isEmpty {
                    Text(prefix).foregroundStyle(.secondary)
                }
                TextField("0", text: $text)
                    .accessibilityLabel(label)
                    .keyboardType(.decimalPad)
            }
        }
    }
}
