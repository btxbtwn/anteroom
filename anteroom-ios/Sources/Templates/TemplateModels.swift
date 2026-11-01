import Foundation

/// Saved listing templates (`/api/listing-templates`, api/listingTemplates.ts).
struct ListingTemplate: Decodable, Identifiable, Hashable, Sendable {
    let id: String
    let name: String
    let revision: Int
    let fields: [String: JSONValue]

    static func == (lhs: Self, rhs: Self) -> Bool { lhs.id == rhs.id && lhs.revision == rhs.revision }
    func hash(into hasher: inout Hasher) { hasher.combine(id); hasher.combine(revision) }
}

struct TemplateSource: Decodable, Sendable {
    let sourceID: String
    let sourceTitle: String
    let expectedBasis: String
    let fields: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case fields
        case sourceID = "source_id", sourceTitle = "source_title", expectedBasis = "expected_basis"
    }
}

struct TemplateReview: Decodable, Sendable {
    let title: String
    let changes: [BulkChange]
    let expectedRevisionID: String
    let expectedBasis: String
    let expectedResult: String

    enum CodingKeys: String, CodingKey {
        case title, changes
        case expectedRevisionID = "expected_revision_id", expectedBasis = "expected_basis", expectedResult = "expected_result"
    }
}

/// A row in the field picker: category ids travel together as one choice.
struct TemplateFieldGroup: Identifiable, Sendable {
    let key: String
    let name: String
    let fields: [String]
    let value: JSONValue
    var id: String { key }
}

enum TemplateFields {
    private static let categoryFields = ["marketplace_categories", "marketplace_category_ids", "marketplace_category_objects", "direct_category_ids"]
    private static let labels = [
        "ebay_specifics": "eBay form", "etsy_specifics": "Etsy form", "poshmark_specifics": "Poshmark form",
        "mercari_specifics": "Mercari form", "depop_specifics": "Depop form", "vinted_specifics": "Vinted form",
        "sellwild_specifics": "Sellwild form", "title": "Title", "description": "Description", "price": "Asking price",
        "cost": "Cost", "quantity": "Quantity", "brand": "Brand", "condition": "Condition", "primaryColor": "Primary color",
        "secondaryColor": "Secondary color", "category_path": "General category", "size": "Size", "sizeType": "Size type",
        "size_us": "US size", "department": "Department", "tags": "Tags", "labels": "Labels", "weight_lb": "Package pounds",
        "weight_oz": "Package ounces", "package_dimensions_in": "Package dimensions",
        "marketplace_categories": "Marketplace categories",
    ]

    static func name(_ key: String) -> String {
        if let label = labels[key] { return label }
        let spaced = key.replacingOccurrences(of: "_specifics", with: " form").replacingOccurrences(of: "_", with: " ")
        return spaced.prefix(1).uppercased() + spaced.dropFirst()
    }

    static func groups(_ fields: [String: JSONValue]) -> [TemplateFieldGroup] {
        var result = fields.keys.sorted().filter { !categoryFields.contains($0) }.map {
            TemplateFieldGroup(key: $0, name: name($0), fields: [$0], value: fields[$0] ?? .null)
        }
        let categories = categoryFields.filter { fields[$0] != nil }
        if !categories.isEmpty {
            result.append(TemplateFieldGroup(key: "marketplace_categories", name: "Marketplace categories", fields: categories,
                                             value: fields["marketplace_categories"] ?? .string("Saved direct marketplace selections")))
        }
        return result
    }

    static func value(_ value: JSONValue) -> String {
        switch value {
        case .null: return "Not set"
        case .string(let text): return text.isEmpty ? "Empty" : text
        case .object: return "Saved answers"
        default: return value.reviewText
        }
    }
}
