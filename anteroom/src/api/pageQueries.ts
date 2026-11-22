import type { QueryClient, QueryKey } from "@tanstack/react-query";
import { api } from "./client";
import type { DirectConnectionsStatus } from "./directConnections";
import { browserTimeZone } from "./home";
import { MAX_BACKGROUND_CONCURRENT, runInBackgroundContext } from "./requestScheduler";

/**
 * The first reads of each sidebar page. Pages read through these definitions
 * and `prefetchPageReads` warms them after the app opens, so opening a page
 * shows its data at once and refreshes it in the background.
 *
 * Warmed reads are kept for half an hour without a screen watching them;
 * the default five minutes would drop them before the seller gets there.
 *
 * These are plain objects rather than `queryOptions(...)`, so component tests
 * that replace React Query with a mock can still import them.
 */
const KEEP_MS = 30 * 60_000;
const FRESH_PREFETCH_MS = 30_000;

const DAY_MS = 86_400_000;

/** The last 30 days, as the eBay dashboard and the sales records open on. */
export function lastThirtyDays(now = Date.now()) {
  return {
    start: new Date(now - 29 * DAY_MS).toISOString().slice(0, 10),
    end: new Date(now).toISOString().slice(0, 10),
  };
}

export function homeQuery(timezone = browserTimeZone()) {
  return {
    queryKey: ["home", timezone] as const,
    queryFn: () => api.home.summary(timezone),
    gcTime: KEEP_MS,
  };
}

export const sourcingQuery = { queryKey: ["sourcing"] as const, queryFn: api.sourcing.get, gcTime: KEEP_MS };
export const boxesQuery = { queryKey: ["boxes"] as const, queryFn: api.boxes.list, gcTime: KEEP_MS };

export const automationsQuery = { queryKey: ["automations"] as const, queryFn: () => api.automations.list(), gcTime: KEEP_MS };
export const automationApprovalsQuery = { queryKey: ["automation-approvals"] as const, queryFn: () => api.automations.approvals(), gcTime: KEEP_MS };
export const automationGapsQuery = { queryKey: ["automation-gaps"] as const, queryFn: () => api.errors.list(), gcTime: KEEP_MS };
export const ebayOffersQuery = { queryKey: ["ebay-offers"] as const, queryFn: api.ebayOffers.status, gcTime: KEEP_MS };
export const promotionAutomationsQuery = { queryKey: ["promotion-automations"] as const, queryFn: api.promotionAutomations.list, gcTime: KEEP_MS };

export const directConnectionsQuery = { queryKey: ["direct-connections"] as const, queryFn: api.directConnections.status, gcTime: KEEP_MS };

export function ebayFinancesQuery(start: string, end: string) {
  return {
    queryKey: ["ebay", "finances", { start, end }, 0] as const,
    queryFn: () => api.ebay.read("finances", { start, end, offset: 0 }),
    retry: false, staleTime: 30_000, gcTime: KEEP_MS,
  };
}

export type SalesLedgerFilters = { start: string; end: string; q: string; attention: string; sort: string; marketplace?: string };

export function salesLedgerQuery(filters: SalesLedgerFilters, page: number) {
  return {
    queryKey: ["sales-ledger", filters, page] as const,
    queryFn: () => api.salesLedger.list(filters, page),
    retry: false, gcTime: KEEP_MS,
  };
}

export function salesOverviewQuery(start: string | null, end: string, marketplace: string) {
  return {
    queryKey: ["analytics", "overview", start, end, marketplace] as const,
    queryFn: () => api.analytics.overview({ start, end, ...(marketplace !== "all" ? { marketplace } : {}) }),
    gcTime: KEEP_MS,
  };
}

export const settingsUiQuery = { queryKey: ["settings-ui"] as const, queryFn: api.settings.ui, gcTime: KEEP_MS };

type Runtime = { views: ReadonlySet<string>; features: ReadonlySet<string> };

function isRecentlyWarm(client: QueryClient, queryKey: QueryKey, now: number): boolean {
  const state = client.getQueryState(queryKey);
  return Boolean(state && state.status === "success" && !state.isInvalidated && now - state.dataUpdatedAt < FRESH_PREFETCH_MS);
}

/**
 * Reads, never writes, for the pages this app shows. Runs on the background
 * lane with bounded concurrency so startup warm-ups never crowd out clicks or
 * exhaust the hosted DB pool.
 */
export async function prefetchPageReads(client: QueryClient, runtime: Runtime, now = Date.now()) {
  const { start, end } = lastThirtyDays(now);
  const prefetch = <T,>(query: { queryKey: QueryKey; queryFn: () => Promise<T>; gcTime?: number; retry?: boolean; staleTime?: number }) => {
    if (isRecentlyWarm(client, query.queryKey, now)) return Promise.resolve();
    return runInBackgroundContext(() => client.prefetchQuery(query));
  };
  const tasks: (() => Promise<unknown>)[] = [() => prefetch(settingsUiQuery)];
  if (runtime.views.has("home")) {
    tasks.push(() => prefetch(homeQuery()));
  }
  if (runtime.views.has("analytics")) {
    tasks.push(() => prefetch(salesLedgerQuery({ start, end, q: "", attention: "all", sort: "newest" }, 1)));
    tasks.push(() => prefetch(salesOverviewQuery(start, end, "all")));
  }
  if (runtime.views.has("sourcing")) {
    tasks.push(() => prefetch(sourcingQuery), () => prefetch(boxesQuery));
  }
  if (runtime.views.has("automations")) {
    tasks.push(
      () => prefetch(automationsQuery),
      () => prefetch(automationApprovalsQuery),
      () => prefetch(automationGapsQuery),
    );
    if (runtime.features.has("ebay_offers")) tasks.push(() => prefetch(ebayOffersQuery));
    if (runtime.features.has("marketplace_sessions")) tasks.push(() => prefetch(promotionAutomationsQuery));
  }
  if (runtime.views.has("marketplaces")) {
    tasks.push(async () => {
      await prefetch(directConnectionsQuery);
      const connections = client.getQueryData<DirectConnectionsStatus>(directConnectionsQuery.queryKey);
      if (connections?.accounts.ebay?.connected) await prefetch(ebayFinancesQuery(start, end));
    });
  }
  let cursor = 0;
  const worker = async () => {
    while (cursor < tasks.length) {
      const task = tasks[cursor++]!;
      await task().catch(() => {});
    }
  };
  await Promise.all(Array.from({ length: Math.min(MAX_BACKGROUND_CONCURRENT, tasks.length) }, worker));
}
