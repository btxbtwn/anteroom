import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { DelistMarketplace, MarketplaceListing } from "../api/marketplaceListings";
import { confirmDialog } from "../ui/confirmDialog";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";

const ORDER: DelistMarketplace[] = ["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild"];
// eBay has no delete and Sellwild offers sellers only its own delist.
const ACTION: Record<DelistMarketplace, string> = {
  ebay: "ended", etsy: "deleted", poshmark: "deleted", mercari: "deleted", depop: "deleted", sellwild: "delisted",
};

export function deletable(entry: MarketplaceListing | undefined): boolean {
  return Boolean(entry?.listing_id && !entry.sold_here && entry.state !== "deleted" && entry.state !== "deleting");
}

export function DelistActions({ convId, title, disabled }: { convId: string; title: string; disabled: boolean }) {
  const queryClient = useQueryClient();
  const queryKey = ["marketplace-listings", convId];
  const listings = useQuery({ queryKey, queryFn: () => api.marketplaceListings.get(convId) });
  const [picked, setPicked] = useState<DelistMarketplace[]>([]);
  const delist = useMutation({
    mutationFn: (marketplaces: DelistMarketplace[]) => api.marketplaceListings.delist(convId, marketplaces),
    retry: false,
    onSuccess: (data) => { queryClient.setQueryData(queryKey, data); setPicked([]); },
    onSettled: () => void queryClient.invalidateQueries({ queryKey }),
  });
  const rows = ORDER.filter((marketplace) => listings.data?.[marketplace]);
  if (!rows.length) return null;
  const open = rows.filter((marketplace) => deletable(listings.data?.[marketplace]));
  const chosen = picked.filter((marketplace) => open.includes(marketplace));

  async function run(marketplaces: DelistMarketplace[]) {
    const lines = marketplaces.map((marketplace) => `• ${marketplaceName(marketplace)}: ${ACTION[marketplace]}`).join("\n");
    const approved = await confirmDialog(`Delist “${title}”?\n${lines}\nDeleted listings can't be restored; listing again creates a new listing.`,
      { variant: "destructive", confirmLabel: `Delist from ${marketplaces.length === 1 ? marketplaceName(marketplaces[0]) : `${marketplaces.length} marketplaces`}` });
    if (approved) delist.mutate(marketplaces);
  }

  const busy = disabled || delist.isPending || listings.isPending;
  return (
    <details className="mx-3 mt-2 rounded-md border border-border px-3 py-2 text-xs">
      <summary className="cursor-pointer font-medium">Live listings</summary>
      <p className="mt-2 text-muted-foreground">Delist removes the listing from that marketplace: deleted on Etsy, Poshmark, Mercari and Depop, ended on eBay, and delisted on Sellwild. The marketplace where it sold is never touched.</p>
      {rows.map((marketplace) => {
        const entry = listings.data![marketplace]!;
        const can = deletable(entry);
        return (
          <div key={marketplace} role="group" aria-label={`${marketplaceName(marketplace)} listing`} className="mt-2 flex flex-wrap items-center gap-2">
            <Checkbox aria-label={`Delist from ${marketplaceName(marketplace)}`} checked={chosen.includes(marketplace)} disabled={busy || !can}
              onCheckedChange={(next) => setPicked(next === true ? [...picked, marketplace] : picked.filter((m) => m !== marketplace))} />
            <span className="w-20 font-medium">{marketplaceName(marketplace)}</span>
            {entry.url && entry.state !== "deleted" && <a className="underline" href={entry.url} target="_blank" rel="noopener noreferrer">View</a>}
            <span className="text-muted-foreground">
              {entry.sold_here ? "Sold here" : entry.state === "deleted" ? `${ACTION[marketplace][0].toUpperCase()}${ACTION[marketplace].slice(1)}`
                : entry.state === "deleting" ? "Delisting…" : entry.state === "failed" ? "Not delisted" : "Live"}
            </span>
            {entry.error && entry.state === "failed" && <p className="basis-full text-destructive">{entry.error}</p>}
          </div>
        );
      })}
      <div className="mt-2 flex flex-wrap gap-2">
        <Button size="xs" variant="destructive-outline" disabled={busy || !chosen.length} onClick={() => void run(chosen)}>
          {delist.isPending ? "Delisting…" : "Delist selected…"}
        </Button>
        <Button size="xs" variant="destructive-outline" disabled={busy || !open.length} onClick={() => void run(open)}>Delist everywhere…</Button>
      </div>
      {(listings.error || delist.error) && <p role="alert" className="mt-2 text-destructive">{(delist.error || listings.error)?.message}</p>}
    </details>
  );
}
