import { describe, expect, it } from "vitest";
import { consentRequest, isConsentPage } from "./appConsent";
import { showsLanding } from "./landingRoute";

describe("app consent link", () => {
  it("reads the pending request Supabase names", () => {
    expect(consentRequest("?authorization_id=abc-123")).toBe("abc-123");
    expect(consentRequest("?authorization_id=")).toBeNull();
    expect(consentRequest("")).toBeNull();
  });

  it("is only the consent path", () => {
    expect(isConsentPage("/oauth/consent")).toBe(true);
    expect(isConsentPage("/")).toBe(false);
    expect(isConsentPage("/oauth/consent/x")).toBe(false);
  });

  it("asks a signed-out seller to sign in rather than showing the landing page", () => {
    expect(showsLanding("?authorization_id=abc", false)).toBe(false);
  });
});
