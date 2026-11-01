import Foundation

@main
struct SignInMessagesTests {
    static func main() {
        precondition(SignInMessages.message(for: .signIn, code: "invalid_credentials").contains("don’t match"))
        precondition(SignInMessages.message(for: .signIn, code: "email_not_confirmed").contains("Confirm your email"))
        precondition(SignInMessages.message(for: .createAccount, code: "user_already_exists").contains("Sign in instead"))
        precondition(SignInMessages.message(for: .createAccount, code: "email_exists").contains("Sign in instead"))
        precondition(SignInMessages.message(for: .reset, code: "over_email_send_rate_limit").contains("Wait a minute"))
        precondition(SignInMessages.message(for: .createAccount, code: "weak_password", serverMessage: "Password should be at least 8 characters.")
            == "Password should be at least 8 characters.")
        precondition(SignInMessages.message(for: .createAccount, code: "weak_password", serverMessage: "") == "Choose a stronger password.")
        precondition(SignInMessages.message(for: .signIn, code: "invalid_credentials", offline: true).contains("Check your connection"))
        precondition(SignInMessages.message(for: .newPassword, code: "something_new") == "Could not save your new password. Please try again.")
        precondition(SignInMessages.message(for: .reset, code: nil) == "Could not send the reset link. Please try again.")
        print("Sign-in message tests passed")
    }
}
