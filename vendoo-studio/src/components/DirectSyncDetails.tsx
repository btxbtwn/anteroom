import type { NativeSync } from "../api/nativeMarketplaces";
import { marketplaceName } from "./marketplaceNames";
import { Popover, PopoverPopup, PopoverTrigger } from "./ui/popover";

export function directSyncDiffers(result: NativeSync | undefined): boolean {
  return Object.values(result?.marketplaces ?? {}).some((entry) => entry?.inventory &&
    (entry.inventory.state !== "active" || entry.inventory.differences.length > 0));
}

export function directSyncIncomplete(result: NativeSync | undefined): boolean {
  return Object.values(result?.marketplaces ?? {}).some((entry) => Boolean(entry?.inventory?.warning));
}

function value(field: string, number: number | null): string {
  return number == null ? "Not set" : field === "price" ? `$${number.toFixed(2)}` : String(number);
}

export function DirectSyncDetails({ result }: { result: NativeSync | undefined }) {
  const rows = Object.entries(result?.marketplaces ?? {});
  if (!rows.length) return null;
  return <Popover>
    <PopoverTrigger className="pr-vendoo-sync-btn">Marketplace details</PopoverTrigger>
    <PopoverPopup width="md" className="max-w-[calc(100vw-2rem)] space-y-3 p-3 text-xs">
      <p className="font-medium">Marketplace check</p>
      <p className="text-muted-foreground">Local edits are kept. No listings were changed. An ended or inactive listing does not confirm a sale.</p>
      {rows.map(([marketplace, outcome]) => {
        const inventory = outcome?.inventory;
        return <div key={marketplace} className="space-y-1 border-t border-border pt-2">
          <p className="font-medium">{marketplaceName(marketplace)}</p>
          {!outcome?.ok ? <p role="alert">{outcome?.error || "Could not check this marketplace."}</p> : inventory ? <>
            <p>Status: {inventory.state.replace(/_/g, " ")}</p>
            <p>Listing ID: {inventory.listing_id}</p>
            {inventory.price != null ? <p>Price: {value("price", inventory.price)}</p> : null}
            {inventory.quantity != null ? <p>Available quantity: {inventory.quantity}</p> : null}
            {inventory.differences.map((difference) => <p key={difference.field}>
              {difference.field === "price" ? "Price" : "Quantity"} differs: Anteroom {value(difference.field, difference.local)} · {marketplaceName(marketplace)} {value(difference.field, difference.remote)}
            </p>)}
            {inventory.warning ? <p>{inventory.warning}</p> : null}
          </> : <p>Checked for sales.</p>}
        </div>;
      })}
    </PopoverPopup>
  </Popover>;
}
