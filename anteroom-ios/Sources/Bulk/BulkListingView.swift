import PhotosUI
import SwiftUI

/// Bulk listing, as on web and desktop: everything is set up in one place
/// before anything is created. Pick photos, group each item's photos, set the
/// box, cost and labels every draft shares and each item's SKU and
/// measurements, then create the drafts, or create and generate them.
/// Nothing is listed or sent.
struct BulkListingView: View {
    let api: AnteroomAPI
    let onFinished: () -> Void

    private struct Created: Sendable {
        let listing: ConversationSummary
        let photos: Int
    }

    /// Items read at once by Read measurements; each is several photo-model calls (READ_CONCURRENCY on the web).
    private static let readConcurrency = 2

    @Environment(\.dismiss) private var dismiss
    @State private var plan = BulkListingPlan()
    /// The prepared JPEG that uploads, and a small thumbnail, per picked photo.
    @State private var jpegs: [UUID: Data] = [:]
    @State private var thumbnails: [UUID: UIImage] = [:]
    @State private var selection: Set<UUID> = []
    @State private var picked: [PhotosPickerItem] = []
    @State private var preparing = 0
    @State private var unreadable = 0
    @State private var cog = ""
    @State private var labels = ""
    @State private var boxID: String?
    @State private var boxes: [SourceBox] = []
    @State private var reading: Set<UUID> = []
    @State private var creating = false
    @State private var progress = 0
    @State private var problems: [String] = []
    @State private var confirmDiscard = false
    /// Drafts to generate once they exist; shows the generate progress.
    @State private var generating: [ConversationSummary]?
    /// Set when some items had problems: the drafts with photos, and whether
    /// the seller asked to generate them. The setup can't be sent twice.
    @State private var outcome: (drafts: [ConversationSummary], generate: Bool)?

    var body: some View {
        if let generating {
            BulkRegenerateView(api: api, listings: generating, mode: .generate, onFinished: onFinished)
        } else if let outcome {
            results(outcome.drafts, generate: outcome.generate)
        } else {
            setup
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
                        createSection
                    }
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
            .disabled(creating)
            .navigationTitle("Bulk listing")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: UUID.self) { id in
                BulkItemEditor(plan: $plan, itemID: id, number: (plan.items.firstIndex { $0.id == id } ?? 0) + 1,
                               thumbnails: thumbnails, reading: reading.contains(id)) { read([id]) }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        if jpegs.isEmpty { dismiss() } else { confirmDiscard = true }
                    }
                    .disabled(creating)
                }
            }
            // Only Cancel closes it, so a stray swipe can't throw away the grouping.
            .interactiveDismissDisabled(!jpegs.isEmpty || creating)
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
            // The box list is optional; without it every draft is "Not from a box".
            .task { if let rows = try? await api.boxes() { boxes = rows } }
        }
    }

    // MARK: Sections

    private var pickSection: some View {
        Section {
            PhotosPicker(selection: $picked, matching: .images) {
                Label(jpegs.isEmpty ? "Choose photos" : "Add more photos", systemImage: "photo.on.rectangle")
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
            Text(jpegs.isEmpty
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
                        BulkThumbnail(image: thumbnails[id], quarterTurns: plan.quarterTurns(id), size: 76)
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
                        Button("Rotate left", systemImage: "rotate.left") { plan.rotate(id, by: -1) }
                        Button("Rotate right", systemImage: "rotate.right") { plan.rotate(id, by: 1) }
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
                 : "Select one item's photos, then tap Make item. The first photo you picked is the cover. Touch and hold a photo to turn or remove it.")
        }
    }

    private var sharedSection: some View {
        Section {
            Picker("Box", selection: $boxID) {
                Text("Not from a box").tag(String?.none)
                ForEach(boxes) { box in
                    Text(box.label).tag(Optional(box.id))
                }
            }
            .frame(minHeight: 44)
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
        Section {
            ForEach(Array(plan.items.enumerated()), id: \.element.id) { index, item in
                NavigationLink(value: item.id) {
                    itemRow(item, number: index + 1)
                }
                .swipeActions {
                    Button("Ungroup", systemImage: "rectangle.stack.badge.minus") { plan.ungroup(item.id) }
                }
                .contextMenu {
                    Button("Ungroup", systemImage: "rectangle.stack.badge.minus") { plan.ungroup(item.id) }
                }
            }
            Button {
                read(plan.items.map(\.id))
            } label: {
                HStack {
                    Label("Read all measurements", systemImage: "ruler")
                    if !reading.isEmpty { Spacer(); ProgressView() }
                }
                .frame(minHeight: 44)
            }
            .disabled(!reading.isEmpty)
        } header: {
            Text("Items")
        } footer: {
            Text("Tap an item to arrange or turn its photos and add its size tag, SKU and measurements. Reading fills only blank measurements from yardstick photos; check them before creating.")
        }
    }

    private func itemRow(_ item: BulkListingPlan.Item, number: Int) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Item \(number) · \(item.photos.count) \(item.photos.count == 1 ? "photo" : "photos")")
                    .font(.subheadline.weight(.medium))
                if reading.contains(item.id) { ProgressView().controlSize(.small) }
            }
            HStack(spacing: 4) {
                ForEach(item.photos.prefix(5), id: \.self) { photo in
                    BulkThumbnail(image: thumbnails[photo], quarterTurns: plan.quarterTurns(photo), size: 44)
                }
                if item.photos.count > 5 {
                    Text("+\(item.photos.count - 5)").font(.caption).foregroundStyle(.secondary)
                }
            }
            if let summary = summary(item) {
                Text(summary).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
        }
        .padding(.vertical, 2)
    }

    private func summary(_ item: BulkListingPlan.Item) -> String? {
        var parts: [String] = []
        let sizeTag = item.sizeTag.trimmingCharacters(in: .whitespacesAndNewlines)
        if !sizeTag.isEmpty { parts.append("Size tag: \(sizeTag)") }
        let sku = item.sku.trimmingCharacters(in: .whitespacesAndNewlines)
        if !sku.isEmpty { parts.append("SKU \(sku)") }
        let measured = item.typedMeasurements
        if !measured.isEmpty {
            let values = item.garment.fields.compactMap { field in measured[field.key].map { "\(field.label) \($0)" } }
            parts.append("\(item.garment.label): \(values.joined(separator: ", "))")
        }
        if let note = item.readNote { parts.append(note) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
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
                .disabled(!plan.loose.isEmpty || !reading.isEmpty)
                Button { create(generate: false) } label: {
                    Text("Create \(plan.items.count) \(plan.items.count == 1 ? "draft" : "drafts")").frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.bordered)
                .disabled(!plan.loose.isEmpty || !reading.isEmpty)
            }
        } footer: {
            Text(createNote)
        }
        .listRowBackground(Color.clear)
    }

    private var createNote: String {
        if !plan.loose.isEmpty {
            return "Group or remove the \(plan.loose.count) remaining \(plan.loose.count == 1 ? "photo" : "photos") first."
        }
        if !reading.isEmpty { return "Waiting for the measurement reading to finish." }
        return "Drafts are saved in Anteroom. Nothing is listed or sent to a marketplace."
    }

    /// What was created when not everything worked. The drafts are saved;
    /// missing photos are added from each draft.
    private func results(_ drafts: [ConversationSummary], generate: Bool) -> some View {
        NavigationStack {
            List {
                Group {
                    Section {
                        Text("\(drafts.count) \(drafts.count == 1 ? "draft is" : "drafts are") saved with photos.")
                        ForEach(problems, id: \.self) { problem in
                            Label(problem, systemImage: "exclamationmark.triangle").foregroundStyle(WorkspaceStyle.destructive).font(.footnote)
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

    // MARK: Actions

    private func makeItem() {
        plan.makeItem(from: selection)
        selection = []
    }

    private func remove(_ id: UUID) {
        plan.remove(id)
        selection.remove(id)
        jpegs[id] = nil
        thumbnails[id] = nil
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
            jpegs[id] = jpeg
            thumbnails[id] = thumbnail.flatMap(UIImage.init(data:))
            ready.append(id)
        }
        plan.add(ready)
    }

    /// Reads measurements from each item's photos, two items at a time.
    /// Typed values stay; only blank fields are filled.
    private func read(_ ids: [UUID]) {
        let todo = ids.filter { !reading.contains($0) }
        guard !todo.isEmpty else { return }
        reading.formUnion(todo)
        // Each lane reads its share of the items one after another.
        let lanes = (0..<min(Self.readConcurrency, todo.count)).map { lane in
            stride(from: lane, to: todo.count, by: Self.readConcurrency).map { todo[$0] }
        }
        Task {
            await withBackgroundTime("Read measurements") {
                let workers = lanes.map { lane in
                    Task { for id in lane { await readOne(id) } }
                }
                for worker in workers { await worker.value }
            }
        }
    }

    private func readOne(_ id: UUID) async {
        defer { reading.remove(id) }
        guard let item = plan.items.first(where: { $0.id == id }) else { return }
        let garment = item.garment
        let photos = item.photos.compactMap { jpegs[$0] }
        plan.setReadNote("Reading the yardstick…", for: id)
        do {
            let result = try await api.readMeasurements(jpegs: photos, garment: garment)
            plan.apply(result, garment: garment, to: id)
        } catch {
            plan.setReadNote(error.localizedDescription, for: id)
        }
    }

    /// Each item becomes its own draft, side by side: create it with its
    /// notes and box, upload its photos in order, then turn the ones the
    /// seller turned. A failed item never stops the others.
    private func create(generate: Bool) {
        guard !creating, plan.loose.isEmpty, !plan.items.isEmpty, reading.isEmpty else { return }
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
        let listing: ConversationSummary
        do {
            let notes = try BulkListingPlan.notes(cog: cog, labels: labels, item: item)
            listing = try await api.createListing(notes: notes, boxID: boxID)
        } catch {
            problems.append("Item \(number) wasn't created. \(error.localizedDescription)")
            return nil
        }
        // Upload in listing order, then turn each saved photo the seller turned.
        var uploaded = 0
        var failed = 0
        var lastError: String?
        var turnFailures = 0
        for id in item.photos {
            guard let jpeg = jpegs[id] else { continue }
            do {
                let photo = try await api.uploadPhoto(listingID: listing.id, jpeg: jpeg)
                uploaded += 1
                if let degrees = plan.rotationDegrees(id) {
                    do {
                        _ = try await api.rotatePhoto(listingID: listing.id, photoID: photo.id, degrees: degrees)
                    } catch {
                        turnFailures += 1
                    }
                }
            } catch {
                failed += 1
                lastError = error.localizedDescription
            }
        }
        if failed > 0 {
            let what = uploaded == 0 ? "No photos uploaded." : "\(failed) of \(failed + uploaded) photos didn't upload."
            problems.append(["Item \(number): \(what)", lastError, "Add them from the draft."].compactMap { $0 }.joined(separator: " "))
        }
        if turnFailures > 0 {
            problems.append("Item \(number): \(turnFailures == 1 ? "1 photo wasn't" : "\(turnFailures) photos weren't") turned. Turn \(turnFailures == 1 ? "it" : "them") from the draft.")
        }
        return Created(listing: listing, photos: uploaded)
    }
}

/// A picked photo's thumbnail, turned the way it will be saved.
struct BulkThumbnail: View {
    let image: UIImage?
    let quarterTurns: Int
    let size: CGFloat

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image).resizable().scaledToFill()
                    .rotationEffect(.degrees(Double(quarterTurns) * 90))
            } else {
                Rectangle().fill(WorkspaceStyle.surface)
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}

/// One item: photo order and cover, turning, SKU and measurements.
private struct BulkItemEditor: View {
    @Binding var plan: BulkListingPlan
    let itemID: UUID
    let number: Int
    let thumbnails: [UUID: UIImage]
    let reading: Bool
    let read: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        Group {
            if let item = plan.items.first(where: { $0.id == itemID }) {
                List {
                    Group {
                        photosSection(item)
                        Section("Size tag") {
                            TextField("e.g. XL, 32 × 30", text: Binding(
                                get: { item.sizeTag },
                                set: { plan.setSizeTag($0, for: itemID) }
                            ))
                            .autocorrectionDisabled()
                            .frame(minHeight: 44)
                            .accessibilityLabel("Size tag")
                            .accessibilityIdentifier("bulk-size-tag")
                        }
                        Section("SKU") {
                            TextField("SKU (optional)", text: Binding(
                                get: { item.sku },
                                set: { plan.setSKU($0, for: itemID) }
                            ))
                            .textInputAutocapitalization(.characters)
                            .autocorrectionDisabled()
                            .frame(minHeight: 44)
                        }
                        measurementsSection(item)
                    }
                    .listRowBackground(WorkspaceStyle.surface)
                }
                .workspaceGroupedList()
                .toolbar { EditButton() }
            } else {
                ContentUnavailableView("This item was ungrouped", systemImage: "rectangle.stack.badge.minus")
            }
        }
        .navigationTitle("Item \(number)")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func photosSection(_ item: BulkListingPlan.Item) -> some View {
        Section {
            ForEach(Array(item.photos.enumerated()), id: \.element) { index, photo in
                HStack(spacing: 12) {
                    BulkThumbnail(image: thumbnails[photo], quarterTurns: plan.quarterTurns(photo), size: 64)
                    Text(index == 0 ? "Cover" : "Photo \(index + 1)").font(.subheadline)
                    Spacer()
                    Button("Rotate left", systemImage: "rotate.left") { plan.rotate(photo, by: -1) }
                        .labelStyle(.iconOnly).buttonStyle(.borderless).frame(width: 44, height: 44)
                    Button("Rotate right", systemImage: "rotate.right") { plan.rotate(photo, by: 1) }
                        .labelStyle(.iconOnly).buttonStyle(.borderless).frame(width: 44, height: 44)
                }
                .contextMenu {
                    if index != 0 {
                        Button("Make cover", systemImage: "star") { plan.makeCover(photo, in: itemID) }
                    }
                    Button("Remove photo", systemImage: "trash", role: .destructive) { plan.remove(photo) }
                }
            }
            .onMove { plan.movePhotos(in: itemID, from: $0, to: $1) }
        } header: {
            Text("Photos")
        } footer: {
            Text("Tap Edit to drag photos into order; the first is the cover. Touch and hold a photo to make it the cover or remove it.")
        }
    }

    private func measurementsSection(_ item: BulkListingPlan.Item) -> some View {
        Section {
            garmentPicker(item)
            ForEach(item.garment.fields, id: \.key) { field in
                WorkspaceField(item.suggested.contains(field.key) ? "\(field.label) (from photos)" : field.label) {
                    TextField("Inches", text: Binding(
                        get: { item.measurements[field.key] ?? "" },
                        set: { plan.setMeasurement(field.key, $0, for: itemID) }
                    ))
                    .keyboardType(.numbersAndPunctuation)
                }
            }
            Button(action: read) {
                HStack {
                    Label("Read measurements", systemImage: "ruler")
                    if reading { Spacer(); ProgressView() }
                }
                .frame(minHeight: 44)
            }
            .disabled(reading)
            if let note = item.readNote {
                Text(note).font(.footnote).foregroundStyle(.secondary)
            }
        } header: {
            Text("Measurements (in)")
        } footer: {
            Text("Reading fills blank fields from yardstick photos. Values you type always stay.")
        }
    }

    @ViewBuilder
    private func garmentPicker(_ item: BulkListingPlan.Item) -> some View {
        let picker = Picker("Garment", selection: Binding(
            get: { item.garment },
            set: { plan.setGarment($0, for: itemID) }
        )) {
            ForEach(SellerInputs.Garment.allCases) { garment in
                Text(garment.label).tag(garment)
            }
        }
        if dynamicTypeSize.isAccessibilitySize {
            picker.pickerStyle(.menu)
        } else {
            picker.pickerStyle(.segmented)
        }
    }
}
