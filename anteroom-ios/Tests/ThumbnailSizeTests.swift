import Foundation

/// The app asks only for thumbnail sizes the server keeps, and reuses a kept
/// image at the same or a larger size.
@main
struct ThumbnailSizeTests {
    static func main() {
        precondition(ThumbnailSize.stored == [96, 192, 384])
        precondition(ThumbnailSize.snapped(48) == 96 && ThumbnailSize.snapped(96) == 96)
        precondition(ThumbnailSize.snapped(97) == 192 && ThumbnailSize.snapped(120) == 192)
        precondition(ThumbnailSize.snapped(192) == 192 && ThumbnailSize.snapped(200) == 384)
        precondition(ThumbnailSize.snapped(400) == 384 && ThumbnailSize.snapped(2400) == 384)

        // A request is served by its own size or a larger one, best first.
        precondition(ThumbnailSize.reusable(for: 120) == [192, 384])
        precondition(ThumbnailSize.reusable(for: 400) == [384])
        precondition(ThumbnailSize.reusable(for: 50) == [96, 192, 384])
        // A smaller copy only stands in while the right size loads, largest first.
        precondition(ThumbnailSize.placeholders(for: 384) == [192, 96])
        precondition(ThumbnailSize.placeholders(for: 96).isEmpty)

        // Keys name the photo, its version and the size, so an edited photo is a new key.
        let key = ThumbnailSize.key(photoID: "p1", version: "abc", size: 192)
        precondition(key == "p1@192@abc")
        precondition(key != ThumbnailSize.key(photoID: "p1", version: "def", size: 192))
        precondition(ThumbnailSize.key(photoID: "p1", version: nil, size: 96) == "p1@96@")
        print("Thumbnail size tests passed")
    }
}
