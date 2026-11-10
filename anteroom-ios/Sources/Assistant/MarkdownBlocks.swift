import Foundation

/// An answer's Markdown split into blocks: SwiftUI's Text draws inline Markdown
/// (bold, links) but runs paragraphs, lists and code together into one.
enum MarkdownBlock: Equatable {
    case paragraph(String)
    case heading(level: Int, text: String)
    case bullet(String)
    case numbered(number: String, text: String)
    case code(String)
    case quote(String)

    static func parse(_ markdown: String) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        var paragraph: [String] = []
        var code: [String]?

        func flush() {
            let text = paragraph.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty { blocks.append(.paragraph(text)) }
            paragraph = []
        }

        for raw in markdown.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("```") {
                if let open = code {
                    blocks.append(.code(open.joined(separator: "\n")))
                    code = nil
                } else {
                    flush()
                    code = []
                }
                continue
            }
            if code != nil {
                code?.append(raw)
                continue
            }
            if line.isEmpty {
                flush()
            } else if let match = line.firstMatch(of: #/^(#{1,6})\s+(.+)$/#) {
                flush()
                blocks.append(.heading(level: match.1.count, text: String(match.2)))
            } else if let match = line.firstMatch(of: #/^[-*+]\s+(.+)$/#) {
                flush()
                blocks.append(.bullet(String(match.1)))
            } else if let match = line.firstMatch(of: #/^(\d{1,3})[.)]\s+(.+)$/#) {
                flush()
                blocks.append(.numbered(number: String(match.1), text: String(match.2)))
            } else if let match = line.firstMatch(of: #/^>\s?(.*)$/#) {
                flush()
                blocks.append(.quote(String(match.1)))
            } else if case .bullet(let text)? = blocks.last, paragraph.isEmpty, raw.hasPrefix("  ") {
                // An indented line continues the list item above it.
                blocks[blocks.count - 1] = .bullet(text + " " + line)
            } else {
                paragraph.append(line)
            }
        }
        // An unclosed fence still shows as code.
        if let open = code { blocks.append(.code(open.joined(separator: "\n"))) }
        flush()
        return blocks
    }
}
