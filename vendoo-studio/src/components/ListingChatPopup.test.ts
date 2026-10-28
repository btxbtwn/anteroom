import { describe, expect, it } from "vitest";
import { isChatToggleShortcut } from "./ListingChatPopup";

const key = (overrides: Partial<Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey">>) => ({
  key: "j", metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, ...overrides,
});

describe("isChatToggleShortcut", () => {
  it("is ⌘J on a Mac", () => {
    expect(isChatToggleShortcut(key({ metaKey: true }), true)).toBe(true);
    expect(isChatToggleShortcut(key({ ctrlKey: true }), true)).toBe(false);
  });

  it("is Ctrl+J elsewhere", () => {
    expect(isChatToggleShortcut(key({ ctrlKey: true, key: "J" }), false)).toBe(true);
    expect(isChatToggleShortcut(key({ metaKey: true }), false)).toBe(false);
  });

  it("ignores other keys and extra modifiers", () => {
    expect(isChatToggleShortcut(key({ metaKey: true, key: "k" }), true)).toBe(false);
    expect(isChatToggleShortcut(key({ metaKey: true, shiftKey: true }), true)).toBe(false);
    expect(isChatToggleShortcut(key({}), true)).toBe(false);
  });
});
