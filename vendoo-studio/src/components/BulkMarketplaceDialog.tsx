import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { BulkMarketplaceAction, BulkMarketplacePreview } from "../api/bulkMarketplace";
import type { DelistMarketplace } from "../api/marketplaceListings";
import { reviewedTasks, runMarketplaceTasks, type MarketplaceTask } from "../bulkMarketplace";
import { marketplaceName } from "./marketplaceNames";
import { PriceDialog } from "./PriceDropParts";
import { PageError, PageNote } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";

export const BULK_MARKETPLACES: DelistMarketplace[] = ["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild", "vinted"];
const ACTIONS: { value: BulkMarketplaceAction; label: string; note: string }[] = [
  { value: "list", label: "List", note: "Publishes each item’s saved listing. An item already live on a marketplace is skipped there; choose Relist for that." },
  { value: "delist", label: "Delist", note: "Removes each live listing from the marketplace; eBay ends it. A marketplace where the item sold is never touched." },
  { value: "relist", label: "Relist", note: "Takes each live listing down, then lists the saved listing again. If listing again fails, the item stays down and is reported." },
  { value: "update", label: "Update", note: "Writes each item’s saved listing onto the listing already live, in place, so likes, views and listing age stay. Nothing is listed or taken down; a failed update leaves the live listing as it was." },
  { value: "not_listed", label: "Mark as not listed", note: "Anteroom forgets the marketplace listing for each item. The marketplace is not contacted: a listing still up there stays up." },
];
const DONE: Record<BulkMarketplaceAction, string> = { list: "Listed", delist: "Delisted", relist: "Relisted", update: "Updated", not_listed: "Marked not listed" };
const ACTIVE: Record<BulkMarketplaceAction, string> = { list: "Listing", delist: "Delisting", relist: "Relisting", update: "Updating", not_listed: "Marking not listed" };

function names(values: readonly string[]): string {
  return values.map(marketplaceName).join(", ");
}

export function BulkMarketplaceDialog({ conversationIds, onClose }: { conversationIds: string[]; onClose: () => void }) {
  const client = useQueryClient();
  const [action, setAction] = useState<BulkMarketplaceAction>("list");
  const [chosen, setChosen] = useState<DelistMarketplace[]>([]);
  const [review, setReview] = useState<BulkMarketplacePreview | null>(null);
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set());
  const [confirmed, setConfirmed] = useState(false);
  const [tasks, setTasks] = useState<MarketplaceTask[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [stopRequested, setStopRequested] = useState(false);
  const stop = useRef(false);
  const preview = useMutation({
    mutationFn: (ids: string[]) => api.bulkMarketplace.preview(ids, action, chosen),
    retry: false,
    onSuccess: (result) => { setReview(result); setUnchecked(new Set()); setConfirmed(false); setTasks(null); },
  });
  const run = useMutation({
    mutationFn: (approved: MarketplaceTask[]) => runMarketplaceTasks(approved, api.bulkMarketplace.perform, () => stop.current,
      (progress, active) => { setTasks(progress); setActiveId(active); }),
    retry: false,
    onSettled: (_result, _error, approved) => {
      setActiveId(null);
      for (const key of ["conversations", "analytics", "automation-approvals"]) void client.invalidateQueries({ queryKey: [key] });
      for (const task of approved) {
        for (const key of ["marketplace-listings", "native-marketplaces", "listing"]) void client.invalidateQueries({ queryKey: [key, task.id] });
      }
    },
  });
  const approved = review ? reviewedTasks(review.rows, unchecked) : [];
  const busy = preview.isPending || run.isPending;
  const verb = ACTIONS.find((entry) => entry.value === (review?.action || action))!;
  const remaining = tasks?.filter((task) => task.state !== "done") || [];
  const start = () => {
    stop.current = false;
    setStopRequested(false);
    setTasks(approved);
    run.mutate(approved);
  };
  const footer = tasks ? <>
    {run.isPending ? <Button variant="outline" disabled={stopRequested} onClick={() => { stop.current = true; setStopRequested(true); }}>{stopRequested ? "Stopping after this item…" : "Stop after current item"}</Button> : <>
      {remaining.length ? <Button variant="outline" disabled={busy} onClick={() => preview.mutate(remaining.map((task) => task.id))}>Review remaining</Button> : null}
      <Button disabled={busy} onClick={onClose}>Done</Button>
    </>}
  </> : review ? <>
    <Button variant="ghost" disabled={busy} onClick={() => setReview(null)}>Back</Button>
    <Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button>
    <Button variant={review.action === "list" ? "default" : "destructive"} disabled={busy || !confirmed || !approved.length} onClick={start}>
      {verb.label} {approved.length} {approved.length === 1 ? "item" : "items"}
    </Button>
  </> : <>
    <Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button>
    <Button disabled={busy || !chosen.length} onClick={() => preview.mutate(conversationIds)}>{preview.isPending ? "Checking…" : "Review items"}</Button>
  </>;
  return <PriceDialog title={tasks ? `${verb.label} results` : review ? `Review ${verb.label.toLowerCase()}` : `Marketplace actions for ${conversationIds.length} items`}
    className="max-w-3xl" busy={busy} onClose={onClose} footer={footer}
    description="Nothing is sent until you review each item and confirm. Items run one at a time, each marketplace once; a failed item is not retried.">
    {preview.error || run.error ? <PageError message={preview.error?.message || run.error?.message || "Could not review these items"} /> : null}
    {tasks ? <div className="space-y-3" aria-live="polite">
      <PageNote>{tasks.filter((task) => task.state === "done").length} done · {tasks.filter((task) => task.state === "failed" || task.state === "unconfirmed").length} need attention · {tasks.filter((task) => task.state === "pending").length} not started</PageNote>
      {tasks.map((task) => <div key={task.id} className="min-w-0 break-words rounded-lg border p-3">
        <p className="font-medium">{task.title}</p>
        <p className="text-xs">{activeId === task.id ? `${ACTIVE[task.request.action]} on ${names(task.request.marketplaces)}…`
          : task.state === "done" ? `${DONE[task.request.action]} on ${names(task.request.marketplaces)}`
          : task.state === "pending" ? "Not started" : task.error}</p>
      </div>)}
      {remaining.length && !run.isPending ? <PageNote>Review remaining checks what each marketplace has now. Nothing is sent again until you confirm a new review.</PageNote> : null}
    </div> : review ? <div className="space-y-3">
      <PageNote>{verb.note}</PageNote>
      <ul className="space-y-2">{review.rows.map((row) => <li key={row.conversation_id} className="min-w-0 space-y-1 rounded-lg border p-3">
        <label className="flex items-center gap-2 font-medium">
          <Checkbox aria-label={`Include ${row.title || "item"}`} disabled={Boolean(row.skip_reason) || busy}
            checked={!row.skip_reason && !unchecked.has(row.conversation_id)}
            onCheckedChange={(checked) => { setConfirmed(false); setUnchecked((previous) => { const next = new Set(previous); if (checked) next.delete(row.conversation_id); else next.add(row.conversation_id); return next; }); }} />
          {row.title || "Untitled item"}
        </label>
        {row.marketplaces.length ? <p className="text-xs">{verb.label}: {names(row.marketplaces)}</p> : null}
        {row.skipped.map((entry) => <p key={entry.marketplace} className="text-xs text-muted-foreground">{entry.reason}</p>)}
        {row.skip_reason ? <p className="text-xs text-muted-foreground">{row.skip_reason}</p> : null}
      </li>)}</ul>
      <label className="flex items-start gap-2 rounded-lg border p-3 font-medium">
        <Checkbox checked={confirmed} disabled={busy || !approved.length} onCheckedChange={(checked) => setConfirmed(checked === true)} />
        <span>{review.action === "not_listed"
          ? `I reviewed these ${approved.length} items and want Anteroom to mark them not listed on the marketplaces shown.`
          : `I reviewed these ${approved.length} items and want to ${verb.label.toLowerCase()} them on the marketplaces shown.`}</span>
      </label>
    </div> : <div className="space-y-4">
      <fieldset className="space-y-2"><legend className="mb-1 font-medium">Action</legend>
        {ACTIONS.map((entry) => <label key={entry.value} className="flex items-start gap-2">
          <input type="radio" name="bulk-marketplace-action" className="mt-1" checked={action === entry.value} disabled={busy} onChange={() => setAction(entry.value)} />
          <span><span className="font-medium">{entry.label}</span><span className="block text-xs text-muted-foreground">{entry.note}</span></span>
        </label>)}
      </fieldset>
      <fieldset className="space-y-2"><legend className="mb-1 font-medium">Marketplaces</legend>
        <div className="grid gap-2 sm:grid-cols-3">{BULK_MARKETPLACES.map((name) => <label key={name} className="flex items-center gap-2">
          <Checkbox checked={chosen.includes(name)} disabled={busy}
            onCheckedChange={(checked) => setChosen((previous) => checked ? BULK_MARKETPLACES.filter((value) => value === name || previous.includes(value)) : previous.filter((value) => value !== name))} />
          {marketplaceName(name)}
        </label>)}</div>
        <PageNote>Choose each marketplace by name. None is chosen for you.</PageNote>
      </fieldset>
    </div>}
  </PriceDialog>;
}
