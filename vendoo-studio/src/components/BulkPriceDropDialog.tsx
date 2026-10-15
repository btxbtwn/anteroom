import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import {
  applyMarkdownsLabel,
  canApplyMarkdowns,
  draftError,
  draftsFromPreview,
  dropPercentLabel,
  markdownDrops,
  markdownOutcomes,
  markdownSummary,
  type MarkdownDraft,
  type MarkdownOutcome,
} from "../bulkPriceDrop";

function money(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return Number.isInteger(value) ? `$${value}` : `$${value.toFixed(2)}`;
}

/**
 * Review a markdown for several listings and save the confirmed prices as
 * Studio revisions. Suggestions come from the seller's own sales and prior
 * drops only — sold comps take minutes per listing and stay on the
 * single-listing dialog. Nothing here sends to Vendoo.
 */
export function BulkPriceDropDialog({
  conversationIds,
  onClose,
}: {
  conversationIds: string[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  // The seller's edits over the loaded suggestions, so a row keeps them across refetches.
  const [edits, setEdits] = useState<ReadonlyMap<string, Partial<MarkdownDraft>>>(new Map());
  const [outcomes, setOutcomes] = useState<MarkdownOutcome[] | null>(null);

  const preview = useQuery({
    queryKey: ["markdown-preview", conversationIds],
    queryFn: () => api.listings.markdownPreview(conversationIds),
    gcTime: 0,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  const drafts = useMemo(
    () =>
      preview.data
        ? draftsFromPreview(preview.data.rows).map((draft) => ({ ...draft, ...edits.get(draft.id) }))
        : null,
    [edits, preview.data],
  );

  const apply = useMutation({
    mutationFn: () => api.listings.applyMarkdowns(markdownDrops(drafts || [])),
    onSuccess: (result) => {
      setOutcomes(markdownOutcomes(drafts || [], result));
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      void queryClient.invalidateQueries({ queryKey: ["suggestions"] });
      for (const row of result.applied) {
        void queryClient.invalidateQueries({ queryKey: ["listing", row.conversation_id] });
        void queryClient.invalidateQueries({ queryKey: ["price-drop-preview", row.conversation_id] });
      }
    },
  });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || apply.isPending) return;
      event.preventDefault();
      event.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [apply.isPending, onClose]);

  const dropCount = useMemo(() => markdownDrops(drafts || []).length, [drafts]);
  const ready = drafts !== null && canApplyMarkdowns(drafts);

  const update = (id: string, patch: Partial<MarkdownDraft>) =>
    setEdits((prev) => new Map(prev).set(id, { ...prev.get(id), ...patch }));

  return createPortal(
    <div className="confirm-dialog price-drop-dialog" role="presentation">
      <button
        type="button"
        className="confirm-dialog-backdrop"
        aria-label="Cancel"
        onClick={() => {
          if (!apply.isPending) onClose();
        }}
      />
      <div
        className="confirm-dialog-popup price-drop-popup bulk-markdown-popup"
        role="dialog"
        aria-modal="true"
        aria-labelledby="bulk-markdown-title"
      >
        <div className="confirm-dialog-header">
          <h2 id="bulk-markdown-title" className="confirm-dialog-title">
            {outcomes ? markdownSummary(outcomes) : "Mark down listings"}
          </h2>
          <p className="confirm-dialog-description">
            {outcomes
              ? "Nothing was sent to Vendoo. Each listing now has unsent edits: open it and press Update Vendoo to send the new price to the marketplaces."
              : "Each price starts on the listing's suggestion from your own sales and prior drops. Edit any price or skip a listing. Applying saves the prices in Studio only."}
          </p>
        </div>

        <div className="price-drop-body">
          {outcomes ? (
            <ul className="price-drop-rows">
              {outcomes.map((outcome) => (
                <li key={outcome.id} className="price-drop-row">
                  <span className="price-drop-row-title">{outcome.title}</span>
                  {outcome.reason === null ? (
                    <span className="price-drop-row-price">
                      {money(outcome.from)} → {money(outcome.to)}{" "}
                      <span
                        className="nav-status nav-status-unsent"
                        title="Edited in Studio since Vendoo last had this listing. Press Update Vendoo to write it onto the Vendoo form."
                      >
                        unsent
                      </span>
                    </span>
                  ) : (
                    <span className="price-drop-row-price is-error">{outcome.reason}</span>
                  )}
                </li>
              ))}
            </ul>
          ) : preview.isError ? (
            <p className="price-drop-status is-error">
              {(preview.error as Error)?.message || "Could not load the suggestions."}
            </p>
          ) : drafts === null ? (
            <p className="price-drop-status">Loading a suggestion for each listing…</p>
          ) : (
            <ul className="bulk-markdown-rows">
              {drafts.map((draft) => (
                <MarkdownRow
                  key={draft.id}
                  draft={draft}
                  disabled={apply.isPending}
                  onChange={(patch) => update(draft.id, patch)}
                />
              ))}
            </ul>
          )}
          {apply.isError ? (
            <p className="price-drop-status is-error">
              {(apply.error as Error)?.message || "The prices could not be saved."}
            </p>
          ) : null}
        </div>

        <div className="confirm-dialog-footer">
          {outcomes ? (
            <button type="button" className="btn btn-primary" onClick={onClose}>
              Done
            </button>
          ) : (
            <>
              <button type="button" className="btn btn-outline" disabled={apply.isPending} onClick={onClose}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!ready || apply.isPending}
                onClick={() => apply.mutate()}
              >
                {apply.isPending ? "Saving…" : applyMarkdownsLabel(dropCount)}
              </button>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function MarkdownRow({
  draft,
  disabled,
  onChange,
}: {
  draft: MarkdownDraft;
  disabled: boolean;
  onChange: (patch: Partial<MarkdownDraft>) => void;
}) {
  const error = draftError(draft);
  const inputId = `bulk-markdown-${draft.id}`;
  const percent = draft.skip ? "" : dropPercentLabel(draft.current, draft.priceText);
  return (
    <li className={`bulk-markdown-row${draft.skip ? " is-skipped" : ""}`}>
      <div className="nav-thumb" aria-hidden="true">
        {draft.coverUrl ? (
          <img className="nav-thumb-img" src={draft.coverUrl} alt="" loading="lazy" draggable={false} />
        ) : null}
      </div>
      <div className="bulk-markdown-row-body">
        <label className="bulk-markdown-row-title" htmlFor={inputId}>{draft.title}</label>
        <div className="bulk-markdown-row-reason">
          {draft.unavailable || draft.reason}
        </div>
        {error ? <div className="bulk-markdown-row-error">{error}</div> : null}
      </div>
      <div className="bulk-markdown-row-price">
        <span className="bulk-markdown-row-current">{money(draft.current)} →</span>
        <input
          id={inputId}
          className="price-drop-custom bulk-markdown-input"
          type="number"
          min={1}
          step={1}
          inputMode="numeric"
          aria-label={`New price for ${draft.title}`}
          aria-invalid={error ? true : undefined}
          value={draft.skip ? "" : draft.priceText}
          placeholder={draft.skip ? "Kept" : ""}
          disabled={disabled || draft.skip || Boolean(draft.unavailable)}
          onChange={(event) => onChange({ priceText: event.target.value })}
        />
        <span className="bulk-markdown-row-percent">{percent}</span>
      </div>
      <label className="bulk-markdown-skip">
        <input
          type="checkbox"
          checked={draft.skip}
          disabled={disabled || Boolean(draft.unavailable)}
          onChange={(event) => onChange({ skip: event.target.checked })}
        />
        Skip
      </label>
    </li>
  );
}
