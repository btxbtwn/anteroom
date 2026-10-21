import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AssistantChanges, AssistantFieldChange, AssistantMessage } from "../api/assistant";

const FIELD_LABELS: Record<AssistantFieldChange["field"], string> = {
  asking: "Asking",
  cost: "Cost",
  title: "Title",
  description: "Description",
  brand: "Brand",
  size: "Size",
  ebay_size: "eBay size",
  poshmark_size: "Poshmark size",
  mercari_size: "Mercari size",
  depop_size: "Depop size",
  etsy_size: "Etsy size",
  sku: "SKU",
  labels: "Labels",
  box: "Box",
};
/** Listings shown before the rest fold behind "Show all". */
const PREVIEW_ROWS = 8;

/** One side of a change as the seller reads it. */
export function changeValue(field: AssistantFieldChange["field"], value: AssistantFieldChange["old"]): string {
  if (value === null || value === "" || (Array.isArray(value) && !value.length)) return "none";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "number" && (field === "asking" || field === "cost")) {
    return `$${Number.isInteger(value) ? value : value.toFixed(2)}`;
  }
  return String(value);
}

/** The card's heading: what is proposed, or what became of it. */
export function changesHeading(changes: AssistantChanges): string {
  const count = (n: number) => `${n} listing${n === 1 ? "" : "s"}`;
  const skipped = changes.items.filter((item) => item.skipped).length;
  if (changes.status === "dismissed") return "Dismissed. Nothing was changed.";
  if (changes.status === "proposed") return `Proposed changes to ${count(changes.items.length)}`;
  const applied = `Applied to ${count(changes.items.length - skipped)}`;
  return skipped ? `${applied}, ${skipped} skipped` : applied;
}

interface Props {
  message: AssistantMessage;
  changes: AssistantChanges;
  /** The answer as Studio now holds it, and whether listings were written. */
  onSettled: (message: AssistantMessage, applied: boolean) => void;
}

/**
 * Changes an answer proposed, each value before and after. Nothing is written
 * until the seller presses Apply.
 */
export function AssistantChangesCard({ message, changes, onSettled }: Props) {
  const [showAll, setShowAll] = useState(false);
  const apply = useMutation({
    mutationFn: () => api.assistant.applyChanges(message.id),
    onSuccess: (saved) => onSettled(saved, true),
  });
  const dismiss = useMutation({
    mutationFn: () => api.assistant.dismissChanges(message.id),
    onSuccess: (saved) => onSettled(saved, false),
  });
  const busy = apply.isPending || dismiss.isPending;
  const failure = (apply.isError ? apply.error : dismiss.isError ? dismiss.error : null) as Error | null;
  const items = showAll ? changes.items : changes.items.slice(0, PREVIEW_ROWS);
  const hidden = changes.items.length - items.length;

  return (
    <section className={`assistant-changes is-${changes.status}`} aria-label="Changes the assistant proposed">
      <h2 className="assistant-changes-heading">{changesHeading(changes)}</h2>
      <ul className="assistant-changes-list">
        {items.map((item) => (
          <li key={item.listing_id} className="assistant-change">
            <a className="assistant-change-title" href={`#listing-${item.listing_id}`}>{item.title}</a>
            {item.fields.map((change) => (
              <div key={change.field} className="assistant-change-field">
                <span className="assistant-change-label">{FIELD_LABELS[change.field] ?? change.field}</span>
                <span className="assistant-change-values">
                  <span className="assistant-change-old">{changeValue(change.field, change.old)}</span>
                  <span aria-hidden="true">→</span>
                  <span className="assistant-change-new">{changeValue(change.field, change.new)}</span>
                </span>
              </div>
            ))}
            {item.skipped ? <p className="assistant-change-skipped">Not changed. {item.skipped}</p> : null}
          </li>
        ))}
      </ul>
      {hidden > 0 ? (
        <button type="button" className="assistant-action" onClick={() => setShowAll(true)}>
          Show all {changes.items.length}
        </button>
      ) : null}
      {changes.status === "proposed" ? (
        <>
          <p className="analytics-note">
            Applying saves these in Studio. A listing already on Vendoo keeps its old values there until you send it
            again.
          </p>
          {failure ? <p className="text-destructive-foreground text-sm">{failure.message || "Could not save the changes."}</p> : null}
          <div className="assistant-changes-actions">
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => dismiss.mutate()}>
              Dismiss
            </button>
            <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => apply.mutate()}>
              {apply.isPending ? "Applying…" : "Apply"}
            </button>
          </div>
        </>
      ) : null}
    </section>
  );
}
