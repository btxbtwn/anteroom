import Foundation

/// The same theme values accepted by desktop and the hosted settings API.
enum AppearanceTheme: String, Codable, CaseIterable, Identifiable, Sendable {
    case dark, light, system
    var id: String { rawValue }
    var label: String { rawValue.capitalized }
}

struct AppearancePreferences: Decodable, Sendable {
    let theme: AppearanceTheme
}

/// Send only the changed setting; never replace unrelated account preferences.
struct AppearanceUpdate: Encodable, Sendable {
    let theme: AppearanceTheme
}
