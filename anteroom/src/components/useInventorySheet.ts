/*
 * Cached marketplace cells and readiness for the inventory's marketplace tabs.
 * Reads fixed blocks of visible rows (matching useInventoryReadiness) and never
 * triggers live marketplace API calls.
 */
import { useQueries } from "@tanstack/react-query";
import { api } from "../api/client";
import type { NativeMarketplace } from "../api/nativeMarketplaces";
import type { ListingSheetRow } from "../api/types";
import type { Listing } from "./sidebar/listingShelves";
import { READINESS_BLOCK, readinessBlocks, readinessKey } from "./useInventoryReadiness";

export function useInventorySheet(
  marketplace: NativeMarketplace | null,
  rows: readonly Listing[],
  first: number,
  last: number,
): { rows: Record<string, ListingSheetRow>; isLoading: boolean } {
  const blocks = marketplace
    ? readinessBlocks(first, Math.min(last, rows.length - 1)).map((block) =>
      rows.slice(block * READINESS_BLOCK, (block + 1) * READINESS_BLOCK))
    : [];
  const results = useQueries({
    queries: marketplace
      ? blocks.filter((block) => block.length).map((block) => ({
        queryKey: ["listing-sheet", marketplace, block.map(readinessKey)],
        queryFn: () => api.nativeMarketplaces.sheet(marketplace, block.map((listing) => listing.id)),
        staleTime: 60_000,
      }))
      : [],
  });
  const merged = Object.assign({}, ...results.map((result) => result.data?.listings ?? {})) as Record<string, ListingSheetRow>;
  const isLoading = results.some((result) => result.isLoading);
  return { rows: merged, isLoading };
}
