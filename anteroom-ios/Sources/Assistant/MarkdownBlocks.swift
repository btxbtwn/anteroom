import Foundation

/// An answer's Markdown split into blocks: SwiftUI's Text draws inline Markdown
/// (bold, links) but runs paragraphs, lists, tables and code together into one.
enum MarkdownBlock: Equatable {
    case paragraph(String)
    case heading(level: Int, text: String)
    case bullet(String)
    case numbered(number: String, text: String)
    case code(String)
    case quote(String)
    /// A table: its header, how each column lines up, and its rows padded to the header's width.
    case table(header: [String], alignments: [ColumnAlignment], rows: [[String]])

    enum ColumnAlignment: Equatable {
        case leading, center, trailing
    }

    static func parse(_ markdown: String) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        var paragraph: [String] = []
        var code: [String]?

        func flush() {
            let text = paragraph.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty { blocks.append(.paragraph(text)) }
            paragraph = []
        }

        let lines = markdown.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        var index = 0
        while index < lines.count {
            let raw = lines[index]
            index += 1
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
            } else if line.contains("|"), index < lines.count, let alignments = tableDivider(lines[index]) {
                // A header row is only a table once the divider row is under it.
                flush()
                let header = tableCells(line)
                index += 1
                var rows: [[String]] = []
                while index < lines.count {
                    let row = lines[index].trimmingCharacters(in: .whitespaces)
                    guard !row.isEmpty, row.contains("|") else { break }
                    let cells = tableCells(row)
                    rows.append((0..<header.count).map { $0 < cells.count ? cells[$0] : "" })
                    index += 1
                }
                blocks.append(.table(header: header,
                                     alignments: (0..<header.count).map { $0 < alignments.count ? alignments[$0] : .leading },
                                     rows: rows))
            } else if let match = line.firstMatch(of: #/^(#{1,6})\s+(.+)$/#) {
                flush()
                blocks.append(.heading(level: match.1.count, text: String(match.2)))
            } else if let match = line.firstMatch(of: #/^[-*+]\s+(.+)$/#) {
                flush()
                blocks.append(.bullet(String(match.1)))
            } else if let match = line.firstMatch(of: #/^(\d{1,3})[.)]\s+(.+)$/#) {
                flush()
                blocks.append(.numbered(number: String(match.1), text: String(match.2)))
            } else if line == "-" || line == "*" || line == "+" {
                // A list item whose first word has not arrived yet.
                flush()
                blocks.append(.bullet(""))
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

    /// The cells of one table row: outer pipes dropped, `\|` kept as a pipe inside a cell.
    private static func tableCells(_ row: String) -> [String] {
        var line = Substring(row.trimmingCharacters(in: .whitespaces))
        if line.hasPrefix("|") { line = line.dropFirst() }
        if line.hasSuffix("|"), !line.hasSuffix("\\|") { line = line.dropLast() }
        var cells: [String] = []
        var cell = ""
        var escaped = false
        for character in line {
            if escaped {
                cell.append(character == "|" ? "|" : "\\\(character)")
                escaped = false
            } else if character == "\\" {
                escaped = true
            } else if character == "|" {
                cells.append(cell.trimmingCharacters(in: .whitespaces))
                cell = ""
            } else {
                cell.append(character)
            }
        }
        if escaped { cell.append("\\") }
        cells.append(cell.trimmingCharacters(in: .whitespaces))
        return cells
    }

    /// The column alignments of a divider row such as `|:--|:-:|--:|`, or nil for any other line.
    private static func tableDivider(_ row: String) -> [ColumnAlignment]? {
        let line = row.trimmingCharacters(in: .whitespaces)
        guard line.contains("|") else { return nil }
        var alignments: [ColumnAlignment] = []
        for cell in tableCells(line) {
            guard cell.firstMatch(of: #/^:?-+:?$/#) != nil else { return nil }
            alignments.append(cell.hasPrefix(":") && cell.hasSuffix(":") ? .center : cell.hasSuffix(":") ? .trailing : .leading)
        }
        return alignments
    }
}
