import { describe, expect, it } from "vitest";
import { showsLanding } from "./landingRoute";

describe("showsLanding", () => {
  it("shows the landing page to a signed-out visitor at the root", () => {
    expect(showsLanding("", false)).toBe(true);
    expect(showsLanding("?utm_source=ebay&ref=newsletter", false)).toBe(true);
  });

  it("goes straight to sign-in when the visitor asked for it", () => {
    expect(showsLanding("?sign-in", false)).toBe(false);
    expect(showsLanding("?create-account", false)).toBe(false);
    expect(showsLanding("?forgot-password", false)).toBe(false);
  });

  it("never hides a confirmation, reset or error link behind the landing page", () => {
    expect(showsLanding("?code=abc", false)).toBe(false);
    expect(showsLanding("?error=access_denied&error_description=expired", false)).toBe(false);
  });

  it("keeps a link to a listing on the sign-in path", () => {
    expect(showsLanding("?listing=abc", false)).toBe(false);
  });

  it("never shows the landing page in the Mac app", () => {
    expect(showsLanding("", true)).toBe(false);
  });
});
