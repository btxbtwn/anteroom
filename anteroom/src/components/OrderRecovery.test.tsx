import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OrderRecovery } from "./OrderRecovery";
import { api } from "../api/client";
import type { OrderRecoveryResult } from "../api/orderRecovery";

const { useMutation } = vi.hoisted(() => ({ useMutation: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useMutation, useQueryClient: () => ({ invalidateQueries: vi.fn() }) }));
const result: OrderRecoveryResult = { marketplace: "etsy", connection: "a".repeat(64), through: "2026-10-26T00:00:00+00:00", page: 1, nextPage: 2,
  ordersScanned: 25, skippedOrders: 1, recorded: 3, updated: 2, unchanged: 19, conflicts: 0, matched: 0, truncated: false };
beforeEach(() => { useMutation.mockClear(); useMutation.mockReturnValue({ isPending: false, mutate: vi.fn(), reset: vi.fn() }); });
function mutation() { return useMutation.mock.calls[useMutation.mock.calls.length - 1][0]; }

describe("direct order recovery", () => {
  it("makes recovery explicit and describes modified orders and local-only behavior", () => {
    const html = renderToStaticMarkup(<OrderRecovery marketplace="etsy" connectedAt={1} />);
    expect(html).toContain("Recover Etsy orders");
    expect(html).toContain("orders changed during these dates");
    expect(html).toContain("Recovery never sends notifications, relists, or delists");
    expect(html).toContain("Order changes from (UTC)");
  });
  it("does not start without a current connection", () => {
    const html = renderToStaticMarkup(<OrderRecovery marketplace="ebay" connectedAt={null} />);
    expect(html).toContain("Reconnect this marketplace");
    expect(html).toMatch(/type="submit"[^>]*disabled/);
  });
  it("recovers pages sequentially with the same connection token", async () => {
    const recover = vi.spyOn(api.marketplaceSales, "recover").mockResolvedValueOnce(result).mockResolvedValueOnce({ ...result, page: 2, nextPage: null });
    renderToStaticMarkup(<OrderRecovery marketplace="etsy" connectedAt={1} />);
    await mutation().mutationFn(true);
    expect(recover).toHaveBeenCalledTimes(2);
    expect(recover.mock.calls[0][0]).toMatchObject({ marketplace: "etsy", page: 1, connected_at: "1" });
    expect(recover.mock.calls[1][0]).toMatchObject({ marketplace: "etsy", page: 2, connection: result.connection, through: result.through, connected_at: "1" });
    recover.mockRestore();
  });
  it("keeps the failed page for an explicit retry without replaying successful pages", async () => {
    const recover = vi.spyOn(api.marketplaceSales, "recover").mockResolvedValueOnce(result).mockRejectedValueOnce(new Error("Connection lost")).mockResolvedValueOnce({ ...result, page: 2, nextPage: null });
    renderToStaticMarkup(<OrderRecovery marketplace="etsy" connectedAt={1} />);
    const run = mutation();
    await expect(run.mutationFn(true)).rejects.toThrow("Connection lost");
    await run.mutationFn(false);
    expect(recover.mock.calls.map(([request]) => request.page)).toEqual([1, 2, 2]);
    expect(run.retry).toBe(false);
    recover.mockRestore();
  });
});
