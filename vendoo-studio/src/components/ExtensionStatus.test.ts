import { describe, expect, it } from "vitest";
import { extensionConnection } from "./ExtensionStatus";

describe("extensionConnection", () => {
  it("says Chrome is connected when the extension matches this build", () => {
    expect(extensionConnection({ connected: true, up_to_date: true })).toEqual({
      label: "Connected",
      tone: "success",
      detail: "Chrome is running this build's listing extension.",
    });
  });

  it("names the extra copy when Chrome has two bridges loaded", () => {
    const view = extensionConnection({
      connected: true,
      up_to_date: true,
      duplicate_bridge: true,
      load_path: "~/Library/Application Support/Anteroom/vendoo-extension",
    });
    expect(view.label).toBe("Two copies loaded");
    expect(view.tone).toBe("warning");
    expect(view.detail).toContain("~/Library/Application Support/Anteroom/vendoo-extension");
    expect(view.detail).toContain("chrome://extensions");
  });

  it("says offline when Chrome has not connected", () => {
    const view = extensionConnection(undefined);
    expect(view.label).toBe("Offline");
    expect(view.tone).toBe("muted");
    expect(view.detail).toContain("Connect Chrome");
  });

  it("names the versions when Chrome is running an older extension", () => {
    const view = extensionConnection({
      connected: true,
      up_to_date: false,
      expected_version: "0.1.297",
      version: "0.1.292",
    });
    expect(view.label).toBe("Outdated");
    expect(view.tone).toBe("warning");
    expect(view.detail).toContain("0.1.297");
    expect(view.detail).toContain("0.1.292");
  });
});
