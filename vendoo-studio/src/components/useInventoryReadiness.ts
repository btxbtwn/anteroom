/*
 * The fields each inventory row still needs per marketplace, read from the same
 * server check as the editor's tabs. Only the rows on screen are asked for, in
 * fixed blocks so scrolling back reuses what was read; a saved edit or a List
 * result changes the row, and so its block is read again.
 */
import { useQueries } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ListingReadiness } from "../api/nativeMarketplaces";
import type { Listing } from "./sidebar/listingShelves";

export const READINESS_BLOCK = 25;

/** The blocks of row indexes the visible range touches. */
export function readinessBlocks(first: number, last: number, size = READINESS_BLOCK): number[] {
  if (last < first || first < 0) return [];
  const blocks = [];
  for (let block = Math.floor(first / size); block <= Math.floor(last / size); block += 1) blocks.push(block);
  return blocks;
}

/** A row's identity for its readiness: a saved edit or a new List result reads it again. */
export function readinessKey(listing: Pick<Listing, "id" | "updated_at" | "direct_records">): string {
  return `${listing.id}:${listing.updated_at ?? ""}:${JSON.stringify(listing.direct_records ?? {})}`;
}

export function useInventoryReadiness(rows: readonly Listing[], first: number, last: number): Record<string, ListingReadiness> {
  const blocks = readinessBlocks(first, Math.min(last, rows.length - 1)).map((block) =>
    // A sold or settled listing is not listed again, so it needs no check.
    rows.slice(block * READINESS_BLOCK, (block + 1) * READINESS_BLOCK).filter((listing) => !listing.settled_at));
  const results = useQueries({
    queries: blocks.filter((block) => block.length).map((block) => ({
      queryKey: ["listing-readiness", block.map(readinessKey)],
      queryFn: () => api.nativeMarketplaces.readiness(block.map((listing) => listing.id)),
      staleTime: 60_000,
    })),
  });
  return Object.assign({}, ...results.map((result) => result.data?.listings ?? {}));
}
