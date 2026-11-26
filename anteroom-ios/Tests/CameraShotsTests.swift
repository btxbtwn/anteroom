import Foundation

/// The camera stays open between shots: each shot joins the tray in the
/// order it was taken, even when a later one finishes preparing first, any
/// shot can be discarded, and the tray stops at the photo limit.
@main
struct CameraShotsTests {
    static func main() {
        var shots = CameraShots<String>(limit: 3)
        precondition(shots.isEmpty && shots.canTake && !shots.isPreparing)
        precondition(shots.countLabel == "No photos yet")
        precondition(shots.doneTitle == "Done")

        // Two shots taken; the second finishes preparing first. Order follows the shutter.
        let first = shots.begin()!
        let second = shots.begin()!
        precondition(shots.isPreparing && shots.isEmpty && shots.shotPending(capturing: false))
        shots.finish(second, with: "B")
        shots.finish(first, with: "A")
        precondition(shots.all == ["A", "B"] && !shots.isPreparing)
        precondition(shots.countLabel == "2 photos" && shots.doneTitle == "Add 2")

        // Done waits for a shot between the shutter and the camera handing it over.
        precondition(shots.shotPending(capturing: true) && !shots.shotPending(capturing: false))

        // Shots still preparing count toward the limit.
        let third = shots.begin()!
        precondition(!shots.canTake && shots.begin() == nil)
        // A shot that couldn't be prepared frees its place.
        shots.finish(third, with: nil)
        precondition(shots.canTake && shots.all == ["A", "B"])

        // Discarding one keeps the others in order.
        let c = shots.begin()!
        shots.finish(c, with: "C")
        precondition(shots.all == ["A", "B", "C"])
        shots.discard(shots.entries[1].id)
        precondition(shots.all == ["A", "C"] && shots.countLabel == "2 photos")
        shots.discard(shots.entries[0].id)
        precondition(shots.countLabel == "1 photo" && shots.doneTitle == "Add 1")

        // Without a limit (Bulk listing) the camera never stops taking shots.
        var bulk = CameraShots<Int>(limit: nil)
        for n in 0..<50 { let t = bulk.begin()!; bulk.finish(t, with: n) }
        precondition(bulk.canTake && bulk.all == Array(0..<50))

        // A limit of zero takes nothing.
        var full = CameraShots<Int>(limit: 0)
        precondition(!full.canTake && full.begin() == nil)
        print("Camera shot tray tests passed")
    }
}
