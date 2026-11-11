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
    /// How an eBay or Etsy sign-in the browser just came back from ended.
    var marketplaceSignInMessage: String?
    let api: AnteroomAPI?
    let changes = LiveChanges()
    private let client: SupabaseClient?
    /// Lets the API client sign out once the store exists.
    private let ended: SignInEndedRelay
    /// The seller chose Sign out; requests refused meanwhile aren't an ended sign-in.
    private var signingOut = false

    init() {
        let ended = SignInEndedRelay()
        self.ended = ended
        guard let key = AppConfig.supabasePublishableKey else {
            client = nil
            api = nil
            state = .missingConfiguration
            return
        }
        // The saved session opens the app straight away, even expired or offline;
        // the library renews it in the background and signs out only when the
        // server refuses it.
        let client: SupabaseClient
#if DEBUG
        if AppConfig.sandboxURL != nil {
            client = SupabaseClient(supabaseURL: AppConfig.supabaseURL, supabaseKey: key,
                                    options: .init(auth: .init(storage: SandboxAuthStorage(),
                                                               emitLocalSessionAsInitialSession: true)))
        } else {
            client = SupabaseClient(supabaseURL: AppConfig.supabaseURL, supabaseKey: key,
                                    options: .init(auth: .init(emitLocalSessionAsInitialSession: true)))
        }
#else
        client = SupabaseClient(supabaseURL: AppConfig.supabaseURL, supabaseKey: key,
                                options: .init(auth: .init(emitLocalSessionAsInitialSession: true)))
#endif
        self.client = client
        api = AnteroomAPI(baseURL: AppConfig.apiBaseURL, auth: APIAuthorization(
            accessToken: { try await SessionStore.renewing { try await client.auth.session.accessToken } },
            refreshedToken: { try await SessionStore.renewing { try await client.auth.refreshSession().accessToken } },
            signedOut: { await ended.fire() }))
        ended.handler = { [weak self] in await self?.sessionEnded() }
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
                // The saved session, possibly expired: requests renew it when they
                // can, and a refusal from the server ends it with `.signedOut`.
                if let session {
                    state = .signedIn(email: session.user.email)
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
        if let saved = client.auth.currentSession {
            state = .signedIn(email: saved.user.email)
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

    func completeMarketplaceSignIn(_ signIn: MarketplaceSignInReturn) async {
        guard let api, case .signedIn = state else {
            marketplaceSignInMessage = "Sign in to Anteroom, then connect \(signIn.name) again."
            return
        }
        do {
            let label = try await api.finishMarketplaceSignIn(signIn)
            marketplaceSignInMessage = "\(label ?? signIn.name) is connected."
        } catch {
            marketplaceSignInMessage = "\(signIn.name) was not connected. \(error.localizedDescription)"
        }
    }

    /// Follows changes from the seller's other devices while the workspace is open.
    func followChanges() async {
        guard let client else { return }
#if DEBUG
        // The local sandbox has no Realtime service; screens reload as before.
        if AppConfig.sandboxURL != nil { return }
#endif
        guard let user = try? await client.auth.session.user else { return }
        await changes.follow(client: client, userID: user.id)
    }

    func signOut() async {
        signingOut = true
        defer { signingOut = false }
        try? await client?.auth.signOut()
        UserDefaults.standard.removeObject(forKey: Self.pendingResetKey)
        await forgetSeller()
        state = .signedOut
    }

    /// The server refused the sign-in and it could not be renewed: back to the sign-in screen.
    private func sessionEnded() async {
        guard case .signedIn = state, !signingOut else { return }
        signingOut = true
        defer { signingOut = false }
        try? await client?.auth.signOut(scope: .local)
        await forgetSeller()
        signInError = "Your sign-in has ended. Sign in again."
        state = .signedOut
    }

    /// Nothing of this seller's stays on the phone after sign-out.
    private func forgetSeller() async {
        await api?.forgetSeller()
        PhotoCache.images.removeAllObjects()
        EditorDraftStore.removeAll()
    }

    /// After the server deleted the account: forget this iPhone's session and
    /// everything it kept of the seller's data. The account is gone, so only
    /// the local session is cleared.
    func finishAccountDeletion() async {
        try? await client?.auth.signOut(scope: .local)
        UserDefaults.standard.removeObject(forKey: Self.pendingResetKey)
        URLCache.shared.removeAllCachedResponses()
        await forgetSeller()
        state = .signedOut
    }

    /// Runs a token read, reporting a refresh token the server refused as
    /// `SignInRejected`. Anything else (offline, a 5xx, rate limiting) passes
    /// through, so the API client shows it as a failure to try again.
    private nonisolated static func renewing(_ read: @Sendable () async throws -> String) async throws -> String {
        do {
            return try await read()
        } catch let auth as AuthError {
            switch auth {
            case .sessionMissing:
                // Nothing saved, or the server refused the refresh token and
                // supabase-swift already removed it.
                throw SignInRejected()
            case .api(_, let code, let data, let response)
                where SignInRenewal.rejects(status: response.statusCode, code: code.rawValue, body: data):
                throw SignInRejected()
            default:
                throw auth
            }
        }
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

/// Created before the store, so the API client can reach it once it exists.
@MainActor
final class SignInEndedRelay {
    var handler: (@MainActor () async -> Void)?

    func fire() async {
        await handler?()
    }
}

struct SignInFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
