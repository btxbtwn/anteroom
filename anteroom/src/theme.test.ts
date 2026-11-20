import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_THEME, normalizeTheme, readCachedTheme, resolveTheme } from "./theme";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("normalizeTheme", () => {
  it("accepts dark, light, and system", () => {
    expect(normalizeTheme("dark")).toBe("dark");
    expect(normalizeTheme("light")).toBe("light");
    expect(normalizeTheme("system")).toBe("system");
  });

  it("follows the device for unknown values", () => {
    expect(DEFAULT_THEME).toBe("system");
    expect(normalizeTheme(undefined)).toBe(DEFAULT_THEME);
    expect(normalizeTheme("solarized")).toBe(DEFAULT_THEME);
    expect(normalizeTheme(1)).toBe(DEFAULT_THEME);
  });
});

describe("resolveTheme", () => {
  it("returns an explicit preference unchanged", () => {
    expect(resolveTheme("light", false)).toBe("light");
    expect(resolveTheme("dark", true)).toBe("dark");
  });

  it("follows the system preference when set to system", () => {
    expect(resolveTheme("system", true)).toBe("light");
    expect(resolveTheme("system", false)).toBe("dark");
  });
});

describe("readCachedTheme", () => {
  it("reads the legacy vendoo-studio:theme key until anteroom:theme is set", () => {
    const store = new Map([["vendoo-studio:theme", "light"]]);
    vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null });
    expect(readCachedTheme()).toBe("light");
    store.set("anteroom:theme", "dark");
    expect(readCachedTheme()).toBe("dark");
  });
});
