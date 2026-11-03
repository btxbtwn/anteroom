import { QueryObserver } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./client";
import { createStudioQueryClient } from "./queryClient";
import { nativeFormOptions, prefetchListingForm, prefetchVintedChoices, vintedCategoriesOptions } from "./listingForms";

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
    expect(list).toHaveBeenCalledExactlyOnceWith("mercari-brands", "v1");
  } finally { client.clear(); }
});

it("reads Vinted's category tree once for every listing and warms it before the tab opens", async () => {
  const client = createStudioQueryClient();
  const read = vi.spyOn(api.nativeMarketplaces, "vintedCategories").mockResolvedValue({ options: ["Women > Jeans"] });
  try {
    await prefetchVintedChoices(client, "one");
    await client.fetchQuery(vintedCategoriesOptions("two"));
    expect(client.getQueryData(vintedCategoriesOptions("three").queryKey)).toEqual({ options: ["Women > Jeans"] });
    expect(read).toHaveBeenCalledExactlyOnceWith("one");
  } finally { client.clear(); }
});
