import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { pollMs } from "../../api/polling";
import type { EbayOffer, EbayOfferSelection, EbayWaterfallStep } from "../../api/ebayOffers";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Input } from "../ui/input";
import { Radio, RadioGroup } from "../ui/radio-group";
import { Spinner } from "../ui/spinner";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";
import { confirmDialog } from "../../ui/confirmDialog";
import { useWorkspaceRuntime } from "../../app/runtime";

export const MAX_WATERFALL_DELAY_MINUTES = 43200;

export interface WaterfallDraftStep {
  discount: string;
  delayMinutes: string;
}

export interface WaterfallValidationResult {
  valid: boolean;
  steps: EbayWaterfallStep[] | undefined;
  rowErrors: (string | null)[];
}

export function validateWaterfallDraft(steps: WaterfallDraftStep[]): WaterfallValidationResult {
  if (!steps.length) {
    return { valid: true, steps: undefined, rowErrors: [] };
  }
  const parsed: EbayWaterfallStep[] = [];
  const rowErrors: (string | null)[] = [];
  let prevDiscount: number | null = null;
  let prevDelay: number | null = null;

  for (const step of steps) {
    const discountText = step.discount.trim();
    const delayText = step.delayMinutes.trim();
    const discountIsInt = /^\d+$/.test(discountText);
    const delayIsInt = /^\d+$/.test(delayText);
    const discountValue = discountIsInt ? Number(discountText) : NaN;
    const delayValue = delayIsInt ? Number(delayText) : NaN;

    let error: string | null = null;
    if (!discountIsInt || !Number.isSafeInteger(discountValue) || discountValue < 5 || discountValue > 99) {
      error = "Discount must be a whole number between 5% and 99%.";
    } else if (!delayIsInt || !Number.isSafeInteger(delayValue) || delayValue < 0 || delayValue > MAX_WATERFALL_DELAY_MINUTES) {
      error = `Delay must be a whole number between 0 and ${MAX_WATERFALL_DELAY_MINUTES.toLocaleString()} minutes.`;
    } else if (prevDiscount !== null && discountValue <= prevDiscount) {
      error = "Discount must be higher than the previous step.";
    } else if (prevDelay !== null && delayValue < prevDelay) {
      error = "Delay cannot be shorter than the previous step.";
    }

    rowErrors.push(error);
    if (error === null) {
      parsed.push({ discount_percent: discountValue, delay_minutes: delayValue });
      prevDiscount = discountValue;
      prevDelay = delayValue;
    }
  }

  const valid = rowErrors.every((entry) => entry === null);
  return { valid, steps: valid ? parsed : undefined, rowErrors };
}

export function formatEnableAllConfirmMessage(
  discountPercent: number,
  minimumPrice: string,
  waterfallSteps?: EbayWaterfallStep[],
): string {
  const minimumText = minimumPrice ? `Offers below $${minimumPrice} are skipped.` : "No minimum price will apply.";
  const waterfallText = waterfallSteps?.length
    ? ` Waterfall steps: ${waterfallSteps
      .map((step, index) => `Step ${index + 1}: ${step.discount_percent}% off after ${step.delay_minutes} min`)
      .join("; ")}.`
    : "";
  return `Enable ${discountPercent}% off offers on all current and future eBay listings in Anteroom? ${minimumText}${waterfallText} This replaces individual automatic offer rules. Anteroom checks every 15 minutes while running and sends to all buyers eBay marks eligible.`;
}

export function EbayOffersSettings() {
  const queryClient = useQueryClient();
  const { hosted } = useWorkspaceRuntime();
  const status = useQuery({ queryKey: ["ebay-offers"], queryFn: api.ebayOffers.status, refetchInterval: () => pollMs(15000) });
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [minimums, setMinimums] = useState<Record<string, string>>({});
  const [discount, setDiscount] = useState("10");
  const [search, setSearch] = useState("");
  const [offers, setOffers] = useState<EbayOffer[] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [scope, setScope] = useState<"selected" | "all" | null>(null);
  const [allDiscount, setAllDiscount] = useState<string | null>(null);
  const [allMinimum, setAllMinimum] = useState<string | null>(null);
  const [waterfallDraft, setWaterfallDraft] = useState<WaterfallDraftStep[] | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["ebay-offers"] });
  const data = status.data;
  const activeWaterfall: WaterfallDraftStep[] = waterfallDraft ?? (data?.allListings.waterfallSteps ?? []).map((step) => ({
    discount: String(step.discount_percent),
    delayMinutes: String(step.delay_minutes),
  }));
  const waterfallValidation = validateWaterfallDraft(activeWaterfall);
  const preview = useMutation({
    mutationFn: ({
      selections,
      discountPercent,
      waterfallSteps,
    }: {
      selections: EbayOfferSelection[];
      discountPercent: number;
      waterfallSteps?: EbayWaterfallStep[];
    }) => api.ebayOffers.preview(selections, discountPercent, waterfallSteps),
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
        setNotice(`Automatic offers enabled for ${ids.length} ${ids.length === 1 ? "listing" : "listings"}. The next check is within 15 minutes while Anteroom is running.`);
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
    onSuccess: (result) => {
      if (hosted) window.open(result.authorizeUrl, "_blank", "noopener,noreferrer");
      setNotice("Finish signing in on the eBay page that opened, then preview your offers again.");
      void queryClient.invalidateQueries({ queryKey: ["direct-connections"] });
    },
    retry: false,
  });
  const enableAll = useMutation({
    mutationFn: ({
      discountPercent,
      minimumPrice,
      waterfallSteps,
    }: {
      discountPercent: number;
      minimumPrice: string;
      waterfallSteps?: EbayWaterfallStep[];
    }) => api.ebayOffers.enableAll(discountPercent, minimumPrice, waterfallSteps),
    retry: false,
    onSuccess: () => {
      setWaterfallDraft(null);
      setAllDiscount(null);
      setAllMinimum(null);
      setNotice("Automatic offers enabled for all current and future eBay listings in Anteroom. The next check is within 15 minutes.");
    },
    onSettled: refresh,
  });
  const globalDiscount = allDiscount ?? String(data?.allListings.discountPercent ?? 10);
  const globalMinimum = allMinimum ?? data?.allListings.minimumPrice ?? "";
  const allEnabled = data?.allListings.enabled === true;
  const allUnchanged = allEnabled && waterfallDraft === null && Number(globalDiscount) === data.allListings.discountPercent
    && (globalMinimum === "" ? data.allListings.minimumPrice === ""
      : data.allListings.minimumPrice !== "" && Number(globalMinimum) === Number(data.allListings.minimumPrice));
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

  function updateWaterfallDraft(next: WaterfallDraftStep[]) {
    setWaterfallDraft(next);
    changed();
  }

  const addWaterfallStep = () => {
    const prev = activeWaterfall[activeWaterfall.length - 1];
    const prevDiscount = prev && /^\d+$/.test(prev.discount.trim()) ? Number(prev.discount.trim()) : null;
    const prevDelay = prev && /^\d+$/.test(prev.delayMinutes.trim()) ? Number(prev.delayMinutes.trim()) : null;
    const baseDiscount = activeScope === "all" ? Number(globalDiscount) : Number(discount);
    const nextDiscount = prevDiscount !== null ? Math.min(99, prevDiscount + 5) : (Number.isInteger(baseDiscount) && baseDiscount >= 5 && baseDiscount <= 99 ? baseDiscount : 10);
    const nextDelay = prevDelay !== null ? Math.min(MAX_WATERFALL_DELAY_MINUTES, prevDelay + 1440) : 15;
    updateWaterfallDraft([...activeWaterfall, { discount: String(nextDiscount), delayMinutes: String(nextDelay) }]);
  };

  return (
    <SettingsSection id="ebay-offers" title="eBay offers" hideTitle>
      <SettingsRow title={<span className="sr-only">Offers to interested buyers</span>}
        description="Every 15 minutes while Anteroom runs, at most one offer per listing a day; US fixed-price listings only."
        control={<Button size="xs" variant="outline" disabled={connect.isPending || busy} onClick={() => connect.mutate()}>
          {connect.isPending ? <Spinner /> : null}{data?.connected ? "Reconnect eBay" : "Connect eBay"}
        </Button>}>
        {data?.lastCheck ? <p className="text-xs text-muted-foreground">Last automatic check: {new Date(data.lastCheck).toLocaleString()}</p> : null}
        {data?.lastError ? <SettingsNote tone="error">{data.lastError}</SettingsNote> : null}
        {status.isPending ? <Spinner /> : null}
        {error ? <p role="alert" className="text-xs text-destructive-foreground">{error.message}</p> : null}
        {notice ? <p role="status" className="text-xs text-foreground">{notice}</p> : null}
        {data?.connected ? <>
          <fieldset className="text-xs" disabled={busy}>
            <legend className="mb-2 font-medium">Listings to include</legend>
            <RadioGroup className="flex-row flex-wrap gap-4" name="ebay-offer-scope" value={activeScope} disabled={busy}
              onValueChange={(next) => { setScope(next as "selected" | "all"); changed(); }}>
              <label className="flex items-center gap-2"><Radio value="selected" />Selected listings</label>
              <label className="flex items-center gap-2"><Radio value="all" />All current and future listings</label>
            </RadioGroup>
          </fieldset>
          {data.allListings.enabled ? <p role="status" className="text-xs text-success-foreground">
            All current and future listings: {data.allListings.discountPercent}% off
            {data.allListings.minimumPrice ? ` · minimum $${data.allListings.minimumPrice}` : " · no minimum price"}
            {data.allListings.waterfallSteps?.length ? ` · ${data.allListings.waterfallSteps.length}-step waterfall` : ""}.
          </p> : null}
          <Button size="xs" variant="outline" disabled={busy || !(data.allListings.enabled || data.listings.some((item) => item.enabled))}
            onClick={() => pause.mutate(undefined)}>Pause all</Button>
          <div className="space-y-2 rounded-lg border border-border/60 p-3 text-xs">
            <div className="flex items-center justify-between gap-2">
              <div>
                <p className="font-medium text-foreground">Waterfall offer steps (optional)</p>
                <p className="text-muted-foreground">Increase the discount over time if a watcher has not accepted. Respects floor prices and eBay cooldowns.</p>
              </div>
              {activeWaterfall.length < 5 ? (
                <Button size="xs" variant="outline" disabled={busy} onClick={addWaterfallStep}>Add step</Button>
              ) : null}
            </div>
            {activeWaterfall.map((step, idx) => (
              <div key={idx} className="space-y-1 border-t border-border/50 pt-2">
                <div className="flex flex-wrap items-end gap-2">
                  <span className="pb-1.5 font-medium text-muted-foreground">Step {idx + 1}</span>
                  <label className="w-24 space-y-1">Discount (%)
                    <Input type="number" min={5} max={99} step={1} value={step.discount} disabled={busy}
                      onChange={(e) => updateWaterfallDraft(activeWaterfall.map((s, i) => (i === idx ? { ...s, discount: e.target.value } : s)))} />
                  </label>
                  <label className="w-28 space-y-1">Delay (minutes)
                    <Input type="number" min={0} max={MAX_WATERFALL_DELAY_MINUTES} step={1} value={step.delayMinutes} disabled={busy}
                      onChange={(e) => updateWaterfallDraft(activeWaterfall.map((s, i) => (i === idx ? { ...s, delayMinutes: e.target.value } : s)))} />
                  </label>
                  <Button size="xs" variant="ghost" disabled={busy}
                    onClick={() => updateWaterfallDraft(activeWaterfall.filter((_, i) => i !== idx))}>Remove</Button>
                </div>
                {waterfallValidation.rowErrors[idx] ? (
                  <p role="alert" className="text-xs text-destructive-foreground">{waterfallValidation.rowErrors[idx]}</p>
                ) : null}
              </div>
            ))}
          </div>
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
            <Button size="sm" variant={allEnabled ? "outline" : "default"} disabled={busy || !globalValid || !waterfallValidation.valid || allUnchanged}
              aria-busy={enableAll.isPending} onClick={async () => {
              if (!waterfallValidation.valid) return;
              const discountPercent = Number(globalDiscount);
              const minimumPrice = globalMinimum;
              const waterfallSteps = waterfallValidation.steps;
              const approved = await confirmDialog(
                formatEnableAllConfirmMessage(discountPercent, minimumPrice, waterfallSteps),
                { confirmLabel: "Enable automatic offers" },
              );
              if (approved) enableAll.mutate({ discountPercent, minimumPrice, waterfallSteps });
            }}>{enableAll.isPending ? <><Spinner />Saving offer settings…</>
              : allUnchanged ? "Offers enabled for all current and future listings"
                : allEnabled ? "Save offer settings" : "Enable offers for all current and future listings"}</Button>
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
                <Checkbox checked={Boolean(selected[item.conversationId])} disabled={busy}
                  onCheckedChange={(next) => { setSelected({ ...selected, [item.conversationId]: next === true }); changed(); }} />
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
            {!visible.length ? <p className="px-3 py-4 text-xs text-muted-foreground">{data.listings.length ? "No matching listings." : "No unsold listings with eBay links. Import them from eBay, or list from Anteroom."}</p> : null}
          </div>
          <Button size="sm" variant="outline" disabled={busy || !selectedListings.length || selectedListings.length > 50 || !discountValid || !floorsValid || !waterfallValidation.valid}
            onClick={() => {
              if (!waterfallValidation.valid) return;
              setOffers(null);
              preview.mutate({
                selections: selectedListings.map((item) => ({
                  conversation_id: item.conversationId,
                  minimum_price: minimums[item.conversationId] ?? item.minimumPrice,
                })),
                discountPercent: Number(discount),
                waterfallSteps: waterfallValidation.steps,
              });
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
            <p className={offer.state === "failed" || offer.state === "unknown" ? "text-destructive-foreground" : "text-muted-foreground"}>
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
