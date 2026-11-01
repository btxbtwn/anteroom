import Foundation

/// SSE framing is independent of UI and transport. Comments are keepalives,
/// multiline data is joined, and a blank line terminates each event.
struct StreamFrame: Equatable, Sendable {
    let event: String
    let data: String
}

struct StreamFrames {
    private var event = "message"
    private var lines: [String] = []

    mutating func accept(_ raw: String) -> StreamFrame? {
        let line = raw.hasSuffix("\r") ? String(raw.dropLast()) : raw
        if line.isEmpty {
            defer { event = "message"; lines = [] }
            return lines.isEmpty ? nil : StreamFrame(event: event, data: lines.joined(separator: "\n"))
        }
        if line.hasPrefix(":") { return nil }
        let parts = line.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
        let field = String(parts[0])
        var value = parts.count == 2 ? String(parts[1]) : ""
        if value.hasPrefix(" ") { value.removeFirst() }
        if field == "event" { event = value.isEmpty ? "message" : value }
        if field == "data" { lines.append(value) }
        return nil
    }
}

extension AsyncSequence where Element == UInt8 {
    /// Each event in a byte stream, in order. Foundation's `lines` skips blank
    /// lines, and a blank line is what ends an SSE event, so lines are split
    /// here. `body` returns false to stop reading. A last event the server
    /// closed without a blank line still arrives.
    func forEachFrame(_ body: (StreamFrame) async throws -> Bool) async throws {
        var frames = StreamFrames()
        var line: [UInt8] = []
        for try await byte in self {
            guard byte == 0x0A else {
                line.append(byte)
                continue
            }
            let text = String(decoding: line, as: UTF8.self)
            line.removeAll(keepingCapacity: true)
            if let frame = frames.accept(text), try await !body(frame) { return }
        }
        if !line.isEmpty, let frame = frames.accept(String(decoding: line, as: UTF8.self)), try await !body(frame) { return }
        if let frame = frames.accept("") { _ = try await body(frame) }
    }
}
