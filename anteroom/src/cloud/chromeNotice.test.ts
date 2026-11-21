import { describe, expect, it } from "vitest";
import { CHROME_WEB_STORE_URL } from "../api/chromeBridge";
import type { CloudConnection } from "../api/cloudExtension";
import { version as BRIDGE_VERSION } from "../../../anteroom-extension/manifest.json";
import { chromeNotice } from "./chromeNotice";

const bridge = (state: CloudConnection["state"], supportedActions: string[] = [], ok = true): CloudConnection =>
  ({ ok, state, extensionVersion: ok ? BRIDGE_VERSION : "", supportedActions });

describe("chromeNotice", () => {
  it("confirms a Bridge that can run marketplace actions", () => {
    const notice = chromeNotice(bridge("connected", ["poshmark.form.read", "marketplace.actions"]), true);
    expect(notice.button).toBe("Chrome connected");
    expect(notice.message.join(" ")).toContain(`Anteroom Bridge ${BRIDGE_VERSION}`);
    expect(notice.action).toBeUndefined();
  });

  it("asks for an update from Chrome Web Store when Chrome runs older Bridge code than this site", () => {
    // Fixes in the Bridge reach Chrome only when its copy is updated and reloaded.
    const notice = chromeNotice({ ...bridge("connected", ["marketplace.actions", "marketplace.promotions"]), extensionVersion: "0.2.9" }, false);
    expect(notice.button).toBe("Update Bridge");
    expect(notice.message.join(" ")).toContain(`Anteroom Bridge 0.2.9 is older than this site’s ${BRIDGE_VERSION}`);
    expect(notice.message.join(" ")).toContain("Chrome Web Store");
    expect(notice.action).toEqual({ label: "Open Chrome Web Store", href: CHROME_WEB_STORE_URL });
  });

  it("accepts a Bridge newer than this site", () => {
    const notice = chromeNotice({ ...bridge("connected", ["marketplace.actions"]), extensionVersion: "99.0.0" }, false);
    expect(notice.button).toBe("Chrome connected");
  });

  it("asks for an update when a connected Bridge cannot list", () => {
    const notice = chromeNotice({ ...bridge("connected", ["poshmark.form.read"]), extensionVersion: "0.2.39" }, false);
    expect(notice.button).toBe("Update Bridge");
    expect(notice.message.join(" ")).toContain("Anteroom Bridge 0.2.39 is out of date");
    expect(notice.action).toEqual({ label: "Open Chrome Web Store", href: CHROME_WEB_STORE_URL });
  });

  it("says when Chrome did not approve the request", () => {
    expect(chromeNotice(bridge("disconnected"), true).message.join(" ")).toContain("didn’t approve");
  });

  it("points sellers to Chrome Web Store when the Bridge is missing", () => {
    const notice = chromeNotice(bridge("unavailable", [], false), true);
    expect(notice.button).toBe("Connect Anteroom Extension");
    expect(notice.message.join(" ")).toContain("isn’t answering");
    expect(notice.message.join(" ")).toContain("Chrome Web Store");
    expect(notice.message.join(" ")).not.toMatch(/ZIP|chrome:\/\/extensions|managed|Open Anteroom on your computer/);
    expect(notice.action).toEqual({ label: "Install from Chrome Web Store", href: CHROME_WEB_STORE_URL });
  });
});
