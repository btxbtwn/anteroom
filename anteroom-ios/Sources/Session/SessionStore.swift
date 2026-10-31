import Foundation
import Observation
import Supabase

/// Owns the Supabase session. Supabase stores the session in the Keychain and
/// refreshes it; the API client asks for a current access token on every call.
@MainActor
@Observable
final class SessionStore {
    enum State: Equatable {
        case loading
        case missingConfiguration
        case signedOut
        case signedIn(email: String?)
    }

    private(set) var state: State = .loading
    private(set) var signInError: String?
    let api: AnteroomAPI?
    private let client: SupabaseClient?

    init() {
        guard let key = AppConfig.supabasePublishableKey else {
            client = nil
            api = nil
            state = .missingConfiguration
            return
        }
        let client = SupabaseClient(supabaseURL: AppConfig.supabaseURL, supabaseKey: key)
        self.client = client
        api = AnteroomAPI(baseURL: AppConfig.apiBaseURL) {
            try await client.auth.session.accessToken
        }
    }

    var email: String? {
        if case .signedIn(let email) = state { return email }
        return nil
    }

    /// Follows sign-in, refresh and sign-out events for the life of the app.
    func start() async {
        guard let client else { return }
#if DEBUG
        if AppConfig.sandboxURL != nil {
            do {
                let local = try await client.auth.signIn(email: "alice@sandbox.test", password: "sandbox")
                state = .signedIn(email: local.user.email)
            } catch {
                signInError = "The local sandbox is unavailable. Start ./sandbox.sh --hosted and relaunch."
                state = .signedOut
            }
            return
        }
#endif
        for await (event, session) in client.auth.authStateChanges {
            switch event {
            case .initialSession:
                // The stored session may have expired; reading it refreshes it.
                if session != nil, let current = try? await client.auth.session {
                    state = .signedIn(email: current.user.email)
                } else {
                    state = .signedOut
                }
            case .signedIn, .tokenRefreshed, .userUpdated:
                if let session { state = .signedIn(email: session.user.email) }
            case .signedOut:
                state = .signedOut
            default:
                break
            }
        }
    }

    func sendSignInLink(to email: String) async throws {
        guard let client else { return }
        try await client.auth.signInWithOTP(email: email, redirectTo: AppConfig.signInRedirectURL)
    }

    /// Finishes sign-in when the emailed link opens the app.
    func completeSignIn(from url: URL) async {
        guard let client, url.scheme == AppConfig.signInRedirectURL.scheme else { return }
        do {
            let session = try await client.auth.session(from: url)
            state = .signedIn(email: session.user.email)
        } catch {
            signInError = error.localizedDescription
        }
    }

    func signOut() async {
        try? await client?.auth.signOut()
        state = .signedOut
    }
}
