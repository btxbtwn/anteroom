/** The price a listing has on one marketplace, as the inventory table shows it. */
import { formatMoney } from "./analyticsFormat";
import { marketplaceCell, type TableListing } from "./inventoryTable";

export interface MarketplacePrice {
  /** The live listing's price there, or null when Anteroom does not know it. */
  amount: number | null;
  /** The amount, or a dash when unknown. */
  label: string;
  /** Known, and not the item's own price: the seller set a marketplace price, or the marketplace changed it. */
  differs: boolean;
}

const LIVE = new Set(["published", "pending_review"]);

function cents(value: number): number {
  return Math.round(value * 100);
}

/**
 * Only a listed cell has a price: what the last confirmed List or Update sent,
 * or what a verified read found. A listing linked by hand or imported from
 * elsewhere has no known price, and the table says so rather than guessing.
 */
export function marketplacePrice(listing: TableListing & { price?: number | null }, marketplace: string): MarketplacePrice | null {
  if (marketplaceCell(listing, marketplace).kind !== "listed") return null;
  const record = listing.direct_records?.[marketplace];
  const amount = record && LIVE.has(record.state) && typeof record.price === "number" ? record.price : null;
  if (amount == null) return { amount: null, label: "—", differs: false };
  const base = listing.price;
  return { amount, label: formatMoney(amount), differs: typeof base === "number" && cents(base) !== cents(amount) };
}

/** What a screen reader and the tooltip say about the price. */
export function marketplacePriceDescription(price: MarketplacePrice, marketplaceName: string, basePrice: number | null | undefined): string {
  if (price.amount == null) return `${marketplaceName} price not known`;
  if (price.differs && typeof basePrice === "number") {
    return `${price.label} on ${marketplaceName}, item price ${formatMoney(basePrice)}`;
  }
  return `${price.label} on ${marketplaceName}`;
}
