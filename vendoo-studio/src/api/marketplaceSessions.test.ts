import { describe, expect, it } from "vitest";

import {
  chromeSessionPresentation,
  sessionIsExpiring,
  sessionNotification,
  type ExtensionSession,
} from "./marketplaceSessions";

const now = Date.parse("2026-10-24T18:00:00");

function session(overrides: Partial<ExtensionSession> = {}): ExtensionSession {
  return { state: "signed_in", expires_at: null, label: null, expiring: false, ...overrides };
}

describe("chrome marketplace sign-in", () => {
  it("shows a saved account and a same-day expiry", () => {
    const expiresAt = Math.floor(now / 1000) + 60 * 60;
    const current = session({ label: "ada", expires_at: expiresAt, expiring: true });
    expect(sessionIsExpiring(current, now)).toBe(true);
    expect(chromeSessionPresentation(current, true, now)).toEqual({
      tone: "warning",
      label: "Signed in as ada · expires today",
    });
  });

  it("asks for a new sign-in when the session expired", () => {
    expect(chromeSessionPresentation(session({ state: "expired", expires_at: 10 }), true, now)).toEqual({
      tone: "error",
      label: "Sign in again",
    });
    expect(sessionNotification("depop", "Depop", session({ state: "expired", expires_at: 10 }), "signed_in")).toMatchObject({
      key: "depop:expired:10",
      title: "Depop sign-in expired",
    });
  });

  it("does not nag when nobody has signed in, and does when a saved sign-in disappears", () => {
    const signedOut = session({ state: "signed_out" });
    expect(sessionNotification("mercari", "Mercari", signedOut, undefined)).toBeNull();
    expect(sessionNotification("mercari", "Mercari", signedOut, "signed_in")?.title).toBe("Mercari needs a sign-in");
    expect(sessionNotification("mercari", "Mercari", signedOut, undefined, true)?.title).toBe("Mercari needs a sign-in");
    expect(chromeSessionPresentation(undefined, false, now).label).toBe("Connect Chrome first");
  });

  it("waits until the sign-in has ended before asking", () => {
    const expiresAt = Math.floor(now / 1000) + 60 * 60;
    expect(sessionNotification("mercari", "Mercari", session({ expires_at: expiresAt, expiring: true }), "signed_in")).toBeNull();
  });
});
