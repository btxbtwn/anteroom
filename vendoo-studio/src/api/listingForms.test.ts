import { QueryObserver } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./client";
import { createStudioQueryClient } from "./queryClient";
import { nativeFormOptions, prefetchListingForm } from "./listingForms";

afterEach(() => vi.restoreAllMocks());

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
    expect(read).toHaveBeenCalledExactlyOnceWith("item", "mercari", "r1");
    unsubscribe();
    await prefetchListingForm(client, "item", "mercari", { direct_category_ids: { mercari: "2009" } }, "r3");
    expect(read).toHaveBeenCalledTimes(2);
    await prefetchListingForm(client, "item", "mercari", { ...listing, mercari_specifics: { shippingMethod: "SOYO" } }, "r4");
    expect(read).toHaveBeenCalledTimes(3);
  } finally { client.clear(); }
});
