import Foundation

/// The desktop crop arithmetic (photoEdits.ts): boxes are fractions of the
/// turned, straightened frame.
enum CropMath {
    static let aspects: [(id: String, label: String, aspect: Double?)] = [
        ("free", "Free", nil), ("1:1", "Square", 1), ("4:5", "4:5", 4.0 / 5), ("3:4", "3:4", 3.0 / 4), ("4:3", "4:3", 4.0 / 3),
    ]
    static let minSide = 0.05
    static let full = PhotoEdits.CropBox(x: 0, y: 0, width: 1, height: 1)

    enum Handle { case move, nw, ne, sw, se }

    /// The largest centered crop of `aspect` inside a frame of `frameAspect` (width / height).
    static func centered(_ aspect: Double, frameAspect: Double) -> PhotoEdits.CropBox {
        if frameAspect > aspect {
            let width = aspect / frameAspect
            return .init(x: (1 - width) / 2, y: 0, width: width, height: 1)
        }
        let height = frameAspect / aspect
        return .init(x: 0, y: (1 - height) / 2, width: 1, height: height)
    }

    /// Drags by `dx`/`dy` (fractions of the frame). Corners resize against the
    /// opposite corner, keep `aspect` when one is chosen, and stay inside the frame.
    static func drag(_ box: PhotoEdits.CropBox, _ handle: Handle, dx: Double, dy: Double,
                     aspect: Double?, frameAspect: Double) -> PhotoEdits.CropBox {
        func clamp(_ value: Double, _ low: Double, _ high: Double) -> Double { min(high, max(low, value)) }
        if handle == .move {
            return .init(x: clamp(box.x + dx, 0, 1 - box.width), y: clamp(box.y + dy, 0, 1 - box.height),
                         width: box.width, height: box.height)
        }
        let west = handle == .nw || handle == .sw
        let north = handle == .nw || handle == .ne
        let anchorX = west ? box.x + box.width : box.x
        let anchorY = north ? box.y + box.height : box.y
        let roomX = west ? anchorX : 1 - anchorX
        let roomY = north ? anchorY : 1 - anchorY
        var width = clamp(box.width + (west ? -dx : dx), minSide, roomX)
        var height = clamp(box.height + (north ? -dy : dy), minSide, roomY)
        if let aspect {
            let ratio = frameAspect / aspect
            height = width * ratio
            if height > roomY {
                height = roomY
                width = height / ratio
            }
        }
        return .init(x: west ? anchorX - width : anchorX, y: north ? anchorY - height : anchorY, width: width, height: height)
    }
}
