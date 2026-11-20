import { QueryObserver } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./client";
import { ApiRequestError } from "./transport";
import { createStudioQueryClient } from "./queryClient";
import { autoRefreshStaleForms, nativeFormOptions, prefetchListingForm, resetChoiceListCacheForTests, staleFormMarketplaces } from "./listingForms";

afterEach(() => {
  resetChoiceListCacheForTests();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function fakeCacheStorage() {
  const entries = new Map<string, string>();
  const cache = {
    match: vi.fn(async (key: string) => {
      const body = entries.get(key);
      return body === undefined ? undefined : new Response(body, { headers: { "Content-Type": "application/json" } });
    }),
    put: vi.fn(async (key: string, response: Response) => {
      entries.set(key, await response.text());
    }),
    delete: vi.fn(async (key: string) => entries.delete(key)),
    keys: vi.fn(async () => Array.from(entries.keys(), (url) => ({ url }))),
  };
  return {
    entries,
    cache,
    storage: { open: vi.fn(async () => cache) },
  };
}

it("opens warmed forms synchronously and fetches again only when their structure changes", async () => {
  const client = createStudioQueryClient();
  const read = vi.spyOn(api.nativeMarketplaces, "form").mockResolvedValue({ marketplace: "mercari", fields: [] } as never);
  const listing = { direct_category_ids: { mercari: "2008" } };
  try {
    await prefetchListingForm(client, "item", "mercari", listing, "r1");
    const observer = new QueryObserver(client, nativeFormOptions("item", "mercari", { ...listing, price: 30 }, "r2"));
    const unsubscribe = observer.subscribe(() => {});
    expect(observer.getCurrentResult().data?.marketplace).toBe("mercari");
    expect(observer.getCurrentResult().isFetching).toBe(false);
    expect(read).toHaveBeenCalledTimes(1);
    expect(read.mock.calls[0]?.slice(0, 3)).toEqual(["item", "mercari", "r1"]);
    unsubscribe();
    await prefetchListingForm(client, "item", "mercari", { direct_category_ids: { mercari: "2009" } }, "r3");
    expect(read).toHaveBeenCalledTimes(2);
    await prefetchListingForm(client, "item", "mercari", { ...listing, mercari_specifics: { shippingMethod: "SOYO" } }, "r4");
    expect(read).toHaveBeenCalledTimes(3);
  } finally { client.clear(); }
});

it("downloads a marketplace's shared choice list once for every listing's form", async () => {
  const client = createStudioQueryClient();
  const ref = { name: "mercari-brands", version: "v1" };
  vi.spyOn(api.nativeMarketplaces, "form").mockImplementation(async (convId) => ({
    marketplace: "mercari",
    fields: [{ key: "mercari_specifics.brand", path: "mercari_specifics.brand", label: "Brand", value: convId, choice_list: ref }],
  }) as never);
  const list = vi.spyOn(api.catalog, "choiceList").mockResolvedValue({ options: ["Nike", "Adidas"], option_values: { Nike: "Nike" } });
  try {
    for (const id of ["one", "two"]) {
      await prefetchListingForm(client, id, "mercari", {}, "r1");
      const form = client.getQueryData(nativeFormOptions(id, "mercari", {}, "r1").queryKey);
      expect(form?.fields[0]).toMatchObject({ value: id, options: ["Nike", "Adidas"], option_values: { Nike: "Nike" } });
      expect(form?.fields[0]).not.toHaveProperty("choice_list");
    }
    expect(list).toHaveBeenCalledTimes(1);
    expect(list.mock.calls[0]?.slice(0, 2)).toEqual(["mercari-brands", "v1"]);
  } finally { client.clear(); }
});

it("persists versioned choice lists in Cache Storage across reloads and prunes old versions", async () => {
  const { entries, storage } = fakeCacheStorage();
  vi.stubGlobal("caches", storage);
  const client = createStudioQueryClient();
  let version = "v1";
  vi.spyOn(api.nativeMarketplaces, "form").mockImplementation(async () => ({
    marketplace: "mercari",
    fields: [{ key: "mercari_specifics.brand", path: "mercari_specifics.brand", label: "Brand", value: "",
      choice_list: { name: "mercari-brands", version } }],
  }) as never);
  const list = vi.spyOn(api.catalog, "choiceList").mockImplementation(async (_name, v) => ({
    options: [v === "v1" ? "Nike" : "Adidas"],
    option_values: {},
  }));
  try {
    await prefetchListingForm(client, "one", "mercari", {}, "r1");
    expect(list).toHaveBeenCalledTimes(1);
    expect(entries.size).toBe(1);

    // Simulate a page reload: clear in-memory caches while keeping Cache Storage.
    resetChoiceListCacheForTests();
    client.clear();

    await prefetchListingForm(client, "two", "mercari", {}, "r1");
    expect(list).toHaveBeenCalledTimes(1);
    const cached = client.getQueryData(nativeFormOptions("two", "mercari", {}, "r1").queryKey);
    expect(cached?.fields[0]?.options).toEqual(["Nike"]);

    // New choice-list version fetches once and evicts the superseded version.
    version = "v2";
    resetChoiceListCacheForTests();
    client.clear();
    await prefetchListingForm(client, "three", "mercari", {}, "r1");
    expect(list).toHaveBeenCalledTimes(2);
    expect(entries.size).toBe(1);
    expect(Array.from(entries.keys())[0]).toContain("v2");
  } finally { client.clear(); }
});

it("keeps nativeFormOptions query keys compact while tracking _native_forms changes", () => {
  const bigForm = { version: 1, fields: Array.from({ length: 100 }, (_, i) => ({ path: `f${i}`, options: ["a", "b", "c"] })) };
  const changedForm = { version: 1, fields: [...bigForm.fields, { path: "extra", options: ["z"] }] };
  const first = nativeFormOptions("item", "ebay", { _native_forms: { ebay: bigForm } }, "r1");
  const same = nativeFormOptions("item", "ebay", { _native_forms: { ebay: bigForm } }, "r2");
  const next = nativeFormOptions("item", "ebay", { _native_forms: { ebay: changedForm } }, "r2");

  expect(first.queryKey).toEqual(same.queryKey);
  expect(first.queryKey).not.toEqual(next.queryKey);
  expect(JSON.stringify(first.queryKey).length).toBeLessThan(200);
});

it("identifies stale or unavailable marketplace forms and prioritizes the active tab", () => {
  const preflight = {
    revision_id: "r1",
    marketplaces: {
      ebay: { ready: false, missing: [], gaps: [{ field: "ebay_specifics", message: "The marketplace form could not be loaded. Reload its fields before listing.", blocking: true }] },
      poshmark: { ready: true, missing: [], gaps: [] },
      mercari: { ready: false, missing: [], gaps: [{ field: "direct_category_ids.mercari", message: "The form's category or dependent choices changed. Reload its fields before listing.", blocking: true }] },
    },
  };
  expect(staleFormMarketplaces(preflight, ["ebay", "poshmark", "mercari", "depop"], "mercari")).toEqual(["mercari", "ebay"]);
  expect(staleFormMarketplaces(preflight, ["poshmark"])).toEqual([]);
});

it("auto-reloads stale marketplace forms sequentially, chaining revisions and preventing loops", async () => {
  const client = createStudioQueryClient();
  const attempted = new Set<string>();
  const calls: Array<[string, string]> = [];
  const refresh = vi.spyOn(api.nativeMarketplaces, "refreshForm").mockImplementation(async (_id, market, rev) => {
    calls.push([market, rev]);
    if (market === "ebay") throw new ApiRequestError("eBay disconnected", 502, false);
    return { revision_id: market === "mercari" ? "r2" : "r3" };
  });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  try {
    const refreshed = await autoRefreshStaleForms(client, "item", ["mercari", "ebay", "poshmark"], "r1", attempted);
    expect(calls).toEqual([["mercari", "r1"], ["ebay", "r2"], ["poshmark", "r2"]]);
    expect(refreshed).toEqual(["mercari", "poshmark"]);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["listing", "item"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["native-preflight", "item"] });

    // Re-running on the same failed revision for eBay must not loop.
    const again = await autoRefreshStaleForms(client, "item", ["ebay"], "r2", attempted);
    expect(again).toEqual([]);
    expect(refresh).toHaveBeenCalledTimes(3);
  } finally { client.clear(); }
});

it("stops auto-reloading silently on 409 revision conflict so seller edits are never raced", async () => {
  const client = createStudioQueryClient();
  const attempted = new Set<string>();
  const calls: string[] = [];
  vi.spyOn(api.nativeMarketplaces, "refreshForm").mockImplementation(async (_id, market) => {
    calls.push(market);
    throw new ApiRequestError("The listing changed while fields loaded.", 409, false);
  });
  try {
    const refreshed = await autoRefreshStaleForms(client, "item", ["mercari", "poshmark"], "r1", attempted);
    expect(refreshed).toEqual([]);
    expect(calls).toEqual(["mercari"]);
  } finally { client.clear(); }
});

it("retries transient Load failed errors once when reading a native form but not explained 502s", () => {
  const opts = nativeFormOptions("item", "ebay", {}, "r1");
  const retry = opts.retry as (count: number, err: unknown) => boolean;
  expect(retry(0, new ApiRequestError("Load failed", 0, true))).toBe(true);
  expect(retry(1, new ApiRequestError("Load failed", 0, true))).toBe(false);
  expect(retry(0, new ApiRequestError("eBay disconnected", 502, true))).toBe(false);
});

