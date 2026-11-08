import Foundation

/// What an assistant reply shows in the transcript, ported from the web's
/// chat/assistantDisplay.ts. A generated listing arrives as JSON; the seller
/// sees a sentence about what was filled, never the JSON itself.
struct AssistantReply: Equatable {
    let text: String
    /// The reply is the generated listing, saved to the draft: offer the editor.
    let isListing: Bool

    init(_ raw: String) {
        let prose = Self.stripJSON(raw)
        if !prose.isEmpty {
            text = prose
            isListing = false
            return
        }
        guard let json = Self.extractJSON(raw), let parsed = try? JSONSerialization.jsonObject(with: Data(json.utf8)) else {
            text = Self.looksLikeJSON(raw) ? "" : raw.trimmingCharacters(in: .whitespacesAndNewlines)
            isListing = false
            return
        }
        if let ops = parsed as? [[String: Any]], ops.first?["op"] != nil {
            text = Self.patchSummary(ops)
            isListing = false
        } else if let object = parsed as? [String: Any], let message = object["message"] as? String,
                  !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            text = message.trimmingCharacters(in: .whitespacesAndNewlines)
            isListing = false
        } else if let object = parsed as? [String: Any], object["title"] != nil,
                  object["description"] != nil || object["price"] != nil {
            text = Self.listingSummary(object)
            isListing = true
        } else {
            text = Self.looksLikeJSON(raw) ? "" : raw.trimmingCharacters(in: .whitespacesAndNewlines)
            isListing = false
        }
    }

    /// "Title, description and 9 item details filled."
    static func listingSummary(_ listing: [String: Any]) -> String {
        func filled(_ value: Any?) -> Bool {
            switch value {
            case nil, is NSNull: false
            case let text as String: !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            case let items as [Any]: !items.isEmpty
            case let object as [String: Any]: !object.isEmpty
            default: true
            }
        }
        var parts: [String] = []
        if filled(listing["title"]) { parts.append("Title") }
        if filled(listing["description"]) { parts.append(parts.isEmpty ? "Description" : "description") }
        let details = listing.filter { $0.key != "title" && $0.key != "description" && filled($0.value) }.count
        if details > 0 {
            let words = "\(details) item \(details == 1 ? "detail" : "details")"
            parts.append(parts.isEmpty ? words.prefix(1).uppercased() + words.dropFirst() : words)
        }
        guard !parts.isEmpty else { return "Listing generated." }
        let joined = parts.count == 1 ? parts[0] : parts.dropLast().joined(separator: ", ") + " and " + parts.last!
        return "\(joined) filled."
    }

    private static func patchSummary(_ ops: [[String: Any]]) -> String {
        let lines: [String] = ops.compactMap { op in
            let label = pathLabel(op["path"] as? String ?? "")
            switch op["op"] as? String {
            case "remove": return "Removed \(label)"
            case "replace", "add": return "\(label): \(display(op["value"]))"
            default: return nil
            }
        }
        if lines.isEmpty { return "Saved those changes to the listing." }
        if lines.count == 1 { return "Updated the listing — \(lines[0])." }
        return "Updated the listing:\n" + lines.map { "• \($0)" }.joined(separator: "\n")
    }

    private static let labels = ["category_path": "Category", "department": "Department", "styleTags": "Style tags",
                                 "primaryStoreCategory": "Store category", "secondaryStoreCategory": "Secondary store category",
                                 "primaryColor": "Primary color", "secondaryColor": "Secondary color"]

    private static func pathLabel(_ path: String) -> String {
        var parts = path.split(separator: "/").map(String.init).filter { $0 != "-" && Int($0) == nil }
        guard !parts.isEmpty else { return "Field" }
        var prefix = ""
        if parts[0].hasSuffix("_specifics") {
            let market = String(parts[0].dropLast("_specifics".count))
            prefix = market == "ebay" ? "eBay" : market.prefix(1).uppercased() + market.dropFirst()
            parts.removeFirst()
        }
        let key = parts.last ?? ""
        let label = labels[key] ?? humanKey(key)
        return prefix.isEmpty ? label : "\(prefix) \(label)"
    }

    private static func humanKey(_ key: String) -> String {
        let spaced = key.replacingOccurrences(of: "_", with: " ").replacingOccurrences(of: "-", with: " ")
            .replacingOccurrences(of: "([a-z])([A-Z])", with: "$1 $2", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
        return spaced.split(separator: " ").map { $0.prefix(1).uppercased() + $0.dropFirst() }.joined(separator: " ")
    }

    private static func display(_ value: Any?) -> String {
        switch value {
        case nil, is NSNull: ""
        case let items as [Any]: items.map { "\($0)" }.joined(separator: ", ")
        default: "\(value!)"
        }
    }

    /// The reply without its JSON: fenced blocks, and a trailing object or array.
    private static func stripJSON(_ text: String) -> String {
        var visible = text.replacingOccurrences(of: "```(?:json)?\\s*[\\s\\S]*?(```|$)", with: "\n",
                                                options: [.regularExpression, .caseInsensitive])
        visible = visible.replacingOccurrences(of: "\\n\\s*\\[[\\s\\S]*$", with: "\n", options: .regularExpression)
        visible = visible.replacingOccurrences(of: "\\n\\s*\\{[\\s\\S]*$", with: "\n", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if visible.hasPrefix("{") || visible.hasPrefix("[") { return "" }
        return visible
    }

    private static func valid(_ text: String) -> Bool {
        (try? JSONSerialization.jsonObject(with: Data(text.utf8))) != nil
    }

    private static func extractJSON(_ text: String) -> String? {
        if let range = text.range(of: "```(?:json)?\\s*[\\s\\S]*?```", options: [.regularExpression, .caseInsensitive]) {
            let inner = text[range].replacingOccurrences(of: "^```(?:json)?", with: "", options: [.regularExpression, .caseInsensitive])
                .replacingOccurrences(of: "```$", with: "", options: .regularExpression)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if valid(inner) { return inner }
        }
        let stripped = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if valid(stripped) { return stripped }
        if let range = stripped.range(of: "\\[[\\s\\S]*\\]", options: .regularExpression), valid(String(stripped[range])),
           let ops = try? JSONSerialization.jsonObject(with: Data(stripped[range].utf8)) as? [[String: Any]],
           ops.first?["op"] != nil {
            return String(stripped[range])
        }
        if let range = stripped.range(of: "\\{[\\s\\S]*\\}", options: .regularExpression), valid(String(stripped[range])) {
            return String(stripped[range])
        }
        return nil
    }

    private static func looksLikeJSON(_ text: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.hasPrefix("[") || trimmed.hasPrefix("{") || text.range(of: "```json", options: .caseInsensitive) != nil
    }
}
