import Foundation

/// What the seller was doing when sign-in failed.
enum SignInAction {
    case signIn, createAccount, reset, newPassword

    var fallback: String {
        switch self {
        case .signIn: "Could not sign in. Please try again."
        case .createAccount: "Could not create your account. Please try again."
        case .reset: "Could not send the reset link. Please try again."
        case .newPassword: "Could not save your new password. Please try again."
        }
    }
}

/// Plain-language messages for Supabase Auth error codes. The web app shows the same ones.
enum SignInMessages {
    static let minimumPasswordLength = 8

    static func message(for action: SignInAction, code: String?, serverMessage: String? = nil, offline: Bool = false) -> String {
        if offline { return "Could not reach sign-in. Check your connection and try again." }
        switch code {
        case "invalid_credentials":
            return "That email and password don’t match. Try again, or reset your password."
        case "email_not_confirmed":
            return "Confirm your email first, using the link we sent you."
        case "user_already_exists", "email_exists":
            return "An account with this email already exists. Sign in instead."
        case "weak_password":
            return serverMessage.flatMap { $0.isEmpty ? nil : $0 } ?? "Choose a stronger password."
        case "same_password":
            return "Choose a password different from your current one."
        case "over_email_send_rate_limit", "over_request_rate_limit":
            return "Too many attempts. Wait a minute and try again."
        case "signup_disabled":
            return "New accounts are not open right now."
        default:
            return action.fallback
        }
    }
}
