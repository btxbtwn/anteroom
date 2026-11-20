/** Plain-language messages for Supabase Auth errors, by error code. */
export type AuthAction = "sign_in" | "create_account" | "reset" | "new_password";

const FALLBACK: Record<AuthAction, string> = {
  sign_in: "Could not sign in. Please try again.",
  create_account: "Could not create your account. Please try again.",
  reset: "Could not send the reset link. Please try again.",
  new_password: "Could not save your new password. Please try again.",
};

export function authErrorMessage(action: AuthAction, error: { code?: string; message?: string; name?: string } | null | undefined): string {
  if (!error) return FALLBACK[action];
  if (error.name === "AuthRetryableFetchError") return "Could not reach sign-in. Check your connection and try again.";
  switch (error.code) {
    case "invalid_credentials":
      return "That email and password don’t match. Try again, or reset your password.";
    case "email_not_confirmed":
      return "Confirm your email first, using the link we sent you.";
    case "user_already_exists":
    case "email_exists":
      return "An account with this email already exists. Sign in instead.";
    case "weak_password":
      return error.message || "Choose a stronger password.";
    case "same_password":
      return "Choose a password different from your current one.";
    case "over_email_send_rate_limit":
    case "over_request_rate_limit":
      return "Too many attempts. Wait a minute and try again.";
    case "signup_disabled":
      return "New accounts are not open right now.";
    default:
      return FALLBACK[action];
  }
}

/** A sign-in link that opened somewhere other than where it was asked for leaves `?code=` it cannot use. */
export function linkOpenedElsewhere(search: string): boolean {
  return new URLSearchParams(search).has("code");
}
