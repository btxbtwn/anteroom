import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { deletableRows, type BulkDeleteRow } from "../api/bulkDelete";
import { PriceDialog } from "./PriceDropParts";
import { PageError, PageNote } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";

/** Review, confirm, then delete the selected items from Anteroom only. Nothing is delisted. */
export function BulkDeleteDialog({ conversationIds, onClose }: { conversationIds: string[]; onClose: () => void }) {
  const client = useQueryClient();
  const [rows, setRows] = useState<BulkDeleteRow[] | null>(null);
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set());
  const [confirmed, setConfirmed] = useState(false);
  const preview = useMutation({ mutationFn: () => api.bulkDelete.preview(conversationIds), retry: false,
    onSuccess: (result) => { setRows(result.rows); setUnchecked(new Set()); setConfirmed(false); } });
  const remove = useMutation({ mutationFn: (approved: { conversation_id: string; review_token: string }[]) => api.bulkDelete.delete(approved), retry: false,
    onSettled: () => { for (const key of ["conversations", "suggestions", "analytics", "sales-ledger"]) void client.invalidateQueries({ queryKey: [key] }); } });
  const approved = rows ? deletableRows(rows, unchecked) : [];
  const busy = preview.isPending || remove.isPending;
  const titleOf = (id: string) => rows?.find((row) => row.conversation_id === id)?.title || "Untitled item";
  const footer = remove.data ? <Button onClick={onClose}>Done</Button> : rows ? <>
    <Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button>
    <Button variant="destructive" disabled={busy || !confirmed || !approved.length} onClick={() => remove.mutate(approved)}>
      {remove.isPending ? "Deleting…" : `Delete ${approved.length} ${approved.length === 1 ? "item" : "items"}`}
    </Button>
  </> : <>
    <Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button>
    <Button disabled={busy} onClick={() => preview.mutate()}>{preview.isPending ? "Checking…" : "Review items"}</Button>
  </>;
  return <PriceDialog title={remove.data ? "Delete results" : rows ? "Review delete" : `Delete ${conversationIds.length} items from Anteroom`}
    className="max-w-2xl" busy={busy} onClose={onClose} footer={footer}
    description="Deletes each item, its photos and its history from Anteroom. Nothing is delisted: an item still live on a marketplace is left out until you delist it or mark it not listed.">
    {preview.error || remove.error ? <PageError message={preview.error?.message || remove.error?.message || "Could not delete these items"} /> : null}
    {remove.data ? <div className="space-y-2">
      <PageNote>{remove.data.deleted.length} deleted · {remove.data.skipped.length} kept</PageNote>
      {remove.data.skipped.map((row) => <p key={row.conversation_id} className="text-xs">{titleOf(row.conversation_id)}: {row.reason}</p>)}
    </div> : rows ? <div className="space-y-3">
      <ul className="space-y-2">{rows.map((row) => <li key={row.conversation_id} className="min-w-0 space-y-1 rounded-lg border p-3">
        <label className="flex items-center gap-2 font-medium">
          <Checkbox aria-label={`Include ${row.title || "item"}`} disabled={Boolean(row.skip_reason) || busy}
            checked={!row.skip_reason && !unchecked.has(row.conversation_id)}
            onCheckedChange={(checked) => { setConfirmed(false); setUnchecked((previous) => { const next = new Set(previous); if (checked) next.delete(row.conversation_id); else next.add(row.conversation_id); return next; }); }} />
          {row.title || "Untitled item"}
        </label>
        {row.skip_reason ? <p className="text-xs text-muted-foreground">{row.skip_reason}</p> : null}
        {row.warning ? <p className="text-xs text-warning-foreground">{row.warning}</p> : null}
      </li>)}</ul>
      <label className="flex items-start gap-2 rounded-lg border p-3 font-medium">
        <Checkbox checked={confirmed} disabled={busy || !approved.length} onCheckedChange={(checked) => setConfirmed(checked === true)} />
        <span>I want to delete these {approved.length} items from Anteroom. This cannot be undone.</span>
      </label>
    </div> : <PageNote>Anteroom checks each item first. You confirm before anything is deleted.</PageNote>}
  </PriceDialog>;
}
