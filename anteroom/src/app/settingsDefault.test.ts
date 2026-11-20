import source from "./App.tsx?raw";
import { expect, it } from "vitest";

it("opens Settings at the shared General default in every runtime", () => {
  expect(source).toContain("useState<SettingsSectionId>(DEFAULT_SETTINGS_SECTION)");
  expect(source).toContain("setSettingsSection(DEFAULT_SETTINGS_SECTION)");
  expect(source).not.toContain('runtime.hosted ? "providers" : DEFAULT_SETTINGS_SECTION');
});
