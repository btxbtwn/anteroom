import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { api } from "./client";

/**
 * The first reads of each sidebar page. Pages read through these definitions
 * and `prefetchPageReads` warms them after the app opens, so opening a page
 * shows its data at once and refreshes it in the background.
 *
 * Warmed reads are kept for half an hour without a screen watching them;
 * the default five minutes would drop them before the seller gets there.
 */
const KEEP_MS = 30 * 60_000;

const DAY_MS = 86_400_000;

/** The last 30 days, as the eBay dashboard and the sales records open on. */
export function lastThirtyDays(now = Date.now()) {
  return {
    start: new Date(now - 29 * DAY_MS).toISOString().slice(0, 10),
    end: new Date(now).toISOString().slice(0, 10),
  };
}

export const sourcingQuery = queryOptions({ queryKey: ["sourcing"], queryFn: api.sourcing.get, gcTime: KEEP_MS });
export const boxesQuery = queryOptions({ queryKey: ["boxes"], queryFn: api.boxes.list, gcTime: KEEP_MS });

export const automationsQuery = queryOptions({ queryKey: ["automations"], queryFn: () => api.automations.list(), gcTime: KEEP_MS });
export const automationApprovalsQuery = queryOptions({ queryKey: ["automation-approvals"], queryFn: () => api.automations.approvals(), gcTime: KEEP_MS });
export const automationGapsQuery = queryOptions({ queryKey: ["automation-gaps"], queryFn: () => api.errors.list(), gcTime: KEEP_MS });
export const ebayOffersQuery = queryOptions({ queryKey: ["ebay-offers"], queryFn: api.ebayOffers.status, gcTime: KEEP_MS });
export const promotionAutomationsQuery = queryOptions({ queryKey: ["promotion-automations"], queryFn: api.promotionAutomations.list, gcTime: KEEP_MS });

export const directConnectionsQuery = queryOptions({ queryKey: ["direct-connections"], queryFn: api.directConnections.status, gcTime: KEEP_MS });

export function ebayFinancesQuery(start: string, end: string) {
  return queryOptions({
    queryKey: ["ebay", "finances", { start, end }, 0],
    queryFn: () => api.ebay.read("finances", { start, end, offset: 0 }),
    retry: false, staleTime: 30_000, gcTime: KEEP_MS,
  });
}

export type SalesLedgerFilters = { start: string; end: string; q: string; attention: string; sort: string; marketplace?: string };

export function salesLedgerQuery(filters: SalesLedgerFilters, page: number) {
  return queryOptions({
    queryKey: ["sales-ledger", filters, page],
    queryFn: () => api.salesLedger.list(filters, page),
    retry: false, gcTime: KEEP_MS,
  });
}

export function salesOverviewQuery(start: string | null, end: string, marketplace: string) {
  return queryOptions({
    queryKey: ["analytics", "overview", start, end, marketplace],
    queryFn: () => api.analytics.overview({ start, end, ...(marketplace !== "all" ? { marketplace } : {}) }),
    gcTime: KEEP_MS,
  });
}

export const settingsUiQuery = queryOptions({ queryKey: ["settings-ui"], queryFn: api.settings.ui, gcTime: KEEP_MS });

type Runtime = { views: ReadonlySet<string>; features: ReadonlySet<string> };

/**
 * Reads, never writes, for the pages this app shows. Each one asks the server
 * once; a page already on screen refreshes its own data as before.
 */
export async function prefetchPageReads(client: QueryClient, runtime: Runtime, now = Date.now()) {
  const { start, end } = lastThirtyDays(now);
  const reads: Promise<unknown>[] = [client.prefetchQuery(settingsUiQuery)];
  if (runtime.views.has("analytics")) {
    reads.push(client.prefetchQuery(salesLedgerQuery({ start, end, q: "", attention: "all", sort: "newest" }, 1)));
    reads.push(client.prefetchQuery(salesOverviewQuery(start, end, "all")));
  }
  if (runtime.views.has("sourcing")) reads.push(client.prefetchQuery(sourcingQuery), client.prefetchQuery(boxesQuery));
  if (runtime.views.has("automations")) {
    reads.push(client.prefetchQuery(automationsQuery), client.prefetchQuery(automationApprovalsQuery), client.prefetchQuery(automationGapsQuery));
    if (runtime.features.has("ebay_offers")) reads.push(client.prefetchQuery(ebayOffersQuery));
    if (runtime.features.has("marketplace_sessions")) reads.push(client.prefetchQuery(promotionAutomationsQuery));
  }
  if (runtime.views.has("marketplaces")) {
    reads.push(client.prefetchQuery(directConnectionsQuery).then(() => {
      const connections = client.getQueryData(directConnectionsQuery.queryKey);
      if (connections?.accounts.ebay?.connected) return client.prefetchQuery(ebayFinancesQuery(start, end));
    }));
  }
  await Promise.all(reads);
}
