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
