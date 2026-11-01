import Foundation

@main
struct CropMathTests {
    static func main() {
        func near(_ a: Double, _ b: Double) -> Bool { abs(a - b) < 1e-9 }
        // A square crop centered in a 4:3 landscape frame spans the full height.
        let square = CropMath.centered(1, frameAspect: 4.0 / 3)
        precondition(near(square.width, 0.75) && near(square.height, 1) && near(square.x, 0.125) && square.y == 0)
        // In a portrait frame a 4:3 crop spans the full width.
        let wide = CropMath.centered(4.0 / 3, frameAspect: 3.0 / 4)
        precondition(near(wide.width, 1) && near(wide.height, 0.5625) && near(wide.y, 0.21875))
        // Moving stays inside the frame.
        let moved = CropMath.drag(.init(x: 0.5, y: 0.5, width: 0.4, height: 0.4), .move, dx: 0.5, dy: -0.9, aspect: nil, frameAspect: 1)
        precondition(near(moved.x, 0.6) && moved.y == 0)
        // A free corner resizes against the opposite corner, never below the minimum.
        let shrunk = CropMath.drag(.init(x: 0.2, y: 0.2, width: 0.5, height: 0.5), .se, dx: -0.6, dy: -0.1, aspect: nil, frameAspect: 1)
        precondition(near(shrunk.width, CropMath.minSide) && near(shrunk.height, 0.4) && near(shrunk.x, 0.2))
        let northwest = CropMath.drag(.init(x: 0.2, y: 0.2, width: 0.5, height: 0.5), .nw, dx: -0.1, dy: -0.1, aspect: nil, frameAspect: 1)
        precondition(near(northwest.x, 0.1) && near(northwest.y, 0.1) && near(northwest.width, 0.6))
        // A fixed aspect keeps its shape and fits the room left.
        let kept = CropMath.drag(.init(x: 0, y: 0, width: 0.5, height: 0.5), .se, dx: 0.4, dy: 0, aspect: 1, frameAspect: 1)
        precondition(near(kept.width, 0.9) && near(kept.height, 0.9))
        let capped = CropMath.drag(.init(x: 0, y: 0.6, width: 0.2, height: 0.2), .se, dx: 0.5, dy: 0, aspect: 1, frameAspect: 1)
        precondition(near(capped.height, 0.4) && near(capped.width, 0.4))
        print("Crop math tests passed")
    }
}
