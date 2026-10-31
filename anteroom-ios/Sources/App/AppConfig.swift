import Foundation

/// Public configuration. The hosted API and Supabase project URL are public;
/// the publishable key comes from Config/Local.xcconfig through Info.plist.
enum AppConfig {
    static let apiBaseURL = URL(string: "https://peaceful-meadowstone-6944.fly.dev")!
    static let supabaseURL = URL(string: "https://ytoqdgtzvutdltfkpxbu.supabase.co")!
    static let signInRedirectURL = URL(string: "anteroom://login-callback")!

    static var supabasePublishableKey: String? {
        guard let key = Bundle.main.object(forInfoDictionaryKey: "SupabasePublishableKey") as? String else {
            return nil
        }
        let trimmed = key.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty ? nil : trimmed
    }
}
