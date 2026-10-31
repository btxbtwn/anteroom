import SwiftUI

struct InventoryView: View {
    @State private var model: InventoryModel
    @State private var search = ""
    @State private var path: [Draft] = []
    @State private var showingNewListing = false
    @State private var createdDraft: Draft?

    init(api: AnteroomAPI) {
        _model = State(initialValue: InventoryModel(api: api))
    }

    var body: some View {
        NavigationStack(path: $path) {
            List {
                Section {
                    ForEach(model.drafts) { draft in
                        NavigationLink(value: draft) {
                            DraftRow(api: model.api, draft: draft)
                        }
                        .listRowSeparator(.hidden)
                        .listRowBackground(Color.clear)
                        .listRowInsets(EdgeInsets(top: 2, leading: 16, bottom: 2, trailing: 16))
                    }
                    if model.canLoadMore {
                        Button("Load more") {
                            Task { await model.loadMore(search: search) }
                        }
                        .disabled(model.isLoading)
                    }
                } header: {
                    Text(search.isEmpty ? "Drafts" : "Search results")
                        .font(.caption.weight(.semibold))
                        .textCase(nil)
                }
            }
            .listStyle(.plain)
            .overlay { emptyState }
            .navigationTitle("Inventory")
            .navigationBarTitleDisplayMode(.inline)
            .scrollContentBackground(.hidden)
            .background(WorkspaceStyle.sidebar)
            .toolbarBackground(WorkspaceStyle.sidebar, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .searchable(text: $search, prompt: "Search titles")
            .refreshable { await model.reload(search: search) }
            .task(id: search) {
                // Wait for typing to pause; a newer keystroke cancels this one.
                if !search.isEmpty {
                    do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
                }
                await model.reload(search: search)
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("New listing", systemImage: "plus") { showingNewListing = true }
                        .frame(minWidth: 44, minHeight: 44)
                        .accessibilityHint("Create a draft, then add photos and item details")
                }
            }
            .sheet(isPresented: $showingNewListing, onDismiss: openCreatedDraft) {
                NewListingView(api: model.api) { draft in
                    model.didSave(draft)
                    createdDraft = draft
                }
            }
            .alert("Couldn't load drafts", isPresented: errorShown) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(model.error ?? "")
            }
            .navigationDestination(for: Draft.self) { draft in
                DraftEditorView(api: model.api, draft: draft, onSaved: model.didSave)
            }
        }
    }

    @ViewBuilder
    private var emptyState: some View {
        if model.drafts.isEmpty && model.hasLoaded {
            if search.isEmpty {
                ContentUnavailableView {
                    Label("Your inventory starts here", systemImage: "tag")
                } description: {
                    Text("Create a listing, add your photos, and keep your draft ready to edit.")
                } actions: {
                    Button("New listing") { showingNewListing = true }
                        .buttonStyle(.borderedProminent)
                        .frame(minHeight: 44)
                }
            } else {
                ContentUnavailableView.search(text: search)
            }
        } else if !model.hasLoaded, model.error != nil {
            ContentUnavailableView {
                Label("Couldn't load inventory", systemImage: "wifi.exclamationmark")
            } description: {
                Text("Check your connection and try again.")
            } actions: {
                Button("Try again") { Task { await model.reload(search: search) } }
                    .disabled(model.isLoading)
                    .buttonStyle(.borderedProminent)
                    .frame(minHeight: 44)
            }
        } else if !model.hasLoaded && model.isLoading {
            ProgressView()
        }
    }

    private var errorShown: Binding<Bool> {
        Binding(get: { model.hasLoaded && model.error != nil }, set: { if !$0 { model.error = nil } })
    }

    private func openCreatedDraft() {
        guard let draft = createdDraft else { return }
        createdDraft = nil
        path.append(draft)
    }

}

struct DraftRow: View {
    let api: AnteroomAPI
    let draft: Draft
    @State private var coverID: String?

    var body: some View {
        InventoryRowContent(title: draft.displayTitle, updated: draft.updatedDate) {
            if let coverID {
                RemotePhoto(api: api, draftID: draft.id, photoID: coverID, maxPixels: 96)
            } else {
                Image(systemName: "photo")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(WorkspaceStyle.surface)
            }
        }
        .task(id: draft.updatedAt) {
            // Load only visible rows. Photo failures don't hide a listing.
            coverID = try? await api.photos(draftID: draft.id).first?.id
        }
    }
}

struct InventoryRowContent<Thumbnail: View>: View {
    let title: String
    let updated: Date?
    let thumbnail: Thumbnail

    init(title: String, updated: Date?, @ViewBuilder thumbnail: () -> Thumbnail) {
        self.title = title
        self.updated = updated
        self.thumbnail = thumbnail()
    }

    var body: some View {
        HStack(spacing: 12) {
            thumbnail
                .frame(width: 44, height: 44)
                .clipped()
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(WorkspaceStyle.border, lineWidth: 1))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.subheadline.weight(.medium)).lineLimit(2)
                HStack(spacing: 6) {
                    Text("Draft")
                    if let updated {
                        Text("·").accessibilityHidden(true)
                        Text(updated, format: .relative(presentation: .named))
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }
}
