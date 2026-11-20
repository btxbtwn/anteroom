import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { ApiRequestError } from "./transport";
import type { ChoiceList, ListingData, ListingDocument, MarketplaceForm } from "./types";
import type { NativeMarketplace, NativePreflight } from "./nativeMarketplaces";
import { getListingEditorValue } from "../listingPaths";
import { nativeFormStructureKey } from "../components/nativeFormStability";

export const DIRECT_FORM_MARKETS = new Set(["ebay", "poshmark", "mercari", "depop", "etsy"]);
const STALE_FORM_GAP = /reload its fields/i;

// Shared marketplace lists (Mercari's 28,000 brands) are public catalog data
// identical for every seller and keyed by content hash. Keep them in memory
// for the page's lifetime and in Cache Storage across reloads so iPad/phone
// reloads don't re-download 1.5+ MB of static options.
const CHOICE_LIST_CACHE = "anteroom-choice-lists-v1";
const CACHE_READ_TIMEOUT_MS = 500;
const choiceLists = new Map<string, Promise<ChoiceList>>();
const savedFormFingerprints = new WeakMap<object, string>();

function choiceListCacheKey(name: string, version: string): string {
  return `/api/catalog/choice-lists/${encodeURIComponent(name)}?v=${encodeURIComponent(version)}`;
}

async function openChoiceListCache(): Promise<Cache | null> {
  if (typeof caches === "undefined") return null;
  try {
    return await caches.open(CHOICE_LIST_CACHE);
  } catch {
    return null;
  }
}

async function readChoiceListFromDevice(name: string, version: string): Promise<ChoiceList | null> {
  const cache = await openChoiceListCache();
  if (!cache) return null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const response = await Promise.race([
      cache.match(choiceListCacheKey(name, version)),
      new Promise<undefined>((resolve) => { timer = setTimeout(() => resolve(undefined), CACHE_READ_TIMEOUT_MS); }),
    ]);
    if (!response) return null;
    const data = (await response.json()) as ChoiceList;
    return data && typeof data === "object" ? data : null;
  } catch {
    return null;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

async function writeChoiceListToDevice(name: string, version: string, data: ChoiceList): Promise<void> {
  const cache = await openChoiceListCache();
  if (!cache) return;
  const targetKey = choiceListCacheKey(name, version);
  const namePrefix = `/api/catalog/choice-lists/${encodeURIComponent(name)}?`;
  try {
    await cache.put(
      targetKey,
      new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } }),
    );
    const existing = await cache.keys();
    await Promise.all(
      existing
        .filter((entry) => {
          const url = entry.url;
          const idx = url.indexOf(namePrefix);
          return idx !== -1 && !url.endsWith(targetKey);
        })
        .map((entry) => cache.delete(entry.url).catch(() => false)),
    );
  } catch {
    // Quota or private-browsing refusal: in-memory map still keeps it for this page.
  }
}

async function loadChoiceList(name: string, version: string): Promise<ChoiceList> {
  const cached = await readChoiceListFromDevice(name, version);
  if (cached) return cached;
  const fresh = await api.catalog.choiceList(name, version);
  await writeChoiceListToDevice(name, version, fresh);
  return fresh;
}

function choiceList(name: string, version: string): Promise<ChoiceList> {
  const key = `${name}@${version}`;
  let list = choiceLists.get(key);
  if (!list) {
    list = loadChoiceList(name, version);
    choiceLists.set(key, list);
    list.catch(() => choiceLists.delete(key));
  }
  return list;
}

/** Reset in-memory choice-list cache between unit tests. */
export function resetChoiceListCacheForTests(): void {
  choiceLists.clear();
}

async function withChoiceLists(form: MarketplaceForm): Promise<MarketplaceForm> {
  if (!form.fields?.some((field) => field.choice_list)) return form;
  const fields = await Promise.all(form.fields.map(async ({ choice_list, ...field }) =>
    choice_list ? { ...field, ...await choiceList(choice_list.name, choice_list.version) } : field));
  return { ...form, fields };
}

function stableHash(text: string): string {
  // Two independent 32-bit FNV-1a hashes to keep query keys compact and collision-resistant.
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    h1 ^= code;
    h1 = Math.imul(h1, 0x01000193);
    h2 ^= code;
    h2 = Math.imul(h2, 0x85ebca6b);
  }
  return `${(h1 >>> 0).toString(36)}-${(h2 >>> 0).toString(36)}-${text.length}`;
}

function savedFormKey(saved: unknown): string {
  if (saved == null) return "";
  if (typeof saved !== "object") return String(saved);
  let fingerprint = savedFormFingerprints.get(saved);
  if (fingerprint === undefined) {
    fingerprint = stableHash(JSON.stringify(saved));
    savedFormFingerprints.set(saved, fingerprint);
  }
  return fingerprint;
}

export function nativeFormOptions(convId: string, tab: string, listing: ListingData, revisionId?: string | null) {
  const saved = getListingEditorValue(listing, `_native_forms.${tab}`);
  return queryOptions({
    queryKey: ["native-editor-form", convId, tab, nativeFormStructureKey(listing, tab), savedFormKey(saved)],
    queryFn: async ({ signal }) => withChoiceLists(await api.nativeMarketplaces.form(convId, tab, revisionId, { signal })),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: (failureCount, error) =>
      failureCount < 1
      && error instanceof ApiRequestError
      && error.retryable
      && (error.status === 0 || error.status === 502 || error.status === 503 || error.status === 504),
    retryDelay: 500,
  });
}

export function prefetchListingForm(client: QueryClient, convId: string, tab: string, listing: ListingData, revisionId: string) {
  if (DIRECT_FORM_MARKETS.has(tab)) return client.prefetchQuery(nativeFormOptions(convId, tab, listing, revisionId));
  return Promise.all([
    client.prefetchQuery({ queryKey: ["listing-fields", convId], queryFn: ({ signal }) => api.listings.fields(convId, { signal }), staleTime: Infinity, retry: false }),
    client.prefetchQuery({ queryKey: ["catalog-dropdown-options"], queryFn: api.catalog.dropdownOptions, staleTime: Infinity, retry: false }),
  ]);
}

/**
 * Direct marketplaces whose saved form contract needs rebuilding because a
 * category/dependent choice changed or a previous capture was unavailable.
 * Places `preferredMarket` first so the tab currently on screen updates first.
 */
export function staleFormMarketplaces(
  preflight: NativePreflight | undefined,
  markets: readonly string[],
  preferredMarket?: string,
): string[] {
  if (!preflight?.marketplaces) return [];
  const stale = markets.filter((market) => {
    if (!DIRECT_FORM_MARKETS.has(market)) return false;
    const gaps = preflight.marketplaces[market as NativeMarketplace]?.gaps ?? [];
    return gaps.some((gap) => STALE_FORM_GAP.test(gap.message));
  });
  if (preferredMarket && stale.includes(preferredMarket)) {
    return [preferredMarket, ...stale.filter((market) => market !== preferredMarket)];
  }
  return stale;
}

export interface AutoRefreshStaleFormsOptions {
  signal?: AbortSignal;
  activeMarket?: string;
  onMarketError?: (market: string, error: unknown) => void;
  onMarketStart?: (market: string | null) => void;
}

/**
 * Automatically reload stale or unavailable marketplace form definitions one by
 * one, advancing the fenced revision id between marketplaces so the seller
 * never has to click "Reload fields". Stops silently on 409 (concurrent edit)
 * or abort, and records every `(revision, market)` attempt to prevent loops.
 */
export async function autoRefreshStaleForms(
  client: QueryClient,
  convId: string,
  markets: readonly string[],
  revisionId: string,
  attempted: Set<string>,
  options?: AutoRefreshStaleFormsOptions,
): Promise<string[]> {
  const signal = options?.signal;
  if (signal?.aborted || !markets.length || !revisionId) return [];

  let currentRevision = revisionId;
  const refreshed: string[] = [];
  let conflicted = false;

  try {
    for (const market of markets) {
      if (signal?.aborted) break;
      const attemptKey = `${currentRevision}:${market}`;
      if (attempted.has(attemptKey)) continue;
      attempted.add(attemptKey);
      options?.onMarketStart?.(market);
      try {
        const result = await api.nativeMarketplaces.refreshForm(convId, market, currentRevision, { signal });
        currentRevision = result.revision_id;
        attempted.add(`${currentRevision}:${market}`);
        refreshed.push(market);
      } catch (error) {
        if (signal?.aborted) {
          attempted.delete(attemptKey);
          break;
        }
        if (error instanceof ApiRequestError && error.status === 409) {
          conflicted = true;
          // A seller edit or background write moved the listing ahead; stop
          // this batch and let the new revision's preflight run cleanly.
          break;
        }
        options?.onMarketError?.(market, error);
        // Keep moving to any other stale marketplaces; this one stays marked
        // attempted for the current revision so it won't loop.
      }
    }
  } finally {
    options?.onMarketStart?.(null);
    if (refreshed.length > 0 || conflicted) {
      await client.invalidateQueries({ queryKey: ["listing", convId] });
      const current = client.getQueryData<ListingDocument>(["listing", convId]);
      const active = options?.activeMarket;
      if (active && refreshed.includes(active) && current?.current_revision_id === currentRevision) {
        await client.fetchQuery({
          ...nativeFormOptions(convId, active, current.listing, currentRevision),
          staleTime: 0,
        }).catch(() => {});
      }
      await Promise.all(["native-preflight", "revisions"].map((key) =>
        client.invalidateQueries({ queryKey: [key, convId] })));
    }
  }
  return refreshed;
}
