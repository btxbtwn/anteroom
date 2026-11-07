import ImageIO
import UIKit

/// Turns picked or captured images (HEIC, PNG, JPEG) into an upright JPEG no
/// larger than the server keeps, so uploads stay small on cellular.
enum PhotoPreparer {
    static let maxPixels = 2400

    static func jpeg(from data: Data) -> Data? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        return jpeg(from: source, maxPixels: maxPixels, quality: 0.85)
    }

    /// A camera photo, drawn upright at no more than `maxPixels` on its long
    /// side and encoded once. Call it off the main thread.
    static func jpeg(from image: UIImage) -> Data? {
        let size = fitted(image.size, maxPixels: maxPixels)
        guard size.width > 0, size.height > 0 else { return nil }
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        // Drawing applies the photo's orientation, so the JPEG needs no rotation tag.
        let renderer = UIGraphicsImageRenderer(size: size, format: format)
        return renderer.jpegData(withCompressionQuality: 0.85) { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
    }

    /// `size` scaled down (never up) so its long side is at most `maxPixels`, in whole pixels.
    static func fitted(_ size: CGSize, maxPixels: Int) -> CGSize {
        let longest = max(size.width, size.height)
        guard longest > 0 else { return .zero }
        let scale = min(1, CGFloat(maxPixels) / longest)
        return CGSize(width: max(1, (size.width * scale).rounded()), height: max(1, (size.height * scale).rounded()))
    }

    /// A small decoded image for thumbnails, without holding the full bitmap.
    static func thumbnail(from data: Data, maxPixels: Int) -> UIImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = scaled(source, maxPixels: maxPixels) else { return nil }
        return UIImage(cgImage: image)
    }

    private static func jpeg(from source: CGImageSource, maxPixels: Int, quality: CGFloat) -> Data? {
        guard let image = scaled(source, maxPixels: maxPixels) else { return nil }
        return UIImage(cgImage: image).jpegData(compressionQuality: quality)
    }

    private static func scaled(_ source: CGImageSource, maxPixels: Int) -> CGImage? {
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixels,
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    }
}
