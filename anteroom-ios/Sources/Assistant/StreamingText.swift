import Foundation

/// What a streaming answer shows: every finished block, plus the block being
/// written up to its last whole word. Inline markup the model has opened but not
/// closed yet is closed here, so the reader never sees `**` turn into bold a
/// moment later. A table waits for its whole block, because a header row only
/// becomes a table once the divider row arrives.
///
/// The same rules as `anteroom/src/components/streamingText.ts`; keep the two
/// in step (their tests list the same cases).
enum StreamingText {
    static func display(_ raw: String) -> String {
        let text = raw.replacingOccurrences(of: "\r\n", with: "\n")
        let boundary = stableBoundary(text)
        let stable = String(text[..<boundary])
        let tail = text[boundary...]
        if tail.isEmpty { return stable }
        // An open code fence runs to the end of the answer; show its whole lines.
        if tail.contains(#/(?:^|\n) {0,3}(?:`{3,}|~{3,})[^\n]*\n/#) {
            guard let newline = tail.lastIndex(of: "\n") else { return stable }
            return stable + tail[...newline]
        }
        if tail.firstMatch(of: #/^[ \t]*\|/#) != nil { return stable }
        return stable + closeInlineMarkdown(wholeWords(tail))
    }

    /// The end of the last block that can no longer change shape: a blank line, a
    /// closed code fence, or the start of the next list item.
    private static func stableBoundary(_ text: String) -> String.Index {
        var open: (indent: Int, character: Character, length: Int)?
        var boundary = text.startIndex
        var lineStart = text.startIndex
        while true {
            let newline = text[lineStart...].firstIndex(of: "\n")
            let line = trimmingTrailingSpaces(text[lineStart..<(newline ?? text.endIndex)])
            if open == nil, lineStart > text.startIndex, startsListItem(line) {
                boundary = lineStart
            }
            guard let newline else { break }
            let next = text.index(after: newline)
            if let fence = fenceStart(line) {
                if let opened = open {
                    if fence.character == opened.character, fence.length >= opened.length,
                       fence.indent <= opened.indent + 3, line.count == fence.indent + fence.length {
                        open = nil
                        boundary = next
                    }
                } else {
                    open = fence
                }
            } else if open == nil, lineStart > text.startIndex, line.allSatisfy({ $0 == " " || $0 == "\t" }) {
                boundary = next
            }
            lineStart = next
        }
        return boundary
    }

    /// A code fence at the start of a line: its indent, and its run of three or more
    /// backticks or tildes. Scanned by hand, as is `startsListItem`: both run for
    /// every line of the answer each time more of it shows.
    private static func fenceStart(_ line: Substring) -> (indent: Int, character: Character, length: Int)? {
        var index = line.startIndex
        var indent = 0
        while index < line.endIndex, line[index] == " " {
            indent += 1
            index = line.index(after: index)
        }
        guard index < line.endIndex, line[index] == "`" || line[index] == "~" else { return nil }
        let character = line[index]
        var length = 0
        while index < line.endIndex, line[index] == character {
            length += 1
            index = line.index(after: index)
        }
        return length >= 3 ? (indent, character, length) : nil
    }

    /// `- `, `* `, `+ `, `1. ` or `1) ` after any indent.
    private static func startsListItem(_ line: Substring) -> Bool {
        var index = line.startIndex
        while index < line.endIndex, line[index] == " " || line[index] == "\t" { index = line.index(after: index) }
        guard index < line.endIndex else { return false }
        if line[index] == "-" || line[index] == "*" || line[index] == "+" {
            index = line.index(after: index)
        } else {
            var digits = 0
            while index < line.endIndex, ("0"..."9").contains(line[index]) {
                digits += 1
                index = line.index(after: index)
            }
            guard (1...9).contains(digits), index < line.endIndex, line[index] == "." || line[index] == ")" else { return false }
            index = line.index(after: index)
        }
        return index < line.endIndex && (line[index] == " " || line[index] == "\t")
    }

    private static func trimmingTrailingSpaces(_ text: Substring) -> Substring {
        var end = text.endIndex
        while end > text.startIndex {
            let before = text.index(before: end)
            guard text[before] == " " || text[before] == "\t" || text[before] == "\r" else { break }
            end = before
        }
        return text[..<end]
    }

    private static func wholeWords(_ tail: Substring) -> Substring {
        guard let cut = tail.lastIndex(where: { $0 == " " || $0 == "\n" || $0 == "\t" }) else { return "" }
        var end = cut
        while end > tail.startIndex {
            let before = tail.index(before: end)
            guard tail[before] == " " || tail[before] == "\t" else { break }
            end = before
        }
        return tail[..<end]
    }

    /// Where a link or image that has not finished arriving starts: `[label`,
    /// `[label]` or `[label](half a target`, at the very end of the text.
    private static func unfinishedLinkStart(_ text: Substring) -> Substring.Index? {
        var search = text.startIndex
        while let bracket = text[search...].firstIndex(of: "[") {
            search = text.index(after: bracket)
            var index = search
            while index < text.endIndex, text[index] != "]", text[index] != "\n" { index = text.index(after: index) }
            var unfinished = index == text.endIndex
            if !unfinished, text[index] == "]" {
                let afterLabel = text.index(after: index)
                if afterLabel == text.endIndex {
                    unfinished = true
                } else if text[afterLabel] == "(" {
                    var target = text.index(after: afterLabel)
                    while target < text.endIndex, text[target] != ")", text[target] != "\n" { target = text.index(after: target) }
                    unfinished = target == text.endIndex
                }
            }
            guard unfinished else { continue }
            if bracket > text.startIndex {
                let before = text.index(before: bracket)
                if text[before] == "!" { return before }
            }
            return bracket
        }
        return nil
    }

    /// Closes inline code, bold, italic and strikethrough left open at the end of a
    /// partial block, and holds back a link whose target has not arrived.
    private static func closeInlineMarkdown(_ block: Substring) -> String {
        var text = block
        if let link = unfinishedLinkStart(text) {
            text = trimmingTrailingSpaces(text[..<link])
        }

        var inCode = false
        var open: [String: Bool] = [:]
        let characters = Array(text)
        var index = 0
        while index < characters.count {
            let character = characters[index]
            if character == "\\" {
                index += 2
                continue
            }
            if character == "`" {
                inCode.toggle()
                index += 1
                continue
            }
            if inCode {
                index += 1
                continue
            }
            if character == "~", index + 1 < characters.count, characters[index + 1] == "~" {
                open["~~"] = !(open["~~"] ?? false)
                index += 2
                continue
            }
            guard character == "*" else {
                index += 1
                continue
            }
            var run = 1
            while index + run < characters.count, characters[index + run] == "*" { run += 1 }
            let lineStart = index == 0 || characters[index - 1] == "\n"
            // "* item" is a list marker, not emphasis.
            let listMarker = run == 1 && lineStart && index + 1 < characters.count && characters[index + 1] == " "
            if !listMarker {
                if run >= 2 { open["**"] = !(open["**"] ?? false) }
                if run % 2 == 1 { open["*"] = !(open["*"] ?? false) }
            }
            index += run
        }

        var shown = String(text)
        var closers = ""
        if inCode {
            if shown.hasSuffix("`") { shown.removeLast() } else { closers += "`" }
        }
        for marker in ["*", "**", "~~"] where open[marker] == true {
            // A marker with nothing after it yet would print literally once closed.
            if shown.hasSuffix(marker) {
                shown.removeLast(marker.count)
                while shown.hasSuffix(" ") || shown.hasSuffix("\t") { shown.removeLast() }
            } else {
                closers += marker
            }
        }
        return shown + closers
    }
}

/// Paces a streaming answer. Text arrives unevenly, a word at a time or several
/// lines in one chunk; this reveals it at a speed that follows how much is
/// waiting, so the reader sees a steady flow that is never far behind. The same
/// arithmetic as the web's `smoothText.ts`.
enum RevealPace {
    /// How long the reveal takes to close most of the gap to what has arrived.
    static let catchUpMs = 180.0
    /// The slowest the reveal moves while text is waiting, in characters a second.
    static let minCharactersPerSecond = 60.0
    /// The time between two steps: 25 a second, each one a redraw.
    static let stepMs = 40.0

    /// How many characters to show after `elapsedMs`, given `shown` of `total` so far.
    static func step(shown: Int, total: Int, elapsedMs: Double) -> Int {
        guard shown < total else { return total }
        let charactersPerMs = max(minCharactersPerSecond / 1000, Double(total - shown) / catchUpMs)
        return min(total, shown + max(1, Int((charactersPerMs * elapsedMs).rounded())))
    }
}
