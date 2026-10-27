import { describe, expect, it, vi } from "vitest";
import type { BulkMarketplaceResult, BulkMarketplaceRow } from "./api/bulkMarketplace";
import { ITEMS_AT_ONCE, reviewedTasks, runMarketplaceTasks } from "./bulkMarketplace";

const row = (id: string, patch: Partial<BulkMarketplaceRow> = {}): BulkMarketplaceRow => ({
  conversation_id: id, title: `Item ${id}`, action: "relist", revision_id: `rev-${id}`, marketplaces: ["ebay"],
  skipped: [], skip_reason: null, review_token: "a".repeat(64), ...patch,
});
const reply = (id: string, ok: boolean, errors = {}): BulkMarketplaceResult => ({
  conversation_id: id, action: "relist", marketplaces: ["ebay"], ok, state: null, error: ok ? null : "failed", errors, down: [],
});

describe("bulk marketplace review", () => {
  it("sends only checked, actionable rows with the exact reviewed request", () => {
    const tasks = reviewedTasks([row("a"), row("b", { skip_reason: "Sold" }), row("c", { review_token: null }), row("d")], new Set(["d"]));
    expect(tasks).toEqual([{ id: "a", title: "Item a", state: "pending", request: {
      conversation_id: "a", action: "relist", marketplaces: ["ebay"], revision_id: "rev-a", review_token: "a".repeat(64), confirm: true,
    } }]);
  });

  it("runs delists one item at a time, never retries, and stops when asked", async () => {
    const order: string[] = [];
    const perform = vi.fn(async (body: { conversation_id: string }) => {
      order.push(body.conversation_id);
      return reply(body.conversation_id, body.conversation_id !== "a", { ebay: "eBay refused" });
    });
    let stop = false;
    const rows = [row("a", { action: "delist" }), row("b", { action: "delist" }), row("c", { action: "delist" })];
    const result = await runMarketplaceTasks(reviewedTasks(rows, new Set()), perform, () => stop,
      (_tasks, active) => { if (active.has("b")) stop = true; });
    expect(order).toEqual(["a", "b"]);
    expect(result.map((task) => task.state)).toEqual(["failed", "done", "pending"]);
    expect(result[0].error).toBe("ebay: eBay refused");
  });

  it("lists a few items at once", async () => {
    let running = 0;
    let most = 0;
    const perform = vi.fn(async (body: { conversation_id: string }) => {
      most = Math.max(most, ++running);
      await new Promise((resolve) => setTimeout(resolve, 1));
      running -= 1;
      return reply(body.conversation_id, true);
    });
    const rows = ["a", "b", "c", "d", "e"].map((id) => row(id, { action: "list" }));
    const result = await runMarketplaceTasks(reviewedTasks(rows, new Set()), perform, () => false, () => {});
    expect(most).toBe(ITEMS_AT_ONCE);
    expect(perform).toHaveBeenCalledTimes(5);
    expect(result.every((task) => task.state === "done")).toBe(true);
  });

  it("reports a lost reply as unconfirmed and an Anteroom refusal as failed", async () => {
    const perform = vi.fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new Error("The item changed since the review. Review again."));
    const result = await runMarketplaceTasks(reviewedTasks([row("a"), row("b")], new Set()), perform, () => false, () => {});
    expect(result.map((task) => task.state)).toEqual(["unconfirmed", "failed"]);
    expect(perform).toHaveBeenCalledTimes(2);
  });
});
