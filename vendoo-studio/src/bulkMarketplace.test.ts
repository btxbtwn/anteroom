import { describe, expect, it, vi } from "vitest";
import type { BulkMarketplaceResult, BulkMarketplaceRow } from "./api/bulkMarketplace";
import { reviewedTasks, runMarketplaceTasks } from "./bulkMarketplace";

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

  it("runs one item at a time, never retries, and stops when asked", async () => {
    const order: string[] = [];
    const perform = vi.fn(async (body: { conversation_id: string }) => {
      order.push(body.conversation_id);
      return reply(body.conversation_id, body.conversation_id !== "a", { ebay: "eBay refused" });
    });
    let stop = false;
    const result = await runMarketplaceTasks(reviewedTasks([row("a"), row("b"), row("c")], new Set()), perform, () => stop,
      (_tasks, active) => { if (active === "b") stop = true; });
    expect(order).toEqual(["a", "b"]);
    expect(result.map((task) => task.state)).toEqual(["failed", "done", "pending"]);
    expect(result[0].error).toBe("ebay: eBay refused");
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
