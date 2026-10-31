import Foundation

/// Public configuration. The hosted API and Supabase project URL are public;
/// the publishable key comes from Config/Local.xcconfig through Info.plist.
enum AppConfig {
    static var sandboxURL: URL? {
#if DEBUG
        guard let raw = ProcessInfo.processInfo.environment["ANTEROOM_SANDBOX_URL"],
              let url = URL(string: raw), url.scheme == "http",
              let host = url.host, ["127.0.0.1", "localhost", "::1"].contains(host),
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty || url.path == "/" else { return nil }
        return url
#else
        return nil
#endif
    }

    static var apiBaseURL: URL { sandboxURL ?? URL(string: "https://peaceful-meadowstone-6944.fly.dev")! }
    static var supabaseURL: URL { sandboxURL?.appending(path: "local") ?? URL(string: "https://ytoqdgtzvutdltfkpxbu.supabase.co")! }
    static let signInRedirectURL = URL(string: "anteroom://login-callback")!

    static var supabasePublishableKey: String? {
        if sandboxURL != nil { return "sandbox-public" }
        guard let key = Bundle.main.object(forInfoDictionaryKey: "SupabasePublishableKey") as? String else {
            return nil
        }
        let trimmed = key.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty ? nil : trimmed
    }
}
