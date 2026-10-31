import PhotosUI
import SwiftUI

/// The draft's photos in a horizontal row, with buttons to add from the camera
/// or library. Photos save as soon as they upload, separately from Save.
struct PhotoStrip: View {
    let api: AnteroomAPI
    let draftID: String

    @State private var photos: [DraftPhoto] = []
    @State private var picked: [PhotosPickerItem] = []
    @State private var uploading = 0
    @State private var showingCamera = false
    @State private var error: String?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var photoToDelete: DraftPhoto?

    private static let limit = 30
    @State private var showAll = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if photos.isEmpty && uploading == 0 {
                VStack(spacing: 10) {
                    Image(systemName: "photo.on.rectangle.angled").font(.title2)
                    Text("Add photos of your item").font(.subheadline.weight(.medium))
                    Text("Use the camera or choose photos from your library.")
                        .font(.footnote).multilineTextAlignment(.center)
                }
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, minHeight: 144)
                .padding(16)
                .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(WorkspaceStyle.border, style: StrokeStyle(lineWidth: 1, dash: [5, 4])))
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 88), spacing: 8)], spacing: 8) {
                    ForEach(showAll ? photos : Array(photos.prefix(6))) { photo in
                        RemotePhoto(api: api, draftID: draftID, photoID: photo.id, maxPixels: 300)
                            .frame(height: 104)
                            .clipped()
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                            .accessibilityLabel("Listing photo")
                            .overlay(alignment: .topTrailing) {
                                Button("Delete photo", systemImage: "trash", role: .destructive) { photoToDelete = photo }
                                    .labelStyle(.iconOnly)
                                    .frame(width: 44, height: 44)
                                    .background(.regularMaterial, in: Circle())
                            }
                    }
                    ForEach(0..<uploading, id: \.self) { _ in
                        ProgressView().frame(maxWidth: .infinity, minHeight: 104)
                            .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 8))
                    }
                }
                if photos.count > 6 {
                    Button(showAll ? "Show fewer photos" : "Show all \(photos.count) photos") { showAll.toggle() }
                        .frame(minHeight: 44)
                }
            }

            let controlsLayout = dynamicTypeSize.isAccessibilitySize
                ? AnyLayout(VStackLayout(alignment: .leading, spacing: 12))
                : AnyLayout(HStackLayout(spacing: 12))
            controlsLayout {
                if UIImagePickerController.isSourceTypeAvailable(.camera) {
                    Button { showingCamera = true } label: {
                        Label("Camera", systemImage: "camera").frame(minHeight: 44)
                    }
                        .accessibilityLabel("Take a listing photo")
                        .buttonStyle(.bordered)
                }
                PhotosPicker(selection: $picked, maxSelectionCount: remaining, matching: .images) {
                    Label("Library", systemImage: "photo.on.rectangle").frame(minHeight: 44)
                }
                .buttonStyle(.bordered)
                .accessibilityLabel("Add photos from library")
                Text("\(photos.count)/\(Self.limit)")
                    .accessibilityLabel("\(photos.count) of \(Self.limit) photos")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .disabled(remaining == 0)

            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
        .confirmationDialog("Delete this photo?", isPresented: Binding(
            get: { photoToDelete != nil },
            set: { if !$0 { photoToDelete = nil } }
        ), titleVisibility: .visible) {
            Button("Delete photo", role: .destructive) {
                if let photo = photoToDelete { delete(photo) }
                photoToDelete = nil
            }
            Button("Cancel", role: .cancel) { photoToDelete = nil }
        }
        .padding(.vertical, 4)
        .task { await load() }
        .onChange(of: picked) { _, items in
            guard !items.isEmpty else { return }
            picked = []
            Task { await upload(items) }
        }
        .fullScreenCover(isPresented: $showingCamera) {
            CameraPicker { data in
                Task { await upload([data]) }
            }
            .ignoresSafeArea()
        }
    }

    private var remaining: Int { max(0, Self.limit - photos.count - uploading) }

    private func load() async {
        do {
            photos = try await api.photos(draftID: draftID)
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func upload(_ items: [PhotosPickerItem]) async {
        uploading += items.count
        for item in items {
            defer { uploading -= 1 }
            guard let data = try? await item.loadTransferable(type: Data.self) else {
                error = "Couldn't read one of the photos."
                continue
            }
            await send(data)
        }
    }

    private func upload(_ captured: [Data]) async {
        uploading += captured.count
        for data in captured {
            defer { uploading -= 1 }
            await send(data)
        }
    }

    private func send(_ original: Data) async {
        let jpeg = await Task.detached(priority: .userInitiated) {
            PhotoPreparer.jpeg(from: original)
        }.value
        guard let jpeg else {
            error = "That photo couldn't be opened."
            return
        }
        do {
            photos.append(try await api.uploadPhoto(draftID: draftID, jpeg: jpeg))
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func delete(_ photo: DraftPhoto) {
        Task {
            do {
                try await api.deletePhoto(draftID: draftID, photoID: photo.id)
                photos.removeAll { $0.id == photo.id }
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// Loads a photo with the seller's token; the photo URLs are not public.
struct RemotePhoto: View {
    let api: AnteroomAPI
    let draftID: String
    let photoID: String
    let maxPixels: Int

    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        ZStack {
            Rectangle().fill(.quaternary)
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
            } else if failed {
                Image(systemName: "photo").foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .task(id: photoID) { await load() }
    }

    private func load() async {
        let key = "\(photoID)@\(maxPixels)" as NSString
        if let cached = PhotoCache.images.object(forKey: key) {
            image = cached
            return
        }
        do {
            let data = try await api.photoThumbnailData(photoID: photoID, size: min(512, maxPixels))
            let size = maxPixels
            let decoded = await Task.detached(priority: .userInitiated) {
                PhotoPreparer.thumbnail(from: data, maxPixels: size)
            }.value
            if let decoded {
                PhotoCache.images.setObject(decoded, forKey: key)
                image = decoded
            } else {
                failed = true
            }
        } catch {
            failed = true
        }
    }
}

@MainActor
enum PhotoCache {
    static let images: NSCache<NSString, UIImage> = {
        let cache = NSCache<NSString, UIImage>()
        cache.countLimit = 200
        return cache
    }()
}
