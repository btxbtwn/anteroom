import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { ChoiceList, ListingData, MarketplaceForm } from "./types";
import { getListingEditorValue } from "../listingPaths";
import { nativeFormStructureKey } from "../components/nativeFormStability";

export const DIRECT_FORM_MARKETS = new Set(["ebay", "poshmark", "mercari", "depop"]);

// Shared marketplace lists (Mercari's 28,000 brands) are the same for every listing.
// Keep each version for the page's lifetime; a failed download is tried again next time.
const choiceLists = new Map<string, Promise<ChoiceList>>();

function choiceList(name: string, version: string): Promise<ChoiceList> {
  const key = `${name}@${version}`;
  let list = choiceLists.get(key);
  if (!list) {
    list = api.catalog.choiceList(name, version);
    choiceLists.set(key, list);
    list.catch(() => choiceLists.delete(key));
  }
  return list;
}

async function withChoiceLists(form: MarketplaceForm): Promise<MarketplaceForm> {
  if (!form.fields?.some((field) => field.choice_list)) return form;
  const fields = await Promise.all(form.fields.map(async ({ choice_list, ...field }) =>
    choice_list ? { ...field, ...await choiceList(choice_list.name, choice_list.version) } : field));
  return { ...form, fields };
}

export function nativeFormOptions(convId: string, tab: string, listing: ListingData, revisionId?: string | null) {
  return queryOptions({
    queryKey: ["native-editor-form", convId, tab, nativeFormStructureKey(listing, tab), getListingEditorValue(listing, `_native_forms.${tab}`)],
    queryFn: async () => withChoiceLists(await api.nativeMarketplaces.form(convId, tab, revisionId)),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

export function prefetchListingForm(client: QueryClient, convId: string, tab: string, listing: ListingData, revisionId: string) {
  if (DIRECT_FORM_MARKETS.has(tab)) return client.prefetchQuery(nativeFormOptions(convId, tab, listing, revisionId));
  return Promise.all([
    client.prefetchQuery({ queryKey: ["listing-fields", convId], queryFn: () => api.listings.fields(convId), staleTime: Infinity, retry: false }),
    client.prefetchQuery({ queryKey: ["catalog-dropdown-options"], queryFn: api.catalog.dropdownOptions, staleTime: Infinity, retry: false }),
  ]);
}
