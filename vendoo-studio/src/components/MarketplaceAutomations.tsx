/**
 * Forms for scheduled Poshmark sharing and automatic offers to likers, shown in
 * the Automations side panel. Each one is saved off and runs only after the
 * seller turns it on; any edit turns it off again.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { PromotionAction, PromotionAutomation, PromotionMarketplace } from "../api/promotions";
import { confirmDialog } from "../ui/confirmDialog";
import { marketplaceName } from "./marketplaceNames";
import {
  ORDER_LABELS, SPEED_LABELS, bodyFromPromotionForm, describeOfferChecks, describePromotionMinimum, describePromotionAutomation, formFromAutomation, withListing,
  type PromotionAutomationForm,
} from "./promotionAutomationsFormat";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";
import { Radio, RadioGroup } from "./ui/radio-group";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { Spinner } from "./ui/spinner";

const KEY = ["promotion-automations"] as const;
const FIELD = "h-8 rounded-md border border-border bg-background px-2 text-sm";

function when(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "";
}

function Choice<T extends string>({ label, value, options, onChange }: {
  label: string; value: T | null; options: readonly { value: T | null; label: string }[]; onChange: (value: T | null) => void;
}) {
  return (
    <Select value={value} onValueChange={(next) => onChange(next as T | null)}>
      <SelectTrigger size="sm" aria-label={label}>
        <SelectValue>{options.find((option) => option.value === value)?.label}</SelectValue>
      </SelectTrigger>
      <SelectPopup>
        {options.map((option) => <SelectItem key={option.value ?? ""} value={option.value}>{option.label}</SelectItem>)}
      </SelectPopup>
    </Select>
  );
}

/** Who turns a sharing schedule or offer rule on: only the seller, after reading what it will do. */
export async function confirmPromotionOn(row: PromotionAutomation): Promise<boolean> {
  const what = row.action === "share"
    ? `Anteroom will share these ${marketplaceName(row.marketplace)} listings to your followers on its own at ${row.times.join(", ")} (${row.timezone}), up to ${(row.daily_cap ?? 0).toLocaleString()} a day.`
    : `Anteroom will send ${row.discount_percent}% off offers to new likers on its own; ${describeOfferChecks(row.marketplace)}; ${describePromotionMinimum(row)}.`;
  return confirmDialog(`Turn on “${row.name}”? ${what} It stops and turns itself off at the first limit or unconfirmed result. Nothing runs until the next round.`, { confirmLabel: "Turn on" });
}

export function usePromotionAutomations(enabled = true) {
  return useQuery({ queryKey: KEY, queryFn: api.promotionAutomations.list, refetchInterval: 30000, enabled });
}

function useSavePromotion(onSaved: () => void) {
  const client = useQueryClient();
  const list = usePromotionAutomations();
  return useMutation({
    mutationFn: async (next: PromotionAutomationForm) => {
      const built = bodyFromPromotionForm(next, list.data?.max_daily_cap ?? 6000);
      if ("error" in built) throw new Error(built.error);
      return next.id ? api.promotionAutomations.update(next.id, built.body) : api.promotionAutomations.create(built.body);
    },
    retry: false,
    onSuccess: onSaved,
    onSettled: () => { void client.invalidateQueries({ queryKey: KEY }); },
  });
}

/** New or edit a sharing schedule or offer rule. Saving always leaves it off. */
export function PromotionEditor({ initial, onSaved, onCancel }: {
  initial: PromotionAutomationForm; onSaved: () => void; onCancel: () => void;
}) {
  const list = usePromotionAutomations();
  const [form, setForm] = useState(initial);
  const save = useSavePromotion(onSaved);
  const data = list.data;
  const labels = data?.shipping_labels || [];
  const maxCap = data?.max_daily_cap ?? 6000;
  const waiting = (marketplace: PromotionMarketplace, action: PromotionAction) => Boolean(data?.awaiting_capture?.[marketplace]?.includes(action));
  const busy = save.isPending;
  const set = (patch: Partial<PromotionAutomationForm>) => setForm({ ...form, ...patch });
  const offerMarketplaces: PromotionMarketplace[] = ["poshmark", "mercari", "depop"];
  return (
    <div className="flex flex-col gap-3 text-sm">
      <p className="text-muted-foreground">Saved off. Turn it on from the list; any edit turns it off again.</p>
      {save.error ? <p role="alert" className="text-destructive-foreground">{save.error.message}</p> : null}
      <label className="flex flex-col gap-1">Name
        <Input value={form.name} maxLength={80} placeholder={form.action === "share" ? "Poshmark sharing" : "Offers to likers"} onChange={(event) => set({ name: event.target.value })} />
      </label>
      {form.action === "offer" && !form.id ? (
        <label className="flex flex-col gap-1">Marketplace
          <Choice label="Marketplace" value={form.marketplace} onChange={(marketplace) => { if (marketplace) set({ marketplace }); }}
            options={offerMarketplaces.map((name) => ({ value: name, label: `${marketplaceName(name)}${waiting(name, "offer") ? " (not available yet)" : ""}` }))} />
        </label>
      ) : null}
      <fieldset><legend className="mb-1">Listings</legend>
        <RadioGroup className="flex-row flex-wrap gap-4" value={form.scope} onValueChange={(scope) => set({ scope: scope as PromotionAutomationForm["scope"] })}>
          <label className="flex items-center gap-2"><Radio value="all" />{form.action === "offer" ? "All current and future listings" : "All live listings"}</label>
          <label className="flex items-center gap-2"><Radio value="items" />Only listings I choose</label>
        </RadioGroup>
      </fieldset>
      <p className="text-xs text-muted-foreground">Choose listings and exclusions under Listings after saving.</p>
      <label className="flex items-center gap-2">Only listings live at least
        <input className={`${FIELD} w-16`} inputMode="numeric" value={form.minDays} onChange={(event) => set({ minDays: event.target.value })} /> days
      </label>
      {form.action === "share" ? <>
        <label className="flex flex-col gap-1">Round times (up to 6)
          <Input value={form.times} placeholder="09:00, 13:00, 20:00" onChange={(event) => set({ times: event.target.value })} />
        </label>
        <label className="flex flex-col gap-1">Time zone
          <Input value={form.timezone} onChange={(event) => set({ timezone: event.target.value })} />
        </label>
        <div className="flex flex-wrap gap-3">
          <label className="flex flex-col gap-1">Order
            <Choice label="Order" value={form.order} onChange={(order) => { if (order) set({ order }); }}
              options={Object.entries(ORDER_LABELS).map(([value, label]) => ({ value: value as PromotionAutomationForm["order"], label }))} />
          </label>
          <label className="flex flex-col gap-1">Speed
            <Choice label="Speed" value={form.speed} onChange={(speed) => { if (speed) set({ speed }); }}
              options={Object.entries(SPEED_LABELS).map(([value, label]) => ({ value: value as PromotionAutomationForm["speed"], label }))} />
          </label>
          <label className="flex flex-col gap-1">Daily cap
            <input className={`${FIELD} w-24`} inputMode="numeric" value={form.dailyCap} onChange={(event) => set({ dailyCap: event.target.value })} />
          </label>
        </div>
        <p className="text-xs text-muted-foreground">The cap counts every share in the last 24 hours, including ones you start yourself (at most {maxCap.toLocaleString()}).</p>
      </> : <>
        <div className="flex flex-wrap gap-3">
          <label className="flex items-center gap-2">Discount
            <input className={`${FIELD} w-16`} inputMode="numeric" value={form.discount} onChange={(event) => set({ discount: event.target.value })} />% off
          </label>
          <label className="flex items-center gap-2">At least
            <input className={`${FIELD} w-16`} inputMode="numeric" value={form.minLikes} onChange={(event) => set({ minLikes: event.target.value })} /> likes
          </label>
          <label className="flex items-center gap-2">Minimum ($, optional)
            <Input className="w-28" type="number" min="0.01" step="0.01" placeholder="No minimum" value={form.minimumPrice}
              onChange={(event) => set({ minimumPrice: event.target.value })} />
          </label>
        </div>
        {form.marketplace === "poshmark" ? (
          <label className="flex flex-col gap-1">Shipping discount
            <Choice label="Shipping discount" value={form.shippingLabel || null} onChange={(shippingLabel) => set({ shippingLabel: shippingLabel ?? "" })}
              options={[{ value: null, label: "No shipping discount" },
                ...[...new Set([...labels, ...(form.shippingLabel ? [form.shippingLabel] : [])])].map((label) => ({ value: label, label }))]} />
            <span className="text-xs text-muted-foreground">Labels come from offers you reviewed. A listing where Poshmark does not offer this one is skipped.</span>
          </label>
        ) : null}
        <p className="text-xs text-muted-foreground">At most one offer a day per listing; saved individual minimums still apply.</p>
      </>}
      <div className="flex gap-2">
        <Button size="sm" disabled={busy} onClick={() => save.mutate(form)}>{busy ? <Spinner /> : null}Save</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

/** One sharing schedule or offer rule: which listings it covers, and what its rounds did. */
export function PromotionDetails({ automation }: { automation: PromotionAutomation }) {
  const client = useQueryClient();
  const saveListings = useSavePromotion(() => undefined);
  const busy = saveListings.isPending;
  const coverage = useQuery({ queryKey: [...KEY, automation.id, "coverage"], queryFn: () => api.promotionAutomations.coverage(automation.id) });
  const activity = useQuery({ queryKey: [...KEY, automation.id, "activity"], queryFn: () => api.promotionAutomations.activity(automation.id), refetchInterval: 30000 });
  const [form, setForm] = useState(() => formFromAutomation(automation));
  const [floors, setFloors] = useState<Record<string, string>>({});
  const refresh = () => { void client.invalidateQueries({ queryKey: KEY }); };
  const saveFloors = useMutation({ mutationFn: () => api.promotions.saveFloors(automation.marketplace, floors), retry: false,
    onSuccess: () => { setFloors({}); refresh(); } });
  const settle = useMutation({ mutationFn: ({ id, how }: { id: string; how: "check" | "checked" }) => (how === "check" ? api.promotions.check(id) : api.promotions.markChecked(id)),
    retry: false, onSettled: refresh });
  const included = (id: string) => (form.scope === "items" ? form.conversationIds.includes(id) : !form.excludedIds.includes(id));
  const changed = form.excludedIds.join() !== automation.excluded_ids.join() || form.conversationIds.join() !== automation.conversation_ids.join();
  const error = coverage.error || activity.error || saveFloors.error || settle.error || saveListings.error;
  return (
    <div className="flex flex-col gap-3 text-sm">
      <p className="text-muted-foreground">{describePromotionAutomation(automation)}</p>
      {error ? <p role="alert" className="text-xs text-destructive-foreground">{error.message}</p> : null}
      <div className="rounded-lg border border-border/60 p-3">
        <p className="font-medium">Listings {coverage.data ? `· ${coverage.data.included} covered` : ""}</p>
        {automation.action === "offer" ? <p className="text-xs text-muted-foreground">Individual minimums are optional and can raise the rule's minimum. Clear one to use the rule's minimum.</p> : null}
        {coverage.isPending ? <Spinner /> : null}
        <ul className="mt-2 max-h-72 space-y-1 overflow-y-auto">
          {(coverage.data?.items || []).map((item) => (
            <li key={item.conversation_id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <label className="flex min-w-0 items-center gap-2">
                <Checkbox checked={included(item.conversation_id)} disabled={busy}
                  onCheckedChange={(next) => setForm(withListing(form, item.conversation_id, next === true))} />
                <span className="truncate">{item.title || item.listing_id}</span>
                <span className="text-muted-foreground">{item.listed_days != null ? `${item.listed_days} d` : ""}{item.too_new ? " · too new" : ""}</span>
              </label>
              {automation.action === "offer" ? <span className="flex items-center gap-1">Minimum $
                <input className={`${FIELD} w-20`} inputMode="decimal" aria-label={`Floor for ${item.title || "listing"}`} placeholder={automation.minimum_price || "None"}
                  value={floors[item.conversation_id] ?? item.floor ?? ""}
                  onChange={(event) => setFloors({ ...floors, [item.conversation_id]: event.target.value.replace(/[^0-9.]/g, "").slice(0, 10) })} />
              </span> : null}
            </li>
          ))}
          {coverage.data && !coverage.data.items.length ? <li className="text-xs text-muted-foreground">No live {marketplaceName(automation.marketplace)} listings with links in Anteroom.</li> : null}
        </ul>
        <div className="mt-2 flex flex-wrap gap-2">
          <Button size="xs" variant="outline" disabled={busy || !changed} onClick={() => saveListings.mutate(form)}>Save listings (turns it off)</Button>
          {automation.action === "offer" ? <Button size="xs" variant="outline" disabled={saveFloors.isPending || !Object.keys(floors).length}
            onClick={() => saveFloors.mutate()}>Save floors</Button> : null}
        </div>
      </div>
      <div className="rounded-lg border border-border/60 p-3">
        <p className="font-medium">Activity</p>
        <ul className="mt-2 max-h-72 space-y-2 overflow-y-auto text-xs">
          {(activity.data?.sends || []).filter((send) => send.state === "unknown").map((send) => (
            <li key={send.id} className="rounded border border-destructive/40 p-2">
              <p><span className="font-medium">{send.title || send.listing_id}</span> · {send.action === "share" ? "share" : `offer $${send.offer_price}`} · not confirmed</p>
              <p className="text-muted-foreground">{send.note}</p>
              <div className="mt-1 flex gap-1.5">
                <Button size="xs" variant="outline" disabled={settle.isPending} onClick={() => settle.mutate({ id: send.id, how: "check" })}>Check result</Button>
                <Button size="xs" variant="ghost" disabled={settle.isPending} onClick={() => settle.mutate({ id: send.id, how: "checked" })}>I checked it</Button>
              </div>
            </li>
          ))}
          {(activity.data?.runs || []).map((run) => (
            <li key={run.id}>
              <span className="text-muted-foreground">{when(run.started_at)}</span> · {run.sent} sent · {run.skipped} skipped · {run.failed} not done
              {run.note ? <span className="block text-muted-foreground">{run.note}</span> : null}
            </li>
          ))}
          {activity.data && !activity.data.runs.length ? <li className="text-muted-foreground">No rounds yet.</li> : null}
        </ul>
      </div>
    </div>
  );
}

