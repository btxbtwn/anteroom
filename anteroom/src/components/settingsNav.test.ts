import { describe, expect, it } from "vitest";
import {
  HOSTED_SETTINGS_SECTIONS,
  hostedSettingsSections,
  isSettingsSectionId,
  searchSettings,
  settingsSearchItemsFor,
  settingsSectionFor,
  SETTINGS_NAV_ITEMS,
  SETTINGS_SEARCH_ITEMS,
} from "./settingsNav";

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
    expect(SETTINGS_NAV_ITEMS.map((item) => item.id)).toEqual(["general", "usage", "listings", "assistant", "providers", "connections", "agents", "data", "logs", "experimental", "help"]);
    expect(searchSettings("mcp").map((item) => item.id)).toEqual(["agent-cli"]);
    expect(searchSettings("claude desktop").map((item) => item.id)).toEqual(["agent-cli"]);
    expect(searchSettings("vendoo")).toContainEqual(expect.objectContaining({section: "experimental", targetId: "listing-mode"}));
    expect(SETTINGS_SEARCH_ITEMS.find((item) => item.id === "chrome")?.title).toBe("Chrome");
    expect(searchSettings("inventory location").map((item) => item.id)).toEqual(["ships-from-zip"]);
    expect(searchSettings("ships from").map((item) => item.id)).toEqual(["ships-from-zip"]);
    expect(searchSettings("zip").map((item) => item.id)).toEqual(["ships-from-zip"]);
    expect(searchSettings("automatic offers")).toEqual([]);
    expect(isSettingsSectionId("billing")).toBe(false);
  });

  it("finds saved seller templates in Listings", () => {
    expect(searchSettings("saved templates")).toContainEqual(expect.objectContaining({id:"listing-templates",section:"listings",targetId:"listing-templates"}));
  });

  it("does not index the removed hosted marketplace browser placeholder", () => {
    for (const query of ["hosted marketplace browser", "without an extension"]) {
      expect(searchSettings(query)).toEqual([]);
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
  it("offers account usage to every hosted seller", () => {
    for (const managed of [true, false]) {
      expect(hostedSettingsSections(managed)).toContain("usage");
      expect(searchSettings("usage", settingsSearchItemsFor(true, managed)).map(item => item.section)).toContain("usage");
    }
    expect(searchSettings("usage", settingsSearchItemsFor(false))).toEqual([]);
    expect(settingsSectionFor("usage", false)).toBe("general");
  });

  it("opens every section the web sidebar offers, Connections, Agents and Help included", () => {
    for (const id of HOSTED_SETTINGS_SECTIONS) expect(settingsSectionFor(id, true)).toBe(id);
    expect(HOSTED_SETTINGS_SECTIONS).toContain("connections");
    expect(HOSTED_SETTINGS_SECTIONS).toContain("agents");
    expect(HOSTED_SETTINGS_SECTIONS).toContain("experimental");
    expect(HOSTED_SETTINGS_SECTIONS).toContain("help");
    expect(searchSettings("mcp", settingsSearchItemsFor(true, true)).map((item) => item.id)).toEqual(["agent-cli"]);
    expect(searchSettings("support", settingsSearchItemsFor(true, true)).map((item) => item.id)).toContain("support");
    expect(searchSettings("how to", settingsSearchItemsFor(true, true)).map((item) => item.id)).toContain("help");
  });

  it("sends desktop-only sections to Providers on the web, and changes nothing on the Mac", () => {
    expect(settingsSectionFor("logs", true)).toBe("providers");
    expect(settingsSectionFor("logs", false)).toBe("logs");
  });

  it("hides Providers and provider search items when hosted managed AI is active", () => {
    expect(hostedSettingsSections(false)).toContain("providers");
    expect(hostedSettingsSections(true)).not.toContain("providers");
    expect(settingsSectionFor("providers", true, true)).toBe("general");
    expect(settingsSectionFor("logs", true, true)).toBe("general");
    expect(settingsSectionFor("providers", false, true)).toBe("providers");

    expect(searchSettings("listing ai", settingsSearchItemsFor(true, false)).map((item) => item.id)).toContain("listing-ai");
    expect(searchSettings("listing ai", settingsSearchItemsFor(true, true))).toEqual([]);
    expect(searchSettings("mimo", settingsSearchItemsFor(true, true))).toEqual([]);
    expect(searchSettings("theme", settingsSearchItemsFor(true, true)).map((item) => item.id)).toEqual(["appearance"]);
  });
});

describe("connected apps search", () => {
  it("is found on the web, where apps sign in, and not on the Mac", () => {
    expect(searchSettings("connected apps", settingsSearchItemsFor(true)).map((item) => item.id)).toEqual(["agent-mcp-connections"]);
    expect(searchSettings("gemini", settingsSearchItemsFor(true)).map((item) => item.id)).toContain("agent-mcp-connections");
    expect(searchSettings("connected apps", settingsSearchItemsFor(false))).toEqual([]);
  });
});
