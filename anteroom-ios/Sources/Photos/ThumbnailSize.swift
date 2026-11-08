import Foundation

/// The thumbnail sizes the server keeps in Storage beside each photo
/// (`KEPT_THUMB_SIZES`). Any other size is remade from the full photo on every
/// request, so the app only asks for these.
enum ThumbnailSize {
    static let stored = [96, 192, 384]

    /// The smallest stored size that covers `pixels`, or the largest one.
    static func snapped(_ pixels: Int) -> Int {
        stored.first { pixels <= $0 } ?? stored[stored.count - 1]
    }

    /// Sizes whose image can stand in for a request of `pixels`, best first:
    /// the size itself, then larger ones, which only scale down.
    static func reusable(for pixels: Int) -> [Int] {
        let wanted = snapped(pixels)
        return stored.filter { $0 >= wanted }
    }

    /// A smaller size to show while the requested one loads, largest first.
    static func placeholders(for pixels: Int) -> [Int] {
        let wanted = snapped(pixels)
        return stored.filter { $0 < wanted }.reversed()
    }

    /// The memory and disk cache key: the photo, its content version and the size.
    static func key(photoID: String, version: String?, size: Int) -> String {
        "\(photoID)@\(size)@\(version ?? "")"
    }
}
