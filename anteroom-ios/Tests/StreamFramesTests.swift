import Foundation

@main
struct StreamFramesTests {
    static func main() {
        var parser = StreamFrames()
        precondition(parser.accept(": keepalive") == nil)
        precondition(parser.accept("") == nil)
        precondition(parser.accept("event: status") == nil)
        precondition(parser.accept("data: Checking photos") == nil)
        precondition(parser.accept("") == StreamFrame(event: "status", data: "Checking photos"))
        // Events must not inherit the preceding event's type.
        precondition(parser.accept("data: [DONE]") == nil)
        precondition(parser.accept("") == StreamFrame(event: "message", data: "[DONE]"))
        precondition(parser.accept("event: error\r") == nil)
        precondition(parser.accept("data: First line\r") == nil)
        precondition(parser.accept("data:  Second line\r") == nil)
        precondition(parser.accept("\r") == StreamFrame(event: "error", data: "First line\n Second line"))
        precondition(parser.accept("id: 42") == nil)
        precondition(parser.accept("retry: 1000") == nil)
        precondition(parser.accept("data") == nil)
        precondition(parser.accept("") == StreamFrame(event: "message", data: ""))
        // An event without data doesn't leak a type into the next event.
        precondition(parser.accept("event: error") == nil)
        precondition(parser.accept("") == nil)
        precondition(parser.accept("data: ✓ Café 👟") == nil)
        precondition(parser.accept("") == StreamFrame(event: "message", data: "✓ Café 👟"))
        print("StreamFrames tests passed")
    }
}
