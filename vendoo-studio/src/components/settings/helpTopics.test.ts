import { describe, expect, it } from "vitest";
import { HELP_TOPICS, helpTopicMarkdown, helpTopicsForMode } from "./helpTopics";

describe("help topics", () => {
  it("have unique ids and a few steps each", () => {
    expect(new Set(HELP_TOPICS.map((topic) => topic.id)).size).toBe(HELP_TOPICS.length);
    for (const topic of HELP_TOPICS) expect(topic.steps.length).toBeGreaterThanOrEqual(3);
  });

  it("render as a numbered list", () => {
    expect(helpTopicMarkdown({ id: "t", title: "T", steps: ["One", "Two"] })).toBe("1. One\n2. Two");
  });

  it("tell the seller Studio never publishes on its own", () => {
    const send = HELP_TOPICS.find((topic) => topic.id === "help-send-to-vendoo");
    expect(send && helpTopicMarkdown(send)).toMatch(/never publishes/);
  });

  it("hides the send guide and drops Vendoo sentences when Send to Vendoo is off", () => {
    const topics = helpTopicsForMode(false);
    expect(topics.map((topic) => topic.id)).not.toContain("help-send-to-vendoo");
    expect(HELP_TOPICS.map((topic) => topic.id)).toContain("help-send-to-vendoo");
    const regenerate = topics.find((topic) => topic.id === "help-regenerate");
    expect(regenerate?.steps.join("\n")).not.toMatch(/Vendoo/);
    expect(topics.find((topic) => topic.id === "help-connect-chrome")?.steps.join("\n")).not.toMatch(/Vendoo/);
    expect(helpTopicsForMode(true)).toBe(HELP_TOPICS);
  });
});
