import { describe, expect, it } from "vitest";
import { CHAT_COLUMN_WIDTH, chatLayout, FORM_ROOM, isChatToggleShortcut } from "./ListingChatPopup";

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

describe("chatLayout", () => {
  it("gives the chat its own column only when the form keeps a comfortable width", () => {
    expect(FORM_ROOM).toBeGreaterThanOrEqual(160 + 440);
    expect(chatLayout(FORM_ROOM + CHAT_COLUMN_WIDTH.default, null)).toBe("column");
    expect(chatLayout(FORM_ROOM + CHAT_COLUMN_WIDTH.default - 1, null)).toBe("float");
  });

  it("keeps the chat a column on a 1280px window beside the default sidebar", () => {
    // 1280 window minus the 340px listing sidebar leaves a 940px form area.
    expect(chatLayout(1280 - 340, null)).toBe("column");
    expect(chatLayout(1100 - 340, null)).toBe("float");
  });

  it("counts the width the seller dragged the column to", () => {
    expect(chatLayout(FORM_ROOM + 400, 400)).toBe("column");
    expect(chatLayout(FORM_ROOM + 400, 420)).toBe("float");
  });

  it("floats before the area has been measured", () => {
    expect(chatLayout(0, null)).toBe("float");
  });
});
