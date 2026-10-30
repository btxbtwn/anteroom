import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { ListingData } from "./types";
import { getListingEditorValue } from "../listingPaths";
import { nativeFormStructureKey } from "../components/nativeFormStability";

export const DIRECT_FORM_MARKETS = new Set(["ebay", "poshmark", "mercari", "depop", "sellwild"]);

export function nativeFormOptions(convId: string, tab: string, listing: ListingData, revisionId?: string | null) {
  return queryOptions({
    queryKey: ["native-editor-form", convId, tab, nativeFormStructureKey(listing, tab), getListingEditorValue(listing, `_native_forms.${tab}`)],
    queryFn: () => api.nativeMarketplaces.form(convId, tab, revisionId),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

export function prefetchListingForm(client: QueryClient, convId: string, tab: string, listing: ListingData, revisionId: string) {
  if (DIRECT_FORM_MARKETS.has(tab)) return client.prefetchQuery(nativeFormOptions(convId, tab, listing, revisionId));
  return Promise.all([
    client.prefetchQuery({ queryKey: ["listing-fields", convId], queryFn: () => api.vendooApi.listingFields(convId), staleTime: Infinity, retry: false }),
    client.prefetchQuery({ queryKey: ["catalog-dropdown-options"], queryFn: api.catalog.dropdownOptions, staleTime: Infinity, retry: false }),
  ]);
}
