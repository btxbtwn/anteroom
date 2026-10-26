import { describe, expect, it } from "vitest";

import type { AutomationDraft } from "../api/automations";
import { bodyFromForm, describeActions, describeSchedule, emptyForm, formFromDraft } from "./automationsFormat";

const draft: AutomationDraft = {
  name: "Relist after 60 days",
  prompt: "When an item reaches 60 days listed on eBay and Poshmark, drop the price by 10%.",
  timezone: "America/Los_Angeles",
  schedule: { kind: "daily", time: "09:00" },
  filters: { min_age_days: 60 },
  actions: [
    { type: "price", mode: "percent", value: 10, floor: 15 },
    { type: "relist" },
  ],
  marketplaces: ["ebay", "poshmark"],
  issues: [],
  ready: true,
};

describe("automations form", () => {
  it("fills the form from a drafted sentence", () => {
    const form = formFromDraft(draft);
    expect(form.kind).toBe("daily");
    expect(form.time).toBe("09:00");
    expect(form.minAge).toBe("60");
    expect(form.priceOn).toBe(true);
    expect(form.priceMode).toBe("percent");
    expect(form.priceValue).toBe("10");
    expect(form.floor).toBe("15");
    expect(form.relist).toBe(true);
    expect(form.marketplaces).toEqual(["ebay", "poshmark"]);
    expect(describeSchedule(draft.schedule, draft.timezone)).toBe("Daily at 09:00 (America/Los_Angeles)");
    expect(describeActions(draft.actions)).toBe("drop 10%, floor $15, relist");
  });

  it("sends a bare sentence for the server to parse", () => {
    const form = emptyForm("UTC");
    form.prompt = "Delist men's t-shirts on eBay on 2026-11-03 at 9:00am.";
    const body = bodyFromForm(form, true);
    expect(body.activate).toBe(true);
    expect(body.prompt).toBe(form.prompt);
    expect(body.actions).toBeUndefined();
    expect(body.schedule).toBeUndefined();
  });

  it("sends the boxes the seller filled in", () => {
    const body = bodyFromForm(formFromDraft(draft), true);
    expect(body.schedule).toEqual({ kind: "daily", time: "09:00" });
    expect(body.actions?.map((action) => action.type)).toEqual(["price", "relist"]);
    expect(body.actions?.[0]).toMatchObject({ mode: "percent", value: 10, floor: 15 });
    expect(body.marketplaces).toEqual(["ebay", "poshmark"]);
    expect(body.filters?.min_age_days).toBe(60);
    expect(body.activate).toBe(true);
  });
});
