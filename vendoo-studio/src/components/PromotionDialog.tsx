import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PromotionAction, PromotionFloor, PromotionMarketplace, PromotionPreview, PromotionSupport } from "../api/promotions";
import { needsShippingChoice, promotionTasks, runPromotionTasks, type PromotionTask } from "../promotions";
import { marketplaceName } from "./marketplaceNames";
import { PriceDialog } from "./PriceDropParts";
import { PageError, PageNote } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";

const FIELD = "h-8 rounded-md border border-border bg-background px-2 text-sm";
const NOTES: Record<PromotionMarketplace, string> = {
  poshmark: "Share to followers, or send an offer to everyone who liked a listing.",
  mercari: "Offer to likers: up to 50 recent likers, at least 10% below the lowest price the item has had.",
  depop: "Send offer to likers in your country. Depop has no free refresh.",
};
const WAITING = "Offers to likers are built but not turned on: Anteroom still needs one capture of this marketplace's offer request from your signed-in browser.";
const DEFAULT_SUPPORT: PromotionSupport = { poshmark: ["offer", "share"] };
const ORDER: PromotionMarketplace[] = ["poshmark", "mercari", "depop"];

/** What Anteroom can do on each marketplace now, and why the rest is not offered. */
export function promotionMarketplaces(supported: PromotionSupport = DEFAULT_SUPPORT, waiting: PromotionSupport = {}) {
  return ORDER.map((id) => {
    const actions = (supported[id] || []).filter((action) => !(waiting[id] || []).includes(action));
    return { id, actions, available: actions.length > 0, note: actions.length ? NOTES[id] : (supported[id] || []).length ? WAITING : NOTES[id] };
  });
}
const ACTIONS: { value: PromotionAction; label: string; note: (name: string) => string }[] = [
  { value: "share", label: "Share to followers", note: (name) => `Shares each listing to your ${name} followers, a few seconds apart.` },
  { value: "offer", label: "Offer to likers", note: (name) => `Sends a private offer to everyone who liked each listing. ${name} sets the minimum discount; your floor is never crossed.` },
];
const risk = (name: string) => `This uses your signed-in ${name} session in Chrome. Marketplaces can limit or review accounts that act too quickly, so Anteroom spaces actions out and stops at the first limit ${name} reports.`;

function doneLabel(action: PromotionAction, count: number): string {
  return action === "share" ? `Share ${count} ${count === 1 ? "listing" : "listings"}` : `Send ${count} ${count === 1 ? "offer" : "offers"}`;
}

export function PromotionDialog({ conversationIds, onClose }: { conversationIds: string[]; onClose: () => void }) {
  const client = useQueryClient();
  const [marketplace, setMarketplace] = useState<PromotionMarketplace | null>(null);
  const [action, setAction] = useState<PromotionAction | null>(null);
  const [discount, setDiscount] = useState("10");
  const [floorEdits, setFloorEdits] = useState<Record<string, string>>({});
  const [review, setReview] = useState<PromotionPreview | null>(null);
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set());
  const [shipping, setShipping] = useState<Record<string, string>>({});
  const [confirmed, setConfirmed] = useState(false);
  const [tasks, setTasks] = useState<PromotionTask[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [stopRequested, setStopRequested] = useState(false);
  const stop = useRef(false);
  const history = useQuery({ queryKey: ["promotions"], queryFn: api.promotions.history, retry: false });
  const floors = useQuery({
    queryKey: ["promotion-floors", marketplace, conversationIds],
    queryFn: () => api.promotions.floors(conversationIds, marketplace as PromotionMarketplace),
    enabled: marketplace !== null && action === "offer",
    retry: false,
  });
  const preview = useMutation({
    mutationFn: () => api.promotions.preview({
      conversation_ids: conversationIds, marketplace: marketplace as PromotionMarketplace, action: action as PromotionAction,
      ...(action === "offer" ? { discount_percent: Number(discount), floors: floorEdits } : {}),
    }),
    retry: false,
    onSuccess: (result) => { setReview(result); setUnchecked(new Set()); setShipping({}); setConfirmed(false); setTasks(null); },
  });
  const run = useMutation({
    mutationFn: (approved: PromotionTask[]) => runPromotionTasks(approved, api.promotions.perform, () => stop.current,
      (progress, active) => { setTasks(progress); setActiveId(active); }),
    retry: false,
    onSettled: () => { setActiveId(null); void client.invalidateQueries({ queryKey: ["promotions"] }); },
  });
  const settle = useMutation({
    mutationFn: ({ id, how }: { id: string; how: "check" | "checked" }) => (how === "check" ? api.promotions.check(id) : api.promotions.markChecked(id)),
    retry: false,
    onSettled: () => { void client.invalidateQueries({ queryKey: ["promotions"] }); },
  });
  const approved = review ? promotionTasks(review.rows, unchecked, shipping) : [];
  const busy = preview.isPending || run.isPending || settle.isPending;
  const percent = Number(discount);
  const discountValid = Number.isInteger(percent) && percent >= 1 && percent <= 99;
  const floorRows: [string, PromotionFloor][] = Object.entries(floors.data || {});
  const missingFloor = action === "offer" && floorRows.some(([id, row]) => row.listing_id && !row.floor && !floorEdits[id]?.trim());
  const ready = marketplace !== null && action !== null && (action === "share" || (discountValid && floors.isSuccess && !missingFloor));
  const attention = (history.data?.history || []).filter((row) => row.state === "unknown");
  const marketplaces = promotionMarketplaces(history.data?.supported, history.data?.awaiting_capture);
  const actions = ACTIONS.filter((entry) => marketplaces.find((row) => row.id === marketplace)?.actions.includes(entry.value));
  const name = marketplaceName(review?.marketplace || marketplace || "poshmark");
  const reviewedAction = review?.action || action || "share";
  const start = () => {
    stop.current = false;
    setStopRequested(false);
    setTasks(approved);
    run.mutate(approved);
  };
  const footer = tasks ? <>
    {run.isPending ? <Button variant="outline" disabled={stopRequested} onClick={() => { stop.current = true; setStopRequested(true); }}>{stopRequested ? "Stopping after this item…" : "Stop after current item"}</Button>
      : <Button disabled={busy} onClick={onClose}>Done</Button>}
  </> : review ? <>
    <Button variant="ghost" disabled={busy} onClick={() => setReview(null)}>Back</Button>
    <Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button>
    <Button disabled={busy || !confirmed || !approved.length} onClick={start}>{doneLabel(review.action, approved.length)}</Button>
  </> : <>
    <Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button>
    <Button disabled={busy || !ready} onClick={() => preview.mutate()}>{preview.isPending ? "Checking…" : "Review items"}</Button>
  </>;
  const error = preview.error || run.error || settle.error || floors.error;
  return <PriceDialog title={tasks ? "Results" : review ? `Review on ${marketplaceName(review.marketplace)}` : `Share or send offers for ${conversationIds.length} ${conversationIds.length === 1 ? "item" : "items"}`}
    className="max-w-3xl" busy={busy} onClose={onClose} footer={footer}
    description="Nothing is sent until you review each item and confirm. Items run one at a time, each once; nothing is retried, listed or delisted.">
    {error ? <PageError message={error.message || "Could not review these items"} /> : null}
    {tasks ? <div className="space-y-3" aria-live="polite">
      <PageNote>{tasks.filter((task) => task.state === "done").length} done · {tasks.filter((task) => task.state !== "done" && task.state !== "pending").length} not done</PageNote>
      {tasks.map((task) => <div key={task.id} className="min-w-0 break-words rounded-lg border p-3">
        <p className="font-medium">{task.title}</p>
        <p className="text-xs">{activeId === task.id ? (reviewedAction === "share" ? "Sharing…" : "Sending offer…") : task.state === "pending" ? "Not started" : task.note}</p>
      </div>)}
    </div> : review ? <div className="space-y-3">
      <PageNote>{risk(name)}</PageNote>
      <ul className="space-y-2">{review.rows.map((row) => <li key={row.id} className="min-w-0 space-y-1 rounded-lg border p-3">
        <label className="flex items-center gap-2 font-medium">
          <Checkbox aria-label={`Include ${row.title || "item"}`} disabled={row.state !== "preview" || busy}
            checked={row.state === "preview" && !unchecked.has(row.id)}
            onCheckedChange={(checked) => { setConfirmed(false); setUnchecked((previous) => { const next = new Set(previous); if (checked) next.delete(row.id); else next.add(row.id); return next; }); }} />
          {row.title || "Untitled item"}
        </label>
        {row.state !== "preview" ? <p className="text-xs text-muted-foreground">{row.note}</p>
          : row.action === "share" ? <p className="text-xs">Share to followers · ${row.price}</p>
          : <div className="space-y-1 text-xs">
            <p>Offer ${row.offer_price} to {row.like_count} {row.like_count === 1 ? "liker" : "likers"} · price ${row.price} · your floor ${row.floor}</p>
            {needsShippingChoice(row) ? <label className="flex flex-wrap items-center gap-2">Shipping discount
              <select className={FIELD} aria-label={`Shipping discount for ${row.title || "item"}`} value={shipping[row.id] || ""} disabled={busy}
                onChange={(event) => { setConfirmed(false); setShipping((previous) => ({ ...previous, [row.id]: event.target.value })); }}>
                <option value="">Choose…</option>
                <option value="none">No shipping discount</option>
                {row.shipping_options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
              </select>
            </label> : null}
          </div>}
      </li>)}</ul>
      {review.action === "offer" && review.marketplace === "poshmark" ? <PageNote>Choose a shipping discount for each offer. Poshmark may require one; if it refuses, nothing is sent for that item.</PageNote> : null}
      <label className="flex items-start gap-2 rounded-lg border p-3 font-medium">
        <Checkbox checked={confirmed} disabled={busy || !approved.length} onCheckedChange={(checked) => setConfirmed(checked === true)} />
        <span>I reviewed these {approved.length} items and want to {review.action === "share" ? "share them to my followers" : "send these offers to likers"} on {marketplaceName(review.marketplace)}.</span>
      </label>
    </div> : <div className="space-y-4">
      {attention.length ? <div className="space-y-2 rounded-lg border p-3">
        <p className="font-medium">Needs a check</p>
        {attention.map((row) => <div key={row.id} className="space-y-1 text-xs">
          <p><span className="font-medium">{row.title || row.listing_id}</span> · {row.action === "share" ? "share" : `offer $${row.offer_price}`} on {marketplaceName(row.marketplace)}</p>
          <p className="text-muted-foreground">{row.note}</p>
          <div className="flex gap-1.5">
            <Button size="xs" variant="outline" disabled={busy} onClick={() => settle.mutate({ id: row.id, how: "check" })}>Check result</Button>
            <Button size="xs" variant="ghost" disabled={busy} onClick={() => settle.mutate({ id: row.id, how: "checked" })}>I checked it</Button>
          </div>
        </div>)}
      </div> : null}
      <fieldset className="space-y-2"><legend className="mb-1 font-medium">Marketplace</legend>
        {marketplaces.map((entry) => <label key={entry.id} className="flex items-start gap-2">
          <input type="radio" name="promotion-marketplace" className="mt-1" checked={marketplace === entry.id} disabled={busy || !entry.available}
            onChange={() => { setMarketplace(entry.id); setAction(null); }} />
          <span><span className="font-medium">{marketplaceName(entry.id)}</span><span className="block text-xs text-muted-foreground">{entry.note}</span></span>
        </label>)}
      </fieldset>
      {marketplace ? <fieldset className="space-y-2"><legend className="mb-1 font-medium">Action</legend>
        {actions.map((entry) => <label key={entry.value} className="flex items-start gap-2">
          <input type="radio" name="promotion-action" className="mt-1" checked={action === entry.value} disabled={busy} onChange={() => setAction(entry.value)} />
          <span><span className="font-medium">{entry.label}</span><span className="block text-xs text-muted-foreground">{entry.note(name)}</span></span>
        </label>)}
      </fieldset> : null}
      {marketplace && action === "offer" ? <div className="space-y-3">
        <label className="flex items-center gap-2 text-sm">Discount
          <input className={`${FIELD} w-20`} inputMode="numeric" aria-label="Discount percent" value={discount} disabled={busy}
            onChange={(event) => setDiscount(event.target.value.replace(/[^0-9]/g, "").slice(0, 2))} />% off the current price
        </label>
        <div className="space-y-1">
          <p className="text-sm font-medium">Lowest price you accept (required)</p>
          {floors.isPending ? <PageNote>Loading saved floors…</PageNote> : null}
          {floorRows.map(([id, row]) => <label key={id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="min-w-0 truncate">{row.title || "Untitled item"}</span>
            {row.listing_id ? <span className="flex items-center gap-1">$<input className={`${FIELD} w-24`} inputMode="decimal" aria-label={`Floor for ${row.title || "item"}`}
              placeholder={row.floor || "Required"} value={floorEdits[id] ?? row.floor ?? ""} disabled={busy}
              onChange={(event) => setFloorEdits((previous) => ({ ...previous, [id]: event.target.value.replace(/[^0-9.]/g, "").slice(0, 10) }))} /></span>
              : <span className="text-muted-foreground">{row.reason}</span>}
          </label>)}
        </div>
      </div> : null}
      {marketplace ? <PageNote>{risk(name)}</PageNote> : null}
    </div>}
  </PriceDialog>;
}
