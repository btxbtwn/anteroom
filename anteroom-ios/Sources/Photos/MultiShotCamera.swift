import SwiftUI
import UIKit

/// Where camera photos come from. Debug builds on the local sandbox, which
/// runs on a simulator without a camera, stand in a generated photo for each
/// shot so the tray can be tested end to end.
enum CameraSource {
    static var hasCamera: Bool { UIImagePickerController.isSourceTypeAvailable(.camera) }

    static var simulated: Bool {
        #if DEBUG
        !hasCamera && AppConfig.sandboxURL != nil
        #else
        false
        #endif
    }

    static var isAvailable: Bool { hasCamera || simulated }

    #if DEBUG
    /// A plain numbered photo for the sandbox's simulated shutter.
    static func sample(_ number: Int) -> UIImage {
        let colors: [UIColor] = [.systemBlue, .systemGreen, .systemOrange, .systemPink, .systemTeal]
        let size = CGSize(width: 900, height: 1200)
        return UIGraphicsImageRenderer(size: size).image { context in
            colors[number % colors.count].setFill()
            context.fill(CGRect(origin: .zero, size: size))
            let text = "\(number + 1)" as NSString
            text.draw(at: CGPoint(x: 360, y: 480), withAttributes: [.font: UIFont.boldSystemFont(ofSize: 220), .foregroundColor: UIColor.white])
        }
    }
    #endif
}

/// A photo taken with the camera, prepared for upload: the JPEG that uploads
/// (shrunk and encoded once by `PhotoPreparer`) and a small tray thumbnail.
struct CapturedShot: @unchecked Sendable {
    let jpeg: Data
    let thumbnail: UIImage?
}

/// The camera, kept open between shots like the system Camera. Each shot is
/// prepared off the main thread and joins a tray with a running count, where
/// any shot can be discarded. Done hands every kept shot over in the order
/// taken; Cancel hands over none.
struct MultiShotCamera: View {
    /// How many more photos fit (the listing's remaining places), or nil for no limit.
    let limit: Int?
    let onDone: ([CapturedShot]) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var shots: CameraShots<CapturedShot>
    @State private var camera = CameraControl()
    @State private var confirmCancel = false
    @State private var simulatedShots = 0

    init(limit: Int?, onDone: @escaping ([CapturedShot]) -> Void) {
        self.limit = limit
        self.onDone = onDone
        _shots = State(initialValue: CameraShots(limit: limit))
    }

    #if LAYOUT_REVIEW
    /// The tray with sample shots over a stand-in preview, for layout review.
    init(reviewing samples: [UIImage], limit: Int?) {
        self.limit = limit
        self.onDone = { _ in }
        var shots = CameraShots<CapturedShot>(limit: limit)
        for image in samples {
            if let id = shots.begin() { shots.finish(id, with: CapturedShot(jpeg: Data(), thumbnail: image)) }
        }
        _shots = State(initialValue: shots)
    }
    #endif

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if CameraSource.hasCamera {
                CameraPreview(control: camera) { image in capture(image) }
                    .ignoresSafeArea()
            } else {
                Label(CameraSource.simulated ? "Sandbox camera: each shot is a sample photo." : "The camera isn't available.",
                      systemImage: "camera")
                    .foregroundStyle(.white.opacity(0.7))
            }
            VStack(spacing: 0) {
                Spacer()
                controls
            }
        }
        .preferredColorScheme(.dark)
        .sensoryFeedback(.impact(weight: .light), trigger: shots.count)
        .confirmationDialog(shots.count <= 1 ? "Discard 1 photo?" : "Discard \(shots.count) photos?",
                            isPresented: $confirmCancel, titleVisibility: .visible) {
            Button("Discard", role: .destructive) { dismiss() }
            Button("Keep taking photos", role: .cancel) {}
        } message: {
            Text("They haven't been added to the listing.")
        }
    }

    private var controls: some View {
        VStack(spacing: 12) {
            if !shots.isEmpty || shots.isPreparing { tray }
            HStack {
                Button("Cancel") {
                    if shots.isEmpty && !pending { dismiss() } else { confirmCancel = true }
                }
                .frame(minWidth: 88, minHeight: 44, alignment: .leading)
                Spacer()
                shutter
                Spacer()
                Button {
                    onDone(shots.all)
                    dismiss()
                } label: {
                    if pending { ProgressView() } else { Text(shots.doneTitle).fontWeight(.semibold) }
                }
                .frame(minWidth: 88, minHeight: 44, alignment: .trailing)
                .disabled(pending)
                .accessibilityIdentifier("camera-done")
                .accessibilityLabel(shots.isEmpty ? "Done" : "Add \(shots.countLabel)")
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 20)
            Text(shots.canTake || shots.limit == nil ? shots.countLabel : "\(shots.countLabel) · That's the most this listing can hold")
                .font(.footnote)
                .foregroundStyle(.white.opacity(0.8))
                .accessibilityIdentifier("camera-count")
        }
        .padding(.top, 12)
        .padding(.bottom, 8)
        .background(.black.opacity(0.55))
    }

    /// A shot is between the shutter and the tray; Done would leave it behind.
    private var pending: Bool { shots.shotPending(capturing: camera.capturing) }

    private var shutter: some View {
        Button {
            #if DEBUG
            if CameraSource.simulated {
                capture(CameraSource.sample(simulatedShots))
                simulatedShots += 1
                return
            }
            #endif
            camera.shoot()
        } label: {
            ZStack {
                Circle().strokeBorder(.white, lineWidth: 4).frame(width: 74, height: 74)
                Circle().fill(.white).frame(width: 60, height: 60)
            }
            .opacity(shots.canTake && !camera.capturing ? 1 : 0.4)
        }
        .buttonStyle(.plain)
        .disabled(!shots.canTake || camera.capturing)
        .accessibilityLabel("Take photo")
        .accessibilityIdentifier("camera-shutter")
    }

    private var tray: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(Array(shots.entries.enumerated()), id: \.element.id) { index, entry in
                        thumbnail(entry, number: index + 1).id(entry.id)
                    }
                    if shots.isPreparing {
                        ProgressView().tint(.white).frame(width: 60, height: 60)
                            .background(.white.opacity(0.15), in: RoundedRectangle(cornerRadius: 6))
                            .id(Int.max)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, 8)
            }
            .onChange(of: shots.count) { _, _ in
                withAnimation { proxy.scrollTo(shots.isPreparing ? Int.max : shots.entries.last?.id, anchor: .trailing) }
            }
        }
    }

    private func thumbnail(_ entry: CameraShots<CapturedShot>.Entry, number: Int) -> some View {
        Group {
            if let image = entry.shot.thumbnail {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                Rectangle().fill(.white.opacity(0.15))
            }
        }
        .frame(width: 60, height: 60)
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .overlay(alignment: .topTrailing) {
            Button {
                withAnimation { shots.discard(entry.id) }
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(.white, .black.opacity(0.7))
                    .font(.title3)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .offset(x: 14, y: -14)
            .accessibilityLabel("Discard photo \(number)")
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Photo \(number)")
    }

    private func capture(_ image: UIImage) {
        guard let id = shots.begin() else { return }
        let photo = TakenPhoto(image: image)
        Task {
            let shot = await Task.detached(priority: .userInitiated) { () -> CapturedShot? in
                guard let jpeg = PhotoPreparer.jpeg(from: photo.image) else { return nil }
                return CapturedShot(jpeg: jpeg, thumbnail: PhotoPreparer.thumbnail(from: jpeg, maxPixels: 180))
            }.value
            withAnimation { shots.finish(id, with: shot) }
        }
    }
}

/// A camera photo on its way to `PhotoPreparer`; UIKit images are safe to read from any thread.
private struct TakenPhoto: @unchecked Sendable {
    let image: UIImage
}

/// Lets the SwiftUI shutter reach the camera's `takePicture()`.
@MainActor @Observable
final class CameraControl {
    fileprivate weak var picker: UIImagePickerController?
    /// True from the shutter until the camera hands the photo over; a second
    /// `takePicture()` meanwhile would be ignored.
    private(set) var capturing = false
    /// The fallback for the shot in flight; only that shot's timer may clear `capturing`.
    private var fallback: Task<Void, Never>?

    func shoot() {
        guard let picker, !capturing else { return }
        capturing = true
        picker.takePicture()
        // The camera can drop a shot (for example when interrupted); never leave the shutter stuck.
        fallback?.cancel()
        fallback = Task { [weak self] in
            try? await Task.sleep(for: .seconds(5))
            guard !Task.isCancelled else { return }
            self?.capturing = false
        }
    }

    fileprivate func captured() {
        fallback?.cancel()
        fallback = nil
        capturing = false
    }
}

/// The live camera preview without the system's controls, so taking a photo
/// does not stop at Retake / Use Photo and the camera stays open. The preview
/// is centered in the space it is given.
private struct CameraPreview: UIViewControllerRepresentable {
    let control: CameraControl
    let onCapture: (UIImage) -> Void

    func makeUIViewController(context: Context) -> PreviewContainer {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.cameraCaptureMode = .photo
        picker.showsCameraControls = false
        picker.delegate = context.coordinator
        control.picker = picker
        return PreviewContainer(picker: picker)
    }

    func updateUIViewController(_ controller: PreviewContainer, context: Context) {
        context.coordinator.parent = self
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    @MainActor
    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        var parent: CameraPreview

        init(_ parent: CameraPreview) {
            self.parent = parent
        }

        func imagePickerController(
            _ picker: UIImagePickerController,
            didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
        ) {
            parent.control.captured()
            if let image = info[.originalImage] as? UIImage {
                parent.onCapture(image)
            }
        }
    }

    /// Holds the picker as a child and centers its 4:3 preview.
    final class PreviewContainer: UIViewController {
        let picker: UIImagePickerController

        init(picker: UIImagePickerController) {
            self.picker = picker
            super.init(nibName: nil, bundle: nil)
        }

        @available(*, unavailable)
        required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

        override func viewDidLoad() {
            super.viewDidLoad()
            view.backgroundColor = .black
            addChild(picker)
            picker.view.frame = view.bounds
            picker.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
            view.addSubview(picker.view)
            picker.didMove(toParent: self)
        }

        override func viewDidLayoutSubviews() {
            super.viewDidLayoutSubviews()
            let width = view.bounds.width
            let previewHeight = width * 4 / 3
            let offset = max(0, (view.bounds.height - previewHeight) / 2)
            picker.cameraViewTransform = CGAffineTransform(translationX: 0, y: offset)
        }
    }
}
