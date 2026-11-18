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

    private enum CodingKeys: String, CodingKey {
        case originalNotes, sku, size, sellerNotes, knownFlaws, descriptionMeasurements
        case cog, packageDimensions, vendooLabels, garment, measurements
    }

    /// Drafts saved before tag-size entry have every original field except size.
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        originalNotes = try values.decode(String.self, forKey: .originalNotes)
        sku = try values.decode(String.self, forKey: .sku)
        size = try values.decodeIfPresent(String.self, forKey: .size)
            ?? (Self.object(originalNotes)?["size"] as? String ?? "")
        sellerNotes = try values.decode(String.self, forKey: .sellerNotes)
        knownFlaws = try values.decode(String.self, forKey: .knownFlaws)
        descriptionMeasurements = try values.decode(String.self, forKey: .descriptionMeasurements)
        cog = try values.decode(String.self, forKey: .cog)
        packageDimensions = try values.decode(String.self, forKey: .packageDimensions)
        vendooLabels = try values.decode(String.self, forKey: .vendooLabels)
        garment = try values.decode(Garment.self, forKey: .garment)
        measurements = try values.decode([String: [String: String]].self, forKey: .measurements)
    }

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
        for (key, value) in textChanges(from: baseline) { object[key] = value }
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

    /// Compare raw input before normalizing an edited size. Untouched tag text stays untouched.
    private func textChanges(from baseline: Self) -> [(String, String)] {
        let values = [
            ("size", size, baseline.size),
            ("sku", sku, baseline.sku), ("sellerNotes", sellerNotes, baseline.sellerNotes),
            ("knownFlaws", knownFlaws, baseline.knownFlaws),
            ("descriptionMeasurements", descriptionMeasurements, baseline.descriptionMeasurements),
            ("cog", cog, baseline.cog), ("packageDimensions", packageDimensions, baseline.packageDimensions),
            ("vendooLabels", vendooLabels, baseline.vendooLabels),
        ]
        return values.compactMap { key, value, before in
            guard value != before else { return nil }
            if key == "size" {
                let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
                guard normalized != before.trimmingCharacters(in: .whitespacesAndNewlines) else { return nil }
                return (key, normalized)
            }
            return (key, value)
        }
    }

    /// Send only edited keys so a stale form cannot restore a server-cleared tag size.
    func noteChanges(since baseline: SellerInputs? = nil) throws -> [String: JSONValue] {
        let before = baseline ?? Self(notes: originalNotes)
        var changes = Dictionary(uniqueKeysWithValues: textChanges(from: before).map { ($0.0, JSONValue.string($0.1)) })
        if garment != before.garment || (measurements != before.measurements && !measurements.isEmpty) {
            changes["garment"] = .string(garment.rawValue)
        }
        var groups: [String: JSONValue] = [:]
        for group in Set(measurements.keys).union(before.measurements.keys) {
            let current = measurements[group] ?? [:]
            let previous = before.measurements[group] ?? [:]
            var edited: [String: JSONValue] = [:]
            for key in Set(current.keys).union(previous.keys) where current[key] != previous[key] {
                edited[key] = .string(current[key] ?? "")
            }
            if !edited.isEmpty { groups[group] = .object(edited) }
        }
        if !groups.isEmpty { changes["measurements"] = .object(groups) }
        return changes
    }

    private static func object(_ raw: String) -> [String: Any]? {
        (try? JSONSerialization.jsonObject(with: Data(raw.utf8))) as? [String: Any]
    }
}
