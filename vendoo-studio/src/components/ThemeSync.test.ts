import { expect, it } from "vitest";
import { savedThemePreference } from "./ThemeSync";

it("keeps the cached theme until Settings answers", () => {
  expect(savedThemePreference(undefined)).toBeNull();
});

it("applies the saved theme, and follows the device when none was chosen", () => {
  expect(savedThemePreference({ theme: "light" })).toBe("light");
  expect(savedThemePreference({ theme: "dark" })).toBe("dark");
  expect(savedThemePreference({})).toBe("system");
});
