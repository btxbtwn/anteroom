import { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { api } = vi.hoisted(() => {
  const read = (name: string) => vi.fn(async () => ({ read: name }));
  return { api: {
    settings: { ui: read("settings-ui") },
    salesLedger: { list: read("sales-ledger") },
    analytics: { overview: read("overview") },
    sourcing: { get: read("sourcing") },
    boxes: { list: read("boxes") },
    automations: { list: read("automations"), approvals: read("approvals") },
    errors: { list: read("gaps") },
    ebayOffers: { status: read("ebay-offers") },
    promotionAutomations: { list: read("promotions") },
    directConnections: { status: vi.fn(async () => ({ accounts: { ebay: { connected: true } } })) },
    ebay: { read: read("ebay") },
  } };
});
vi.mock("./client", () => ({ api }));

import {
  automationsQuery, boxesQuery, directConnectionsQuery, ebayFinancesQuery, lastThirtyDays, prefetchPageReads,
  salesLedgerQuery, salesOverviewQuery, settingsUiQuery, sourcingQuery,
} from "./pageQueries";

const NOW = Date.parse("2026-11-08T14:24:00Z");
const allViews = { views: new Set(["analytics", "sourcing", "automations", "marketplaces"]), features: new Set(["ebay_offers", "marketplace_sessions"]) };

beforeEach(() => vi.clearAllMocks());

describe("page prefetch", () => {
  it("opens on the same 30 days the eBay dashboard and sales records show", () => {
    expect(lastThirtyDays(NOW)).toEqual({ start: "2026-10-10", end: "2026-11-08" });
  });

  it("answers each page's first reads before the page opens", async () => {
    const client = new QueryClient();
    await prefetchPageReads(client, allViews, NOW);
    const { start, end } = lastThirtyDays(NOW);
    for (const key of [
      settingsUiQuery.queryKey, sourcingQuery.queryKey, boxesQuery.queryKey, automationsQuery.queryKey,
      directConnectionsQuery.queryKey, ebayFinancesQuery(start, end).queryKey,
      salesLedgerQuery({ start, end, q: "", attention: "all", sort: "newest" }, 1).queryKey,
      salesOverviewQuery(start, end, "all").queryKey,
      ["ebay-offers"], ["promotion-automations"], ["automation-approvals"], ["automation-gaps"],
    ]) expect(client.getQueryData(key), JSON.stringify(key)).toBeDefined();
    expect(api.ebay.read).toHaveBeenCalledWith("finances", { start, end, offset: 0 });
  });

  it("skips pages and features this app does not show", async () => {
    const client = new QueryClient();
    await prefetchPageReads(client, { views: new Set(["sourcing"]), features: new Set() }, NOW);
    expect(api.sourcing.get).toHaveBeenCalledTimes(1);
    expect(api.settings.ui).toHaveBeenCalledTimes(1);
    for (const read of [api.automations.list, api.ebayOffers.status, api.promotionAutomations.list, api.salesLedger.list, api.directConnections.status, api.ebay.read]) {
      expect(read).not.toHaveBeenCalled();
    }
  });

  it("reads eBay earnings only for a connected eBay account", async () => {
    api.directConnections.status.mockResolvedValueOnce({ accounts: { ebay: { connected: false } } });
    await prefetchPageReads(new QueryClient(), allViews, NOW);
    expect(api.ebay.read).not.toHaveBeenCalled();
  });

  it("keeps warmed reads after the default five minutes", async () => {
    vi.useFakeTimers();
    const client = new QueryClient();
    await prefetchPageReads(client, allViews, NOW);
    vi.advanceTimersByTime(10 * 60_000);
    expect(client.getQueryData(sourcingQuery.queryKey)).toBeDefined();
    vi.useRealTimers();
  });

  it("caps concurrent prefetch reads to at most two in flight and skips recently warmed queries", async () => {
    let inFlight = 0;
    let peak = 0;
    const track = <T,>(fn: ReturnType<typeof vi.fn>, value: T) => {
      fn.mockImplementation(async () => {
        inFlight += 1;
        peak = Math.max(peak, inFlight);
        await new Promise((resolve) => setTimeout(resolve, 10));
        inFlight -= 1;
        return value;
      });
    };
    track(api.settings.ui, { read: "settings-ui" });
    track(api.salesLedger.list, { read: "sales-ledger" });
    track(api.analytics.overview, { read: "overview" });
    track(api.sourcing.get, { read: "sourcing" });
    track(api.boxes.list, { read: "boxes" });
    track(api.automations.list, { read: "automations" });
    track(api.automations.approvals, { read: "approvals" });
    track(api.errors.list, { read: "gaps" });
    track(api.ebayOffers.status, { read: "ebay-offers" });
    track(api.promotionAutomations.list, { read: "promotions" });
    track(api.directConnections.status, { accounts: { ebay: { connected: true } } });
    track(api.ebay.read, { read: "ebay" });

    const client = new QueryClient();
    const now = Date.now();
    await prefetchPageReads(client, allViews, now);
    expect(peak).toBeLessThanOrEqual(2);
    expect(api.sourcing.get).toHaveBeenCalledTimes(1);

    // A second immediate pass skips already-warm queries.
    await prefetchPageReads(client, allViews, now + 1000);
    expect(api.sourcing.get).toHaveBeenCalledTimes(1);
    expect(api.boxes.list).toHaveBeenCalledTimes(1);
  });
});
