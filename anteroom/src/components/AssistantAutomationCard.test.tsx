import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { AssistantAutomation, AssistantMessage } from "../api/assistant";
import { AssistantAutomationCard, automationHeading } from "./AssistantAutomationCard";

vi.mock("@tanstack/react-query", () => ({
  useMutation: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
}));

const proposal: AssistantAutomation = {
  status: "proposed",
  proposal: {
    name: "Relist after 60 days",
    prompt: "When an item reaches 60 days listed on eBay, relist it.",
    timezone: "America/Los_Angeles",
    schedule: { kind: "daily", time: "09:00" },
    filters: { min_age_days: 60 },
    actions: [{ type: "relist" }, { type: "price", mode: "percent", value: 10, floor: 15 }],
    marketplaces: ["ebay", "poshmark"],
    issues: [],
    ready: true,
  },
};

const message: AssistantMessage = { id: "m1", role: "assistant", text: "Ready to review.", created_at: null, automation: proposal };

describe("assistant automation card", () => {
  it("shows the draft and asks before anything is saved", () => {
    const html = renderToStaticMarkup(
      <AssistantAutomationCard message={message} automation={proposal} onSettled={vi.fn()} />,
    );
    expect(html).toContain(automationHeading(proposal));
    expect(html).toContain("Daily at 09:00 (America/Los_Angeles)");
    expect(html).toContain("ebay, poshmark");
    expect(html).toContain(">Confirm<");
    expect(html).toContain(">Dismiss<");
  });

  it("hides confirm once the seller has confirmed", () => {
    const confirmed: AssistantAutomation = { ...proposal, status: "confirmed", automation_id: "abc" };
    const html = renderToStaticMarkup(
      <AssistantAutomationCard message={{ ...message, automation: confirmed }} automation={confirmed} onSettled={vi.fn()} />,
    );
    expect(html).toContain("Confirmed");
    expect(html).not.toContain(">Confirm<");
  });
});
