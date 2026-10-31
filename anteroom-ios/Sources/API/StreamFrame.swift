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
