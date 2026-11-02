import { describe, expect, it } from "vitest";
import type { CloudConnection } from "../api/cloudExtension";
import { chromeNotice } from "./chromeNotice";

const bridge = (state: CloudConnection["state"], supportedActions: string[] = [], ok = true): CloudConnection =>
  ({ ok, state, extensionVersion: ok ? "0.2.41" : "", supportedActions });

describe("chromeNotice", () => {
  it("confirms a Bridge that can run marketplace actions", () => {
    const notice = chromeNotice(bridge("connected", ["poshmark.form.read", "marketplace.actions"]), true);
    expect(notice.button).toBe("Chrome connected");
    expect(notice.message.join(" ")).toContain("Anteroom Bridge 0.2.41");
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
    expect(notice.button).toBe("Connect Chrome");
    expect(notice.message.join(" ")).toContain("isn’t answering");
    expect(notice.message.join(" ")).not.toMatch(/ZIP|chrome:\/\/extensions|managed/);
  });
});
