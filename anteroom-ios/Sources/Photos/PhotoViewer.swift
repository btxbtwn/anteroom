import SwiftUI

/// Full-size photos, one page each, with the desktop photo editor's actions:
/// rotate, adjust, reset to the original, and delete. Each change saves
/// immediately, like the desktop editor.
struct PhotoViewer: View {
    let api: AnteroomAPI
    let listingID: String
    @Binding var photos: [ListingPhoto]
    @State var selection: String
    let onDelete: (ListingPhoto) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var busy = false
    @State private var error: String?
    @State private var adjusting: ListingPhoto?
    @State private var confirmDelete = false

    var body: some View {
        NavigationStack {
            TabView(selection: $selection) {
                ForEach(photos) { photo in
                    FullPhoto(api: api, listingID: listingID, photo: photo)
                        .tag(photo.id)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: photos.count > 1 ? .always : .never))
            .background(Color.black)
            .navigationTitle(position)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.visible, for: .navigationBar, .bottomBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
                ToolbarItemGroup(placement: .bottomBar) {
                    Button("Rotate left", systemImage: "rotate.left") { rotate(-90) }
                    Spacer()
                    Button("Rotate right", systemImage: "rotate.right") { rotate(90) }
                    Spacer()
                    Button("Adjust", systemImage: "slider.horizontal.3") { adjusting = current }
                    Spacer()
                    Menu("More", systemImage: "ellipsis.circle") {
                        Button("Revert to original", systemImage: "arrow.uturn.backward") { reset() }
                            .disabled(!(current?.isEdited ?? false))
                        Button("Delete photo", systemImage: "trash", role: .destructive) { confirmDelete = true }
                    }
                }
            }
            .disabled(busy || current == nil)
            .overlay { if busy { ProgressView().controlSize(.large).tint(.white) } }
            .alert("Couldn't change this photo", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(error ?? "")
            }
            .confirmationDialog("Delete this photo?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete photo", role: .destructive) {
                    guard let photo = current else { return }
                    let next = neighbor(of: photo)
                    onDelete(photo)
                    if let next { selection = next } else { dismiss() }
                }
            }
            .sheet(item: $adjusting) { photo in
                PhotoAdjustSheet(api: api, listingID: listingID, photo: photo) { replace($0) }
            }
        }
    }

    private var current: ListingPhoto? { photos.first { $0.id == selection } }

    private var position: String {
        guard let index = photos.firstIndex(where: { $0.id == selection }) else { return "Photo" }
        return "\(index + 1) of \(photos.count)"
    }

    private func neighbor(of photo: ListingPhoto) -> String? {
        guard let index = photos.firstIndex(of: photo) else { return nil }
        if index + 1 < photos.count { return photos[index + 1].id }
        return index > 0 ? photos[index - 1].id : nil
    }

    private func replace(_ photo: ListingPhoto) {
        if let index = photos.firstIndex(where: { $0.id == photo.id }) { photos[index] = photo }
    }

    private func rotate(_ degrees: Int) {
        guard let photo = current else { return }
        run { try await api.rotatePhoto(listingID: listingID, photoID: photo.id, degrees: degrees) }
    }

    private func reset() {
        guard let photo = current else { return }
        run { try await api.resetPhotoEdits(listingID: listingID, photoID: photo.id) }
    }

    private func run(_ change: @escaping () async throws -> ListingPhoto) {
        busy = true
        Task {
            defer { busy = false }
            do { replace(try await change()) } catch { self.error = error.localizedDescription }
        }
    }
}

/// One photo at full size, pinch to zoom.
private struct FullPhoto: View {
    let api: AnteroomAPI
    let listingID: String
    let photo: ListingPhoto
    @State private var image: UIImage?
    @State private var failed = false
    @State private var zoom: CGFloat = 1

    var body: some View {
        ZStack {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
                    .scaleEffect(zoom)
                    .gesture(MagnifyGesture()
                        .onChanged { zoom = min(4, max(1, $0.magnification)) }
                        .onEnded { _ in withAnimation { zoom = 1 } })
                    .accessibilityLabel("Listing photo")
            } else if failed {
                Label("Couldn't load this photo", systemImage: "photo").foregroundStyle(.white)
            } else {
                ProgressView().tint(.white)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .task(id: photo.version ?? photo.id) {
            do {
                let data = try await api.photoData(listingID: listingID, photoID: photo.id)
                let decoded = await Task.detached(priority: .userInitiated) {
                    PhotoPreparer.thumbnail(from: data, maxPixels: 2400)
                }.value
                image = decoded
                failed = decoded == nil
            } catch {
                failed = true
            }
        }
    }
}

/// Straighten, exposure, brightness and contrast, previewed by the server
/// from the original upload before anything is saved.
struct PhotoAdjustSheet: View {
    let api: AnteroomAPI
    let listingID: String
    let photo: ListingPhoto
    let onSaved: (ListingPhoto) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var edits: PhotoEdits
    @State private var preview: UIImage?
    @State private var saving = false
    @State private var error: String?

    init(api: AnteroomAPI, listingID: String, photo: ListingPhoto, onSaved: @escaping (ListingPhoto) -> Void) {
        self.api = api
        self.listingID = listingID
        self.photo = photo
        self.onSaved = onSaved
        _edits = State(initialValue: photo.edits ?? PhotoEdits())
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    ZStack {
                        Color.black
                        if let preview {
                            Image(uiImage: preview).resizable().scaledToFit()
                        } else {
                            ProgressView().tint(.white)
                        }
                    }
                    .frame(height: 260)
                    .listRowInsets(EdgeInsets())
                    .accessibilityLabel("Preview")
                }
                Section {
                    slider("Straighten", value: $edits.straighten, range: -45...45, unit: "°")
                    slider("Exposure", value: $edits.exposure, range: -2...2, unit: "")
                    slider("Brightness", value: $edits.brightness, range: -100...100, unit: "")
                    slider("Contrast", value: $edits.contrast, range: -100...100, unit: "")
                } footer: {
                    Text("Edits always start from your original photo, so you can revert at any time.")
                }
                if let error {
                    Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
                }
            }
            .navigationTitle("Adjust")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(saving) }
                ToolbarItem(placement: .confirmationAction) {
                    if saving {
                        ProgressView()
                    } else {
                        Button("Save") { save() }.fontWeight(.semibold).disabled(edits == (photo.edits ?? PhotoEdits()))
                    }
                }
            }
            .task(id: edits) {
                // Wait for the slider to settle before asking for a new render.
                do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
                if let data = try? await api.previewPhotoEdits(listingID: listingID, photoID: photo.id, edits: edits) {
                    preview = UIImage(data: data)
                }
            }
        }
        .interactiveDismissDisabled(saving)
    }

    private func slider(_ label: String, value: Binding<Double>, range: ClosedRange<Double>, unit: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(label)
                Spacer()
                Text(value.wrappedValue.formatted(.number.precision(.fractionLength(0...1))) + unit)
                    .monospacedDigit().foregroundStyle(.secondary)
            }
            Slider(value: value, in: range)
                .accessibilityLabel(label)
        }
        .frame(minHeight: 44)
    }

    private func save() {
        saving = true
        error = nil
        Task {
            defer { saving = false }
            do {
                onSaved(try await api.savePhotoEdits(listingID: listingID, photoID: photo.id, edits: edits))
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// Drag photos into the order marketplaces will show them; the first is the cover.
struct ArrangePhotosSheet: View {
    let api: AnteroomAPI
    let listingID: String
    @Binding var photos: [ListingPhoto]

    @Environment(\.dismiss) private var dismiss
    @State private var order: [ListingPhoto] = []
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(Array(order.enumerated()), id: \.element.id) { index, photo in
                        HStack(spacing: 12) {
                            RemotePhoto(api: api, photoID: photo.id, maxPixels: 120, version: photo.version)
                                .frame(width: 56, height: 56)
                                .clipShape(RoundedRectangle(cornerRadius: 6))
                            Text(index == 0 ? "Cover photo" : "Photo \(index + 1)")
                        }
                        .accessibilityElement(children: .combine)
                    }
                    .onMove { order.move(fromOffsets: $0, toOffset: $1) }
                } footer: {
                    Text("The first photo is the cover marketplaces show.")
                }
                if let error {
                    Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
                }
            }
            .environment(\.editMode, .constant(.active))
            .navigationTitle("Arrange photos")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(saving) }
                ToolbarItem(placement: .confirmationAction) {
                    if saving {
                        ProgressView()
                    } else {
                        Button("Save") { save() }.fontWeight(.semibold).disabled(order.map(\.id) == photos.map(\.id))
                    }
                }
            }
            .onAppear { order = photos }
        }
        .interactiveDismissDisabled(saving)
    }

    private func save() {
        saving = true
        error = nil
        Task {
            defer { saving = false }
            do {
                try await api.reorderPhotos(listingID: listingID, ids: order.map(\.id))
                photos = order
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
