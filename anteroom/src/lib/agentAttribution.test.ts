import { describe, expect, it } from "vitest";
import { fromAgent } from "./agentAttribution";

describe("fromAgent", () => {
  it("names the agent that started an action", () => {
    expect(fromAgent("mcp:Claude")).toBe("From Claude (AI assistant)");
    expect(fromAgent("mcp:")).toBe("From your AI assistant");
  });

  it("says nothing for the seller's own actions", () => {
    expect(fromAgent(null)).toBeNull();
    expect(fromAgent(undefined)).toBeNull();
    expect(fromAgent("seller")).toBeNull();
  });
});
