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
        /// A password-reset link opened the app; the seller picks a new password first.
        case choosingNewPassword(email: String?)
        case signedIn(email: String?)
    }

    /// Supabase's PKCE links do not say which email sent them, so remember that
    /// this iPhone asked for a reset until a link signs it in.
    private static let pendingResetKey = "anteroom.pendingPasswordReset"

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
        let client: SupabaseClient
#if DEBUG
        if AppConfig.sandboxURL != nil {
            client = SupabaseClient(supabaseURL: AppConfig.supabaseURL, supabaseKey: key,
                                    options: .init(auth: .init(storage: SandboxAuthStorage())))
        } else {
            client = SupabaseClient(supabaseURL: AppConfig.supabaseURL, supabaseKey: key)
        }
#else
        client = SupabaseClient(supabaseURL: AppConfig.supabaseURL, supabaseKey: key)
#endif
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
                // A reset link signs in too; keep asking for the new password.
                if let session, !isChoosingNewPassword { state = .signedIn(email: session.user.email) }
            case .signedOut:
                state = .signedOut
            default:
                break
            }
        }
    }

    /// Reads the saved session again after a slow or failed restore.
    func retryRestore() async {
        guard let client, state == .loading else { return }
#if DEBUG
        if AppConfig.sandboxURL != nil {
            if let local = try? await client.auth.signIn(email: "alice@sandbox.test", password: "sandbox") {
                state = .signedIn(email: local.user.email)
            }
            return
        }
#endif
        if let current = try? await client.auth.session {
            state = .signedIn(email: current.user.email)
        } else {
            state = .signedOut
        }
    }

    private var isChoosingNewPassword: Bool {
        if case .choosingNewPassword = state { return true }
        return false
    }

    func signIn(email: String, password: String) async throws {
        guard let client else { return }
        do {
            let session = try await client.auth.signIn(email: email, password: password)
            UserDefaults.standard.removeObject(forKey: Self.pendingResetKey)
            signInError = nil
            state = .signedIn(email: session.user.email)
        } catch {
            throw Self.readable(error, for: .signIn)
        }
    }

    /// Creates the account. Returns true when Supabase wants the email confirmed
    /// first; the confirmation link opens this app and signs it in.
    func createAccount(email: String, password: String) async throws -> Bool {
        guard let client else { return false }
        do {
            let response = try await client.auth.signUp(email: email, password: password, redirectTo: AppConfig.signInRedirectURL)
            guard let session = response.session else { return true }
            state = .signedIn(email: session.user.email)
            return false
        } catch {
            throw Self.readable(error, for: .createAccount)
        }
    }

    func sendPasswordReset(to email: String) async throws {
        guard let client else { return }
        do {
            try await client.auth.resetPasswordForEmail(email, redirectTo: AppConfig.signInRedirectURL)
            UserDefaults.standard.set(true, forKey: Self.pendingResetKey)
        } catch {
            throw Self.readable(error, for: .reset)
        }
    }

    func setNewPassword(_ password: String) async throws {
        guard let client else { return }
        do {
            let user = try await client.auth.update(user: UserAttributes(password: password))
            state = .signedIn(email: user.email)
        } catch {
            throw Self.readable(error, for: .newPassword)
        }
    }

    /// Finishes an emailed link (account confirmation or password reset) that opened the app.
    func completeSignIn(from url: URL) async {
        guard let client, url.scheme == AppConfig.signInRedirectURL.scheme else { return }
        do {
            let session = try await client.auth.session(from: url)
            signInError = nil
            if UserDefaults.standard.bool(forKey: Self.pendingResetKey) {
                UserDefaults.standard.removeObject(forKey: Self.pendingResetKey)
                state = .choosingNewPassword(email: session.user.email)
            } else {
                state = .signedIn(email: session.user.email)
            }
        } catch {
            signInError = "That link opened somewhere other than where it was asked for, or it expired. If you just confirmed your email, sign in with your password."
        }
    }

    func signOut() async {
        try? await client?.auth.signOut()
        UserDefaults.standard.removeObject(forKey: Self.pendingResetKey)
        state = .signedOut
    }

    private static func readable(_ error: Error, for action: SignInAction) -> SignInFailure {
        if let auth = error as? AuthError {
            var serverMessage: String?
            if case .weakPassword(let message, _) = auth { serverMessage = message }
            return SignInFailure(message: SignInMessages.message(for: action, code: auth.errorCode.rawValue, serverMessage: serverMessage))
        }
        return SignInFailure(message: SignInMessages.message(for: action, code: nil, offline: error is URLError))
    }
}

struct SignInFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
