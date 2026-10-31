import Foundation

@main
struct AppearancePreferencesTests {
    static func main() throws {
        let decoder = JSONDecoder()
        let response = Data(#"{"ok":true,"theme":"light","listing_names":"sku","recent_vendoo_labels":["Keep me"]}"#.utf8)
        let preferences = try decoder.decode(AppearancePreferences.self, from: response)
        precondition(preferences.theme == .light)
        for theme in AppearanceTheme.allCases {
            let body = try JSONEncoder().encode(AppearanceUpdate(theme: theme))
            let object = try JSONSerialization.jsonObject(with: body) as! [String: String]
            precondition(object == ["theme": theme.rawValue], "A theme update must not replace other seller preferences")
            let reopened = try decoder.decode(AppearancePreferences.self, from: body)
            precondition(reopened.theme == theme)
        }
        do {
            _ = try decoder.decode(AppearancePreferences.self, from: Data(#"{"theme":"invalid"}"#.utf8))
            preconditionFailure("An invalid server preference must produce a visible loading error")
        } catch is DecodingError {}
        print("Appearance preference contract tests passed")
    }
}
