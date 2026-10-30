import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { BULK_FIELDS, CHOICE_FIELDS, bulkEditsFromInputs, bulkReviewTokens, type BulkCategories, type BulkEdits, type BulkField, type BulkReviewRow, type ListMode } from "../api/bulkEdit";
import { EbayCategorySelect, TreeCategorySelect } from "./ListingFormControls";
import { PriceDialog } from "./PriceDropParts";
import { PageError, PageNote } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { useWorkspaceRuntime } from "../app/runtime";

const LABELS: Record<BulkField, string> = { title: "Title", description: "Description", brand: "Brand", size: "Size", sku: "SKU", price: "Price", cost: "Cost", quantity: "Quantity",
  condition: "Condition", primaryColor: "Primary color", secondaryColor: "Secondary color" };
const NO_COLOR = "__none__";
type CategoryScope = "general" | "ebay" | "depop";
const CATEGORY_SCOPES: { scope: CategoryScope; label: string; note: string }[] = [
  { scope: "general", label: "Shared category", note: "The category every marketplace starts from." },
  { scope: "ebay", label: "eBay category", note: "eBay’s own category, from eBay’s category search. Its item specifics reload when you open each item." },
  { scope: "depop", label: "Depop category", note: "Depop’s own category. A Depop size picked for the old category is cleared." },
];

function ListModeSelect({ label, value, onChange, disabled }: { label: string; value: ListMode; onChange: (value: ListMode) => void; disabled: boolean }) {
  const noun = label.toLowerCase();
  return <Select value={value} onValueChange={(next) => { if (next) onChange(next as ListMode); }} disabled={disabled}>
    <SelectTrigger aria-label={`${label} action`}><SelectValue /></SelectTrigger>
    <SelectPopup><SelectItem value="keep">Keep {noun}</SelectItem><SelectItem value="add">Add {noun}</SelectItem><SelectItem value="remove">Remove {noun}</SelectItem><SelectItem value="replace">Replace all {noun}</SelectItem></SelectPopup>
  </Select>;
}
function show(value: unknown): string { return value == null ? "Not set" : typeof value === "string" ? value || "Empty" : JSON.stringify(value); }

export function BulkEditDialog({ conversationIds, onClose }: { conversationIds: string[]; onClose: () => void }) {
  const { features } = useWorkspaceRuntime();
  const client = useQueryClient();
  const [fields, setFields] = useState<Partial<Record<BulkField, string>>>({});
  const [labelsMode, setLabelsMode] = useState<ListMode>("keep");
  const [labels, setLabels] = useState("");
  const [tagsMode, setTagsMode] = useState<ListMode>("keep");
  const [tags, setTags] = useState("");
  const [categories, setCategories] = useState<Partial<Record<CategoryScope, { id: string; path: string } | null>>>({});
  const { data: dropdowns } = useQuery({ queryKey: ["catalog-dropdown-options"], queryFn: api.catalog.dropdownOptions, staleTime: Infinity });
  const choices = (field: BulkField): string[] => {
    const vendoo = dropdowns?.forms?.vendoo || {};
    return field === "condition" ? vendoo.condition || [] : vendoo.primaryColor || [];
  };
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
  const submitReview = () => {
    setError(null);
    try {
      const unpicked = Object.entries(categories).find(([, value]) => value === null);
      if (unpicked) throw new Error(`Pick the ${CATEGORY_SCOPES.find((entry) => entry.scope === unpicked[0])!.label.toLowerCase()} from the search, or uncheck it.`);
      const category: BulkCategories = {};
      if (categories.general) category.general = categories.general.id;
      if (categories.depop) category.depop = categories.depop.id;
      if (categories.ebay) category.ebay = categories.ebay;
      preview.mutate(bulkEditsFromInputs(fields, labelsMode, labels, { tagsMode, tagsText: tags, category }));
    } catch (error) { setError((error as Error).message); }
  };
  const fieldControl = (field: BulkField) => {
    const value = fields[field] ?? "";
    const set = (next: string) => setFields({ ...fields, [field]: next });
    if (CHOICE_FIELDS.has(field)) {
      const blank = field === "secondaryColor";
      return <Select value={value || (blank ? NO_COLOR : "")} onValueChange={(next) => { if (next) set(next === NO_COLOR ? "" : next as string); }} disabled={busy}>
        <SelectTrigger aria-label={LABELS[field]}><SelectValue placeholder={`Choose ${LABELS[field].toLowerCase()}`} /></SelectTrigger>
        <SelectPopup>{blank ? <SelectItem value={NO_COLOR}>No secondary color</SelectItem> : null}{choices(field).map((option) => <SelectItem key={option} value={option}>{option}</SelectItem>)}</SelectPopup>
      </Select>;
    }
    if (field === "description") return <Textarea aria-label={LABELS[field]} value={value} disabled={busy} onChange={(event) => set(event.target.value)} />;
    return <Input aria-label={LABELS[field]} inputMode={["price", "cost", "quantity"].includes(field) ? "decimal" : "text"} value={value} disabled={busy} onChange={(event) => set(event.target.value)} />;
  };
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
      <PageNote>Check only the fields to replace on every selected item. Unchecked fields stay as they are. Blank description, brand or SKU clears that field; blank cost means unknown. {features.has("catalog_categories") ? "Categories must be picked from the search. " : null}Prices and quantities are whole numbers. Size and price changes include the editor’s dependent fields in the review.</PageNote>
      {BULK_FIELDS.map((field) => <div key={field} className="space-y-1"><label className="flex items-center gap-2"><Checkbox checked={fields[field] !== undefined} disabled={busy} onCheckedChange={(checked) => setFields((previous) => { const next = { ...previous }; if (checked) next[field] = ""; else delete next[field]; return next; })} />Replace {LABELS[field]}</label>
        {fields[field] !== undefined ? fieldControl(field) : null}
      </div>)}
      {(features.has("catalog_categories") ? CATEGORY_SCOPES : []).map(({ scope, label, note }) => <div key={scope} className="space-y-1">
        <label className="flex items-center gap-2"><Checkbox checked={scope in categories} disabled={busy} onCheckedChange={(checked) => setCategories((previous) => { const next = { ...previous }; if (checked) next[scope] = null; else delete next[scope]; return next; })} />Replace {label}</label>
        {scope in categories ? <>
          {scope === "ebay"
            ? <EbayCategorySelect id="bulk-category-ebay" value={categories.ebay?.path || ""} onChange={(id, path) => setCategories((previous) => ({ ...previous, ebay: { id, path } }))} />
            : <TreeCategorySelect id={`bulk-category-${scope}`} marketplace={scope} name={scope === "general" ? "shared" : "Depop"} disabled={busy}
              value={categories[scope]?.path || ""} onChange={(id, path) => setCategories((previous) => ({ ...previous, [scope]: { id, path } }))} />}
          <PageNote>{note}</PageNote>
        </> : null}
      </div>)}
      <div className="space-y-2"><p>Tags</p><ListModeSelect label="Tags" value={tagsMode} onChange={setTagsMode} disabled={busy} />
        {tagsMode !== "keep" ? <><Input aria-label="Tags, comma separated" value={tags} disabled={busy} onChange={(event) => setTags(event.target.value)} /><PageNote>Comma-separated search tags. Etsy keeps the first 13 it accepts.</PageNote></> : null}
      </div>
      <div className="space-y-2"><p>Labels</p><ListModeSelect label="Labels" value={labelsMode} onChange={setLabelsMode} disabled={busy} />
        {labelsMode !== "keep" ? <><Input aria-label="Labels, comma separated" value={labels} disabled={busy} onChange={(event) => setLabels(event.target.value)} /><PageNote>Comma-separated labels. Replacing with an empty list clears all labels.</PageNote></> : null}
      </div>
    </div>}
  </PriceDialog>;
}
