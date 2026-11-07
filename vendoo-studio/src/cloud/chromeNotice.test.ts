import { describe, expect, it } from "vitest";
import type { CloudConnection } from "../api/cloudExtension";
import { version as BRIDGE_VERSION } from "../../../vendoo-extension/manifest.json";
import { chromeNotice } from "./chromeNotice";

const bridge = (state: CloudConnection["state"], supportedActions: string[] = [], ok = true): CloudConnection =>
  ({ ok, state, extensionVersion: ok ? BRIDGE_VERSION : "", supportedActions });

describe("chromeNotice", () => {
  it("confirms a Bridge that can run marketplace actions", () => {
    const notice = chromeNotice(bridge("connected", ["poshmark.form.read", "marketplace.actions"]), true);
    expect(notice.button).toBe("Chrome connected");
    expect(notice.message.join(" ")).toContain(`Anteroom Bridge ${BRIDGE_VERSION}`);
  });

  it("asks for an update when Chrome runs older Bridge code than this site", () => {
    // Fixes in the Bridge reach Chrome only when its copy is updated and reloaded.
    const notice = chromeNotice({ ...bridge("connected", ["marketplace.actions", "marketplace.promotions"]), extensionVersion: "0.2.9" }, false);
    expect(notice.button).toBe("Update Bridge");
    expect(notice.message.join(" ")).toContain(`Anteroom Bridge 0.2.9 is older than this site’s ${BRIDGE_VERSION}`);
    expect(notice.message.join(" ")).toContain("Connect Chrome");
  });

  it("accepts a Bridge newer than this site", () => {
    const notice = chromeNotice({ ...bridge("connected", ["marketplace.actions"]), extensionVersion: "99.0.0" }, false);
    expect(notice.button).toBe("Chrome connected");
  });

  it("asks for an update when a connected Bridge cannot list", () => {
    const notice = chromeNotice({ ...bridge("connected", ["poshmark.form.read"]), extensionVersion: "0.2.39" }, false);
    expect(notice.button).toBe("Update Bridge");
    expect(notice.message.join(" ")).toContain("Anteroom Bridge 0.2.39 is out of date");
  });

  it("says when Chrome did not approve the request", () => {
    expect(chromeNotice(bridge("disconnected"), true).message.join(" ")).toContain("didn’t approve");
  });

  it("says the Bridge is missing when nothing answers", () => {
    const notice = chromeNotice(bridge("unavailable", [], false), true);
    expect(notice.button).toBe("Connect Anteroom Extension");
    expect(notice.message.join(" ")).toContain("isn’t answering");
    expect(notice.message.join(" ")).not.toMatch(/ZIP|chrome:\/\/extensions|managed/);
  });
});
