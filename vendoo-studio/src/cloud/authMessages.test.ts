import { describe, expect, it } from "vitest";
import { authErrorMessage, linkOpenedElsewhere } from "./authMessages";

describe("sign-in messages", () => {
  it("tells the seller what went wrong in plain words", () => {
    expect(authErrorMessage("sign_in", { code: "invalid_credentials" })).toContain("don’t match");
    expect(authErrorMessage("sign_in", { code: "email_not_confirmed" })).toContain("Confirm your email");
    expect(authErrorMessage("create_account", { code: "user_already_exists" })).toContain("Sign in instead");
    expect(authErrorMessage("reset", { code: "over_email_send_rate_limit" })).toContain("Wait a minute");
  });

  it("shows the server's password rule when a password is too weak", () => {
    expect(authErrorMessage("create_account", { code: "weak_password", message: "Password should be at least 8 characters." }))
      .toBe("Password should be at least 8 characters.");
  });

  it("separates a lost connection from a refused sign-in", () => {
    expect(authErrorMessage("sign_in", { name: "AuthRetryableFetchError" })).toContain("Check your connection");
  });

  it("falls back to a message for the action", () => {
    expect(authErrorMessage("new_password", { code: "something_new" })).toBe("Could not save your new password. Please try again.");
    expect(authErrorMessage("reset", null)).toBe("Could not send the reset link. Please try again.");
  });

  it("recognizes a sign-in link opened in another browser", () => {
    expect(linkOpenedElsewhere("?code=abc")).toBe(true);
    expect(linkOpenedElsewhere("?forgot-password")).toBe(false);
  });
});
