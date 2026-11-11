import Foundation

@main
struct SignInRenewalTests {
    static func main() {
        let empty = Data()
        // The server refused the refresh token: sign out.
        precondition(SignInRenewal.rejects(status: 400, code: "refresh_token_not_found", body: empty))
        precondition(SignInRenewal.rejects(status: 400, code: "refresh_token_already_used", body: empty))
        precondition(SignInRenewal.rejects(status: 403, code: "session_not_found", body: empty))
        precondition(SignInRenewal.rejects(status: 401, code: "session_expired", body: empty))
        precondition(SignInRenewal.rejects(status: 400, code: "unknown",
                                           body: Data(#"{"error":"invalid_grant","error_description":"Invalid Refresh Token"}"#.utf8)))
        // The server failed or is busy: keep the saved sign-in and try again later.
        precondition(!SignInRenewal.rejects(status: 500, code: "unexpected_failure", body: empty))
        precondition(!SignInRenewal.rejects(status: 503, code: "refresh_token_not_found", body: empty))
        precondition(!SignInRenewal.rejects(status: 429, code: "over_request_rate_limit", body: empty))
        precondition(!SignInRenewal.rejects(status: 504, code: nil,
                                            body: Data(#"{"error":"invalid_grant"}"#.utf8)))
        // A refusal for another reason (a captcha, a bad request) is not an ended sign-in.
        precondition(!SignInRenewal.rejects(status: 400, code: "validation_failed", body: empty))
        precondition(!SignInRenewal.rejects(status: 400, code: nil, body: Data("not json".utf8)))
        print("Sign-in renewal tests passed")
    }
}
