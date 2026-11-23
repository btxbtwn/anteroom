import type { DelistMarketplace, MarketplaceListing, MarketplaceListings } from "../api/marketplaceListings";
import { marketplaceName } from "./marketplaceNames";

const ORDER: DelistMarketplace[] = ["ebay", "etsy", "poshmark", "mercari", "depop"];
// What Delist does on each marketplace in the confirm dialog: eBay has no delete.
// The rail itself always says "Delisted" once the listing is gone.
const REMOVED: Record<DelistMarketplace, string> = {
  ebay: "Ended", etsy: "Deleted", poshmark: "Deleted", mercari: "Deleted", depop: "Deleted",
};

export function deletable(entry: MarketplaceListing | undefined): boolean {
  return Boolean(entry?.listing_id && !entry.sold_here && entry.state !== "deleted" && entry.state !== "deleting");
}

/** The marketplaces this item can be delisted from here, in rail order. */
export function delistTargets(listings: MarketplaceListings, canAct: (marketplace: string) => boolean): DelistMarketplace[] {
  return ORDER.filter((marketplace) => canAct(marketplace) && deletable(listings[marketplace]));
}

/** The delist confirm's copy. The seller names the marketplaces by approving exactly these. */
export function delistPrompt(title: string, marketplaces: DelistMarketplace[], lead?: string): { message: string; confirmLabel: string } {
  const names = marketplaces.map(marketplaceName);
  const lines = marketplaces.map((marketplace) => `• ${marketplaceName(marketplace)}: ${REMOVED[marketplace].toLowerCase()}`).join("\n");
  const question = `Delist “${title}” from ${names.length === 1 ? names[0] : `${names.length} marketplaces`}?`;
  return {
    message: `${lead ? `${lead}\n` : ""}${question}\n${lines}\nA deleted listing can't be restored; listing again creates a new one.`,
    confirmLabel: names.length === 1 ? `Delist from ${names[0]}` : `Delist from ${names.length}`,
  };
}
