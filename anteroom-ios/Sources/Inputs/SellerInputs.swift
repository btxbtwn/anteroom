import Foundation

/// The desktop ItemDetails notes contract. Keep the original document so
/// editing a known field never drops agent-managed or future fields.
struct SellerInputs: Equatable, Sendable, Codable {
    enum Garment: String, CaseIterable, Identifiable, Sendable, Codable {
        case top, pants, skirt
        var id: String { rawValue }
        var label: String { rawValue.capitalized }

        /// Keys and labels mirror garmentMeasurements.ts and the backend
        /// GARMENT_MEASUREMENTS definition.
        var fields: [(key: String, label: String)] {
            switch self {
            case .top: return [("pitToPit", "Pit to Pit"), ("length", "Length"), ("sleeve", "Sleeve")]
            case .pants: return [("waist", "Waist"), ("rise", "Rise"), ("inseam", "Inseam"), ("legOpening", "Leg Opening")]
            case .skirt: return [("waist", "Waist"), ("hip", "Hip"), ("length", "Length")]
            }
        }
    }

    private let originalNotes: String
    var sku: String
    var size: String
    var sellerNotes: String
    var knownFlaws: String
    var descriptionMeasurements: String
    var cog: String
    var packageDimensions: String
    var vendooLabels: String
    var garment: Garment
    private var measurements: [String: [String: String]]

    init(notes: String? = nil) {
        let raw = notes ?? ""
        originalNotes = raw
        let object = Self.object(raw)
        sku = object?["sku"] as? String ?? ""
        size = object?["size"] as? String ?? ""
        sellerNotes = object?["sellerNotes"] as? String ?? (object == nil ? raw : "")
        knownFlaws = object?["knownFlaws"] as? String ?? ""
        descriptionMeasurements = object?["descriptionMeasurements"] as? String ?? ""
        cog = object?["cog"] as? String ?? ""
        packageDimensions = object?["packageDimensions"] as? String ?? ""
        vendooLabels = object?["vendooLabels"] as? String ?? ""
        garment = Garment(rawValue: object?["garment"] as? String ?? "") ?? .top
        measurements = ((object?["measurements"] as? [String: Any]) ?? [:]).compactMapValues { group in
            (group as? [String: Any])?.compactMapValues { $0 as? String }
        }
    }

    var labels: [String] {
        vendooLabels.split(separator: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
    }

    mutating func addLabel(_ value: String) {
        for candidate in value.split(separator: ",") {
            let label = candidate.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !label.isEmpty, !labels.contains(where: { $0.caseInsensitiveCompare(label) == .orderedSame }) else { continue }
            vendooLabels = (labels + [label]).joined(separator: ", ")
        }
    }

    mutating func removeLabel(_ value: String) {
        vendooLabels = labels.filter { $0.caseInsensitiveCompare(value) != .orderedSame }.joined(separator: ", ")
    }

    func measurement(_ key: String) -> String { measurements[garment.rawValue]?[key] ?? "" }

    mutating func setMeasurement(_ key: String, value: String) {
        measurements[garment.rawValue, default: [:]][key] = value
    }

    func encodedNotes() throws -> String {
        let baseline = Self(notes: originalNotes)
        if self == baseline { return originalNotes }
        var object = Self.object(originalNotes) ?? (originalNotes.isEmpty ? [:] : ["sellerNotes": originalNotes])
        let textValues = [
            ("size", size.trimmingCharacters(in: .whitespacesAndNewlines), baseline.size),
            ("sku", sku, baseline.sku), ("sellerNotes", sellerNotes, baseline.sellerNotes),
            ("knownFlaws", knownFlaws, baseline.knownFlaws),
            ("descriptionMeasurements", descriptionMeasurements, baseline.descriptionMeasurements),
            ("cog", cog, baseline.cog), ("packageDimensions", packageDimensions, baseline.packageDimensions),
            ("vendooLabels", vendooLabels, baseline.vendooLabels),
        ]
        for (key, value, before) in textValues where value != before { object[key] = value }
        if garment != baseline.garment || measurements != baseline.measurements { object["garment"] = garment.rawValue }
        var all = object["measurements"] as? [String: Any] ?? [:]
        for (group, fields) in measurements {
            var values = all[group] as? [String: Any] ?? [:]
            for (key, value) in fields where value != baseline.measurements[group]?[key] { values[key] = value }
            all[group] = values
        }
        if measurements != baseline.measurements { object["measurements"] = all }
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        return String(decoding: data, as: UTF8.self)
    }

    /// Send only edited keys so a stale form cannot restore a server-cleared tag size.
    func noteChanges(since baseline: SellerInputs? = nil) throws -> [String: JSONValue] {
        let encoded = try encodedNotes()
        guard !encoded.isEmpty else { return [:] }
        let after = (try? JSONDecoder().decode([String: JSONValue].self, from: Data(encoded.utf8)))
            ?? ["sellerNotes": .string(encoded)]
        let previousNotes = try baseline?.encodedNotes() ?? originalNotes
        let before = (try? JSONDecoder().decode([String: JSONValue].self, from: Data(previousNotes.utf8)))
            ?? (previousNotes.isEmpty ? [:] : ["sellerNotes": .string(previousNotes)])
        var changes = after.filter { key, value in value != before[key] }
        if case .object(let groups) = after["measurements"] {
            let previous: [String: JSONValue]
            if case .object(let value) = before["measurements"] { previous = value } else { previous = [:] }
            var changedGroups: [String: JSONValue] = [:]
            for (group, value) in groups {
                guard case .object(let fields) = value else { continue }
                let old: [String: JSONValue]
                if case .object(let value) = previous[group] { old = value } else { old = [:] }
                let edited = fields.filter { key, value in value != old[key] }
                if !edited.isEmpty { changedGroups[group] = .object(edited) }
            }
            changes["measurements"] = changedGroups.isEmpty ? nil : .object(changedGroups)
        }
        return changes
    }

    private static func object(_ raw: String) -> [String: Any]? {
        (try? JSONSerialization.jsonObject(with: Data(raw.utf8))) as? [String: Any]
    }
}
