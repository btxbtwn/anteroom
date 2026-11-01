import { useId, useRef, useState, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2Icon, ChevronDownIcon, ChevronRightIcon, ClipboardCheckIcon, PencilLineIcon, TriangleAlertIcon } from "lucide-react";
import { api } from "../../api/client";
import type { EbayPayload, EbayResult, EbayReview } from "../../api/ebay";
import { PageError } from "../WorkspacePage";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Textarea } from "../ui/textarea";
import { Spinner } from "../ui/spinner";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogPanel, DialogPopup, DialogTitle } from "../ui/dialog";
import { useFormDraft } from "./FormDrafts";

export type FieldSpec = {
  name: string; label: string; type?: string; initial?: string; options?: (string | { value: string; label: string })[];
  optional?: boolean; min?: string; max?: string; step?: string; maxLength?: number; help?: string; group?: string;
};

function FormFields({ fields, values, change, disabled }: {
  fields: FieldSpec[]; values: Record<string, string>; change: (name: string, value: string) => void; disabled: boolean;
}) {
  const id = useId();
  const groups = [...new Set(fields.map((field) => field.group || ""))];
  return <div className="ebay-form-fields">{groups.map((group) => <fieldset key={group} className="ebay-form-group" disabled={disabled}>
    {group ? <legend>{group}</legend> : null}
    {fields.filter((field) => (field.group || "") === group).map((field) => {
      const fieldId = `${id}-${field.name}`;
      const props = { id: fieldId, required: !field.optional, "aria-describedby": field.help ? `${fieldId}-help` : undefined,
        value: values[field.name] ?? "", disabled, onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => change(field.name, event.target.value) };
      return <div className="ebay-field" key={field.name}>
        <label htmlFor={fieldId} className="ebay-field-label">{field.label}{field.optional ? <span className="ebay-field-optional">Optional</span> : null}</label>
        {field.options ? <select {...props}><option value="">Choose…</option>{field.options.map((option) => {
          const value = typeof option === "string" ? option : option.value;
          return <option key={value} value={value}>{typeof option === "string" ? option : option.label}</option>;
        })}</select> : field.type === "textarea" ? <Textarea {...props} rows={4} maxLength={field.maxLength} />
          : <Input {...props} nativeInput type={field.type || "text"} min={field.min} max={field.max} maxLength={field.maxLength}
            step={field.step || (field.type === "number" ? "any" : undefined)} />}
        {field.help ? <p className="ebay-field-help" id={`${fieldId}-help`}>{field.help}</p> : null}
      </div>;
    })}
  </fieldset>)}</div>;
}

export function ActionForm({ title, kind, fields = [], context = {}, build, disabled = false, onResult, children, defaultOpen = true, draftId }: {
  title: string; kind: string; fields?: FieldSpec[]; context?: EbayPayload; disabled?: boolean; defaultOpen?: boolean; draftId?: string;
  build?: (values: Record<string, string>) => EbayPayload; onResult?: (result: EbayResult) => void; children?: ReactNode;
}) {
  const queryClient = useQueryClient();
  const bodyId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirming = useRef(false);
  const [expanded, setExpanded] = useState(defaultOpen);
  const resource = ["conversation_id", "order_id", "dispute_id", "resource_id", "offer_id", "campaign_id", "line_item_id"].map((key) => context[key] || "");
  const [values, setValues] = useFormDraft(JSON.stringify([kind, draftId ?? resource]),
    () => Object.fromEntries(fields.map((field) => [field.name, field.initial || ""])));
  const [review, setReview] = useState<(EbayReview & { payload: EbayPayload; contextKey: string }) | null>(null);
  const [result, setResult] = useState<EbayResult | null>(null);
  const [buildError, setBuildError] = useState("");
  const contextKey = JSON.stringify(context);
  const activeReview = review?.contextKey === contextKey ? review : null;
  const preview = useMutation({ mutationFn: ({ payload }: { payload: EbayPayload; contextKey: string }) => api.ebay.preview(kind, payload), retry: false,
    onSuccess: (data, input) => { setReview({ ...data, ...input }); setResult(null); } });
  const confirm = useMutation({ mutationFn: () => {
    if (!activeReview) throw new Error("Review the action first.");
    return api.ebay.confirm(activeReview.id, kind, activeReview.payload);
  }, retry: false, onSettled: () => { confirming.current = false; }, onSuccess: (data) => {
    setResult(data); setReview(null); onResult?.(data);
    void queryClient.invalidateQueries({ queryKey: ["ebay"] });
  } });
  const busy = preview.isPending || confirm.isPending;
  const destructive = ["refund", "revise", "inventory_revision"].includes(kind);
  return <div className="ebay-action">
    <h3><button type="button" className="ebay-action-heading" aria-label={title} aria-expanded={expanded} aria-controls={bodyId} disabled={busy} onClick={() => setExpanded(!expanded)}>
      <span><PencilLineIcon aria-hidden="true" /><span><span className="ebay-action-title">{title}</span><span className="ebay-action-hint">Enter details, then review before confirming</span></span></span>
      {expanded ? <ChevronDownIcon aria-hidden="true" /> : <ChevronRightIcon aria-hidden="true" />}
    </button></h3>
    <div hidden={!expanded} id={bodyId} className="ebay-action-body">
      <form className="ebay-form" aria-label={title} onSubmit={(event) => {
        event.preventDefault(); confirm.reset(); setBuildError(""); setReview(null);
        try { preview.mutate({ payload: { ...context, ...(build ? build(values) : values) }, contextKey }); }
        catch { setBuildError("Check the dates and amounts, then try again."); }
      }}>
        {children}
        <FormFields fields={fields} values={values} disabled={disabled || busy} change={(name, value) => {
          setValues({ ...values, [name]: value }); setReview(null); setResult(null); preview.reset(); confirm.reset(); setBuildError("");
        }} />
        {preview.error || buildError ? <PageError message={preview.error?.message || buildError} /> : null}
        <div className="ebay-form-footer">
          <Button type="submit" disabled={disabled || busy}>{preview.isPending ? <Spinner /> : <ClipboardCheckIcon />}Review {title.toLowerCase()}</Button>
          <p>{disabled ? "Complete the selection above to continue." : "You’ll confirm the exact change in the next step."}</p>
        </div>
      </form>
    </div>
    {result ? <div className="ebay-action-result" role="status">{result.state === "unknown" ? <TriangleAlertIcon /> : <CheckCircle2Icon />}<p>{result.message}{result.receiptId ? <span className="block text-xs text-muted-foreground mt-1">Receipt {result.receiptId}</span> : null}</p></div> : null}
    <Dialog open={Boolean(activeReview)} onOpenChange={(open) => { if (!open && !confirming.current) setReview(null); }}>
      <DialogPopup className="ebay-action-dialog sm:max-w-xl" initialFocus={cancelRef} showCloseButton={!confirm.isPending}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>Check the details below. Confirming makes this change in your eBay account.</DialogDescription>
        </DialogHeader>
        <DialogPanel>
          <div className="ebay-review-list" role="region" aria-label={`Review ${title}`}>
            {activeReview?.images?.length ? <div className="ebay-review-images">{activeReview.images.filter((url) => /^https:\/\/([a-z0-9-]+\.)*ebayimg\.com\//i.test(url)).map((url, index) =>
              <a key={url} href={url} target="_blank" rel="noreferrer"><img src={url} alt={`Listing photo ${index + 1}`} /></a>)}</div> : null}
            {activeReview?.summary.map((line, index) => <p key={index} className="ebay-review-line">{line}</p>)}
          </div>
          {activeReview ? <p className="text-xs text-muted-foreground">This review expires at {new Date(activeReview.expiresAt).toLocaleTimeString()}.</p> : null}
          {confirm.error ? <PageError message={confirm.error.message} /> : null}
        </DialogPanel>
        <DialogFooter>
          <Button ref={cancelRef} variant="outline" disabled={confirm.isPending} onClick={() => { if (!confirming.current) setReview(null); }}>Back to details</Button>
          <Button aria-label={`Confirm ${title.toLowerCase()}`} aria-busy={confirm.isPending} variant={destructive ? "destructive" : "default"} disabled={confirm.isPending || confirm.isError || !activeReview} onClick={() => {
            if (confirming.current) return;
            confirming.current = true;
            confirm.mutate();
          }}>
            {confirm.isPending ? <Spinner /> : null}Confirm {title.toLowerCase()}
          </Button>
        </DialogFooter>
      </DialogPopup>
    </Dialog>
  </div>;
}
