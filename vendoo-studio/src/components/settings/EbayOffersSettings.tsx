import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { EbayOffer, EbayOfferSelection } from "../../api/ebayOffers";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Spinner } from "../ui/spinner";
import { useVendooEnabled } from "../../vendooMode";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";
import { confirmDialog } from "../../ui/confirmDialog";

export function EbayOffersSettings() {
  const vendooOn = useVendooEnabled();
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["ebay-offers"], queryFn: api.ebayOffers.status, refetchInterval: 15000 });
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [minimums, setMinimums] = useState<Record<string, string>>({});
  const [discount, setDiscount] = useState("10");
  const [search, setSearch] = useState("");
  const [offers, setOffers] = useState<EbayOffer[] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [scope, setScope] = useState<"selected" | "all" | null>(null);
  const [allDiscount, setAllDiscount] = useState<string | null>(null);
  const [allMinimum, setAllMinimum] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["ebay-offers"] });
  const preview = useMutation({
    mutationFn: (selections: EbayOfferSelection[]) => api.ebayOffers.preview(selections, Number(discount)),
    retry: false,
    onSuccess: (result) => { setOffers(result.offers); setNotice(null); },
    onSettled: refresh,
  });
  const action = useMutation({
    mutationFn: async ({ kind, ids }: { kind: "send" | "enable" | "checked"; ids: string[] }) => {
      if (kind === "send") {
        const result = await api.ebayOffers.send(ids);
        setOffers((previous) => previous?.map((offer) => ({ ...offer, ...result.offers.find((row) => row.id === offer.id) })) ?? null);
        setNotice("Send complete. Review each result below.");
      } else if (kind === "enable") {
        await api.ebayOffers.enable(ids);
        setOffers(null);
        setNotice(`Automatic offers enabled for ${ids.length} ${ids.length === 1 ? "listing" : "listings"}. The next check is within 30 minutes while Anteroom is running.`);
      } else {
        await api.ebayOffers.checked(ids[0]);
        setNotice("Marked checked. Automation remains paused. Preview again to approve another offer after the cooldown.");
      }
    },
    retry: false,
    onSettled: refresh,
  });
  const pause = useMutation({
    mutationFn: (ruleId?: string) => api.ebayOffers.pause(ruleId),
    retry: false,
    onSuccess: (_result, ruleId) => setNotice(ruleId ? "Automatic offers paused for this listing." : "All automatic offers paused."),
    onSettled: refresh,
  });
  const connect = useMutation({
    mutationFn: () => api.directConnections.connect("ebay"),
    onSuccess: () => {
      setNotice("Finish signing in on the eBay page that opened, then preview your offers again.");
      void queryClient.invalidateQueries({ queryKey: ["direct-connections"] });
    },
    retry: false,
  });
  const enableAll = useMutation({
    mutationFn: ({ discountPercent, minimumPrice }: { discountPercent: number; minimumPrice: string }) =>
      api.ebayOffers.enableAll(discountPercent, minimumPrice),
    retry: false,
    onSuccess: () => setNotice("Automatic offers enabled for all current and future eBay listings in Anteroom. The next check is within 30 minutes."),
    onSettled: refresh,
  });
  const data = status.data;
  const globalDiscount = allDiscount ?? String(data?.allListings.discountPercent ?? 10);
  const globalMinimum = allMinimum ?? data?.allListings.minimumPrice ?? "";
  const activeScope = scope ?? (data?.allListings.enabled ? "all" : "selected");
  const globalValid = Number.isInteger(Number(globalDiscount)) && Number(globalDiscount) >= 5 && Number(globalDiscount) <= 99
    && (!globalMinimum || (/^\d+(\.\d{1,2})?$/.test(globalMinimum) && Number(globalMinimum) > 0));
  const busy = preview.isPending || action.isPending || enableAll.isPending || pause.isPending;
  const selectedListings = data?.listings.filter((item) => selected[item.conversationId]) ?? [];
  const enableIds = offers?.filter((offer) => offer.state === "preview" && offer.id).map((offer) => offer.id!) ?? [];
  const readyIds = offers?.filter((offer) => offer.state === "preview" && offer.eligible && offer.id).map((offer) => offer.id!) ?? [];
  const error = status.error || preview.error || action.error || pause.error || connect.error || enableAll.error;
  const discountValid = Number.isInteger(Number(discount)) && Number(discount) >= 5 && Number(discount) <= 99;
  const floorsValid = selectedListings.every((item) => /^\d+(\.\d{1,2})?$/.test(minimums[item.conversationId] ?? item.minimumPrice)
    && Number(minimums[item.conversationId] ?? item.minimumPrice) > 0);
  const visible = data?.listings.filter((item) => `${item.title} ${item.listingId}`.toLowerCase().includes(search.toLowerCase())) ?? [];

  function changed() {
    setOffers(null);
    setNotice(null);
    preview.reset();
    action.reset();
  }

  return (
    <SettingsSection id="ebay-offers" title="eBay offers" hideTitle>
      <SettingsRow title={<span className="sr-only">Offers to interested buyers</span>}
        description="Choose selected listings or cover all current and future eBay listings in Anteroom. Offers go to every buyer eBay marks eligible. Shipping and tax are separate."
        control={<Button size="xs" variant="outline" disabled={connect.isPending || busy} onClick={() => connect.mutate()}>
          {connect.isPending ? <Spinner /> : null}{data?.connected ? "Reconnect eBay" : "Connect eBay"}
        </Button>}>
        <SettingsNote>
          Automatic offers check every 30 minutes while Anteroom is running, with at most one send per listing every 24 hours.
          Counteroffers are off. This supports US fixed-price listings without variations.
          Existing connections need to reconnect once to grant offer permissions.
        </SettingsNote>
        {data?.lastCheck ? <p className="text-xs text-muted-foreground">Last automatic check: {new Date(data.lastCheck).toLocaleString()}</p> : null}
        {data?.lastError ? <SettingsNote tone="error">{data.lastError}</SettingsNote> : null}
        {status.isPending ? <Spinner /> : null}
        {error ? <p role="alert" className="text-xs text-destructive">{error.message}</p> : null}
        {notice ? <p role="status" className="text-xs text-foreground">{notice}</p> : null}
        {data?.connected ? <>
          <fieldset className="flex flex-wrap gap-4 text-xs" disabled={busy}>
            <legend className="mb-2 font-medium">Listings to include</legend>
            <label className="flex items-center gap-2"><input type="radio" name="ebay-offer-scope" checked={activeScope === "selected"}
              onChange={() => { setScope("selected"); changed(); }} />Selected listings</label>
            <label className="flex items-center gap-2"><input type="radio" name="ebay-offer-scope" checked={activeScope === "all"}
              onChange={() => { setScope("all"); changed(); }} />All current and future listings</label>
          </fieldset>
          {data.allListings.enabled ? <p role="status" className="text-xs text-success-foreground">
            All current and future listings: {data.allListings.discountPercent}% off
            {data.allListings.minimumPrice ? ` · minimum $${data.allListings.minimumPrice}` : " · no minimum price"}.
          </p> : null}
          <Button size="xs" variant="outline" disabled={busy || !(data.allListings.enabled || data.listings.some((item) => item.enabled))}
            onClick={() => pause.mutate(undefined)}>Pause all</Button>
          {activeScope === "all" ? <div className="space-y-3 rounded-lg border border-border/60 p-3">
            <p className="text-xs text-muted-foreground">Include every current eBay listing in Anteroom and automatically include future listings as they are listed or imported. No individual selection is needed.</p>
            <div className="flex flex-wrap gap-3">
              <label className="w-28 space-y-1 text-xs">Discount (%)
                <Input aria-label="Discount for all eBay listings" type="number" min={5} max={99} step={1} value={globalDiscount}
                  disabled={busy} onChange={(event) => { setAllDiscount(event.target.value); setNotice(null); }} />
              </label>
              <label className="w-40 space-y-1 text-xs">Minimum ($, optional)
                <Input aria-label="Minimum price for all eBay listings" type="number" min="0.01" step="0.01" placeholder="No minimum"
                  value={globalMinimum} disabled={busy} onChange={(event) => { setAllMinimum(event.target.value); setNotice(null); }} />
              </label>
            </div>
            <Button size="sm" disabled={busy || !globalValid} onClick={async () => {
              const approved = await confirmDialog(`Enable ${globalDiscount}% off offers on all current and future eBay listings in Anteroom? ${globalMinimum ? `Offers below $${globalMinimum} are skipped.` : "No minimum price will apply."} This replaces individual automatic offer rules. Anteroom checks every 30 minutes while running and sends to all buyers eBay marks eligible.`, { confirmLabel: "Enable automatic offers" });
              if (approved) enableAll.mutate({ discountPercent: Number(globalDiscount), minimumPrice: globalMinimum });
            }}>Enable offers for all current and future listings</Button>
          </div> : <>
          {data.allListings.enabled ? <SettingsNote>Pause all offers before enabling individual rules.</SettingsNote> : null}
          <div className="flex flex-wrap items-end gap-3">
            <label className="w-28 space-y-1 text-xs">Discount (%)
              <Input type="number" min={5} max={99} step={1} value={discount} disabled={busy}
                onChange={(event) => { setDiscount(event.target.value); changed(); }} />
            </label>
            <label className="min-w-40 flex-1 space-y-1 text-xs">Find a listing
              <Input type="search" value={search} placeholder="Title or eBay item ID" onChange={(event) => setSearch(event.target.value)} />
            </label>
          </div>
          <div className="max-h-80 overflow-y-auto rounded-lg border border-border/60">
            {visible.map((item) => <div key={item.listingId} className="flex flex-wrap items-center gap-3 border-b border-border/50 px-3 py-3 last:border-0">
              <label className="flex min-w-40 flex-1 cursor-pointer items-start gap-2 text-xs">
                <input type="checkbox" checked={Boolean(selected[item.conversationId])} disabled={busy}
                  onChange={(event) => { setSelected({ ...selected, [item.conversationId]: event.target.checked }); changed(); }} />
                <span className="min-w-0"><span className="block font-medium">{item.title}</span>
                  <a className="text-muted-foreground underline" href={`https://www.ebay.com/itm/${item.listingId}`} target="_blank" rel="noreferrer">View on eBay</a>
                  {item.enabled ? <span className="ml-2 text-success-foreground">Auto: {item.discountPercent}% off · minimum ${item.minimumPrice}</span> : null}
                </span>
              </label>
              <label className="w-28 space-y-1 text-xs">Minimum ($)
                <Input aria-label={`Minimum price for ${item.title}`} type="number" min="0.01" step="0.01" placeholder="Required"
                  value={minimums[item.conversationId] ?? item.minimumPrice} disabled={busy}
                  onChange={(event) => { setMinimums({ ...minimums, [item.conversationId]: event.target.value }); changed(); }} />
              </label>
              {item.enabled && item.ruleId ? <Button size="xs" variant="ghost" disabled={pause.isPending}
                onClick={() => pause.mutate(item.ruleId!)}>Pause</Button> : null}
            </div>)}
            {!visible.length ? <p className="px-3 py-4 text-xs text-muted-foreground">{data.listings.length ? "No matching listings." : vendooOn ? "No unsold listings with eBay links. Sync your inventory from Vendoo in Settings → Data." : "No unsold listings with eBay links. Import them from eBay, or list from Anteroom."}</p> : null}
          </div>
          <Button size="sm" variant="outline" disabled={busy || !selectedListings.length || selectedListings.length > 50 || !discountValid || !floorsValid}
            onClick={() => {
              setOffers(null);
              preview.mutate(selectedListings.map((item) => ({ conversation_id: item.conversationId,
                minimum_price: minimums[item.conversationId] ?? item.minimumPrice })));
            }}>
            {preview.isPending ? <Spinner /> : null}Preview {selectedListings.length || "selected"} {selectedListings.length === 1 ? "listing" : "listings"}
          </Button>
          {selectedListings.length > 50 ? <SettingsNote tone="error">Select at most 50 listings per preview.</SettingsNote> : null}
          </>}
        </> : null}
        {offers ? <div className="space-y-3 rounded-lg border border-border p-3" aria-label="Offer preview">
          <p className="text-xs font-medium">Review offers · previews expire after 15 minutes</p>
          {offers.map((offer, index) => <div key={offer.id ?? index} className="space-y-1 border-t border-border/50 pt-2 text-xs">
            <p className="font-medium">{offer.title || offer.listingId}</p>
            {offer.offerPrice ? <p>${offer.currentPrice} → <strong>${offer.offerPrice}</strong> · minimum ${offer.minimumPrice} · {offer.discountPercent}% off</p> : null}
            <p className={offer.state === "failed" || offer.state === "unknown" ? "text-destructive" : "text-muted-foreground"}>
              {offer.state === "preview" ? (offer.eligible ? "Ready to send to eBay-eligible buyers." : "No eligible buyers yet. You can enable automatic offers for future buyers.") : offer.note || offer.state}
            </p>
          </div>)}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy || !readyIds.length} onClick={() => action.mutate({ kind: "send", ids: readyIds })}>
              {action.isPending && action.variables.kind === "send" ? <Spinner /> : null}Send {readyIds.length} {readyIds.length === 1 ? "offer" : "offers"} now
            </Button>
            <Button size="sm" variant="outline" disabled={busy || !enableIds.length || data?.allListings.enabled} onClick={() => action.mutate({ kind: "enable", ids: enableIds })}>
              Enable automatic offers for {enableIds.length} {enableIds.length === 1 ? "listing" : "listings"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">Sending contacts eligible buyers immediately. Enabling automation saves these discounts and minimums for future checks.</p>
        </div> : null}
        {data?.history.length ? <details className="rounded-lg border border-border/60 p-3 text-xs">
          <summary className="cursor-pointer font-medium">Offer activity</summary>
          <div className="mt-2 max-h-80 space-y-3 overflow-y-auto">
            {data.history.map((offer) => <div key={offer.id} className="border-t border-border/50 pt-2">
              <p className="font-medium">{offer.title || offer.listingId} · ${offer.offerPrice}</p>
              <p className="text-muted-foreground">{offer.createdAt ? new Date(offer.createdAt).toLocaleString() : ""} · {offer.state}</p>
              <p className="mt-1">{offer.note || (offer.state === "sending" ? "Waiting for eBay to confirm the send." : "")}</p>
              {offer.state === "unknown" && offer.id ? <div className="mt-2 flex flex-wrap items-center gap-3">
                <a className="underline" href="https://www.ebay.com/sh/lst/active" target="_blank" rel="noreferrer">Check offers in Seller Hub</a>
                <Button size="xs" variant="outline" disabled={busy} onClick={() => action.mutate({ kind: "checked", ids: [offer.id!] })}>I've checked Seller Hub</Button>
              </div> : null}
            </div>)}
          </div>
        </details> : null}
      </SettingsRow>
    </SettingsSection>
  );
}
