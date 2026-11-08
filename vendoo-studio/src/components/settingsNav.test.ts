import { describe, expect, it } from "vitest";
import { HOSTED_SETTINGS_SECTIONS, isSettingsSectionId, searchSettings, settingsSectionFor, SETTINGS_NAV_ITEMS, SETTINGS_SEARCH_ITEMS } from "./settingsNav";

describe("searchSettings", () => {
  it("returns nothing for a blank query", () => {
    expect(searchSettings("   ")).toEqual([]);
  });

  it("matches titles and search terms case-insensitively", () => {
    expect(searchSettings("TAILNET").map((item) => item.id)).toEqual(["tailscale-https"]);
    expect(searchSettings("brave search").map((item) => item.id)).toEqual(["brave"]);
    expect(searchSettings("description formula").map((item) => item.id)).toContain("listing-formulas");
  });

  it("matches when every word appears somewhere in the item", () => {
    expect(searchSettings("sign  chatgpt").map((item) => item.id)).toContain("chatgpt");
  });

  it("finds appearance by light/dark synonyms", () => {
    expect(searchSettings("light mode").map((item) => item.id)).toEqual(["appearance"]);
    expect(searchSettings("color scheme").map((item) => item.id)).toEqual(["appearance"]);
  });

  it("only points at real sections", () => {
    for (const item of SETTINGS_SEARCH_ITEMS) expect(isSettingsSectionId(item.section)).toBe(true);
    expect(SETTINGS_NAV_ITEMS.map((item) => item.id)).toEqual(["general", "listings", "assistant", "providers", "connections", "agents", "data", "logs", "experimental", "help"]);
    expect(searchSettings("mcp").map((item) => item.id)).toEqual(["agent-cli"]);
    expect(searchSettings("claude desktop").map((item) => item.id)).toEqual(["agent-cli"]);
    expect(searchSettings("vendoo")).toContainEqual(expect.objectContaining({section: "experimental", targetId: "listing-mode"}));
    expect(SETTINGS_SEARCH_ITEMS.find((item) => item.id === "chrome")?.title).toBe("Chrome");
    expect(searchSettings("inventory location").map((item) => item.id)).toEqual(["ebay-inventory-location"]);
    expect(searchSettings("ships from").map((item) => item.id)).toEqual(["ships-from-zip"]);
    expect(searchSettings("zip").map((item) => item.id).sort()).toEqual(["ebay-inventory-location", "ships-from-zip"]);
    expect(searchSettings("automatic offers")).toEqual([]);
    expect(isSettingsSectionId("billing")).toBe(false);
  });

  it("finds saved seller templates in Listings", () => {
    expect(searchSettings("saved templates")).toContainEqual(expect.objectContaining({id:"listing-templates",section:"listings",targetId:"listing-templates"}));
  });

  it("finds the hosted browser experiment by its name and extension-free workflow", () => {
    for (const query of ["hosted marketplace browser", "without an extension"]) {
      expect(searchSettings(query)).toContainEqual(expect.objectContaining({
        section: "experimental", targetId: "hosted-marketplace-browser",
      }));
    }
  });

  it("gives every search result its own id", () => {
    const ids = SETTINGS_SEARCH_ITEMS.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("finds both the listing checklist and the sign-in accounts", () => {
    expect(searchSettings("marketplaces").map((item) => item.id).sort()).toEqual(["accounts", "where-you-list"]);
  });
});

describe("settings on the web", () => {
  it("opens every section the web sidebar offers, Connections included", () => {
    for (const id of HOSTED_SETTINGS_SECTIONS) expect(settingsSectionFor(id, true)).toBe(id);
    expect(HOSTED_SETTINGS_SECTIONS).toContain("connections");
    expect(HOSTED_SETTINGS_SECTIONS).toContain("experimental");
  });

  it("sends desktop-only sections to Providers on the web, and changes nothing on the Mac", () => {
    expect(settingsSectionFor("logs", true)).toBe("providers");
    expect(settingsSectionFor("logs", false)).toBe("logs");
  });
});
