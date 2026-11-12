import Foundation

/// The sign-in server refused the saved sign-in: its refresh token is gone,
/// used, or belongs to an ended session. Only this signs the seller out; any
/// other failure to renew (offline, a server error, rate limiting) is shown
/// as one to try again, and the saved sign-in is kept.
struct SignInRejected: Error {}

/// Tells a refresh the server refused apart from one that merely failed.
/// Kept free of the Supabase types so it can be tested on its own.
enum SignInRenewal {
    /// Codes Supabase Auth answers a refresh with when the refresh token can
    /// never work again. supabase-swift turns the first four into
    /// `AuthError.sessionMissing` and clears the saved session itself.
    static let rejectionCodes: Set<String> = [
        "refresh_token_not_found", "refresh_token_already_used",
        "session_not_found", "session_expired",
        "user_not_found", "user_banned",
    ]

    /// Whether a refresh answered with `status` and error `code` rejected the
    /// sign-in. Older servers send no `code`, only OAuth's `invalid_grant` in
    /// the body's `error` field.
    static func rejects(status: Int, code: String?, body: Data) -> Bool {
        guard [400, 401, 403].contains(status) else { return false }
        if let code, rejectionCodes.contains(code) { return true }
        struct OAuthError: Decodable { let error: String? }
        return (try? JSONDecoder().decode(OAuthError.self, from: body))?.error == "invalid_grant"
    }
}
