import SwiftUI

struct InventoryView: View {
    @Environment(SessionStore.self) private var session
    @State private var model: InventoryModel
    @State private var search = ""
    @State private var path: [Draft] = []
    @State private var isNaming = false
    @State private var newTitle = ""

    init(api: AnteroomAPI) {
        _model = State(initialValue: InventoryModel(api: api))
    }

    var body: some View {
        NavigationStack(path: $path) {
            List {
                ForEach(model.drafts) { draft in
                    NavigationLink(value: draft) {
                        DraftRow(draft: draft)
                    }
                }
                if model.canLoadMore {
                    Button("Load more") {
                        Task { await model.loadMore(search: search) }
                    }
                    .disabled(model.isLoading)
                }
            }
            .listStyle(.plain)
            .overlay { emptyState }
            .navigationTitle("Drafts")
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
                ToolbarItem(placement: .topBarLeading) {
                    accountMenu
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("New draft", systemImage: "plus") {
                        newTitle = ""
                        isNaming = true
                    }
                }
            }
            .alert("New draft", isPresented: $isNaming) {
                TextField("Title", text: $newTitle)
                Button("Cancel", role: .cancel) {}
                Button("Create") { create() }
                    .disabled(newTitle.trimmingCharacters(in: .whitespaces).isEmpty)
            } message: {
                Text("What are you listing?")
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
                ContentUnavailableView(
                    "No drafts yet",
                    systemImage: "tag",
                    description: Text("Tap + to start a listing.")
                )
            } else {
                ContentUnavailableView.search(text: search)
            }
        } else if !model.hasLoaded && model.isLoading {
            ProgressView()
        }
    }

    private var accountMenu: some View {
        Menu {
            if let email = session.email {
                Text(email)
            }
            Button("Sign out", systemImage: "rectangle.portrait.and.arrow.right", role: .destructive) {
                Task { await session.signOut() }
            }
        } label: {
            Image(systemName: "person.crop.circle")
        }
    }

    private var errorShown: Binding<Bool> {
        Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })
    }

    private func create() {
        let title = newTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return }
        Task {
            if let draft = await model.create(title: title) {
                path.append(draft)
            }
        }
    }
}

private struct DraftRow: View {
    let draft: Draft

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(draft.displayTitle)
                .font(.body)
                .lineLimit(2)
            if let updated = draft.updatedDate {
                Text(updated, format: .relative(presentation: .named))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }
}
