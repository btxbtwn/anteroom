import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { BULK_FIELDS, bulkEditsFromInputs, bulkReviewTokens, type BulkEdits, type BulkField, type BulkReviewRow } from "../api/bulkEdit";
import { PriceDialog } from "./PriceDropParts";
import { PageError, PageNote } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";

const LABELS: Record<BulkField, string> = { title: "Title", description: "Description", brand: "Brand", size: "Size", sku: "SKU", price: "Price", cost: "Cost", quantity: "Quantity" };
function show(value: unknown): string { return value == null ? "Not set" : typeof value === "string" ? value || "Empty" : JSON.stringify(value); }

export function BulkEditDialog({ conversationIds, onClose }: { conversationIds: string[]; onClose: () => void }) {
  const client = useQueryClient();
  const [fields, setFields] = useState<Partial<Record<BulkField, string>>>({});
  const [labelsMode, setLabelsMode] = useState<BulkEdits["labels_mode"]>("keep");
  const [labels, setLabels] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [review, setReview] = useState<{ edits: BulkEdits; rows: BulkReviewRow[] } | null>(null);
  const [skips, setSkips] = useState<Set<string>>(new Set());
  const preview = useMutation({ mutationFn: (edits: BulkEdits) => api.bulkEdit.preview(conversationIds, edits), retry: false,
    onSuccess: (result, edits) => { setReview({ edits, rows: result.rows }); setSkips(new Set()); } });
  const apply = useMutation({ mutationFn: () => api.bulkEdit.apply(review!.edits, bulkReviewTokens(review!.rows.filter((row) => !skips.has(row.conversation_id)))), retry: false,
    onSuccess: (result) => {
      for (const key of ["conversations", "suggestions", "analytics", "sales-ledger", "size-audit"]) void client.invalidateQueries({ queryKey: [key] });
      for (const row of result.applied) {
        for (const key of ["listing", "revisions", "price-drop-preview", "marketplace-listings"]) void client.invalidateQueries({ queryKey: [key, row.conversation_id] });
      }
    } });
  const busy = preview.isPending || apply.isPending;
  const tokens = review ? bulkReviewTokens(review.rows.filter((row) => !skips.has(row.conversation_id))) : [];
  const submitReview = () => { setError(null); try { preview.mutate(bulkEditsFromInputs(fields, labelsMode, labels)); } catch (error) { setError((error as Error).message); } };
  return <PriceDialog title={apply.data ? "Bulk edit results" : review ? "Review bulk edits" : `Edit ${conversationIds.length} listings`} className="max-w-3xl" busy={busy} onClose={onClose}
    description="Save changes in Anteroom. Publishing, updating live listings and delisting require separate marketplace actions. Each saved edit has a revision you can restore."
    footer={apply.data ? <Button onClick={onClose}>Done</Button> : <>
      {review ? <Button variant="ghost" disabled={busy} onClick={() => { setReview(null); apply.reset(); }}>Back to fields</Button> : null}
      <Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button>
      <Button disabled={busy || Boolean(review && !tokens.length)} onClick={review ? () => apply.mutate() : submitReview}>{busy ? "Working…" : review ? `Save ${tokens.length} listings` : "Review changes"}</Button>
    </>}>
    {error || preview.error || apply.error ? <PageError message={error || preview.error?.message || apply.error?.message || "Could not save changes"} /> : null}
    {apply.data ? <div className="space-y-3"><PageNote>{apply.data.applied.length} saved · {apply.data.skipped.length} not changed</PageNote>
      {review?.rows.filter((row) => apply.data.applied.some((saved) => saved.conversation_id === row.conversation_id)).map((row) => <p key={row.conversation_id}>{row.title}: saved in Anteroom{apply.data.applied.find((saved) => saved.conversation_id === row.conversation_id)?.valid ? "" : " · listing needs review"}</p>)}
      {apply.data.skipped.map((row) => <p key={row.conversation_id}>{review?.rows.find((item) => item.conversation_id === row.conversation_id)?.title || row.conversation_id}: {row.reason}</p>)}
    </div> : review ? <ul className="space-y-4">{review.rows.map((row) => <li key={row.conversation_id} className="min-w-0 space-y-2 rounded-lg border p-3">
      <label className="flex items-center gap-2 font-medium"><Checkbox disabled={Boolean(row.skip_reason) || busy} checked={!row.skip_reason && !skips.has(row.conversation_id)} onCheckedChange={(checked) => setSkips((previous) => { const next = new Set(previous); if (checked) next.delete(row.conversation_id); else next.add(row.conversation_id); return next; })} />{row.title}</label>
      {row.skip_reason ? <p className="text-muted-foreground">{row.skip_reason}</p> : row.changes.map((change) => <div key={change.field} className="break-words text-xs"><p className="font-medium">{change.field.replace(/_/g, " ")}</p><pre className="whitespace-pre-wrap break-words font-sans">{show(change.old)} → {show(change.new)}</pre></div>)}
    </li>)}</ul> : <div className="space-y-4">
      <PageNote>Check only the fields to replace on every selected item. Unchecked fields stay as they are. Blank description, brand or SKU clears that field; blank cost means unknown. Prices and quantities are whole numbers. Size and price changes include the editor’s dependent fields in the review.</PageNote>
      {BULK_FIELDS.map((field) => <div key={field} className="space-y-1"><label className="flex items-center gap-2"><Checkbox checked={fields[field] !== undefined} disabled={busy} onCheckedChange={(checked) => setFields((previous) => { const next = { ...previous }; if (checked) next[field] = ""; else delete next[field]; return next; })} />Replace {LABELS[field]}</label>
        {fields[field] !== undefined ? field === "description" ? <Textarea aria-label={LABELS[field]} value={fields[field]} disabled={busy} onChange={(event) => setFields({ ...fields, [field]: event.target.value })} /> : <Input aria-label={LABELS[field]} inputMode={["price", "cost", "quantity"].includes(field) ? "decimal" : "text"} value={fields[field]} disabled={busy} onChange={(event) => setFields({ ...fields, [field]: event.target.value })} /> : null}
      </div>)}
      <div className="space-y-2"><p>Labels</p><Select value={labelsMode} onValueChange={(value) => { if (value) setLabelsMode(value); }} disabled={busy}><SelectTrigger aria-label="Label action"><SelectValue /></SelectTrigger><SelectPopup><SelectItem value="keep">Keep labels</SelectItem><SelectItem value="add">Add labels</SelectItem><SelectItem value="remove">Remove labels</SelectItem><SelectItem value="replace">Replace all labels</SelectItem></SelectPopup></Select>
        {labelsMode !== "keep" ? <><Input aria-label="Labels, comma separated" value={labels} disabled={busy} onChange={(event) => setLabels(event.target.value)} /><PageNote>Comma-separated labels. Replacing with an empty list clears all labels.</PageNote></> : null}
      </div>
    </div>}
  </PriceDialog>;
}
