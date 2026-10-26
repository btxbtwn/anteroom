import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { BatchCopyPreviewRow } from "../api/listingCopy";
import { approvedCopies, copyDraft, copyDraftError, runCopies, type CopyDraft, type CopyTask } from "../bulkCopy";
import { PriceDialog } from "./PriceDropParts";
import { PageError, PageNote } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";

export function BulkCopyDialog({ conversationIds, onClose, onOpenListing }: { conversationIds: string[]; onClose: () => void; onOpenListing: (id: string) => void }) {
  const client = useQueryClient();
  const [review, setReview] = useState<BatchCopyPreviewRow[] | null>(null);
  const [edits, setEdits] = useState<ReadonlyMap<string, CopyDraft>>(new Map());
  const [tasks, setTasks] = useState<CopyTask[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [stopRequested, setStopRequested] = useState(false);
  const stop = useRef(false);
  const preview = useQuery({ queryKey: ["bulk-copy-preview", conversationIds], queryFn: () => api.conversations.copyBatchPreview(conversationIds), retry: false,
    staleTime: Infinity, gcTime: 0, refetchOnWindowFocus: false });
  const rows = review || preview.data?.rows;
  const remaining = tasks?.filter((task) => task.state !== "saved") || [];
  const reload = useMutation({ mutationFn: () => api.conversations.copyBatchPreview(remaining.length ? remaining.map((task) => task.sourceId) : conversationIds), retry: false,
    onSuccess: (result) => { setReview(result.rows); setTasks(null); } });
  const run = useMutation({ mutationFn: (approved: CopyTask[]) => runCopies(approved, api.conversations, () => stop.current,
    (progress, active) => { setTasks(progress); setActiveId(active); }), retry: false,
    onSettled: () => { setActiveId(null); void client.invalidateQueries({ queryKey: ["conversations"] }); void client.invalidateQueries({ queryKey: ["analytics"] }); } });
  const start = (approved: CopyTask[]) => { stop.current = false; setStopRequested(false); setTasks(approved); run.mutate(approved); };
  const busy = run.isPending || reload.isPending;
  const count = rows?.filter((row) => !row.skip_reason && !(edits.get(row.source_id) || copyDraft(row)).skip).length || 0;
  const valid = rows?.every((row) => !copyDraftError(row, edits.get(row.source_id) || copyDraft(row)));
  return <PriceDialog title={tasks ? "Copy results" : "Review inventory copies"} className="max-w-3xl" busy={busy} onClose={onClose}
    description="Each selected item becomes a separate local draft with a new SKU and quantity. Review each item’s description, condition and measurements before listing. Copying sends nothing to a marketplace."
    footer={tasks ? <>
      {run.isPending ? <Button variant="outline" disabled={stopRequested} onClick={() => { stop.current = true; setStopRequested(true); }}>{stopRequested ? "Stopping after this item…" : "Stop after current item"}</Button> : <>
        {remaining.length ? <><Button variant="ghost" disabled={busy} onClick={() => reload.mutate()}>Review remaining</Button><Button variant="outline" disabled={busy} onClick={() => start(tasks)}>{tasks.some((task) => task.state === "failed") ? "Retry remaining" : "Continue remaining"}</Button></> : null}
        <Button disabled={busy} onClick={onClose}>Done</Button>
      </>}
    </> : <><Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button>{preview.error ? <Button onClick={() => void preview.refetch()}>Reload sources</Button> : null}<Button disabled={busy || !rows || !valid || !count} onClick={() => start(approvedCopies(rows!, edits))}>Create {count} local {count === 1 ? "draft" : "drafts"}</Button></>}>
    {preview.error || reload.error || run.error ? <PageError message={run.error?.message || reload.error?.message || preview.error?.message || "Could not load copies"} /> : null}
    {tasks ? <div className="space-y-3" aria-live="polite"><PageNote>{tasks.filter((task) => task.state === "saved").length} created · {tasks.filter((task) => task.state === "failed").length} need attention · {tasks.filter((task) => task.state === "pending").length} remaining</PageNote>
      {tasks.map((task) => <div key={task.sourceId} className="min-w-0 break-words rounded-lg border p-3"><p className="font-medium">{task.title}</p><p>{activeId === task.sourceId ? "Copying…" : task.state === "saved" ? "Local draft created" : task.state === "failed" ? task.error : "Waiting"}</p>{task.targetId && !busy ? <Button size="xs" variant="ghost" onClick={() => onOpenListing(task.targetId!)}>Open draft</Button> : null}</div>)}
      {remaining.length && !run.isPending ? <PageNote>Retry keeps the approved values and checks for a completed copy first, so a lost reply does not create another draft. Review remaining reloads only the items that were not confirmed.</PageNote> : null}
    </div> : rows ? <div className="space-y-4"><PageNote>Set a separate inventory name, SKU and quantity for each new item. Photos are independent copies; cost is optional. Marketplace links, jobs, sales history and sourcing-box assignments stay with the originals.</PageNote>
      {rows.map((row) => {
        const draft = edits.get(row.source_id) || copyDraft(row), error = copyDraftError(row, draft);
        const update = (patch: Partial<CopyDraft>) => setEdits((previous) => new Map(previous).set(row.source_id, { ...draft, ...patch }));
        return <div key={row.source_id} className="min-w-0 space-y-3 rounded-lg border p-3">
          <label className="flex items-center gap-2 font-medium"><Checkbox checked={!draft.skip} disabled={Boolean(row.skip_reason) || busy} onCheckedChange={(checked) => update({ skip: checked !== true })} />{row.source_title}</label>
          {row.skip_reason ? <PageNote>{row.skip_reason}</PageNote> : <fieldset disabled={draft.skip || busy} className="space-y-3 disabled:opacity-50">
            <label className="block space-y-1 text-xs">Inventory name<Input aria-label={`Inventory name for ${row.source_title}`} value={draft.title} maxLength={200} onChange={(event) => update({ title: event.target.value })} /></label>
            <div className="grid gap-3 sm:grid-cols-2"><label className="space-y-1 text-xs">New SKU (optional)<Input aria-label={`New SKU for ${row.source_title}`} value={draft.sku} maxLength={120} onChange={(event) => update({ sku: event.target.value })} /></label><label className="space-y-1 text-xs">Quantity<Input aria-label={`Quantity for ${row.source_title}`} inputMode="numeric" value={draft.quantity} onChange={(event) => update({ quantity: event.target.value })} /></label></div>
            <label className="flex items-center gap-2 text-xs"><Checkbox checked={draft.includePhotos} disabled={draft.skip || busy || !row.photo_count} onCheckedChange={(checked) => update({ includePhotos: checked === true })} />Copy {row.photo_count || 0} saved photos for {row.source_title}</label>
            <label className="flex items-center gap-2 text-xs"><Checkbox checked={draft.includeCost} disabled={draft.skip || busy} onCheckedChange={(checked) => update({ includeCost: checked === true })} />Copy recorded cost for {row.source_title}</label>
            {error ? <PageError message={error} /> : null}
          </fieldset>}
        </div>;
      })}
    </div> : <PageNote>Loading selected items…</PageNote>}
  </PriceDialog>;
}
