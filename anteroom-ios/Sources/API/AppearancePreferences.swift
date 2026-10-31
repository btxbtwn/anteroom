import Foundation

/// The same theme values accepted by desktop and the hosted settings API.
enum AppearanceTheme: String, Codable, CaseIterable, Identifiable, Sendable {
    case dark, light, system
    var id: String { rawValue }
    var label: String { rawValue.capitalized }
}

/// What the inventory calls each listing. By SKU, a listing without one keeps its title.
enum ListingNames: String, Codable, CaseIterable, Identifiable, Sendable {
    case title, sku
    var id: String { rawValue }
    var label: String { self == .sku ? "SKU" : "Title" }
}

struct AppearancePreferences: Decodable, Sendable {
    let theme: AppearanceTheme
    let listingNames: ListingNames

    enum CodingKeys: String, CodingKey {
        case theme
        case listingNames = "listing_names"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        theme = try values.decode(AppearanceTheme.self, forKey: .theme)
        listingNames = (try? values.decodeIfPresent(ListingNames.self, forKey: .listingNames)) ?? .title
    }
}

/// Send only the changed setting; never replace unrelated account preferences.
struct AppearanceUpdate: Encodable, Sendable {
    var theme: AppearanceTheme?
    var listingNames: ListingNames?

    enum CodingKeys: String, CodingKey {
        case theme
        case listingNames = "listing_names"
    }
}
