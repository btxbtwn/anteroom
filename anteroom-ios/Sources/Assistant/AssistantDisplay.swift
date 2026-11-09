import Foundation

/// How the listing assistant chat shows a saved assistant turn. Matches
/// `vendoo-studio/src/components/chat/assistantDisplay.ts`: listing JSON,
/// patches and `missing_fields` payloads stay in storage, but the bubble
/// shows a short seller-facing summary instead of the raw JSON.
enum AssistantDisplay {
    static func text(_ text: String) -> String {
        let prose = stripJsonPayloads(text)
        if !prose.isEmpty { return prose }

        guard let json = extractJson(text) else {
            return looksLikeJsonStream(text) ? "" : text.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        guard let data = json.data(using: .utf8),
              let parsed = try? JSONSerialization.jsonObject(with: data) else {
            return looksLikeJsonStream(text) ? "" : text.trimmingCharacters(in: .whitespacesAndNewlines)
        }

        if let ops = parsed as? [[String: Any]], ops.first?["op"] != nil {
            return summarizeJsonPatch(ops)
        }
        if let object = parsed as? [String: Any] {
            if let missing = object["missing_fields"] as? [[String: Any]] {
                return summarizeMissingFields(missing)
            }
            if let message = object["message"] as? String {
                let trimmed = message.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty { return trimmed }
            }
        }
        if isListingJson(json) || isListingJson(text) {
            return "Listing generated."
        }
        return looksLikeJsonStream(text) ? "" : text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func isListingJson(_ text: String) -> Bool {
        text.contains("\"title\"") && (text.contains("\"description\"") || text.contains("\"price\""))
    }

    static func extractJson(_ text: String) -> String? {
        if let fenced = firstMatch(in: text, pattern: #"```(?:json)?\s*([\s\S]*?)```"#, options: [.caseInsensitive]),
           let innerRange = Range(fenced.range(at: 1), in: text) {
            let inner = String(text[innerRange]).trimmingCharacters(in: .whitespacesAndNewlines)
            if parsesAsJson(inner) { return inner }
        }

        let stripped = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if parsesAsJson(stripped) { return stripped }

        if let arrayMatch = firstMatch(in: stripped, pattern: #"\[[\s\S]*\]"#),
           let range = Range(arrayMatch.range, in: stripped) {
            let candidate = String(stripped[range])
            if let data = candidate.data(using: .utf8),
               let parsed = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]],
               parsed.first?["op"] != nil {
                return candidate
            }
        }

        guard let objectMatch = firstMatch(in: stripped, pattern: #"\{[\s\S]*\}"#),
              let range = Range(objectMatch.range, in: stripped) else {
            return nil
        }
        let candidate = String(stripped[range])
        return parsesAsJson(candidate) ? candidate : nil
    }
}

private extension AssistantDisplay {
    static let patchLabels: [String: String] = [
        "category_path": "Category",
        "department": "Department",
        "styleTags": "Style tags",
        "primaryStoreCategory": "Store category",
        "secondaryStoreCategory": "Secondary store category",
        "primaryColor": "Primary color",
        "secondaryColor": "Secondary color",
    ]

    static let marketplaceDisplay: [String: String] = [
        "general": "General",
        "ebay": "eBay",
        "etsy": "Etsy",
        "poshmark": "Poshmark",
        "mercari": "Mercari",
        "depop": "Depop",
        "facebook": "Facebook",
        "shopify": "Shopify",
    ]

    static func stripJsonPayloads(_ text: String) -> String {
        var visible = replacing(in: text, pattern: #"```(?:json)?\s*[\s\S]*?(```|$)"#, options: [.caseInsensitive], with: "\n")
        visible = replacing(in: visible, pattern: #"\n\s*\[[\s\S]*$"#, with: "\n")
        visible = replacing(in: visible, pattern: #"\n\s*\{[\s\S]*$"#, with: "\n")
        visible = visible.trimmingCharacters(in: .whitespacesAndNewlines)
        if visible.hasPrefix("[") || visible.hasPrefix("{") { return "" }
        return visible
    }

    static func looksLikeJsonStream(_ text: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.hasPrefix("[") || trimmed.hasPrefix("{") { return true }
        return firstMatch(in: text, pattern: #"```json"#, options: [.caseInsensitive]) != nil
    }

    static func summarizeJsonPatch(_ ops: [[String: Any]]) -> String {
        let lines = ops.flatMap { op -> [String] in
            let label = pathLabel(op["path"] as? String ?? "")
            let kind = op["op"] as? String ?? ""
            if kind == "remove" { return ["Removed \(label)"] }
            if kind == "replace" || kind == "add" {
                return ["\(label): \(displayValue(op["value"]))"]
            }
            return []
        }
        if lines.isEmpty { return "Saved those changes to the listing." }
        if lines.count == 1 { return "Updated the listing — \(lines[0])." }
        return "Updated the listing:\n" + lines.map { "- \($0)" }.joined(separator: "\n")
    }

    static func summarizeMissingFields(_ rows: [[String: Any]]) -> String {
        let lines = rows.flatMap { row -> [String] in
            let field = stringValue(row["field"]).trimmingCharacters(in: .whitespacesAndNewlines)
            let value = displayValue(row["value"]).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !field.isEmpty, !value.isEmpty else { return [] }
            var market = stringValue(row["marketplace"]).trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            if market.isEmpty { market = "general" }
            let label = marketplaceDisplay[market] ?? humanKey(market)
            return ["\(label) / \(field): \(value)"]
        }
        if lines.isEmpty { return "Saved field values to the listing JSON." }
        if lines.count == 1 { return "Saved to the listing JSON — \(lines[0])." }
        return "Saved to the listing JSON:\n" + lines.map { "- \($0)" }.joined(separator: "\n")
    }

    static func pathLabel(_ path: String) -> String {
        var parts = path
            .replacingOccurrences(of: #"^/"#, with: "", options: .regularExpression)
            .split(separator: "/", omittingEmptySubsequences: false)
            .map(String.init)
            .filter { part in
                !part.isEmpty && part != "-" && firstMatch(in: part, pattern: #"^\d+$"#) == nil
            }
        guard !parts.isEmpty else { return "Field" }

        var prefix = ""
        if parts[0].hasSuffix("_specifics") {
            let market = String(parts[0].dropLast("_specifics".count))
            if market == "ebay" {
                prefix = "eBay"
            } else if let first = market.first {
                prefix = String(first).uppercased() + market.dropFirst()
            }
            parts.removeFirst()
        }
        let key = parts.last ?? ""
        let label = patchLabels[key] ?? humanKey(key)
        return prefix.isEmpty ? label : "\(prefix) \(label)"
    }

    static func humanKey(_ token: String) -> String {
        var value = token.replacingOccurrences(of: "_", with: " ")
        value = replacing(in: value, pattern: #"([a-z])([A-Z])"#, with: "$1 $2")
        value = value.replacingOccurrences(of: "-", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return replacing(in: value, pattern: #"\b\w"#) { String($0).uppercased() }
    }

    static func displayValue(_ value: Any?) -> String {
        switch value {
        case nil, is NSNull: return ""
        case let array as [Any]: return array.map { displayValue($0) }.joined(separator: ", ")
        case let number as NSNumber:
            if CFGetTypeID(number) == CFBooleanGetTypeID() {
                return number.boolValue ? "true" : "false"
            }
            return number.stringValue
        case let text as String: return text
        default: return String(describing: value!)
        }
    }

    static func stringValue(_ value: Any?) -> String {
        switch value {
        case nil, is NSNull: return ""
        case let text as String: return text
        default: return String(describing: value!)
        }
    }

    static func parsesAsJson(_ text: String) -> Bool {
        guard let data = text.data(using: .utf8) else { return false }
        return (try? JSONSerialization.jsonObject(with: data)) != nil
    }

    static func firstMatch(in text: String, pattern: String,
                           options: NSRegularExpression.Options = []) -> NSTextCheckingResult? {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: options) else { return nil }
        let range = NSRange(text.startIndex..., in: text)
        return regex.firstMatch(in: text, options: [], range: range)
    }

    static func replacing(in text: String, pattern: String,
                          options: NSRegularExpression.Options = [], with template: String) -> String {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: options) else { return text }
        let range = NSRange(text.startIndex..., in: text)
        return regex.stringByReplacingMatches(in: text, options: [], range: range, withTemplate: template)
    }

    static func replacing(in text: String, pattern: String,
                          options: NSRegularExpression.Options = [],
                          with transform: (Substring) -> String) -> String {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: options) else { return text }
        let nsRange = NSRange(text.startIndex..., in: text)
        var result = ""
        var cursor = text.startIndex
        regex.enumerateMatches(in: text, options: [], range: nsRange) { match, _, _ in
            guard let match, let range = Range(match.range, in: text) else { return }
            result += text[cursor..<range.lowerBound]
            result += transform(text[range])
            cursor = range.upperBound
        }
        result += text[cursor...]
        return result
    }
}
