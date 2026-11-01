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
                    Button("Edit", systemImage: "slider.horizontal.3") { adjusting = current }
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

/// The desktop photo editor (PhotoEditor.tsx): Crop, Adjust and Background,
/// previewed by the server from the original upload before anything is saved.
struct PhotoAdjustSheet: View {
    let api: AnteroomAPI
    let listingID: String
    let photo: ListingPhoto
    let onSaved: (ListingPhoto) -> Void

    private static let backgrounds: [(color: String, label: String)] = [
        ("#ffffff", "White"), ("#f4f4f5", "Light grey"), ("#e8e0d5", "Linen"), ("#000000", "Black"),
    ]

    @Environment(\.dismiss) private var dismiss
    @State private var edits: PhotoEdits
    @State private var tool = "crop"
    @State private var aspectID = "free"
    @State private var preview: UIImage?
    @State private var rendering = false
    @State private var saving = false
    @State private var error: String?
    @State private var dragStart: PhotoEdits.CropBox?

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
                    previewArea
                        .frame(height: 280)
                        .listRowInsets(EdgeInsets())
                }
                Section {
                    Picker("Tool", selection: $tool) {
                        Text("Crop").tag("crop")
                        Text("Adjust").tag("adjust")
                        Text("Background").tag("background")
                    }
                    .pickerStyle(.segmented)
                }
                switch tool {
                case "crop": cropControls
                case "background": backgroundControls
                default: adjustControls
                }
                if let error {
                    Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
                }
            }
            .navigationTitle("Edit photo")
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
            .task(id: "\(tool == "crop")|\(renderKey)") {
                // Wait for a slider or drag to settle before asking for a new render.
                do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
                rendering = true
                defer { rendering = false }
                do {
                    let data = try await api.previewPhotoEdits(listingID: listingID, photoID: photo.id, edits: renderEdits, crop: tool != "crop")
                    preview = UIImage(data: data)
                    error = nil
                } catch is CancellationError {
                } catch {
                    self.error = error.localizedDescription
                }
            }
        }
        .interactiveDismissDisabled(saving)
    }

    /// While cropping, the whole frame is rendered and the crop is drawn on it.
    private var renderEdits: PhotoEdits {
        guard tool == "crop" else { return edits }
        var whole = edits
        whole.crop = nil
        return whole
    }

    private var renderKey: String {
        let e = renderEdits
        return "\(e.rotate)|\(e.straighten)|\(e.exposure)|\(e.brightness)|\(e.contrast)|\(e.background ?? "")|\(String(describing: e.crop))"
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
                        if tool == "crop" { cropOverlay(size: fitted) }
                    }
                    .frame(width: fitted.width, height: fitted.height)
                    .position(x: geometry.size.width / 2, y: geometry.size.height / 2)
                }
            } else {
                ProgressView().tint(.white)
            }
            if rendering && preview != nil {
                Text(edits.background != nil && tool == "background" ? "Cutting out the item…" : "Updating…")
                    .font(.caption).padding(6).background(.regularMaterial, in: Capsule())
                    .frame(maxHeight: .infinity, alignment: .bottom).padding(8)
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
            HStack {
                Button("Rotate left", systemImage: "rotate.left") { turn(-90) }
                Spacer()
                Button("Rotate right", systemImage: "rotate.right") { turn(90) }
            }
            .buttonStyle(.borderless)
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

    @ViewBuilder
    private var adjustControls: some View {
        Section {
            slider("Straighten", value: $edits.straighten, range: -45...45, unit: "°")
            slider("Exposure", value: $edits.exposure, range: -2...2, unit: "")
            slider("Brightness", value: $edits.brightness, range: -100...100, unit: "")
            slider("Contrast", value: $edits.contrast, range: -100...100, unit: "")
        } footer: {
            Text("Edits always start from your original photo, so you can revert at any time.")
        }
    }

    @ViewBuilder
    private var backgroundControls: some View {
        Section {
            Button {
                edits.background = nil
            } label: {
                HStack {
                    Text("Original").foregroundStyle(.primary)
                    Spacer()
                    if edits.background == nil { Image(systemName: "checkmark").foregroundStyle(WorkspaceStyle.accent) }
                }
            }
            ForEach(Self.backgrounds, id: \.color) { option in
                Button {
                    edits.background = option.color
                } label: {
                    HStack {
                        RoundedRectangle(cornerRadius: 4).fill(Color(hex: option.color))
                            .overlay(RoundedRectangle(cornerRadius: 4).stroke(WorkspaceStyle.border))
                            .frame(width: 22, height: 22)
                        Text(option.label).foregroundStyle(.primary)
                        Spacer()
                        if edits.background == option.color { Image(systemName: "checkmark").foregroundStyle(WorkspaceStyle.accent) }
                    }
                }
                .accessibilityLabel("\(option.label) background")
            }
            ColorPicker("Other colour", selection: Binding(
                get: { Color(hex: edits.background ?? "#ffffff") },
                set: { edits.background = $0.hexString }
            ), supportsOpacity: false)
        } header: {
            Text("Behind the item")
        } footer: {
            Text("Cuts out the item and puts a plain colour behind it.")
        }
    }

    private func turn(_ degrees: Int) {
        edits.rotate = ((edits.rotate + degrees) % 360 + 360) % 360
        // The crop turns with the picture (PhotoEdits.turned on the server).
        if var crop = edits.crop {
            let turns = ((degrees / 90) % 4 + 4) % 4
            for _ in 0..<turns {
                crop = .init(x: max(0, 1 - crop.y - crop.height), y: crop.x, width: crop.height, height: crop.width)
            }
            edits.crop = crop
        }
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

extension Color {
    /// `#rrggbb`.
    init(hex: String) {
        let value = UInt32(hex.trimmingCharacters(in: CharacterSet(charactersIn: "#")), radix: 16) ?? 0xFFFFFF
        self.init(red: Double((value >> 16) & 255) / 255, green: Double((value >> 8) & 255) / 255, blue: Double(value & 255) / 255)
    }

    /// `#rrggbb` for the server's background colour.
    var hexString: String {
        let resolved = UIColor(self)
        var red: CGFloat = 0, green: CGFloat = 0, blue: CGFloat = 0, alpha: CGFloat = 0
        resolved.getRed(&red, green: &green, blue: &blue, alpha: &alpha)
        func byte(_ value: CGFloat) -> Int { Int((max(0, min(1, value)) * 255).rounded()) }
        return String(format: "#%02x%02x%02x", byte(red), byte(green), byte(blue))
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
