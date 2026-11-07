import PhotosUI
import SwiftUI

/// Bulk listing, as on web and desktop: everything is set up in one place
/// before anything is created. Pick photos, group each item's photos, set the
/// cost and labels every draft shares and each item's SKU, then create the
/// drafts, or create and generate them. Nothing is listed or sent.
struct BulkListingView: View {
    let api: AnteroomAPI
    let onFinished: () -> Void

    /// A picked photo, prepared once: the JPEG that uploads and a thumbnail.
    private struct Picked {
        let jpeg: Data
        let thumbnail: UIImage?
    }

    private struct Created: Sendable {
        let listing: ListingSummary
        let photos: Int
    }

    @Environment(\.dismiss) private var dismiss
    @State private var plan = BulkListingPlan()
    @State private var photos: [UUID: Picked] = [:]
    @State private var selection: Set<UUID> = []
    @State private var picked: [PhotosPickerItem] = []
    @State private var preparing = 0
    @State private var unreadable = 0
    @State private var cog = ""
    @State private var labels = ""
    @State private var creating = false
    @State private var progress = 0
    @State private var problems: [String] = []
    @State private var confirmDiscard = false
    /// Drafts to generate once they exist; shows the generate progress.
    @State private var generating: [ListingSummary]?
    /// Set when some items had problems: the drafts with photos, and whether
    /// the seller asked to generate them. The setup can't be sent twice.
    @State private var outcome: (drafts: [ListingSummary], generate: Bool)?

    var body: some View {
        if let generating {
            BulkRegenerateView(api: api, listings: generating, mode: .generate, onFinished: onFinished)
        } else if let outcome {
            results(outcome.drafts, generate: outcome.generate)
        } else {
            setup
        }
    }

    /// What was created when not everything worked. The drafts are saved;
    /// missing photos are added from each draft.
    private func results(_ drafts: [ListingSummary], generate: Bool) -> some View {
        NavigationStack {
            List {
                Group {
                    Section {
                        Text("\(drafts.count) \(drafts.count == 1 ? "draft is" : "drafts are") saved with photos.")
                        ForEach(problems, id: \.self) { problem in
                            Label(problem, systemImage: "exclamationmark.triangle").foregroundStyle(.red).font(.footnote)
                        }
                    }
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
            .navigationTitle("Bulk listing")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { onFinished(); dismiss() }
                }
                if generate && !drafts.isEmpty {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Generate \(drafts.count)") { generating = drafts }.fontWeight(.semibold)
                    }
                }
            }
            .interactiveDismissDisabled()
        }
    }

    private var setup: some View {
        NavigationStack {
            List {
                Group {
                    pickSection
                    if !plan.loose.isEmpty { looseSection }
                    if !plan.items.isEmpty {
                        sharedSection
                        itemsSection
                    }
                    if !plan.items.isEmpty { createSection }
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
            .disabled(creating)
            .navigationTitle("Bulk listing")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        if photos.isEmpty { dismiss() } else { confirmDiscard = true }
                    }
                    .disabled(creating)
                }
            }
            // Only Cancel closes it, so a stray swipe can't throw away the grouping.
            .interactiveDismissDisabled(!photos.isEmpty || creating)
            .confirmationDialog("Discard bulk listing?", isPresented: $confirmDiscard, titleVisibility: .visible) {
                Button("Discard", role: .destructive) { dismiss() }
            } message: {
                Text("The photos and grouping here haven't been saved.")
            }
            .onChange(of: picked) { _, items in
                guard !items.isEmpty else { return }
                picked = []
                Task { await prepare(items) }
            }
        }
    }

    // MARK: Sections

    private var pickSection: some View {
        Section {
            PhotosPicker(selection: $picked, matching: .images) {
                Label(photos.isEmpty ? "Choose photos" : "Add more photos", systemImage: "photo.on.rectangle")
                    .frame(minHeight: 44)
            }
            .accessibilityIdentifier("bulk-choose-photos")
            if preparing > 0 {
                HStack { ProgressView(); Text("Preparing \(preparing) photos…").foregroundStyle(.secondary) }
            }
            if unreadable > 0 {
                Label(unreadable == 1 ? "1 photo couldn't be opened." : "\(unreadable) photos couldn't be opened.",
                      systemImage: "exclamationmark.triangle")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        } footer: {
            Text(photos.isEmpty
                 ? "Choose the photos for every item at once. Next, you group each item's photos, and Anteroom makes one draft per item."
                 : "\(plan.items.count) \(plan.items.count == 1 ? "item" : "items") · \(plan.loose.count) \(plan.loose.count == 1 ? "photo" : "photos") to group")
        }
    }

    private var looseSection: some View {
        Section {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 76), spacing: 6)], spacing: 6) {
                ForEach(plan.loose, id: \.self) { id in
                    let chosen = selection.contains(id)
                    Button {
                        if chosen { selection.remove(id) } else { selection.insert(id) }
                    } label: {
                        thumbnail(id, size: 76)
                            .overlay(alignment: .topTrailing) {
                                Image(systemName: chosen ? "checkmark.circle.fill" : "circle")
                                    .font(.title3)
                                    .foregroundStyle(chosen ? Color.accentColor : .white)
                                    .shadow(radius: 2)
                                    .padding(4)
                            }
                            .overlay {
                                RoundedRectangle(cornerRadius: 6)
                                    .strokeBorder(chosen ? Color.accentColor : .clear, lineWidth: 3)
                            }
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(chosen ? "Selected photo" : "Photo")
                    .accessibilityAddTraits(chosen ? .isSelected : [])
                    .contextMenu {
                        Button("Remove photo", systemImage: "trash", role: .destructive) { remove(id) }
                    }
                }
            }
            .padding(.vertical, 4)
            HStack {
                Button("Make item (\(selection.count))", systemImage: "square.stack") { makeItem() }
                    .buttonStyle(.borderedProminent)
                    .disabled(selection.isEmpty || selection.count > BulkListingPlan.photosPerItem)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("bulk-make-item")
                Spacer()
                Menu("More") {
                    Button(selection.count == plan.loose.count ? "Clear selection" : "Select all") {
                        selection = selection.count == plan.loose.count ? [] : Set(plan.loose)
                    }
                    Button("Each photo is its own item", systemImage: "square.grid.2x2") {
                        plan.makeItemEach()
                        selection = []
                    }
                }
                .frame(minHeight: 44)
            }
        } header: {
            Text("Photos to group")
        } footer: {
            Text(selection.count > BulkListingPlan.photosPerItem
                 ? "An item can have up to \(BulkListingPlan.photosPerItem) photos."
                 : "Select one item's photos, then tap Make item. The first photo you picked is the cover. Touch and hold a photo to remove it.")
        }
    }

    private var sharedSection: some View {
        Section {
            WorkspaceField("Cost of goods") {
                TextField("$0.00", text: $cog).keyboardType(.decimalPad)
            }
            WorkspaceField("Labels") {
                TextField("Spring, Ready", text: $labels)
            }
        } header: {
            Text("Every draft")
        } footer: {
            Text("Saved to each draft's item details, the same as editing one by one.")
        }
    }

    private var itemsSection: some View {
        Section("Items") {
            ForEach(Array(plan.items.enumerated()), id: \.element.id) { index, item in
                VStack(alignment: .leading, spacing: 8) {
                    Text("Item \(index + 1) · \(item.photos.count) \(item.photos.count == 1 ? "photo" : "photos")")
                        .font(.subheadline.weight(.medium))
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(item.photos, id: \.self) { photo in
                                thumbnail(photo, size: 56)
                                    .contextMenu {
                                        Button("Remove photo", systemImage: "trash", role: .destructive) { remove(photo) }
                                    }
                            }
                        }
                    }
                    TextField("SKU (optional)", text: Binding(
                        get: { item.sku },
                        set: { plan.setSKU($0, for: item.id) }
                    ))
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .frame(minHeight: 44)
                }
                .padding(.vertical, 4)
                .swipeActions {
                    Button("Ungroup", systemImage: "rectangle.stack.badge.minus") { plan.ungroup(item.id) }
                }
                .contextMenu {
                    Button("Ungroup", systemImage: "rectangle.stack.badge.minus") { plan.ungroup(item.id) }
                }
            }
        }
    }

    private var createSection: some View {
        Section {
            if creating {
                HStack { ProgressView(); Text("Creating drafts… \(progress) of \(plan.items.count)") }
            } else {
                Button { create(generate: true) } label: {
                    Label("Create & generate \(plan.items.count)", systemImage: "sparkles").frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.borderedProminent)
                .disabled(!plan.loose.isEmpty)
                Button { create(generate: false) } label: {
                    Text("Create \(plan.items.count) \(plan.items.count == 1 ? "draft" : "drafts")").frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.bordered)
                .disabled(!plan.loose.isEmpty)
            }
        } footer: {
            Text(plan.loose.isEmpty
                 ? "Drafts are saved in Anteroom. Nothing is listed or sent to a marketplace."
                 : "Group or remove the \(plan.loose.count) remaining \(plan.loose.count == 1 ? "photo" : "photos") first.")
        }
        .listRowBackground(Color.clear)
    }

    @ViewBuilder
    private func thumbnail(_ id: UUID, size: CGFloat) -> some View {
        Group {
            if let image = photos[id]?.thumbnail {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                Rectangle().fill(WorkspaceStyle.surface)
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    // MARK: Actions

    private func makeItem() {
        plan.makeItem(from: selection)
        selection = []
    }

    private func remove(_ id: UUID) {
        plan.remove(id)
        selection.remove(id)
        photos[id] = nil
    }

    /// Prepares picked photos in picking order, off the main thread.
    private func prepare(_ items: [PhotosPickerItem]) async {
        preparing += items.count
        var ready: [UUID] = []
        for item in items {
            defer { preparing -= 1 }
            guard let data = try? await item.loadTransferable(type: Data.self) else { unreadable += 1; continue }
            // Only Data crosses threads; the small thumbnail decodes here.
            let prepared = await Task.detached(priority: .userInitiated) { () -> (Data, Data?)? in
                guard let jpeg = PhotoPreparer.jpeg(from: data) else { return nil }
                let thumbnail = PhotoPreparer.thumbnail(from: jpeg, maxPixels: 240)?.jpegData(compressionQuality: 0.8)
                return (jpeg, thumbnail)
            }.value
            guard let prepared else { unreadable += 1; continue }
            let (jpeg, thumbnail) = prepared
            let id = UUID()
            photos[id] = Picked(jpeg: jpeg, thumbnail: thumbnail.flatMap(UIImage.init(data:)))
            ready.append(id)
        }
        plan.add(ready)
    }

    /// Each item becomes its own draft, side by side: create it with its
    /// notes, then upload its photos in order. A failed item never stops the others.
    private func create(generate: Bool) {
        guard !creating, plan.loose.isEmpty, !plan.items.isEmpty else { return }
        creating = true
        progress = 0
        problems = []
        let items = plan.items
        Task {
            let created = await withBackgroundTime("Bulk listing") {
                let tasks = items.enumerated().map { index, item in
                    Task { await createDraft(item, number: index + 1) }
                }
                var created: [Created] = []
                for task in tasks {
                    if let draft = await task.value { created.append(draft) }
                }
                return created
            }
            creating = false
            let withPhotos = created.filter { $0.photos > 0 }.map(\.listing)
            if !problems.isEmpty {
                outcome = (withPhotos, generate)
            } else if generate && !withPhotos.isEmpty {
                generating = withPhotos
            } else {
                onFinished()
                dismiss()
            }
        }
    }

    private func createDraft(_ item: BulkListingPlan.Item, number: Int) async -> Created? {
        defer { progress += 1 }
        let listing: ListingSummary
        do {
            let notes = try BulkListingPlan.notes(cog: cog, labels: labels, sku: item.sku)
            listing = try await api.createListing(notes: notes)
        } catch {
            problems.append("Item \(number) wasn't created. \(error.localizedDescription)")
            return nil
        }
        let jpegs = item.photos.compactMap { photos[$0]?.jpeg }
        let report = await PhotoUploadReport.run(jpegs, prepare: { $0 },
                                                 upload: { try await api.uploadPhoto(listingID: listing.id, jpeg: $0) },
                                                 uploaded: { _ in })
        if let message = report.message {
            problems.append("Item \(number): \(message) Add them from the draft.")
        }
        return Created(listing: listing, photos: report.uploaded)
    }
}
