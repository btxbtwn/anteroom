import SwiftUI

/// Full-size photos with crop, rotation, revert and delete actions.
struct PhotoViewer: View {
    let api: AnteroomAPI
    let listingID: String
    @Binding var photos: [ListingPhoto]
    @State var selection: String
    /// Deletes a photo and returns why it failed, or nil once it is gone.
    let onDelete: (ListingPhoto) async -> String?

    @Environment(\.dismiss) private var dismiss
    @State private var busy = false
    @State private var error: String?
    @State private var cropping: ListingPhoto?
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
                    Button("Crop photo", systemImage: "crop") { cropping = current }
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
                Button("Delete photo", role: .destructive) { delete() }
            }
            .sheet(item: $cropping) { photo in
                PhotoCropSheet(api: api, listingID: listingID, photo: photo) { replace($0) }
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

    /// Moves on only once the photo is gone; a failure stays on it and says why.
    private func delete() {
        guard let photo = current else { return }
        let next = neighbor(of: photo)
        busy = true
        Task {
            defer { busy = false }
            if let failure = await onDelete(photo) {
                error = failure
            } else if let next {
                selection = next
            } else {
                dismiss()
            }
        }
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

struct PhotoCropSheet: View {
    let api: AnteroomAPI
    let listingID: String
    let photo: ListingPhoto
    let onSaved: (ListingPhoto) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var edits: PhotoEdits
    @State private var aspectID = "free"
    @State private var preview: UIImage?
    @State private var saving = false
    @State private var error: String?
    @State private var dragStart: CropBox?

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
                Group {
                    Section {
                        previewArea
                            .frame(height: 280)
                            .listRowInsets(EdgeInsets())
                    }
                    cropControls
                    if let error {
                        Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(WorkspaceStyle.destructive) }
                    }
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
            .navigationTitle("Crop photo")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(saving) }
                ToolbarItem(placement: .confirmationAction) {
                    if saving {
                        ProgressView()
                    } else {
                        Button("Save") { save() }.fontWeight(.semibold).disabled(preview == nil || edits.crop == photo.edits?.crop)
                    }
                }
            }
            .task {
                do {
                    let data = try await api.previewPhotoEdits(
                        listingID: listingID, photoID: photo.id, edits: photo.edits ?? PhotoEdits(), crop: false
                    )
                    preview = await Task.detached(priority: .userInitiated) {
                        PhotoPreparer.thumbnail(from: data, maxPixels: 2400)
                    }.value
                    if preview == nil { error = "Couldn't load this photo" }
                } catch is CancellationError {
                } catch {
                    self.error = error.localizedDescription
                }
            }
        }
        .interactiveDismissDisabled(saving)
    }

    @ViewBuilder
    private var previewArea: some View {
        ZStack {
            Color.black
            if let preview {
                GeometryReader { geometry in
                    let fitted = fit(preview.size, in: geometry.size)
                    ZStack(alignment: .topLeading) {
                        Image(uiImage: preview).resizable().scaledToFit()
                            .frame(width: fitted.width, height: fitted.height)
                        cropOverlay(size: fitted)
                    }
                    .frame(width: fitted.width, height: fitted.height)
                    .position(x: geometry.size.width / 2, y: geometry.size.height / 2)
                }
            } else if error == nil {
                ProgressView().tint(.white)
            }
        }
        .accessibilityLabel("Preview")
    }

    private func fit(_ image: CGSize, in box: CGSize) -> CGSize {
        guard image.width > 0, image.height > 0 else { return box }
        let scale = min(box.width / image.width, box.height / image.height)
        return CGSize(width: image.width * scale, height: image.height * scale)
    }

    private var frameAspect: Double {
        guard let preview, preview.size.height > 0 else { return 1 }
        return preview.size.width / preview.size.height
    }

    private var aspect: Double? { CropMath.aspects.first { $0.id == aspectID }?.aspect ?? nil }

    private func cropOverlay(size: CGSize) -> some View {
        let box = edits.crop ?? CropMath.full
        let rect = CGRect(x: box.x * size.width, y: box.y * size.height, width: box.width * size.width, height: box.height * size.height)
        return ZStack(alignment: .topLeading) {
            Path { path in
                path.addRect(CGRect(origin: .zero, size: size))
                path.addRect(rect)
            }
            .fill(Color.black.opacity(0.5), style: FillStyle(eoFill: true))
            Rectangle().stroke(Color.white, lineWidth: 2)
                .frame(width: rect.width, height: rect.height)
                .contentShape(Rectangle())
                .offset(x: rect.minX, y: rect.minY)
                .gesture(dragGesture(.move, size: size))
            ForEach(Array([(CropMath.Handle.nw, rect.minX, rect.minY), (.ne, rect.maxX, rect.minY),
                           (.sw, rect.minX, rect.maxY), (.se, rect.maxX, rect.maxY)].enumerated()), id: \.offset) { _, corner in
                Circle().fill(Color.white).frame(width: 22, height: 22)
                    .frame(width: 44, height: 44).contentShape(Rectangle())
                    .offset(x: corner.1 - 22, y: corner.2 - 22)
                    .gesture(dragGesture(corner.0, size: size))
            }
        }
        .accessibilityElement()
        .accessibilityLabel("Crop area")
        .accessibilityValue(edits.crop == nil ? "Whole photo" : "Cropped")
    }

    private func dragGesture(_ handle: CropMath.Handle, size: CGSize) -> some Gesture {
        DragGesture(minimumDistance: 1)
            .onChanged { value in
                let start = dragStart ?? (edits.crop ?? CropMath.full)
                if dragStart == nil { dragStart = start }
                edits.crop = CropMath.drag(start, handle, dx: value.translation.width / size.width,
                                           dy: value.translation.height / size.height, aspect: aspect, frameAspect: frameAspect)
            }
            .onEnded { _ in dragStart = nil }
    }

    @ViewBuilder
    private var cropControls: some View {
        Section {
            Picker("Aspect", selection: Binding(get: { aspectID }, set: { id in
                aspectID = id
                if let next = CropMath.aspects.first(where: { $0.id == id })?.aspect {
                    edits.crop = CropMath.centered(next, frameAspect: frameAspect)
                }
            })) {
                ForEach(CropMath.aspects, id: \.id) { Text($0.label).tag($0.id) }
            }
            .pickerStyle(.segmented)
            .disabled(preview == nil)
            if edits.crop != nil {
                Button("Clear crop") {
                    edits.crop = nil
                    aspectID = "free"
                }
            }
        } footer: {
            Text("Drag the box or its corners. Edits always start from your original photo, so you can revert at any time.")
        }
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
                Group {
                    Section {
                        ForEach(Array(order.enumerated()), id: \.element.id) { index, photo in
                            HStack(spacing: 12) {
                                RemotePhoto(api: api, photoID: photo.id, maxPixels: 192, version: photo.version)
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
                        Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(WorkspaceStyle.destructive) }
                    }
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
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
