import Foundation

struct MarketplaceSettings: Decodable, Sendable {
    struct Item: Decodable, Identifiable, Sendable {
        let id: String
        let label: String
        let fillable: Bool
    }
    let available: [Item]
    let selected: [String]
}

struct PackageDimensions: Codable, Equatable, Sendable {
    var length: Int
    var width: Int
    var height: Int
}

struct HiddenField: Decodable, Identifiable, Sendable {
    let marketplace: String
    let field: String
    let label: String?
    var id: String { marketplace + "|" + field }
}

struct HiddenFields: Decodable, Sendable {
    let always: [HiddenField]
}

struct ListingFormulas: Decodable, Sendable {
    let title: String
    let description: String
    let defaultTitle: String
    let defaultDescription: String

    enum CodingKeys: String, CodingKey {
        case title, description
        case defaultTitle = "default_title"
        case defaultDescription = "default_description"
    }
}
