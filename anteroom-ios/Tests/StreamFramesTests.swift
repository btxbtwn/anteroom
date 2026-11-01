import Foundation

@main
struct StreamFramesTests {
    static func main() async throws {
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
        // Byte streams keep the blank lines that end each event.
        let text = ": keepalive\n\nevent: status\ndata: Reading\n\ndata: a\ndata: b\n\nevent: error\r\ndata: Stopped\r\n\r\ndata: tail"
        let bytes = AsyncStream<UInt8> { stream in
            for byte in Array(text.utf8) { stream.yield(byte) }
            stream.finish()
        }
        var seen: [StreamFrame] = []
        try await bytes.forEachFrame { seen.append($0); return true }
        precondition(seen == [StreamFrame(event: "status", data: "Reading"), StreamFrame(event: "message", data: "a\nb"),
                              StreamFrame(event: "error", data: "Stopped"), StreamFrame(event: "message", data: "tail")])
        print("StreamFrames tests passed")
    }
}
