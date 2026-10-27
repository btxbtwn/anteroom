import { describe, expect, it, vi } from "vitest";
import type { Promotion, PromotionResult } from "./api/promotions";
import { promotionTasks, runPromotionTasks } from "./promotions";

const row = (id: string, overrides: Partial<Promotion> = {}): Promotion => ({
  id, batch_id: "b", conversation_id: id, marketplace: "poshmark", action: "share", listing_id: "x", title: id, price: "50.00",
  offer_price: null, floor: null, discount_percent: null, like_count: 1, shipping_options: [], shipping_discount_id: null,
  shipping_discount_label: null, state: "preview", note: null, marketplace_message: null, created_at: null, expires_at: null,
  finished_at: null, ...overrides,
});
const reply = (id: string, overrides: Partial<PromotionResult> = {}): PromotionResult => ({ ...row(id), state: "done", error_code: null, stop: false, ...overrides });

describe("promotion tasks", () => {
  it("keeps only checked reviewed rows, and offers only with a shipping choice", () => {
    const rows = [row("a"), row("b", { state: "skipped" }), row("c"), row("d", { action: "offer" }), row("e", { action: "offer" })];
    expect(promotionTasks(rows, new Set(["c"]), { e: "none" }).map((task) => [task.id, task.shippingDiscountId])).toEqual([["a", undefined], ["e", "none"]]);
  });

  it("sends each once and stops the rest on a rate limit", async () => {
    const perform = vi.fn(async (id: string) => (id === "b" ? reply(id, { state: "failed", stop: true, note: "Poshmark is limiting requests" }) : reply(id)));
    const tasks = promotionTasks([row("a"), row("b"), row("c")], new Set(), {});
    const result = await runPromotionTasks(tasks, perform, () => false, () => {});
    expect(perform.mock.calls.map(([id]) => id)).toEqual(["a", "b"]);
    expect(result.map((task) => task.state)).toEqual(["done", "failed", "stopped"]);
  });

  it("stops after an unconfirmed result and never retries", async () => {
    const perform = vi.fn(async () => { throw new TypeError("Failed to fetch"); });
    const result = await runPromotionTasks(promotionTasks([row("a"), row("b")], new Set(), {}), perform, () => false, () => {});
    expect(perform).toHaveBeenCalledTimes(1);
    expect(result.map((task) => task.state)).toEqual(["unconfirmed", "stopped"]);
  });
});
