import { describe, expect, it } from "vitest";
import { HELP_TOPICS, helpTopicMarkdown } from "./helpTopics";

describe("help topics", () => {
  it("have unique ids and a few steps each", () => {
    expect(new Set(HELP_TOPICS.map((topic) => topic.id)).size).toBe(HELP_TOPICS.length);
    for (const topic of HELP_TOPICS) expect(topic.steps.length).toBeGreaterThanOrEqual(3);
  });

  it("render as a numbered list", () => {
    expect(helpTopicMarkdown({ id: "t", title: "T", steps: ["One", "Two"] })).toBe("1. One\n2. Two");
  });

  it("tell the seller an agent cannot publish without confirmation", () => {
    const agent = HELP_TOPICS.find((topic) => topic.id === "help-connect-agent");
    expect(agent && helpTopicMarkdown(agent)).toMatch(/cannot publish or delete unless you confirm/);
  });

  it("do not mention Vendoo", () => {
    const text = HELP_TOPICS.map(helpTopicMarkdown).join("\n").replace(/vendoo-studio/g, "");
    expect(text).not.toMatch(/Vendoo/i);
  });
});
