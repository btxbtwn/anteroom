import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ConversationSummary } from "../api/types";
import type { BulkMarketplaceAction, BulkMarketplacePreview } from "../api/bulkMarketplace";
import type { DelistMarketplace } from "../api/marketplaceListings";
import { prepareMarketplaceText, reviewedTasks, runMarketplaceTasks, type MarketplaceTask, type TextPrepResult } from "../bulkMarketplace";
import { TEXT_MODES, type TextMode } from "../api/listingRules";
import { marketplaceName } from "./marketplaceNames";
import { PriceDialog } from "./PriceDropParts";
import { PageError, PageNote } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Radio, RadioGroup } from "./ui/radio-group";

export const BULK_MARKETPLACES: DelistMarketplace[] = ["ebay", "etsy", "poshmark", "mercari", "depop"];
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

export function BulkMarketplaceDialog({
  conversationIds,
  onClose,
  initialAction = "list",
  initialMarketplaces = [],
}: {
  conversationIds: string[];
  onClose: () => void;
  /** Pre-selected action when opened from a specific marketplace tab. */
  initialAction?: BulkMarketplaceAction;
  /** Pre-selected marketplace(s) when the seller explicitly started from a marketplace tab. */
  initialMarketplaces?: readonly DelistMarketplace[];
}) {
  const client = useQueryClient();
  const [action, setAction] = useState<BulkMarketplaceAction>(initialAction);
  const [chosen, setChosen] = useState<DelistMarketplace[]>(() =>
    BULK_MARKETPLACES.filter((value) => initialMarketplaces.includes(value)));
  const [review, setReview] = useState<BulkMarketplacePreview | null>(null);
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set());
  const [confirmed, setConfirmed] = useState(false);
  const [tasks, setTasks] = useState<MarketplaceTask[] | null>(null);
  const [activeIds, setActiveIds] = useState<ReadonlySet<string>>(new Set());
  const [stopRequested, setStopRequested] = useState(false);
  const stop = useRef(false);
  const [textMode, setTextMode] = useState<TextMode>("keep");
  const [textResults, setTextResults] = useState<TextPrepResult[] | null>(null);
  const [textProgress, setTextProgress] = useState<number | null>(null);
  // Only List, Relist and Update send text; the other actions leave it alone.
  const writesText = action === "list" || action === "relist" || action === "update";
  const preview = useMutation({
    mutationFn: async (ids: string[]) => {
      if (writesText && textMode !== "keep") {
        setTextProgress(0);
        try {
          // Saved locally before the review, so the review shows what would be sent.
          setTextResults(await prepareMarketplaceText(ids, chosen, textMode, api.bulkEdit.marketplaceText, setTextProgress));
        } finally {
          setTextProgress(null);
        }
      } else {
        setTextResults(null);
      }
      return api.bulkMarketplace.preview(ids, action, chosen);
    },
    retry: false,
    onSuccess: (result) => { setReview(result); setUnchecked(new Set()); setConfirmed(false); setTasks(null); },
  });
  const run = useMutation({
    mutationFn: (approved: MarketplaceTask[]) => runMarketplaceTasks(approved, api.bulkMarketplace.perform, () => stop.current,
      (progress, active) => { setTasks(progress); setActiveIds(active); }),
    retry: false,
    onSettled: (_result, _error, approved) => {
      setActiveIds(new Set());
      for (const key of ["conversations", "analytics", "automation-approvals", "listing-sheet"]) void client.invalidateQueries({ queryKey: [key] });
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
    {run.isPending ? <Button variant="outline" disabled={stopRequested} onClick={() => { stop.current = true; setStopRequested(true); }}>{stopRequested ? "Stopping after the items running now…" : "Stop after the items running now"}</Button> : <>
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
    <Button disabled={busy || !chosen.length} onClick={() => preview.mutate(conversationIds)}>
      {textProgress !== null ? `Preparing text ${textProgress} of ${conversationIds.length}…` : preview.isPending ? "Checking…" : "Review items"}
    </Button>
  </>;
  return <PriceDialog title={tasks ? `${verb.label} results` : review ? `Review ${verb.label.toLowerCase()}` : `Marketplace actions for ${conversationIds.length} ${conversationIds.length === 1 ? "item" : "items"}`}
    className="max-w-3xl" busy={busy} onClose={onClose} footer={footer}
    description="Nothing is sent until you review each item and confirm. List and Update can run together; other actions run one item at a time. Stop lets running items finish and leaves the rest untouched; failures require a new review.">
    {preview.error || run.error ? <PageError message={preview.error?.message || run.error?.message || "Could not review these items"} /> : null}
    {textResults ? <TextSummary results={textResults} titles={new Map([
      ...(client.getQueryData<ConversationSummary[]>(["conversations"]) ?? []).map((row) => [row.id, row.title || "Untitled item"] as const),
      ...(review?.rows ?? []).map((row) => [row.conversation_id, row.title || "Untitled item"] as const),
    ])} /> : null}
    {tasks ? <div className="space-y-3" aria-live="polite">
      <PageNote>{tasks.filter((task) => task.state === "done").length} done · {tasks.filter((task) => task.state === "failed" || task.state === "unconfirmed").length} need attention · {tasks.filter((task) => task.state === "pending").length} not started</PageNote>
      {tasks.map((task) => <div key={task.id} className="min-w-0 break-words rounded-lg border p-3">
        <p className="font-medium">{task.title}</p>
        <p className="text-xs">{activeIds.has(task.id) ? `${ACTIVE[task.request.action]} on ${names(task.request.marketplaces)}…`
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
      <fieldset><legend className="mb-3 font-medium">Action</legend>
        <RadioGroup name="bulk-marketplace-action" className="gap-2" value={action} disabled={busy} onValueChange={(value) => setAction(value as BulkMarketplaceAction)}>
          {ACTIONS.map((entry) => <label key={entry.value} className="flex items-start gap-2">
            <Radio value={entry.value} className="mt-0.5" />
            <span><span className="font-medium">{entry.label}</span><span className="block text-xs text-muted-foreground">{entry.note}</span></span>
          </label>)}
        </RadioGroup>
      </fieldset>
      <fieldset className="space-y-2"><legend className="mb-1 font-medium">Marketplaces</legend>
        <div className="grid gap-2 sm:grid-cols-3">{BULK_MARKETPLACES.map((name) => <label key={name} className="flex items-center gap-2">
          <Checkbox checked={chosen.includes(name)} disabled={busy}
            onCheckedChange={(checked) => setChosen((previous) => checked ? BULK_MARKETPLACES.filter((value) => value === name || previous.includes(value)) : previous.filter((value) => value !== name))} />
          {marketplaceName(name)}
        </label>)}</div>
        <PageNote>
          {initialMarketplaces.length > 0
            ? "Pre-selected from this tab. You can change the selection here; nothing is sent until you review each item and confirm."
            : "Choose each marketplace by name. None is chosen for you."}
        </PageNote>
      </fieldset>
      {writesText ? <fieldset><legend className="mb-3 font-medium">Marketplace text</legend>
        <RadioGroup name="bulk-marketplace-text" className="gap-2" value={textMode} disabled={busy} onValueChange={(value) => setTextMode(value as TextMode)}>
          {TEXT_MODES.map((entry) => <label key={entry.id} className="flex items-start gap-2">
            <Radio value={entry.id} className="mt-0.5" />
            <span><span className="font-medium">{entry.label}</span><span className="block text-xs text-muted-foreground">{entry.detail}</span></span>
          </label>)}
        </RadioGroup>
        {textMode !== "keep" ? <PageNote className="mt-2">The text is saved on each marketplace's form in Anteroom before the review. Nothing is sent until you confirm.</PageNote> : null}
      </fieldset> : null}
    </div>}
  </PriceDialog>;
}

function TextSummary({ results, titles }: { results: TextPrepResult[]; titles: Map<string, string> }) {
  const changed = results.filter((result) => result.ok && result.changed).length;
  const failed = results.filter((result) => !result.ok);
  return <div className="space-y-1 rounded-lg border p-3 text-xs">
    <p className="font-medium">Marketplace text: {changed} {changed === 1 ? "item" : "items"} updated in Anteroom
      {failed.length ? ` · ${failed.length} kept as they were` : ""}</p>
    {results.map((result, index) => !result.ok || result.skipped ? (
      <p key={result.id} className="text-muted-foreground">{titles.get(result.id) || `Item ${index + 1}`}: {result.ok
        ? `${result.skipped} ${result.skipped === 1 ? "field" : "fields"} skipped`
        : result.error}</p>
    ) : null)}
  </div>;
}
