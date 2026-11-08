import { describe, expect, it } from "vitest";
import { chatGroup, chatTime, groupChats } from "./chatHistory";

const now = new Date(2026, 10, 8, 15, 0);
const at = (days: number, hour = 10) => {
  const date = new Date(2026, 10, 8 - days, hour);
  return { updated_at: date.toISOString(), created_at: null };
};

describe("chat history", () => {
  it("groups by local day: today, yesterday, the week before, older", () => {
    expect([0, 1, 2, 6, 7, 40].map((days) => chatGroup(at(days), now))).toEqual([
      "Today", "Yesterday", "Previous 7 days", "Previous 7 days", "Older", "Older",
    ]);
    expect(chatGroup({ updated_at: null, created_at: null }, now)).toBe("Older");
  });

  it("falls back to when the chat started, and reads the server's naive UTC times", () => {
    const naive = new Date(2026, 10, 8, 9).toISOString().replace("Z", "");
    expect(chatGroup({ updated_at: null, created_at: naive }, now)).toBe("Today");
  });

  it("labels the time the way the group needs it", () => {
    expect(chatTime(at(1), now)).toBe("Yesterday");
    expect(chatTime(at(0), now)).toMatch(/10:00/);
    expect(chatTime({ updated_at: new Date(2025, 2, 3).toISOString(), created_at: null }, now)).toMatch(/2025/);
    expect(chatTime({ updated_at: null, created_at: null }, now)).toBe("");
  });

  it("lists newest first under each heading, leaving empty groups out", () => {
    const chats = [
      { id: "old", ...at(30) },
      { id: "today-early", ...at(0, 8) },
      { id: "today-late", ...at(0, 14) },
      { id: "yesterday", ...at(1) },
    ];
    expect(groupChats(chats, now).map(({ group, chats: rows }) => [group, rows.map((row) => row.id)])).toEqual([
      ["Today", ["today-late", "today-early"]],
      ["Yesterday", ["yesterday"]],
      ["Older", ["old"]],
    ]);
  });
});
