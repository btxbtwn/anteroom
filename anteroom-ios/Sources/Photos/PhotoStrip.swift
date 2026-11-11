import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

/// The listing's photos, with buttons to add from the camera, library or Files.
/// Photos save as soon as they upload, separately from Save. Like the hosted
/// web app, new photos can be added while the listing is a draft.
///
/// The editor reads the photos with the listing and hands them in. Until they
/// arrive, the inventory row's cover stands in for them.
struct PhotoStrip: View {
    let api: AnteroomAPI
    let listingID: String
    @Binding var photos: [ListingPhoto]
    var loaded = true
    var cover: (id: String, version: String?)? = nil
    var canAdd = true

    @State private var picked: [PhotosPickerItem] = []
    @State private var uploading = 0
    @State private var showingCamera = false
    @State private var showingFiles = false
    @State private var error: String?
    /// Prepared photos that didn't upload, kept for Retry.
    @State private var failedUploads: [Data] = []
    @State private var uploadMessage: String?
    @State private var uploadFailures = 0
    @State private var photoToDelete: ListingPhoto?

    /// The server keeps at most 20 photos per item (`MAX_PHOTO_COUNT`).
    private static let limit = 20
    @State private var showAll = false
    @State private var viewing: String?
    @State private var arranging = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !loaded {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 8)], spacing: 8) {
                    if let cover {
                        RemotePhoto(api: api, photoID: cover.id, maxPixels: 384, version: cover.version)
                            .frame(height: 136)
                            .clipped()
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                            .accessibilityLabel("Cover photo")
                    } else {
                        ProgressView().frame(maxWidth: .infinity, minHeight: 136)
                            .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 8))
                    }
                }
            } else if photos.isEmpty && uploading == 0 {
                VStack(spacing: 10) {
                    Image(systemName: "photo.on.rectangle.angled").font(.title2)
                    Text(canAdd ? "Add photos of your item" : "No photos").font(.subheadline.weight(.medium))
                    if canAdd {
                        Text("Use the camera or choose photos from your library or Files.")
                            .font(.footnote).multilineTextAlignment(.center)
                    }
                }
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, minHeight: 144)
                .padding(16)
                .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(WorkspaceStyle.border, style: StrokeStyle(lineWidth: 1, dash: [5, 4])))
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 8)], spacing: 8) {
                    ForEach(showAll ? photos : Array(photos.prefix(6))) { photo in
                        Button { viewing = photo.id } label: {
                            RemotePhoto(api: api, photoID: photo.id, maxPixels: 384, version: photo.version)
                                .frame(height: 136)
                                .clipped()
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                            .buttonStyle(.plain)
                            .accessibilityLabel(photo.id == photos.first?.id ? "Cover photo" : "Listing photo")
                            .accessibilityHint("Opens the photo to view, rotate or adjust it")
                            .overlay(alignment: .topTrailing) {
                                Button("Delete photo", systemImage: "trash", role: .destructive) { photoToDelete = photo }
                                    .labelStyle(.iconOnly)
                                    .frame(width: 44, height: 44)
                                    .background(.regularMaterial, in: Circle())
                            }
                    }
                    ForEach(0..<uploading, id: \.self) { _ in
                        ProgressView().frame(maxWidth: .infinity, minHeight: 136)
                            .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 8))
                    }
                }
                HStack {
                    if photos.count > 6 {
                        Button(showAll ? "Show fewer photos" : "Show all \(photos.count) photos") { showAll.toggle() }
                            .frame(minHeight: 44)
                    }
                    Spacer()
                    if photos.count > 1 {
                        Button("Arrange", systemImage: "arrow.up.arrow.down") { arranging = true }
                            .frame(minHeight: 44)
                            .accessibilityHint("Change the photo order and cover")
                    }
                }
            }

            if canAdd {
            // One row when it fits; otherwise the counter, then the buttons, wrap.
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { addButtons; photoCount }
                VStack(alignment: .leading, spacing: 12) { HStack(spacing: 12) { addButtons }; photoCount }
                VStack(alignment: .leading, spacing: 12) { addButtons; photoCount }
            }
            .disabled(remaining == 0)
            } else {
                Text("New photos can be added while this listing is a draft.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            if let uploadMessage {
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    Text(uploadMessage)
                        .font(.footnote)
                        .foregroundStyle(.red)
                    Spacer(minLength: 0)
                    if !failedUploads.isEmpty {
                        Button("Retry") { Task { await retryFailed() } }
                            .font(.footnote.weight(.semibold))
                            .frame(minHeight: 44)
                            .disabled(uploading > 0)
                            .accessibilityLabel("Retry \(failedUploads.count) \(failedUploads.count == 1 ? "photo" : "photos")")
                    }
                }
            }
            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
        .sensoryFeedback(.error, trigger: uploadFailures)
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
        .fullScreenCover(item: Binding(get: { viewing.map(PhotoSelection.init) }, set: { viewing = $0?.id })) { start in
            PhotoViewer(api: api, listingID: listingID, photos: $photos, selection: start.id) { delete($0) }
        }
        .sheet(isPresented: $arranging) {
            ArrangePhotosSheet(api: api, listingID: listingID, photos: $photos)
        }
        .onChange(of: picked) { _, items in
            guard !items.isEmpty else { return }
            picked = []
            Task { await upload(items) }
        }
        .fileImporter(isPresented: $showingFiles, allowedContentTypes: [.image], allowsMultipleSelection: true) { result in
            switch result {
            case .success(let urls):
                Task { await uploadFiles(Array(urls.prefix(remaining))) }
            case .failure(let failure):
                error = failure.localizedDescription
            }
        }
        .fullScreenCover(isPresented: $showingCamera) {
            CameraPicker { image in
                Task { await uploadCaptured(image) }
            }
            .ignoresSafeArea()
        }
    }

    @ViewBuilder private var addButtons: some View {
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
        Button { showingFiles = true } label: {
            Label("Files", systemImage: "folder").frame(minHeight: 44)
        }
            .buttonStyle(.bordered)
            .accessibilityLabel("Add photos from Files")
    }

    private var photoCount: some View {
        Text("\(photos.count)/\(Self.limit)")
            .accessibilityLabel("\(photos.count) of \(Self.limit) photos")
            .font(.footnote)
            .foregroundStyle(.secondary)
    }

    private var remaining: Int { max(0, Self.limit - photos.count - uploading - failedUploads.count) }

    private func upload(_ items: [PhotosPickerItem]) async {
        await send(items) { item in
            guard let data = try? await item.loadTransferable(type: Data.self) else { return nil }
            return await Task.detached(priority: .userInitiated) { PhotoPreparer.jpeg(from: data) }.value
        }
    }

    /// Photos picked in Files (iCloud Drive, On My iPhone, connected drives).
    /// The URLs sit outside the app's sandbox, so each is opened only while it is read.
    private func uploadFiles(_ urls: [URL]) async {
        await send(urls) { url in
            await Task.detached(priority: .userInitiated) {
                let scoped = url.startAccessingSecurityScopedResource()
                defer { if scoped { url.stopAccessingSecurityScopedResource() } }
                guard let data = try? Data(contentsOf: url) else { return nil }
                return PhotoPreparer.jpeg(from: data)
            }.value
        }
    }

    private func uploadCaptured(_ image: UIImage) async {
        let photo = CapturedPhoto(image: image)
        await send([photo]) { captured in
            await Task.detached(priority: .userInitiated) { PhotoPreparer.jpeg(from: captured.image) }.value
        }
    }

    /// Sends the kept photos again, as they were prepared.
    private func retryFailed() async {
        let kept = failedUploads
        failedUploads = []
        await send(kept) { $0 }
    }

    /// Uploads a batch, keeping iOS from suspending it if the seller switches
    /// apps, then says how many didn't make it.
    private func send<Source>(_ sources: [Source], prepare: @MainActor (Source) async -> Data?) async {
        uploading += sources.count
        PhotoUploadTracker.shared.begin(listingID, count: sources.count)
        let report = await withBackgroundTime("Upload photos") {
            await PhotoUploadReport.run(sources, prepare: prepare,
                                        upload: { try await api.uploadPhoto(listingID: listingID, jpeg: $0) },
                                        uploaded: { photos.append($0) },
                                        finished: {
                                            uploading -= 1
                                            PhotoUploadTracker.shared.end(listingID)
                                        })
        }
        failedUploads += report.failed
        uploadMessage = report.message
        if report.problems > 0 { uploadFailures += 1 }
    }

    private func delete(_ photo: ListingPhoto) {
        Task {
            do {
                try await api.deletePhoto(listingID: listingID, photoID: photo.id)
                photos.removeAll { $0.id == photo.id }
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// Loads a photo with the seller's token; the photo URLs are not public.
/// It asks for the nearest size the server keeps, shows a larger copy already
/// in memory at once, and shows a smaller one while the right size loads.
/// `urgent` is for the open listing, whose photos go ahead of inventory covers.
struct RemotePhoto: View {
    let api: AnteroomAPI
    let photoID: String
    let maxPixels: Int
    var version: String? = nil
    var urgent = true

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
        .task(id: "\(photoID)@\(version ?? "")") { await load() }
    }

    private func load() async {
        if let cached = PhotoCache.image(photoID: photoID, version: version, pixels: maxPixels) {
            image = cached
            return
        }
        // A smaller copy is better than a spinner while this size downloads.
        if let smaller = PhotoCache.placeholder(photoID: photoID, version: version, pixels: maxPixels) { image = smaller }
        failed = false
        let size = ThumbnailSize.snapped(maxPixels)
        do {
            let data = try await api.photoThumbnailData(photoID: photoID, size: size, version: version, urgent: urgent)
            let decoded = await Task.detached(priority: .userInitiated) {
                PhotoPreparer.thumbnail(from: data, maxPixels: size)
            }.value
            if let decoded {
                PhotoCache.store(decoded, photoID: photoID, version: version, size: size)
                image = decoded
            } else if image == nil {
                failed = true
            }
        } catch {
            // Leaving the screen is not a failure; anything else shows the broken-photo mark.
            if !error.isCancellation && image == nil { failed = true }
        }
    }
}

/// Decoded thumbnails by photo, version and stored size, for this sign-in.
@MainActor
enum PhotoCache {
    static let images: NSCache<NSString, UIImage> = {
        let cache = NSCache<NSString, UIImage>()
        cache.countLimit = 300
        return cache
    }()

    /// The image at the requested size, or a larger one, which only scales down.
    static func image(photoID: String, version: String?, pixels: Int) -> UIImage? {
        for size in ThumbnailSize.reusable(for: pixels) {
            if let image = images.object(forKey: key(photoID, version, size)) { return image }
        }
        return nil
    }

    /// The largest smaller copy, to show while the requested size loads.
    static func placeholder(photoID: String, version: String?, pixels: Int) -> UIImage? {
        for size in ThumbnailSize.placeholders(for: pixels) {
            if let image = images.object(forKey: key(photoID, version, size)) { return image }
        }
        return nil
    }

    static func store(_ image: UIImage, photoID: String, version: String?, size: Int) {
        images.setObject(image, forKey: key(photoID, version, size))
    }

    private static func key(_ photoID: String, _ version: String?, _ size: Int) -> NSString {
        ThumbnailSize.key(photoID: photoID, version: version, size: size) as NSString
    }
}

/// A camera photo on its way to `PhotoPreparer`; UIKit images are safe to read from any thread.
private struct CapturedPhoto: @unchecked Sendable {
    let image: UIImage
}

private struct PhotoSelection: Identifiable {
    let id: String
}
