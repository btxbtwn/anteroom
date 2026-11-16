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

  it("names the current editor sections and generation control", () => {
    const text = helpTopicMarkdown(HELP_TOPICS.find((topic) => topic.id === "help-create-listing")!);
    for (const label of ["General", "Measurements (in)", "Notes & Labels", "Generate listing"]) {
      expect(text).toContain(`**${label}**`);
    }
    for (const removed of ["**Input**", "**Forms**", "**Generate Listing**"]) expect(text).not.toContain(removed);
  });

  it("explains the default Sales records view and names current inventory analytics metrics", () => {
    const text = helpTopicMarkdown(HELP_TOPICS.find((topic) => topic.id === "help-analytics")!);
    expect(text).toContain("**Sales records**");
    expect(text).toContain("**Record amounts**");
    expect(text).toContain("**Inventory analytics**");
    for (const label of ["Revenue", "Profit", "Items sold", "Median days to sell", "Sell-through", "Profit margin"]) {
      expect(text).toContain(`**${label}**`);
    }
    for (const removed of ["**Sold**", "**Sell-through rate**", "**Median time to sell**"]) expect(text).not.toContain(removed);
  });

  it("routes sale plans through eBay Marketing and bulk rewriting through More", () => {
    const price = helpTopicMarkdown(HELP_TOPICS.find((topic) => topic.id === "help-price-drops")!);
    expect(price).toContain("**Marketplaces** → **eBay** → **Marketing**");
    expect(price).toContain("**More** → **Regenerate…**");
    const regenerate = helpTopicMarkdown(HELP_TOPICS.find((topic) => topic.id === "help-regenerate")!);
    expect(regenerate).toContain("**More** → **Regenerate…**");
    expect(regenerate).toContain("press **History**");
    expect(regenerate).not.toContain("**History** in the listing header");
    const backups = helpTopicMarkdown(HELP_TOPICS.find((topic) => topic.id === "help-backups")!);
    expect(backups).not.toContain("**History** in its header");
  });
});
